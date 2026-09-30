"""The triage card says what your own settings do with the digest's notice, never a fixed sentence.

The digest is delivered through `DashboardState.notify` as a notice, and what becomes of it is your
choice: quiet hours stop a ping, and your rule for notices says whether it pings, lands in the bell
as a badge, waits for the notification digest, or is never sent. The card said one thing for every
case — "a digest that lands inside that window is held back from your notifications" — so with a
badge or digest rule, where the notice does land (in the bell, or in the next notification digest),
the card said it had been held back.

The view now carries what `notify()` itself would do with this digest's notice inside the window
and outside it (`notification_rules.rule_outcome`, the rule layer `notify()` acts on), so these
tests deliver a real digest through the real gate and check that the card's reading is what
happened.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from personalclaw import notification_rules as nr
from personalclaw.dashboard.handlers import proactive as P
from personalclaw.proactive.pipeline import make_notify_deliver
from personalclaw.proactive.rank import Digest
from personalclaw.providers import entity_routes as er

_TITLE = "Morning triage"
_BODY = "3 items since yesterday.\n  1. Invoice from Example Supplies"


def _window(*, over_now: bool) -> tuple[str, str]:
    """A two-hour quiet window on LOCAL time, over now or well away from it."""
    now = datetime.now() + (timedelta(hours=0) if over_now else timedelta(hours=6))
    return (now - timedelta(hours=1)).strftime("%H:%M"), (now + timedelta(hours=1)).strftime(
        "%H:%M"
    )


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(er, "_entity_settings_path", lambda entity: tmp_path / f"{entity}.json")
    monkeypatch.setattr(nr, "config_dir", lambda: tmp_path)
    return tmp_path


@pytest.fixture
def state(monkeypatch):
    """A `DashboardState` with only what `notify()` touches, and every way out recorded."""
    from personalclaw.action_providers import services
    from personalclaw.dashboard import state as st
    from personalclaw.dashboard.desktop_registry import DesktopRegistry

    ds = object.__new__(st.DashboardState)
    ds._notification_log = []
    ds.desktop = DesktopRegistry()
    out: dict[str, list] = {"broadcast": []}
    monkeypatch.setattr(st.DashboardState, "_broadcast", lambda self, n: out["broadcast"].append(n))
    monkeypatch.setattr(st.DashboardState, "_announce_logged", lambda self, n: None)
    monkeypatch.setattr(st.DashboardState, "_push_target", lambda self, k, n: None)
    monkeypatch.setattr(st.DashboardState, "_channel_dm_target", lambda self, n: None)
    monkeypatch.setattr(st, "_persist_notification", lambda note: None)
    monkeypatch.setattr(services, "get_action_services", lambda: SimpleNamespace(state=ds))
    ds.out = out  # type: ignore[attr-defined]
    return ds


def _settings(*, quiet: bool, over_now: bool = True, **extra) -> None:
    start, end = _window(over_now=over_now)
    er._save_entity_settings(
        "notifications",
        {"quiet_hours_enabled": quiet, "quiet_hours_start": start, "quiet_hours_end": end, **extra},
    )


def _rule(mode: str, **extra) -> None:
    nr.save_rules({"rules": {"system/info": {"mode": mode, **extra}}})


def _deliver() -> None:
    """The digest's own delivery, as the triage run sends it."""
    assert make_notify_deliver(run_id="run-1", trigger_id="system:triage:digest")(
        Digest(title=_TITLE, body=_BODY)
    )


def _what_happened(home, state) -> str:
    """Where the digest's notice went: the bell, the bell as a badge, the notification digest, or
    nowhere."""
    queue = home / nr.DIGEST_QUEUE_NAME
    queued = [
        json.loads(line)
        for line in (queue.read_text().splitlines() if queue.is_file() else [])
        if line.strip()
    ]
    if queued:
        return "digest"
    if state._notification_log:
        (note,) = state._notification_log
        return "badge" if note.get("badge_only") else "immediate"
    return "nowhere"


def _notice() -> dict:
    return P._digest_notice(title=_TITLE, body=_BODY)


_NOWHERE = {"suppressed", "never", "dropped"}


# ── inside quiet hours, the card reads what your rule makes of it ──


@pytest.mark.parametrize(
    ("mode", "inside"),
    [("badge", "badge"), ("digest", "digest"), ("immediate", "suppressed"), ("never", "never")],
)
def test_inside_quiet_hours_the_card_reads_what_your_rule_did(home, state, mode, inside):
    _settings(quiet=True)
    _rule(mode)

    _deliver()

    notice = _notice()
    assert notice["inside"] == inside
    happened = _what_happened(home, state)
    assert happened == (inside if inside not in _NOWHERE else "nowhere")
    assert state.out["broadcast"] == [], "nothing pings inside quiet hours"


@pytest.mark.parametrize(
    ("mode", "outside"),
    [("badge", "badge"), ("digest", "digest"), ("immediate", "immediate"), ("never", "never")],
)
def test_outside_quiet_hours_the_card_reads_what_your_rule_did(home, state, mode, outside):
    _settings(quiet=True, over_now=False)
    _rule(mode)

    _deliver()

    notice = _notice()
    assert notice["outside"] == outside
    happened = _what_happened(home, state)
    assert happened == (outside if outside not in _NOWHERE else "nowhere")


def test_a_keyword_of_yours_raises_a_badge_rule_to_a_ping_outside_and_a_badge_inside(home, state):
    """Your condition matched the digest's own text: the card reads this digest, not the kind."""
    _rule("badge", conditions={"keywords": ["invoice"]})

    _settings(quiet=True, over_now=False)
    _deliver()
    assert _what_happened(home, state) == "immediate"
    assert _notice()["outside"] == "immediate"

    state._notification_log.clear()
    _settings(quiet=True, over_now=True)
    _deliver()
    assert _what_happened(home, state) == "badge"
    assert _notice()["inside"] == "badge"


def test_a_minimum_severity_above_notices_drops_it_at_every_hour(home, state):
    _settings(quiet=True, min_severity="warning")
    _rule("badge")

    _deliver()

    notice = _notice()
    assert (notice["inside"], notice["outside"]) == ("dropped", "dropped")
    assert notice["min_severity"] == "warning"
    assert _what_happened(home, state) == "nowhere"


def test_with_quiet_hours_off_inside_is_outside(home):
    _settings(quiet=False)
    _rule("immediate")

    notice = _notice()

    assert notice["quiet_hours"]["enabled"] is False
    assert notice["inside"] == notice["outside"] == "immediate"


def test_the_notice_names_the_rule_it_read(home):
    _settings(quiet=True)

    notice = _notice()

    assert notice["known"] is True
    assert notice["rule"] == "Notice"
    assert notice["quiet_hours"]["start"] and notice["quiet_hours"]["end"]


def test_unreadable_settings_are_unknown_not_off(home, monkeypatch):
    def _broken():
        raise OSError("settings unreadable")

    monkeypatch.setattr(er, "load_notifications_settings", _broken)

    notice = _notice()

    assert notice["known"] is False
    assert "inside" not in notice and "outside" not in notice


def test_the_ready_view_carries_the_notice_for_its_own_digest(home, monkeypatch):
    _settings(quiet=True)
    _rule("digest")
    monkeypatch.setattr(
        P,
        "_install_state",
        lambda: {"installed": True, "enabled": True, "schedule": None, "drift": False},
    )
    monkeypatch.setattr(
        P,
        "_latest_digest",
        lambda: (
            {"run_id": "run-1", "status": "completed", "finished_at": "2026-09-30T03:10:00Z"},
            {"digest_title": _TITLE, "digest_body": _BODY, "delivered": True, "collected": 3},
            [],
        ),
    )
    from aiohttp.test_utils import make_mocked_request

    resp = asyncio.run(P.api_proactive_digest(make_mocked_request("GET", "/api/proactive/digest")))
    view = json.loads(resp.body.decode())

    assert view["state"] == "ready"
    assert "quiet_hours" not in view
    assert (view["notice"]["inside"], view["notice"]["outside"]) == ("digest", "digest")

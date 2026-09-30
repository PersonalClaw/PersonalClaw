"""Quiet hours hold or quieten a notice your own rule already sends quietly; they never lose it.

A rule that sends a kind to the morning digest, or as a badge, is already the quiet delivery:
nothing pings for it at any hour. Quiet hours dropped such a notice outright before the rule was
read, so a loop that finished at 03:00 left no notice anywhere and the 08:00 digest said
"nothing queued". Quiet hours exist to stop interruptions ("not now"), and these interrupt
nobody: the digest one waits for the digest, the badge one lands in the bell silently.

A rule that pings (`immediate`) keeps its designed quiet-hours suppression, and mute and a raised
minimum severity still mean "not at all" whatever the rule says.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

from personalclaw import notification_rules as nr
from personalclaw.providers import entity_routes as er


def _window_over_now() -> tuple[str, str]:
    """A two-hour quiet window centred on LOCAL now — `notify()` reads the wall clock."""
    now = datetime.now()
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
    from personalclaw.dashboard import state as st
    from personalclaw.dashboard.desktop_registry import DesktopRegistry

    ds = object.__new__(st.DashboardState)
    ds._notification_log = []
    ds.desktop = DesktopRegistry()
    out: dict[str, list] = {"broadcast": [], "logged": [], "push": [], "dm": []}
    monkeypatch.setattr(st.DashboardState, "_broadcast", lambda self, n: out["broadcast"].append(n))
    monkeypatch.setattr(
        st.DashboardState, "_announce_logged", lambda self, n: out["logged"].append(n)
    )
    monkeypatch.setattr(st.DashboardState, "_push_target", lambda self, k, n: out["push"].append(n))
    monkeypatch.setattr(
        st.DashboardState, "_channel_dm_target", lambda self, n: out["dm"].append(n)
    )
    monkeypatch.setattr(st, "_persist_notification", lambda note: None)
    ds.out = out  # type: ignore[attr-defined]
    return ds


def _quiet_hours(**extra) -> None:
    start, end = _window_over_now()
    er._save_entity_settings(
        "notifications",
        {"quiet_hours_enabled": True, "quiet_hours_start": start, "quiet_hours_end": end, **extra},
    )


def _rules(rules: dict) -> None:
    nr.save_rules({"rules": rules})


def _queued(home) -> list[dict]:
    path = home / nr.DIGEST_QUEUE_NAME
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_a_digest_rule_queues_the_notice_for_the_digest_inside_quiet_hours(home, state):
    """🔴 Red on integration: dropped before the rule was read — no queue row, nothing at all."""
    _quiet_hours()
    _rules({"loop/complete": {"mode": "digest"}})

    state.notify("loop_complete", "Release notes finished", "Cycle 2 passed.")

    assert [n["title"] for n in _queued(home)] == ["Release notes finished"]
    assert state._notification_log == []
    assert state.out["broadcast"] == []


def test_a_badge_rule_lands_in_the_bell_silently_inside_quiet_hours(home, state):
    """🔴 Red on integration: dropped. A badge is recorded and counted, and nothing pings."""
    _quiet_hours()
    _rules({"cron/result": {"mode": "badge", "targets": ["dashboard", "push", "native"]}})

    state.notify("cron", "Morning brief finished", "Five bullets.")

    (note,) = state._notification_log
    assert note["title"] == "Morning brief finished"
    assert note["mode"] == "badge" and note["badge_only"] is True
    assert "native" not in note
    assert state.out["broadcast"] == [] and state.out["push"] == [] and state.out["dm"] == []
    assert len(state.out["logged"]) == 1


def test_a_condition_that_raises_a_quiet_rule_leaves_a_badge_inside_quiet_hours(home, state):
    """A keyword raises a digest rule to a ping. Inside quiet hours nothing pings, and the match
    must not be what loses a notice its own rule would have kept: it lands in the bell, silently,
    rather than waiting for the digest."""
    _quiet_hours()
    _rules(
        {"loop/complete": {"mode": "digest", "conditions": {"keywords": ["release"]}}},
    )

    state.notify("loop_complete", "Release notes finished", "Cycle 2 passed.")

    (note,) = state._notification_log
    assert note["mode"] == "badge" and note["escalated_by"] == "keyword: release"
    assert state.out["broadcast"] == [] and state.out["push"] == []


def test_a_ping_rule_is_still_suppressed_inside_quiet_hours(home, state):
    """The designed suppression stays: an `immediate` notice of a kind nobody has to answer is
    not raised inside the window. This is the vacuity floor for the two above: the same kind,
    the same window, and only the rule differs."""
    _quiet_hours()
    _rules({"loop/complete": {"mode": "immediate"}})

    state.notify("loop_complete", "Release notes finished", "Cycle 2 passed.")

    assert state._notification_log == [] and _queued(home) == []
    assert state.out["broadcast"] == []


def test_outside_quiet_hours_the_rule_delivers_as_it_always_did(home, state):
    er._save_entity_settings("notifications", {"quiet_hours_enabled": False})
    _rules({"loop/complete": {"mode": "digest"}, "cron/result": {"mode": "badge"}})

    state.notify("loop_complete", "Release notes finished", "Cycle 2 passed.")
    state.notify("cron", "Morning brief finished", "Five bullets.")

    assert [n["title"] for n in _queued(home)] == ["Release notes finished"]
    assert [n["title"] for n in state._notification_log] == ["Morning brief finished"]


def test_mute_and_min_severity_still_mean_not_at_all(home, state):
    """ "Not now" is quiet hours; "not at all" is mute and the severity floor, and a quiet rule
    does not bring back what either of those refused."""
    _rules({"loop/complete": {"mode": "digest"}, "cron/result": {"mode": "badge"}})
    for settings in ({"mute_all": True}, {"min_severity": "warning"}):
        _quiet_hours(**settings)
        state.notify("loop_complete", "Release notes finished", "Cycle 2 passed.")
        state.notify("cron", "Morning brief finished", "Five bullets.")
    assert _queued(home) == [] and state._notification_log == []


def test_the_gate_says_quiet_hours_rather_than_drop(home):
    """The gate's answer names the window, so `notify()` can ask the rule what "quiet" means for
    this notice instead of losing it: `hush` for a kind nobody has to answer, `quiet` for one
    somebody must, `drop` only for mute and the severity floor."""
    start, end = _window_over_now()
    er._save_entity_settings(
        "notifications",
        {"quiet_hours_enabled": True, "quiet_hours_start": start, "quiet_hours_end": end},
    )
    assert er.notification_posture("loop_complete") == er.POSTURE_HUSH
    assert er.notification_posture("needs_input") == er.POSTURE_QUIET
    assert er.notification_posture("error") == er.POSTURE_DELIVER
    er._save_entity_settings("notifications", {"mute_all": True})
    assert er.notification_posture("loop_complete") == er.POSTURE_DROP

"""What a restart left undone reaches the bell, whatever the hour.

A stopped gateway missed a scheduled run. On the next start the Triggers page showed the card
("Waiting for you after a restart … Missed 1 scheduled run") and nothing reached the bell: no
"Missed scheduled runs" notice in the bell, on the notifications page or in the notification log,
so the only place that knew was a page the owner had no reason to open. The restart happened at
01:27 local time, inside the owner's quiet hours, and the notice went out as a plain notice. Quiet
hours drop a plain notice outright (it is not recorded anywhere), so the card was kept and its
notice was thrown away.

The runs a restart left undone wait for the owner to run or dismiss each one, so their notice is
a decision the owner owes. Quiet hours keep it silent and still record it: it is in the bell,
unread, without a toast. The same holds for the notice the start after a stop sends about the
runs the stop cut off.

Driven through the real `DashboardState.notify`, because the delivery gate is exactly what the
recording fakes in the neighbouring suites stand in for.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest

from personalclaw import notification_kinds as nk
from personalclaw.dashboard.state import DashboardState
from personalclaw.gateway import GatewayOrchestrator
from personalclaw.providers import entity_routes
from personalclaw.triggers import service as SVC
from personalclaw.triggers.models import Trigger
from personalclaw.triggers.service import to_iso
from personalclaw.triggers.store import TriggerStore

HOUR = 3600.0


def _quiet_hours(*, around_now: bool) -> None:
    """Quiet hours two hours wide, centred on local now or twelve hours away from it.

    `notify` reads the local wall clock, so both cases come from one read of it and differ only
    in where the window sits.
    """
    centre = datetime.now() + timedelta(hours=0 if around_now else 12)
    entity_routes._save_entity_settings(
        "notifications",
        {
            "quiet_hours_enabled": True,
            "quiet_hours_start": (centre - timedelta(hours=1)).strftime("%H:%M"),
            "quiet_hours_end": (centre + timedelta(hours=1)).strftime("%H:%M"),
        },
    )
    assert entity_routes.quiet_hours_now() is around_now


def _state() -> DashboardState:
    return DashboardState(sessions=MagicMock(count=0), start_time=0.0)


def _gateway(state) -> GatewayOrchestrator:
    gateway = GatewayOrchestrator.__new__(GatewayOrchestrator)
    gateway.dashboard_state = state
    return gateway


def _boot_with_a_missed_slot(tmp_path) -> dict:
    """A daily digest whose slot passed while the gateway was stopped; the real boot sweep."""
    store = TriggerStore(base_dir=tmp_path)
    trigger = Trigger(
        id="clock:digest",
        name="Morning digest",
        kind="clock",
        spec={"kind": "cron", "expr": "0 * * * *", "timezone": "UTC"},
        workflow={"inline": {"provider": "notify", "config": {}}},
        capabilities={"providers": ["notify"]},
    )
    slot = (time.time() // HOUR) * HOUR
    trigger.next_fire_at = to_iso(slot)
    store.save_all([trigger])
    # Back fifteen minutes after its slot: missed (`scheduling.slot_missed`) whatever minute of the
    # hour this runs at, and with no later slot of the hourly cron passed yet.
    return SVC.boot(store, now=slot + 15 * 60)


def _notes(state) -> list[dict]:
    return [n for n in state._notification_log if n.get("event") == "automation.missed_review"]


def test_the_missed_run_notice_is_kept_in_the_bell_during_quiet_hours(tmp_path):
    _quiet_hours(around_now=True)
    state = _state()
    gateway = _gateway(None)  # the boot passes run before the dashboard exists
    gateway._record_boot_review(_boot_with_a_missed_slot(tmp_path), [], base_dir=tmp_path)
    gateway.dashboard_state = state
    gateway._surface_held_boot_review()

    (note,) = _notes(state)
    assert note["title"] == "Missed scheduled runs"
    assert note["body"].startswith("1 scheduled run was missed across 1 automation")
    assert note["mode"] == "badge" and note["badge_only"] is True, "recorded, and no toast"
    assert note["statusUrl"] == "#/triggers"


def test_outside_quiet_hours_the_same_notice_is_shown_at_once(tmp_path):
    """The control: the only difference from the case above is where the window sits."""
    _quiet_hours(around_now=False)
    state = _state()
    _gateway(state)._record_boot_review(_boot_with_a_missed_slot(tmp_path), [], base_dir=tmp_path)

    (note,) = _notes(state)
    assert note["mode"] == "immediate" and not note.get("badge_only")


def test_a_raised_minimum_severity_still_lets_it_through(tmp_path):
    """Runs that did not happen wait on the owner, like the triggers an upgrade left off: a
    minimum severity of warning keeps passing them."""
    entity_routes._save_entity_settings("notifications", {"min_severity": "warning"})
    state = _state()
    _gateway(state)._record_boot_review(_boot_with_a_missed_slot(tmp_path), [], base_dir=tmp_path)

    assert len(_notes(state)) == 1


def test_the_notice_about_the_runs_a_stop_cut_off_is_kept_too(tmp_path):
    """The stop keeps the card of each run it cuts off, and the start after it says so, once."""
    from personalclaw.triggers import reaper

    _quiet_hours(around_now=True)
    reaper.record_stopped_run(
        "clock:backup", started_at=time.time() - 30, restarting=True, base_dir=tmp_path
    )
    state = _state()
    _gateway(state)._record_boot_review({}, [], base_dir=tmp_path)

    (note,) = _notes(state)
    assert note["title"] == "Runs interrupted by a restart"
    assert note["kind"] == nk.RUN_REVIEW
    assert note["badge_only"] is True


def test_a_notice_that_cannot_be_sent_is_logged_where_it_is_seen(tmp_path, caplog):
    """Losing the notice leaves the card as the only trace, so the failure is a WARNING."""
    state = MagicMock()
    state.notify.side_effect = RuntimeError("the notification log is not writable")
    with caplog.at_level(logging.WARNING, logger="personalclaw.gateway"):
        _gateway(state)._record_boot_review(
            _boot_with_a_missed_slot(tmp_path), [], base_dir=tmp_path
        )
    assert state.notify.call_count == 1
    assert any(
        r.levelno == logging.WARNING and "missed-fire review" in r.getMessage()
        for r in caplog.records
    ), [r.getMessage() for r in caplog.records]


@pytest.mark.parametrize("key", ["cron/run_review"])
def test_the_restart_review_is_a_decision_quiet_hours_record(key):
    """What makes quiet hours record it rather than drop it, pinned on the registration."""
    source, kind = key.split("/")
    registered = nk.resolve_kind(source, kind)
    assert registered.key == key
    assert nk.kind_for_legacy(nk.RUN_REVIEW).key == key
    assert registered.decision and registered.attention
    assert registered.default_severity == nk.SEV_WARNING
    assert entity_routes._must_be_answered(registered)

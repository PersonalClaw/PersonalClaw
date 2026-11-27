"""A schedule's preview is the clock's own answer: its runs are the fires the clock arms.

Before it is saved, an automation's owner (and the agent that made it) sees its next runs, with
clock changes, skip dates, quiet hours and the spread said in words (`triggers.preview`). The
preview walks the steps the clock arms with, so for every cadence, zone and skip date its running
instants are the clock's successive fires, and nothing a second calculation could drift on.
"""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from personalclaw.instants import utc_iso
from personalclaw.triggers import arm
from personalclaw.triggers.models import Trigger
from personalclaw.triggers.preview import ALL_SKIPPED, ELAPSED, next_runs

NEW_YORK = "America/New_York"
LONDON = "Europe/London"

#: Wednesday 25 February 2026, 12:00 UTC: the moment every preview here is read at.
NOW = datetime(2026, 2, 25, 12, tzinfo=timezone.utc).timestamp()


def _clock(spec: dict, *, id: str = "", gates: dict | None = None, enabled: bool = True) -> Trigger:
    return Trigger(
        id=id, name="Morning summary", kind="clock", enabled=enabled, spec=spec, gates=gates or {}
    )


def _z(iso: str) -> str:
    return datetime.fromisoformat(iso).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")


def _clock_fires(trigger: Trigger, count: int) -> list[float]:
    """The clock's own successive fires: `arm.next_fire`, each from just after the one before."""
    fires, at = [], NOW
    for _ in range(count):
        at = arm.next_fire(trigger, now=at + 1 if fires else at)
        if at <= 0:
            break
        fires.append(at)
    return fires


_SPECS = [
    {"kind": "cron", "expr": "0 9 * * 1-5", "timezone": NEW_YORK},
    {"kind": "cron", "expr": "30 2 * * *", "timezone": NEW_YORK},
    {"kind": "cron", "expr": "30 1 * * *", "timezone": LONDON},
    {"kind": "cron", "expr": "*/20 * * * *", "timezone": "UTC"},
    {
        "kind": "cron",
        "expr": "0 18 * * 0",
        "timezone": LONDON,
        "skip_dates": ["2026-03-01", "2026-03-15"],
    },
    {
        "kind": "cron",
        "expr": "0 9 * * *",
        "timezone": NEW_YORK,
        "skip_dates": ["2026-02-26", "2026-02-27"],
    },
    {"kind": "interval", "interval_secs": 5400, "created_at": NOW - 4000, "timezone": NEW_YORK},
    {
        "kind": "interval",
        "interval_secs": 86400,
        "created_at": NOW - 600,
        "skip_dates": ["2026-02-27"],
        "timezone": "UTC",
    },
    {"kind": "at", "at": NOW + 7200, "timezone": LONDON},
]


@pytest.mark.parametrize(
    "spec", _SPECS, ids=lambda s: f"{s['kind']}-{s.get('expr', s.get('interval_secs', 'once'))}"
)
@pytest.mark.parametrize("jitter", [0, 300])
def test_the_preview_s_runs_are_the_clock_s_fires(spec, jitter) -> None:
    """For a saved automation (it has an id, so its spread is its own), the running items are the
    clock's successive fires, instant for instant."""
    trigger = _clock(
        {**spec, **({"jitter_secs": jitter} if jitter else {})}, id="clock:morning-summary"
    )
    preview = next_runs(trigger, now=NOW, count=5)
    assert preview.outcome == "ok", preview.problem
    running = [i.at for i in preview.items if i.runs]
    assert running == [utc_iso(f) for f in _clock_fires(trigger, len(running))]
    assert len(running) == (1 if spec["kind"] == "at" else 5)


def test_a_time_the_clocks_skip_says_when_it_runs_instead() -> None:
    march_6 = datetime(2026, 3, 6, 12, tzinfo=timezone.utc).timestamp()
    preview = next_runs(
        _clock({"kind": "cron", "expr": "30 2 * * *", "timezone": NEW_YORK}), now=march_6, count=5
    )
    (forward,) = [i for i in preview.items if i.note_kind == "clock_forward"]
    assert (
        forward.note
        == "02:30 does not happen on Sun 8 Mar 2026 (the clocks go forward), so it runs at 03:00."
    )
    assert _z(forward.at) == "2026-03-08T07:00Z" and forward.runs
    assert forward.wall == "Sun 8 Mar 2026, 03:00"


def test_a_time_the_clocks_show_twice_runs_once() -> None:
    late = datetime(2026, 10, 28, tzinfo=timezone.utc).timestamp()
    preview = next_runs(
        _clock({"kind": "cron", "expr": "30 1 * * *", "timezone": NEW_YORK}), now=late, count=6
    )
    back = [i for i in preview.items if i.note_kind == "clock_back"]
    assert [b.note for b in back] == [
        "01:30 happens twice on Sun 1 Nov 2026 (the clocks go back); it runs once, at the first."
    ]
    assert _z(back[0].at) == "2026-11-01T05:30Z"
    days = [i.wall.split(",")[0] for i in preview.items]
    assert len(days) == len(set(days)), "one run a day, the repeated night included"


def test_a_skip_date_is_a_skipped_run_not_a_gap() -> None:
    spec = {"kind": "cron", "expr": "0 9 * * *", "timezone": NEW_YORK, "skip_dates": ["2026-02-26"]}
    preview = next_runs(_clock(spec), now=NOW, count=3)
    assert [i.runs for i in preview.items] == [True, False, True, True]
    skipped = preview.items[1]
    assert skipped.note_kind == "skip_date"
    assert skipped.note == "Skipped: 26 Feb 2026 is one of its skip dates."
    assert skipped.wall == "Thu 26 Feb 2026, 09:00"


def test_quiet_hours_that_skip_say_so() -> None:
    """Quiet hours are read on this machine's clock, as the fire path reads them, so the window is
    set around the first hourly slot as this machine shows it."""
    first_slot = datetime(2026, 2, 25, 13, tzinfo=timezone.utc).timestamp()
    hour = datetime.fromtimestamp(first_slot).hour
    hours = f"{hour:02d}:00–{hour:02d}:30"
    gates = {"quiet_hours": {"start": f"{hour:02d}:00", "end": f"{hour:02d}:30"}}
    preview = next_runs(
        _clock({"kind": "cron", "expr": "0 * * * *", "timezone": "UTC"}, gates=gates),
        now=NOW,
        count=2,
    )
    assert [i.runs for i in preview.items] == [False, True, True]
    first = preview.items[0]
    assert first.note_kind == "quiet_hours"
    assert first.note == f"Skipped: {hour:02d}:00 is in its quiet hours ({hours})."


def test_a_long_quiet_stretch_lists_a_few_skipped_runs_then_the_runs() -> None:
    first_slot = datetime(2026, 2, 25, 12, 15, tzinfo=timezone.utc).timestamp()
    start = datetime.fromtimestamp(first_slot)
    end = datetime.fromtimestamp(first_slot + 4 * 3600)
    gates = {"quiet_hours": {"start": f"{start:%H:%M}", "end": f"{end:%H:%M}"}}
    preview = next_runs(
        _clock({"kind": "cron", "expr": "*/15 * * * *", "timezone": "UTC"}, gates=gates),
        now=NOW,
        count=3,
    )
    assert [i.runs for i in preview.items] == [False, False, False, True, True, True]
    assert _z(preview.items[3].at) == "2026-02-25T16:15Z"


def test_quiet_hours_over_every_run_never_run() -> None:
    gates = {"quiet_hours": {"start": "00:00", "end": "23:59"}}
    preview = next_runs(
        _clock({"kind": "cron", "expr": "0 9 * * *", "timezone": "UTC"}, gates=gates),
        now=NOW,
        count=2,
    )
    assert preview.outcome == "never"


def test_quiet_hours_that_catch_up_run_when_they_end() -> None:
    gates = {
        "quiet_hours": {"windows": [{"start": "00:00", "end": "23:59"}], "resolution": "catch_up"}
    }
    preview = next_runs(
        _clock({"kind": "cron", "expr": "0 9 * * *", "timezone": "UTC"}, gates=gates),
        now=NOW,
        count=1,
    )
    (item,) = preview.items
    assert (item.runs, item.note_kind) == (True, "quiet_hours")
    assert item.note == "In its quiet hours (00:00–23:59), so it runs when they end, at 23:59."
    assert (
        datetime.fromtimestamp(datetime.fromisoformat(item.at).timestamp()).strftime("%H:%M")
        == "23:59"
    )


def test_a_draft_s_spread_is_said_on_each_run() -> None:
    preview = next_runs(
        _clock({"kind": "cron", "expr": "0 9 * * *", "timezone": "UTC", "jitter_secs": 300}),
        now=NOW,
        count=2,
    )
    assert {i.note for i in preview.items} == {
        "It runs up to 5 minutes after this, at a time picked for this automation when it is saved."
    }
    strict = next_runs(
        _clock(
            {
                "kind": "cron",
                "expr": "0 9 * * *",
                "timezone": "UTC",
                "jitter_secs": 300,
                "strict": True,
            }
        ),
        now=NOW,
        count=2,
    )
    assert {i.note_kind for i in strict.items} == {""}


def test_an_elapsed_one_time_run_never_runs() -> None:
    preview = next_runs(_clock({"kind": "at", "at": NOW - 60, "timezone": "UTC"}), now=NOW)
    assert (preview.outcome, preview.problem, preview.items) == ("never", ELAPSED, ())


def test_a_one_time_run_on_a_skip_date_never_runs() -> None:
    spec = {
        "kind": "at",
        "at": datetime(2026, 2, 26, 15, tzinfo=timezone.utc).timestamp(),
        "timezone": "UTC",
        "skip_dates": ["2026-02-26"],
    }
    preview = next_runs(_clock(spec), now=NOW)
    assert (preview.outcome, preview.problem, preview.items) == ("never", ALL_SKIPPED, ())


def test_a_spec_the_save_refuses_is_refused_in_the_save_s_words() -> None:
    from personalclaw.triggers.tools import spec_error_refusal

    spec = {"kind": "cron", "expr": "99 99 * * *", "timezone": "UTC"}
    preview = next_runs(_clock(spec), now=NOW)
    assert preview.outcome == "refused"
    assert preview.problem == spec_error_refusal("clock", spec).text.removeprefix("Error:").strip()


def test_a_zone_that_does_not_exist_is_refused() -> None:
    preview = next_runs(
        _clock({"kind": "cron", "expr": "0 9 * * *", "timezone": "Mars/Olympus"}), now=NOW
    )
    assert preview.outcome == "refused" and "Mars/Olympus" in preview.problem


def test_an_automation_that_is_off_previews_what_it_does_once_on() -> None:
    off = _clock({"kind": "cron", "expr": "0 9 * * *", "timezone": "UTC"}, enabled=False)
    preview = next_runs(off, now=NOW, count=2)
    assert preview.outcome == "ok" and len(preview.items) == 2
    assert off.enabled is False, "the preview leaves the automation as it was"


def test_the_preview_names_its_zone_and_cadence() -> None:
    preview = next_runs(
        _clock({"kind": "cron", "expr": "0 9 * * 1-5", "timezone": NEW_YORK}), now=NOW, count=1
    )
    assert preview.zone == NEW_YORK
    assert preview.cadence and "9" in preview.cadence
    assert ZoneInfo(preview.zone)

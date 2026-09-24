"""A future one-shot appears on the week grid (issue 561).

`_project_one` ended at a guard whose comment justified dropping an **elapsed** one-shot — "`at` is
a single fire, and an elapsed one is not a forecast. Nothing to plot." — but the `return` was
unconditional. So a one-shot armed for Thursday contributed nothing, and the user saw an empty
Thursday with no sign their trigger existed. `at` is a first-class clock kind, not a legacy shape:
`triggers/models.py` lists it in `CLOCK_KINDS` and `triggers/arm.py` arms it.

Measured by calling the real `_project_one` (positive controls first, so a zero means something):

    cron  '0 9 * * *'                    ->   7 occurrences   ✅
    interval 3600s                       -> 167 occurrences   ✅
    at, two days inside the window       ->   0 occurrences   ← expected 1
    at, six days inside the window       ->   0 occurrences   ← expected 1
    at, elapsed                          ->   0 occurrences   ✅ (the guard's own reasoning)
    at, thirty days out                  ->   0 occurrences   ✅

The fix adds **no machinery**, because `cadence_next_fire` already IS the one-shot stepper: for
kind `at` it answers `at if at > now else 0.0`. Routing `at` through the same `next_after` seam the
cron and adaptive kinds use produces all four behaviours with no special case — which is why these
tests assert the three NEGATIVE cases as hard as the positive one. A fix that plotted an elapsed
one-shot, or one beyond the window, would be a worse bug than the silence it replaced: a forecast
that shows a fire that will never happen.

The frontend half rides in the same commit (`web/src/pages/triggers/WeekGridView.tsx`): its empty
state used to say "a one-shot is not projected here yet", and `weekGridEmptyStateCopy.test.ts`
pinned that string precisely so the projection could not ship without it.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Any

from personalclaw.dashboard.handlers import triggers as handlers
from personalclaw.triggers.models import CLOCK_KINDS


class _Clock:
    """The minimum a clock trigger needs to be projected. Deliberately not a real `Trigger`: this
    exercises `_project_one`, whose contract is the spec dict plus `next_fire_at`/`gates`."""

    def __init__(self, kind: str, spec: dict[str, Any], name: str = "t") -> None:
        self.id = "t1"
        self.name = name
        self.kind = "clock"
        self.enabled = True
        self.spec = {"kind": kind, **spec}
        self.gates: dict[str, Any] = {}
        self.next_fire_at = ""


def _window() -> tuple[datetime, float]:
    """A 7-day window starting on the hour, and `now` inside it."""
    start = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    return start, time.time()


def _count(trigger: Any, start: datetime, days: int = 7) -> int:
    rows, _truncated = handlers._project_one(trigger, start=start, days=days)
    return len(rows)


def test_at_is_a_first_class_clock_kind():
    """The premise. If `at` were a legacy shape the omission would be defensible."""
    assert "at" in CLOCK_KINDS


def test_a_one_shot_inside_the_window_plots_exactly_once():
    """🔑 The reported defect: a one-shot armed for a date inside the projected week."""
    start, now = _window()
    trigger = _Clock("at", {"at": now + 2 * 86400})
    rows, truncated = handlers._project_one(trigger, start=start, days=7)
    assert len(rows) == 1, "a one-shot inside the window contributed nothing"
    assert not truncated
    assert rows[0].at == trigger.spec["at"], "plotted at some other instant than the one armed"


def test_a_one_shot_late_in_the_window_still_plots():
    """A fire on the LAST day is the one an off-by-one bound would drop, and the one a user is
    most likely to be checking for."""
    start, now = _window()
    assert _count(_Clock("at", {"at": now + 6 * 86400}), start) == 1


def test_an_ELAPSED_one_shot_plots_nothing():
    """🪤 The guard's own reasoning, kept. A forecast that shows a fire which already happened —
    and can never happen again (`arm.cadence_next_fire` returns 0.0 for a past `at`) — is worse
    than the silence this fix replaces."""
    start, now = _window()
    assert _count(_Clock("at", {"at": now - 2 * 86400}), start) == 0


def test_a_one_shot_BEYOND_the_window_plots_nothing():
    """The window is the whole contract of a week grid."""
    start, now = _window()
    assert _count(_Clock("at", {"at": now + 30 * 86400}), start) == 0


def test_a_one_shot_with_no_instant_plots_nothing():
    """A malformed spec must not crash the projection: the week route serves every clock row, so
    one unarmable trigger would empty the whole grid."""
    start, _now = _window()
    assert _count(_Clock("at", {}), start) == 0
    assert _count(_Clock("at", {"at": 0}), start) == 0
    assert _count(_Clock("at", {"at": "not-an-epoch"}), start) == 0


def test_it_plots_once_and_not_once_per_day():
    """🪤 A stepper that failed to terminate would fill the whole week with the same fire. The
    loop stops because `cadence_next_fire(at)` answers 0.0 once `now == at`, which is the property
    that makes a one-shot expressible as a stepper at all."""
    start, now = _window()
    rows, truncated = handlers._project_one(_Clock("at", {"at": now + 3600}), start=start, days=7)
    assert len(rows) == 1
    assert not truncated, "the projection hit its cap — the stepper is not terminating"


def test_the_kinds_that_already_plotted_are_unchanged():
    """Vacuity floor and regression guard in one: these are the positive controls from the
    measurement, so a change that broke them would be caught here rather than in the field."""
    start, _now = _window()
    assert _count(_Clock("cron", {"expr": "0 9 * * *"}), start) == 7
    assert _count(_Clock("interval", {"interval_secs": 3600}), start) == 167
    assert _count(_Clock("cron", {"expr": "0 9 * * 1"}), start) == 1


def test_a_quiet_window_annotates_a_one_shot_rather_than_hiding_it():
    """One-shots inherit the grid's annotate-don't-filter rule, because they went through the
    shared projection rather than a bespoke path. A suppressed fire the user cannot see is
    exactly the confusion the week view exists to end."""
    start, now = _window()
    at = now + 2 * 86400
    moment = datetime.fromtimestamp(at, tz=timezone.utc)
    # 🪤 The zone is DECLARED, not left empty. `_resolve_zone("")` falls through to the app config
    # and then to SERVER-LOCAL, so a window computed in UTC would compare against whatever zone the
    # machine happens to be in — green in CI, red on a laptop, and for a reason that has nothing to
    # do with one-shots.
    trigger = _Clock("at", {"at": at, "timezone": "UTC"})
    # The dict form is the one `parse_windows` documents as "the common case". A `"HH:MM-HH:MM"`
    # string is DROPPED with an issue, which would make this test pass for the wrong reason by
    # annotating nothing at all.
    trigger.gates = {
        "quiet_hours": {
            "start": moment.strftime("%H:%M"),
            "end": (moment + timedelta(hours=1)).strftime("%H:%M"),
        }
    }
    rows, _truncated = handlers._project_one(trigger, start=start, days=7)
    assert len(rows) == 1, "a quiet window filtered the fire instead of annotating it"
    assert rows[0].suppressed_by, "the fire is inside quiet hours but carries no reason"


def test_the_empty_state_copy_no_longer_denies_one_shots():
    """The same-commit obligation, asserted from Python too.

    `weekGridEmptyStateCopy.test.ts` is the primary rail, but it only runs in the web job. The
    projection and the sentence that describes it live in different CI lanes, and a backend-only
    change is exactly the shape that would ship the contradiction.
    """
    from pathlib import Path

    view = (
        Path(__file__).resolve().parents[1]
        / "web"
        / "src"
        / "pages"
        / "triggers"
        / "WeekGridView.tsx"
    ).read_text()
    assert (
        "a one-shot is not projected here yet" not in view
    ), "the week grid now plots a one-shot, but its empty state still says it does not"
    assert "interval, cron and one-shot alike" in view

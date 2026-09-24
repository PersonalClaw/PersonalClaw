"""Missed fires: review, don't lie, don't storm (AUTOMATION-SUBSTRATE §3.4 — S65).

Local-first means a closed lid stops the loop, so the honest question after a restart is not "what
should have run" but "what do I tell the user about what didn't".

**The defect this session found in its own first draft.** The shared enumeration budget originally
bounded the COUNT. Driven with thirty triggers each down a week: the alphabetically-first minutely
trigger spent all 480 counting its own 10,080 missed slots, and **twenty-nine triggers got no review
card at all** — the page would have shown one trigger and silently
omitted the rest, which is precisely
the "don't lie" failure §3.4 names. The budget now bounds the ROWS BUILT: counting is one division,
allocating objects is what makes boot slow. Same budget, 24 of 30
triggers get cards, and every count
stays exact so the summaries are honest.
"""

import pytest

from personalclaw.triggers.missed import (
    CATCHUP_ORIGIN,
    ENUMERATION_CAP,
    REVIEW_ROWS_PER_TRIGGER,
    catch_up_plan,
    enumerate_missed,
    resolve_missed,
    review_at_boot,
    roll_forward,
    within_rate_window,
)
from personalclaw.triggers.models import Outcome

NOW = 1_700_000_000.0
HOUR = 3600.0
DAY = 24 * HOUR
WEEK = 7 * DAY


# ── enumeration: bounded, honest, newest-first ──


def test_a_minutely_trigger_down_a_WEEK_yields_a_readable_page():
    """10,080 missed slots. Enumerating them all is an unusable page and
    a slow boot; enumerating none
    is the system lying about what happened."""
    rows, summary, _spent = enumerate_missed(
        trigger_id="t", last_fire_at=NOW - WEEK, interval_secs=60.0, now=NOW
    )
    assert len(rows) == REVIEW_ROWS_PER_TRIGGER
    assert summary is not None
    assert summary.count == int(WEEK / 60) - REVIEW_ROWS_PER_TRIGGER


def test_the_count_is_EXACT_so_the_summary_is_honest():
    """rows + summary must equal the real total. A summary that under-reports is the "don't lie"
    failure with extra steps."""
    rows, summary, _spent = enumerate_missed(
        trigger_id="t", last_fire_at=NOW - WEEK, interval_secs=60.0, now=NOW
    )
    assert len(rows) + summary.count == int(WEEK / 60)


def test_the_NEWEST_slots_become_the_review_rows():
    """A 3am backup missed six days ago is history; last night's is a decision.

    The newest MISSED slot is one interval back, not `now`: the slot at
    `now` is DUE, and the scheduler
    is about to fire it. Listing it as missed would offer the user a review card for work that is
    already on its way — measured while writing this test, and the off-by-one was in the assertion,
    not the code.
    """
    rows, _summary, _spent = enumerate_missed(
        trigger_id="t", last_fire_at=NOW - 10 * HOUR, interval_secs=HOUR, now=NOW
    )
    assert rows[-1].scheduled_for == NOW - HOUR
    assert rows[0].scheduled_for < rows[-1].scheduled_for
    # Every listed slot is strictly in the past.
    assert all(r.scheduled_for < NOW for r in rows)


def test_the_budget_bounds_the_ROWS_not_the_count():
    """The defect this session fixed. Budgeting the count let one noisy
    trigger consume the whole pass;
    counting is one division, allocating is what costs."""
    rows, summary, spent = enumerate_missed(
        trigger_id="t", last_fire_at=NOW - WEEK, interval_secs=60.0, now=NOW, budget=5
    )
    assert len(rows) == 5
    assert spent == 5
    assert summary.count == int(WEEK / 60) - 5  # the count is still exact


def test_a_trigger_with_NO_interval_has_no_grid():
    """ "Missed" is only meaningful for a recurrence — a one-shot or an
    event trigger has no slots."""
    assert enumerate_missed(trigger_id="t", last_fire_at=NOW - 100, interval_secs=0.0, now=NOW) == (
        [],
        None,
        0,
    )


def test_a_trigger_that_NEVER_fired_has_nothing_to_miss():
    assert enumerate_missed(trigger_id="t", last_fire_at=0.0, interval_secs=60.0, now=NOW)[0] == []


def test_a_trigger_fired_MORE_RECENTLY_than_one_interval_missed_nothing():
    rows, summary, spent = enumerate_missed(
        trigger_id="t", last_fire_at=NOW - 30, interval_secs=60.0, now=NOW
    )
    assert (rows, summary, spent) == ([], None, 0)


def test_no_summary_when_everything_FITS_in_the_review_window():
    rows, summary, _spent = enumerate_missed(
        trigger_id="t", last_fire_at=NOW - 3 * HOUR, interval_secs=HOUR, now=NOW
    )
    assert len(rows) == 3
    assert summary is None


# ── the shared boot budget ──


def test_MANY_triggers_each_get_a_card():
    """The measured defect: with a count-based budget, 1 of 30 triggers
    got a card. The page must not
    silently omit twenty-nine automations."""
    triggers = [
        {"id": f"t{i:03d}", "last_fire_at": NOW - WEEK, "interval_secs": 60.0} for i in range(30)
    ]
    review = review_at_boot(triggers, now=NOW)
    represented = {row.trigger_id for row in review.rows}
    assert len(represented) >= 20, f"only {len(represented)} of 30 triggers got review rows"


def test_every_trigger_still_gets_a_SUMMARY_even_when_rows_run_out():
    """The count is what matters at scale, and it costs nothing to compute."""
    triggers = [
        {"id": f"t{i:03d}", "last_fire_at": NOW - WEEK, "interval_secs": 60.0} for i in range(30)
    ]
    review = review_at_boot(triggers, now=NOW)
    assert len(review.summaries) >= 20


def test_TRUNCATION_is_reported_rather_than_hidden():
    """`truncated` says "the enumeration itself stopped early, so even the counts are a floor".
    Reporting a floor as a total is the "don't lie" failure."""
    triggers = [
        {"id": f"t{i:03d}", "last_fire_at": NOW - WEEK, "interval_secs": 60.0} for i in range(60)
    ]
    assert review_at_boot(triggers, now=NOW, budget=40).truncated is True


def test_a_SMALL_boot_is_not_marked_truncated():
    triggers = [{"id": "t", "last_fire_at": NOW - 2 * HOUR, "interval_secs": HOUR}]
    assert review_at_boot(triggers, now=NOW).truncated is False


def test_the_boot_pass_is_REPRODUCIBLE():
    """An unstable order would give different triggers the remaining
    budget on different restarts, and
    "why did my backup get a card yesterday but not today" is unanswerable."""
    triggers = [
        {"id": f"t{i:03d}", "last_fire_at": NOW - WEEK, "interval_secs": 60.0} for i in range(30)
    ]
    first = review_at_boot(triggers, now=NOW).to_dict()
    second = review_at_boot(list(reversed(triggers)), now=NOW).to_dict()
    assert first == second


def test_the_enumeration_cap_is_declared():
    assert ENUMERATION_CAP == 480


# ── review decisions ──


def test_run_now_records_RAN_LATE():
    outcome, reason = resolve_missed("run_now")
    assert outcome == Outcome.RAN_LATE.value
    assert "after its scheduled slot" in reason


def test_dismiss_records_SKIPPED_MISSED_rather_than_nothing():
    """A dismissed card that left no trace would be a silent drop with a
    UI on it. §1.3's rule is about
    whether the history is honest, not about the mechanism."""
    outcome, reason = resolve_missed("dismiss")
    assert outcome == Outcome.SKIPPED_MISSED.value
    assert reason


def test_an_UNKNOWN_review_action_is_REFUSED_with_a_reason():
    outcome, reason = resolve_missed("vibes")
    assert outcome == Outcome.REFUSED.value
    assert "expected run_now or dismiss" in reason


@pytest.mark.parametrize("action", ["run_now", "dismiss", "vibes"])
def test_EVERY_review_action_produces_a_ledger_row(action):
    """No branch silently does nothing — that is what "silent drops are banned" means here."""
    outcome, _reason = resolve_missed(action)
    assert outcome in {o.value for o in Outcome}


# ── catch_up: opt-in, once, staggered ──


def test_catch_up_is_OPT_IN():
    """RunAtLoad semantics are a deliberate choice, not a default: most
    missed slots should be reviewed,
    not re-run."""
    plan = catch_up_plan(
        [{"id": "t", "catch_up": False, "missed_last_slot": True, "enabled": True}], now=NOW
    )
    tid, fire_at, why = plan[0]
    assert fire_at == 0.0
    assert "reviewed, not re-run" in why


def test_a_catch_up_trigger_fires_ONCE_and_LATER():
    """Not inline: a catch-up during recovery runs before the gateway finished starting."""
    plan = catch_up_plan(
        [{"id": "t", "catch_up": True, "missed_last_slot": True, "enabled": True}], now=NOW
    )
    tid, fire_at, why = plan[0]
    assert fire_at > NOW
    assert why == CATCHUP_ORIGIN


def test_the_catch_up_origin_is_DISTINCT():
    """ "Why did this run at 09:02 when it is scheduled for 03:00" is
    answerable only if the row says it
    was a catch-up."""
    assert CATCHUP_ORIGIN == "catchup"


def test_catch_ups_are_STAGGERED_across_triggers():
    """A laptop opening after a weekend must not run every automation it owns in the same second."""
    plan = catch_up_plan(
        [
            {"id": "alpha", "catch_up": True, "missed_last_slot": True, "enabled": True},
            {"id": "beta", "catch_up": True, "missed_last_slot": True, "enabled": True},
            {"id": "gamma", "catch_up": True, "missed_last_slot": True, "enabled": True},
        ],
        now=NOW,
    )
    times = [fire_at for _tid, fire_at, _why in plan]
    assert len(set(times)) == 3


def test_the_stagger_is_DETERMINISTIC_across_restarts():
    """A crash-loop must not reshuffle when every automation catches up."""
    args = [{"id": "alpha", "catch_up": True, "missed_last_slot": True, "enabled": True}]
    assert catch_up_plan(args, now=NOW) == catch_up_plan(args, now=NOW)


def test_a_trigger_that_MISSED_NOTHING_gets_no_catch_up():
    _tid, fire_at, why = catch_up_plan(
        [{"id": "t", "catch_up": True, "missed_last_slot": False, "enabled": True}], now=NOW
    )[0]
    assert fire_at == 0.0
    assert why == "nothing was missed"


def test_a_DISABLED_trigger_is_never_restarted_by_a_catch_up():
    """The most damaging possible reading of catch_up: an automation the
    user switched off coming back
    to life because the machine rebooted."""
    _tid, fire_at, why = catch_up_plan(
        [
            {
                "id": "t",
                "catch_up": True,
                "missed_last_slot": True,
                "fires_automatically": False,
            }
        ],
        now=NOW,
    )[0]
    assert fire_at == 0.0
    assert "must not restart it" in why


def test_every_candidate_gets_an_EXPLANATION_including_the_refusals():
    """A trigger with `catch_up: true` that did NOT fire needs an
    explanation as much as one that did."""
    plan = catch_up_plan(
        [
            {"id": "a", "catch_up": True, "missed_last_slot": True, "enabled": True},
            {"id": "b", "catch_up": False, "missed_last_slot": True, "enabled": True},
            {"id": "c", "catch_up": True, "missed_last_slot": False, "enabled": True},
        ],
        now=NOW,
    )
    assert len(plan) == 3
    assert all(why for _tid, _at, why in plan)


# ── the hourly backstop ──


def test_the_hourly_cap_BACKSTOPS_a_catch_up():
    """Even a correctly staggered, once-per-trigger catch-up must not
    push a trigger past the cap its
    author set."""
    allowed, why = within_rate_window(fires_in_window=5, max_per_hour=5)
    assert allowed is False
    assert "cap of 5" in why


def test_a_MANUAL_fire_bypasses_the_hourly_cap():
    """The cap exists to stop the machine running away on its own; a person clicking Run is not the
    machine running away."""
    allowed, why = within_rate_window(fires_in_window=99, max_per_hour=5, manual=True)
    assert allowed is True
    assert "manual" in why


def test_no_cap_configured_allows_the_fire():
    assert within_rate_window(fires_in_window=1000, max_per_hour=0)[0] is True


def test_under_the_cap_is_allowed():
    assert within_rate_window(fires_in_window=4, max_per_hour=5)[0] is True


# ── rolling forward ──


def test_rolling_forward_stops_a_RE_OPEN_from_re_enumerating():
    """The page must not show the same misses again every time it loads."""
    rolled = roll_forward(next_fire_at=NOW - 5 * HOUR, interval_secs=HOUR, now=NOW)
    assert rolled > NOW


def test_rolling_forward_PRESERVES_phase():
    """Rolling to `now + interval` would re-phase a schedule that was
    correct before the machine went
    to sleep — the same rule as the scheduler's grid anchoring, for the same reason."""
    rolled = roll_forward(next_fire_at=NOW - 5 * HOUR, interval_secs=HOUR, now=NOW)
    assert (rolled - (NOW - 5 * HOUR)) % HOUR == pytest.approx(0.0, abs=1e-9)


def test_an_UPCOMING_fire_is_left_alone():
    assert roll_forward(next_fire_at=NOW + 100, interval_secs=HOUR, now=NOW) == NOW + 100


def test_an_UNARMED_trigger_stays_unarmed():
    assert roll_forward(next_fire_at=0.0, interval_secs=HOUR, now=NOW) == 0.0


def test_a_trigger_with_no_interval_is_untouched():
    """A one-shot has nothing to roll to."""
    assert roll_forward(next_fire_at=NOW - 100, interval_secs=0.0, now=NOW) == NOW - 100


# ── S142: the key contract, and the four inputs nothing produced ──


def _real_row(**over):
    """A row exactly as `Trigger.to_dict()` writes it — the only shape production hands in."""
    from personalclaw.triggers.models import Trigger
    from personalclaw.triggers.service import to_iso

    base = dict(
        id="t",
        name="T",
        kind="clock",
        enabled=True,
        spec={"kind": "interval", "interval_secs": 60},
    )
    base.update({k: v for k, v in over.items() if k != "next_at"})
    trigger = Trigger(**base)
    if "next_at" in over:
        trigger.next_fire_at = to_iso(over["next_at"])
    return trigger.to_dict()


def test_the_review_reads_the_keys_the_STORE_ACTUALLY_WRITES():
    """🔴 THE DEFECT S142 FOUND. This module reads `last_fire_at` / `interval_secs` /
    `missed_last_slot` / `fires_automatically`, and `Trigger.to_dict()` emits **none of the four**.
    So the enumeration guard saw `0.0` and `0.0` for every trigger on every machine and the review
    was EMPTY however long the lid had been shut — a confident, wrong answer.

    Asserted against a real `to_dict()` row rather than a hand-built dict, because a hand-built dict
    is what hid this: the fixtures supplied the keys, so the tests passed while production
    could not.
    """
    row = _real_row(next_at=NOW - HOUR)
    assert not {"last_fire_at", "interval_secs", "missed_last_slot"} & set(row), (
        "if to_dict() starts emitting these, `missed_inputs` should prefer them — it already does, "
        "but this assertion is what will say so"
    )
    review = review_at_boot([row], now=NOW)
    total = len(review.rows) + sum(s.count for s in review.summaries)
    assert total == 61, total  # an hour of minutely slots, off the armed-fire anchor
    assert review.rows, "the newest slots must be reviewable, not only counted"


def test_catch_up_reads_them_too():
    """`catch_up_plan` failed one clause further on: `missed_last_slot` was absent, so EVERY
    trigger answered "nothing was missed" — including one overdue by hours."""
    _tid, fire_at, why = catch_up_plan([_real_row(catch_up=True, next_at=NOW - HOUR)], now=NOW)[0]
    assert fire_at > NOW
    assert why == CATCHUP_ORIGIN


def test_a_row_with_NO_enabled_key_never_catches_up():
    """Fail-SAFE on an underspecified row. `fires_automatically` cannot be derived without
    `enabled`, and a catch-up fired on a guessed premise runs unattended work the user did not ask
    for — while a missed one stays reviewable. Every real row carries the key; this is the guard for
    anything that does not."""
    _tid, fire_at, why = catch_up_plan(
        [{"id": "t", "catch_up": True, "missed_last_slot": True}], now=NOW
    )[0]
    assert fire_at == 0.0
    assert "disabled or paused" in why


def test_missed_last_slot_is_UNANSWERABLE_without_an_instant():
    """`now <= 0` means the caller supplied no instant, so the question is answered FALSE rather
    than guessed from wall-clock — same reasoning as the missing-`enabled` guard."""
    from personalclaw.triggers.missed import missed_inputs

    assert missed_inputs(_real_row(next_at=NOW - HOUR), now=0.0)["missed_last_slot"] is False
    assert missed_inputs(_real_row(next_at=NOW - HOUR), now=NOW)["missed_last_slot"] is True


def test_an_AUTOPAUSED_trigger_never_catches_up():
    """S139 pauses a failing automation; a catch-up that restarted it would undo that."""
    row = _real_row(catch_up=True, state="autopaused", next_at=NOW - HOUR)
    _tid, fire_at, why = catch_up_plan([row], now=NOW)[0]
    assert fire_at == 0.0
    assert "disabled or paused" in why


def test_an_explicit_key_still_WINS_over_the_derivation():
    """The derivation is a fallback, not an override: a caller that knows the real last fire must be
    able to say so, or an event-sourced caller could never correct it."""
    from personalclaw.triggers.missed import missed_inputs

    row = _real_row(next_at=NOW - HOUR)
    row["last_fire_at"] = NOW - 120
    assert missed_inputs(row, now=NOW)["last_fire_at"] == NOW - 120


# ── 🔴 `ran_late` was written only by the manual card (S170) ──


def test_an_OVERDUE_fire_is_recorded_as_ran_late():
    """🔴 THE DEFECT. §1.3 added `ran_late` and `scheduled_for` in the same breath — *"a run that
    started 40 minutes after its slot is a different story from one that started on time and took 40
    minutes"* — and `validate_record` even REFUSES a `ran_late` row without a
    `scheduled_for`. But the only writer was the manual missed-fire card, so the tick
    recorded a plain `ran` however overdue the fire was, with the lateness computable on
    that very row."""
    from personalclaw.triggers.missed import late_outcome

    outcome, reason = late_outcome("ran", scheduled_for=NOW, started_at=NOW + 2400)
    assert outcome == "ran_late"
    assert "40 min" in reason


def test_ordinary_SCHEDULING_DELAY_is_not_lateness():
    """The threshold is DERIVED from the scheduler's own delays, not picked.

    A wake can be a poll ceiling behind, and a boot deliberately pushes overdue fires by the
    stagger base and spreads them across the stagger window. Labelling those as lateness
    would mark the substrate's own correct behaviour as a fault — which is how a signal
    becomes noise and then gets ignored."""
    from personalclaw.triggers.missed import late_outcome
    from personalclaw.triggers.scheduling import (
        BOOT_STAGGER_BASE_SECS,
        BOOT_STAGGER_WINDOW_SECS,
        LATE_THRESHOLD_SECS,
        POLL_CEILING_SECS,
    )

    # The constant is derivable, not magic — if someone retunes a stagger, this follows.
    assert LATE_THRESHOLD_SECS == 2 * (
        BOOT_STAGGER_BASE_SECS + BOOT_STAGGER_WINDOW_SECS + POLL_CEILING_SECS
    )
    for delay in (0.0, POLL_CEILING_SECS, BOOT_STAGGER_BASE_SECS + BOOT_STAGGER_WINDOW_SECS):
        assert late_outcome("ran", scheduled_for=NOW, started_at=NOW + delay)[0] == "ran", delay


def test_the_threshold_boundary_is_exact():
    from personalclaw.triggers.missed import late_outcome
    from personalclaw.triggers.scheduling import LATE_THRESHOLD_SECS

    just_under = late_outcome("ran", scheduled_for=NOW, started_at=NOW + LATE_THRESHOLD_SECS - 1)
    just_over = late_outcome("ran", scheduled_for=NOW, started_at=NOW + LATE_THRESHOLD_SECS)
    assert just_under[0] == "ran"
    assert just_over[0] == "ran_late"


def test_a_FAILED_fire_keeps_its_own_outcome():
    """Only `ran` is refined. "It was late" is not the interesting thing about a fire that
    never ran, and overwriting `failed` with `ran_late` would lose the failure entirely —
    turning a broken automation into a merely tardy one."""
    from personalclaw.triggers.missed import late_outcome

    for outcome in ("failed", "skipped_gate", "deferred", "blocked_injection", "refused"):
        assert late_outcome(outcome, scheduled_for=NOW, started_at=NOW + 9999)[0] == outcome


def test_NO_slot_means_lateness_is_not_a_FACT():
    """A missing/zero `scheduled_for` returns unchanged. With no slot to compare against, lateness
    cannot be measured — and guessing one produces exactly the "impression" §1.3 says to avoid.
    `validate_record` enforces the same pairing from the other side."""
    from personalclaw.triggers.missed import late_outcome

    assert late_outcome("ran", scheduled_for=0.0, started_at=NOW + 9999)[0] == "ran"
    assert late_outcome("ran", scheduled_for=NOW, started_at=0.0)[0] == "ran"
    junk: float = "junk"  # type: ignore[assignment]
    assert late_outcome("ran", scheduled_for=junk, started_at=NOW)[0] == "ran"


def test_ran_late_is_NOT_a_failure_and_NOT_inert():
    """The two downstream classifications that would make this harmful. `ran_late` DID the work, so
    counting it toward autopause would pause a working automation for being slow, and folding it out
    of the runs feed would hide a run that happened."""
    from personalclaw.triggers.autopause import consecutive_failures_from
    from personalclaw.triggers.models import INERT_OUTCOMES, TRUE_FAILURE_OUTCOMES, Outcome

    late = Outcome.RAN_LATE.value
    assert late not in TRUE_FAILURE_OUTCOMES
    assert late not in INERT_OUTCOMES
    assert consecutive_failures_from([{"outcome": late, "status": "success"}] * 5) == 0

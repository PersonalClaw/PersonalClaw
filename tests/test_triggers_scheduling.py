"""Crash-safe scheduling discipline (AUTOMATION-SUBSTRATE §3.1 — S63).

§2's disposition is explicit that `schedule.py` is ABSORBED by rename, not
rewritten — so this session
layers the discipline onto the shipped mechanism rather than replacing it. Everything here is a pure
decision the service applies, which is what makes it assertable without a running gateway.

**The property measured first, because getting it wrong breaks migration day.** The shipped
`ScheduleService._jitter_offset` spreads jobs into stable id-derived slots. If
the trigger service used
a different algorithm, every migrated schedule would land in a different
sub-minute slot than the job
it came from — a silent re-phasing of every automation on the machine. The
parity test below asserts
bit-identical output against the real shipped function, for four ids including the empty one.

**The bug each rule prevents,** since a scheduling rule with no named failure is a rule nobody can
review:

* persist-before-execute → a crash between "decided to fire" and "fired" double-fires;
* recompute-from-completion → a run that overruns its interval re-fires instantly, forever;
* grid anchoring → that same recompute re-phases the schedule to whenever the overrun happened;
* boot stagger → a restart fires every automation at once;
* claim self-expiry → a killed process wedges a trigger permanently;
* revalidate-on-fire → a trigger disabled while the timer slept still fires
once, which reads as the
  off switch not working.
"""

import pytest

from personalclaw.triggers.scheduling import (
    BOOT_STAGGER_BASE_SECS,
    CLAIM_MAX_DURATION_SECS,
    POLL_CEILING_SECS,
    TIMER_CEILING_SECS,
    Claim,
    Dueness,
    boot_recovery,
    claim_fire,
    coalesce_wakes,
    is_due,
    jitter_offset,
    next_wake_delay,
    recompute_from_completion,
    revalidate,
)

NOW = 1_700_000_000.0


# ── jitter parity: the migration-day property ──


@pytest.mark.parametrize("trigger_id", ["job-1", "system:heartbeat:fts", "t-abc123", ""])
def test_jitter_is_BIT_IDENTICAL_to_the_shipped_scheduler(trigger_id):
    """A different algorithm would re-phase every migrated schedule into a
    different sub-minute slot —
    a silent change to when every automation on the machine runs."""
    from personalclaw.schedule import ScheduleService

    assert jitter_offset(trigger_id, 120.0) == pytest.approx(
        ScheduleService._jitter_offset(trigger_id, 120.0), abs=1e-9
    )


def test_jitter_is_DETERMINISTIC_across_calls():
    """A random offset re-rolls every fire, so two triggers can still collide on
    any given fire and a
    restart reshuffles everything. Stable slots are the point."""
    assert jitter_offset("t-1", 60.0) == jitter_offset("t-1", 60.0)


def test_different_ids_land_in_DIFFERENT_slots():
    assert jitter_offset("t-1", 60.0) != jitter_offset("t-2", 60.0)


def test_jitter_stays_INSIDE_the_window():
    for tid in ("a", "b", "c", "system:x:y"):
        assert 0.0 <= jitter_offset(tid, 60.0) < 60.0


def test_a_ZERO_window_yields_no_offset():
    """Guard for a caller that disabled jitter — a modulo by zero would be the alternative."""
    assert jitter_offset("t-1", 0.0) == 0.0


# ── dueness reads the PERSISTED fire time ──


def test_a_trigger_is_due_when_its_persisted_time_has_passed():
    due, why = is_due(next_fire_at=NOW - 1, now=NOW, fires_automatically=True)
    assert due is True
    assert why == Dueness.DUE.value


def test_NOT_ARMED_is_distinguished_from_NOT_YET():
    """The first means nothing computed a next fire — a bug, or a just-enabled trigger. The second
    means the schedule is working. Collapsing them hides the bug behind the normal case."""
    _d1, unarmed = is_due(next_fire_at=0.0, now=NOW, fires_automatically=True)
    _d2, waiting = is_due(next_fire_at=NOW + 60, now=NOW, fires_automatically=True)
    assert unarmed == Dueness.NOT_ARMED.value
    assert waiting == Dueness.NOT_YET.value


def test_a_trigger_that_does_not_fire_automatically_is_never_due():
    due, why = is_due(next_fire_at=NOW - 1, now=NOW, fires_automatically=False)
    assert due is False
    assert why == Dueness.DISABLED.value


def test_an_EXPIRED_trigger_is_refused_even_when_armed():
    """Auto-expiry exists so a user-created recurring automation needs a
    deliberate renewal. Honouring
    a stale `next_fire_at` past that date would defeat it."""
    due, why = is_due(next_fire_at=NOW - 1, now=NOW, fires_automatically=True, expires_at=NOW - 100)
    assert due is False
    assert why == Dueness.EXPIRED.value


def test_an_unexpired_trigger_with_an_expiry_still_fires():
    due, _why = is_due(
        next_fire_at=NOW - 1, now=NOW, fires_automatically=True, expires_at=NOW + 10_000
    )
    assert due is True


# ── the wake delay ──


def test_an_IDLE_machine_sleeps_the_poll_ceiling_not_zero():
    """A busy-loop is the worst possible answer to "nothing to do"."""
    assert next_wake_delay([], NOW) == POLL_CEILING_SECS


def test_the_ceiling_is_what_picks_up_an_EXTERNAL_edit():
    """An MCP tool in another process mutates the store; the 30s poll + mtime
    sync IS the propagation
    contract other surfaces rely on, so a far-future schedule must not sleep past it."""
    assert next_wake_delay([NOW + 10**9], NOW) == POLL_CEILING_SECS


def test_a_SOON_fire_is_slept_exactly():
    assert next_wake_delay([NOW + 5], NOW) == 5.0


def test_an_OVERDUE_fire_wakes_immediately_not_negatively():
    """A negative sleep raises; zero means "go now"."""
    assert next_wake_delay([NOW - 100], NOW) == 0.0


def test_the_delay_is_CLAMPED_to_the_timer_ceiling():
    """A 32-bit millisecond timer truncates one enormous sleep, so the scheduler clamps and re-ticks
    rather than trusting it."""
    assert next_wake_delay([NOW + TIMER_CEILING_SECS * 10], NOW) <= TIMER_CEILING_SECS


def test_UNARMED_triggers_are_ignored_when_choosing_the_delay():
    assert next_wake_delay([0.0, -1.0, NOW + 7], NOW) == 7.0


# ── recompute: from completion, anchored to the grid ──


def test_recompute_is_from_COMPLETION_not_the_missed_slot():
    """A run that takes 90s on a 60s interval would otherwise be due the instant
    it finishes, forever,
    and the machine never idles."""
    nxt = recompute_from_completion(interval_secs=60.0, created_at=1000.0, completed_at=1150.0)
    assert nxt > 1150.0


def test_recompute_ANCHORS_to_the_created_at_grid():
    """`completed_at + interval` re-phases the schedule to whenever the
    overrun happened: a job created
    to run on the hour drifts to :07 after one slow day and stays there."""
    # grid: 1000, 1060, 1120, 1180 — a completion at 1150 must land on 1180, not 1210.
    assert (
        recompute_from_completion(interval_secs=60.0, created_at=1000.0, completed_at=1150.0)
        == 1180.0
    )


def test_the_phase_SURVIVES_repeated_overruns():
    """The property that matters over days, not one cycle: every recompute
    lands back on the grid."""
    created, interval = 1000.0, 60.0
    for completed in (1150.0, 1275.0, 1400.5, 1999.9):
        nxt = recompute_from_completion(
            interval_secs=interval, created_at=created, completed_at=completed
        )
        assert (nxt - created) % interval == pytest.approx(0.0, abs=1e-9)
        assert nxt > completed


def test_an_on_time_completion_gets_the_NEXT_slot():
    assert (
        recompute_from_completion(interval_secs=60.0, created_at=1000.0, completed_at=1060.0)
        == 1120.0
    )


def test_a_ZERO_interval_is_not_a_schedule():
    """Guard rather than a division: a zero interval would be an infinite fire loop."""
    assert (
        recompute_from_completion(interval_secs=0.0, created_at=1000.0, completed_at=1100.0) == 0.0
    )


def test_a_MISSING_created_at_falls_back_to_completion():
    """A migrated row with no birth time still needs a grid; anchoring on completion is the only
    honest choice, and it is stable from then on."""
    nxt = recompute_from_completion(interval_secs=60.0, created_at=0.0, completed_at=1100.0)
    assert nxt == 1160.0


# ── boot recovery ──


def test_an_overdue_fire_is_PUSHED_not_fired_inline():
    """Firing overdue work during recovery is what makes a restart run every
    automation at once — and
    it would run before the gateway finished starting."""
    when, why = boot_recovery(next_fire_at=NOW - 5000, now=NOW, trigger_id="t", catch_up=False)
    assert when >= NOW + BOOT_STAGGER_BASE_SECS
    assert why == "missed_dropped"


def test_catch_up_is_RECORDED_but_still_staggered():
    """The plan's catch_up is "fire ONCE at boot/wake" — session 65 owns the
    exactly-once bookkeeping;
    recovery's job is only to make it survivable."""
    _when, why = boot_recovery(next_fire_at=NOW - 5000, now=NOW, trigger_id="t", catch_up=True)
    assert why == "caught_up_staggered"


def test_a_STILL_UPCOMING_fire_is_left_alone():
    """Re-arming a schedule that is still valid would re-phase it for no gain."""
    when, why = boot_recovery(next_fire_at=NOW + 500, now=NOW, trigger_id="t", catch_up=False)
    assert when == NOW + 500
    assert why == "still_upcoming"


def test_an_UNARMED_trigger_stays_unarmed():
    assert boot_recovery(next_fire_at=0.0, now=NOW, trigger_id="t", catch_up=False) == (
        0.0,
        "not_armed",
    )


def test_two_triggers_overdue_by_the_SAME_amount_do_not_land_together():
    """The thundering herd this exists to prevent."""
    a, _ = boot_recovery(next_fire_at=NOW - 100, now=NOW, trigger_id="alpha", catch_up=False)
    b, _ = boot_recovery(next_fire_at=NOW - 100, now=NOW, trigger_id="beta", catch_up=False)
    assert a != b


def test_the_stagger_is_REPRODUCIBLE_across_restarts():
    """Deterministic, so a crash-loop does not reshuffle every schedule on each restart."""
    first, _ = boot_recovery(next_fire_at=NOW - 100, now=NOW, trigger_id="alpha", catch_up=False)
    second, _ = boot_recovery(next_fire_at=NOW - 100, now=NOW, trigger_id="alpha", catch_up=False)
    assert first == second


# ── the fire claim (single flight) ──


def test_an_unheld_trigger_can_be_claimed():
    claim, refusal = claim_fire(None, trigger_id="t", holder="svc-1", now=NOW)
    assert refusal == ""
    assert claim.holder == "svc-1"


def test_a_HELD_claim_refuses_the_next_fire_under_skip():
    """The semantics autonudge already has for a mid-turn nudge, and the natural implementation of
    `overlap: skip`."""
    held = Claim(trigger_id="t", holder="svc-1", claimed_at=NOW)
    claim, refusal = claim_fire(held, trigger_id="t", holder="svc-2", now=NOW + 10, overlap="skip")
    assert claim is None
    assert "held by svc-1" in refusal


def test_PARALLEL_overlap_does_not_refuse():
    """The trigger opted into concurrent runs; a lock that refused anyway would make the setting
    inert."""
    held = Claim(trigger_id="t", holder="svc-1", claimed_at=NOW)
    claim, refusal = claim_fire(
        held, trigger_id="t", holder="svc-2", now=NOW + 10, overlap="parallel"
    )
    assert refusal == ""
    assert claim is not None


def test_QUEUE_refuses_like_skip_because_the_OUTCOME_carries_the_difference():
    """This function only says whether THIS fire may proceed; whether the
    caller drops or enqueues is
    a decision about the record, not the lock."""
    held = Claim(trigger_id="t", holder="svc-1", claimed_at=NOW)
    claim, _refusal = claim_fire(
        held, trigger_id="t", holder="svc-2", now=NOW + 10, overlap="queue"
    )
    assert claim is None


def test_an_EXPIRED_claim_never_refuses_anything():
    """The self-expiry at work: a killed process must not wedge the trigger forever."""
    stale = Claim(trigger_id="t", holder="dead", claimed_at=NOW - CLAIM_MAX_DURATION_SECS - 1)
    claim, refusal = claim_fire(stale, trigger_id="t", holder="svc-2", now=NOW)
    assert refusal == ""
    assert claim.holder == "svc-2"


def test_the_claim_ceiling_matches_the_TASK_LEASE_ceiling():
    """The same question — how long may one holder hold? — should not have two answers on one
    machine."""
    from personalclaw.workflows.pool import MAX_LEASE_SECS

    assert CLAIM_MAX_DURATION_SECS == MAX_LEASE_SECS


def test_a_claim_serializes_with_its_DERIVED_expiry():
    """A surface deciding whether a claim is stale should not have to re-derive the rule."""
    payload = Claim(trigger_id="t", holder="h", claimed_at=NOW).to_dict()
    assert payload["expires_at"] == NOW + CLAIM_MAX_DURATION_SECS


# ── coalescing ──


def test_due_triggers_are_COALESCED_into_one_wake():
    """The reason N triggers replacing one 60s heartbeat do not wake the laptop N times."""
    fires = {"a": NOW - 1, "b": NOW, "c": NOW + 600}
    assert coalesce_wakes(fires, NOW) == ["a", "b"]


def test_the_batch_order_is_STABLE():
    """An unstable order makes two runs of one batch interleave differently, and any bug in one of
    them intermittent."""
    fires = {"z": NOW, "a": NOW, "m": NOW}
    assert coalesce_wakes(fires, NOW) == coalesce_wakes(fires, NOW)
    assert coalesce_wakes(fires, NOW) == ["a", "m", "z"]


def test_earlier_fires_come_FIRST():
    fires = {"late": NOW, "early": NOW - 5}
    assert coalesce_wakes(fires, NOW) == ["early", "late"]


def test_UNARMED_triggers_are_never_in_a_batch():
    assert coalesce_wakes({"a": 0.0, "b": -1.0}, NOW) == []


def test_nothing_due_is_an_EMPTY_batch():
    assert coalesce_wakes({"a": NOW + 100}, NOW) == []


# ── revalidate on fire ──


def test_a_trigger_DISABLED_while_the_timer_slept_does_not_fire():
    """Reads as the off switch not working — the single most damaging bug an
    automation surface can
    have."""
    ok, why = revalidate(still_enabled=False, next_fire_at_at_arm=NOW, next_fire_at_now=NOW)
    assert ok is False
    assert "disabled" in why


def test_a_trigger_RESCHEDULED_while_the_timer_slept_does_not_fire():
    """Otherwise a user who moved a job to 9am also gets the 3am fire it was already armed for."""
    ok, why = revalidate(still_enabled=True, next_fire_at_at_arm=NOW, next_fire_at_now=NOW + 3600)
    assert ok is False
    assert "rescheduled" in why


def test_an_unchanged_trigger_proceeds():
    ok, why = revalidate(still_enabled=True, next_fire_at_at_arm=NOW, next_fire_at_now=NOW)
    assert ok is True
    assert why == ""


# ── the disposition table, checked against the tree ──


def test_EVERY_named_surface_still_EXISTS():
    """A markdown table cannot be checked against the code. This one can: a rename during the
    migration fails here instead of leaving a row pointing at nothing."""
    from personalclaw.triggers.disposition import missing_surfaces

    assert missing_surfaces() == []


def test_the_ABSORBED_surfaces_each_name_what_they_KEEP():
    """ "Absorbed" without a keeps-list is how a rewrite loses the semantics a rename would have
    kept — `schedule.py` alone has ten behaviours §2 says are preserved verbatim."""
    from personalclaw.triggers.disposition import absorbed

    for row in absorbed():
        assert row.keeps or row.note, f"{row.surface} says absorbed but names nothing preserved"


def test_the_schedule_machinery_keeps_its_LOAD_BEARING_behaviours():
    """Each of these fails silently if dropped: a rewritten jitter re-phases every schedule, a lost
    same-minute guard double-fires, a lost mtime sync stops picking up MCP-process edits."""
    from personalclaw.triggers.disposition import DISPOSITION

    row = next(r for r in DISPOSITION if r.module == "personalclaw.schedule")
    joined = " ".join(row.keeps)
    for behaviour in ("jitter", "same-minute", "mtime", "reaper", "fcntl"):
        assert behaviour in joined


def test_KEPT_WITH_DUTY_is_distinct_from_KEPT():
    """A kept surface is untouched; one that gains a duty needs an edit in this program. Collapsing
    them lets a required emission read as "nothing to do here" — and then the
    bus has no publishers.
    """
    from personalclaw.triggers.disposition import Verdict, gains_a_duty

    duties = gains_a_duty()
    assert duties
    assert all(r.verdict is Verdict.KEPT_WITH_DUTY for r in duties)
    modules = {r.module for r in duties}
    assert "personalclaw.fs_watch" in modules


def test_autonudge_absorption_is_marked_LAST():
    """Blocked on LOOPS-EVOLUTION Phase 4 — the loop engine rides autonudge as its tick
    engine, so absorbing it early would take the loops' clock away."""
    from personalclaw.triggers.disposition import DISPOSITION

    row = next(r for r in DISPOSITION if r.module == "personalclaw.autonudge")
    assert "LAST" in row.note
    assert "Phase 4" in row.note


def test_the_policy_layer_is_KEPT_not_absorbed():
    """`HookManager`'s declarative allow/deny rules are policy, not automation. Absorbing them would
    turn a synchronous permission check into an async run."""
    from personalclaw.triggers.disposition import DISPOSITION, Verdict

    rows = [r for r in DISPOSITION if "HookManager" in r.surface]
    assert rows and all(r.verdict is Verdict.KEPT for r in rows)

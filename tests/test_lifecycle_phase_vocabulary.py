"""`PP-16` "one status vocabulary": terminality is DERIVED from one shared lifecycle phase.

`LoopStatus` and `RunStatus` disagreed about the terminality of the SAME word — `failed` refused
any further transition on a run while a failed loop is a `resume` source — because "has it
stopped producing" and "may it still transition" were one frozenset each, maintained beside each
enum by hand. `LifecyclePhase` declares the first once for both nouns; each noun declares which
of its ended states remain resumable; terminality is the difference.

These rails exist so the asymmetry cannot be quietly tidied away. The one that matters most is
`test_a_failed_loop_can_still_resume`: it drives the real store, so moving `FAILED` out of
`RESUMABLE_ENDED_STATUSES` withdraws resumability from every failed loop and turns red here
rather than in a user's stalled campaign.
"""

from __future__ import annotations

import pytest

from personalclaw.loop import store
from personalclaw.loop.loop import (
    ACTIVE_STATUSES,
    ENDED_STATUSES,
    LOOP_PHASES,
    PRELAUNCH_STATUSES,
    RESUMABLE_ENDED_STATUSES,
    TERMINAL_STATUSES,
    Loop,
    LoopStatus,
)
from personalclaw.workflows.models import (
    ENDED_RUN_STATUSES,
    RESUMABLE_ENDED_RUN_STATUSES,
    RUN_PHASES,
    TERMINAL_RUN_STATUSES,
    LifecyclePhase,
    RunStatus,
)


@pytest.fixture(autouse=True)
def _tmp_config(monkeypatch, tmp_path):
    monkeypatch.setattr("personalclaw.loop.store.config_dir", lambda: tmp_path)
    return tmp_path


def _loop(**over) -> Loop:
    base = dict(
        id="",
        name="L",
        kind="goal",
        task="investigate the latency regression",
        project_id="p-1",
        kind_config={"goal_type": "open_ended", "granularity": "balanced"},
    )
    base.update(over)
    return store.create(Loop(**base))


# ── the vocabulary is exhaustive over both nouns ──────────────────────────────


@pytest.mark.parametrize(
    "enum_cls, phase_map, label",
    [(LoopStatus, LOOP_PHASES, "LoopStatus"), (RunStatus, RUN_PHASES, "RunStatus")],
)
def test_every_status_member_has_exactly_one_phase(enum_cls, phase_map, label):
    """A phase map that skips a member would silently drop it out of every derived set — a new
    status would be non-terminal, non-active and non-prelaunch at once. Exhaustive BOTH ways: a
    phantom key means a deleted member left its classification behind."""
    assert len(list(enum_cls)) > 0, f"vacuity floor: {label} has no members"
    missing = set(enum_cls) - set(phase_map)
    assert (
        not missing
    ), f"{label} members with no LifecyclePhase: {sorted(s.value for s in missing)}"
    phantom = set(phase_map) - set(enum_cls)
    assert not phantom, f"phase rows for non-members of {label}: {phantom}"
    for status, phase in phase_map.items():
        assert isinstance(phase, LifecyclePhase), f"{label}.{status.name} phase is not a phase"


def test_both_nouns_use_the_same_phase_vocabulary():
    """The point of the atom: ONE vocabulary, not two that happen to rhyme. Both maps draw from
    the same enum, and every phase is actually used by at least one noun (a phase nothing
    classifies into is a member of a vocabulary nobody speaks)."""
    used = set(LOOP_PHASES.values()) | set(RUN_PHASES.values())
    assert used == set(LifecyclePhase), (
        "declared LifecyclePhase members no noun classifies into: "
        f"{sorted(p.value for p in set(LifecyclePhase) - used)}"
    )


# ── terminality is derived, not declared ─────────────────────────────────────


@pytest.mark.parametrize(
    "ended, resumable, terminal, label",
    [
        (ENDED_STATUSES, RESUMABLE_ENDED_STATUSES, TERMINAL_STATUSES, "loop"),
        (
            ENDED_RUN_STATUSES,
            RESUMABLE_ENDED_RUN_STATUSES,
            TERMINAL_RUN_STATUSES,
            "run",
        ),
    ],
)
def test_terminal_is_ended_minus_resumable(ended, resumable, terminal, label):
    """Terminality must stay a DERIVATION. If someone re-declares one of these as a literal, the
    identity below is the first thing to break."""
    assert ended, f"vacuity floor: the {label} vocabulary has no ended state"
    assert (
        terminal == ended - resumable
    ), f"{label} terminality is no longer derived from its phases"
    assert resumable <= ended, f"{label} calls a NON-ended status resumable-from-ended"


def test_loop_derived_sets_partition_the_vocabulary():
    """Prelaunch / active / ended must cover every member exactly once. A member in none of them
    is invisible to every list filter and every guard at the same time — the defect class the
    frontend half of this atom already found three live instances of."""
    covered = PRELAUNCH_STATUSES | ACTIVE_STATUSES | ENDED_STATUSES
    assert covered == set(LoopStatus), (
        "LoopStatus members in no derived bucket: "
        f"{sorted(s.value for s in set(LoopStatus) - covered)}"
    )
    for a, b, name in [
        (PRELAUNCH_STATUSES, ACTIVE_STATUSES, "prelaunch/active"),
        (PRELAUNCH_STATUSES, ENDED_STATUSES, "prelaunch/ended"),
        (ACTIVE_STATUSES, ENDED_STATUSES, "active/ended"),
    ]:
        assert not (a & b), f"{name} overlap: {sorted(s.value for s in a & b)}"


# ── the FAILED asymmetry, stated as one paired fact ──────────────────────────


def test_failed_is_ended_for_both_nouns_but_terminal_for_only_the_run():
    """The atom's whole cost, pinned as ONE assertion so neither half can drift alone.

    `failed` ends both nouns. It is terminal only for the run, because a run is one attempt and
    a loop is a campaign of attempts. Collapsing these — in EITHER direction — is a behaviour
    change: making the loop's terminal would strand every failed loop, and making the run's
    resumable would put a run back in flight after `service.delete_run` was allowed to bin it.
    """
    assert LoopStatus.FAILED in ENDED_STATUSES
    assert RunStatus.FAILED in ENDED_RUN_STATUSES
    assert LoopStatus.FAILED not in TERMINAL_STATUSES, "a failed loop must stay resumable"
    assert RunStatus.FAILED in TERMINAL_RUN_STATUSES, "a failed run must stay terminal"
    assert RESUMABLE_ENDED_STATUSES == frozenset({LoopStatus.FAILED}), (
        "the loop's resumable-ended set is the one place `failed` diverges from the run's; "
        "changing its membership is an owner decision about resumability, not a tidy-up"
    )
    assert RESUMABLE_ENDED_RUN_STATUSES == frozenset(), "no ended run may be left"


def test_failed_is_not_counted_as_an_active_loop():
    """`FAILED` being ENDED is what keeps it out of the active badge — previously a hand-written
    comment on a hand-written literal. It is resumable but has no armed worker."""
    assert LoopStatus.FAILED not in ACTIVE_STATUSES
    assert ACTIVE_STATUSES, "vacuity floor: no active statuses at all"


# ── the behaviour the derivation is supposed to preserve ─────────────────────


def test_a_failed_loop_can_still_resume():
    """THE load-bearing rail. Driven through the real store, not the sets: a failed loop must be
    able to go back to RUNNING. Flip `FAILED` into terminality and this is the red."""
    loop = _loop()
    store.update_status(loop.id, LoopStatus.RUNNING)
    store.update_status(loop.id, LoopStatus.FAILED)
    resumed = store.update_status(loop.id, LoopStatus.RUNNING)
    assert resumed.status == LoopStatus.RUNNING.value


@pytest.mark.parametrize("terminal", sorted(TERMINAL_STATUSES, key=lambda s: s.value))
def test_no_loop_leaves_a_terminal_status(terminal):
    """The other side of the same guard: every DERIVED terminal member is actually refused, so
    the derivation and `store.update_status` cannot drift apart."""
    loop = _loop()
    store.update_status(loop.id, terminal)
    with pytest.raises(store.TransitionError):
        store.update_status(loop.id, LoopStatus.RUNNING)


@pytest.mark.parametrize("ended", sorted(ENDED_STATUSES, key=lambda s: s.value))
def test_arriving_at_an_ended_status_stamps_completed_at(ended):
    """`ENDED_STATUSES` replaced an anonymous inline tuple in `update_status`. Every member of
    the named set must still stamp, or the rename lost a status its end time."""
    loop = _loop()
    stamped = store.update_status(loop.id, ended)
    assert stamped.completed_at is not None, f"{ended.value} left completed_at unset"


def test_resuming_a_failed_loop_clears_its_completed_at():
    """The one observable cost of `ended` and `terminal` having been a single word: `FAILED`
    stamped `completed_at` and was still allowed to leave, so a resumed loop ran with a
    `started_at` LATER than the moment it supposedly finished. Leaving an ended state un-ends
    the loop."""
    loop = _loop()
    failed = store.update_status(loop.id, LoopStatus.FAILED)
    assert failed.completed_at is not None, "precondition: FAILED stamps an end time"
    resumed = store.update_status(loop.id, LoopStatus.RUNNING)
    assert resumed.completed_at is None, (
        "a resumed loop kept the completed_at its failure stamped — it is running and "
        "finished at the same time"
    )
    assert resumed.started_at is not None

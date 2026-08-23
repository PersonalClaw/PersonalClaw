"""One projection to tasks (PP-16) — the loop side and the run side must not each carry their
own answer to "has this task finished".

A loop and a `WorkflowRun` are the same work unit in two shapes, so a task status cannot mean
one thing to the loop's phase gate and another to the engine's projection. The loop side used to
carry its own string list; this rail is what keeps it from growing a second one back.

**Railed in both drift directions.** A new `TaskStatus` member with no ruling reds, and a
predicate that stops agreeing with the canonical `TERMINAL_STATUSES` reds. Both are the failure
mode that matters: a status the gate does not recognize is finished work the loop waits on
forever.

**The agreement assertions carry a vacuity floor.** "Both sides agree" is satisfied trivially by
two constant functions, so a rail that only compared them would assert nothing — the floor below
proves each predicate actually discriminates, and that the two are genuinely different notions
rather than one function under two names.
"""

from __future__ import annotations

from personalclaw.loop import tasks_link
from personalclaw.tasks.models import TERMINAL_STATUSES, TaskStatus
from personalclaw.workflows import materialize


def test_loop_side_and_run_side_are_one_vocabulary() -> None:
    """The whole clause, asserted directly: for EVERY task status, the loop side's predicate and
    the run side's give the same answer. Exhaustive over the enum rather than a sampled few, so a
    member added later cannot slip through with a divergent ruling.
    """
    for status in TaskStatus:
        assert tasks_link._is_resolved(status) == materialize.is_resolved(status), status
        assert tasks_link._is_done(status) == materialize.is_done(status), status


def test_resolved_matches_the_canonical_terminal_tuple() -> None:
    """The one vocabulary is the task graph's OWN, not a third dialect that happens to agree
    today. Derived-not-restated is the property under test: if someone re-lists the members here
    or in the projection, this reds the moment the two lists differ.
    """
    for status in TaskStatus:
        assert materialize.is_resolved(status) is (status in TERMINAL_STATUSES), status


def test_every_status_the_projection_mints_has_a_terminality_ruling() -> None:
    """`STATE_TO_STATUS` mints task statuses from engine states. Every status it can produce must
    be one the terminality predicates classify deliberately — a minted status the gate never
    accounted for is the concrete shape of "two projections".
    """
    minted = set(materialize.STATE_TO_STATUS.values())
    assert minted, "the projection table is empty — this rail would assert nothing"
    for status in minted:
        # A ruling exists (the predicate returns a real bool, not None-ish) for everything minted.
        assert isinstance(materialize.is_resolved(status), bool), status
        assert materialize.normalize_status(status) is status, status


def test_foreign_provider_vocabulary_is_normalized() -> None:
    """Tasks are written through the task façade, so a task read BACK can carry a provider's own
    status spelling. `TaskStatus` has no `completed` member and `Task.from_dict` coerces an
    unknown status to OPEN — so an un-normalized "completed" reads as work still to do.
    """
    assert materialize.is_done("completed") is True
    assert materialize.is_resolved("completed") is True
    # Case and surrounding whitespace are provider noise, not a different state. A case-sensitive
    # normalizer is a latent version of the very bug the alias row exists to prevent.
    assert materialize.is_done("DONE") is True
    assert materialize.is_resolved(" cancelled ") is True


def test_unknown_status_is_not_terminal() -> None:
    """The one direction of error a gate must never make. An unrecognized status defaulting to
    terminal would report unfinished work as complete and release a phase gate early; defaulting
    to non-terminal merely keeps the work visible.
    """
    for junk in ("", None, "bogus", "almost_done", object()):
        assert materialize.is_resolved(junk) is False, junk
        assert materialize.is_done(junk) is False, junk
        assert materialize.normalize_status(junk) is None, junk


# ── vacuity floor ────────────────────────────────────────────────────────────────────────────
# Everything above is an AGREEMENT rail, and agreement is the easiest property in the world to
# satisfy vacuously: two functions that both return False agree on every input. These three prove
# the rail is measuring something.


def test_vacuity_floor_predicates_actually_discriminate() -> None:
    """Neither predicate is constant. A constant `is_resolved` would satisfy every agreement
    assertion above while making the loop's phase gate either never close or close instantly.
    """
    resolved = {s for s in TaskStatus if materialize.is_resolved(s)}
    unresolved = {s for s in TaskStatus if not materialize.is_resolved(s)}
    assert resolved, "is_resolved is constant False — the agreement rails are vacuous"
    assert unresolved, "is_resolved is constant True — the agreement rails are vacuous"

    done = {s for s in TaskStatus if materialize.is_done(s)}
    assert done, "is_done is constant False — the agreement rails are vacuous"
    assert done != set(TaskStatus), "is_done is constant True — the agreement rails are vacuous"


def test_vacuity_floor_the_two_predicates_are_different_notions() -> None:
    """`is_done` is strictly narrower than `is_resolved`. If they ever became the same function,
    the agreement rails would still pass while a cancelled task started counting as an
    accomplishment — which is precisely what the loop's per-task completion check must not do.
    """
    done = {s for s in TaskStatus if materialize.is_done(s)}
    resolved = {s for s in TaskStatus if materialize.is_resolved(s)}
    assert (
        done < resolved
    ), f"is_done must be strictly narrower than is_resolved: {done} vs {resolved}"
    assert TaskStatus.CANCELLED in resolved - done


def test_vacuity_floor_the_loop_side_holds_no_logic_of_its_own() -> None:
    """The clean break, asserted rather than trusted: the loop side must DELEGATE, not keep a
    copy that currently happens to agree. A re-introduced private string list would pass every
    agreement rail above on today's members and drift on the next one — so this pins the
    delegation itself by making the run side lie and requiring the loop side to repeat the lie.
    """
    original = materialize.is_resolved
    try:
        materialize.is_resolved = lambda status: True  # type: ignore[assignment]
        assert tasks_link._is_resolved(TaskStatus.OPEN) is True, (
            "tasks_link._is_resolved did not follow materialize.is_resolved — it is carrying its "
            "own implementation again, which is the duplication PP-16 removed"
        )
    finally:
        materialize.is_resolved = original  # type: ignore[assignment]
    assert materialize.is_resolved(TaskStatus.OPEN) is False, "monkeypatch was not restored"

"""Tests for the project umbrella, truthful lifecycle and the Work board (WORK-CONTAINERS, S46).

The property this module exists for: **the board must never lie after a crash.** Two ways it can,
both tested here.

* Marking every stale `running` run aborted is the obvious boot sweep and it is wrong. An isolated
  run whose worktree survived the restart has recoverable work, and aborting it destroys that work
  while reporting success.
* A run whose record exists but which never got a concurrency slot is `queued`, not `working`.
Without
  the record-before-slot ordering the two are indistinguishable, and the board reports work in
  flight
  that has not begun.

The third property is that an expired claim is not a claim. A badge naming a holder who no longer
holds it tells the user the work is taken when it is free — worse than no badge.
"""

import pytest

from personalclaw.workflows.containers import (
    BOARD_ORDER,
    COLLAPSED_ORIGINS,
    DEFAULT_LEASE_SECS,
    LEDGERS,
    MAX_LEASE_SECS,
    UNATTENDED_ORIGINS,
    BoardState,
    Claim,
    Completeness,
    Substrate,
    attention_count,
    board_row,
    board_state_for,
    claim,
    collect_sections,
    group_board,
    ledger_entry,
    project_block,
    release,
    sweep_decision,
)
from personalclaw.workflows.models import OriginKind, RunOrigin, RunStatus, WorkflowRun


def run(
    run_id: str = "r1",
    *,
    status: RunStatus = RunStatus.RUNNING,
    started: bool = True,
    origin: OriginKind = OriginKind.MANUAL,
    project: str = "p-1",
) -> WorkflowRun:
    return WorkflowRun(
        id=run_id,
        workflow_name="deep-research",
        status=status,
        origin=RunOrigin(kind=origin),
        project_id=project,
        started_at="2026-08-02T00:00:00Z" if started else None,
    )


# ── the board is a projection, not a second source ──


def test_needs_input_is_pinned_first():
    """The only group where the run is stopped waiting on the person reading the board. Burying it
    under twelve working rows is how a run sits blocked overnight."""
    assert BOARD_ORDER[0] is BoardState.NEEDS_INPUT


def test_a_blocked_run_projects_to_needs_input():
    assert board_state_for(run(status=RunStatus.NEEDS_INPUT)) is BoardState.NEEDS_INPUT


def test_a_started_running_run_is_WORKING():
    assert board_state_for(run(status=RunStatus.RUNNING, started=True)) is BoardState.WORKING


def test_a_running_run_with_no_start_time_is_QUEUED():
    """This is what §5.2's record-before-slot ordering buys. Without the distinction the board
    reports work in flight that has not begun, and the user waits on nothing."""
    assert board_state_for(run(status=RunStatus.RUNNING, started=False)) is BoardState.QUEUED


def test_a_draft_run_is_queued_not_done():
    assert board_state_for(run(status=RunStatus.DRAFT)) is BoardState.QUEUED


def test_a_paused_run_is_SUSPENDED_and_resumable():
    row = board_row(run(status=RunStatus.PAUSED), now=100.0)
    assert row.state is BoardState.SUSPENDED
    assert row.resumable is True


def test_an_escalated_run_lands_in_REVIEW():
    """Escalated is not done — something is waiting on a judgement, and filing it under Done is how
    it never gets one."""
    assert board_state_for(run(status=RunStatus.ESCALATED)) is BoardState.REVIEW


@pytest.mark.parametrize("status", [RunStatus.COMPLETE, RunStatus.FAILED, RunStatus.CANCELLED])
def test_terminal_statuses_are_done(status):
    assert board_state_for(run(status=status)) is BoardState.DONE


# ── the origin rules have to key on the REAL enum ──


def test_the_collapse_rule_reads_the_run_origins_ENUM_VALUE():
    """Measured: `WorkflowRun.origin` is a `RunOrigin` object, not a string. Reading it with
    `str(...)` produced a dataclass repr, so every origin comparison silently failed — no run was
    ever collapsed or suppressed. Two inert rules from one wrong type read."""
    row = board_row(run(origin=OriginKind.SUBAGENT_TOOL), now=0.0)
    assert row.collapsed is True


def test_an_ordinary_run_is_not_collapsed():
    assert board_row(run(origin=OriginKind.MANUAL), now=0.0).collapsed is False


def test_every_collapse_and_suppress_value_EXISTS_in_the_enum():
    """An earlier version named `housekeeping` and `heartbeat`, neither of which is an
    `OriginKind` —
    a rule keyed on a value that can never occur is a rule that never fires, and nothing reports it.
    """
    valid = {kind.value for kind in OriginKind}
    assert COLLAPSED_ORIGINS <= valid
    assert UNATTENDED_ORIGINS <= valid


def test_a_blocked_run_wants_attention():
    assert board_row(run(status=RunStatus.NEEDS_INPUT), now=0.0).attention is True


def test_a_blocked_UNATTENDED_origin_run_does_not_raise_a_badge():
    """A run the system started on its own initiative raising a badge is attention the user never
    asked to spend, and a badge that fires for something unrequested trains them to ignore badges.
    """
    row = board_row(run(status=RunStatus.NEEDS_INPUT, origin=OriginKind.IDLE), now=0.0)
    assert row.attention is False


def test_a_collapsed_run_can_still_want_attention():
    """Collapsed is visual noise; attention is "someone is blocked". A subagent batch waiting on an
    approval is still waiting."""
    row = board_row(run(status=RunStatus.NEEDS_INPUT, origin=OriginKind.SUBAGENT_TOOL), now=0.0)
    assert row.collapsed is True
    assert row.attention is True


def test_the_count_pill_counts_only_real_attention():
    rows = [
        board_row(run("a", status=RunStatus.NEEDS_INPUT), now=0.0),
        board_row(run("b", status=RunStatus.NEEDS_INPUT, origin=OriginKind.IDLE), now=0.0),
        board_row(run("c", status=RunStatus.RUNNING), now=0.0),
    ]
    assert attention_count(rows) == 1


# ── grouping ──


def test_groups_come_back_in_board_order():
    rows = [
        board_row(run("a", status=RunStatus.COMPLETE), now=0.0),
        board_row(run("b", status=RunStatus.NEEDS_INPUT), now=0.0),
    ]
    assert [g["state"] for g in group_board(rows)] == ["needs_input", "done"]


def test_an_empty_group_is_OMITTED():
    """ "Suspended (0)" spends a heading on the absence of a problem, and six such headings push the
    rows the user came for below the fold."""
    groups = group_board([board_row(run("a", status=RunStatus.COMPLETE), now=0.0)])
    assert [g["state"] for g in groups] == ["done"]


def test_a_group_reports_its_own_attention_count():
    rows = [
        board_row(run("a", status=RunStatus.NEEDS_INPUT), now=0.0),
        board_row(run("b", status=RunStatus.NEEDS_INPUT, origin=OriginKind.IDLE), now=0.0),
    ]
    group = group_board(rows)[0]
    assert group["count"] == 2
    assert group["attention"] == 1


def test_an_empty_board_groups_to_nothing():
    assert group_board([]) == []


# ── the boot sweep checks the SUBSTRATE first ──


def test_an_isolated_run_whose_worktree_SURVIVED_is_suspended_not_aborted():
    """The measurement §5.2 turns on. Aborting it destroys recoverable work and reports success."""
    decision = sweep_decision(run(status=RunStatus.RUNNING), Substrate(kind="worktree", alive=True))
    assert decision.board_state is BoardState.SUSPENDED
    assert decision.status is RunStatus.PAUSED
    assert decision.resumable is True
    assert "survived" in decision.reason


def test_an_isolated_run_whose_substrate_DIED_is_honestly_aborted():
    decision = sweep_decision(
        run(status=RunStatus.RUNNING), Substrate(kind="container", alive=False)
    )
    assert decision.status is RunStatus.CANCELLED
    assert decision.resumable is False
    assert "gone" in decision.reason


def test_an_INLINE_run_can_never_be_suspended():
    """Its substrate IS the process, so it cannot have survived a restart. Reporting it as suspended
    would offer a Resume that cannot work — an affordance that fails is worse than none."""
    decision = sweep_decision(run(status=RunStatus.RUNNING), Substrate(kind="inline", alive=True))
    assert decision.board_state is BoardState.DONE
    assert decision.reason == "server restarted"
    assert decision.resumable is False


@pytest.mark.parametrize("status", [RunStatus.COMPLETE, RunStatus.FAILED, RunStatus.CANCELLED])
def test_a_terminal_run_is_left_ALONE_by_the_sweep(status):
    """Re-deciding a completed run would let a boot sweep overwrite a real outcome with an inferred
    one — the board would then disagree with the run's own record."""
    decision = sweep_decision(run(status=status), Substrate(kind="worktree", alive=True))
    assert decision.status is status
    assert "left untouched" in decision.reason


def test_the_sweep_decision_states_its_REASON():
    """A run that silently changed state at boot is a support question; one marked with "server
    restarted" is legible."""
    assert sweep_decision(run(), Substrate()).reason


# ── claim leases ──


def test_a_free_claim_is_granted():
    lease, why = claim("session-a", now=1000.0)
    assert lease is not None and why == ""
    assert lease.holder == "session-a"
    assert lease.expires_at == 1000.0 + DEFAULT_LEASE_SECS


def test_a_held_claim_is_refused_with_WHO_holds_it():
    """ "Someone else has this" is a normal outcome a board renders, not an error a worker crashes
    on — and a refusal that does not name the holder leaves a stuck claim mysterious."""
    held = Claim(holder="session-a", expires_at=2000.0)
    lease, why = claim("session-b", now=1000.0, existing=held)
    assert lease is None
    assert "session-a" in why


def test_the_SAME_holder_renews_rather_than_being_refused():
    """A worker that lost its in-memory state and re-claimed its own work would otherwise be locked
    out of it until the TTL expired — a self-inflicted stall."""
    held = Claim(holder="session-a", expires_at=2000.0, taken_at=1000.0)
    lease, why = claim("session-a", now=1500.0, existing=held)
    assert lease is not None and why == ""
    assert lease.renewals == 1
    assert lease.taken_at == 1000.0


def test_an_EXPIRED_claim_does_not_block_a_new_holder():
    """An indefinite lease turns one dead worker into a permanently stuck task."""
    stale = Claim(holder="session-a", expires_at=500.0)
    lease, why = claim("session-b", now=1000.0, existing=stale)
    assert lease is not None and why == ""
    assert lease.holder == "session-b"


def test_the_ttl_is_capped():
    lease, _ = claim("s", now=0.0, ttl=999_999)
    assert lease.expires_at == MAX_LEASE_SECS


def test_a_zero_ttl_still_produces_a_usable_lease():
    """A zero-length lease is an immediately-expired one, which would let two workers both believe
    they hold it."""
    lease, _ = claim("s", now=0.0, ttl=0)
    assert lease.expires_at > 0.0


def test_an_anonymous_claim_is_refused():
    assert claim("", now=0.0) == (None, "no holder")


def test_only_the_holder_may_RELEASE():
    """A release that let anyone drop anyone's claim would make the lease advisory in the one
    direction that matters — a second worker could steal work mid-execution by releasing first."""
    held = Claim(holder="session-a", expires_at=2000.0)
    still_held, why = release(held, "session-b")
    assert still_held is held
    assert "session-a" in why


def test_the_holder_can_release():
    held = Claim(holder="session-a", expires_at=2000.0)
    assert release(held, "session-a") == (None, "")


def test_releasing_nothing_is_not_an_error():
    assert release(None, "session-a") == (None, "")


def test_an_expired_claim_is_not_RENDERED_on_the_board():
    """A badge naming a holder who no longer holds it tells the user the work is taken when it is
    free, which is worse than no badge at all."""
    stale = Claim(holder="session-a", expires_at=500.0)
    assert board_row(run(), claim_record=stale, now=1000.0).claim is None


def test_a_live_claim_IS_rendered():
    live = Claim(holder="session-a", expires_at=2000.0)
    assert board_row(run(), claim_record=live, now=1000.0).claim is live


# ── per-section isolation in the /work aggregation ──


def test_one_failing_source_degrades_ONE_section():
    """Five heterogeneous sources fail independently. A single try/catch around the aggregate would
    let a stale legacy-loop reader take down the run list — and the whole first paint."""

    def boom():
        raise RuntimeError("legacy loop store is unreachable")

    sections, completeness = collect_sections(
        {"runs": lambda: [{"id": "r1"}], "loops": boom}, now=5.0
    )
    by_name = {s["name"]: s for s in sections}
    assert by_name["runs"]["status"] == "ok"
    assert by_name["runs"]["items"] == [{"id": "r1"}]
    assert by_name["loops"]["status"] == "error"
    assert completeness is Completeness.PARTIAL


def test_a_failed_section_reports_WHAT_failed():
    sections, _ = collect_sections({"loops": lambda: (_ for _ in ()).throw(ValueError("bad row"))})
    assert "bad row" in sections[0]["error"]


def test_all_sources_failing_is_reported_as_ERROR_not_partial():
    """ "Partial" on a board with nothing on it would read as "there is not much work", which is a
    claim about the user's work rather than about the failure."""

    def boom():
        raise RuntimeError("x")

    _sections, completeness = collect_sections({"a": boom, "b": boom})
    assert completeness is Completeness.ERROR


def test_a_fully_loaded_board_is_COMPLETE():
    _sections, completeness = collect_sections({"runs": lambda: []})
    assert completeness is Completeness.COMPLETE


def test_every_section_carries_a_loadedAt_stamp():
    """Slow sections render as skeletons, and a skeleton with no stamp cannot be told from one that
    loaded and was empty."""
    sections, _ = collect_sections({"runs": lambda: []}, now=42.0)
    assert sections[0]["loadedAt"] == 42.0


def test_a_plain_list_source_is_accepted():
    sections, _ = collect_sections({"runs": [{"id": "r1"}]})
    assert sections[0]["items"] == [{"id": "r1"}]


def test_no_sources_is_complete_rather_than_an_error():
    assert collect_sections({})[1] is Completeness.COMPLETE


# ── the project block ──


def test_the_three_fields_stay_DISTINGUISHABLE():
    """Brief is what/why, overview is current state, instructions are procedure. An agent that
    cannot tell the goal from the current state treats a finished sub-goal as still open."""
    block = project_block(brief="ship the thing", overview="auth is done", instructions="use uv")
    assert block.index("BRIEF") < block.index("OVERVIEW") < block.index("INSTRUCTIONS")
    assert "current state" in block


def test_an_empty_project_produces_NO_block():
    """An empty labelled block reads as "this project has no goal", which is a claim about the
    project rather than about the data."""
    assert project_block(brief="", overview="", instructions="") == ""


def test_a_partially_filled_project_omits_the_empty_labels():
    block = project_block(brief="ship it", overview="", instructions="")
    assert "BRIEF" in block
    assert "OVERVIEW" not in block


# ── the wayfinder ledgers ──


def test_the_three_ledgers_each_state_what_they_are_FOR():
    """The fog bucket's promotion test travels with the mechanism, or it becomes a place things go
    to be forgotten."""
    assert set(LEDGERS) == {"decisions", "fog", "out_of_scope"}
    assert "promote" in LEDGERS["fog"]
    assert "index, not a store" in LEDGERS["decisions"]


def test_an_unknown_ledger_is_REFUSED():
    """A typo'd kind silently creating a fourth ledger would split the decisions log in two, and
    neither half would be complete."""
    with pytest.raises(ValueError):
        ledger_entry("milestones", "x")


def test_an_out_of_scope_entry_always_carries_a_reason():
    """One without a reason is indistinguishable from something that was forgotten, and the whole
    value of the bucket is that revisiting it later is cheap because the reasoning is recorded."""
    entry = ledger_entry("out_of_scope", "mobile app")
    assert entry["reason"] == "no reason recorded"


def test_a_stated_reason_is_kept_verbatim():
    entry = ledger_entry("out_of_scope", "mobile app", reason="no iOS device to test on")
    assert entry["reason"] == "no iOS device to test on"


def test_a_decisions_entry_carries_its_link():
    entry = ledger_entry("decisions", "chose sqlite", link="run-42")
    assert entry["link"] == "run-42"


def test_a_fog_entry_needs_no_reason():
    """A fog entry is a question nobody can state precisely yet. Demanding a reason for it would be
    demanding the precision the bucket exists to defer."""
    assert "reason" not in ledger_entry("fog", "how should retries interact with the cache?")

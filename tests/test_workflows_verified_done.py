"""Tests for verified done, the actor matrix and cascade-fail (TASKS-SOPS §1, S56).

The module's whole subject is that the actor who did the work is not the actor who judges it. So the
tests are mostly refusals, and three of them were measured against real machinery rather than
assumed:

**The tristate is real.** `loop/gates.run_verify_command` returns `None` for a missing binary
AND for a
command the safety screen refuses — verified by running both. Reading `None` as a pass would
make a
broken check indistinguishable from a passing one, and the broken one is silent.

**A criterion-free task must stay completable.** Gating on `Verdict.passed` alone blocked it,
because an
empty verdict is `None` — so every task without a criterion would have become permanently
blocked, and
most tasks have no criterion. Checked against the existing `Task.can_mark_complete` seam, which
says a
task with no exit criteria is freely completable.

**The cascade follows BINDINGS, not the tree.** Driven on a graph with a later sibling reading the
failed node's output — the shape a tree walk misses, and the one where an unblocked task is most
misleading.
"""

import asyncio

import pytest

from personalclaw.tasks.models import Task, TaskStatus
from personalclaw.workflows.verified_done import (
    AGENT_BLOCKED_KINDS,
    ALLOWED_TARGETS,
    CASCADE_BLOCKED_KIND,
    COMPLETION_SECTIONS,
    HEARTBEAT_STALE_MINS,
    PROJECTION_PAUSING_STATUSES,
    UNCLAIMED_STALE_HOURS,
    Actor,
    Check,
    CheckKind,
    CheckResult,
    Verdict,
    cascade_blocked,
    cascade_cleared,
    coalesce_started,
    completion_record,
    criterion_is_irreversible,
    done_without_evidence,
    evaluate_file_phrase,
    evidence_entry,
    may_transition,
    parse_criterion,
    project_verified_status,
    sticky_cancel,
    sweep,
)

HOUR = 3600.0
NOW = 1_700_000_000.0


# ── the tristate is real ──


def test_the_REAL_verify_machinery_returns_a_tristate():
    """Driven against `loop/gates.run_verify_command` rather than assumed. Both `None`
    cases matter: a
    missing binary and a command the safety screen refuses are indistinguishable from a failing
    check
    unless the tristate is honored."""
    from personalclaw.loop.gates import run_verify_command

    async def probe():
        return (
            await run_verify_command("true", None),
            await run_verify_command("false", None),
            await run_verify_command("definitely-not-a-binary-xyz", None),
            await run_verify_command("rm -rf /", None),
        )

    ok, failed, missing, refused = asyncio.run(probe())
    assert ok is True
    assert failed is False
    assert missing is None, "a missing binary must be 'can't tell', not a failure"
    assert refused is None, "a refused command must be 'can't tell', not a failure"


def test_an_UNRUNNABLE_check_is_not_a_pass():
    verdict = Verdict(results=[CheckResult("command", True), CheckResult("command", None)])
    assert verdict.passed is None


def test_UNRUNNABLE_wins_over_failed():
    """ "One check failed and one could not run" is a criterion nobody has evaluated. Calling it a
    failure would send the user after the wrong problem."""
    verdict = Verdict(results=[CheckResult("command", False), CheckResult("command", None)])
    assert verdict.passed is None


def test_every_check_must_pass_for_a_pass():
    """Not a threshold: an acceptance criterion with a failed check has not been met, and a 0.8
    score is
    not "mostly done" — it is one unmet requirement. The scoring exists for the report."""
    verdict = Verdict(results=[CheckResult("command", True), CheckResult("command", False)])
    assert verdict.passed is False
    assert 0 < verdict.score < 1


# ── pass-state gating ──


def test_a_claimed_completion_with_a_FAILED_criterion_projects_BLOCKED():
    """The worker's claim is an input, not the answer."""
    status, kind = project_verified_status(Verdict(results=[CheckResult("command", False)]))
    assert status is TaskStatus.BLOCKED
    assert kind == "needs_input"


def test_an_UNRUNNABLE_criterion_blocks_with_CAPABILITY():
    """The check needs something the environment lacks — a different problem from the work being
    wrong,
    and it points at a different fix."""
    status, kind = project_verified_status(Verdict(results=[CheckResult("command", None)]))
    assert status is TaskStatus.BLOCKED
    assert kind == "capability"


def test_a_PASSING_criterion_projects_done():
    status, kind = project_verified_status(Verdict(results=[CheckResult("command", True)]))
    assert status is TaskStatus.DONE
    assert kind == ""


def test_a_CRITERION_FREE_task_stays_completable():
    """Measured: gating on `Verdict.passed` alone blocked it, because an empty verdict is `None` —
    so
    EVERY criterion-free task would have become permanently blocked, and most tasks have no
    criterion.
    Gating something because nobody asked for a check is the inverse of the point."""
    status, kind = project_verified_status(Verdict())
    assert status is TaskStatus.DONE
    assert kind == ""


def test_the_gating_agrees_with_the_EXISTING_exit_criteria_seam():
    """`Task.can_mark_complete` already says a task with no exit criteria is freely completable. Two
    seams disagreeing about the same question would make completability depend on which one ran."""
    assert Task(id="t", title="x").can_mark_complete() is True
    assert project_verified_status(Verdict())[0] is TaskStatus.DONE


def test_a_non_done_claim_passes_through_unjudged():
    """Only a claimed COMPLETION needs verifying. Running a criterion to decide whether something is
    in-progress would spend a subprocess to answer a question nobody asked."""
    status, kind = project_verified_status(
        Verdict(results=[CheckResult("command", False)]), claimed=TaskStatus.IN_PROGRESS
    )
    assert status is TaskStatus.IN_PROGRESS
    assert kind == ""


def test_the_engines_flip_is_IRREVERSIBLE():
    """Re-evaluating would make "done" depend on when you asked."""
    assert criterion_is_irreversible() is True


# ── the actor matrix ──


def test_an_AGENT_cannot_mark_its_own_task_done():
    """The worker-self-report hole, at the task level. Without this, the tool an agent uses to
    report a
    problem is also the tool it uses to declare victory."""
    ok, why = may_transition(Actor.AGENT, TaskStatus.DONE)
    assert ok is False
    assert "cannot mark its own task done" in why


def test_the_refusal_tells_the_agent_what_to_do_INSTEAD():
    """A refusal that does not say what to do reads as the feature being broken."""
    _ok, why = may_transition(Actor.AGENT, TaskStatus.DONE)
    assert "let" in why.lower() and "check" in why.lower()


def test_an_AGENT_cannot_claim_IN_PROGRESS():
    """A claim state, not a proposal: `in_progress` is set by engine dispatch, because it means
    "this is
    being worked on now" and only the dispatcher knows."""
    assert may_transition(Actor.AGENT, TaskStatus.IN_PROGRESS)[0] is False


def test_an_AGENT_may_PROPOSE_a_block():
    ok, why = may_transition(Actor.AGENT, TaskStatus.BLOCKED, blocked_kind="needs_input")
    assert ok is True
    assert why == ""


@pytest.mark.parametrize("kind", sorted(AGENT_BLOCKED_KINDS))
def test_an_agent_may_report_each_allowed_blocked_kind(kind):
    assert may_transition(Actor.AGENT, TaskStatus.BLOCKED, blocked_kind=kind)[0] is True


def test_an_AGENT_may_not_file_its_own_failure_as_TRANSIENT():
    """That would be requesting its own retry — a worker deciding it deserves another attempt."""
    ok, why = may_transition(Actor.AGENT, TaskStatus.BLOCKED, blocked_kind="transient")
    assert ok is False
    assert "retry" in why


def test_an_agent_block_with_NO_kind_is_refused():
    """An unspecified block is a worker saying "something is wrong" with
    nothing actionable in it."""
    assert may_transition(Actor.AGENT, TaskStatus.BLOCKED, blocked_kind="")[0] is False


def test_the_ENGINE_may_record_any_outcome():
    """It observed the work. Restricting it would mean an engine that saw a failure could not record
    one."""
    for status in TaskStatus:
        assert may_transition(Actor.ENGINE, status)[0] is True


def test_a_USER_may_not_SKIP_a_task():
    """A skip is a routing decision the run makes. A user who wants work skipped asks the run
    (`workflow_skip`), so the board and the run agree afterwards."""
    assert may_transition(Actor.USER, TaskStatus.SKIPPED)[0] is False


def test_a_USER_may_complete_a_STANDALONE_task():
    assert may_transition(Actor.USER, TaskStatus.DONE, managed=False)[0] is True


def test_a_USER_may_not_write_a_MANAGED_task():
    """Two writers on one status field produce a board that disagrees with the run it shows."""
    ok, why = may_transition(Actor.USER, TaskStatus.DONE, managed=True)
    assert ok is False
    assert "workflow_skip" in why or "workflow_rewind" in why


def test_the_ENGINE_may_still_write_a_managed_task():
    """It is the owner. A guard that blocked the engine would freeze every managed task."""
    assert may_transition(Actor.ENGINE, TaskStatus.DONE, managed=True)[0] is True


def test_the_matrix_is_per_ACTOR():
    """A pair table would be 3×6×6 entries mostly repeating one rule, and the rule that matters is
    "may this actor claim this outcome"."""
    assert set(ALLOWED_TARGETS) == set(Actor)
    assert ALLOWED_TARGETS[Actor.AGENT] < ALLOWED_TARGETS[Actor.USER]
    assert ALLOWED_TARGETS[Actor.USER] < ALLOWED_TARGETS[Actor.ENGINE]


def test_the_projection_pausing_statuses_are_ENUMERATED():
    """A user who parked a task for an external reason has made a decision, and an engine recompute
    that
    overwrote it would silently undo it. Left undefined, this would be whatever the recompute
    happened
    to do."""
    assert TaskStatus.BLOCKED in PROJECTION_PAUSING_STATUSES
    assert TaskStatus.CANCELLED in PROJECTION_PAUSING_STATUSES


# ── the acceptance schema ──


def test_a_BARE_STRING_criterion_becomes_one_command_check():
    """The common authoring shape (`done_criterion: "pytest -q"`). Demanding the object form would
    make
    the cheap case expensive."""
    checks, problems = parse_criterion("pytest -q")
    assert [c.kind for c in checks] == [CheckKind.COMMAND]
    assert checks[0].command == "pytest -q"
    assert problems == []


def test_an_empty_criterion_parses_to_nothing():
    assert parse_criterion("")[0] == []
    assert parse_criterion(None)[0] == []


def test_a_MALFORMED_check_is_DROPPED_and_reported():
    """A malformed entry silently becoming a passing check would make a typo look like verification,
    which is the exact failure this module is about."""
    checks, problems = parse_criterion([{"kind": "file_phrase"}])
    assert checks == []
    assert any("DROPPED, not treated as passing" in p for p in problems)


def test_an_unknown_check_KIND_is_dropped():
    checks, problems = parse_criterion([{"kind": "vibes", "command": "x"}])
    assert checks == []
    assert problems


def test_a_ZERO_weight_check_still_counts():
    """A zero-weight check that ran and failed is information; silently dropping it would let an
    author
    disable a check by typo."""
    checks, _ = parse_criterion([{"kind": "command", "command": "a", "weight": 0}])
    assert checks[0].effective_weight == 1.0


def test_weights_are_honored():
    checks, _ = parse_criterion([{"kind": "command", "command": "a", "weight": 3}])
    assert checks[0].effective_weight == 3.0


def test_the_score_is_WEIGHTED():
    """The author said which checks matter. A pass count would ignore that."""
    verdict = Verdict(
        results=[
            CheckResult("command", True, weight=3.0),
            CheckResult("command", False, weight=1.0),
        ]
    )
    assert verdict.score == 0.75


def test_a_check_round_trips():
    check = Check(kind=CheckKind.FILE_PHRASE, path="README.md", required_phrases=["install"])
    assert Check.from_dict(check.to_dict()) == check


# ── file_phrase evaluation ──


def test_a_present_phrase_PASSES():
    check = Check(kind=CheckKind.FILE_PHRASE, path="a.md", required_phrases=["hello"])
    result = evaluate_file_phrase(check, lambda _p: "say hello there")
    assert result.passed is True


def test_a_MISSING_phrase_fails_and_names_it():
    check = Check(kind=CheckKind.FILE_PHRASE, path="a.md", required_phrases=["hello", "world"])
    result = evaluate_file_phrase(check, lambda _p: "say hello")
    assert result.passed is False
    assert "world" in result.detail


def test_an_UNREADABLE_file_is_not_a_failure():
    """The phrase may well be there in a file this process cannot see. Reporting "the phrase is
    missing" would be a claim about content nobody read."""
    check = Check(kind=CheckKind.FILE_PHRASE, path="a.md", required_phrases=["hello"])
    assert evaluate_file_phrase(check, lambda _p: None).passed is None


def test_a_RAISING_reader_is_not_a_failure_either():
    check = Check(kind=CheckKind.FILE_PHRASE, path="a.md", required_phrases=["hello"])

    def boom(_path):
        raise PermissionError("nope")

    result = evaluate_file_phrase(check, boom)
    assert result.passed is None
    assert "PermissionError" in result.detail


# ── cascade-fail ──


def graph() -> dict[str, list[str]]:
    """A binding graph with a later SIBLING reading the failed node — the shape a
    tree walk misses, and where an unblocked task is most misleading."""
    return {
        "gather": [],
        "analyze": ["gather"],
        "report": ["analyze"],
        "sibling": ["gather"],
        "unrelated": [],
    }


def test_a_cascade_follows_BINDINGS_not_the_tree():
    """A tree walk would miss a later sibling that reads the failed node's output — the common
    shape, and
    the one where an unblocked task is most misleading."""
    result = cascade_blocked("gather", "source unreachable", graph())
    assert "sibling" in result.blocked


def test_a_cascade_is_TRANSITIVE():
    """A node blocked by the cascade blocks ITS dependents too, or the board shows the second ring
    as
    workable when nothing in it can start."""
    result = cascade_blocked("gather", "x", graph())
    assert "report" in result.blocked


def test_a_cascade_leaves_UNRELATED_work_alone():
    """Over-blocking would empty the board of work that is genuinely startable."""
    assert "unrelated" not in cascade_blocked("gather", "x", graph()).blocked


def test_the_failed_node_is_not_in_its_own_cascade():
    assert "gather" not in cascade_blocked("gather", "x", graph()).blocked


def test_the_cascade_reason_NAMES_the_cause():
    """ "Blocked" alone sends the user hunting; "Node gather failed: source unreachable" points at
    the
    fix."""
    result = cascade_blocked("gather", "source unreachable", graph())
    assert "gather" in result.reason
    assert "source unreachable" in result.reason


def test_a_cascade_with_no_cause_still_reads():
    assert cascade_blocked("gather", "", graph()).reason == "Node gather failed"


def test_a_cascade_notifies_ONCE():
    """A parallel fan-in failure produces N cascade events in milliseconds, and N alerts for one
    cause is
    how a user mutes the channel that was about to tell them something important."""
    result = cascade_blocked("gather", "x", graph())
    assert result.notify_once is True
    assert len(result.blocked) > 1


def test_a_DEPENDENCY_CYCLE_does_not_spin():
    """A cycle in the dependency map is a bug, and spinning on one would hang the engine rather than
    report it."""
    result = cascade_blocked("a", "x", {"a": ["b"], "b": ["a"], "c": ["a"]})
    assert set(result.blocked) == {"b", "c"}


def test_CLEARING_covers_the_same_set_it_blocked():
    """A dependent left blocked after its prerequisite recovered is work the
    board hides — the same lie in the other direction.
    lie
    in the other direction."""
    blocked = cascade_blocked("gather", "x", graph()).blocked
    assert cascade_cleared("gather", graph()) == blocked


def test_the_cascade_kind_is_DISTINCT_from_a_nodes_own_failure():
    """The dependent task is not broken — its prerequisite is, and the fix is upstream."""
    assert CASCADE_BLOCKED_KIND == "upstream_failed"


def test_an_empty_graph_cascades_nothing():
    assert cascade_blocked("gather", "x", {}).blocked == []


# ── the completion record ──


def test_every_completion_SECTION_is_present():
    """An absent "risks and follow-ups" reads as "there are none", which is the claim a reader most
    wants
    to be true and least wants guessed."""
    record = completion_record(files_changed=["a.py"])
    for section in COMPLETION_SECTIONS:
        assert record[section]
    assert record["risks and follow-ups"] == ["nothing recorded"]


def test_a_recorded_section_keeps_its_items():
    record = completion_record(tests=["12 passed"], behavior="retries are bounded")
    assert record["tests"] == ["12 passed"]
    assert record["behavior"] == ["retries are bounded"]


def test_the_record_shape_is_FIXED():
    """A report whose shape varied per template would need a renderer per template — so it would
    get one
    generic renderer showing none of it."""
    assert len(COMPLETION_SECTIONS) == 5
    assert set(completion_record()) == set(COMPLETION_SECTIONS)


def test_evidence_carries_a_FOLLOWABLE_ref():
    """A completion with evidence that cannot be opened is a completion with a footnote."""
    entry = evidence_entry("artifact", "s47-digest", detail="v2")
    assert entry == {"kind": "artifact", "ref": "s47-digest", "detail": "v2"}


def test_a_DONE_task_with_no_evidence_is_flagged():
    assert done_without_evidence(TaskStatus.DONE, []) is True


def test_a_done_task_WITH_evidence_is_not_flagged():
    assert done_without_evidence(TaskStatus.DONE, [{"kind": "gate"}]) is False


def test_an_OPEN_task_with_no_evidence_is_not_flagged():
    """Evidence is about completion. An open task has nothing to prove yet."""
    assert done_without_evidence(TaskStatus.OPEN, []) is False


# ── the stuck-work sweep ──


class FakeTask:
    def __init__(self, task_id, status, **kw):
        self.id = task_id
        self.status = status
        self.created_at = kw.get("created_at", "")
        self.updated_at = kw.get("updated_at", "")
        self.last_heartbeat_at = kw.get("last_heartbeat_at", "")
        self.evidence = kw.get("evidence", [])


def iso(epoch: float) -> str:
    from datetime import datetime, timezone

    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def test_a_SILENT_in_progress_task_is_flagged():
    """A stage that has said nothing for twenty minutes is either wedged or doing something the user
    should know is slow, and both are worth surfacing."""
    stale = FakeTask(
        "t1",
        TaskStatus.IN_PROGRESS,
        last_heartbeat_at=iso(NOW - (HEARTBEAT_STALE_MINS + 5) * 60),
    )
    findings = sweep([stale], now=NOW)
    assert [f.kind for f in findings] == ["stale_heartbeat"]
    assert "minutes" in findings[0].detail


def test_a_RECENTLY_beating_task_is_not_flagged():
    fresh = FakeTask("t1", TaskStatus.IN_PROGRESS, last_heartbeat_at=iso(NOW - 60))
    assert sweep([fresh], now=NOW) == []


def test_an_UNCLAIMED_ready_task_is_flagged():
    """A task nobody picked up is either mis-scoped or waiting on something nobody recorded."""
    old = FakeTask("t2", TaskStatus.OPEN, created_at=iso(NOW - (UNCLAIMED_STALE_HOURS + 1) * HOUR))
    findings = sweep([old], now=NOW)
    assert [f.kind for f in findings] == ["unclaimed"]


def test_a_FRESH_open_task_is_not_flagged():
    fresh = FakeTask("t2", TaskStatus.OPEN, created_at=iso(NOW - 60))
    assert sweep([fresh], now=NOW) == []


def test_a_DONE_task_with_no_evidence_is_swept():
    done = FakeTask("t3", TaskStatus.DONE, evidence=[])
    assert [f.kind for f in sweep([done], now=NOW)] == ["done_without_evidence"]


def test_the_sweep_reports_rather_than_FIXES():
    """Auto-resolving a stall would hide the condition that caused it, and the same stall would
    recur with
    nothing recorded."""
    stale = FakeTask("t1", TaskStatus.IN_PROGRESS, last_heartbeat_at=iso(NOW - 999 * 60))
    findings = sweep([stale], now=NOW)
    assert findings
    assert stale.status is TaskStatus.IN_PROGRESS  # untouched


def test_an_UNPARSEABLE_timestamp_does_not_stop_the_sweep():
    """A sweep that raised on one bad timestamp would stop reporting every OTHER stall — the
    opposite of
    what a diagnostics pass is for."""
    bad = FakeTask("t1", TaskStatus.IN_PROGRESS, last_heartbeat_at="not a date")
    good = FakeTask("t2", TaskStatus.DONE, evidence=[])
    findings = sweep([bad, good], now=NOW)
    assert [f.task_id for f in findings] == ["t2"]


def test_a_task_with_NO_timestamps_is_not_flagged():
    """Absence is not staleness. Flagging it would put every freshly-created task on the strip."""
    assert sweep([FakeTask("t1", TaskStatus.IN_PROGRESS)], now=NOW) == []


def test_the_heartbeat_falls_back_to_UPDATED_AT():
    """Not every provider writes a heartbeat, and a task nobody has touched in an hour
    is stale on either field."""
    stale = FakeTask("t1", TaskStatus.IN_PROGRESS, updated_at=iso(NOW - 99 * 60))
    assert [f.kind for f in sweep([stale], now=NOW)] == ["stale_heartbeat"]


def test_an_empty_sweep_finds_nothing():
    assert sweep([], now=NOW) == []


# ── idempotent timing ──


def test_CANCELLED_is_sticky():
    """Projection is an idempotent REBUILD — the normal path. Without stickiness every rebuild
    would
    resurrect work someone deliberately stopped."""
    assert sticky_cancel(TaskStatus.CANCELLED, TaskStatus.OPEN) is TaskStatus.CANCELLED


def test_a_non_cancelled_task_takes_the_new_status():
    assert sticky_cancel(TaskStatus.OPEN, TaskStatus.DONE) is TaskStatus.DONE


def test_started_at_is_written_ONCE():
    """A retry that rewrote it would make a task running for an hour look like it
    started thirty seconds ago — and the heartbeat sweep reads exactly that field.
    ago — and the heartbeat sweep reads exactly that field to decide whether work has stalled."""
    assert (
        coalesce_started("2026-01-01T00:00:00Z", "2026-08-02T00:00:00Z") == "2026-01-01T00:00:00Z"
    )


def test_started_at_is_set_when_absent():
    assert coalesce_started("", "2026-08-02T00:00:00Z") == "2026-08-02T00:00:00Z"

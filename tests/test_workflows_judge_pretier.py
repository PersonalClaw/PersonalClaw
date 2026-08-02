"""Tests for the free rule tier and the actor invariants.

Loop judges run every cycle, so a rule-solvable failure reaching the model costs
tokens on every iteration of every run, forever. These tests pin what the free tier
catches — and, just as importantly, that it never issues a PASS.
"""

import pytest

from personalclaw.workflows.judge_actors import (
    JUDGE_EVIDENCE_ROLES,
    TERMINAL_ACTORS,
    WORKER_ALLOWED,
    Actor,
    assemble_judge_evidence,
    blind_provenance,
    check_transition,
    plan_judge_session,
    resolve_transition,
    validate_judge_model,
)
from personalclaw.workflows.judge_contract import FallbackCheck, Isolation
from personalclaw.workflows.judge_pretier import (
    FAILURE_CLASSES,
    MIN_SUBSTANCE_CHARS,
    check_existence,
    check_failure_patterns,
    check_mechanical,
    check_structural,
    check_stubs,
    run_fallback_check,
    run_pretier,
)

# ── the free rules ──


def test_empty_output_is_rejected_without_a_model_call():
    result = check_mechanical("")
    assert result.rejected and result.failure_class == "empty_output"
    assert not result.should_invoke_judge


def test_output_under_the_substance_floor_is_rejected():
    assert check_mechanical("done" * 2).rejected
    assert not check_mechanical("x" * (MIN_SUBSTANCE_CHARS + 1)).rejected


@pytest.mark.parametrize(
    "admission",
    [
        "I couldn't get this working",
        "I was unable to complete the refactor",
        "failed to resolve the import error",
        "giving up on this approach",
        "this needs human intervention",
        "I'll leave this to you for now",
    ],
)
def test_a_worker_give_up_is_caught_by_regex(admission):
    """If the worker said it could not do this, no model needs to adjudicate that."""
    result = check_failure_patterns(admission)
    assert result.rejected and result.failure_class == "worker_gave_up"


@pytest.mark.parametrize(
    "error",
    [
        "command not found: pytest",
        "permission denied",
        "no such file or directory",
        "ModuleNotFoundError: no module named x",
        "connection refused",
        "Traceback (most recent call last):",
    ],
)
def test_tool_errors_get_their_own_failure_class(error):
    """The environment breaking is a different thing from the work being wrong, and
    the escalation ladder treats them differently."""
    result = check_failure_patterns(error)
    assert result.rejected and result.failure_class == "tool_error"


def test_a_give_up_outranks_a_tool_error():
    """Ordered deliberately: a give-up is about the work, and that is the more
    actionable classification."""
    result = check_failure_patterns("I was unable to finish; also command not found")
    assert result.failure_class == "worker_gave_up"


@pytest.mark.parametrize(
    "stub",
    ["raise NotImplementedError", "TODO: implement the parser", "FIXME", "pass  # stub"],
)
def test_stub_markers_are_caught(stub):
    """Work replaced by a promise to do the work."""
    assert check_stubs(stub).rejected


def test_clean_output_survives_every_rule():
    result = run_pretier(
        worker_output="Implemented the parser; all 42 tests pass with real output.",
        artifacts=2,
    )
    assert not result.rejected
    assert result.should_invoke_judge  # only now is the model worth its cost


# ── structural checks ──


def test_a_referenced_path_that_does_not_exist_is_a_rejection(tmp_path):
    """A deliverable citing a file it did not create is unfinished, and that is a fact
    about the filesystem rather than an opinion about quality."""
    result = check_structural(["nope.txt"], root=tmp_path)
    assert result.rejected and result.failure_class == "missing_artifact"


def test_existing_paths_pass(tmp_path):
    (tmp_path / "real.txt").write_text("x")
    assert not check_structural(["real.txt"], root=tmp_path).rejected


def test_no_referenced_paths_is_not_a_failure():
    assert not check_structural([]).rejected
    assert not check_structural(["", None]).rejected  # type: ignore[list-item]


# ── the existence gate ──


def test_zero_of_everything_is_rejected():
    """Zero artifacts, zero commits, zero changed files is not a borderline case."""
    result = check_existence()
    assert result.rejected and result.failure_class == "no_output"


@pytest.mark.parametrize("kw", [{"artifacts": 1}, {"commits": 1}, {"changed_files": 1}])
def test_any_output_clears_the_existence_gate(kw):
    assert not check_existence(**kw).rejected


def test_the_existence_gate_can_be_disabled():
    """A research or review template legitimately produces no files."""
    result = run_pretier(
        worker_output="A thorough analysis of the tradeoffs, with citations.",
        check_existence_gate=False,
    )
    assert not result.rejected


# ── short-circuiting and auditability ──


def test_the_pretier_short_circuits_at_the_first_rejection():
    """Once the work is provably unfinished, further checks cost time and change
    nothing."""
    result = run_pretier(worker_output="")
    assert result.checks_run == ["mechanical"]


def test_the_checks_run_are_recorded_for_audit():
    """A rejection should be auditable rather than mysterious."""
    result = run_pretier(worker_output="I was unable to finish this work at all today")
    assert "mechanical" in result.checks_run
    assert "failure_patterns" in result.checks_run


def test_a_clean_pass_records_every_check():
    result = run_pretier(worker_output="Real substantial work was completed here.", artifacts=1)
    for expected in ("mechanical", "failure_patterns", "stubs", "structural", "existence"):
        assert expected in result.checks_run


def test_the_pretier_never_issues_a_pass():
    """These rules can prove work is UNFINISHED; they cannot prove it is good. A cheap
    PASS would recreate self-approval with extra steps."""
    result = run_pretier(worker_output="Everything is perfect and complete.", artifacts=5)
    assert not result.rejected
    assert result.should_invoke_judge  # deferred to the judge, never approved here


def test_every_failure_class_is_declared():
    """The escalation ladder routes on these, so an undeclared class would be a
    silent routing gap."""
    produced = set()
    for kw in (
        {"worker_output": ""},
        {"worker_output": "I was unable to complete this task at all"},
        {"worker_output": "running it: command not found: pytest, so it failed"},
        {"worker_output": "def f():\n    raise NotImplementedError\n# more text here"},
        {"worker_output": "Finished the work exactly as described above."},
    ):
        result = run_pretier(**kw)
        if result.failure_class:
            produced.add(result.failure_class)
    assert produced <= set(FAILURE_CLASSES)
    assert len(produced) >= 4


# ── the deterministic fallback ──


@pytest.mark.asyncio
async def test_artifact_exists_is_evaluated(tmp_path):
    target = tmp_path / "out.txt"
    assert (
        await run_fallback_check(FallbackCheck.ARTIFACT_EXISTS, artifact_path=str(target)) is False
    )
    target.write_text("x")
    assert (
        await run_fallback_check(FallbackCheck.ARTIFACT_EXISTS, artifact_path=str(target)) is True
    )


@pytest.mark.asyncio
async def test_a_check_with_no_input_is_none_not_false():
    """None means "could not run". Collapsing it into False would turn an
    unconfigured check into a failing deliverable."""
    assert await run_fallback_check(FallbackCheck.ARTIFACT_EXISTS, artifact_path="") is None
    assert await run_fallback_check(FallbackCheck.COMMAND_EXIT_CODE, command="") is None


@pytest.mark.asyncio
async def test_diff_nonempty_reads_the_line_count():
    assert await run_fallback_check(FallbackCheck.DIFF_NONEMPTY, diff_lines=3) is True
    assert await run_fallback_check(FallbackCheck.DIFF_NONEMPTY, diff_lines=0) is False


@pytest.mark.asyncio
async def test_an_unknown_check_is_none():
    assert await run_fallback_check("not_a_check") is None


# ── the actor invariant ──


def test_the_worker_may_never_complete_its_own_work():
    """A state-machine rule, so no prompt can route around it."""
    ruling = check_transition(Actor.WORKER, "done")
    assert not ruling.allowed
    assert ruling.redirected_to == "review"


def test_a_worker_done_is_redirected_not_rejected():
    """The work may genuinely be finished; the right response to "I think I'm done" is
    to route it to a checker, not to error."""
    state, note = resolve_transition(Actor.WORKER, "done")
    assert state == "review"
    assert "may not complete its own work" in note


@pytest.mark.parametrize("state", sorted(WORKER_ALLOWED))
def test_the_worker_may_reach_its_permitted_states(state):
    assert check_transition(Actor.WORKER, state).allowed


@pytest.mark.parametrize("actor", sorted(TERMINAL_ACTORS, key=lambda a: a.value))
def test_terminal_actors_may_complete(actor):
    assert check_transition(actor, "done").allowed


def test_a_judge_transition_is_not_annotated():
    state, note = resolve_transition(Actor.JUDGE, "done")
    assert state == "done" and note == ""


def test_an_unknown_actor_gets_the_restrictive_treatment():
    """Defaulting to permissive here would be the exact hole this closes."""
    ruling = check_transition("mystery_actor", "done")
    assert not ruling.allowed
    assert "unknown actor" in ruling.reason


def test_an_unknown_actor_falls_to_failed_not_done():
    state, note = resolve_transition("mystery", "done")
    assert state == "failed" and note


def test_the_worker_may_not_set_other_terminal_states():
    for state in ("degraded", "escalated", "cancelled", "discarded"):
        assert not check_transition(Actor.WORKER, state).allowed


# ── judge isolation ──


def test_a_judge_always_gets_a_fresh_session():
    """Asking a model to disagree with its own reasoning trace is something it is
    measurably poor at — and the trace is right there in its context."""
    for mode in (Isolation.FRESH, Isolation.CROSS_MODEL):
        assert plan_judge_session(isolation=mode, worker_session_key="s-1").fresh_session


def test_cross_model_requires_a_different_family():
    spec = plan_judge_session(isolation=Isolation.CROSS_MODEL, worker_model="claude-opus-5")
    assert spec.require_different_family
    assert spec.avoid_family == "claude"


def test_a_same_family_judge_is_refused_under_cross_model():
    """Same-family judges share the blind spots they are supposed to catch — an
    "independent" same-family judge is a control wearing a costume."""
    spec = plan_judge_session(isolation=Isolation.CROSS_MODEL, worker_model="claude-opus-5")
    ok, reason = validate_judge_model(spec, "claude-sonnet-5")
    assert not ok and "different family" in reason


def test_a_different_family_judge_is_accepted():
    spec = plan_judge_session(isolation=Isolation.CROSS_MODEL, worker_model="claude-opus-5")
    assert validate_judge_model(spec, "gpt-5")[0]


def test_an_undeterminable_family_is_refused_under_cross_model():
    """Silence about the family is not evidence of difference."""
    spec = plan_judge_session(isolation=Isolation.CROSS_MODEL, worker_model="claude-opus-5")
    assert not validate_judge_model(spec, "")[0]


def test_fresh_isolation_does_not_constrain_the_model():
    spec = plan_judge_session(isolation=Isolation.FRESH, worker_model="claude-opus-5")
    assert validate_judge_model(spec, "claude-opus-5")[0]


def test_an_unknown_isolation_value_falls_back_to_fresh():
    assert plan_judge_session(isolation="nonsense").fresh_session


# ── provenance blinding and evidence assembly ──


@pytest.mark.parametrize(
    "marker",
    ["Attempt 4 of 5", "retry #3", "iteration 7 of 8", "cycle 2", "this is the final attempt"],
)
def test_provenance_markers_are_stripped(marker):
    """ "Attempt 4 of 5" tells a judge how much patience is left, which is exactly the
    pressure that produces a lenient pass."""
    blinded = blind_provenance(f"Some work. {marker}. More work.")
    assert "redacted" in blinded
    assert marker.lower() not in blinded.lower()


def test_blinding_leaves_ordinary_text_alone():
    text = "The parser handles 4 of the 5 documented cases."
    assert blind_provenance(text) == text


def test_blinding_handles_empty_input():
    assert blind_provenance("") == ""


def test_worker_narration_is_structurally_excluded_from_evidence():
    """A worker cannot argue its way to a PASS if its arguments never reach the judge
    — a stronger guarantee than any instruction to discount them."""
    messages = [
        {"role": "user", "content": "implement the parser"},
        {"role": "assistant", "content": "I am confident this is complete and correct"},
        {"role": "tool_call", "content": "pytest -q"},
        {"role": "tool_output", "content": "42 passed"},
        {"role": "worker", "content": "trust me, it works"},
    ]
    evidence = assemble_judge_evidence(messages)
    roles = [m["role"] for m in evidence]
    assert "assistant" not in roles and "worker" not in roles
    assert roles == ["user", "tool_call", "tool_output"]


def test_evidence_roles_exclude_the_assistant_by_design():
    assert "assistant" not in JUDGE_EVIDENCE_ROLES
    assert "worker" not in JUDGE_EVIDENCE_ROLES
    assert {"tool_call", "tool_output"} <= JUDGE_EVIDENCE_ROLES


def test_evidence_is_blinded_by_default():
    messages = [{"role": "user", "content": "spec text, attempt 3 of 5"}]
    assert "redacted" in assemble_judge_evidence(messages)[0]["content"]


def test_blinding_can_be_turned_off_for_debugging():
    messages = [{"role": "user", "content": "attempt 3 of 5"}]
    assert "attempt 3" in assemble_judge_evidence(messages, blind=False)[0]["content"]


def test_assembling_nothing_is_empty():
    assert assemble_judge_evidence([]) == []
    assert assemble_judge_evidence(None) == []  # type: ignore[arg-type]

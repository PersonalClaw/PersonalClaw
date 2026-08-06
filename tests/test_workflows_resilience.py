"""Retry intelligence, the circuit breaker, budgets, and engine-owned completion.

These are the mechanisms that decide when the engine stops spending and whether work is
actually done — so the tests are mostly about the NEGATIVE cases, which is where each
mechanism earns its place:

* a blind retry reproduces the same failure, so a correction hint must reach the next
  attempt;
* a loop can thrash forever, so the breaker must trip WITHOUT a model call;
* a soft budget must pause resumably rather than throw away paid-for work;
* an agent must not certify its own work, so a ladder cannot average a hard failure away
  and an unparseable judge verdict is a failure rather than a guess.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from personalclaw.workflows.bindings import BindingContext
from personalclaw.workflows.engine import NodeResult, apply_artifact_gate, dispatch_gate
from personalclaw.workflows.models import (
    Failure,
    FailureClass,
    InstanceState,
    Node,
)
from personalclaw.workflows.resilience import (
    DEFAULT_ERROR_STREAK,
    ESCALATION_OPTIONS,
    MAX_DIGEST_ATTEMPTS,
    WARN_FRACTION,
    Attempt,
    BreakerState,
    attempt_from_failure,
    check_breaker,
    check_budget,
    error_signature,
    escalation_artifact,
    estimate_calls,
    retry_prompt,
)
from personalclaw.workflows.verify import (
    LADDER_ORDER,
    Verdict,
    check_required_artifacts,
    judge_session_key,
    parse_verdict,
    requires_fresh_judge,
    run_ladder,
)

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _fail(cls: FailureClass = FailureClass.TRANSIENT, msg: str = "boom") -> Failure:
    return Failure(failure_class=cls, cause_plain=msg)


class TestErrorSignature:
    def test_the_same_error_hashes_identically(self) -> None:
        a = error_signature(_fail(msg="connection refused"))
        b = error_signature(_fail(msg="connection refused"))
        assert a == b

    def test_incidental_numbers_are_normalized_away(self) -> None:
        """Without this the breaker's identical-error streak never reaches 2 — a changing
        request id or line number would make the same error look new every time."""
        a = error_signature(_fail(msg="timeout after 30s (req 12345)"))
        b = error_signature(_fail(msg="timeout after 45s (req 98765)"))
        assert a == b

    def test_different_classes_differ_even_with_the_same_message(self) -> None:
        assert error_signature(_fail(FailureClass.NETWORK, "x")) != error_signature(
            _fail(FailureClass.TIMEOUT, "x")
        )

    def test_genuinely_different_errors_differ(self) -> None:
        assert error_signature(_fail(msg="disk full")) != error_signature(
            _fail(msg="permission denied")
        )


class TestAttemptRecords:
    def test_an_attempt_carries_the_typed_class_and_a_fix(self) -> None:
        f = Failure(
            failure_class=FailureClass.PROTOCOL,
            cause_plain="bad json",
            remediation="return only the object",
        )
        a = attempt_from_failure(1, f, tokens=50, duration_secs=1.25)
        assert a.failure_class == "protocol"
        assert a.fix_instruction == "return only the object"
        assert a.tokens == 50
        assert a.error_signature

    def test_a_failure_with_no_remediation_falls_back_to_the_mode_hint(self) -> None:
        """Every failure class has a hint, so a retry is never blind."""
        a = attempt_from_failure(1, Failure(failure_class=FailureClass.TIMEOUT))
        assert "shorter" in a.fix_instruction.lower()

    def test_attempts_round_trip(self) -> None:
        a = attempt_from_failure(2, _fail(), tokens=7)
        assert Attempt.from_dict(a.to_dict()).to_dict() == a.to_dict()


class TestRetryPrompt:
    def test_a_first_attempt_gets_the_prompt_unchanged(self) -> None:
        assert retry_prompt("do the thing", []) == "do the thing"

    def test_a_retry_carries_the_failure_and_the_correction(self) -> None:
        a = attempt_from_failure(1, _fail(FailureClass.NETWORK, "unreachable"))
        out = retry_prompt("do the thing", [a])
        assert "do the thing" in out
        assert "PREVIOUS ATTEMPTS FAILED" in out
        assert "unreachable" in out
        assert "CORRECTION:" in out

    def test_the_digest_is_pruned(self) -> None:
        """A digest that grows with every attempt spends the context the correction needs."""
        attempts = [attempt_from_failure(i, _fail(msg=f"e{i}")) for i in range(1, 9)]
        out = retry_prompt("base", attempts)
        assert out.count("Attempt ") == MAX_DIGEST_ATTEMPTS

    def test_structured_expected_actual_is_included_when_present(self) -> None:
        a = attempt_from_failure(1, _fail())
        a.expected = "a JSON object"
        a.actual = "a paragraph"
        out = retry_prompt("base", [a])
        assert "expected: a JSON object" in out
        assert "actual:   a paragraph" in out


class TestCircuitBreaker:
    """Deterministic and LLM-free: the failure it catches is a loop that never converges,
    and paying a model to notice would be slower and less reliable."""

    def _loop(self, **cfg) -> Node:
        return Node.from_dict(
            {
                "kind": "loop",
                "id": "l",
                "config": cfg,
                "body": {"kind": "transform", "id": "b", "config": {"expr": "1"}},
            }
        )

    def test_a_healthy_loop_does_not_trip(self) -> None:
        st = BreakerState()
        for i in range(5):
            st.record(output=f"different-{i}")
        assert check_breaker(self._loop(), st).tripped is False

    def test_the_iteration_cap_trips(self) -> None:
        st = BreakerState()
        for i in range(3):
            st.record(output=i)
        v = check_breaker(self._loop(max_iterations=3), st)
        assert v.tripped and v.reason == "max_iterations"

    def test_repeated_identical_errors_trip(self) -> None:
        st = BreakerState()
        sig = error_signature(_fail(msg="same problem"))
        for i in range(DEFAULT_ERROR_STREAK):
            st.record(signature=sig, output=f"out{i}")
        v = check_breaker(self._loop(), st)
        assert v.tripped and v.reason == "repeated_error"

    def test_alternating_errors_do_not_trip(self) -> None:
        """Two different errors in rotation is progress of a sort — it is not a thrash."""
        st = BreakerState()
        a, b = error_signature(_fail(msg="one")), error_signature(_fail(msg="two"))
        for i, sig in enumerate([a, b, a, b]):
            st.record(signature=sig, output=f"out{i}")
        assert check_breaker(self._loop(), st).tripped is False

    def test_byte_identical_output_trips(self) -> None:
        st = BreakerState()
        for _ in range(3):
            st.record(output={"same": "every time"})
        v = check_breaker(self._loop(identical_streak=2), st)
        assert v.tripped and v.reason == "identical_output"

    def test_the_token_cap_trips(self) -> None:
        st = BreakerState()
        st.record(output="a", tokens=600)
        st.record(output="b", tokens=600)
        v = check_breaker(self._loop(max_tokens=1000), st)
        assert v.tripped and v.reason == "token_cap"

    def test_nonsense_thresholds_fall_back_to_defaults(self) -> None:
        st = BreakerState()
        sig = error_signature(_fail())
        for i in range(DEFAULT_ERROR_STREAK):
            st.record(signature=sig, output=f"o{i}")
        assert check_breaker(self._loop(error_streak=0), st).tripped is True


class TestEscalation:
    def test_the_artifact_names_five_actionable_options(self) -> None:
        """A bare "it failed" leaves the user to invent the next move; these are the moves."""
        art = escalation_artifact("n1", reason="retries_exhausted", detail="d")
        assert art["options"] == list(ESCALATION_OPTIONS)
        assert len(ESCALATION_OPTIONS) == 5
        assert art["node_id"] == "n1"

    def test_the_attempts_ride_along_as_evidence(self) -> None:
        attempts = [attempt_from_failure(1, _fail()), attempt_from_failure(2, _fail())]
        art = escalation_artifact("n", reason="r", attempts=attempts)
        assert len(art["attempts"]) == 2
        assert art["attempts"][0]["attempt"] == 1


class TestBudget:
    def test_no_cap_means_unbounded(self) -> None:
        """A cap the user did not ask for that silently halts a run is worse than no cap."""
        v = check_budget(10_000_000, 0)
        assert not v.over and not v.warn

    def test_the_warning_fires_at_the_declared_fraction(self) -> None:
        v = check_budget(int(1000 * WARN_FRACTION), 1000)
        assert v.warn and not v.over

    def test_under_the_warning_line_is_silent(self) -> None:
        assert not check_budget(500, 1000).warn

    def test_over_the_cap_also_warns(self) -> None:
        """Not mutually exclusive on purpose: one large node can jump from 40% straight
        past the cap, and treating `over` as "no warning needed" leaves the user with a
        paused run and no notice it was coming."""
        v = check_budget(1500, 1000)
        assert v.over and v.warn

    def test_a_cost_cap_is_honored_independently(self) -> None:
        v = check_budget(10, 1_000_000, spent_cost=5.0, cap_cost=1.0)
        assert v.over and "cost budget" in v.reason

    def test_the_fraction_is_reported_for_display(self) -> None:
        assert check_budget(250, 1000).fraction == 0.25


class TestCallEstimate:
    def test_llm_nodes_are_counted_and_zero_token_nodes_are_not(self) -> None:
        root = Node.from_dict(
            {
                "kind": "sequence",
                "id": "s",
                "children": [
                    {"kind": "infer", "id": "a", "config": {"prompt": "p"}},
                    {"kind": "stage", "id": "b", "config": {"prompt": "p"}},
                    {"kind": "transform", "id": "c", "config": {"expr": "1"}},
                    {"kind": "action", "id": "d", "config": {"provider": "bash"}},
                ],
            }
        )
        est = estimate_calls(root)
        assert est["llm_calls"] == 2
        assert est["actions"] == 1
        assert est["nodes"] == 5

    def test_a_counted_loop_multiplies_its_body(self) -> None:
        root = Node.from_dict(
            {
                "kind": "loop",
                "id": "l",
                "config": {"mode": "counted", "n": 4},
                "body": {"kind": "infer", "id": "b", "config": {"prompt": "p"}},
            }
        )
        assert estimate_calls(root)["llm_calls"] == 4

    def test_a_literal_foreach_multiplies_by_its_item_count(self) -> None:
        root = Node.from_dict(
            {
                "kind": "foreach",
                "id": "f",
                "config": {"items": ["a", "b", "c"]},
                "body": {"kind": "infer", "id": "b", "config": {"prompt": "p"}},
            }
        )
        assert estimate_calls(root)["llm_calls"] == 3

    def test_a_bound_foreach_assumes_one_because_the_count_is_unknowable(self) -> None:
        """An ESTIMATE, and named one: `items` is a binding, so its size is not knowable
        before the run."""
        root = Node.from_dict(
            {
                "kind": "foreach",
                "id": "f",
                "config": {"items": "{{inputs.xs}}"},
                "body": {"kind": "infer", "id": "b", "config": {"prompt": "p"}},
            }
        )
        assert estimate_calls(root)["llm_calls"] == 1


class TestVerdictParsing:
    def test_the_four_verdicts_parse(self) -> None:
        for v in Verdict:
            assert parse_verdict(v.value) == v

    def test_a_dict_shape_is_accepted(self) -> None:
        assert parse_verdict({"verdict": "PASS"}) == Verdict.PASS
        assert parse_verdict({"result": "REJECT"}) == Verdict.REJECT

    def test_case_and_whitespace_are_tolerated(self) -> None:
        assert parse_verdict("  pass  ") == Verdict.PASS

    def test_one_vocabulary_word_in_a_sentence_is_accepted(self) -> None:
        assert parse_verdict("My verdict is PASS given the evidence.") == Verdict.PASS

    def test_two_vocabulary_words_stay_ambiguous(self) -> None:
        """Guessing here would make a control-flow decision out of noise."""
        assert parse_verdict("Should this PASS or REJECT?") is None

    def test_prose_with_no_verdict_is_none_not_a_guess(self) -> None:
        assert parse_verdict("I think it looks mostly fine?") is None
        assert parse_verdict(None) is None


class TestLadder:
    def test_all_passing_criteria_pass_the_gate(self) -> None:
        criteria = [{"name": "lint", "rung": "static"}, {"name": "unit", "rung": "runtime"}]
        r = run_ladder(criteria, {"lint": True, "unit": True})
        assert r.passed and r.verdict == Verdict.PASS

    def test_a_hard_failure_rejects_without_averaging(self) -> None:
        """Averaging is what lets a confident model pass a gate it structurally failed."""
        criteria = [
            {"name": "lint", "rung": "static", "hard": True},
            {"name": "unit", "rung": "runtime"},
            {"name": "e2e", "rung": "system"},
        ]
        r = run_ladder(criteria, {"lint": False, "unit": True, "e2e": True})
        assert not r.passed
        assert r.verdict == Verdict.REJECT
        assert r.stopped_at == "static"

    def test_rungs_run_in_order_regardless_of_declaration_order(self) -> None:
        criteria = [
            {"name": "e2e", "rung": "system", "hard": True},
            {"name": "lint", "rung": "static", "hard": True},
        ]
        r = run_ladder(criteria, {"e2e": False, "lint": False})
        assert r.stopped_at == "static"  # static failed first, so it reports first

    def test_a_soft_failure_does_not_stop_the_ladder(self) -> None:
        criteria = [
            {"name": "style", "rung": "static", "hard": False},
            {"name": "unit", "rung": "runtime", "hard": True},
        ]
        r = run_ladder(criteria, {"style": False, "unit": True})
        assert r.passed
        assert len(r.results) == 2

    def test_an_unevaluated_criterion_fails_rather_than_passing(self) -> None:
        """A no-skip ladder quietly becomes skippable if a missing outcome passes."""
        r = run_ladder([{"name": "unit", "rung": "runtime", "hard": True}], {})
        assert not r.passed

    def test_numeric_scores_are_compared_to_a_threshold(self) -> None:
        crit = [{"name": "cov", "rung": "static", "threshold": 0.8, "hard": True}]
        assert run_ladder(crit, {"cov": 0.9}).passed
        assert not run_ladder(crit, {"cov": 0.5}).passed

    def test_a_verdict_string_works_as_a_criterion_outcome(self) -> None:
        crit = [{"name": "judge", "rung": "system", "hard": True}]
        assert run_ladder(crit, {"judge": "PASS"}).passed
        assert not run_ladder(crit, {"judge": "REJECT"}).passed

    def test_an_unknown_rung_is_treated_as_static(self) -> None:
        r = run_ladder([{"name": "x", "rung": "invented", "hard": True}], {"x": True})
        assert r.passed
        assert r.results[0].rung == "static"

    def test_the_rung_order_is_cheapest_first(self) -> None:
        assert LADDER_ORDER == ("static", "runtime", "system")


class TestFreshJudge:
    def test_the_session_key_cannot_collide_with_a_producer(self) -> None:
        """A producer's session is `subagent:<id>`, so the `judge:` prefix makes reuse
        structurally impossible."""
        key = judge_session_key("run1", "root.children[0]", epoch=2)
        assert key.startswith("judge:")
        assert "run1" in key and "root.children[0]" in key and key.endswith(":2")

    def test_independence_is_the_default(self) -> None:
        assert requires_fresh_judge({}) is True
        assert requires_fresh_judge({"self_judge": False}) is True

    def test_self_judging_requires_explicit_opt_in(self) -> None:
        assert requires_fresh_judge({"self_judge": True}) is False


class TestRequiredArtifacts:
    def test_no_patterns_is_vacuously_satisfied(self) -> None:
        with tempfile.TemporaryDirectory() as ws:
            assert check_required_artifacts([], Path(ws)).satisfied

    def test_a_missing_file_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as ws:
            c = check_required_artifacts(["report.md"], Path(ws))
            assert not c.satisfied and c.missing == ["report.md"]

    def test_a_present_file_yields_a_digest(self) -> None:
        """The digest lets a later reader tell whether the artifact that satisfied the gate
        is still the one on disk."""
        with tempfile.TemporaryDirectory() as ws:
            Path(ws, "report.md").write_text("hello")
            c = check_required_artifacts(["report.md"], Path(ws))
            assert c.satisfied
            assert c.digests[0]["path"] == "report.md"
            assert c.digests[0]["size"] == 5
            assert c.digests[0]["sha256"]

    def test_globs_match_nested_files(self) -> None:
        with tempfile.TemporaryDirectory() as ws:
            nested = Path(ws, "out")
            nested.mkdir()
            (nested / "a.json").write_text("{}")
            assert check_required_artifacts(["*.json"], Path(ws)).satisfied

    def test_a_traversing_pattern_cannot_satisfy_a_gate(self) -> None:
        """A glob is spec-authored text, so `../../etc/passwd` must not count."""
        with tempfile.TemporaryDirectory() as ws:
            c = check_required_artifacts(["../../../etc/passwd"], Path(ws))
            assert not c.satisfied

    def test_an_absolute_pattern_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as ws:
            assert not check_required_artifacts(["/etc/hosts"], Path(ws)).satisfied


class TestArtifactGate:
    def test_a_node_claiming_success_without_its_files_fails(self) -> None:
        """The single most common way agent-declared completion lies."""
        node = Node.from_dict(
            {
                "kind": "stage",
                "id": "s",
                "config": {"prompt": "p", "required_artifacts": ["out.md"]},
            }
        )
        with tempfile.TemporaryDirectory() as ws:
            r = apply_artifact_gate(node, NodeResult(state=InstanceState.DONE), ws)
            assert r.state == InstanceState.FAILED
            assert "required artifacts missing" in r.failure.cause_plain

    def test_digests_are_attached_on_success(self) -> None:
        node = Node.from_dict(
            {
                "kind": "stage",
                "id": "s",
                "config": {"prompt": "p", "required_artifacts": ["out.md"]},
            }
        )
        with tempfile.TemporaryDirectory() as ws:
            Path(ws, "out.md").write_text("x")
            r = apply_artifact_gate(node, NodeResult(state=InstanceState.DONE, output={}), ws)
            assert r.state == InstanceState.DONE
            assert r.output["artifacts"]

    def test_a_node_without_the_declaration_is_untouched(self) -> None:
        node = Node.from_dict({"kind": "stage", "id": "s", "config": {"prompt": "p"}})
        original = NodeResult(state=InstanceState.DONE, output="x")
        assert apply_artifact_gate(node, original, None) is original

    def test_an_already_failing_node_is_not_re_judged(self) -> None:
        node = Node.from_dict(
            {
                "kind": "stage",
                "id": "s",
                "config": {"prompt": "p", "required_artifacts": ["out.md"]},
            }
        )
        failing = NodeResult(state=InstanceState.FAILED, failure=_fail())
        assert apply_artifact_gate(node, failing, None) is failing

    def test_a_declared_gate_with_no_workspace_is_an_internal_error(self) -> None:
        node = Node.from_dict(
            {
                "kind": "stage",
                "id": "s",
                "config": {"prompt": "p", "required_artifacts": ["out.md"]},
            }
        )
        r = apply_artifact_gate(node, NodeResult(state=InstanceState.DONE), None)
        assert r.failure.failure_class == FailureClass.INTERNAL


class TestJudgeGate:
    async def test_a_pass_verdict_completes_the_gate(self) -> None:
        async def judge(prompt, *, use_case="reasoning", output_type=None):
            return "PASS"

        node = Node.from_dict(
            {"kind": "gate", "id": "g", "config": {"kind": "judge", "prompt": "good?"}}
        )
        r = await dispatch_gate(node, BindingContext(), now=0.0, completion=judge)
        assert r.state == InstanceState.DONE
        # The verdict rides out with its evidence chain now (LOOPS-EVOLUTION R3): the controller
        # emits `judge_verdict` from these fields. Additive — `verdict` is unchanged.
        assert r.output["verdict"] == "PASS"
        assert r.output["judge_status"] == "kept"
        assert r.output["judge_evidence"]["samples"] == ["PASS"]

    async def test_each_verdict_maps_to_a_distinct_state(self) -> None:
        """RETRY and ESCALATE are separate because they mean different things to a human."""
        expected = {
            "PASS": InstanceState.DONE,
            "RETRY": InstanceState.FAILED,
            "ESCALATE": InstanceState.ESCALATED,
            "REJECT": InstanceState.FAILED,
        }
        for verdict, state in expected.items():

            async def judge(prompt, *, use_case="reasoning", output_type=None, _v=verdict):
                return _v

            node = Node.from_dict(
                {"kind": "gate", "id": "g", "config": {"kind": "judge", "prompt": "?"}}
            )
            r = await dispatch_gate(node, BindingContext(), now=0.0, completion=judge)
            assert r.state == state, verdict

    async def test_retry_is_retryable_and_reject_is_not(self) -> None:
        for verdict, retryable in (("RETRY", True), ("REJECT", False)):

            async def judge(prompt, *, use_case="reasoning", output_type=None, _v=verdict):
                return _v

            node = Node.from_dict(
                {"kind": "gate", "id": "g", "config": {"kind": "judge", "prompt": "?"}}
            )
            r = await dispatch_gate(node, BindingContext(), now=0.0, completion=judge)
            assert r.failure.retryable is retryable, verdict

    async def test_prose_instead_of_a_verdict_is_a_protocol_failure(self) -> None:
        """Never a silent pass — guessing would route on noise."""

        async def waffle(prompt, *, use_case="reasoning", output_type=None):
            return "Well, it depends on several factors."

        node = Node.from_dict(
            {"kind": "gate", "id": "g", "config": {"kind": "judge", "prompt": "?"}}
        )
        r = await dispatch_gate(node, BindingContext(), now=0.0, completion=waffle)
        assert r.state == InstanceState.FAILED
        assert r.failure.failure_class == FailureClass.PROTOCOL

    async def test_the_closed_enum_is_demanded_in_the_prompt(self) -> None:
        seen = {}

        async def judge(prompt, *, use_case="reasoning", output_type=None):
            seen["prompt"] = prompt
            seen["use_case"] = use_case
            return "PASS"

        node = Node.from_dict(
            {"kind": "gate", "id": "g", "config": {"kind": "judge", "prompt": "rubric"}}
        )
        await dispatch_gate(node, BindingContext(), now=0.0, completion=judge)
        assert "PASS, RETRY, ESCALATE, REJECT" in seen["prompt"]
        # A judge reasons, so it defaults to the reasoning tier rather than the cheap one.
        assert seen["use_case"] == "reasoning"

    async def test_a_judge_gate_needs_a_rubric(self) -> None:
        node = Node.from_dict({"kind": "gate", "id": "g", "config": {"kind": "judge"}})
        r = await dispatch_gate(node, BindingContext(), now=0.0)
        assert r.state == InstanceState.FAILED
        assert r.failure.failure_class == FailureClass.USER


class TestLadderGate:
    async def test_every_criterion_is_evaluated_through_the_injected_verifier(self) -> None:
        seen: list[str] = []

        async def verifier(crit):
            seen.append(crit["name"])
            return True

        node = Node.from_dict(
            {
                "kind": "gate",
                "id": "g",
                "config": {
                    "kind": "ladder",
                    "criteria": [
                        {"name": "lint", "rung": "static"},
                        {"name": "unit", "rung": "runtime"},
                    ],
                },
            }
        )
        r = await dispatch_gate(node, BindingContext(), now=0.0, verify=verifier)
        assert r.state == InstanceState.DONE
        assert seen == ["lint", "unit"]

    async def test_a_hard_failure_rejects_the_gate(self) -> None:
        async def verifier(crit):
            return crit["name"] != "lint"

        node = Node.from_dict(
            {
                "kind": "gate",
                "id": "g",
                "config": {
                    "kind": "ladder",
                    "criteria": [
                        {"name": "lint", "rung": "static", "hard": True},
                        {"name": "unit", "rung": "runtime"},
                    ],
                },
            }
        )
        r = await dispatch_gate(node, BindingContext(), now=0.0, verify=verifier)
        assert r.state == InstanceState.FAILED
        assert r.output["stopped_at"] == "static"

    async def test_a_ladder_needs_criteria(self) -> None:
        node = Node.from_dict({"kind": "gate", "id": "g", "config": {"kind": "ladder"}})
        r = await dispatch_gate(node, BindingContext(), now=0.0, verify=lambda c: True)
        assert r.state == InstanceState.FAILED

    async def test_no_verifier_wired_is_internal_not_a_pass(self) -> None:
        node = Node.from_dict(
            {"kind": "gate", "id": "g", "config": {"kind": "ladder", "criteria": [{"name": "x"}]}}
        )
        r = await dispatch_gate(node, BindingContext(), now=0.0)
        assert r.failure.failure_class == FailureClass.INTERNAL

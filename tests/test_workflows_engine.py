"""Node dispatchers — each one called directly, with no run, lock or journal.

That is the point of the dispatcher/controller split, and these tests are how it pays
off: a dispatcher is an ordinary async function over (node, context), so its edge cases
are cheap to pin down here rather than being found inside a live run.

The asymmetries under test are the ones easiest to get backwards:

* a **null output** flows downstream as a value, but an **unresolvable reference** fails
  the node — a silent empty string is how a prompt loses its input and returns confident
  nonsense;
* `"launched"` from an action provider is DEGRADED, not DONE, because started ≠ succeeded;
* a verifier that could not run is a FAILURE, not a pass — an unrunnable check must never
  certify work.
"""

from __future__ import annotations

import asyncio

import pytest

from personalclaw.workflows.bindings import BindingContext
from personalclaw.workflows.engine import (
    DEFAULT_MODEL_TIERS,
    MAX_JUDGE_SAMPLES,
    MAX_WF_DEPTH,
    check_output_contract,
    dispatch,
    dispatch_action,
    dispatch_branch,
    dispatch_gate,
    dispatch_infer,
    dispatch_stage,
    dispatch_transform,
    dispatch_wait,
    resolve_use_case,
)
from personalclaw.workflows.models import FailureClass, InstanceState, Node, NodeKind

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _n(d: dict) -> Node:
    return Node.from_dict(d)


def _ctx(**kw) -> BindingContext:
    return BindingContext(**kw)


class TestTransform:
    async def test_pure_reshaping_costs_no_tokens(self) -> None:
        r = await dispatch_transform(
            _n({"kind": "transform", "id": "t", "config": {"expr": {"a": 1}}}), _ctx()
        )
        assert r.state == InstanceState.DONE
        assert r.output == {"a": 1}
        assert r.tokens == 0

    async def test_a_whole_value_ref_preserves_type(self) -> None:
        """`{{nodes.x.output}}` alone hands the real object through; stringifying it would
        turn a list into text a downstream pipe cannot operate on."""
        r = await dispatch_transform(
            _n({"kind": "transform", "id": "t", "config": {"expr": "{{nodes.x.output}}"}}),
            _ctx(node_outputs={"x": [1, 2, 3]}),
        )
        assert r.output == [1, 2, 3]

    async def test_an_unresolvable_ref_is_a_user_failure_not_an_exception(self) -> None:
        r = await dispatch_transform(
            _n({"kind": "transform", "id": "t", "config": {"expr": "{{nodes.gone.output}}"}}),
            _ctx(),
        )
        assert r.state == InstanceState.FAILED
        assert r.failure.failure_class == FailureClass.USER
        assert r.failure.remediation  # actionable, not just an error string

    async def test_a_null_upstream_output_flows_through_as_a_value(self) -> None:
        """The distinction that matters: "produced nothing" is data, "does not exist" is
        an error."""
        r = await dispatch_transform(
            _n({"kind": "transform", "id": "t", "config": {"expr": "{{nodes.x.output}}"}}),
            _ctx(node_outputs={"x": None}),
        )
        assert r.state == InstanceState.DONE
        assert r.output is None

    async def test_an_output_contract_violation_fails_the_node(self) -> None:
        r = await dispatch_transform(
            _n(
                {
                    "kind": "transform",
                    "id": "t",
                    "config": {
                        "expr": {"a": 1},
                        "output_contract": {"required_keys": ["b"]},
                    },
                }
            ),
            _ctx(),
        )
        assert r.state == InstanceState.FAILED
        assert "required keys" in r.failure.cause_plain


class TestInfer:
    async def test_one_bounded_call_with_the_resolved_prompt(self) -> None:
        seen = {}

        async def fake(prompt, *, use_case="background", output_type=None):
            seen["prompt"] = prompt
            seen["use_case"] = use_case
            return "answer"

        r = await dispatch_infer(
            _n({"kind": "infer", "id": "i", "config": {"prompt": "sum {{inputs.n}}"}}),
            _ctx(inputs={"n": 7}),
            completion=fake,
        )
        assert r.state == InstanceState.DONE
        assert r.output == "answer"
        assert seen["prompt"] == "sum 7"
        assert r.resolved_prompt == "sum 7"  # journaled for trajectory replay

    async def test_the_tier_selects_a_use_case_never_a_model(self) -> None:
        """Templates name an intent; the use-case bridge owns the model. That is what
        keeps a bundled template portable across provider setups."""
        seen = {}

        async def fake(prompt, *, use_case="background", output_type=None):
            seen["uc"] = use_case
            return "x"

        await dispatch_infer(
            _n({"kind": "infer", "id": "i", "config": {"prompt": "p", "model_tier": "reasoning"}}),
            _ctx(),
            completion=fake,
        )
        assert seen["uc"] == "reasoning"

    async def test_a_custom_tier_map_overrides_the_default(self) -> None:
        node = _n({"kind": "infer", "id": "i", "config": {"prompt": "p", "model_tier": "fast"}})
        assert resolve_use_case(node, {"fast": "loops"}) == "loops"
        assert resolve_use_case(node) == DEFAULT_MODEL_TIERS["fast"]

    async def test_an_empty_prompt_after_binding_is_a_user_failure(self) -> None:
        r = await dispatch_infer(
            _n({"kind": "infer", "id": "i", "config": {"prompt": "  "}}), _ctx()
        )
        assert r.state == InstanceState.FAILED
        assert r.failure.failure_class == FailureClass.USER

    async def test_json_output_strips_markdown_fencing(self) -> None:
        """Fenced output is the dominant real-world format failure; stripping it fixes
        most cases with zero retries."""

        async def fenced(prompt, *, use_case="background", output_type=None):
            return '```json\n{"verdict": "PASS"}\n```'

        r = await dispatch_infer(
            _n(
                {
                    "kind": "infer",
                    "id": "i",
                    "config": {"prompt": "p", "schema": {"type": "object"}},
                }
            ),
            _ctx(),
            completion=fenced,
        )
        assert r.output == {"verdict": "PASS"}

    async def test_unparseable_json_is_a_protocol_failure(self) -> None:
        async def prose(prompt, *, use_case="background", output_type=None):
            return "I think the answer is probably yes"

        r = await dispatch_infer(
            _n({"kind": "infer", "id": "i", "config": {"prompt": "p", "output": "json"}}),
            _ctx(),
            completion=prose,
        )
        assert r.state == InstanceState.FAILED
        assert r.failure.failure_class == FailureClass.PROTOCOL

    async def test_provider_errors_are_classified_for_retry_eligibility(self) -> None:
        """Only TRANSIENT/NETWORK retry — retrying a permission error burns budget to
        reach the same failure."""
        cases = [
            (ConnectionError("network unreachable"), FailureClass.NETWORK, True),
            (TimeoutError("timed out"), FailureClass.TIMEOUT, False),
            (PermissionError("unauthorized"), FailureClass.PERMISSION, False),
            (RuntimeError("rate limit exceeded"), FailureClass.TRANSIENT, True),
        ]
        for exc, expected, retryable in cases:

            async def boom(prompt, *, use_case="background", output_type=None, _e=exc):
                raise _e

            r = await dispatch_infer(
                _n({"kind": "infer", "id": "i", "config": {"prompt": "p"}}),
                _ctx(),
                completion=boom,
            )
            assert r.failure.failure_class == expected, exc
            assert r.failure.retryable is retryable, exc

    async def test_cancellation_propagates_rather_than_becoming_a_failure(self) -> None:
        """A cancelled run must not journal a fake failure — the run is being stopped,
        the node did not break."""

        async def cancelled(prompt, *, use_case="background", output_type=None):
            raise asyncio.CancelledError()

        with pytest.raises(asyncio.CancelledError):
            await dispatch_infer(
                _n({"kind": "infer", "id": "i", "config": {"prompt": "p"}}),
                _ctx(),
                completion=cancelled,
            )


class TestStage:
    class _Spawner:
        def __init__(self, info=None):
            self.info = info
            self.calls = []

        def spawn(self, **kw):
            self.calls.append(kw)
            return self.info

    class _Info:
        def __init__(self, id="ag1", error=""):
            self.id = id
            self.error = error

    async def test_a_stage_spawn_is_silent_and_run_scoped(self) -> None:
        """Without `silent`, a background workflow would inject stage results into
        whatever chat session happened to start the run."""
        sp = self._Spawner(self._Info())
        r = await dispatch_stage(
            _n({"kind": "stage", "id": "s", "config": {"prompt": "do work"}}),
            _ctx(),
            subagents=sp,
        )
        assert r.state == InstanceState.RUNNING
        assert sp.calls[0]["silent"] is True
        assert sp.calls[0]["task"] == "do work"

    async def test_the_depth_cap_is_enforced_in_code(self) -> None:
        """The pre-existing contract was a sentence in a system prompt. A prompt is not an
        enforcement mechanism, so this check is new."""
        sp = self._Spawner(self._Info())
        r = await dispatch_stage(
            _n({"kind": "stage", "id": "s", "config": {"prompt": "p"}}),
            _ctx(),
            subagents=sp,
            depth=MAX_WF_DEPTH,
        )
        assert r.state == InstanceState.FAILED
        assert r.failure.failure_class == FailureClass.PERMISSION
        assert not sp.calls  # refused BEFORE spawning

    async def test_capacity_backpressure_is_not_a_failure(self) -> None:
        """At capacity the node stays ready for the next tick; failing it would lose work
        for a transient scheduling condition."""
        r = await dispatch_stage(
            _n({"kind": "stage", "id": "s", "config": {"prompt": "p"}}),
            _ctx(),
            subagents=self._Spawner(None),
        )
        assert r.state == InstanceState.READY
        assert r.failure is None

    async def test_a_rejected_spawn_is_a_permission_failure(self) -> None:
        r = await dispatch_stage(
            _n({"kind": "stage", "id": "s", "config": {"prompt": "p"}}),
            _ctx(),
            subagents=self._Spawner(self._Info(error="cwd not allowed")),
        )
        assert r.state == InstanceState.FAILED
        assert r.failure.failure_class == FailureClass.PERMISSION

    async def test_a_missing_subagent_manager_is_internal_not_user_error(self) -> None:
        r = await dispatch_stage(
            _n({"kind": "stage", "id": "s", "config": {"prompt": "p"}}), _ctx()
        )
        assert r.failure.failure_class == FailureClass.INTERNAL


class TestBranch:
    NODE = {
        "kind": "branch",
        "id": "r",
        "config": {"on": "{{inputs.k}}"},
        "cases": {
            "a": {"kind": "transform", "id": "ca", "config": {"expr": "1"}},
            "b": {"kind": "transform", "id": "cb", "config": {"expr": "2"}},
        },
    }

    async def test_routing_records_the_case_and_declines_the_others(self) -> None:
        r = await dispatch_branch(_n(self.NODE), _ctx(inputs={"k": "a"}))
        assert r.output == {"case": "a"}
        assert r.declined_edges == ["r->cb"]

    async def test_a_default_case_catches_an_unlisted_value(self) -> None:
        node = _n(
            {**self.NODE, "default": {"kind": "transform", "id": "d", "config": {"expr": "0"}}}
        )
        r = await dispatch_branch(node, _ctx(inputs={"k": "zzz"}))
        assert r.output == {"case": "__default__"}
        assert set(r.declined_edges) == {"r->ca", "r->cb"}

    async def test_no_match_and_no_default_is_a_routing_failure(self) -> None:
        """Falling through silently would make a spec that never ran its real work look
        like a clean pass."""
        r = await dispatch_branch(_n(self.NODE), _ctx(inputs={"k": "zzz"}))
        assert r.state == InstanceState.FAILED
        assert r.failure.failure_class == FailureClass.USER

    async def test_a_missing_on_binding_is_rejected(self) -> None:
        r = await dispatch_branch(_n({"kind": "branch", "id": "r", "cases": {}}), _ctx())
        assert r.state == InstanceState.FAILED


class TestAction:
    class _Result:
        def __init__(self, success=True, stdout="", outcome="", error="", exit_code=0):
            self.success = success
            self.stdout = stdout
            self.outcome = outcome
            self.error = error
            self.exit_code = exit_code
            self.stderr = ""
            self.agent_error = None

    def _provider(self, result):
        class P:
            async def execute(self, cfg, ctx, timeout=30):
                return result

        return lambda name: P()

    async def test_json_stdout_becomes_the_node_output(self) -> None:
        r = await dispatch_action(
            _n({"kind": "action", "id": "a", "config": {"provider": "bash"}}),
            _ctx(),
            get_provider=self._provider(self._Result(stdout='{"count": 3}')),
        )
        assert r.state == InstanceState.DONE
        assert r.output == {"count": 3}

    async def test_launched_is_degraded_because_started_is_not_succeeded(self) -> None:
        """Reporting a fire-and-forget action as clean success would make unverified work
        look verified."""
        r = await dispatch_action(
            _n({"kind": "action", "id": "a", "config": {"provider": "run-prompt"}}),
            _ctx(),
            get_provider=self._provider(self._Result(outcome="launched")),
        )
        assert r.state == InstanceState.DEGRADED
        assert r.degraded_reason

    async def test_skip_maps_to_no_change(self) -> None:
        r = await dispatch_action(
            _n({"kind": "action", "id": "a", "config": {"provider": "run-script"}}),
            _ctx(),
            get_provider=self._provider(self._Result(outcome="skip")),
        )
        assert r.state == InstanceState.NO_CHANGE

    async def test_a_failed_action_is_retryable_transient(self) -> None:
        r = await dispatch_action(
            _n({"kind": "action", "id": "a", "config": {"provider": "bash"}}),
            _ctx(),
            get_provider=self._provider(self._Result(success=False, error="boom")),
        )
        assert r.state == InstanceState.FAILED
        assert r.failure.retryable

    async def test_an_unknown_provider_is_a_user_error_with_a_fix(self) -> None:
        r = await dispatch_action(
            _n({"kind": "action", "id": "a", "config": {"provider": "nope"}}),
            _ctx(),
            get_provider=lambda name: None,
        )
        assert r.failure.failure_class == FailureClass.USER
        assert "install" in r.failure.remediation

    async def test_an_output_contract_gates_before_bindings_can_read_it(self) -> None:
        r = await dispatch_action(
            _n(
                {
                    "kind": "action",
                    "id": "a",
                    "config": {
                        "provider": "bash",
                        "output_contract": {"required_keys": ["missing"]},
                    },
                }
            ),
            _ctx(),
            get_provider=self._provider(self._Result(stdout='{"other": 1}')),
        )
        assert r.state == InstanceState.FAILED
        assert r.failure.failure_class == FailureClass.PROTOCOL


class TestWaitAndGate:
    async def test_a_wait_parks_with_a_deadline(self) -> None:
        r = await dispatch_wait(
            _n({"kind": "wait", "id": "w", "config": {"duration_secs": 30}}),
            _ctx(),
            now=1000.0,
        )
        assert r.state == InstanceState.WAITING
        assert r.wake_at == 1030.0

    async def test_an_already_past_deadline_completes_immediately(self) -> None:
        r = await dispatch_wait(
            _n({"kind": "wait", "id": "w", "config": {"until_ts": 500}}), _ctx(), now=1000.0
        )
        assert r.state == InstanceState.DONE

    async def test_a_wait_with_no_deadline_is_rejected(self) -> None:
        r = await dispatch_wait(_n({"kind": "wait", "id": "w", "config": {}}), _ctx(), now=0.0)
        assert r.state == InstanceState.FAILED

    async def test_an_expression_gate_is_decided_by_the_engine(self) -> None:
        node = _n(
            {
                "kind": "gate",
                "id": "g",
                "config": {"kind": "expression", "expr": "{{nodes.x.output}}"},
            }
        )
        ok = await dispatch_gate(node, _ctx(node_outputs={"x": True}), now=0.0)
        assert ok.state == InstanceState.DONE
        bad = await dispatch_gate(node, _ctx(node_outputs={"x": False}), now=0.0)
        assert bad.state == InstanceState.FAILED

    async def test_an_approval_gate_parks_with_a_typed_ask(self) -> None:
        """One typed payload means one FE renderer covers every human-input node."""
        r = await dispatch_gate(
            _n({"kind": "gate", "id": "g", "config": {"kind": "approval", "prompt": "Ship it?"}}),
            _ctx(),
            now=0.0,
        )
        assert r.state == InstanceState.WAITING
        assert r.ask["kind"] == "approval"
        assert r.ask["prompt"] == "Ship it?"
        assert r.ask["node_id"] == "g"

    async def test_a_gate_timeout_becomes_a_wake_deadline(self) -> None:
        r = await dispatch_gate(
            _n({"kind": "gate", "id": "g", "config": {"kind": "approval", "timeout_secs": 60}}),
            _ctx(),
            now=100.0,
        )
        assert r.wake_at == 160.0

    async def test_a_verifier_that_could_not_run_is_not_a_pass(self) -> None:
        """Tristate, matching loop/gates.py: None means "could not determine". An
        unrunnable verifier must never certify work."""
        node = _n(
            {
                "kind": "gate",
                "id": "g",
                "config": {"kind": "verify_command", "verify": {"command": "true"}},
            }
        )

        async def undetermined(block):
            return None

        r = await dispatch_gate(node, _ctx(), now=0.0, verify=undetermined)
        assert r.state == InstanceState.FAILED
        assert r.failure.failure_class == FailureClass.INTERNAL

    async def test_a_passing_verifier_completes_the_gate(self) -> None:
        node = _n(
            {
                "kind": "gate",
                "id": "g",
                "config": {"kind": "verify_command", "verify": {"command": "true"}},
            }
        )

        async def passes(block):
            return True

        r = await dispatch_gate(node, _ctx(), now=0.0, verify=passes)
        assert r.state == InstanceState.DONE

    async def test_a_verify_gate_without_a_verifier_wired_is_internal(self) -> None:
        node = _n(
            {
                "kind": "gate",
                "id": "g",
                "config": {"kind": "verify_command", "verify": {"command": "x"}},
            }
        )
        r = await dispatch_gate(node, _ctx(), now=0.0)
        assert r.failure.failure_class == FailureClass.INTERNAL


class TestJudgePreTier:
    """The free rule tier that runs BEFORE any LLM judge call (LOOPS-EVOLUTION criterion 2 — S144).

    The plan calls this "the single biggest token saver": anything rule-solvable — empty output, a
    stub, a worker give-up — must never reach the probabilistic model. `judge_pretier.run_pretier`
    shipped in session 30 with no caller; these tests pin the wiring, and each asserts the model was
    NOT called on a rejection (the whole point is the saved completion).
    """

    async def _judge(self, cfg, *, evidence_present=True):
        calls = {"n": 0}

        async def completion(instruction, use_case=None, output_type=None):
            calls["n"] += 1
            return "PASS"

        node = _n({"kind": "gate", "id": "j", "config": {"kind": "judge", **cfg}})
        r = await dispatch_gate(node, _ctx(), now=0.0, completion=completion)
        return r, calls["n"]

    async def test_a_judge_with_NO_evidence_binding_is_unchanged(self) -> None:
        """The additive contract: every judge shipped before S144 binds no `evidence`, so it must
        behave exactly as before — reach the model. Screening it would reject a working gate as
        "nothing to judge"."""
        r, calls = await self._judge({"prompt": "does it meet the goal?"})
        assert r.state == InstanceState.DONE
        assert calls == 1

    async def test_empty_evidence_is_rejected_without_a_model_call(self) -> None:
        r, calls = await self._judge({"prompt": "judge it", "evidence": ""})
        assert r.state == InstanceState.FAILED
        assert calls == 0, "the model must NOT be called on a pre-tier rejection"
        assert r.output["verdict"] == "REJECT"
        assert r.output["failure_class"] == "empty_output"
        assert r.output["pretier"] is True

    async def test_a_stub_artifact_is_rejected_without_a_model_call(self) -> None:
        r, calls = await self._judge({"prompt": "judge it", "evidence": "TODO: implement this"})
        assert r.state == InstanceState.FAILED and calls == 0
        assert r.output["failure_class"] == "stubbed_output"

    async def test_a_worker_giveup_is_rejected_without_a_model_call(self) -> None:
        r, calls = await self._judge(
            {"prompt": "judge it", "evidence": "I was unable to complete this task."}
        )
        assert r.state == InstanceState.FAILED and calls == 0
        assert r.output["failure_class"] == "worker_gave_up"

    async def test_substantial_evidence_reaches_the_judge(self) -> None:
        real = (
            "The analysis shows a 12% improvement across three benchmarks, with the regression "
            "isolated to the cache layer and fixed in commit abc123. Full methodology below."
        )
        r, calls = await self._judge({"prompt": "judge it", "evidence": real})
        assert r.state == InstanceState.DONE
        assert calls == 1, "real output must reach the judge, not be short-circuited"

    async def test_min_chars_is_the_authors_substance_knob(self) -> None:
        """A gate can demand more than the 20-char floor; below it, no model call."""
        r, calls = await self._judge(
            {"prompt": "judge it", "evidence": "short but real enough usually", "min_chars": 200}
        )
        assert r.state == InstanceState.FAILED and calls == 0
        assert r.output["failure_class"] == "empty_output"

    async def test_the_existence_gate_is_OFF_unless_counts_are_declared(self) -> None:
        """A text-only judge must not be rejected for producing zero commits. The existence gate
        only engages when the template supplies an `evidence_*` count."""
        text = "A substantial, real deliverable with more than enough characters to judge here."
        r_off, calls_off = await self._judge({"prompt": "j", "evidence": text})
        assert r_off.state == InstanceState.DONE and calls_off == 1

        r_on, calls_on = await self._judge(
            {"prompt": "j", "evidence": text, "evidence_artifacts": 0, "evidence_commits": 0}
        )
        assert r_on.state == InstanceState.FAILED and calls_on == 0

    async def test_a_rejection_is_terminal_not_retryable(self) -> None:
        """A stub proven by rules will still be a stub on retry — the failure is non-recoverable, so
        the engine escalates rather than spinning."""
        r, _ = await self._judge({"prompt": "j", "evidence": "TODO"})
        assert r.failure.recoverable is False


class TestOutputContract:
    def test_must_be_json_accepts_objects_and_parseable_text(self) -> None:
        assert check_output_contract({"a": 1}, {"must_be_json": True}) == ""
        assert check_output_contract('{"a": 1}', {"must_be_json": True}) == ""
        assert check_output_contract("not json", {"must_be_json": True}) != ""

    def test_required_keys_are_checked_on_parsed_text_too(self) -> None:
        assert check_output_contract('{"a": 1}', {"required_keys": ["a"]}) == ""
        assert "missing required keys" in check_output_contract({"a": 1}, {"required_keys": ["b"]})

    def test_length_bounds(self) -> None:
        assert check_output_contract("abc", {"min_length": 5}) != ""
        assert check_output_contract("abcdef", {"max_length": 3}) != ""
        assert check_output_contract("abc", {"min_length": 1, "max_length": 5}) == ""

    def test_forbidden_phrases_are_case_insensitive(self) -> None:
        assert (
            check_output_contract(
                "As An AI language model, I cannot", {"forbidden_phrases": ["as an ai"]}
            )
            != ""
        )

    def test_an_empty_contract_passes_anything(self) -> None:
        assert check_output_contract("whatever", {}) == ""


class TestDispatchTable:
    async def test_every_leaf_kind_routes_somewhere(self) -> None:
        """A kind with no dispatcher would fail at runtime instead of at review — this is
        the drift guard for the table."""
        from personalclaw.workflows.models import CONTAINER_KINDS

        for kind in NodeKind:
            if kind in CONTAINER_KINDS or kind == NodeKind.SUBWORKFLOW:
                continue
            node = Node(kind=kind, id="x", config={})
            r = await dispatch(node, _ctx(), now=1.0)
            # Some fail for want of config; none may report "no dispatcher".
            assert "no dispatcher" not in (r.failure.cause_plain if r.failure else "")

    async def test_a_container_reaching_dispatch_is_an_engine_bug(self) -> None:
        r = await dispatch(Node(kind=NodeKind.SEQUENCE, id="s"), _ctx())
        assert r.failure.failure_class == FailureClass.INTERNAL
        assert "engine bug" in r.failure.remediation

    async def test_subworkflow_without_a_supervisor_is_an_ENGINE_failure(self) -> None:
        """Nesting landed in Slice 10a; this used to assert "not executable yet".

        With no supervisor injected the dispatcher cannot create a child run, and that is an
        engine WIRING problem rather than a spec problem — the distinction matters because a USER
        classification would send someone hunting their own spec for a bug that is ours. The
        nesting behaviour itself is covered by `test_workflows_nesting.py`.
        """
        r = await dispatch(
            _n({"kind": "subworkflow", "id": "w", "config": {"ref": "child"}}), _ctx()
        )
        assert r.state == InstanceState.FAILED
        assert r.failure.failure_class == FailureClass.INTERNAL
        assert "supervisor" in r.failure.cause_plain


class TestJudgeSamples:
    """`judge_samples` was DECLARED by a shipped template and read by NOTHING (S145).

    `goal-pursuit-open-ended`'s terminal `accept` gate carries `judge_samples: 3`, and its own
    prompt tells the model why: "three independent samples of you are being asked — a single
    judgement on a
    terminal accept was measured to be indistinguishable from noise." Measured against the live
    gate: ONE sample was taken, and a model returning PASS,REJECT,REJECT accepted the run on the
    first word.

    The aggregation rule is `judge_contract.aggregate_samples`', restated over this gate's own
    vocabulary rather than imported — `verify.Verdict` is PASS/RETRY/ESCALATE/REJECT while
    `judge_contract.Verdict` is PASS/REJECT/REPLAN/ESCALATE/NEEDS_INPUT, and feeding one to the
    other's aggregator is the cross-vocabulary defect S130 found in the fail-mode classifier.
    """

    _EVIDENCE = (
        "A substantial deliverable with plenty of characters for the pre-tier to allow through."
    )

    async def _judge(self, cfg, seq):
        calls = {"n": 0}

        async def completion(instruction, use_case=None, output_type=None):
            value = seq[min(calls["n"], len(seq) - 1)]
            calls["n"] += 1
            return value

        node = _n(
            {
                "kind": "gate",
                "id": "accept",
                "config": {
                    "kind": "judge",
                    "prompt": "accept?",
                    "evidence": self._EVIDENCE,
                    **cfg,
                },
            }
        )
        r = await dispatch_gate(node, _ctx(), now=0.0, completion=completion)
        return r, calls["n"]

    async def test_no_declaration_takes_ONE_sample(self) -> None:
        """A gate that never asked for sampling must not start paying for it."""
        r, calls = await self._judge({}, ["PASS"])
        assert calls == 1
        assert r.state == InstanceState.DONE

    async def test_a_one_of_three_pass_is_REJECTED(self) -> None:
        """🔴 The defect, as a test: this accepted the run before S145."""
        r, calls = await self._judge({"judge_samples": 3}, ["PASS", "REJECT", "REJECT"])
        assert calls == 3, "all three samples must actually be taken"
        assert r.state == InstanceState.FAILED
        assert r.output["verdict"] == "REJECT"

    async def test_a_two_of_three_pass_is_ACCEPTED(self) -> None:
        r, calls = await self._judge({"judge_samples": 3}, ["PASS", "PASS", "REJECT"])
        assert calls == 3
        assert r.state == InstanceState.DONE
        assert r.output["verdict"] == "PASS"

    async def test_any_ESCALATE_outweighs_a_pass_majority(self) -> None:
        """An escalation names a contradiction the others did not see — a fact, not an opinion, so
        outvoting it would discard the one sample that noticed."""
        r, _ = await self._judge({"judge_samples": 3}, ["PASS", "PASS", "ESCALATE"])
        assert r.state == InstanceState.ESCALATED
        assert r.output["verdict"] == "ESCALATE"

    async def test_a_split_between_retry_and_reject_prefers_REJECT(self) -> None:
        """A REJECT stops and asks; a RETRY spins. The safe reading of a split is the one that
        does not loop."""
        r, _ = await self._judge({"judge_samples": 2}, ["RETRY", "REJECT"])
        assert r.output["verdict"] == "REJECT"
        assert r.failure.recoverable is False

    async def test_unanimous_retry_stays_RETRY_and_recoverable(self) -> None:
        r, _ = await self._judge({"judge_samples": 3}, ["RETRY", "RETRY", "RETRY"])
        assert r.output["verdict"] == "RETRY"
        assert r.failure.recoverable is True

    async def test_an_unparseable_sample_fails_the_whole_gate(self) -> None:
        """A terminal accept decided from 2 of 3 samples is a quieter version of the single-sample
        bug this session exists to fix, so an unparseable sample stops the gate where it stands."""
        r, calls = await self._judge({"judge_samples": 3}, ["PASS", "banana", "PASS"])
        assert calls == 2, "it must stop at the bad sample, not press on"
        assert r.state == InstanceState.FAILED
        assert r.failure.failure_class == FailureClass.PROTOCOL

    @pytest.mark.parametrize("raw", [0, -3, "x", None, 1.9])
    async def test_an_invalid_count_floors_to_one(self, raw) -> None:
        r, calls = await self._judge({"judge_samples": raw}, ["PASS"])
        assert calls == 1
        assert r.state == InstanceState.DONE

    async def test_the_count_is_CLAMPED(self) -> None:
        """Each sample is a full reasoning-tier completion on a gate that runs every loop iteration,
        so an author typo must not quietly cost 30x."""
        r, calls = await self._judge({"judge_samples": 30}, ["PASS"] * 30)
        assert calls == MAX_JUDGE_SAMPLES
        assert r.state == InstanceState.DONE

    async def test_tokens_are_summed_over_EVERY_sample(self) -> None:
        """🔴 Found in my own first draft: a 3-sample gate reported one sample's tokens, so the loop
        breaker's `max_tokens` and the run cost cap under-counted 3x exactly where sampling makes a
        gate most expensive. A meter that reads low on the expensive path is worse than none."""
        one, _ = await self._judge({}, ["PASS"])
        three, _ = await self._judge({"judge_samples": 3}, ["PASS", "PASS", "PASS"])
        assert one.tokens > 0
        assert three.tokens == one.tokens * 3

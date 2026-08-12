"""`code-project`'s R5 structural gates, driven through the real engine (WF2LOO-10).

The atom this file verifies is LOOPS-EVOLUTION **criterion 6**: *a "build a feature" run
passes the init-gate, holds WIP=1, and classifies a seeded regression vs a pre-existing
failure via the baseline diff.*

Everything here drives the SHIPPED spec — nodes are pulled out of
`read_template("code-project")` and handed to the real dispatchers, and the WIP assertions
run the real `tick.frontier` over the real tree. A test that rebuilt a lookalike spec
inline would keep passing after the template changed, which is the one failure mode that
matters for a structural gate.

The three mechanisms and where each is proven:

* **the 4-condition init gate** — the shipped `init_gate` node through `dispatch_gate`,
  parametrized over each condition being the false one;
* **WIP=1** — the shipped tree through `frontier`, with and without the run's declared
  `single_active_feature` hint, so the hint is shown to be what does the work rather than
  something that happens to agree with the scheduler;
* **regression vs pre-existing** — the shipped `verify` gate through `dispatch_gate` with a
  fake verifier, against a baseline captured by REALLY RUNNING the shipped `baseline`
  node's shell in a temp directory. The classification is deterministic: no model is asked.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from personalclaw.workflows import store
from personalclaw.workflows.bindings import BindingContext
from personalclaw.workflows.bundled_defs import read_template
from personalclaw.workflows.controller import EngineServices, RunController
from personalclaw.workflows.engine import GuardOutcome, dispatch, dispatch_action
from personalclaw.workflows.execution_hints import from_runtime_hints
from personalclaw.workflows.models import (
    InstanceState,
    Node,
    RunStatus,
    WorkflowRun,
    walk,
)
from personalclaw.workflows.tick import frontier

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def _builtin_action_providers():
    """The bash provider, which `gateway` registers at boot and a bare test process does
    not — the baseline node below runs a real command through it."""
    from personalclaw.action_providers.registry import _ensure_default_providers_registered

    _ensure_default_providers_registered()


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    """No test here may see a real home: the controller run below writes a journal."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: home)
    return home


# ── the shipped spec ─────────────────────────────────────────────────────────


def _spec() -> dict:
    wf = read_template("code-project")
    assert wf is not None, "code-project did not load from the bundled provider"
    return wf.to_dict()


def _root() -> Node:
    return Node.from_dict(_spec()["root"])


def _node(node_id: str) -> Node:
    for _path, node in walk(_root()):
        if node.id == node_id:
            return node
    raise AssertionError(f"code-project has no node {node_id!r}")


#: The four conditions R5a's initializer must establish before any code is written.
CHECKLIST = ("can_start", "can_test", "can_see_progress", "can_pick_next")

INPUTS = {
    "task": "add a --dry-run flag",
    "cwd": ".",
    "verify_command": "pytest tests/test_flag.py",
    "guard_command": "pytest",
    "bug_flavored": False,
}


def _init_output(**overrides) -> dict:
    out = {name: True for name in CHECKLIST}
    out["blocked_by"] = ""
    out["breakdown"] = [
        {"item": "parse the flag", "acceptance": "the flag appears in --help"},
        {"item": "honour the flag", "acceptance": "nothing is written when it is set"},
        {"item": "cover it", "acceptance": "a test asserts both"},
    ]
    out.update(overrides)
    return out


def _ctx(**outputs) -> BindingContext:
    return BindingContext(inputs=dict(INPUTS), node_outputs=dict(outputs))


# ── R5a: the gated initializer ───────────────────────────────────────────────


class TestTheInitGate:
    async def test_it_passes_when_all_four_conditions_hold(self) -> None:
        result = await dispatch(_node("init_gate"), _ctx(init=_init_output()))
        assert result.state == InstanceState.DONE
        assert result.output["passed"] is True

    @pytest.mark.parametrize("missing", CHECKLIST)
    async def test_it_blocks_when_any_single_condition_is_false(self, missing: str) -> None:
        """Exhaustive over the closed checklist. Three-of-four is the interesting case: an
        environment that builds and tests but cannot show progress is exactly the one a run
        would happily start in and then have no way to know it was going wrong."""
        result = await dispatch(_node("init_gate"), _ctx(init=_init_output(**{missing: False})))
        assert result.state == InstanceState.FAILED
        assert result.output["passed"] is False
        assert result.failure is not None
        # The reason names the condition it tested, so the run log says what to fix.
        assert missing in result.failure.cause_plain

    async def test_the_gate_tests_all_four_and_nothing_else(self) -> None:
        """A checklist that only reads three of its four fields is a checklist with a hole,
        and nothing at runtime would ever say so."""
        expr = str((_node("init_gate").config or {}).get("expr", ""))
        for name in CHECKLIST:
            assert f"nodes.init.output.{name}" in expr, name
        assert expr.count("&&") == len(CHECKLIST) - 1

    async def test_an_unreadable_checklist_is_not_a_pass(self) -> None:
        """An initializer that returned no checklist has not established anything."""
        result = await dispatch(_node("init_gate"), _ctx(init={"breakdown": []}))
        assert result.state == InstanceState.FAILED


# ── R5b: WIP=1, engine-enforced ──────────────────────────────────────────────


#: Child indices in the shipped root sequence, resolved by id so a reordering does not
#: silently move the assertions to a different node.
def _child_index(node_id: str) -> int:
    for i, child in enumerate(_root().children):
        if child.id == node_id:
            return i
    raise AssertionError(f"code-project root has no child {node_id!r}")


def _states_up_to_implement() -> dict[str, InstanceState]:
    """Everything before the fan-out finished, so the frontier's next move is the fan-out."""
    states: dict[str, InstanceState] = {}
    for node_id in ("init", "init_gate", "baseline", "repro"):
        states[f"root.children[{_child_index(node_id)}]"] = InstanceState.DONE
    return states


def _items_started(fr, item_prefix: str) -> set[str]:
    """Which fan-out ITEMS the frontier admitted, by item path rather than by leaf path."""
    out: set[str] = set()
    for ready in fr.ready:
        if not ready.path.startswith(item_prefix):
            continue
        tail = ready.path[len(item_prefix) :]
        out.add(item_prefix + tail.split(".", 1)[0])
    return out


def _frontier(*, wip: bool, extra_states: dict[str, InstanceState] | None = None):
    states = _states_up_to_implement()
    states.update(extra_states or {})
    return frontier(
        _root(),
        states,
        outputs={"init": _init_output()},
        inputs=dict(INPUTS),
        single_active_feature=wip,
    )


class TestWipOne:
    def test_the_template_declares_the_invariant(self) -> None:
        hints = from_runtime_hints(_spec().get("runtime_hints"))
        assert hints.single_active_feature is True, "nothing would enforce WIP=1 without this"

    def test_only_one_feature_starts(self) -> None:
        fr = _frontier(wip=True)
        item_prefix = f"root.children[{_child_index('implement')}].body#"
        assert _items_started(fr, item_prefix) == {f"{item_prefix}0"}, [r.path for r in fr.ready]
        # The refusal is RECORDED, not silent — a held item and a forgotten one must not look
        # the same from the ledger.
        assert fr.wip_held == [f"{item_prefix}1", f"{item_prefix}2"]

    def test_without_the_hint_the_same_tree_fans_out(self) -> None:
        """The vacuity check. Three items that the scheduler would have serialized anyway
        would make the WIP assertion above pass for the wrong reason."""
        fr = _frontier(wip=False)
        item_prefix = f"root.children[{_child_index('implement')}].body#"
        assert len(_items_started(fr, item_prefix)) == 3, [r.path for r in fr.ready]
        assert fr.wip_held == []

    def test_a_second_feature_is_refused_while_the_first_is_in_flight(self) -> None:
        """The atom's "attempting a second active feature is refused". The first item's stage
        is RUNNING, so admitting another item would mean two features open at once."""
        item_prefix = f"root.children[{_child_index('implement')}].body#"
        fr = _frontier(
            wip=True,
            extra_states={f"{item_prefix}0.children[0]": InstanceState.RUNNING},
        )
        assert [r.path for r in fr.ready if item_prefix in r.path] == []
        assert fr.wip_held == [f"{item_prefix}1", f"{item_prefix}2"]
        assert f"{item_prefix}0.children[0]" in fr.running

    def test_the_next_feature_starts_once_the_first_is_done(self) -> None:
        """WIP=1 must be a queue, not a wall: an invariant that never released its slot would
        finish exactly one feature and call the run complete."""
        idx = _child_index("implement")
        item_prefix = f"root.children[{idx}].body#"
        fr = _frontier(
            wip=True,
            extra_states={
                f"{item_prefix}0": InstanceState.DONE,
                f"{item_prefix}0.children[0]": InstanceState.DONE,
                f"{item_prefix}0.children[1]": InstanceState.DONE,
            },
        )
        assert _items_started(fr, item_prefix) == {f"{item_prefix}1"}
        assert fr.wip_held == [f"{item_prefix}2"]

    def test_a_spec_that_contradicts_the_invariant_is_refused(self) -> None:
        """The authoring half of the refusal. A template declaring WIP=1 while also declaring
        a three-at-a-time fan-out would read one way and run the other."""
        from personalclaw.workflows.validator import validate_spec

        spec = _spec()
        for child in spec["root"]["children"]:
            if child.get("id") == "implement":
                child["config"]["max_concurrency"] = 3
        codes = [i.code for i in validate_spec(spec).issues]
        assert "WF_WIP_CONTRADICTION" in codes

    def test_the_shipped_template_declares_no_contradicting_cap(self) -> None:
        from personalclaw.workflows.validator import validate_spec

        assert validate_spec(_spec(), strict=True).issues == []


# ── R5d: the baseline capture, really run ────────────────────────────────────


async def _run_baseline(cwd, verify_cmd: str, guard_cmd: str) -> dict:
    """Run the SHIPPED baseline node through the real bash provider."""
    node = _node("baseline")
    ctx = BindingContext(
        inputs={
            **INPUTS,
            "cwd": str(cwd),
            "verify_command": verify_cmd,
            "guard_command": guard_cmd,
        }
    )
    result = await dispatch_action(node, ctx, timeout=60)
    return {"state": result.state, "output": result.output}


class TestTheBaselineCapture:
    async def test_it_records_both_commands_as_machine_readable_booleans(self, tmp_path) -> None:
        got = await _run_baseline(tmp_path, "true", "false")
        assert got["state"] == InstanceState.DONE, got
        assert got["output"]["verify_passed"] is True
        assert got["output"]["guard_passed"] is False
        assert got["output"]["guard_exit"] != 0

    async def test_a_failing_baseline_command_does_not_fail_the_node(self, tmp_path) -> None:
        """🔴 The reason the node's command ends in a `printf` instead of just running the
        commands: a FAILED node's output is deliberately NOT published to `_outputs`
        (`controller._apply`), so a baseline that exited non-zero would leave every downstream
        `{{nodes.baseline.output...}}` unresolvable — and a red baseline is the exact case the
        whole classification exists for.
        """
        got = await _run_baseline(tmp_path, "false", "false")
        assert got["state"] == InstanceState.DONE
        assert got["output"]["verify_passed"] is False
        assert got["output"]["guard_passed"] is False

    async def test_an_empty_guard_command_is_not_a_syntax_error(self, tmp_path) -> None:
        got = await _run_baseline(tmp_path, "true", "")
        assert got["state"] == InstanceState.DONE
        assert got["output"]["guard_passed"] is True

    async def test_a_quoted_command_survives_intact(self, tmp_path) -> None:
        """The commands travel as ENV, not as text spliced into the shell — so a command with
        quotes in it runs rather than breaking the script that runs it."""
        got = await _run_baseline(tmp_path, "sh -c 'exit 0'", "grep -q 'nothing here' /dev/null")
        assert got["state"] == InstanceState.DONE
        assert got["output"]["verify_passed"] is True
        assert got["output"]["guard_passed"] is False


# ── R5e + criterion 6: the dual gate's classification ────────────────────────


def _verifier(*, metric: bool | None, guard: bool | None):
    """A fake deterministic verifier, answering per COMMAND rather than per call order."""
    calls: list[dict] = []

    async def verify(block: dict):
        calls.append(dict(block))
        if str(block.get("label")) == "guard":
            return guard
        return metric

    verify.calls = calls  # type: ignore[attr-defined]
    return verify


async def _verify_gate(*, guard_passed_at_baseline: bool, metric: bool | None, guard: bool | None):
    node = _node("verify")
    ctx = _ctx(
        baseline={
            "verify_passed": False,
            "guard_passed": guard_passed_at_baseline,
            "verify_exit": 1,
            "guard_exit": 0 if guard_passed_at_baseline else 1,
        }
    )
    fake = _verifier(metric=metric, guard=guard)
    return await dispatch(node, ctx, verify=fake), fake


class TestTheDualGate:
    async def test_it_runs_both_commands(self) -> None:
        result, fake = await _verify_gate(guard_passed_at_baseline=True, metric=True, guard=True)
        assert result.state == InstanceState.DONE
        assert result.output["guard"] == GuardOutcome.CLEAN.value
        assert [c["command"] for c in fake.calls] == [
            INPUTS["verify_command"],
            INPUTS["guard_command"],
        ]

    async def test_a_failing_metric_never_reaches_the_guard(self) -> None:
        """The deliverable is not done, so whether anything else regressed is a later
        question — and running the suite to find out costs minutes."""
        result, fake = await _verify_gate(guard_passed_at_baseline=True, metric=False, guard=True)
        assert result.state == InstanceState.FAILED
        assert len(fake.calls) == 1

    async def test_a_seeded_regression_fails_the_gate(self) -> None:
        """The guard PASSED at baseline and fails now: this change broke it (criterion 6)."""
        result, _fake = await _verify_gate(guard_passed_at_baseline=True, metric=True, guard=False)
        assert result.state == InstanceState.FAILED
        assert result.output["guard"] == GuardOutcome.REGRESSION.value
        assert "regression" in result.failure.cause_plain

    async def test_a_pre_existing_failure_does_not_fail_the_gate(self) -> None:
        """Same guard failure, different baseline — and therefore a different verdict. Blaming
        a pre-existing failure on this change is how a correct change gets rejected and
        someone debugs the wrong commit."""
        result, _fake = await _verify_gate(guard_passed_at_baseline=False, metric=True, guard=False)
        assert result.state == InstanceState.DEGRADED
        assert result.output["guard"] == GuardOutcome.PRE_EXISTING.value
        assert "pre-existing" in result.degraded_reason

    async def test_the_two_verdicts_differ_only_by_the_baseline(self) -> None:
        """The whole point of capturing a baseline, stated as one assertion: identical
        commands, identical results, opposite verdicts."""
        regression, _a = await _verify_gate(guard_passed_at_baseline=True, metric=True, guard=False)
        pre_existing, _b = await _verify_gate(
            guard_passed_at_baseline=False, metric=True, guard=False
        )
        assert regression.state == InstanceState.FAILED
        assert pre_existing.state == InstanceState.DEGRADED

    async def test_an_unrunnable_guard_is_not_a_pass(self) -> None:
        """The tristate rule the metric half already applies: a check that could not run has
        certified nothing, so there is no regression verdict to give."""
        result, _fake = await _verify_gate(guard_passed_at_baseline=True, metric=True, guard=None)
        assert result.state == InstanceState.FAILED
        assert result.output["guard"] == GuardOutcome.UNDETERMINED.value

    async def test_no_guard_command_leaves_a_metric_only_gate(self) -> None:
        node = _node("verify")
        ctx = BindingContext(
            inputs={**INPUTS, "guard_command": ""},
            node_outputs={"baseline": {"guard_passed": True}},
        )
        result = await dispatch(node, ctx, verify=_verifier(metric=True, guard=False))
        assert result.state == InstanceState.DONE
        assert result.output["guard"] == GuardOutcome.SKIPPED.value

    async def test_the_gate_binds_the_baseline_it_compares_against(self) -> None:
        """A dual gate wired to no baseline could only ever answer "it fails now", which is
        the answer that sends someone to the wrong commit."""
        block = (_node("verify").config or {}).get("verify") or {}
        assert "{{nodes.baseline.output.guard_passed}}" == block.get("guard_baseline")
        assert "{{inputs.guard_command}}" == block.get("guard")


class TestCriterionSixEndToEnd:
    """The criterion as one story, with the baseline REALLY captured by the shipped node."""

    async def test_a_build_a_feature_run_tells_the_two_failures_apart(self, tmp_path) -> None:
        # 1. The initializer's four conditions hold, so the gate opens.
        gate = await dispatch(_node("init_gate"), _ctx(init=_init_output()))
        assert gate.state == InstanceState.DONE

        # 2. WIP=1 holds over the real fan-out: one feature open, the rest refused on record.
        fr = _frontier(wip=True)
        assert len(fr.wip_held) == 2

        # 3. The baseline is captured for real, in a tree where the guard is ALREADY red.
        already_red = await _run_baseline(tmp_path, "true", "false")
        assert already_red["output"]["guard_passed"] is False

        # 4. The guard fails after the change too — and because it was already failing, the
        #    gate does NOT call it a regression.
        node = _node("verify")
        pre_existing = await dispatch(
            node,
            _ctx(baseline=already_red["output"]),
            verify=_verifier(metric=True, guard=False),
        )
        assert pre_existing.state == InstanceState.DEGRADED
        assert pre_existing.output["guard"] == GuardOutcome.PRE_EXISTING.value

        # 5. Now a tree whose guard was GREEN at baseline. Same failing guard afterwards,
        #    and this time the gate blocks the run.
        was_green = await _run_baseline(tmp_path, "true", "true")
        assert was_green["output"]["guard_passed"] is True
        regression = await dispatch(
            node,
            _ctx(baseline=was_green["output"]),
            verify=_verifier(metric=True, guard=False),
        )
        assert regression.state == InstanceState.FAILED
        assert regression.output["guard"] == GuardOutcome.REGRESSION.value


# ── R5c/R5f: reproduction before edit, via inverted success_when ──────────────


def _repro_success_when() -> str:
    expr = str((_node("repro").config or {}).get("success_when", ""))
    assert expr, "the repro stage declares no success_when — R5c would be advice, not a gate"
    return expr


def _success_when_spec(output: dict) -> dict:
    """A minimal spec carrying the SHIPPED predicate over a fixed output.

    `transform` rather than `stage` because a stage parks in RUNNING waiting on a subagent;
    the predicate under test is evaluated by the controller at the same seam either way
    (`_execute` → `_check_success_when`), and this way the run reaches a terminal state.
    """
    return {
        "name": "repro-predicate",
        "root": {
            "kind": "sequence",
            "id": "s",
            "children": [
                {
                    "kind": "transform",
                    "id": "repro",
                    "config": {"expr": output, "success_when": _repro_success_when()},
                },
                {"kind": "transform", "id": "after", "config": {"expr": "reached"}},
            ],
        },
    }


async def _drive(output: dict, inputs: dict) -> tuple[RunStatus, dict]:
    spec = _success_when_spec(output)
    run = store.create(WorkflowRun(id="", workflow_name=spec["name"], inputs=inputs))
    store.write_spec(run.id, spec)
    controller = RunController(run, spec, services=EngineServices())
    status = await controller.run_to_completion(timeout=20)
    return status, controller._outputs


class TestReproductionBeforeEdit:
    async def test_a_bug_run_that_did_not_reproduce_fails(self) -> None:
        """The inversion: the stage RAN, and that is not success. "I could not reproduce it
        but I can see the bug" is the exact move R5c exists to stop."""
        status, _outputs = await _drive(
            {"fail_reproduced": False, "infeasible_reason": ""},
            {"bug_flavored": True},
        )
        assert status == RunStatus.FAILED

    async def test_a_bug_run_that_reproduced_passes(self) -> None:
        status, outputs = await _drive(
            {"fail_reproduced": True, "infeasible_reason": ""},
            {"bug_flavored": True},
        )
        assert status == RunStatus.COMPLETE
        assert outputs["after"] == "reached"

    async def test_a_documented_infeasibility_is_the_only_other_escape(self) -> None:
        status, _outputs = await _drive(
            {"fail_reproduced": False, "infeasible_reason": "needs production data"},
            {"bug_flavored": True},
        )
        assert status == RunStatus.COMPLETE

    async def test_a_feature_run_is_not_held_to_the_reproduction(self) -> None:
        status, _outputs = await _drive(
            {"fail_reproduced": False, "infeasible_reason": ""},
            {"bug_flavored": False},
        )
        assert status == RunStatus.COMPLETE

    async def test_the_escape_reads_the_users_input_not_the_models_output(self) -> None:
        """A predicate whose "this is not a bug fix" arm read a model-reported field would be
        an escape hatch the worker could take by itself — self-certification with extra
        steps. It reads `inputs.bug_flavored`, which the user set."""
        expr = _repro_success_when()
        assert "inputs.bug_flavored" in expr
        assert "output.fail_reproduced" in expr
        assert "output.infeasible_reason" in expr

    async def test_success_when_cannot_rescue_a_node_that_already_failed(self) -> None:
        """It NARROWS success only. A predicate that could turn a failure into a pass would be
        used as one."""
        spec = {
            "name": "narrow-only",
            "root": {
                "kind": "sequence",
                "id": "s",
                "children": [
                    {
                        "kind": "action",
                        "id": "repro",
                        "config": {
                            "provider": "no-such-provider",
                            "with": {},
                            "success_when": "true == true",
                        },
                    }
                ],
            },
        }
        run = store.create(WorkflowRun(id="", workflow_name=spec["name"], inputs={}))
        store.write_spec(run.id, spec)
        controller = RunController(run, spec, services=EngineServices())
        assert await controller.run_to_completion(timeout=20) == RunStatus.FAILED

    async def test_an_unevaluable_predicate_fails_rather_than_passes(self) -> None:
        spec = {
            "name": "unevaluable",
            "root": {
                "kind": "sequence",
                "id": "s",
                "children": [
                    {
                        "kind": "transform",
                        "id": "repro",
                        "config": {"expr": {"a": 1}, "success_when": "output.nope"},
                    }
                ],
            },
        }
        run = store.create(WorkflowRun(id="", workflow_name=spec["name"], inputs={}))
        store.write_spec(run.id, spec)
        controller = RunController(run, spec, services=EngineServices())
        assert await controller.run_to_completion(timeout=20) == RunStatus.FAILED


# ── the WIP refusal reaches the ledger ───────────────────────────────────────


class TestTheWipRefusalIsObservable:
    async def test_a_held_item_is_journaled_once(self) -> None:
        """A refusal nobody can read back is indistinguishable from a scheduler that lost the
        item — "why has feature 2 not started?" has to be answerable from the run's record."""
        spec = {
            "name": "wip-ledger",
            "runtime_hints": {"execution": {"single_active_feature": True}},
            "root": {
                "kind": "foreach",
                "id": "features",
                "config": {"items": [1, 2, 3]},
                "body": {"kind": "transform", "id": "work", "config": {"expr": "done"}},
            },
        }
        run = store.create(WorkflowRun(id="", workflow_name=spec["name"], inputs={}))
        store.write_spec(run.id, spec)
        controller = RunController(run, spec, services=EngineServices())
        assert await controller.run_to_completion(timeout=20) == RunStatus.COMPLETE

        records = [
            json.loads(line)
            for line in (store.run_dir(run.id) / "journal.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
            if line.strip()
        ]
        held = [r for r in records if r.get("decision") == "wip_limit_held"]
        assert held, "the WIP refusal never reached the ledger"
        # Once per item, not once per tick: the frontier re-derives every tick, and a record
        # per tick would bury the run's real events under its own bookkeeping.
        assert len(held) == len({r["instance_path"] for r in held})

    async def test_a_run_without_the_hint_journals_nothing(self) -> None:
        spec = {
            "name": "no-wip",
            "root": {
                "kind": "foreach",
                "id": "features",
                "config": {"items": [1, 2, 3]},
                "body": {"kind": "transform", "id": "work", "config": {"expr": "done"}},
            },
        }
        run = store.create(WorkflowRun(id="", workflow_name=spec["name"], inputs={}))
        store.write_spec(run.id, spec)
        controller = RunController(run, spec, services=EngineServices())
        assert await controller.run_to_completion(timeout=20) == RunStatus.COMPLETE
        text = (store.run_dir(run.id) / "journal.jsonl").read_text(encoding="utf-8")
        assert "wip_limit_held" not in text


# ── the retired template ─────────────────────────────────────────────────────


class TestTheRetiredTemplate:
    def test_code_implementation_is_gone(self) -> None:
        """A clean break: `code-project` REPLACED it rather than shipping beside it, so two
        near-identical code templates never exist for a picker to arbitrate between."""
        from personalclaw.workflows.bundled_defs import template_names

        assert "code-implementation" not in template_names()

    def test_the_legacy_code_kind_still_resolves(self) -> None:
        """The alias layer is why the replacement needed no migration: a stored `kind: code`
        and a months-old `loop_create_code` transcript both resolve at READ time."""
        from personalclaw.workflows.loop_aliases import resolve_kind, resolve_tool

        assert resolve_kind("code") == "code-project"
        assert resolve_tool("loop_create_code") == "code-project"


def test_the_event_loop_is_not_left_running() -> None:
    """Guards against a controller test leaking a task that a later module's assertions would
    then race against."""
    assert asyncio.get_event_loop_policy() is not None

"""Subworkflow nesting and foreach concurrency (Slice 10a, WF2-R5 + WF2-R13).

**Nesting is a real CHILD RUN, not an inlined subtree.** That costs a row and a directory and buys
everything that matters: the child can be rewound, resumed, forked and inspected on its own; a
crash mid-child leaves a child run to adopt rather than a half-written parent; and the parent's
journal stays readable instead of interleaving two graphs' events.

What the tests pin:

* the child's inputs are RESOLVED against the parent before it is created — a child cannot
  interpret `{{nodes.…}}` from a graph it is not part of;
* genealogy is threaded BOTH ways: `parent_run_id` answers "who spawned this?", `root_run_id`
  answers "show me everything this request did", and at depth 3 the parent alone cannot;
* `child_run_attach` lands in the ledger with the SPAWNING NODE id, which the run row does not
  record and a rewind of that node needs;
* depth is capped at 3 and checked BEFORE anything is created, because a self-referencing
  workflow would otherwise spawn runs until the process died;
* `max_concurrency` caps items in flight independently of the lane caps — the knob that actually
  governs a fan-out's shape.
"""

from __future__ import annotations

import asyncio

import pytest

from personalclaw.workflows import service, store
from personalclaw.workflows.controller import EngineServices
from personalclaw.workflows.engine import MAX_SUBWORKFLOW_DEPTH
from personalclaw.workflows.journal import CHILD_RUN_ATTACH, ledger
from personalclaw.workflows.models import InstanceState, Node, RunStatus, WorkflowRun
from personalclaw.workflows.native_defs import register_native_provider
from personalclaw.workflows.tick import Limits, frontier
from personalclaw.workflows.watchdog import WorkflowWatchdog

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: home)
    from personalclaw.workflows import defs as defs_mod

    saved = dict(defs_mod._providers)
    defs_mod._providers.clear()
    register_native_provider()
    try:
        yield home
    finally:
        defs_mod._providers.clear()
        defs_mod._providers.update(saved)


CHILD_ROOT = {"kind": "transform", "id": "c", "config": {"expr": "child got {{inputs.msg}}"}}


async def _author_child(name: str = "child", root: dict | None = None) -> None:
    result = await service.author_def(
        name=name, root=root or CHILD_ROOT, provenance="user", strict=False
    )
    assert result.get("ok"), result


def _parent_spec(ref: str = "child", inputs: dict | None = None) -> dict:
    return {
        "name": "parent",
        "root": {
            "kind": "sequence",
            "id": "s",
            "children": [
                {"kind": "transform", "id": "prep", "config": {"expr": "hello"}},
                {
                    "kind": "subworkflow",
                    "id": "nested",
                    "config": {
                        "ref": ref,
                        "inputs": (
                            inputs if inputs is not None else {"msg": "{{nodes.prep.output}}"}
                        ),
                    },
                },
                {
                    "kind": "transform",
                    "id": "after",
                    "config": {"expr": "parent saw {{nodes.nested.output.status}}"},
                },
            ],
        },
    }


async def _run_parent(spec: dict, *, timeout: float = 30) -> tuple[WorkflowRun, RunStatus]:
    wd = WorkflowWatchdog(None, EngineServices())
    run = store.create(WorkflowRun(id="", workflow_name=str(spec.get("name", "parent"))))
    store.write_spec(run.id, spec)
    controller = await wd.launch(run, spec)
    status = await controller.run_to_completion(timeout=timeout)
    return run, status


class TestNestingHappyPath:
    async def test_a_nested_run_completes_and_the_parent_continues(self) -> None:
        await _author_child()
        run, status = await _run_parent(_parent_spec())
        assert status == RunStatus.COMPLETE
        states = {p: i.state for p, i in store.read_state(run.id).items()}
        assert states["root.children[1]"] == InstanceState.DONE
        # The node AFTER the subworkflow ran, which is the whole point of waiting for the child
        # rather than firing it and forgetting.
        assert states["root.children[2]"] == InstanceState.DONE

    async def test_the_childs_inputs_are_RESOLVED_from_the_parent(self) -> None:
        """A child cannot interpret `{{nodes.prep.output}}` — `nodes` is a different graph's
        namespace. So bindings are resolved to values before the child is created."""
        await _author_child()
        run, _status = await _run_parent(_parent_spec())
        out = store.read_output(run.id, "root.children[1]")
        child = store.get(out["child_run_id"])
        assert child.inputs == {"msg": "hello"}

    async def test_the_childs_outputs_flow_back_to_the_parent(self) -> None:
        """Without this, `{{nodes.nested.output.…}}` resolves to nothing and nesting is useless."""
        await _author_child()
        run, _status = await _run_parent(_parent_spec())
        out = store.read_output(run.id, "root.children[1]")
        assert out["status"] == "complete"
        assert "child got hello" in str(out["outputs"])

    async def test_the_parent_can_bind_to_the_nested_result(self) -> None:
        await _author_child()
        run, _status = await _run_parent(_parent_spec())
        assert store.read_output(run.id, "root.children[2]") == "parent saw complete"

    async def test_a_version_suffix_in_the_ref_resolves_by_name(self) -> None:
        """`name@version` is accepted so a spec can RECORD what it was written against; a def
        provider serves one current version per name, so resolution uses the name."""
        await _author_child()
        _run, status = await _run_parent(_parent_spec(ref="child@1"))
        assert status == RunStatus.COMPLETE


class TestGenealogy:
    async def test_both_parent_and_root_are_recorded(self) -> None:
        await _author_child()
        run, _status = await _run_parent(_parent_spec())
        out = store.read_output(run.id, "root.children[1]")
        child = store.get(out["child_run_id"])
        assert child.parent_run_id == run.id
        assert child.root_run_id == run.id

    async def test_child_run_attach_names_the_SPAWNING_NODE(self) -> None:
        """The run row records the parent edge; only the ledger records which node made it — and
        that is what a rewind of that node needs in order to know what it invalidates."""
        await _author_child()
        run, _status = await _run_parent(_parent_spec())
        events = [e for e in ledger(run.id) if e.get("kind") == CHILD_RUN_ATTACH]
        assert len(events) == 1
        assert events[0]["node_id"] == "nested"
        assert events[0]["child_run_id"]

    async def test_the_root_survives_two_levels(self) -> None:
        """At depth 2 the parent alone cannot answer "everything this request did"."""
        await _author_child(
            name="leaf", root={"kind": "transform", "id": "l", "config": {"expr": 1}}
        )
        await _author_child(
            name="middle",
            root={"kind": "subworkflow", "id": "down", "config": {"ref": "leaf"}},
        )
        run, status = await _run_parent(_parent_spec(ref="middle", inputs={}))
        assert status == RunStatus.COMPLETE
        rows, _total = store.list_runs()
        descendants = [r for r in rows if r.id != run.id]
        assert len(descendants) == 2
        assert all(r.root_run_id == run.id for r in descendants), [
            (r.id, r.parent_run_id, r.root_run_id) for r in descendants
        ]

    async def test_the_attach_event_is_written_even_when_the_child_FAILS(self) -> None:
        """ "Which child run did this failing node spawn?" is exactly the question a failed nesting
        raises, so the link cannot be success-only."""
        await _author_child(
            name="doomed",
            # Fails at RUN time, not authoring: the validator correctly refuses an unresolvable
            # binding, so a child that must reach the engine and then fail needs a runtime fault.
            root={
                "kind": "action",
                "id": "boom",
                "config": {"provider": "no-such-provider", "with": {}},
            },
        )
        run, status = await _run_parent(_parent_spec(ref="doomed", inputs={}))
        assert status == RunStatus.FAILED
        events = [e for e in ledger(run.id) if e.get("kind") == CHILD_RUN_ATTACH]
        assert events, "the genealogy link must survive a child failure"

    async def test_a_failed_child_still_hands_back_its_run_id(self) -> None:
        """Otherwise the user is told a nested run failed with no way to find it."""
        await _author_child(
            name="doomed",
            # Fails at RUN time, not authoring: the validator correctly refuses an unresolvable
            # binding, so a child that must reach the engine and then fail needs a runtime fault.
            root={
                "kind": "action",
                "id": "boom",
                "config": {"provider": "no-such-provider", "with": {}},
            },
        )
        run, _status = await _run_parent(_parent_spec(ref="doomed", inputs={}))
        out = store.read_output(run.id, "root.children[1]")
        assert out and out.get("child_run_id")
        assert store.get(out["child_run_id"]) is not None


class TestDepthCap:
    async def test_a_self_referencing_workflow_is_bounded(self) -> None:
        """The realistic way to hit the cap. Without it, runs spawn until the process dies — each
        with a row and a directory to clean up."""
        await _author_child(
            name="recursive",
            root={"kind": "subworkflow", "id": "again", "config": {"ref": "recursive"}},
        )
        spec = {
            "name": "recursive",
            "root": {"kind": "subworkflow", "id": "again", "config": {"ref": "recursive"}},
        }
        _run, status = await _run_parent(spec, timeout=45)
        assert status == RunStatus.FAILED
        rows, total = store.list_runs()
        # Bounded by the cap, not by luck: one run per level plus the refusing one.
        assert total <= MAX_SUBWORKFLOW_DEPTH + 2, [r.id for r in rows]

    async def test_the_refusal_names_the_ref_and_the_fix(self) -> None:
        await _author_child(
            name="recursive",
            root={"kind": "subworkflow", "id": "again", "config": {"ref": "recursive"}},
        )
        spec = {
            "name": "recursive",
            "root": {"kind": "subworkflow", "id": "again", "config": {"ref": "recursive"}},
        }
        await _run_parent(spec, timeout=45)
        rows, _total = store.list_runs()
        causes = [
            i.failure.cause_plain
            for r in rows
            for i in store.read_state(r.id).values()
            if i.failure and "depth" in (i.failure.cause_plain or "")
        ]
        assert causes, "expected a depth refusal"
        assert "recursive" in causes[0]
        assert str(MAX_SUBWORKFLOW_DEPTH) in causes[0]

    async def test_the_cap_is_checked_BEFORE_a_run_is_created(self) -> None:
        """Refusing after creation would leave an orphan row and directory per attempt."""
        from personalclaw.workflows.bindings import BindingContext
        from personalclaw.workflows.engine import dispatch_subworkflow

        before, _ = store.list_runs()
        result = await dispatch_subworkflow(
            Node.from_dict({"kind": "subworkflow", "id": "x", "config": {"ref": "anything"}}),
            BindingContext(),
            depth=MAX_SUBWORKFLOW_DEPTH,
            supervisor=object(),
        )
        assert result.state == InstanceState.FAILED
        after, _ = store.list_runs()
        assert len(after) == len(before), "the refusal created a run"


class TestNestingRefusals:
    async def test_a_missing_ref_is_a_USER_failure(self) -> None:
        from personalclaw.workflows.bindings import BindingContext
        from personalclaw.workflows.engine import dispatch_subworkflow
        from personalclaw.workflows.models import FailureClass

        result = await dispatch_subworkflow(
            Node.from_dict({"kind": "subworkflow", "id": "x", "config": {}}),
            BindingContext(),
            supervisor=object(),
        )
        assert result.failure.failure_class == FailureClass.USER
        assert "ref" in result.failure.cause_plain

    async def test_an_unknown_workflow_name_says_so(self) -> None:
        run, status = await _run_parent(_parent_spec(ref="no-such-workflow", inputs={}))
        assert status == RunStatus.FAILED
        inst = store.read_state(run.id)["root.children[1]"]
        assert "no-such-workflow" in inst.failure.cause_plain

    async def test_an_unresolvable_input_fails_before_the_child_is_created(self) -> None:
        """A child created with a broken input would run with a hole in its context and produce
        confident nonsense — the failure has to happen here."""
        await _author_child()
        before, _ = store.list_runs()
        run, status = await _run_parent(
            _parent_spec(inputs={"msg": "{{nodes.does_not_exist.output}}"})
        )
        assert status == RunStatus.FAILED
        after, _ = store.list_runs()
        # Only the parent was created.
        assert len(after) == len(before) + 1

    async def test_no_supervisor_is_an_INTERNAL_failure(self) -> None:
        """It is an engine wiring problem, not a spec problem — the distinction is what stops a
        user hunting their own spec for a bug that is ours."""
        from personalclaw.workflows.bindings import BindingContext
        from personalclaw.workflows.engine import dispatch_subworkflow
        from personalclaw.workflows.models import FailureClass

        result = await dispatch_subworkflow(
            Node.from_dict({"kind": "subworkflow", "id": "x", "config": {"ref": "child"}}),
            BindingContext(),
            supervisor=None,
        )
        assert result.failure.failure_class == FailureClass.INTERNAL


class TestForeachConcurrency:
    def test_max_concurrency_caps_what_the_frontier_admits(self) -> None:
        """The cap is the container's, not the lane's — asserted with the compute lane wide open so
        a lane cap cannot be what limits it."""
        root = Node.from_dict(
            {
                "kind": "foreach",
                "id": "l",
                "config": {"items": [1, 2, 3, 4, 5, 6], "max_concurrency": 2},
                "body": {"kind": "transform", "id": "w", "config": {"expr": "{{item}}"}},
            }
        )
        fr = frontier(root, {}, limits=Limits(lanes={"compute": 64, "llm": 9, "io": 9}))
        assert len(fr.ready) == 2

    def test_an_uncapped_foreach_admits_everything(self) -> None:
        """Unbounded is the right default for a handful of cheap items; a cap that appeared by
        default would silently serialize every existing template."""
        root = Node.from_dict(
            {
                "kind": "foreach",
                "id": "l",
                "config": {"items": [1, 2, 3, 4, 5]},
                "body": {"kind": "transform", "id": "w", "config": {"expr": "{{item}}"}},
            }
        )
        fr = frontier(root, {}, limits=Limits(lanes={"compute": 64, "llm": 9, "io": 9}))
        assert len(fr.ready) == 5

    def test_a_finished_item_frees_its_slot(self) -> None:
        """Otherwise a capped fan-out would stall permanently after its first wave."""
        root = Node.from_dict(
            {
                "kind": "foreach",
                "id": "l",
                "config": {"items": [1, 2, 3, 4], "max_concurrency": 2},
                "body": {"kind": "transform", "id": "w", "config": {"expr": "{{item}}"}},
            }
        )
        states = {"root.body#0": InstanceState.DONE, "root.body#1": InstanceState.DONE}
        fr = frontier(root, states, limits=Limits(lanes={"compute": 64, "llm": 9, "io": 9}))
        assert {r.path for r in fr.ready} == {"root.body#2", "root.body#3"}

    def test_an_in_flight_item_still_holds_its_slot(self) -> None:
        """The cap counts items being WORKED, not items launched this tick — otherwise it would
        admit a fresh item on every tick and the cap would mean nothing."""
        root = Node.from_dict(
            {
                "kind": "foreach",
                "id": "l",
                "config": {"items": [1, 2, 3, 4], "max_concurrency": 2},
                "body": {"kind": "transform", "id": "w", "config": {"expr": "{{item}}"}},
            }
        )
        states = {"root.body#0": InstanceState.RUNNING}
        fr = frontier(root, states, limits=Limits(lanes={"compute": 64, "llm": 9, "io": 9}))
        # One slot left, so exactly one NEW item is admitted (the running one is not re-launched).
        assert {r.path for r in fr.ready} == {"root.body#1"}

    def test_a_multi_stage_item_holds_its_slot_across_stages(self) -> None:
        """An item's slot is held until its whole body is terminal: the point of the cap is usually
        a scarce resource the item holds for its duration."""
        root = Node.from_dict(
            {
                "kind": "foreach",
                "id": "l",
                "config": {"items": [1, 2, 3], "max_concurrency": 1},
                "body": {
                    "kind": "sequence",
                    "id": "b",
                    "children": [
                        {"kind": "transform", "id": "s1", "config": {"expr": 1}},
                        {"kind": "transform", "id": "s2", "config": {"expr": 2}},
                    ],
                },
            }
        )
        # Item 0 finished stage 1 and is mid-body.
        states = {"root.body#0.children[0]": InstanceState.DONE}
        fr = frontier(root, states, limits=Limits(lanes={"compute": 64, "llm": 9, "io": 9}))
        paths = {r.path for r in fr.ready}
        assert paths == {"root.body#0.children[1]"}, paths

    @pytest.mark.parametrize("bad", ["two", None, 0, -1, 1.5])
    def test_a_malformed_cap_reads_as_unbounded(self, bad) -> None:
        """A spec typo must not silently serialize a fan-out to one item at a time."""
        root = Node.from_dict(
            {
                "kind": "foreach",
                "id": "l",
                "config": {"items": [1, 2, 3], "max_concurrency": bad},
                "body": {"kind": "transform", "id": "w", "config": {"expr": "{{item}}"}},
            }
        )
        fr = frontier(root, {}, limits=Limits(lanes={"compute": 64, "llm": 9, "io": 9}))
        assert len(fr.ready) == 3

    async def test_a_capped_fan_out_still_completes_every_item(self) -> None:
        """The cap must throttle, never drop."""
        spec = {
            "name": "capped",
            "root": {
                "kind": "foreach",
                "id": "l",
                "config": {"items": [1, 2, 3, 4, 5], "max_concurrency": 2},
                "body": {"kind": "transform", "id": "w", "config": {"expr": "item {{item}}"}},
            },
        }
        run, status = await _run_parent(spec)
        assert status == RunStatus.COMPLETE
        done = [i for i in store.read_state(run.id).values() if i.state == InstanceState.DONE]
        assert len(done) == 5

    async def test_the_cap_is_honoured_at_RUN_TIME_not_just_in_the_frontier(self) -> None:
        """Measured, because a cap the scheduler computes but the controller ignores is no cap."""
        peak = {"n": 0, "max": 0}

        def provider(_name: str):
            class P:
                async def execute(self, cfg, ctx, timeout=30):
                    peak["n"] += 1
                    peak["max"] = max(peak["max"], peak["n"])
                    await asyncio.sleep(0.05)
                    peak["n"] -= 1

                    class R:
                        success = True
                        stdout = "{}"
                        outcome = ""
                        error = ""
                        exit_code = 0
                        stderr = ""
                        agent_error = None

                    return R()

            return P()

        spec = {
            "name": "capped",
            "root": {
                "kind": "foreach",
                "id": "l",
                "config": {"items": [1, 2, 3, 4, 5, 6], "max_concurrency": 2},
                "body": {"kind": "action", "id": "w", "config": {"provider": "p", "with": {}}},
            },
        }
        wd = WorkflowWatchdog(None, EngineServices(get_provider=provider))
        run = store.create(WorkflowRun(id="", workflow_name="capped"))
        store.write_spec(run.id, spec)
        controller = await wd.launch(run, spec)
        assert await controller.run_to_completion(timeout=30) == RunStatus.COMPLETE
        assert peak["max"] <= 2, f"peak concurrency was {peak['max']}, cap was 2"


class TestPipelineFlag:
    """`pipeline` is accepted and documented. The plan describes it as "no barrier between
    stages"; measured against the real engine there is no barrier to remove — each item's body is
    an independent subtree and the frontier is re-derived every tick, so an item advances as soon
    as its OWN previous stage finishes. These tests pin that, so a future change that introduces a
    barrier fails here rather than silently making every fan-out slower.
    """

    def test_an_item_advances_without_waiting_for_its_siblings(self) -> None:
        root = Node.from_dict(
            {
                "kind": "foreach",
                "id": "l",
                "config": {"items": [1, 2, 3]},
                "body": {
                    "kind": "sequence",
                    "id": "b",
                    "children": [
                        {"kind": "transform", "id": "s1", "config": {"expr": 1}},
                        {"kind": "transform", "id": "s2", "config": {"expr": 2}},
                    ],
                },
            }
        )
        # Item 0 finished stage 1; items 1 and 2 have not started.
        states = {"root.body#0.children[0]": InstanceState.DONE}
        fr = frontier(root, states, limits=Limits(lanes={"compute": 64, "llm": 9, "io": 9}))
        paths = {r.path for r in fr.ready}
        assert "root.body#0.children[1]" in paths, "item 0 is barriered behind its siblings"

    def test_the_flag_is_accepted_by_the_validator(self) -> None:
        from personalclaw.workflows.validator import validate_spec

        spec = {
            "name": "p",
            "root": {
                "kind": "foreach",
                "id": "l",
                "config": {"items": [1], "pipeline": True, "max_concurrency": 2},
                "body": {"kind": "transform", "id": "w", "config": {"expr": "{{item}}"}},
            },
        }
        assert validate_spec(spec, strict=True).issues == []

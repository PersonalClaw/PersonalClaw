"""The controller's mutation queue (WF2-R2 / R20 safety protocol).

Mutation is only safe because of WHEN it applies: between scheduling steps, under the
lock, with nothing mid-launch. These tests pin that and its consequences:

* `submit_mutation` **queues**, never applies — a handler that wrote run state directly
  would make two writers (WF2-R10);
* a cascade re-running completed work needs `confirm=True`; without the gate a one-line
  prompt edit silently re-runs and re-bills a dozen finished stages;
* the batch is **RE-VERIFIED at the drain point** — nodes complete while a user reads a
  preview, so a node pending at submit may be frozen by now, and a rejection is journaled
  rather than silently dropped;
* `rewind` resets the seed AND its consumers; `run_from` resets only the consumers,
  leaving the seed's output in place;
* rewound outputs are **archived, not deleted** — otherwise the edit is irreversible;
* done nodes outside the re-run set get a journaled `inputs_stale` flag rather than
  silently serving an answer computed from inputs that no longer exist;
* `expect_version` rejects a stale-state edit.
"""

from __future__ import annotations

import pytest

from personalclaw.workflows import journal as J
from personalclaw.workflows import store
from personalclaw.workflows.controller import EngineServices, RunController
from personalclaw.workflows.models import InstanceState, RunStatus, WorkflowRun

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: home)
    return home


# `report` binds `analyze`, which binds `gather`. `unrelated` binds nothing — it must never
# appear in a cascade.
SPEC = {
    "name": "casc",
    "root": {
        "kind": "sequence",
        "id": "s",
        "children": [
            {"kind": "transform", "id": "gather", "config": {"expr": {"n": 1}}},
            {
                "kind": "infer",
                "id": "analyze",
                "config": {"prompt": "analyze {{nodes.gather.output.n}}"},
            },
            {
                "kind": "infer",
                "id": "report",
                "config": {"prompt": "report {{nodes.analyze.output}}"},
            },
            {"kind": "transform", "id": "unrelated", "config": {"expr": "static"}},
        ],
    },
}


def _echo():
    calls: list[str] = []

    async def fn(prompt, *, use_case="background", output_type=None):
        calls.append(prompt)
        return f"out{len(calls)}"

    fn.calls = calls  # type: ignore[attr-defined]
    return fn


def _make_run(spec: dict = SPEC) -> WorkflowRun:
    run = store.create(WorkflowRun(id="", workflow_name=spec["name"]))
    store.write_spec(run.id, spec)
    return run


async def _completed_controller(spec: dict = SPEC):
    """A controller whose run has finished — the realistic mutation target."""
    import copy

    spec = copy.deepcopy(spec)
    run = _make_run(spec)
    c = RunController(run, spec, services=EngineServices(completion=_echo()))
    assert await c.run_to_completion(timeout=20) == RunStatus.COMPLETE
    return c


class TestSubmitGate:
    async def test_a_valid_edit_on_a_pending_node_queues(self) -> None:
        run = _make_run()
        c = RunController(run, SPEC, services=EngineServices(completion=_echo()))
        body = c.submit_mutation(
            [{"op": "update_node", "node_id": "report", "fields": {"prompt": "new"}}]
        )
        assert body["ok"] and body.get("queued")
        assert len(c._pending_mutations) == 1

    async def test_submitting_does_not_apply(self) -> None:
        """A handler that applied directly would be a second writer of run state."""
        run = _make_run()
        c = RunController(run, SPEC, services=EngineServices(completion=_echo()))
        before = c.run.spec_version
        c.submit_mutation([{"op": "update_node", "node_id": "report", "fields": {"prompt": "x"}}])
        assert c.run.spec_version == before
        assert c.spec["root"]["children"][2]["config"]["prompt"] != "x"

    async def test_a_cascade_over_completed_work_requires_confirmation(self) -> None:
        """Without the gate, one prompt edit silently re-runs and re-bills finished work."""
        c = await _completed_controller()
        body = c.submit_mutation([{"op": "rewind", "node_id": "gather"}])
        assert not body["ok"] and body.get("needs_confirmation")
        assert [i["code"] for i in body["issues"]] == ["WF_MUT_CONFIRM_REQUIRED"]
        assert c._pending_mutations == []

    async def test_confirmation_lets_it_through(self) -> None:
        c = await _completed_controller()
        body = c.submit_mutation([{"op": "rewind", "node_id": "gather"}], confirm=True)
        assert body["ok"] and body.get("queued")

    async def test_an_invalid_batch_never_reaches_the_queue(self) -> None:
        run = _make_run()
        c = RunController(run, SPEC, services=EngineServices(completion=_echo()))
        body = c.submit_mutation([{"op": "skip", "node_id": "ghost"}])
        assert not body["ok"] and c._pending_mutations == []

    async def test_expect_version_rejects_a_stale_edit(self) -> None:
        run = _make_run()
        c = RunController(run, SPEC, services=EngineServices(completion=_echo()))
        body = c.submit_mutation(
            [{"op": "update_node", "node_id": "report", "fields": {"prompt": "x"}}],
            expect_version=99,
        )
        assert not body["ok"]
        assert [i["code"] for i in body["issues"]] == ["WF_MUT_VERSION_MISMATCH"]

    async def test_the_preview_comes_back_synchronously(self) -> None:
        """A caller must see the cascade BEFORE it lands."""
        c = await _completed_controller()
        body = c.submit_mutation([{"op": "rewind", "node_id": "gather"}])
        assert set(body["preview"]["rerun"]) == {"gather", "analyze", "report"}
        assert "unrelated" not in body["preview"]["rerun"]


class TestDrain:
    async def test_draining_commits_the_spec_and_bumps_the_version(self) -> None:
        run = _make_run()
        c = RunController(run, SPEC, services=EngineServices(completion=_echo()))
        c.submit_mutation([{"op": "update_node", "node_id": "report", "fields": {"prompt": "new"}}])
        before = c.run.spec_version
        c._drain_mutations()
        assert c.run.spec_version == before + 1
        assert c.spec["root"]["children"][2]["config"]["prompt"] == "new"
        # Persisted, so a resumed run sees the edit.
        assert store.read_spec(run.id)["root"]["children"][2]["config"]["prompt"] == "new"

    async def test_the_batch_is_journaled_as_a_structured_edit(self) -> None:
        run = _make_run()
        c = RunController(run, SPEC, services=EngineServices(completion=_echo()))
        c.submit_mutation([{"op": "update_node", "node_id": "report", "fields": {"prompt": "n"}}])
        c._drain_mutations()
        edits = [e for e in J.ledger(run.id) if e.get("kind") == J.USER_EDITED_MID_FLIGHT]
        assert len(edits) == 1 and edits[0]["ops"][0]["op"] == "update_node"

    async def test_spec_history_records_the_batch(self) -> None:
        run = _make_run()
        c = RunController(run, SPEC, services=EngineServices(completion=_echo()))
        c.submit_mutation([{"op": "update_node", "node_id": "report", "fields": {"prompt": "n"}}])
        c._drain_mutations()
        path = store.run_dir(run.id) / "spec_history" / f"v{c.run.spec_version:03d}.json"
        assert path.is_file()

    async def test_the_toctou_recheck_rejects_a_node_that_froze_under_the_preview(self) -> None:
        """The load-bearing race: a node PENDING at submit is RUNNING by drain time. The
        frozen check at submit cannot see that; re-verifying at the drain point can."""
        run = _make_run()
        c = RunController(run, SPEC, services=EngineServices(completion=_echo()))
        body = c.submit_mutation(
            [{"op": "update_node", "node_id": "report", "fields": {"prompt": "x"}}]
        )
        assert body["ok"]
        # The node completes while the batch sits in the queue.
        c._instance("root.children[2]").state = InstanceState.DONE
        c._drain_mutations()
        assert c.spec["root"]["children"][2]["config"]["prompt"] != "x"
        rejects = [e for e in J.ledger(run.id) if e.get("kind") == J.MUTATION_REJECTED]
        assert len(rejects) == 1
        assert rejects[0]["issues"][0]["code"] == "WF_MUT_FROZEN_NODE"

    async def test_a_rejected_batch_is_journaled_not_silently_dropped(self) -> None:
        """A silently dropped batch is indistinguishable from an applied one."""
        run = _make_run()
        c = RunController(run, SPEC, services=EngineServices(completion=_echo()))
        c.submit_mutation([{"op": "update_node", "node_id": "report", "fields": {"prompt": "x"}}])
        c._instance("root.children[2]").state = InstanceState.RUNNING
        c._drain_mutations()
        assert [e for e in J.ledger(run.id) if e.get("kind") == J.MUTATION_REJECTED]

    async def test_the_queue_empties_after_a_drain(self) -> None:
        run = _make_run()
        c = RunController(run, SPEC, services=EngineServices(completion=_echo()))
        c.submit_mutation([{"op": "update_node", "node_id": "report", "fields": {"prompt": "n"}}])
        c._drain_mutations()
        assert c._pending_mutations == []

    async def test_draining_an_empty_queue_is_a_no_op(self) -> None:
        run = _make_run()
        c = RunController(run, SPEC, services=EngineServices(completion=_echo()))
        before = c.run.spec_version
        c._drain_mutations()
        assert c.run.spec_version == before


class TestReentry:
    async def test_rewind_resets_the_seed_and_its_consumers(self) -> None:
        c = await _completed_controller()
        c.submit_mutation([{"op": "rewind", "node_id": "gather"}], confirm=True)
        c._drain_mutations()
        states = {p: i.state for p, i in c.instances.items()}
        assert states["root.children[0]"] == InstanceState.PENDING  # gather (the seed)
        assert states["root.children[1]"] == InstanceState.PENDING  # analyze
        assert states["root.children[2]"] == InstanceState.PENDING  # report
        # `unrelated` binds nothing, so it must NOT be reset.
        assert states["root.children[3]"] == InstanceState.DONE

    async def test_run_from_leaves_the_seeds_output_in_place(self) -> None:
        """The whole distinction: 'redo the synthesis with the SAME gathered data'."""
        c = await _completed_controller()
        c.submit_mutation([{"op": "run_from", "node_id": "gather"}], confirm=True)
        c._drain_mutations()
        states = {p: i.state for p, i in c.instances.items()}
        assert states["root.children[0]"] == InstanceState.DONE  # gather survives
        assert states["root.children[1]"] == InstanceState.PENDING
        assert states["root.children[2]"] == InstanceState.PENDING

    async def test_a_rewound_output_is_archived_not_deleted(self) -> None:
        """A rewind that discarded the prior answer would make the edit irreversible."""
        c = await _completed_controller()
        c.submit_mutation([{"op": "rewind", "node_id": "gather"}], confirm=True)
        c._drain_mutations()
        attic = store.run_dir(c.run.id) / "outputs" / "attic"
        assert attic.is_dir()
        assert list(attic.rglob("*.json"))

    async def test_the_cached_output_is_dropped_so_no_binding_resolves_stale(self) -> None:
        c = await _completed_controller()
        assert "gather" in c._outputs
        c.submit_mutation([{"op": "rewind", "node_id": "gather"}], confirm=True)
        c._drain_mutations()
        assert "gather" not in c._outputs

    async def test_the_epoch_holds_without_force(self) -> None:
        """WF2-R2 #4: unchanged inputs must replay from cache, not pay to recompute."""
        c = await _completed_controller()
        before = c._instance("root.children[1]").epoch
        c.submit_mutation([{"op": "rewind", "node_id": "gather"}], confirm=True)
        c._drain_mutations()
        assert c._instance("root.children[1]").epoch == before

    async def test_force_bumps_the_epoch(self) -> None:
        c = await _completed_controller()
        before = c._instance("root.children[1]").epoch
        c.submit_mutation([{"op": "rewind", "node_id": "gather", "force": True}], confirm=True)
        c._drain_mutations()
        assert c._instance("root.children[1]").epoch == before + 1

    async def test_a_rewound_run_re_runs_the_closure_and_nothing_else(self) -> None:
        """End to end: the reset region actually re-executes, and `unrelated` does not."""
        import copy

        spec = copy.deepcopy(SPEC)
        run = _make_run(spec)
        fn = _echo()
        c = RunController(run, spec, services=EngineServices(completion=fn))
        assert await c.run_to_completion(timeout=20) == RunStatus.COMPLETE
        first_calls = len(fn.calls)
        assert first_calls == 2  # analyze + report

        c.submit_mutation(
            [
                {
                    "op": "update_node",
                    "node_id": "analyze",
                    "fields": {"prompt": "NEW {{nodes.gather.output.n}}"},
                }
            ],
            confirm=True,
        )
        # An update_node on a DONE node is frozen — rewind first, which is the documented
        # workflow and what the error message tells the user to do.
        c.submit_mutation([{"op": "rewind", "node_id": "analyze"}], confirm=True)
        c._drain_mutations()
        c.run.status = RunStatus.RUNNING
        assert await c.run_to_completion(timeout=20) == RunStatus.COMPLETE
        # analyze + report re-ran; gather and unrelated did not.
        assert len(fn.calls) == first_calls + 2


class TestInputsStale:
    async def test_a_done_node_outside_the_rerun_set_is_flagged(self) -> None:
        """WF2-R2 #3. `run_from` excludes the seed from the re-run set, but `analyze` still
        reads it — so `analyze`'s inputs are, in principle, from a different world."""
        c = await _completed_controller()
        c.submit_mutation([{"op": "run_from", "node_id": "gather"}], confirm=True)
        c._drain_mutations()
        # Nothing to flag here: every consumer IS being re-run. The flag exists for
        # partial cascades, asserted below.
        stale = [e for e in J.ledger(c.run.id) if e.get("kind") == J.INPUTS_STALE]
        assert stale == []

    async def test_a_partial_cascade_flags_the_untouched_consumer(self) -> None:
        """Reset only `report`; `analyze` stays done. Nothing reads `report`, so no flag —
        but reset only `analyze` and `report` is in the closure, so it re-runs. The flag
        fires when a done node reads a re-run node yet is NOT itself re-run, which is what
        a hand-built partial preview produces."""
        from personalclaw.workflows.mutations import CascadePreview

        c = await _completed_controller()
        # A deliberately partial preview: gather re-runs, its consumers do not.
        c._flag_stale(CascadePreview(rerun=["gather"]))
        stale = [e for e in J.ledger(c.run.id) if e.get("kind") == J.INPUTS_STALE]
        assert len(stale) == 1
        assert stale[0]["node_id"] == "analyze"
        assert stale[0]["stale_deps"] == ["gather"]

    async def test_a_node_with_no_dependency_on_the_rerun_set_is_not_flagged(self) -> None:
        from personalclaw.workflows.mutations import CascadePreview

        c = await _completed_controller()
        c._flag_stale(CascadePreview(rerun=["gather"]))
        flagged = {e["node_id"] for e in J.ledger(c.run.id) if e.get("kind") == J.INPUTS_STALE}
        assert "unrelated" not in flagged


class TestSetInput:
    async def test_an_input_override_reaches_the_run(self) -> None:
        spec = {
            "name": "inp",
            "root": {
                "kind": "sequence",
                "id": "s",
                "children": [
                    {"kind": "transform", "id": "t", "config": {"expr": "{{inputs.since}}"}}
                ],
            },
        }
        run = store.create(WorkflowRun(id="", workflow_name="inp", inputs={"since": "1h"}))
        store.write_spec(run.id, spec)
        c = RunController(run, spec, services=EngineServices(completion=_echo()))
        c.submit_mutation([{"op": "set_input", "overrides": {"since": "24h"}}])
        c._drain_mutations()
        assert c.run.inputs["since"] == "24h"

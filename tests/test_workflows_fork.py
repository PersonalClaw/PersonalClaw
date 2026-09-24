"""Checkpoints, `fork`, `revert`, and the Slice-4 property tests.

The plan names six properties for this slice; each is a class below. They are properties
rather than examples because the failure modes are combinatorial — a cascade that is right
for one graph shape and wrong for another is exactly what a hand-picked example misses.

The load-bearing claims:

* **the cascade equals the BINDING closure, not the tree descendants** — asserted over
  several graph shapes, including the one where the two differ most;
* **rewind is idempotent** — resetting twice equals resetting once, or a retried mutation
  double-archives and loses an output;
* **a fork isolates state and shares nothing it does not admit to** — the parent is
  byte-identical afterwards, and the shared axes are surfaced, not hidden;
* a fork's journal prefix produces real cache HITS (that is what makes it cheap) AND its
  outputs come along, or a hit reads a missing file and resolves a binding to None;
* **revert refuses with the dependents NAMED** when later state already consumed the value;
* frozen nodes never mutate, and acyclicity survives every accepted batch.
"""

from __future__ import annotations

import json

import pytest

from personalclaw.workflows import checkpoints as CP
from personalclaw.workflows import mutations as M
from personalclaw.workflows import store
from personalclaw.workflows.controller import EngineServices, RunController
from personalclaw.workflows.models import (
    SUCCESS_STATES,
    InstanceState,
    Node,
    NodeInstance,
    RunStatus,
    WorkflowRun,
    walk,
)

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


CHAIN = {
    "name": "chain",
    "root": {
        "kind": "sequence",
        "id": "s",
        "children": [
            {"kind": "transform", "id": "a", "config": {"expr": {"n": 1}}},
            {"kind": "infer", "id": "b", "config": {"prompt": "b {{nodes.a.output.n}}"}},
            {"kind": "infer", "id": "c", "config": {"prompt": "c {{nodes.b.output}}"}},
            {"kind": "transform", "id": "iso", "config": {"expr": "static"}},
        ],
    },
}

#: A nested shape where tree descendants and binding consumers diverge hardest: `outer`
#: contains `inner`, but the consumer of `inner` sits OUTSIDE `outer` entirely.
NESTED = {
    "name": "nested",
    "root": {
        "kind": "sequence",
        "id": "s",
        "children": [
            {
                "kind": "sequence",
                "id": "outer",
                "children": [
                    {"kind": "transform", "id": "inner", "config": {"expr": {"v": 2}}},
                ],
            },
            {
                "kind": "infer",
                "id": "outside",
                "config": {"prompt": "uses {{nodes.inner.output.v}}"},
            },
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


async def _completed(spec: dict = CHAIN):
    import copy

    spec = copy.deepcopy(spec)
    run = store.create(WorkflowRun(id="", workflow_name=spec["name"]))
    store.write_spec(run.id, spec)
    c = RunController(run, spec, services=EngineServices(completion=_echo()))
    assert await c.run_to_completion(timeout=20) == RunStatus.COMPLETE
    return c


# ── property: the cascade IS the binding closure ─────────────────────────────


class TestCascadeEqualsBindingClosure:
    """The plan's headline property: cascade == binding closure, NOT tree descendants."""

    @pytest.mark.parametrize(
        "spec,seed,expected",
        [
            (CHAIN, "a", {"a", "b", "c"}),
            (CHAIN, "b", {"b", "c"}),
            (CHAIN, "c", {"c"}),
            (CHAIN, "iso", {"iso"}),
            (NESTED, "inner", {"inner", "outside"}),
        ],
    )
    def test_the_closure_is_exactly_the_binding_reachable_set(
        self, spec: dict, seed: str, expected: set
    ) -> None:
        assert M.binding_closure(Node.from_dict(spec["root"]), {seed}) == expected

    def test_a_consumer_outside_the_containing_subtree_is_still_caught(self) -> None:
        """`outside` is not inside `outer`, so no tree walk from `inner` reaches it — yet it
        consumes `inner`'s output and MUST be reset with it."""
        root = Node.from_dict(NESTED["root"])
        inner = [n for _p, n in walk(root) if n.id == "inner"][0]
        tree_only = {n.id for _p, n in walk(inner) if n.id}
        closure = M.binding_closure(root, {"inner"})
        assert "outside" not in tree_only
        assert "outside" in closure

    def test_the_closure_never_includes_an_unrelated_node(self) -> None:
        """The other half: over-running is a cost bug, not just a correctness one."""
        for seed in ("a", "b", "c"):
            assert "iso" not in M.binding_closure(Node.from_dict(CHAIN["root"]), {seed})


# ── property: rewind idempotence ─────────────────────────────────────────────


class TestRewindIdempotence:
    async def test_rewinding_twice_equals_rewinding_once(self) -> None:
        """A retried mutation must not double-archive or lose an output."""
        c = await _completed()
        c.submit_mutation([{"op": "rewind", "node_id": "b"}], confirm=True)
        c._drain_mutations()
        first = {p: (i.state, i.epoch, i.output_ref) for p, i in c.instances.items()}
        attic_after_first = len(
            list((store.run_dir(c.run.id) / "outputs" / "attic").rglob("*.json"))
        )

        c.submit_mutation([{"op": "rewind", "node_id": "b"}], confirm=True)
        c._drain_mutations()
        second = {p: (i.state, i.epoch, i.output_ref) for p, i in c.instances.items()}
        attic_after_second = len(
            list((store.run_dir(c.run.id) / "outputs" / "attic").rglob("*.json"))
        )

        assert first == second
        # Nothing left to archive the second time — the outputs were already cleared.
        assert attic_after_second == attic_after_first

    async def test_a_rewound_node_holds_no_stale_output_reference(self) -> None:
        c = await _completed()
        c.submit_mutation([{"op": "rewind", "node_id": "a"}], confirm=True)
        c._drain_mutations()
        for path, inst in c.instances.items():
            if inst.state == InstanceState.PENDING:
                assert inst.output_ref == "", path


# ── property: no frozen mutation, acyclicity preserved ───────────────────────


class TestInvariantsHold:
    @pytest.mark.parametrize("state", ["running", "done", "failed", "cancelled", "escalated"])
    def test_no_op_can_edit_a_frozen_node(self, state: str) -> None:
        instances = {
            "root.children[1]": NodeInstance(path="root.children[1]", state=InstanceState(state))
        }
        for op_name in ("update_node", "delete", "move", "skip"):
            raw = {"op": op_name, "node_id": "b"}
            if op_name == "update_node":
                raw["fields"] = {"prompt": "x"}
            if op_name == "move":
                raw["parent_id"] = "s"
            ops, _ = M.parse_batch([raw])
            issues = M.validate_batch(ops, Node.from_dict(CHAIN["root"]), instances)
            assert "WF_MUT_FROZEN_NODE" in [i.code for i in issues], f"{op_name}/{state}"

    def test_every_accepted_batch_leaves_the_spec_acyclic(self) -> None:
        """Acyclicity is re-checked on the CANDIDATE, so a batch cannot smuggle in a cycle."""
        result = M.prepare_batch(
            [
                {
                    "op": "insert",
                    "parent_id": "s",
                    "index": 0,
                    "node": {
                        "kind": "transform",
                        "id": "cycle",
                        "config": {"expr": "{{nodes.cycle.output}}"},
                    },
                }
            ],
            CHAIN,
            {},
        )
        # Either rejected outright, or accepted with a spec the validator calls acyclic —
        # never accepted with a cycle in it.
        if result.ok:
            from personalclaw.workflows.validator import validate_spec

            assert validate_spec(result.spec).ok


# ── checkpoints ──────────────────────────────────────────────────────────────


class TestCheckpoints:
    async def test_a_checkpoint_captures_the_instance_map(self) -> None:
        c = await _completed()
        cp = CP.save_checkpoint(c.run, c.instances, note="after first pass")
        assert cp.id == "001" and cp.note == "after first pass"
        loaded = CP.load_checkpoint(c.run.id, "001")
        assert loaded is not None
        assert set(loaded.instance_map()) == set(c.instances)

    async def test_checkpoint_ids_increment_and_sort(self) -> None:
        c = await _completed()
        first = CP.save_checkpoint(c.run, c.instances)
        second = CP.save_checkpoint(c.run, c.instances)
        assert (first.id, second.id) == ("001", "002")
        assert [cp.id for cp in CP.list_checkpoints(c.run.id)] == ["001", "002"]

    async def test_a_checkpoint_does_not_copy_outputs(self) -> None:
        """Outputs are content-addressed in `outputs/`; copying them per checkpoint would
        double disk cost for no gain."""
        c = await _completed()
        cp = CP.save_checkpoint(c.run, c.instances)
        raw = json.loads(
            (store.run_dir(c.run.id) / CP.CHECKPOINT_DIR / f"{cp.id}.json").read_text()
        )
        assert "instances" in raw
        assert not any("output" in str(k) for k in raw if k != "instances")

    def test_an_unknown_checkpoint_reads_as_none(self) -> None:
        assert CP.load_checkpoint("nope", "001") is None

    def test_listing_a_run_with_no_checkpoints_is_empty(self) -> None:
        assert CP.list_checkpoints("nope") == []


# ── property: fork isolation ─────────────────────────────────────────────────


class TestForkIsolation:
    async def test_the_parent_is_untouched_by_a_fork(self) -> None:
        c = await _completed()
        before_state = store.read_state(c.run.id)
        before_spec = store.read_spec(c.run.id)
        before_journal = store.read_jsonl(c.run.id, "journal.jsonl")

        CP.fork_run(c.run, c.spec, c.instances)

        assert {p: i.to_dict() for p, i in store.read_state(c.run.id).items()} == {
            p: i.to_dict() for p, i in before_state.items()
        }
        assert store.read_spec(c.run.id) == before_spec
        assert store.read_jsonl(c.run.id, "journal.jsonl") == before_journal

    async def test_the_child_gets_its_own_run_dir_and_provenance(self) -> None:
        c = await _completed()
        result = CP.fork_run(c.run, c.spec, c.instances, note="try stricter")
        child = store.get(result.child.id)
        assert child.id != c.run.id
        assert child.parent_run_id == c.run.id
        assert child.forked_from["run_id"] == c.run.id
        assert child.forked_from["note"] == "try stricter"

    async def test_the_whole_tree_shares_one_root_run_id(self) -> None:
        """So 'show me this run tree' stays ONE query rather than a recursive walk."""
        c = await _completed()
        first = CP.fork_run(c.run, c.spec, c.instances)
        second = CP.fork_run(first.child, c.spec, c.instances)
        assert store.get(first.child.id).root_run_id == c.run.id
        assert store.get(second.child.id).root_run_id == c.run.id

    async def test_writing_to_the_child_does_not_reach_the_parent(self) -> None:
        c = await _completed()
        result = CP.fork_run(c.run, c.spec, c.instances)
        child_instances = store.read_state(result.child.id)
        for inst in child_instances.values():
            inst.state = InstanceState.PENDING
        store.write_state(result.child.id, child_instances)
        # The parent's nodes are still done.
        assert all(i.state in SUCCESS_STATES for i in store.read_state(c.run.id).values())

    async def test_the_shared_axes_are_surfaced_not_hidden(self) -> None:
        """A caller who believes a fork is a sandbox will corrupt both runs."""
        c = await _completed()
        result = CP.fork_run(c.run, c.spec, c.instances)
        body = result.to_dict()
        assert body["shared_axes"]
        assert any("filesystem" in axis for axis in body["shared_axes"])
        assert result.isolation_notes and all(
            n.startswith("NOT isolated") for n in result.isolation_notes
        )

    async def test_the_fork_axis_is_threaded_into_the_childs_inputs(self) -> None:
        """Per-fork disambiguation for the axes a fork cannot isolate (WF2-R2 am.) — a
        unique-name generator seeds off it instead of colliding with the parent."""
        c = await _completed()
        result = CP.fork_run(c.run, c.spec, c.instances)
        assert store.get(result.child.id).inputs["__fork_axis"] == result.fork_axis
        assert c.run.id in result.fork_axis


class TestForkIsCheap:
    async def test_the_journal_prefix_carries_over(self) -> None:
        """This is WHY a fork is cheap: cache keys carry no run id, so a copied prefix
        HITS."""
        c = await _completed()
        result = CP.fork_run(c.run, c.spec, c.instances)
        assert result.cached_prefix > 0
        assert store.read_jsonl(result.child.id, "journal.jsonl")

    async def test_the_outputs_come_along_so_a_cache_hit_reads_a_real_file(self) -> None:
        """Without the outputs, a hit (keys match) reads a MISSING file and resolves a
        binding to None — a silent wrong answer, the exact failure this slice prevents."""
        c = await _completed()
        result = CP.fork_run(c.run, c.spec, c.instances)
        for path, inst in store.read_state(result.child.id).items():
            if inst.state in SUCCESS_STATES:
                assert store.read_output(result.child.id, path) is not None, path

    async def test_a_forked_run_resumes_from_cache_with_no_model_calls(self) -> None:
        """The end-to-end payoff: a fork of a COMPLETE run re-runs nothing."""
        c = await _completed()
        result = CP.fork_run(c.run, c.spec, c.instances)
        fn = _echo()
        child = RunController(
            store.get(result.child.id), c.spec, services=EngineServices(completion=fn)
        )
        assert await child.run_to_completion(timeout=20) == RunStatus.COMPLETE
        assert fn.calls == []  # everything served from the copied journal

    async def test_a_fork_from_a_checkpoint_uses_that_snapshot(self) -> None:
        c = await _completed()
        # A checkpoint taken when only `a` was done.
        partial = {
            "root.children[0]": NodeInstance(path="root.children[0]", state=InstanceState.DONE)
        }
        cp = CP.save_checkpoint(c.run, partial, note="early")
        result = CP.fork_run(c.run, c.spec, c.instances, checkpoint_id=cp.id)
        child_state = store.read_state(result.child.id)
        assert set(child_state) == {"root.children[0]"}

    async def test_an_unknown_checkpoint_raises_rather_than_forking_wrong(self) -> None:
        c = await _completed()
        with pytest.raises(ValueError, match="unknown checkpoint"):
            CP.fork_run(c.run, c.spec, c.instances, checkpoint_id="999")


class TestForkThroughTheMutationQueue:
    async def test_the_fork_op_branches_a_child_and_leaves_this_run_alone(self) -> None:
        c = await _completed()
        before = {p: i.state for p, i in store.read_state(c.run.id).items()}
        body = c.submit_mutation([{"op": "fork", "note": "stricter judge"}], confirm=True)
        assert body["ok"]
        c._drain_mutations()
        children, _total = store.list_runs()
        forks = [r for r in children if r.parent_run_id == c.run.id]
        assert len(forks) == 1
        assert forks[0].forked_from["note"] == "stricter judge"
        # This run is untouched — the whole point of fork versus rewind.
        assert {p: i.state for p, i in store.read_state(c.run.id).items()} == before

    async def test_a_forked_child_starts_in_draft(self) -> None:
        """A fork exists to be edited before it runs; auto-starting would race that edit."""
        c = await _completed()
        c.submit_mutation([{"op": "fork"}], confirm=True)
        c._drain_mutations()
        runs, _total = store.list_runs()
        child = [r for r in runs if r.parent_run_id == c.run.id][0]
        assert child.status == RunStatus.DRAFT

    async def test_the_fork_is_journaled_as_a_child_attach(self) -> None:
        from personalclaw.workflows.journal import CHILD_RUN_ATTACH, ledger

        c = await _completed()
        c.submit_mutation([{"op": "fork"}], confirm=True)
        c._drain_mutations()
        attaches = [e for e in ledger(c.run.id) if e.get("kind") == CHILD_RUN_ATTACH]
        assert len(attaches) == 1 and attaches[0]["parent_run_id"] == c.run.id

    async def test_an_unknown_checkpoint_is_journaled_as_rejected_not_raised(self) -> None:
        """A bad checkpoint id must not take the controller's tick loop down."""
        from personalclaw.workflows.journal import MUTATION_REJECTED, ledger

        c = await _completed()
        c.submit_mutation([{"op": "fork", "checkpoint_id": "999"}], confirm=True)
        c._drain_mutations()
        rejects = [e for e in ledger(c.run.id) if e.get("kind") == MUTATION_REJECTED]
        assert rejects and rejects[0]["issues"][0]["code"] == "WF_MUT_UNKNOWN_CHECKPOINT"


class TestPruneFork:
    async def test_pruning_removes_the_child_and_its_row(self) -> None:
        c = await _completed()
        result = CP.fork_run(c.run, c.spec, c.instances)
        assert CP.prune_fork(result.child.id)
        assert not store.run_dir(result.child.id).exists()
        assert store.get(result.child.id) is None

    async def test_pruning_leaves_the_parent_alone(self) -> None:
        c = await _completed()
        result = CP.fork_run(c.run, c.spec, c.instances)
        CP.prune_fork(result.child.id)
        assert store.get(c.run.id) is not None
        assert store.read_state(c.run.id)

    def test_a_traversal_id_is_refused(self) -> None:
        """A stored run id is not a trust boundary (WF2-R13 deletion-sweep contract)."""
        assert not CP.prune_fork("../../etc")


# ── revert ───────────────────────────────────────────────────────────────────


class TestRevert:
    async def test_a_leaf_node_reverts_cleanly(self) -> None:
        """Nothing consumes `c`, so undoing it is safe."""
        c = await _completed()
        paths, conflict = CP.revert_node(c.root, c.instances, "c")
        assert conflict is None and paths == ["root.children[2]"]

    async def test_a_consumed_node_refuses_with_the_dependents_named(self) -> None:
        """Named, not counted: the whole reason to refuse is so the user can decide."""
        c = await _completed()
        paths, conflict = CP.revert_node(c.root, c.instances, "a")
        assert paths == []
        assert conflict is not None
        assert conflict.node_id == "a" and conflict.dependents == ["b"]

    async def test_an_unconsumed_node_reverts_even_mid_chain(self) -> None:
        """`b` feeds `c`; reset `c` first and reverting `b` becomes legal."""
        c = await _completed()
        c.instances["root.children[2]"].state = InstanceState.PENDING
        paths, conflict = CP.revert_node(c.root, c.instances, "b")
        assert conflict is None and paths == ["root.children[1]"]

    async def test_reverting_archives_the_output(self) -> None:
        c = await _completed()
        paths, _conflict = CP.revert_node(c.root, c.instances, "c")
        reset = CP.revert_paths(c.run.id, c.instances, paths, version=c.run.spec_version)
        assert reset == 1
        assert c.instances["root.children[2]"].state == InstanceState.PENDING
        assert list((store.run_dir(c.run.id) / "outputs" / "attic").rglob("*.json"))

    async def test_the_conflict_serializes_for_a_409(self) -> None:
        c = await _completed()
        _paths, conflict = CP.revert_node(c.root, c.instances, "a")
        assert conflict.to_dict() == {"node_id": "a", "dependents": ["b"]}

"""Mid-flight mutation (WF2-R2, WF2-R20) — the cascade, the grammar, the transaction.

The load-bearing claims, each defending a specific way this goes wrong:

* **the cascade follows BINDINGS, not the tree** — a later SIBLING reading an edited
  node's output is not a tree descendant, and resetting only the subtree leaves it with a
  stale input: a silently inconsistent run, the worst failure mode because nothing looks
  broken;
* **a rejected batch writes NOTHING** (WF2-R20e) — ops apply to a deep copy, so a failure
  leaves the prior spec as the single source of truth;
* frozen (RUNNING/terminal) nodes reject, EXCEPT under `rewind`/`run_from`, which exist to
  unfreeze;
* mixed addressing and overlapping edits are rejected as batches, not silently reconciled;
* structural ops apply in descending index, so one op cannot shift another's coordinates;
* **the epoch bumps only on force** — a rewind that did not change inputs must replay from
  cache, not pay to recompute the same answer;
* `id`/`kind` are immutable mid-run: they are the identity every binding and journal key
  names.
"""

from __future__ import annotations

import pytest

from personalclaw.workflows import mutations as M
from personalclaw.workflows.models import InstanceState, Node, NodeInstance

# A spec whose SIBLING binds an earlier node's output — the WF2-R2 shape. `report` is not
# a tree descendant of `gather`, but it consumes its output.
SPEC = {
    "name": "cascade",
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
                "config": {"prompt": "report on {{nodes.analyze.output}}"},
            },
            {"kind": "transform", "id": "unrelated", "config": {"expr": "static"}},
        ],
    },
}


def _root(spec: dict | None = None) -> Node:
    return Node.from_dict((spec or SPEC)["root"])


def _instances(**states: str) -> dict[str, NodeInstance]:
    """Build an instance map keyed by the paths of SPEC's children."""
    order = ["gather", "analyze", "report", "unrelated"]
    out: dict[str, NodeInstance] = {}
    for i, node_id in enumerate(order):
        state = states.get(node_id)
        if state is None:
            continue
        out[f"root.children[{i}]"] = NodeInstance(
            path=f"root.children[{i}]", state=InstanceState(state)
        )
    return out


# ── the binding graph ────────────────────────────────────────────────────────


class TestBindingGraph:
    def test_the_consumer_graph_is_built_from_bindings(self) -> None:
        graph = M.dependents_graph(_root())
        assert graph["gather"] == {"analyze"}
        assert graph["analyze"] == {"report"}
        assert "unrelated" not in graph  # nothing consumes it

    def test_the_closure_reaches_transitive_consumers(self) -> None:
        assert M.binding_closure(_root(), {"gather"}) == {"gather", "analyze", "report"}

    def test_the_closure_excludes_unrelated_nodes(self) -> None:
        """The point of a binding closure: don't re-run work that cannot have changed."""
        assert "unrelated" not in M.binding_closure(_root(), {"gather"})

    def test_a_sibling_consumer_is_in_the_closure_though_not_a_tree_descendant(self) -> None:
        """WF2-R2's core correction. `report` is a SIBLING of `gather`, not a descendant —
        a tree-based reset would leave it holding a stale input."""
        tree_descendants = {n.id for _p, n in M.walk(_root()) if n.id} - {"s"}
        assert "report" in tree_descendants  # it IS in the tree, as a sibling
        closure = M.binding_closure(_root(), {"gather"})
        assert "report" in closure

    def test_the_closure_is_cycle_safe(self) -> None:
        """A malformed spec must not hang the controller computing a preview."""
        cyclic = {
            "kind": "sequence",
            "id": "s",
            "children": [
                {"kind": "transform", "id": "a", "config": {"expr": "{{nodes.b.output}}"}},
                {"kind": "transform", "id": "b", "config": {"expr": "{{nodes.a.output}}"}},
            ],
        }
        assert M.binding_closure(Node.from_dict(cyclic), {"a"}) == {"a", "b"}


class TestCascadePreview:
    def test_completed_downstream_work_needs_confirmation(self) -> None:
        """A user who edits one prompt and unknowingly re-runs twelve stages has been
        billed for a surprise."""
        preview = M.cascade_preview(
            _root(),
            _instances(gather="done", analyze="done", report="done"),
            {"gather"},
        )
        assert preview.needs_confirmation
        assert set(preview.rerun) == {"gather", "analyze", "report"}

    def test_an_untouched_run_needs_no_confirmation(self) -> None:
        preview = M.cascade_preview(_root(), {}, {"gather"})
        assert not preview.needs_confirmation

    def test_committed_effects_in_the_cascade_are_surfaced(self) -> None:
        """Surfaced, never silently re-fired — this is what `redo_effects` gates."""
        from personalclaw.workflows.effects import EffectRecord, EffectStatus

        effects = {
            "root.children[1]": [
                EffectRecord(idempotency_key="k", effect_status=EffectStatus.COMMITTED, epoch=0)
            ]
        }
        preview = M.cascade_preview(
            _root(), _instances(gather="done", analyze="done"), {"gather"}, effects=effects
        )
        assert "analyze" in preview.committed_effects

    def test_a_compensated_effect_is_not_surfaced(self) -> None:
        from personalclaw.workflows.effects import EffectRecord, EffectStatus

        effects = {
            "root.children[1]": [
                EffectRecord(idempotency_key="k", effect_status=EffectStatus.COMMITTED, epoch=0),
                EffectRecord(idempotency_key="k", effect_status=EffectStatus.COMPENSATED),
            ]
        }
        preview = M.cascade_preview(
            _root(), _instances(gather="done", analyze="done"), {"gather"}, effects=effects
        )
        assert preview.committed_effects == []


# ── grammar hardening (WF2-R20) ──────────────────────────────────────────────


class TestOpParsing:
    def test_canonical_ops_parse(self) -> None:
        op = M.Op.from_dict({"op": "update_node", "node_id": "a", "fields": {"prompt": "x"}})
        assert op.kind == M.OpKind.UPDATE_NODE and op.fields == {"prompt": "x"}

    @pytest.mark.parametrize(
        "alias,expected",
        [
            ("edit_node", M.OpKind.UPDATE_NODE),
            ("add_node", M.OpKind.INSERT),
            ("remove", M.OpKind.DELETE),
            ("reset", M.OpKind.REWIND),
            ("rerun_from", M.OpKind.RUN_FROM),
        ],
    )
    def test_llm_aliases_are_normalized(self, alias: str, expected: M.OpKind) -> None:
        """A model that wrote `edit_node` meant `update_node`; rejecting it teaches
        nothing and costs a turn."""
        assert M.Op.from_dict({"op": alias, "node_id": "a"}).kind == expected

    def test_field_aliases_are_normalized(self) -> None:
        op = M.Op.from_dict({"op": "update_node", "node_id": "a", "fields": {"model": "fast"}})
        assert op.fields == {"model_tier": "fast"}

    def test_an_unknown_op_raises_rather_than_guessing(self) -> None:
        """Guessing would apply the WRONG edit to a live run."""
        with pytest.raises(ValueError, match="unknown mutation op"):
            M.Op.from_dict({"op": "frobnicate", "node_id": "a"})

    def test_a_batch_collects_every_parse_failure(self) -> None:
        """All at once, so a model that got two ops wrong learns both in one reprompt."""
        ops, issues = M.parse_batch([{"op": "nope"}, {"op": "also_nope"}])
        assert ops == [] and len(issues) == 2


class TestBatchValidation:
    def test_mixed_addressing_is_rejected(self) -> None:
        ops, _ = M.parse_batch(
            [
                {"op": "update_node", "node_id": "gather", "fields": {"expr": 2}},
                {"op": "insert", "parent_id": "s", "index": 0, "node": {"kind": "transform"}},
            ]
        )
        codes = [i.code for i in M.validate_batch(ops, _root(), {})]
        assert "WF_MUT_MIXED_ADDRESSING" in codes

    def test_overlapping_edits_are_rejected(self) -> None:
        ops, _ = M.parse_batch(
            [
                {"op": "update_node", "node_id": "gather", "fields": {"expr": 1}},
                {"op": "skip", "node_id": "gather"},
            ]
        )
        codes = [i.code for i in M.validate_batch(ops, _root(), {})]
        assert "WF_MUT_OVERLAPPING_EDITS" in codes

    def test_an_unknown_node_is_rejected(self) -> None:
        ops, _ = M.parse_batch([{"op": "skip", "node_id": "ghost"}])
        issues = M.validate_batch(ops, _root(), {})
        assert [i.code for i in issues] == ["WF_MUT_UNKNOWN_NODE"]

    def test_a_running_node_cannot_be_edited(self) -> None:
        ops, _ = M.parse_batch([{"op": "update_node", "node_id": "analyze", "fields": {"a": 1}}])
        issues = M.validate_batch(ops, _root(), _instances(analyze="running"))
        assert [i.code for i in issues] == ["WF_MUT_FROZEN_NODE"]

    def test_a_completed_node_cannot_be_edited(self) -> None:
        ops, _ = M.parse_batch([{"op": "update_node", "node_id": "gather", "fields": {"a": 1}}])
        issues = M.validate_batch(ops, _root(), _instances(gather="done"))
        assert [i.code for i in issues] == ["WF_MUT_FROZEN_NODE"]

    def test_rewind_may_target_a_completed_node(self) -> None:
        """Unfreezing completed work is exactly what rewind is FOR."""
        ops, _ = M.parse_batch([{"op": "rewind", "node_id": "gather"}])
        assert M.validate_batch(ops, _root(), _instances(gather="done")) == []

    def test_run_from_may_target_a_completed_node(self) -> None:
        ops, _ = M.parse_batch([{"op": "run_from", "node_id": "analyze"}])
        assert M.validate_batch(ops, _root(), _instances(analyze="done")) == []

    def test_a_pending_node_is_mutable(self) -> None:
        ops, _ = M.parse_batch([{"op": "update_node", "node_id": "report", "fields": {"a": 1}}])
        assert M.validate_batch(ops, _root(), _instances(report="pending")) == []

    def test_an_empty_update_is_rejected(self) -> None:
        ops, _ = M.parse_batch([{"op": "update_node", "node_id": "report", "fields": {}}])
        assert [i.code for i in M.validate_batch(ops, _root(), {})] == ["WF_MUT_EMPTY_UPDATE"]

    def test_insert_without_a_node_payload_is_rejected(self) -> None:
        ops, _ = M.parse_batch([{"op": "insert", "parent_id": "s", "index": 0}])
        assert "WF_MUT_INSERT_NO_NODE" in [i.code for i in M.validate_batch(ops, _root(), {})]

    def test_insert_with_an_invalid_node_is_rejected(self) -> None:
        ops, _ = M.parse_batch(
            [{"op": "insert", "parent_id": "s", "index": 0, "node": {"kind": "nonsense"}}]
        )
        assert "WF_MUT_INSERT_BAD_NODE" in [i.code for i in M.validate_batch(ops, _root(), {})]

    def test_moving_a_node_into_its_own_subtree_is_rejected(self) -> None:
        """It would silently detach a whole region from the graph."""
        nested = {
            "kind": "sequence",
            "id": "outer",
            "children": [
                {
                    "kind": "sequence",
                    "id": "mid",
                    "children": [{"kind": "transform", "id": "leaf", "config": {"expr": 1}}],
                }
            ],
        }
        ops, _ = M.parse_batch([{"op": "move", "node_id": "mid", "parent_id": "leaf"}])
        codes = [i.code for i in M.validate_batch(ops, Node.from_dict(nested), {})]
        assert "WF_MUT_MOVE_INTO_SELF" in codes

    def test_set_input_needs_overrides(self) -> None:
        ops, _ = M.parse_batch([{"op": "set_input", "overrides": {}}])
        assert [i.code for i in M.validate_batch(ops, _root(), {})] == ["WF_MUT_EMPTY_OVERRIDES"]


# ── application ──────────────────────────────────────────────────────────────


class TestApply:
    def test_update_node_patches_config_on_a_copy(self) -> None:
        candidate, issues = M.apply_batch(
            M.parse_batch(
                [{"op": "update_node", "node_id": "gather", "fields": {"expr": {"n": 9}}}]
            )[0],
            SPEC,
            {},
        )
        assert issues == []
        assert candidate["root"]["children"][0]["config"]["expr"] == {"n": 9}
        # The original is untouched — the atomic-failure contract.
        assert SPEC["root"]["children"][0]["config"]["expr"] == {"n": 1}

    def test_changing_id_or_kind_is_refused(self) -> None:
        """They are the identity every binding and journal key names — changing them
        mid-run orphans both."""
        for field_name in ("id", "kind"):
            _cand, issues = M.apply_batch(
                M.parse_batch(
                    [{"op": "update_node", "node_id": "gather", "fields": {field_name: "x"}}]
                )[0],
                SPEC,
                {},
            )
            assert [i.code for i in issues] == ["WF_MUT_IMMUTABLE_FIELD"], field_name

    def test_delete_removes_the_node(self) -> None:
        candidate, issues = M.apply_batch(
            M.parse_batch([{"op": "delete", "node_id": "unrelated"}])[0], SPEC, {}
        )
        assert issues == []
        assert [c["id"] for c in candidate["root"]["children"]] == [
            "gather",
            "analyze",
            "report",
        ]

    def test_insert_places_a_node_at_the_index(self) -> None:
        candidate, issues = M.apply_batch(
            M.parse_batch(
                [
                    {
                        "op": "insert",
                        "parent_id": "s",
                        "index": 1,
                        "node": {"kind": "transform", "id": "new", "config": {"expr": 1}},
                    }
                ]
            )[0],
            SPEC,
            {},
        )
        assert issues == []
        assert [c["id"] for c in candidate["root"]["children"]][:3] == [
            "gather",
            "new",
            "analyze",
        ]

    def test_move_detaches_then_reinserts(self) -> None:
        candidate, issues = M.apply_batch(
            M.parse_batch([{"op": "move", "node_id": "unrelated", "parent_id": "s", "index": 0}])[
                0
            ],
            SPEC,
            {},
        )
        assert issues == []
        assert [c["id"] for c in candidate["root"]["children"]][0] == "unrelated"
        assert len(candidate["root"]["children"]) == 4  # moved, not duplicated

    def test_structural_ops_apply_in_descending_index(self) -> None:
        """Coordinate-preserving order (WF2-R20c): otherwise the first delete shifts the
        index the second one named, and the wrong node dies."""
        candidate, issues = M.apply_batch(
            M.parse_batch(
                [
                    {"op": "insert", "index": 1, "node": {"kind": "transform", "id": "x"}},
                    {"op": "insert", "index": 3, "node": {"kind": "transform", "id": "y"}},
                ]
            )[0],
            SPEC,
            {},
        )
        assert issues == []
        ids = [c["id"] for c in candidate["root"]["children"]]
        assert ids.index("x") == 1 and ids.index("y") == 4

    def test_set_input_merges_overrides(self) -> None:
        candidate, issues = M.apply_batch(
            M.parse_batch([{"op": "set_input", "overrides": {"since": "2h"}}])[0],
            {**SPEC, "inputs": {"other": 1}},
            {},
        )
        assert issues == []
        assert candidate["inputs"] == {"other": 1, "since": "2h"}

    def test_skip_is_recorded_on_the_spec(self) -> None:
        """Recorded so a RESUMED run re-derives the same decision; the instance-state
        write stays the controller's (single-writer rule)."""
        candidate, issues = M.apply_batch(
            M.parse_batch([{"op": "skip", "node_id": "report"}])[0], SPEC, {}
        )
        assert issues == []
        assert candidate["root"]["children"][2]["config"]["__skipped"] is True

    def test_inline_subworkflow_is_a_typed_refusal(self) -> None:
        """A typed refusal, never a silent no-op that makes a spec look applied."""
        _cand, issues = M.apply_batch(
            M.parse_batch([{"op": "inline_subworkflow", "node_id": "gather"}])[0], SPEC, {}
        )
        assert [i.code for i in issues] == ["WF_MUT_UNSUPPORTED"]


# ── the whole transaction ────────────────────────────────────────────────────


class TestPrepareBatch:
    def test_a_valid_batch_returns_a_candidate_spec(self) -> None:
        result = M.prepare_batch(
            [{"op": "update_node", "node_id": "report", "fields": {"prompt": "new"}}],
            SPEC,
            {},
        )
        assert result.ok and result.spec is not None
        assert result.spec["root"]["children"][2]["config"]["prompt"] == "new"

    def test_a_rejected_batch_returns_no_spec_at_all(self) -> None:
        """WF2-R20e: nothing is written, so the prior spec stays the source of truth."""
        result = M.prepare_batch([{"op": "skip", "node_id": "ghost"}], SPEC, {})
        assert not result.ok and result.spec is None

    def test_a_batch_that_breaks_the_spec_is_rejected_after_applying_to_the_copy(self) -> None:
        """Individually legal, collectively broken: deleting `analyze` orphans the binding
        `report` holds on it. Only re-validating the CANDIDATE catches this."""
        result = M.prepare_batch([{"op": "delete", "node_id": "analyze"}], SPEC, {})
        assert not result.ok and result.spec is None
        assert result.issues

    def test_the_preview_rides_along_with_a_valid_batch(self) -> None:
        result = M.prepare_batch(
            [{"op": "rewind", "node_id": "gather"}],
            SPEC,
            _instances(gather="done", analyze="done", report="done"),
        )
        assert result.ok
        assert set(result.preview.rerun) == {"gather", "analyze", "report"}
        assert result.preview.needs_confirmation

    def test_set_input_seeds_the_cascade_from_the_whole_graph(self) -> None:
        """An input override reaches every node that reads an input."""
        result = M.prepare_batch(
            [{"op": "set_input", "overrides": {"x": 1}}],
            SPEC,
            _instances(gather="done"),
        )
        assert result.ok and "gather" in result.preview.rerun

    def test_an_unreadable_spec_is_reported_not_raised(self) -> None:
        result = M.prepare_batch([], {"root": {"kind": "nonsense"}}, {})
        assert not result.ok and [i.code for i in result.issues] == ["WF_MUT_BAD_SPEC"]

    def test_the_result_serializes(self) -> None:
        d = M.prepare_batch([{"op": "skip", "node_id": "report"}], SPEC, {}).to_dict()
        assert set(d) == {"ok", "issues", "ops", "preview"}


# ── history + epoch ──────────────────────────────────────────────────────────


class TestHistoryAndEpoch:
    def test_a_history_record_carries_structured_ops_and_a_spec_hash(self) -> None:
        """Structured ops, not a textual diff: a later refiner needs to know what KIND of
        correction a human made, which a diff destroys."""
        ops, _ = M.parse_batch([{"op": "update_node", "node_id": "report", "fields": {"a": 1}}])
        record = M.history_record(ops, actor="chat", version=3, spec=SPEC)
        assert record["version"] == 3 and record["actor"] == "chat"
        assert record["ops"][0]["op"] == "update_node"
        assert record["spec_hash"]

    def test_the_record_keeps_the_authors_original_payload(self) -> None:
        ops, _ = M.parse_batch([{"op": "edit_node", "node_id": "report", "fields": {"model": "x"}}])
        record = M.history_record(ops, actor="chat", version=1, spec=SPEC)
        assert record["ops"][0]["op"] == "update_node"  # what applied
        assert record["raw_ops"][0]["op"] == "edit_node"  # what was written

    def test_the_epoch_bumps_only_on_force(self) -> None:
        """WF2-R2 #4: a rewind that did not change inputs must replay from cache rather
        than pay to recompute the same answer."""
        instances = {"root.children[0]": NodeInstance(path="root.children[0]", epoch=2)}
        assert M.next_epoch(instances, ["root.children[0]"], force=False) == 2
        assert M.next_epoch(instances, ["root.children[0]"], force=True) == 3

    def test_the_epoch_takes_the_max_across_the_region(self) -> None:
        instances = {
            "a": NodeInstance(path="a", epoch=1),
            "b": NodeInstance(path="b", epoch=4),
        }
        assert M.next_epoch(instances, ["a", "b"], force=True) == 5

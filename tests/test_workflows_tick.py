"""The frontier — purity, container semantics, lanes, and the WF2-R18 join rules.

`frontier()` is a pure function, and these tests lean on that hard: every case is a
`(spec, states) -> decision` assertion with no engine, no lock and no clock. That is the
payoff of keeping scheduling separate from execution — the scheduler's edge cases are
cheap to pin down here instead of being discovered in a live run.

The two WF2-R18 regressions are acceptance criteria for the slice, and they guard
opposite failure directions:

* an UNTAKEN conditional path must never deadlock a downstream join, and
* an async fan-out must never fire its join early on the one fast leg.

Getting one right by breaking the other is the classic way this scheduler goes wrong, so
both live here side by side.
"""

from __future__ import annotations

from personalclaw.workflows.models import (
    InstanceState,
    JoinMode,
    Node,
    NodeKind,
)
from personalclaw.workflows.tick import (
    DEFAULT_LANE_CAPS,
    Limits,
    container_outcome,
    edge_key,
    frontier,
    loop_should_continue,
)


def _node(d: dict) -> Node:
    return Node.from_dict(d)


def _paths(fr) -> list[str]:
    return [r.path for r in fr.ready]


SEQ = {
    "kind": "sequence",
    "id": "s",
    "children": [
        {"kind": "transform", "id": "a", "config": {"expr": "1"}},
        {"kind": "transform", "id": "b", "config": {"expr": "2"}},
        {"kind": "transform", "id": "c", "config": {"expr": "3"}},
    ],
}


class TestPurity:
    def test_same_inputs_give_the_same_answer(self) -> None:
        """Determinism is what makes rewind tractable: state is re-derived, not patched."""
        root = _node(SEQ)
        states = {"root.children[0]": InstanceState.DONE}
        first = _paths(frontier(root, states))
        for _ in range(5):
            assert _paths(frontier(root, states)) == first

    def test_the_state_map_is_not_mutated(self) -> None:
        root = _node(SEQ)
        states = {"root.children[0]": InstanceState.DONE}
        before = dict(states)
        frontier(root, states)
        assert states == before

    def test_admission_order_is_stable_when_a_lane_is_oversubscribed(self) -> None:
        """Two identical runs must launch the same nodes in the same order, or the
        journal's replay guarantees mean nothing."""
        root = _node(
            {
                "kind": "parallel",
                "id": "p",
                "children": [
                    {"kind": "infer", "id": f"n{i}", "config": {"prompt": "x"}} for i in range(6)
                ],
            }
        )
        lim = Limits(lanes={"llm": 2, "io": 2, "compute": 64})
        runs = [_paths(frontier(root, {}, limits=lim)) for _ in range(5)]
        assert all(r == runs[0] for r in runs)
        assert len(runs[0]) == 2


class TestSequence:
    def test_one_child_at_a_time_in_order(self) -> None:
        root = _node(SEQ)
        assert _paths(frontier(root, {})) == ["root.children[0]"]
        assert _paths(frontier(root, {"root.children[0]": InstanceState.DONE})) == [
            "root.children[1]"
        ]

    def test_completion_is_derived_from_children(self) -> None:
        root = _node(SEQ)
        states = {f"root.children[{i}]": InstanceState.DONE for i in range(3)}
        fr = frontier(root, states)
        assert fr.complete and fr.outcome == InstanceState.DONE

    def test_a_degraded_child_degrades_the_container(self) -> None:
        """DEGRADED is a SUCCESS with provenance — the run completes, and the reason
        stays visible rather than being flattened to `done`."""
        root = _node(SEQ)
        states = {
            "root.children[0]": InstanceState.DONE,
            "root.children[1]": InstanceState.DEGRADED,
            "root.children[2]": InstanceState.DONE,
        }
        fr = frontier(root, states)
        assert fr.complete and fr.outcome == InstanceState.DEGRADED


class TestParallelAndNeeds:
    def test_all_children_are_admitted_at_once(self) -> None:
        root = _node(
            {
                "kind": "parallel",
                "id": "p",
                "children": [
                    {"kind": "transform", "id": "a", "config": {"expr": "1"}},
                    {"kind": "transform", "id": "b", "config": {"expr": "2"}},
                ],
            }
        )
        assert len(_paths(frontier(root, {}))) == 2

    def test_needs_gates_on_a_terminal_predecessor(self) -> None:
        root = _node(
            {
                "kind": "parallel",
                "id": "p",
                "children": [
                    {"kind": "transform", "id": "a", "config": {"expr": "1"}},
                    {
                        "kind": "transform",
                        "id": "b",
                        "needs": ["a"],
                        "config": {"expr": "2"},
                    },
                ],
            }
        )
        assert _paths(frontier(root, {})) == ["root.children[0]"]
        after = frontier(root, {"root.children[0]": InstanceState.DONE})
        assert "root.children[1]" in _paths(after)

    def test_a_failed_predecessor_still_satisfies_needs(self) -> None:
        """`needs` means AFTER, not AFTER-SUCCESS. What a failure means downstream is the
        child's `on_error` policy — encoding it in the scheduler would take the choice
        away from the spec author."""
        root = _node(
            {
                "kind": "parallel",
                "id": "p",
                "children": [
                    {"kind": "transform", "id": "a", "config": {"expr": "1"}},
                    {
                        "kind": "transform",
                        "id": "b",
                        "needs": ["a"],
                        "config": {"expr": "2"},
                    },
                ],
            }
        )
        fr = frontier(root, {"root.children[0]": InstanceState.FAILED})
        assert "root.children[1]" in _paths(fr)

    def test_quorum_completes_without_every_child(self) -> None:
        states = [InstanceState.DONE, InstanceState.DONE, InstanceState.FAILED]
        assert container_outcome(states, join=JoinMode.QUORUM, quorum=2) == InstanceState.DONE
        assert container_outcome(states, join=JoinMode.QUORUM, quorum=3) == InstanceState.FAILED

    def test_any_join_succeeds_on_one_success(self) -> None:
        states = [InstanceState.FAILED, InstanceState.DONE]
        assert container_outcome(states, join=JoinMode.ANY) == InstanceState.DONE


class TestActiveEdgeJoinGating:
    """WF2-R18 — the two acceptance regressions, guarding opposite failures."""

    BRANCH_JOIN = {
        "kind": "parallel",
        "id": "p",
        "children": [
            {
                "kind": "branch",
                "id": "router",
                "config": {"on": "{{inputs.kind}}"},
                "cases": {
                    "bug": {"kind": "transform", "id": "fix", "config": {"expr": "f"}},
                    "feat": {"kind": "transform", "id": "build", "config": {"expr": "b"}},
                },
            },
            {
                "kind": "transform",
                "id": "merge",
                "needs": ["router"],
                "config": {"expr": "m"},
            },
        ],
    }

    def test_a_branch_is_real_work_and_runs_before_its_cases(self) -> None:
        root = _node(self.BRANCH_JOIN)
        fr = frontier(root, {}, inputs={"kind": "bug"})
        assert _paths(fr) == ["root.children[0]"]

    def test_the_untaken_case_is_marked_for_skipping(self) -> None:
        """Marking it SKIPPED is what makes it TERMINAL, which is what lets the join
        proceed. Left pending, it would block the join forever."""
        root = _node(self.BRANCH_JOIN)
        fr = frontier(
            root,
            {"root.children[0]": InstanceState.DONE},
            inputs={"kind": "bug"},
            outputs={"router": {"case": "bug"}},
        )
        assert fr.to_skip == ["root.children[0].cases[feat]"]
        assert _paths(fr) == ["root.children[0].cases[bug]"]

    def test_an_untaken_branch_never_deadlocks_a_join(self) -> None:
        """WF2-R18 regression #1."""
        root = _node(self.BRANCH_JOIN)
        states = {
            "root.children[0]": InstanceState.DONE,
            "root.children[0].cases[feat]": InstanceState.SKIPPED,
            "root.children[0].cases[bug]": InstanceState.DONE,
        }
        fr = frontier(root, states, inputs={"kind": "bug"}, outputs={"router": {"case": "bug"}})
        assert not fr.blocked, fr.block_reason
        assert "root.children[1]" in _paths(fr)

    def test_an_async_fan_out_does_not_fire_a_join_early(self) -> None:
        """WF2-R18 regression #2. WAITING is not terminal, so the join stays gated while
        the slow legs are parked — firing on the fast leg alone would merge partial work
        and look like a complete result."""
        root = _node(
            {
                "kind": "parallel",
                "id": "p",
                "children": [
                    {"kind": "transform", "id": "fast", "config": {"expr": "q"}},
                    {"kind": "wait", "id": "slow1", "config": {"duration_secs": 60}},
                    {"kind": "wait", "id": "slow2", "config": {"duration_secs": 60}},
                    {
                        "kind": "transform",
                        "id": "join",
                        "needs": ["fast", "slow1", "slow2"],
                        "config": {"expr": "j"},
                    },
                ],
            }
        )
        states = {
            "root.children[0]": InstanceState.DONE,
            "root.children[1]": InstanceState.WAITING,
            "root.children[2]": InstanceState.WAITING,
        }
        fr = frontier(root, states)
        assert "root.children[3]" not in _paths(fr)
        assert len(fr.waiting) == 2
        assert not fr.blocked  # parked, not deadlocked

    def test_a_declined_edge_makes_its_target_skippable(self) -> None:
        """An explicitly declined edge can never be satisfied by execution, so its target
        is unreachable and gets skipped rather than waited on."""
        root = _node(
            {
                "kind": "parallel",
                "id": "p",
                "children": [
                    {
                        "kind": "branch",
                        "id": "r",
                        "config": {"on": "{{inputs.k}}"},
                        "cases": {"x": {"kind": "transform", "id": "cx", "config": {"expr": "1"}}},
                    },
                    {"kind": "transform", "id": "dead", "needs": ["r"], "config": {"expr": "2"}},
                ],
            }
        )
        fr = frontier(
            root,
            {
                "root.children[0]": InstanceState.DONE,
                "root.children[0].cases[x]": InstanceState.DONE,
            },
            inputs={"k": "x"},
            outputs={"r": {"case": "x"}},
            declined_edges={edge_key("r", "dead")},
        )
        assert "root.children[1]" in fr.to_skip
        assert "root.children[1]" not in _paths(fr)

    def test_declining_is_not_inferred_from_routing_elsewhere(self) -> None:
        """A branch routing among its CASES says nothing about a sibling whose `needs`
        merely names the branch. Inferring a decline there would starve that sibling."""
        root = _node(self.BRANCH_JOIN)
        states = {
            "root.children[0]": InstanceState.DONE,
            "root.children[0].cases[feat]": InstanceState.SKIPPED,
            "root.children[0].cases[bug]": InstanceState.DONE,
        }
        fr = frontier(
            root,
            states,
            inputs={"kind": "bug"},
            outputs={"router": {"case": "bug"}},
            # The branch declined its OTHER CASE, not the sibling.
            declined_edges={edge_key("router", "build")},
        )
        assert "root.children[1]" in _paths(fr)
        assert "root.children[1]" not in fr.to_skip


class TestForeach:
    FE = {
        "kind": "foreach",
        "id": "f",
        "config": {"items": "{{inputs.xs}}"},
        "body": {"kind": "transform", "id": "b", "config": {"expr": "{{item}}"}},
    }

    def test_one_instance_per_item(self) -> None:
        fr = frontier(_node(self.FE), {}, inputs={"xs": ["a", "b", "c"]})
        assert _paths(fr) == ["root.body#0", "root.body#1", "root.body#2"]

    def test_items_are_threaded_into_the_ready_node(self) -> None:
        fr = frontier(_node(self.FE), {}, inputs={"xs": ["x", "y"]})
        assert [r.item for r in fr.ready] == ["x", "y"]
        assert all(r.has_item for r in fr.ready)

    def test_an_unresolved_items_binding_is_not_ready_and_not_an_error(self) -> None:
        """The upstream node simply has not produced it yet."""
        node = _node(
            {
                "kind": "foreach",
                "id": "f",
                "config": {"items": "{{nodes.upstream.output}}"},
                "body": {"kind": "transform", "id": "b", "config": {"expr": "1"}},
            }
        )
        fr = frontier(node, {})
        assert not fr.ready and not fr.complete

    def test_an_empty_fan_out_is_vacuously_complete(self) -> None:
        fr = frontier(_node(self.FE), {}, inputs={"xs": []})
        assert fr.complete and fr.outcome == InstanceState.DONE

    def test_skip_policy_tolerates_a_failed_item(self) -> None:
        """One bad item must not sink the whole fan-out — but the container reports
        DEGRADED so the failure stays visible."""
        states = {
            "root.body#0": InstanceState.DONE,
            "root.body#1": InstanceState.FAILED,
        }
        fr = frontier(_node(self.FE), states, inputs={"xs": ["a", "b"]})
        assert fr.complete and fr.outcome == InstanceState.DEGRADED

    def test_halt_policy_stops_scheduling_after_a_failure(self) -> None:
        node = _node({**self.FE, "config": {**self.FE["config"], "on_item_error": "halt"}})
        states = {"root.body#0": InstanceState.FAILED}
        fr = frontier(node, states, inputs={"xs": ["a", "b", "c"]})
        assert not fr.ready


class TestLoop:
    LP = {
        "kind": "loop",
        "id": "l",
        "config": {"mode": "counted", "n": 3},
        "body": {"kind": "transform", "id": "b", "config": {"expr": "{{iter}}"}},
    }

    def test_one_iteration_in_flight_at_a_time(self) -> None:
        fr = frontier(_node(self.LP), {})
        assert _paths(fr) == ["root.body@0"]

    def test_the_iteration_counter_selects_the_instance_path(self) -> None:
        fr = frontier(
            _node(self.LP),
            {"root.body@0": InstanceState.DONE},
            iterations={"root": 1},
        )
        assert _paths(fr) == ["root.body@1"]

    def test_counted_exit(self) -> None:
        node = _node(self.LP)
        assert loop_should_continue(node, iteration=1)[0] is True
        assert loop_should_continue(node, iteration=3)[0] is False

    def test_max_iterations_is_a_hard_cap(self) -> None:
        node = _node(
            {
                **self.LP,
                "config": {"mode": "until", "condition": "{{inputs.never}}", "max_iterations": 2},
            }
        )
        keep, reason = loop_should_continue(node, iteration=2)
        assert keep is False and reason == "max_iterations"

    def test_an_unresolvable_until_condition_stops_rather_than_spins(self) -> None:
        """A loop that cannot evaluate its own exit test is broken; spinning forever is
        the worse of the two readings."""
        node = _node({**self.LP, "config": {"mode": "until", "condition": "{{nodes.gone.output}}"}})
        keep, reason = loop_should_continue(node, iteration=1)
        assert keep is False and reason == "condition_unresolvable"

    def test_until_dry_exits_on_the_declared_streak(self) -> None:
        node = _node({**self.LP, "config": {"mode": "until_dry", "streak": 2}})
        assert loop_should_continue(node, iteration=1, dry_streak=1)[0] is True
        assert loop_should_continue(node, iteration=2, dry_streak=2)[0] is False


class TestLanes:
    def test_lanes_are_derived_from_kind_never_declared(self) -> None:
        root = _node(
            {
                "kind": "parallel",
                "id": "p",
                "children": [
                    {"kind": "infer", "id": "i", "config": {"prompt": "x"}},
                    {"kind": "action", "id": "a", "config": {"provider": "bash"}},
                    {"kind": "transform", "id": "t", "config": {"expr": "1"}},
                ],
            }
        )
        lanes = {r.node.id: r.lane for r in frontier(root, {}).ready}
        assert lanes == {"i": "llm", "a": "io", "t": "compute"}

    def test_excess_ready_work_is_deferred_not_dropped(self) -> None:
        root = _node(
            {
                "kind": "parallel",
                "id": "p",
                "children": [
                    {"kind": "infer", "id": f"n{i}", "config": {"prompt": "x"}} for i in range(5)
                ],
            }
        )
        fr = frontier(root, {}, limits=Limits(lanes={"llm": 2, "io": 2, "compute": 64}))
        assert len(fr.ready) == 2
        assert len(fr.deferred) == 3
        assert not fr.blocked  # deferred work is not a deadlock

    def test_already_running_nodes_count_against_the_cap(self) -> None:
        root = _node(
            {
                "kind": "parallel",
                "id": "p",
                "children": [
                    {"kind": "infer", "id": f"n{i}", "config": {"prompt": "x"}} for i in range(4)
                ],
            }
        )
        fr = frontier(
            root,
            {},
            limits=Limits(lanes={"llm": 2, "io": 2, "compute": 64}),
            running_lanes={"llm": 1},
        )
        assert len(fr.ready) == 1

    def test_a_bare_total_is_split_across_lanes(self) -> None:
        """Back-compat: a config carrying one number keeps working, and the LLM lane gets
        the lion's share because that is where a workflow spends its time."""
        lim = Limits.from_config(6)
        assert lim.cap("llm") == 4
        assert lim.cap("io") == 2

    def test_a_lane_dict_is_honored_and_unknown_lanes_ignored(self) -> None:
        lim = Limits.from_config({"llm": 9, "bogus": 3})
        assert lim.cap("llm") == 9
        assert lim.cap("io") == DEFAULT_LANE_CAPS["io"]

    def test_garbage_config_falls_back_to_defaults(self) -> None:
        assert Limits.from_config("nonsense").cap("llm") == DEFAULT_LANE_CAPS["llm"]


class TestDeadlockDetection:
    def test_nothing_runnable_and_nothing_in_flight_is_blocked(self) -> None:
        """A silent hang is the worst outcome for an unattended run, so this state is
        named rather than left to a timeout."""
        root = _node(
            {
                "kind": "parallel",
                "id": "p",
                "children": [
                    {"kind": "transform", "id": "a", "needs": ["ghost"], "config": {"expr": "1"}},
                ],
            }
        )
        # `needs` naming a non-sibling is a validation error; at runtime it must not hang.
        fr = frontier(root, {})
        assert fr.ready or fr.blocked

    def test_a_running_node_is_not_a_deadlock(self) -> None:
        root = _node(SEQ)
        fr = frontier(root, {"root.children[0]": InstanceState.RUNNING})
        assert not fr.blocked
        assert fr.running == ["root.children[0]"]


class TestContainerOutcome:
    def test_no_children_is_done(self) -> None:
        assert container_outcome([]) == InstanceState.DONE

    def test_an_unfinished_child_keeps_the_container_running(self) -> None:
        assert (
            container_outcome([InstanceState.DONE, InstanceState.PENDING]) == InstanceState.RUNNING
        )

    def test_all_skipped_reports_skipped_not_done(self) -> None:
        """An all-skipped container did no work; calling it DONE would make an empty run
        look productive."""
        assert (
            container_outcome([InstanceState.SKIPPED, InstanceState.SKIPPED])
            == InstanceState.SKIPPED
        )

    def test_severity_ordering_picks_the_most_important_verdict(self) -> None:
        assert (
            container_outcome([InstanceState.DONE, InstanceState.FAILED, InstanceState.SKIPPED])
            == InstanceState.FAILED
        )
        assert (
            container_outcome([InstanceState.FAILED, InstanceState.CANCELLED])
            == InstanceState.CANCELLED
        )


class TestNodeKindCoverage:
    def test_every_kind_has_a_lane(self) -> None:
        """A new kind without a lane would be unschedulable — this is the drift guard."""
        from personalclaw.workflows.models import lane_for

        for kind in NodeKind:
            assert lane_for(kind) in ("llm", "io", "compute")

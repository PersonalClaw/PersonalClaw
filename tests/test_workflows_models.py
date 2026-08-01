"""Workflow data model — the node algebra, path addressing, and tolerant reads.

The load-bearing property is the READER's tolerance (WF2-R12): a spec written by a newer
engine, or holding an enum value this build has never heard of, must still load. Bundled
templates and flywheel-proposed diffs outlive engine versions, and a strict parser turns
an upgrade into data loss.

The deliberate exception is an unknown node KIND — the engine genuinely cannot schedule
what it cannot dispatch, so that raises.
"""

from __future__ import annotations

from personalclaw.workflows.models import (
    FROZEN_STATES,
    LANE_COMPUTE,
    LANE_IO,
    LANE_LLM,
    RETRYABLE_CLASSES,
    SUCCESS_STATES,
    TERMINAL_RUN_STATUSES,
    TERMINAL_STATES,
    Failure,
    FailureClass,
    InstanceState,
    Node,
    NodeInstance,
    NodeKind,
    OriginKind,
    OverlapPolicy,
    RunStatus,
    WorkflowDef,
    WorkflowRun,
    lane_for,
    valid_name,
    walk,
)


def _spec() -> dict:
    return {
        "kind": "sequence",
        "id": "root",
        "children": [
            {"kind": "infer", "id": "classify", "config": {"prompt": "p"}},
            {
                "kind": "branch",
                "id": "route",
                "config": {"on": "{{nodes.classify.output.k}}"},
                "cases": {"bug": {"kind": "stage", "id": "fix", "config": {"prompt": "f"}}},
                "default": {"kind": "action", "id": "note", "config": {"provider": "notify"}},
            },
            {
                "kind": "foreach",
                "id": "each",
                "config": {"items": "{{inputs.xs}}"},
                "body": {"kind": "transform", "id": "t", "config": {"expr": "{{item}}"}},
            },
        ],
    }


class TestNodeTree:
    def test_parses_every_container_shape(self) -> None:
        n = Node.from_dict(_spec())
        assert n.kind is NodeKind.SEQUENCE
        assert len(n.children) == 3
        assert n.children[1].cases["bug"].kind is NodeKind.STAGE
        assert n.children[1].default_case is not None
        assert n.children[2].body is not None

    def test_paths_are_stable_and_address_every_node(self) -> None:
        """The path IS the instance key, so its shape is a contract — a rewind
        invalidates a journal region by path prefix."""
        paths = [p for p, _ in walk(Node.from_dict(_spec()))]
        assert paths == [
            "root",
            "root.children[0]",
            "root.children[1]",
            "root.children[1].cases[bug]",
            "root.children[1].default",
            "root.children[2]",
            "root.children[2].body",
        ]

    def test_round_trip_is_lossless(self) -> None:
        d = _spec()
        assert Node.from_dict(d).to_dict() == Node.from_dict(Node.from_dict(d).to_dict()).to_dict()

    def test_child_nodes_covers_all_container_shapes(self) -> None:
        n = Node.from_dict(_spec())
        assert len(n.children[1].child_nodes()) == 2  # one case + default
        assert len(n.children[2].child_nodes()) == 1  # body


class TestLanes:
    def test_lane_derives_from_kind_never_from_the_author(self) -> None:
        """A foreach over minutes-long local-model actions must not head-of-line-block
        a run's LLM stages (WF2-R21), so the lane cannot be author-declared."""
        assert lane_for(NodeKind.STAGE) == LANE_LLM
        assert lane_for(NodeKind.INFER) == LANE_LLM
        assert lane_for(NodeKind.ACTION) == LANE_IO
        assert lane_for(NodeKind.SUBWORKFLOW) == LANE_IO
        assert lane_for(NodeKind.TRANSFORM) == LANE_COMPUTE
        assert lane_for(NodeKind.SEQUENCE) == LANE_COMPUTE

    def test_a_config_lane_key_cannot_override_it(self) -> None:
        n = Node.from_dict({"kind": "transform", "config": {"lane": "llm"}})
        assert n.lane == LANE_COMPUTE


class TestTolerantReads:
    def test_unknown_node_fields_survive_a_round_trip(self) -> None:
        d = {
            "kind": "stage",
            "id": "s",
            "config": {"prompt": "p"},
            "retry_policy": {"jitter": True},
        }
        n = Node.from_dict(d)
        assert n.extra == {"retry_policy": {"jitter": True}}
        assert n.to_dict()["retry_policy"] == {"jitter": True}

    def test_unknown_def_fields_survive_a_round_trip(self) -> None:
        d = {"name": "x", "root": {"kind": "sequence", "children": []}, "quantum": 1}
        wf = WorkflowDef.from_dict(d)
        assert wf.extra == {"quantum": 1}
        assert WorkflowDef.from_dict(wf.to_dict()).extra == {"quantum": 1}

    def test_unknown_enum_values_fall_back_rather_than_raise(self) -> None:
        """A newer engine may write a status this build lacks. Refusing to load the row
        would hide the run entirely — including from the user trying to delete it."""
        assert (
            WorkflowRun.from_dict({"id": "a", "workflow_name": "w", "status": "teleporting"}).status
            is RunStatus.DRAFT
        )
        assert Failure.from_dict({"class": "gremlins"}).failure_class is FailureClass.INTERNAL
        assert (
            WorkflowRun.from_dict(
                {"id": "a", "workflow_name": "w", "origin": {"kind": "telepathy"}}
            ).origin.kind
            is OriginKind.MANUAL
        )
        assert (
            WorkflowDef.from_dict(
                {"name": "n", "root": {"kind": "sequence"}, "on_overlap": "nope"}
            ).on_overlap
            is OverlapPolicy.SKIP
        )
        assert (
            NodeInstance.from_dict({"path": "root", "state": "vibing"}).state
            is InstanceState.PENDING
        )

    def test_unknown_node_kind_raises_because_it_cannot_be_dispatched(self) -> None:
        for bad in ({"kind": "quantum"}, {"kind": ""}, {}):
            try:
                Node.from_dict(bad)
            except ValueError:
                continue
            raise AssertionError(f"{bad!r} should not have parsed")

    def test_def_without_a_root_raises(self) -> None:
        try:
            WorkflowDef.from_dict({"name": "x"})
        except ValueError as exc:
            assert "root" in str(exc)
        else:
            raise AssertionError("a def with no root must not parse")


class TestOutcomeModel:
    def test_degraded_is_a_success_not_a_failure(self) -> None:
        """Degrade, don't die: an absent optional capability must not sink a template."""
        assert InstanceState.DEGRADED in SUCCESS_STATES
        assert InstanceState.DEGRADED in TERMINAL_STATES
        assert InstanceState.FAILED not in SUCCESS_STATES

    def test_running_and_terminal_nodes_are_both_frozen(self) -> None:
        """The frozen-region invariant: a mutation may only touch un-run nodes."""
        assert InstanceState.RUNNING in FROZEN_STATES
        assert InstanceState.DONE in FROZEN_STATES
        assert InstanceState.PENDING not in FROZEN_STATES
        assert InstanceState.READY not in FROZEN_STATES

    def test_only_transient_and_network_failures_retry(self) -> None:
        """Retrying a user or permission error burns budget to reach the same failure."""
        assert RETRYABLE_CLASSES == {FailureClass.TRANSIENT, FailureClass.NETWORK}
        assert Failure(failure_class=FailureClass.TRANSIENT).retryable
        assert not Failure(failure_class=FailureClass.USER).retryable
        assert not Failure(failure_class=FailureClass.PERMISSION).retryable

    def test_failure_keeps_cause_and_remediation_separate(self) -> None:
        """The widget renders remediation as an actionable step; collapsing the two
        leaves the user with an error and no next move."""
        f = Failure(
            failure_class=FailureClass.NETWORK,
            cause_plain="the endpoint refused the connection",
            remediation="check the service is running, then resume the run",
        )
        d = f.to_dict()
        assert d["cause_plain"] != d["remediation"]
        assert d["retryable"] is True
        assert Failure.from_dict(d).remediation == f.remediation


class TestRunGenealogy:
    def test_a_run_is_its_own_root_by_default(self) -> None:
        """Defaulting here rather than at each call site keeps the run-tree query
        total — every run has a root_run_id, so none is invisible to it."""
        assert WorkflowRun(id="abc123", workflow_name="w").root_run_id == "abc123"

    def test_an_explicit_root_is_preserved_for_spawns_and_forks(self) -> None:
        child = WorkflowRun(id="child1", workflow_name="sub", root_run_id="parent1")
        assert child.root_run_id == "parent1"

    def test_terminal_statuses(self) -> None:
        assert RunStatus.COMPLETE in TERMINAL_RUN_STATUSES
        assert RunStatus.NEEDS_INPUT not in TERMINAL_RUN_STATUSES
        assert WorkflowRun(id="a", workflow_name="w", status=RunStatus.FAILED).is_terminal
        assert not WorkflowRun(id="a", workflow_name="w", status=RunStatus.RUNNING).is_terminal


class TestNames:
    def test_valid_names(self) -> None:
        for ok in ("research", "deep-research", "a", "a1-b2"):
            assert valid_name(ok), ok

    def test_invalid_names_are_rejected(self) -> None:
        # The name becomes a directory, so anything path-shaped is a traversal risk.
        for bad in ("Research", "with space", "-leading", "a" * 64, "", "../escape", "a/b"):
            assert not valid_name(bad), bad

"""The admission seam: tightest-wins composition and the two hold vocabularies (PP-11).

`tests/test_workflows_frontier_golden.py` proves the seam changed nothing. This module proves the
seam is a seam — that composition is by capacity rather than by position, that a tie between two
policies resolves toward the refusal that has a name, and that `PP-12` can add `Lease` here without
re-auditing the three rules already in the list.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from personalclaw.workflows.admission import (
    DEFAULT_LANE_CAPS,
    RANK_CAPACITY,
    RANK_INVARIANT,
    AdmissionPolicy,
    AdmissionRequest,
    ContainerConcurrency,
    Hold,
    Lane,
    Limits,
    Scope,
    Wip,
    compose,
    default_policies,
)
from personalclaw.workflows.models import Node
from personalclaw.workflows.tick import frontier

LANE_REQ = AdmissionRequest(scope=Scope.LANE, key="llm")


def _container(**config) -> AdmissionRequest:
    node = Node.from_dict({"id": "fan", "kind": "foreach", "config": config})
    return AdmissionRequest(scope=Scope.CONTAINER, key="root.fan", node=node)


@dataclass(frozen=True)
class _Fixed(AdmissionPolicy):
    """A stand-in for the policies `PP-12` will add — enough to test composition without
    pre-committing to `Lease`'s shape."""

    value: int | None = None
    name = "fixed"
    hold = Hold.DEFERRED
    rank = RANK_CAPACITY

    def capacity(self, request: AdmissionRequest) -> int | None:
        return self.value


class TestComposition:
    def test_the_tightest_capacity_wins_regardless_of_list_position(self) -> None:
        """Order is documentation. Composition is by capacity, which is the property that lets a
        policy be appended without re-auditing the ones already there."""
        loose, tight = _Fixed(value=9), _Fixed(value=2)
        assert compose((loose, tight), LANE_REQ).capacity == 2
        assert compose((tight, loose), LANE_REQ).capacity == 2

    def test_two_policies_both_refusing_is_one_refusal_at_the_tighter_number(self) -> None:
        """The both-defer case. Two policies that both say no must not compound into a smaller
        capacity, and must not let the looser one grant admission the tighter one refused."""
        verdict = compose((_Fixed(value=3), _Fixed(value=1)), LANE_REQ)
        assert verdict.capacity == 1
        assert not verdict.admits(1), "the looser policy admitted what the tighter one refused"
        assert not verdict.admits(5)
        assert verdict.admits(0), "composition tightened past the tightest policy"

    def test_an_abstaining_policy_is_not_an_infinite_cap(self) -> None:
        """`None` means "no opinion", not "unbounded" — a bucket every policy abstains on stays
        genuinely unbounded, while one policy's opinion still binds."""
        assert compose((_Fixed(value=None), _Fixed(value=None)), LANE_REQ).capacity is None
        assert compose((_Fixed(value=None),), LANE_REQ).bounded is False
        assert compose((_Fixed(value=None), _Fixed(value=4)), LANE_REQ).capacity == 4

    def test_an_empty_policy_list_admits_everything(self) -> None:
        verdict = compose((), LANE_REQ)
        assert verdict.bounded is False
        assert verdict.admits(10_000)
        assert verdict.hold == Hold.UNRECORDED

    def test_the_binding_policy_is_reported_so_a_refusal_is_explainable(self) -> None:
        """A verdict that only carried a number could say "no" but never "no, because the run
        declared WIP=1", and the second is the one a user needs."""
        verdict = compose(default_policies(Limits(), single_active_feature=True), _container())
        assert verdict.binding is not None
        assert verdict.binding.name == "single_active_feature"
        assert verdict.hold == Hold.WIP_HELD

    def test_admits_compares_strictly_against_the_capacity(self) -> None:
        verdict = compose((_Fixed(value=2),), LANE_REQ)
        assert [verdict.admits(n) for n in (0, 1, 2, 3)] == [True, True, False, False]


class TestTieBreak:
    """The tie is the single most refactor-fragile decision in the admission step."""

    def test_wip_names_the_refusal_when_max_concurrency_binds_at_the_same_number(self) -> None:
        """`max_concurrency: 1` under WIP=1. Both bind at 1; only one refusal has a name.

        This is `cap = 1 if wip else _max_concurrency(node)` — the line the seam replaced — read
        back out. Break the tie the other way and the run stops recording that it enforced its own
        declared invariant.
        """
        policies = default_policies(Limits(), single_active_feature=True)
        verdict = compose(policies, _container(max_concurrency=1))
        assert verdict.capacity == 1
        assert verdict.hold == Hold.WIP_HELD, (
            "a tie must resolve toward the declared invariant: `wip_held` is how the ledger "
            "answers 'why is item 2 not running'"
        )

    def test_max_concurrency_alone_refuses_anonymously(self) -> None:
        """The control half. Without the invariant, the same capacity records nothing — a capped
        container's unstarted item is neither lane pressure nor an enforced invariant."""
        policies = default_policies(Limits(), single_active_feature=False)
        verdict = compose(policies, _container(max_concurrency=1))
        assert verdict.capacity == 1
        assert verdict.hold == Hold.UNRECORDED

    def test_the_invariant_rank_is_above_the_capacity_rank(self) -> None:
        """Stated as a rail so the ordering cannot drift silently."""
        assert RANK_INVARIANT > RANK_CAPACITY
        assert Wip(active=True).rank == RANK_INVARIANT
        assert ContainerConcurrency().rank == RANK_CAPACITY
        assert Lane().rank == RANK_CAPACITY

    def test_a_tighter_capacity_still_beats_a_higher_rank(self) -> None:
        """Rank breaks TIES only. A policy that ranks high must not be able to loosen a tighter
        one — that would make the composition order-dependent again."""
        high = Wip(active=True)
        verdict = compose((high, _Fixed(value=0)), _container())
        assert verdict.capacity == 0
        assert verdict.binding is not high


class TestPolicyScopes:
    def test_each_policy_abstains_outside_its_own_scope(self) -> None:
        """Three policies coexist in one list because two of them say nothing about a lane and one
        says nothing about a container."""
        assert Lane().capacity(_container(max_concurrency=2)) is None
        assert ContainerConcurrency().capacity(LANE_REQ) is None
        assert Wip(active=True).capacity(LANE_REQ) is None

    def test_lane_capacity_is_the_configured_cap_and_falls_back_for_unknown_lanes(self) -> None:
        policy = Lane(limits=Limits(lanes={"llm": 7}))
        assert policy.capacity(AdmissionRequest(scope=Scope.LANE, key="llm")) == 7
        assert (
            policy.capacity(AdmissionRequest(scope=Scope.LANE, key="io")) == DEFAULT_LANE_CAPS["io"]
        )
        assert policy.capacity(AdmissionRequest(scope=Scope.LANE, key="nonsense")) == 1

    def test_an_inactive_wip_abstains_rather_than_declaring_itself_unbounded(self) -> None:
        """So a run without the invariant composes exactly as it did before the policy existed."""
        assert Wip(active=False).capacity(_container()) is None
        assert compose((Wip(active=False),), _container()).bounded is False

    @pytest.mark.parametrize(
        "declared, expected",
        [
            (2, 2),
            (1, 1),
            (0, None),
            (-3, None),
            (None, None),
            (True, None),
            (1.5, None),
            ("2", None),
        ],
    )
    def test_max_concurrency_reads_a_true_int_only(self, declared, expected) -> None:
        """`int(1.5)` truncates to 1 and `int(True)` is 1, so a coercing read would silently
        serialize a fan-out to one item at a time — expensive and invisible, because the run still
        succeeds. Unset and non-positive are unbounded, not 'one at a time'."""
        config = {} if declared is None else {"max_concurrency": declared}
        assert ContainerConcurrency().capacity(_container(**config)) == expected

    def test_the_default_list_is_the_three_rules_in_documented_order(self) -> None:
        policies = default_policies(Limits(), single_active_feature=True)
        assert [p.name for p in policies] == ["lane", "max_concurrency", "single_active_feature"]


class TestSeamIsLoadBearing:
    """The seam must be the ONLY path to an admission decision — a second `max_concurrency` read
    hiding in `tick.py` would make `PP-12`'s `Lease` silently partial."""

    def test_the_frontier_reads_no_cap_of_its_own(self) -> None:
        import ast
        from pathlib import Path

        import personalclaw.workflows.tick as tick_mod

        src = Path(tick_mod.__file__).read_text(encoding="utf-8")
        tree = ast.parse(src)
        literals = [
            n.value
            for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and n.value == "max_concurrency"
        ]
        assert not literals, (
            "tick.py reads `max_concurrency` directly — admission decisions must come from the "
            "policy list, or PP-12's Lease will not apply to this container"
        )

    def test_a_policy_appended_to_the_list_tightens_the_real_frontier(self) -> None:
        """End to end: an extra policy in the composed list must actually change what `frontier()`
        admits. If it does not, the seam is decorative."""
        spec = {
            "id": "root",
            "kind": "foreach",
            "config": {"items": ["a", "b", "c"]},
            "body": {"id": "body", "kind": "transform", "config": {"expr": "1"}},
        }
        root = Node.from_dict(spec)
        wide = frontier(root, {})
        assert len(wide.ready) == 3, "the uncapped fan-out should offer every item"

        narrow = frontier(root, {}, single_active_feature=True)
        assert len(narrow.ready) == 1
        assert narrow.wip_held == [
            "root.body#1",
            "root.body#2",
        ], "the invariant's refusals must be named, not merely absent"
        assert not narrow.deferred, "an invariant hold is not lane pressure"

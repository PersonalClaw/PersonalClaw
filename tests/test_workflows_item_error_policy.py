"""`foreach` item-error policies — three members, three different run-level outcomes (WV-13).

`ItemErrorPolicy.COLLECT` shipped as a DECLARED STRATEGY WITH NO EXECUTOR: `models.py` declared
it, `validator.py` accepted it and `service.capabilities` advertised it to authoring models,
while the derivation branched on `HALT` and `SKIP` only. Nothing was visibly broken, so nothing
found it for the length of the engine program.

So the load-bearing assertions here are the ones a "COLLECT exists" test could never make:

* **three policies, three DIFFERENT observables** for the same seeded failing item, driven
  through the real controller/tick path — a fan-out that halts and fails, one that runs
  everything and completes, and one that runs everything and fails;
* **the exhaustiveness ratchet** — every member of the enum has its own branch in
  `tick.foreach_outcome`, proven both by driving each member and by reading the branches out of
  the source, and the unreachable tail RAISES so a fourth member added later cannot silently
  inherit a neighbour's behaviour;
* **the collected failures reach the ledger** — COLLECT's contract is "run everything, then hand
  me the failures", and a FAILED run that does not say which of its items broke has collected
  nothing.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path
from typing import cast

import pytest

from personalclaw.workflows import journal as J
from personalclaw.workflows import store
from personalclaw.workflows import tick as tick_mod
from personalclaw.workflows.controller import EngineServices, RunController
from personalclaw.workflows.models import InstanceState, ItemErrorPolicy, RunStatus, WorkflowRun
from personalclaw.workflows.tick import foreach_outcome, item_error_policy

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    """Runs write a real journal, so they get a real directory — never the user's home."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: home)
    return home


#: One item of three cannot satisfy the body's `{{item.v}}` binding, so exactly one item fails —
#: deterministically, in the real dispatcher, with no model call and no retry (a BindingError is
#: a USER failure, and USER is not retryable).
ITEMS = [{"name": "alpha", "v": 1}, {"name": "bravo"}, {"name": "charlie", "v": 3}]


def _spec(policy: str) -> dict:
    return {
        "name": f"fan-{policy}",
        "root": {
            "kind": "foreach",
            "id": "fan",
            # One item at a time, which is what makes HALT observable at all: unbounded, every
            # item is already launched before the first failure exists to halt on.
            "config": {"items": ITEMS, "on_item_error": policy, "max_concurrency": 1},
            "body": {"kind": "transform", "id": "body", "config": {"expr": "{{item.v}}"}},
        },
    }


async def _drive(policy: str) -> tuple[RunStatus, set[int], list[dict]]:
    """Run a 3-item fan-out under one policy. Returns (status, item indexes that ran, ledger)."""
    spec = _spec(policy)
    run = store.create(WorkflowRun(id="", workflow_name=spec["name"]))
    store.write_spec(run.id, spec)
    controller = RunController(run, spec, services=EngineServices())
    status = await controller.run_to_completion(timeout=25)
    started = {
        int(path.split(".body#", 1)[1].split(".", 1)[0])
        for path in controller.instances
        if ".body#" in path
    }
    return status, started, J.ledger(run.id)


class TestThreeWayBehaviour:
    """The proof that the members are worth having: one seeded failure, three outcomes."""

    async def test_halt_fails_the_run_and_leaves_the_rest_of_the_fan_out_unrun(self) -> None:
        status, started, _ = await _drive("halt")
        assert status == RunStatus.FAILED
        # Items 0 and 1 ran (1 is the one that failed); item 2 was never started.
        assert started == {0, 1}

    async def test_skip_runs_every_item_and_completes(self) -> None:
        status, started, _ = await _drive("skip")
        assert status == RunStatus.COMPLETE  # container DEGRADED, which is a SUCCESS state
        assert started == {0, 1, 2}

    async def test_collect_runs_every_item_and_then_fails_the_run(self) -> None:
        status, started, _ = await _drive("collect")
        assert status == RunStatus.FAILED
        assert started == {0, 1, 2}

    async def test_the_three_policies_are_not_the_same_run(self) -> None:
        """The distinctness assertion stated directly, so a future change that collapses two
        policies into one behaviour fails HERE rather than looking like a passing suite."""
        outcomes = {}
        for policy in ("halt", "skip", "collect"):
            status, started, _ = await _drive(policy)
            outcomes[policy] = (status, len(started))
        assert len(set(outcomes.values())) == 3, outcomes


class TestCollectedFailuresReachTheLedger:
    """COLLECT's data half: the failures are the deliverable."""

    async def test_collect_journals_the_failed_items(self) -> None:
        _, _, ledger = await _drive("collect")
        records = [r for r in ledger if r["kind"] == J.ITEMS_COLLECTED]
        assert len(records) == 1, "one record per fan-out, not one per item and not none"
        record = records[0]
        assert record["node_id"] == "fan"
        assert record["outcome"] == InstanceState.FAILED.value
        assert record["failed_items"] == 1
        (failure,) = record["failures"]
        assert failure["item_index"] == 1
        assert failure["item_label"] == "bravo"  # names its item, not just its index
        assert failure["failure_class"] == "user"
        assert failure["cause"]

    @pytest.mark.parametrize("policy", ["halt", "skip"])
    async def test_only_collect_writes_the_record(self, policy: str) -> None:
        """SKIP tolerated the failure and HALT refused to continue past it — neither promised
        anyone a collected set, and writing one would make the record meaningless."""
        _, _, ledger = await _drive(policy)
        assert not [r for r in ledger if r["kind"] == J.ITEMS_COLLECTED]

    async def test_a_clean_fan_out_writes_no_record(self) -> None:
        spec = _spec("collect")
        spec["root"]["config"]["items"] = [{"name": "alpha", "v": 1}, {"name": "beta", "v": 2}]
        run = store.create(WorkflowRun(id="", workflow_name="clean"))
        store.write_spec(run.id, spec)
        controller = RunController(run, spec, services=EngineServices())
        assert await controller.run_to_completion(timeout=25) == RunStatus.COMPLETE
        assert not [r for r in J.ledger(run.id) if r["kind"] == J.ITEMS_COLLECTED]

    async def test_the_record_is_written_once_not_once_per_tick(self) -> None:
        """A container's state is DERIVED, so every tick re-examines a terminal fan-out. Without
        the dedup the ledger would carry one collected-failure record per remaining tick."""
        spec = _spec("collect")
        run = store.create(WorkflowRun(id="", workflow_name="dedup"))
        store.write_spec(run.id, spec)
        controller = RunController(run, spec, services=EngineServices())
        await controller.run_to_completion(timeout=25)
        # Extra frontier derivations after the run is terminal must not append again.
        controller._frontier()
        controller._frontier()
        assert len([r for r in J.ledger(run.id) if r["kind"] == J.ITEMS_COLLECTED]) == 1


class TestExhaustiveness:
    """The ratchet. A fourth member must not inherit a third member's behaviour."""

    @pytest.mark.parametrize("policy", list(ItemErrorPolicy), ids=lambda p: p.value)
    def test_every_member_has_a_branch(self, policy: ItemErrorPolicy) -> None:
        """Drives the decision function with every member of the closed enum. A member added
        without a branch reaches the raise, and this fails on the day it is added."""
        assert isinstance(foreach_outcome(policy, [InstanceState.DONE]), InstanceState)

    def test_an_unhandled_policy_raises_rather_than_defaulting(self) -> None:
        """Proof the ratchet can fail: a value with no branch is refused, not quietly mapped to
        whichever policy happened to be written last."""
        with pytest.raises(AssertionError, match="no branch for ItemErrorPolicy"):
            foreach_outcome(cast(ItemErrorPolicy, "future"), [InstanceState.DONE])

    def test_the_source_branches_on_every_member_by_name(self) -> None:
        """Read out of the SOURCE, not the behaviour: `foreach_outcome` must name each member.
        A branch that dispatches on something else (a truthiness test, a fallthrough shared by
        two members) would still satisfy the parametrized test above while leaving the next
        member's semantics undeclared."""
        source = Path(inspect.getsourcefile(tick_mod) or "").read_text(encoding="utf-8")
        tree = ast.parse(source)
        fn = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "foreach_outcome"
        )
        named = {
            node.attr
            for node in ast.walk(fn)
            if isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "ItemErrorPolicy"
        }
        assert named == {member.name for member in ItemErrorPolicy}

    def test_skips_verdict_is_the_only_success(self) -> None:
        """The pure form of the run-level difference, without a controller: only SKIP's verdict
        is a state `_ROOT_TO_RUN` maps to COMPLETE."""
        seeded = [InstanceState.FAILED, InstanceState.DONE, InstanceState.DONE]
        assert foreach_outcome(ItemErrorPolicy.SKIP, seeded) == InstanceState.DEGRADED
        assert foreach_outcome(ItemErrorPolicy.COLLECT, seeded) == InstanceState.FAILED
        assert foreach_outcome(ItemErrorPolicy.HALT, seeded) == InstanceState.FAILED

    def test_neither_skip_nor_collect_reports_a_verdict_mid_flight(self) -> None:
        """Both run every item: a fan-out with an unfinished item is RUNNING under either, which
        is what stops the container from claiming a verdict while work is outstanding."""
        mixed = [InstanceState.FAILED, InstanceState.PENDING]
        assert foreach_outcome(ItemErrorPolicy.SKIP, mixed) == InstanceState.RUNNING
        assert foreach_outcome(ItemErrorPolicy.COLLECT, mixed) == InstanceState.RUNNING

    def test_collect_does_not_flatten_a_more_severe_item_verdict(self) -> None:
        """A cancelled item outranks a failed one: COLLECT reports the worst verdict rather than
        hard-coding FAILED, so "someone cancelled item 2" survives the collapse."""
        assert (
            foreach_outcome(
                ItemErrorPolicy.COLLECT, [InstanceState.FAILED, InstanceState.CANCELLED]
            )
            == InstanceState.CANCELLED
        )

    def test_an_unknown_config_string_reads_as_the_default(self) -> None:
        """The AUTHORING half is the validator's job (`WF_BAD_ITEM_ERROR`); the runtime read of a
        spec that got past it must still be a real member, never a crash mid-run."""
        from personalclaw.workflows.models import Node

        node = Node.from_dict(
            {"kind": "foreach", "id": "f", "config": {"items": [], "on_item_error": "nonsense"}}
        )
        assert item_error_policy(node) == ItemErrorPolicy.SKIP

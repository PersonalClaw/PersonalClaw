"""`on_overlap` — three policies, and the queue `queue` never had (WV-14).

`OverlapPolicy.QUEUE` shipped as the exact OPPOSITE of its name. The run-workflow provider
compared against `SKIP` (return early) and `CANCEL_PREVIOUS` (cancel, then start) and let
`queue` fall through to `store.create` + `_launch` — so the policy whose name promises
ORDERING started a second run beside the still-running first one, silently, which is the
precise hazard `OverlapPolicy.SKIP`'s own comment says the default exists to prevent.

The load-bearing assertions here are the ones a "QUEUE exists" test could never make:

* **three policies, three DIFFERENT observables** for the same busy def — nothing created,
  one run created and NOT started, and the prior cancelled with a new run started;
* **a hand-made DRAFT is never launched by the drain.** `RunStatus.DRAFT` is also where a
  user's deliberately-unstarted editor draft lives, so a drain keyed on "DRAFT for this
  def" would start work the user never asked for. Queued-ness is a marker, and this is the
  test that pins it;
* **the drain runs on the real path** — a real controller reaching its real terminal write
  launches the queued run, and a fresh watchdog's poll does the same after a restart;
* **the exhaustiveness ratchet** — every member of the closed enum has its own branch in
  `overlap.decide`, proven by driving each member AND by reading the branches out of the
  source, with a raising tail so a fourth member cannot inherit a neighbour's behaviour.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from personalclaw.action_providers.run_workflow_provider import RunWorkflowActionProvider
from personalclaw.workflows import defs as defs_mod
from personalclaw.workflows import overlap as overlap_mod
from personalclaw.workflows import store
from personalclaw.workflows.controller import EngineServices, RunController
from personalclaw.workflows.models import OverlapPolicy, RunStatus, WorkflowRun
from personalclaw.workflows.overlap import MAX_QUEUE_DEPTH, OverlapAction, decide
from personalclaw.workflows.watchdog import WorkflowWatchdog

pytestmark = pytest.mark.anyio

NAME = "slow-sweep"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    """Runs write a real store, journal and FLOCK — never the user's home.

    `PERSONALCLAW_HOME` as well as the patched `store.config_dir`: the drain's single-flight
    lock lives under `concurrency._locks_dir()`, which resolves `config_dir` through its own
    import, so patching only the store's would leave the lock file in the real home.
    """
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: home)
    return home


def _spec(overlap: str) -> dict[str, Any]:
    """A one-node def carrying the policy under test. `_overlap_of` reads `on_overlap` off a
    dict def, and `_spec_of` returns the dict itself, so this is both def and spec."""
    return {
        "name": NAME,
        "on_overlap": overlap,
        "root": {"kind": "transform", "id": "only", "config": {"expr": "done"}},
    }


class _StubDefs(defs_mod.WorkflowDefProvider):
    def __init__(self, spec: dict[str, Any]) -> None:
        self._spec = spec

    @property
    def name(self) -> str:
        return "wv14-stub"

    async def list_defs(self, *, limit: int = 200, offset: int = 0):
        return [self._spec], 1

    async def get_def(self, name: str):
        return self._spec if name == self._spec["name"] else None


@pytest.fixture
def harness(monkeypatch):
    """A registered def provider plus a real watchdog wired in as the action supervisor."""
    watchdog = WorkflowWatchdog()

    def _install(overlap: str) -> dict[str, Any]:
        spec = _spec(overlap)
        provider = _StubDefs(spec)
        defs_mod.register_provider(provider)
        monkeypatch.setattr(
            "personalclaw.action_providers.services.get_action_services",
            lambda: SimpleNamespace(workflows=watchdog),
        )
        return spec

    try:
        yield SimpleNamespace(install=_install, watchdog=watchdog)
    finally:
        defs_mod.unregister_provider("wv14-stub")


async def _fire(**config: Any):
    """One trigger-origin start through the real provider."""
    ctx = cast(Any, SimpleNamespace(context="trigger-wv14"))
    return await RunWorkflowActionProvider().execute({"workflow": NAME, **config}, ctx)


def _busy_prior(spec: dict[str, Any]) -> WorkflowRun:
    """A prior run the engine is still driving. RUNNING in the store with no controller is
    exactly the shape adoption sees, so this is the real precondition, not a stub."""
    prior = store.create(WorkflowRun(id="", workflow_name=NAME, status=RunStatus.RUNNING))
    store.write_spec(prior.id, spec)
    return prior


def _drafts() -> list[WorkflowRun]:
    rows, _ = store.list_runs(workflow_name=NAME, status=RunStatus.DRAFT, limit=50)
    return rows


# ── the three policies, three observables ───────────────────────────────────


class TestThreePolicies:
    async def test_skip_creates_nothing(self, harness) -> None:
        spec = harness.install("skip")
        prior = _busy_prior(spec)
        result = await _fire()
        assert result.outcome == "skip"
        assert json.loads(result.stdout)["run_id"] == prior.id
        assert _drafts() == []

    async def test_queue_creates_a_run_and_does_not_start_it(self, harness) -> None:
        """The regression. Before WV-14 this returned `launched` with a live controller
        running BESIDE the prior; the queued run must exist and must not be running."""
        spec = harness.install("queue")
        prior = _busy_prior(spec)

        result = await _fire()

        assert result.outcome == "queued"
        body = json.loads(result.stdout)
        assert body["queued"] is True and body["started"] is False
        assert body["behind"] == [prior.id]
        queued = store.get(body["run_id"])
        assert queued is not None
        assert queued.status == RunStatus.DRAFT
        # Nothing is driving it, and the prior is untouched.
        assert harness.watchdog.controller(queued.id) is None
        assert store.get(prior.id).status == RunStatus.RUNNING
        assert [r.id for r in store.active_runs()] == [prior.id]

    async def test_cancel_previous_cancels_then_starts(self, harness) -> None:
        spec = harness.install("cancel_previous")
        prior = _busy_prior(spec)
        result = await _fire()
        assert result.outcome == "launched"
        assert store.cancel_requested(prior.id)
        started = json.loads(result.stdout)["run_id"]
        assert harness.watchdog.controller(started) is not None
        await harness.watchdog.controller(started).run_to_completion(timeout=15)

    async def test_the_three_policies_are_not_the_same_start(self, harness) -> None:
        """Stated directly, so a later change that collapses two policies fails HERE rather
        than looking like a passing suite."""
        observed: dict[str, tuple[str, int]] = {}
        for policy in ("skip", "queue", "cancel_previous"):
            spec = harness.install(policy)
            prior = _busy_prior(spec)
            result = await _fire()
            observed[policy] = (result.outcome, len(overlap_mod.queued_runs(NAME)))
            # Clear the def between policies so each starts from "one prior in flight".
            for run, _ in [(r, None) for r in store.list_runs(workflow_name=NAME, limit=50)[0]]:
                controller = harness.watchdog.controller(run.id)
                if controller is not None:
                    await controller.stop()
                    harness.watchdog.forget(run.id)
                store.delete(run.id)
            assert prior.id not in {r.id for r in store.active_runs()}

        assert observed["skip"] == ("skip", 0)
        assert observed["queue"] == ("queued", 1)
        assert observed["cancel_previous"] == ("launched", 0)
        assert len(set(observed.values())) == 3

    async def test_queue_starts_immediately_when_nothing_is_in_flight(self, harness) -> None:
        """`queue` is not `always queue` — with a free def it starts now, or a per-hour
        trigger against a one-minute workflow would never run anything directly."""
        harness.install("queue")
        result = await _fire()
        assert result.outcome == "launched"
        run_id = json.loads(result.stdout)["run_id"]
        await harness.watchdog.controller(run_id).run_to_completion(timeout=15)


# ── the cap ─────────────────────────────────────────────────────────────────


class TestCap:
    async def test_a_start_past_the_cap_is_dropped_and_says_so(self, harness) -> None:
        spec = harness.install("queue")
        _busy_prior(spec)
        first = await _fire()
        assert first.outcome == "queued"

        second = await _fire()

        assert second.outcome == "skip"
        body = json.loads(second.stdout)
        assert body["dropped"] is True
        assert body["reason"] == "queue_full"
        assert body["max_queue_depth"] == MAX_QUEUE_DEPTH
        assert body["queued_run_id"] == json.loads(first.stdout)["run_id"]
        # And the cap is a cap: no second queued run was created.
        assert len(_drafts()) == 1

    async def test_a_dropped_start_is_logged(self, harness, caplog) -> None:
        """A truncation that only appeared in a returned dict would be invisible to anyone
        reading the logs of a queue that is not keeping up."""
        spec = harness.install("queue")
        _busy_prior(spec)
        await _fire()
        with caplog.at_level(
            "WARNING", logger="personalclaw.action_providers.run_workflow_provider"
        ):
            await _fire()
        assert any("dropped a queued start" in r.getMessage() for r in caplog.records)


# ── the drain, on the real path ──────────────────────────────────────────────


class TestDrain:
    async def test_a_finishing_run_launches_the_queued_one(self, harness) -> None:
        """create → the prior finishes → the queued run launches. Driven through the real
        controller's real terminal write, which is the drain's live call site."""
        spec = harness.install("queue")
        prior = _busy_prior(spec)
        queued_id = json.loads((await _fire()).stdout)["run_id"]
        assert store.get(queued_id).status == RunStatus.DRAFT

        controller = RunController(
            prior, spec, services=EngineServices(supervisor=harness.watchdog)
        )
        harness.watchdog.register(controller)
        assert await controller.run_to_completion(timeout=20) == RunStatus.COMPLETE

        launched = harness.watchdog.controller(queued_id)
        assert launched is not None, "the queued run was never started"
        assert await launched.run_to_completion(timeout=20) == RunStatus.COMPLETE
        assert store.get(queued_id).status == RunStatus.COMPLETE

    async def test_a_hand_made_draft_is_never_launched(self, harness) -> None:
        """The worst available outcome of this atom is starting work a user never asked to
        start. An unlaunched editor draft is DRAFT for the SAME def and carries no marker."""
        spec = harness.install("queue")
        prior = _busy_prior(spec)
        mine = store.create(WorkflowRun(id="", workflow_name=NAME))
        store.write_spec(mine.id, spec)
        assert not overlap_mod.is_queued(store.get(mine.id))

        controller = RunController(
            prior, spec, services=EngineServices(supervisor=harness.watchdog)
        )
        harness.watchdog.register(controller)
        await controller.run_to_completion(timeout=20)
        # And the poll path, which is the other caller.
        await overlap_mod.drain_all(harness.watchdog)

        assert harness.watchdog.controller(mine.id) is None
        assert store.get(mine.id).status == RunStatus.DRAFT

    async def test_the_watchdog_poll_drains_after_a_restart(self, harness) -> None:
        """The restart answer, driven: the queued run is a durable DRAFT row plus its spec
        file, and a watchdog that has never seen it drains it on its first poll."""
        spec = harness.install("queue")
        prior = _busy_prior(spec)
        queued_id = json.loads((await _fire()).stdout)["run_id"]
        # The prior did not survive the "restart": nothing is in flight for the def.
        prior.status = RunStatus.COMPLETE
        store.save(prior)

        fresh = WorkflowWatchdog()
        await fresh._poll_once()

        assert fresh.controller(queued_id) is not None
        await fresh.controller(queued_id).run_to_completion(timeout=20)

    async def test_the_queue_waits_while_the_prior_is_only_paused(self, harness) -> None:
        """PAUSED counts as active, so a suspended crash-survivor still holds the queue. The
        honest answer: a queued run waits for an explicit Resume, it does not overtake."""
        spec = harness.install("queue")
        prior = _busy_prior(spec)
        queued_id = json.loads((await _fire()).stdout)["run_id"]
        prior.status = RunStatus.PAUSED
        store.save(prior)

        assert await overlap_mod.drain(NAME, harness.watchdog) is None
        assert store.get(queued_id).status == RunStatus.DRAFT

    async def test_the_drain_launches_one_run_once(self, harness) -> None:
        """Idempotent and single-flight: a second drain (a concurrent finish, or a poll
        landing on top of one) must not launch a second controller or re-launch the same
        run."""
        spec = harness.install("queue")
        prior = _busy_prior(spec)
        queued_id = json.loads((await _fire()).stdout)["run_id"]
        prior.status = RunStatus.COMPLETE
        store.save(prior)

        results = await asyncio.gather(
            overlap_mod.drain(NAME, harness.watchdog),
            overlap_mod.drain(NAME, harness.watchdog),
        )
        assert [r for r in results if r] == [queued_id]
        assert await overlap_mod.drain(NAME, harness.watchdog) is None
        await harness.watchdog.controller(queued_id).run_to_completion(timeout=20)

    async def test_a_queued_run_whose_spec_vanished_is_failed_not_retried_forever(
        self, harness
    ) -> None:
        """A queue head the drain can never launch would be re-examined on every 5s poll and
        would block every start behind it."""
        spec = harness.install("queue")
        prior = _busy_prior(spec)
        queued_id = json.loads((await _fire()).stdout)["run_id"]
        (store.run_dir(queued_id) / "spec.json").unlink(missing_ok=True)
        prior.status = RunStatus.COMPLETE
        store.save(prior)

        assert await overlap_mod.drain(NAME, harness.watchdog) is None
        gone = store.get(queued_id)
        assert gone.status == RunStatus.FAILED
        assert "spec is missing" in gone.error_message
        assert overlap_mod.queued_runs(NAME) == []


# ── the marker ──────────────────────────────────────────────────────────────


class TestMarker:
    async def test_the_marker_survives_a_reload(self, harness) -> None:
        """`extra` is a persisted JSON column, so the marker comes back off disk — a
        process-local set would forget the queue on the restart it exists for."""
        spec = harness.install("queue")
        _busy_prior(spec)
        queued_id = json.loads((await _fire()).stdout)["run_id"]
        reloaded = store.get(queued_id)
        assert overlap_mod.is_queued(reloaded)
        assert reloaded.extra[overlap_mod.QUEUED_AT_KEY]
        assert [r.id for r in overlap_mod.queued_runs(NAME)] == [queued_id]

    async def test_a_launched_run_carries_no_marker(self, harness) -> None:
        harness.install("queue")
        run_id = json.loads((await _fire()).stdout)["run_id"]
        assert not overlap_mod.is_queued(store.get(run_id))
        await harness.watchdog.controller(run_id).run_to_completion(timeout=15)

    def test_no_new_run_status_member_was_added(self) -> None:
        """The queue is a marker, not a state. A new `RunStatus` member would be a
        state-machine change with a frontend status union and badge `Record` to match."""
        assert {s.value for s in RunStatus} == {
            "draft",
            "running",
            "paused",
            "needs_input",
            "complete",
            "failed",
            "cancelled",
            "escalated",
        }


# ── a dry run writes nothing ────────────────────────────────────────────────


class TestDryRun:
    async def test_a_dry_run_against_a_busy_def_names_the_decision_and_writes_nothing(
        self, harness
    ) -> None:
        spec = harness.install("queue")
        _busy_prior(spec)
        result = await _fire(dry_run=True)
        assert result.outcome == "skip"
        assert json.loads(result.stdout)["would"] == "queue"
        assert _drafts() == []


# ── the ratchet ─────────────────────────────────────────────────────────────


class TestExhaustiveness:
    """A fourth member must not inherit a third member's behaviour — which is how `QUEUE`
    inherited "start now" for the length of the engine program."""

    @pytest.mark.parametrize("policy", list(OverlapPolicy), ids=lambda p: p.value)
    def test_every_member_has_a_branch(self, policy: OverlapPolicy) -> None:
        assert isinstance(decide(policy, active=1, queued=0), OverlapAction)

    def test_an_unhandled_policy_raises_rather_than_defaulting(self) -> None:
        """Proof the ratchet can fail: a value with no branch is refused, not quietly mapped
        to whichever policy happened to be written last."""
        with pytest.raises(AssertionError, match="no branch for OverlapPolicy"):
            decide(cast(OverlapPolicy, "future"), active=1, queued=0)

    def test_the_source_branches_on_every_member_by_name(self) -> None:
        """Read out of the SOURCE, not the behaviour: a branch that dispatched on something
        else (a truthiness test, a fallthrough shared by two members) would still satisfy the
        parametrized test above while leaving the next member's semantics undeclared — which
        is exactly the shape `QUEUE` hid in."""
        source = Path(inspect.getsourcefile(overlap_mod) or "").read_text(encoding="utf-8")
        fn = next(
            node
            for node in ast.parse(source).body
            if isinstance(node, ast.FunctionDef) and node.name == "decide"
        )
        named = {
            node.attr
            for node in ast.walk(fn)
            if isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "OverlapPolicy"
        }
        assert named == {member.name for member in OverlapPolicy}

    def test_the_provider_refuses_an_action_it_has_no_branch_for(self) -> None:
        """The call site's dangerous default is "fall through and launch". Every member of
        `OverlapAction` is named in the provider, so a new one cannot silently launch."""
        source = Path(
            inspect.getsourcefile(
                inspect.getmodule(RunWorkflowActionProvider) or RunWorkflowActionProvider
            )
            or ""
        ).read_text(encoding="utf-8")
        for member in OverlapAction:
            assert f"Act.{member.name}" in source, f"OverlapAction.{member.name} has no call site"


class TestDecisionTable:
    """The pure decision, stated as a table — the semantics in one readable place."""

    def test_nothing_in_flight_starts_under_every_policy(self) -> None:
        for policy in OverlapPolicy:
            action = decide(policy, active=0, queued=0)
            assert action in (OverlapAction.START, OverlapAction.CANCEL_THEN_START)

    def test_a_busy_def_gives_each_policy_a_different_action(self) -> None:
        assert decide(OverlapPolicy.SKIP, active=1, queued=0) == OverlapAction.SKIP
        assert decide(OverlapPolicy.QUEUE, active=1, queued=0) == OverlapAction.QUEUE
        assert (
            decide(OverlapPolicy.CANCEL_PREVIOUS, active=1, queued=0)
            == OverlapAction.CANCEL_THEN_START
        )

    def test_the_cap_drops_rather_than_growing(self) -> None:
        assert decide(OverlapPolicy.QUEUE, active=1, queued=MAX_QUEUE_DEPTH) == OverlapAction.DROP

    def test_a_pending_start_is_not_overtaken_by_a_new_one(self) -> None:
        """The window between "the prior finished" and "the drain launched it": a start
        arriving then must queue, not jump ahead of the run already waiting."""
        assert decide(OverlapPolicy.QUEUE, active=0, queued=0) == OverlapAction.START
        assert decide(OverlapPolicy.QUEUE, active=0, queued=1) == OverlapAction.DROP

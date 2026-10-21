"""A dispatched `stage` node's completion must have a CONSUMER.

`engine.dispatch_stage` spawns a subagent and returns `RUNNING` carrying
`{"subagent_id": info.id}` (``engine.py:779``). Nothing read that key, so a stage node
never left `RUNNING`: `_await_progress` pops the finished asyncio task out of
`_inflight` (``controller.py:2686``) before `_apply` runs, and once `_inflight` is empty
the tick loop takes the `else` branch at ``controller.py:573`` and yields on
`asyncio.sleep(0)` forever — which also means `_await_progress`, the ONLY caller of
`liveness.enforce_stall_timeouts` (``controller.py:2682``), stops being called at all. Measured: a
node stayed RUNNING for fifteen minutes after its subagent reported `done: True`.

These tests assert the CALL SITE, not a helper in isolation: the whole defect was that no
code path ever asked the subagent manager whether the spawn had finished. Every test here
except `test_an_unknown_subagent_id_does_not_invent_a_verdict` (the fail-safe guard, which
is vacuous until the reconciliation exists) reds against unmodified `main`.

**The CADENCE half (#259's second defect).** Settling the node did not stop the tick loop
spinning while it was still live: a dispatched stage has no awaitable in `_inflight` and no
`wake_at`, so the idle branch had no delay to sleep on and slept `0` — which, now that the
reconciler polls on every tick, means one `SubagentManager.get` per event-loop turn for the
whole multi-minute life of the subagent. Measured on `main`: 16737 `_step` calls and as many
lookups in a 9.00s window (1859/s), 6.14s CPU = 68% of one core, for a single otherwise-idle
run. The delay and the reconciler read ONE predicate
(`stage_settlement.awaiting_out_of_band_work`), and the `ast` rail below is what keeps them from
drifting back apart.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from workflows_stage_completion_shared import _FakeSubagents

from personalclaw.workflows import stage_settlement, store
from personalclaw.workflows.controller import EngineServices, RunController
from personalclaw.workflows.models import FailureClass, InstanceState, RunStatus, WorkflowRun

STAGE_PATH = "root.children[0]"

#: Bounded so a red (the run never terminating) costs seconds, not a hung suite. Nothing
#: here waits on wall-clock progress: the fake's completion is driven by the number of
#: reconciliation lookups the controller performs, so a passing run terminates on the
#: first tick after the subagent reports done rather than after a sleep.
RUN_TIMEOUT = 6.0


def _spec() -> dict[str, Any]:
    return {
        "name": "stage-completion",
        "root": {
            "kind": "sequence",
            "id": "root",
            "children": [{"kind": "stage", "id": "work", "config": {"prompt": "do the thing"}}],
        },
    }


@pytest.fixture
def wired(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A real `RunController` over a real spec, with only the subagent manager faked."""
    monkeypatch.setattr("personalclaw.workflows.leases.config_dir", lambda: tmp_path)

    def _build(**kw: Any) -> tuple[RunController, _FakeSubagents, list[dict[str, Any]]]:
        fake = _FakeSubagents(**kw)
        spec = _spec()
        run = store.create(WorkflowRun(id="", workflow_name="stage-completion"))
        store.write_spec(run.id, spec)
        controller = RunController(
            run, spec, services=EngineServices(subagents=fake, cwd=str(tmp_path))
        )
        # Spy on the ledger writer rather than the ledger FILE: the recorded symptom
        # was "no `step_completed`", and the call is the thing under test.
        completed: list[dict[str, Any]] = []
        real = controller.journal.step_completed

        def _spy(path: str, node_id: str, **kwargs: Any) -> None:
            completed.append({"path": path, "node_id": node_id, **kwargs})
            real(path, node_id, **kwargs)

        controller.journal.step_completed = _spy  # type: ignore[method-assign]
        return controller, fake, completed

    return _build


def _drive(controller: RunController) -> RunStatus:
    return asyncio.run(controller.run_to_completion(timeout=RUN_TIMEOUT))


def _recorded_sleeps(controller: RunController, monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Every delay the tick loop actually awaits, with the wait itself removed.

    The observable for the hot spin is the DELAY the idle branch sleeps on, so the test reads
    exactly that instead of timing the loop. Nothing here waits on the wall clock: the fake
    returns immediately and the run still terminates on the fake manager's lookup count, so a
    passing run and a spinning one take the same (negligible) time and the assertion cannot
    turn into a host-speed measurement.
    """
    real_sleep = asyncio.sleep
    seen: list[float] = []

    async def _fake_sleep(delay: float, *a: Any, **kw: Any) -> Any:
        seen.append(delay)
        return await real_sleep(0)

    async def _fake_wait(delay: float) -> None:
        # The idle branch's bounded wait (a wake can end it early): its delay is the observable.
        seen.append(delay)
        await real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", _fake_sleep)
    monkeypatch.setattr(controller, "_wait_for_wake", _fake_wait)
    return seen


# ── the call site ────────────────────────────────────────────────────────────


def test_the_controller_ASKS_the_manager_whether_a_dispatched_stage_finished(wired):
    """THE call-site assertion. On unmodified `main` this is zero: `subagent_id` is written
    at ``engine.py:779`` and read nowhere, so no code path ever performs the lookup. A
    timeout helper proven in isolation would not catch this — the helper is never reached
    for this node kind.
    """
    controller, fake, _ = wired()
    _drive(controller)

    assert fake.spawns, "the stage never dispatched, so this test proves nothing"
    assert fake.gets, (
        "the controller never asked the subagent manager whether the spawn finished — "
        "`subagent_id` is still a write with no reader"
    )
    assert (
        fake.gets[0] == "sub1"
    ), f"the reconciliation looked up {fake.gets[0]!r}, not the id the spawn returned"


def test_a_finished_stage_leaves_RUNNING_and_journals_step_completed(wired):
    """The reported symptom, inverted: `done: True` must become a terminal node."""
    controller, fake, completed = wired()
    status = _drive(controller)

    inst = controller.instances[STAGE_PATH]
    assert (
        inst.state is InstanceState.DONE
    ), f"a finished stage is still {inst.state.value} — this is the fifteen-minute hang"
    assert inst.completed_at, "a terminal node with no completion timestamp"
    assert status is RunStatus.COMPLETE, f"the run never completed (status={status.value})"
    assert [c["node_id"] for c in completed] == [
        "work"
    ], f"no `step_completed` for the stage: {completed}"


def test_the_stages_OUTPUT_reaches_the_binding_namespace_not_the_subagent_id(wired):
    """The RUNNING branch seeded `_outputs["work"] = {"subagent_id": ...}`. A downstream
    `{{nodes.work}}` binding must resolve to the subagent's RESULT, or the fix would leave
    every consumer of a stage reading an engine-internal id.
    """
    controller, fake, _ = wired()
    _drive(controller)

    out = controller._outputs.get("work")
    assert out is not None, "the stage produced no output at all"
    rendered = repr(out)
    assert "sub1" not in rendered, f"the binding namespace still holds the subagent id: {out!r}"
    assert "the stage's answer" in rendered, f"the subagent's result never landed: {out!r}"


# ── the hung stage ───────────────────────────────────────────────────────────


def test_a_reaped_stage_becomes_a_TIMEOUT_failure(wired):
    """A hung stage reaches the timeout machinery that actually owns a spawned worker's
    deadline: `SubagentManager._reaper_loop` (``subagent.py:739``) force-kills at
    `_default_timeout` and `_force_reap` sets `done=True` + `error="Reaped after ..."`
    (``subagent.py:791-793``). That verdict already existed and was simply unread — so
    reconciling it is what makes a hung stage visible, without a second deadline.
    """
    controller, fake, completed = wired(error="Reaped after 900s (exceeded 900s deadline) [stage]")
    status = _drive(controller)

    inst = controller.instances[STAGE_PATH]
    assert (
        inst.state is InstanceState.FAILED
    ), f"a reaped subagent left its node {inst.state.value} — the hang is not observable"
    assert inst.failure is not None
    assert inst.failure.failure_class is FailureClass.TIMEOUT, inst.failure.to_dict()
    assert "Reaped after 900s" in (
        inst.failure.cause_plain or ""
    ), "the manager's own verdict was discarded and replaced with a generic message"
    assert not completed, "a reaped stage must not be journalled as a completed step"
    assert status is RunStatus.FAILED, f"the run did not surface the failure (status={status})"


def test_an_unknown_subagent_id_does_not_invent_a_verdict(wired):
    """Fail-safe: `get()` returning None means the manager has no record — after a gateway
    restart its memory is empty for every id. "Unknown" must not be read as "finished":
    inventing DONE would bless work that never reported, and inventing FAILED would kill a
    run the watchdog is mid-adoption of. It stays RUNNING and the existing
    `audit.STALE_RUNNING` finding (``audit.py:39``) remains the backstop.

    Vacuous until the reconciliation exists, so it is falsified by mutation rather than by
    a red on `main`.
    """
    controller, fake, completed = wired(known=False)
    _drive(controller)

    inst = controller.instances[STAGE_PATH]
    assert (
        inst.state is InstanceState.RUNNING
    ), f"an unknown subagent id was turned into {inst.state.value} — a verdict on no evidence"
    assert inst.failure is None, "a failure was invented for a subagent nobody could look up"
    assert not completed, "an unknown subagent was journalled as a completed step"


# ── the tick cadence while the stage is live (the hot spin) ──────────────────


def test_a_live_stage_gets_a_bounded_poll_delay_not_a_zero_sleep(wired):
    """The second defect in #259, which the reconciler did not address and made worse.

    A dispatched stage is live work with no awaitable (`_inflight` is empty) and no deadline
    (`wake_at` is for WAITING), so the tick loop's idle branch had nothing to sleep on and slept
    `0` — turning the reconciler's per-tick `SubagentManager.get` into a busy poll for the whole
    multi-minute life of the subagent.
    """
    from personalclaw.workflows.controller import TICK_WAKE_SECS

    controller, fake, _ = wired(finish_after=99)
    inst = controller._instance(STAGE_PATH)
    inst.state = InstanceState.RUNNING
    inst.subagent_id = "sub1"

    assert not controller._inflight, "the premise is an EMPTY `_inflight`"
    assert controller._next_wake_delay() is None, (
        "`_next_wake_delay` reports a deadline for a RUNNING stage — this test's premise "
        "(that it cannot see one) no longer holds"
    )
    delay = controller._dispatched_poll_delay()
    assert delay is not None, "a live stage still yields no delay, so the idle branch sleeps 0"
    assert delay == TICK_WAKE_SECS, (
        f"the poll delay is {delay}, not the bound in-flight work already gets from "
        "`_await_progress` — a second cadence constant is a second answer to one question"
    )


def test_a_settled_stage_stops_being_polled(wired):
    """The delay must vanish with the work. A poll deadline that outlived the RUNNING node
    would keep a finished run's loop awake, trading a busy poll for a slow leak."""
    controller, fake, _ = wired()
    _drive(controller)

    assert controller.instances[STAGE_PATH].state is InstanceState.DONE
    assert stage_settlement.awaiting_out_of_band_work(controller) == []
    assert controller._dispatched_poll_delay() is None


def test_the_idle_tick_branch_never_sleeps_zero_while_a_stage_is_live(
    wired, monkeypatch: pytest.MonkeyPatch
):
    """End to end through the real loop: on `main` every recorded delay is `0`.

    Measured on `main` before this test existed: 16737 `_step` calls and as many manager
    lookups in a 9.00s window (1859/s), 6.14s CPU = 68% of one core, for one otherwise-idle
    run holding one RUNNING stage.
    """
    from personalclaw.workflows.controller import TICK_WAKE_SECS

    # Three lookups: the run cannot settle on the first reconciliation, so the idle branch is
    # forced to decide how long to wait at least once.
    controller, fake, _ = wired(finish_after=3)
    seen = _recorded_sleeps(controller, monkeypatch)
    status = _drive(controller)

    assert status is RunStatus.COMPLETE, f"the run did not finish (status={status.value})"
    assert seen, "the idle branch never ran, so this proves nothing about its delay"
    assert (
        0 not in seen
    ), f"the tick loop slept zero while the stage was live — this is the hot spin: {seen}"
    assert all(0 < d <= TICK_WAKE_SECS for d in seen), f"an unbounded tick delay: {seen}"


# ── the non-stage RUNNING path ───────────────────────────────────────────────
# The census that no dispatcher but `dispatch_stage` returns RUNNING reads engine.py, so it
# is a rail: tests/rails/test_workflows_stage_completion_tree.py.

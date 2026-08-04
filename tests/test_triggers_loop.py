"""The one clock loop, and the double-fire the cutover had to prevent (§3 / §6 — S100).

**🔴 MEASURED BEFORE WRITING — a store-only trigger had NO firing path.** S88 shipped
`service.tick()`, S96 taught it to arm, S97 made `overlap` enforce, S98 imported the crons and S99
re-pointed the API's read. But nothing CALLED the tick:

    boot starts ScheduleService: True
    boot starts a TICK loop    : False

So a trigger created the new way (store only, as `tools.create` writes it) was invisible to
the legacy
service and unreachable by the engine that could fire it. Re-pointing the API's WRITES first — the
order the queue implied — would have produced silently dead automations.

**🔴 AND RUNNING BOTH LOOPS WOULD DOUBLE-FIRE.** Measured on the owner's real store after the boot
migration: the legacy timer would fire `['j-at','j-cron','j-every','j-seq']` and the tick would fire
`['j-at','j-cron']` — a real overlap of two live automations. So the tick becomes the SOLE clock
engine and the legacy timer is not armed (`load_without_timer`).
"""

from __future__ import annotations

import asyncio

import pytest

from personalclaw.triggers import claims as C
from personalclaw.triggers import loop as L
from personalclaw.triggers import service as SVC
from personalclaw.triggers import wakeup as WK
from personalclaw.triggers.models import Trigger
from personalclaw.triggers.store import TriggerStore

NOW = 1_800_000_000.0


class _Provider:
    async def shutdown(self):
        return None


def _manager(*keys):
    from personalclaw.session import SessionManager, _Session

    manager = SessionManager.__new__(SessionManager)
    manager._sessions = {}
    for key in keys:
        manager._sessions[key] = _Session(provider=_Provider())
    return manager


def _clock(tid="j", *, overlap="skip", secs=60, enabled=True):
    return Trigger(
        id=tid,
        name=f"J-{tid}",
        kind="clock",
        enabled=enabled,
        overlap=overlap,
        spec={"kind": "interval", "interval_secs": secs},
        workflow={"provider": "run-prompt", "config": {"message": "go"}},
        next_fire_at=SVC.to_iso(NOW - 1),
    )


async def _ok(_payload):
    return {"status": "ok"}


# ── 🔴 the loop that was missing ──


def test_one_iteration_fires_dispatches_and_executes(tmp_path):
    """🔴 THE gap: the tick could decide, but nothing drove it. This is the whole chain in one call —
    tick → dispatch (S89) → drain (S90) — with only the LLM turn injected."""
    store = TriggerStore(base_dir=tmp_path)
    store.upsert(_clock())
    manager = _manager(WK.session_key_for("j"))
    ran: list[str] = []

    async def runner(payload):
        ran.append(payload.get("trigger_id", ""))
        return {"status": "ok"}

    result = asyncio.run(
        L.tick_once(store, runner=runner, sessions=manager, base_dir=tmp_path, now=NOW)
    )
    assert [f.trigger.id for f in result.fires] == ["j"]
    assert ran == ["j"]


def test_the_claim_is_released_so_the_next_slot_fires(tmp_path):
    """🔴 Found while wiring this loop: `executor.drain` took no `base_dir`, so `run_one`'s claim
    release was a no-op on every drained fire — which would block an `overlap: skip` trigger for the
    full 1h claim expiry after its first run. The release only works if the root reaches it."""
    store = TriggerStore(base_dir=tmp_path)
    store.upsert(_clock())
    manager = _manager(WK.session_key_for("j"))
    asyncio.run(L.tick_once(store, runner=_ok, sessions=manager, base_dir=tmp_path, now=NOW))
    assert C.is_running("j", now=NOW, base_dir=tmp_path) is False

    trigger = store.get("j").trigger
    trigger.next_fire_at = SVC.to_iso(NOW + 59)
    store.upsert(trigger)
    again = asyncio.run(
        L.tick_once(store, runner=_ok, sessions=manager, base_dir=tmp_path, now=NOW + 60)
    )
    assert [f.trigger.id for f in again.fires] == ["j"]


def test_the_next_fire_advances(tmp_path):
    store = TriggerStore(base_dir=tmp_path)
    store.upsert(_clock())
    manager = _manager(WK.session_key_for("j"))
    asyncio.run(L.tick_once(store, runner=_ok, sessions=manager, base_dir=tmp_path, now=NOW))
    assert SVC.to_epoch(store.get("j").trigger.next_fire_at) > NOW


def test_nothing_due_is_a_quiet_no_op(tmp_path):
    store = TriggerStore(base_dir=tmp_path)
    trigger = _clock()
    trigger.next_fire_at = SVC.to_iso(NOW + 3600)
    store.upsert(trigger)
    ran: list[str] = []

    async def runner(payload):
        ran.append("x")
        return {"status": "ok"}

    result = asyncio.run(
        L.tick_once(store, runner=runner, sessions=_manager(), base_dir=tmp_path, now=NOW)
    )
    assert result.fires == []
    assert ran == []


def test_an_empty_store_is_a_no_op(tmp_path):
    result = asyncio.run(
        L.tick_once(TriggerStore(base_dir=tmp_path), runner=_ok, base_dir=tmp_path, now=NOW)
    )
    assert result.fires == []


# ── resilience: the loop must outlive a bad fire ──


def test_a_failing_runner_does_not_stop_the_iteration(tmp_path):
    """One trigger's failure must not strand the others — a clock loop that died on one bad action
    would silently retire every automation on the machine."""
    store = TriggerStore(base_dir=tmp_path)
    store.save_all([_clock("a", overlap="parallel"), _clock("b", overlap="parallel")])
    manager = _manager(WK.session_key_for("a"), WK.session_key_for("b"))
    seen: list[str] = []

    async def flaky(payload):
        tid = payload.get("trigger_id", "")
        seen.append(tid)
        if tid == "a":
            raise RuntimeError("boom")
        return {"status": "ok"}

    result = asyncio.run(
        L.tick_once(store, runner=flaky, sessions=manager, base_dir=tmp_path, now=NOW)
    )
    assert sorted(f.trigger.id for f in result.fires) == ["a", "b"]
    assert sorted(seen) == ["a", "b"]  # both attempted


def test_no_session_manager_leaves_the_fires_decided_not_dropped(tmp_path):
    """An API-only process has no session manager. The fires must stay decided (the tick already
    persisted each next fire and wrote a ledger row) rather than vanishing silently."""
    store = TriggerStore(base_dir=tmp_path)
    store.upsert(_clock())
    result = asyncio.run(L.tick_once(store, runner=_ok, sessions=None, base_dir=tmp_path, now=NOW))
    assert [f.trigger.id for f in result.fires] == ["j"]
    assert result.ledger_rows


def test_run_forever_survives_a_raising_tick(monkeypatch, tmp_path):
    """🔴 `run_forever` must log and continue on any exception but re-raise `CancelledError`, or
    `_shutdown` could never stop it."""
    calls = {"n": 0}

    async def boom(*a, **k):
        calls["n"] += 1
        if calls["n"] >= 2:
            raise asyncio.CancelledError()
        raise RuntimeError("bad tick")

    async def no_sleep(_secs):
        return None

    monkeypatch.setattr(L, "tick_once", boom)
    monkeypatch.setattr(L.asyncio, "sleep", no_sleep)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(L.run_forever(TriggerStore(base_dir=tmp_path), runner=_ok))
    assert calls["n"] == 2  # it kept going after the RuntimeError


def test_the_iteration_sleep_is_bounded(monkeypatch, tmp_path):
    """A malformed `next_sleep` must not park the clock for hours."""

    class _Result:
        fires: list = []
        next_sleep = 10_000.0
        ledger_rows: list = []

    slept: list[float] = []

    async def fake_tick(*a, **k):
        return _Result()

    async def fake_sleep(secs):
        slept.append(secs)
        raise asyncio.CancelledError()

    monkeypatch.setattr(L, "tick_once", fake_tick)
    monkeypatch.setattr(L.asyncio, "sleep", fake_sleep)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(L.run_forever(TriggerStore(base_dir=tmp_path), runner=_ok))
    assert slept == [L.MAX_ITERATION_SLEEP_SECS]


# ── 🔴 the cutover: exactly one clock engine ──


def test_boot_rotates_run_history_without_a_legacy_service():
    """🔴 SUPERSEDED (S112). This asserted `load_without_timer` — the legacy service's boot call.

    That method did two things: load `crons.json` (for CRUD nothing uses any more) and rotate run
    history. Only the rotation was load-bearing, and `ScheduleRunStore` owns it, so boot calls that
    directly and the class is gone.
    """
    import inspect

    from personalclaw.gateway import GatewayOrchestrator

    src = inspect.getsource(GatewayOrchestrator._init_cron)
    assert "ScheduleRunStore(config_dir()).rotate_all()" in src
    assert "load_without_timer" not in src


def test_boot_has_no_legacy_timer_left_to_arm(tmp_path):
    """🔴 The double-fire guard, now unconditional. Measured on the owner's real store at the S100
    cutover: both engines held `j-at` and `j-cron` after the migration, so arming both would
    fire each twice. S100 stopped arming the legacy timer; S112 deleted the class that owned
    it, so there is no second engine left to arm by accident."""
    import inspect

    from personalclaw.gateway import GatewayOrchestrator

    src = inspect.getsource(GatewayOrchestrator._init_cron)
    # Strip comments: the boot path still EXPLAINS what retired, and the property under test is
    # about the CODE. Asserting on prose would make a docstring edit fail a behaviour test.
    code = "\n".join(ln for ln in src.split("\n") if not ln.strip().startswith("#"))
    assert "cron_svc" not in code
    assert "ScheduleService(" not in code


def test_boot_starts_the_clock_loop(tmp_path):
    """A loop nobody launches is the defect this session opened with."""
    import inspect

    from personalclaw.gateway import GatewayOrchestrator

    src = inspect.getsource(GatewayOrchestrator._init_cron)
    assert "_clock_loop" in src
    assert "_clock_task" in src


def test_shutdown_cancels_the_clock_loop():
    """A dangling clock task across shutdown leaks a firing loop into the next process."""
    import inspect

    from personalclaw.gateway import GatewayOrchestrator

    src = inspect.getsource(GatewayOrchestrator._shutdown)
    assert "_clock_task" in src


def test_one_dispatch_path_serves_both_clock_and_file_fires():
    """🔴 The clock loop and the file-watch loop must execute an action the same way. Two
    near-identical dispatches were the dual path the clean break forbids, so `_fire_file_trigger`
    delegates to `_fire_store_trigger`."""
    import inspect

    from personalclaw.gateway import GatewayOrchestrator

    src = inspect.getsource(GatewayOrchestrator._fire_file_trigger)
    assert "_fire_store_trigger" in src
    # And the shared dispatch must not hard-code one source's event name.
    shared = inspect.getsource(GatewayOrchestrator._fire_store_trigger)
    assert "event: str" in shared


def test_the_shared_dispatch_reads_both_action_shapes(tmp_path):
    """A migrated cron nests its action under `workflow.inline`; S92's chat tools write a flat
    `{provider, config}`. Reading only one would render half a real store's triggers unfireable."""
    import inspect

    from personalclaw.gateway import GatewayOrchestrator

    src = inspect.getsource(GatewayOrchestrator._fire_store_trigger)
    assert "inline" in src


# ── the live refresh a scheduled fire pushes (S107) ──


def _orch_with_state(state):
    """A bare orchestrator carrying just the dashboard state — enough to drive the fire path."""
    from personalclaw.gateway import GatewayOrchestrator

    orch = object.__new__(GatewayOrchestrator)
    orch.dashboard_state = state
    return orch


class _RecordingState:
    def __init__(self):
        self.pushed = []

    def push_refresh(self, *kinds):
        self.pushed.append(kinds)


def test_a_store_backed_fire_pushes_a_live_refresh():
    """🔴 The defect (S107): since the cutover, a SCHEDULED fire updated no open view.

    `ScheduleService._record_run` pushed `cron_history`, and `_record_run` is reachable only from
    `run_job` (manual) and `_run_job_isolated` (the retired timer) — so a user watching Executions
    saw a stale page until they navigated. Both kinds are pushed: `cron_history` for the run feed,
    `crons` for the list's status dots and next-fire times.
    """
    state = _RecordingState()
    _orch_with_state(state)._push_trigger_refresh()
    assert state.pushed == [("crons", "cron_history")]


def test_a_failing_fire_still_pushes_the_refresh():
    """In a `finally`, because a failed run is exactly the one someone is watching for."""
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import patch

    class BadProvider:
        async def execute(self, cfg, ctx, timeout=30):
            raise RuntimeError("action blew up")

    state = _RecordingState()
    orch = _orch_with_state(state)
    trigger = SimpleNamespace(
        id="clock:x", workflow={"provider": "bash", "config": {"command": "true"}}
    )
    with patch("personalclaw.action_providers.get_action_provider", lambda name: BadProvider()):
        asyncio.run(orch._fire_store_trigger(trigger, {"trigger_id": "clock:x"}))

    assert state.pushed == [("crons", "cron_history")]


def test_a_dashboardless_gateway_does_not_fail_on_the_refresh():
    """`--no-dashboard` has nothing to notify."""
    _orch_with_state(None)._push_trigger_refresh()  # must not raise


def test_an_orchestrator_with_no_dashboard_attribute_at_all_survives():
    """🔴 Found by the suite: `dashboard_state` may not EXIST yet.

    An orchestrator that has not reached `_init_dashboard` (or a bare one, which is how
    `test_gateway_file_watch.py` drives the fire path) has no such attribute — and an AttributeError
    raised from the fire path's `finally` would REPLACE the fire's own outcome. Hence `getattr`.
    """
    from personalclaw.gateway import GatewayOrchestrator

    bare = GatewayOrchestrator.__new__(GatewayOrchestrator)
    assert not hasattr(bare, "dashboard_state")
    bare._push_trigger_refresh()  # must not raise


def test_a_broken_broadcast_never_fails_a_fire():
    class Boom:
        def push_refresh(self, *kinds):
            raise RuntimeError("websocket closed")

    _orch_with_state(Boom())._push_trigger_refresh()  # must not raise


def test_the_legacy_refresh_callback_is_gone():
    """🔴 The clean break. The callback fired only from `_record_run`, and the manual-run HANDLER
    already pushes both kinds in its own `finally` — so keeping the seam would have meant a
    configurable hook that nothing configures and a duplicate broadcast on the one path it reached.
    """
    import inspect

    import pytest

    from personalclaw.gateway import GatewayOrchestrator

    # S112 deleted the whole class, which is a stronger statement than "the method is gone".
    with pytest.raises(ImportError):
        from personalclaw.schedule import ScheduleService  # noqa: F401
    src = inspect.getsource(GatewayOrchestrator)
    assert "set_refresh_callback" not in src

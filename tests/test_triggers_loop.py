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
        capabilities={"providers": ["run-prompt"]},
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


# ── S142: criterion 7's two SEPARATE wake sources, both of which had no caller ──


def _spool_one(home, *, key="notes/x"):
    from personalclaw.triggers.dispatch import Envelope, spool_fire

    return spool_fire(
        Envelope(
            seq=0,
            source="event:e-1",
            kind="memory.memory_write",
            payload={"trigger_id": "e-1", "key": key, "value": "hi"},
            emitted_at=NOW,
        )
    )


def test_an_IDLE_tick_still_drains_the_spool(tmp_path, monkeypatch):
    """🔴 `drain_spooled_fires` had NO caller, so a fire parked by a sync CLI memory write sat on
    disk forever — the silent drop the spool was written to fix, one layer up.

    Driven on an EMPTY due-set on purpose: the drain sits above `if not result.fires` because
    its own
    docstring says the spool is a separate wake source and "a tick with no due clock trigger must
    still drain it". An idle machine is exactly when a spooled fire is waiting.
    """
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    from personalclaw.config import loader

    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    from personalclaw.triggers.dispatch import drain_spool

    store = TriggerStore(base_dir=tmp_path)  # no triggers at all -> zero fires
    assert _spool_one(tmp_path)
    assert len(drain_spool()[0]) == 1

    result = asyncio.run(L.tick_once(store, runner=_ok, sessions=None, base_dir=tmp_path, now=NOW))
    assert result.fires == []
    assert drain_spool()[0] == [], "an idle tick must still consume the spool"


def test_the_spool_drains_EXACTLY_ONCE(tmp_path, monkeypatch):
    """Criterion 7's "no double-fire". `clear_spool` acks only what was handled, so a second tick
    finds nothing — and a fire that arrives DURING a drain survives it."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    from personalclaw.config import loader

    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    from personalclaw.triggers.dispatch import drain_spool

    store = TriggerStore(base_dir=tmp_path)
    _spool_one(tmp_path)
    fired: list[str] = []
    import personalclaw.event_triggers as et

    monkeypatch.setattr(et, "emit_event", lambda **kw: fired.append(kw["key"]))
    asyncio.run(L.tick_once(store, runner=_ok, sessions=None, base_dir=tmp_path, now=NOW))
    assert fired == ["notes/x"]
    asyncio.run(L.tick_once(store, runner=_ok, sessions=None, base_dir=tmp_path, now=NOW + 1))
    assert fired == ["notes/x"], "the second tick must not re-fire an acked spool entry"
    assert drain_spool()[0] == []


def test_a_spooled_fire_re_enters_through_the_SAME_seam(tmp_path, monkeypatch):
    """Not a second dispatch path. A spooled fire goes back through `emit_event`, the seam a
    LIVE memory write uses, so it cannot skip a gate a live fire walks — which is exactly how the
    `web_watch` screen gap (S134) opened."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    from personalclaw.config import loader

    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    seen: list[dict] = []
    import personalclaw.event_triggers as et

    monkeypatch.setattr(et, "emit_event", lambda **kw: seen.append(kw))
    _spool_one(tmp_path)
    asyncio.run(
        L.tick_once(
            TriggerStore(base_dir=tmp_path), runner=_ok, sessions=None, base_dir=tmp_path, now=NOW
        )
    )
    assert seen and seen[0]["event_type"] == "memory_write", seen
    assert seen[0]["key"] == "notes/x"


def test_a_DAMAGED_spool_line_does_not_hide_the_rest(tmp_path, monkeypatch):
    """A partial write at power-loss damages one line. Append-only JSONL is chosen so the others
    survive; the skipped line is logged, because it is a fire nobody will ever run."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    from personalclaw.config import loader

    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    from personalclaw.triggers.dispatch import spool_path

    _spool_one(tmp_path, key="good")
    with spool_path().open("a", encoding="utf-8") as handle:
        handle.write('{"seq": 1, "truncated\n')
    fired: list[str] = []
    import personalclaw.event_triggers as et

    monkeypatch.setattr(et, "emit_event", lambda **kw: fired.append(kw["key"]))
    asyncio.run(
        L.tick_once(
            TriggerStore(base_dir=tmp_path), runner=_ok, sessions=None, base_dir=tmp_path, now=NOW
        )
    )
    assert fired == ["good"]


# ── WF2AUT-13: §3.3's cursor rule had a complete decision layer and ZERO production callers ──
#
# `Handling`, `DrainAction`, `drain_decision` and `classify_handler_outcome` were written,
# documented and unit tested; nothing in `src/` referenced any of them. The drain did
# `handled += 1` unconditionally and its own comment named "the poison pill `drain_decision`
# names" while never calling it. Every test below drives the REAL drain (`tick_once`), not
# `drain_decision` directly — a decision layer proven only by its own unit tests is exactly the
# shape that shipped inert.


def _spool_kind(home, *, kind="memory.memory_write", key="notes/x", value="hi", now=NOW):
    from personalclaw.triggers.dispatch import Envelope, spool_fire

    return spool_fire(
        Envelope(
            seq=0,
            source="event:e-1",
            kind=kind,
            payload={"trigger_id": "e-1", "key": key, "value": value},
            emitted_at=now,
        )
    )


def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    from personalclaw.config import loader

    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)


def _engine_down(monkeypatch):
    """Make the PRE-DELIVERY engine resolve fail — a failure provably before any side effect."""
    import personalclaw.event_triggers as et

    def _boom():
        raise RuntimeError("event engine unreachable")

    monkeypatch.setattr(et, "get_engine", _boom)


def _tick(tmp_path, *, now=NOW):
    return asyncio.run(
        L.tick_once(
            TriggerStore(base_dir=tmp_path), runner=_ok, sessions=None, base_dir=tmp_path, now=now
        )
    )


def test_a_TRANSIENT_pre_delivery_failure_is_HELD_not_acked(tmp_path, monkeypatch):
    """The inert policy, now live. A re-entry that failed BEFORE the side-effect boundary used to
    be counted `handled += 1` and acked off the spool; it is now held for a bounded retry."""
    _isolate(tmp_path, monkeypatch)
    from personalclaw.triggers.dispatch import drain_spool, read_spool_hold

    _spool_kind(tmp_path)
    _engine_down(monkeypatch)

    _tick(tmp_path)

    kept = drain_spool()[0]
    assert len(kept) == 1, "a transient pre-delivery failure must NOT be acked off the spool"
    assert read_spool_hold() == (kept[0].event_id, 1), "the budget must be on disk, not in memory"


def test_a_failure_AFTER_the_side_effect_boundary_is_NEVER_retried(tmp_path, monkeypatch):
    """🔴 THE SAFETY PIN. HOLD means an envelope can run twice, and a double-fire is the one
    outcome criterion 7 bans. So the boundary is `emit_event` itself: once entered, the drain
    cannot know how far it got, and "I don't know" must resolve to DELIVERED.

    Driven by making `emit_event` raise — which production's `emit_event` never does (it swallows
    into a debug log), so a test is the only way to reach the branch that must not retry.
    """
    _isolate(tmp_path, monkeypatch)
    import personalclaw.event_triggers as et
    from personalclaw.triggers.dispatch import drain_spool, read_spool_hold

    _spool_kind(tmp_path)
    entered: list[str] = []

    def _raise_after_entering(**kw):
        entered.append(kw["key"])
        raise RuntimeError("the action already ran, then this blew up")

    monkeypatch.setattr(et, "emit_event", _raise_after_entering)

    _tick(tmp_path)
    assert entered == ["notes/x"], "the boundary must actually have been crossed"
    assert drain_spool()[0] == [], "a post-boundary failure must be ACKED, never held"
    assert read_spool_hold() == ("", 0)

    _tick(tmp_path, now=NOW + 1)
    assert entered == ["notes/x"], "a second tick must not re-enter an envelope whose action ran"


def test_the_retry_budget_IS_DURABLE_and_bounded(tmp_path, monkeypatch):
    """`held_retries` lives on disk, so HOLD is bounded ACROSS process lifetimes. An in-memory
    count would reset on every restart, and a retry that survives a restart without a surviving
    count is an UNBOUNDED retry — strictly worse than the unconditional ack this replaced, which
    at least terminated.

    Each `tick_once` call re-reads the sidecar from disk, which is exactly what a restart does.
    """
    _isolate(tmp_path, monkeypatch)
    from personalclaw.triggers.dispatch import (
        MAX_TRANSIENT_RETRIES,
        drain_spool,
        read_spool_hold,
        spool_hold_path,
    )

    _spool_kind(tmp_path)
    _engine_down(monkeypatch)
    event_id = drain_spool()[0][0].event_id

    counts = []
    for attempt in range(MAX_TRANSIENT_RETRIES):
        _tick(tmp_path, now=NOW + attempt)
        counts.append(read_spool_hold()[1])
        if not drain_spool()[0]:
            break

    assert counts == [1, 2, 3, 4, 0], counts
    assert drain_spool()[0] == [], "the poison pill must be given up, not held forever"
    assert not spool_hold_path().exists(), "the budget must be forgotten once the envelope is gone"
    assert event_id


def test_a_HOLD_is_HEAD_OF_LINE_and_keeps_the_tail_in_ORDER(tmp_path, monkeypatch):
    """`clear_spool(handled=N)` can only ack a PREFIX — there is no "keep line 1, ack line 2". So
    the drain stops at the first envelope it must hold and leaves the tail on disk, in order.
    Re-entering the tail first would reorder the stream; acking it would strand the head."""
    _isolate(tmp_path, monkeypatch)
    import personalclaw.event_triggers as et
    from personalclaw.triggers.dispatch import drain_spool

    _spool_kind(tmp_path, key="first")
    _spool_kind(tmp_path, key="second")
    fired: list[str] = []
    monkeypatch.setattr(et, "emit_event", lambda **kw: fired.append(kw["key"]))

    _engine_down(monkeypatch)
    _tick(tmp_path)
    assert fired == [], "nothing may re-enter while the head is held"
    assert [e.payload["key"] for e in drain_spool()[0]] == ["first", "second"]

    monkeypatch.undo()
    _isolate(tmp_path, monkeypatch)
    monkeypatch.setattr(et, "emit_event", lambda **kw: fired.append(kw["key"]))
    _tick(tmp_path, now=NOW + 1)
    assert fired == ["first", "second"], "the tail must follow the head, in order"


def test_an_IDENTICAL_payload_spooled_twice_fires_ONCE(tmp_path, monkeypatch):
    """`SKIP_DUPLICATE`'s honest producer. `event_id`/`payload_hash` are DETERMINISTIC over
    (source, kind, payload), so two lines with one hash inside `DEDUP_WINDOW_SECS` are the work
    the window was written to collapse — "an fs event fired twice for one save"."""
    _isolate(tmp_path, monkeypatch)
    import personalclaw.event_triggers as et
    from personalclaw.triggers.dispatch import drain_spool

    _spool_kind(tmp_path, key="same")
    _spool_kind(tmp_path, key="same")
    fired: list[str] = []
    monkeypatch.setattr(et, "emit_event", lambda **kw: fired.append(kw["key"]))

    _tick(tmp_path)
    assert fired == ["same"], "the second identical line must be skipped, not re-fired"
    assert drain_spool()[0] == [], "and it must still be ACKED, not left to retry forever"


def test_the_dedup_window_does_not_collapse_two_SEPARATE_facts(tmp_path, monkeypatch):
    """Compared on EMIT times, not on the tick's clock: the same payload seen an hour apart is two
    facts. A window that read the tick's `now` for both would make every re-spool a duplicate."""
    _isolate(tmp_path, monkeypatch)
    import personalclaw.event_triggers as et
    from personalclaw.triggers.dispatch import DEDUP_WINDOW_SECS

    _spool_kind(tmp_path, key="same", now=NOW)
    _spool_kind(tmp_path, key="same", now=NOW + DEDUP_WINDOW_SECS + 1)
    fired: list[str] = []
    monkeypatch.setattr(et, "emit_event", lambda **kw: fired.append(kw["key"]))

    _tick(tmp_path)
    assert fired == ["same", "same"]


def test_an_envelope_with_no_event_kind_is_PERMANENT_not_retried(tmp_path, monkeypatch):
    """`Handling.PERMANENT`'s honest producer, and a real drop this found: an envelope with no
    kind used to reach `emit_event` with an empty `event_type`, match nothing, and be counted as a
    delivered fire — a drop wearing a success. No retry can make it routable, so it is consumed
    LOUDLY rather than held (holding would be the poison pill `drain_decision` warns about)."""
    _isolate(tmp_path, monkeypatch)
    import personalclaw.event_triggers as et
    from personalclaw.triggers.dispatch import drain_spool

    _spool_kind(tmp_path, kind="", key="unroutable")
    called: list[dict] = []
    monkeypatch.setattr(et, "emit_event", lambda **kw: called.append(kw))

    _tick(tmp_path)
    assert called == [], "an unroutable envelope must not reach the emitter at all"
    assert drain_spool()[0] == [], "and must be acked, because no retry can route it"


def test_a_HOLD_that_cannot_persist_its_budget_is_ACKED_not_retried_forever(tmp_path, monkeypatch):
    """The one place the design refuses to hold. An unbounded retry is worse than the swallow this
    replaced, so a drain that cannot write the count acks the envelope loudly instead."""
    _isolate(tmp_path, monkeypatch)
    from personalclaw.triggers import dispatch as D

    _spool_kind(tmp_path)
    _engine_down(monkeypatch)
    monkeypatch.setattr(D, "write_spool_hold", lambda **kw: False)

    _tick(tmp_path)
    assert D.drain_spool()[0] == [], "an unbudgetable hold must be acked, not retried forever"


# ── the exhaustiveness ratchet (the WV-13/WV-14 pattern: a raising tail + an AST read) ──


class TestTheDrainIsExhaustive:
    def test_the_drain_branches_on_every_DrainAction_by_name(self) -> None:
        """Read out of the SOURCE, not the behaviour: a branch that dispatched on something else
        (a truthiness test, a fallthrough shared by two members) would still satisfy a behavioural
        test while leaving the next member's semantics undeclared."""
        import ast
        import inspect
        from pathlib import Path

        from personalclaw.triggers.dispatch import DrainAction

        source = Path(inspect.getsourcefile(L) or "").read_text(encoding="utf-8")
        fn = next(
            node
            for node in ast.parse(source).body
            if isinstance(node, ast.FunctionDef) and node.name == "_drain_spool"
        )
        named = {
            node.attr
            for node in ast.walk(fn)
            if isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "DrainAction"
        }
        assert named == {member.name for member in DrainAction}

    def test_an_unhandled_DrainAction_raises_rather_than_silently_acking(
        self, tmp_path, monkeypatch
    ) -> None:
        """Proof the ratchet CAN fail. A member with no branch must refuse, not inherit whichever
        action happened to be written last — the silent ack this whole atom exists to remove."""
        _isolate(tmp_path, monkeypatch)
        from personalclaw.triggers import dispatch as D

        _spool_kind(tmp_path)
        monkeypatch.setattr(D, "drain_decision", lambda **kw: ("future_action", ""))
        with pytest.raises(AssertionError, match="no branch for DrainAction"):
            L._drain_spool(now=NOW)

    def test_drain_decision_branches_on_every_Handling_by_name(self) -> None:
        import ast
        import inspect
        from pathlib import Path

        from personalclaw.triggers import dispatch as D

        source = Path(inspect.getsourcefile(D) or "").read_text(encoding="utf-8")
        fn = next(
            node
            for node in ast.parse(source).body
            if isinstance(node, ast.FunctionDef) and node.name == "drain_decision"
        )
        named = {
            node.attr
            for node in ast.walk(fn)
            if isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "Handling"
        }
        assert named == {member.name for member in D.Handling}

    def test_an_unhandled_handling_raises_rather_than_inheriting_the_transient_rule(self) -> None:
        """The transient rules used to be the FALLTHROUGH, so an unknown handling string silently
        inherited "retry five times then drop"."""
        from personalclaw.triggers.dispatch import drain_decision

        with pytest.raises(AssertionError, match="no branch for handling"):
            drain_decision(handling="future_handling", held_retries=0)

    @pytest.mark.parametrize("member", ["DELIVERED", "TRANSIENT", "PERMANENT"])
    def test_every_Handling_member_is_PRODUCED_at_the_real_boundary(
        self, member, tmp_path, monkeypatch
    ) -> None:
        """Producers, not just branches. `Handling` had no writer anywhere in `src/` — every member
        must now come out of `_reenter_spooled` for a state a real spooled envelope can be in."""
        _isolate(tmp_path, monkeypatch)
        import personalclaw.event_triggers as et
        from personalclaw.triggers.dispatch import Envelope, Handling

        kind = "memory.memory_write"
        if member == "PERMANENT":
            kind = ""
        if member == "TRANSIENT":
            _engine_down(monkeypatch)
        else:
            monkeypatch.setattr(et, "emit_event", lambda **kw: None)
        handling, _detail = L._reenter_spooled(
            Envelope(seq=0, source="event:e-1", kind=kind, payload={"key": "k"}, emitted_at=NOW),
            now=NOW,
        )
        assert handling == getattr(Handling, member).value


# ── S142: pending approvals re-arm (`retry_queue` had no caller either) ──


class _Unready:
    """A session manager whose enqueue fails until `ready` is set."""

    _sessions: dict = {}

    def __init__(self):
        self.ready = False
        self.queued: list[str] = []

    def enqueue(self, key, ts, text, force=False, wakeup=None):
        if not self.ready:
            return False
        self.queued.append(key)
        return True


def _resume(tid="t-0"):
    return WK.Wakeup(kind="resume", trigger_id=tid, session_key=f"s-{tid}", seq=1, emitted_at=NOW)


def test_an_undeliverable_RESUME_is_held_and_re_armed():
    """🔴 `wakeup.retry_queue` had NO caller, so criterion 7's "pending approvals re-arm" was
    implemented and unreachable: a resume whose session was not ready was built, classified
    REQUEUED, and thrown away. §3.2 refuses to let anyone drop one — it carries a gate answer, and
    eating it strands the parked run forever waiting for a reply the user already gave."""
    sessions = _Unready()
    delivery = WK.deliver(sessions, _resume())
    assert delivery.needs_retry, delivery.disposition

    pending: list = []
    L._hold_resumes(WK, [delivery], pending)
    assert len(pending) == 1

    assert L._retry_pending_resumes(sessions, pending) == 0
    assert len(pending) == 1, "a still-unready session must KEEP the resume, not drop it"

    sessions.ready = True
    assert L._retry_pending_resumes(sessions, pending) == 1
    assert pending == [], "a delivered resume leaves the queue"


def test_a_droppable_WAKE_is_never_held():
    """Only a resume is un-droppable. Holding wakes too would re-fire a scheduled trigger whose
    session was merely busy — which `overlap: skip` exists to prevent."""
    sessions = _Unready()
    wake = WK.Wakeup(kind="wake", trigger_id="t-1", session_key="s-1", seq=1, emitted_at=NOW)
    delivery = WK.deliver(sessions, wake)
    pending: list = []
    L._hold_resumes(WK, [delivery], pending)
    assert pending == []


def test_the_resume_queue_is_BOUNDED_and_drops_the_OLDEST():
    """§3.2 says a resume is never dropped; this is the bounded exception. A session that stays
    unready forever would grow the queue without limit, and an OOM takes down every automation
    rather than one. The OLDEST goes: the run that asked longest ago is likeliest to be gone, and
    the newest answer is the one a user is still waiting on."""
    pending = [
        WK.Wakeup(kind="resume", trigger_id=f"old-{i}", session_key=f"s{i}")
        for i in range(L.MAX_PENDING_RESUMES + 5)
    ]
    newest = WK.Delivery(
        WK.Disposition.REQUEUED.value,
        WK.Wakeup(kind="resume", trigger_id="newest", session_key="s-new"),
    )
    L._hold_resumes(WK, [newest], pending)
    assert len(pending) == L.MAX_PENDING_RESUMES
    assert pending[-1].trigger_id == "newest"


def test_the_retry_queue_SURVIVES_a_tick(tmp_path):
    """The queue is owned by `run_forever`, not by `tick_once`: a list held inside one iteration
    would be discarded on every return, which is the same silent drop §3.2 refuses."""
    store = TriggerStore(base_dir=tmp_path)
    store.upsert(_clock())
    pending: list = [_resume()]
    asyncio.run(
        L.tick_once(
            store,
            runner=_ok,
            sessions=_Unready(),
            base_dir=tmp_path,
            now=NOW,
            pending_resumes=pending,
        )
    )
    assert len(pending) == 1, "an unready session must leave the held resume in place across a tick"

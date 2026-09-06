"""The nudge adapter — autonudge's semantics riding the automations substrate (WF2AUT-11 half 2).

`personalclaw/autonudge.py` (private JSON store + one asyncio timer per loop) is DELETED; these
tests pin the port in `personalclaw/triggers/nudge.py`: loops are `Trigger{kind: idle}` rows,
due-ness rides `idle_poll`'s sidecar, and delivery walks `AutoNudgeService.deliver` — so every
timing here is INJECTED through `now=`; there is not one `sleep` in this file.

The suite keeps the OLD file's semantic coverage (fire on idle, first_idle_secs one-shot +
clamps, one-loop-per-session, restart survival, max_cycles deactivation, stop-sentinel removal,
delivered-only counting, fail-open on a raising deliverer, notify_* backpressure, feature flag)
and adds the port's own seams: the trigger-store row shape, the wire-compat NudgeLoop view, the
lossless `autonudge.json` migration, and the end-to-end tick (`loop.tick_once` → `idle_poll` →
`deliver` → `on_fire`) that IS "the loop tick engine rides kind:idle".
"""

import json
from dataclasses import asdict

import pytest

from personalclaw.triggers import idle_poll as IP
from personalclaw.triggers import nudge as N
from personalclaw.triggers.nudge import AutoNudgeService, NudgeLoop
from personalclaw.triggers.store import TriggerStore

NOW = 1_800_000_000.0


@pytest.fixture(autouse=True)
def _enable(monkeypatch):
    monkeypatch.setenv("PERSONALCLAW_AUTONUDGE", "1")
    yield
    # A leaked singleton would make another file's idle fires route through a dead service.
    N._INSTANCE = None


@pytest.fixture
def svc(tmp_path):
    return AutoNudgeService(base_dir=tmp_path)


def _arm(loop_id, tmp_path, *, armed_at=NOW, cycle_count=0, error_count=0):
    state = IP.load_state(loop_id, base_dir=tmp_path)
    state.armed_at = armed_at
    state.cycle_count = cycle_count
    state.error_count = error_count
    IP.save_state(loop_id, state, base_dir=tmp_path)
    return state


def _row(svc_or_path, loop_id):
    base = svc_or_path if not isinstance(svc_or_path, AutoNudgeService) else svc_or_path._base_dir
    loaded = TriggerStore(base_dir=base).get(loop_id)
    assert loaded is not None, f"no trigger row for {loop_id}"
    return loaded


# ── the row shape: a nudge loop IS a kind:idle trigger ──


@pytest.mark.asyncio
async def test_add_writes_a_clean_kind_idle_row(svc, tmp_path):
    loop = await svc.add(session_name="chat-1-123", message="go", idle_secs=30, max_cycles=4)
    loaded = _row(tmp_path, loop.id)
    assert loaded.issues == [], "the minted row must parse without a single validation chip"
    t = loaded.trigger
    assert t.kind == "idle"
    assert t.spec["scope"] == "session:chat-1-123"
    assert t.spec["message"] == "go"
    assert t.spec["max_cycles"] == 4
    assert t.session == "conversation:chat-1-123"
    assert N.is_nudge(t), "the message is the routing discriminator and it must read as one"


@pytest.mark.asyncio
async def test_the_wire_view_keeps_the_legacy_field_census(svc):
    """`asdict(NudgeLoop)` is the `/api/autonudge` payload and the `autonudge_state` WS body.
    The port must not add, drop, or rename a key."""
    loop = await svc.add(session_name="chat-1-123", message="go", idle_secs=30)
    assert set(asdict(loop)) == {
        "id",
        "session_name",
        "message",
        "idle_secs",
        "max_cycles",
        "cycle_count",
        "active",
        "last_fire_ts",
        "created_ts",
        "stop_sentinel_path",
        "error_count",
        "first_idle_secs",
    }
    assert loop.created_ts > 0.0


# ── fire on idle, through the adapter's deliver ──


@pytest.mark.asyncio
async def test_deliver_fires_on_fire_and_counts_the_cycle(svc, tmp_path):
    fired: list[NudgeLoop] = []

    async def on_fire(loop):
        fired.append(loop)
        return True

    svc._on_fire = on_fire
    loop = await svc.add(session_name="chat-1-123", message="go", idle_secs=15)
    _arm(loop.id, tmp_path)
    ok, why = await svc.deliver(_row(tmp_path, loop.id).trigger, now=NOW + 16)
    assert (ok, why) == (True, "")
    assert [lp.id for lp in fired] == [loop.id]
    refreshed = svc.get_by_session("chat-1-123")
    assert refreshed.cycle_count == 1
    assert refreshed.last_fire_ts == NOW + 16


@pytest.mark.asyncio
async def test_skip_when_delivery_returns_false(svc, tmp_path):
    """Delivered-only (the old autonudge lines 343-352): a skipped fire (session mid-turn)
    advances NOTHING — not cycle_count, not last_fire, not the arm point — so it retries
    next tick instead of waiting another whole quiet period."""

    async def on_fire_skip(loop):
        return False

    svc._on_fire = on_fire_skip
    loop = await svc.add(session_name="chat-1-123", message="go", idle_secs=15)
    _arm(loop.id, tmp_path)
    ok, why = await svc.deliver(_row(tmp_path, loop.id).trigger, now=NOW + 16)
    assert ok is False and why == IP.SKIP_SESSION_BUSY
    state = IP.load_state(loop.id, base_dir=tmp_path)
    assert state.cycle_count == 0
    assert state.last_fire == 0.0
    assert state.armed_at == NOW, "a dropped fire re-armed, costing a whole extra quiet period"


@pytest.mark.asyncio
async def test_fire_callback_exception_does_not_deactivate(svc, tmp_path):
    """An exception in on_fire is swallowed as not-delivered; the loop stays active."""

    async def on_fire_raise(loop):
        raise RuntimeError("kaboom")

    svc._on_fire = on_fire_raise
    loop = await svc.add(session_name="chat-1-123", message="go", idle_secs=15)
    _arm(loop.id, tmp_path)
    ok, _why = await svc.deliver(_row(tmp_path, loop.id).trigger, now=NOW + 16)
    assert ok is False
    refreshed = svc.get_by_session("chat-1-123")
    assert refreshed.cycle_count == 0
    assert refreshed.active is True


@pytest.mark.asyncio
async def test_no_deliverer_is_a_typed_non_delivery(svc, tmp_path):
    loop = await svc.add(session_name="chat-1-123", message="go", idle_secs=15)
    _arm(loop.id, tmp_path)
    ok, why = await svc.deliver(_row(tmp_path, loop.id).trigger, now=NOW + 16)
    assert (ok, why) == (False, "no_deliverer")


# ── max_cycles + stop sentinel (the old _timer's pre-checks, in order) ──


@pytest.mark.asyncio
async def test_max_cycles_deactivates_but_keeps_the_loop(svc, tmp_path):
    events: list[str] = []
    svc.subscribe(lambda ev, lp: events.append(ev))
    loop = await svc.add(session_name="chat-1-123", message="go", idle_secs=15, max_cycles=2)
    _arm(loop.id, tmp_path, cycle_count=2)
    ok, why = await svc.deliver(_row(tmp_path, loop.id).trigger, now=NOW + 16)
    assert (ok, why) == (False, "max_cycles")
    refreshed = svc.get_by_session("chat-1-123")
    assert refreshed is not None, "cap reached must DEACTIVATE, not remove — a resume re-arms it"
    assert refreshed.active is False
    assert "updated" in events


@pytest.mark.asyncio
async def test_stop_sentinel_removes_loop(svc, tmp_path):
    sentinel = tmp_path / "STOP"
    loop = await svc.add(
        session_name="chat-1-123", message="go", idle_secs=15, stop_sentinel_path=str(sentinel)
    )
    _arm(loop.id, tmp_path)
    sentinel.write_text("halt")
    ok, why = await svc.deliver(_row(tmp_path, loop.id).trigger, now=NOW + 16)
    assert (ok, why) == (False, "stop_sentinel")
    assert svc.get_by_session("chat-1-123") is None
    assert not IP._state_path(loop.id, tmp_path).exists(), "a removed loop left its sidecar behind"


# ── backpressure: the exact notify_* contracts the chat hot paths rely on ──


@pytest.mark.asyncio
async def test_notify_turn_complete_rearms_from_turn_end(svc, tmp_path, monkeypatch):
    loop = await svc.add(session_name="chat-1-123", message="go", idle_secs=15)
    _arm(loop.id, tmp_path, armed_at=NOW - 999)
    monkeypatch.setattr(N.time, "time", lambda: NOW)
    svc.notify_turn_complete("chat-1-123")
    state = IP.load_state(loop.id, base_dir=tmp_path)
    assert state.armed_at == NOW, "the quiet period must restart at the TURN END"
    # And the trigger is not due until a full idle_secs after that.
    trigger = _row(tmp_path, loop.id).trigger
    assert IP.is_idle(trigger, state, now=NOW + 5)[0] is False
    assert IP.is_idle(trigger, state, now=NOW + 16)[0] is True


@pytest.mark.asyncio
async def test_user_input_defers_the_pending_nudge(svc, tmp_path, monkeypatch):
    """The old engine CANCELLED the timer; the poll world moves the arm point — same effect
    (nothing fires until a fresh quiet period elapses), and it survives a restart."""
    loop = await svc.add(session_name="chat-1-123", message="go", idle_secs=15)
    _arm(loop.id, tmp_path)
    trigger = _row(tmp_path, loop.id).trigger
    assert IP.is_idle(trigger, IP.load_state(loop.id, base_dir=tmp_path), now=NOW + 16)[0]
    monkeypatch.setattr(N.time, "time", lambda: NOW + 10)
    svc.notify_user_input("chat-1-123")
    state = IP.load_state(loop.id, base_dir=tmp_path)
    assert IP.is_idle(trigger, state, now=NOW + 16)[0] is False, "user input did not defer"


@pytest.mark.asyncio
async def test_three_consecutive_errored_turns_deactivate(svc, tmp_path):
    events: list[str] = []
    svc.subscribe(lambda ev, lp: events.append(ev))
    loop = await svc.add(session_name="chat-1-123", message="go", idle_secs=15)
    svc.notify_turn_complete("chat-1-123", errored=True)
    svc.notify_turn_complete("chat-1-123", errored=True)
    assert svc.get_by_session("chat-1-123").active is True
    svc.notify_turn_complete("chat-1-123", errored=True)
    refreshed = svc.get_by_session("chat-1-123")
    assert refreshed is not None, "errored-out must deactivate, not remove — it can be resumed"
    assert refreshed.active is False
    assert refreshed.error_count == 3
    assert "errored_out" in events
    assert loop.id == refreshed.id


@pytest.mark.asyncio
async def test_a_clean_turn_resets_the_error_count(svc, tmp_path):
    await svc.add(session_name="chat-1-123", message="go", idle_secs=15)
    svc.notify_turn_complete("chat-1-123", errored=True)
    svc.notify_turn_complete("chat-1-123", errored=True)
    svc.notify_turn_complete("chat-1-123", errored=False)
    svc.notify_turn_complete("chat-1-123", errored=True)
    assert svc.get_by_session("chat-1-123").active is True, "the reset on a clean turn vanished"


# ── first_idle_secs: one-shot + clamps ──


@pytest.mark.asyncio
async def test_first_idle_secs_shortens_only_the_first_fire(svc, tmp_path):
    async def on_fire(loop):
        return True

    svc._on_fire = on_fire
    loop = await svc.add(
        session_name="code-abcd1234", message="go", idle_secs=120, first_idle_secs=15
    )
    assert loop.first_idle_secs == 15
    _arm(loop.id, tmp_path)
    trigger = _row(tmp_path, loop.id).trigger
    state = IP.load_state(loop.id, base_dir=tmp_path)
    assert IP.wait_secs(trigger, state) == 15
    ok, _ = await svc.deliver(trigger, now=NOW + 16)
    assert ok is True
    # Spent: the view reads 0 and the next wait is the full idle_secs.
    refreshed = svc.get_by_session("code-abcd1234")
    assert refreshed.first_idle_secs == 0
    assert IP.wait_secs(trigger, IP.load_state(loop.id, base_dir=tmp_path)) == 120


@pytest.mark.asyncio
async def test_first_idle_secs_clamped_to_idle_secs(svc):
    a = await svc.add(session_name="code-aaaaaaaa", message="go", idle_secs=30, first_idle_secs=999)
    assert a.first_idle_secs == 30
    b = await svc.add(session_name="code-bbbbbbbb", message="go", idle_secs=120, first_idle_secs=1)
    assert b.first_idle_secs == 15
    c = await svc.add(session_name="code-cccccccc", message="go", idle_secs=120)
    assert c.first_idle_secs == 0


@pytest.mark.asyncio
async def test_idle_secs_clamped(svc):
    loop_low = await svc.add(session_name="s1", message="m", idle_secs=5)
    assert loop_low.idle_secs == 15
    loop_high = await svc.add(session_name="s2", message="m", idle_secs=100_000)
    assert loop_high.idle_secs == 86400


# ── CRUD semantics the HTTP surface and loop manager rely on ──


@pytest.mark.asyncio
async def test_one_loop_per_session_replaces(svc):
    l1 = await svc.add(session_name="chat-1-123", message="first", idle_secs=15)
    l2 = await svc.add(session_name="chat-1-123", message="second", idle_secs=15)
    assert l1.id != l2.id
    all_loops = svc.list_all()
    assert len(all_loops) == 1
    assert all_loops[0].message == "second"


@pytest.mark.asyncio
async def test_update_changes_message_and_idle(svc, tmp_path):
    loop = await svc.add(session_name="chat-1-123", message="old", idle_secs=30)
    updated = await svc.update(loop.id, message="new", idle_secs=60)
    assert updated is not None
    assert updated.message == "new"
    assert updated.idle_secs == 60
    assert _row(tmp_path, loop.id).trigger.spec["message"] == "new"


@pytest.mark.asyncio
async def test_deactivated_loops_stay_listed_for_the_watchdog(svc, tmp_path):
    """`LoopWatchdog._loop_exhausted` reads `.active`/`.cycle_count` AFTER deactivation to tell
    'budget spent' from 'paused mid-budget' — so a deactivated loop must remain visible."""
    loop = await svc.add(session_name="loop-abc123", message="go", idle_secs=15, max_cycles=3)
    _arm(loop.id, tmp_path, cycle_count=3)
    await svc.update(loop.id, active=False)
    view = svc.get_by_session("loop-abc123")
    assert view is not None
    assert view.active is False and view.cycle_count == 3


# ── persistence + restart ──


@pytest.mark.asyncio
async def test_persistence_across_restart(tmp_path):
    svc1 = AutoNudgeService(base_dir=tmp_path)
    await svc1.start()
    loop = await svc1.add(session_name="chat-1-123", message="go", idle_secs=15, max_cycles=5)
    svc1.stop()

    svc2 = AutoNudgeService(base_dir=tmp_path)
    await svc2.start()
    restored = svc2.get_by_session("chat-1-123")
    assert restored is not None
    assert restored.id == loop.id
    assert restored.message == "go"
    assert restored.max_cycles == 5
    # The boot re-arm: a restart waits a fresh quiet period, exactly as the old
    # re-armed timers did — the arm point moved to start() time.
    assert IP.load_state(loop.id, base_dir=tmp_path).armed_at >= loop.created_ts
    svc2.stop()


@pytest.mark.asyncio
async def test_disabled_when_flag_off(tmp_path, monkeypatch):
    monkeypatch.setenv("PERSONALCLAW_AUTONUDGE", "0")
    svc = AutoNudgeService(base_dir=tmp_path)
    await svc.start()
    assert not N.enabled()
    assert N.get_instance() is None, "a disabled service must not register the singleton"


# ── the lossless legacy migration ──


@pytest.mark.asyncio
async def test_legacy_autonudge_json_migrates_losslessly(tmp_path):
    legacy = {
        "version": 1,
        "loops": [
            {
                "id": "ab12cd34",
                "session_name": "loop-xyz",
                "message": "keep going",
                "idle_secs": 90,
                "max_cycles": 7,
                "cycle_count": 3,
                "active": True,
                "last_fire_ts": NOW - 100,
                "created_ts": NOW - 5000,
                "stop_sentinel_path": "/tmp/x/STOP",
                "error_count": 1,
                "first_idle_secs": 20,
            },
            {"id": "ee55ff66", "session_name": "chat-2", "message": "hi", "active": False},
        ],
    }
    (tmp_path / "autonudge.json").write_text(json.dumps(legacy), encoding="utf-8")
    svc = AutoNudgeService(base_dir=tmp_path)
    await svc.start()

    lp = svc.get_by_session("loop-xyz")
    assert lp is not None
    assert lp.id == "ab12cd34", "the loop id is the wire handle and must survive verbatim"
    assert (lp.message, lp.idle_secs, lp.max_cycles) == ("keep going", 90, 7)
    assert (lp.cycle_count, lp.error_count) == (3, 1)
    assert lp.last_fire_ts == NOW - 100
    assert lp.created_ts == NOW - 5000
    assert lp.stop_sentinel_path == "/tmp/x/STOP"
    assert lp.first_idle_secs == 0, "cycle_count > 0 means the one-shot was already spent"

    inactive = svc.get_by_session("chat-2")
    assert inactive is not None and inactive.active is False

    assert not (tmp_path / "autonudge.json").exists()
    assert (tmp_path / "autonudge.json.migrated").exists(), "kept for rollback, renamed not deleted"
    svc.stop()


# ── 🔴 end to end: the loop tick engine rides kind:idle ──


@pytest.mark.asyncio
async def test_a_nudge_row_fires_through_the_REAL_tick(tmp_path, monkeypatch):
    """`loop.tick_once` → `idle_poll.poll` → `AutoNudgeService.deliver` → `on_fire`. No timers,
    no clock trigger — the substrate tick IS the loop ticker now. The injected runner must NOT
    see the fire: a nudge row routes to the deliverer, never the wake path."""
    from personalclaw.triggers import loop as L

    fired: list[str] = []

    async def on_fire(loop):
        fired.append(loop.session_name)
        return True

    svc = AutoNudgeService(base_dir=tmp_path, on_fire=on_fire)
    loop = await svc.add(session_name="loop-abc123", message="next cycle", idle_secs=60)
    _arm(loop.id, tmp_path)
    monkeypatch.setattr(N, "_INSTANCE", svc)

    ran: list[str] = []

    async def runner(payload):
        ran.append(payload.get("trigger_id", ""))
        return {"status": "ok"}

    store = TriggerStore(base_dir=tmp_path)
    await L.tick_once(store, runner=runner, sessions=None, base_dir=tmp_path, now=NOW + 61)
    assert fired == ["loop-abc123"], "the tick did not reach the nudge deliverer"
    assert ran == [], "a nudge fire leaked into the wake/action path"
    assert IP.load_state(loop.id, base_dir=tmp_path).cycle_count == 1


@pytest.mark.asyncio
async def test_a_due_nudge_row_with_NO_service_is_a_typed_skip(tmp_path, monkeypatch):
    """Flag off / API-only process: the fire is reported, never wake-path'd and never counted,
    so it retries once a service exists."""
    svc = AutoNudgeService(base_dir=tmp_path)
    loop = await svc.add(session_name="loop-abc123", message="next cycle", idle_secs=60)
    _arm(loop.id, tmp_path)
    monkeypatch.setattr(N, "_INSTANCE", None)
    store = TriggerStore(base_dir=tmp_path)
    delivered, skipped = await IP.poll(store, None, None, now=NOW + 61, base_dir=tmp_path)
    assert delivered == 0
    assert [r["reason"] for r in skipped] == [IP.SKIP_NUDGE_UNAVAILABLE]
    assert IP.load_state(loop.id, base_dir=tmp_path).cycle_count == 0


def test_the_routing_discriminators_agree():
    """`idle_poll._is_nudge` is `nudge.is_nudge` inlined (the import would be circular); if the
    two ever disagree, a row could count as owned for the fence but ride the wake path."""
    from personalclaw.triggers.models import Trigger

    for spec in ({}, {"message": ""}, {"message": "  "}, {"message": "go"}):
        t = Trigger(id="idle:x", name="x", kind="idle", spec=dict(spec))
        assert IP._is_nudge(t) == N.is_nudge(t), f"discriminators disagree on {spec!r}"


# ── the HTTP helpers that stayed in dashboard/handlers/autonudge.py ──


@pytest.mark.asyncio
async def test_resolve_stop_sentinel(tmp_path, monkeypatch):
    from personalclaw.dashboard.handlers import autonudge as _autonudge_mod

    monkeypatch.setattr(_autonudge_mod, "workspace_root", lambda: tmp_path)
    path = _autonudge_mod.resolve_stop_sentinel("chat:1/123", "")
    assert path == str(tmp_path / ".stop-chat_1_123")


def test_render_nudge_message():
    from personalclaw.dashboard.handlers.autonudge import render_nudge_message

    result = render_nudge_message("halt: create {{STOP_FILE}}", "/tmp/.stop-x")
    assert result == "halt: create /tmp/.stop-x"
    assert "{{STOP_FILE}}" not in result
    assert render_nudge_message("create {{STOP_FILE}}", None) == "create "

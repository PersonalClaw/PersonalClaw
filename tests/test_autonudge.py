"""Tests for AutoNudgeService — reactive idle timer, persistence, kill switch."""

import pytest

from personalclaw.autonudge import AutoNudgeService, NudgeLoop
from personalclaw.dashboard.handlers import autonudge as _autonudge_mod
from personalclaw.dashboard.handlers.autonudge import render_nudge_message


@pytest.fixture(autouse=True)
def _enable(monkeypatch):
    monkeypatch.setenv("PERSONALCLAW_AUTONUDGE", "1")


@pytest.fixture
def svc(tmp_path):
    return AutoNudgeService(base_dir=tmp_path)


@pytest.mark.asyncio
async def test_add_and_fire_on_idle(svc, monkeypatch):
    """Arming a timer and letting it elapse triggers the fire callback."""
    fired: list[NudgeLoop] = []

    async def on_fire(loop):
        fired.append(loop)
        return True

    svc._on_fire = on_fire
    # Patch asyncio.sleep inside the service's _timer to a no-op so the
    # test exercises the real fire path without waiting _MIN_IDLE_SECS.
    import personalclaw.autonudge as _an

    async def _nosleep(_secs):
        return None

    monkeypatch.setattr(_an.asyncio, "sleep", _nosleep)
    await svc.start()
    loop = await svc.add(session_name="chat-1-123", message="go", idle_secs=15)
    # The timer task was created on add(); await it to completion.
    await svc._timers[loop.id]
    assert len(fired) == 1
    assert fired[0].id == loop.id
    # cycle_count should have been bumped by _timer.
    assert svc._loops[loop.id].cycle_count == 1


@pytest.mark.asyncio
async def test_first_idle_secs_shortens_only_the_first_fire(svc, monkeypatch):
    """A loop armed with first_idle_secs sleeps the short delay on the first fire,
    then clears it so later fires wait the full idle_secs."""
    import personalclaw.autonudge as _an

    slept: list[int] = []

    async def _record_sleep(secs):
        slept.append(secs)

    async def on_fire(loop):
        return True

    monkeypatch.setattr(_an.asyncio, "sleep", _record_sleep)
    svc._on_fire = on_fire
    await svc.start()
    loop = await svc.add(session_name="code-abcd1234", message="go", idle_secs=120, first_idle_secs=15)
    # first_idle_secs is recorded on the loop and used for the FIRST sleep.
    assert loop.first_idle_secs == 15
    await svc._timers[loop.id]
    assert slept[-1] == 15
    assert svc._loops[loop.id].cycle_count == 1
    # cleared after the first fire — a re-armed timer now uses the full idle_secs.
    assert svc._loops[loop.id].first_idle_secs == 0
    svc._cancel_timer(loop.id)
    svc._arm_timer(svc._loops[loop.id])
    await svc._timers[loop.id]
    assert slept[-1] == 120


@pytest.mark.asyncio
async def test_first_idle_secs_clamped_to_idle_secs(svc):
    """first_idle_secs is never longer than idle_secs nor below the floor."""
    await svc.start()
    # larger than idle_secs → clamped down to idle_secs
    a = await svc.add(session_name="code-aaaaaaaa", message="go", idle_secs=30, first_idle_secs=999)
    assert a.first_idle_secs == 30
    # below the floor → raised to _MIN_IDLE_SECS
    b = await svc.add(session_name="code-bbbbbbbb", message="go", idle_secs=120, first_idle_secs=1)
    assert b.first_idle_secs == 15
    # 0 → disabled (unchanged original behavior)
    c = await svc.add(session_name="code-cccccccc", message="go", idle_secs=120)
    assert c.first_idle_secs == 0


@pytest.mark.asyncio
async def test_user_input_cancels_timer(svc):
    fired: list[NudgeLoop] = []

    async def on_fire(loop):
        fired.append(loop)

    svc._on_fire = on_fire
    await svc.start()
    loop = await svc.add(session_name="chat-1-123", message="go", idle_secs=15)
    assert loop.id in svc._timers
    svc.notify_user_input("chat-1-123")
    assert loop.id not in svc._timers


@pytest.mark.asyncio
async def test_notify_turn_complete_rearms(svc):
    await svc.start()
    loop = await svc.add(session_name="chat-1-123", message="go", idle_secs=15)
    svc._cancel_timer(loop.id)
    assert loop.id not in svc._timers
    svc.notify_turn_complete("chat-1-123")
    assert loop.id in svc._timers


@pytest.mark.asyncio
async def test_persistence_across_restart(tmp_path):
    svc1 = AutoNudgeService(base_dir=tmp_path)
    await svc1.start()
    loop = await svc1.add(session_name="chat-1-123", message="go", idle_secs=15, max_cycles=5)
    svc1.stop()

    # New instance reads the same file and restores loops.
    svc2 = AutoNudgeService(base_dir=tmp_path)
    await svc2.start()
    restored = svc2.get_by_session("chat-1-123")
    assert restored is not None
    assert restored.id == loop.id
    assert restored.message == "go"
    assert restored.max_cycles == 5
    assert loop.id in svc2._timers  # timer re-armed
    svc2.stop()


@pytest.mark.asyncio
async def test_max_cycles_deactivates(svc, monkeypatch):
    import personalclaw.autonudge as _an

    async def _nosleep(_secs):
        return None

    monkeypatch.setattr(_an.asyncio, "sleep", _nosleep)
    await svc.start()
    loop = await svc.add(session_name="chat-1-123", message="go", idle_secs=15, max_cycles=2)
    loop.cycle_count = 2  # simulate cap reached
    svc._save()
    svc._cancel_timer(loop.id)
    await svc._timer(loop)
    # _timer with cycle_count==max deactivates the loop (doesn't remove it).
    refreshed = svc._loops[loop.id]
    assert not refreshed.active


@pytest.mark.asyncio
async def test_stop_sentinel_removes_loop(svc, tmp_path, monkeypatch):
    import personalclaw.autonudge as _an

    async def _nosleep(_secs):
        return None

    monkeypatch.setattr(_an.asyncio, "sleep", _nosleep)
    await svc.start()
    sentinel = tmp_path / "STOP"
    loop = await svc.add(
        session_name="chat-1-123", message="go", idle_secs=15, stop_sentinel_path=str(sentinel)
    )
    sentinel.write_text("halt")
    svc._cancel_timer(loop.id)
    await svc._timer(loop)
    assert svc.get_by_session("chat-1-123") is None


@pytest.mark.asyncio
async def test_one_loop_per_session_replaces(svc):
    await svc.start()
    l1 = await svc.add(session_name="chat-1-123", message="first", idle_secs=15)
    l2 = await svc.add(session_name="chat-1-123", message="second", idle_secs=15)
    assert l1.id != l2.id
    # Only the second loop should remain.
    all_loops = svc.list_all()
    assert len(all_loops) == 1
    assert all_loops[0].message == "second"


@pytest.mark.asyncio
async def test_disabled_when_flag_off(tmp_path, monkeypatch):
    monkeypatch.setenv("PERSONALCLAW_AUTONUDGE", "0")
    svc = AutoNudgeService(base_dir=tmp_path)
    await svc.start()
    # Service is a no-op when flag is off — add/remove still work on the in-memory
    # dict but timers never arm. Verify via the enabled() helper.
    from personalclaw.autonudge import enabled

    assert not enabled()


@pytest.mark.asyncio
async def test_update_changes_message_and_idle(svc):
    await svc.start()
    loop = await svc.add(session_name="chat-1-123", message="old", idle_secs=30)
    updated = await svc.update(loop.id, message="new", idle_secs=60)
    assert updated is not None
    assert updated.message == "new"
    assert updated.idle_secs == 60


@pytest.mark.asyncio
async def test_idle_secs_clamped(svc):
    """Verify add() clamps idle_secs to [_MIN_IDLE_SECS, _MAX_IDLE_SECS]."""
    await svc.start()
    # Below min → clamped up to 15.
    loop_low = await svc.add(session_name="s1", message="m", idle_secs=5)
    assert loop_low.idle_secs == 15
    # Above max → clamped down to 86400.
    loop_high = await svc.add(session_name="s2", message="m", idle_secs=100_000)
    assert loop_high.idle_secs == 86400


@pytest.mark.asyncio
async def test_skip_when_delivery_returns_false(svc, monkeypatch):
    """When _on_fire returns False (e.g. session mid-turn), cycle_count stays put."""
    import personalclaw.autonudge as _an

    async def _nosleep(_secs):
        return None

    monkeypatch.setattr(_an.asyncio, "sleep", _nosleep)

    fired: list[NudgeLoop] = []

    async def on_fire_skip(loop):
        fired.append(loop)
        return False  # delivery skipped

    svc._on_fire = on_fire_skip
    await svc.start()
    loop = await svc.add(session_name="chat-1-123", message="go", idle_secs=15)
    await svc._timers[loop.id]
    # Callback ran, but delivery was skipped → cycle_count must not bump.
    assert len(fired) == 1
    assert svc._loops[loop.id].cycle_count == 0
    assert svc._loops[loop.id].last_fire_ts == 0.0


@pytest.mark.asyncio
async def test_fire_callback_exception_does_not_deactivate(svc, monkeypatch):
    """An exception in _on_fire is swallowed; cycle_count unchanged; loop stays active."""
    import personalclaw.autonudge as _an

    async def _nosleep(_secs):
        return None

    monkeypatch.setattr(_an.asyncio, "sleep", _nosleep)

    async def on_fire_raise(loop):
        raise RuntimeError("kaboom")

    svc._on_fire = on_fire_raise
    await svc.start()
    loop = await svc.add(session_name="chat-1-123", message="go", idle_secs=15)
    await svc._timers[loop.id]
    refreshed = svc._loops[loop.id]
    assert refreshed.cycle_count == 0  # exception treated as not-delivered
    assert refreshed.active is True  # loop still alive — timer can re-arm
    # Simulate next turn-complete → timer re-arms cleanly.
    svc.notify_turn_complete("chat-1-123")
    assert loop.id in svc._timers


@pytest.mark.asyncio
async def test_delivered_bumps_cycle_count(svc, monkeypatch):
    """When _on_fire returns True, cycle_count bumps and 'fired' event emits."""
    import personalclaw.autonudge as _an

    async def _nosleep(_secs):
        return None

    monkeypatch.setattr(_an.asyncio, "sleep", _nosleep)

    events: list[tuple[str, str]] = []
    svc.subscribe(lambda ev, lp: events.append((ev, lp.id if lp else "")))

    async def on_fire_ok(loop):
        return True

    svc._on_fire = on_fire_ok
    await svc.start()
    loop = await svc.add(session_name="chat-1-123", message="go", idle_secs=15)
    await svc._timers[loop.id]
    assert svc._loops[loop.id].cycle_count == 1
    assert svc._loops[loop.id].last_fire_ts > 0.0
    assert ("fired", loop.id) in events


@pytest.mark.asyncio
async def test_resolve_stop_sentinel(tmp_path, monkeypatch):
    """resolve_stop_sentinel computes per-session path from workspace."""
    monkeypatch.setattr(_autonudge_mod, "workspace_root", lambda: tmp_path)
    path = _autonudge_mod.resolve_stop_sentinel("chat:1/123", "")
    assert path == str(tmp_path / ".stop-chat_1_123")


def test_render_nudge_message():
    """render_nudge_message replaces {{STOP_FILE}} with the sentinel path."""
    result = render_nudge_message("halt: create {{STOP_FILE}}", "/tmp/.stop-x")
    assert result == "halt: create /tmp/.stop-x"
    assert "{{STOP_FILE}}" not in result

    # None sentinel produces empty string
    result2 = render_nudge_message("create {{STOP_FILE}}", None)
    assert result2 == "create "

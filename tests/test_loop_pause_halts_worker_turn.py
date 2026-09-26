"""Pausing, stopping or deleting a loop STOPS its worker — the cycle in flight included.

Measured 2026-09-25, a Goal loop (the loops-table path): the cockpit said "Paused", and 3.5 minutes
later the worker wrote `findings/cycle_2.json`. Deleting a loop mid-cycle left its worker session
behind — Home offered `dashboard_loop-baa9e5cc`, an "Untitled chat" holding 219k tokens — while a
second deleted loop's session was removed cleanly. Both have ONE cause:

A loop cycle is one task in the gateway's autonudge driver (`_run_turn_bounded`): a turn, then up to
`_MAX_CYCLE_REPROMPTS` re-prompt turns when no finding appeared. Pause/stop/delete disarmed the
NUDGE LOOP (so no NEXT cycle fired) and nothing else — the in-flight cycle ran on, re-prompting
itself, on a session a delete had already removed (re-saving its transcript). Whether a deleted loop
left an orphan depended only on whether a cycle happened to be in flight: the inconsistency.

The fix has two halves and each is pinned here: the manager STOPS the running turn
(`halt_worker_turns`, through the chat Stop's own `stop_turn`), and the cycle driver checks its loop
is still armed before every re-prompt.
"""

from __future__ import annotations

import asyncio
from collections import deque
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from personalclaw.loop import manager, store
from personalclaw.loop.loop import Loop, LoopStatus


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr("personalclaw.loop.files.config_dir", lambda: tmp_path)
    import personalclaw.tasks.native as nat

    monkeypatch.setattr(nat, "config_dir", lambda: tmp_path, raising=False)
    return tmp_path


class _Session:
    def __init__(self, key: str, *, running: bool) -> None:
        self.key = key
        self._running = running
        self._queue: deque[Any] = deque(["a queued turn"])

    @property
    def running(self) -> bool:
        return self._running


class _SessionManager:
    def __init__(self) -> None:
        self.stopped: list[str] = []

    async def stop_turn(self, key: str, *, force: bool = False, **_: Any) -> str:
        self.stopped.append(key)
        return "soft"


class _State:
    def __init__(self, *keys: str) -> None:
        self._sessions = {k: _Session(k, running=True) for k in keys}
        self.sessions = _SessionManager()


class _Nudge:
    def __init__(self, lid: str, session_name: str) -> None:
        self.id, self.session_name, self.active = lid, session_name, True


class _Svc:
    """The nudge service's PUBLIC surface only — no `_loops` dict, which is the real service's
    shape since WF2AUT-11 (its rows live in the trigger store)."""

    def __init__(self, *session_names: str) -> None:
        self._rows = {f"N{i}": _Nudge(f"N{i}", s) for i, s in enumerate(session_names)}

    def get_by_session(self, session_name: str) -> _Nudge | None:
        return next((n for n in self._rows.values() if n.session_name == session_name), None)

    def list_all(self) -> list[_Nudge]:
        return list(self._rows.values())

    async def update(self, loop_id: str, **kw: Any) -> None:
        for k, v in kw.items():
            setattr(self._rows[loop_id], k, v)

    async def remove(self, loop_id: str) -> None:
        self._rows.pop(loop_id, None)


def _running_loop() -> Loop:
    loop = store.create(Loop(id="", name="G", kind="goal", task="write the packing note"))
    return store.update_status(loop.id, LoopStatus.RUNNING)


def test_pause_stops_the_turn_in_flight_and_drops_the_queued_one() -> None:
    """At `origin/main` `stop_turn` is never called and the queued turn survives — so the cycle
    finishes, and a cancelled turn's finally-block would start the queued one."""
    loop = _running_loop()
    key = manager.session_key(loop.id)
    state, svc = _State(key), _Svc(key)
    asyncio.run(manager.pause(state, svc, loop.id))
    assert store.get(loop.id).status == LoopStatus.PAUSED.value
    assert state.sessions.stopped == [f"dashboard:{key}"], state.sessions.stopped
    assert not state._sessions[key]._queue
    assert svc.get_by_session(key).active is False


def test_pause_stops_parallel_task_workers_too() -> None:
    loop = _running_loop()
    main, task = manager.session_key(loop.id), manager.task_session_key(loop.id, "t1")
    state, svc = _State(main, task), _Svc(main, task)
    asyncio.run(manager.pause(state, svc, loop.id))
    assert sorted(state.sessions.stopped) == sorted([f"dashboard:{main}", f"dashboard:{task}"])


def test_stop_stops_the_turn_in_flight() -> None:
    loop = _running_loop()
    key = manager.session_key(loop.id)
    state, svc = _State(key), _Svc(key)
    asyncio.run(manager.stop(state, svc, loop.id))
    assert store.get(loop.id).status == LoopStatus.STOPPED.value
    assert state.sessions.stopped == [f"dashboard:{key}"]


def test_delete_teardown_stops_the_turn_before_the_session_is_reaped() -> None:
    loop = _running_loop()
    key = manager.session_key(loop.id)
    state, svc = _State(key), _Svc(key)
    asyncio.run(manager.teardown_for_delete(state, svc, loop.id))
    assert state.sessions.stopped == [f"dashboard:{key}"]


def test_teardown_removes_task_workers_through_the_public_surface() -> None:
    """At `origin/main` `_teardown` read `getattr(svc, "_loops", {})` — an empty dict on the real
    service — so a stopped parallel loop's task-worker nudge loops were never removed."""
    loop = _running_loop()
    main, task = manager.session_key(loop.id), manager.task_session_key(loop.id, "t1")
    svc = _Svc(main, task)
    asyncio.run(manager.teardown_worker(svc, loop.id))
    assert svc.list_all() == [], [n.session_name for n in svc.list_all()]


def test_an_idle_worker_is_not_stopped() -> None:
    """Between cycles there is no turn to stop; stopping one anyway would reset a healthy one."""
    loop = _running_loop()
    key = manager.session_key(loop.id)
    state, svc = _State(key), _Svc(key)
    state._sessions[key]._running = False
    asyncio.run(manager.pause(state, svc, loop.id))
    assert state.sessions.stopped == []


# ── the cycle driver's half ──


class _FakeNudgeService:
    """Stands in for `AutoNudgeService` so the gateway hands it the REAL `_fire` callback."""

    last: "_FakeNudgeService | None" = None

    def __init__(self, *, base_dir: Any, on_fire: Any) -> None:
        self.on_fire = on_fire
        self.rows: dict[str, _Nudge] = {}
        _FakeNudgeService.last = self

    async def start(self) -> None:
        return None

    def subscribe(self, _observer: Any) -> None:
        return None

    def get_by_session(self, name: str) -> _Nudge | None:
        return self.rows.get(name)

    async def remove(self, loop_id: str) -> None:
        return None

    def notify_turn_complete(self, *_: Any, **__: Any) -> None:
        return None


class _LoopSession:
    def __init__(self, key: str) -> None:
        self.key = key
        self._app = "loop"
        self.task: Any = None
        self._last_turn_errored = False
        self._suppress_autonudge_rearm = False
        self.appended: list[str] = []

    @property
    def running(self) -> bool:
        return self.task is not None and not self.task.done()

    def append(self, role: str, content: str, cls: str = "") -> None:
        self.appended.append(role)


def test_a_cycle_abandons_its_reprompts_once_its_loop_is_paused(tmp_path: Path) -> None:
    """The driver's own half. A cycle whose first turn produced no finding re-prompts up to
    `_MAX_CYCLE_REPROMPTS` times; if the loop was paused DURING that turn, it must not.

    At `origin/main` `run_chat` is called 1 + `_MAX_CYCLE_REPROMPTS` times here — the re-prompt loop
    checked only "did a finding appear?", never "is this loop still armed?".
    """
    from personalclaw.config.loader import AppConfig
    from personalclaw.gateway import GatewayOrchestrator
    from personalclaw.triggers.nudge import NudgeLoop  # noqa: F401 — the shape `_fire` is handed

    cfg = AppConfig()
    with patch.object(cfg, "load_credentials", return_value={}):
        orch = GatewayOrchestrator(cfg, no_dashboard=False, no_crons=True, no_open=True)
    key = "loop-abcd1234"
    session = _LoopSession(key)
    dstate = MagicMock()
    dstate._sessions = {key: session}
    dstate._background_tasks = set()
    dstate.sessions.get_provider.return_value = MagicMock(start_fresh_turn_session=AsyncMock())
    orch.dashboard_state = dstate
    orch.loop_watchdog = None
    calls: list[str] = []

    async def _fake_run_chat(_state: Any, _sess: Any, msg: str) -> None:
        calls.append(msg)
        # The user pauses while the cycle's first turn is running.
        _FakeNudgeService.last.rows[key].active = False

    nudge = MagicMock(
        id="N1", session_name=key, message="do one cycle", stop_sentinel_path="", cycle_count=0
    )

    async def _go() -> None:
        with (
            patch("personalclaw.gateway.autonudge_enabled", return_value=True),
            patch("personalclaw.gateway.AutoNudgeService", _FakeNudgeService),
            patch("personalclaw.dashboard.chat.run_chat", _fake_run_chat),
            patch("personalclaw.loop.files.get_findings", lambda _lid: []),
            patch("personalclaw.loop.files.loop_dir", lambda _lid: tmp_path),
        ):
            await orch._init_autonudge()
            svc = _FakeNudgeService.last
            svc.rows[key] = _Nudge("N1", key)
            assert await svc.on_fire(nudge) is True
            await asyncio.wait_for(session.task, timeout=10)

    asyncio.run(_go())
    assert len(calls) == 1, f"a paused loop's cycle ran {len(calls)} turns"


def test_an_armed_cycle_still_reprompts_when_no_finding_appeared(tmp_path: Path) -> None:
    """Positive control: the guard must not disable the re-prompt path it guards."""
    from personalclaw.config.loader import AppConfig
    from personalclaw.gateway import _MAX_CYCLE_REPROMPTS, GatewayOrchestrator

    cfg = AppConfig()
    with patch.object(cfg, "load_credentials", return_value={}):
        orch = GatewayOrchestrator(cfg, no_dashboard=False, no_crons=True, no_open=True)
    key = "loop-abcd5678"
    session = _LoopSession(key)
    dstate = MagicMock()
    dstate._sessions = {key: session}
    dstate._background_tasks = set()
    dstate.sessions.get_provider.return_value = MagicMock(start_fresh_turn_session=AsyncMock())
    orch.dashboard_state = dstate
    orch.loop_watchdog = None
    calls: list[str] = []

    async def _fake_run_chat(_state: Any, _sess: Any, msg: str) -> None:
        calls.append(msg)

    nudge = MagicMock(
        id="N1", session_name=key, message="do one cycle", stop_sentinel_path="", cycle_count=0
    )

    async def _go() -> None:
        with (
            patch("personalclaw.gateway.autonudge_enabled", return_value=True),
            patch("personalclaw.gateway.AutoNudgeService", _FakeNudgeService),
            patch("personalclaw.dashboard.chat.run_chat", _fake_run_chat),
            patch("personalclaw.loop.files.get_findings", lambda _lid: []),
            patch("personalclaw.loop.files.loop_dir", lambda _lid: tmp_path),
        ):
            await orch._init_autonudge()
            svc = _FakeNudgeService.last
            svc.rows[key] = _Nudge("N1", key)
            assert await svc.on_fire(nudge) is True
            await asyncio.wait_for(session.task, timeout=10)

    asyncio.run(_go())
    assert len(calls) == 1 + _MAX_CYCLE_REPROMPTS, calls

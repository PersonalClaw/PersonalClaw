"""An Attended loop asks before its workers act; an Unattended one runs on a grant it states.

Measured: an Attended code loop ran about 55 shell commands in one cycle (``sed -i`` on the
user's source, ``find /``, file edits) and never asked. Two things made every loop unattended
whatever its Mode said:

* the loop manager armed every worker, main and per-task, with a standing grant (``_trust``)
  whatever ``loop.attended`` was, so nothing asked at all;
* the chat runner classified every ``loop-…`` session as unattended by its KEY, so even a
  worker without the grant would have had each ask declined at once instead of put to a person.

A worker's Mode is its loop's, set each time the loop arms it, and a person answers an
Attended worker's calls through the same path a chat's go (the card, the bell, the channel).
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from personalclaw.dashboard.chat_runner import run_chat
from personalclaw.dashboard.state import DashboardState, _ChatSession
from personalclaw.history import ConversationLog
from personalclaw.hooks import ToolHookResult
from personalclaw.llm.base import EVENT_COMPLETE, EVENT_PERMISSION_REQUEST, LLMEvent
from personalclaw.loop import manager, store
from personalclaw.loop.loop import Loop


@pytest.fixture(autouse=True)
def _tmp_home(monkeypatch, tmp_path):
    monkeypatch.setattr("personalclaw.loop.files.config_dir", lambda: tmp_path)
    monkeypatch.setattr("personalclaw.tasks.hierarchy.config_dir", lambda: tmp_path)
    import personalclaw.tasks.native as nat

    monkeypatch.setattr(nat, "config_dir", lambda: tmp_path, raising=False)
    yield tmp_path
    manager._LOOP_GRANTS.clear()


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class _State:
    """The slice of DashboardState the loop manager arms workers through."""

    def __init__(self):
        self._sessions: dict[str, _ChatSession] = {}
        self.sessions = MagicMock()

    def get_or_create_session(self, *, name, agent, model, workspace_dir, app, project_id=""):
        s = self._sessions.get(name)
        if s is None:
            s = _ChatSession(name, agent=agent, workspace_dir=workspace_dir, model=model)
            s._app = app
            self._sessions[name] = s
        return s

    def push_sessions_update(self):
        pass


class _Svc:
    def __init__(self):
        self._loops: dict[str, SimpleNamespace] = {}
        self._n = 0

    async def add(self, *, session_name, message, **_kw):
        for lid in [k for k, lp in self._loops.items() if lp.session_name == session_name]:
            del self._loops[lid]
        self._n += 1
        lp = SimpleNamespace(
            id=f"N{self._n}", session_name=session_name, message=message, active=True
        )
        self._loops[lp.id] = lp
        return lp

    def get_by_session(self, session_name):
        return next((lp for lp in self._loops.values() if lp.session_name == session_name), None)

    def list_all(self):
        return list(self._loops.values())

    async def update(self, loop_id, **kw):
        for k, v in kw.items():
            setattr(self._loops[loop_id], k, v)

    async def remove(self, loop_id):
        self._loops.pop(loop_id, None)


def _code_loop(*, attended: bool, provider: str = "") -> Loop:
    return store.create(
        Loop(
            id="",
            name="C",
            kind="code",
            task="fix the double-escaped digest titles",
            attended=attended,
            provider=provider,
            plan=[{"stage": "implementation", "title": "Impl"}],
            kind_config={"entry_stage": "implementation"},
        )
    )


def _task():
    return SimpleNamespace(
        id="t-0a1b2c3d", title="digest.py", description="", action_plan=[], exit_criteria=[]
    )


# ── who answers a worker's asks, set when the loop arms it ────────────────────


def test_an_attended_loops_workers_hold_no_standing_grant():
    loop = _code_loop(attended=True, provider="acp:some-cli")
    state, svc = _State(), _Svc()
    _run(manager.start(state, svc, loop.id))
    skey = _run(manager.spawn_task_worker(state, svc, store.get(loop.id), _task(), "/tmp/wt"))

    for key in (manager.session_key(loop.id), skey):
        worker = state._sessions[key]
        assert worker._trust is False, f"{key} runs every tool without asking"
        assert worker._unattended is False, f"{key} would have its asks declined unasked"
        assert worker.acp_mode == "", f"{key} hands its agent CLI an ask-nobody mode"


def test_an_unattended_loops_workers_run_on_their_grant():
    loop = _code_loop(attended=False, provider="acp:some-cli")
    state, svc = _State(), _Svc()
    _run(manager.start(state, svc, loop.id))
    skey = _run(manager.spawn_task_worker(state, svc, store.get(loop.id), _task(), "/tmp/wt"))

    for key in (manager.session_key(loop.id), skey):
        worker = state._sessions[key]
        assert worker._trust is True
        assert worker._unattended is True
        assert worker.acp_mode == "bypassPermissions"


def test_this_loop_reaches_every_worker_and_ends_with_the_run():
    loop = _code_loop(attended=True)
    state, svc = _State(), _Svc()
    _run(manager.start(state, svc, loop.id))
    main = state._sessions[manager.session_key(loop.id)]

    manager.grant_every_worker(state, loop.id)
    skey = _run(manager.spawn_task_worker(state, svc, store.get(loop.id), _task(), "/tmp/wt"))
    task_worker = state._sessions[skey]
    assert main._trust is True and task_worker._trust is True

    _run(manager.pause(state, svc, loop.id))
    _run(manager.start(state, svc, loop.id))

    assert main._trust is False and task_worker._trust is False, "a resumed run kept the grant"
    assert svc.get_by_session(skey).active is True, "the resume left the task worker stood down"


def test_the_card_that_says_this_loop_reaches_the_loops_other_workers(tmp_path):
    from personalclaw.approval_answer import YOU

    state, _client = _chat_state(tmp_path)
    stage = state.get_or_create_session(name="loop-0a1b2c3d", app="loop")
    asking = state.get_or_create_session(name="loop-0a1b2c3d-t-0a1b2c3d", app="loop")
    another_loop = state.get_or_create_session(name="loop-99999999", app="loop")
    asking._approval_futures["req-1"] = asyncio.get_event_loop().create_future()

    state.decide_session_approval(asking, "req-1", "trust", by=YOU)

    assert asking._trust is True and stage._trust is True
    assert another_loop._trust is False
    assert manager._LOOP_GRANTS == {"0a1b2c3d"}


# ── the worker's turn asks a person ───────────────────────────────────────────


async def _events(items):
    for item in items:
        yield item


def _chat_state(tmp_path):
    sessions = MagicMock(count=0)
    sessions.get_pid = MagicMock(return_value=None)
    client = AsyncMock()
    client.provider_id = "native"
    client.context_usage_pct = MagicMock(return_value=None)
    del client.cancel_session
    sessions.get_or_create = AsyncMock(return_value=(client, True, False))
    sessions.record_failure = AsyncMock()
    sessions.check_context_usage = MagicMock()
    state = DashboardState(
        sessions=sessions, start_time=0.0, conversation_log=ConversationLog(base_dir=tmp_path)
    )
    builder = MagicMock()
    builder.hooks.on_tool_call.return_value = ToolHookResult.allow()
    builder.build_message.return_value = ("hello", None)
    state.context_builder = builder
    hooks = MagicMock()
    hooks.fire_for_ids = AsyncMock(return_value=[])
    state._hook_store = hooks
    state.broadcast_ws = MagicMock()
    state.push_sessions_update = MagicMock()
    client.stream = MagicMock(
        side_effect=lambda *a, **kw: _events(
            [
                LLMEvent(
                    kind=EVENT_PERMISSION_REQUEST,
                    request_id="req-1",
                    title="bash",
                    tool_input='{"command": "sed -i s/a/b/ src/app.py"}',
                    risk_level="destructive",
                ),
                LLMEvent(kind=EVENT_COMPLETE, stop_reason="end_turn"),
            ]
        )
    )
    return state, client


def _worker(key: str, *, unattended: bool) -> _ChatSession:
    s = _ChatSession(key)
    s._app = "loop"
    s._unattended = unattended
    return s


@pytest.mark.asyncio
async def test_an_attended_loop_workers_call_is_put_to_a_person(tmp_path):
    state, client = _chat_state(tmp_path)
    worker = _worker("loop-0a1b2c3d", unattended=False)
    seen: dict = {}

    async def _answer():
        for _ in range(500):
            fut = worker._approval_futures.get("req-1")
            if fut is not None and not fut.done():
                seen.update(state._pending_approvals)
                fut.set_result("approved")
                return True
            await asyncio.sleep(0.01)
        return False

    answered = asyncio.get_event_loop().create_task(_answer())
    with patch("personalclaw.dashboard.chat_runner.sel", MagicMock()):
        await asyncio.wait_for(run_chat(state, worker, "run the next cycle"), timeout=20)

    assert await answered, "the call was never put to anybody"
    assert state.sessions.get_or_create.call_args.kwargs["unattended"] is False
    assert [e["session"] for e in seen.values()] == ["loop-0a1b2c3d"]
    client.approve_tool.assert_awaited_with("req-1")
    assert not any("auto-denied" in str(m.get("content", "")) for m in worker.messages)


@pytest.mark.asyncio
async def test_an_unattended_loop_worker_still_declines_what_nothing_granted(tmp_path):
    state, client = _chat_state(tmp_path)
    worker = _worker("loop-0a1b2c3d", unattended=True)

    with patch("personalclaw.dashboard.chat_runner.sel", MagicMock()):
        await asyncio.wait_for(run_chat(state, worker, "run the next cycle"), timeout=20)

    client.reject_tool.assert_awaited_with("req-1")
    assert state.sessions.get_or_create.call_args.kwargs["unattended"] is True
    assert any("auto-denied" in str(m.get("content", "")) for m in worker.messages)


def test_the_inbox_row_names_the_loop_that_is_asking():
    from personalclaw.dashboard.approval_state import _approval_row_body

    loop = store.create(Loop(id="", name="Digest titles", kind="code", task="fix the titles"))
    ask = {"agent": "personalclaw-coder", "tool": "bash", "risk": "destructive"}

    for key in (f"loop-{loop.id}", f"loop-{loop.id}-t-0a1b2c3d"):
        body = _approval_row_body({**ask, "session": key})
        assert body.startswith(
            "personalclaw-coder in the loop “Digest titles” is waiting for your decision on bash"
        ), body
    # A chat's own ask keeps its words.
    assert _approval_row_body({**ask, "session": "chat-7"}).startswith(
        "personalclaw-coder in a chat is waiting"
    )


# ── waiting on the owner is not a wedged worker ───────────────────────────────


class _WatchedState:
    """What the loop watchdog reads of the dashboard: sessions, streams, notices, and whether a
    worker's call is waiting on its owner."""

    def __init__(self, waiting: set[str] | None = None):
        from personalclaw.dashboard.sse import SseRegistry

        self._sessions: dict = {}
        self._sse = SseRegistry()
        self._waiting = waiting or set()

    def loop_sse(self):
        return self._sse

    def push_refresh(self, *kinds):
        pass

    def notify(self, *args, **kwargs):
        pass

    def waiting_on_owner(self, key: str) -> bool:
        return key in self._waiting


def _running_code_loop(*, attended: bool, **over) -> Loop:
    from personalclaw.loop.loop import LoopStatus

    loop = _code_loop(attended=attended)
    store.update_status(loop.id, LoopStatus.RUNNING, **over)
    return store.get(loop.id)


def test_an_attended_loop_has_no_standing_grant_to_expire():
    from personalclaw.loop import files as loop_files
    from personalclaw.loop import watchdog as W

    loop = _running_code_loop(attended=True, started_at=1.0)
    wd = W.LoopWatchdog(_WatchedState(), _Svc())

    _run(wd._poll_once())

    assert store.get(loop.id).status == "running"
    assert loop_files.pending_question(loop.id) is None


def test_the_end_of_an_unattended_runs_trust_window_reaches_every_worker():
    from personalclaw.loop import watchdog as W
    from personalclaw.loop.loop import LoopStatus

    loop = _code_loop(attended=False, provider="acp:some-cli")
    state, svc = _State(), _Svc()
    _run(manager.start(state, svc, loop.id))
    skey = _run(manager.spawn_task_worker(state, svc, store.get(loop.id), _task(), "/tmp/wt"))
    store.update_status(loop.id, LoopStatus.RUNNING, started_at=1.0)  # long past its window
    watched = _WatchedState()
    watched._sessions = state._sessions

    _run(W.LoopWatchdog(watched, svc)._poll_once())

    assert store.get(loop.id).status == "needs_input"
    for key in (manager.session_key(loop.id), skey):
        worker = state._sessions[key]
        assert worker._trust is False, f"{key} still runs every call unasked"
        assert worker.acp_mode == "", f"{key} still tells its agent CLI to skip its asks"


def test_a_worker_waiting_on_its_owners_answer_is_not_failed_as_wedged():
    from personalclaw.loop import watchdog as W

    loop = _running_code_loop(attended=True, started_at=1.0)
    key = manager.session_key(loop.id)
    state = _WatchedState(waiting={key})
    state._sessions[key] = SimpleNamespace(key=key, running=True, messages=[])
    wd = W.LoopWatchdog(state, _Svc())
    _run(wd._poll_once())  # first sight seeds the liveness clock
    wd._last_activity[loop.id] = 1.0  # the turn has been running for a very long time
    wd._running_since[loop.id] = 1.0

    _run(wd._poll_once())

    assert store.get(loop.id).status == "running", store.get(loop.id).error_message


def test_the_same_worker_not_waiting_on_anyone_is_still_failed_as_wedged():
    from personalclaw.loop import watchdog as W

    loop = _running_code_loop(attended=True, started_at=1.0)
    key = manager.session_key(loop.id)
    state = _WatchedState()
    state._sessions[key] = SimpleNamespace(key=key, running=True, messages=[])
    wd = W.LoopWatchdog(state, _Svc())
    _run(wd._poll_once())
    wd._last_activity[loop.id] = 1.0
    wd._running_since[loop.id] = 1.0

    _run(wd._poll_once())

    assert store.get(loop.id).status == "failed"
    assert "wedged" in (store.get(loop.id).error_message or "")


# ── a turn's bound does not count the wait on its owner ───────────────────────


@pytest.mark.asyncio
async def test_a_turns_bound_stops_while_it_waits_on_its_owner(monkeypatch):
    from personalclaw import cancellation

    monkeypatch.setattr(cancellation, "PAUSED_CLOCK_TICK_SECS", 0.02)
    waiting = {"now": True}

    async def _turn():
        await asyncio.sleep(0.4)  # most of it spent waiting on the owner
        waiting["now"] = False
        await asyncio.sleep(0.05)
        return "answered"

    got = await cancellation.wait_for_unpaused(
        _turn(), 0.2, paused=lambda: waiting["now"], what="test turn"
    )
    assert got == "answered"


@pytest.mark.asyncio
async def test_a_turn_working_past_its_bound_is_still_stopped(monkeypatch):
    from personalclaw import cancellation

    monkeypatch.setattr(cancellation, "PAUSED_CLOCK_TICK_SECS", 0.02)
    stopped = asyncio.Event()

    async def _turn():
        try:
            await asyncio.sleep(5)
        except asyncio.CancelledError:
            stopped.set()
            raise

    with pytest.raises(asyncio.TimeoutError):
        await cancellation.wait_for_unpaused(_turn(), 0.1, paused=lambda: False, what="test")
    assert stopped.is_set()

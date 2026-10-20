"""Stopping a chat's turn ends what that turn started: its batch run, its subagents, and what they
were waiting on. Nothing an earlier turn or another chat started.

Measured on a running gateway: a chat turn handed two tasks to ``subagent_run``, which ran them as
one batch workflow run, each step's subagent waiting on its owner's approval. She pressed Stop. The
turn ended, yet the batch went on running and spending, and its steps' approvals stayed answerable:
an Allow would have started agents for a turn she had stopped. A loop's Stop already ended what the
loop started; a turn's Stop reached only the subagents its session had spawned, and said nothing of
why.

The link is the one the loop's rule uses: a batch run records the session that started it
(``origin.session_key``, from the call's session header), a background subagent its parent session.
What that session started since its turn began is the turn's. So the Stop ends each child as its
turn's Stop: the run is cancelled saying so, its step's subagent is stopped, and the approval it was
waiting on ends naming why, on every surface.

A batch now asks once before any of it starts (`workflows.batch_start`), so a Stop while it asks
ends that ask, saying why, and the batch never starts. Under an operator ceiling that has every
start ask, a started batch's steps still each ask, and the Stop ends those too.

The first tests drive the REAL chain: a turn on the real chat runner whose scripted model calls
``subagent_run`` with two tasks through the real tool, which hands the batch to the real workflow
routes with the turn's session header, under a real supervisor. Its one ask goes through the start's
relay to the real approval registry, and its steps are a real ``RunController`` over a real
``SubagentManager`` whose spawns ask that registry too. Then the real Stop route.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any
from unittest.mock import MagicMock
from urllib.parse import quote

import pytest
from aiohttp.test_utils import TestClient, TestServer
from test_dashboard_approval import _make_session
from test_ended_owner_ends_its_approvals import world  # noqa: F401 - a fixture
from test_ended_owner_ends_its_approvals import (  # the shared approval harness
    _app,
    _open_rows,
    _real_manager,
    _resolved,
    _until,
)

from personalclaw import mcp_core, mcp_subagents
from personalclaw.config.loader import AppConfig
from personalclaw.dashboard.chat import api_chat_session_stop, run_chat
from personalclaw.dashboard.state import DashboardState
from personalclaw.llm.base import (
    EVENT_COMPLETE,
    EVENT_PERMISSION_REQUEST,
    EVENT_TEXT_CHUNK,
    LLMEvent,
)
from personalclaw.session import SessionManager, _Session
from personalclaw.workflows import batch_start
from personalclaw.workflows import controller as controller_mod
from personalclaw.workflows import store
from personalclaw.workflows.controller import EngineServices
from personalclaw.workflows.handlers import register_workflow_routes
from personalclaw.workflows.models import (
    InstanceState,
    OriginKind,
    RunOrigin,
    RunStatus,
    WorkflowRun,
)
from personalclaw.workflows.watchdog import WorkflowWatchdog

CHAT = "chat-a"
KEY = f"dashboard:{CHAT}"

#: What the turn's Stop says it ended, on the run and on each of its steps.
RUN_ENDING = "Stopped because its chat turn was stopped."
AGENT_ENDING = "Cancelled because its chat turn was stopped"

#: Two read-only checks, each a contract the batch compiler accepts.
TASKS = [
    {
        "task": "check the release notes for links that no longer resolve",
        "title": "Check the links",
        "objective": "find every link in the release notes that does not resolve",
        "output_format": "a markdown list of the links that do not resolve",
        "boundary": "only read the release notes, change nothing",
    },
    {
        "task": "check the release notes for spelling mistakes",
        "title": "Check the spelling",
        "objective": "find every misspelled word in the release notes",
        "output_format": "a markdown list of the misspelled words",
        "boundary": "only read the release notes, change nothing",
    },
]


class _ScriptedTurn:
    """The turn's model, scripted: it says what it will do, calls ``subagent_run`` with two tasks
    (the real tool, run off the event loop as a tool call is), which asks its owner once to start
    them, then asks to run a command and waits on that ask. A Stop ends the wait with a refusal,
    and the turn ends."""

    def __init__(self) -> None:
        #: The batch, by its definition's name, as the tool's answer names it.
        self.batch: str = ""

    def __call__(self, *_a: Any, **_kw: Any):
        return self._stream()

    async def _stream(self):
        yield LLMEvent(kind=EVENT_TEXT_CHUNK, text="I'll run both checks as one batch.")
        token = mcp_core.set_current_session_key(KEY)
        try:
            out = await asyncio.to_thread(
                mcp_subagents._call_tool, "subagent_run", {"tasks": TASKS}
            )
        finally:
            mcp_core.reset_current_session_key(token)
        assert out.startswith("{"), f"the batch was not asked for: {out}"
        self.batch = json.loads(out.splitlines()[0])["batch"]
        yield LLMEvent(
            kind=EVENT_PERMISSION_REQUEST,
            title="bash",
            tool_kind="execute",
            request_id="req-1",
            tool_call_id="tc-req-1",
            tool_input=json.dumps({"command": "ls notes"}),
        )
        yield LLMEvent(kind=EVENT_TEXT_CHUNK, text="Stopped.")
        yield LLMEvent(kind=EVENT_COMPLETE, stop_reason="end_turn")


@pytest.fixture
def gateway(world, monkeypatch):  # noqa: F811 - the imported fixture
    """The world's dashboard state, plus what the gateway gives it: a subagent manager whose
    spawns ask the real registry, a workflow supervisor, and the routes the tool calls."""
    from personalclaw.workflows.native_defs import register_native_provider

    monkeypatch.setattr(controller_mod, "TICK_WAKE_SECS", 0.02)
    register_native_provider()  # where the batch's definition is saved, as the gateway registers
    # She has a model bound for the batch's steps: the run's preflight asks, and no step here
    # ever reaches a model (each waits on its start's approval).
    monkeypatch.setattr(
        "personalclaw.providers.provider_bridge.can_resolve_use_case", lambda _use_case: True
    )
    manager = _real_manager(world.state)
    world.state.workflows = WorkflowWatchdog(
        world.state, services=EngineServices(subagents=manager, cwd="")
    )
    world.manager = manager
    # The scripted turn's runtime measures no context, so a turn's end records none.
    world.client.context_usage_pct = MagicMock(return_value=None)
    app = _app(world.state)
    register_workflow_routes(app)
    app.router.add_post("/api/chat/sessions/{session}/stop", api_chat_session_stop)
    world.app = app
    return world


def _route_tool_calls_to(monkeypatch, http: TestClient, loop: asyncio.AbstractEventLoop) -> None:
    """The tool's gateway calls, answered by the real routes: each carries the session of the
    call that made it, as ``mcp_core._internal_headers`` sends it."""

    def _post(path: str, body: dict) -> dict:
        headers = {"X-Session-Key": mcp_core._resolve_session_key()}

        async def _send() -> dict:
            resp = await http.post(path, json=body, headers=headers)
            text = await resp.text()
            try:
                return json.loads(text)
            except ValueError:
                raise AssertionError(f"{path} answered {resp.status}: {text[:2000]}") from None

        return asyncio.run_coroutine_threadsafe(_send(), loop).result(timeout=30)

    monkeypatch.setattr(mcp_subagents, "_post", _post)


class _Runtime:
    """The runtime the session manager holds for the chat: it acknowledges a cancel."""

    keeps_cancelled_turns = True

    def __init__(self) -> None:
        self.reached: list[int] = []

    async def cancel(self, *, wait_ack_timeout: float = 0.0) -> str:
        return "acked"

    def note_subagents_stopped(self, count: int) -> None:
        self.reached.append(count)


def _chat(w):
    session = _make_session(CHAT)
    session.agent = "researcher"
    w.state._sessions[session.key] = session
    return session


def _hold_the_runtime(w) -> _Session:
    """The session manager holds a runtime for the chat while its turn runs, as the gateway's
    does once a turn has started one; the Stop reaches what the turn started through it."""
    held = _Session(provider=_Runtime())  # type: ignore[arg-type]
    w.state.sessions._sessions[KEY] = held
    return held


def _waiting_spawns(w) -> list[str]:
    return [a for a in w.state._pending_approvals if a.startswith("spawn:")]


async def _start_the_turn(w, model: _ScriptedTurn):
    """The turn, parked on its own ask once the batch it handed `subagent_run` asks to start."""
    session = _chat(w)
    w.client.stream = model
    turn = asyncio.create_task(run_chat(w.state, session, "check the release notes"))
    session.task = turn
    await _until(lambda: "req-1" in session._approval_futures, "the turn never asked", tries=4000)
    await _until(
        lambda: f"batch:{model.batch}" in w.state._pending_approvals,
        "the batch never asked to start",
        tries=4000,
    )
    return turn


@pytest.mark.asyncio
async def test_stopping_a_turn_ends_the_batch_it_is_still_asking_to_start(gateway, monkeypatch):
    w = gateway
    model = _ScriptedTurn()
    async with TestClient(TestServer(w.app)) as http:
        _route_tool_calls_to(monkeypatch, http, asyncio.get_running_loop())
        turn = await _start_the_turn(w, model)
        ask = f"batch:{model.batch}"
        # Premises: the batch asks once, in the turn's chat and on the Inbox, and nothing of it
        # exists yet, so a green cannot be a batch that never reached the measured state.
        assert w.state._pending_approvals[ask]["session"] == CHAT
        assert _open_rows(w, ask), "the batch's ask never reached the Inbox"
        assert _waiting_spawns(w) == []
        assert store.list_runs(workflow_name=model.batch, limit=1)[0] == []

        _hold_the_runtime(w)
        resp = await http.post(f"/api/chat/sessions/{CHAT}/stop")
        assert resp.status == 200, await resp.text()
        await asyncio.wait_for(turn, timeout=10)
        await _until(lambda: ask not in w.state._pending_approvals, "the batch still asks")

    (frame,) = _resolved(w, ask)
    assert frame["outcome"] == "cancelled", frame
    assert frame["ended"] == "its chat turn was stopped", frame
    assert not _open_rows(w, ask), "the Inbox still offers the ask"
    # It never starts, its card says why, and no gateway asks it again.
    assert store.list_runs(workflow_name=model.batch, limit=1)[0] == []
    state = batch_start.state_of(model.batch)
    assert state["status"] == batch_start.NOT_STARTED, state
    assert "its chat turn was stopped" in state["error"], state
    await w.state.workflows.stop()


@pytest.mark.asyncio
async def test_stopping_a_turn_ends_the_batch_it_started(gateway, monkeypatch):
    """Under an operator ceiling that has every start ask, the batch's Allow starts its run and each
    step then asks to start: the Stop ends the run, its steps and their asks."""
    w = gateway
    model = _ScriptedTurn()
    monkeypatch.setattr(
        "personalclaw.guardrails.policy.ceiling_permits_approval", lambda _level: False
    )
    async with TestClient(TestServer(w.app)) as http:
        _route_tool_calls_to(monkeypatch, http, asyncio.get_running_loop())
        turn = await _start_the_turn(w, model)
        resp = await http.post(f"/api/approvals/{quote(f'batch:{model.batch}')}/approve")
        assert resp.status == 200, await resp.text()
        await _until(
            lambda: len(_waiting_spawns(w)) == 2, "the batch's steps never asked", tries=4000
        )
        spawns = _waiting_spawns(w)
        (run,), _total = store.list_runs(workflow_name=model.batch, limit=1)
        # Premises: the batch is the turn's, started from its session, and its steps' asks are
        # on the Inbox, so a green cannot be a run that never reached the measured state.
        assert run.origin.session_key == KEY, run
        assert run.status is RunStatus.RUNNING, run.status
        for approval_id in spawns:
            assert _open_rows(w, approval_id), "a step's ask never reached the Inbox"

        _hold_the_runtime(w)
        resp = await http.post(f"/api/chat/sessions/{CHAT}/stop")
        assert resp.status == 200, await resp.text()
        await asyncio.wait_for(turn, timeout=10)
        await _until(
            lambda: store.get(run.id).status is RunStatus.CANCELLED,
            "the batch the stopped turn started is still going",
            tries=4000,
        )

    run = store.get(run.id)
    assert run.error_message == RUN_ENDING, run.error_message
    states = {inst.state for inst in store.read_state(run.id).values()}
    assert states == {InstanceState.CANCELLED}, states
    for approval_id in spawns:
        info = w.manager.get(approval_id.removeprefix("spawn:"))
        assert info.cancelled and info.done, vars(info)
        assert info.error == AGENT_ENDING, info.error
        assert approval_id not in w.state._pending_approvals, "Home and To triage still list it"
        (frame,) = _resolved(w, approval_id)
        assert frame["outcome"] == "cancelled", frame
        assert frame["ended"] == (
            "the workflow run that asked for it was cancelled because its chat turn was stopped"
        ), frame
        assert not _open_rows(w, approval_id), "the Inbox still offers the ask"
    await w.state.workflows.stop()


# ── the same rule, directly on the turn's Stop ────────────────────────────────────────────────


def _batch(session_key: str, *, created_at: str = "") -> WorkflowRun:
    run = store.create(
        WorkflowRun(
            id="",
            workflow_name="subagent-batch-1",
            status=RunStatus.RUNNING,
            origin=RunOrigin(kind=OriginKind.API, session_key=session_key),
            created_at=created_at,
        )
    )
    store.save(run)
    return run


def _earlier() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 120))


async def _turn_running(w):
    """A turn of the chat that is still running: parked on its own ask, as one is while it works."""
    session = _chat(w)
    events = [
        LLMEvent(
            kind=EVENT_PERMISSION_REQUEST,
            title="bash",
            tool_kind="execute",
            request_id="req-1",
            tool_call_id="tc-req-1",
            tool_input=json.dumps({"command": "ls notes"}),
        ),
        LLMEvent(kind=EVENT_COMPLETE, stop_reason="end_turn"),
    ]

    async def _stream(*_a, **_kw):
        for event in events:
            yield event

    w.client.stream = _stream
    turn = asyncio.create_task(run_chat(w.state, session, "check the release notes"))
    session.task = turn
    await _until(lambda: "req-1" in session._approval_futures, "the turn never asked")
    return turn


def _spawn_waiting(w, parent: str, *, started: float = 0.0):
    info = w.manager.spawn("check the dates", parent_session_key=parent)
    if started:
        info.started = started
    return info


@pytest.mark.asyncio
async def test_a_subagent_the_turn_started_ends_saying_its_turn_was_stopped(gateway):
    w = gateway
    w.state.workflows = None
    turn = await _turn_running(w)
    info = _spawn_waiting(w, KEY)
    await _until(lambda: f"spawn:{info.id}" in w.state._pending_approvals, "never asked")

    _hold_the_runtime(w)
    await w.state.sessions.stop_turn(KEY)
    await asyncio.wait_for(turn, timeout=5)

    assert info.cancelled and info.done
    assert info.error == AGENT_ENDING, info.error


@pytest.mark.asyncio
async def test_what_an_earlier_turn_started_goes_on(gateway):
    """The positive control for "the turn": a batch and a subagent the chat started BEFORE this
    turn began are an earlier turn's, which ended without being stopped. Its Stop leaves them be."""
    w = gateway
    w.state.workflows = None
    earlier_run = _batch(KEY, created_at=_earlier())
    earlier_agent = _spawn_waiting(w, KEY, started=time.time() - 120)
    turn = await _turn_running(w)
    this_run = _batch(KEY)

    _hold_the_runtime(w)
    await w.state.sessions.stop_turn(KEY)
    await asyncio.wait_for(turn, timeout=5)

    assert store.cancel_requested(this_run.id), "the stopped turn's own batch went on"
    assert not store.cancel_requested(earlier_run.id), "an earlier turn's batch was stopped"
    assert not earlier_agent.cancelled and not earlier_agent.done, "an earlier turn's subagent died"


@pytest.mark.asyncio
async def test_what_another_chat_started_is_left_alone(gateway):
    w = gateway
    w.state.workflows = None
    other_run = _batch("dashboard:chat-b")
    other_agent = _spawn_waiting(w, "dashboard:chat-b")
    turn = await _turn_running(w)

    _hold_the_runtime(w)
    await w.state.sessions.stop_turn(KEY)
    await asyncio.wait_for(turn, timeout=5)

    assert not store.cancel_requested(other_run.id)
    assert not other_agent.cancelled and not other_agent.done


@pytest.mark.asyncio
async def test_a_batch_a_subagent_of_the_turn_started_ends_with_it(gateway):
    """A background subagent the turn spawned may start a batch of its own, from its own session
    (``subagent:<id>``). The Stop that ends the subagent ends that batch too."""
    w = gateway
    w.state.workflows = None
    turn = await _turn_running(w)
    info = _spawn_waiting(w, KEY)
    its_batch = _batch(f"subagent:{info.id}")

    _hold_the_runtime(w)
    await w.state.sessions.stop_turn(KEY)
    await asyncio.wait_for(turn, timeout=5)

    assert info.cancelled and info.done
    assert store.cancel_requested(its_batch.id), "the batch the turn's subagent started went on"
    assert store.cancel_reason(its_batch.id) == "its chat turn was stopped"


@pytest.mark.asyncio
async def test_the_dashboard_state_registers_what_a_turn_stop_ends(monkeypatch):
    """The wiring, not just the function: the Stop reaches what the turn started only through the
    stopper the dashboard state registers with the session manager."""
    from personalclaw import started_work

    reached: list[tuple[Any, str]] = []

    async def _end_turn(state: Any, session_key: str) -> int:
        reached.append((state, session_key))
        return 3

    monkeypatch.setattr(started_work, "end_turn", _end_turn)
    manager = SessionManager(AppConfig())
    assert manager._stop_children is None
    state = DashboardState(sessions=manager, start_time=0.0)
    assert manager._stop_children is not None
    assert await manager._stop_children(KEY) == 3
    assert reached == [(state, KEY)]


@pytest.mark.asyncio
async def test_a_stop_before_the_prompt_went_out_ends_nothing(gateway):
    """A turn the chat runner has not yet registered has started nothing, and what the chat's
    earlier turns started is theirs: a Stop that early ends none of it."""
    w = gateway
    w.state.workflows = None
    earlier_run = _batch(KEY, created_at=_earlier())
    earlier_agent = _spawn_waiting(w, KEY, started=time.time() - 120)

    _hold_the_runtime(w)
    await w.state.sessions.stop_turn(KEY)

    assert not store.cancel_requested(earlier_run.id)
    assert not earlier_agent.cancelled and not earlier_agent.done


@pytest.fixture
def loop_home(tmp_path, monkeypatch):
    monkeypatch.setattr("personalclaw.loop.files.config_dir", lambda: tmp_path)
    return tmp_path


@pytest.mark.asyncio
async def test_a_paused_loops_cycle_ends_what_it_started_saying_the_loop_was_paused(
    gateway, loop_home
):
    """A pause stops a loop's cycle in flight (``manager.halt_worker_turns``, through the turn's
    Stop), and what that cycle's turn started ends saying the loop was paused, not that a chat
    turn was stopped. What an earlier cycle started goes on: the loop has not ended."""
    from personalclaw.loop import store as loop_store
    from personalclaw.loop.loop import Loop, LoopStatus
    from personalclaw.resilience.active_jobs import get_tracker

    w = gateway
    w.state.workflows = None
    loop = loop_store.create(Loop(id="", name="Release notes", kind="goal", task="verify"))
    loop_store.update_status(loop.id, LoopStatus.PAUSED)
    worker = f"loop-{loop.id}"
    earlier_run = _batch(f"dashboard:{worker}", created_at=_earlier())
    held = _Session(provider=_Runtime())  # type: ignore[arg-type]
    w.state.sessions._sessions[f"dashboard:{worker}"] = held
    get_tracker().register(worker, now=time.time())
    try:
        this_run = _batch(f"dashboard:{worker}")
        await w.state.sessions.stop_turn(f"dashboard:{worker}")
    finally:
        get_tracker().clear(worker)

    assert store.cancel_reason(this_run.id) == "its loop “Release notes” was paused"
    assert not store.cancel_requested(earlier_run.id), "an earlier cycle's batch was stopped"

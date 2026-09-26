"""An approval ends with the work that asked for it, and nothing is approved for work that ended.

Measured on a running gateway (day-8 validation, run ``df5827ca``): a ``deep-research`` run was
cancelled while its ``sweep`` stage waited for spawn approval. The run read ``cancelled``, yet its
page still offered Approve/Reject, Home still said "1 approval waiting", and approving it from Home
logged ``subagent_run spawned`` for ``workflow:df5827ca:sweep`` — a subagent started for a run
that was gone. The cancel path never touched the approvals registry, and neither did the decision
path: it answered whatever was listed.

The owners an approval can have — a workflow run, a loop, a chat turn, a subagent — each end their
approvals here as ``cancelled`` (not "denied": nobody decided anything), on every surface: the
registry that ``GET /api/approvals`` serves, the Inbox row, the ``approval_resolved`` frame every
open card acts on, and a chat's transcript row. And the decision path refuses an owner that has
ended, whichever door the answer came through.

These drive REAL code at the seams the defect lived in: a real ``RunController`` over a real
``SubagentManager`` whose spawn approval goes through a real ``DashboardState.request_approval``,
and the real chat runner parked on a real pending approval.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp.test_utils import TestClient, TestServer
from chat_test_helpers import _api_app
from test_dashboard_approval import (  # the shared run_chat harness
    _complete_event,
    _context_builder,
    _make_hook_store,
    _make_session,
    _set_stream,
)

from personalclaw.config.loader import AppConfig
from personalclaw.dashboard.approval_state import chat_approval_id
from personalclaw.dashboard.chat import api_chat_session_approve, run_chat
from personalclaw.dashboard.handlers.sessions import api_approval_resolve, api_approvals
from personalclaw.dashboard.state import DashboardState
from personalclaw.history import ConversationLog
from personalclaw.inbox import OPEN_STATUSES, InboxStore
from personalclaw.llm.base import EVENT_PERMISSION_REQUEST, EVENT_TEXT_CHUNK, LLMEvent
from personalclaw.sel import SecurityEventLog
from personalclaw.session import SessionManager
from personalclaw.workflows import controller as controller_mod
from personalclaw.workflows import service as workflows_service
from personalclaw.workflows import store
from personalclaw.workflows.controller import EngineServices, RunController
from personalclaw.workflows.models import RunStatus, WorkflowRun

# ── shared harness ─────────────────────────────────────────────────────────────────────────────


def _state_over_a_real_session_manager(tmp_path) -> tuple[DashboardState, AsyncMock]:
    """The dashboard state the gateway builds — over a REAL `SessionManager`, so the stop verb a
    test calls is the real one, wired the way the gateway wires it. Only the four methods that
    would reach a provider process are stood in for (the shape of `_make_state`'s mock)."""
    sessions: Any = SessionManager(AppConfig())
    client = AsyncMock()
    sessions.get_or_create = AsyncMock(return_value=(client, True, False))
    sessions.record_failure = AsyncMock()
    sessions.check_context_usage = MagicMock()
    sessions.get_pid = MagicMock(return_value=None)
    state = DashboardState(
        sessions=sessions, start_time=0.0, conversation_log=ConversationLog(base_dir=tmp_path)
    )
    state.context_builder = _context_builder()
    state._hook_store = _make_hook_store()
    state.push_sessions_update = MagicMock()  # type: ignore[method-assign]
    return state, client


@pytest.fixture
def world(tmp_path, monkeypatch):
    """A real dashboard state with a live Inbox store and a frame log, plus an audit capture."""
    state, client = _state_over_a_real_session_manager(tmp_path)
    frames: list[tuple[str, dict]] = []
    state.broadcast_ws = (  # type: ignore[method-assign]
        lambda kind, data=None: frames.append((kind, data or {}))
    )
    inbox = InboxStore(tmp_path / "inbox_items.json")
    state._inbox_svc = SimpleNamespace(inbox=inbox)
    audit: list = []
    monkeypatch.setattr(SecurityEventLog, "log", lambda self, event: audit.append(event))
    monkeypatch.setattr("personalclaw.workflows.leases.config_dir", lambda: tmp_path)
    return SimpleNamespace(state=state, client=client, inbox=inbox, frames=frames, audit=audit)


def _resolved(w, approval_id: str) -> list[dict]:
    return [d for kind, d in w.frames if kind == "approval_resolved" and d.get("id") == approval_id]


def _open_rows(w, approval_id: str) -> list:
    return [
        i
        for i in w.inbox.items.values()
        if i.refs.get("approval") == approval_id and i.status in OPEN_STATUSES
    ]


def _cancel_rows(w) -> list[tuple[str, str]]:
    return [(e.operation, e.resources) for e in w.audit if e.operation == "approval_cancelled"]


async def _until(predicate, what: str, *, tries: int = 600) -> None:
    for _ in range(tries):
        if predicate():
            return
        await asyncio.sleep(0.005)
    raise AssertionError(f"never happened within {tries * 5} ms: {what}")


def _app(state):
    app = _api_app(state)
    app.router.add_get("/api/approvals", api_approvals)
    app.router.add_post("/api/approvals/{id}/{action}", api_approval_resolve)
    app.router.add_post("/api/chat/sessions/{session}/approve", api_chat_session_approve)
    return app


# ── a workflow run: the measured defect ────────────────────────────────────────────────────────


def _stage_spec() -> dict[str, Any]:
    return {
        "name": "cancel-mid-approval",
        "root": {
            "kind": "sequence",
            "id": "root",
            "children": [{"kind": "stage", "id": "sweep", "config": {"prompt": "sweep sources"}}],
        },
    }


def _real_manager(state):
    """A real SubagentManager whose spawn gate asks the REAL dashboard state, as the gateway's
    `_spawn_approve` does: the spawn's id, listed under the parent session its info names."""
    from personalclaw.subagent import SubagentManager

    sessions = MagicMock()
    sessions.get_pid = MagicMock(return_value=None)
    sessions.get_or_create = AsyncMock(side_effect=RuntimeError("no provider in this test"))
    sessions.release = MagicMock()
    sessions.reset = AsyncMock()
    sessions.get_approval_policy = MagicMock(return_value="")
    ctx = MagicMock()
    ctx.build_message = MagicMock(return_value=("sweep sources", None))
    ctx.hooks.auto_approve_subagent_spawn = False  # the interactive gate, as on the day-8 host
    holder: dict[str, Any] = {}

    async def _spawn_approve(request_id: str, description: str, parent_key: str = "") -> bool:
        info = holder["manager"].get(request_id.removeprefix("spawn:"))
        return await state.request_approval(
            request_id, "subagent", description, session=info.parent_session_key if info else ""
        )

    manager = SubagentManager(
        sessions=sessions, ctx_builder=ctx, on_spawn_approval=_spawn_approve, is_yolo=lambda: False
    )
    holder["manager"] = manager
    state.subagents = manager
    return manager


@pytest.fixture
def run_world(world, monkeypatch):
    # A dispatched stage is POLLED every TICK_WAKE_SECS; shrink it so the cancel lands promptly.
    monkeypatch.setattr(controller_mod, "TICK_WAKE_SECS", 0.02)
    manager = _real_manager(world.state)
    run = store.create(WorkflowRun(id="", workflow_name="cancel-mid-approval"))
    store.write_spec(run.id, _stage_spec())
    controller = RunController(
        run,
        _stage_spec(),
        services=EngineServices(subagents=manager, cwd="", attention_state=world.state),
    )
    world.manager = manager
    world.run = run
    world.controller = controller
    return world


async def _until_spawn_waits(w) -> str:
    await _until(
        lambda: any(a.startswith("spawn:") for a in w.state._pending_approvals),
        "the stage's spawn never asked for approval",
    )
    (approval_id,) = [a for a in w.state._pending_approvals if a.startswith("spawn:")]
    return approval_id


@pytest.mark.asyncio
async def test_cancelling_a_run_ends_its_stage_approval_everywhere_as_cancelled(run_world):
    """The day-8 repro, end to end: cancel while the stage waits, then look at every surface."""
    w = run_world
    driving = asyncio.create_task(w.controller.run_to_completion(timeout=20.0))
    approval_id = await _until_spawn_waits(w)
    # Premises, so a green cannot be a run that never reached the defect's state.
    listed = w.state._pending_approvals[approval_id]
    assert listed["session"] == f"workflow:{w.run.id}:sweep", listed
    assert _open_rows(w, approval_id), "the approval never reached the Inbox"

    assert workflows_service.cancel_run(w.run.id)["ok"] is True
    status = await asyncio.wait_for(driving, timeout=20)

    assert status is RunStatus.CANCELLED
    assert approval_id not in w.state._pending_approvals, "Home/To triage still list it"
    (frame,) = _resolved(w, approval_id)
    assert frame["outcome"] == "cancelled" and frame["approved"] is False, frame
    assert not _open_rows(w, approval_id), "the Inbox row outlived the run"
    info = w.manager.get(approval_id.removeprefix("spawn:"))
    assert info.cancelled and info.done, vars(info)
    assert info.error == "Cancelled: the workflow run was cancelled", info.error
    assert [r for r in _cancel_rows(w) if r[1].startswith(approval_id)], w.audit


@pytest.mark.asyncio
async def test_approving_after_the_run_is_cancelled_runs_nothing(run_world):
    """The measured harm: Approve from Home spawned the subagent. Now the listing is gone, and a
    stale tab's click is refused rather than answered."""
    w = run_world
    driving = asyncio.create_task(w.controller.run_to_completion(timeout=20.0))
    approval_id = await _until_spawn_waits(w)
    workflows_service.cancel_run(w.run.id)
    await asyncio.wait_for(driving, timeout=20)

    async with TestClient(TestServer(_app(w.state))) as http:
        resp = await http.post(f"/api/approvals/{approval_id}/approve")
        body = await resp.text()
    assert resp.status == 404, body
    await asyncio.sleep(0.05)
    w.manager._sessions.get_or_create.assert_not_awaited()
    spawned = [e for e in w.audit if getattr(e, "operation", "") == "subagent_run"]
    assert not [e for e in spawned if e.outcome == "spawned"], spawned


# ── the decision path's defence in depth ───────────────────────────────────────────────────────


async def _background_wait(w, approval_id: str, session: str) -> asyncio.Task:
    task = asyncio.create_task(
        w.state.request_approval(approval_id, "subagent", "subagent_run(sweep)", session=session)
    )
    await _until(lambda: approval_id in w.state._pending_approvals, "never listed")
    return task


@pytest.mark.asyncio
async def test_a_listed_approval_whose_run_ended_is_refused_not_answered(world):
    """A path that forgot to end an approval must still not turn Approve into work: the door
    refuses with a sentence naming the run, the waiter gets a refusal, every surface drops it."""
    run = store.create(WorkflowRun(id="", workflow_name="gone"))
    run.status = RunStatus.CANCELLED
    store.save(run)
    task = await _background_wait(world, "spawn:abc123", f"workflow:{run.id}:sweep")

    async with TestClient(TestServer(_app(world.state))) as http:
        resp = await http.post("/api/approvals/spawn:abc123/approve")
        body = await resp.json()
    assert resp.status == 409, body
    assert body["error"]["code"] == "approval_owner_ended", body
    assert body["error"]["message"] == (
        "Nothing was run: the workflow run that asked for it was cancelled."
    ), body
    assert await asyncio.wait_for(task, timeout=2) is False, "the waiter was told to proceed"
    assert "spawn:abc123" not in world.state._pending_approvals
    (frame,) = _resolved(world, "spawn:abc123")
    assert frame["outcome"] == "cancelled", frame


@pytest.mark.asyncio
async def test_a_cancel_requested_run_is_refused_before_its_controller_applies_it(world):
    """Between the cancel request and the controller's next step the run still reads RUNNING —
    and an Approve in that window is exactly the one to refuse."""
    run = store.create(WorkflowRun(id="", workflow_name="cancelling"))
    run.status = RunStatus.RUNNING
    store.save(run)
    store.request_cancel(run.id)
    task = await _background_wait(world, "spawn:abc124", f"workflow:{run.id}:sweep")

    assert world.state.resolve_approval("spawn:abc124", True) is False
    assert await asyncio.wait_for(task, timeout=2) is False


@pytest.mark.asyncio
async def test_a_live_run_approval_is_answered_as_before(world):
    """The positive control: the refusal is about ENDED owners, not about workflow approvals."""
    run = store.create(WorkflowRun(id="", workflow_name="live"))
    run.status = RunStatus.RUNNING
    store.save(run)
    task = await _background_wait(world, "spawn:abc125", f"workflow:{run.id}:sweep")

    async with TestClient(TestServer(_app(world.state))) as http:
        resp = await http.post("/api/approvals/spawn:abc125/approve")
        body = await resp.text()
    assert resp.status == 200, body
    assert await asyncio.wait_for(task, timeout=2) is True
    (frame,) = _resolved(world, "spawn:abc125")
    assert frame["approved"] is True, frame


@pytest.mark.asyncio
async def test_a_cancelled_subagent_approval_is_refused(world):
    world.state.subagents = SimpleNamespace(
        get=lambda agent_id: SimpleNamespace(cancelled=True, done=True)
    )
    task = await _background_wait(world, "spawn:abc126", "")

    assert world.state.resolve_approval("spawn:abc126", True) is False
    assert await asyncio.wait_for(task, timeout=2) is False
    (row,) = _cancel_rows(world)
    assert row[1] == "spawn:abc126: the subagent that asked for it was cancelled", row


@pytest.mark.asyncio
async def test_an_owner_lookup_that_fails_refuses_an_approve(world, monkeypatch):
    """Fail closed: "could not tell whether the run is still live" does not run the call."""

    def _unreadable(run_id):
        raise OSError("store unreadable")

    monkeypatch.setattr(store, "get", _unreadable)
    task = await _background_wait(world, "spawn:abc127", "workflow:r1:sweep")

    assert world.state.resolve_approval("spawn:abc127", True) is False
    assert await asyncio.wait_for(task, timeout=2) is False


# ── a chat turn ────────────────────────────────────────────────────────────────────────────────

CHAT = "chat-a"


def _bash_request(request_id: str = "req-1") -> LLMEvent:
    return LLMEvent(
        kind=EVENT_PERMISSION_REQUEST,
        title="bash",
        tool_kind="execute",
        request_id=request_id,
        tool_call_id=f"tc-{request_id}",
        tool_input=json.dumps({"command": "rm -rf /tmp/scratch"}),
    )


def _turn() -> list[LLMEvent]:
    return [_bash_request(), LLMEvent(kind=EVENT_TEXT_CHUNK, text="Done."), _complete_event()]


def _permission_resolved(session, request_id: str = "req-1") -> str:
    for msg in session.messages:
        if msg.get("role") == "permission":
            cls = json.loads(msg.get("cls", "{}"))
            if cls.get("request_id") == request_id:
                return str(cls.get("resolved", ""))
    raise AssertionError("no permission row in the transcript")


def _chat(world, key: str = CHAT):
    session = _make_session(key)
    session.agent = "researcher"
    world.state._sessions[session.key] = session
    return session


async def _park(world, session) -> asyncio.Task:
    _set_stream(world.client, _turn())
    task = asyncio.create_task(run_chat(world.state, session, "clean up"))
    await _until(lambda: "req-1" in session._approval_futures, "the chat never asked")
    await _until(
        lambda: chat_approval_id(session.key, "req-1") in world.state._pending_approvals,
        "the chat's approval was never listed",
    )
    return task


@pytest.mark.asyncio
async def test_stopping_a_turn_ends_its_approval_everywhere_as_cancelled(world):
    """Through the REAL stop verb: `SessionManager.stop_turn` on a real SessionManager, wired to
    the real dashboard state the way the gateway wires it."""
    session = _chat(world)
    task = await _park(world, session)
    aid = chat_approval_id(session.key, "req-1")

    await world.state.sessions.stop_turn(f"dashboard:{CHAT}")
    await _until(
        lambda: aid not in world.state._pending_approvals,
        "the stopped turn's approval is still listed on every surface",
    )
    await asyncio.wait_for(task, timeout=5)

    world.client.reject_tool.assert_awaited_once_with("req-1")
    world.client.approve_tool.assert_not_awaited()
    assert aid not in world.state._pending_approvals
    (frame,) = _resolved(world, aid)
    assert frame["outcome"] == "cancelled", frame
    assert not _open_rows(world, aid)
    # The transcript's permanent record says what happened, so a reload shows no live card.
    assert _permission_resolved(session) == "cancelled"
    tools = [m["content"] for m in session.messages if m.get("role") == "tool"]
    assert "bash (cancelled)" in tools, tools
    assert _cancel_rows(world) == [("approval_cancelled", f"{aid}: its turn was stopped")]


def test_the_dashboard_state_registers_its_stop_hook():
    """The wiring, not just the method: an unregistered hook is a closed gap that looks closed."""
    manager = SessionManager(AppConfig())
    assert manager._on_turn_stop is None
    state = DashboardState(sessions=manager, start_time=0.0)
    assert manager._on_turn_stop == state.cancel_turn_approvals


@pytest.mark.asyncio
async def test_the_stop_hook_runs_even_with_no_provider_registered():
    manager = SessionManager(AppConfig())
    told: list[str] = []
    manager.register_turn_stop_hook(lambda key: told.append(key) or 0)

    assert await manager.stop_turn("dashboard:nobody") == "idle"
    assert told == ["dashboard:nobody"]


@pytest.mark.asyncio
async def test_an_unanswered_chat_approval_says_it_expired(world, monkeypatch):
    """A timeout is not a Deny either: the card and the transcript say it expired."""
    monkeypatch.setattr(type(world.state), "_APPROVAL_TIMEOUT", 0.05)
    session = _chat(world)
    _set_stream(world.client, _turn())
    await asyncio.wait_for(run_chat(world.state, session, "clean up"), timeout=5)

    aid = chat_approval_id(session.key, "req-1")
    (frame,) = _resolved(world, aid)
    assert frame["outcome"] == "expired", frame
    assert _permission_resolved(session) == "expired"


@pytest.mark.asyncio
async def test_a_background_approval_nobody_answers_says_it_expired(world, monkeypatch):
    monkeypatch.setattr(type(world.state), "_UNATTENDED_APPROVAL_TIMEOUT", 0.05)
    assert await world.state.request_approval("cron-1", "cron", "bash") is False
    (frame,) = _resolved(world, "cron-1")
    assert frame["outcome"] == "expired", frame


@pytest.mark.asyncio
async def _deny_in_chat(world):
    session = _chat(world)
    task = await _park(world, session)
    async with TestClient(TestServer(_app(world.state))) as http:
        resp = await http.post(
            f"/api/chat/sessions/{CHAT}/approve", json={"action": "rejected", "request_id": "req-1"}
        )
    assert resp.status == 200
    await asyncio.wait_for(task, timeout=5)
    (frame,) = _resolved(world, chat_approval_id(session.key, "req-1"))
    return session, frame


@pytest.mark.asyncio
async def test_a_decision_still_says_what_the_person_chose(world):
    """The baseline, on both trees: an answer is an answer."""
    session, frame = await _deny_in_chat(world)
    assert frame["approved"] is False, frame
    assert _permission_resolved(session) == "rejected"


@pytest.mark.asyncio
async def test_a_decision_frame_names_its_outcome(world):
    """The frame now says WHICH of the four endings happened, so a card can tell a Deny from a
    stopped turn instead of rendering both as "denied"."""
    _session, frame = await _deny_in_chat(world)
    assert frame["outcome"] == "rejected", frame


# ── a loop ─────────────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def loop_home(tmp_path, monkeypatch):
    monkeypatch.setattr("personalclaw.loop.files.config_dir", lambda: tmp_path)
    return tmp_path


def _running_loop():
    from personalclaw.loop import store as loop_store
    from personalclaw.loop.loop import Loop, LoopStatus

    loop = loop_store.create(Loop(id="", name="G", kind="goal", task="investigate the regression"))
    loop_store.update_status(loop.id, LoopStatus.RUNNING)
    return loop


@pytest.mark.asyncio
async def test_a_stopped_loops_approval_is_refused(world, loop_home):
    """Defence in depth for the loop owner. A loop's worker turn is unattended by construction,
    so it never parks an approval itself; what waits is a subagent that worker spawned, listed
    under the worker's session. The door checks the loop, not just the waiter."""
    from personalclaw.loop import store as loop_store
    from personalclaw.loop.loop import LoopStatus, LoopStopReason

    loop = _running_loop()
    task = await _background_wait(world, "spawn:fff666", f"loop-{loop.id}")
    loop_store.update_status(loop.id, LoopStatus.STOPPED, stop_reason=LoopStopReason.USER)

    async with TestClient(TestServer(_app(world.state))) as http:
        resp = await http.post("/api/approvals/spawn:fff666/approve")
        body = await resp.json()
    assert resp.status == 409, body
    assert body["error"]["message"] == "Nothing was run: the loop that asked for it was stopped."
    assert await asyncio.wait_for(task, timeout=2) is False


# ── a subagent's own tool calls ────────────────────────────────────────────────────────────────


def test_two_subagents_waiting_on_the_same_raw_id_are_two_approvals():
    """An ACP agent's permission ids are per-connection JSON-RPC ids: two subagents both ask on
    "1". Keyed by the raw id they shared one registry row, so cancelling one withdrew the other's
    approval while it still waited."""
    from personalclaw.subagent import approval_subagent_id, spawn_approval_id, tool_approval_id

    first, second = tool_approval_id("aaa111", "1"), tool_approval_id("bbb222", "1")
    assert first != second
    assert (approval_subagent_id(first), approval_subagent_id(second)) == ("aaa111", "bbb222")
    assert approval_subagent_id(spawn_approval_id("ccc333")) == "ccc333"
    # Not a subagent's: a chat approval id, an MCP elicitation, a bare `subagent:` session key.
    for other in ("chat-a:req-1", "mcp-elicit-0123", "subagent:ddd444"):
        assert approval_subagent_id(other) == "", other


@pytest.mark.asyncio
async def test_a_subagent_tool_call_is_listed_under_its_own_id(world):
    """The subagent's approval callback is handed an id naming the subagent, while the agent is
    still answered on its own raw id."""
    from personalclaw.subagent import SubagentInfo, SubagentManager

    asked: list[str] = []

    async def _on_tool_approval(event: LLMEvent, parent_session_key: str = "") -> bool:
        asked.append(str(event.request_id))
        return False

    client = AsyncMock()
    client.stream = MagicMock(
        side_effect=lambda *a, **kw: _events(
            LLMEvent(kind=EVENT_PERMISSION_REQUEST, title="bash", request_id="1"),
            _complete_event(),
        )
    )
    sessions = MagicMock()
    sessions.get_pid = MagicMock(return_value=None)
    sessions.get_or_create = AsyncMock(return_value=(client, True, False))
    sessions.release = MagicMock()
    sessions.reset = AsyncMock()
    sessions.get_approval_policy = MagicMock(return_value="")
    sessions.get_agent = MagicMock(return_value="")
    ctx = MagicMock()
    ctx.build_message = MagicMock(return_value=("do it", None))
    ctx.hooks.on_tool_call = MagicMock(return_value=SimpleNamespace(action="ask"))
    manager = SubagentManager(
        sessions=sessions,
        ctx_builder=ctx,
        on_tool_approval=_on_tool_approval,
        is_yolo=lambda: False,
    )
    info = SubagentInfo(id="eee555", task="do it", parent_session_key="dashboard:chat-a")
    manager._agents[info.id] = info

    await asyncio.wait_for(manager._run_inner(info, f"subagent:{info.id}"), timeout=5)

    assert asked == ["subagent:eee555:1"], asked
    client.reject_tool.assert_awaited_with("1")


async def _events(*events: LLMEvent):
    for event in events:
        yield event

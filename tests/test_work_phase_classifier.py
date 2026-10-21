"""A chat turn says what it is doing as the gateway measures it, in one ``work_phase`` frame.

The chat used to be told one free-text status per turn ("Thinking…") and guessed the rest from the
words, so a search, a plan, a wait on her answer and a first message on an idle chat all read the
same, or not at all. Now the gateway classifies each step of the turn from the events it already
sees — the turn accepted, its model starting, each tool call by its name or declared kind, the
first word of its answer, an approval or a question waiting on her, a helper it hands work to —
and says the phase when it changes, never once per streamed chunk. The turn's last frame says how
it ended.

Everything runs through the REAL chat runner, the real approval registry, the real question
registry and the gateway's real subagent event handler; only the runtimes are fakes.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp.test_utils import TestClient, TestServer
from chat_test_helpers import _make_app
from table_rows import row

from personalclaw.approval_answer import YOU
from personalclaw.dashboard.chat_runner import run_chat
from personalclaw.dashboard.state import DashboardState, _ChatSession
from personalclaw.history import ConversationLog
from personalclaw.llm.events import (
    EVENT_COMPLETE,
    EVENT_PERMISSION_REQUEST,
    EVENT_TEXT_CHUNK,
    EVENT_TOOL_CALL,
    EVENT_TOOL_RESULT,
    AgentEvent,
)
from personalclaw.owner_questions import normalize
from personalclaw.work_phase import WorkPhase, classify_call

CHAT = "chat-phase-1"


class _World:
    """One chat on a state whose every frame is recorded, served by a runtime of the test's."""

    def __init__(self, tmp_path, stream: Callable[[], AsyncIterator[AgentEvent]], *, acp=False):
        self.client = AsyncMock()
        self.client.provider_id = "acp:scripted" if acp else "scripted"
        self.client.context_usage_pct = MagicMock(return_value=10.0)
        self.client.set_steer_source = MagicMock(return_value=False)
        self.client.announce_failover = MagicMock()
        self.client.stream = MagicMock(side_effect=lambda *a, **kw: stream())
        sessions = MagicMock(count=0)
        sessions.get_pid = MagicMock(return_value=None)
        sessions.record_failure = AsyncMock()
        sessions.check_context_usage = MagicMock()
        sessions.record_success = MagicMock()
        sessions.set_steer_drains = MagicMock(return_value=[])
        sessions.get_or_create = AsyncMock(return_value=(self.client, True, False))
        self.state = DashboardState(
            sessions=sessions,
            start_time=0.0,
            conversation_log=ConversationLog(base_dir=tmp_path),
        )
        from personalclaw.hooks import ToolHookResult

        cb = MagicMock()
        cb.hooks.on_tool_call.return_value = ToolHookResult.allow()
        cb.build_message.return_value = ("hello", None)
        cb.conversation_log = MagicMock()
        cb.conversation_log.read_messages = MagicMock(return_value=[])
        self.state.context_builder = cb
        hooks = MagicMock()
        hooks.fire_for_ids = AsyncMock(return_value=[])
        self.state._hook_store = hooks
        self.frames: list[tuple[str, Any]] = []
        self.state.broadcast_ws = lambda kind, data=None: self.frames.append((kind, data))
        self.state.push_sessions_update = MagicMock()
        self.state.push_refresh = MagicMock()
        self.session: _ChatSession = self.state.get_or_create_session(CHAT)
        self.session._trust = True
        self.session._titled = True

    def phases(self) -> list[dict]:
        """The chat's own phase frames: a helper's, which can name the chat too, are its own."""
        return [d for k, d in self.frames if k == "work_phase" and d["kind"] == "chat"]

    def said(self) -> list[tuple[str, str]]:
        """The phases said, each with its line, in order."""
        return [(f["phase"], f["line"]) for f in self.phases()]

    def count(self, kind: str) -> int:
        return sum(1 for k, _ in self.frames if k == kind)

    async def turn(self, message: str = "hello") -> None:
        """Run one turn from her message, and every turn it queues."""
        self.session.append("user", message, "msg msg-u")
        with _quiet():
            await run_chat(self.state, self.session, message)
            while self.session.task is not None and not self.session.task.done():
                await asyncio.wait_for(self.session.task, timeout=10)

    def start(self, message: str = "hello") -> asyncio.Task:
        """Start one turn from her message, as the dashboard does, and hand it back running."""
        self.session.append("user", message, "msg msg-u")

        async def run() -> None:
            with _quiet():
                await run_chat(self.state, self.session, message)

        task = asyncio.ensure_future(run())
        self.session.task = task
        return task

    async def until(self, phase: str) -> dict:
        for _ in range(500):
            said = [f for f in self.phases() if f["phase"] == phase]
            if said:
                return said[-1]
            await asyncio.sleep(0.01)
        raise AssertionError(f"{phase!r} was never said: {self.said()}")


def _quiet():
    """The parts of a turn's end that are not this contract: its audit rows and its chips."""
    from contextlib import ExitStack

    stack = ExitStack()
    stack.enter_context(patch("personalclaw.dashboard.chat_runner.sel", MagicMock()))
    stack.enter_context(patch("personalclaw.dashboard.chat_runner.maybe_offer_check_work"))
    stack.enter_context(
        patch("personalclaw.dashboard.chat_runner._maybe_followups", new=AsyncMock())
    )
    stack.enter_context(patch("personalclaw.dashboard.chat_plan.maybe_submit_plan_draft"))
    return stack


def _call(title: str, call_id: str, kind: str = "", tool_input: Any = None) -> AgentEvent:
    return AgentEvent(
        kind=EVENT_TOOL_CALL,
        title=title,
        tool_kind=kind,
        tool_call_id=call_id,
        tool_input=tool_input or {},
    )


def _result(call_id: str) -> AgentEvent:
    return AgentEvent(kind=EVENT_TOOL_RESULT, tool_call_id=call_id, tool_output="ok")


def _answer(words: int = 1) -> list[AgentEvent]:
    return [AgentEvent(kind=EVENT_TEXT_CHUNK, text=f"word{i} ") for i in range(words)] + [
        AgentEvent(kind=EVENT_COMPLETE, stop_reason="end_turn")
    ]


def _scripted(*events: AgentEvent) -> Callable[[], AsyncIterator[AgentEvent]]:
    async def stream() -> AsyncIterator[AgentEvent]:
        for event in events:
            yield event

    return stream


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)


# ── 1. accepted, then started ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_first_message_on_an_idle_chat_is_queued_before_anything_runs(tmp_path):
    """The turn is said the moment it is accepted, before its runtime is asked for — a busy local
    model can take minutes to start, and the chat showed nothing of the turn until then."""
    said_when: dict[str, list[str]] = {}

    async def stream() -> AsyncIterator[AgentEvent]:
        said_when["streaming"] = [f["phase"] for f in w.phases()]
        for event in _answer():
            yield event

    w = _World(tmp_path, stream)

    async def acquire(*_a, **_kw):
        said_when["acquired"] = [f["phase"] for f in w.phases()]
        return (w.client, True, False)

    w.state.sessions.get_or_create = AsyncMock(side_effect=acquire)
    await w.turn()

    first = w.phases()[0]
    assert (first["phase"], first["line"]) == ("queued", "Waiting to start")
    assert first["work"] == f"chat:{CHAT}" and first["session"] == CHAT and first["kind"] == "chat"
    # The turn is named by the row that started it: her message.
    assert first["turn"]["message_id"] == w.session.messages[0]["ts"]
    assert first["turn"]["started_at"].endswith("+00:00")
    assert said_when["acquired"] == ["queued"]
    assert said_when["streaming"] == ["queued", "thinking"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("task_mode", "said"), [("agent", ("thinking", "Thinking")), ("plan", ("planning", "Planning"))]
)
async def test_the_turn_says_it_thinks_as_its_model_starts_and_plans_in_plan_mode(
    tmp_path, task_mode, said
):
    w = _World(tmp_path, _scripted(*_answer()))
    w.session._task_mode = task_mode
    await w.turn()
    assert w.said()[1] == said


# ── 2. each step, by what it is ───────────────────────────────────────────────────────────


#: A call's card title and its declared kind, and the phase and line it reads as.
CALLS = [
    ("knowledge_search", "", "searching", "Searching your knowledge"),
    ("memory_recall", "", "searching", "Searching your memory"),
    ("grep", "", "searching", "Searching the files"),
    ("glob", "", "searching", "Searching the files"),
    ("tool_search", "", "searching", "Searching your tools"),
    ("web_search", "", "searching", "Searching the web"),
    ("shell", "", "tool", "shell"),
    # An agent CLI declares what a call is: a search, a think, or any other step.
    ("Find `parse_url` in src", "search", "searching", "Find `parse_url` in src"),
    ("Plan the change", "think", "planning", "Planning"),
    ("Run: npm test", "execute", "tool", "Run: npm test"),
    # A search name a CLI happens to use is still read by its declared kind.
    ("grep", "execute", "tool", "grep"),
]


def test_a_call_reads_as_the_step_it_is(subtests):
    assert len(CALLS) == 11, "a call was dropped from the table"
    for title, kind, phase, line in CALLS:
        with row(subtests, title=title, kind=kind):
            assert classify_call(title, kind) == (WorkPhase(phase), line)


@pytest.mark.asyncio
async def test_a_native_turn_says_each_step_once_and_its_answer_once(tmp_path):
    w = _World(
        tmp_path,
        _scripted(
            _call("knowledge_search", "t1", tool_input={"query": "release"}),
            _result("t1"),
            _call("shell", "t2", tool_input={"command": "ls"}),
            _result("t2"),
            *_answer(words=40),
        ),
    )
    await w.turn()

    assert w.said() == [
        ("queued", "Waiting to start"),
        ("thinking", "Thinking"),
        ("searching", "Searching your knowledge"),
        ("thinking", "Thinking"),
        ("tool", "shell"),
        ("thinking", "Thinking"),
        ("streaming", "Writing"),
        ("done", w.phases()[-1]["line"]),
    ]
    assert w.phases()[-1]["line"].startswith("Done in ")
    # Never once per chunk: forty words streamed, and the answer was said once.
    assert w.count("chat_chunk") == 40
    assert w.count("work_phase") <= w.count("chat_chunk") // 4


@pytest.mark.asyncio
async def test_an_agent_cli_turn_reads_its_steps_by_their_declared_kind(tmp_path):
    w = _World(
        tmp_path,
        _scripted(
            _call("Find `parse_url` in src", "t1", kind="search"),
            _result("t1"),
            _call("Plan the change", "t2", kind="think"),
            _result("t2"),
            *_answer(),
        ),
        acp=True,
    )
    await w.turn()
    assert w.said()[1:6] == [
        ("thinking", "Thinking"),
        ("searching", "Find `parse_url` in src"),
        ("thinking", "Thinking"),
        ("planning", "Planning"),
        ("thinking", "Thinking"),
    ]


# ── 3. waiting on her ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_an_approval_is_said_while_it_waits_and_the_step_resumes_after(tmp_path):
    w = _World(
        tmp_path,
        _scripted(
            _call("shell", "t1", tool_input={"command": "rm -rf build"}),
            AgentEvent(
                kind=EVENT_PERMISSION_REQUEST,
                title="shell",
                request_id="r1",
                tool_call_id="t1",
                tool_input=json.dumps({"command": "rm -rf build"}),
            ),
            _result("t1"),
            *_answer(),
        ),
    )
    w.session._trust = False
    task = w.start()
    waiting = await w.until("approval")
    assert waiting["line"] == "Needs your OK: shell"
    assert waiting["turn"] is not None

    w.state.decide_session_approval(w.session, "r1", "approved", by=YOU)
    await asyncio.wait_for(task, timeout=10)

    said = w.said()
    at = said.index(("approval", "Needs your OK: shell"))
    assert said[at - 1 : at + 3] == [
        ("tool", "shell"),
        ("approval", "Needs your OK: shell"),
        ("tool", "shell"),
        ("thinking", "Thinking"),
    ]


@pytest.mark.asyncio
async def test_a_question_waiting_on_her_answer_is_said_as_input(tmp_path):
    questions = normalize(
        {"questions": [{"question": "Which branch?", "options": [{"label": "main"}]}]}
    )

    async def stream() -> AsyncIterator[AgentEvent]:
        yield _call("ask_user", "t1")
        # What PersonalClaw's own question tool does inside the runtime: ask, and wait.
        await w.state.owner_questions.ask(f"dashboard:{CHAT}", questions, asked_by="The agent")
        yield _result("t1")
        for event in _answer():
            yield event

    w = _World(tmp_path, stream)
    task = w.start()
    asked = await w.until("input")
    assert asked["line"] == "Asks: Which branch?"

    (card,) = w.state.owner_questions.pending_for(CHAT)
    w.state.owner_questions.answer(CHAT, card["id"], answers=[{"selected": [0]}])
    await asyncio.wait_for(task, timeout=10)

    said = w.said()
    at = said.index(("input", "Asks: Which branch?"))
    assert said[at + 1] == ("tool", "ask_user")


# ── 4. handing work to helpers, through the gateway's own subagent events ─────────────────


def _gateway_events(state: DashboardState):
    """The gateway's subagent event handler, wired to *state*, and the manager it reads."""
    from test_gateway import _make_orchestrator, _mock_sessions

    orch = _make_orchestrator()
    orch.sessions = _mock_sessions()
    orch.ctx_builder = MagicMock()
    orch.ctx_builder.hooks = MagicMock()
    orch.dashboard_state = state
    with (
        patch("personalclaw.trust_mode.is_yolo_active", return_value=False),
        patch("personalclaw.gateway.SubagentManager") as manager_type,
    ):
        manager = MagicMock()
        manager.start_reaper = MagicMock()
        manager.running = []
        manager_type.return_value = manager
        orch._init_subagents()
    return manager_type.call_args[1]["on_event"], manager


@pytest.mark.asyncio
async def test_a_helper_started_for_the_turn_is_said_as_handing_off(tmp_path):
    from personalclaw.subagent import SubagentInfo

    gate = asyncio.Event()

    async def stream() -> AsyncIterator[AgentEvent]:
        await gate.wait()
        for event in _answer():
            yield event

    w = _World(tmp_path, stream)
    on_event, manager = _gateway_events(w.state)
    task = w.start()
    await w.until("thinking")

    helpers = [
        SubagentInfo(id=f"h{i}", task="look it up", parent_session_key=f"dashboard:{CHAT}")
        for i in (1, 2)
    ]
    for helper in helpers:
        manager.running = [*manager.running, helper]
        await on_event("subagent_spawn", helper, {"task": helper.task, "agent": ""})
    manager.running = [helpers[1]]
    await on_event("subagent_done", helpers[0], {})
    manager.running = []
    await on_event("subagent_done", helpers[1], {})
    gate.set()
    await asyncio.wait_for(task, timeout=10)

    assert w.said()[2:6] == [
        ("delegating", "Handing off to 1 helper"),
        ("delegating", "Handing off to 2 helpers"),
        ("delegating", "Handing off to 1 helper"),
        ("thinking", "Thinking"),
    ]


@pytest.mark.asyncio
async def test_the_helpers_counted_are_every_one_at_work_for_the_chat(tmp_path):
    """Its own helpers and the tasks of a batch it started are all its helpers: the count is theirs
    together, and the turn is back to thinking only when the last of them ends."""
    from personalclaw.subagent import SubagentInfo

    gate = asyncio.Event()

    async def stream() -> AsyncIterator[AgentEvent]:
        await gate.wait()
        for event in _answer():
            yield event

    w = _World(tmp_path, stream)
    on_event, manager = _gateway_events(w.state)
    task = w.start()
    await w.until("thinking")

    own = SubagentInfo(id="own", task="look it up", parent_session_key=f"dashboard:{CHAT}")
    batch = SubagentInfo(id="task-1", task="one of three", parent_session_key="workflow:run-1:n1")
    other = SubagentInfo(id="else", task="not ours", parent_session_key="dashboard:another-chat")

    def task_of_chat(key: str) -> tuple[str, str, str]:
        return (CHAT, "run-1", "Compare") if key == batch.parent_session_key else ("", "", "")

    with patch("personalclaw.workflows.batch_start.task_of_chat", task_of_chat):
        manager.running = [other, own]
        await on_event("subagent_spawn", own, {"task": own.task, "agent": ""})
        manager.running = [other, own, batch]
        await on_event("subagent_spawn", batch, {"task": batch.task, "agent": ""})
        await on_event("subagent_done", batch, {})
        manager.running = [other, own]
        await on_event("subagent_done", own, {})
        manager.running = [other]
    gate.set()
    await asyncio.wait_for(task, timeout=10)

    assert w.said()[2:6] == [
        ("delegating", "Handing off to 1 helper"),
        ("delegating", "Handing off to 2 helpers"),
        ("delegating", "Handing off to 1 helper"),
        ("thinking", "Thinking"),
    ]


@pytest.mark.asyncio
async def test_a_helper_that_outlives_its_turn_does_not_hold_the_chat(tmp_path):
    from personalclaw.subagent import SubagentInfo

    w = _World(tmp_path, _scripted(*_answer()))
    on_event, manager = _gateway_events(w.state)
    await w.turn()
    before = len(w.phases())

    helper = SubagentInfo(id="late", task="t", parent_session_key=f"dashboard:{CHAT}")
    manager.running = [helper]
    await on_event("subagent_spawn", helper, {"task": "t", "agent": ""})
    assert len(w.phases()) == before


# ── 5. how it ends ────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_failed_turn_ends_on_an_error_frame_with_its_reason(tmp_path):
    w = _World(tmp_path, _scripted())
    w.state.sessions.get_or_create = AsyncMock(side_effect=RuntimeError("upstream said no"))
    await w.turn()

    last = w.phases()[-1]
    assert last["phase"] == "error"
    assert last["turn"] is None and last["last_outcome"] == "error"
    reason = next(m["content"] for m in reversed(w.session.messages) if m["role"] == "error")
    assert last["line"] == f"Stopped: {reason}"


@pytest.mark.asyncio
async def test_a_stop_is_said_while_it_is_asked_and_the_turn_ends_done(tmp_path):
    gate = asyncio.Event()

    async def stream() -> AsyncIterator[AgentEvent]:
        yield AgentEvent(kind=EVENT_TEXT_CHUNK, text="Half an ")
        await gate.wait()
        yield AgentEvent(kind=EVENT_COMPLETE, stop_reason="cancelled")

    w = _World(tmp_path, stream)

    async def stop_turn(*_a, **_kw) -> str:
        gate.set()
        return "soft"

    w.state.sessions.stop_turn = AsyncMock(side_effect=stop_turn)
    task = w.start()
    await w.until("streaming")
    with patch("personalclaw.dashboard.chat_handlers.sel", MagicMock()):
        async with TestClient(TestServer(_make_app(w.state))) as http:
            assert (await http.post(f"/api/chat/sessions/{CHAT}/stop")).status == 200
            await asyncio.wait_for(task, timeout=10)

    stopping = [f for f in w.phases() if f["stopping"]]
    assert stopping and stopping[0]["phase"] == "streaming"
    last = w.phases()[-1]
    assert (last["phase"], last["line"]) == ("done", "Stopped")
    assert last["turn"] is None and last["last_outcome"] == "stopped"
    assert last["stopping"] is False

"""A message she types into a running turn is a row of hers in the chat, where the turn took it.

The composer sends a message typed while an answer streams as a steer, and a native turn takes it
at its next model boundary: the model reads it inside the answer being written. Nothing wrote it
to the chat. Her words lived only in the runtime's history, so a reload showed the answer with
nothing of hers in it, and learning, which reads the chat, never saw them.

Now the turn that takes a steer says so in its stream (``EVENT_STEER``), and the chat writes it in
the one place a steer becomes a row (``running_turn.take_steer``): after what the answer said
before it, before what it says next, in her words, with the time she sent it. A steer the turn
does not take runs next from the queue and is written then, once.

Driven through the real chat handler, the real turn engine, the real session manager and a real
native runtime answered by a scripted model. The model's first answer pauses after its first words
until the test has sent her steer, the way she types while an answer streams.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp.test_utils import TestClient, TestServer
from chat_test_helpers import _api_app

from personalclaw.agents.native.runtime import _MAX_STEERS_PER_TURN, NativeAgentRuntime
from personalclaw.agents.provider import AgentRuntimeDefinition
from personalclaw.config import loader as config_loader
from personalclaw.config.loader import AppConfig
from personalclaw.context import ContextBuilder
from personalclaw.dashboard.chat import api_chat, api_chat_session_detail
from personalclaw.dashboard.chat_utils import persisted_history_key
from personalclaw.dashboard.state import DashboardState
from personalclaw.history import ConversationLog, consolidation_line
from personalclaw.llm.events import EVENT_COMPLETE, EVENT_TEXT_CHUNK, EVENT_TOOL_CALL, AgentEvent
from personalclaw.memory import MemoryStore
from personalclaw.memory_service import MemoryService
from personalclaw.own_words import own_words
from personalclaw.session import SessionManager
from personalclaw.skills import SkillsLoader
from personalclaw.tool_providers.base import RiskLevel, ToolDefinition, ToolProvider, ToolResult
from personalclaw.vector_memory import VectorMemoryStore

ASKED = "Write the loader for the garden config."
STEER = "Use tabs, not spaces."
STEER_TS = "2026-10-04T09:30:05+00:00"
FIRST = "I'll indent it with four spaces."
SECOND = "Switching to tabs, as you asked."


class _Model:
    """A model that answers each request with its next reply and calls no tool.

    Its first answer stops after its first words until ``go_on`` is set: the test sends her steer
    in that pause, while the answer is still being written."""

    supports_tools = True
    _model = "scripted"

    def __init__(self, *replies: str) -> None:
        self.replies = list(replies)
        self.requests: list[list[dict]] = []
        self.answering = asyncio.Event()
        self.go_on = asyncio.Event()

    async def complete(self, messages, *, tools=None, model=None, reasoning_effort=""):
        self.requests.append([dict(m) for m in messages])
        yield AgentEvent(kind=EVENT_TEXT_CHUNK, text=self.replies.pop(0))
        if len(self.requests) == 1:
            self.answering.set()
            await self.go_on.wait()
        yield AgentEvent(kind=EVENT_COMPLETE)

    async def cancel(self, *, wait_ack_timeout: float = 0.0) -> str:
        return "acked"


class _World:
    """One gateway's chat surface: the handler, the turn engine, the session manager and a native
    runtime per chat, answered by :class:`_Model`."""

    def __init__(self, tmp_path: Path, *replies: str) -> None:
        self.model = _Model(*replies)

        def factory(session_key=None, **_kwargs):
            return NativeAgentRuntime(
                definition=AgentRuntimeDefinition(
                    name="PersonalClaw", provider="native", model="scripted"
                ),
                model_provider=self.model,
                tool_providers=[],
                cwd=tmp_path,
            )

        self.sessions = SessionManager(AppConfig(), provider_factory=factory)
        self.log = ConversationLog(base_dir=tmp_path / "history")
        self.state = DashboardState(
            sessions=self.sessions, start_time=0.0, conversation_log=self.log
        )
        self.state.context_builder = ContextBuilder(
            memory=MemoryStore(workspace=tmp_path / "ws"),
            skills=SkillsLoader(skills_path=tmp_path / "skills", install_builtins=False),
            conversation_log=self.log,
        )
        self.state._hook_store = None
        self.frames: list[tuple[str, dict]] = []
        self.state.broadcast_ws = lambda kind, data=None: self.frames.append(
            (kind, dict(data or {}))
        )
        self.state.push_sessions_update = lambda *a, **k: None

    @asynccontextmanager
    async def client(self):
        app = _api_app(self.state)
        app.router.add_post("/api/chat", api_chat)
        async with TestClient(TestServer(app)) as client:
            with patch("personalclaw.dashboard.chat_runner.sel", MagicMock()):
                yield client
        await self.sessions.close_all()

    async def send(self, client: TestClient, message: str, **body: Any) -> dict:
        resp = await client.post("/api/chat?ws=1", json={"message": message, **body})
        assert resp.status == 200, await resp.text()
        return await resp.json()

    async def steer(self, client: TestClient, key: str, text: str, ts: str = STEER_TS) -> dict:
        """What the composer sends for a message typed while the turn runs."""
        return await self.send(
            client, text, session=key, queue_mode="steer", meta={"client_ts": ts}
        )

    async def settled(self, key: str):
        """The chat, once it has answered everything it was sent."""
        session = self.state._sessions[key]
        for _ in range(1500):
            task = session.task
            if (task is None or task.done()) and not session._queue:
                return session
            await asyncio.sleep(0.01)
        raise AssertionError(f"the chat never settled: {session.messages}")

    def frames_of(self, kind: str) -> list[dict]:
        return [data for k, data in self.frames if k == kind]


def _conversation(messages: list[dict]) -> list[tuple[str, str]]:
    """The chat as she reads it: her messages and the answers, in order."""
    return [(m["role"], m["content"]) for m in messages if m.get("role") in ("user", "assistant")]


async def _reopened(log: ConversationLog, key: str) -> list[dict]:
    """The chat as a page reads it after the gateway restarted: from its file alone."""
    state = DashboardState(sessions=MagicMock(count=0), start_time=0.0, conversation_log=log)
    app = _api_app(state)
    app.router.add_get("/api/chat/sessions/{session}", api_chat_session_detail)
    async with TestClient(TestServer(app)) as client:
        resp = await client.get(f"/api/chat/sessions/{key}")
        assert resp.status == 200, await resp.text()
        return (await resp.json())["messages"]


async def _a_turn_she_steers(world: _World, *steers: tuple[str, str]) -> Any:
    """She asks, and while the first answer is being written sends each of *steers* (its words
    and the time she sent it) into the running turn. Returns the chat once all is answered."""
    async with world.client() as client:
        key = (await world.send(client, ASKED))["session"]
        await asyncio.wait_for(world.model.answering.wait(), timeout=10)
        for text, ts in steers:
            assert await world.steer(client, key, text, ts) == {"ok": True, "steered": True}
        world.model.go_on.set()
        return await world.settled(key)


# ── her steer is a row of hers, where the turn took it ─────────────────────────────────────


@pytest.mark.asyncio
async def test_a_steer_the_turn_takes_is_her_row_where_she_sent_it_and_after_a_restart(tmp_path):
    world = _World(tmp_path, FIRST, SECOND)
    session = await _a_turn_she_steers(world, (STEER, STEER_TS))

    # The premise: the running turn took it, so the model read it inside the same answer.
    assert len(world.model.requests) == 2
    assert STEER in json.dumps(world.model.requests[1])

    # Her words sit between what the answer said before them and what it said after.
    conversation = [("user", ASKED), ("assistant", FIRST), ("user", STEER), ("assistant", SECOND)]
    assert _conversation(session.messages) == conversation
    row = next(m for m in session.messages if m.get("role") == "user" and m["content"] == STEER)
    assert row["ts"] == STEER_TS, "her row keeps the time she sent it"
    assert own_words(row) == STEER, "the row holds her words, not the runtime's label for them"

    # And a page that reads the chat again after a restart finds them in the same place.
    reread = await _reopened(world.log, session.key)
    assert _conversation(reread) == conversation
    kept = next(m for m in reread if m["role"] == "user" and m["content"] == STEER)
    assert kept["ts"] == STEER_TS
    assert kept["meta"]["steered"] is True


@pytest.mark.asyncio
async def test_open_pages_are_told_her_steer_after_the_words_it_follows(tmp_path):
    world = _World(tmp_path, FIRST, SECOND)
    session = await _a_turn_she_steers(world, (STEER, STEER_TS))

    assert world.frames_of("chat_user_message") == [
        {"session": session.key, "content": STEER, "ts": STEER_TS, "steer": True}
    ]
    # The answer so far is settled first, so a page shows her message below it, not inside it,
    # and what the answer says next streams after it.
    told = [
        (kind, data.get("content"))
        for kind, data in world.frames
        if kind in ("chat_chunk", "chat_segment", "chat_user_message")
    ]
    assert told == [
        ("chat_chunk", FIRST),
        ("chat_segment", None),
        ("chat_user_message", STEER),
        ("chat_chunk", SECOND),
    ]


@pytest.mark.asyncio
async def test_a_steer_sent_without_a_mode_under_the_steer_policy_is_the_same_row(tmp_path):
    """The API's door: a caller that states no mode gets the platform's, and a steer it sends is
    written the way the composer's is."""
    config_loader.config_dir()
    config_loader.config_path().write_text(
        json.dumps({"resilience": {"mid_turn_policy": "steer"}}), encoding="utf-8"
    )
    world = _World(tmp_path, FIRST, SECOND)
    async with world.client() as client:
        key = (await world.send(client, ASKED))["session"]
        await asyncio.wait_for(world.model.answering.wait(), timeout=10)
        assert await world.send(client, STEER, session=key) == {"ok": True, "steered": True}
        world.model.go_on.set()
        session = await world.settled(key)

    assert _conversation(session.messages) == [
        ("user", ASKED),
        ("assistant", FIRST),
        ("user", STEER),
        ("assistant", SECOND),
    ]


@pytest.mark.asyncio
async def test_a_send_cannot_mark_itself_a_steer(tmp_path):
    """Whether a row is a steer is said by the code that writes it, never by the send."""
    world = _World(tmp_path, FIRST)
    world.model.go_on.set()
    async with world.client() as client:
        key = (await world.send(client, ASKED, meta={"steered": True}))["session"]
        session = await world.settled(key)

    row = next(m for m in session.messages if m.get("role") == "user")
    assert "steered" not in (row.get("meta") or {})


# ── learning reads it as hers ──────────────────────────────────────────────────────────────


@pytest.fixture
def memory(tmp_path, monkeypatch) -> MemoryService:
    """The memory the turn learns into, with its record store wired."""
    store = VectorMemoryStore(db_path=tmp_path / "memory.db")
    store.init()
    svc = MemoryService.over_vector_store(store)
    monkeypatch.setattr("personalclaw.memory_service.service_for", lambda _provider: svc)
    # The forked skill review would ask a model in the background; the paths read here are the
    # model-free captures.
    config_loader.config_dir()
    config_loader.config_path().write_text(
        json.dumps({"learning": {"skill_ladder": False}}), encoding="utf-8"
    )
    return svc


@pytest.mark.asyncio
async def test_learning_reads_her_steer_as_her_own_words(tmp_path, memory):
    correction = "that's not what I asked, use tabs"
    world = _World(tmp_path, FIRST, SECOND)
    session = await _a_turn_she_steers(world, (correction, STEER_TS))

    # The turn's own review learns the correction she steered it with, in her words.
    lessons = [str(json.loads(row["value_json"])) for row in memory.get_lessons()]
    assert any(correction in lesson for lesson in lessons), lessons
    assert not any("Steering" in lesson for lesson in lessons)

    # The consolidation pass, which reads the chat's file, reads her steer as hers.
    kept = world.log.read_messages(persisted_history_key(world.log, session.key))
    lines = [consolidation_line(m) for m in kept]
    assert any(line.endswith(f"USER: {correction}") for line in lines), lines


# ── a steer the turn did not take is one row, written when it runs ─────────────────────────


@pytest.mark.asyncio
async def test_a_steer_past_the_turns_cap_runs_next_as_one_row(tmp_path):
    """One turn takes at most a few steers. The rest wait in the queue for the next turn, and
    each is written once: the ones the turn took where it took them, the one it did not when it
    runs."""
    steers = [(f"Also check case {n}.", f"2026-10-04T09:30:0{n}+00:00") for n in range(5)]
    assert len(steers) == _MAX_STEERS_PER_TURN + 1, "the fixture must overflow the cap"
    world = _World(tmp_path, FIRST, SECOND, "Case 4 is fine too.")
    session = await _a_turn_she_steers(world, *steers)

    taken, owed = steers[:_MAX_STEERS_PER_TURN], steers[_MAX_STEERS_PER_TURN]
    assert _conversation(session.messages) == [
        ("user", ASKED),
        ("assistant", FIRST),
        *[("user", text) for text, _ts in taken],
        ("assistant", SECOND),
        ("user", owed[0]),
        ("assistant", "Case 4 is fine too."),
    ]
    # The page's queue strip is told the waiting message is the steer she sent at that time.
    pushes = world.frames_of("queue_push")
    assert [(p["content"], p["steer_ts"]) for p in pushes] == [owed]


@pytest.mark.asyncio
async def test_a_steer_an_agent_cli_took_and_then_refused_is_one_row(tmp_path):
    """An agent CLI that takes mid-turn prompts may refuse one after it was written to it. Its row
    is in the chat already, so it runs next without a second one, and the chat says why it is
    answered on its own."""
    from personalclaw.dashboard import running_turn
    from personalclaw.dashboard.chat_runner import run_chat
    from personalclaw.dashboard.state import _ChatSession
    from personalclaw.hooks import ToolHookResult
    from personalclaw.llm.events import EVENT_STEER

    state = DashboardState(
        sessions=MagicMock(count=0),
        start_time=0.0,
        conversation_log=ConversationLog(base_dir=tmp_path / "history"),
    )
    sessions = state.sessions
    sessions.get_pid = MagicMock(return_value=None)
    sessions.record_failure = AsyncMock()
    sessions.check_context_usage = MagicMock()
    sessions.set_steer_drains = MagicMock(return_value=[])
    sessions.add_steer = MagicMock(return_value=True)
    frames: list[tuple[str, dict]] = []
    state.broadcast_ws = lambda kind, data=None: frames.append((kind, dict(data or {})))
    state.push_sessions_update = MagicMock()
    cb = MagicMock()
    cb.hooks.on_tool_call.return_value = ToolHookResult.allow()
    cb.build_message.side_effect = lambda text, *a, **k: (text, None)
    cb.conversation_log = None
    state.context_builder = cb
    hooks = MagicMock()
    hooks.fire_for_ids = AsyncMock(return_value=[])
    state._hook_store = hooks
    session = _ChatSession("chat-9-refused")
    session._trust = True
    state._sessions[session.key] = session
    refused: list[str] = []

    async def _turn(message: str):
        if message == ASKED:
            yield AgentEvent(kind=EVENT_TEXT_CHUNK, text=FIRST)
            # She sends her steer while it writes; the agent CLI takes it at its next boundary…
            assert running_turn.steer(state, session, STEER, ts=STEER_TS)
            yield AgentEvent(kind=EVENT_STEER, text=STEER)
            refused.append(STEER)  # …and then answers that it cannot service it.
            yield AgentEvent(kind=EVENT_TEXT_CHUNK, text=SECOND)
        else:
            yield AgentEvent(kind=EVENT_TEXT_CHUNK, text=f"Answering: {message}")
        yield AgentEvent(kind=EVENT_COMPLETE, stop_reason="end_turn")

    def _owed() -> list[str]:
        out = list(refused)
        refused.clear()
        return out

    client = MagicMock()
    client.provider_id = "acp:test-cli"
    client.stream = MagicMock(side_effect=_turn)
    client.set_steer_source = MagicMock(return_value=True)
    client.undelivered_steers = MagicMock(side_effect=_owed)
    sessions.get_or_create = AsyncMock(return_value=(client, False, False))

    session.append("user", ASKED, "msg msg-u")
    with patch("personalclaw.dashboard.chat_runner.sel", MagicMock()):
        await run_chat(state, session, ASKED)
        for _ in range(500):
            if session.task is None and not session._queue:
                break
            await asyncio.sleep(0.01)

    assert [m["content"] for m in session.messages if m.get("role") == "user"] == [ASKED, STEER]
    assert _conversation(session.messages) == [
        ("user", ASKED),
        ("assistant", FIRST),
        ("user", STEER),
        ("assistant", SECOND),
        ("assistant", f"Answering: {STEER}"),
    ]
    notices = [m["content"] for m in session.messages if m.get("role") == "notice"]
    assert notices == [running_turn.STEER_NOT_TAKEN_NOTICE]
    # Its bubble is the one the turn wrote when it took it; the queue adds none.
    assert [d.get("steer") for k, d in frames if k == "chat_user_message"] == [True]


class _Look(ToolProvider):
    """A read the model calls once."""

    @property
    def name(self) -> str:
        return "look"

    @property
    def display_name(self) -> str:
        return "Look"

    async def list_tools(self):
        return [
            ToolDefinition(
                name="look",
                description="Read the config.",
                parameters={"type": "object"},
                risk_level=RiskLevel.SAFE,
            )
        ]

    async def invoke(self, tool_name, arguments):
        return ToolResult(success=True, output="garden.toml: 12 lines")


class _LooksFirst(_Model):
    """Calls ``look`` first, and pauses on that call until her steer is in; then answers."""

    async def complete(self, messages, *, tools=None, model=None, reasoning_effort=""):
        self.requests.append([dict(m) for m in messages])
        if len(self.requests) == 1:
            yield AgentEvent(kind=EVENT_TOOL_CALL, tool_call_id="c1", title="look", tool_input="{}")
            self.answering.set()
            await self.go_on.wait()
            yield AgentEvent(kind=EVENT_COMPLETE, stop_reason="tool_use")
            return
        yield AgentEvent(kind=EVENT_TEXT_CHUNK, text=self.replies.pop(0))
        yield AgentEvent(kind=EVENT_COMPLETE)


@pytest.mark.asyncio
async def test_a_steer_taken_after_a_tool_call_is_her_row_after_that_call(tmp_path):
    world = _World(tmp_path, SECOND)
    world.model = _LooksFirst(SECOND)
    look = _Look()

    def factory(session_key=None, **_kwargs):
        return NativeAgentRuntime(
            definition=AgentRuntimeDefinition(
                name="PersonalClaw", provider="native", model="scripted"
            ),
            model_provider=world.model,
            tool_providers=[look],
            cwd=tmp_path,
        )

    world.sessions._provider_factory = factory
    chat = world.state.get_or_create_session("chat-5-look")
    chat._trust = True  # the read runs without a card, so the turn goes straight on
    async with world.client() as client:
        await world.send(client, ASKED, session=chat.key)
        await asyncio.wait_for(world.model.answering.wait(), timeout=10)
        assert await world.steer(client, chat.key, STEER) == {"ok": True, "steered": True}
        world.model.go_on.set()
        session = await world.settled(chat.key)

    assert STEER in json.dumps(world.model.requests[1])
    rows = [(m["role"], m["content"]) for m in session.messages if m.get("role") != "system"]
    assert [role for role, _ in rows] == ["user", "tool", "user", "assistant"], rows
    assert rows[2] == ("user", STEER) and rows[3] == ("assistant", SECOND)


# ── a turn with no steer ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_turn_with_no_steer_is_unchanged(tmp_path):
    world = _World(tmp_path, FIRST)
    world.model.go_on.set()
    async with world.client() as client:
        key = (await world.send(client, ASKED))["session"]
        session = await world.settled(key)

    assert _conversation(session.messages) == [("user", ASKED), ("assistant", FIRST)]
    assert len(world.model.requests) == 1
    assert world.frames_of("chat_user_message") == []
    assert world.frames_of("queue_push") == []
    assert not any((m.get("meta") or {}).get("steered") for m in session.messages)

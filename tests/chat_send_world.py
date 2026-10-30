"""One gateway's chat surface for the tests of a send: the real handler, turn engine and sessions.

``POST /api/chat`` is driven through the real chat handler (``dashboard.chat.api_chat``), the real
turn engine (``chat_runner.run_chat``), the real ``SessionManager`` and a native runtime per chat,
answered by :class:`Model`. Its first answer can pause after its first words (``hold``), the way an
answer is still being written when the owner sends again. A second :class:`World` over the same
home is the gateway after a restart: it knows a chat only from its file.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from aiohttp.test_utils import TestClient, TestServer
from chat_test_helpers import _api_app

from personalclaw import ids
from personalclaw.agents.native.runtime import NativeAgentRuntime
from personalclaw.agents.provider import AgentRuntimeDefinition
from personalclaw.config import loader as config_loader
from personalclaw.config.loader import AppConfig
from personalclaw.context import ContextBuilder
from personalclaw.dashboard.api_version_gate import api_version_middleware
from personalclaw.dashboard.chat import api_chat, api_chat_session_edit_resend
from personalclaw.dashboard.chat_utils import persisted_history_key
from personalclaw.dashboard.state import DashboardState
from personalclaw.history import ConversationLog
from personalclaw.llm.events import EVENT_COMPLETE, EVENT_TEXT_CHUNK, AgentEvent
from personalclaw.memory import MemoryStore
from personalclaw.session import SessionManager
from personalclaw.skills import SkillsLoader
from personalclaw.turn_source import starts_a_turn


class Model:
    """A model that answers each request with its next reply (``ok`` once they run out) and calls
    no tool. While it is held (*hold*, :meth:`hold`), an answer stops after its first words until
    ``go_on`` is set."""

    supports_tools = True
    _model = "scripted"

    def __init__(self, *replies: str, hold: bool = False) -> None:
        self.replies = list(replies)
        self.requests: list[list[dict]] = []
        self.answering = asyncio.Event()
        self.go_on = asyncio.Event()
        if not hold:
            self.go_on.set()

    async def complete(self, messages, *, tools=None, model=None, reasoning_effort=""):
        self.requests.append([dict(m) for m in messages])
        yield AgentEvent(kind=EVENT_TEXT_CHUNK, text=self.replies.pop(0) if self.replies else "ok")
        if not self.go_on.is_set():
            self.answering.set()
            await self.go_on.wait()
        yield AgentEvent(kind=EVENT_COMPLETE)

    async def cancel(self, *, wait_ack_timeout: float = 0.0) -> str:
        self.go_on.set()
        return "acked"

    def hold(self) -> None:
        """Hold the next answer after its first words, until ``go_on`` is set."""
        self.answering.clear()
        self.go_on.clear()

    def read(self) -> str:
        """Everything the model was sent, across every request."""
        return json.dumps(self.requests)


class World:
    """The chat surface over the home *tmp_path*'s history, answered by :class:`Model`, each chat on
    a *runtime* (the native runtime, or a test's subclass of it)."""

    def __init__(
        self,
        tmp_path: Path,
        *replies: str,
        hold: bool = False,
        runtime: type[NativeAgentRuntime] = NativeAgentRuntime,
    ) -> None:
        self.model = Model(*replies, hold=hold)

        def factory(session_key=None, **_kwargs):
            return runtime(
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
    async def client(self, *, versioned: bool = False):
        """A client of the chat surface; *versioned* puts the gateway's API version check in front
        of it (``api_version_gate``), as every gateway has."""
        app = _api_app(self.state)
        if versioned:
            app.middlewares.append(api_version_middleware())
        app.router.add_post("/api/chat", api_chat)
        app.router.add_post(
            "/api/chat/sessions/{session}/edit-resend", api_chat_session_edit_resend
        )
        async with TestClient(TestServer(app)) as client:
            with (
                patch("personalclaw.dashboard.chat_runner.sel", MagicMock()),
                patch("personalclaw.dashboard.chat_handlers.sel", MagicMock()),
            ):
                yield client
        await self.sessions.close_all()

    async def post(
        self, client: TestClient, message: str, *, ws: bool = True, **body: Any
    ) -> tuple[int, Any]:
        """``POST /api/chat``: the answer's status, and its body (the events of a stream)."""
        resp = await client.post(
            "/api/chat?ws=1" if ws else "/api/chat", json={"message": message, **body}
        )
        if resp.content_type == "text/event-stream":
            return resp.status, sse_events(await resp.text())
        return resp.status, await resp.json()

    async def send(self, client: TestClient, message: str, **body: Any) -> dict:
        """A send the chat takes in: its receipt."""
        status, answer = await self.post(client, message, **body)
        assert status == 200, answer
        return answer

    async def settled(self, key: str):
        """The chat, once it has answered everything it was sent."""
        session = self.state._sessions[key]
        for _ in range(1500):
            task = session.task
            if (task is None or task.done()) and not session._queue:
                return session
            await asyncio.sleep(0.01)
        raise AssertionError(f"the chat never settled: {session.messages}")

    def file_rows(self, key: str) -> list[dict]:
        """The chat's rows as its file holds them."""
        return self.log.read_messages(persisted_history_key(self.log, key))

    def frames_of(self, kind: str) -> list[dict]:
        return [data for k, data in self.frames if k == kind]


def configure(settings: dict) -> None:
    """Write *settings* as this test home's config file."""
    config_loader.config_dir()
    config_loader.config_path().write_text(json.dumps(settings), encoding="utf-8")


def new_id() -> str:
    """A message id, as a client mints one."""
    return ids.new_id("msg")


def turn_starts(rows: list[dict]) -> list[dict]:
    """The rows of a chat that each started a turn: how many times something ran."""
    return [row for row in rows if starts_a_turn(row)]


def rows_of(rows: list[dict], words: str) -> list[dict]:
    """The owner's rows of a chat that hold *words*."""
    return [r for r in rows if r.get("role") == "user" and words in str(r.get("content", ""))]


def sse_events(text: str) -> list[tuple[str, Any]]:
    """A stream's events as ``(event, data)``, ``message`` for one the stream gave no name."""
    events: list[tuple[str, Any]] = []
    for block in text.split("\n\n"):
        name, data = "message", []
        for line in block.splitlines():
            if line.startswith("event: "):
                name = line[len("event: ") :]
            elif line.startswith("data: "):
                data.append(line[len("data: ") :])
        if data:
            joined = "\n".join(data)
            try:
                events.append((name, json.loads(joined)))
            except ValueError:
                events.append((name, joined))
    return events

"""A fixed conversation driven through the real turn engine, and what its model is handed.

Shared by ``tests/test_reply_style_reaches_the_model.py`` and the script that rewrites its
snapshots (``tests/reply_style_snapshots.py``). It imports nothing of the reply-style module, so
the same conversation can be replayed on any tree and its requests compared.

The model is a recorder: it answers "ok" and keeps every message list it was handed. What a
reply style changes is what the model is ASKED, so that is what is read; a recorded or scripted
model cannot show a reply changing.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from personalclaw.agents.native.runtime import NativeAgentRuntime
from personalclaw.agents.provider import AgentRuntimeDefinition
from personalclaw.config import loader as config_loader
from personalclaw.context import USER_REQUEST_MARKER, ContextBuilder
from personalclaw.dashboard.chat_runner import run_chat
from personalclaw.dashboard.state import DashboardState
from personalclaw.history import ConversationLog
from personalclaw.llm.events import EVENT_COMPLETE, EVENT_TEXT_CHUNK, AgentEvent
from personalclaw.memory import MemoryStore
from personalclaw.skills import SkillsLoader

AGENT = "PersonalClaw"
#: The fixed conversation: two turns, the second one a follow-up.
FIRST = "What does the nightly backup job do?"
SECOND = "And what if it fails?"

SNAPSHOTS = Path(__file__).resolve().parent / "fixtures" / "reply_style"


class RecordingModel:
    supports_tools = True
    _model = "recorder"

    def __init__(self) -> None:
        self.requests: list[list[dict]] = []

    async def complete(self, messages, *, tools=None, model=None, reasoning_effort=""):
        self.requests.append([dict(m) for m in messages])
        yield AgentEvent(kind=EVENT_TEXT_CHUNK, text="ok")
        yield AgentEvent(kind=EVENT_COMPLETE)

    async def cancel(self, *, wait_ack_timeout: float = 0.0) -> str:
        return "acked"


class Conversation:
    """One chat on the default agent, over a home whose config is *config*."""

    def __init__(self, tmp_path: Path, *, agent: dict | None = None, config: dict | None = None):
        data: dict[str, Any] = {
            "agent": {"bot_name": "Aide"},
            "dashboard": {"user_name": "Robin"},
            "default_agent": AGENT,
            "agents": {
                AGENT: {
                    "provider": "native",
                    "system_prompt": "",
                    "source": "builtin",
                    **(agent or {}),
                },
            },
        }
        for section, values in (config or {}).items():
            data.setdefault(section, {}).update(values)
        config_loader.config_dir()
        config_loader.config_path().write_text(json.dumps(data), encoding="utf-8")
        self.tmp = tmp_path
        self.model = RecordingModel()
        self._turns = 0

    async def start(self) -> "Conversation":
        runtime = NativeAgentRuntime(
            definition=AgentRuntimeDefinition(name=AGENT, provider="native", model="recorder"),
            model_provider=self.model,
            tool_providers=[],
            cwd=self.tmp,
        )
        await runtime.start()
        runtime.set_approval_policy("auto")

        async def get_or_create(*_a: Any, **_k: Any):
            self._turns += 1
            return runtime, self._turns == 1, False

        sessions = MagicMock(count=0)
        sessions._sessions = {}
        sessions.get_pid = MagicMock(return_value=None)
        sessions.get_channel_link = MagicMock(return_value=(None, None))
        sessions.get_or_create = AsyncMock(side_effect=get_or_create)
        sessions.record_failure = AsyncMock()
        sessions.reset = AsyncMock()
        log = ConversationLog(base_dir=self.tmp / "sessions")
        self.state = DashboardState(sessions=sessions, start_time=0.0, conversation_log=log)
        self.state.context_builder = ContextBuilder(
            memory=MemoryStore(workspace=self.tmp / "ws"),
            skills=SkillsLoader(skills_path=self.tmp / "skills", install_builtins=False),
            conversation_log=log,
        )
        self.state._hook_store = None
        self.state.broadcast_ws = lambda *a, **k: None
        self.state.push_sessions_update = MagicMock()
        self.session = self.state.get_or_create_session("chat-reply-style")
        self.session.agent = AGENT
        return self

    async def send(self, text: str) -> None:
        self.session.append("user", text, "msg msg-u")
        with patch("personalclaw.dashboard.chat_runner.sel", MagicMock()):
            await run_chat(self.state, self.session, text)

    async def both_turns(self) -> list[str]:
        await self.send(FIRST)
        await self.send(SECOND)
        return [request_part(r) for r in self.model.requests]


def request_part(request: list[dict]) -> str:
    """What a request says from its request marker on: the turn's own message and whatever the
    platform appended to it. Nothing in it depends on the machine, the date or the home."""
    content = str(request[-1]["content"])
    return content[content.index(USER_REQUEST_MARKER) :]


def appended_to(part: str, said: str) -> str:
    """What the platform appended to the message *said*, up to the widget guidance."""
    after = part[part.index(said) + len(said) :]
    return after[: after.index("[WIDGETS]")] if "[WIDGETS]" in after else after


_DATE = re.compile(r"^\[CURRENT DATE\] .*$", re.M)


def normalized(requests: list[list[dict]], tmp_path: Path) -> list[list[str]]:
    """Every message of every request, with what depends on the machine taken out: the test's
    folder, the home folder and the date line."""
    home = str(Path.home())
    return [
        [
            _DATE.sub(
                "[CURRENT DATE] <date>",
                str(m.get("content") or "").replace(str(tmp_path), "<tmp>").replace(home, "<home>"),
            )
            for m in request
        ]
        for request in requests
    ]


#: The snapshot cases: each style, alone and under an identity that carries a persona.
CASES: tuple[tuple[str, str], ...] = tuple(
    (style, theme)
    for style in ("default", "plain", "terse", "conversational")
    for theme in ("", "retro-terminal")
)


def case_name(style: str, theme: str) -> str:
    return f"{style}+{theme}" if theme else style


async def record(tmp_path: Path, style: str, theme: str) -> list[list[str]]:
    """The fixed conversation under *style* and *theme*, as its model was handed it."""
    chat = await Conversation(tmp_path).start()
    chat.session.reply_style = style
    chat.session.color_theme = theme
    await chat.both_turns()
    return normalized(chat.model.requests, tmp_path)

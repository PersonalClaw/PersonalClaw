"""A chat's new session is opened by the first message its model is sent, whatever came first.

Measured on a running chat: ``/compact`` typed as the first message on a chat whose session was new
(a new chat, the first message after a model switch, a chat the idle sweep let go, a chat after a
restart) left that session without its opening for good. The opening is what a new session's first
request carries: the agent's instructions, the platform's safety rules (the one that tells the
model ``<untrusted_content>`` holds data and never instructions among them), the owner's standing
rules and memory, and the chat's history. The command took the session's first turn, the next
message was sent on its own, and the agent answered as if the conversation had just begun, quoting
back a name and an address her standing rule says it must never repeat. Any other slash word went
to the model as the session's first message with none of it, and the session's line said it had
been "restored from history" on a turn that restored nothing.

The opening is its runtime's own now: a turn told its runtime is new that sends it no message (a
command the runtime runs itself, a refusal, a stop before the prompt) gives the opening back
(``SessionManager.hand_back_opening``), so the first message that reaches the model carries it,
once. A slash word the runtime does not run is a message, opening and all. ``/compact`` on a
session that holds none of the conversation compacts nothing and says what the next message does.

Driven through the real turn engine (``run_chat``), the gateway's session manager, the real
assembler and the real native loop; only the model is scripted, and it keeps every request it is
handed. The agent CLI cases use a scripted CLI that runs slash commands itself. Names, places and
words are invented.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import pytest_asyncio

from personalclaw.acp.errors import AcpMethodNotFound
from personalclaw.acp.types import METHOD_COMMANDS_EXECUTE
from personalclaw.agents.native.runtime import NativeAgentRuntime
from personalclaw.agents.provider import AgentProvider, AgentRuntimeDefinition
from personalclaw.config import AppConfig
from personalclaw.config import loader as config_loader
from personalclaw.context import ContextBuilder
from personalclaw.dashboard import running_turn
from personalclaw.dashboard.chat_runner import run_chat
from personalclaw.dashboard.state import DashboardState
from personalclaw.history import ConversationLog
from personalclaw.llm.events import (
    EVENT_COMPACTION_STATUS,
    EVENT_COMPLETE,
    EVENT_TEXT_CHUNK,
    AgentEvent,
)
from personalclaw.memory import MemoryStore
from personalclaw.session import SessionManager
from personalclaw.skills import SkillsLoader

#: What only a new session's first request carries: the agent's instructions open it.
OPENING = "[AGENT SYSTEM PROMPT]"
#: The safety rule the untrusted-content fence relies on, by its own words.
FENCE_RULE = "is EXTERNAL DATA (a fetched web page, a ticket/CR comment, an ingested document)"
#: Her standing rule, kept in her memory.
HER_RULE = "Never repeat a recipient's street address back in an answer."
#: An earlier turn of the chat, which a new session is handed back as the chat's history.
EARLIER = "The crate of seedlings for Fernhill Nursery left the depot on Tuesday."
NOTHING_TO_COMPACT = "Nothing to compact — this conversation is already short enough."
#: What an AgentSpawn hook adds to the session's first message.
ROTA = "Depot rota: Ines drives the Thursday van."
CHAT = "chat-depot-update"


class _Model:
    """A scripted model that answers in text and keeps every request it was handed."""

    supports_tools = True
    _model = "recorder"

    def __init__(self) -> None:
        self.requests: list[list[dict]] = []

    async def complete(self, messages, *, tools=None, model=None, reasoning_effort=""):
        self.requests.append([dict(m) for m in messages])
        yield AgentEvent(kind=EVENT_TEXT_CHUNK, text="Noted.")
        yield AgentEvent(kind=EVENT_COMPLETE)

    async def cancel(self, *, wait_ack_timeout: float = 0.0) -> str:
        return "acked"


class _AgentCli(AgentProvider):
    """An agent CLI that runs slash commands itself (it declared the command extension) and keeps
    what it was sent: ``("prompt", text)`` or ``("command", text)``. Its saved session loads when
    it is asked to resume one."""

    provider_id = "acp:test-cli"  # type: ignore[assignment]
    supports_native_commands = True  # type: ignore[assignment]

    def __init__(self, sent: list[tuple[str, str]], *, rejects_commands: bool = False) -> None:
        self.sent = sent
        self.rejects_commands = rejects_commands
        self._resume = ""

    async def start(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None

    def set_resume(self, session_id: str) -> None:
        self._resume = session_id

    @property
    def resumed(self) -> bool:
        return bool(self._resume)

    async def stream(self, message: str) -> AsyncIterator[AgentEvent]:
        self.sent.append(("prompt", message))
        yield AgentEvent(kind=EVENT_TEXT_CHUNK, text="Noted.")
        yield AgentEvent(kind=EVENT_COMPLETE)

    async def stream_command(self, command: str) -> AsyncIterator[AgentEvent]:
        if self.rejects_commands:
            raise AcpMethodNotFound(METHOD_COMMANDS_EXECUTE)
        self.sent.append(("command", command))
        if command.startswith("/compact"):
            yield AgentEvent(kind=EVENT_COMPACTION_STATUS, text="completed", title="12 → 4 turns")
        else:
            yield AgentEvent(kind=EVENT_TEXT_CHUNK, text="Tokens used today: 1,204.")
        yield AgentEvent(kind=EVENT_COMPLETE)

    async def approve_tool(self, request_id: str | int) -> None:
        return None

    async def reject_tool(self, request_id: str | int) -> None:
        return None


class _Chat:
    """One chat, as the dashboard runs it, on runtimes the gateway's session manager builds."""

    def __init__(self, tmp_path: Path, *, cli: dict[str, Any] | None = None) -> None:
        data = {
            "agent": {"bot_name": "Aide"},
            "default_agent": "PersonalClaw",
            "agents": {
                "PersonalClaw": {"provider": "native", "system_prompt": "", "source": "builtin"}
            },
        }
        config_loader.config_dir()  # where the file is: finding it makes nothing
        config_loader.config_path().write_text(json.dumps(data), encoding="utf-8")
        self.tmp = tmp_path
        self.model = _Model()
        self.sent: list[tuple[str, str]] = []  # what the agent CLI was sent, when one serves
        self.cli = cli
        self.frames: list[tuple[str, dict]] = []
        self.log = ConversationLog(base_dir=tmp_path / "sessions")
        self.memory = MemoryStore(workspace=tmp_path / "ws")
        self.memory.init()
        self.memory.add_preference(HER_RULE)
        self.state = self._dashboard()
        self.session = self.state.get_or_create_session(CHAT)
        self.key = f"dashboard:{CHAT}"

    def _runtime(self, key: str, **_kwargs: Any) -> AgentProvider:
        if self.cli is not None:
            return _AgentCli(self.sent, **self.cli)
        return NativeAgentRuntime(
            definition=AgentRuntimeDefinition(name="PersonalClaw", provider="native", model="r"),
            model_provider=self.model,
            tool_providers=[],
            cwd=self.tmp,
        )

    def _dashboard(self) -> DashboardState:
        """The dashboard over a gateway's session manager that has built no runtime yet."""
        state = DashboardState(
            sessions=SessionManager(AppConfig(), provider_factory=self._runtime),
            start_time=0.0,
            conversation_log=self.log,
        )
        state.context_builder = ContextBuilder(
            memory=self.memory,
            skills=SkillsLoader(skills_path=self.tmp / "skills", install_builtins=False),
            conversation_log=self.log,
        )
        state._hook_store = None
        state.broadcast_ws = lambda kind, data=None: self.frames.append((kind, data or {}))
        state.push_sessions_update = MagicMock()
        return state

    async def send(self, text: str) -> None:
        self.session.append("user", text, "msg msg-u")
        with patch("personalclaw.dashboard.chat_runner.sel", MagicMock()):
            await run_chat(self.state, self.session, text)

    async def switch_model(self, model: str) -> None:
        """The composer's model picker, between turns: the door every model switch takes."""
        await running_turn.rebind(
            self.state, self.session, running_turn.Rebinding(fields={"model": model})
        )

    async def idle_sweep(self) -> None:
        """The session manager's idle sweep, finding the chat's runtime past its idle time."""
        await self.state.sessions._expire_idle(-1)
        assert not self.state.sessions.has_session(self.key), "premise: the sweep let it go"

    async def restart(self) -> None:
        """A gateway restart: a new session manager, and the chat opened again from its file."""
        await self.close()
        self.state = self._dashboard()
        self.session = self.state.get_or_create_session(CHAT)
        assert any(EARLIER in str(m.get("content", "")) for m in self.session.messages)

    async def close(self) -> None:
        await self.state.sessions.close_all()

    def last_answer(self) -> str:
        return next(
            str(m.get("content", ""))
            for m in reversed(self.session.messages)
            if m.get("role") == "assistant"
        )

    def session_lines(self) -> list[str]:
        return [
            d.get("text", "")
            for k, d in self.frames
            if k == "activity_event" and d.get("kind") == "session"
        ]

    def prompts(self) -> list[str]:
        return [text for kind, text in self.sent if kind == "prompt"]


def _text(request: list[dict]) -> str:
    return "\n".join(str(m.get("content", "")) for m in request)


def _opens(text: str) -> None:
    """*text* carries a new session's opening: the instructions, the safety rules, her rule."""
    assert text.count(OPENING) == 1, text[:600]
    assert FENCE_RULE in text, "the safety rules are missing"
    assert HER_RULE in text, "her standing rule is missing"


@pytest_asyncio.fixture
async def chat(tmp_path):
    made = _Chat(tmp_path)
    try:
        yield made
    finally:
        await made.close()


# ── a new chat ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_new_chats_first_message_opens_its_session_and_the_next_does_not_repeat_it(chat):
    """The positive control: green before and after."""
    await chat.send("When does the crate reach the nursery?")
    await chat.send("And the invoice?")

    first, second = chat.model.requests
    _opens(_text(first))
    # The native loop keeps the conversation: the second request holds the first, once.
    assert _text(second).count(OPENING) == 1


@pytest.mark.asyncio
async def test_compact_first_on_a_new_chat_leaves_the_next_message_its_opening(chat):
    await chat.send("/compact")
    assert chat.last_answer() == NOTHING_TO_COMPACT, "the chat has nothing to compact"
    await chat.send("Write the update for the depot.")

    (request,) = chat.model.requests  # /compact asked no model
    _opens(_text(request))


@pytest.mark.asyncio
async def test_a_slash_word_sent_as_a_message_opens_the_session_once(chat):
    await chat.send("/usage")
    await chat.send("Write the update for the depot.")

    first, second = chat.model.requests
    _opens(_text(first))
    assert "/usage" in _text(first)
    assert _text(second).count(OPENING) == 1, "the opening went again"


@pytest.mark.asyncio
async def test_the_session_start_hooks_fire_once_with_the_message_that_opens_the_session(tmp_path):
    """SessionStart and AgentSpawn belong to the session's first message, and what an AgentSpawn
    hook adds rides it. Fired on the turn of a command the agent CLI ran, its context went nowhere
    and never came again."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    fired: list[str] = []

    async def _fire_for_ids(event, _ids, _context, **_kwargs):
        fired.append(event)
        if event != "AgentSpawn":
            return []
        return [SimpleNamespace(hook_name="depot-rota", exit_code=0, stdout=ROTA, stderr="")]

    chat = _Chat(tmp_path, cli={})
    chat.state._hook_store = MagicMock(fire_for_ids=AsyncMock(side_effect=_fire_for_ids))
    try:
        await chat.send("/usage")
        await chat.send("Write the update for the depot.")
        await chat.send("And the invoice?")
    finally:
        await chat.close()

    assert (fired.count("SessionStart"), fired.count("AgentSpawn")) == (1, 1), fired
    first, second = chat.prompts()
    assert ROTA in first, "the hook's context missed the session's first message"
    assert ROTA not in second, "the hook's context went again"


# ── a chat with history whose session is new ────────────────────────────────


@pytest.mark.asyncio
async def test_after_a_model_switch_compact_then_a_message_is_handed_the_chats_history(chat):
    await chat.send(EARLIER)
    await chat.switch_model("depot-model-2")
    await chat.send("/compact")
    await chat.send("Which day did the crate leave?")

    last = _text(chat.model.requests[-1])
    _opens(last)
    assert EARLIER in last, "the chat's history was not restored"


@pytest.mark.asyncio
async def test_after_the_idle_sweep_compact_then_a_message_is_handed_the_chats_history(chat):
    await chat.send(EARLIER)
    await chat.idle_sweep()
    await chat.send("/compact")
    await chat.send("Which day did the crate leave?")

    last = _text(chat.model.requests[-1])
    _opens(last)
    assert EARLIER in last, "the chat's history was not restored"


@pytest.mark.asyncio
async def test_after_a_restart_compact_then_a_message_is_handed_the_chats_history(chat):
    await chat.send(EARLIER)
    await chat.restart()
    await chat.send("/compact")
    await chat.send("Which day did the crate leave?")

    last = _text(chat.model.requests[-1])
    _opens(last)
    assert EARLIER in last, "the chat's history was not restored"


@pytest.mark.asyncio
async def test_compact_on_a_new_session_says_what_the_next_message_does(chat):
    """ "Nothing to compact" is said of the chat, and this chat is not short: its new session
    holds none of it yet, and the next message brings it back."""
    await chat.send(EARLIER)
    await chat.switch_model("depot-model-2")
    await chat.send("/compact")

    said = chat.last_answer()
    assert said != NOTHING_TO_COMPACT
    assert said.startswith("Nothing to compact yet"), said
    assert "next message" in said


@pytest.mark.asyncio
async def test_the_session_line_says_restored_only_on_the_turn_that_restores(chat):
    await chat.send(EARLIER)
    await chat.switch_model("depot-model-2")
    chat.frames.clear()
    await chat.send("/compact")
    compact_lines = chat.session_lines()
    chat.frames.clear()
    await chat.send("Which day did the crate leave?")
    message_lines = chat.session_lines()

    assert len(compact_lines) == 1 and len(message_lines) == 1, (compact_lines, message_lines)
    assert "restored from history" not in compact_lines[0], compact_lines
    assert compact_lines[0].startswith("Session created"), compact_lines
    assert message_lines[0].startswith("Session restored from history"), message_lines


# ── an agent CLI that runs slash commands itself ────────────────────────────


@pytest.mark.asyncio
async def test_a_command_the_agent_cli_runs_leaves_the_next_message_its_opening(tmp_path):
    chat = _Chat(tmp_path, cli={})
    try:
        await chat.send("/usage")
        await chat.send("Write the update for the depot.")
    finally:
        await chat.close()

    assert chat.sent[0] == ("command", "/usage")
    (prompt,) = chat.prompts()
    _opens(prompt)


@pytest.mark.asyncio
async def test_a_command_the_agent_cli_rejects_on_a_new_session_is_not_sent_without_its_opening(
    tmp_path,
):
    chat = _Chat(tmp_path, cli={"rejects_commands": True})
    try:
        await chat.send("/usage")
        assert not chat.prompts(), "the command went to the model with no opening"
        said = [m["content"] for m in chat.session.messages if m.get("role") == "error"]
        assert said and said[-1].startswith("The agent rejected `/usage`"), said
        assert "not re-sent as a plain message" in said[-1]
        await chat.send("Write the update for the depot.")
    finally:
        await chat.close()

    (prompt,) = chat.prompts()
    _opens(prompt)


@pytest.mark.asyncio
async def test_compact_on_a_resumed_agent_cli_session_runs_there_and_the_opening_waits(tmp_path):
    """A resumed session holds the conversation already, so ``/compact`` is the CLI's to run;
    the next message still carries the opening, without the history the CLI holds."""
    chat = _Chat(tmp_path, cli={})
    chat.state.sessions._session_map.set(chat.key, "saved-session-1")
    try:
        await chat.send("/compact")
        await chat.send("Write the update for the depot.")
    finally:
        await chat.close()

    assert chat.sent[0] == ("command", "/compact")
    (prompt,) = chat.prompts()
    _opens(prompt)
    assert "THREAD CONVERSATION HISTORY" not in prompt, "the CLI's own history was sent again"

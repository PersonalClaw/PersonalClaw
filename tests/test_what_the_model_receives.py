"""What a chat turn actually SENDS the model — recorded at the model boundary.

Every test here drives the real turn engine (``run_chat``) through the real assembler
(``ContextBuilder``) into a real ``NativeAgentRuntime``, and reads the request the model
provider was handed. Nothing between the user's message and the model is mocked, so each
assertion is about the bytes a model receives — the only place the four defects below
were ever visible:

* the saved assistant name and the prompt bound in Settings → Prompts never arrived
  (the default agent's seeded profile prompt replaced the binding on every turn);
* the user's own name never arrived;
* a brand-new chat sent its first message twice (once as "previous history");
* a new session carried another session's messages ("[Other chat tabs]").
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from personalclaw.agents.native.runtime import NativeAgentRuntime
from personalclaw.agents.provider import AgentRuntimeDefinition
from personalclaw.config import loader as config_loader
from personalclaw.context import ContextBuilder
from personalclaw.dashboard.chat_persistence import save_session_to_history
from personalclaw.dashboard.chat_runner import run_chat
from personalclaw.dashboard.state import DashboardState
from personalclaw.history import ConversationLog
from personalclaw.llm.events import EVENT_COMPLETE, EVENT_TEXT_CHUNK, AgentEvent
from personalclaw.memory import MemoryStore
from personalclaw.skills import SkillsLoader

BOT = "Chlos Aide"
OWNER = "Maya R. Chen"
BOUND_MARKER = "BOUND-PROMPT-MARKER-7731"
# The text every install seeded before the fix persisted as the default agent's own
# prompt. Written into config.json below to model such an install exactly.
SEEDED_LITERAL = (
    "You are PersonalClaw, a helpful personal AI agent running locally for the "
    "user. You can read and write files in the user's workspace, run code, "
    "search the web, manage tasks and memory, and use the tools available to "
    "you. Be concise and direct. Prefer doing the work over describing it. When "
    "a task needs a tool, use it; when you are unsure, ask. Respect the user's "
    "approval prompts before taking consequential actions."
)


class _RecordingModel:
    """A ModelProvider that records every request and answers one short line."""

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


def _write_config(agent_profile: dict | None, *, voice: str = "") -> None:
    """An install's config.json: the saved names, and (optionally) the default agent's
    profile as an existing install persisted it."""
    data: dict = {"agent": {"bot_name": BOT}, "dashboard": {"user_name": OWNER}}
    if agent_profile is not None:
        profile = dict(agent_profile)
        if voice:
            profile["voice"] = voice
        data["agents"] = {"PersonalClaw": profile}
        data["default_agent"] = "PersonalClaw"
    config_loader.config_path().write_text(json.dumps(data), encoding="utf-8")


def _bind_chat_to_a_custom_prompt() -> None:
    """Settings → Prompts: "Prompt for Chat" → a user prompt using both saved names."""
    from personalclaw.prompt_providers.base import PromptTemplate
    from personalclaw.prompt_providers.registry import (
        _ensure_default_providers_registered,
        get_prompt_provider,
    )
    from personalclaw.providers.prompt_use_cases import save_active_prompts

    _ensure_default_providers_registered()
    get_prompt_provider("native").create_prompt(
        PromptTemplate(
            name="probe-chat",
            kind="system",
            content=f"You are {{{{bot_name}}}}, working for {{{{user_name}}}}. {BOUND_MARKER}",
        )
    )
    save_active_prompts({"chat": "native:probe-chat"})


async def _state(tmp_path: Path, *, is_new: bool = True) -> tuple[DashboardState, _RecordingModel]:
    model = _RecordingModel()
    runtime = NativeAgentRuntime(
        definition=AgentRuntimeDefinition(name="PersonalClaw", provider="native", model="recorder"),
        model_provider=model,
        tool_providers=[],
        cwd=tmp_path,
    )
    await runtime.start()
    runtime.set_approval_policy("auto")

    sessions = MagicMock(count=0)
    sessions._sessions = {}
    sessions.get_pid = MagicMock(return_value=None)
    sessions.get_channel_link = MagicMock(return_value=(None, None))
    sessions.get_or_create = AsyncMock(return_value=(runtime, is_new, False))
    sessions.record_failure = AsyncMock()
    log = ConversationLog(base_dir=tmp_path / "sessions")
    state = DashboardState(sessions=sessions, start_time=0.0, conversation_log=log)
    state.context_builder = ContextBuilder(
        memory=MemoryStore(workspace=tmp_path / "ws"),
        skills=SkillsLoader(skills_path=tmp_path / "skills", install_builtins=False),
        conversation_log=log,
    )
    state._hook_store = None
    state.broadcast_ws = MagicMock()
    state.push_sessions_update = MagicMock()
    return state, model


async def _send(state: DashboardState, session, text: str) -> None:
    """One turn exactly as the dashboard dispatches it: the user's bubble is appended to
    the session buffer FIRST (``api_chat``), then the turn runs."""
    session.append("user", text, "msg msg-u")
    with patch("personalclaw.dashboard.chat_runner.sel", MagicMock()):
        await run_chat(state, session, text)


def _sent(model: _RecordingModel) -> str:
    """Everything the FIRST model request of the turn carried, as one string."""
    assert model.requests, "the turn never reached the model"
    return "\n".join(str(m.get("content") or "") for m in model.requests[0])


# ── B8 / B12: the bound prompt, rendered with the saved names, is what is sent ──


@pytest.mark.asyncio
async def test_an_existing_install_sends_the_bound_chat_prompt_with_both_names(tmp_path):
    """The validator's install exactly: the default agent carries the seeded literal in
    config.json, the names are saved, and Chat is bound to another prompt."""
    _write_config({"provider": "native", "system_prompt": SEEDED_LITERAL, "source": "builtin"})
    _bind_chat_to_a_custom_prompt()
    state, model = await _state(tmp_path)
    session = state.get_or_create_session("probe-bound")

    await _send(state, session, "What is your name?")

    sent = _sent(model)
    assert BOUND_MARKER in sent, "the prompt bound for Chat never reached the model"
    assert f"You are {BOT}, working for {OWNER}." in sent
    assert "You are PersonalClaw, a helpful" not in sent


@pytest.mark.asyncio
async def test_a_fresh_install_sends_the_bundled_chat_prompt_under_the_saved_name(tmp_path):
    """No agents yet: the migration seeds the default agent. Unbound Chat serves the
    bundled prompt, whose opening names the assistant the user chose."""
    _write_config(None)
    state, model = await _state(tmp_path)
    session = state.get_or_create_session("probe-fresh")

    await _send(state, session, "Hello there")

    sent = _sent(model)
    assert f"You are {BOT} — powered by the PersonalClaw" in sent.replace("--", "—")
    assert "You are PersonalClaw, a helpful" not in sent
    # The runtime-identity block agrees with the system prompt, and names the owner.
    assert f"[CURRENT AGENT] {BOT}" in sent
    assert f"[USER] {OWNER}" in sent


@pytest.mark.asyncio
async def test_an_agent_voice_layers_on_the_bound_prompt_instead_of_replacing_it(tmp_path):
    _write_config({"provider": "native", "system_prompt": ""}, voice="Blunt and dry.")
    _bind_chat_to_a_custom_prompt()
    state, model = await _state(tmp_path)
    session = state.get_or_create_session("probe-voice")

    await _send(state, session, "Hi")

    sent = _sent(model)
    assert "Blunt and dry." in sent
    assert BOUND_MARKER in sent, "the voice replaced the bound prompt instead of layering on it"


@pytest.mark.asyncio
async def test_picking_the_default_agent_explicitly_still_gets_the_bound_prompt(tmp_path):
    """The chat picker sets ``session.agent = "PersonalClaw"``. That spelling was read as
    a CUSTOM agent, whose prompt comes from a legacy file that does not exist for it."""
    _write_config({"provider": "native", "system_prompt": ""})
    _bind_chat_to_a_custom_prompt()
    state, model = await _state(tmp_path)
    session = state.get_or_create_session("probe-picked", agent="PersonalClaw")

    await _send(state, session, "Hi")

    assert BOUND_MARKER in _sent(model)


@pytest.mark.asyncio
async def test_a_users_own_prompt_on_the_default_agent_still_wins(tmp_path):
    """VACUITY FLOOR: the migration clears only the seeded literal. A prompt the user
    wrote into the default agent in the Agents UI is theirs and still replaces the
    binding — that is what editing an agent's prompt means."""
    _write_config({"provider": "native", "system_prompt": "You are my custom persona XYZZY."})
    _bind_chat_to_a_custom_prompt()
    state, model = await _state(tmp_path)
    session = state.get_or_create_session("probe-custom")

    await _send(state, session, "Hi")

    sent = _sent(model)
    assert "You are my custom persona XYZZY." in sent
    assert BOUND_MARKER not in sent


# ── Handover 1: each message is sent once ────────────────────────────────────


def _count(text: str, needle: str) -> int:
    return text.count(needle)


@pytest.mark.asyncio
async def test_a_brand_new_chat_sends_its_first_message_once(tmp_path):
    _write_config(None)
    state, model = await _state(tmp_path)
    session = state.get_or_create_session("probe-once")

    await _send(state, session, "first message ONCE-4417")

    sent = _sent(model)
    assert _count(sent, "ONCE-4417") == 1, sent
    assert "Previous chat history" not in sent
    assert "THREAD CONVERSATION HISTORY" not in sent, "a new chat has no history to restore"


@pytest.mark.asyncio
async def test_the_first_message_is_sent_once_even_after_the_flush_loop_persisted_it(tmp_path):
    """The 5 s dirty-session flush can write the in-flight message to disk before the
    turn assembles. History read from the log then replayed it as prior history."""
    _write_config(None)
    state, model = await _state(tmp_path)
    session = state.get_or_create_session("probe-flushed")
    session.append("user", "flushed message ONCE-5528", "msg msg-u")
    save_session_to_history(state, session)  # what _flush_dirty_sessions does

    with patch("personalclaw.dashboard.chat_runner.sel", MagicMock()):
        await run_chat(state, session, "flushed message ONCE-5528")

    sent = _sent(model)
    assert _count(sent, "ONCE-5528") == 1, sent


@pytest.mark.asyncio
async def test_an_existing_conversation_reaches_a_fresh_runtime_once(tmp_path):
    """A fresh runtime over prior turns (restart, hard stop, eviction): the prior turns
    are restored ONCE, and the message being sent appears once, as the request."""
    _write_config(None)
    state, model = await _state(tmp_path)
    session = state.get_or_create_session("probe-restore")
    session.append("user", "earlier question PRIOR-6639", "msg msg-u")
    session.append("assistant", "earlier answer PRIOR-6640", "msg msg-a")
    save_session_to_history(state, session)

    await _send(state, session, "follow-up NOW-6641")

    sent = _sent(model)
    assert _count(sent, "PRIOR-6639") == 1, sent
    assert _count(sent, "PRIOR-6640") == 1, sent
    assert _count(sent, "NOW-6641") == 1, sent
    assert "THREAD CONVERSATION HISTORY" in sent, "the prior turns were not restored at all"


# ── Handover 2: history never crosses sessions ───────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("stop_between", [False, True])
async def test_no_other_sessions_text_reaches_a_new_session(tmp_path, stop_between):
    """Two conversations from one browser tab: the first is persisted (and, in one
    variant, hard-stopped), then a NEW session sends its first message. Nothing the
    first session said may appear anywhere in the second one's request."""
    _write_config(None)
    state, _ = await _state(tmp_path)
    first = state.get_or_create_session("probe-first")
    first.append("user", "private note ALPHA-SECRET-4411", "msg msg-u")
    first.append("assistant", "noted ALPHA-REPLY-4412", "msg msg-a")
    if stop_between:
        first.append(
            "system",
            json.dumps({"kind": "stop_event", "outcome": "hard", "state": "stopped"}),
            json.dumps({"kind": "stop_event", "outcome": "hard", "state": "stopped"}),
        )
    save_session_to_history(state, first)

    state2, model = await _state(tmp_path)  # same on-disk log, as after a restart
    second = state2.get_or_create_session("probe-second")
    await _send(state2, second, "hello from the second session")

    sent = _sent(model)
    assert "ALPHA-SECRET-4411" not in sent
    assert "ALPHA-REPLY-4412" not in sent
    assert "Other chat tabs" not in sent

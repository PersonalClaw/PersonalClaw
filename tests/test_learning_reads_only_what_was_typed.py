"""What a turn learns from is what the person typed, never the text the platform put around it.

Every test here drives the real turn engine (``run_chat``) through the real assembler into a real
``NativeAgentRuntime`` answered by a scripted model, then reads what the turn's learning wrote to
memory: lessons, learned preferences and the glossary.

A saved prompt run with ``@name`` reaches the model as its whole body, and so does an attached
file's text, a pasted block, a theme's persona and an automation's message. All of that is the
turn's material and none of it is the person's own words. Learning used to read the message the
model was sent, so a prompt whose body says "List what I said I would do" was taken for a
correction ("I said") and its body was saved as a "User correction to honor".
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
from personalclaw.dashboard.chat_runner import run_chat
from personalclaw.dashboard.chat_utils import _history_key_for, _prepare_messages
from personalclaw.dashboard.state import CRON_NOTIFY_END, CRON_NOTIFY_PREFIX, DashboardState
from personalclaw.history import ConversationLog
from personalclaw.llm.events import EVENT_COMPLETE, EVENT_TEXT_CHUNK, AgentEvent
from personalclaw.memory import MemoryStore
from personalclaw.memory_service import MemoryService
from personalclaw.own_words import RAN_PROMPT, own_words
from personalclaw.preference_facets import load_facets
from personalclaw.skills import SkillsLoader
from personalclaw.vector_memory import VectorMemoryStore

#: A saved prompt as an importer brings one over from an agent CLI's commands folder. Its body
#: says "what I said", which the correction heuristic reads as a person pushing back, and "Do not
#: read", which the preference detector reads as a standing veto.
WEEKLY_REVIEW = (
    "Run my weekly review from ~/Notes/Garden.\n\n"
    "- Read the last 7 daily notes in `Daily/`.\n"
    "- List what I said I would do last Sunday and whether it happened.\n"
    "- Propose at most five priorities for the coming week.\n\n"
    "Never read `Health/` or `Finance/`. Write nothing; I will copy what I want."
)


class _ScriptedModel:
    """A ModelProvider that records every request and answers one line, calling no tool."""

    supports_tools = True
    _model = "scripted"

    def __init__(self, reply: str = "Done.") -> None:
        self.requests: list[list[dict]] = []
        self.reply = reply

    async def complete(self, messages, *, tools=None, model=None, reasoning_effort=""):
        self.requests.append([dict(m) for m in messages])
        yield AgentEvent(kind=EVENT_TEXT_CHUNK, text=self.reply)
        yield AgentEvent(kind=EVENT_COMPLETE)

    async def cancel(self, *, wait_ack_timeout: float = 0.0) -> str:
        return "acked"


@pytest.fixture
def memory(tmp_path, monkeypatch) -> MemoryService:
    """The memory every turn of a test learns into, with its record store wired."""
    store = VectorMemoryStore(db_path=tmp_path / "memory.db")
    store.init()
    svc = MemoryService.over_vector_store(store)
    monkeypatch.setattr("personalclaw.memory_service.service_for", lambda _provider: svc)
    # The forked skill review would ask a model in the background; the paths under test are the
    # model-free captures, so the review is off rather than left to fail against no model.
    config_loader.config_dir()
    config_loader.config_path().write_text(
        json.dumps({"learning": {"skill_ladder": False}}), encoding="utf-8"
    )
    return svc


def _save_prompt(name: str, content: str) -> None:
    from personalclaw.prompt_providers.base import PromptTemplate
    from personalclaw.prompt_providers.registry import (
        _ensure_default_providers_registered,
        get_prompt_provider,
    )

    _ensure_default_providers_registered()
    get_prompt_provider("native").create_prompt(PromptTemplate(name=name, content=content))


async def _state(tmp_path: Path, reply: str = "Done.") -> tuple[DashboardState, _ScriptedModel]:
    model = _ScriptedModel(reply)
    runtime = NativeAgentRuntime(
        definition=AgentRuntimeDefinition(name="PersonalClaw", provider="native", model="scripted"),
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
    sessions.get_or_create = AsyncMock(return_value=(runtime, True, False))
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


async def _send(
    state, session, text: str, *, role: str = "user", cls: str = "msg msg-u", meta=None
):
    """One turn as its dispatcher starts it: the turn's own row first, then the turn."""
    session.append(role, text, cls, meta=meta)
    with patch("personalclaw.dashboard.chat_runner.sel", MagicMock()):
        await run_chat(state, session, text)


def _sent(model: _ScriptedModel) -> str:
    assert model.requests, "the turn never reached the model"
    return "\n".join(str(m.get("content") or "") for m in model.requests[-1])


def _lessons(svc: MemoryService) -> list[str]:
    return [str(json.loads(row["value_json"])) for row in svc.get_lessons()]


def _glossary(svc: MemoryService) -> list[str]:
    for slot in svc.slots():
        if slot["name"] == "glossary":
            return [line["text"] for line in slot["lines"] if not line["tombstoned"]]
    return []


def _learned_nothing(svc: MemoryService) -> None:
    assert _lessons(svc) == []
    assert _glossary(svc) == []
    assert load_facets(svc._vs) == []


# ── a saved prompt ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_running_a_saved_prompt_learns_nothing_from_its_body(tmp_path, memory):
    _save_prompt("weekly-review", WEEKLY_REVIEW)
    state, model = await _state(tmp_path)
    session = state.get_or_create_session("weekly")

    await _send(state, session, "@weekly-review")

    assert "List what I said I would do last Sunday" in _sent(model), "the prompt never ran"
    _learned_nothing(memory)


@pytest.mark.asyncio
async def test_a_correction_typed_beside_a_saved_prompt_is_learned_in_her_own_words(
    tmp_path, memory
):
    _save_prompt("weekly-review", WEEKLY_REVIEW)
    state, model = await _state(tmp_path)
    session = state.get_or_create_session("weekly")

    await _send(state, session, "@weekly-review that's not what I asked, use the Garden vault")

    assert "Run my weekly review" in _sent(model), "the prompt never ran"
    assert _lessons(memory) == [
        "User correction to honor: that's not what I asked, use the Garden vault"
    ]


@pytest.mark.asyncio
async def test_running_a_saved_prompt_with_prompts_get_learns_nothing_from_its_body(
    tmp_path, memory
):
    _save_prompt("weekly-review", WEEKLY_REVIEW)
    state, model = await _state(tmp_path)
    session = state.get_or_create_session("weekly")

    await _send(state, session, "/prompts get weekly-review")

    assert "List what I said I would do last Sunday" in _sent(model), "the prompt never ran"
    _learned_nothing(memory)


@pytest.mark.asyncio
async def test_her_turn_shows_the_prompt_text_it_ran_live_and_after_a_reload(tmp_path, memory):
    """Her message stays what she typed, and the turn says what the agent was sent in its place:
    the prompt's name and its text, announced as the prompt expands and kept on her message."""
    _save_prompt("handoff", "Write a handoff note for whoever is on call next.")
    state, model = await _state(tmp_path)
    session = state.get_or_create_session("handoff")

    await _send(state, session, "@handoff")

    live = [
        call.args[1]
        for call in state.broadcast_ws.call_args_list
        if call.args[0] == "activity_event" and call.args[1].get("kind") == "prompt"
    ]
    assert len(live) == 1, live
    ran = live[0]["prompt"]
    assert live[0]["session"] == session.key
    assert ran["name"] == "handoff"
    assert "Write a handoff note for whoever is on call next." in ran["text"]
    assert ran["text"] in _sent(model), "what the turn shows is not what the agent was sent"

    rows = state.conversation_log.read_messages(_history_key_for(session.key))
    (mine,) = [m for m in _prepare_messages(rows, running=False) if m["role"] == "user"]
    assert mine["content"] == "@handoff"
    assert mine["meta"][RAN_PROMPT] == ran
    _learned_nothing(memory)


# ── what else the turn builder puts around her words ─────────────────────────


@pytest.mark.asyncio
async def test_an_attached_files_text_is_not_learned_as_her_words(tmp_path, memory):
    uploads = config_loader.config_dir() / "uploads"
    uploads.mkdir(parents=True, exist_ok=True)
    notes = uploads / "a1b2c3_meeting-notes.txt"
    notes.write_text(
        "That's not what I said in the meeting. Never schedule calls before ten.\n",
        encoding="utf-8",
    )
    state, model = await _state(tmp_path)
    session = state.get_or_create_session("notes")

    await _send(state, session, "summarize the attached notes", meta={"files": [str(notes)]})

    assert "Never schedule calls before ten" in _sent(model), "the file's text never reached it"
    _learned_nothing(memory)


@pytest.mark.asyncio
async def test_a_pasted_block_is_not_learned_as_her_words(tmp_path, memory):
    block = "ERROR checksum is wrong for chunk 7\nWARN never retry a corrupt chunk"
    state, model = await _state(tmp_path)
    session = state.get_or_create_session("paste")

    await _send(
        state,
        session,
        f"why does the import stop here?\n{block}",
        meta={"pastes": [{"content": block}]},
    )

    assert "checksum is wrong" in _sent(model)
    _learned_nothing(memory)


@pytest.mark.asyncio
async def test_a_themes_persona_is_not_learned_as_her_words(tmp_path, memory):
    state, model = await _state(tmp_path)
    session = state.get_or_create_session("themed")
    session.color_theme = "lumon"

    await _send(state, session, "thanks, that helps")

    assert "LUMON PERSONA" in _sent(model), "the theme's persona never rode along"
    _learned_nothing(memory)


@pytest.mark.asyncio
async def test_an_automations_message_is_not_learned_as_her_words(tmp_path, memory):
    state, model = await _state(tmp_path)
    session = state.get_or_create_session("digest")
    wrapped = (
        f'{CRON_NOTIFY_PREFIX}"Morning digest"]\n'
        "That's not what I said yesterday: the build is green.\n"
        f"{CRON_NOTIFY_END}"
    )

    await _send(
        state, session, wrapped, role="inject", cls=json.dumps({"cronLabel": "Morning digest"})
    )

    assert "the build is green" in _sent(model)
    _learned_nothing(memory)


@pytest.mark.asyncio
async def test_messages_sent_during_a_turn_teach_only_what_each_one_typed(tmp_path, memory):
    """Two messages sent while a turn ran become the next turn together, under a line the merge
    adds, and the second carried a pasted block: that turn learns the words she typed, with the
    login in the address she typed kept out of them as it is kept out of the message."""
    config_loader.config_path().write_text(
        json.dumps(
            {"learning": {"skill_ladder": False}, "dashboard": {"merge_queued_messages": True}}
        ),
        encoding="utf-8",
    )
    block = "ERROR checksum is wrong for chunk 7"
    state, model = await _state(tmp_path)
    session = state.get_or_create_session("queued")
    session.queue_append("that's not what I meant, keep the summary to three lines")
    typed = "and read https://sam:opensesame@files.example.com/report"
    session.queue_append(f"{typed}\n{block}", own_words=typed)

    session.append("user", "summarize the import log", "msg msg-u")
    with patch("personalclaw.dashboard.chat_runner.sel", MagicMock()):
        await run_chat(state, session, "summarize the import log")
        await session.task

    assert "checksum is wrong" in _sent(model), "the queued messages never ran"
    (row,) = [m for m in session.messages if "queued messages merged" in m["content"]]
    words = own_words(row)
    assert words.startswith("that's not what I meant, keep the summary to three lines\n\nand read")
    assert "opensesame" not in row["content"] and "opensesame" not in words
    assert "checksum" not in words and "merged" not in words
    assert _lessons(memory) == [f"User correction to honor: {words}"]


@pytest.mark.asyncio
async def test_the_assistants_own_advice_is_not_learned_as_hers(tmp_path, memory):
    """The answer's advice is the assistant's, so a turn whose answer says what never to do and
    what to use instead teaches nothing when she only asked."""
    advice = (
        "Never use kill -9 on the demo server; send SIGTERM and let its handler finish. "
        "Don't add a TTL or a lease to make the slot fit: cut what does not fit."
    )
    state, _model = await _state(tmp_path, reply=advice)
    session = state.get_or_create_session("advice")

    await _send(state, session, "how should I stop the demo server between takes?")

    _learned_nothing(memory)


# ── what she types is still learned ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_correction_she_types_is_still_learned(tmp_path, memory):
    """Control: the same turn engine, the same memory, and her own correction is learned."""
    state, _model = await _state(tmp_path)
    session = state.get_or_create_session("plain")

    await _send(state, session, "that's not what I meant, keep the summary to three lines")

    assert _lessons(memory) == [
        "User correction to honor: that's not what I meant, keep the summary to three lines"
    ]

"""What a model is shown of a conversation's earlier turns names who said each one.

A shared channel thread can hold the owner's lines and a colleague's. Memory consolidation already
reads them apart: the owner's line as the user's, the colleague's whole and fenced as someone
else's. The model that continues the conversation did not: every history it was handed (the turns
a fresh runtime is given back after a restart, the compressed history, the summary background
compression keeps for an idle chat, the stopped turn handed back to the next one) labelled every
user line ``User:``, so once the runtime that heard the turns was gone it could not tell the owner's
words from her colleague's, and a colleague's "Mira is allergic to shellfish" read as hers.

Now each of those readers takes the line's own record of where it came from
(``turn_source.sent_by_owner``): the owner's line is the user's, and anyone else's is shown as
consolidation shows it. A dashboard chat's history reads as it always did.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from personalclaw import channel_inbound as ci
from personalclaw import channel_trust as ct
from personalclaw.channel_transports.base import ChannelMessage
from personalclaw.config.credentials import owner_id_credential, save_credential
from personalclaw.config.loader import CRED_OWNER_ID, AppConfig
from personalclaw.dashboard.chat_persistence import (
    prior_turns_transcript,
    save_all_sessions_to_history,
)
from personalclaw.dashboard.chat_utils import persisted_history_key
from personalclaw.dashboard.state import DashboardState
from personalclaw.history import ConversationLog
from personalclaw.llm.events import EVENT_COMPLETE, EVENT_TEXT_CHUNK, AgentEvent
from personalclaw.session import SessionManager

PROVIDER = "teamchat"
CHANNEL = "C0TEAM0001"
THREAD = "1790800000.000100"
OWNER = "U0MIRAOWNR"
COLLEAGUE = "U0JONASCOL"

HERS = "Book the team offsite for the second week of May."
ABOUT_HER = "Mira is allergic to shellfish, so never book seafood places for her."

#: How a line someone else sent opens, as consolidation already shows it.
THEIRS = "SENT BY SOMEONE OTHER THAN THE USER (not the user's words): "
FENCE = f"<untrusted_content source=channel:{PROVIDER}:{COLLEAGUE}>"


@pytest.fixture(autouse=True)
def _a_shared_channel(unset_env):
    unset_env(CRED_OWNER_ID, owner_id_credential(PROVIDER))
    save_credential(owner_id_credential(PROVIDER), OWNER)
    ct.allow_sender(PROVIDER, OWNER, name="Mira")
    ct.allow_sender(PROVIDER, COLLEAGUE, name="Jonas")
    ct.track(PROVIDER, CHANNEL, "Team")
    yield


class _Door:
    """The door a channel's messages cross, over a dashboard state."""

    def __init__(self, state: DashboardState) -> None:
        self.dashboard_state = state
        self._count = 0

    async def say(self, sender: str, text: str) -> None:
        self._count += 1
        msg = ChannelMessage(
            channel_id=CHANNEL,
            text=text,
            sender=sender,
            thread_id=THREAD,
            message_id=f"m-{self._count}",
        )
        verdict = await ci.deliver_inbound(self, PROVIDER, msg, is_dm=False, turn_runner=_notes_it)
        assert verdict.allowed and not verdict.fenced_text, verdict
        for _ in range(50):
            running = {t for t in self.dashboard_state._background_tasks if not t.done()}
            if not running:
                return
            await asyncio.wait(running, timeout=10)
        raise AssertionError("turns kept starting")


async def _notes_it(state: Any, session: Any, message: str) -> None:
    """A turn that answers without a model: the rows are what this is about."""
    session.append("assistant", "Noted.", "msg msg-a")


def _plain_state(home: Path) -> DashboardState:
    return DashboardState(
        sessions=SessionManager(AppConfig()),
        start_time=0.0,
        conversation_log=ConversationLog(base_dir=home / "sessions"),
    )


async def _a_shared_thread(home: Path) -> tuple[DashboardState, str]:
    """The owner and her colleague each write in the thread, the door hands both to its chat, and
    the chat is saved: what a gateway that stops now leaves on disk. Returns the state and the
    chat's name."""
    state = _plain_state(home)
    door = _Door(state)
    await door.say(OWNER, HERS)
    await door.say(COLLEAGUE, ABOUT_HER)
    save_all_sessions_to_history(state)
    session = state.get_linked_session(THREAD)
    assert session is not None
    return state, session.key


def _theirs_in(text: str) -> None:
    """*text* shows the colleague's line as theirs, fenced, and never as the user's."""
    assert f"User: {ABOUT_HER}" not in text, text
    assert f"{THEIRS}{FENCE}" in text, text
    after = text.split(f"{THEIRS}{FENCE}", 1)[1]
    assert after.lstrip("\n").startswith(f"{ABOUT_HER}\n</untrusted_content>"), text


# ── after a restart ──────────────────────────────────────────────────────────────────────────────


class _Recording:
    """A model that answers at once, keeping what it was handed."""

    supports_tools = True
    _model = "scripted"

    def __init__(self) -> None:
        self.handed: list[list[dict]] = []

    async def complete(self, messages, *, tools=None, model=None, reasoning_effort=""):
        self.handed.append(list(messages))
        yield AgentEvent(kind=EVENT_TEXT_CHUNK, text="Done.")
        yield AgentEvent(kind=EVENT_COMPLETE)


@pytest.mark.asyncio
async def test_after_a_restart_the_model_reads_the_colleagues_line_as_theirs(tmp_path):
    """🔴 Red on integration: the fresh runtime was handed ``User: Mira is allergic…``."""
    from personalclaw.agents.native.runtime import NativeAgentRuntime
    from personalclaw.agents.provider import AgentRuntimeDefinition
    from personalclaw.context import ContextBuilder
    from personalclaw.context_engine import set_engine
    from personalclaw.dashboard.chat_runner import run_chat
    from personalclaw.memory import MemoryStore
    from personalclaw.skills import SkillsLoader
    from personalclaw.turn_source import DASHBOARD_SOURCE

    _, name = await _a_shared_thread(tmp_path)

    # The gateway starts again: a new state over the same transcripts, and a new runtime that
    # holds nothing of the conversation, so the turn is given its history back.
    model = _Recording()
    runtime = NativeAgentRuntime(
        definition=AgentRuntimeDefinition(name="PersonalClaw", provider="native", model="scripted"),
        model_provider=model,
        tool_providers=[],
        cwd=tmp_path,
    )
    await runtime.start()
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
    set_engine(None)

    session = state.get_or_create_session(name)
    assert [m["content"] for m in session.messages if m["role"] == "user"] == [HERS, ABOUT_HER]
    session.append("user", "And the venue?", "msg msg-u", source=DASHBOARD_SOURCE)
    with patch("personalclaw.dashboard.chat_runner.sel", MagicMock()):
        await run_chat(state, session, "And the venue?")

    handed = json.dumps(model.handed[0])
    first = next(m["content"] for m in model.handed[0] if m.get("role") == "user")
    assert f"User: {HERS}" in first, first
    _theirs_in(first)
    assert "And the venue?" in handed


# ── compression ──────────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_compressed_history_names_the_colleagues_line_as_theirs(tmp_path):
    """🔴 Red on integration: the compressed history and its verbatim head said ``User:``."""
    from personalclaw.context import compress_thread_history

    state, name = await _a_shared_thread(tmp_path)
    session = state._sessions[name]
    turns = prior_turns_transcript(session, "")

    # Short enough to be handed back whole.
    whole = await compress_thread_history(turns, f"dashboard:{name}", "next")
    assert whole is not None
    assert f"User: {HERS}" in whole
    _theirs_in(whole)

    # Long enough to be compressed: the compressor reads the line as theirs, and so do the
    # recent exchanges the result keeps verbatim.
    filler = [{"role": "assistant", "content": "x" * 30_000} for _ in range(2)]
    prompts: list[str] = []

    async def _compressor(prompt: str, **_kw: Any) -> str:
        prompts.append(prompt)
        return "They planned an offsite."

    with patch("personalclaw.chores.run_chore", side_effect=_compressor):
        compressed = await compress_thread_history(filler + turns, f"dashboard:{name}", "next")
    assert compressed is not None and prompts
    _theirs_in(prompts[0])
    _theirs_in(compressed.split("## Recent exchanges (verbatim)", 1)[1])


@pytest.mark.asyncio
async def test_the_background_summary_is_written_from_lines_that_name_their_speaker(tmp_path):
    """🔴 Red on integration: background compression summarized ``user: Mira is allergic…``."""
    from personalclaw import bg_compress

    state, name = await _a_shared_thread(tmp_path)
    log = state.conversation_log
    assert log is not None
    rows = log.read_messages(persisted_history_key(log, name))
    bodies: list[str] = []

    async def _prose(body: str, **_kw: Any) -> str:
        bodies.append(body)
        return "An offsite was planned."

    with patch("personalclaw.tool_providers.prose_compress.compress_prose", side_effect=_prose):
        summary = await bg_compress._summarize_oldest(rows, key=persisted_history_key(log, name))
    assert "An offsite was planned." in summary
    (body,) = bodies
    assert f"User: {HERS}" in body
    _theirs_in(body)


@pytest.mark.asyncio
async def test_a_stopped_turn_a_colleague_asked_for_is_given_back_as_theirs(tmp_path):
    """The turn handed back to the next one after a stop is the colleague's request: it says
    so."""
    from personalclaw.context import build_cancelled_turn_preamble

    state, name = await _a_shared_thread(tmp_path)
    log = state.conversation_log
    assert log is not None
    preamble = build_cancelled_turn_preamble(log, persisted_history_key(log, name))
    _theirs_in(preamble)


# ── what learning quotes from the conversation ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_skill_proposals_excerpt_quotes_only_the_owners_own_words(tmp_path):
    """🔴 Red on integration: a promoted skill's excerpt quoted the colleague's words as hers,
    because the turns it was read from had lost who sent them."""
    from personalclaw.learning import skill_promotion

    state, name = await _a_shared_thread(tmp_path)
    log = state.conversation_log
    assert log is not None
    transcript = log.recent(persisted_history_key(log, name), max_messages=100)
    promoted = skill_promotion.promote(
        name="plan an offsite",
        description="When the team plans an offsite.",
        procedure="1. Pick the week. 2. Book the venue.",
        rationale="The offsite planning worked.",
        session_key=f"dashboard:{name}",
        transcript=transcript,
    )
    assert promoted.proposal is not None, promoted.refusal
    excerpt = promoted.proposal.source_excerpt
    assert HERS in excerpt
    assert ABOUT_HER not in excerpt


# ── the dashboard's own chat ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_dashboard_chats_history_reads_as_it_always_did():
    """The control: what the owner types in the dashboard is the user's in every history."""
    from personalclaw.context import compress_thread_history
    from personalclaw.history import model_view
    from personalclaw.turn_source import DASHBOARD_SOURCE

    rows = [
        {"role": "user", "content": HERS, **DASHBOARD_SOURCE},
        {"role": "assistant", "content": "Booked."},
        {"role": "user", "content": "And the venue?", **DASHBOARD_SOURCE},
    ]
    whole = await compress_thread_history(model_view(rows, None), "dashboard:chat-41", "next")
    assert whole == f"User: {HERS}\nAssistant: Booked.\nUser: And the venue?"

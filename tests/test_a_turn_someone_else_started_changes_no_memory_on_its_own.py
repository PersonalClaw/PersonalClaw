"""A turn someone other than the owner started changes none of her memory on its own.

A shared channel thread can have the owner and a colleague she allowed to talk to the agent. The
door lets the colleague's message in unfenced, because the agent may act on what they ask, and
memory already takes none of their words as hers. But the agent's own memory tools still wrote
whatever the turn asked for: a colleague's "remember that Mira is allergic to shellfish" was saved
as a lesson of hers, with the source that outranks everything else memory holds, and a
colleague's "forget that" removed what she had taught.

Now a memory change the agent makes in a turn the owner did not start (the turn's own message, by
``turn_source.sent_by_owner``) is held for her own word: nothing is written, the owner is asked
through the approval registry (the card, the Inbox, the phone, her channel), and what she allows
is written as hers; a Deny keeps nothing. The agent is told so in a true sentence. The same check
stands in front of every write to her memory the turn makes in the gateway, the stores' own
included, and work the turn starts (a subagent) carries it. What the owner asks for in the same
thread, and in the dashboard, is written as before.

Driven as the turn drives it: the real gateway over a scratch home, the door a channel's message
crosses, the real turn engine on a native runtime whose model calls the memory tools, and those
tools' calls back to the gateway over its own API. An agent CLI brings file and shell tools of its
own, which the turn engine's gate sees only as a permission request: there, a change to a memory
document is refused before any grant (the chat's Trust) could approve it. And consolidation,
which reads only the owner's words, still runs when a colleague's turn ends.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from personalclaw import channel_inbound as ci
from personalclaw import channel_trust as ct
from personalclaw import memory_writes, session_restrictions
from personalclaw.channel_transports.base import ChannelMessage
from personalclaw.config.credentials import owner_id_credential, save_credential
from personalclaw.config.loader import CRED_OWNER_ID
from personalclaw.history import ConversationLog
from personalclaw.llm.events import EVENT_COMPLETE, EVENT_TEXT_CHUNK, EVENT_TOOL_CALL, AgentEvent
from personalclaw.vector_memory import VectorMemoryStore

#: A chat channel this test sets up, by its provider key, and a channel there the bot is in.
PROVIDER = "teamchat"
CHANNEL = "C0TEAM0001"
#: The owner's id on the channel, as its first contact stored it, and a colleague she allowed.
OWNER = "U0MIRAOWNR"
COLLEAGUE = "U0JONASCOL"

#: What the colleague asks the agent to remember about her, and what she asks it herself.
ABOUT_HER = "Mira is allergic to shellfish, so never book seafood places for her."
HERS = "I take the 7:40 train on Tuesdays, so no meetings before nine that day."
#: A lesson she taught before.
TAUGHT = "Mira keeps every travel booking in the shared trips calendar."

_AUTH_SHORTCUTS = (
    "PERSONALCLAW_AUTH_MODE",
    "PERSONALCLAW_BYPASS_LOCAL_NETWORKS",
    "PERSONALCLAW_SESSION_KEY",
)


def _share_the_channel() -> None:
    """In the home in use: the channel knows its owner, she and her colleague are both let in,
    and the channel is tracked, so a message from either of them crosses the door unfenced."""
    save_credential(owner_id_credential(PROVIDER), OWNER)
    ct.allow_sender(PROVIDER, OWNER, name="Mira")
    ct.allow_sender(PROVIDER, COLLEAGUE, name="Jonas")
    ct.track(PROVIDER, CHANNEL, "Team")


@pytest.fixture(autouse=True)
def _a_shared_channel(unset_env):
    unset_env(CRED_OWNER_ID, owner_id_credential(PROVIDER))
    _share_the_channel()
    yield


class _Agent:
    """A model that makes, in each turn it is given, the next call it was told to make (or the
    calls of a list, one after another), and then answers. What it was handed each time is kept:
    the last thing in it is a call's result."""

    supports_tools = True
    _model = "scripted"

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict] | list[tuple[str, dict]]] = []
        self.handed: list[list[dict]] = []
        self._this_turn: list[tuple[str, dict]] = []

    async def complete(self, messages, *, tools=None, model=None, reasoning_effort=""):
        self.handed.append(list(messages))
        if not (messages and messages[-1].get("role") == "tool"):
            item = self.calls.pop(0) if self.calls else []
            self._this_turn = list(item) if isinstance(item, list) else [item]
        if self._this_turn:
            name, arguments = self._this_turn.pop(0)
            yield AgentEvent(
                kind=EVENT_TOOL_CALL,
                tool_call_id=f"call-{len(self.handed)}",
                title=name,
                tool_input=json.dumps(arguments),
            )
        else:
            yield AgentEvent(kind=EVENT_TEXT_CHUNK, text="Noted.")
        yield AgentEvent(kind=EVENT_COMPLETE, input_tokens=10, output_tokens=3)

    def told(self) -> str:
        """What the last call it made answered."""
        last = self.handed[-1][-1]
        assert last.get("role") == "tool", last
        return str(last.get("content") or "")


@dataclass
class _Gateway:
    """The gateway, its home, the agent's model, and the door a channel's messages cross."""

    home: Path
    state: Any
    agent: _Agent
    sent: int = field(default=0)

    @property
    def dashboard_state(self) -> Any:
        return self.state

    async def say(self, sender: str, text: str, *, thread: str = "1790700000.000100") -> None:
        """*sender* writes *text* in a thread of the channel, and the door hands it to the
        thread's chat, whose turn runs on the real turn engine."""
        from personalclaw.dashboard.chat_runner import run_chat

        self.sent += 1
        msg = ChannelMessage(
            channel_id=CHANNEL,
            text=text,
            sender=sender,
            thread_id=thread,
            message_id=f"m-{self.sent}",
        )
        verdict = await ci.deliver_inbound(self, PROVIDER, msg, is_dm=False, turn_runner=run_chat)
        assert verdict.allowed and not verdict.fenced_text, verdict
        await _settled(self.state)

    def chat(self, thread: str = "1790700000.000100") -> Any:
        session = self.state.get_linked_session(thread)
        assert session is not None, "the door linked no chat to the thread"
        return session


def _waits_for_her(state: Any, task: asyncio.Task) -> bool:
    """Whether *task* is a held change whose ask is still waiting for the owner's answer: it ends
    when she answers, or when her approval window closes."""
    coro = task.get_coro()
    frame = getattr(coro, "cr_frame", None)
    asked = frame.f_locals.get("ask_id") if frame is not None else None
    return getattr(coro, "__name__", "") == "_ask_then_write" and asked in state._pending_approvals


async def _settled(state: Any) -> None:
    """Wait for every turn and every task the gateway started to end, except an ask still waiting
    for the owner's answer: what she has answered is written before this returns."""
    for _ in range(100):
        running = {
            t for t in state._background_tasks if not t.done() and not _waits_for_her(state, t)
        }
        if not running:
            return
        await asyncio.wait(running, timeout=10)
    raise AssertionError("work kept starting")


@contextlib.asynccontextmanager
async def _gateway(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, files: bool = False
) -> AsyncIterator[_Gateway]:
    """The real gateway over a scratch home holding a lesson she taught, whose chats' turns run on
    a native runtime with the memory tools (and, with *files*, the file tools over the home's
    workspace) and every call approved without asking, as a chat with Trust on runs them: the
    worst case for a turn someone else started."""
    from personalclaw.agents.native.builtin_tools import NativeBuiltinToolProvider
    from personalclaw.agents.native.runtime import NativeAgentRuntime
    from personalclaw.agents.provider import AgentRuntimeDefinition
    from personalclaw.context import ContextBuilder
    from personalclaw.context_engine import set_engine
    from personalclaw.memory import MemoryStore
    from personalclaw.skills import SkillsLoader
    from personalclaw.tool_providers.registry import create_memory_provider

    home = tmp_path / "data"
    user_home = tmp_path / "user"
    user_home.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    monkeypatch.setenv("HOME", str(user_home))
    for name in _AUTH_SHORTCUTS:
        monkeypatch.delenv(name, raising=False)
    _share_the_channel()
    store = VectorMemoryStore(db_path=home / "memory.db")
    store.init()
    assert store.write_lesson(TAUGHT, source="user_explicit")
    store.close()

    agent = _Agent()
    (home / "workspace").mkdir(parents=True, exist_ok=True)
    tools: list[Any] = [create_memory_provider()]
    if files:
        tools.append(NativeBuiltinToolProvider(home / "workspace", sandbox_mode="off"))
    runtime = NativeAgentRuntime(
        definition=AgentRuntimeDefinition(name="PersonalClaw", provider="native", model="scripted"),
        model_provider=agent,
        tool_providers=tools,
        # The folder every chat starts in, as the gateway gives a chat one.
        cwd=home / "workspace" if files else tmp_path,
    )
    await runtime.start()
    runtime.set_approval_policy("auto")

    async def _runtime_for(key: str, *_a: Any, **_kw: Any):
        runtime.set_session_key(key)
        return runtime, True, False

    sessions = MagicMock(count=0)
    sessions._sessions = {}
    sessions.get_pid = MagicMock(return_value=None)
    sessions.get_channel_link = MagicMock(return_value=(None, None))
    sessions.get_or_create = AsyncMock(side_effect=_runtime_for)
    sessions.record_failure = AsyncMock()

    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>spa</html>", encoding="utf-8")
    import personalclaw.dashboard.handlers.core as handlers_core_mod
    import personalclaw.dashboard.server as server_mod

    monkeypatch.setattr(server_mod, "_DIST_DIR", dist)
    monkeypatch.setattr(handlers_core_mod, "_DIST_DIR", dist)
    log = ConversationLog(base_dir=home / "sessions")
    runner, state = await server_mod.start_dashboard(
        sessions=sessions, port=0, conversation_log=log
    )
    monkeypatch.setenv("PERSONALCLAW_PORT", str(runner.addresses[0][1]))
    # The memory the gateway's routes and the turn's readers use: her records in memory.db.
    markdown = MemoryStore(workspace=home / "workspace")
    markdown.init()
    records = VectorMemoryStore(db_path=home / "memory.db")
    records.init()
    markdown.vector_store = records
    state.context_builder = ContextBuilder(
        memory=markdown,
        skills=SkillsLoader(skills_path=home / "skills", install_builtins=False),
        conversation_log=log,
    )
    state._hook_store = None
    set_engine(None)
    try:
        yield _Gateway(home=home, state=state, agent=agent)
    finally:
        # What nobody answered ends unanswered, writing nothing, as when its turn is stopped.
        for approval_id in list(state._pending_approvals):
            state.cancel_approval(approval_id, reason="the test ended")
        await _settled(state)
        await runner.cleanup()
        records.close()


def _lessons(home: Path) -> dict[str, dict]:
    """Every live lesson memory holds, by its rule."""
    store = VectorMemoryStore(db_path=home / "memory.db")
    store.init()
    try:
        return {str(json.loads(row["value_json"])): dict(row) for row in store.get_lessons()}
    finally:
        store.close()


def _asks(state: Any, tool: str) -> list[dict]:
    """The approvals waiting for the owner that ask about *tool*."""
    return [e for e in state._pending_approvals.values() if e.get("tool") == tool]


def _audit(operation: str) -> list[dict]:
    from personalclaw.sel import sel

    return [e for e in sel().recent(limit=500) if e.get("operation") == operation]


@pytest.fixture(autouse=True)
def _unmarked():
    """The registry is process-wide: no mark one test makes reaches another."""
    yield
    for key in list(session_restrictions._asked_by):
        session_restrictions.clear(key)


# ── a colleague's turn ───────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_colleagues_turn_that_asks_to_remember_writes_nothing_and_asks_the_owner(
    tmp_path, monkeypatch
):
    """🔴 Red on integration: the colleague's statement about her was saved as her own lesson,
    and nobody asked her."""
    async with _gateway(tmp_path, monkeypatch) as gw:
        gw.agent.calls.append(("memory_remember", {"rule": ABOUT_HER, "category": "preference"}))
        await gw.say(COLLEAGUE, f"Please remember this: {ABOUT_HER}")
        told = gw.agent.told()
        lessons = _lessons(gw.home)
        asked = _asks(gw.state, "memory_remember")

    assert ABOUT_HER not in lessons, "nothing is written on someone else's say-so"
    assert TAUGHT in lessons, "what she taught is untouched"
    (ask,) = asked
    # The agent is told, in a true sentence, who asked, that nothing was written, and that the
    # owner decides.
    assert told.startswith("Not saved yet: Jonas (U0JONASCOL) on teamchat asked for this"), told
    assert "nothing says they are the owner" in told
    assert "the owner has been asked whether to keep it" in told
    # The owner is asked, by her own card, naming who asked and what would be kept.
    assert ABOUT_HER in ask["tool_input"]
    assert "Jonas (U0JONASCOL) on teamchat asked the agent to remember this" in ask["tool_purpose"]
    held = [e for e in _audit("memory_remember") if e.get("outcome") == "needs_human"]
    assert held, "the hold is in the security log"


@pytest.mark.asyncio
async def test_what_the_owner_allows_is_kept_as_hers_and_a_deny_keeps_nothing(
    tmp_path, monkeypatch
):
    """Her Allow is her word: the lesson is written as hers. Her Deny writes nothing."""
    from personalclaw import approval_answer

    other = "Mira never wants calls after six in the evening."
    async with _gateway(tmp_path, monkeypatch) as gw:
        gw.agent.calls.append(("memory_remember", {"rule": ABOUT_HER, "category": "preference"}))
        await gw.say(COLLEAGUE, f"Please remember this: {ABOUT_HER}")
        gw.agent.calls.append(("memory_remember", {"rule": other, "category": "preference"}))
        await gw.say(COLLEAGUE, f"And remember this: {other}")
        allow = next(e for e in _asks(gw.state, "memory_remember") if ABOUT_HER in e["tool_input"])
        deny = next(e for e in _asks(gw.state, "memory_remember") if other in e["tool_input"])

        # Nobody but the owner answers: the agent's own answer is refused.
        assert not gw.state.resolve_approval(allow["id"], True, by=approval_answer.agent())
        assert gw.state.resolve_approval(allow["id"], True, by=approval_answer.YOU)
        assert gw.state.resolve_approval(deny["id"], False, by=approval_answer.YOU)
        await _settled(gw.state)
        lessons = _lessons(gw.home)

    assert lessons[ABOUT_HER]["source"] == "user_explicit", "her Allow makes it hers"
    assert other not in lessons, "her Deny keeps nothing"
    outcomes = [e.get("outcome") for e in _audit("memory_remember")]
    assert "approved" in outcomes and "rejected" in outcomes, outcomes


@pytest.mark.asyncio
async def test_a_colleagues_turn_that_asks_to_forget_removes_nothing_until_she_allows_it(
    tmp_path, monkeypatch
):
    """🔴 Red on integration: the colleague's "forget that" removed what she had taught."""
    from personalclaw import approval_answer

    async with _gateway(tmp_path, monkeypatch) as gw:
        gw.agent.calls.append(("memory_forget", {"query": "trips calendar"}))
        await gw.say(COLLEAGUE, "Forget what you know about the trips calendar.")
        told = gw.agent.told()
        still = _lessons(gw.home)
        assert TAUGHT in still, "nothing is removed on someone else's say-so"
        (ask,) = _asks(gw.state, "memory_forget")
        assert gw.state.resolve_approval(ask["id"], True, by=approval_answer.YOU)
        await _settled(gw.state)
        after = _lessons(gw.home)

    assert told.startswith("Nothing removed yet: Jonas (U0JONASCOL) on teamchat asked"), told
    assert "trips calendar" in ask["tool_input"]
    assert TAUGHT not in after, "her Allow removes it"


# ── what she asks for herself ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_owners_own_turn_in_the_same_thread_still_remembers_at_once(
    tmp_path, monkeypatch
):
    """The control: her own message, through the same door into the same chat, is her word."""
    async with _gateway(tmp_path, monkeypatch) as gw:
        gw.agent.calls.append(("memory_remember", {"rule": ABOUT_HER, "category": "preference"}))
        await gw.say(COLLEAGUE, f"Please remember this: {ABOUT_HER}")
        gw.agent.calls.append(("memory_remember", {"rule": HERS, "category": "preference"}))
        await gw.say(OWNER, f"Remember this: {HERS}")
        told = gw.agent.told()
        lessons = _lessons(gw.home)
        asked = _asks(gw.state, "memory_remember")

    assert told == f"Saved lesson (global): {HERS}"
    assert lessons[HERS]["source"] == "user_explicit"
    assert not [e for e in asked if HERS in e["tool_input"]], "her own lesson asks nobody"


@pytest.mark.asyncio
async def test_a_dashboard_chat_still_remembers_at_once(tmp_path, monkeypatch):
    """The control: what the owner types in the dashboard is hers, as it always was."""
    from personalclaw.dashboard.chat_runner import run_chat
    from personalclaw.turn_source import DASHBOARD_SOURCE

    async with _gateway(tmp_path, monkeypatch) as gw:
        gw.agent.calls.append(("memory_remember", {"rule": HERS, "category": "preference"}))
        session = gw.state.get_or_create_session("chat-31-1790700500")
        session.append("user", f"Remember this: {HERS}", "msg msg-u", source=DASHBOARD_SOURCE)
        await run_chat(gw.state, session, f"Remember this: {HERS}")
        told = gw.agent.told()
        lessons = _lessons(gw.home)
        asked = _asks(gw.state, "memory_remember")

    assert told == f"Saved lesson (global): {HERS}"
    assert HERS in lessons and asked == []


# ── one check, wherever the turn writes from ─────────────────────────────────────────────────────


def _colleague() -> dict[str, str]:
    from personalclaw.turn_source import arrived_on

    return arrived_on("1790700000.000100", COLLEAGUE, PROVIDER)


def test_the_stores_refuse_a_write_in_a_colleagues_turn_and_say_why(tmp_path):
    """A write the turn makes in the gateway itself, past every tool, is refused by the store:
    the turn's scope names who asked, and the refusal says it in words."""
    store = VectorMemoryStore(db_path=tmp_path / "memory.db")
    store.init()
    try:
        with memory_writes.derived_from("dashboard:chat-32-1790700600"):
            memory_writes.asked_for(_colleague())
            with pytest.raises(memory_writes.MemoryWriteRefused) as refused:
                store.write_lesson(ABOUT_HER, source="user_explicit")
            assert memory_writes.changes_no_memory()
        assert refused.value.code == memory_writes.ASKED_BY_SOMEONE_ELSE
        assert refused.value.reason.startswith(
            "Nothing was written to the owner's memory: Jonas (U0JONASCOL) on teamchat asked"
        )
        # The control: her own turn writes.
        with memory_writes.derived_from("dashboard:chat-32-1790700600"):
            memory_writes.asked_for({})
            assert store.write_lesson(HERS, source="user_explicit")
    finally:
        store.close()


@pytest.mark.asyncio
async def test_a_colleagues_turn_changes_no_memory_document_through_the_file_tools(
    tmp_path, monkeypatch
):
    """🔴 Red on integration: the colleague's words were written into her preferences.md. The file
    tools ask the same check before they change a memory document, so in her colleague's turn
    ``write_file`` refuses it, saying who asked; in her own turn the document takes her words."""
    async with _gateway(tmp_path, monkeypatch, files=True) as gw:
        preferences = gw.home / "workspace" / "memory" / "preferences.md"
        before = preferences.read_text(encoding="utf-8")

        def write(line: str) -> tuple[str, dict]:
            content = preferences.read_text(encoding="utf-8") + f"- {line}\n"
            return ("write_file", {"path": "memory/preferences.md", "content": content})

        read = ("read_file", {"path": "memory/preferences.md"})
        gw.agent.calls.append([read, write(ABOUT_HER)])
        await gw.say(COLLEAGUE, f"Add this to Mira's preferences: {ABOUT_HER}")
        told = gw.agent.told()
        theirs = preferences.read_text(encoding="utf-8")
        gw.agent.calls.append([read, write(HERS)])
        await gw.say(OWNER, f"Add this to my preferences: {HERS}")
        hers_told = gw.agent.told()
        hers = preferences.read_text(encoding="utf-8")

    assert theirs == before, "nothing is written on someone else's say-so"
    assert "is part of long-term memory" in told, told
    assert "Jonas (U0JONASCOL) on teamchat asked for this" in told, told
    assert ABOUT_HER not in hers and HERS in hers, hers_told


def test_a_turn_someone_else_started_teaches_nothing_from_itself():
    """The turn's own learning asks the same check: a colleague's turn opens no write."""
    from types import SimpleNamespace

    from personalclaw.learning.gate import Cadence, GateReason, LearningGate

    chat = SimpleNamespace(key="chat-33-1790700700", memory_mode="persistent")
    cfg = SimpleNamespace(enabled=True, min_tool_calls=4, correction_heuristic=True)
    with memory_writes.derived_from(f"dashboard:{chat.key}"):
        memory_writes.asked_for(_colleague())
        theirs = LearningGate.for_session(chat, cfg).decide(Cadence.PER_TURN, correction=True)
    with memory_writes.derived_from(f"dashboard:{chat.key}"):
        memory_writes.asked_for({})
        hers = LearningGate.for_session(chat, cfg).decide(Cadence.PER_TURN, correction=True)
    assert not theirs.permitted and theirs.reason is GateReason.ASKED_BY_SOMEONE_ELSE
    assert hers.permitted


@pytest.mark.asyncio
async def test_a_subagent_a_colleagues_turn_starts_changes_no_memory_on_its_own(
    tmp_path, monkeypatch
):
    """Work the turn starts carries who asked for it: its subagent's lesson is held as well."""
    from types import SimpleNamespace

    from personalclaw import mcp_core
    from personalclaw.tool_providers.registry import create_memory_provider

    async with _gateway(tmp_path, monkeypatch) as gw:
        gw.state.get_or_create_session("chat-34-1790700800")
        chat = "dashboard:chat-34-1790700800"
        child = "subagent:5ab1e7c0"
        info = SimpleNamespace(parent_session_key=chat, app="")
        monkeypatch.setattr(gw.state, "subagents", SimpleNamespace(get=lambda _id: info, count=0))
        with memory_writes.derived_from(chat):
            memory_writes.asked_for(_colleague())
            memory_writes.hand_on(child, chat)
        token = mcp_core.set_current_session_key(child)
        try:
            result = await create_memory_provider().invoke(
                "memory_remember", {"rule": ABOUT_HER, "category": "preference"}
            )
        finally:
            mcp_core.reset_current_session_key(token)
        await _settled(gw.state)
        lessons = _lessons(gw.home)

    assert result.success, result.error
    assert result.output.startswith("Not saved yet: Jonas (U0JONASCOL) on teamchat asked")
    assert ABOUT_HER not in lessons


@pytest.mark.asyncio
async def test_a_turn_a_channel_runs_itself_names_who_asked_for_it(tmp_path, monkeypatch):
    """A channel that runs a thread's conversation itself says whose message its turn answers
    (``turn_asked_by``), and its agent's memory tools are held to it while the turn runs."""
    from personalclaw import mcp_core
    from personalclaw.sdk.channel import turn_asked_by
    from personalclaw.tool_providers.registry import create_memory_provider

    thread = "1790700900.000900"

    async def _remember(rule: str) -> str:
        token = mcp_core.set_current_session_key(thread)
        try:
            result = await create_memory_provider().invoke(
                "memory_remember", {"rule": rule, "category": "preference"}
            )
        finally:
            mcp_core.reset_current_session_key(token)
        assert result.success, result.error
        return str(result.output)

    async with _gateway(tmp_path, monkeypatch) as gw:
        with turn_asked_by(thread, _colleague()):
            theirs = await _remember(ABOUT_HER)
        from personalclaw.turn_source import arrived_on

        with turn_asked_by(thread, arrived_on(thread, OWNER, PROVIDER)):
            hers = await _remember(HERS)
        await _settled(gw.state)
        lessons = _lessons(gw.home)

    assert theirs.startswith("Not saved yet: Jonas (U0JONASCOL) on teamchat asked")
    assert hers == f"Saved lesson (global): {HERS}"
    assert ABOUT_HER not in lessons and HERS in lessons
    assert session_restrictions.asked_by(thread) is None, "the mark ends with the turn"


def test_a_merged_queue_holding_a_colleagues_message_is_not_the_owners_turn():
    """Messages that waited behind a running turn run as one row, which records no source of its
    own when they came from different people: it records each one's, so a merge holding the
    colleague's message is not a turn the owner alone started."""
    from personalclaw.turn_source import QUEUED_FROM, arrived_on, asked_by

    hers, theirs = arrived_on("t", OWNER, PROVIDER), arrived_on("t", COLLEAGUE, PROVIDER)
    merged = {"role": "user", "content": "two messages", "meta": {QUEUED_FROM: [hers, theirs]}}
    assert asked_by(merged) == theirs
    assert asked_by({"role": "user", "content": "x", "meta": {QUEUED_FROM: [hers, hers]}}) == {}
    assert asked_by({"role": "user", "content": "x", **hers}) == {}
    assert asked_by({"role": "user", "content": "x", **theirs}) == theirs
    assert asked_by({"role": "user", "content": "an imported line"}) == {}


def test_a_decision_resolved_in_a_colleagues_turn_says_why_it_kept_no_lesson(tmp_path):
    """The journal's lesson is a change to her memory like any other: in a colleague's turn none
    is kept, and the outcome says why rather than only that none was written. Her decision itself
    is resolved: the journal is her knowledge, not her memory."""
    import os

    from personalclaw.decisions import horizon_from_days, log_decision, resolve_decision
    from personalclaw.knowledge.store import KnowledgeStore
    from personalclaw.memory_service import MemoryService
    from personalclaw.triggers.store import TriggerStore

    store = KnowledgeStore(os.path.join(tmp_path, "knowledge.db"))
    triggers = TriggerStore(base_dir=tmp_path)
    records = VectorMemoryStore(db_path=tmp_path / "memory.db")
    records.init()
    try:
        item = log_decision(
            store=store,
            trigger_store=triggers,
            summary="Take the early train on offsite days",
            content="Reasoning: the venue opens at nine.",
            expectation="Nobody waits for me at the venue",
            confidence=0.6,
            domain="personal",
            review_horizon=horizon_from_days(30),
        )
        with memory_writes.derived_from("dashboard:chat-35-1790701000"):
            memory_writes.asked_for(_colleague())
            resolved = resolve_decision(
                item["id"],
                outcome="it worked, nobody waited",
                grade="as_expected",
                store=store,
                trigger_store=triggers,
                memory=MemoryService.over_vector_store(records),
            )
        lessons = list(records.get_lessons())
    finally:
        records.close()

    assert resolved["status"] == "resolved"
    assert resolved["lesson_memory_key"] is None and lessons == []
    assert resolved["lesson_not_kept"].startswith(
        "Nothing was written to the owner's memory: Jonas (U0JONASCOL) on teamchat asked"
    )


# ── an agent CLI's own tools ─────────────────────────────────────────────────────────────────────


def _a_memory_document() -> Path:
    """Her preferences.md in the home in use, where the memory store keeps it."""
    from personalclaw import memory

    return memory.memory_folders()[0] / "preferences.md"


#: What an agent CLI asks before one of its own tools changes her preferences.md: a write as
#: claude-code asks it (the file under ``file_path``), one as kiro asks it (the file under ``path``,
#: from the chat's folder, beside a ``command`` of its own that is no shell command), a shell
#: command that appends to the file, and a write of the CLI's own titled as one of PersonalClaw's
#: tools, whose arguments that tool does not take.
_CHANGES: dict[str, Any] = {
    "write": lambda doc: (f"Write {doc}", {"file_path": str(doc), "content": f"- {ABOUT_HER}\n"}),
    "fs_write": lambda _doc: (
        "fs_write",
        {"command": "append", "path": "memory/preferences.md", "fileText": f"- {ABOUT_HER}\n"},
    ),
    "shell": lambda _doc: (
        "Running: echo",
        {"command": f"echo '- {ABOUT_HER}' >> memory/preferences.md"},
    ),
    "titled_as_ours": lambda doc: (
        "mcp__personalclaw-core__notify_attachment",
        {"file_path": str(doc), "content": f"- {ABOUT_HER}\n"},
    ),
}

#: What it asks before a tool only reads the file: one of its own, its shell, and PersonalClaw's
#: own tool that sends a file, which PersonalClaw holds to its own checks.
_READS: dict[str, Any] = {
    "read": lambda _doc: ("Reading memory/preferences.md", {"path": "memory/preferences.md"}),
    "cat": lambda _doc: ("Running: cat", {"command": "cat memory/preferences.md"}),
    "send": lambda doc: (
        "mcp__personalclaw-core__notify_attachment",
        {"path": str(doc), "description": "her preferences"},
    ),
}


async def _cli_turn(
    tmp_path: Path, sender: str, title: str, args: dict, *, mode: str = "persistent"
) -> tuple[Any, list[tuple[Any, Any]], str]:
    """One turn of a chat with Trust on whose agent CLI asks about one call of its own tool, the
    turn's message sent by *sender* in a thread of the channel. Returns the CLI, the audit rows'
    (outcome, who decided), and the transcript's text."""
    from test_acp_permission_authority import (
        _context_builder,
        _drive,
        _make_state,
        _session,
        _set_stream,
    )

    from personalclaw.llm.base import EVENT_PERMISSION_REQUEST, LLMEvent
    from personalclaw.turn_source import arrived_on

    state, client = _make_state(tmp_path, context_builder=_context_builder())
    session = _session(trust=True)
    session.memory_mode = mode
    session.append(
        "user", "hello", "msg msg-u", source=arrived_on("1790701200.001200", sender, PROVIDER)
    )
    _set_stream(
        client,
        [
            LLMEvent(
                kind=EVENT_PERMISSION_REQUEST,
                title=title,
                tool_kind="edit",
                request_id="req-1",
                tool_input=json.dumps(args),
            ),
            LLMEvent(kind=EVENT_COMPLETE, stop_reason="end_turn"),
        ],
    )
    rows = MagicMock()
    await _drive(state, session, sel_mock=rows)
    decided = [
        (c.kwargs.get("outcome"), (c.kwargs.get("metadata") or {}).get("decided_by"))
        for c in rows.return_value.log_tool_invocation.call_args_list
        if c.kwargs.get("request_id") == "req-1"
    ]
    return client, decided, json.dumps([m.get("content") for m in session.messages])


@pytest.mark.asyncio
@pytest.mark.parametrize("change", sorted(_CHANGES))
async def test_an_agent_clis_own_tool_changes_no_memory_document_in_a_colleagues_turn(
    tmp_path, change
):
    """🔴 Red before: with the chat's Trust on, the agent CLI's own write to her preferences.md ran
    in her colleague's turn. PersonalClaw's file tools' check never sees an agent CLI's own tool,
    and nothing at the gate the CLI asks looked. Now the gate refuses the change before Trust could
    approve it, saying who asked; in her own turn Trust approves it as before."""
    title, args = _CHANGES[change](_a_memory_document())

    theirs, their_rows, their_chat = await _cli_turn(tmp_path, COLLEAGUE, title, args)
    hers, her_rows, _ = await _cli_turn(tmp_path, OWNER, title, args)

    theirs.reject_tool.assert_awaited_once_with("req-1")
    theirs.approve_tool.assert_not_awaited()
    assert their_rows == [("refused", memory_writes.ASKED_BY_SOMEONE_ELSE)], their_rows
    assert "is part of long-term memory" in their_chat, their_chat
    assert "Jonas (U0JONASCOL) on teamchat asked for this" in their_chat, their_chat
    hers.approve_tool.assert_awaited_once_with("req-1")
    hers.reject_tool.assert_not_awaited()
    assert her_rows and her_rows[0][0] == "auto_approved", her_rows


@pytest.mark.asyncio
@pytest.mark.parametrize("read", sorted(_READS))
async def test_an_agent_clis_read_of_a_memory_document_in_a_colleagues_turn_runs(tmp_path, read):
    """The control: a read changes nothing, so the gate leaves it to the chat's Trust."""
    title, args = _READS[read](_a_memory_document())
    client, rows, _ = await _cli_turn(tmp_path, COLLEAGUE, title, args)
    client.approve_tool.assert_awaited_once_with("req-1")
    assert rows and rows[0][0] == "auto_approved", rows


@pytest.mark.asyncio
async def test_an_agent_cli_running_before_an_incognito_chat_changes_no_memory_document(tmp_path):
    """The same gate holds an Incognito chat's agent CLI that was started unfenced, a process the
    chat shares: its own write to a memory document is refused when it asks, as PersonalClaw's
    file tools refuse one."""
    title, args = _CHANGES["write"](_a_memory_document())
    client, rows, chat = await _cli_turn(tmp_path, OWNER, title, args, mode="incognito")
    client.reject_tool.assert_awaited_once_with("req-1")
    client.approve_tool.assert_not_awaited()
    assert rows == [("refused", memory_writes.RESTRICTED_SESSION_BLOCK)], rows
    assert memory_writes.REFUSAL in chat


@pytest.mark.asyncio
@pytest.mark.parametrize("read", sorted(_READS))
async def test_an_agent_clis_read_of_a_memory_document_in_a_temporary_chat_is_refused(
    tmp_path, read
):
    """The gate asks the shell's own screen, which a Temporary chat's reads are held to as well:
    a Temporary chat starts blank, so its agent CLI's own read of a memory document is refused
    where the CLI asks. A call to PersonalClaw's own tool that sends a file is left to that tool,
    which holds the file back itself (``file_scope.held_from_reads``)."""
    from personalclaw.file_scope import MEMORY_WITHHELD

    title, args = _READS[read](_a_memory_document())
    client, rows, _ = await _cli_turn(tmp_path, OWNER, title, args, mode="temporary")
    if read == "send":
        client.approve_tool.assert_awaited_once_with("req-1")
        return
    client.reject_tool.assert_awaited_once_with("req-1")
    client.approve_tool.assert_not_awaited()
    assert rows == [("refused", MEMORY_WITHHELD)], rows


def test_a_call_a_channels_turn_puts_to_its_gate_is_held_to_who_asked():
    """A channel that runs a thread's turn itself puts its agent CLI's calls to the same gate, in
    its own process: the turn names who asked (``turn_asked_by``), and the gate reads it there."""
    from personalclaw.acp.permission_authority import screen_tool_call
    from personalclaw.hooks import TOOL_DENY
    from personalclaw.sdk.channel import turn_asked_by
    from personalclaw.turn_source import arrived_on

    title, args = _CHANGES["write"](_a_memory_document())
    thread = "1790701300.001300"
    with turn_asked_by(thread, _colleague()):
        theirs = screen_tool_call(None, title, json.dumps(args))
    with turn_asked_by(thread, arrived_on(thread, OWNER, PROVIDER)):
        hers = screen_tool_call(None, title, json.dumps(args))
    outside = screen_tool_call(None, title, json.dumps(args))

    assert theirs.action == TOOL_DENY, theirs
    assert theirs.control == memory_writes.ASKED_BY_SOMEONE_ELSE
    assert "Jonas (U0JONASCOL) on teamchat asked for this" in theirs.reason
    assert hers.action != TOOL_DENY and outside.action != TOOL_DENY


# ── what PersonalClaw's own passes take ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_consolidation_still_runs_when_a_colleagues_turn_ends(tmp_path, monkeypatch):
    """Consolidation reads the whole conversation and takes only her words from it, so who asked
    for the turn that just ended decides nothing of it. 🔴 Red before: its gate was asked as that
    turn's work, so a thread's consolidation was refused (``gate:asked_by_someone_else``) at the
    end of every turn a colleague asked for."""
    async with _gateway(tmp_path, monkeypatch) as gw:
        gw.state.consolidator = MagicMock()
        await gw.say(COLLEAGUE, "What time is the offsite dinner?")
        started = gw.state.consolidator.maybe_consolidate.call_args_list
        refused = [e for e in _audit("consolidate") if e.get("outcome") == "denied"]

    assert started, "the thread's consolidation was not started"
    assert refused == [], refused

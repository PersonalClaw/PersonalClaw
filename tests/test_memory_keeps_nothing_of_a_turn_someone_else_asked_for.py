"""Memory keeps nothing of a turn someone else asked for, whatever a model makes of it.

A shared group on a chat channel holds the owner and the people she lets talk to the agent. One of
them asks the agent to remember something about themselves ("I am vegetarian now, please remember
that for our meal plans"); the agent's memory tool waits for her word, and she says no. When the
group's session ended, consolidation read the whole conversation: their line, fenced and labelled
as not the user's words, the refused call, and the agent's answer to them. A small local model read
past the label and filed their claim as hers: two episodes, a line in preferences.md, the day's
history and the session's sealed summary. A label a model reads was the only control.

Now consolidation reads only the turns the owner asked for (``own_words.her_turns``): a turn
someone else asked for gives it nothing, not their message, not what the agent did or said for
them, and nor does a turn in which she refused a change to her memory. So whatever the model makes
of what it is shown, it is never shown their words. Her own messages in the same group are
consolidated as before. The same rule holds for the report of a helper someone else's turn
started, for work that lasts (a loop) someone else asked for, for the turn's own learning after her
refusal, and for the suggestions written for her from recent chats.

Driven as the gateway drives it: the real gateway over a scratch home, the door the group's
messages cross, the real turn engine on a native runtime whose scripted model calls the memory
tool, her Deny through the approval registry, the gateway's own helper-report delivery, and the
real consolidation and seal, with a scripted consolidation model that takes every line it is shown
for the user's own words.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import test_a_turn_someone_else_started_changes_no_memory_on_its_own as shared
from test_a_turn_someone_else_started_changes_no_memory_on_its_own import (  # noqa: F401
    COLLEAGUE,
    OWNER,
    PROVIDER,
    _a_shared_channel,
    _asks,
    _settled,
    _unmarked,
)

from personalclaw import approval_answer, memory_writes
from personalclaw.history import ConversationLog, HistoryConsolidator
from personalclaw.llm.events import EVENT_COMPLETE, EVENT_TEXT_CHUNK, EVENT_TOOL_CALL, AgentEvent
from personalclaw.memory import MemoryStore
from personalclaw.skills import SkillsLoader
from personalclaw.turn_source import arrived_on
from personalclaw.vector_memory import VectorMemoryStore

#: What someone else in the group asks the agent to keep about themselves, and the agent's answer.
THEIRS = "I am vegetarian now, please remember that for our meal plans."
TO_THEM = "I can't save that without the owner's word, but I'll plan our meals as vegetarian here."
#: What the owner says herself in the same group.
HERS = "Book the lake cabin for the first weekend of June."

#: What the scripted consolidation model files as the user's for a cue it finds in what it is shown:
#: every line it reads is hers to it, whatever label the line carries.
FILED = (
    ("vegetarian", "Diet: vegetarian, for every meal plan"),
    ("lake cabin", "Trips: the lake cabin, first weekend of June"),
    ("corner bistro", "Plans: dinner at the corner bistro on Thursday"),
    ("seafood", "Never book seafood places"),
)
_CONVERSATION = "## Conversation to Process"


def _between(prompt: str, head: str, tail: str) -> str:
    if head not in prompt:
        return ""
    return prompt.split(head, 1)[1].split(tail, 1)[0]


def _misreading_model(prompts: list[str]):
    """A consolidation model that keeps, as the user's own, every cue it finds in the conversation
    it is shown (:data:`FILED`): as an episode, a line of preferences.md and the day's entry. It is
    what a small model that reads past a label does, on purpose, so what holds is what it is shown.
    """

    async def run_chore(prompt: str, **_kw: Any) -> str:
        prompts.append(prompt)
        if _CONVERSATION not in prompt:
            return "{}"  # a formation pass's question: nothing to decide
        shown = prompt.split(_CONVERSATION, 1)[1].lower()
        kept = [fact for cue, fact in FILED if cue in shown]
        prefs = _between(prompt, "## Current Preferences\n", "\n\n## Current Projects")
        projects = _between(prompt, "## Current Projects\n", f"\n\n{_CONVERSATION}")
        return json.dumps(
            {
                "history_entry": "In this conversation: " + ("; ".join(kept) or "nothing"),
                "semantic": [],
                "episodic": [{"text": fact, "tags": ["kept"], "importance": 0.8} for fact in kept],
                "preferences_update": prefs.rstrip("\n")
                + "\n"
                + "".join(f"- {fact}\n" for fact in kept),
                "projects_update": projects,
                "lessons": [],
                "self_persona": [],
            }
        )

    return run_chore


class _Answering(shared._Agent):
    """The shared scripted agent, answering each turn with the next of its answers once its calls
    are made ("Noted." when none is left)."""

    def __init__(self) -> None:
        super().__init__()
        self.answers: list[str] = []

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
            text = self.answers.pop(0) if self.answers else "Noted."
            yield AgentEvent(kind=EVENT_TEXT_CHUNK, text=text)
        yield AgentEvent(kind=EVENT_COMPLETE, input_tokens=10, output_tokens=3)


@pytest.fixture
def gateway(tmp_path, monkeypatch):
    """The shared channel's gateway (``shared._gateway``) with an agent that answers in words, and
    the links the door makes kept, so every message of the thread reaches its one chat."""
    from chat_test_helpers import links_kept_in_a_session_map

    monkeypatch.setattr(shared, "_Agent", _Answering)

    @contextlib.asynccontextmanager
    async def _one_chat_per_thread():
        async with shared._gateway(tmp_path, monkeypatch) as gw:
            links_kept_in_a_session_map(gw.state.sessions)
            yield gw

    return _one_chat_per_thread


def _history_key(gw: Any, session_key: str) -> str:
    from personalclaw.dashboard.chat_utils import persisted_history_key

    log = gw.state.conversation_log
    assert log is not None
    return persisted_history_key(log, session_key)


async def _consolidate(gw: Any, key: str, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """End the conversation *key* as an idle session ends (``consolidate_session``: its pass, then
    its seal) over the home's own memory, the scripted model answering; returns what it was asked.
    """
    prompts: list[str] = []
    monkeypatch.setattr("personalclaw.chores.run_chore", _misreading_model(prompts))
    memory = MemoryStore(workspace=gw.home / "workspace")
    memory.init()
    records = VectorMemoryStore(db_path=gw.home / "memory.db")
    records.init()
    memory.vector_store = records
    consolidator = HistoryConsolidator(
        log=gw.state.conversation_log,
        memory=memory,
        vector_store=records,
        skills_loader=SkillsLoader(skills_path=gw.home / "skills", install_builtins=False),
    )
    try:
        await consolidator.consolidate_session(key)
    finally:
        records.close()
    return prompts


def _kept(home: Path) -> str:
    """Every word her memory holds now: each live record, preferences.md and the daily history."""
    store = VectorMemoryStore(db_path=home / "memory.db")
    store.init()
    try:
        texts = [record.text for record in store.iter_records()]
    finally:
        store.close()
    documents = home / "workspace" / "memory"
    texts.append((documents / "preferences.md").read_text(encoding="utf-8"))
    texts += [day.read_text(encoding="utf-8") for day in (documents / "history").glob("*.md")]
    return "\n".join(texts).lower()


def _sealed(home: Path) -> list[str]:
    """The summaries the seal of an ended session kept (``MemoryService.seal_session``)."""
    store = VectorMemoryStore(db_path=home / "memory.db")
    store.init()
    try:
        return [r.text for r in store.iter_records(kinds={"episodic"}) if "sealed" in r.tags]
    finally:
        store.close()


async def _theirs_refused(gw: Any) -> None:
    """Someone else in the group asks the agent to remember their claim; the agent's memory tool
    waits for the owner, and she says no."""
    gw.agent.calls.append(("memory_remember", {"rule": THEIRS, "category": "preference"}))
    gw.agent.answers.append(TO_THEM)
    await gw.say(COLLEAGUE, THEIRS)
    (ask,) = _asks(gw.state, "memory_remember")
    assert gw.state.resolve_approval(ask["id"], False, by=approval_answer.YOU)
    await _settled(gw.state)


# ── a claim someone else made and she refused, in a shared group ─────────────────────────────────


@pytest.mark.asyncio
async def test_a_claim_someone_else_made_and_she_refused_reaches_none_of_her_memory(
    gateway, monkeypatch
):
    """🔴 Red on integration: the consolidation model was shown their line, fenced and labelled,
    and the agent's answer to them, and what it filed as hers was written to every store: an
    episode, preferences.md, the day's history and the sealed summary. Her own line in the same
    group is the control: it is consolidated as before."""
    async with gateway() as gw:
        await _theirs_refused(gw)
        gw.agent.answers.append("I'll book the lake cabin for the first weekend of June.")
        await gw.say(OWNER, HERS)
        key = _history_key(gw, gw.chat().key)
        prompts = await _consolidate(gw, key, monkeypatch)
        kept = _kept(gw.home)
        sealed = _sealed(gw.home)

    assert "vegetarian" not in kept, "their claim is in her memory"
    assert sealed and all("vegetarian" not in s.lower() for s in sealed), sealed
    (prompt,) = [p for p in prompts if _CONVERSATION in p]
    shown = prompt.split(_CONVERSATION, 1)[1]
    assert "vegetarian" not in shown.lower(), "their turn reached the model"
    assert THEIRS not in prompt and TO_THEM not in prompt
    assert f"USER: {HERS}" in shown, "her own line is consolidated as before"
    assert "lake cabin" in kept


@pytest.mark.asyncio
async def test_a_session_of_nothing_but_someone_elses_turn_asks_no_model_and_keeps_nothing(
    gateway, monkeypatch
):
    """🔴 Red on integration: the group's session held only their turn, and its consolidation was
    still sent to a model, which filed their claim as hers. Nothing in it is hers, so no model is
    asked and nothing is kept, and the pass ends as one that found nothing to keep does: its
    messages count as consolidated and the maintenance of the memory runs."""
    maintained: list[str] = []

    async def _maintain(_self: Any, key: str, _memory: Any, _svc: Any) -> None:
        maintained.append(key)

    monkeypatch.setattr(HistoryConsolidator, "_maintain", _maintain)
    async with gateway() as gw:
        await _theirs_refused(gw)
        key = _history_key(gw, gw.chat().key)
        prompts = await _consolidate(gw, key, monkeypatch)
        kept = _kept(gw.home)
        left = gw.state.conversation_log.unconsolidated_count(key)

    assert [p for p in prompts if _CONVERSATION in p] == [], "a model was asked"
    assert "vegetarian" not in kept
    assert left == 0, "the messages it read were not counted as consolidated"
    assert maintained == [key], "the pass skipped the maintenance a pass that kept nothing runs"


# ── a turn in which she refused a change to her memory ───────────────────────────────────────────


class _AsksToRemember:
    """A model that, in a turn whose request asks it to remember, calls the memory tool with the
    request's words, and otherwise answers."""

    supports_tools = True
    _model = "scripted"

    async def complete(self, messages, *, tools=None, model=None, reasoning_effort=""):
        from personalclaw.context import USER_REQUEST_MARKER

        last = messages[-1] if messages else {}
        request = str(last.get("content") or "").rpartition(USER_REQUEST_MARKER)[2]
        if last.get("role") == "user" and "remember" in request.lower():
            yield AgentEvent(
                kind=EVENT_TOOL_CALL,
                tool_call_id=f"call-{len(messages)}",
                title="memory_remember",
                tool_input=json.dumps({"rule": request.strip(), "category": "preference"}),
            )
            yield AgentEvent(kind=EVENT_COMPLETE)
            return
        yield AgentEvent(kind=EVENT_TEXT_CHUNK, text="All right.")
        yield AgentEvent(kind=EVENT_COMPLETE)

    async def cancel(self, *, wait_ack_timeout: float = 0.0) -> str:
        return "acked"


def _memory_tool() -> Any:
    """The memory tool by its name, which asks before it runs, as a chat's Normal posture asks
    before every change."""
    from personalclaw.tool_providers.base import ToolDefinition, ToolProvider, ToolResult

    class _Remember(ToolProvider):
        @property
        def name(self) -> str:
            return "memory"

        @property
        def display_name(self) -> str:
            return "Memory"

        async def list_tools(self) -> list[ToolDefinition]:
            return [
                ToolDefinition(
                    name="memory_remember",
                    description="Save a lesson.",
                    parameters={"type": "object"},
                    requires_approval=True,
                )
            ]

        async def invoke(self, tool_name: str, arguments: dict[str, Any]) -> ToolResult:
            return ToolResult(success=True, output=f"Saved lesson (global): {arguments['rule']}")

    return _Remember()


async def _her_engine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Any, Any]:
    """A dashboard state whose chat's turns run through the real chat runner and agent loop, the
    memory tool asking before it runs, learning into a record store of the test's own."""
    from personalclaw.agents.native.runtime import NativeAgentRuntime
    from personalclaw.agents.provider import AgentRuntimeDefinition
    from personalclaw.config import loader as config_loader
    from personalclaw.context import ContextBuilder
    from personalclaw.dashboard.state import DashboardState
    from personalclaw.memory_service import MemoryService

    store = VectorMemoryStore(db_path=tmp_path / "memory.db")
    store.init()
    svc = MemoryService.over_vector_store(store)
    monkeypatch.setattr("personalclaw.memory_service.service_for", lambda _provider: svc)
    config_loader.config_dir()
    config_loader.config_path().write_text(
        json.dumps({"learning": {"skill_ladder": False}}), encoding="utf-8"
    )
    runtime = NativeAgentRuntime(
        definition=AgentRuntimeDefinition(name="PersonalClaw", provider="native", model="scripted"),
        model_provider=_AsksToRemember(),
        tool_providers=[_memory_tool()],
        cwd=tmp_path,
    )
    await runtime.start()
    runtime.set_approval_policy("")
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
    return state, svc


async def _she_says(state: Any, text: str, *, answer: str = "") -> None:
    """She types *text* in her chat ``diet-plans``; when its turn asks to save something, she
    answers *answer* (``rejected`` or ``approved``) on its card."""
    from personalclaw.approval_answer import YOU
    from personalclaw.dashboard.chat_runner import run_chat

    session = state.get_or_create_session("diet-plans")
    session.append("user", text, "msg msg-u")
    with patch("personalclaw.dashboard.chat_runner.sel", MagicMock()):
        task = asyncio.create_task(run_chat(state, session, text))
        if answer:
            for _ in range(400):
                if session._approval_futures:
                    break
                await asyncio.sleep(0.005)
            (request_id,) = list(session._approval_futures)
            state.decide_session_approval(session, request_id, answer, by=YOU)
        await asyncio.wait_for(task, timeout=10)


@pytest.mark.asyncio
async def test_what_she_refused_to_have_saved_is_not_kept_later(tmp_path, monkeypatch):
    """🔴 Red on integration: she said no to saving it, and both the turn's own learning (the
    veto in her words) and consolidation (her words, beside the refused call) kept it anyway. A
    turn in which she refused a change to her memory keeps nothing; her next turn, where she
    refused nothing, teaches and is consolidated as before."""
    from personalclaw.dashboard.chat_utils import persisted_history_key

    state, svc = await _her_engine(tmp_path, monkeypatch)
    refused = "Never book seafood places for me, remember that."
    await _she_says(state, refused, answer="rejected")
    lessons_after_her_no = [str(json.loads(r["value_json"])) for r in svc.get_lessons()]
    await _she_says(state, f"No, wrong: never book anything far from the {HERS[9:19]}.")
    await _she_says(state, HERS)
    lessons = [str(json.loads(r["value_json"])) for r in svc.get_lessons()]

    log = state.conversation_log
    key = persisted_history_key(log, state.get_or_create_session("diet-plans").key)
    gw = type("_Home", (), {"home": tmp_path, "state": state})()
    prompts = await _consolidate(gw, key, monkeypatch)
    (prompt,) = [p for p in prompts if _CONVERSATION in p]
    shown = prompt.split(_CONVERSATION, 1)[1]

    assert not any("seafood" in lesson.lower() for lesson in lessons_after_her_no), lessons
    assert "seafood" not in shown.lower(), "the turn she refused to have kept reached the model"
    assert f"USER: {HERS}" in shown, "her next turns are consolidated as before"
    assert any("far from the" in lesson for lesson in lessons), "her next turn teaches as before"


# ── a helper's report, and work that lasts, someone else asked for ───────────────────────────────


@pytest.mark.asyncio
async def test_a_helpers_report_someone_elses_turn_asked_for_is_not_consolidated(
    gateway, monkeypatch
):
    """🔴 Red on integration: the report of a helper a colleague's turn started was handed to an
    idle chat with no row of its own, so its turn read as the chat's own, and consolidation filed
    the work done for them as hers. The report's row now records who asked for its work, and its
    turn is theirs."""
    from test_work_someone_else_asked_for_keeps_who_asked import (
        CHAT,
        CHAT_NAME,
        _an_agent,
        _completion,
        _Turn,
    )

    from personalclaw.subagent import agent_work_id

    async with gateway() as gw:
        gw.state.get_or_create_session(CHAT_NAME)
        with _Turn(shared._colleague()):
            memory_writes.hand_on(agent_work_id("5ab1e7c4"), CHAT)
        gw.agent.answers.append("Booked: the corner bistro at seven on Thursday.")
        await _completion(gw.state)([_an_agent("5ab1e7c4")])
        await _settled(gw.state)
        key = _history_key(gw, CHAT_NAME)
        rows = gw.state.conversation_log.read_messages(key)
        prompts = await _consolidate(gw, key, monkeypatch)
        kept = _kept(gw.home)

    assert "corner bistro" not in kept
    assert not any("corner bistro" in p for p in prompts)
    from personalclaw.turn_source import ASKED_FOR_BY

    (report,) = [r for r in rows if r.get("role") == "subagent"]
    assert report["meta"][ASKED_FOR_BY] == shared._colleague()


@pytest.mark.asyncio
async def test_a_report_that_waited_behind_her_turn_records_who_asked_for_it(gateway, monkeypatch):
    """🔴 Red on integration: a report queued while the chat was busy ran with its row reading as
    the chat's own, and consolidation took the work done for the colleague as hers. Her own turn
    before it is the control."""
    from personalclaw.dashboard.chat_runner import run_chat
    from personalclaw.turn_source import DASHBOARD_SOURCE

    report = "[Subagent completion event]\nAgent `5ab1e7c5` completed\nTask: dinner\n\nBooked."
    async with gateway() as gw:
        chat = gw.state.get_or_create_session("chat-51-1790760000")
        chat.queue_append(report, asked_for_by=shared._colleague())
        gw.agent.answers += [
            "I'll book the lake cabin for the first weekend of June.",
            "Booked: the corner bistro at seven on Thursday.",
        ]
        chat.append("user", HERS, "msg msg-u", source=DASHBOARD_SOURCE)
        await run_chat(gw.state, chat, HERS)
        await _settled(gw.state)
        key = _history_key(gw, chat.key)
        prompts = await _consolidate(gw, key, monkeypatch)
        kept = _kept(gw.home)

    (prompt,) = [p for p in prompts if _CONVERSATION in p]
    assert "corner bistro" not in prompt.lower() and "corner bistro" not in kept
    assert "lake cabin" in kept, "her own turn is consolidated as before"


@pytest.mark.asyncio
async def test_a_loop_someone_else_asked_for_keeps_nothing_of_its_sessions(gateway, monkeypatch):
    """🔴 Red on integration: a loop a colleague's turn made worked in a session of its own, and
    that session's consolidation filed what it did for them as hers. Work someone else asked for
    keeps nothing in her memory, and no model is asked to."""
    from test_work_someone_else_asked_for_keeps_who_asked import _a_loop, _Turn

    from personalclaw.dashboard.chat_runner import run_chat
    from personalclaw.loop import manager as loop_manager

    async with gateway() as gw:
        with _Turn(shared._colleague()):
            loop = _a_loop()
        # One cycle of the loop's worker, as its scheduler starts it: its nudge, in its own chat.
        worker = gw.state.get_or_create_session(name=loop_manager.session_key(loop.id), app="loop")
        nudge = "[cycle 2] Carry on with the loop's task."
        worker.append("nudge", nudge, "msg msg-nudge")
        gw.agent.answers.append("Noted: Jonas eats vegetarian at every team dinner.")
        await run_chat(gw.state, worker, nudge)
        await _settled(gw.state)
        key = _history_key(gw, worker.key)
        prompts = await _consolidate(gw, key, monkeypatch)
        kept = _kept(gw.home)

    assert [p for p in prompts if _CONVERSATION in p] == []
    assert "vegetarian" not in kept


# ── the suggestions written for her ──────────────────────────────────────────────────────────────


def test_the_suggestions_read_only_the_lines_she_sent(tmp_path, monkeypatch):
    """🔴 Red on integration: a suggestion is put in her message box to send as her own words, and
    it was written from every user line of her recent chats, labelled ``User:``, someone else's in
    a shared group among them."""
    from personalclaw.dashboard.state import DashboardState
    from personalclaw.session import SessionManager
    from personalclaw.suggestions import _build_context

    monkeypatch.setattr("personalclaw.config.loader.AppConfig.load", lambda *a, **k: MagicMock())
    log = ConversationLog(base_dir=tmp_path / "sessions")
    group = "1790700000.000100"
    for sender, text in ((COLLEAGUE, THEIRS), (OWNER, HERS)):
        log.append(
            "chat-52-1790770000",
            "user",
            text,
            source_thread=group,
            source_user=sender,
            source_channel=PROVIDER,
        )
        log.append("chat-52-1790770000", "assistant", "Noted.")
    state = DashboardState(
        sessions=MagicMock(spec=SessionManager), start_time=0.0, conversation_log=log
    )

    context = _build_context(state)

    assert f"User: {HERS}" in context
    assert "vegetarian" not in context.lower()


# ── the rule, on a conversation's rows ───────────────────────────────────────────────────────────


def _row(role: str, content: str, **extra: Any) -> dict[str, Any]:
    return {"role": role, "content": content, **extra}


def test_a_turn_is_read_by_who_asked_for_the_row_that_started_it():
    """Each row belongs to the turn the row before it that starts one started, and the turn is
    hers or not by that row (``turn_source.asked_by``): their message, what the agent did for them
    and its answer are theirs. What she sent in such a turn is hers: a message she sent into it as
    it ran (a steer joins the turn it was sent into), her part of a row her queued message and
    theirs run as."""
    from personalclaw.own_words import OWN_WORDS, her_turns
    from personalclaw.turn_source import QUEUED_FROM, STEERED

    theirs = arrived_on("t", COLLEAGUE, PROVIDER)
    hers = arrived_on("t", OWNER, PROVIDER)
    rows = [
        _row("assistant", "Welcome back."),
        _row("user", THEIRS, **theirs),
        _row("tool", "memory_remember"),
        _row("assistant", TO_THEM),
        _row("user", "Also make it quick.", meta={STEERED: True}),
        _row("assistant", "Quick it is, for them."),
        _row("user", HERS, **hers),
        _row("assistant", "Booked the cabin."),
        _row(
            "user",
            f"{HERS}\n\n{THEIRS}",
            meta={QUEUED_FROM: [hers, theirs], OWN_WORDS: HERS},
        ),
        _row("assistant", "Both noted."),
    ]

    kept = her_turns(rows)

    assert [r["content"] for r in kept] == [
        "Welcome back.",
        "Also make it quick.",
        HERS,
        "Booked the cabin.",
        f"{HERS}\n\n{THEIRS}",
    ]
    assert her_turns(rows, start=7) == [rows[7], rows[8]]


def test_a_refusal_of_a_change_to_her_memory_is_read_from_the_turns_own_rows():
    """Her Deny of a memory tool's call, by whatever name the agent's runtime gives that tool, is
    the record; a Deny of another call is not, and neither is an approval nobody answered."""
    from personalclaw.declined_calls import declined_a_memory_change

    def turn(title: str, resolved: str) -> list[dict[str, Any]]:
        return [
            _row("user", "Remember that I'm vegetarian this week."),
            _row("permission", title, cls=json.dumps({"request_id": "r1", "resolved": resolved})),
            _row("tool", f"{title} (rejected)"),
        ]

    for title in (
        "memory_remember",
        "mcp__personalclaw-core__memory_forget",
        "Running: @personalclaw-core/triage_rules",
    ):
        assert declined_a_memory_change(turn(title, "rejected")), title
    assert not declined_a_memory_change(turn("memory_remember", "approved"))
    assert not declined_a_memory_change(turn("memory_remember", "expired"))
    assert not declined_a_memory_change(turn("bash", "rejected"))
    assert not declined_a_memory_change(turn("memory_recall", "rejected")), "a read changes nothing"


@pytest.mark.asyncio
async def test_a_pass_shows_its_model_only_the_messages_it_counts():
    """The pass reads whose turn its new messages are in from the whole transcript, a second read:
    a message sent between the two reads is not one it counted, and so not one it marks
    consolidated, so it waits for the pass that counts it rather than being kept twice."""
    counted = _row("user", HERS)
    sent_since = _row("user", "And dinner at the corner bistro on Thursday.")
    log = MagicMock()
    log.get_unconsolidated = MagicMock(return_value=([counted], 1))
    log.read_messages = MagicMock(return_value=[counted, sent_since])
    log.get_metadata = MagicMock(return_value={})
    memory = MagicMock()
    memory.read_preferences = MagicMock(return_value="")
    memory.read_projects = MagicMock(return_value="")
    consolidator = HistoryConsolidator(log=log, memory=memory, vector_store=None, migrated=True)
    consolidator._maintain = AsyncMock()  # type: ignore[method-assign]
    prompts: list[str] = []

    async def _model(prompt: str, _key: str, **_kw: object) -> dict:
        prompts.append(prompt)
        return {"history_entry": "booked the lake cabin"}

    consolidator._call_llm = _model  # type: ignore[method-assign]
    await consolidator._consolidate_locked("k", include_history=True)

    (prompt,) = prompts
    shown = prompt.split(_CONVERSATION, 1)[1]
    assert HERS in shown and "corner bistro" not in shown, shown
    log.mark_consolidated.assert_called_once_with("k", 1)


# ── what an earlier version kept ─────────────────────────────────────────────────────────────────


def test_what_consolidation_kept_of_a_group_she_never_wrote_in_is_taken_back(tmp_path):
    """🔴 Red on integration: nothing took back what an earlier version's consolidation kept of a
    conversation only other people asked anything in. At the start it goes, with the daily history
    entry that repeats it, and twice changes nothing. What she allowed herself there stays, and so
    does everything of a conversation she wrote in too, or one whose senders nothing can place."""
    from personalclaw.memory_writes import take_back_what_others_turns_left

    thread = "1790800000.000100"
    log = ConversationLog(base_dir=tmp_path / "sessions")
    group, mixed, legacy = (
        "dashboard:chat-60-1790800000",
        "dashboard:chat-61-1790800100",
        "dashboard:chat-62-1790800200",
    )
    for key, said in (
        (group, [(COLLEAGUE, PROVIDER, THEIRS)]),
        (mixed, [(OWNER, PROVIDER, HERS), (COLLEAGUE, PROVIDER, THEIRS)]),
        (legacy, [(COLLEAGUE, None, THEIRS)]),  # saved before rows named their channel
    ):
        for sender, channel, text in said:
            log.append(
                key,
                "user",
                text,
                source_thread=thread,
                source_user=sender,
                source_channel=channel,
            )
            log.append(key, "assistant", TO_THEM)
    store = VectorMemoryStore(db_path=tmp_path / "memory.db")
    store.init()
    markdown = MemoryStore(workspace=tmp_path / "ws")
    markdown.init()
    summary = "In this conversation: Diet: vegetarian, for every meal plan"
    allowed = "Jonas is vegetarian, so plan the team dinners with that in mind."
    try:
        for key in (group, mixed, legacy):
            with memory_writes.derived_from(key, memory_mode="persistent"):
                assert store.write_episodic(
                    f"Diet: vegetarian ({key})", conversation_id=key, source=f"consolidation:{key}"
                )
        with memory_writes.derived_from(group, memory_mode="persistent"):
            assert store.write_episodic(
                summary, conversation_id=group, tags=["sealed", "session"], source="seal"
            )
            assert store.write_lesson("Plan vegetarian meals for everyone", source="consolidation")
            with memory_writes.on_the_owners_word():
                assert store.write_lesson(allowed, source="user_explicit")
        markdown.append_history(summary)
        markdown.append_history("Trips: the lake cabin, first weekend of June")

        removed = take_back_what_others_turns_left(store, markdown, log)
        again = take_back_what_others_turns_left(store, markdown, log)
        live = [r.text for r in store.iter_records()]
    finally:
        store.close()
    history = "\n".join(
        day.read_text(encoding="utf-8") for day in markdown._history_dir.glob("*.md")
    )

    assert removed == 4, removed  # its episode, its sealed summary, its lesson, and the entry
    assert again == 0
    assert not any(group in text for text in live) and summary not in live
    assert "Plan vegetarian meals for everyone" not in live
    assert allowed in live, "what she allowed herself is hers"
    assert f"Diet: vegetarian ({mixed})" in live, "a conversation she wrote in keeps its pass's"
    assert f"Diet: vegetarian ({legacy})" in live, "nothing places the senders there"
    assert summary not in history and "lake cabin" in history

"""Memory takes only the owner's own words, whoever else writes in the conversation.

A conversation on a chat channel can have more than one person in it: a group where a friend the
owner paired writes too, a shared channel thread where a colleague she allowed answers, a mailbox a
correspondent she trusts writes to, a direct message an open channel lets anyone send. The door
lets each of them in, and their message enters the chat as a user line, unfenced, because the agent
may act on what they ask. Memory read every user line as hers: consolidation was shown a friend's
"Rin is allergic to peanuts" as something the user said and kept it as a fact she stated, and a
correction someone else typed was learned as hers.

Each line records who sent it and on which channel (``turn_source``). Whether the owner sent it is
answered from that record and the owner the channel keeps (``turn_source.sent_by_owner``), and
every reader of the owner's words asks it (``own_words.own_words``): consolidation shows another
person's line as theirs, fenced, and a turn's learning takes nothing from it. What the owner sends
in the same conversation still counts, and so does what she types in the dashboard.

The door, the dashboard state, the conversation log, the trust store and the turn engine are real,
in this test's home; only the models are stand-ins.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from personalclaw import channel_inbound as ci
from personalclaw import channel_trust as ct
from personalclaw.agents.native.runtime import NativeAgentRuntime
from personalclaw.agents.provider import AgentRuntimeDefinition
from personalclaw.channel_transports.base import ChannelMessage
from personalclaw.config import loader as config_loader
from personalclaw.config.credentials import (
    delete_credential,
    owner_id_credential,
    save_credential,
)
from personalclaw.config.loader import CRED_OWNER_ID, AppConfig
from personalclaw.context import USER_REQUEST_MARKER, ContextBuilder
from personalclaw.dashboard.chat_persistence import save_all_sessions_to_history
from personalclaw.dashboard.chat_runner import run_chat
from personalclaw.dashboard.chat_utils import persisted_history_key
from personalclaw.dashboard.state import DashboardState
from personalclaw.history import ConversationLog, HistoryConsolidator, consolidation_line
from personalclaw.llm.events import EVENT_COMPLETE, EVENT_TEXT_CHUNK, EVENT_TOOL_CALL, AgentEvent
from personalclaw.memory import MemoryStore
from personalclaw.memory_record import MemoryKind
from personalclaw.memory_service import MemoryService
from personalclaw.own_words import own_words
from personalclaw.session import SessionManager
from personalclaw.skills import SkillsLoader
from personalclaw.tool_providers.base import ToolDefinition, ToolProvider, ToolResult
from personalclaw.turn_source import DASHBOARD_SOURCE, arrived_on
from personalclaw.vector_memory import VectorMemoryStore

#: A chat channel this test sets up, by its provider key.
PROVIDER = "groupchat"
#: A group the bot is in: one conversation everyone in it shares.
GROUP = "-1009000"
#: The owner's id on the channel, as its owner pairing stored it.
OWNER = "4242"
#: Someone else in the group, whom the owner trusts to talk to the agent.
FRIEND = "5151"
#: A second friend in the group, trusted too.
OTHER_FRIEND = "6262"

#: What the friend says about the owner, and what the owner says herself.
ABOUT_HER = "Rin is allergic to peanuts and her favourite food is Thai, so plan the menu for that"
HERS = "Book the lake cabin for the first weekend of June"

#: A channel that runs its conversations itself, a thread of it, and who wrote there.
SELF_RUN = "threadchat"
THREAD = "1700000300.000300"
OWNER_THERE = "U0RINOWNER"
COLLEAGUE = "U0OLACOLL"

#: A tool the agent calls when a message asks for the forecast: what a turn did, beside what its
#: message said, and what procedural memory keeps of one call of it that worked.
FORECAST = "check_the_forecast"
ASKS_FOR_IT = "check the forecast for the lake"
FORECAST_PRIOR = f"{FORECAST} on '{FORECAST}' → success"
#: A correction of hers, and one that reads as a correction from whoever sends it.
HER_CORRECTION = "that's not what I meant, keep the plan to three lines"
THEIR_CORRECTION = "that's not what I meant, never book anything near a road"


@pytest.fixture(autouse=True)
def _a_group_with_a_friend_in_it(unset_env):
    """The channel knows its owner, the owner and her friend are both let in, and the group is
    tracked, so a message from either of them crosses the door unfenced."""
    unset_env(CRED_OWNER_ID, owner_id_credential(PROVIDER), owner_id_credential(SELF_RUN))
    save_credential(owner_id_credential(PROVIDER), OWNER)
    save_credential(owner_id_credential(SELF_RUN), OWNER_THERE)
    ct.allow_sender(PROVIDER, OWNER, name="Rin")
    ct.allow_sender(PROVIDER, FRIEND, name="Ola")
    ct.allow_sender(PROVIDER, OTHER_FRIEND, name="Kai")
    ct.track(PROVIDER, GROUP, "Lake trip")
    yield


class _Gateway:
    """The dashboard state and session store over this test's home, and the door the group's
    messages are delivered through (``GatewayServices.deliver_channel_inbound``)."""

    def __init__(self, state: DashboardState) -> None:
        self.dashboard_state = state
        self._count = 0

    async def say(self, sender: str, text: str, *, turn_runner: Any) -> None:
        """*sender* writes *text* in the group, and the door hands it to the group's chat."""
        self._count += 1
        msg = ChannelMessage(
            channel_id=GROUP,
            text=text,
            sender=sender,
            thread_id=GROUP,
            message_id=f"m-{self._count}",
        )
        verdict = await ci.deliver_inbound(
            self, PROVIDER, msg, is_dm=False, turn_runner=turn_runner
        )
        assert verdict.allowed and not verdict.fenced_text, verdict
        for _ in range(50):
            running = {t for t in self.dashboard_state._background_tasks if not t.done()}
            if not running:
                return
            await asyncio.wait(running, timeout=10)
        raise AssertionError("turns kept starting")


async def _notes_it(state: Any, session: Any, message: str) -> None:
    """A turn that answers without asking a model: the rows are what this is about."""
    session.append("assistant", "Noted.", "msg msg-a")


def _plain_state(home: Path) -> DashboardState:
    return DashboardState(
        sessions=SessionManager(AppConfig()),
        start_time=0.0,
        conversation_log=ConversationLog(base_dir=home / "sessions"),
    )


async def _consolidation_prompt(log: ConversationLog, key: str, tmp_path: Path) -> str:
    """What consolidation shows its model of the conversation *key*: the prompt's conversation."""
    memory = MemoryStore(workspace=tmp_path / "memory")
    memory.init()
    consolidator = HistoryConsolidator(
        log=log,
        memory=memory,
        skills_loader=SkillsLoader(skills_path=tmp_path / "skills", install_builtins=False),
        auto_skills_enabled=False,
    )
    prompts: list[str] = []

    async def _model(prompt: str, _key: str, **_kw: object) -> dict:
        prompts.append(prompt)
        return {"history_entry": "planned the lake trip"}

    with patch.object(consolidator, "_call_llm", side_effect=_model):
        await consolidator._consolidate(key, include_history=True)
    (prompt,) = prompts
    return prompt.split("## Conversation to Process", 1)[1]


def _the_group_chat(state: DashboardState) -> str:
    """The history key of the chat the door linked to the group."""
    session = state.get_linked_session(GROUP)
    assert session is not None, "the door linked no chat to the group"
    log = state.conversation_log
    assert log is not None
    return persisted_history_key(log, session.key)


# ── consolidation ─────────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_consolidation_shows_a_friends_words_in_a_group_as_theirs_and_hers_as_hers(
    tmp_path,
):
    """🔴 Red before: the friend's line reached the consolidation model as ``USER: Rin is allergic
    to peanuts…``, the user's own statement, and was kept as one. Her own line is the control."""
    state = _plain_state(tmp_path)
    group = _Gateway(state)
    await group.say(OWNER, HERS, turn_runner=_notes_it)
    await group.say(FRIEND, ABOUT_HER, turn_runner=_notes_it)
    save_all_sessions_to_history(state)
    assert state.conversation_log is not None

    conversation = await _consolidation_prompt(
        state.conversation_log, _the_group_chat(state), tmp_path
    )

    assert f"USER: {HERS}\n" in conversation
    assert f"USER: {ABOUT_HER}" not in conversation
    (line,) = [ln for ln in conversation.splitlines() if "SENT BY SOMEONE OTHER" in ln]
    assert line.endswith(f"<untrusted_content source=channel:{PROVIDER}:{FRIEND}>")
    after = conversation.split(line, 1)[1]
    assert after.lstrip("\n").startswith(f"{ABOUT_HER}\n</untrusted_content>")


@pytest.mark.asyncio
async def test_a_channel_that_runs_its_thread_names_itself_and_only_its_owner_is_her(tmp_path):
    """A channel that runs a conversation itself writes each turn with the thread, the sender and
    itself (``save_conversation_turn``): the colleague's line is theirs, the owner's is hers."""
    from personalclaw.llm_helpers import save_conversation_turn

    log = ConversationLog(base_dir=tmp_path / "sessions")
    for sender, text in ((OWNER_THERE, HERS), (COLLEAGUE, ABOUT_HER)):
        save_conversation_turn(
            log,
            THREAD,
            text,
            "Noted.",
            source_thread=THREAD,
            source_user=sender,
            source_channel=SELF_RUN,
        )
    assert [m.get("source_channel") for m in log.read_messages(THREAD)] == [SELF_RUN] * 4

    conversation = await _consolidation_prompt(log, THREAD, tmp_path)

    assert f"USER: {HERS}\n" in conversation
    assert f"USER: {ABOUT_HER}" not in conversation
    assert f"<untrusted_content source=channel:{SELF_RUN}:{COLLEAGUE}>" in conversation


@pytest.mark.asyncio
async def test_the_door_records_the_channel_on_each_line(tmp_path):
    """The line the door takes in names the channel it came on, beside its thread and sender, in
    the chat and in its file: that is what the owner it is checked against is the owner of."""
    state = _plain_state(tmp_path)
    await _Gateway(state).say(FRIEND, ABOUT_HER, turn_runner=_notes_it)
    save_all_sessions_to_history(state)
    assert state.conversation_log is not None

    (line,) = [
        m
        for m in state.conversation_log.read_messages(_the_group_chat(state))
        if m["role"] == "user"
    ]
    assert (line["source_thread"], line["source_user"], line.get("source_channel")) == (
        GROUP,
        FRIEND,
        PROVIDER,
    )


# ── a turn's own learning ─────────────────────────────────────────────────────────────────────────


class _ScriptedModel:
    """A ModelProvider that answers one line, and first calls the forecast tool when the turn's
    request asks for the forecast: what follows the request marker in the message it is sent, not
    the earlier turns in front of it."""

    supports_tools = True
    _model = "scripted"

    async def complete(self, messages, *, tools=None, model=None, reasoning_effort=""):
        last = messages[-1] if messages else {}
        request = str(last.get("content") or "").rpartition(USER_REQUEST_MARKER)[2]
        if last.get("role") == "user" and ASKS_FOR_IT in request:
            yield AgentEvent(
                kind=EVENT_TOOL_CALL,
                tool_call_id=f"call-{len(messages)}",
                title=FORECAST,
                tool_input="{}",
            )
            yield AgentEvent(kind=EVENT_COMPLETE)
            return
        yield AgentEvent(kind=EVENT_TEXT_CHUNK, text="Done.")
        yield AgentEvent(kind=EVENT_COMPLETE)

    async def cancel(self, *, wait_ack_timeout: float = 0.0) -> str:
        return "acked"


class _Forecast(ToolProvider):
    """The forecast tool, which runs without asking and answers."""

    @property
    def name(self) -> str:
        return "forecast"

    @property
    def display_name(self) -> str:
        return "Forecast"

    async def list_tools(self) -> list[ToolDefinition]:
        return [
            ToolDefinition(
                name=FORECAST,
                description="The lake's forecast.",
                parameters={"type": "object"},
                requires_approval=False,
            )
        ]

    async def invoke(self, tool_name: str, arguments: dict[str, Any]) -> ToolResult:
        return ToolResult(success=True, output="Sunny all weekend.")


@pytest.fixture
def memory(tmp_path, monkeypatch) -> MemoryService:
    """The memory every turn of a test learns into, with its record store wired."""
    store = VectorMemoryStore(db_path=tmp_path / "memory.db")
    store.init()
    svc = MemoryService.over_vector_store(store)
    monkeypatch.setattr("personalclaw.memory_service.service_for", lambda _provider: svc)
    config_loader.config_dir()
    config_loader.config_path().write_text(
        json.dumps({"learning": {"skill_ladder": False}}), encoding="utf-8"
    )
    return svc


async def _turn_engine(tmp_path: Path) -> DashboardState:
    """A dashboard state whose turns run through the real chat runner and agent loop."""
    runtime = NativeAgentRuntime(
        definition=AgentRuntimeDefinition(name="PersonalClaw", provider="native", model="scripted"),
        model_provider=_ScriptedModel(),
        tool_providers=[_Forecast()],
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
    return state


def _group_chat_engine(tmp_path: Path) -> DashboardState:
    """A dashboard state over the real session store, whose turns run through the real chat runner
    and agent loop: the door links the group to one chat, and that chat keeps one agent."""

    def factory(_key: Any = None, **_kw: Any) -> NativeAgentRuntime:
        return NativeAgentRuntime(
            definition=AgentRuntimeDefinition(
                name="PersonalClaw", provider="native", model="scripted"
            ),
            model_provider=_ScriptedModel(),
            tool_providers=[_Forecast()],
            cwd=tmp_path,
        )

    log = ConversationLog(base_dir=tmp_path / "sessions")
    state = DashboardState(
        sessions=SessionManager(AppConfig(), provider_factory=factory),
        start_time=0.0,
        conversation_log=log,
    )
    state.context_builder = ContextBuilder(
        memory=MemoryStore(workspace=tmp_path / "ws"),
        skills=SkillsLoader(skills_path=tmp_path / "skills", install_builtins=False),
        conversation_log=log,
    )
    state._hook_store = None
    state.broadcast_ws = MagicMock()
    state.push_sessions_update = MagicMock()
    return state


def _lessons(svc: MemoryService) -> list[str]:
    return [str(json.loads(row["value_json"])) for row in svc.get_lessons()]


def _priors(svc: MemoryService) -> list[str]:
    """What procedural memory holds: one prior for each tool outcome a turn taught."""
    return sorted(r.text for r in svc.get_records(kinds={MemoryKind.PROCEDURAL.value}))


async def _run_chat(state: Any, session: Any, message: str) -> None:
    with patch("personalclaw.dashboard.chat_runner.sel", MagicMock()):
        await run_chat(state, session, message)


async def _say_here(state: DashboardState, text: str) -> None:
    """She types *text* in the chat ``lake-trip`` and its turn runs."""
    session = state.get_or_create_session("lake-trip")
    session.append("user", text, "msg msg-u")
    await _run_chat(state, session, text)


def _in_group(sender: str) -> dict[str, str]:
    """Where a message *sender* wrote in the group came from, as the door records it."""
    return arrived_on(GROUP, sender, PROVIDER)


async def _queued_turn(state: DashboardState, *queued: tuple[Mapping[str, str], str]) -> dict:
    """Run a turn of the chat ``lake-trip`` while *queued* messages, each ``(source, text)``, wait
    behind it as the door queues what arrives while a turn runs, and return the one row they then
    run as, once its turn is done."""
    config_loader.config_path().write_text(
        json.dumps(
            {"learning": {"skill_ladder": False}, "dashboard": {"merge_queued_messages": True}}
        ),
        encoding="utf-8",
    )
    session = state.get_or_create_session("lake-trip")
    for source, text in queued:
        channel = PROVIDER if source.get("source_channel") else ""
        session.queue_append(text, channel=channel, source=source)
    await _say_here(state, "plan the lake trip")
    await session.task
    (row,) = [m for m in session.messages if "queued messages merged" in str(m.get("content"))]
    return row


@pytest.mark.asyncio
async def test_a_correction_a_friend_types_in_the_group_is_not_learned_as_hers(tmp_path, memory):
    """🔴 Red before: the friend's "that's not what I meant" was saved as a correction of hers.
    Her own correction, through the same door into the same chat, is the control."""
    state = await _turn_engine(tmp_path)
    group = _Gateway(state)

    theirs = "that's not what I meant, never book anything near a road"
    await group.say(FRIEND, theirs, turn_runner=_run_chat)
    assert _lessons(memory) == []

    hers = "that's not what I meant, keep the plan to three lines"
    await group.say(OWNER, hers, turn_runner=_run_chat)
    assert _lessons(memory) == [f"User correction to honor: {hers}"]


@pytest.mark.asyncio
async def test_queued_messages_from_her_and_a_friend_teach_only_her_words(tmp_path, memory):
    """Messages that waited behind a running turn run as one row, and the friend's waited there
    beside hers: that row's words are the owner's only, and consolidation reads only them as
    hers."""
    config_loader.config_path().write_text(
        json.dumps(
            {"learning": {"skill_ladder": False}, "dashboard": {"merge_queued_messages": True}}
        ),
        encoding="utf-8",
    )
    state = await _turn_engine(tmp_path)
    session = state.get_or_create_session("lake-trip")
    hers = "that's not what I meant, keep the plan to three lines"
    theirs = "and never book anything near a road"
    for sender, text in ((OWNER, hers), (FRIEND, theirs)):
        # As the door queues a message that arrives while the chat's turn runs.
        session.queue_append(
            text,
            channel=PROVIDER,
            source={"source_thread": GROUP, "source_user": sender, "source_channel": PROVIDER},
        )

    session.append("user", "plan the lake trip", "msg msg-u")
    await _run_chat(state, session, "plan the lake trip")
    await session.task

    (row,) = [m for m in session.messages if theirs in m["content"]]
    assert own_words(row) == hers
    assert f"USER: {hers}" in consolidation_line(row)
    assert theirs not in consolidation_line(row)
    assert _lessons(memory) == [f"User correction to honor: {hers}"]


@pytest.mark.asyncio
async def test_a_friends_correction_queued_beside_her_message_teaches_nothing(tmp_path, memory):
    """The control for her words teaching in such a row: the friend's correction, queued beside a
    message of hers that corrects nothing, is not learned, nor the veto in it."""
    state = await _turn_engine(tmp_path)

    row = await _queued_turn(state, (_in_group(OWNER), HERS), (_in_group(FRIEND), THEIR_CORRECTION))

    assert own_words(row) == HERS
    assert _lessons(memory) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("second", [FRIEND, OTHER_FRIEND], ids=["one-friend", "two-friends"])
async def test_queued_messages_none_of_them_hers_teach_nothing(tmp_path, memory, second):
    """The control: a row whose queued messages only her friends sent teaches none of their words,
    though they read as a correction, and nothing of what its turn did."""
    state = await _turn_engine(tmp_path)

    row = await _queued_turn(
        state, (_in_group(FRIEND), THEIR_CORRECTION), (_in_group(second), ASKS_FOR_IT)
    )

    assert own_words(row) == ""
    assert _lessons(memory) == []
    assert _priors(memory) == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "first",
    [DASHBOARD_SOURCE, _in_group(OWNER)],
    ids=["typed-here-and-in-the-group", "in-the-group"],
)
async def test_queued_messages_all_of_them_hers_teach_as_before(tmp_path, memory, first):
    """A row whose queued messages she sent, wherever she sent them, teaches as a message of hers
    does: her correction, and the tool its turn called as a prior of hers."""
    state = await _turn_engine(tmp_path)

    row = await _queued_turn(state, (first, HER_CORRECTION), (_in_group(OWNER), ASKS_FOR_IT))

    assert own_words(row) == f"{HER_CORRECTION}\n\n{ASKS_FOR_IT}"
    assert _lessons(memory) == [f"User correction to honor: {own_words(row)}"]
    assert _priors(memory) == [FORECAST_PRIOR]


@pytest.mark.asyncio
async def test_what_a_turn_did_for_her_and_a_friend_together_teaches_nothing(tmp_path, memory):
    """🔴 Red before: her correction, queued beside the friend's request, taught nothing. Her words
    in that row teach as hers; the tool its turn called for the friend is no prior of hers, in
    that turn or in her next one that learns. What her own turn does still is."""
    state = await _turn_engine(tmp_path)

    await _queued_turn(state, (_in_group(OWNER), HER_CORRECTION), (_in_group(FRIEND), ASKS_FOR_IT))
    assert _lessons(memory) == [f"User correction to honor: {HER_CORRECTION}"]
    assert _priors(memory) == []

    await _say_here(state, "no, wrong: the plan has to fit on one card")
    assert _priors(memory) == []

    await _say_here(state, f"no, wrong: {ASKS_FOR_IT} yourself")
    assert _priors(memory) == [FORECAST_PRIOR]


@pytest.mark.asyncio
async def test_what_a_friends_turn_did_is_not_learned_by_her_next_turn(tmp_path, memory):
    """🔴 Red before: the friend's turn learned nothing itself, but its tool outcomes waited on the
    agent, and her next correction learned them as priors of hers. What her own turn does still
    is."""
    state = _group_chat_engine(tmp_path)
    group = _Gateway(state)

    await group.say(FRIEND, ASKS_FOR_IT, turn_runner=_run_chat)
    chat = _the_group_chat(state)
    await group.say(OWNER, HER_CORRECTION, turn_runner=_run_chat)
    assert _the_group_chat(state) == chat, "the group's messages reach one chat"
    assert _lessons(memory) == [f"User correction to honor: {HER_CORRECTION}"]
    assert _priors(memory) == []

    await group.say(OWNER, f"no, wrong: {ASKS_FOR_IT} yourself", turn_runner=_run_chat)
    assert _priors(memory) == [FORECAST_PRIOR]


# ── who the owner is ──────────────────────────────────────────────────────────────────────────────


def _line(**source: str) -> dict:
    return {"role": "user", "content": "keep the plan to three lines", **source}


def test_whether_the_owner_sent_a_line_is_read_from_its_source_and_the_channels_owner():
    """The one answer every reader of her words asks, for each source a line can record."""
    from personalclaw.turn_source import arrived_on, sent_by_owner

    dashboard = {"source_thread": "dashboard", "source_user": "dashboard"}
    assert sent_by_owner(_line(**dashboard)) is True
    assert sent_by_owner(_line()) is True, "a line that records no source keeps its reading"
    assert sent_by_owner(_line(**arrived_on(GROUP, OWNER, PROVIDER))) is True
    assert sent_by_owner(_line(**arrived_on(GROUP, FRIEND, PROVIDER))) is False
    # A channel that knows no owner: nobody there is her.
    assert sent_by_owner(_line(**arrived_on("D1", "777", "otherchat"))) is False
    # A sender with no channel: a program through the OpenAI-compatible door, or a channel's line
    # saved before lines named their channel. Nothing says the owner sent it.
    assert sent_by_owner(_line(**arrived_on("v1:client-1:default", "client-1"))) is False
    assert sent_by_owner(_line(**arrived_on(THREAD, OWNER_THERE))) is False
    # A channel's line saved with no sender: whoever wrote it, nothing says it was her.
    assert sent_by_owner(_line(**arrived_on(THREAD, None))) is False
    assert sent_by_owner(_line(**arrived_on(THREAD, None, SELF_RUN))) is False


def test_a_slack_style_owner_id_matches_in_either_spelling():
    """A channel that spells one user with a ``U`` or a ``W`` in front matches the owner either
    way, as it does when it sends the owner a message."""
    from personalclaw.turn_source import arrived_on, sent_by_owner

    assert sent_by_owner(_line(**arrived_on(THREAD, "W0RINOWNER", SELF_RUN))) is True


def test_a_line_from_a_program_or_a_channel_that_knows_no_owner_holds_none_of_her_words():
    """🔴 Red before: every user line read as hers, whoever sent it."""
    endpoint = _line(source_thread="v1:client-1:default", source_user="client-1")
    unknown = _line(source_thread="D1", source_user="777", source_channel="otherchat")
    for line in (endpoint, unknown):
        assert own_words(line) == ""
        assert "SENT BY SOMEONE OTHER THAN THE USER" in consolidation_line(line)
    # The control: a dashboard line and the channel owner's line are hers.
    assert own_words(_line(source_thread="dashboard", source_user="dashboard")) == (
        "keep the plan to three lines"
    )
    mine = _line(source_thread=GROUP, source_user=OWNER, source_channel=PROVIDER)
    assert own_words(mine) == "keep the plan to three lines"
    assert consolidation_line(mine).endswith("USER: keep the plan to three lines")
    # Once the channel forgets who she is, nothing said there is hers.
    assert delete_credential(owner_id_credential(PROVIDER)) is True
    assert own_words(mine) == ""

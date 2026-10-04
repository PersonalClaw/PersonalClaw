"""A saved chat keeps where each of its turns came from.

Every row of a transcript records the thread it arrived on and who sent it there
(``source_thread``, ``source_user``). A channel's message records its channel thread and its
sender, whether the guarded door hands it to a chat or a channel that runs the conversation itself
writes it to the conversation's file; a program's message through the OpenAI-compatible door
records that program; what the owner types in the dashboard records the dashboard.

The dashboard rewrites a chat's whole file from what the chat holds each time it saves it, and it
wrote the dashboard as the source of every row, whatever the row had recorded. So once the
dashboard saved a chat that came from a channel, every turn the channel brought read as typed in
the dashboard: the transcript, the one record of a message the door let in, said the owner had
written it there, and the agent's search of earlier chats named the dashboard as who said it. The
chat loaded from its file dropped each row's source too, and so did a fork of the chat, a rewound
tail and a message that waited in the queue behind a running turn.

Now each row keeps the source it was taken in with, a copy carries its row's, a row several queued
messages run as records the source they share (none, when they came from different places), and a
save writes back what each row records.

The dashboard state, the session store and the conversation log are real, in this test's home.
"""

from __future__ import annotations

import asyncio
import functools
import json
import os
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from chat_test_helpers import _api_app, _make_app, _make_state

from personalclaw import channel_inbound as ci
from personalclaw import channel_trust as ct
from personalclaw import chat_recall
from personalclaw.channel_transports.base import ChannelMessage
from personalclaw.config.loader import AppConfig
from personalclaw.dashboard.chat_persistence import (
    _rehydrate_session_from_history,
    save_all_sessions_to_history,
    save_session_to_history,
)
from personalclaw.dashboard.chat_utils import persisted_history_key
from personalclaw.dashboard.state import DashboardState
from personalclaw.history import ConversationLog
from personalclaw.hooks import ToolHookResult
from personalclaw.inbox_providers import native_source
from personalclaw.llm.events import EVENT_COMPLETE, EVENT_TEXT_CHUNK, AgentEvent
from personalclaw.llm_helpers import save_conversation_turn
from personalclaw.session import SessionManager

PROVIDER = "discord"
#: A direct message: the DM channel is the conversation, and its sender is someone else's id.
DM = "880000000000000001"
ADA = "770000000000000002"
#: What a row the dashboard's own chat takes in records.
DASHBOARD = {"source_thread": "dashboard", "source_user": "dashboard"}
#: What a message Ada sends in the DM records.
FROM_ADA = {"source_thread": DM, "source_user": ADA}

#: A thread of a channel that runs its conversations itself, and who wrote in it.
THREAD = "1700000200.000200"
IN_THE_THREAD = {"source_thread": THREAD, "source_user": "U1"}


def _source(row: dict) -> dict:
    """What *row* records of where it came from, as the file holds it."""
    return {k: row[k] for k in ("source_thread", "source_user") if k in row}


def _on_disk(state: DashboardState, name: str) -> list[tuple[str, str, dict]]:
    log = state.conversation_log
    assert log is not None
    return [
        (m["role"], m["content"], _source(m))
        for m in log.read_messages(persisted_history_key(log, name))
    ]


@pytest.fixture(autouse=True)
def _a_trusted_sender():
    """Ada is let in, and no verdict outlives its test (the door caches one per message)."""
    ci.reset_admissions()
    ct.allow_sender(PROVIDER, ADA, name="Ada")
    yield
    ci.reset_admissions()


class _Gateway:
    """The gateway's session store and dashboard state over this test's home, and the door its
    channels deliver to (``GatewayServices.deliver_channel_inbound``)."""

    def __init__(self, home: Path) -> None:
        self.sessions: Any = SessionManager(AppConfig())
        self.dashboard_state = DashboardState(
            sessions=self.sessions,
            start_time=0.0,
            conversation_log=ConversationLog(base_dir=home / "sessions"),
        )
        self._count = 0

    @property
    def state(self) -> DashboardState:
        return self.dashboard_state

    async def deliver(self, text: str, *, turn_runner: Any) -> None:
        """Ada sends *text* in the DM, and the door hands it to the chat linked to the DM."""
        self._count += 1
        msg = ChannelMessage(
            channel_id=DM, text=text, sender=ADA, thread_id=DM, message_id=f"m-{self._count}"
        )
        verdict = await ci.deliver_inbound(self, PROVIDER, msg, is_dm=True, turn_runner=turn_runner)
        assert verdict.allowed, verdict

    async def settle(self) -> None:
        """Wait for every turn the door started, and every turn its queue started after it."""
        for _ in range(50):
            running = {t for t in self.state._background_tasks if not t.done()}
            if not running:
                return
            await asyncio.wait(running, timeout=10)
        raise AssertionError("turns kept starting")

    async def message(self, text: str) -> str:
        """Ada sends *text*; returns the chat the door ran its turn in. The turn notes it."""
        reached: list[str] = []

        async def _notes_it(state: Any, session: Any, message: str) -> None:
            reached.append(session.key)
            session.append("assistant", f"Noted: {message}", "msg msg-a")

        await self.deliver(text, turn_runner=_notes_it)
        await self.settle()
        assert reached, "the door ran no turn"
        return reached[-1]


async def _typed_in_the_dashboard(state: DashboardState, chat: str, text: str, monkeypatch) -> None:
    """The owner types *text* into the chat in the dashboard (``POST /api/chat``). The turn it
    starts is not what this is about, so it runs nothing."""

    async def _no_turn(st: Any, sl: Any, msg: str) -> None:
        return

    monkeypatch.setattr("personalclaw.dashboard.chat_handlers.run_chat", _no_turn)
    async with TestClient(TestServer(_make_app(state))) as client:
        resp = await client.post("/api/chat", json={"session": chat, "message": text})
        assert resp.status == 200, await resp.text()
        resp.close()


# ── a channel's message keeps its channel ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_channel_message_and_a_dashboard_turn_in_one_chat_each_keep_their_source(
    tmp_path, monkeypatch
):
    """🔴 Red before: the save wrote the dashboard as the source of Ada's message too."""
    gateway = _Gateway(tmp_path)
    chat = await gateway.message("Book the 9:10 to the coast")
    await _typed_in_the_dashboard(gateway.state, chat, "And a return on Sunday", monkeypatch)

    save_all_sessions_to_history(gateway.state)

    assert _on_disk(gateway.state, chat) == [
        ("user", "Book the 9:10 to the coast", FROM_ADA),
        ("assistant", "Noted: Book the 9:10 to the coast", DASHBOARD),
        ("user", "And a return on Sunday", DASHBOARD),
    ]


@pytest.mark.asyncio
async def test_a_turn_typed_in_the_dashboard_still_records_the_dashboard(tmp_path, monkeypatch):
    """The control, green before and after: what the owner types in the dashboard, and the chat's
    own answer, record the dashboard, as every row of a dashboard chat always has."""
    state = _make_state(tmp_path)
    state.get_or_create_session("chat-plans")
    await _typed_in_the_dashboard(state, "chat-plans", "Plan the trip", monkeypatch)
    state._sessions["chat-plans"].append("assistant", "Here is a plan.", "msg msg-a")

    save_all_sessions_to_history(state)

    assert _on_disk(state, "chat-plans") == [
        ("user", "Plan the trip", DASHBOARD),
        ("assistant", "Here is a plan.", DASHBOARD),
    ]


def _scripted(*answers: str, hold_first: asyncio.Event) -> AsyncMock:
    """Stand in for the model process: each turn answers the next of *answers*, and the first
    waits for *hold_first*, so a message can arrive while it runs."""
    pending = list(answers)
    started: list[int] = []

    async def _events():
        started.append(1)
        if len(started) == 1:
            await hold_first.wait()
        yield AgentEvent(kind=EVENT_TEXT_CHUNK, text=pending.pop(0))
        yield AgentEvent(kind=EVENT_COMPLETE, stop_reason="end_turn")

    client = AsyncMock()
    client.provider_id = "scripted"
    client.context_usage_pct = MagicMock(return_value=10.0)
    client.stream = MagicMock(side_effect=lambda *a, **kw: _events())
    return client


def _can_run_turns(gateway: _Gateway, client: AsyncMock) -> None:
    """The turns run through the real chat runner; only the model process is a stand-in."""
    sessions = gateway.sessions
    sessions.get_or_create = AsyncMock(return_value=(client, True, False))
    sessions.record_failure = AsyncMock()
    sessions.check_context_usage = MagicMock()
    sessions.get_pid = MagicMock(return_value=None)
    cb = MagicMock()
    cb.hooks.on_tool_call.return_value = ToolHookResult.allow()
    cb.build_message.return_value = ("hello", None)
    cb.conversation_log = MagicMock()
    cb.conversation_log.read_messages = MagicMock(return_value=[])
    gateway.state.context_builder = cb
    hooks = MagicMock()
    hooks.fire_for_ids = AsyncMock(return_value=[])
    gateway.state._hook_store = hooks


@pytest.mark.asyncio
async def test_a_channel_message_that_waited_behind_a_running_turn_keeps_its_source(tmp_path):
    """🔴 Red before: the queue ran Ada's second message as a row of the dashboard's."""
    from personalclaw.dashboard.chat_runner import run_chat

    gateway = _Gateway(tmp_path)
    release = asyncio.Event()
    _can_run_turns(gateway, _scripted("Booked.", "And the return.", hold_first=release))
    runner = functools.partial(run_chat, arrived_from_channel=True)
    with (
        patch("personalclaw.dashboard.chat_runner.sel", MagicMock()),
        patch("personalclaw.dashboard.chat_runner.maybe_offer_check_work"),
        patch("personalclaw.dashboard.chat_runner._maybe_followups", new=AsyncMock()),
        patch("personalclaw.dashboard.chat_plan.maybe_submit_plan_draft"),
    ):
        await gateway.deliver("Book the 9:10 to the coast", turn_runner=runner)
        (chat,) = gateway.state._sessions.values()
        await gateway.deliver("And a return on Sunday", turn_runner=runner)
        assert [item["content"] for item in chat._queue] == ["And a return on Sunday"]
        release.set()
        await gateway.settle()

    save_all_sessions_to_history(gateway.state)

    turns = [row for row in _on_disk(gateway.state, chat.key) if row[0] in ("user", "assistant")]
    assert turns == [
        ("user", "Book the 9:10 to the coast", FROM_ADA),
        ("assistant", "Booked.", DASHBOARD),
        ("user", "And a return on Sunday", FROM_ADA),
        ("assistant", "And the return.", DASHBOARD),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("second_from", "merged"), [("the DM", FROM_ADA), ("the dashboard", {})], ids=["one", "two"]
)
async def test_queued_messages_run_as_one_row_record_a_source_only_when_they_share_one(
    tmp_path, monkeypatch, second_from, merged
):
    """🔴 Red before: the row two queued messages ran as was saved as the dashboard's, whoever sent
    them. Now it records the source both share; messages from Ada and from the dashboard, run as
    one row, came from neither place alone, so it records none."""
    from personalclaw.dashboard.chat_runner import run_chat

    cfg = AppConfig()
    cfg.dashboard.merge_queued_messages = True
    monkeypatch.setattr(AppConfig, "load", staticmethod(lambda *a, **k: cfg))
    gateway = _Gateway(tmp_path)
    release = asyncio.Event()
    _can_run_turns(gateway, _scripted("Booked.", "Noted both.", hold_first=release))
    runner = functools.partial(run_chat, arrived_from_channel=True)
    with (
        patch("personalclaw.dashboard.chat_runner.sel", MagicMock()),
        patch("personalclaw.dashboard.chat_runner.maybe_offer_check_work"),
        patch("personalclaw.dashboard.chat_runner._maybe_followups", new=AsyncMock()),
        patch("personalclaw.dashboard.chat_plan.maybe_submit_plan_draft"),
    ):
        await gateway.deliver("Book the 9:10 to the coast", turn_runner=runner)
        (chat,) = gateway.state._sessions.values()
        await gateway.deliver("And a return on Sunday", turn_runner=runner)
        if second_from == "the DM":
            await gateway.deliver("A window seat, please", turn_runner=runner)
        else:
            await _typed_in_the_dashboard(
                gateway.state, chat.key, "A window seat, please", monkeypatch
            )
        assert len(chat._queue) == 2
        release.set()
        await gateway.settle()

    save_all_sessions_to_history(gateway.state)

    users = [row for row in _on_disk(gateway.state, chat.key) if row[0] == "user"]
    assert users == [
        ("user", "Book the 9:10 to the coast", FROM_ADA),
        (
            "user",
            "[2 queued messages merged]\n\nAnd a return on Sunday\n\nA window seat, please",
            merged,
        ),
    ]


# ── a conversation a channel runs itself ────────────────────────────────────────────────────────


@pytest.fixture
def state(tmp_path):
    """The gateway's dashboard state, the one a channel's own turn reaches."""
    state = _make_state(tmp_path)
    state.push_sessions_update = MagicMock()
    before = native_source.get_dashboard_state()
    native_source.set_dashboard_state(state)
    yield state
    native_source.set_dashboard_state(before)


def _turn(state: DashboardState, asked: str, said: str) -> None:
    """One turn the channel ran in the thread, written as it writes one."""
    log = state.conversation_log
    assert log is not None
    save_conversation_turn(log, THREAD, asked, said, source_thread=THREAD, source_user="U1")


def test_a_conversation_a_channel_runs_keeps_each_turns_source_when_the_dashboard_saves_it(state):
    """🔴 Red before: opening the conversation in the dashboard and saving it wrote the dashboard
    over the source of every turn the channel had written, and of the turn written after."""
    _turn(state, "tidy the notes", "Done.")
    assert _rehydrate_session_from_history(state, THREAD) is not None
    _turn(state, "and the drafts?", "Moved them.")

    save_all_sessions_to_history(state)

    assert _on_disk(state, THREAD) == [
        ("user", "tidy the notes", IN_THE_THREAD),
        ("assistant", "Done.", IN_THE_THREAD),
        ("user", "and the drafts?", IN_THE_THREAD),
        ("assistant", "Moved them.", IN_THE_THREAD),
    ]


def test_the_agents_search_of_earlier_chats_names_who_said_a_channel_turn(state):
    """🔴 Red before: once the dashboard had saved the conversation, ``chat_search`` said the
    turn was the dashboard's (``user (dashboard)``) rather than the thread's sender's."""
    _turn(state, "where did the drafts go?", "Moved them to the archive.")
    assert _rehydrate_session_from_history(state, THREAD) is not None
    save_all_sessions_to_history(state)
    log = state.conversation_log
    assert log is not None

    found = chat_recall.search("drafts", log=log)

    (chat,) = found.chats
    assert [(t.role, t.text) for t in chat.turns][0] == ("user (U1)", "where did the drafts go?")


# ── a copy of a row carries the row's own ──────────────────────────────────────────────────────


#: What the rows of a chat's file record, in every way a file holds a source: a channel's, the
#: dashboard's, a thread with no sender, and none at all. The last row, "Plan the return", keeps
#: in a rewound tail the turn an edit of it replaced: Ada's.
_SOURCES_IN_THE_FILE = [FROM_ADA, DASHBOARD, {"source_thread": DM}, {}, DASHBOARD]


def _a_chat_file(home: Path) -> None:
    ts = "2026-10-01T09:0{}:00+00:00"
    rows = [
        {"role": "user", "content": "Book the 9:10", "ts": ts.format(1)},
        {"role": "assistant", "content": "Booked.", "ts": ts.format(2)},
        {"role": "user", "content": "A draft", "ts": ts.format(3)},
        {"role": "assistant", "content": "Saved it.", "ts": ts.format(4)},
        {"role": "user", "content": "Plan the return", "ts": ts.format(5)},
    ]
    for row, source in zip(rows, _SOURCES_IN_THE_FILE):
        row.update(source)
    tail = [{"role": "user", "content": "Back on Sunday", "ts": ts.format(6), **FROM_ADA}]
    rows[-1]["rewound"] = [{"ts": ts.format(7), "messages": tail}]
    meta = {"_type": "metadata", "created_at": "2026-10-01T09:00:00+00:00", "last_consolidated": 0}
    path = home / "dashboard_chat-trip.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in (meta, *rows)), encoding="utf-8")


def test_loading_a_chat_from_its_file_and_saving_it_changes_no_rows_source(state, tmp_path):
    """🔴 Red before: the chat loaded from its file kept no row's source, so the save that followed
    wrote the dashboard on every row, a row that had recorded none included, and dropped the
    source of a row kept in a rewound tail."""
    _a_chat_file(tmp_path)
    chat = _rehydrate_session_from_history(state, "chat-trip")
    assert chat is not None

    save_session_to_history(state, chat, force=True)

    log = state.conversation_log
    assert log is not None
    rows = log.read_messages("dashboard:chat-trip")
    assert [_source(m) for m in rows] == _SOURCES_IN_THE_FILE
    assert [_source(m) for m in rows[-1]["rewound"][0]["messages"]] == [FROM_ADA]


@pytest.mark.asyncio
async def test_a_rewound_tail_restored_as_a_fork_keeps_the_source_of_each_turn(state, tmp_path):
    """🔴 Red before: restoring the tail an edit replaced wrote Ada's message, and every turn
    before it, into the new chat as the dashboard's."""
    from personalclaw.dashboard.chat import api_chat_session_fork_rewound

    _a_chat_file(tmp_path)
    assert _rehydrate_session_from_history(state, "chat-trip") is not None
    app = _api_app(state)
    app.router.add_post("/api/chat/sessions/{session}/fork-rewound", api_chat_session_fork_rewound)

    async with TestClient(TestServer(app)) as client:
        resp = await client.post("/api/chat/sessions/chat-trip/fork-rewound", json={"index": 4})
        assert resp.status == 200, await resp.text()
        fork = (await resp.json())["key"]

    assert [source for _role, _content, source in _on_disk(state, fork)] == [
        *_SOURCES_IN_THE_FILE[:4],
        FROM_ADA,
    ]


@pytest.mark.asyncio
async def test_a_fork_of_a_channel_chat_keeps_the_source_of_each_turn_it_copies(tmp_path):
    """🔴 Red before: the fork wrote Ada's message into the new chat as the dashboard's."""
    gateway = _Gateway(tmp_path)
    chat = await gateway.message("Book the 9:10 to the coast")

    async with TestClient(TestServer(_make_app(gateway.state))) as client:
        resp = await client.post(f"/api/chat/sessions/{chat}/fork", json={})
        assert resp.status == 200, await resp.text()
        fork = (await resp.json())["key"]

    assert _on_disk(gateway.state, fork) == [
        ("user", "Book the 9:10 to the coast", FROM_ADA),
        ("assistant", "Noted: Book the 9:10 to the coast", DASHBOARD),
    ]


# ── a program's message through the OpenAI-compatible door ────────────────────────────────────


@pytest.fixture
def openai_door(monkeypatch):
    """The OpenAI-compatible surface switched on for one agent, and its token. The token is
    mirrored into the environment by the code that mints it, so it is taken out again here."""
    from personalclaw.config.external_access import ExternalAccessConfig
    from personalclaw.config.external_access import ExternalAccessSurfaceConfig as Surface
    from personalclaw.config.loader import AgentConfig
    from personalclaw.inbound import auth
    from personalclaw.inbound import openai_dialect as dialect

    cfg = AppConfig()
    cfg.external_access = ExternalAccessConfig(
        enabled=True, openai=Surface(enabled=True, allow_remote=False)
    )
    cfg.agents = {"researcher": AgentConfig()}
    cfg.default_agent = "researcher"
    monkeypatch.setattr(AppConfig, "load", staticmethod(lambda *a, **k: cfg))
    env = "PERSONALCLAW_INBOUND_OPENAI_TOKEN"
    monkeypatch.delenv(env, raising=False)
    yield auth.create_surface_token(dialect.OPENAI_SURFACE)
    os.environ.pop(env, None)


@pytest.mark.asyncio
async def test_a_message_a_program_sends_through_the_openai_compatible_door_records_that_program(
    tmp_path, openai_door
):
    """🔴 Red before: a message another program sent to ``/v1/chat/completions`` was saved as typed
    in the dashboard."""
    from personalclaw.inbound import openai_dialect as dialect

    async def _answers(state: Any, session: Any, message: str) -> None:
        session.stream_chunk("Two alerts, both resolved.")
        session.finish_stream("Two alerts, both resolved.")
        session.signal_done()

    async def _no_other_agent(*_a: Any, **_k: Any) -> None:
        raise AssertionError("no request here names another agent")

    state = _make_state(tmp_path)
    app = web.Application()
    app["state"] = state
    dialect.register_routes(app, turn_runner=_answers, agent_mover=_no_other_agent)
    body = {"model": "researcher", "messages": [{"role": "user", "content": "Any alerts?"}]}
    async with TestClient(TestServer(app)) as client:
        resp = await client.post(
            dialect.ROUTE_CHAT, json=body, headers={"Authorization": f"Bearer {openai_door}"}
        )
        assert resp.status == 200, await resp.text()

    (session,) = state._sessions.values()
    save_session_to_history(state, session)
    assert _on_disk(state, session.key) == [
        ("user", "Any alerts?", {"source_thread": session.key, "source_user": "openai"}),
        ("assistant", "Two alerts, both resolved.", DASHBOARD),
    ]

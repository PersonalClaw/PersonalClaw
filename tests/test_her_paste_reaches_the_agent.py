"""What she pasted reaches the agent and stays on her row, whichever way her message is sent.

The composer turns a long paste into a ``[Paste #N]`` marker in her draft and a card above it. It
sends the message with each block in place of its marker, and the blocks themselves
(``meta.pastes``), so the chat shows each as its chip and learning tells what she pasted from what
she typed. A message sent while a turn ran, as a steer or queued for when the turn ends, kept only
the text: the blocks were dropped on the way to her row, so a reload showed the whole paste in her
bubble and learning read it as her words. An edit or a rewind dropped them the same way. A send
that carries its blocks but still holds one's marker would reach the model as the marker alone, so
the gateway refuses it.

Driven through the real chat handler, the real turn engine, the real session manager and a real
native runtime answered by a scripted model, as the steer tests are. The model's first answer
pauses after its first words until the test has sent what she sends while it is written.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from aiohttp.test_utils import TestClient, TestServer
from chat_test_helpers import _api_app

from personalclaw.agents.native.runtime import _MAX_STEERS_PER_TURN, NativeAgentRuntime
from personalclaw.agents.provider import AgentRuntimeDefinition
from personalclaw.config import loader as config_loader
from personalclaw.config.loader import AppConfig
from personalclaw.context import ContextBuilder
from personalclaw.dashboard.chat import (
    api_chat,
    api_chat_session_detail,
    api_chat_session_edit_resend,
)
from personalclaw.dashboard.state import DashboardState
from personalclaw.history import ConversationLog
from personalclaw.llm.events import EVENT_COMPLETE, EVENT_TEXT_CHUNK, AgentEvent
from personalclaw.memory import MemoryStore
from personalclaw.own_words import own_words
from personalclaw.session import SessionManager
from personalclaw.skills import SkillsLoader

ASKED = "Help me tidy the parser."
TYPED = "Use this version instead:"
BLOCK = (
    "def parse(line):\n"
    '    key, _, value = line.partition("=")\n'
    "    # the version she means\n"
    "    return key.strip(), value.strip()"
)
#: Her message as the composer sends it: the block in place of its marker, and the block itself.
SENT = f"{TYPED} {BLOCK}"
PASTES = [{"seq": 1, "lines": 4, "content": BLOCK}]
SENT_TS = "2026-10-04T09:30:05+00:00"
FIRST = "I'll keep the old parser."
SECOND = "Switching to your version."


class _Model:
    """A model that answers each request with its next reply and calls no tool. Its first answer
    stops after its first words until ``go_on`` is set: the test sends her message in that pause."""

    supports_tools = True
    _model = "scripted"

    def __init__(self, *replies: str) -> None:
        self.replies = list(replies)
        self.requests: list[list[dict]] = []
        self.answering = asyncio.Event()
        self.go_on = asyncio.Event()

    async def complete(self, messages, *, tools=None, model=None, reasoning_effort=""):
        self.requests.append([dict(m) for m in messages])
        yield AgentEvent(kind=EVENT_TEXT_CHUNK, text=self.replies.pop(0) if self.replies else "Ok.")
        if len(self.requests) == 1:
            self.answering.set()
            await self.go_on.wait()
        yield AgentEvent(kind=EVENT_COMPLETE)

    async def cancel(self, *, wait_ack_timeout: float = 0.0) -> str:
        return "acked"

    def read(self, request: int) -> str:
        """The text of every message the model was sent in its *request*-th request."""
        return "\n".join(str(m.get("content", "")) for m in self.requests[request])


class _World:
    """One gateway's chat surface: the handler, the turn engine, the session manager and a native
    runtime per chat, answered by :class:`_Model`."""

    def __init__(self, tmp_path: Path, *replies: str) -> None:
        self.model = _Model(*replies)

        def factory(session_key=None, **_kwargs):
            return NativeAgentRuntime(
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
    async def client(self):
        app = _api_app(self.state)
        app.router.add_post("/api/chat", api_chat)
        app.router.add_get("/api/chat/sessions/{session}", api_chat_session_detail)
        app.router.add_post(
            "/api/chat/sessions/{session}/edit-resend", api_chat_session_edit_resend
        )
        async with TestClient(TestServer(app)) as client:
            with patch("personalclaw.dashboard.chat_runner.sel", MagicMock()):
                yield client
        await self.sessions.close_all()

    async def send(self, client: TestClient, message: str, **body: Any) -> dict:
        resp = await client.post("/api/chat?ws=1", json={"message": message, **body})
        assert resp.status == 200, await resp.text()
        return await resp.json()

    async def her_message(
        self, client: TestClient, key: str, mode: str, text: str = SENT, ts: str = SENT_TS
    ) -> dict:
        """What the composer sends for a message with a paste typed while the turn runs."""
        return await self.send(
            client,
            text,
            session=key,
            queue_mode=mode,
            meta={"client_ts": ts, "pastes": PASTES},
        )

    async def settled(self, key: str):
        """The chat, once it has answered everything it was sent."""
        session = self.state._sessions[key]
        for _ in range(1500):
            task = session.task
            if (task is None or task.done()) and not session._queue:
                return session
            await asyncio.sleep(0.01)
        raise AssertionError(f"the chat never settled: {session.messages}")

    def frames_of(self, kind: str) -> list[dict]:
        return [data for k, data in self.frames if k == kind]


def _her_rows(messages: list[dict]) -> list[dict]:
    return [m for m in messages if m.get("role") == "user"]


async def _reopened(log: ConversationLog, key: str) -> list[dict]:
    """The chat as a page reads it after the gateway restarted: from its file alone."""
    state = DashboardState(sessions=MagicMock(count=0), start_time=0.0, conversation_log=log)
    app = _api_app(state)
    app.router.add_get("/api/chat/sessions/{session}", api_chat_session_detail)
    async with TestClient(TestServer(app)) as client:
        resp = await client.get(f"/api/chat/sessions/{key}")
        assert resp.status == 200, await resp.text()
        return (await resp.json())["messages"]


def _kept_as_sent(row: dict) -> None:
    """Her row holds what she sent: the block in her text, the block itself, and her own words."""
    assert row["content"] == SENT
    assert row["meta"]["pastes"] == PASTES
    assert own_words(row) == TYPED, "learning reads what she typed, not what she pasted"


# ── sent while a turn runs ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_steer_with_a_paste_reaches_the_model_and_her_row_keeps_the_block(tmp_path):
    world = _World(tmp_path, FIRST, SECOND)
    async with world.client() as client:
        key = (await world.send(client, ASKED))["session"]
        await asyncio.wait_for(world.model.answering.wait(), timeout=10)
        assert await world.her_message(client, key, "steer") == {"ok": True, "steered": True}
        world.model.go_on.set()
        session = await world.settled(key)

    assert BLOCK in world.model.read(1), "the running turn read what she pasted"
    _asked, steer = _her_rows(session.messages)
    _kept_as_sent(steer)
    assert steer["meta"]["steered"] is True
    # The open page is told the block with her message, so her bubble shows it as its chip.
    (told,) = world.frames_of("chat_user_message")
    assert told["pastes"] == PASTES and told["content"] == SENT
    # And a page that reads the chat again after a restart finds it the same way.
    kept = [m for m in await _reopened(world.log, key) if m["role"] == "user"][1]
    assert kept["meta"]["pastes"] == PASTES and kept["content"] == SENT


@pytest.mark.asyncio
async def test_a_queued_send_with_a_paste_reaches_the_model_and_her_row_keeps_the_block(tmp_path):
    world = _World(tmp_path, FIRST, SECOND)
    async with world.client() as client:
        key = (await world.send(client, ASKED))["session"]
        await asyncio.wait_for(world.model.answering.wait(), timeout=10)
        assert await world.her_message(client, key, "followup") == {"ok": True, "queued": True}
        # The queue strip is told the block with it, live and when the page reads the chat.
        (pushed,) = world.frames_of("queue_push")
        assert pushed["pastes"] == PASTES and pushed["content"] == SENT
        detail = await (await client.get(f"/api/chat/sessions/{key}")).json()
        assert [(q["content"], q["pastes"]) for q in detail["queue"]] == [(SENT, PASTES)]
        world.model.go_on.set()
        session = await world.settled(key)

    assert BLOCK in world.model.read(1), "the turn it ran as read what she pasted"
    _kept_as_sent(_her_rows(session.messages)[1])
    (told,) = world.frames_of("chat_user_message")
    assert told["pastes"] == PASTES
    kept = [m for m in await _reopened(world.log, key) if m["role"] == "user"][1]
    assert kept["meta"]["pastes"] == PASTES


@pytest.mark.asyncio
async def test_a_steer_the_turn_never_takes_keeps_its_paste_when_it_runs(tmp_path):
    """One turn takes a few steers; the next waits in the queue for the next turn, and keeps its
    block on the way there."""
    replies = (FIRST, SECOND, "And that one too.")
    world = _World(tmp_path, *replies)
    async with world.client() as client:
        key = (await world.send(client, ASKED))["session"]
        await asyncio.wait_for(world.model.answering.wait(), timeout=10)
        for n in range(_MAX_STEERS_PER_TURN):
            ts = f"2026-10-04T09:30:0{n}+00:00"
            await world.send(
                client,
                f"Also check case {n}.",
                session=key,
                queue_mode="steer",
                meta={"client_ts": ts},
            )
        assert await world.her_message(client, key, "steer") == {"ok": True, "steered": True}
        world.model.go_on.set()
        session = await world.settled(key)

    owed = _her_rows(session.messages)[-1]
    _kept_as_sent(owed)
    (pushed,) = world.frames_of("queue_push")
    assert pushed["pastes"] == PASTES and pushed["steer_ts"] == SENT_TS
    assert BLOCK in world.model.read(len(world.model.requests) - 1)


@pytest.mark.asyncio
async def test_a_message_that_replaces_the_running_turn_keeps_its_paste(tmp_path):
    """Under the cancel-and-replace policy her message stops the answer and runs next, from the
    queue: with its block on her row, as any queued message."""
    config_loader.config_dir()
    config_loader.config_path().write_text(
        json.dumps({"resilience": {"mid_turn_policy": "cancel_and_replace"}}), encoding="utf-8"
    )
    world = _World(tmp_path, FIRST, SECOND)
    async with world.client() as client:
        key = (await world.send(client, ASKED))["session"]
        await asyncio.wait_for(world.model.answering.wait(), timeout=10)
        answer = await world.send(
            client, SENT, session=key, meta={"client_ts": SENT_TS, "pastes": PASTES}
        )
        assert answer == {"ok": True, "cancelled_and_replaced": True}
        world.model.go_on.set()
        session = await world.settled(key)

    _kept_as_sent(_her_rows(session.messages)[-1])
    assert BLOCK in world.model.read(len(world.model.requests) - 1)


# ── sent again ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_rewind_with_a_paste_reaches_the_model_and_her_row_keeps_the_block(tmp_path):
    world = _World(tmp_path, FIRST, SECOND, "Again, with your version.")
    world.model.go_on.set()
    async with world.client() as client:
        key = (await world.send(client, SENT, meta={"client_ts": SENT_TS, "pastes": PASTES}))[
            "session"
        ]
        await world.settled(key)
        await world.send(client, "Thanks.", session=key)
        await world.settled(key)
        resp = await client.post(
            f"/api/chat/sessions/{key}/edit-resend",
            json={
                "content": SENT,
                "ts": SENT_TS,
                "index": 0,
                "client_ts": "2026-10-04T09:40:00+00:00",
                "rewind": True,
                "again": True,
                "pastes": PASTES,
            },
        )
        assert resp.status == 200, await resp.text()
        session = await world.settled(key)

    assert BLOCK in world.model.read(len(world.model.requests) - 1)
    (row,) = _her_rows(session.messages)
    _kept_as_sent(row)
    (kept,) = [m for m in await _reopened(world.log, key) if m["role"] == "user"]
    assert kept["meta"]["pastes"] == PASTES


# ── a marker never reaches the model in place of its block ─────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["", "steer", "followup"])
async def test_a_send_that_holds_the_marker_of_a_block_it_carries_is_refused(tmp_path, mode):
    world = _World(tmp_path, FIRST, SECOND)
    async with world.client() as client:
        key = (await world.send(client, ASKED))["session"]
        await asyncio.wait_for(world.model.answering.wait(), timeout=10)
        body: dict[str, Any] = {
            "message": f"{TYPED} [Paste #1]",
            "session": key,
            "meta": {"client_ts": SENT_TS, "pastes": PASTES},
        }
        if mode:
            body["queue_mode"] = mode
        world.model.go_on.set()
        await world.settled(key)
        if mode:  # sent while a turn runs: start one, and send it while that one answers
            world.model.go_on.clear()
            world.model.answering.clear()
            world.model.requests.clear()
            await world.send(client, "One more thing.", session=key)
            await asyncio.wait_for(world.model.answering.wait(), timeout=10)
        resp = await client.post("/api/chat?ws=1", json=body)
        assert resp.status == 400, await resp.text()
        assert (await resp.json())["error"]["code"] == "paste_not_expanded"
        world.model.go_on.set()
        session = await world.settled(key)

    assert not any("[Paste #1]" in m["content"] for m in _her_rows(session.messages))
    assert not any("[Paste #1]" in world.model.read(i) for i in range(len(world.model.requests)))
    assert world.frames_of("queue_push") == []


@pytest.mark.asyncio
async def test_a_rewind_that_holds_the_marker_of_a_block_it_carries_is_refused(tmp_path):
    world = _World(tmp_path, FIRST, SECOND)
    world.model.go_on.set()
    async with world.client() as client:
        key = (await world.send(client, SENT, meta={"client_ts": SENT_TS, "pastes": PASTES}))[
            "session"
        ]
        await world.settled(key)
        resp = await client.post(
            f"/api/chat/sessions/{key}/edit-resend",
            json={"content": f"{TYPED} [Paste #1]", "ts": SENT_TS, "pastes": PASTES},
        )
        assert resp.status == 400, await resp.text()
        assert (await resp.json())["error"]["code"] == "paste_not_expanded"
        session = await world.settled(key)

    (row,) = _her_rows(session.messages)
    assert row["content"] == SENT, "her message is left as it was"
    assert len(world.model.requests) == 1


@pytest.mark.asyncio
async def test_words_that_look_like_a_marker_are_sent_as_written(tmp_path):
    """Control: with no block, ``[Paste #1]`` is words she typed, and they go as she wrote them."""
    world = _World(tmp_path, FIRST)
    world.model.go_on.set()
    async with world.client() as client:
        key = (await world.send(client, "What does [Paste #1] mean in the composer?"))["session"]
        session = await world.settled(key)

    assert "[Paste #1] mean" in world.model.read(0)
    (row,) = _her_rows(session.messages)
    assert row["content"] == "What does [Paste #1] mean in the composer?"


@pytest.mark.asyncio
async def test_queued_messages_merged_into_one_turn_keep_each_block_as_its_own_chip(tmp_path):
    config_loader.config_dir()
    config_loader.config_path().write_text(
        json.dumps({"dashboard": {"merge_queued_messages": True}}), encoding="utf-8"
    )
    other = "SELECT name\nFROM parsers\nWHERE broken\n;"
    world = _World(tmp_path, FIRST, "Both read.")
    async with world.client() as client:
        key = (await world.send(client, ASKED))["session"]
        await asyncio.wait_for(world.model.answering.wait(), timeout=10)
        await world.her_message(client, key, "followup")
        await world.send(
            client,
            f"And this query: {other}",
            session=key,
            queue_mode="followup",
            meta={"client_ts": SENT_TS, "pastes": [{"seq": 1, "lines": 4, "content": other}]},
        )
        world.model.go_on.set()
        session = await world.settled(key)

    (merged,) = [m for m in _her_rows(session.messages) if "queued messages merged" in m["content"]]
    pastes = merged["meta"]["pastes"]
    # Each block keeps its own number in the row, so each shows as its own chip.
    assert [p["content"] for p in pastes] == [BLOCK, other]
    assert len({p["seq"] for p in pastes}) == 2
    assert BLOCK in merged["content"] and other in merged["content"]
    (told,) = world.frames_of("chat_user_message")
    assert told["pastes"] == pastes

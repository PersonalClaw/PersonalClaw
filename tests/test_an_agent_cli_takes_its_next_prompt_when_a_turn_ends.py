"""An agent CLI takes its next prompt the moment a turn ends, on every Python.

An agent CLI's session sends one prompt at a time: a turn holds the session's turn lock until it
ends. The lock was held inside the turn's event stream, and given back only when that stream was
closed. The chat runner stops reading a turn at its terminal event and kept the stream, and
neither it nor the layers in between closed it, so the lock went back only when the interpreter
collected the stream. Measured on Linux with Python 3.12, it was still held seconds later, after
a collection; the next prompt on the session was never sent, and its caller waited out the turn
limit. On macOS with Python 3.13 it went back at once.

The rule now: a turn gives its session back where it ends, before its terminal event is handed
on, and whatever stops reading a turn's stream part way closes it, which gives the session back
once the agent has been told to stop and has answered.

The streams under test are kept by the test (``kept_streams``, or held in a local), so no
collection can close them, which makes every check here the same on every interpreter. The
session tests drive ``AcpSession`` over a fake connection; the chat tests drive the real chat
runner, session manager and ACP client against ``scripted_acp_agent.py`` over stdio.
"""

from __future__ import annotations

import asyncio
import inspect
import json
from collections.abc import Callable

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request
from scripted_acp_agent import PLAIN_ANSWER
from test_a_deny_or_a_stop_ends_an_agent_cli_turn_as_she_meant import ASKED, _World

from personalclaw.acp.session import AcpSession
from personalclaw.acp.types import (
    EVENT_COMPLETE,
    EVENT_TEXT_CHUNK,
    METHOD_CANCEL,
    METHOD_PROMPT,
    JsonRpcMessage,
)
from personalclaw.approval_answer import YOU
from personalclaw.dashboard.chat import api_chat_session_stop
from personalclaw.dashboard.chat_runner import TURN_STOPPED

#: How long a prompt may take to reach the agent once the turn before it has ended. The fake and
#: the scripted agent answer in milliseconds, so anything near this is a prompt that never went.
_AT_ONCE = 5.0


# ── one session, over a fake connection ──────────────────────────────────────


class _Connection:
    """What an ``AcpSession`` sends, each request with the future its answer resolves."""

    def __init__(self) -> None:
        self.queue: asyncio.Queue[JsonRpcMessage] = asyncio.Queue()
        self.requests: list[tuple[str, dict, asyncio.Future]] = []
        self.cancels = 0

    def session(self) -> AcpSession:
        async def send_request(method, params):
            answer = asyncio.get_running_loop().create_future()
            self.requests.append((method, params, answer))
            return len(self.requests), answer

        async def send_response(req_id, result):
            return None

        async def cancel_session():
            self.cancels += 1

        return AcpSession(
            "sess-1",
            self.queue,
            send_request=send_request,
            send_response=send_response,
            cancel_session=cancel_session,
            is_process_alive=lambda: True,
        )

    def prompts(self) -> list[str]:
        return [
            "".join(block.get("text", "") for block in params["prompt"])
            for method, params, _ in self.requests
            if method == METHOD_PROMPT
        ]

    def says(self, text: str) -> None:
        """The agent streams *text* (a notification on the session's queue)."""
        self.queue.put_nowait(
            JsonRpcMessage(
                method="session/update",
                params={
                    "sessionId": "sess-1",
                    "update": {
                        "sessionUpdate": "agent_message_chunk",
                        "content": {"type": "text", "text": text},
                    },
                },
            )
        )

    def answers(self, n: int, stop_reason: str = "end_turn") -> None:
        """The agent answers the *n*-th request (1-based)."""
        _, _, answer = self.requests[n - 1]
        answer.set_result(JsonRpcMessage(id=n, result={"stopReason": stop_reason}))

    async def sent(self, n: int) -> None:
        """Until the session has sent *n* requests, at once."""
        await _until(lambda: len(self.requests) >= n, f"request {n} was never sent")


async def _until(ready: Callable[[], bool], what: str, within: float = _AT_ONCE) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + within
    while not ready():
        if loop.time() > deadline:
            raise AssertionError(what)
        await asyncio.sleep(0.01)


async def _read(stream) -> list:
    return [event async for event in stream]


@pytest.mark.asyncio
async def test_a_turn_its_reader_leaves_at_the_last_event_gives_the_session_back_at_once():
    """The chat runner stops reading at the terminal event and keeps the stream. Red before the
    fix: the lock was held inside the stream until it was collected, so the next prompt on the
    session was never sent."""
    wire = _Connection()
    session = wire.session()
    first = session.stream_events("Summarise the incident report", timeout=5)
    wire.says("The alerts come from one adapter.")
    kinds = []
    async for event in first:
        kinds.append(event.kind)
        if kinds == [EVENT_TEXT_CHUNK]:
            wire.answers(1)
        if event.kind == EVENT_COMPLETE:
            break  # where the chat runner stops reading
    assert kinds == [EVENT_TEXT_CHUNK, EVENT_COMPLETE]
    assert inspect.getasyncgenstate(first) == inspect.AGEN_SUSPENDED, "vacuity: kept open"

    assert not session._turn_lock.locked(), "the session was still held by the turn that ended"
    second = asyncio.ensure_future(_read(session.stream_events("What do I check first?")))
    await wire.sent(2)
    assert wire.prompts()[1] == "What do I check first?"
    wire.says("Check the adapter's timeouts first.")
    await asyncio.sleep(0.05)
    wire.answers(2)
    events = await asyncio.wait_for(second, timeout=_AT_ONCE)
    assert [e.kind for e in events] == [EVENT_TEXT_CHUNK, EVENT_COMPLETE]
    assert events[0].text == "Check the adapter's timeouts first."
    await first.aclose()


@pytest.mark.asyncio
async def test_a_turn_read_to_its_end_gives_the_session_back():
    """The control: a reader that reads the stream to its end, and the next prompt goes out."""
    wire = _Connection()
    session = wire.session()
    reading = asyncio.ensure_future(_read(session.stream_events("Summarise the incident report")))
    await wire.sent(1)
    wire.says("The alerts come from one adapter.")
    await asyncio.sleep(0.05)
    wire.answers(1)
    assert [e.kind for e in await reading] == [EVENT_TEXT_CHUNK, EVENT_COMPLETE]

    assert not session._turn_lock.locked()
    second = asyncio.ensure_future(_read(session.stream_events("What do I check first?")))
    await wire.sent(2)
    wire.answers(2)
    assert [e.kind for e in await asyncio.wait_for(second, timeout=_AT_ONCE)] == [EVENT_COMPLETE]


@pytest.mark.asyncio
async def test_a_turn_closed_part_way_gives_the_session_back_once_the_agent_answers_the_stop():
    """A reader that stops part way closes the stream: the agent is told to stop, and the next
    prompt goes out once the agent has answered the one it was sent before. Red before the fix:
    the lock was given back as the stream closed, but the turn's own cleanup, which records the
    answer still owed and tells the agent to stop, ran only later, when the inner stream was
    collected; a prompt sent in between went out while the agent was still on the first one."""
    wire = _Connection()
    session = wire.session()
    first = session.stream_events("Review the last commit", timeout=5)
    wire.says("Reading the commit")
    event = await anext(first)
    assert event.kind == EVENT_TEXT_CHUNK
    await first.aclose()  # the reader stops part way, and closes what it read

    assert not session._turn_lock.locked()
    second = asyncio.ensure_future(_read(session.stream_events("Then just the summary")))
    await asyncio.sleep(0.2)
    assert wire.prompts() == [
        "Review the last commit"
    ], "the next prompt went out before the agent answered the one it was told to stop"
    await _until(lambda: wire.cancels == 1, "the agent was never told to stop")
    wire.answers(1, "cancelled")  # the agent answers the stop
    await wire.sent(2)
    assert wire.prompts()[1] == "Then just the summary"
    wire.answers(2)
    assert [e.kind for e in await asyncio.wait_for(second, timeout=_AT_ONCE)] == [EVENT_COMPLETE]


# ── one chat, on one agent CLI, through the chat runner ──────────────────────


@pytest.fixture
def kept_streams(monkeypatch):
    """Every turn stream an agent CLI session hands out, kept, so no collection can close one."""
    kept: list = []
    hand_out = AcpSession.stream_events

    def keeping(self, *args, **kwargs):
        stream = hand_out(self, *args, **kwargs)
        kept.append(stream)
        return stream

    monkeypatch.setattr(AcpSession, "stream_events", keeping)
    return kept


@pytest.fixture
def chat(tmp_path, monkeypatch):
    # The handshake's settle wait for MCP start-up notices: this agent sends none.
    monkeypatch.setattr("personalclaw.acp.client._DRAIN_DURATION", 0.05)
    worlds: list[_World] = []

    def make(scenario: str) -> _World:
        world = _World(
            tmp_path, scenario, dialect="codex", runtime="acp:codex", keys="spec", budget=5.0
        )
        worlds.append(world)
        return world

    return make


async def _turn_ends(task: asyncio.Task) -> None:
    await asyncio.wait_for(asyncio.shield(task), timeout=20)


async def _asked_again(w: _World) -> None:
    """Until the chat holds the agent's request for an approval, not yet answered."""
    await _until(
        lambda: ASKED in w.session._approval_futures
        and not w.session._approval_futures[ASKED].done(),
        f"the chat never asked about the command: {w.session.messages[-4:]}",
        within=10,
    )


@pytest.mark.asyncio
async def test_her_next_message_reaches_the_agent_cli_that_answered_the_last(chat, kept_streams):
    """Red before the fix: the second message's prompt was never sent to the live agent CLI,
    because the first turn's stream, left at its terminal event, still held the session."""
    w = chat("answers")
    try:
        await _turn_ends(w.start("Summarise the incident report"))
        assert w.answers() == [PLAIN_ANSWER]
        assert len(kept_streams) == 1, "vacuity: the turn's stream is kept"

        task = w.start("And what should I check first?")
        await _until(
            lambda: len(w.methods("session/prompt")) == 2,
            "the second message never reached the agent CLI",
        )
        await _turn_ends(task)
        assert w.answers() == [PLAIN_ANSWER, PLAIN_ANSWER]
        assert w.errors() == []
        # One process, one session: the second prompt went to the agent that answered the first.
        assert len(w.wire("spawn")) == 1 and len(w.methods("session/new")) == 1
    finally:
        await w.close()


@pytest.mark.asyncio
async def test_after_a_stop_her_next_message_reaches_the_same_agent_cli(chat, kept_streams):
    """A Stop while an approval waits ends the turn as stopped and keeps the agent CLI for the
    next message. Red before the fix: that message's prompt was never sent."""
    w = chat("wait-for-stop")
    try:
        task = w.start("summarise my notes")
        await _asked_again(w)
        app = web.Application()
        app["state"] = w.state
        request = make_mocked_request(
            "POST",
            f"/api/chat/sessions/{w.session.key}/stop",
            match_info={"session": w.session.key},
            app=app,
        )
        assert json.loads((await api_chat_session_stop(request)).body) == {
            "ok": True,
            "stopped": True,
        }
        await _turn_ends(task)
        assert w.outcomes()[-1:] == [TURN_STOPPED]

        task = w.start("then just list the files it changed")
        await _until(
            lambda: len(w.methods("session/prompt")) == 2,
            "the message after the Stop never reached the agent CLI",
        )
        await _asked_again(w)
        w.state.decide_session_approval(w.session, ASKED, "approved", by=YOU)
        await _turn_ends(task)
        assert any("I ran it." in a for a in w.answers()), w.session.messages
        assert len(w.wire("spawn")) == 1 and len(w.methods("session/new")) == 1
        assert len(kept_streams) == 2, "vacuity: both turns' streams are kept"
    finally:
        await w.close()


@pytest.mark.asyncio
async def test_a_turn_the_chat_stops_reading_part_way_leaves_the_agent_cli_for_the_next(
    chat, kept_streams, monkeypatch
):
    """The chat's own reading fails part way, here while it lists the agent's request for an
    approval: the turn ends in an error, its stream is closed, the agent is told to stop, and the
    next message runs on the same agent CLI. Red before the fix: nothing closed the stream, so
    the agent was never told to stop and the next message's prompt was never sent."""
    w = chat("wait-for-stop")
    hold = w.state.hold_session_approval
    failed: list[str] = []

    async def fails_once(*args, **kwargs):
        if not failed:
            failed.append("the approval could not be listed")
            raise RuntimeError(failed[0])
        return await hold(*args, **kwargs)

    monkeypatch.setattr(w.state, "hold_session_approval", fails_once)
    try:
        await _turn_ends(w.start("summarise my notes"))
        assert failed, "vacuity: the chat's reading failed part way"
        assert w.errors(), "the failed turn said nothing"
        await _until(lambda: len(w.methods(METHOD_CANCEL)) == 1, "the agent was never told to stop")

        task = w.start("then just list the files it changed")
        await _until(
            lambda: len(w.methods("session/prompt")) == 2,
            "the next message never reached the agent CLI",
        )
        await _asked_again(w)
        w.state.decide_session_approval(w.session, ASKED, "approved", by=YOU)
        await _turn_ends(task)
        assert any("I ran it." in a for a in w.answers()), w.session.messages
        assert len(w.wire("spawn")) == 1
        # The request of the turn that failed was answered as a stopped turn's is.
        assert [a["outcome"] for a in w.wire("permission_answer")] == ["cancelled", "selected"]
    finally:
        await w.close()

"""Every way a chat turn ends says so in a last ``work_phase`` frame, and leaves nothing ticking.

A turn's phase is said again while it runs (``chat_phase``'s liveness), so a turn whose ending went
unsaid would read as running forever on every surface that shows it. This census walks the
runner's exits — anchored on the outcomes ``terminal_outcome_for_turn`` can give, so a new outcome
cannot ship without a case here — and holds each to the same three things: one last frame, saying
how the turn ended, with no turn in it; and no liveness task left behind. A retry the runner queues
for the same turn (its connection was lost before any step) is not an ending: the turn goes on.
"""

from __future__ import annotations

import asyncio
import inspect
import itertools
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, patch

import pytest
from test_work_phase_classifier import CHAT, _answer, _call, _quiet, _scripted, _World

from personalclaw.acp.errors import AcpProcessDied
from personalclaw.acp.types import CANCELLED_STOP_REASONS
from personalclaw.dashboard import chat_phase, chat_runner, turn_deadline
from personalclaw.dashboard.chat_runner import (
    TURN_COMPLETE,
    TURN_ERROR,
    TURN_INTERRUPTED,
    TURN_STOPPED,
    run_chat,
    terminal_outcome_for_turn,
)
from personalclaw.llm.events import (
    EVENT_COMPLETE,
    EVENT_PERMISSION_REQUEST,
    EVENT_TEXT_CHUNK,
    AgentEvent,
)
from personalclaw.restart_request import RESTARTING


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)


def _forever() -> AsyncIterator[AgentEvent]:
    async def stream() -> AsyncIterator[AgentEvent]:
        await asyncio.Event().wait()
        yield AgentEvent(kind=EVENT_COMPLETE)

    return stream()


async def _nothing_ticking() -> list[str]:
    """The liveness tasks still running once the loop has had a turn to settle cancellations."""
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    return [
        t.get_name()
        for t in asyncio.all_tasks()
        if t.get_name().startswith("work_phase:") and not t.done()
    ]


def _endings(w: _World) -> list[dict]:
    return [f for f in w.phases() if f["turn"] is None]


async def _completes(w: _World) -> None:
    w.client.stream.side_effect = lambda *a, **kw: _scripted(*_answer())()
    await w.turn()


async def _stops(w: _World) -> None:
    w.client.stream.side_effect = lambda *a, **kw: _scripted(
        AgentEvent(kind=EVENT_COMPLETE, stop_reason="cancelled")
    )()
    await w.turn()


async def _fails(w: _World) -> None:
    w.state.sessions.get_or_create = AsyncMock(side_effect=RuntimeError("upstream said no"))
    await w.turn()


async def _is_interrupted(w: _World) -> None:
    w.client.stream.side_effect = lambda *a, **kw: _forever()
    w.start()
    await w.until("thinking")
    with patch("personalclaw.restart_request.stopping_for", return_value=RESTARTING):
        await w.state.end_running_turns(timeout=10)


async def _answers_a_command_locally(w: _World) -> None:
    await w.turn("/quit")


async def _lists_its_prompts_locally(w: _World) -> None:
    with patch("personalclaw.dashboard.chat_runner._list_provider_prompts", return_value=[]):
        await w.turn("/prompts")


async def _is_stopped_before_its_prompt(w: _World) -> None:
    """A Stop (or a move) that lands while the turn is put together: its model is never asked."""
    asked: list[str] = []

    async def stream() -> AsyncIterator[AgentEvent]:
        asked.append("asked")
        for event in _answer():
            yield event

    async def acquire(*_a, **_kw):
        w.session._stop_asked = True
        return (w.client, True, False)

    w.client.stream.side_effect = lambda *a, **kw: stream()
    w.state.sessions.get_or_create = AsyncMock(side_effect=acquire)
    await w.turn()
    assert asked == []


async def _passes_its_time_limit(w: _World) -> None:
    w.client.stream.side_effect = lambda *a, **kw: _forever()
    w.session.append("user", "hello", "msg msg-u")
    with _quiet():
        await turn_deadline.run_within(
            w.state, w.session, run_chat(w.state, w.session, "hello"), 0.2
        )


async def _is_declined(w: _World) -> None:
    """Her Deny, and the agent ends the turn with nothing more to say."""
    w.session._trust = False
    w.client.stream.side_effect = lambda *a, **kw: _scripted(
        _call("shell", "t1", tool_input={"command": "ls"}),
        AgentEvent(
            kind=EVENT_PERMISSION_REQUEST,
            title="shell",
            request_id="r1",
            tool_call_id="t1",
            tool_input='{"command": "ls"}',
        ),
        AgentEvent(kind=EVENT_COMPLETE, stop_reason="end_turn"),
    )()
    task = w.start()
    await w.until("approval")
    from personalclaw.approval_answer import YOU

    w.state.decide_session_approval(w.session, "r1", "rejected", by=YOU)
    await asyncio.wait_for(task, timeout=10)


#: Each way out of the runner: how it is driven, how its turn ended (``last_outcome``), and the
#: phase and line its last frame says it in. ``None`` for a line read off the transcript.
ENDINGS = [
    ("complete", _completes, "complete", "done", "Done in "),
    ("stopped", _stops, "stopped", "done", "Stopped"),
    ("error", _fails, "error", "error", None),
    ("interrupted", _is_interrupted, "interrupted", "error", None),
    ("answered locally", _answers_a_command_locally, "complete", "done", "Done in "),
    ("listed locally", _lists_its_prompts_locally, "complete", "done", "Done in "),
    ("stopped before its prompt", _is_stopped_before_its_prompt, "stopped", "done", "Stopped"),
    ("past its time limit", _passes_its_time_limit, "error", "error", None),
    ("declined", _is_declined, "error", "done", None),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("drive", "outcome", "phase", "line"), [e[1:] for e in ENDINGS], ids=[e[0] for e in ENDINGS]
)
async def test_each_way_out_says_how_the_turn_ended_once(tmp_path, drive, outcome, phase, line):
    w = _World(tmp_path, _scripted(*_answer()))
    await drive(w)

    (last,) = _endings(w)
    assert w.phases()[-1] is last, "a frame followed the turn's ending"
    assert (last["phase"], last["last_outcome"]) == (phase, outcome)
    if line is None:
        # The reason the transcript gives, in the turn's own last error or notice row.
        told = next(m["content"] for m in reversed(w.session.messages) if m["role"] == "error")
        assert told in last["line"]
    else:
        assert last["line"].startswith(line)
    assert await _nothing_ticking() == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("took", "line"), [(0.3, "Done in under a second"), (12.2, "Done in 12 seconds")]
)
async def test_a_finished_turn_says_how_long_it_took(tmp_path, monkeypatch, took, line):
    clock = [1_000.0]
    monkeypatch.setattr(chat_phase, "_now", lambda: clock[0])

    def stream() -> AsyncIterator[AgentEvent]:
        async def answer() -> AsyncIterator[AgentEvent]:
            clock[0] += took
            for event in _answer():
                yield event

        return answer()

    w = _World(tmp_path, stream)
    await w.turn()
    (last,) = _endings(w)
    assert last["line"] == line


@pytest.mark.asyncio
async def test_a_turn_that_answers_after_her_deny_is_done_not_declined(tmp_path):
    """Her Deny of one call, and the agent answers without it: the turn ends complete."""
    w = _World(tmp_path, _scripted())
    w.session._trust = False
    w.client.stream.side_effect = lambda *a, **kw: _scripted(
        _call("shell", "t1", tool_input={"command": "ls"}),
        AgentEvent(
            kind=EVENT_PERMISSION_REQUEST,
            title="shell",
            request_id="r1",
            tool_call_id="t1",
            tool_input='{"command": "ls"}',
        ),
        AgentEvent(kind=EVENT_TEXT_CHUNK, text="Then I will leave the folder as it is."),
        AgentEvent(kind=EVENT_COMPLETE, stop_reason="end_turn"),
    )()
    task = w.start()
    await w.until("approval")
    from personalclaw.approval_answer import YOU

    w.state.decide_session_approval(w.session, "r1", "rejected", by=YOU)
    await asyncio.wait_for(task, timeout=10)

    (last,) = _endings(w)
    assert (last["phase"], last["last_outcome"]) == ("done", "complete")
    assert last["line"].startswith("Done in "), last["line"]


@pytest.mark.asyncio
async def test_a_turn_gives_only_its_own_error_as_its_reason(tmp_path):
    """An earlier turn's error is not why a later one stopped, though no row names the later one:
    a turn engine that raises before writing one, run as ``run_chat`` is."""
    w = _World(tmp_path, _scripted())
    w.session.append("error", "An earlier turn's failure.", "msg msg-err")
    failing = chat_runner._says_its_phases(AsyncMock(side_effect=RuntimeError("no row written")))
    with pytest.raises(RuntimeError):
        await failing(w.state, w.session, "a message with no row of its own")

    (last,) = _endings(w)
    assert (last["phase"], last["last_outcome"], last["line"]) == ("error", "error", "Stopped")


@pytest.mark.asyncio
async def test_the_restart_sentence_is_the_reason_an_interrupted_turn_gives(tmp_path):
    w = _World(tmp_path, _scripted())
    await _is_interrupted(w)
    (last,) = _endings(w)
    assert last["line"] == (
        "Stopped: The gateway restarted before this reply finished. Send your message again to "
        "retry."
    )


@pytest.mark.asyncio
async def test_a_queued_follow_up_ends_its_turn_before_it_starts_its_own(tmp_path):
    gate = asyncio.Event()
    streams = iter(range(2))

    def stream(*_a, **_kw) -> AsyncIterator[AgentEvent]:
        async def first() -> AsyncIterator[AgentEvent]:
            await gate.wait()
            for event in _answer():
                yield event

        return first() if next(streams) == 0 else _scripted(*_answer())()

    w = _World(tmp_path, _scripted())
    w.client.stream.side_effect = stream
    task = w.start("first")
    await w.until("thinking")
    w.session.queue_append("and another", attended=True)
    gate.set()
    await asyncio.wait_for(task, timeout=10)
    while w.session.task is not None and not w.session.task.done():
        await asyncio.wait_for(w.session.task, timeout=10)

    first, second = _endings(w)
    turns = [f["turn"]["message_id"] for f in w.phases() if f["turn"]]
    assert len(set(turns)) == 2, "two turns, each named by its own message"
    at = w.phases().index(first)
    assert w.phases()[at + 1]["phase"] == "queued", "the next turn starts after the first ends"
    assert (first["last_outcome"], second["last_outcome"]) == ("complete", "complete")
    assert await _nothing_ticking() == []


@pytest.mark.asyncio
async def test_the_frame_that_ends_a_turn_asks_no_stop_of_it(tmp_path):
    """A stop asked of a turn is over with it: the frame that ends a turn a stop was asked of, with
    her next message queued behind it, says no stop is asked, as the routes then serve it."""
    gate = asyncio.Event()
    streams = iter(range(2))

    def stream(*_a, **_kw) -> AsyncIterator[AgentEvent]:
        async def first() -> AsyncIterator[AgentEvent]:
            await gate.wait()
            yield AgentEvent(kind=EVENT_COMPLETE, stop_reason="cancelled")

        return first() if next(streams) == 0 else _scripted(*_answer())()

    w = _World(tmp_path, _scripted())
    w.client.stream.side_effect = stream
    task = w.start("first")
    await w.until("thinking")
    w.session.queue_append("and another", attended=True)
    w.session._stopping = True  # asked, and not yet answered, as her Stop leaves it
    chat_phase.restate(w.state, w.session)
    assert w.phases()[-1]["stopping"] is True
    gate.set()
    await asyncio.wait_for(task, timeout=10)
    while w.session.task is not None and not w.session.task.done():
        await asyncio.wait_for(w.session.task, timeout=10)

    first, second = _endings(w)
    assert first["last_outcome"] == "stopped"
    assert (first["stopping"], second["stopping"]) == (False, False)


@pytest.mark.asyncio
async def test_a_retry_of_the_same_turn_is_not_an_ending(tmp_path):
    """Its connection was lost before any step, so its message is sent again, as the same turn."""
    w = _World(tmp_path, _scripted(*_answer()))
    w.state.sessions.get_or_create = AsyncMock(
        side_effect=[AcpProcessDied("agent exited"), (w.client, True, False)]
    )
    await w.turn()

    (last,) = _endings(w)
    assert last is w.phases()[-1] and last["last_outcome"] == "complete"
    named = {f["turn"]["message_id"] for f in w.phases() if f["turn"]}
    assert len(named) == 1, "the retry is the same turn"
    assert [f["phase"] for f in w.phases()].count("queued") == 1
    assert await _nothing_ticking() == []


def test_every_outcome_the_runner_can_give_has_a_case_here() -> None:
    """The census is anchored on the one function that says how a turn ended: every outcome it
    gives, over every combination of what it reads, is a way out with its case above."""
    facts = [name for name in inspect.signature(terminal_outcome_for_turn).parameters]
    flags = [name for name in facts if name != "stop_reason"]
    assert "stop_reason" in facts and len(flags) >= 4, f"read {facts} — vacuous"
    given = {
        terminal_outcome_for_turn(stop_reason=reason, **dict(zip(flags, values, strict=True)))
        for reason in ("end_turn", *sorted(CANCELLED_STOP_REASONS))
        for values in itertools.product((False, True), repeat=len(flags))
    }
    closed = {TURN_COMPLETE, TURN_STOPPED, TURN_ERROR, TURN_INTERRUPTED}
    assert given == closed, f"it gives {sorted(given)}; the closed set is {sorted(closed)}"
    assert given <= {e[2] for e in ENDINGS}, sorted(given - {e[2] for e in ENDINGS})


@pytest.mark.asyncio
async def test_the_phase_names_the_chat_the_ending_belongs_to(tmp_path):
    w = _World(tmp_path, _scripted(*_answer()))
    await w.turn()
    assert {f["session"] for f in w.phases()} == {CHAT}

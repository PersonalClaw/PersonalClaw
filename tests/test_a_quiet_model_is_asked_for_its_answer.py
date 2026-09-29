"""A model that runs tools and then writes nothing is asked, once, for its reply.

Measured on a local model: after fifteen shell calls its sixteenth answer was empty, the loop
took "no tool calls" for the end of the turn, and the person got no reply at all. Resending their
message would run every step again, so the native loop goes on instead: one more inference, with
the results it already has and a note asking for the reply. The note rides that one request and
never enters the history; a second silence ends the turn, and the surface says so.
"""

from __future__ import annotations

import logging

import pytest

from personalclaw.agents.native.runtime import ANSWER_OWED_NOTE, NativeAgentRuntime
from personalclaw.agents.provider import AgentRuntimeDefinition
from personalclaw.llm.events import (
    EVENT_COMPLETE,
    EVENT_TEXT_CHUNK,
    EVENT_TOOL_CALL,
    STOP_MAX_TOKENS,
    AgentEvent,
)
from personalclaw.tool_providers.base import RiskLevel, ToolDefinition, ToolProvider, ToolResult

pytestmark = pytest.mark.asyncio


class _Script:
    """A model that replays one list of events per request and keeps every request it got."""

    supports_tools = True
    _model = "scripted"

    def __init__(self, turns: list[list[AgentEvent]]) -> None:
        self._turns = turns
        self.calls = 0
        self.seen: list[list[dict]] = []

    async def complete(self, messages, *, tools=None, model=None, reasoning_effort=""):
        self.seen.append(list(messages))
        idx = min(self.calls, len(self._turns) - 1)
        self.calls += 1
        for ev in self._turns[idx]:
            yield ev


class _Look(ToolProvider):
    """One read-only tool, so the loop runs a real step."""

    @property
    def name(self) -> str:
        return "look"

    @property
    def display_name(self) -> str:
        return "Look"

    async def list_tools(self):
        return [
            ToolDefinition(
                name="look",
                description="Read something.",
                parameters={"type": "object"},
                risk_level=RiskLevel.SAFE,
            )
        ]

    async def invoke(self, tool_name, arguments):
        return ToolResult(success=True, output="notes.md: 3 lines changed")


def _call(i: int = 1) -> list[AgentEvent]:
    return [
        AgentEvent(kind=EVENT_TOOL_CALL, tool_call_id=f"c{i}", title="look", tool_input="{}"),
        AgentEvent(kind=EVENT_COMPLETE),
    ]


QUIET = [AgentEvent(kind=EVENT_COMPLETE)]
ANSWER = [
    AgentEvent(kind=EVENT_TEXT_CHUNK, text="The change renames one helper."),
    AgentEvent(kind=EVENT_COMPLETE),
]


async def _runtime(model: _Script, *, surface: str = "") -> NativeAgentRuntime:
    rt = NativeAgentRuntime(
        definition=AgentRuntimeDefinition(name="T", provider="native", model="scripted"),
        model_provider=model,
        tool_providers=[_Look()],
        surface=surface,
    )
    await rt.start()
    rt.set_approval_policy("auto")
    return rt


async def _turn(rt: NativeAgentRuntime, message: str = "Review the diff.") -> list[AgentEvent]:
    return [ev async for ev in rt.stream(message)]


def _texts(events: list[AgentEvent]) -> list[str]:
    return [ev.text for ev in events if ev.kind == EVENT_TEXT_CHUNK]


def _is_the_note(msg: dict) -> bool:
    return msg.get("role") == "user" and msg.get("content") == ANSWER_OWED_NOTE


async def test_a_model_quiet_after_its_tools_is_asked_once_and_its_reply_ends_the_turn(caplog):
    model = _Script([_call(), QUIET, ANSWER])
    rt = await _runtime(model)
    with caplog.at_level(logging.WARNING, logger="personalclaw.agents.native.runtime"):
        events = await _turn(rt)

    assert model.calls == 3, "the quiet answer was taken for the end of the turn"
    assert _texts(events) == ["The change renames one helper."]
    done = events[-1]
    assert done.kind == EVENT_COMPLETE and done.stop_reason == "end_turn"
    assert done.tool_call_count == 1
    # The note rides the one request that asks, at its tail, and no other request.
    asking = model.seen[2][-1]
    assert _is_the_note(asking) and asking.get("_volatile") is True
    assert not any(_is_the_note(m) for m in model.seen[1])
    # …and never the history: no note, and no empty assistant message for the silence.
    assert not any(_is_the_note(m) for m in rt._messages)
    assert not any(
        m.get("role") == "assistant" and not m.get("content") and not m.get("tool_calls")
        for m in rt._messages
    )
    assert any(
        "without a reply" in r.getMessage() for r in caplog.records
    ), "the log said nothing about how the turn went on"


async def test_a_second_silence_ends_the_turn_with_nothing_written():
    model = _Script([_call(), QUIET, QUIET, ANSWER])
    rt = await _runtime(model)
    events = await _turn(rt)

    assert model.calls == 3, "asked more than once — a quiet model must not hold the turn open"
    assert _texts(events) == []
    assert events[-1].kind == EVENT_COMPLETE and events[-1].tool_call_count == 1


async def test_the_request_is_not_carried_into_the_next_turn():
    model = _Script([_call(), QUIET, ANSWER, ANSWER])
    rt = await _runtime(model)
    await _turn(rt)
    await _turn(rt, "Thanks. Anything else?")

    assert model.calls == 4
    assert not any(_is_the_note(m) for m in model.seen[3])


async def test_a_turn_that_ran_nothing_is_not_asked():
    """A blank turn is its surface's to retry: nothing ran, so resending costs nothing."""
    model = _Script([QUIET, ANSWER])
    rt = await _runtime(model)
    await _turn(rt)
    assert model.calls == 1


async def test_a_turn_that_wrote_before_its_last_call_is_not_asked():
    written_then_saved = [
        AgentEvent(kind=EVENT_TEXT_CHUNK, text="Done — saving a note of it."),
        *_call(),
    ]
    model = _Script([written_then_saved, QUIET, ANSWER])
    rt = await _runtime(model)
    await _turn(rt)
    assert model.calls == 2, "an answer followed by one closing call was asked for an answer"


async def test_a_loop_worker_is_not_asked():
    """A loop's deliverable is a file, and the loop re-prompts a cycle that wrote none."""
    model = _Script([_call(), QUIET, ANSWER])
    rt = await _runtime(model, surface="loops")
    await _turn(rt)
    assert model.calls == 2


async def test_a_reply_cut_at_the_output_cap_is_not_asked():
    """Asking again meets the same cap; the length stop is what the surface reports."""
    model = _Script([_call(), [AgentEvent(kind=EVENT_COMPLETE, stop_reason=STOP_MAX_TOKENS)]])
    rt = await _runtime(model)
    events = await _turn(rt)
    assert model.calls == 2
    assert events[-1].stop_reason == STOP_MAX_TOKENS

"""A tool call cut at the output limit says so on its card, and a second cut ends the turn.

From an owner's chat (#5725): a model call streamed for nearly five minutes and stopped at its
output limit inside a ``write_file``. The call was not run, and the model was told why (#1773), but
the card the owner saw was a failed "Write file" with the note written for the model ("Respond more
concisely so the full answer fits"). Nothing said the reply had hit its output limit, or that the
agent would try again. And the model-call log's row for that attempt said ``output_cap`` with no
tokens and no cost, though its usage was known.

Now the card says it for the person, "Cut off at the output limit after 5 minutes. Trying again.",
while the model's history keeps its own note. A second cut in the same turn ends the turn at the
cap, with the same words, and the chat ends it on the notice it offers Retry on. The capped
attempt's row carries its tokens and cost, and the turn's usage row names that row, so the usage
fold's census does not count the call twice.

Driven through the real native loop over a scripted model; no real model is called.
"""

from __future__ import annotations

import pytest
from test_native_runtime import _defn, _ScriptedModel, _Tool

from personalclaw.agents.native import loop_phases
from personalclaw.agents.native import runtime as runtime_mod
from personalclaw.agents.native.runtime import NativeAgentRuntime
from personalclaw.dashboard import turn_endings
from personalclaw.guardrails.failure import FailureMode, correction_note
from personalclaw.llm.events import (
    EVENT_COMPLETE,
    EVENT_TEXT_CHUNK,
    EVENT_TOOL_CALL,
    EVENT_TOOL_RESULT,
    AgentEvent,
)

#: How long the cut answer streamed: 4 minutes 48 seconds, as in the owner's chat.
STREAMED_MS = 288_000
#: The output tokens it stopped at.
CAP = 32_000
TRYING_AGAIN = "Cut off at the output limit after 5 minutes. Trying again."
ENDED = "Cut off at the output limit after 5 minutes."


def _cut_call(call_id: str) -> list[AgentEvent]:
    """One answer cut at its output limit while it wrote a ``write_file``: the call carries the
    length stop and a prefix of its arguments, and so does the answer's end."""
    call = AgentEvent(
        kind=EVENT_TOOL_CALL,
        tool_call_id=call_id,
        title="write_file",
        tool_input='{"path": "report.md", "content": "# Q3\\n\\nRevenue',
        stop_reason="max_tokens",
    )
    done = AgentEvent(
        kind=EVENT_COMPLETE,
        stop_reason="max_tokens",
        input_tokens=12_000,
        output_tokens=CAP,
        cost_usd=0.81,
    )
    return [call, done]


ANSWER = [AgentEvent(kind=EVENT_TEXT_CHUNK, text="Wrote it."), AgentEvent(kind=EVENT_COMPLETE)]


class _SlowModel(_ScriptedModel):
    """The scripted model, each answer taking :data:`STREAMED_MS` on the loop's clock."""

    def __init__(self, turns, clock: list[float]) -> None:
        super().__init__(turns)
        self._clock = clock

    async def complete(self, messages, *, tools=None, model=None, reasoning_effort=""):
        self._clock[0] += STREAMED_MS
        async for ev in super().complete(
            messages, tools=tools, model=model, reasoning_effort=reasoning_effort
        ):
            yield ev


@pytest.fixture
def clock(monkeypatch) -> list[float]:
    now = [1_000.0]
    monkeypatch.setattr(loop_phases, "now_ms", lambda: now[0])
    return now


@pytest.fixture
def rows(monkeypatch) -> list:
    """The model-call log rows the native loop writes."""
    written: list = []
    monkeypatch.setattr(runtime_mod, "record_attempt", written.append)
    return written


async def _turn(turns, clock) -> tuple[list[AgentEvent], _SlowModel, _Tool]:
    model = _SlowModel(turns, clock)
    tool = _Tool(name="write_file")
    runtime = NativeAgentRuntime(definition=_defn(), model_provider=model, tool_providers=[tool])
    await runtime.start()
    events = [ev async for ev in runtime.stream("write the Q3 report")]
    return events, model, tool


def _cards(events: list[AgentEvent]) -> dict[str, str]:
    return {e.tool_call_id: e.tool_output for e in events if e.kind == EVENT_TOOL_RESULT}


def _told(model: _ScriptedModel, call_id: str) -> list[str]:
    """What the model's last request answered the call *call_id* with."""
    return [
        str(m.get("content") or "")
        for m in model.seen_messages[-1]
        if m.get("role") == "tool" and m.get("tool_call_id") == call_id
    ]


@pytest.mark.asyncio
async def test_a_cut_call_s_card_says_so_and_the_turn_tries_again(clock, rows) -> None:
    events, model, tool = await _turn([_cut_call("tc-1"), ANSWER], clock)

    assert _cards(events) == {"tc-1": TRYING_AGAIN}
    assert tool.invoked == [], "a call cut mid-way runs nothing"
    assert model.calls == 2, "the turn tried again"
    told = _told(model, "tc-1")
    assert told and told[0].endswith(correction_note(FailureMode.TOKEN_OVERFLOW)), told
    assert "Cut off at the output limit" not in told[0], "the card's words are not the model's"
    assert events[-1].kind == EVENT_COMPLETE and events[-1].stop_reason == "end_turn"


@pytest.mark.asyncio
async def test_a_second_cut_in_the_turn_ends_it_saying_so(clock, rows) -> None:
    events, model, tool = await _turn([_cut_call("tc-1"), _cut_call("tc-2"), ANSWER], clock)

    assert _cards(events) == {"tc-1": TRYING_AGAIN, "tc-2": ENDED}
    assert (
        model.calls == 2
    ), "asked again at the same limit, it is cut again: not asked a third time"
    assert tool.invoked == []
    end = events[-1]
    assert (end.kind, end.stop_reason, end.output_cap) == (EVENT_COMPLETE, "max_tokens", CAP)
    # The chat ends such a turn on the notice it offers Retry on: no answer, and its stop says why.
    assert (
        turn_endings.unanswered_turn(
            wrote_text=False,
            stop_reason=end.stop_reason,
            saw_compaction=False,
            needs_session_reset=False,
            is_slash=False,
            tool_call_count=end.tool_call_count,
            is_loop=False,
        )
        == turn_endings.UNANSWERED_SAYS_WHY
    )


@pytest.mark.asyncio
async def test_a_call_with_unreadable_arguments_that_was_not_cut_keeps_its_note(
    clock, rows
) -> None:
    """Floor: arguments that are not JSON, with no length stop, are the model's mistake to fix,
    not a cut. The card shows what the model is told, and two in a row do not end the turn."""

    def bad(call_id: str) -> list[AgentEvent]:
        call = AgentEvent(
            kind=EVENT_TOOL_CALL, tool_call_id=call_id, title="write_file", tool_input="{'p': 1}"
        )
        return [call, AgentEvent(kind=EVENT_COMPLETE)]

    events, model, _tool = await _turn([bad("tc-1"), bad("tc-2"), ANSWER], clock)

    cards = _cards(events)
    assert all("not valid JSON" in c and "output limit" not in c for c in cards.values()), cards
    assert model.calls == 3, "the turn went on to its answer"
    assert events[-1].stop_reason == "end_turn"


@pytest.mark.asyncio
async def test_the_capped_attempt_s_row_carries_its_tokens_and_cost(clock, rows) -> None:
    events, _model, _tool = await _turn([_cut_call("tc-1"), ANSWER], clock)

    capped = [r for r in rows if r.failure_mode == FailureMode.OUTPUT_CAP.value]
    assert len(capped) == 1, rows
    row = capped[0]
    assert (row.tokens_in, row.tokens_out, row.dollars_est) == (12_000, CAP, 0.81)
    assert row.priced is True and row.estimated is False, "its provider reported the cost"
    assert row.audit_id in events[-1].audit_ids, "the turn's usage row names it, so it counts once"

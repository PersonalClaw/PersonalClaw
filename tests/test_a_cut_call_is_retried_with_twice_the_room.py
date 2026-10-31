"""A tool call cut at the output limit is tried again with twice the room, where its provider takes
it.

After #5725's first half, a call cut at its output limit says so and the turn tries again, but at
the same limit, where a long ``write_file`` meets the limit again (#5795). Core could not ask one
request for more: ``complete()`` carried no output cap, and only the provider knows its model's
ceiling. Now a provider declares that it takes a cap for one request
(``ModelProvider.takes_output_cap``), and holds it to its own configured cap and its model's
ceiling. The request after a cut call asks such a provider for twice what the call stopped at,
once in a turn. Any other provider is sent no such keyword, and is asked again as before.

Driven through the real native loop over scripted models, bare and behind the spend guard.
"""

from __future__ import annotations

import pytest
from test_a_call_cut_at_the_output_limit_says_so_on_its_card import ANSWER, CAP, _cut_call
from test_native_runtime import _defn, _ScriptedModel, _Tool

from personalclaw.agents.native.runtime import NativeAgentRuntime
from personalclaw.guardrails.model_call import ModelCallGuard
from personalclaw.llm.events import EVENT_COMPLETE, EVENT_TEXT_CHUNK, EVENT_TOOL_CALL, AgentEvent

#: A call that reads whole and runs.
READ = [
    AgentEvent(
        kind=EVENT_TOOL_CALL, tool_call_id="tc-read", title="read_file", tool_input='{"path": "a"}'
    ),
    AgentEvent(kind=EVENT_COMPLETE),
]


class _TakesACap(_ScriptedModel):
    """A scripted provider that takes an output cap for one request, and keeps each it was sent."""

    takes_output_cap = True

    def __init__(self, turns) -> None:
        super().__init__(turns)
        self.caps: list[int | None] = []

    async def complete(
        self, messages, *, tools=None, model=None, reasoning_effort="", max_tokens=None
    ):
        self.caps.append(max_tokens)
        async for ev in super().complete(
            messages, tools=tools, model=model, reasoning_effort=reasoning_effort
        ):
            yield ev


async def _turn(model) -> list[AgentEvent]:
    runtime = NativeAgentRuntime(
        definition=_defn(),
        model_provider=model,
        tool_providers=[_Tool(name="write_file"), _Tool(name="read_file")],
    )
    await runtime.start()
    return [ev async for ev in runtime.stream("write the Q3 report")]


@pytest.mark.asyncio
async def test_the_try_after_a_cut_call_asks_for_twice_the_room() -> None:
    model = _TakesACap([_cut_call("tc-1"), ANSWER])
    events = await _turn(model)

    assert model.caps == [None, 2 * CAP]
    assert events[-1].stop_reason == "end_turn"


@pytest.mark.asyncio
async def test_the_room_rides_that_one_request() -> None:
    model = _TakesACap([_cut_call("tc-1"), READ, ANSWER])
    await _turn(model)

    assert model.caps == [None, 2 * CAP, None]


@pytest.mark.asyncio
async def test_a_second_cut_is_not_given_more_room_and_ends_the_turn() -> None:
    model = _TakesACap([_cut_call("tc-1"), _cut_call("tc-2"), ANSWER])
    events = await _turn(model)

    assert model.caps == [None, 2 * CAP]
    assert events[-1].stop_reason == "max_tokens"


@pytest.mark.asyncio
async def test_a_provider_that_takes_no_cap_is_sent_none_and_asked_again_as_before() -> None:
    """Its ``complete`` has no ``max_tokens``: sending one would raise. It is asked again at its
    own limit, as before."""
    model = _ScriptedModel([_cut_call("tc-1"), ANSWER])
    events = await _turn(model)

    assert model.calls == 2
    assert events[-1].stop_reason == "end_turn"


@pytest.mark.asyncio
async def test_behind_the_spend_guard_the_declaration_and_the_room_pass_through() -> None:
    inner = _TakesACap([_cut_call("tc-1"), ANSWER])
    guard = ModelCallGuard(inner, use_case="loops", provider_name="planner-test", model="m")
    assert guard.takes_output_cap is True
    await _turn(guard)

    assert inner.caps == [None, 2 * CAP]


@pytest.mark.asyncio
async def test_behind_the_spend_guard_a_provider_that_takes_no_cap_is_sent_none() -> None:
    inner = _ScriptedModel([_cut_call("tc-1"), ANSWER])
    guard = ModelCallGuard(inner, use_case="loops", provider_name="planner-test", model="m")
    assert guard.takes_output_cap is False
    events = await _turn(guard)

    assert inner.calls == 2 and events[-1].stop_reason == "end_turn"


@pytest.mark.asyncio
async def test_room_goes_only_to_the_model_the_request_goes_to() -> None:
    """The declaration is read where the request goes out: room asked of a model that takes it is
    not sent to one that has no such keyword, which would refuse the call outright. The turn's
    model is changed after the cut call's room was asked and before the next request goes out."""
    first = _TakesACap([_cut_call("tc-1")])
    then = _ScriptedModel([ANSWER])  # its `complete` has no `max_tokens`
    runtime = NativeAgentRuntime(
        definition=_defn(), model_provider=first, tool_providers=[_Tool(name="write_file")]
    )
    await runtime.start()

    def moved_on() -> list[str]:  # the steer phase: after the calls, before the next request
        runtime._model = then
        return []

    runtime._take_steers = moved_on  # type: ignore[method-assign]
    events = [ev async for ev in runtime.stream("write the Q3 report")]

    assert (first.caps, then.calls) == ([None], 1)
    assert events[-1].stop_reason == "end_turn"


@pytest.mark.asyncio
async def test_the_spend_guard_sends_a_cap_only_to_a_provider_that_takes_one() -> None:
    """Whoever asks: the guard is the boundary, so a caller that asked a cap of a guarded model
    that takes none sends it none."""
    inner = _ScriptedModel([[AgentEvent(kind=EVENT_TEXT_CHUNK, text="ok"), ANSWER[-1]]])
    guard = ModelCallGuard(inner, use_case="loops", provider_name="planner-test", model="m")

    seen = [ev async for ev in guard.complete([{"role": "user", "content": "hi"}], max_tokens=9)]

    assert inner.calls == 1 and any(ev.text == "ok" for ev in seen)

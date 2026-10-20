"""Each turn's tool-catalog note replaces the last one; it never piles up in the history.

A native turn whose tool set is larger than the retrieval budget sends the full schemas of the
tools that matter this turn and lists every other tool by name in a note. That note was appended
to the loop's history and never taken out, so every turn carried every earlier turn's catalog as
well as its own. Measured with a scripted model and 80 tools: 1, 2, 3 … 6 copies of the catalog
over six turns, the request growing by about 3,300 characters a turn that said nothing new —
and a provider that moves notes to the tail (the Anthropic client) shipped all of them there.
"""

from __future__ import annotations

import pytest
from test_native_runtime import _defn, _drain, _ManyTools, _ScriptedModel

from personalclaw.agents.native.runtime import NativeAgentRuntime
from personalclaw.llm.events import EVENT_COMPLETE, EVENT_TEXT_CHUNK, EVENT_TOOL_CALL, AgentEvent

_CATALOG = "[tool catalog]"


def _catalog_notes(messages: list[dict]) -> list[dict]:
    return [m for m in messages if m.get("role") == "system" and _CATALOG in str(m.get("content"))]


def _chars(messages: list[dict]) -> int:
    return sum(len(str(m.get("content") or "")) for m in messages)


@pytest.mark.asyncio
async def test_six_turns_send_one_catalog_note_each():
    reply = [AgentEvent(kind=EVENT_TEXT_CHUNK, text="ok"), AgentEvent(kind=EVENT_COMPLETE)]
    model = _ScriptedModel([reply])
    rt = NativeAgentRuntime(
        definition=_defn(), model_provider=model, tool_providers=[_ManyTools(80)]
    )
    await rt.start()

    sent_sizes = []
    for turn in range(1, 7):
        await _drain(rt, f"turn {turn}: something no niche tool is for")
        sent = model.seen_messages[-1]
        assert len(_catalog_notes(sent)) == 1, f"turn {turn} sent {len(_catalog_notes(sent))}"
        assert len(_catalog_notes(rt._messages)) == 1
        sent_sizes.append(_chars(sent) - _chars(_catalog_notes(sent)))

    # What the requests grow by is the conversation itself (a user line and a reply a turn),
    # nothing more: without the catalog each request is the last one plus that turn's words.
    growth = [b - a for a, b in zip(sent_sizes, sent_sizes[1:], strict=False)]
    assert max(growth) < 200, growth


@pytest.mark.asyncio
async def test_the_note_stays_for_every_inference_of_its_own_turn():
    """Within a turn the note is part of the prompt every inference sends (the tool loop's second
    request still offers the catalog), at the place it was put: after the turn's user message."""
    model = _ScriptedModel(
        [
            [
                AgentEvent(
                    kind=EVENT_TOOL_CALL,
                    tool_call_id="c1",
                    title="niche_tool_3",
                    tool_input='{"q": "x"}',
                ),
                AgentEvent(kind=EVENT_COMPLETE),
            ],
            [AgentEvent(kind=EVENT_TEXT_CHUNK, text="done"), AgentEvent(kind=EVENT_COMPLETE)],
        ]
    )
    rt = NativeAgentRuntime(
        definition=_defn(), model_provider=model, tool_providers=[_ManyTools(80)]
    )
    await rt.start()
    await _drain(rt, "run the third niche tool")

    first, second = model.seen_messages[0], model.seen_messages[1]
    assert len(_catalog_notes(first)) == 1 and len(_catalog_notes(second)) == 1
    note_at = second.index(_catalog_notes(second)[0])
    assert second[note_at - 1]["role"] == "user"

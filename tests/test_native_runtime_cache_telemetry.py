"""#3434 — the native runtime must carry the prompt-cache counts to its terminal event.

All three terminal ``AgentEvent`` constructions in ``agents/native/runtime.py`` omitted
``cache_read_tokens`` / ``cache_creation_tokens``, and the file contained **zero**
references to either field name. The counts were produced correctly by the adapters and
dropped one layer up, for **every** provider — which is the real reason 0 of 60 ledger
rows ever carried a non-zero cache count. A structural zero is indistinguishable from
"caching is not working", which is how it stayed invisible.

WHAT THESE TESTS SPAN, AND WHY THAT SHAPE
=========================================
Adapter → terminal event, end to end, with no network: a **vendor-shaped usage object**
goes through the **real** ``_read_cache_usage`` of each adapter (the two functions share
a name and the ``(creation, read)`` contract — ``llm/anthropic.py:84``,
``llm/openai.py:52``), and the numbers those readers return are what the scripted model
puts on its ``EVENT_COMPLETE``, exactly as ``anthropic.py:648`` / ``openai.py:464`` do.
So a regression in either reader or in the runtime's propagation reds here.

Both provider families are covered because the defect is provider-independent and a
single-provider test would let the other regress (the issue says so explicitly).

NON-VACUITY
===========
Every propagation case is parametrised over a non-zero row AND a zero row, asserting
EQUALITY with what the adapter reported. A structural zero (the defect) fails the
non-zero row; a hard-coded or fabricated constant fails the zero row. Neither can pass
both, so "the assertion can fail" is established by construction rather than by comment.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from personalclaw.agents.native.runtime import NativeAgentRuntime
from personalclaw.agents.provider import AgentRuntimeDefinition
from personalclaw.llm.anthropic import _read_cache_usage as _anthropic_read_cache
from personalclaw.llm.events import EVENT_COMPLETE, EVENT_TOOL_CALL, AgentEvent
from personalclaw.llm.openai import _read_cache_usage as _openai_read_cache

# The measured numbers from the issue: the provider reported 4224 cache reads and the
# terminal event carried 0.
_READ = 4224
_CREATION = 1024


class _AnthropicUsage:
    """The Anthropic ``usage`` shape: the counts sit directly on the object."""

    def __init__(self, creation: int, read: int) -> None:
        self.cache_creation_input_tokens = creation
        self.cache_read_input_tokens = read


class _OpenAIDetails:
    def __init__(self, creation: int, read: int) -> None:
        self.cache_write_tokens = creation
        self.cached_tokens = read


class _OpenAIUsage:
    """The OpenAI-family shape: nested one level down on ``prompt_tokens_details``."""

    def __init__(self, creation: int, read: int) -> None:
        self.prompt_tokens_details = _OpenAIDetails(creation, read)


#: ``(label, reader, usage_factory)`` — the two provider dialects, each with the REAL
#: adapter function that produces the pair the terminal event carries.
_DIALECTS = [
    pytest.param("anthropic", _anthropic_read_cache, _AnthropicUsage, id="anthropic"),
    pytest.param("openai", _openai_read_cache, _OpenAIUsage, id="openai-family"),
]


class _ScriptedModel:
    """Replays scripted turns, like ``tests/test_stop_means_stop.py``'s."""

    supports_tools = True
    _model = "scripted"

    def __init__(self, turns: list[list[AgentEvent]]) -> None:
        self._turns = turns
        self.calls = 0

    async def complete(self, messages, *, tools=None, model=None, reasoning_effort=""):
        idx = min(self.calls, len(self._turns) - 1)
        self.calls += 1
        for ev in self._turns[idx]:
            yield ev

    async def cancel(self, *, wait_ack_timeout: float = 0.0) -> str:
        return "acked"


def _complete(*, creation: int, read: int, inp: int = 77, out: int = 1) -> AgentEvent:
    """The provider's terminal event, built the way both adapters build theirs."""
    return AgentEvent(
        kind=EVENT_COMPLETE,
        input_tokens=inp,
        output_tokens=out,
        cache_creation_tokens=creation,
        cache_read_tokens=read,
    )


async def _drive(tmp_path: Path, turns: list[list[AgentEvent]]) -> list[AgentEvent]:
    runtime = NativeAgentRuntime(
        definition=AgentRuntimeDefinition(name="T", provider="native", model="scripted"),
        model_provider=_ScriptedModel(turns),
        tool_providers=[],
        cwd=tmp_path,
    )
    await runtime.start()
    runtime.set_approval_policy("auto")
    return [ev async for ev in runtime.stream("go")]


def _terminal(events: list[AgentEvent]) -> AgentEvent:
    finals = [ev for ev in events if ev.kind == EVENT_COMPLETE]
    # Vacuity floor: a drive that produced no terminal event would make every
    # assertion below unreachable, and an empty-list index error is not a diagnosis.
    assert len(finals) == 1, f"expected exactly one terminal event, got {len(finals)}"
    return finals[0]


# ── adapter → terminal event, both dialects, non-zero and zero ──────────────────


@pytest.mark.parametrize(("label", "reader", "usage_cls"), _DIALECTS)
@pytest.mark.parametrize(("creation", "read"), [(_CREATION, _READ), (0, 0)])
def test_the_cache_counts_survive_the_runtime_for_both_provider_dialects(
    tmp_path: Path, label: str, reader, usage_cls, creation: int, read: int
) -> None:
    """A count the adapter reported must arrive on the runtime's terminal event.

    The zero row is the non-vacuity control: it fails if anything fabricates a count,
    and the non-zero row fails on the structural zero this issue filed.
    """
    adapter_creation, adapter_read = reader(usage_cls(creation, read))
    # The readers must agree with the vendor payload, or this test is measuring a stub.
    assert (adapter_creation, adapter_read) == (
        creation,
        read,
    ), f"{label}'s _read_cache_usage did not read its own vendor shape"

    events = asyncio.run(
        _drive(tmp_path, [[_complete(creation=adapter_creation, read=adapter_read)]])
    )
    terminal = _terminal(events)

    assert terminal.cache_read_tokens == read
    assert terminal.cache_creation_tokens == creation
    # The firing control from the issue: these arrive intact and always did, so a red
    # above is the cache fields specifically, not a broken event path.
    assert terminal.input_tokens == 77
    assert terminal.output_tokens == 1


# ── the accumulator: a multi-cycle turn sums them, like input_tokens ────────────


def test_a_multi_cycle_turn_sums_the_cache_counts_exactly_as_it_sums_input_tokens(
    tmp_path: Path,
) -> None:
    """Why the runtime needs an accumulator, and why last-inference-only is wrong.

    Every consumer reconstructs the whole served prompt as
    ``input + cache_creation + cache_read`` (``stats.py:160``, ``pricing.py:169``,
    ``llm/openai.py:444``). If ``input_tokens`` is a turn total while the cache counts
    are one inference's, that sum divides a whole-turn numerator by a single-inference
    denominator — wrong arithmetic, not merely a missing field. So the assertion is
    that the cache counts are aggregated over the SAME cycles ``input_tokens`` is.
    """
    first = [
        AgentEvent(kind=EVENT_TOOL_CALL, tool_call_id="c1", title="nope", tool_input="{}"),
        _complete(creation=10, read=100, inp=7, out=1),
    ]
    second = [_complete(creation=20, read=200, inp=11, out=2)]

    terminal = _terminal(asyncio.run(_drive(tmp_path, [first, second])))

    assert terminal.num_turns == 2, "the drive did not run two cycles; the sums are vacuous"
    assert terminal.input_tokens == 18
    assert terminal.output_tokens == 3
    assert terminal.cache_read_tokens == 300
    assert terminal.cache_creation_tokens == 30


# ── the cancelled exit must not throw the counts away either ───────────────────


def test_a_stop_between_cycles_keeps_the_cache_counts_it_already_spent(
    tmp_path: Path,
) -> None:
    """The cancelled exit is one of the three sites, and it is the easiest to forget.

    ``input_tokens``/``output_tokens``/``cost_usd`` were once omitted here too, so a stop
    between ReAct cycles threw away every token the earlier cycles burned. A stop that
    hides spend is why users distrust the button; a stop that hides the CACHED half of
    that spend misprices it in the opposite direction.
    """
    first = [
        AgentEvent(kind=EVENT_TOOL_CALL, tool_call_id="c1", title="nope", tool_input="{}"),
        _complete(creation=12, read=340, inp=9, out=1),
    ]

    async def _run() -> list[AgentEvent]:
        runtime = NativeAgentRuntime(
            definition=AgentRuntimeDefinition(name="T", provider="native", model="scripted"),
            model_provider=_ScriptedModel([first]),
            tool_providers=[],
            cwd=tmp_path,
        )
        await runtime.start()
        runtime.set_approval_policy("auto")
        seen: list[AgentEvent] = []
        async for ev in runtime.stream("go"):
            seen.append(ev)
            if ev.kind == EVENT_TOOL_CALL:
                await runtime.cancel()
        return seen

    terminal = _terminal(asyncio.run(_run()))

    assert terminal.input_tokens == 9, "the stop discarded the spend; the counts below are vacuous"
    assert terminal.cache_read_tokens == 340
    assert terminal.cache_creation_tokens == 12


# ── the field names exist at all (guards a silent rename) ──────────────────────


def test_the_runtime_names_both_cache_fields() -> None:
    """The filed symptom was literally "zero references to either field name"."""
    from personalclaw.agents.native import runtime as rt

    text = Path(rt.__file__).read_text(encoding="utf-8")
    assert text.count("cache_read_tokens=") == 3, "all three terminal events must carry it"
    assert text.count("cache_creation_tokens=") == 3

"""Before any usage reading, the compaction estimate divides by the window the runtime serves.

A native loop with no usage reading yet (its first inference) estimates the history's size and
compacts at the Settings threshold. For a model on this machine it divided by the conservative
4,096-token local floor, even when the runtime that serves the model had already said which window
it loaded it with: Ollama publishes it (``/api/ps``), and the turn asks it before assembly
(``context_headroom.resolve_window``). A history of ~4,000 tokens then read ~98% of a window
serving 32,768 and was compacted before the model had seen it once.

The served window is trusted where the model runs: a runtime on this machine that serves the model
itself (``llm.registry.served_on_this_machine``). A passing-on endpoint's "served" number can be a
table's architectural maximum, so it keeps the floor.
"""

from __future__ import annotations

import pytest

from personalclaw.agents.native.runtime import NativeAgentRuntime
from personalclaw.agents.provider import AgentRuntimeDefinition
from personalclaw.context_compaction import total_chars
from personalclaw.llm.capabilities import Capability, ProviderCapability
from personalclaw.llm.events import EVENT_COMPACTION_STATUS, EVENT_COMPLETE, AgentEvent
from personalclaw.llm.registry import ProviderEntry, get_default_registry
from personalclaw.model_windows import LOCAL_SERVED_CONTEXT_WINDOW

_SERVED = 32_768


def _type(name: str, *, hosts_model: bool) -> None:
    get_default_registry().register_type(
        ProviderCapability(
            type=name,
            capabilities=frozenset({Capability.CHAT}),
            supports_streaming=True,
            supports_tools=True,
            supports_embeddings=False,
            supports_vision=False,
            max_context_tokens=0,
            hosts_model=hosts_model,
        ),
        lambda **_kw: None,
    )


class _Model:
    """A provider built for a local entry: it reports no usage, and says the window it serves."""

    supports_tools = True

    def __init__(self, entry: str, served: int | None) -> None:
        self._model = "m"
        self.served_ref = f"{entry}:m"
        self._served = served

    async def served_context_window(self) -> int | None:
        return self._served

    async def complete(self, messages, *, tools=None, model=None, reasoning_effort=""):
        yield AgentEvent(kind=EVENT_COMPLETE)


def _local(entry: str, type_: str, *, hosts_model: bool) -> str:
    _type(type_, hosts_model=hosts_model)
    get_default_registry().register_entry(
        ProviderEntry(
            name=entry, type=type_, model="", options={"endpoint": "http://127.0.0.1:11434"}
        )
    )
    return entry


def _runtime(model: _Model) -> NativeAgentRuntime:
    rt = NativeAgentRuntime(
        definition=AgentRuntimeDefinition(name="T", provider="native", model="m"),
        model_provider=model,
        tool_providers=[],
    )
    rt._messages = _convo(6)
    return rt


async def _turn(rt: NativeAgentRuntime) -> list[AgentEvent]:
    return [ev async for ev in rt.stream("latest request")]


@pytest.mark.asyncio
async def test_a_window_the_local_runtime_serves_is_what_the_first_estimate_divides_by():
    rt = _runtime(_Model(_local("runs-here", "model-server", hosts_model=True), _SERVED))
    before = total_chars(rt._messages)

    events = await _turn(rt)

    assert not [e for e in events if e.kind == EVENT_COMPACTION_STATUS], "compacted too early"
    assert total_chars(rt._messages) > before, "the history was compacted"
    assert rt._estimated_context_pct() == pytest.approx(
        (total_chars(rt._messages) / 3.0) / _SERVED * 100.0
    )


@pytest.mark.asyncio
async def test_a_runtime_that_cannot_say_keeps_the_local_floor():
    """The control: nothing served, so the estimate keeps the floor and the same history is
    compacted, as it always was."""
    rt = _runtime(_Model(_local("runs-here-quiet", "model-server-quiet", hosts_model=True), None))

    events = await _turn(rt)

    assert [e for e in events if e.kind == EVENT_COMPACTION_STATUS]


@pytest.mark.asyncio
async def test_a_passing_on_endpoint_keeps_the_local_floor_whatever_it_says_it_serves():
    """An endpoint here that passes requests on answers with a table's number, which for a local
    model is its architectural maximum: taken as served, the backstop could never fire."""
    rt = _runtime(_Model(_local("relay-here", "forwarder", hosts_model=False), 262_144))

    events = await _turn(rt)

    assert [e for e in events if e.kind == EVENT_COMPACTION_STATUS]
    assert LOCAL_SERVED_CONTEXT_WINDOW == 4_096


def _convo(n_tool_rounds: int, tool_size: int = 2000) -> list[dict]:
    """A compactable history: ~98% of the 4,096-token floor at 3 chars/token, ~12% of 32,768."""
    msgs: list[dict] = [{"role": "user", "content": "system + first message"}]
    for i in range(n_tool_rounds):
        msgs.append(
            {
                "role": "assistant",
                "content": f"step {i}",
                "tool_calls": [
                    {
                        "id": f"c{i}",
                        "type": "function",
                        "function": {"name": "bash", "arguments": f'{{"path": "src/f{i}.py"}}'},
                    }
                ],
            }
        )
        msgs.append({"role": "tool", "tool_call_id": f"c{i}", "content": "X" * tool_size})
    msgs.append({"role": "assistant", "content": "done"})
    return msgs

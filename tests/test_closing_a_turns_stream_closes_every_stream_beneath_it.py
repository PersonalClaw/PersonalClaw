"""Closing a turn's stream closes every stream beneath it, at once; and whatever stops reading a
turn's stream closes it.

A turn's events reach whoever reads them through layers, each an async generator passing on the
stream beneath it: the chat's usage rows over a provider's metering over an agent CLI's client
over its session. A layer that was closed left the stream it read open, so that stream, and what
it held (an agent CLI's session, an open request, a local model's turn, a spend hold), waited for
the interpreter to collect it. And the readers that stop at a turn's terminal event, or part way,
closed nothing.

Every check here is made right after the close returns, before the event loop runs anything else:
a close the interpreter schedules for a stream it collected cannot pass for one. The inner streams
are kept by the test as well, so no collection can close them in any case.
"""

from __future__ import annotations

import asyncio
import inspect
import sys
from collections.abc import Callable
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from personalclaw.acp.client import AcpClient
from personalclaw.acp.errors import AcpMethodNotFound
from personalclaw.acp.types import AcpEvent, AcpPromptStats
from personalclaw.agents.native.runtime import NativeAgentRuntime
from personalclaw.agents.provider import AgentProvider, AgentRuntimeDefinition
from personalclaw.apps.native_contract import NATIVE_DIR, load_bundle_module, namespaced_module_name
from personalclaw.dashboard.chat_utils import stream_slash_command
from personalclaw.guardrails.model_call import ModelCallGuard
from personalclaw.llm.acp_agent import AcpAgentProvider
from personalclaw.llm.acp_session_provider import AcpSessionProvider
from personalclaw.llm.base import ModelProvider
from personalclaw.llm.credentials import Credential
from personalclaw.llm.events import EVENT_COMPLETE, EVENT_TEXT_CHUNK
from personalclaw.llm.events import AgentEvent as LLMEvent
from personalclaw.llm.scripted import ScriptedProvider
from personalclaw.providers.provider_bridge import METERED_AXES
from personalclaw.usage_ledger import Attribution, spent_rows

_ASKED = [{"role": "user", "content": "Summarise the incident report"}]
_KEY = Credential(name="t", kind="api_key", secret="placeholder", source="none")


def _said() -> LLMEvent:
    return LLMEvent(kind=EVENT_TEXT_CHUNK, text="Reading the incident report")


def _acp_said() -> AcpEvent:
    return AcpEvent(kind=EVENT_TEXT_CHUNK, text="Reading the incident report")


def _closed(streams: list) -> bool:
    return bool(streams) and all(
        inspect.getasyncgenstate(s) == inspect.AGEN_CLOSED for s in streams
    )


class _Turn:
    """The streams a layer reads, each kept here: its *events*, then a turn still in flight."""

    def __init__(self, *events) -> None:
        self.events = events
        self.streams: list = []

    def __call__(self, *_args, **_kwargs):
        stream = self._events()
        self.streams.append(stream)
        return stream

    async def _events(self):
        for event in self.events:
            yield event
        await asyncio.Event().wait()
        yield self.events[-1]


def _relays(provider) -> list:
    """The relays *provider* reads its requests through (``InFlightRequests.relay``), kept."""
    kept: list = []
    relay = provider._requests.relay

    def keeping(source):
        stream = relay(source)
        kept.append(stream)
        return stream

    provider._requests.relay = keeping
    return kept


class _Model(ModelProvider):
    """A model whose turns are *turn*'s streams."""

    def __init__(self, turn: _Turn) -> None:
        self._turn = turn

    async def start(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None

    def stream(self, message):
        return self._turn(message)

    async def approve_tool(self, request_id) -> None:
        return None

    async def reject_tool(self, request_id) -> None:
        return None

    def context_usage_pct(self):
        return None


class _GuardedModel(_Model):
    """...and whose completions and commands are too, for the guard that wraps all three."""

    def complete(self, messages, **_kwargs):
        return self._turn(messages)

    def stream_command(self, command):
        return self._turn(command)


class _Runtime(AgentProvider):
    """An agent runtime whose turns are *turn*'s streams."""

    def __init__(self, turn: _Turn) -> None:
        self._turn = turn

    @property
    def provider_id(self) -> str:
        return "test:runtime"

    async def start(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None

    def stream(self, message):
        return self._turn(message)

    async def approve_tool(self, request_id) -> None:
        return None

    async def reject_tool(self, request_id) -> None:
        return None


# ── the layers ────────────────────────────────────────────────────────────────
# Each builds a layer over streams this test keeps, and returns (the layer's stream, them).


def _unsaid(_sentence: str) -> None:
    return None


def _usage_rows(_mp):
    turn = _Turn(_said())
    return spent_rows(turn(), lambda _event: None), turn.streams


def _slash(**client):
    def build(_mp):
        turn = _Turn(_said())
        fake = SimpleNamespace(stream=turn, stream_command=turn, **client)
        layer = stream_slash_command(fake, "/compact", prompt="compact this chat", notify=_unsaid)
        return layer, turn.streams

    return build


def _slash_refused_then_sent_plain(_mp):
    turn = _Turn(_said())

    async def unknown(_command):
        raise AcpMethodNotFound("commands/execute")
        yield  # an async generator

    fake = SimpleNamespace(stream=turn, stream_command=unknown, supports_native_commands=True)
    return stream_slash_command(fake, "/compact", prompt="compact", notify=_unsaid), turn.streams


def _model(call: Callable):
    def build(_mp):
        turn = _Turn(_said())
        return call(_Model(turn)), turn.streams

    return build


def _guard(call: Callable):
    def build(_mp):
        turn = _Turn(_said())
        guard = ModelCallGuard(_GuardedModel(turn), use_case="chat", provider_name="t", model="m")
        return call(guard), turn.streams

    return build


def _runtime_command(_mp):
    turn = _Turn(_said())
    return _Runtime(turn).stream_command("/plan the week"), turn.streams


def _native_command(_mp):
    turn = _Turn(_said())
    runtime = NativeAgentRuntime(
        definition=AgentRuntimeDefinition(name="T", provider="native", model="m:1b"),
        model_provider=_Model(_Turn(_said())),
        tool_providers=[],
    )
    runtime.stream = turn  # the turn the runtime runs a command it has no command for as
    return runtime.stream_command("/plan the week"), turn.streams


def _acp_client(command: bool):
    def build(_mp):
        turn = _Turn(_acp_said())
        client = AcpClient()

        async def ready() -> None:
            return None

        client._ready_for_a_turn = ready
        client._can_execute_commands = True
        client._session = SimpleNamespace(
            stream_events=turn,
            stream_command=turn,
            set_steer_source=lambda _pull: False,
            set_question_handler=lambda _handler: None,
            last_prompt_stats=AcpPromptStats(),
            _last_stop_reason="",
        )
        layer = client.stream_command("/compact") if command else client.stream_events("go")
        return layer, turn.streams

    return build


def _acp_agent(command: bool = False, metered: bool = False):
    def build(_mp):
        turn = _Turn(_acp_said())
        provider = AcpAgentProvider(
            command=["/nonexistent/pc-fixture-agent"], runtime_id="acp:demo-cli"
        )
        provider._client = SimpleNamespace(stream_events=turn, stream_command=turn)
        if metered:
            provider.set_spend_axis(sorted(METERED_AXES)[0])
            assert provider.spend_axis, "vacuity: the turn is metered"
        layer = provider.stream_command("/compact") if command else provider.stream("go")
        return layer, turn.streams

    return build


def _acp_session_provider(command: bool):
    def build(_mp):
        turn = _Turn(_acp_said())
        connection = SimpleNamespace(supports_native_commands=True)
        session = SimpleNamespace(
            session_id="S1",
            stream_events=turn,
            stream_command=turn,
            last_prompt_stats=AcpPromptStats(),
        )
        provider = AcpSessionProvider(connection, session, runtime_id="acp:demo-cli")
        layer = provider.stream_command("/compact") if command else provider.stream("go")
        return layer, turn.streams

    return build


def _openai(complete: bool):
    def build(_mp):
        from personalclaw.llm.openai import OpenAIProvider

        provider = OpenAIProvider(model="m", credential=_KEY, base_url="http://127.0.0.1:9/v1")
        provider._stream_chat = provider._complete_chat = _Turn(_said())
        kept = _relays(provider)
        return (provider.complete(_ASKED) if complete else provider.stream("go")), kept

    return build


def _anthropic(complete: bool):
    def build(mp):
        from tests import anthropic_sdk_fake

        anthropic_sdk_fake.install(mp)
        from personalclaw.llm.anthropic import AnthropicProvider

        provider = AnthropicProvider(model="m", credential=_KEY)
        provider._stream_chat = provider._complete_chat = _Turn(_said())
        kept = _relays(provider)
        return (provider.complete(_ASKED) if complete else provider.stream("go")), kept

    return build


def _scripted(complete: bool):
    def build(_mp):
        turn = _Turn(_said())
        provider = ScriptedProvider.__new__(ScriptedProvider)
        provider._emit = turn
        return (provider.complete(_ASKED) if complete else provider.stream("go")), turn.streams

    return build


def _bundled_app(name: str):
    module = load_bundle_module(NATIVE_DIR / name, name, "provider")
    sys.modules.pop(namespaced_module_name(name, "provider"), None)
    return module


def _ollama(complete: bool):
    def build(_mp):
        ollama = _bundled_app("ollama-models")
        provider = ollama.OllamaProvider(model="m:1b", endpoint="http://127.0.0.1:9")
        provider._stream_chat = provider._complete_chat = _Turn(_said())
        kept = _relays(provider)
        return (provider.complete(_ASKED) if complete else provider.stream("go")), kept

    return build


def _bundled_chat(_mp):
    turn = _Turn(_said())
    bundled = _bundled_app("bundled-chat")
    provider = bundled.BundledChatProvider.__new__(bundled.BundledChatProvider)
    provider.complete = turn  # the completion its plain stream is answered by
    return provider.stream("go"), turn.streams


_LAYERS = {
    "usage rows": _usage_rows,
    "a slash command the provider runs itself": _slash(compacts_in_process=True),
    "a slash command sent as a plain message": _slash(supports_native_commands=False),
    "a slash command the agent runs": _slash(supports_native_commands=True),
    "a slash command refused, then sent plain": _slash_refused_then_sent_plain,
    "a model's command, its default": _model(lambda m: m.stream_command("/plan the week")),
    "a model's completion, its default": _model(lambda m: m.complete(_ASKED)),
    "the model guard's stream": _guard(lambda g: g.stream("go")),
    "the model guard's completion": _guard(lambda g: g.complete(_ASKED)),
    "the model guard's command": _guard(lambda g: g.stream_command("/plan the week")),
    "a runtime's command, its default": _runtime_command,
    "the native runtime's command": _native_command,
    "an agent CLI client's prompt": _acp_client(command=False),
    "an agent CLI client's command": _acp_client(command=True),
    "an agent CLI provider's prompt": _acp_agent(),
    "an agent CLI provider's command": _acp_agent(command=True),
    "an agent CLI provider's metered prompt": _acp_agent(metered=True),
    "a pooled agent CLI session's prompt": _acp_session_provider(command=False),
    "a pooled agent CLI session's command": _acp_session_provider(command=True),
    "an OpenAI-compatible stream": _openai(complete=False),
    "an OpenAI-compatible completion": _openai(complete=True),
    "an Anthropic-compatible stream": _anthropic(complete=False),
    "an Anthropic-compatible completion": _anthropic(complete=True),
    "the scripted model's stream": _scripted(complete=False),
    "the scripted model's completion": _scripted(complete=True),
    "the bundled Ollama provider's stream": _ollama(complete=False),
    "the bundled Ollama provider's completion": _ollama(complete=True),
    "the bundled chat model's stream": _bundled_chat,
}


@pytest.mark.parametrize("build", list(_LAYERS.values()), ids=list(_LAYERS))
@pytest.mark.asyncio
async def test_closing_a_layer_closes_the_stream_it_passes_on(build, monkeypatch):
    """Red before the fix, for every layer: the stream it passed on was left open."""
    layer, beneath = build(monkeypatch)
    first = await asyncio.wait_for(anext(layer), timeout=5)
    assert first.kind == EVENT_TEXT_CHUNK and first.text == "Reading the incident report"
    assert beneath and not _closed(beneath), "vacuity: the stream beneath is open while read"

    await layer.aclose()

    assert _closed(beneath), "the stream beneath the layer was left open"
    await asyncio.sleep(0)  # a relay's request, cancelled as it closed, ends its task


# ── the readers that stop before a turn's stream has ended ────────────────────


def _answered() -> _Turn:
    """A turn's stream with an answer and its terminal event, and nothing read after that."""
    return _Turn(_said(), LLMEvent(kind=EVENT_COMPLETE, stop_reason="end_turn"))


@pytest.mark.asyncio
async def test_a_one_shot_reader_closes_the_turn_it_stops_reading_at_its_end():
    """``stream_and_collect`` stops at the terminal event (cron, heartbeat, rooms, one-shot
    calls). Red before the fix: it closed nothing."""
    from personalclaw.llm_helpers import stream_and_collect

    turn = _answered()
    assert await stream_and_collect(_Model(turn), "Summarise it") == "Reading the incident report"
    assert _closed(turn.streams)


@pytest.mark.asyncio
async def test_the_loop_judge_closes_the_turn_it_stops_reading_at_its_end():
    from personalclaw.loop.judge import _stream

    turn = _answered()
    judge = SimpleNamespace(_provider=_Model(turn))
    usage = Attribution(source="loop", session_key="loop:test")
    assert await _stream(judge, "Is the cycle done?", usage) == "Reading the incident report"
    assert _closed(turn.streams)


@pytest.mark.asyncio
async def test_a_subagent_closes_the_turn_it_stops_reading_at_its_end():
    from test_subagent import _mock_ctx_builder, _mock_sessions

    from personalclaw.subagent import SubagentInfo, SubagentManager

    turn = _answered()
    sessions = _mock_sessions()
    client = sessions.get_or_create.return_value[0]
    client.stream = MagicMock(side_effect=turn)
    manager = SubagentManager(sessions=sessions, ctx_builder=_mock_ctx_builder())
    info = SubagentInfo(id="sa0001", task="read the report", parent_session_key="")
    with patch("personalclaw.subagent.Stats"), patch("personalclaw.subagent.sel"):
        await manager._run_inner(info, "subagent:sa0001")
    assert info.done and len(turn.streams) == 1
    assert _closed(turn.streams)


def _chat(tmp_path, turn: _Turn):
    """A chat whose turns run on a model that answers with *turn*'s streams."""
    from test_dashboard_approval import _context_builder, _make_session, _make_state

    state, _client = _make_state(tmp_path, context_builder=_context_builder())
    state.sessions.get_or_create = AsyncMock(return_value=(_Model(turn), True, False))
    return state, _make_session()


@pytest.mark.asyncio
async def test_the_chat_closes_the_turn_it_stops_reading_at_its_end(tmp_path):
    from test_dashboard_approval import _patch_stats

    from personalclaw.dashboard.chat import run_chat

    turn = _answered()
    state, session = _chat(tmp_path, turn)
    with _patch_stats():
        await run_chat(state, session, "Summarise the incident report")
    assert len(turn.streams) == 1 and not session._last_turn_errored
    assert _closed(turn.streams)


@pytest.mark.asyncio
async def test_the_chat_closes_a_turn_whose_reading_fails_part_way(tmp_path, monkeypatch):
    from test_dashboard_approval import _patch_stats

    from personalclaw.dashboard.chat import run_chat

    turn = _Turn(_said())  # still in flight when the reading fails
    state, session = _chat(tmp_path, turn)

    def fails(_session, _chunk):
        raise RuntimeError("the answer could not be shown")

    monkeypatch.setattr(type(session), "stream_chunk", fails)
    with _patch_stats():
        await asyncio.wait_for(run_chat(state, session, "Summarise the report"), timeout=10)
    assert session._last_turn_errored, "vacuity: the reading failed part way"
    assert _closed(turn.streams)

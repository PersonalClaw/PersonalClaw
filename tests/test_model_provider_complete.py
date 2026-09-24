"""Unit tests for the stateless ``ModelProvider.complete()`` implementations.

The native agent loop calls ``complete(messages, tools=..., model=...)`` turn by
turn: it owns conversation history (so ``complete`` is STATELESS and never reads
``self._history``), passes the FULL message list each turn, and passes tool
schemas in OpenAI shape. These tests mock each SDK client (the ``openai`` /
``anthropic`` SDKs are not installed in this dev env) and assert:

* OpenAIProvider:
  - sends the full ``messages`` list (not just the last user message);
  - forwards ``tools`` to the API call when provided;
  - emits an ``EVENT_TOOL_CALL`` with the right id/title/input from a scripted
    streamed tool-call delta;
  - emits a terminal ``EVENT_COMPLETE`` carrying usage;
  - honors the ``model=`` override and leaves ``self._history`` untouched.
* AnthropicProvider:
  - extracts the system message into the top-level ``system=`` param;
  - translates an OpenAI-shaped assistant ``tool_calls`` + ``role:"tool"``
    result into Anthropic ``tool_use`` / ``tool_result`` blocks;
  - maps OpenAI ``tools`` to Anthropic ``input_schema`` shape;
  - emits ``EVENT_TOOL_CALL`` + terminal ``EVENT_COMPLETE`` from a scripted stream.
"""

import sys
import types
from typing import Any

import pytest

from personalclaw.llm.base import (
    EVENT_COMPLETE,
    EVENT_TEXT_CHUNK,
    EVENT_TOOL_CALL,
)
from personalclaw.llm.credentials import Credential

# ── Fakes for the openai SDK (mirror tests/test_provider_openai.py) ──────


class _FakeStream:
    """Async iterable that yields pre-canned chunks."""

    def __init__(self, chunks: list[Any]) -> None:
        self._chunks = chunks

    def __aiter__(self) -> "_FakeStream":
        self._iter = iter(self._chunks)
        return self

    async def __anext__(self) -> Any:
        try:
            return next(self._iter)
        except StopIteration:
            raise StopAsyncIteration


class _FakeChoice:
    def __init__(self, delta: Any, finish_reason: str | None = None) -> None:
        self.delta = delta
        self.finish_reason = finish_reason


class _FakeDelta:
    def __init__(self, content: str | None = None, tool_calls: list[Any] | None = None) -> None:
        self.content = content
        self.tool_calls = tool_calls


class _FakeFunction:
    def __init__(self, name: str | None = None, arguments: str | None = None) -> None:
        self.name = name
        self.arguments = arguments


class _FakeToolCallDelta:
    def __init__(
        self,
        *,
        id: str | None = None,
        index: int = 0,
        function: _FakeFunction | None = None,
    ) -> None:
        self.id = id
        self.index = index
        self.function = function


class _FakeChunk:
    def __init__(
        self,
        choices: list[_FakeChoice] | None = None,
        usage: Any | None = None,
    ) -> None:
        self.choices = choices or []
        self.usage = usage


class _FakeUsage:
    def __init__(self, prompt_tokens: int, completion_tokens: int) -> None:
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens


class _FakeChatCompletions:
    def __init__(self, chunks: list[Any]) -> None:
        self._chunks = chunks
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> _FakeStream:
        self.calls.append(kwargs)
        return _FakeStream(self._chunks)


class _FakeChat:
    def __init__(self, completions: _FakeChatCompletions) -> None:
        self.completions = completions


class _FakeAsyncOpenAI:
    constructed: list[dict[str, Any]] = []

    def __init__(self, *, api_key: str, base_url: str | None = None) -> None:
        type(self).constructed.append({"api_key": api_key, "base_url": base_url})
        self.api_key = api_key
        self.base_url = base_url
        self.chat = _FakeChat(_FakeChatCompletions(chunks=[]))
        self.closed = False

    async def close(self) -> None:
        self.closed = True


@pytest.fixture
def fake_openai(monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    """Install a fake ``openai`` module into ``sys.modules``."""
    fake = types.ModuleType("openai")
    fake.AsyncOpenAI = _FakeAsyncOpenAI  # type: ignore[attr-defined]

    class _BadRequestError(Exception):
        pass

    fake.BadRequestError = _BadRequestError  # type: ignore[attr-defined]
    _FakeAsyncOpenAI.constructed = []
    monkeypatch.setitem(sys.modules, "openai", fake)
    return fake


# ── Fakes for the anthropic SDK (mirror tests/test_provider_anthropic.py) ─


class _FakeStreamIter:
    def __init__(self, events: list[Any]) -> None:
        self._events = events

    def __aiter__(self) -> "_FakeStreamIter":
        self._iter = iter(self._events)
        return self

    async def __anext__(self) -> Any:
        try:
            return next(self._iter)
        except StopIteration:
            raise StopAsyncIteration


class _FakeStreamCM:
    def __init__(self, events: list[Any]) -> None:
        self._events = events

    async def __aenter__(self) -> _FakeStreamIter:
        return _FakeStreamIter(self._events)

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        return None


class _FakeMessages:
    def __init__(self, stream_events: list[Any]) -> None:
        self._events = stream_events
        self.calls: list[dict[str, Any]] = []

    def stream(self, **kwargs: Any) -> _FakeStreamCM:
        self.calls.append(kwargs)
        return _FakeStreamCM(self._events)


class _FakeAsyncAnthropic:
    constructed: list[dict[str, Any]] = []

    def __init__(self, *, api_key: str, base_url: str | None = None) -> None:
        type(self).constructed.append({"api_key": api_key, "base_url": base_url})
        self.api_key = api_key
        self.base_url = base_url
        self.messages = _FakeMessages(stream_events=[])
        self.closed = False

    async def close(self) -> None:
        self.closed = True


@pytest.fixture
def fake_anthropic(monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    """Install a fake ``anthropic`` module into ``sys.modules``."""
    fake = types.ModuleType("anthropic")
    fake.AsyncAnthropic = _FakeAsyncAnthropic  # type: ignore[attr-defined]
    _FakeAsyncAnthropic.constructed = []
    monkeypatch.setitem(sys.modules, "anthropic", fake)
    return fake


def _cred() -> Credential:
    return Credential(name="x", kind="api_key", secret="sk-test", source="env")


# ── supports_tools flags ────────────────────────────────────────────────


def test_supports_tools_flags() -> None:
    """Each core-resident provider declares the supports_tools value the loop branches
    on. (Model-provider apps — ollama/bedrock/vllm — assert their supports_tools in
    their own apps/<name>-models/tests/.)"""
    from personalclaw.llm.anthropic import AnthropicProvider
    from personalclaw.llm.openai import OpenAIProvider

    assert OpenAIProvider.supports_tools is True
    assert AnthropicProvider.supports_tools is True


# ── OpenAI complete() ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_openai_complete_sends_full_messages_and_tools(
    fake_openai: types.ModuleType,
) -> None:
    """complete() forwards the ENTIRE messages list + tools kwarg to the API."""
    from personalclaw.llm.openai import OpenAIProvider

    chunks = [
        _FakeChunk(choices=[_FakeChoice(delta=_FakeDelta(content="Hi"))]),
        _FakeChunk(
            choices=[_FakeChoice(delta=_FakeDelta(content=None), finish_reason="stop")],
            usage=_FakeUsage(prompt_tokens=12, completion_tokens=4),
        ),
    ]
    completions = _FakeChatCompletions(chunks=chunks)
    provider = OpenAIProvider(model="gpt-4o-mini", credential=_cred())
    provider._client.chat = _FakeChat(completions)

    messages = [
        {"role": "system", "content": "be terse"},
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "ok"},
        {"role": "user", "content": "second"},
    ]
    tools = [
        {
            "type": "function",
            "function": {"name": "get_weather", "description": "", "parameters": {}},
        }
    ]

    events = [e async for e in provider.complete(messages, tools=tools)]

    # (a) the full messages list was sent, not just the last user message.
    sent = completions.calls[0]["messages"]
    assert sent == messages
    assert len(sent) == 4
    # (b) tools kwarg forwarded.
    assert completions.calls[0]["tools"] == tools
    # (d) terminal EVENT_COMPLETE carries usage.
    complete_events = [e for e in events if e.kind == EVENT_COMPLETE]
    assert len(complete_events) == 1
    assert complete_events[0].input_tokens == 12
    assert complete_events[0].output_tokens == 4
    # stateless: history untouched.
    assert provider._history == []


@pytest.mark.asyncio
async def test_openai_complete_emits_tool_call_from_streamed_deltas(
    fake_openai: types.ModuleType,
) -> None:
    """(c) A scripted streamed tool-call delta yields EVENT_TOOL_CALL with id/title/input."""
    from personalclaw.llm.openai import OpenAIProvider

    chunks = [
        # id + name on the first fragment; arguments streamed in pieces.
        _FakeChunk(
            choices=[
                _FakeChoice(
                    delta=_FakeDelta(
                        tool_calls=[
                            _FakeToolCallDelta(
                                id="call_abc",
                                index=0,
                                function=_FakeFunction(name="get_weather", arguments='{"city":'),
                            )
                        ]
                    )
                )
            ]
        ),
        _FakeChunk(
            choices=[
                _FakeChoice(
                    delta=_FakeDelta(
                        tool_calls=[
                            _FakeToolCallDelta(index=0, function=_FakeFunction(arguments='"sf"}'))
                        ]
                    )
                )
            ]
        ),
        _FakeChunk(
            choices=[_FakeChoice(delta=_FakeDelta(content=None), finish_reason="tool_calls")],
            usage=_FakeUsage(prompt_tokens=20, completion_tokens=6),
        ),
    ]
    provider = OpenAIProvider(model="gpt-4o-mini", credential=_cred())
    provider._client.chat = _FakeChat(_FakeChatCompletions(chunks=chunks))

    events = [e async for e in provider.complete([{"role": "user", "content": "weather?"}])]

    tool_events = [e for e in events if e.kind == EVENT_TOOL_CALL]
    assert len(tool_events) == 1
    assert tool_events[0].tool_call_id == "call_abc"
    assert tool_events[0].title == "get_weather"
    assert tool_events[0].tool_input == '{"city":"sf"}'

    complete_events = [e for e in events if e.kind == EVENT_COMPLETE]
    assert len(complete_events) == 1
    assert complete_events[0].input_tokens == 20
    assert complete_events[0].output_tokens == 6


@pytest.mark.asyncio
async def test_openai_complete_model_override_and_no_tools_kwarg(
    fake_openai: types.ModuleType,
) -> None:
    """model= overrides the configured model; absent tools → no tools kwarg sent."""
    from personalclaw.llm.openai import OpenAIProvider

    chunks = [
        _FakeChunk(choices=[_FakeChoice(delta=_FakeDelta(content="x"))]),
        _FakeChunk(
            choices=[_FakeChoice(delta=_FakeDelta(content=None), finish_reason="stop")],
            usage=_FakeUsage(prompt_tokens=1, completion_tokens=1),
        ),
    ]
    completions = _FakeChatCompletions(chunks=chunks)
    provider = OpenAIProvider(model="gpt-4o-mini", credential=_cred())
    provider._client.chat = _FakeChat(completions)

    _ = [e async for e in provider.complete([{"role": "user", "content": "hi"}], model="o3-mini")]

    assert completions.calls[0]["model"] == "o3-mini"
    assert "tools" not in completions.calls[0]


@pytest.mark.asyncio
async def test_openai_complete_retries_without_stream_options_on_bad_request(
    fake_openai: types.ModuleType,
) -> None:
    """A 400 mentioning stream_options triggers one retry without it (kept from stream())."""
    from personalclaw.llm.openai import OpenAIProvider

    bad_request = fake_openai.BadRequestError  # type: ignore[attr-defined]

    class _PickyCompletions(_FakeChatCompletions):
        async def create(self, **kwargs: Any) -> _FakeStream:
            self.calls.append(kwargs)
            if "stream_options" in kwargs:
                raise bad_request("unsupported parameter: stream_options")
            return _FakeStream(self._chunks)

    chunks = [
        _FakeChunk(choices=[_FakeChoice(delta=_FakeDelta(content="ok"), finish_reason="stop")]),
    ]
    completions = _PickyCompletions(chunks=chunks)
    provider = OpenAIProvider(model="gpt-4o-mini", credential=_cred())
    provider._client.chat = _FakeChat(completions)

    events = [e async for e in provider.complete([{"role": "user", "content": "hi"}])]

    # Two attempts: first with stream_options (rejected), retry without it.
    assert len(completions.calls) == 2
    assert "stream_options" in completions.calls[0]
    assert "stream_options" not in completions.calls[1]
    assert any(e.kind == EVENT_TEXT_CHUNK for e in events)
    assert any(e.kind == EVENT_COMPLETE for e in events)


# ── Anthropic complete() ────────────────────────────────────────────────


def _ms_event(input_tokens: int = 0, output_tokens: int = 0) -> types.SimpleNamespace:
    usage = types.SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens)
    message = types.SimpleNamespace(usage=usage)
    return types.SimpleNamespace(type="message_start", message=message)


def _content_block_start_tool(index: int, tool_id: str, name: str) -> types.SimpleNamespace:
    block = types.SimpleNamespace(type="tool_use", id=tool_id, name=name)
    return types.SimpleNamespace(type="content_block_start", index=index, content_block=block)


def _text_delta(index: int, text: str) -> types.SimpleNamespace:
    delta = types.SimpleNamespace(type="text_delta", text=text)
    return types.SimpleNamespace(type="content_block_delta", index=index, delta=delta)


def _input_json_delta(index: int, partial: str) -> types.SimpleNamespace:
    delta = types.SimpleNamespace(type="input_json_delta", partial_json=partial)
    return types.SimpleNamespace(type="content_block_delta", index=index, delta=delta)


def _content_block_stop(index: int) -> types.SimpleNamespace:
    return types.SimpleNamespace(type="content_block_stop", index=index)


def _message_delta(output_tokens: int) -> types.SimpleNamespace:
    usage = types.SimpleNamespace(output_tokens=output_tokens)
    delta = types.SimpleNamespace(stop_reason="tool_use")
    return types.SimpleNamespace(type="message_delta", delta=delta, usage=usage)


@pytest.mark.asyncio
async def test_anthropic_complete_translates_messages_and_tools(
    fake_anthropic: types.ModuleType,
) -> None:
    """System → system= param; OpenAI tool_calls/tool → Anthropic blocks; tools mapped."""
    from personalclaw.llm.anthropic import AnthropicProvider

    events = [
        _ms_event(input_tokens=15),
        _content_block_start_tool(0, tool_id="toolu_1", name="get_weather"),
        _input_json_delta(0, '{"city":'),
        _input_json_delta(0, '"sf"}'),
        _content_block_stop(0),
        _message_delta(output_tokens=7),
    ]
    msgs = _FakeMessages(stream_events=events)
    provider = AnthropicProvider(model="claude-x", credential=_cred())
    provider._client.messages = msgs

    # OpenAI-shaped conversation incl. a prior assistant tool call + tool result.
    messages = [
        {"role": "system", "content": "be terse"},
        {"role": "user", "content": "weather in sf?"},
        {
            "role": "assistant",
            "content": "let me check",
            "tool_calls": [
                {
                    "id": "toolu_prev",
                    "type": "function",
                    "function": {"name": "get_weather", "arguments": '{"city":"sf"}'},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "toolu_prev", "content": "sunny"},
    ]
    tools = [
        {
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "Look up weather",
                "parameters": {"type": "object", "properties": {"city": {"type": "string"}}},
            },
        }
    ]

    out = [e async for e in provider.complete(messages, tools=tools)]

    call = msgs.calls[0]
    # System extracted to top-level param (not left in messages).
    assert call["system"] == "be terse"
    sent = call["messages"]
    assert all(m["role"] != "system" for m in sent)
    # user / assistant-tool_use / tool_result(user) — 3 Anthropic messages.
    assert [m["role"] for m in sent] == ["user", "assistant", "user"]
    # assistant turn carries a text block + a tool_use block with parsed input.
    asst_blocks = sent[1]["content"]
    assert {"type": "text", "text": "let me check"} in asst_blocks
    tool_use = [b for b in asst_blocks if b.get("type") == "tool_use"][0]
    assert tool_use["id"] == "toolu_prev"
    assert tool_use["name"] == "get_weather"
    assert tool_use["input"] == {"city": "sf"}
    # tool result became a tool_result block in a user turn.
    tr_blocks = sent[2]["content"]
    assert tr_blocks == [{"type": "tool_result", "tool_use_id": "toolu_prev", "content": "sunny"}]
    # tools mapped to Anthropic input_schema shape.
    assert call["tools"] == [
        {
            "name": "get_weather",
            "description": "Look up weather",
            "input_schema": {
                "type": "object",
                "properties": {"city": {"type": "string"}},
            },
        }
    ]

    # Tool call emitted + terminal EVENT_COMPLETE with usage.
    tool_events = [e for e in out if e.kind == EVENT_TOOL_CALL]
    assert len(tool_events) == 1
    assert tool_events[0].tool_call_id == "toolu_1"
    assert tool_events[0].title == "get_weather"
    assert tool_events[0].tool_input == '{"city":"sf"}'

    complete_events = [e for e in out if e.kind == EVENT_COMPLETE]
    assert len(complete_events) == 1
    assert complete_events[0].input_tokens == 15
    assert complete_events[0].output_tokens == 7
    # stateless: history untouched.
    assert provider._history == []


@pytest.mark.asyncio
async def test_anthropic_complete_model_override(
    fake_anthropic: types.ModuleType,
) -> None:
    """model= overrides the configured model id for the call."""
    from personalclaw.llm.anthropic import AnthropicProvider

    events = [_ms_event(input_tokens=3), _message_delta(output_tokens=1)]
    msgs = _FakeMessages(stream_events=events)
    provider = AnthropicProvider(model="claude-default", credential=_cred())
    provider._client.messages = msgs

    _ = [
        e
        async for e in provider.complete(
            [{"role": "user", "content": "hi"}], model="claude-override"
        )
    ]

    assert msgs.calls[0]["model"] == "claude-override"
    # No system message present → no system kwarg sent.
    assert "system" not in msgs.calls[0]
    # No tools → no tools kwarg sent.
    assert "tools" not in msgs.calls[0]

"""A reasoning effort reaches each Claude model in the shape it takes, and its thinking comes back.

Measured defect: ``AnthropicProvider.complete`` sent every effort as
``thinking: {type: enabled, budget_tokens: N}``. Opus 4.7 and later, Sonnet 5 and later, Haiku 5
and later and the Fable and Mythos lines refuse ``budget_tokens`` with a 400, so every turn with a
reasoning effort failed on them. They take ``thinking: {type: adaptive}`` and the depth as
``output_config.effort``; Opus 4.6 and Sonnet 4.6 take that too, without ``xhigh``.

Second defect: the thinking blocks a model wrote were dropped, so the request after a tool call,
on a model that still takes a budget, carried an assistant turn with no thinking block and was
refused. The blocks now come back on the terminal event as a reasoning record stamped with the
model, the native loop keeps it on the assistant message, and only the model that wrote them is
sent them again. A model that binds them to an exact prefix is sent none, and a provider that does
not read the record never sees it.

Every call here goes to ``tests/anthropic_sdk_fake.py``; nothing leaves the machine.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from personalclaw.agents.native.runtime import NativeAgentRuntime
from personalclaw.agents.provider import AgentRuntimeDefinition
from personalclaw.context_compaction import total_chars
from personalclaw.context_gauge import prompt_text_chars
from personalclaw.guardrails.model_call import ModelCallGuard, request_chars
from personalclaw.llm.anthropic import AnthropicProvider
from personalclaw.llm.base import ModelProvider
from personalclaw.llm.catalog import returns_thinking, thinking_shape
from personalclaw.llm.credentials import Credential
from personalclaw.llm.events import EVENT_COMPLETE, EVENT_TEXT_CHUNK, AgentEvent
from personalclaw.llm.reasoning_record import REASONING_KEY, without_reasoning
from tests import anthropic_sdk_fake


@pytest.fixture
def sdk(monkeypatch):
    return anthropic_sdk_fake.install(monkeypatch)


def _provider(model: str, *, max_tokens: int = 4096, **options: Any) -> AnthropicProvider:
    return AnthropicProvider(
        model=model,
        credential=Credential(name="x", kind="api_key", secret="fake-key-test", source="env"),
        max_tokens=max_tokens,
        extra_options=options,
    )


async def _events(stream) -> list[AgentEvent]:
    return [ev async for ev in stream]


def _sent(call: dict[str, Any]) -> dict[str, Any]:
    fields = {k: v for k, v in call.items() if k != "extra_body"}
    fields.update(call.get("extra_body") or {})
    return fields


_HI = [{"role": "user", "content": "hi"}]


# ── the request shape, family by family ──────────────────────────────────────


@pytest.mark.anyio
@pytest.mark.parametrize(
    "model",
    [
        "claude-opus-4-8",
        "claude-opus-4-7",
        "claude-opus-5-5",
        "claude-sonnet-5",
        "claude-sonnet-5-5",
        "claude-haiku-5-5",
        "claude-fable-5",
        "claude-fable-5-1",
        "claude-mythos-5",
    ],
)
async def test_a_current_model_is_sent_adaptive_thinking_and_no_budget(sdk, model):
    """The regression: before, each of these was sent ``budget_tokens`` and answered 400."""
    await _events(_provider(model).complete(_HI, reasoning_effort="high"))

    sent = _sent(sdk.calls[0])
    assert sent["thinking"] == {"type": "adaptive"}
    assert sent["output_config"] == {"effort": "high"}
    assert "budget_tokens" not in json.dumps(sent)


@pytest.mark.anyio
@pytest.mark.parametrize("model", ["claude-opus-4-6", "claude-sonnet-4-6"])
async def test_the_4_6_models_think_adaptively_and_take_xhigh_as_high(sdk, model):
    await _events(_provider(model).complete(_HI, reasoning_effort="xhigh"))

    sent = _sent(sdk.calls[0])
    assert sent["thinking"] == {"type": "adaptive"}
    assert sent["output_config"] == {"effort": "high"}


@pytest.mark.anyio
async def test_xhigh_reaches_a_model_that_takes_it(sdk):
    await _events(_provider("claude-opus-4-8").complete(_HI, reasoning_effort="xhigh"))

    assert _sent(sdk.calls[0])["output_config"] == {"effort": "xhigh"}


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("effort", "budget"),
    [("low", 4_096), ("high", 24_576), ("xhigh", 40_960), ("max", 63_999)],
)
async def test_an_older_model_keeps_its_budget(sdk, effort, budget):
    await _events(
        _provider("claude-haiku-4-5", max_tokens=80_000).complete(_HI, reasoning_effort=effort)
    )

    sent = _sent(sdk.calls[0])
    assert sent["thinking"] == {"type": "enabled", "budget_tokens": budget}
    assert "output_config" not in sent


@pytest.mark.anyio
async def test_a_budget_stays_below_the_output_cap(sdk):
    await _events(_provider("claude-sonnet-4-5").complete(_HI, reasoning_effort="max"))

    assert _sent(sdk.calls[0])["thinking"] == {"type": "enabled", "budget_tokens": 3_072}


@pytest.mark.anyio
@pytest.mark.parametrize("model", ["claude-opus-4-8", "claude-haiku-4-5", "claude-fable-5-1"])
async def test_no_effort_sends_no_thinking_fields(sdk, model):
    await _events(_provider(model).complete(_HI))

    sent = _sent(sdk.calls[0])
    assert "thinking" not in sent and "output_config" not in sent


@pytest.mark.anyio
async def test_an_adaptive_thinking_turn_drops_the_temperature(sdk):
    await _events(
        _provider("claude-sonnet-4-6", temperature=0.7).complete(_HI, reasoning_effort="low")
    )

    sent = _sent(sdk.calls[0])
    assert sent["thinking"] == {"type": "adaptive"}
    assert "temperature" not in sent


def test_every_spelling_of_a_model_reads_as_its_family():
    for model in (
        "claude-opus-4.8",
        "global.anthropic.claude-opus-4-8",
        "us.anthropic.claude-opus-4-7-v1:0",
        "anthropic.claude-sonnet-5",
        "CLAUDE-HAIKU-5.5",
    ):
        assert thinking_shape(model).adaptive, model
    # A date suffix is no minor version: Sonnet 4 of May 2025 still takes a budget.
    for model in ("claude-sonnet-4-20250514", "claude-3-7-sonnet-20250219", "claude-opus-4-5"):
        assert not thinking_shape(model).adaptive, model
    assert not thinking_shape("glm-5.2").adaptive


def test_only_models_that_do_not_bind_thinking_to_a_prefix_get_it_back():
    for model in ("claude-haiku-4-5", "claude-opus-4-8", "claude-sonnet-5", "claude-fable-5"):
        assert returns_thinking(model), model
    for model in (
        "claude-opus-5-5",
        "claude-sonnet-5-5",
        "claude-haiku-5-5",
        "claude-fable-5-1",
        "claude-mythos-5",
        "glm-5.2",
        "",
    ):
        assert not returns_thinking(model), model


# ── the thinking blocks ──────────────────────────────────────────────────────


def _thinking_answer(*, tool: bool) -> list[Any]:
    """An answer that thinks (with an empty thinking text, as the newer models write it), then
    either calls a tool or answers in text."""
    ns = SimpleNamespace
    events = [
        ns(type="message_start", message=ns(usage=ns(input_tokens=11, output_tokens=0))),
        ns(type="content_block_start", index=0, content_block=ns(type="thinking", thinking="")),
        ns(type="content_block_delta", index=0, delta=ns(type="signature_delta", signature="sig")),
        ns(type="content_block_delta", index=0, delta=ns(type="signature_delta", signature="-1")),
        ns(type="content_block_stop", index=0),
        ns(
            type="content_block_start",
            index=1,
            content_block=ns(type="redacted_thinking", data="opaque"),
        ),
        ns(type="content_block_stop", index=1),
    ]
    if tool:
        events += [
            ns(
                type="content_block_start",
                index=2,
                content_block=ns(type="tool_use", id="toolu_1", name="lookup"),
            ),
            ns(
                type="content_block_delta",
                index=2,
                delta=ns(type="input_json_delta", partial_json='{"q": "x"}'),
            ),
            ns(type="content_block_stop", index=2),
        ]
        reason = "tool_use"
    else:
        events += [
            ns(type="content_block_start", index=2, content_block=ns(type="text")),
            ns(type="content_block_delta", index=2, delta=ns(type="text_delta", text="Done.")),
            ns(type="content_block_stop", index=2),
        ]
        reason = "end_turn"
    events.append(ns(type="message_delta", delta=ns(stop_reason=reason), usage=ns(output_tokens=5)))
    return events


_BLOCKS = [
    {"type": "thinking", "thinking": "", "signature": "sig-1"},
    {"type": "redacted_thinking", "data": "opaque"},
]


@pytest.mark.anyio
async def test_the_blocks_a_model_wrote_come_back_on_the_terminal_event(sdk, monkeypatch):
    monkeypatch.setattr(anthropic_sdk_fake, "_events", lambda _text: _thinking_answer(tool=True))

    events = await _events(_provider("claude-haiku-4-5").complete(_HI, reasoning_effort="low"))

    (done,) = [e for e in events if e.kind == EVENT_COMPLETE]
    assert done.reasoning == {"model": "claude-haiku-4-5", "blocks": _BLOCKS}


@pytest.mark.anyio
async def test_an_answer_without_thinking_carries_no_record(sdk):
    events = await _events(_provider("claude-haiku-4-5").complete(_HI))

    assert [e.reasoning for e in events if e.kind == EVENT_COMPLETE] == [None]


def _tool_loop(model: str) -> list[dict]:
    """The history after one tool call the model made, as the native loop holds it."""
    return [
        {"role": "user", "content": "look it up"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "toolu_1",
                    "type": "function",
                    "function": {"name": "lookup", "arguments": "{}"},
                }
            ],
            REASONING_KEY: {"model": model, "blocks": _BLOCKS},
        },
        {"role": "tool", "tool_call_id": "toolu_1", "content": "found"},
    ]


@pytest.mark.anyio
async def test_a_budget_models_tool_loop_starts_its_turn_with_its_thinking(sdk):
    """Before: the assistant turn carried only its tool_use, which a thinking request refuses."""
    model = "claude-haiku-4-5"
    await _events(_provider(model).complete(_tool_loop(model), reasoning_effort="low"))

    sent = _sent(sdk.calls[0])
    assistant = sent["messages"][1]
    assert assistant["content"][:2] == _BLOCKS
    assert assistant["content"][2]["type"] == "tool_use"
    assert sent["thinking"]["type"] == "enabled"
    assert REASONING_KEY not in json.dumps(sent)


@pytest.mark.anyio
async def test_a_plain_answer_goes_back_with_its_thinking_first(sdk):
    model = "claude-opus-4-8"
    history = [
        {"role": "user", "content": "hi"},
        {
            "role": "assistant",
            "content": "Hello.",
            REASONING_KEY: {"model": model, "blocks": _BLOCKS},
        },
        {"role": "user", "content": "again"},
    ]
    await _events(_provider(model).complete(history, reasoning_effort="high"))

    assert sdk.calls[0]["messages"][1]["content"] == [*_BLOCKS, {"type": "text", "text": "Hello."}]


@pytest.mark.anyio
async def test_another_models_blocks_are_left_off(sdk):
    """A fallback answering after the home model: what the home model wrote is not its to send.
    On a budget model that leaves the tool call without thinking, so that request asks for none."""
    await _events(
        _provider("claude-haiku-4-5").complete(
            _tool_loop("claude-opus-4-8"), reasoning_effort="low"
        )
    )

    sent = _sent(sdk.calls[0])
    assert [b["type"] for b in sent["messages"][1]["content"]] == ["tool_use"]
    assert "thinking" not in sent


@pytest.mark.anyio
async def test_a_per_call_model_is_the_one_whose_blocks_go_back(sdk):
    await _events(
        _provider("claude-opus-4-8").complete(
            _tool_loop("claude-haiku-4-5"), model="claude-haiku-4-5", reasoning_effort="low"
        )
    )

    assert sdk.calls[0]["messages"][1]["content"][:2] == _BLOCKS


@pytest.mark.anyio
@pytest.mark.parametrize(
    "model", ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5", "claude-fable-5-1"]
)
async def test_a_model_that_binds_thinking_to_its_prefix_is_sent_none(sdk, model):
    await _events(_provider(model).complete(_tool_loop(model), reasoning_effort="high"))

    sent = _sent(sdk.calls[0])
    assert [b["type"] for b in sent["messages"][1]["content"]] == ["tool_use"]
    assert sent["thinking"] == {"type": "adaptive"}
    assert REASONING_KEY not in json.dumps(sent)


# ── the record stays out of everything that is not its own provider ──────────


class _AppProvider(ModelProvider):
    """A provider an app ships (Bedrock, an OpenAI-compatible endpoint): it declares nothing about
    the reasoning record, and keeps every message list it is handed."""

    supports_tools = True

    def __init__(self, reasoning: dict | None = None, *, reads: bool = False) -> None:
        self.requests: list[list[dict]] = []
        self._reasoning = reasoning
        if reads:
            self.reads_reasoning = True

    async def start(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None

    async def stream(self, message: str):
        yield AgentEvent(kind=EVENT_COMPLETE)

    async def complete(self, messages, *, tools=None, model=None, reasoning_effort=""):
        self.requests.append([dict(m) for m in messages])
        yield AgentEvent(kind=EVENT_TEXT_CHUNK, text="Answered.")
        yield AgentEvent(
            kind=EVENT_COMPLETE, input_tokens=1, output_tokens=1, reasoning=self._reasoning
        )

    async def approve_tool(self, request_id) -> None:
        return None

    async def reject_tool(self, request_id) -> None:
        return None

    def context_usage_pct(self) -> float | None:
        return None


async def _two_turns(model: ModelProvider) -> NativeAgentRuntime:
    rt = NativeAgentRuntime(
        definition=AgentRuntimeDefinition(name="a", provider="native", model="m"),
        model_provider=model,
        tool_providers=[],
    )
    await rt.start()
    for message in ("first", "second"):
        async for _ in rt.stream(message):
            pass
    return rt


_RECORD = {"model": "m", "blocks": _BLOCKS}


@pytest.mark.asyncio
async def test_the_native_loop_keeps_the_record_and_hands_it_to_its_reader():
    reader = _AppProvider(_RECORD, reads=True)

    await _two_turns(reader)

    (assistant,) = [m for m in reader.requests[1] if m.get("role") == "assistant"]
    assert assistant[REASONING_KEY] == _RECORD
    assert assistant["content"] == "Answered."


@pytest.mark.asyncio
@pytest.mark.parametrize("guarded", [False, True])
async def test_an_app_providers_complete_never_receives_the_record(guarded):
    """The record is in the history (a model of another kind wrote it before a switch), and a
    provider that does not declare it reads one is handed every message without it."""
    app = _AppProvider()
    model: ModelProvider = (
        ModelCallGuard(app, use_case="loops", provider_name="app-test", model="m")
        if guarded
        else app
    )
    rt = NativeAgentRuntime(
        definition=AgentRuntimeDefinition(name="a", provider="native", model="m"),
        model_provider=model,
        tool_providers=[],
    )
    await rt.start()
    rt._messages.append({"role": "user", "content": "earlier"})
    rt._messages.append({"role": "assistant", "content": "Earlier.", REASONING_KEY: _RECORD})

    async for _ in rt.stream("now"):
        pass

    assert app.requests, "the provider was called"
    for request in app.requests:
        assert all(REASONING_KEY not in m for m in request), request
    # The history keeps it, for the model that wrote it when it answers again.
    assert any(REASONING_KEY in m for m in rt._messages)


def test_the_spend_guard_passes_the_declaration_through():
    assert ModelCallGuard(
        _AppProvider(reads=True), use_case="loops", provider_name="p", model="m"
    ).reads_reasoning
    assert not ModelCallGuard(
        _AppProvider(), use_case="loops", provider_name="p", model="m"
    ).reads_reasoning


def test_without_reasoning_copies_and_never_mutates():
    message = {"role": "assistant", "content": "x", REASONING_KEY: _RECORD}
    plain = [{"role": "user", "content": "y"}]

    assert without_reasoning(plain) is plain
    (out,) = without_reasoning([message])
    assert REASONING_KEY not in out and REASONING_KEY in message


def test_no_text_reader_counts_the_record():
    """Compaction's size, the context gauge's estimate and the spend guard's weighing read text;
    the record is model data and adds nothing to any of them."""
    plain = [{"role": "assistant", "content": "Answered."}]
    carrying = [
        {
            **plain[0],
            REASONING_KEY: {
                "model": "m",
                "blocks": [{"type": "thinking", "thinking": "x" * 5000, "signature": "s" * 5000}],
            },
        }
    ]

    assert total_chars(carrying) == total_chars(plain)
    assert prompt_text_chars(carrying) == prompt_text_chars(plain)
    assert request_chars(carrying) == request_chars(plain)

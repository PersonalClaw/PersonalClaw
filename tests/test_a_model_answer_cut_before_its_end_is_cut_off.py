"""A model's answer whose stream ends before its provider says it is finished is cut off.

Every protocol a model adapter reads ends an answer with an event of its own: an OpenAI-compatible
choice's ``finish_reason``, the Anthropic message's ``stop_reason``, Ollama's ``done`` line. A
stream can also just end before it, a connection that closed cleanly or a body a proxy cut short,
and the client libraries raise nothing then. Measured before: an OpenAI-compatible endpoint that
closed its stream after a sentence and half a tool call made the adapter yield the half call
(``{"path": "notes/q3``, no stop reason) and an ``EVENT_COMPLETE`` with an empty stop reason, a
complete answer to everything that read it. Ollama's adapter and the Anthropic-compatible one did
the same.

Each adapter is driven as it runs: the OpenAI-compatible one through the real ``openai`` SDK and
Ollama's through ``httpx``, both against a loopback server that closes the body where the test
says; the Anthropic-compatible one against a stand-in for its SDK (the SDK is not a dev dependency).
A cut answer raises ``AnswerCutOff`` after the text that arrived, and emits no tool call and no
terminal event; a whole answer reads exactly as it did. The native loop then ends the turn and
runs nothing, and the spend guard records the call as failed rather than charging it as complete.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import types
from types import SimpleNamespace
from typing import Any

import pytest

from personalclaw.apps.native_contract import NATIVE_DIR, load_bundle_module, namespaced_module_name
from personalclaw.guardrails.failure import AnswerCutOff
from personalclaw.llm.credentials import Credential
from personalclaw.llm.events import (
    EVENT_COMPLETE,
    EVENT_TEXT_CHUNK,
    EVENT_TOOL_CALL,
    AgentEvent,
)
from personalclaw.llm.stream_end import until_terminal
from personalclaw.tool_providers.base import RiskLevel, ToolDefinition, ToolProvider, ToolResult
from tests.loopback_model_endpoint import MODEL, ModelEndpoint, chunk, text, tool_delta, usage

pytest.importorskip("openai", reason="the `openai` extra is not installed: the SDK drive is unrun")

FIRST_HALF = "Here is the first half"
HALF_ARGUMENTS = '{"path": "notes/q3'
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "write_note",
            "description": "Write a note.",
            "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
        },
    }
]
MESSAGES = [{"role": "user", "content": "Write the Q3 note."}]


def _credential() -> Credential:
    return Credential(name="key", kind="api_key", secret="fake-key-test", source="env")


TEXT = text(FIRST_HALF)
USAGE = usage()


async def _read(events) -> tuple[list[AgentEvent], BaseException | None]:
    """Every event *events* yields, and what it raised at the end (``None`` when nothing)."""
    seen: list[AgentEvent] = []
    try:
        async for event in events:
            seen.append(event)
    except Exception as exc:  # noqa: BLE001 — the test asserts on what it was
        return seen, exc
    return seen, None


def _kinds(events: list[AgentEvent]) -> list[str]:
    return [e.kind for e in events]


# ── The OpenAI-compatible adapter, through the real SDK ────────────────────────────────────────


def _openai(endpoint: ModelEndpoint):
    from personalclaw.llm.openai import OpenAIProvider

    return OpenAIProvider(model=MODEL, credential=_credential(), base_url=f"{endpoint.url}/v1")


def _openai_call(provider, path: str):
    if path == "complete":
        return provider.complete(MESSAGES, tools=TOOLS)
    return provider.stream("Write the Q3 note.")


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["complete", "stream"])
async def test_an_openai_compatible_answer_cut_before_its_finish_reason_is_cut_off(path):
    async with ModelEndpoint([TEXT, tool_delta(HALF_ARGUMENTS)]) as endpoint:
        events, raised = await _read(_openai_call(_openai(endpoint), path))

    # What arrived is passed on as what it is; the half call and the terminal event are not.
    assert _kinds(events) == [EVENT_TEXT_CHUNK], _kinds(events)
    assert events[0].text == FIRST_HALF
    assert isinstance(raised, AnswerCutOff), raised
    assert (raised.adapter, raised.missing, raised.model) == (
        "OpenAI-compatible",
        "a finish_reason",
        MODEL,
    )


@pytest.mark.asyncio
async def test_an_end_of_stream_marker_with_no_finish_reason_is_a_cut_too():
    # `[DONE]` with no choice ever saying how the answer ended: the stop reason is missing.
    async with ModelEndpoint([TEXT, "[DONE]"]) as endpoint:
        events, raised = await _read(_openai(endpoint).complete(MESSAGES))

    assert _kinds(events) == [EVENT_TEXT_CHUNK]
    assert isinstance(raised, AnswerCutOff), raised


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["complete", "stream"])
async def test_a_whole_openai_compatible_answer_reads_as_it_did(path):
    whole = [
        TEXT,
        tool_delta('{"path": "notes/q3.md"}'),
        chunk({}, "tool_calls"),
        USAGE,
        "[DONE]",
    ]
    async with ModelEndpoint(whole) as endpoint:
        events, raised = await _read(_openai_call(_openai(endpoint), path))

    assert raised is None, raised
    assert _kinds(events) == [EVENT_TEXT_CHUNK, EVENT_TOOL_CALL, EVENT_COMPLETE]
    call, done = events[1], events[2]
    assert (call.title, call.tool_input, call.stop_reason) == (
        "write_note",
        '{"path": "notes/q3.md"}',
        "tool_calls",
    )
    assert (done.stop_reason, done.input_tokens, done.output_tokens) == ("tool_calls", 12, 3)


@pytest.mark.asyncio
async def test_a_call_cut_at_the_output_cap_still_reaches_the_runtime_as_a_length_stop():
    # The answer ended (`length`), so its call is passed on, carrying the reason: the runtime then
    # tells the model its call was too long rather than that it left out an argument.
    async with ModelEndpoint([tool_delta(HALF_ARGUMENTS), chunk({}, "length"), "[DONE]"]) as ep:
        events, raised = await _read(_openai(ep).complete(MESSAGES, tools=TOOLS))

    assert raised is None, raised
    assert _kinds(events) == [EVENT_TOOL_CALL, EVENT_COMPLETE]
    assert (events[0].tool_input, events[0].stop_reason) == (HALF_ARGUMENTS, "length")
    assert events[1].stop_reason == "length"


# ── Ollama's adapter (the bundled app), through httpx ──────────────────────────────────────────


@pytest.fixture()
def ollama():
    name = namespaced_module_name("ollama-models", "provider")
    try:
        yield load_bundle_module(NATIVE_DIR / "ollama-models", "ollama-models", "provider")
    finally:
        sys.modules.pop(name, None)


OLLAMA_TEXT = {
    "model": MODEL,
    "message": {"role": "assistant", "content": FIRST_HALF},
    "done": False,
}
OLLAMA_CALL = {
    "model": MODEL,
    "message": {
        "role": "assistant",
        "content": "",
        "tool_calls": [{"function": {"name": "write_note", "arguments": {"path": "notes/q3.md"}}}],
    },
    "done": False,
}
OLLAMA_DONE = {
    "model": MODEL,
    "message": {"role": "assistant", "content": ""},
    "done": True,
    "done_reason": "stop",
    "prompt_eval_count": 12,
    "eval_count": 3,
}


def _ollama_call(module, endpoint: ModelEndpoint, path: str):
    provider = module.OllamaProvider(model=MODEL, endpoint=endpoint.url, timeout=30.0)
    if path == "complete":
        return provider.complete(MESSAGES, tools=TOOLS)
    return provider.stream("Write the Q3 note.")


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["complete", "stream"])
async def test_an_ollama_answer_cut_before_its_done_line_is_cut_off(ollama, path):
    async with ModelEndpoint([OLLAMA_TEXT, OLLAMA_CALL]) as endpoint:
        events, raised = await _read(_ollama_call(ollama, endpoint, path))

    assert _kinds(events) == [EVENT_TEXT_CHUNK], _kinds(events)
    assert events[0].text == FIRST_HALF
    assert isinstance(raised, AnswerCutOff), raised
    assert (raised.adapter, raised.missing) == ("Ollama", "the done line")


@pytest.mark.asyncio
async def test_a_whole_ollama_answer_reads_as_it_did(ollama):
    async with ModelEndpoint([OLLAMA_TEXT, OLLAMA_CALL, OLLAMA_DONE]) as endpoint:
        events, raised = await _read(_ollama_call(ollama, endpoint, "complete"))

    assert raised is None, raised
    assert _kinds(events) == [EVENT_TEXT_CHUNK, EVENT_TOOL_CALL, EVENT_COMPLETE]
    assert events[1].tool_input == '{"path": "notes/q3.md"}'
    assert (events[2].stop_reason, events[2].input_tokens, events[2].output_tokens) == (
        "stop",
        12,
        3,
    )


# ── The Anthropic-compatible adapter, against a stand-in for its SDK ───────────────────────────


def _ev(kind: str, **fields: Any) -> SimpleNamespace:
    return SimpleNamespace(type=kind, **fields)


START = _ev(
    "message_start",
    message=SimpleNamespace(usage=SimpleNamespace(input_tokens=11, output_tokens=1)),
)
TEXT_BLOCK = [
    _ev("content_block_start", index=0, content_block=SimpleNamespace(type="text")),
    _ev("content_block_delta", index=0, delta=SimpleNamespace(type="text_delta", text=FIRST_HALF)),
    _ev("content_block_stop", index=0),
]


def _tool_block(partial_json: str, *, stopped: bool) -> list[SimpleNamespace]:
    block = SimpleNamespace(type="tool_use", id="toolu_1", name="write_note")
    events = [
        _ev("content_block_start", index=1, content_block=block),
        _ev(
            "content_block_delta",
            index=1,
            delta=SimpleNamespace(type="input_json_delta", partial_json=partial_json),
        ),
    ]
    return events + ([_ev("content_block_stop", index=1)] if stopped else [])


def _ended(reason: str) -> list[SimpleNamespace]:
    return [
        _ev(
            "message_delta",
            delta=SimpleNamespace(stop_reason=reason),
            usage=SimpleNamespace(output_tokens=9),
        ),
        _ev("message_stop"),
    ]


def _anthropic(monkeypatch, events: list) -> Any:
    class _Stream:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return None

        def __aiter__(self):
            self._events = iter(events)
            return self

        async def __anext__(self):
            try:
                return next(self._events)
            except StopIteration:
                raise StopAsyncIteration from None

    class _Messages:
        def stream(self, **_request):
            return _Stream()

    class AsyncAnthropic:
        def __init__(self, **_kwargs):
            self.messages = _Messages()

        async def close(self):
            return None

    sdk = types.ModuleType("anthropic")
    sdk.AsyncAnthropic = AsyncAnthropic  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "anthropic", sdk)
    from personalclaw.llm.anthropic import AnthropicProvider

    return AnthropicProvider(model=MODEL, credential=_credential())


def _anthropic_call(provider, path: str):
    if path == "complete":
        return provider.complete(MESSAGES, tools=TOOLS)
    return provider.stream("Write the Q3 note.")


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["complete", "stream"])
@pytest.mark.parametrize(
    "tail",
    [
        _tool_block(HALF_ARGUMENTS, stopped=False),
        # Its block closed, but the message never said how it ended.
        _tool_block('{"path": "notes/q3.md"}', stopped=True) + [_ev("message_stop")],
    ],
    ids=["inside-a-tool-block", "no-stop-reason"],
)
async def test_an_anthropic_compatible_answer_cut_before_its_stop_reason_is_cut_off(
    monkeypatch, path, tail
):
    provider = _anthropic(monkeypatch, [START, *TEXT_BLOCK, *tail])
    events, raised = await _read(_anthropic_call(provider, path))

    assert _kinds(events) == [EVENT_TEXT_CHUNK], _kinds(events)
    assert isinstance(raised, AnswerCutOff), raised
    assert (raised.adapter, raised.missing) == ("Anthropic-compatible", "a stop_reason")


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["complete", "stream"])
async def test_a_whole_anthropic_compatible_answer_reads_as_it_did(monkeypatch, path):
    whole = [START, *TEXT_BLOCK, *_tool_block('{"path": "notes/q3.md"}', stopped=True)]
    provider = _anthropic(monkeypatch, whole + _ended("tool_use"))
    events, raised = await _read(_anthropic_call(provider, path))

    assert raised is None, raised
    assert _kinds(events) == [EVENT_TEXT_CHUNK, EVENT_TOOL_CALL, EVENT_COMPLETE]
    call, done = events[1], events[2]
    # The call now comes once the message has said how it ended, and carries that, as the
    # OpenAI-compatible adapter's calls always did; everything else is as it was.
    assert (call.tool_call_id, call.title, call.tool_input, call.stop_reason) == (
        "toolu_1",
        "write_note",
        '{"path": "notes/q3.md"}',
        "tool_use",
    )
    assert (done.stop_reason, done.input_tokens, done.output_tokens) == ("tool_use", 11, 9)


@pytest.mark.asyncio
async def test_a_tool_block_cut_at_max_tokens_still_reaches_the_runtime_as_one(monkeypatch):
    # The message ended (`max_tokens`) with its tool block still open: the call is passed on with
    # the reason, which is what lets the runtime say it was too long.
    events_in = [START, *_tool_block(HALF_ARGUMENTS, stopped=False), *_ended("max_tokens")]
    provider = _anthropic(monkeypatch, events_in)
    events, raised = await _read(provider.complete(MESSAGES, tools=TOOLS))

    assert raised is None, raised
    assert _kinds(events) == [EVENT_TOOL_CALL, EVENT_COMPLETE]
    assert (events[0].tool_input, events[0].stop_reason) == (HALF_ARGUMENTS, "max_tokens")


# ── The one rule ────────────────────────────────────────────────────────────────────────────────


async def _source(*items):
    for item in items:
        yield item


@pytest.mark.asyncio
async def test_a_stream_that_reaches_its_terminal_event_passes_through_whole():
    out = [
        e
        async for e in until_terminal(
            _source(1, 2, 3), ends=lambda e: e == 3, adapter="A", missing="x"
        )
    ]
    assert out == [1, 2, 3]


@pytest.mark.asyncio
async def test_a_stream_that_ends_before_it_raises_and_says_whose_and_what(caplog):
    with caplog.at_level(logging.WARNING, logger="personalclaw.llm.stream_end"):
        seen, raised = await _read(
            until_terminal(
                _source(1, 2),
                ends=lambda e: e == 3,
                adapter="Example",
                missing="its end",
                model="m:1",
            )
        )

    assert seen == [1, 2]
    assert isinstance(raised, AnswerCutOff)
    assert str(raised) == (
        "the Example stream for m:1 ended before its end arrived: the answer was cut off"
    )
    assert [r.getMessage() for r in caplog.records] == [str(raised)]


@pytest.mark.asyncio
async def test_a_reader_that_stops_early_closes_the_stream_and_nothing_is_judged():
    closed = asyncio.Event()

    async def _held():
        try:
            yield 1
            await asyncio.sleep(60)
            yield 2
        finally:
            closed.set()

    reading = until_terminal(_held(), ends=lambda e: e == 3, adapter="A", missing="x")
    assert await reading.__anext__() == 1
    await reading.aclose()
    assert closed.is_set(), "the stream beneath was left open"


def test_the_cut_off_sentence_is_what_the_chat_shows():
    from personalclaw.llm_helpers import failure_clause, humanize_provider_error

    cut = AnswerCutOff(adapter="OpenAI-compatible", missing="a finish_reason", model=MODEL)
    assert humanize_provider_error(cut) == (
        "The model's answer was cut off: its stream ended before the model said it was finished. "
        "Try again; if it keeps happening, check the model's provider and the gateway log."
    )
    # What a fallback's line reads for the model that was cut off.
    assert failure_clause(cut) == "its stream ended before its answer was finished"
    assert cut.chat_meta() == {
        "cut_off": {"adapter": "OpenAI-compatible", "missing": "a finish_reason", "model": MODEL}
    }


# ── The native loop: the turn ends, and nothing it was asked to run runs ───────────────────────


class _Notes(ToolProvider):
    """A tool provider whose one tool writes a note, and which records every call it is given."""

    def __init__(self) -> None:
        self.ran: list[dict] = []

    @property
    def name(self) -> str:
        return "notes"

    @property
    def display_name(self) -> str:
        return "Notes"

    async def list_tools(self):
        return [
            ToolDefinition(
                name="write_note",
                description="Write a note.",
                parameters={"type": "object", "properties": {"path": {"type": "string"}}},
                risk_level=RiskLevel.SAFE,
            )
        ]

    async def invoke(self, tool_name, arguments):
        self.ran.append({"tool": tool_name, "arguments": arguments})
        return ToolResult(success=True, output="written")


def _runtime(provider, notes: _Notes, tmp_path):
    from personalclaw.agents.native.runtime import NativeAgentRuntime
    from personalclaw.agents.provider import AgentRuntimeDefinition

    runtime = NativeAgentRuntime(
        definition=AgentRuntimeDefinition(name="T", provider="native", model=MODEL),
        model_provider=provider,
        tool_providers=[notes],
        cwd=tmp_path,
    )
    runtime.set_approval_policy("auto")
    return runtime


@pytest.mark.asyncio
@pytest.mark.parametrize("adapter", ["openai", "ollama", "anthropic"])
async def test_a_cut_answer_ends_the_native_turn_and_its_half_call_never_runs(
    adapter, ollama, monkeypatch, tmp_path
):
    notes = _Notes()
    if adapter == "anthropic":
        endpoint = None
        provider = _anthropic(
            monkeypatch, [START, *TEXT_BLOCK, *_tool_block(HALF_ARGUMENTS, stopped=False)]
        )
    else:
        body = (
            [OLLAMA_TEXT, OLLAMA_CALL]
            if adapter == "ollama"
            else [TEXT, tool_delta(HALF_ARGUMENTS)]
        )
        endpoint = ModelEndpoint(body)
        await endpoint.__aenter__()
        provider = (
            ollama.OllamaProvider(model=MODEL, endpoint=endpoint.url, timeout=30.0)
            if adapter == "ollama"
            else _openai(endpoint)
        )
    try:
        runtime = _runtime(provider, notes, tmp_path)
        await runtime.start()
        events, raised = await _read(runtime.stream("Write the Q3 note."))
    finally:
        if endpoint is not None:
            await endpoint.__aexit__(None, None, None)

    assert isinstance(raised, AnswerCutOff), raised
    assert notes.ran == [], "a call from an answer that never ended was run"
    assert [e.text for e in events if e.kind == EVENT_TEXT_CHUNK] == [FIRST_HALF]
    assert EVENT_COMPLETE not in _kinds(events), "the turn still reported a complete answer"
    if endpoint is not None:
        # Text had been shown, so the turn is not sent again behind the person's back.
        assert len(endpoint.requests) == 1


@pytest.mark.asyncio
async def test_a_cut_before_anything_was_shown_is_asked_once_more_as_a_lost_connection_is(tmp_path):
    # Nothing reached the person, so the loop's one retry of a failed inference applies, as it
    # does to a connection lost before the reply began; the second cut ends the turn.
    notes = _Notes()
    async with ModelEndpoint([tool_delta(HALF_ARGUMENTS)]) as endpoint:
        runtime = _runtime(_openai(endpoint), notes, tmp_path)
        await runtime.start()
        events, raised = await _read(runtime.stream("Write the Q3 note."))

    assert isinstance(raised, AnswerCutOff), raised
    assert len(endpoint.requests) == 2
    assert notes.ran == []


@pytest.mark.asyncio
async def test_a_retried_cut_that_then_answers_whole_runs_its_call(tmp_path):
    whole = [tool_delta('{"path": "notes/q3.md"}'), chunk({}, "tool_calls"), "[DONE]"]
    reply = [chunk({"role": "assistant", "content": "Saved."}), chunk({}, "stop"), "[DONE]"]
    notes = _Notes()
    async with ModelEndpoint([tool_delta(HALF_ARGUMENTS)], whole, reply) as endpoint:
        runtime = _runtime(_openai(endpoint), notes, tmp_path)
        await runtime.start()
        events, raised = await _read(runtime.stream("Write the Q3 note."))

    assert raised is None, raised
    assert notes.ran == [{"tool": "write_note", "arguments": {"path": "notes/q3.md"}}]
    assert events[-1].kind == EVENT_COMPLETE


# ── The spend guard: a stream with no terminal event is a failed call ──────────────────────────


@pytest.mark.asyncio
async def test_the_guard_records_a_stream_with_no_terminal_event_as_a_failed_call(
    tmp_path, monkeypatch
):
    from personalclaw.guardrails.audit import read_recent
    from personalclaw.guardrails.breaker import get_breaker
    from personalclaw.guardrails.model_call import ModelCallGuard
    from personalclaw.llm.base import LLMEvent, ModelProvider

    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)

    class _Unfinished(ModelProvider):
        """A provider of an app's own that ends its stream without its terminal event."""

        async def start(self):
            return None

        async def shutdown(self):
            return None

        async def stream(self, message):
            yield LLMEvent(kind=EVENT_TEXT_CHUNK, text=FIRST_HALF)

        async def approve_tool(self, request_id):
            return None

        async def reject_tool(self, request_id):
            return None

        def context_usage_pct(self):
            return None

    guard = ModelCallGuard(
        _Unfinished(), use_case="summarization", provider_name="unfinished", model=MODEL
    )
    events, raised = await _read(guard.stream("Summarize the week."))

    assert [e.text for e in events] == [FIRST_HALF]
    assert isinstance(raised, AnswerCutOff), raised
    assert (raised.adapter, raised.missing) == ("unfinished", "its terminal event")
    (row,) = read_recent()
    assert (row["passed"], row["failure_mode"]) == (False, "provider_error")
    assert get_breaker("unfinished").consecutive_failures == 1


# ── A knowledge step: a cut-off answer is never stored as the step's text ──────────────────────


@pytest.mark.asyncio
async def test_a_knowledge_steps_cut_off_answer_is_not_stored_as_its_text(tmp_path, monkeypatch):
    # The step's call is behind the spend guard, as every knowledge call is. Its half answer used
    # to come back as the step's whole text (an OCR transcription, a description), indexed as
    # complete with nothing saying otherwise.
    from personalclaw.guardrails.model_call import ModelCallGuard
    from personalclaw.knowledge.pipeline.nodes import _llm

    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    monkeypatch.setattr("personalclaw.llm_helpers.use_case_chain", lambda _use_case: [])
    async with ModelEndpoint([TEXT]) as endpoint:
        guarded = ModelCallGuard(
            _openai(endpoint), use_case="image_modality", provider_name="loop", model=MODEL
        )

        async def _resolved(_use_case):
            return guarded

        monkeypatch.setattr(_llm, "_single_provider", _resolved)
        text = await _llm.complete_text("image_modality", "Describe this image.")

    assert len(endpoint.requests) == 1
    assert text == "", "the half answer was handed back as the step's text"

"""A model that runs out of output room before it answers is named, asked again differently, and
its turn ends saying so.

Measured: two review subagents on a hosted model stopped at exactly 8,192 output tokens on every
call, with no text and no tool call. The model-call log recorded each call as ``none``, passed; the
native loop asked again in the same words, met the same cap, and both agents ended "completed"
with "_No response._".

Now every provider the native loop drives carries its length stop on the terminal event, the log
records such a call as ``output_cap`` (failed when it produced nothing), and the loop asks once
more for a brief answer, without extended reasoning: on the providers whose cap counts reasoning,
that is what spent the room. A model still out of room ends the turn at the cap, and the turn's
terminal event names it.

Scripted models only; nothing here reaches a real provider.
"""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any

import httpx
import pytest

from personalclaw.agents.native.runtime import NativeAgentRuntime
from personalclaw.agents.provider import AgentRuntimeDefinition
from personalclaw.apps.native_contract import NATIVE_DIR, load_bundle_module, namespaced_module_name
from personalclaw.guardrails import audit
from personalclaw.guardrails.model_call import ModelCallGuard
from personalclaw.llm.base import ModelProvider
from personalclaw.llm.events import (
    EVENT_COMPLETE,
    EVENT_TEXT_CHUNK,
    EVENT_TOOL_CALL,
    STOP_MAX_TOKENS,
    AgentEvent,
)
from personalclaw.llm.registry import ProviderEntry
from personalclaw.tool_providers.base import RiskLevel, ToolDefinition, ToolProvider, ToolResult

CAP = 8_192


def _capped(*, text: str = "", tool_input: Any = None) -> list[AgentEvent]:
    """One inference that stopped at its output cap, after *text* and a call with *tool_input*."""
    events: list[AgentEvent] = []
    if text:
        events.append(AgentEvent(kind=EVENT_TEXT_CHUNK, text=text))
    if tool_input is not None:
        events.append(
            AgentEvent(kind=EVENT_TOOL_CALL, tool_call_id="c1", title="look", tool_input=tool_input)
        )
    events.append(
        AgentEvent(
            kind=EVENT_COMPLETE,
            stop_reason=STOP_MAX_TOKENS,
            input_tokens=39_311,
            output_tokens=CAP,
        )
    )
    return events


ANSWER = [
    AgentEvent(kind=EVENT_TEXT_CHUNK, text="The change renames one helper."),
    AgentEvent(kind=EVENT_COMPLETE, stop_reason="end_turn", output_tokens=40),
]


class _Script(ModelProvider):
    """Replays one list of events per request, keeping each request and its reasoning effort."""

    supports_tools = True

    def __init__(self, turns: list[list[AgentEvent]]) -> None:
        self._turns = turns
        self.requests: list[list[dict]] = []
        self.efforts: list[str] = []

    async def start(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None

    async def stream(self, message: str):
        async for ev in self.complete([{"role": "user", "content": message}]):
            yield ev

    async def approve_tool(self, request_id) -> None:
        return None

    async def reject_tool(self, request_id) -> None:
        return None

    def context_usage_pct(self) -> float | None:
        return None

    async def complete(self, messages, *, tools=None, model=None, reasoning_effort=""):
        self.requests.append(list(messages))
        self.efforts.append(reasoning_effort)
        for ev in self._turns[min(len(self.requests) - 1, len(self._turns) - 1)]:
            yield ev


@pytest.fixture
def calls(tmp_path, monkeypatch):
    """The model-call log, read back."""
    log = tmp_path / "model_calls.jsonl"
    monkeypatch.setattr(audit, "_audit_path", lambda: log)
    return lambda: audit.read_recent()


# ── the model-call log ────────────────────────────────────────────────────────────────────────


def _guarded(turns: list[list[AgentEvent]]) -> ModelCallGuard:
    return ModelCallGuard(
        _Script(turns), use_case="orchestration", provider_name="fake-cloud", model="m-1"
    )


async def _drain(guard: ModelCallGuard) -> list[AgentEvent]:
    return [ev async for ev in guard.complete([{"role": "user", "content": "review the diff"}])]


@pytest.mark.asyncio
async def test_a_call_that_spent_its_cap_on_nothing_is_an_output_cap_failure(calls):
    """🔴 Before: ``failure_mode "none"``, ``passed true``, for a call that produced nothing."""
    await _drain(_guarded([_capped()]))
    (row,) = calls()
    assert (row["failure_mode"], row["passed"]) == ("output_cap", False), row
    assert row["tokens_out"] == CAP


@pytest.mark.asyncio
async def test_a_call_cut_mid_tool_call_produced_nothing_that_runs(calls):
    await _drain(_guarded([_capped(tool_input='{"path": "notes/q3-recon')]))
    (row,) = calls()
    assert (row["failure_mode"], row["passed"]) == ("output_cap", False), row


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "turn", [_capped(text="The change renames one helper, and"), _capped(tool_input='{"p": 1}')]
)
async def test_a_call_cut_after_it_produced_something_is_named_and_passed(calls, turn):
    await _drain(_guarded([turn]))
    (row,) = calls()
    assert (row["failure_mode"], row["passed"]) == ("output_cap", True), row


@pytest.mark.asyncio
async def test_a_call_that_finished_is_a_plain_pass(calls):
    await _drain(_guarded([ANSWER]))
    (row,) = calls()
    assert (row["failure_mode"], row["passed"]) == ("none", True), row


# ── the native loop ───────────────────────────────────────────────────────────────────────────


class _Look(ToolProvider):
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


async def _turn(model: ModelProvider, *, effort: str = "high") -> list[AgentEvent]:
    rt = NativeAgentRuntime(
        definition=AgentRuntimeDefinition(name="T", provider="native", model="m-1"),
        model_provider=model,
        tool_providers=[_Look()],
        reasoning_effort=effort,
    )
    await rt.start()
    rt.set_approval_policy("auto")
    events = [ev async for ev in rt.stream("Review the diff.")]
    assert not any(_is_room_note(m) for m in rt._messages), "the note entered the history"
    return events


def _is_room_note(msg: dict) -> bool:
    content = msg.get("content")
    return msg.get("role") == "user" and isinstance(content, str) and "output room" in content


def _texts(events: list[AgentEvent]) -> str:
    return "".join(ev.text for ev in events if ev.kind == EVENT_TEXT_CHUNK)


@pytest.mark.asyncio
async def test_a_model_out_of_room_is_asked_once_more_briefly_and_without_reasoning():
    """🔴 Before: it was asked again in the same words, with the same reasoning, and met the same
    cap; with no tools run, it was not asked at all."""
    model = _Script([_capped(), ANSWER])
    events = await _turn(model)

    assert len(model.requests) == 2, "the capped silence was taken for the end of the turn"
    asking = model.requests[1][-1]
    assert _is_room_note(asking) and asking.get("_volatile") is True, asking
    assert f"({CAP:,} tokens)" in asking["content"] and "briefly" in asking["content"]
    assert not any(_is_room_note(m) for m in model.requests[0])
    assert model.efforts == ["high", ""], "the ask must change the request, not repeat it"
    assert _texts(events) == "The change renames one helper."
    assert events[-1].stop_reason == "end_turn" and events[-1].output_cap == 0


@pytest.mark.asyncio
async def test_a_model_still_out_of_room_ends_the_turn_at_its_cap_and_names_it(calls):
    model = _Script([[*_capped(tool_input="{}")[:-1], AgentEvent(kind=EVENT_COMPLETE)], _capped()])
    events = await _turn(model)

    assert len(model.requests) == 3, "asked more than once, or not at all"
    assert _is_room_note(model.requests[2][-1])
    done = events[-1]
    assert done.kind == EVENT_COMPLETE
    assert (done.stop_reason, done.output_cap) == (STOP_MAX_TOKENS, CAP)
    assert _texts(events) == ""
    # A turn a person watches has no guard, so the loop records its capped calls itself.
    rows = [r for r in calls() if r["use_case"] == "native_loop"]
    assert [(r["failure_mode"], r["passed"]) for r in rows] == [("output_cap", False)] * 2, rows


@pytest.mark.asyncio
async def test_a_guarded_call_is_recorded_once(calls):
    """The guard records an automated call; the loop adds no second row for it."""
    model = ModelCallGuard(
        _Script([_capped(), _capped()]), use_case="orchestration", provider_name="p", model="m-1"
    )
    await _turn(model)
    assert [(r["use_case"], r["failure_mode"], r["passed"]) for r in calls()] == [
        ("orchestration", "output_cap", False)
    ] * 2


@pytest.mark.asyncio
async def test_a_reply_cut_mid_sentence_is_its_answer_and_is_not_asked_again():
    model = _Script([_capped(text="The change renames one helper, and"), ANSWER])
    events = await _turn(model)
    assert len(model.requests) == 1
    assert (events[-1].stop_reason, events[-1].output_cap) == (STOP_MAX_TOKENS, CAP)


@pytest.mark.asyncio
async def test_a_loop_worker_out_of_room_is_asked_too():
    """Its deliverable is a file: the ask lets it make the call that writes it."""
    model = _Script([_capped(), ANSWER])
    rt = NativeAgentRuntime(
        definition=AgentRuntimeDefinition(name="T", provider="native", model="m-1"),
        model_provider=model,
        tool_providers=[_Look()],
        surface="loops",
    )
    await rt.start()
    [ev async for ev in rt.stream("Work on the brief.")]
    assert len(model.requests) == 2


# ── each provider says how it stopped (the wire clients are in test_model_provider_complete) ──


APP = "ollama-models"
ENDPOINT = "http://127.0.0.1:11434"


@pytest.fixture()
def ollama():
    try:
        yield load_bundle_module(NATIVE_DIR / APP, APP, "provider")
    finally:
        sys.modules.pop(namespaced_module_name(APP, "provider"), None)


@pytest.mark.parametrize("path", ["stream", "complete"])
def test_ollama_says_a_completion_stopped_at_its_cap(ollama, path):
    body = (
        json.dumps({"message": {"role": "assistant", "content": ""}, "done": False})
        + "\n"
        + json.dumps(
            {"done": True, "done_reason": "length", "prompt_eval_count": 10, "eval_count": 512}
        )
        + "\n"
    )
    provider = ollama._factory(
        entry=ProviderEntry(
            name="ollama",
            type="ollama",
            model="gemma4:12b",
            options={"endpoint": ENDPOINT, "default_model": "gemma4:12b"},
        )
    )

    async def _no_window(model):
        return None

    provider._served_window = _no_window
    provider._client = httpx.AsyncClient(
        base_url=ENDPOINT,
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text=body)),
    )

    async def _collect():
        events = (
            provider.stream("hello")
            if path == "stream"
            else provider.complete([{"role": "user", "content": "hello"}])
        )
        return [ev async for ev in events]

    (done,) = [ev for ev in asyncio.run(_collect()) if ev.kind == EVENT_COMPLETE]
    assert (done.stop_reason, done.output_tokens) == ("length", 512)

"""Rail: the native loop retries ONE pre-stream inference transient, and nothing else.

Issues #2287 (the native agent loop has no correction-retry — ModelCallGuard owns the
machinery and the loop never enters it) and #252 (interactive dashboard chat has no
transient-failure recovery — one provider 5xx permanently loses the turn) share one
root cause: the loop's ``self._model.complete(...)`` stream had no failure handling,
so the first raised exception propagated out of ``run()`` and the turn died.

The fix is a loop-level policy, and this rail pins each edge of it:

- a retryable failure (provider 5xx class, timeout) BEFORE anything user-visible
  streamed is retried exactly once, transparently — the turn completes;
- the retry carries the taxonomy's correction note when one exists (timeout) and
  nothing when none does (provider_error);
- a second failure in the same agent turn propagates — no retry storms;
- a NON_RETRYABLE mode (open circuit breaker) is never retried;
- a mid-stream failure AFTER visible output propagates — a retry would duplicate
  what the user already read;
- cancellation is never swallowed;
- every exceptional attempt lands in the guard-shaped audit (failed rows, and the
  passing row of a retry), and a healthy inference writes none.
"""

from __future__ import annotations

import asyncio

import pytest

import personalclaw.agents.native.runtime as runtime_mod
from personalclaw.agents.native.builtin_tools import NativeBuiltinToolProvider
from personalclaw.agents.native.runtime import NativeAgentRuntime
from personalclaw.agents.provider import AgentRuntimeDefinition
from personalclaw.guardrails.failure import CircuitOpenError, FailureMode
from personalclaw.llm.events import (
    EVENT_COMPLETE,
    EVENT_TEXT_CHUNK,
    AgentEvent,
)

pytestmark = pytest.mark.asyncio


def _text(t: str) -> AgentEvent:
    return AgentEvent(kind=EVENT_TEXT_CHUNK, text=t)


def _complete() -> AgentEvent:
    return AgentEvent(kind=EVENT_COMPLETE, stop_reason="end_turn")


class _FlakyModel:
    """A ModelProvider whose ``complete`` replays a script of attempts.

    Each script entry is either an exception instance (raised — optionally after
    yielding the events listed with it) or a list of events (streamed).
    Entries shaped ``(events, exc)`` yield the events, THEN raise — the
    mid-stream-failure case.
    """

    supports_tools = True

    def __init__(self, script: list) -> None:
        self._script = script
        self.calls = 0

    async def complete(self, messages, *, tools=None, model=None, reasoning_effort=""):
        idx = min(self.calls, len(self._script) - 1)
        self.calls += 1
        self.last_messages = list(messages)
        entry = self._script[idx]
        if isinstance(entry, BaseException):
            raise entry
        if isinstance(entry, tuple):
            events, exc = entry
            for ev in events:
                yield ev
            raise exc
        for ev in entry:
            yield ev

    async def cancel(self, *, wait_ack_timeout: float = 0.0) -> str:
        return "acked"


async def _run(tmp_path, model, monkeypatch) -> tuple[list[AgentEvent], list]:
    """Drive one turn; return (events, captured audit rows)."""
    rows: list = []
    monkeypatch.setattr(runtime_mod, "record_attempt", rows.append)
    monkeypatch.setattr(runtime_mod, "_INFERENCE_RETRY_BACKOFF_SECS", 0.0)
    rt = NativeAgentRuntime(
        definition=AgentRuntimeDefinition(name="T", provider="native", model="scripted"),
        model_provider=model,
        tool_providers=[NativeBuiltinToolProvider(tmp_path, sandbox_mode="none")],
        cwd=tmp_path,
    )
    await rt.start()
    rt.set_approval_policy("auto")
    events = [ev async for ev in rt.stream("go")]
    return events, rows


async def test_pre_stream_transient_is_retried_once_and_the_turn_completes(tmp_path, monkeypatch):
    model = _FlakyModel([RuntimeError("HTTP 500"), [_text("hi"), _complete()]])
    events, rows = await _run(tmp_path, model, monkeypatch)

    assert model.calls == 2
    texts = [ev.text for ev in events if ev.kind == EVENT_TEXT_CHUNK]
    assert texts == ["hi"], "the retried stream is the ONLY visible one — no duplicates"
    assert any(ev.kind == EVENT_COMPLETE for ev in events)
    # Audit: one failed direct attempt + one passing retry attempt.
    assert [(r.attempt, r.failure_mode, r.passed, r.strategy) for r in rows] == [
        (1, FailureMode.PROVIDER_ERROR.value, False, "direct"),
        (2, FailureMode.NONE.value, True, "retry"),
    ]
    assert all(r.use_case == "native_loop" for r in rows)


async def test_provider_error_retry_does_not_inject_a_correction_note(tmp_path, monkeypatch):
    model = _FlakyModel([RuntimeError("HTTP 500"), [_text("ok"), _complete()]])
    await _run(tmp_path, model, monkeypatch)
    # correction_note(PROVIDER_ERROR) is "" — the retry re-issues unchanged.
    assert not any(m.get("_volatile") and m.get("role") == "user" for m in model.last_messages)


async def test_timeout_retry_injects_the_taxonomys_correction_note(tmp_path, monkeypatch):
    model = _FlakyModel([asyncio.TimeoutError(), [_text("ok"), _complete()]])
    events, rows = await _run(tmp_path, model, monkeypatch)
    assert any(ev.kind == EVENT_COMPLETE for ev in events)
    tail_notes = [m for m in model.last_messages if m.get("role") == "user" and m.get("_volatile")]
    assert len(tail_notes) == 1 and "timed out" in tail_notes[0]["content"]
    assert rows[0].failure_mode == FailureMode.TIMEOUT.value


async def test_a_second_failure_in_the_same_turn_propagates(tmp_path, monkeypatch):
    model = _FlakyModel([RuntimeError("HTTP 500"), RuntimeError("HTTP 500 again")])
    with pytest.raises(RuntimeError, match="again"):
        await _run(tmp_path, model, monkeypatch)
    assert model.calls == 2, "exactly one retry — never a storm"


async def test_non_retryable_circuit_open_is_never_retried(tmp_path, monkeypatch):
    model = _FlakyModel([CircuitOpenError("bedrock", 30.0)])
    with pytest.raises(CircuitOpenError):
        await _run(tmp_path, model, monkeypatch)
    assert model.calls == 1, "an open breaker fails in microseconds — retrying defeats it"


async def test_mid_stream_failure_after_visible_output_propagates(tmp_path, monkeypatch):
    model = _FlakyModel([([_text("partial")], RuntimeError("dropped")), [_complete()]])
    with pytest.raises(RuntimeError, match="dropped"):
        await _run(tmp_path, model, monkeypatch)
    assert model.calls == 1, "visible output already streamed — a retry would duplicate it"


async def test_cancellation_is_never_swallowed(tmp_path, monkeypatch):
    model = _FlakyModel([asyncio.CancelledError()])
    with pytest.raises(asyncio.CancelledError):
        await _run(tmp_path, model, monkeypatch)
    assert model.calls == 1


async def test_a_healthy_inference_writes_no_audit_rows(tmp_path, monkeypatch):
    model = _FlakyModel([[_text("hi"), _complete()]])
    events, rows = await _run(tmp_path, model, monkeypatch)
    assert any(ev.kind == EVENT_COMPLETE for ev in events)
    assert rows == [], "healthy native traffic must not re-baseline the audit fold"

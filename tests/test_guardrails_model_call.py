"""Tests for the model-call chokepoint (AUTONOMY-GUARDRAILS §2 — Session 1).

Covers the circuit breaker FSM, the attempt-level JSONL audit trail, and the
ModelCallGuard integration (breaker check → hard timeout → audit) plus the typed
``output_type`` parse-with-targeted-retry path on ``one_shot_completion``.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from personalclaw.guardrails.audit import AttemptRecord, read_recent, record_attempt
from personalclaw.guardrails.breaker import BreakerState, CircuitBreaker, get_breaker
from personalclaw.guardrails.failure import (
    NON_RETRYABLE,
    CircuitOpenError,
    FailureMode,
    ModelCallTimeout,
    OutputContractError,
    correction_note,
    is_retryable,
)
from personalclaw.guardrails.model_call import ModelCallGuard, wrap_model_call_guard
from personalclaw.llm.base import EVENT_COMPLETE, EVENT_TEXT_CHUNK, LLMEvent, ModelProvider

# ── A minimal fake ModelProvider ────────────────────────────────────────────


class FakeProvider(ModelProvider):
    """Emits scripted text then EVENT_COMPLETE, or raises / hangs on demand."""

    def __init__(self, *, text="ok", tokens=(3, 5), raise_exc=None, hang=False, chunk_delay=0.0):
        self._text = text
        self._tokens = tokens
        self._raise = raise_exc
        self._hang = hang
        self._chunk_delay = chunk_delay
        self.started = False
        self.shut = False

    async def start(self):
        self.started = True

    async def shutdown(self):
        self.shut = True

    async def stream(self, message):
        if self._raise is not None:
            raise self._raise
        if self._hang:
            await asyncio.Event().wait()  # never completes
        if self._chunk_delay:
            await asyncio.sleep(self._chunk_delay)
        yield LLMEvent(kind=EVENT_TEXT_CHUNK, text=self._text)
        yield LLMEvent(
            kind=EVENT_COMPLETE, input_tokens=self._tokens[0], output_tokens=self._tokens[1]
        )

    async def approve_tool(self, request_id):  # pragma: no cover - never called
        pass

    async def reject_tool(self, request_id):  # pragma: no cover - never called
        pass

    def context_usage_pct(self):
        return 0.0


async def _drain(provider, message="hi"):
    return "".join([e.text async for e in provider.stream(message) if e.kind == EVENT_TEXT_CHUNK])


# ── Failure taxonomy ─────────────────────────────────────────────────────────


def test_non_retryable_modes():
    assert FailureMode.INJECTION_BLOCKED in NON_RETRYABLE
    assert FailureMode.CIRCUIT_OPEN in NON_RETRYABLE
    assert not is_retryable(FailureMode.INJECTION_BLOCKED)
    assert not is_retryable(FailureMode.CIRCUIT_OPEN)
    assert not is_retryable(FailureMode.NONE)
    assert is_retryable(FailureMode.SCHEMA_VIOLATION)
    assert is_retryable(FailureMode.TIMEOUT)


def test_correction_notes_present_for_retryable_only():
    assert correction_note(FailureMode.SCHEMA_VIOLATION)
    assert correction_note(FailureMode.TIMEOUT)
    assert correction_note(FailureMode.CIRCUIT_OPEN) == ""
    assert correction_note(FailureMode.NONE) == ""


# ── Circuit breaker FSM ──────────────────────────────────────────────────────


def test_breaker_opens_after_threshold():
    b = CircuitBreaker("p", threshold=3, recovery_secs=30)
    assert b.state() is BreakerState.CLOSED
    b.record_failure()
    b.record_failure()
    assert b.state() is BreakerState.CLOSED  # not yet at threshold
    b.record_failure()
    assert b.state() is BreakerState.OPEN
    assert b.is_open()


def test_breaker_success_resets_failures():
    b = CircuitBreaker("p", threshold=2, recovery_secs=30)
    b.record_failure()
    b.record_success()
    assert b.consecutive_failures == 0
    b.record_failure()  # only 1 after reset → still closed
    assert b.state() is BreakerState.CLOSED


def test_breaker_half_open_after_recovery(monkeypatch):
    b = CircuitBreaker("p", threshold=1, recovery_secs=30)
    b.record_failure(now=100.0)
    assert b.state(now=100.0) is BreakerState.OPEN
    assert b.is_open(now=120.0)  # 20s < 30s recovery
    assert b.state(now=131.0) is BreakerState.HALF_OPEN  # 31s ≥ recovery
    assert not b.is_open(now=131.0)  # half-open admits a probe


def test_breaker_half_open_probe_failure_reopens():
    b = CircuitBreaker("p", threshold=1, recovery_secs=10)
    b.record_failure(now=0.0)
    assert b.state(now=20.0) is BreakerState.HALF_OPEN
    b.record_failure(now=20.0)  # probe failed
    assert b.state(now=20.0) is BreakerState.OPEN
    assert b.retry_after(now=25.0) == pytest.approx(5.0)  # recovery clock reset at 20


def test_breaker_half_open_probe_success_closes():
    b = CircuitBreaker("p", threshold=1, recovery_secs=10)
    b.record_failure(now=0.0)
    assert b.state(now=20.0) is BreakerState.HALF_OPEN
    b.record_success()
    assert b.state() is BreakerState.CLOSED


def test_get_breaker_shares_by_name():
    a = get_breaker("shared")
    b = get_breaker("shared")
    assert a is b
    # An empty name yields a throwaway (never registered / shared).
    assert get_breaker("") is not get_breaker("")


# ── Audit trail ──────────────────────────────────────────────────────────────


def test_audit_appends_and_reads_back(tmp_path, monkeypatch):
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    record_attempt(
        AttemptRecord(
            audit_id="a1",
            ts=1.0,
            use_case="reasoning",
            provider="P",
            model="m",
            attempt=1,
            passed=True,
            tokens_in=3,
            tokens_out=5,
        )
    )
    record_attempt(
        AttemptRecord(
            audit_id="a1",
            ts=2.0,
            use_case="reasoning",
            provider="P",
            model="m",
            attempt=2,
            failure_mode=FailureMode.TIMEOUT.value,
            passed=False,
        )
    )
    rows = read_recent()
    assert len(rows) == 2
    assert rows[0]["audit_id"] == "a1" and rows[0]["passed"] is True
    assert rows[1]["failure_mode"] == "timeout"
    # On-disk is valid JSONL.
    lines = (tmp_path / "model_calls.jsonl").read_text().strip().splitlines()
    assert all(json.loads(line) for line in lines)


# ── ModelCallGuard integration ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_guard_passes_through_and_audits_success(tmp_path, monkeypatch):
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    guard = ModelCallGuard(
        FakeProvider(text="hello"), use_case="reasoning", provider_name="P", model="m"
    )
    await guard.start()
    assert await _drain(guard) == "hello"
    rows = read_recent()
    assert len(rows) == 1
    assert rows[0]["passed"] is True
    assert rows[0]["failure_mode"] == "none"
    assert rows[0]["tokens_in"] == 3 and rows[0]["tokens_out"] == 5
    # Success closed / kept-closed the breaker.
    assert not get_breaker("P").is_open()


@pytest.mark.asyncio
async def test_guard_audits_when_consumer_breaks_on_complete(tmp_path, monkeypatch):
    """The canonical consumer (stream_and_collect) BREAKS on EVENT_COMPLETE rather
    than draining to StopAsyncIteration. The guard must still record exactly one
    success row (regression: recording only after loop-exit audited nothing on the
    real path, since the generator was suspended at the terminal yield forever)."""
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    guard = ModelCallGuard(
        FakeProvider(text="hi"), use_case="reasoning", provider_name="P", model="m"
    )
    await guard.start()
    # Mirror stream_and_collect: break on the first EVENT_COMPLETE, then close.
    got = ""
    agen = guard.stream("x")
    async for ev in agen:
        if ev.kind == EVENT_TEXT_CHUNK:
            got += ev.text
        elif ev.kind == EVENT_COMPLETE:
            break
    await agen.aclose()
    assert got == "hi"
    rows = read_recent()
    assert len(rows) == 1
    assert rows[0]["passed"] is True and rows[0]["failure_mode"] == "none"
    assert rows[0]["tokens_in"] == 3 and rows[0]["tokens_out"] == 5
    assert not get_breaker("P").is_open()


@pytest.mark.asyncio
async def test_guard_hard_timeout(tmp_path, monkeypatch):
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    guard = ModelCallGuard(
        FakeProvider(hang=True),
        use_case="reasoning",
        provider_name="P",
        model="m",
        timeout_secs=0.05,
    )
    with pytest.raises(ModelCallTimeout):
        await _drain(guard)
    rows = read_recent()
    assert rows[-1]["failure_mode"] == "timeout"
    assert get_breaker("P").consecutive_failures == 1


@pytest.mark.asyncio
async def test_guard_provider_error_trips_breaker(tmp_path, monkeypatch):
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    b = get_breaker("P", threshold=2, recovery_secs=30)
    for _ in range(2):
        guard = ModelCallGuard(
            FakeProvider(raise_exc=RuntimeError("boom")),
            use_case="reasoning",
            provider_name="P",
            model="m",
        )
        with pytest.raises(RuntimeError):
            await _drain(guard)
    assert b.is_open()
    # Third call is refused in ~0 work with CircuitOpenError, audited circuit_open.
    guard = ModelCallGuard(
        FakeProvider(text="never"), use_case="reasoning", provider_name="P", model="m"
    )
    with pytest.raises(CircuitOpenError):
        await _drain(guard)
    assert read_recent()[-1]["failure_mode"] == "circuit_open"


def test_wrap_is_idempotent():
    inner = FakeProvider()
    g1 = wrap_model_call_guard(inner, use_case="reasoning", provider_name="P", model="m")
    g2 = wrap_model_call_guard(g1, use_case="reasoning", provider_name="P", model="m")
    assert g2 is g1
    assert isinstance(g1, ModelCallGuard)


def test_guard_proxies_supports_tools():
    inner = FakeProvider()
    inner.supports_tools = True
    g = ModelCallGuard(inner, use_case="reasoning", provider_name="P", model="m")
    assert g.supports_tools is True


def test_guard_getattr_proxies_unknown_attrs():
    inner = FakeProvider()
    inner.custom_marker = "xyz"  # not on the ABC
    g = ModelCallGuard(inner, use_case="reasoning", provider_name="P", model="m")
    assert g.custom_marker == "xyz"


# ── Typed output on one_shot_completion (§2.4) ───────────────────────────────


@pytest.mark.asyncio
async def test_one_shot_output_type_retries_then_raises(monkeypatch):
    import personalclaw.llm_helpers as helpers

    calls = []

    async def _fake_resolve(uc, **kw):
        return FakeProvider(text="not json at all")

    async def _fake_stream(provider, prompt, **kw):
        calls.append(prompt)
        return "not json at all"

    monkeypatch.setattr(
        "personalclaw.providers.provider_bridge.resolve_provider_for_use_case",
        lambda uc, **kw: FakeProvider(text="not json at all"),
    )
    monkeypatch.setattr(helpers, "stream_and_collect", _fake_stream)

    with pytest.raises(OutputContractError):
        await helpers.one_shot_completion("give me json", use_case="background", output_type=dict)
    # Exactly one targeted retry: two total stream attempts.
    assert len(calls) == 2
    assert correction_note(FailureMode.SCHEMA_VIOLATION) in calls[1]


@pytest.mark.asyncio
async def test_one_shot_output_type_succeeds_first_try(monkeypatch):
    import personalclaw.llm_helpers as helpers

    calls = []

    async def _fake_stream(provider, prompt, **kw):
        calls.append(prompt)
        return '{"ok": true}'

    monkeypatch.setattr(
        "personalclaw.providers.provider_bridge.resolve_provider_for_use_case",
        lambda uc, **kw: FakeProvider(),
    )
    monkeypatch.setattr(helpers, "stream_and_collect", _fake_stream)

    out = await helpers.one_shot_completion("json pls", use_case="background", output_type=dict)
    assert json.loads(out) == {"ok": True}
    assert len(calls) == 1  # no retry needed


@pytest.mark.asyncio
async def test_one_shot_no_output_type_returns_raw(monkeypatch):
    import personalclaw.llm_helpers as helpers

    async def _fake_stream(provider, prompt, **kw):
        return "free-form text, not json"

    monkeypatch.setattr(
        "personalclaw.providers.provider_bridge.resolve_provider_for_use_case",
        lambda uc, **kw: FakeProvider(),
    )
    monkeypatch.setattr(helpers, "stream_and_collect", _fake_stream)

    out = await helpers.one_shot_completion("anything", use_case="background")
    assert out == "free-form text, not json"

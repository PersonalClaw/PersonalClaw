"""The model-call record the guard publishes (`guardrails.calls`), read back by the code that
made the calls: a workflow step's usage, a cancel's cut-off count, a candidate's temperature.

Driven through the REAL `ModelCallGuard` wherever a claim is about what the guard observes, so
"the record says X" can only pass if the chokepoint actually wrote X.
"""

from __future__ import annotations

import asyncio

import pytest

from personalclaw.guardrails.calls import (
    ABANDONED,
    DONE,
    FAILED,
    OPEN,
    CallLog,
    ModelCall,
    capture_model_calls,
    open_call,
)
from personalclaw.guardrails.model_call import wrap_model_call_guard
from personalclaw.llm.base import EVENT_COMPLETE, EVENT_TEXT_CHUNK, LLMEvent


class _Adapter:
    def __init__(self, *, usage: tuple[int, int] = (7, 3), fail: bool = False, hold=None):
        self.usage = usage
        self.fail = fail
        self.hold = hold

    sampling_temperature = 0.4

    async def start(self) -> None:
        pass

    async def shutdown(self) -> None:
        pass

    async def stream(self, message: str):
        if self.hold is not None:
            await self.hold.wait()
        if self.fail:
            raise ConnectionError("refused")
        yield LLMEvent(kind=EVENT_TEXT_CHUNK, text="ok")
        yield LLMEvent(kind=EVENT_COMPLETE, input_tokens=self.usage[0], output_tokens=self.usage[1])


class _OllamaLikeAdapter(_Adapter):
    """A local adapter by the guard's own rule (`_is_local_provider` reads the type name)."""


async def _call(adapter, *, model: str = "unlisted-model-x") -> str:
    guard = wrap_model_call_guard(adapter, use_case="background", provider_name="p", model=model)
    chunks = []
    async for event in guard.stream("hi"):
        if event.kind == EVENT_TEXT_CHUNK:
            chunks.append(event.text)
        if event.kind == EVENT_COMPLETE:
            break
    return "".join(chunks)


# ── the log's arithmetic ─────────────────────────────────────────────────────


def _call_record(state: str, *, tokens=(0, 0), reported=True, priced=True, cost=0.0) -> ModelCall:
    return ModelCall(
        provider="p",
        model="m",
        state=state,
        input_tokens=tokens[0],
        output_tokens=tokens[1],
        usage_reported=reported,
        priced=priced,
        cost_usd=cost,
    )


def test_finished_reported_calls_sum_and_a_failed_one_adds_nothing():
    log = CallLog([_call_record(DONE, tokens=(5, 2), cost=0.01), _call_record(FAILED)])
    assert (log.tokens, log.cost_usd, log.cut_off) == (7, 0.01, 0)


def test_an_unreported_usage_is_unknown_not_zero():
    log = CallLog([_call_record(DONE, tokens=(5, 2)), _call_record(DONE, reported=False)])
    assert log.tokens is None


def test_an_unpriced_call_makes_the_cost_unknown():
    assert CallLog([_call_record(DONE, priced=False)]).cost_usd is None


@pytest.mark.parametrize("state", [OPEN, ABANDONED])
def test_a_generation_that_never_finished_is_cut_off_and_its_spend_unknown(state):
    log = CallLog([_call_record(DONE, tokens=(5, 2)), _call_record(state)])
    assert log.cut_off == 1
    assert log.tokens is None and log.cost_usd is None


def test_the_floor_is_what_the_finished_calls_reported():
    """What a cancel can still say: the cut-off call spent something unmeasured, and the finished
    ones spent exactly this. An unreported or unpriced finished call adds nothing to it."""
    log = CallLog(
        [
            _call_record(DONE, tokens=(5, 2), cost=0.01),
            _call_record(DONE, tokens=(9, 9), reported=False, priced=False, cost=0.5),
            _call_record(OPEN),
        ]
    )
    assert (log.floor_tokens, log.floor_cost_usd) == (7, 0.01)


def test_a_floor_with_nothing_reported_is_unknown_not_zero():
    """No call finished, so no provider said anything: a floor of 0 would read as a measurement."""
    log = CallLog([_call_record(OPEN), _call_record(ABANDONED)])
    assert (log.floor_tokens, log.floor_cost_usd) == (None, None)


def test_the_log_names_each_provider_once_in_first_use_order():
    calls = [ModelCall(provider=p, model="m") for p in ("cloud-b", "cloud-a", "cloud-b", "")]
    assert CallLog(calls).providers == ["cloud-b", "cloud-a"]


def test_nothing_bound_records_nowhere():
    assert open_call("p", "m", temperature=None) is None


def test_a_nested_log_sees_its_own_calls_and_passes_them_to_the_enclosing_one():
    with capture_model_calls() as outer:
        with capture_model_calls() as inner:
            first = open_call("p", "m", temperature=0.2)
        second = open_call("p", "m", temperature=0.7)
    assert inner.calls == [first]
    assert outer.calls == [first, second]
    # ONE object in both: settling it once is seen by every log that holds it.
    assert outer.calls[0] is inner.calls[0]


# ── what the guard publishes ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_guard_records_usage_model_and_the_temperature_it_sent():
    with capture_model_calls() as log:
        assert await _call(_Adapter()) == "ok"
    [call] = log.calls
    assert (call.state, call.provider, call.model) == (DONE, "p", "unlisted-model-x")
    assert (call.input_tokens, call.output_tokens, call.usage_reported) == (7, 3, True)
    assert call.temperature == 0.4
    # A hosted model with no price row is UNPRICED — never a free zero.
    assert call.priced is False


@pytest.mark.asyncio
async def test_a_local_models_zero_is_a_measurement():
    with capture_model_calls() as log:
        await _call(_OllamaLikeAdapter())
    assert log.calls[0].priced is True
    assert log.cost_usd == 0.0


@pytest.mark.asyncio
async def test_a_provider_that_reports_no_usage_is_recorded_as_unreported():
    with capture_model_calls() as log:
        await _call(_Adapter(usage=(0, 0)))
    assert log.calls[0].usage_reported is False
    assert log.tokens is None


@pytest.mark.asyncio
async def test_a_failed_call_is_recorded_as_failed():
    with capture_model_calls() as log:
        with pytest.raises(ConnectionError):
            await _call(_Adapter(fail=True))
    assert [c.state for c in log.calls] == [FAILED]


@pytest.mark.asyncio
async def test_a_cancelled_generation_is_open_then_abandoned():
    hold = asyncio.Event()
    with capture_model_calls() as log:
        task = asyncio.create_task(_call(_Adapter(hold=hold)))
        await asyncio.sleep(0.05)
        # The instant a controller cancels, the call is still OPEN: that is what it counts.
        assert [c.state for c in log.calls] == [OPEN]
        assert log.cut_off == 1
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert [c.state for c in log.calls] == [ABANDONED]
    assert log.cut_off == 1


@pytest.mark.asyncio
async def test_a_breaker_refusal_is_not_a_model_call():
    from personalclaw.guardrails.breaker import get_breaker
    from personalclaw.guardrails.failure import CircuitOpenError

    breaker = get_breaker("p")
    for _ in range(breaker.threshold):
        breaker.record_failure()
    with capture_model_calls() as log:
        with pytest.raises(CircuitOpenError):
            await _call(_Adapter())
    assert log.calls == []


# ── a call's sign of life ────────────────────────────────────────────────────


class _SlowAdapter(_Adapter):
    """Streams its answer in pieces, `gap` seconds apart: a model that is still generating."""

    def __init__(self, gap: float, pieces: int) -> None:
        super().__init__()
        self.gap, self.pieces = gap, pieces

    async def stream(self, message: str):
        for _ in range(self.pieces):
            await asyncio.sleep(self.gap)
            yield LLMEvent(kind=EVENT_TEXT_CHUNK, text="x")
        yield LLMEvent(kind=EVENT_COMPLETE, input_tokens=7, output_tokens=3)


@pytest.mark.asyncio
async def test_every_event_a_provider_sends_is_the_logs_latest_sign_of_life():
    """What the workflow stall clock reads: a call that is streaming is not silent."""
    guard = wrap_model_call_guard(
        _SlowAdapter(0.05, 3), use_case="background", provider_name="p", model="m"
    )
    seen: list[float | None] = []
    with capture_model_calls() as log:
        assert log.last_activity is None, "nothing has been called yet"
        async for event in guard.stream("hi"):
            seen.append(log.last_activity)
            if event.kind == EVENT_COMPLETE:
                break
    assert len(seen) == 4 and None not in seen, seen
    assert all(later > earlier for earlier, later in zip(seen, seen[1:])), seen


@pytest.mark.asyncio
async def test_a_call_that_sends_nothing_shows_no_sign_of_life_after_it_opened():
    hold = asyncio.Event()
    with capture_model_calls() as log:
        task = asyncio.create_task(_call(_Adapter(hold=hold)))
        await asyncio.sleep(0.05)
        opened = log.last_activity
        assert opened is not None, "opening the call is the first sign of life"
        await asyncio.sleep(0.1)
        assert log.last_activity == opened, "silence moved the clock"
        hold.set()
        await task
    assert log.last_activity > opened

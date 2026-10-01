"""The daily dollar cap holds calls that run at the same time.

The cap was checked before each call against the spend already charged, so calls fanned out
together all passed the same reading: one unattended run of five calls in the same second, each
costing $2.10 at gpt-4o-mini's listed price, spent $10.50 against a $4.00 cap.

A call that costs money now sets aside what it may cost before it starts, and starts only when
that fits beside what is spent and what the calls already running have set aside. What it set
aside is replaced by what it cost when it settles, and given back when it fails. Until a call to a
model has finished today its cost is not known, so a second call to that model waits for it.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from personalclaw.guardrails.budgets import Budget, SpendMeter
from personalclaw.guardrails.failure import BudgetExceededError
from personalclaw.guardrails.model_call import wrap_model_call_guard
from personalclaw.llm.base import EVENT_COMPLETE, EVENT_TEXT_CHUNK, LLMEvent
from personalclaw.routing import rates as rates_mod
from personalclaw.routing.rates import save_overlay

#: gpt-4o-mini's listed price, $0.15 in and $0.60 out per 1M tokens: 10M in and 1M out is $2.10.
_BILLED = (10_000_000, 1_000_000)
_PER_CALL = 2.10
_CAP = 4.00


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    rates_mod._overlay_cache = None
    yield tmp_path
    rates_mod._overlay_cache = None


class _Billed:
    """A cloud model's API, reached through the guard: it answers after a moment and reports the
    tokens it billed. ``sent`` records each request it received, and when."""

    supports_tools = False

    def __init__(self, sent: list[tuple[float, float]], usage=_BILLED, *, fails: bool = False):
        self.sent = sent
        self.usage = usage
        self.fails = fails

    async def start(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None

    async def stream(self, message: str):
        started = time.monotonic()
        await asyncio.sleep(0.05)
        if self.fails:
            raise ConnectionError("the provider hung up")
        self.sent.append((started, time.monotonic()))
        yield LLMEvent(kind=EVENT_TEXT_CHUNK, text="draft")
        yield LLMEvent(kind=EVENT_COMPLETE, input_tokens=self.usage[0], output_tokens=self.usage[1])


def _guard(
    meter: SpendMeter, provider, *, cap: float = _CAP, entry: str = "relay", model="gpt-4o-mini"
):
    return wrap_model_call_guard(
        provider,
        use_case="background",
        provider_name=entry,
        model=model,
        budget=Budget(max_dollars=cap),
        meter=meter,
    )


async def _call(guard) -> str | BudgetExceededError:
    try:
        return "".join(
            [e.text async for e in guard.stream("Draft a reply.") if e.kind == EVENT_TEXT_CHUNK]
        )
    except BudgetExceededError as exc:
        return exc


def test_five_calls_fanned_out_together_spend_no_more_than_the_cap(tmp_path):
    """🔴 Measured before the fix: all five were sent, and the day read $10.50 of $4.00."""
    meter = SpendMeter(config_dir=tmp_path)
    sent: list[tuple[float, float]] = []

    async def _fan_out():
        guards = [_guard(meter, _Billed(sent)) for _ in range(5)]
        return await asyncio.gather(*(_call(g) for g in guards))

    results = asyncio.run(_fan_out())

    assert meter.day_totals().dollars == pytest.approx(_PER_CALL)
    assert meter.day_totals().dollars <= _CAP
    assert (
        len(sent) == 1
    ), "one call fits the cap; the other four were refused before they were sent"
    refused = [r for r in results if isinstance(r, BudgetExceededError)]
    assert len(refused) == 4
    assert {(r.scope, r.dimension) for r in refused} == {("day", "dollars")}
    assert refused[0].sentence() == (
        "The daily dollar budget has $2.10 of $4.00 spent, and a call to relay:gpt-4o-mini may "
        "cost $2.10, more than the $1.90 left: raise Max dollars / day in Settings → Guardrails "
        "(0 removes the cap), or wait for it to reset at midnight."
    )


def test_a_call_that_would_not_fit_what_is_left_is_refused_before_it_is_sent(tmp_path):
    """🔴 One after the other, too: at $2.10 of $4.00, a call that has cost $2.10 today is not
    sent. Measured before the fix: it was, and the day read $4.20 of $4.00."""
    meter = SpendMeter(config_dir=tmp_path)
    sent: list[tuple[float, float]] = []

    first = asyncio.run(_call(_guard(meter, _Billed(sent))))
    second = asyncio.run(_call(_guard(meter, _Billed(sent))))

    assert first == "draft"
    assert isinstance(second, BudgetExceededError)
    assert len(sent) == 1
    assert meter.day_totals().dollars == pytest.approx(_PER_CALL)


def test_a_call_waits_for_the_ones_running_and_starts_when_they_leave_room(tmp_path):
    """A call that does not fit beside the calls running now waits for them rather than being
    refused: here the first settles for less than it set aside, and the second then fits. 🔴 Before
    the fix the second never waited: it started while the first was still running."""
    save_overlay({"relay:house-blend": {"in_per_mtok": 1.0, "out_per_mtok": 1.0}})
    meter = SpendMeter(config_dir=tmp_path)
    learned: list[tuple[float, float]] = []
    # The model's first call today: $0.02 (10k in, 10k out at $1 per 1M each way).
    asyncio.run(
        _call(_guard(meter, _Billed(learned, (10_000, 10_000)), cap=0.05, model="house-blend"))
    )
    assert meter.day_totals().dollars == pytest.approx(0.02)

    sent: list[tuple[float, float]] = []

    async def _two():
        cheap = _guard(meter, _Billed(sent, (2_500, 2_500)), cap=0.05, model="house-blend")
        after = _guard(meter, _Billed(sent, (2_500, 2_500)), cap=0.05, model="house-blend")
        return await asyncio.gather(_call(cheap), _call(after))

    results = asyncio.run(_two())

    assert results == ["draft", "draft"]
    (first_start, first_end), (second_start, _second_end) = sorted(sent)
    assert second_start >= first_end, "the second call waited for the first to settle"
    assert meter.day_totals().dollars == pytest.approx(0.03)


def test_what_a_failed_call_set_aside_is_given_back(tmp_path):
    """A call that fails charges nothing and holds nothing: the next one is weighed against the
    spend alone."""
    meter = SpendMeter(config_dir=tmp_path)
    sent: list[tuple[float, float]] = []

    failed = asyncio.run(_drain_error(_guard(meter, _Billed(sent, fails=True))))
    after = asyncio.run(_call(_guard(meter, _Billed(sent))))

    assert isinstance(failed, ConnectionError)
    assert after == "draft"
    assert meter.day_totals().dollars == pytest.approx(_PER_CALL)
    assert meter.held() == (0, 0.0), "nothing stays set aside once every call has settled"


async def _drain_error(guard) -> BaseException | None:
    try:
        async for _ in guard.stream("Draft a reply."):
            pass
    except Exception as exc:  # noqa: BLE001 - the failure is what the test reads
        return exc
    return None

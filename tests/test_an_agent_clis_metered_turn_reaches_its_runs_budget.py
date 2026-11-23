"""An agent CLI's metered turn reaches the run it is part of, as a guarded model call does.

A workflow step's usage and its run's caps (``workflows.step_usage``, ``workflows.run_budget``)
read the model calls published to the ``guardrails.calls`` log bound around the step, and only the
spend guard published one. An agent CLI makes its own model calls in its own process, so a step on
one published nothing: its usage read as no calls, and a run's dollar or token cap held it to
nothing, silently. ``acp.spend.AcpTurnMeter`` now publishes each turn it meters, priced when the
CLI says what it cost and unpriced when it says nothing, which a run's dollar cap pauses at rather
than counting as free (``workflows.resilience.check_budget``). 🔴 Red before: every row here that
expects a published call.
"""

from __future__ import annotations

import asyncio

import pytest
from test_automation_spend_is_metered import (
    TOKENS_IN,
    TOKENS_OUT,
    _agent_cli,
    _cli_turn,
    _CliClient,
)

from personalclaw.guardrails.calls import ABANDONED, DONE, OPEN, capture_model_calls
from personalclaw.llm.base import EVENT_COMPLETE, EVENT_TEXT_CHUNK


class _SilentCli(_CliClient):
    """A CLI that says nothing of what its turn used: no tokens, no cost (Gemini CLI's today)."""

    async def stream_events(self, message: str):
        from personalclaw.acp.types import AcpEvent

        self.sent.append(message)
        yield AcpEvent(kind=EVENT_TEXT_CHUNK, text="done")
        yield AcpEvent(kind=EVENT_COMPLETE)


class _UnfinishedCli(_CliClient):
    """A CLI whose turn's stream ends with no answer and no error."""

    async def stream_events(self, message: str):
        from personalclaw.acp.types import AcpEvent

        self.sent.append(message)
        yield AcpEvent(kind=EVENT_TEXT_CHUNK, text="half an answer")


class _StoppedCli(_CliClient):
    """A CLI whose turn is still answering when the step is stopped."""

    async def stream_events(self, message: str):
        from personalclaw.acp.types import AcpEvent

        self.sent.append(message)
        yield AcpEvent(kind=EVENT_TEXT_CHUNK, text="working")
        await asyncio.Event().wait()


@pytest.mark.asyncio
async def test_a_turn_whose_cli_said_its_cost_is_a_priced_call_of_its_step():
    from personalclaw.workflows.step_usage import measured

    provider = _agent_cli(_CliClient(cost_usd=0.42))
    provider.set_spend_axis("orchestration")
    with capture_model_calls() as log:
        await _cli_turn(provider)

    (call,) = log.calls
    assert (call.state, call.provider, call.priced) == (DONE, "acp:scripted-cli", True)
    assert (call.input_tokens, call.output_tokens) == (TOKENS_IN, TOKENS_OUT)
    usage = measured(log)
    assert usage.cost_usd == pytest.approx(0.42)
    assert usage.tokens == TOKENS_IN + TOKENS_OUT


@pytest.mark.asyncio
async def test_a_turn_whose_cli_said_nothing_is_an_unpriced_call_never_a_free_one():
    from personalclaw.workflows.step_usage import measured

    provider = _agent_cli(_SilentCli())
    provider.set_spend_axis("reasoning")
    with capture_model_calls() as log:
        await _cli_turn(provider)

    (call,) = log.calls
    assert call.state == DONE
    assert (call.priced, call.usage_reported) == (False, False)
    usage = measured(log)
    assert (usage.cost_usd, usage.tokens) == (None, None), "said as not recorded, not as $0"


@pytest.mark.asyncio
async def test_a_turn_whose_cli_said_nothing_is_charged_as_one_the_cap_could_not_count():
    """Priced from the no tokens it reported, its model's rate read $0, and the day's dollar total
    counted a turn it could not count as a free one."""
    from personalclaw.guardrails.budgets import get_meter

    provider = _agent_cli(_SilentCli())
    provider.set_spend_axis("loops")
    await _cli_turn(provider)

    day = get_meter().day_totals()
    assert (day.dollars, day.unpriced) == (0.0, 1)


@pytest.mark.asyncio
async def test_a_stopped_turn_is_a_call_the_step_walked_away_from():
    provider = _agent_cli(_StoppedCli())
    provider.set_spend_axis("orchestration")
    with capture_model_calls() as log:
        turn = asyncio.ensure_future(_cli_turn(provider))
        for _ in range(2000):  # until the turn is under way, or long enough to say it never was
            if log.calls:
                break
            await asyncio.sleep(0)
        turn.cancel()
        with pytest.raises(asyncio.CancelledError):
            await turn

    (call,) = log.calls
    assert call.state == ABANDONED


@pytest.mark.asyncio
async def test_a_turn_whose_stream_ends_unanswered_is_a_call_cut_off_never_a_priced_one():
    """Nothing says what it cost, so its run counts it as cut off: its tokens and dollars read as
    unknown, and a dollar cap pauses there. Settled as a $0 one, it would have been free."""
    from personalclaw.workflows.step_usage import measured

    provider = _agent_cli(_UnfinishedCli())
    provider.set_spend_axis("orchestration")
    with capture_model_calls() as log:
        await _cli_turn(provider)

    (call,) = log.calls
    assert call.state == OPEN
    usage = measured(log)
    assert usage.calls_cut_off == 1
    assert (usage.cost_usd, usage.tokens) == (None, None)


@pytest.mark.asyncio
async def test_a_persons_turn_on_an_agent_cli_publishes_nothing():
    """A chat on an agent CLI is on no metered axis, and is left alone, as a person's chat is."""
    provider = _agent_cli(_CliClient(cost_usd=0.42))
    provider.set_spend_axis("chat")
    with capture_model_calls() as log:
        await _cli_turn(provider)

    assert log.calls == []

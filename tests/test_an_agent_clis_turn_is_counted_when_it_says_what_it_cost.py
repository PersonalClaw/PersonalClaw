"""An agent CLI's turn is counted when the CLI says what it cost (``acp.turn_spend``).

ACP has an agent say what its session has cost so far on its usage update (``cost``
``{amount, currency}``) and, in the protocol's unstable part, its token totals on its answer to a
prompt (``usage``). Core read only the usage update's context window, so an agent CLI's turn reached
its ``EVENT_COMPLETE`` with no cost and no tokens: the chat wrote no Usage row for it (its row was
written only for a turn with tokens), the day's meter charged a metered one nothing it reported,
and the usage page never showed it. 🔴 Red before: every row here that expects a cost or a row.

The adapter rows use the frames each adapter's published code sends (2026-11-23): Claude Code's
(``claude-agent-acp``) a usage update with the session's cost in USD after every result and its
token totals on the answer; Gemini CLI's a usage update with its context window only. Codex's and
Kiro CLI's could not be read from what they publish; whatever they send is read by the same rule,
which the protocol rows below hold.
"""

from __future__ import annotations

import asyncio

import pytest
from test_a_repeating_tool_loop_is_stopped import _chat_state, _chat_turn, _events
from test_context_pct_honesty import _mk_session

from personalclaw.acp.types import METHOD_SESSION_UPDATE, JsonRpcMessage
from personalclaw.dashboard.state import _ChatSession
from personalclaw.llm.events import EVENT_COMPLETE, AgentEvent


def _usage(*, used: int = 40_000, size: int = 200_000, **more: object) -> JsonRpcMessage:
    return JsonRpcMessage(
        method=METHOD_SESSION_UPDATE,
        params={
            "sessionId": "A",
            "update": {"sessionUpdate": "usage_update", "used": used, "size": size, **more},
        },
    )


def _claude(cost: float, inputs: int, outputs: int, cached: int = 0) -> tuple[list, dict]:
    """A turn as claude-agent-acp ends it: its usage update with the session's cost so far, and
    its answer with the session's token totals."""
    frames = [_usage(cost={"amount": cost, "currency": "USD"})]
    answer = {
        "stopReason": "end_turn",
        "usage": {
            "inputTokens": inputs,
            "outputTokens": outputs,
            "cachedReadTokens": cached,
            "totalTokens": inputs + outputs + cached,
        },
    }
    return frames, answer


def _gemini() -> tuple[list, dict]:
    """A turn as Gemini CLI ends it: a usage update with its context window, and no cost."""
    return [_usage(used=12_000, size=1_048_576)], {"stopReason": "end_turn"}


async def _turn(s, q, sent, frames: list, answer: dict):
    """Drive one turn of *s* with *frames*, answered with *answer*; its ``EVENT_COMPLETE``."""
    for f in frames:
        q.put_nowait(f)

    async def _answer():
        while not sent:
            await asyncio.sleep(0)
        rid, fut = sent[-1]
        await asyncio.sleep(0.05)  # the frames are read before the answer
        fut.set_result(JsonRpcMessage(id=rid, result=answer))

    task = asyncio.ensure_future(_answer())
    done = [ev async for ev in s.stream_events("hi", timeout=5) if ev.kind == EVENT_COMPLETE]
    await task
    sent.clear()
    assert len(done) == 1
    return done[0]


def _opened_here():
    s, q, sent = _mk_session()
    s._spend.opened_here()
    return s, q, sent


# ── what each adapter sends ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_claude_codes_turns_carry_what_each_one_cost_and_used():
    s, q, sent = _opened_here()
    first = await _turn(s, q, sent, *_claude(0.30, 1_200, 300, cached=50))
    second = await _turn(s, q, sent, *_claude(0.42, 2_000, 500, cached=80))
    assert first.cost_usd == pytest.approx(0.30)
    assert (first.input_tokens, first.output_tokens, first.cache_read_tokens) == (1_200, 300, 50)
    assert second.cost_usd == pytest.approx(0.12)
    assert (second.input_tokens, second.output_tokens, second.cache_read_tokens) == (800, 200, 30)


@pytest.mark.asyncio
async def test_gemini_clis_turns_carry_nothing_it_did_not_say():
    s, q, sent = _opened_here()
    done = await _turn(s, q, sent, *_gemini())
    assert (done.cost_usd, done.input_tokens, done.output_tokens) == (0.0, 0, 0)


# ── the protocol's rule, whichever CLI sends it ───────────────────────────────


@pytest.mark.asyncio
async def test_a_resumed_sessions_first_report_holds_turns_this_process_never_ran():
    """Its first total also holds the turns before the resume: it sets where counting starts."""
    s, q, sent = _mk_session()  # session/load: nothing known of what it cost before
    first = await _turn(s, q, sent, *_claude(5.00, 90_000, 20_000))
    second = await _turn(s, q, sent, *_claude(5.25, 91_000, 20_400))
    assert (first.cost_usd, first.input_tokens, first.output_tokens) == (0.0, 0, 0)
    assert second.cost_usd == pytest.approx(0.25)
    assert (second.input_tokens, second.output_tokens) == (1_000, 400)


@pytest.mark.asyncio
async def test_a_turn_the_agent_said_nothing_of_adds_nothing_and_loses_nothing():
    s, q, sent = _opened_here()
    await _turn(s, q, sent, *_claude(0.30, 1_200, 300))
    silent = await _turn(s, q, sent, [], {"stopReason": "end_turn"})
    assert (silent.cost_usd, silent.input_tokens) == (0.0, 0)
    # The next report counts from the last total said, so nothing is lost or counted twice.
    later = await _turn(s, q, sent, *_claude(0.50, 2_000, 600))
    assert later.cost_usd == pytest.approx(0.20)


@pytest.mark.asyncio
async def test_a_total_that_went_down_is_no_turns_cost_and_counting_goes_on_from_it():
    s, q, sent = _opened_here()
    await _turn(s, q, sent, *_claude(0.80, 3_000, 900))
    reset = await _turn(s, q, sent, *_claude(0.05, 200, 40))
    after = await _turn(s, q, sent, *_claude(0.15, 500, 100))
    assert (reset.cost_usd, reset.input_tokens) == (0.0, 0)
    assert after.cost_usd == pytest.approx(0.10)
    assert (after.input_tokens, after.output_tokens) == (300, 60)


@pytest.mark.parametrize(
    "cost",
    [
        {"amount": 0.3, "currency": "EUR"},
        {"amount": 12, "currency": "credits"},
        {"amount": -1, "currency": "USD"},
        {"amount": True, "currency": "USD"},
        {"amount": "0.3", "currency": "USD"},
        {"currency": "USD"},
        "0.30",
    ],
    ids=["euros", "another-unit", "negative", "a-bool", "a-string", "no-amount", "not-an-object"],
)
def test_a_cost_that_is_not_dollars_said_as_a_number_is_not_read(cost):
    from personalclaw.acp import turn_spend

    assert turn_spend.reported_cost(_usage(cost=cost)) is None


def test_the_cost_is_read_off_a_usage_update_and_nothing_else():
    from personalclaw.acp import turn_spend

    said = {"cost": {"amount": 0.3, "currency": "USD"}}
    assert turn_spend.reported_cost(_usage(**said)) == 0.3
    other = JsonRpcMessage(
        method=METHOD_SESSION_UPDATE,
        params={"update": {"sessionUpdate": "agent_message_chunk", **said}},
    )
    assert turn_spend.reported_cost(other) is None


@pytest.mark.parametrize(
    "usage",
    [
        None,
        {"outputTokens": 3},
        {"inputTokens": 3},
        {"inputTokens": -1, "outputTokens": 3},
        {"inputTokens": 3, "outputTokens": "4"},
        {"inputTokens": 3, "outputTokens": 4, "cachedReadTokens": True},
    ],
    ids=["none", "no-input", "no-output", "negative", "a-string", "a-bool-cache"],
)
def test_token_totals_that_are_not_counts_are_not_read(usage):
    from personalclaw.acp import turn_spend

    assert turn_spend.reported_tokens({"stopReason": "end_turn", "usage": usage}) is None


# ── where it is counted ───────────────────────────────────────────────────────


def _rows() -> list[dict]:
    from personalclaw import usage_ledger

    return usage_ledger._iter_rows()


@pytest.mark.asyncio
async def test_a_chats_turn_on_an_agent_cli_that_said_its_cost_writes_its_usage_row(tmp_path):
    """The chat wrote a turn's row only when it carried tokens; a CLI may say a cost and none."""
    state, client = _chat_state(tmp_path, "acp:claude")
    client.stream = lambda *a, **kw: _events([AgentEvent(kind=EVENT_COMPLETE, cost_usd=0.12)])
    before = len(_rows())

    await _chat_turn(state, _ChatSession("chat-cli-cost"))

    rows = _rows()[before:]
    assert len(rows) == 1, rows
    assert rows[0]["cost_usd"] == pytest.approx(0.12) and rows[0]["priced"] is True


@pytest.mark.asyncio
async def test_a_chats_turn_on_an_agent_cli_that_said_nothing_writes_no_row(tmp_path):
    state, client = _chat_state(tmp_path, "acp:gemini")
    client.stream = lambda *a, **kw: _events([AgentEvent(kind=EVENT_COMPLETE)])
    before = len(_rows())

    await _chat_turn(state, _ChatSession("chat-cli-silent"))

    assert _rows()[before:] == []

"""The context-% surface never states a number the backend did not supply (G8).

ACP-AGENT-PARITY `G8`/`O7`: a `context_usage` frame was emitted every turn with
``pct: 0.0`` and the live telemetry line printed ``context 0%`` on all ~14 turns of an
audited drive — including turns carrying 18 KB of injected context. The producer could
not express "unknown": ``AcpPromptStats.context_pct`` and ``AgentEvent.context_usage_pct``
were bare defaulted floats, so "the adapter told us nothing" and "the context is
genuinely empty" were the same value, and every consumer rendered the second.

The fix makes the measurement optional (``float | None``) end to end. These tests hold
BOTH directions, because each is a real bug this repo has shipped:

* ``None`` (unmeasured) must render NOTHING — no percentage anywhere on the surface.
* ``0.0`` (measured empty) must render ``0%`` — folding a legitimate zero into the
  absent marker is the inverse defect, and it hides a real answer.

Every case below is asserted on the RENDERED surface (the composed line, the API dict)
rather than on the field alone, and each pair is additionally asserted to DISAGREE —
a test that only checks ``is None`` passes just as happily when both cases collapse
again to the same output.
"""

from __future__ import annotations

import asyncio

import pytest

from personalclaw.acp.adapter import acp_event_to_agent_event
from personalclaw.acp.session import AcpSession
from personalclaw.acp.types import METHOD_METADATA, AcpEvent, AcpPromptStats, JsonRpcMessage
from personalclaw.dashboard.chat_runner import _turn_complete_line
from personalclaw.llm.events import AgentEvent

# ── the printed surface: dashboard/chat_runner._turn_complete_line ────────────────


def _line(pct: float | None) -> str:
    return _turn_complete_line(
        events=12,
        tool_calls=3,
        context_pct=pct,
        input_tokens=0,
        output_tokens=0,
        cost_usd=0.0,
        priced=False,
    )


class TestTurnCompleteLine:
    """The exact surface the ACP audit watched print ``context 0%`` fourteen times."""

    def test_unmeasured_states_no_percentage_at_all(self):
        line = _line(None)
        # Not "reports 0" — states NO number. Both the word and the sign must be gone,
        # so a future ``context ?%`` or ``context 0%`` regression reddens here.
        assert "context" not in line
        assert "%" not in line
        # The rest of the line is untouched: omitting the chip is not dropping telemetry.
        assert line == "Turn complete: 12 events, 3 tool calls"

    def test_measured_zero_states_zero(self):
        # A genuinely empty context is a real answer and must be shown as one.
        assert "context 0%" in _line(0.0)

    def test_measured_value_states_the_value(self):
        assert "context 42%" in _line(41.6)

    def test_unmeasured_and_measured_zero_disagree(self):
        # THE load-bearing assertion. Both directions collapse to one output if the
        # producer loses its ability to say "unknown" OR if a consumer starts folding a
        # legitimate 0 into the absent marker — this reddens for either.
        assert _line(None) != _line(0.0)


# ── the ACP producer: acp/session.py ──────────────────────────────────────────────


def _mk_session(session_id: str = "A"):
    q: asyncio.Queue[JsonRpcMessage] = asyncio.Queue()
    sent_futs: list = []

    async def send_request(method, params):
        rid = 100 + len(sent_futs)
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        sent_futs.append((rid, fut))
        return rid, fut

    async def send_response(req_id, result):
        return None

    async def cancel_session():
        return None

    s = AcpSession(
        session_id,
        q,
        send_request=send_request,
        send_response=send_response,
        cancel_session=cancel_session,
        is_process_alive=lambda: True,
    )
    return s, q, sent_futs


def _metadata(session_id: str, pct: float | None) -> JsonRpcMessage:
    params: dict = {"sessionId": session_id}
    if pct is not None:
        params["contextUsagePercentage"] = pct
    return JsonRpcMessage(method=METHOD_METADATA, params=params)


async def _run_turn(s, q, sent_futs, frames: list[JsonRpcMessage]) -> None:
    """Drive one full turn, queueing ``frames`` before the terminal response lands."""
    for f in frames:
        q.put_nowait(f)

    async def _resolve():
        while not sent_futs:
            await asyncio.sleep(0)
        rid, fut = sent_futs[-1]
        await asyncio.sleep(0.05)  # let the drain consume the queued frames first
        fut.set_result(JsonRpcMessage(id=rid, result={"stopReason": "end_turn"}))

    task = asyncio.ensure_future(_resolve())
    async for _ev in s.stream_events("hi", timeout=5):
        pass
    await task


class TestAcpProducer:
    def test_fresh_session_reports_unknown(self):
        s, _q, _f = _mk_session()
        assert s.context_usage_pct() is None

    @pytest.mark.asyncio
    async def test_turn_with_no_metadata_frame_stays_unknown(self):
        """The audited case: an adapter that never sends contextUsagePercentage."""
        s, q, futs = _mk_session()
        await _run_turn(s, q, futs, [])
        assert s.context_usage_pct() is None

    @pytest.mark.asyncio
    async def test_metadata_frame_without_the_key_stays_unknown(self):
        # A metadata frame arrives but carries no percentage — still nothing measured.
        s, q, futs = _mk_session()
        await _run_turn(s, q, futs, [_metadata("A", None)])
        assert s.context_usage_pct() is None

    @pytest.mark.asyncio
    async def test_reported_zero_is_measured_zero(self):
        s, q, futs = _mk_session()
        await _run_turn(s, q, futs, [_metadata("A", 0)])
        assert s.context_usage_pct() == 0.0

    @pytest.mark.asyncio
    async def test_unmeasured_and_reported_zero_disagree_on_the_line(self):
        """Same producer, two inputs, rendered: the two cases must not print alike."""
        silent, q1, f1 = _mk_session("S")
        await _run_turn(silent, q1, f1, [])
        zero, q2, f2 = _mk_session("Z")
        await _run_turn(zero, q2, f2, [_metadata("Z", 0)])
        assert _line(silent.context_usage_pct()) != _line(zero.context_usage_pct())
        assert "%" not in _line(silent.context_usage_pct())
        assert "context 0%" in _line(zero.context_usage_pct())

    @pytest.mark.asyncio
    async def test_known_pct_carries_across_a_silent_later_turn(self):
        """The cross-turn carry (session.py) survives: a measured value persists when a
        later turn's adapter reports nothing, rather than snapping back to unknown."""
        s, q, futs = _mk_session()
        await _run_turn(s, q, futs, [_metadata("A", 61.5)])
        assert s.context_usage_pct() == 61.5
        await _run_turn(s, q, futs, [])  # silent turn
        assert s.context_usage_pct() == 61.5

    @pytest.mark.asyncio
    async def test_unknown_carries_as_unknown(self):
        """Carrying "unknown" forward must not manufacture a value — the carry is what
        the audit saw repeat a fabricated 0% across fourteen turns."""
        s, q, futs = _mk_session()
        for _ in range(3):
            await _run_turn(s, q, futs, [])
            assert s.context_usage_pct() is None


# ── the neutral event + its ACP→neutral adapter ───────────────────────────────────


class TestNeutralEvent:
    def test_defaults_are_unknown_not_zero(self):
        assert AgentEvent(kind="complete").context_usage_pct is None
        assert AcpEvent(kind="complete").context_usage_pct is None
        assert AcpPromptStats().context_pct is None

    def test_adapter_preserves_both_answers(self):
        unknown = acp_event_to_agent_event(AcpEvent(kind="complete"))
        measured = acp_event_to_agent_event(AcpEvent(kind="complete", context_usage_pct=0.0))
        assert unknown.context_usage_pct is None
        assert measured.context_usage_pct == 0.0
        assert unknown.context_usage_pct != measured.context_usage_pct

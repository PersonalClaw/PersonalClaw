"""A failed chat turn must SAY what failed — its error message is never empty.

Measured on a fresh instance (`.lanes/day56b-evidence/s20-*`): a provider whose connection is
refused raises ``httpx.ReadError('')``. The turn's error message was appended with
``content: ""`` — broadcast over the WebSocket and written to the transcript on disk — so the
chat showed a red error bar with nothing in it, 2 runs of 2. ``str(exc)`` is empty for that
whole family (every httpx timeout, ``asyncio.TimeoutError``, a bare ``ConnectionResetError``),
and the turn handler rendered the exception as its ``str``.

This drives the REAL turn handler (``run_chat``'s ``except Exception`` branch) with the exact
exception class, and reads the message the session recorded — the same dict that is broadcast
and persisted.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from personalclaw.dashboard.chat_runner import run_chat
from personalclaw.dashboard.state import DashboardState, _ChatSession
from personalclaw.history import ConversationLog


def _state_whose_turn_raises(tmp_path, exc: BaseException) -> DashboardState:
    sessions = MagicMock(count=0)
    sessions.get_pid = MagicMock(return_value=None)
    sessions.record_failure = AsyncMock()
    sessions.get_or_create = AsyncMock(side_effect=exc)
    state = DashboardState(
        sessions=sessions,
        start_time=0.0,
        conversation_log=ConversationLog(base_dir=tmp_path),
    )
    state.context_builder = MagicMock()
    state.broadcast_ws = MagicMock()
    state.push_sessions_update = MagicMock()
    state.push_refresh = MagicMock()
    return state


async def _run_failing_turn(tmp_path, exc: BaseException) -> str:
    state = _state_whose_turn_raises(tmp_path, exc)
    session = _ChatSession("chat-1-test")
    session._titled = True
    with (
        patch("personalclaw.dashboard.chat_runner.maybe_offer_check_work"),
        patch("personalclaw.dashboard.chat_runner._maybe_followups", new=AsyncMock()),
        patch("personalclaw.dashboard.chat_plan.maybe_submit_plan_draft"),
    ):
        await run_chat(state, session, "hi")
    errors = [m["content"] for m in session.messages if m.get("role") == "error"]
    assert len(errors) == 1, session.messages
    return errors[0]


@pytest.mark.asyncio
async def test_a_dropped_provider_connection_is_said_not_blank(tmp_path):
    req = httpx.Request("POST", "http://127.0.0.1:11435/api/chat")
    content = await _run_failing_turn(tmp_path, httpx.ReadError("", request=req))
    assert content.strip(), "the error bar would render empty"
    # The endpoint survives the credential/exfiltration redaction the handler applies.
    assert "model provider at 127.0.0.1:11435 was lost" in content


@pytest.mark.asyncio
async def test_a_timed_out_turn_is_said_not_blank(tmp_path):
    import asyncio

    content = await _run_failing_turn(tmp_path, asyncio.TimeoutError())
    assert "timed out" in content


@pytest.mark.asyncio
async def test_a_message_bearing_error_still_reaches_the_user_verbatim(tmp_path):
    # The falsifiability control: the fix only fills an EMPTY message. A real one is
    # still shown as the provider said it, not replaced by a composed sentence.
    content = await _run_failing_turn(tmp_path, RuntimeError("upstream said something specific"))
    assert content == "upstream said something specific"

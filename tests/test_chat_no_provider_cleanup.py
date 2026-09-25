"""Regression for #2856 — a chat turn whose provider fails to resolve.

On a fresh install with no model provider bound (`needs_model: true`), building the
native runtime raises ``ProviderResolutionError`` for use case ``chat`` *before*
``run_chat`` reaches its per-turn telemetry block. The user saw the correct streamed
error, but the turn's background task then crashed with

    UnboundLocalError: cannot access local variable '_turn_tool_call_count' ...

because the ``finally`` block's done-branch reads ``_turn_tool_call_count`` (via
``maybe_offer_check_work``) on every turn exit, and the counter was only bound deep
inside the ``try``. The crash replaced the streamed error, skipped end-of-turn cleanup
(the done marker, the autonudge re-arm, the "check this work" offer), and logged as an
unretrieved task exception. The fix binds the counter before the ``try``.

The real-home writers (``config_dir()``, SEL) are already redirected to a tmp dir by
the autouse ``_isolate_real_home_writers`` fixture in ``conftest.py``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from personalclaw.dashboard.chat_runner import run_chat
from personalclaw.dashboard.state import DashboardState, _ChatSession
from personalclaw.history import ConversationLog
from personalclaw.providers.provider_bridge import ProviderResolutionError


def _make_state(tmp_path) -> DashboardState:
    """A state whose runtime build fails exactly as it does with no provider bound."""
    sessions = MagicMock(count=0)
    sessions.get_pid = MagicMock(return_value=None)
    sessions.record_failure = AsyncMock()
    sessions.get_or_create = AsyncMock(
        side_effect=ProviderResolutionError(
            "WHAT: no model provider resolves for use case 'chat'\n"
            "WHY: no provider is bound\nFIX: add a model provider in Settings"
        )
    )
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


@pytest.mark.asyncio
async def test_no_provider_turn_runs_cleanup_without_unbound_error(tmp_path):
    state = _make_state(tmp_path)
    session = _ChatSession("chat-1-test")
    # Skip the auto-title background model call — it is orthogonal to the counter crash.
    session._titled = True

    with (
        patch("personalclaw.dashboard.chat_runner.maybe_offer_check_work") as offer,
        patch("personalclaw.dashboard.chat_runner._maybe_followups", new=AsyncMock()),
        patch("personalclaw.dashboard.chat_plan.maybe_submit_plan_draft"),
    ):
        # Before the fix this raised UnboundLocalError('_turn_tool_call_count') out of
        # the finally block; the primary assertion is simply that it RETURNS.
        await run_chat(state, session, "hi")

    # The user still saw the streamed provider error.
    assert any(m.get("role") == "error" for m in session.messages)
    assert session._last_turn_errored is True
    state.sessions.record_failure.assert_awaited_once()

    # End-of-turn cleanup ran to completion: the done-branch reached the offer with the
    # initialized counter (no tools ran on a failed turn), broadcast chat_done, and cleared
    # the task handle. The end-of-turn marker is a live-reader signal, never a transcript
    # entry.
    offer.assert_called_once()
    assert offer.call_args.args[2] == 0
    assert not any(m.get("role") == "done" for m in session.messages)
    state.broadcast_ws.assert_any_call("chat_done", {"session": session.key})
    assert session.task is None

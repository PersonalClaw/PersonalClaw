"""Origin-aware approval-timeout policy: unattended prompts fail closed fast."""

from __future__ import annotations

import asyncio

import pytest
from chat_test_helpers import _make_state


def test_interactive_source_gets_long_timeout(tmp_path):
    state = _make_state(tmp_path)
    assert state._approval_timeout_for("dashboard") == state._APPROVAL_TIMEOUT
    assert state._approval_timeout_for("cli") == state._APPROVAL_TIMEOUT
    assert state._approval_timeout_for("") == state._APPROVAL_TIMEOUT


def test_unattended_sources_get_short_timeout(tmp_path):
    state = _make_state(tmp_path)
    for src in ("cron", "loop", "heartbeat", "schedule", "autonudge"):
        assert state._approval_timeout_for(src) == state._UNATTENDED_APPROVAL_TIMEOUT, src
    # Substring match — a decorated source label still resolves unattended.
    assert state._approval_timeout_for("cron:job-123") == state._UNATTENDED_APPROVAL_TIMEOUT
    assert state._approval_timeout_for("gateway:heartbeat") == state._UNATTENDED_APPROVAL_TIMEOUT


def test_unattended_timeout_is_shorter_than_interactive(tmp_path):
    state = _make_state(tmp_path)
    assert state._UNATTENDED_APPROVAL_TIMEOUT < state._APPROVAL_TIMEOUT


@pytest.mark.asyncio
async def test_timeout_fails_closed_to_deny(tmp_path, monkeypatch):
    """An unanswered approval denies (returns False) on timeout."""
    state = _make_state(tmp_path)
    # Force an immediate timeout regardless of source.
    monkeypatch.setattr(state, "_approval_timeout_for", lambda source: 0.01)
    result = await state.request_approval("a1", "cron", "rm -rf /", session="loop-x")
    assert result is False
    # The pending approval is cleaned up after timeout.
    assert "a1" not in state._pending_approvals
    assert "a1" not in state._approval_futures


@pytest.mark.asyncio
async def test_approval_granted_before_timeout(tmp_path):
    """A resolve before the window returns True (the timeout doesn't interfere)."""
    state = _make_state(tmp_path)

    async def _resolve_soon():
        await asyncio.sleep(0.01)
        state.resolve_approval("a2", approved=True)

    task = asyncio.create_task(_resolve_soon())
    result = await state.request_approval("a2", "dashboard", "ls", session="chat-1")
    await task
    assert result is True

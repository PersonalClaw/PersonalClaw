"""One approval window for every approval that waits, and it fails closed (F-33).

`request_approval` used to pick its window by a substring of `source`: five minutes for
`cron`/`loop`/`heartbeat`/`schedule`/`autonudge`, two hours otherwise. No production caller ever
passed such a source — every one is `subagent` or `mcp:<server>` (the MCP question is also cut
off by its own call ceiling) — so the five-minute rule governed nothing while reading as the rule
for night-time work. What unattended work really does is decline at once, without an approval at
all. The branch is gone, and the window is the owner's setting (`approval_window_secs`).
"""

from __future__ import annotations

import asyncio
import types

import pytest
from chat_test_helpers import _make_state

import personalclaw.dashboard.approval_state as approval_state
from personalclaw.approval_answer import YOU


def _capture_window(monkeypatch) -> list[float]:
    """Record the window `request_approval` waits, and expire it at once."""
    seen: list[float] = []

    async def _expire(aw, timeout=None):
        seen.append(timeout)
        return await asyncio.wait_for(aw, timeout=0.01)

    only_here = types.SimpleNamespace(
        **{name: getattr(asyncio, name) for name in dir(asyncio) if not name.startswith("__")}
    )
    only_here.wait_for = _expire
    monkeypatch.setattr(approval_state, "asyncio", only_here)
    return seen


@pytest.mark.asyncio
async def test_every_source_waits_the_same_window(tmp_path, monkeypatch):
    state = _make_state(tmp_path)
    monkeypatch.setattr(state, "approval_window_secs", lambda: 5400.0)
    seen = _capture_window(monkeypatch)
    for source in ("subagent", "mcp:files", "cron:job-1", "gateway:heartbeat", ""):
        assert await state.request_approval(f"a-{source}", source, "Bash") is False
    assert seen == [5400.0] * 5


def test_an_unreadable_config_waits_the_default_window(tmp_path, monkeypatch):
    def _broken():
        raise ValueError("config.json is not JSON")

    monkeypatch.setattr("personalclaw.config.loader.AppConfig.load", staticmethod(_broken))
    assert _make_state(tmp_path).approval_window_secs() == 7200.0


@pytest.mark.asyncio
async def test_timeout_fails_closed_to_deny(tmp_path, monkeypatch):
    """An unanswered approval denies (returns False) on timeout."""
    state = _make_state(tmp_path)
    monkeypatch.setattr(state, "approval_window_secs", lambda: 0.01)
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
        state.resolve_approval("a2", approved=True, by=YOU)

    task = asyncio.create_task(_resolve_soon())
    result = await state.request_approval("a2", "dashboard", "ls", session="chat-1")
    await task
    assert result is True

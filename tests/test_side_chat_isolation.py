"""Side-chat isolation — the load-bearing guarantee.

A side turn must NEVER touch the parent transcript: no _ChatSession.append, no
_on_message broadcast, no message added to session.messages. These tests pin that
structural invariant (the primary isolation layer) + the snapshot/prompt builders
+ the stale-frame drop. If a future change routes side content through append(),
test_side_turn_never_appends_to_parent fails — by design.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from personalclaw.dashboard import side as side_mod
from personalclaw.dashboard.side_context import build_side_message, build_snapshot
from personalclaw.dashboard.side_state import SideState
from personalclaw.dashboard.state import _ChatSession


def _parent_with_history() -> _ChatSession:
    s = _ChatSession(key="parent", agent="default")
    s.append("user", "deploy the stack", "msg msg-u", broadcast=False)
    s.append("assistant", "Deployed. Gateway is healthy.", "msg msg-a", broadcast=False)
    return s


def test_snapshot_reads_only_visible_parent_messages():
    s = _parent_with_history()
    s.append("tool", "🔧 bash", "msg msg-tool", broadcast=False)  # non-visible
    snap = build_snapshot(s)
    assert "User: deploy the stack" in snap
    assert "Assistant: Deployed. Gateway is healthy." in snap
    assert "bash" not in snap  # tool messages excluded


def test_build_side_message_includes_snapshot_question_and_prior():
    s = _parent_with_history()
    side = SideState(open=True)
    side.append("user", "what model?")
    side.append("assistant", "glm-5.1")
    prompt = build_side_message(s, side, "summarize what we did")
    assert "deploy the stack" in prompt  # snapshot
    assert "what model?" in prompt  # prior side Q
    assert "summarize what we did" in prompt  # new question
    assert "read-only" in prompt.lower()  # boundary envelope


@pytest.mark.asyncio
async def test_side_turn_never_appends_to_parent():
    """THE isolation guarantee: a full side turn adds ZERO messages to the parent
    and never calls the parent's append/_on_message."""
    s = _parent_with_history()
    parent_msg_count = len(s.messages)
    on_message = MagicMock()
    s._on_message = on_message  # would fire inside append() if it were called
    s._side = SideState(open=True)
    s._side.last_run_id = "run-1"  # api_side_turn sets this before dispatching
    s._side.is_complete = False

    # Fake provider + session manager: stream two chunks, no real model.
    state = MagicMock()
    state.broadcast_ws = MagicMock()
    state._background_tasks = set()
    provider = MagicMock()
    state.sessions = MagicMock()
    state.sessions.get_or_create = AsyncMock(return_value=(provider, True, False))
    state.sessions.release = MagicMock()

    async def fake_stream_and_collect(prov, msg, *, approval_policy, on_chunk=None, **kw):
        if on_chunk:
            on_chunk("The model ")
            on_chunk("is glm-5.1.")
        return "The model is glm-5.1."

    with patch("personalclaw.llm_helpers.stream_and_collect", new=fake_stream_and_collect):
        await side_mod._run_side_turn(state, "parent", s, s._side, "what model?", "run-1")

    # Parent transcript completely untouched.
    assert len(s.messages) == parent_msg_count
    on_message.assert_not_called()
    # Answer landed ONLY on the side buffer.
    assert s._side.messages[-1].role == "assistant"
    assert "glm-5.1" in s._side.messages[-1].content
    assert s._side.is_complete is True
    # A side_result frame with done=True was broadcast.
    assert any(
        c.args[0] == side_mod.SIDE_RESULT_EVENT and c.args[1].get("done")
        for c in state.broadcast_ws.call_args_list
    )


@pytest.mark.asyncio
async def test_side_turn_rejects_tools():
    """The side turn must request REJECT_ALL so no tool can run."""
    s = _parent_with_history()
    s._side = SideState(open=True)
    s._side.last_run_id = "run-1"
    state = MagicMock()
    state.broadcast_ws = MagicMock()
    state.sessions = MagicMock()
    state.sessions.get_or_create = AsyncMock(return_value=(MagicMock(), True, False))
    state.sessions.release = MagicMock()
    captured = {}

    async def fake_sac(prov, msg, *, approval_policy, on_chunk=None, **kw):
        captured["policy"] = approval_policy
        return ""

    with patch("personalclaw.llm_helpers.stream_and_collect", new=fake_sac):
        await side_mod._run_side_turn(state, "parent", s, s._side, "q", "run-1")

    from personalclaw.llm_helpers import ToolApprovalPolicy

    assert captured["policy"] == ToolApprovalPolicy.REJECT_ALL


@pytest.mark.asyncio
async def test_stale_run_frames_are_dropped():
    """If the side chat is closed (or a newer run starts) mid-stream, late deltas
    must not broadcast — _emit drops frames whose run_id != side.last_run_id."""
    s = _parent_with_history()
    s._side = SideState(open=True)
    s._side.last_run_id = "run-2"  # a newer run already claimed the buffer
    state = MagicMock()
    state.broadcast_ws = MagicMock()
    state.sessions = MagicMock()
    state.sessions.get_or_create = AsyncMock(return_value=(MagicMock(), True, False))
    state.sessions.release = MagicMock()

    async def fake_sac(prov, msg, *, approval_policy, on_chunk=None, **kw):
        if on_chunk:
            on_chunk("stale chunk")
        return "stale chunk"

    with patch("personalclaw.llm_helpers.stream_and_collect", new=fake_sac):
        # Run with the OLD run-1 id — every _emit should be dropped.
        await side_mod._run_side_turn(state, "parent", s, s._side, "q", "run-1")

    assert state.broadcast_ws.call_count == 0  # all frames stale → dropped

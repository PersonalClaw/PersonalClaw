"""Tests for dashboard tool approval flow — normal/trust/yolo modes."""

import asyncio
import time
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from personalclaw.dashboard.chat import run_chat
from personalclaw.dashboard.state import DashboardState, _ChatSession, parse_cls_meta
from personalclaw.history import ConversationLog
from personalclaw.hooks import ToolHookResult
from personalclaw.llm.base import (
    EVENT_COMPLETE,
    EVENT_PERMISSION_REQUEST,
    LLMEvent,
)

# ── Helpers ──


async def _async_iter(items: list):  # type: ignore[type-arg]
    for item in items:
        yield item


def _answer_approval(session, request_id: str, decision: str):
    """Resolve the approval future for *request_id* as soon as `run_chat` registers it.

    🔴 issue 2563. Four tests here each hand-rolled this as a fixed
    ``await asyncio.sleep(0.05)`` followed by ``if fut and not fut.done()``. Two separate
    defects, and the second is what made the first expensive:

    * **50 ms is a race, not a wait.** Under CI load ``run_chat`` has not yet put
      ``_approval_futures[request_id]`` in place when the sleep expires.
    * **The guard could not observe its own failure.** On a miss ``fut`` is ``None``, so
      ``if fut and …`` silently did nothing, nothing ever resolved the approval, and
      ``run_chat`` blocked until pytest-timeout killed it at **120 s**. The reported failure
      was therefore a timeout, and the actual cause never appeared in the log — a miss and a
      healthy skip were indistinguishable. Measured in CI run 34070833025, where it reddened a
      PR whose diff cannot reach this file.

    So this polls for the future rather than guessing how long registration takes, and
    **fails loudly** if it never appears. It is one helper rather than four copies on purpose:
    four independent responders is what let one shape drift into a flake in the first place,
    and a fifth test copying the old pattern would reintroduce it.
    """

    async def _responder() -> None:
        # 2 s ceiling at 5 ms granularity: still resolves in one tick when the machine is
        # idle, and survives a loaded runner. Deliberately not `sleep(2)` — the point is to
        # be fast when it can be and patient when it must be.
        for _ in range(400):
            fut = session._approval_futures.get(request_id)
            if fut is not None:
                if not fut.done():
                    fut.set_result(decision)
                return
            await asyncio.sleep(0.005)
        raise AssertionError(
            f"run_chat never registered an approval future for {request_id!r} within 2s, so "
            f"nothing would have answered it. Without this raise the test would hang until "
            f"pytest-timeout killed it and reported a timeout instead of this cause (#2563). "
            f"Futures present: {sorted(session._approval_futures)}"
        )

    # `asyncio.create_task` rather than `get_event_loop().create_task` — the latter is
    # deprecated and emits "There is no current event loop" under 3.12+.
    return asyncio.create_task(_responder())


@contextmanager
def _patch_stats():
    with patch("personalclaw.dashboard.chat.sel") as mock_sel:
        mock_sel.return_value = MagicMock()
        yield


def _permission_event(
    title: str = "fs_write",
    tool_kind: str = "edit",
) -> LLMEvent:
    return LLMEvent(
        kind=EVENT_PERMISSION_REQUEST,
        title=title,
        tool_kind=tool_kind,
        request_id="req-1",
    )


def _complete_event() -> LLMEvent:
    return LLMEvent(kind=EVENT_COMPLETE, stop_reason="end_turn")


def _make_hook_store() -> MagicMock:
    hs = MagicMock()
    hs.fire_for_ids = AsyncMock(return_value=[])
    return hs


def _make_state(
    tmp_path,
    context_builder=None,
    hook_store=None,
) -> tuple[DashboardState, AsyncMock]:
    """Return (state, client) with all async methods properly mocked."""
    sessions = MagicMock(count=0)
    sessions.get_pid = MagicMock(return_value=None)
    client = AsyncMock()
    sessions.get_or_create = AsyncMock(return_value=(client, True, False))
    sessions.record_failure = AsyncMock()
    sessions.check_context_usage = MagicMock()
    state = DashboardState(
        sessions=sessions,
        start_time=0.0,
        conversation_log=ConversationLog(base_dir=tmp_path),
    )
    state.context_builder = context_builder
    state._hook_store = hook_store or _make_hook_store()
    state.broadcast_ws = MagicMock()
    state.push_sessions_update = MagicMock()
    return state, client


def _make_session(key: str = "chat-1-test", trust: bool = False) -> _ChatSession:
    session = _ChatSession(key)
    session._trust = trust
    return session


def _set_stream(client: AsyncMock, events: list[LLMEvent]) -> None:
    """Make client.stream() return an async iterable of events."""
    client.stream = MagicMock(side_effect=lambda *a, **kw: _async_iter(events))


def _tool_messages(session: _ChatSession) -> list[dict]:
    return [m for m in session.messages if m.get("role") in ("tool", "permission")]


def _context_builder(hook_result: ToolHookResult = ToolHookResult.allow()) -> MagicMock:
    cb = MagicMock()
    cb.hooks.on_tool_call.return_value = hook_result
    cb.build_message.return_value = ("hello", None)
    return cb


# ── Tests ──


@pytest.mark.asyncio
async def test_the_approval_responder_fails_loudly_when_nothing_registers():
    """The fix for #2563 is only a fix if the miss is LOUD. This is that proof.

    The old responder's failure mode was silence: on a miss it did nothing, `run_chat` blocked,
    and pytest-timeout reported a 120 s timeout with no trace of the cause. So the helper is
    not merely "less racy" — it must turn that silence into a named failure, and this asserts
    it directly rather than trusting the four callers to surface it.

    Nothing here ever registers a future (no `run_chat` runs), which is exactly the state the
    flake produced under load. Two things are asserted: the error NAMES the cause, and it
    arrives on the helper's own ~2 s ceiling rather than on the 120 s test timeout — the
    difference between a diagnosable failure and the one that reddened an unrelated PR.
    """
    session = _make_session()
    assert not session._approval_futures, "precondition: nothing is registered"

    started = time.monotonic()
    task = _answer_approval(session, "req-1", "approved")
    with pytest.raises(AssertionError, match="never registered an approval future"):
        await task
    elapsed = time.monotonic() - started

    assert elapsed < 30, (
        f"the responder took {elapsed:.1f}s to give up. It must fail on its own ceiling, not "
        "ride the 120s pytest-timeout — that timeout is what hid the cause in #2563."
    )


class TestApprovalModes:
    """Verify that normal/trust/yolo modes route permission requests correctly."""

    @pytest.mark.asyncio
    async def test_normal_mode_prompts_interactively(self, tmp_path):
        """Normal mode (no trust, no yolo) must send a permission message."""
        state, client = _make_state(tmp_path, context_builder=_context_builder())
        session = _make_session()
        _set_stream(client, [_permission_event(), _complete_event()])

        _answer_approval(session, "req-1", "approved")

        with _patch_stats():
            await run_chat(state, session, "hello")

        msgs = _tool_messages(session)
        assert any(
            m["role"] == "permission" for m in msgs
        ), f"Expected interactive prompt, got: {msgs}"
        client.approve_tool.assert_called_once()

    @pytest.mark.asyncio
    async def test_trust_mode_auto_approves(self, tmp_path):
        """Trust mode must auto-approve without interactive prompt."""
        state, client = _make_state(tmp_path, context_builder=_context_builder())
        session = _make_session(trust=True)
        _set_stream(client, [_permission_event(), _complete_event()])

        with _patch_stats():
            await run_chat(state, session, "hello")

        msgs = _tool_messages(session)
        assert not any(m["role"] == "permission" for m in msgs), "Trust mode should not prompt"
        # Auto-approved tools are broadcast via WS, not appended to session
        state.broadcast_ws.assert_any_call(
            "tool_call",
            {
                "session": session.key,
                "tool": _permission_event().title,
                "kind": _permission_event().tool_kind,
                "auto": True,
                "tool_call_id": "",
                "purpose": "",
                "input_preview": "",
            },
        )
        client.approve_tool.assert_called_once()

    @pytest.mark.asyncio
    async def test_yolo_mode_auto_approves(self, tmp_path):
        """YOLO mode must auto-approve without interactive prompt."""
        state, client = _make_state(tmp_path, context_builder=_context_builder())
        state.enable_yolo()
        session = _make_session()
        _set_stream(client, [_permission_event(), _complete_event()])

        with _patch_stats():
            await run_chat(state, session, "hello")

        msgs = _tool_messages(session)
        assert not any(m["role"] == "permission" for m in msgs), "YOLO mode should not prompt"
        state.broadcast_ws.assert_any_call(
            "tool_call",
            {
                "session": session.key,
                "tool": _permission_event().title,
                "kind": _permission_event().tool_kind,
                "auto": True,
                "tool_call_id": "",
                "purpose": "",
                "input_preview": "",
            },
        )
        client.approve_tool.assert_called_once()

    @pytest.mark.asyncio
    async def test_hook_deny_rejects(self, tmp_path):
        """Hook deny must reject the tool without prompting."""
        cb = _context_builder(ToolHookResult.deny("blocked by policy"))
        state, client = _make_state(tmp_path, context_builder=cb)
        session = _make_session()
        _set_stream(client, [_permission_event(), _complete_event()])

        with _patch_stats():
            await run_chat(state, session, "hello")

        msgs = _tool_messages(session)
        assert any("blocked" in m.get("content", "").lower() for m in msgs)
        client.reject_tool.assert_called_once()
        client.approve_tool.assert_not_called()

    @pytest.mark.asyncio
    async def test_hook_auto_approve_skips_prompt(self, tmp_path):
        """Hook auto-approve must approve without interactive prompt."""
        cb = _context_builder(ToolHookResult.auto_approve())
        state, client = _make_state(tmp_path, context_builder=cb)
        session = _make_session()
        _set_stream(client, [_permission_event(), _complete_event()])

        with _patch_stats():
            await run_chat(state, session, "hello")

        assert not any(m["role"] == "permission" for m in _tool_messages(session))
        client.approve_tool.assert_called_once()
        client.reject_tool.assert_not_called()

    @pytest.mark.asyncio
    async def test_interactive_reject(self, tmp_path):
        """User rejecting interactively must call reject_tool."""
        state, client = _make_state(tmp_path, context_builder=_context_builder())
        session = _make_session()
        _set_stream(client, [_permission_event(), _complete_event()])

        _answer_approval(session, "req-1", "rejected")

        with _patch_stats():
            await run_chat(state, session, "hello")

        client.reject_tool.assert_called_once()

    @pytest.mark.asyncio
    async def test_interactive_approve_with_empty_hooks(self, tmp_path):
        """After interactive approve, empty hook results must NOT reject."""
        state, client = _make_state(tmp_path, context_builder=_context_builder())
        session = _make_session()
        _set_stream(client, [_permission_event(), _complete_event()])

        _answer_approval(session, "req-1", "approved")

        with _patch_stats():
            await run_chat(state, session, "hello")

        msgs = _tool_messages(session)
        assert not any(
            "no hooks" in m.get("content", "") for m in msgs
        ), f"Empty hook results should not reject: {msgs}"
        client.approve_tool.assert_called_once()


class TestTrustYoloPropagation:
    """Trust/YOLO mode propagates approval policy to session manager."""

    @pytest.mark.asyncio
    async def test_run_chat_propagates_trust_to_session(self, tmp_path):
        """When session has _trust=True, run_chat sets session approval policy to auto."""
        state, client = _make_state(tmp_path, context_builder=_context_builder())
        session = _make_session(trust=True)
        _set_stream(client, [_complete_event()])

        with _patch_stats():
            await run_chat(state, session, "hello")

        state.sessions.set_approval_policy.assert_called_with(f"dashboard:{session.key}", "auto")

    @pytest.mark.asyncio
    async def test_run_chat_propagates_yolo_to_session(self, tmp_path):
        """When state._yolo=True, run_chat sets session approval policy to auto."""
        state, client = _make_state(tmp_path, context_builder=_context_builder())
        state.enable_yolo()
        session = _make_session()
        _set_stream(client, [_complete_event()])

        with _patch_stats():
            await run_chat(state, session, "hello")

        state.sessions.set_approval_policy.assert_called_with(f"dashboard:{session.key}", "auto")

    @pytest.mark.asyncio
    async def test_run_chat_no_propagation_without_trust_or_yolo(self, tmp_path):
        """Without trust or YOLO, set_approval_policy clears to default."""
        state, client = _make_state(tmp_path, context_builder=_context_builder())
        session = _make_session()
        _set_stream(client, [_complete_event()])

        with _patch_stats():
            await run_chat(state, session, "hello")

        state.sessions.set_approval_policy.assert_called_once_with(f"dashboard:{session.key}", "")


class TestResolveApprovalSessionFallback:
    """resolve_approval falls through to session-level futures for chat tool approvals."""

    @pytest.mark.asyncio
    async def test_resolves_session_future_when_state_has_none(self, tmp_path):
        """resolve_approval finds futures in session._approval_futures."""
        state, _ = _make_state(tmp_path)
        session = _make_session()
        state._sessions[session.key] = session

        loop = asyncio.get_running_loop()
        fut: asyncio.Future[str] = loop.create_future()
        session._approval_futures["req-42"] = fut

        result = state.resolve_approval("req-42", True)

        assert result is True
        assert fut.done()
        assert fut.result() == "approved"
        state.broadcast_ws.assert_called_with(
            "approval_resolved", {"id": "req-42", "approved": True}
        )
        state.push_sessions_update.assert_called_once()

    @pytest.mark.asyncio
    async def test_session_reject(self, tmp_path):
        """resolve_approval rejects session futures correctly."""
        state, _ = _make_state(tmp_path)
        session = _make_session()
        state._sessions[session.key] = session

        loop = asyncio.get_running_loop()
        fut: asyncio.Future[str] = loop.create_future()
        session._approval_futures["req-43"] = fut

        result = state.resolve_approval("req-43", False)

        assert result is True
        assert fut.result() == "rejected"

    @pytest.mark.asyncio
    async def test_state_futures_checked_first(self, tmp_path):
        """State-level futures take priority over session-level."""
        state, _ = _make_state(tmp_path)
        session = _make_session()
        state._sessions[session.key] = session

        loop = asyncio.get_running_loop()
        state_fut: asyncio.Future[bool] = loop.create_future()
        session_fut: asyncio.Future[str] = loop.create_future()
        state._approval_futures["req-44"] = state_fut
        session._approval_futures["req-44"] = session_fut

        state.resolve_approval("req-44", True)

        assert state_fut.done()
        assert (
            not session_fut.done()
        ), "Session future should not be touched when state future exists"

    @pytest.mark.asyncio
    async def test_returns_false_when_not_found(self, tmp_path):
        """resolve_approval returns False when ID not in state or any session."""
        state, _ = _make_state(tmp_path)
        session = _make_session()
        state._sessions[session.key] = session

        assert state.resolve_approval("nonexistent", True) is False


class TestToolCallIdRedaction:
    """Verify tool_call_id is redacted before use in event loop."""

    @pytest.mark.asyncio
    async def test_tool_call_id_redacted_in_trust_mode(self, tmp_path):
        state, client = _make_state(tmp_path, context_builder=_context_builder())
        session = _make_session(trust=True)
        evt = _permission_event()
        evt.tool_call_id = "tcid-clean"
        evt.tool_purpose = "test purpose"
        _set_stream(client, [evt, _complete_event()])

        with _patch_stats():
            await run_chat(state, session, "hello")

        # Trust mode broadcasts tool_call via WS with tool_call_id
        state.broadcast_ws.assert_any_call(
            "tool_call",
            {
                "session": session.key,
                "tool": evt.title,
                "kind": evt.tool_kind,
                "auto": True,
                "tool_call_id": "tcid-clean",
                "purpose": "test purpose",
                "input_preview": "",
            },
        )


class TestBatchRejection:
    """Verify batch rejection auto-rejects remaining tools."""

    @pytest.mark.asyncio
    async def test_batch_rejection_auto_rejects_remaining(self, tmp_path):
        state, client = _make_state(tmp_path, context_builder=_context_builder())
        session = _make_session()
        evt1 = _permission_event(title="tool_a")
        evt1.request_id = "req-1"
        evt1.tool_call_id = "tc-1"
        evt2 = _permission_event(title="tool_b")
        evt2.request_id = "req-2"
        evt2.tool_call_id = "tc-2"
        _set_stream(client, [evt1, evt2, _complete_event()])

        _answer_approval(session, "req-1", "rejected")

        with _patch_stats():
            await run_chat(state, session, "hello")

        # First tool rejected interactively, second auto-rejected
        client.reject_tool.assert_any_call("req-1")
        client.reject_tool.assert_any_call("req-2")
        assert session._batch_rejected is False  # reset in finally

    @pytest.mark.asyncio
    async def test_batch_rejected_reset_on_exception(self, tmp_path):
        """_batch_rejected is reset even if event loop raises."""
        state, client = _make_state(tmp_path, context_builder=_context_builder())
        session = _make_session()
        session._batch_rejected = True

        async def _exploding_stream():
            yield _permission_event()
            raise RuntimeError("boom")

        client.stream = MagicMock(side_effect=lambda *a, **kw: _exploding_stream())

        with _patch_stats():
            try:
                await run_chat(state, session, "hello")
            except RuntimeError:
                pass

        assert session._batch_rejected is False


class TestToolCompletionTracking:
    """Verify tool completion state tracking."""

    @pytest.mark.asyncio
    async def test_trust_mode_with_tool_call_id(self, tmp_path):
        """Trust mode auto-approve broadcasts tool_call_id in WS."""
        state, client = _make_state(tmp_path, context_builder=_context_builder())
        session = _make_session(trust=True)
        evt = _permission_event()
        evt.tool_call_id = "tc-42"
        _set_stream(client, [evt, _complete_event()])

        with _patch_stats():
            await run_chat(state, session, "hello")

        # Verify tool_call broadcast includes tool_call_id
        calls = [c for c in state.broadcast_ws.call_args_list if c[0][0] == "tool_call"]
        assert len(calls) > 0
        assert calls[0][0][1]["tool_call_id"] == "tc-42"


class TestStateMetaAndPermissions:
    """Cover state.py meta handling and permission resolution."""

    def test_append_with_meta(self):
        session = _make_session()
        session.append(
            "tool", "test", meta={"tool_call_id": "tc-1", "purpose": "testing"}, broadcast=False
        )
        assert session.messages[-1]["meta"]["tool_call_id"] == "tc-1"

    def test_mark_permission_resolved(self):
        import json

        session = _make_session()
        cls_data = json.dumps({"request_id": "req-42"})
        session.append("permission", "tool_x", cls_data, broadcast=False)
        session.mark_permission_resolved("req-42", "rejected")
        updated = json.loads(session.messages[-1]["cls"])
        assert updated["resolved"] == "rejected"

    def test_mark_permission_resolved_not_found(self):
        session = _make_session()
        # Should not raise
        session.mark_permission_resolved("nonexistent", "approved")

    def test_parse_cls_meta_normalizes_request_id(self):
        meta = parse_cls_meta('{"request_id": "req-1", "tool_input": "x"}')
        assert "approval_id" in meta
        assert "request_id" not in meta

    def test_meta_stored_on_message(self):
        session = _make_session()
        session.append("tool", "test", meta={"tool_call_id": "tc-1"}, broadcast=False)
        assert session.messages[-1].get("meta", {}).get("tool_call_id") == "tc-1"

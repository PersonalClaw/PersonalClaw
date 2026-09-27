"""A call a subagent's runtime declined for want of an approver reaches the owner's Inbox (F-33).

A subagent is unattended by construction, so the native runtime declines a call that needs an
approval inside its own loop and says so on the tool result only (`TOOL_META_AUTO_DENIED`). The
subagent manager is the consumer of that stream; measured on a live General loop (2026-09-25), a
worker step's `write_file` was declined this way and the only trace was the step's own "could not
create the file". The manager now relays it, and the gateway records it against the PARENT — the
chat that spawned the helper, or the workflow step it ran for — which is where a person looks.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from test_gateway import _make_orchestrator, _mock_dashboard_state
from test_gateway import _mock_sessions as _gateway_sessions
from test_subagent import _mock_ctx_builder, _mock_sessions

from personalclaw.llm.base import EVENT_COMPLETE, EVENT_TOOL_RESULT, LLMEvent
from personalclaw.subagent import SubagentManager


@pytest.mark.asyncio
async def test_the_manager_relays_a_declined_call_and_nothing_else() -> None:
    sessions = _mock_sessions()
    provider = sessions.get_or_create.return_value[0]

    async def _stream(*_a, **_k):
        yield LLMEvent(kind=EVENT_TOOL_RESULT, title="read_file", tool_output="ok")
        yield LLMEvent(
            kind=EVENT_TOOL_RESULT,
            title="write_file",
            tool_output="Error: this tool needs approval but the run is unattended",
            tool_meta={"ok": False, "auto_denied": True},
        )
        yield LLMEvent(kind=EVENT_COMPLETE)

    provider.stream = MagicMock(side_effect=lambda *a, **k: _stream())
    seen: list[tuple[str, dict]] = []

    async def _on_event(etype: str, _info: object, extra: dict) -> None:
        seen.append((etype, extra))

    manager = SubagentManager(
        sessions=sessions, ctx_builder=_mock_ctx_builder(), on_event=_on_event, is_yolo=lambda: True
    )
    with patch("personalclaw.subagent.Stats"), patch("personalclaw.subagent.sel"):
        info = manager.spawn("write the digest", parent_session_key="dashboard:chat-a")
        assert info is not None
        await manager._tasks[info.id]
        await manager.flush_deliveries()

    denied = [extra for etype, extra in seen if etype == "subagent_auto_denied"]
    assert denied == [{"tool": "write_file"}], seen


def _orchestrator():
    orch = _make_orchestrator()
    orch.sessions = _gateway_sessions()
    orch.ctx_builder = MagicMock()
    orch.ctx_builder.hooks = MagicMock()
    orch.ctx_builder.build_message = MagicMock(return_value=("msg", None))
    orch.dashboard_state = _mock_dashboard_state()
    with patch("personalclaw.trust_mode.is_yolo_active", return_value=False):
        with patch("personalclaw.gateway.SubagentManager") as manager_cls:
            manager = MagicMock()
            manager.running = []
            manager.running_agents_for = MagicMock(return_value=[])
            manager.get = MagicMock(return_value=None)
            manager_cls.return_value = manager
            orch._init_subagents()
    return orch, manager_cls.call_args[1]["on_event"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("parent", "title", "who"),
    [
        ("dashboard:chat-a", "Clean the scratch dir", "A subagent of “Clean the scratch dir”"),
        ("workflow:run-7:write", "", "A workflow step"),
        ("dashboard:chat-b", "", "A subagent"),
    ],
)
async def test_the_gateway_records_it_against_the_parent(parent, title, who) -> None:
    orch, on_event = _orchestrator()
    chat = MagicMock()
    chat.title = title
    orch.dashboard_state.get_session = MagicMock(return_value=chat if title else None)
    info = MagicMock()
    info.id = "sub-1"
    info.parent_session_key = parent
    # A chat's or a workflow step's helper — no trigger started it (`SubagentInfo.trigger_id`).
    info.trigger_id = ""

    with patch("personalclaw.dashboard.auto_denials.note_unattended") as note:
        await on_event("subagent_auto_denied", info, {"tool": "write_file"})

    note.assert_called_once()
    kwargs = note.call_args.kwargs
    assert kwargs["session_key"] == parent.removeprefix("dashboard:")
    assert kwargs["tool"] == "write_file"
    assert kwargs["who"] == who
    orch.dashboard_state.broadcast_ws.assert_not_called()

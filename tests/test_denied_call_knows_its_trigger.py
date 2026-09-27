"""A call a trigger's run was denied knows which trigger ran it, so the note can run it again.

A trigger's `invoke-agent` or `run-prompt` action spawns a subagent, and a subagent asks for a tool
approval through the registry with no chat behind it (`session=""`). When nobody answered, #3698's
note said "A subagent asked to run write_file…" and offered nothing: the trigger that started the
work was known to the dispatch and to nothing after it. PR #3698 left it undone for that reason
("its trigger can't be derived reliably from the session key").

So the dispatch says which trigger it is, and every hop keeps it: the action's context
(`ActionContext.trigger_id`), the spawn (`SubagentInfo.trigger_id`), the approval
(`request_approval(trigger=…)`) and the note (`refs.trigger`). The Inbox can then offer to run that
trigger again, and the note resolves when the call that re-run asks for is answered.
"""

from __future__ import annotations

import asyncio
import copy
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from test_gateway import _make_orchestrator, _mock_dashboard_state
from test_gateway import _mock_sessions as _gateway_sessions
from test_pending_approval_every_surface import world  # noqa: F401 - a fixture, used by name
from test_subagent import _mock_ctx_builder, _mock_sessions

import personalclaw.action_providers as AP
import personalclaw.config.loader as loader
from personalclaw.action_providers.base import ActionContext, ActionResult
from personalclaw.action_providers.invoke_agent_provider import InvokeAgentActionProvider
from personalclaw.action_providers.run_prompt_provider import RunPromptActionProvider
from personalclaw.dashboard.handlers import trigger_runs
from personalclaw.dashboard.handlers import triggers as T
from personalclaw.gateway import GatewayOrchestrator
from personalclaw.inbox import OPEN_STATUSES
from personalclaw.llm.base import EVENT_PERMISSION_REQUEST, LLMEvent
from personalclaw.subagent import SubagentManager
from personalclaw.triggers.models import Trigger
from personalclaw.triggers.store import TriggerStore

_AGENT = {"inline": {"provider": "invoke-agent", "config": {"task_template": "write the digest"}}}


# ── the dispatch says which trigger it is ────────────────────────────────────────────────────


class _Seen:
    """An action provider that keeps the context each run was handed."""

    def __init__(self) -> None:
        self.contexts: list[ActionContext] = []

    async def execute(self, config, ctx, timeout=30):
        self.contexts.append(ctx)
        return ActionResult(success=True, stdout="ran")


@pytest.fixture
def seen(tmp_path, monkeypatch):
    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(T, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(T, "_trigger_store", lambda: TriggerStore(base_dir=tmp_path))
    recorder = _Seen()
    real = AP.get_action_provider
    monkeypatch.setattr(
        AP, "get_action_provider", lambda name: recorder if name == "invoke-agent" else real(name)
    )
    return recorder


def _nightly(**over) -> Trigger:
    fields = {
        "id": "nightly",
        "name": "Nightly digest",
        "kind": "clock",
        "enabled": True,
        "created_by": "user",
        "spec": {"kind": "cron", "expr": "0 9 * * *"},
        "workflow": copy.deepcopy(_AGENT),
        "capabilities": {"providers": ["invoke-agent"]},
    }
    return Trigger(**{**fields, **over})


def test_run_now_hands_its_action_the_trigger_that_dispatched_it(seen):
    ran, _note = asyncio.run(
        trigger_runs._dispatch_store_action(_nightly(), {"trigger_id": "nightly"})
    )
    assert ran is True
    assert [c.trigger_id for c in seen.contexts] == ["nightly"]


def test_an_unattended_fire_hands_it_the_trigger_too(seen):
    asyncio.run(
        object.__new__(GatewayOrchestrator)._fire_store_trigger(
            _nightly(kind="file"), {"trigger_id": "nightly", "kind": "file"}, event="file.changed"
        )
    )
    assert [c.trigger_id for c in seen.contexts] == ["nightly"]


# ── the agent it spawns keeps it ────────────────────────────────────────────────────────────


def _services(spawned: dict):
    def _bg(coro):
        return asyncio.ensure_future(coro)

    return SimpleNamespace(
        subagents=SimpleNamespace(spawn=lambda **kw: spawned.update(kw)), spawn_background=_bg
    )


@pytest.mark.parametrize(
    ("module", "provider", "config"),
    [
        ("invoke_agent_provider", InvokeAgentActionProvider, {"task_template": "write the digest"}),
        ("run_prompt_provider", RunPromptActionProvider, {"prompt_id": "digest"}),
    ],
)
def test_both_agent_actions_spawn_under_the_trigger(monkeypatch, module, provider, config):
    import importlib

    mod = importlib.import_module(f"personalclaw.action_providers.{module}")
    spawned: dict = {}
    monkeypatch.setattr(mod, "get_action_services", lambda: _services(spawned))
    if module == "run_prompt_provider":
        monkeypatch.setattr(mod, "render_saved_prompt", lambda pid, v: "write the digest")

    async def go():
        res = await provider().execute(
            config, ActionContext(event="Schedule", trigger_id="nightly")
        )
        await asyncio.sleep(0.05)
        return res

    assert asyncio.run(go()).success is True
    assert spawned["trigger_id"] == "nightly"


@pytest.mark.asyncio
async def test_the_subagent_keeps_the_trigger_it_ran_for() -> None:
    manager = SubagentManager(
        sessions=_mock_sessions(), ctx_builder=_mock_ctx_builder(), is_yolo=lambda: True
    )
    with patch("personalclaw.subagent.Stats"), patch("personalclaw.subagent.sel"):
        info = manager.spawn("write the digest", trigger_id="nightly")
        assert info is not None and info.trigger_id == "nightly"
        await manager._tasks[info.id]
        await manager.flush_deliveries()


# ── its approval and its note say so ────────────────────────────────────────────────────────


def _orchestrator(info):
    orch = _make_orchestrator()
    orch.sessions = _gateway_sessions()
    orch.ctx_builder = MagicMock()
    orch.ctx_builder.hooks = MagicMock()
    orch.dashboard_state = _mock_dashboard_state()
    with patch("personalclaw.trust_mode.is_yolo_active", return_value=False):
        with patch("personalclaw.gateway.SubagentManager") as manager_cls:
            manager = MagicMock()
            manager.running = []
            manager.running_agents_for = MagicMock(return_value=[])
            manager.get = MagicMock(
                side_effect=lambda agent_id: info if agent_id == info.id else None
            )
            manager_cls.return_value = manager
            orch._init_subagents()
    return orch, manager_cls.call_args[1]


@pytest.mark.asyncio
async def test_the_approval_a_trigger_s_agent_asks_for_is_listed_under_the_trigger() -> None:
    info = SimpleNamespace(id="ab12", parent_session_key="", trigger_id="nightly")
    orch, hooks = _orchestrator(info)
    ask = LLMEvent(
        kind=EVENT_PERMISSION_REQUEST,
        request_id="subagent:ab12:tc-1",
        title="write_file",
        tool_input={"path": "digest.md"},
    )
    with patch("personalclaw.trust_mode.is_yolo_active", return_value=False):
        await hooks["on_tool_approval"](ask, "")
    orch.dashboard_state.request_approval.assert_awaited_once()
    assert orch.dashboard_state.request_approval.await_args.kwargs["trigger"] == "nightly"


@pytest.mark.asyncio
async def test_a_call_a_trigger_s_agent_declined_is_noted_under_the_trigger() -> None:
    info = SimpleNamespace(id="ab12", parent_session_key="", trigger_id="nightly")
    orch, hooks = _orchestrator(info)
    with patch("personalclaw.auto_denials.note_unattended") as note:
        await hooks["on_event"]("subagent_auto_denied", info, {"tool": "write_file"})
    note.assert_called_once()
    assert note.call_args.kwargs["trigger"] == "nightly"


@pytest.fixture
def named(tmp_path, monkeypatch):
    """The trigger store the note reads the trigger's name from."""
    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    TriggerStore(base_dir=tmp_path).upsert(_nightly())


def _notes(store) -> list:
    return [i for i in store.items.values() if i.refs.get("auto_denied")]


async def _unanswered(w, approval_id: str) -> bool:
    w.state.approval_window_secs = lambda: 0.05
    return await w.state.request_approval(
        approval_id,
        "subagent",
        "write_file",
        tool_input=json.dumps({"path": "digest.md"}),
        trigger="nightly",
    )


@pytest.mark.asyncio
async def test_a_trigger_s_approval_nobody_answered_names_the_trigger(world, named):  # noqa: F811
    assert await _unanswered(world, "subagent:ab12:tc-1") is False
    (note,) = _notes(world.store)
    assert note.refs["auto_denied"] == "expired"
    assert note.refs["trigger"] == "nightly"
    assert "chat" not in note.refs, "no chat asked, so there is no chat to ask again"
    assert "The trigger “Nightly digest” asked to run write_file" in note.message


@pytest.mark.asyncio
async def test_its_note_is_handled_when_the_rerun_asks_again_and_is_answered(
    world, named  # noqa: F811
):
    await _unanswered(world, "subagent:ab12:tc-1")
    (note,) = _notes(world.store)

    world.state.approval_window_secs = lambda: 60.0
    rerun = asyncio.create_task(
        world.state.request_approval(
            "subagent:cd34:tc-1",
            "subagent",
            "write_file",
            tool_input=json.dumps({"path": "digest.md"}),
            trigger="nightly",
        )
    )
    for _ in range(400):
        if "subagent:cd34:tc-1" in world.state._pending_approvals:
            break
        await asyncio.sleep(0.005)
    assert world.store.items[note.id].status in OPEN_STATUSES, "asked, not yet answered"
    assert world.state.resolve_approval("subagent:cd34:tc-1", True) is True
    assert await asyncio.wait_for(rerun, timeout=5) is True

    row = world.store.items[note.id]
    assert row.status == "handled" and row.refs["retry"] == "approved"

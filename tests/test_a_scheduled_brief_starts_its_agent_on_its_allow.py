"""A scheduled fire of an allowed Invoke Agent trigger starts its agent without asking again.

The owner allowed "Morning brief" to use the Invoke Agent action when she created it. Every fire at
its hour then asked her to approve its own agent ("Approval needed: subagent_run(…)"), so an ask
left unanswered before she woke ran the brief into its deadline. The agent's start now rides the
trigger's Allow (`triggers.grants.allows_its_agent`); these tests hold that for the path a
schedule takes — the clock loop's runner into the store-trigger dispatch — and for a trigger whose
schedule the owner later edited, as the editor saves it.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from test_one_allow_covers_a_triggers_agent import _manager

from personalclaw import approval_grants
from personalclaw.action_providers import invoke_agent_provider
from personalclaw.gateway import GatewayOrchestrator
from personalclaw.sel import sel
from personalclaw.triggers import grants
from personalclaw.triggers.models import Trigger
from personalclaw.triggers.store import TriggerStore

BRIEF = "Summarise my Inbox and my open tasks. Five bullets."


class _State:
    def __init__(self) -> None:
        self.notes: list[dict[str, Any]] = []

    def notify(self, kind, title, body, *, meta=None, raised_by_app=""):
        self.notes.append({"kind": kind, "title": title, "body": body})

    def push_refresh(self, *keys: str) -> None:
        return None

    def broadcast_ws(self, *_a: Any, **_k: Any) -> None:
        return None


def _request(body: dict) -> object:
    class _Req:
        async def json(self) -> dict:
            return body

        def get(self, key: str, default: object = None) -> object:
            return default

    return _Req()


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    monkeypatch.setattr("personalclaw.dashboard.handlers.triggers.config_dir", lambda: tmp_path)
    # Settings → Agent defaults → Approval mode "interactive": nothing else approves the start.
    monkeypatch.setattr(approval_grants, "approval_mode_now", lambda: "interactive")
    return tmp_path


async def _create(body: dict) -> str:
    """Create the trigger as the Triggers page does, the owner allowing it (`confirm: true`)."""
    from personalclaw.dashboard.handlers import triggers as handlers

    resp = await handlers._create_schedule(_State(), body, _request(body))
    payload = json.loads(resp.body.decode())
    assert resp.status == 200, payload
    return str(payload["trigger"]["raw_id"])


async def _fire_on_schedule(home, monkeypatch, raw: str) -> tuple[AsyncMock, Any]:
    """Fire the stored trigger as the clock loop's runner does, into a real subagent manager."""
    asked = AsyncMock(return_value=True)
    manager = _manager(asked)
    monkeypatch.setattr(
        invoke_agent_provider,
        "get_action_services",
        lambda: SimpleNamespace(subagents=manager),
    )
    orch = object.__new__(GatewayOrchestrator)
    orch.dashboard_state = _State()
    with patch("personalclaw.subagent.SubagentManager._run", new=AsyncMock()):
        row = TriggerStore(base_dir=home).get(raw)
        assert row is not None
        await orch._fire_store_trigger(row.trigger, {"trigger_id": raw})
        [info] = list(manager._agents.values())
        await asyncio.wait_for(manager._tasks[info.id], timeout=10)
    return asked, info


def _started_by_its_allow(agent_id: str) -> bool:
    return any(
        r.get("operation") == "subagent_run"
        and r.get("outcome") == "auto_approved_spawn"
        and (r.get("metadata") or {}).get("subagent_id") == agent_id
        and (r.get("metadata") or {}).get("decided_by") == approval_grants.TRIGGER
        for r in sel().recent(500)
    )


_CREATE = {
    "name": "Morning brief",
    "cron": "15 7 * * 1-5",
    "timezone": "America/Toronto",
    "action": {"provider": "invoke-agent", "config": {"task_template": BRIEF}},
    "confirm": True,
}


@pytest.mark.asyncio
async def test_the_scheduled_fire_starts_its_agent_without_asking(home, monkeypatch):
    raw = await _create(_CREATE)
    asked, info = await _fire_on_schedule(home, monkeypatch, raw)
    asked.assert_not_awaited()
    assert info.trigger_id == raw
    assert _started_by_its_allow(info.id)


@pytest.mark.asyncio
async def test_an_edited_schedule_still_starts_its_agent_without_asking(home, monkeypatch):
    """The editor saves the whole form back: its cadence, delivery and the action it rebuilt from
    its agent fields. A save that changed only when it runs keeps the Allow."""
    from personalclaw.dashboard.handlers import triggers as handlers

    raw = await _create(_CREATE)
    edit = {
        "name": "Morning brief",
        "cron": "44 8 * * 1-5",
        "timezone": "America/Toronto",
        "silent": False,
        "strict_schedule": False,
        "channel": "",
        "skip_dates": [],
        "failure_delivery": "inbox",
        "failure_dedupe": False,
        "action": {
            "provider": "invoke-agent",
            "config": {"task_template": BRIEF, "agent": "", "model": "", "approval_mode": ""},
        },
    }
    resp = handlers._update_schedule(_State(), raw, edit)
    assert resp.status == 200, resp.body
    stored = TriggerStore(base_dir=home).get(raw).trigger
    assert stored.spec["expr"] == "44 8 * * 1-5"
    assert grants.missing(stored) == []
    asked, info = await _fire_on_schedule(home, monkeypatch, raw)
    asked.assert_not_awaited()
    assert _started_by_its_allow(info.id)


@pytest.mark.asyncio
async def test_an_edit_that_changes_the_agents_task_asks_again(home, monkeypatch):
    """The control: the Allow covers the action as it was allowed. A new task is a new question."""
    from personalclaw.dashboard.handlers import triggers as handlers

    raw = await _create(_CREATE)
    resp = handlers._update_schedule(
        _State(),
        raw,
        {"action": {"provider": "invoke-agent", "config": {"task_template": "Delete my notes."}}},
    )
    assert resp.status == 200, resp.body
    assert grants.missing(TriggerStore(base_dir=home).get(raw).trigger) == ["invoke-agent"]
    assert grants.allows_its_agent(raw) is False


def test_the_clock_loop_hands_the_stored_row_to_the_dispatch(home, monkeypatch):
    """The clock loop's runner fires the STORED trigger, the one its Allow is written on."""
    import personalclaw.triggers.loop as clock_loop

    seen: list[str] = []

    async def fake_fire(self, trigger, payload, **_kw):
        seen.append(str(trigger.id))

    async def one_tick(store, *, runner, **_kw):
        await runner({"trigger_id": "clock:morning-brief"})

    TriggerStore(base_dir=home).upsert(
        Trigger(id="clock:morning-brief", name="Morning brief", kind="clock")
    )
    monkeypatch.setattr(GatewayOrchestrator, "_fire_store_trigger", fake_fire)
    monkeypatch.setattr(clock_loop, "run_forever", one_tick)
    orch = object.__new__(GatewayOrchestrator)
    orch.sessions = MagicMock()
    asyncio.run(orch._clock_loop())
    assert seen == ["clock:morning-brief"]

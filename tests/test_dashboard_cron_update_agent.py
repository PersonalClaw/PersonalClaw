"""Schedule-trigger PATCH persists the canonical action (agent + approval mode) to the STORE.

A schedule trigger's agent rides the canonical ``action`` (invoke-agent's ``config.agent``), not a
top-level ``agent`` key, and a PATCH must persist that change rather than dropping it.

🔴 REWRITTEN FOR S110. These tests asserted the `crons.update_job(...)` CALL SHAPE —
`kwargs["action"]["config"]["agent"]` on a MagicMock — the legacy fallback the facade's CRUD
retirement deleted. A call-shape assertion proves which function was invoked, never that anything
was stored; the mock happily accepted `action=` for as long as that path existed. These drive the
real store and read the row back, so they assert the mapping actually survives a write.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from personalclaw.dashboard.handlers import triggers as T
from personalclaw.dashboard.handlers.triggers import api_trigger_detail
from personalclaw.triggers.models import Trigger
from personalclaw.triggers.store import TriggerStore


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A tmp home the handler resolves its store through (its own module-level `config_dir`)."""
    import personalclaw.config.loader as loader

    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(T, "config_dir", lambda: tmp_path)
    return tmp_path


def _seed(home, raw_id="abc123", *, agent="", approval_mode=""):
    config = {"task_template": "m"}
    if agent:
        config["agent"] = agent
    if approval_mode:
        config["approval_mode"] = approval_mode
    TriggerStore(base_dir=home).upsert(
        Trigger(
            id=raw_id,
            name="t",
            kind="clock",
            enabled=True,
            spec={"kind": "interval", "interval_secs": 300},
            workflow={"inline": {"provider": "invoke-agent", "config": config}},
        )
    )


def _make_request(body: dict, raw_id: str = "abc123") -> MagicMock:
    mock_state = MagicMock()
    mock_state._sessions = {}
    request = MagicMock()
    request.app = {"state": mock_state}
    request.method = "PUT"
    request.match_info = {"id": f"schedule:{raw_id}"}
    request.json = AsyncMock(return_value=body)
    return request


def _agent_action(agent: str, task: str = "m", approval_mode: str = "") -> dict:
    config = {"task_template": task, "agent": agent}
    if approval_mode:
        config["approval_mode"] = approval_mode
    return {"action": {"provider": "invoke-agent", "config": config}}


def _stored_config(home, raw_id="abc123") -> dict:
    row = TriggerStore(base_dir=home).get(raw_id)
    assert row is not None
    inline = (row.trigger.workflow or {}).get("inline") or {}
    return dict(inline.get("config") or {})


class TestScheduleTriggerUpdateAgent:
    @pytest.mark.asyncio
    async def test_the_agent_is_persisted_inside_the_canonical_action(self, home):
        _seed(home)
        resp = await api_trigger_detail(_make_request(_agent_action("bxt-brain-leader")))
        assert resp.status == 200
        assert _stored_config(home)["agent"] == "bxt-brain-leader"

    @pytest.mark.asyncio
    async def test_the_provider_is_preserved(self, home):
        _seed(home)
        resp = await api_trigger_detail(_make_request(_agent_action("worker")))
        assert resp.status == 200
        inline = (TriggerStore(base_dir=home).get("abc123").trigger.workflow or {})["inline"]
        assert inline["provider"] == "invoke-agent"

    @pytest.mark.asyncio
    async def test_other_fields_are_patched_alongside_the_action(self, home):
        _seed(home)
        body = {"name": "renamed"}
        body.update(_agent_action("bxt-brain-leader", approval_mode="auto"))
        resp = await api_trigger_detail(_make_request(body))
        assert resp.status == 200

        assert TriggerStore(base_dir=home).get("abc123").trigger.name == "renamed"
        config = _stored_config(home)
        assert config["agent"] == "bxt-brain-leader"
        assert config["approval_mode"] == "auto"

    @pytest.mark.asyncio
    async def test_a_name_only_patch_leaves_the_agent_alone(self, home):
        """🔴 The regression this file exists for: a rename must not blank the agent the user set."""
        _seed(home, agent="keepme", approval_mode="auto")
        resp = await api_trigger_detail(_make_request({"name": "renamed"}))
        assert resp.status == 200
        config = _stored_config(home)
        assert config["agent"] == "keepme"
        assert config["approval_mode"] == "auto"
        assert TriggerStore(base_dir=home).get("abc123").trigger.name == "renamed"

    @pytest.mark.asyncio
    async def test_an_unknown_id_returns_404(self, home):
        resp = await api_trigger_detail(
            _make_request(_agent_action("bxt-brain-leader"), raw_id="missing")
        )
        assert resp.status == 404

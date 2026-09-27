"""How long an approval waits is the owner's setting, through the whole config contract.

It was a class constant — two hours — so an approval asked at night was refused before anyone
woke, and nothing but code could change that. `agent.approval_timeout_minutes` now carries it:
the dataclass field and its `_meta`, `load()`, `to_dict()`, the PATCH allowlist and a control in
Settings → Agent defaults. The answer still fails CLOSED whatever the window: past it the call is
denied, and the Inbox says so (`test_denied_calls_say_so_in_the_inbox.py`).
"""

from __future__ import annotations

import json
from dataclasses import fields

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.config.editable import _EDITABLE_CONFIG
from personalclaw.config.loader import AgentConfig, AppConfig


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    return tmp_path


def _write_agent(home, **agent) -> None:
    (home / "config.json").write_text(json.dumps({"agent": agent}), encoding="utf-8")


def test_the_field_is_declared_with_its_help_text():
    (spec,) = [f for f in fields(AgentConfig) if f.name == "approval_timeout_minutes"]
    assert spec.default == 120, "two hours stays the default"
    assert spec.metadata["label"] == "Approval Wait (minutes)"
    assert "Unattended runs never wait" in spec.metadata["help"]


def test_it_round_trips_through_load_and_to_dict(home):
    _write_agent(home, approval_timeout_minutes=600)
    cfg = AppConfig.load()
    assert cfg.agent.approval_timeout_minutes == 600
    assert cfg.to_dict()["agent"]["approval_timeout_minutes"] == 600
    cfg.save()
    assert AppConfig.load().agent.approval_timeout_minutes == 600


@pytest.mark.parametrize(
    ("raw", "read"),
    [
        (0, 120),
        (-5, 120),
        ("soon", 120),
        (True, 120),
        (None, 120),
        (99999, 7 * 24 * 60),
        (45.9, 120),
        (45, 45),
    ],
)
def test_a_hand_edited_value_reads_as_a_real_window(home, raw, read):
    """Never zero: a zero window would deny every approval the moment it was asked."""
    _write_agent(home, approval_timeout_minutes=raw)
    assert AppConfig.load().agent.approval_timeout_minutes == read


def test_the_registry_waits_what_the_setting_says(home, tmp_path):
    from chat_test_helpers import _make_state

    _write_agent(home, approval_timeout_minutes=30)
    assert _make_state(tmp_path).approval_window_secs() == 1800.0


def test_the_allowlist_declares_its_bounds():
    assert _EDITABLE_CONFIG["agent.approval_timeout_minutes"] == {
        "type": "int",
        "min": 1,
        "max": 7 * 24 * 60,
    }


@pytest.mark.asyncio
async def test_the_settings_write_path_saves_it_and_refuses_a_window_out_of_bounds(home):
    from personalclaw.dashboard.handlers import api_personalclaw_config_patch

    app = web.Application()
    app.router.add_patch("/api/config/personalclaw", api_personalclaw_config_patch)
    async with TestClient(TestServer(app)) as client:
        ok = await client.patch(
            "/api/config/personalclaw",
            json={"path": "agent.approval_timeout_minutes", "value": 720},
        )
        assert ok.status == 200, await ok.text()
        stored = json.loads((home / "config.json").read_text(encoding="utf-8"))
        assert stored["agent"]["approval_timeout_minutes"] == 720
        for bad in (0, 7 * 24 * 60 + 1, "a while"):
            r = await client.patch(
                "/api/config/personalclaw",
                json={"path": "agent.approval_timeout_minutes", "value": bad},
            )
            assert r.status == 400, (bad, await r.text())
    assert AppConfig.load().agent.approval_timeout_minutes == 720

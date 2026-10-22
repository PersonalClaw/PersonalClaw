"""'Keep my question in view' is a chat setting the gateway keeps (#3820's Oct 14 addition).

An opt-in, off by default: while a reply taller than the window streams, the reading column pins a
one-line banner with the owner's latest question. It is a display preference kept server-side, as
every other chat display preference is, so it holds across the owner's browsers: the config
dataclass with its metadata, ``load()``, ``to_dict()``, the dashboard config route's GET and its
PUT allowlist (a boolean, refused as anything else), and the Settings control.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.config import loader
from personalclaw.config.loader import AppConfig

WEB = Path(__file__).resolve().parents[1] / "web" / "src"


def _home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, data: dict) -> Path:
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps(data))
    monkeypatch.setattr("personalclaw.config.loader.config_path", lambda: cfg_path)
    return cfg_path


def test_it_is_off_by_default_and_said_in_the_config(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch, {})
    cfg = AppConfig.load()
    assert cfg.dashboard.keep_question_in_view is False
    assert cfg.to_dict()["dashboard"]["keep_question_in_view"] is False
    field = loader.DashboardConfig.__dataclass_fields__["keep_question_in_view"]
    assert field.metadata.get("label") or field.metadata, "it carries its label and help"


def test_it_round_trips_through_the_file(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch, {"dashboard": {"keep_question_in_view": True}})
    cfg = AppConfig.load()
    assert cfg.dashboard.keep_question_in_view is True
    cfg.save()
    assert AppConfig.load().dashboard.keep_question_in_view is True


@pytest.mark.asyncio
async def test_the_dashboard_config_route_reads_and_writes_it(tmp_path, monkeypatch):
    from personalclaw.dashboard.handlers.files import api_dashboard_config

    _home(tmp_path, monkeypatch, {})
    app = web.Application()
    app.router.add_get("/api/dashboard/config", api_dashboard_config)
    app.router.add_put("/api/dashboard/config", api_dashboard_config)
    async with TestClient(TestServer(app)) as client:

        async def kept():
            return (await (await client.get("/api/dashboard/config")).json())[
                "keep_question_in_view"
            ]

        assert await kept() is False
        resp = await client.put("/api/dashboard/config", json={"keep_question_in_view": True})
        assert resp.status == 200
        assert await kept() is True
        resp = await client.put("/api/dashboard/config", json={"keep_question_in_view": "yes"})
        assert resp.status == 400


def test_the_settings_panel_has_its_control_and_the_api_its_field():
    panel = (WEB / "pages" / "settings" / "ChatPanel.tsx").read_text(encoding="utf-8")
    assert "keep_question_in_view" in panel
    api = (WEB / "lib" / "api.ts").read_text(encoding="utf-8")
    assert "keep_question_in_view: boolean" in api

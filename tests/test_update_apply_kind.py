"""Per-kind self-update apply routing (plan 34 S4 T4.3).

Verifies POST /api/update branches on the detected install kind: container and
desktop return a structured instructions payload (no apply runs); pip/git route
into their apply pipelines. Uses a mocked request + a minimal state stub so no
real gateway, subprocess, or network is involved (hermetic).
"""

from __future__ import annotations

import json

import pytest
from aiohttp.test_utils import make_mocked_request

from personalclaw.dashboard.handlers import updates as upd


class _StateStub:
    def __init__(self) -> None:
        self._background_tasks: set = set()
        self.refreshes: list = []
        self.progress: list = []

    def push_refresh(self, *kinds: str) -> None:
        self.refreshes.extend(kinds)

    def push_update_progress(self, step: str, detail: str = "") -> None:
        self.progress.append((step, detail))


def _req() -> object:
    req = make_mocked_request("POST", "/api/update")
    req.app["state"] = _StateStub()
    return req


@pytest.fixture(autouse=True)
def _reset_flight():
    upd._apply_in_flight = False
    yield
    upd._apply_in_flight = False


@pytest.mark.asyncio
async def test_container_returns_instructions(monkeypatch) -> None:
    monkeypatch.setenv("PERSONALCLAW_INSTALL_KIND", "container")

    async def _fake_status(_cur):
        return {
            "kind": "container",
            "apply_method": "instructions",
            "instructions": ["docker compose pull", "docker compose up -d"],
        }

    monkeypatch.setattr(
        "personalclaw.dashboard.handlers.updates_kind.build_update_status", _fake_status
    )
    resp = await upd.api_update_apply(_req())
    body = json.loads(resp.body.decode())
    assert resp.status == 200
    assert body["status"] == "instructions"
    assert body["kind"] == "container"
    assert body["instructions"] == ["docker compose pull", "docker compose up -d"]
    # No apply ran, so the in-flight guard was never claimed.
    assert upd._apply_in_flight is False


@pytest.mark.asyncio
async def test_desktop_returns_instructions(monkeypatch) -> None:
    monkeypatch.setenv("PERSONALCLAW_INSTALL_KIND", "desktop")

    async def _fake_status(_cur):
        return {"kind": "desktop", "apply_method": "desktop_delegate", "instructions": []}

    monkeypatch.setattr(
        "personalclaw.dashboard.handlers.updates_kind.build_update_status", _fake_status
    )
    resp = await upd.api_update_apply(_req())
    body = json.loads(resp.body.decode())
    assert resp.status == 200
    assert body["kind"] == "desktop"
    assert body["status"] == "instructions"


@pytest.mark.asyncio
async def test_pip_kind_routes_to_pip_update(monkeypatch) -> None:
    monkeypatch.delenv("PERSONALCLAW_INSTALL_KIND", raising=False)
    monkeypatch.delenv("PERSONALCLAW_PROJECT_DIR", raising=False)

    called = {"pip": False}

    async def _fake_pip(request, state):
        from aiohttp import web

        called["pip"] = True
        return web.json_response({"ok": True, "status": "updating", "kind": "pip"})

    monkeypatch.setattr(upd, "_apply_pip_update", _fake_pip)
    resp = await upd.api_update_apply(_req())
    body = json.loads(resp.body.decode())
    assert called["pip"] is True
    assert body["kind"] == "pip"

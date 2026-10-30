"""The providers API names each capability an app contributes, and reads one by its id (#3841).

``GET /api/providers`` had one row per provider, every row named only after its app, so two
providers of one app could not be told apart, and ``GET /api/providers/{name}/schema`` returned the
first one's form whichever was meant. Each row now carries the capability's ``id``, ``key`` and
``label``, and ``GET /api/providers/{name}/capabilities/{id}`` reads that one. The capability gets a
path segment of its own, so it can never take the path of a route added under
``/api/providers/{name}`` later.

The read is child detail in the sub-resource census: the registry holds each record under
``<app>/<id>`` and the route reads the one exact key, so a ghost app, an id the app doesn't
declare and another app's id under this app's name are each a 404, never an empty answer. An app
caller is held to its own name before the handler runs, so a refused read opens nothing.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.apps.manifest import AppManifest
from personalclaw.providers import registry as registry_module

pytestmark = pytest.mark.security

_GIT = {"type": "object", "properties": {"remote": {"type": "string"}}}
_FOLDER = {"type": "object", "properties": {"folder": {"type": "string"}}}


def _app(name: str, **fields: Any) -> AppManifest:
    return AppManifest.from_dict(
        {"name": name, "version": "1.0.0", "displayName": name.title(), "description": "An app."}
        | fields
    )


@pytest.fixture
def registry():
    registry_module.reset_provider_registry()
    reg = registry_module.get_provider_registry()
    reg.register(
        _app(
            "twin",
            providers=[
                {
                    "type": "sync",
                    "implementation": "a:create",
                    "id": "git",
                    "label": "Sync through a git remote",
                    "settingsSchema": _GIT,
                },
                {
                    "type": "sync",
                    "implementation": "b:create",
                    "id": "folder",
                    "label": "Sync through a shared folder",
                    "description": "Any folder both computers can see.",
                    "settingsSchema": _FOLDER,
                },
            ],
        )
    )
    reg.register(_app("solo", provider={"type": "channel", "implementation": "c:create"}))
    try:
        yield reg
    finally:
        registry_module.reset_provider_registry()


def _gateway(caller: str = "") -> web.Application:
    """The provider routes behind the real app permission middleware, asked by the owner or by
    the app *caller*."""
    from personalclaw.dashboard.server import app_permission_middleware
    from personalclaw.providers.instance_routes import register_instance_routes
    from personalclaw.providers.routes import register_routes

    @web.middleware
    async def identity(request, handler):
        request["user"] = "owner"
        request["app"] = caller
        return await handler(request)

    app = web.Application(middlewares=[identity, app_permission_middleware])
    register_routes(app)
    register_instance_routes(app)
    return app


@pytest.mark.asyncio
async def test_each_row_names_its_capability(registry) -> None:
    async with TestClient(TestServer(_gateway())) as client:
        rows = (await (await client.get("/api/providers")).json())["providers"]
    mine = {r["key"]: r for r in rows if r["name"] in ("twin", "solo")}
    assert sorted(mine) == ["solo/channel", "twin/folder", "twin/git"]
    assert (mine["twin/folder"]["id"], mine["twin/folder"]["label"]) == (
        "folder",
        "Sync through a shared folder",
    )
    assert mine["twin/folder"]["provider"]["description"] == "Any folder both computers can see."
    assert mine["twin/folder"]["description"] == "An app.", "the row's description stays the app's"
    assert (mine["solo/channel"]["id"], mine["solo/channel"]["label"]) == ("channel", "")
    assert all({"id", "key", "label"} <= set(r) for r in rows), "every row names its capability"


@pytest.mark.asyncio
async def test_one_capability_is_read_by_its_id(registry) -> None:
    async with TestClient(TestServer(_gateway())) as client:
        resp = await client.get("/api/providers/twin/capabilities/folder")
        assert resp.status == 200
        body = await resp.json()
        assert (body["key"], body["label"], body["schema"]) == (
            "twin/folder",
            "Sync through a shared folder",
            _FOLDER,
        ), "the capability asked for, not the app's first"
        assert body["enabled"] is False and "state" in body["availability"]
        git = await (await client.get("/api/providers/twin/capabilities/git")).json()
        assert git["schema"] == _GIT
        old = await (await client.get("/api/providers/solo/capabilities/channel")).json()
        assert old["key"] == "solo/channel", "an app written before ids is read by its derived id"


@pytest.mark.asyncio
async def test_an_unknown_capability_or_app_is_not_found(registry) -> None:
    async with TestClient(TestServer(_gateway())) as client:
        resp = await client.get("/api/providers/twin/capabilities/inbox")
        assert resp.status == 404
        assert "'twin' has no capability 'inbox'" in (await resp.json())["error"]
        assert (await client.get("/api/providers/gone/capabilities/git")).status == 404


@pytest.mark.asyncio
async def test_the_per_app_schema_read_is_gone(registry) -> None:
    async with TestClient(TestServer(_gateway())) as client:
        assert (await client.get("/api/providers/twin/schema")).status == 404
        config = await client.get("/api/providers/twin/config")
        assert config.status == 200 and "config" in await config.json(), "config is the app's"


def test_the_capability_route_holds_an_app_to_its_own() -> None:
    from personalclaw.apps.permissions import ROUTE_AUTHZ, AppMay, OwnedTarget

    row = ROUTE_AUTHZ["GET /api/providers/{name}/capabilities/{capability}"]
    assert isinstance(row, AppMay)
    assert OwnedTarget("name", app=True) in row.owns
    assert "GET /api/providers/{name}/schema" not in ROUTE_AUTHZ


@pytest.fixture
def installed(tmp_path: Path, monkeypatch) -> Path:
    """``twin`` and ``solo`` installed and enabled in a scratch home, each allowed the providers
    API, so only the route's own rule can refuse one."""
    from personalclaw.apps import manager
    from personalclaw.config import loader

    for name in ("twin", "solo"):
        folder = tmp_path / "apps" / name
        folder.mkdir(parents=True)
        manifest = {"name": name, "version": "1.0.0", "displayName": name, "description": "x"}
        (folder / "app.json").write_text(
            json.dumps({**manifest, "permissions": {"api": ["/api/providers"]}})
        )
        (folder / "installed.json").write_text(
            json.dumps({"name": name, "version": "1.0.0", "enabled": True})
        )
    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(manager, "config_dir", lambda: tmp_path)
    monkeypatch.setattr("personalclaw.sel.sel", lambda: MagicMock())
    return tmp_path


@pytest.mark.asyncio
async def test_the_capability_is_the_named_apps_before_anything_is_read(
    registry, installed, monkeypatch
) -> None:
    from personalclaw.providers import routes

    looked_up: list[tuple[str, Any]] = []
    boards: list[str] = []
    real_get, real_board = registry.get, routes.get_availability_board
    monkeypatch.setattr(
        registry, "get", lambda name, cap=None: looked_up.append((name, cap)) or real_get(name, cap)
    )
    monkeypatch.setattr(
        routes, "get_availability_board", lambda: boards.append("read") or real_board()
    )

    async with TestClient(TestServer(_gateway("solo"))) as client:
        resp = await client.get("/api/providers/twin/capabilities/folder")
        assert resp.status == 403
        assert "'twin' is not this app" in await resp.text()
        assert (looked_up, boards) == ([], []), "a refused read opens no record and no board"
        own = await client.get("/api/providers/solo/capabilities/channel")
        assert own.status == 200 and (await own.json())["key"] == "solo/channel"
    async with TestClient(TestServer(_gateway("twin"))) as client:
        assert (await client.get("/api/providers/twin/capabilities/folder")).status == 200

    looked_up.clear()
    boards.clear()
    async with TestClient(TestServer(_gateway())) as client:
        resp = await client.get("/api/providers/twin/capabilities/channel")
        assert resp.status == 404, "solo's id under twin's name is not twin's capability"
        assert "'twin' has no capability 'channel'" in (await resp.json())["error"]
    assert looked_up == [("twin", "channel")] and boards == [], "nothing is read past the lookup"

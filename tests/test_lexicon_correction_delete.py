"""A learned speech correction can be deleted — by you, and only by you.

Settings → Voice → Learned corrections listed each fix with an Always/Suggest toggle and no way to
remove it: ``lexicon/handlers.py`` registered GET/POST/PATCH for corrections and no DELETE. A wrong
correction set to Always rewrites that word every time you dictate it, into the message your agent
reads, and the only way out was ``POST /api/lexicon/reset`` — which throws away every term and
every correction you ever taught it.

The route is the owner's (``ROUTE_AUTHZ``): a correction is something you taught the transcriber,
and an app that could remove it — or wipe them all through reset — could undo that work.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.apps import manager
from personalclaw.lexicon import handlers as lexicon_handlers
from personalclaw.lexicon.service import LexiconService
from personalclaw.lexicon.store import LexiconStore

APP = "probe-lexicon-app"


@pytest.fixture
def service(tmp_path, monkeypatch) -> LexiconService:
    svc = LexiconService(LexiconStore(str(tmp_path / "lexicon.db")))
    monkeypatch.setattr(lexicon_handlers, "get_lexicon_service", lambda: svc)
    return svc


def _lexicon_app() -> web.Application:
    app = web.Application()
    lexicon_handlers.register_lexicon_routes(app)
    return app


@pytest.mark.asyncio
async def test_deleting_a_correction_removes_it_and_its_rewrite(service) -> None:
    service.learn_correction("cube control", "kubectl", always=True)
    service.learn_correction("post gress", "postgres")
    [corr] = [c for c in service.list_corrections() if c.heard == "cube control"]
    assert service.store.auto_corrections() == {"cube control": "kubectl"}

    async with TestClient(TestServer(_lexicon_app())) as client:
        resp = await client.delete(f"/api/lexicon/corrections/{corr.id}")
        assert resp.status == 200, await resp.text()
        assert await resp.json() == {"ok": True}
        listed = await (await client.get("/api/lexicon/corrections")).json()

    assert [c["heard"] for c in listed["corrections"]] == ["post gress"], "only that one goes"
    # The rewrite goes with it: dictating "cube control" is no longer turned into "kubectl".
    assert service.store.auto_corrections() == {}


@pytest.mark.asyncio
async def test_deleting_a_correction_that_is_not_there_says_so(service) -> None:
    async with TestClient(TestServer(_lexicon_app())) as client:
        resp = await client.delete("/api/lexicon/corrections/corr_missing")
        assert resp.status == 404
        assert (await resp.json())["error"] == "correction not found"


# ── only the owner ─────────────────────────────────────────────────────────────────


def _install(home: Path, name: str, permissions: dict[str, Any]) -> None:
    appdir = home / "apps" / name
    appdir.mkdir(parents=True, exist_ok=True)
    (appdir / "app.json").write_text(
        json.dumps(
            {
                "name": name,
                "version": "1.0.0",
                "displayName": name,
                "description": "x",
                "permissions": permissions,
            }
        ),
        encoding="utf-8",
    )
    (appdir / "installed.json").write_text(
        json.dumps({"name": name, "version": "1.0.0", "enabled": True}), encoding="utf-8"
    )


@contextmanager
def _home(tmp_path: Path) -> Iterator[Path]:
    with (
        patch("personalclaw.config.loader.config_dir", return_value=tmp_path),
        patch.object(manager, "config_dir", return_value=tmp_path),
    ):
        yield tmp_path


def _gateway(app_name: str, routes: list[tuple[str, str]]) -> web.Application:
    """The REAL app-permission middleware in front of stub handlers at the real route templates."""
    from personalclaw.dashboard.server import app_permission_middleware

    @web.middleware
    async def identity(request: web.Request, handler: Any) -> web.StreamResponse:
        request["user"] = "owner"
        if app_name:
            request["app"] = app_name
        return await handler(request)

    async def reached(request: web.Request) -> web.Response:
        return web.json_response({"reached": True})

    app = web.Application(middlewares=[identity, app_permission_middleware])
    for method, template in routes:
        app.router.add_route(method, template, reached)
    return app


@pytest.fixture
def sel_rows():
    fake = MagicMock()
    with patch("personalclaw.sel.sel", return_value=fake):
        yield fake.log_api_access


async def _call(app_name: str, method: str, template: str, path: str) -> int:
    async with TestClient(TestServer(_gateway(app_name, [(method, template)]))) as client:
        resp = await client.request(method, path, json={})
        return resp.status


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method,template,path",
    [
        ("DELETE", "/api/lexicon/corrections/{id}", "/api/lexicon/corrections/corr_1"),
        # Reset deletes every correction at once; left open to an app, it would make the
        # owner-only delete above a formality.
        ("POST", "/api/lexicon/reset", "/api/lexicon/reset"),
    ],
)
async def test_an_app_that_declared_the_lexicon_cannot_remove_your_corrections(
    tmp_path, sel_rows, method, template, path
) -> None:
    with _home(tmp_path) as home:
        _install(home, APP, {"api": ["/api/lexicon"]})
        assert await _call(APP, method, template, path) == 403
        assert await _call("", method, template, path) == 200, "the owner keeps it"
    denied = [
        c
        for c in sel_rows.call_args_list
        if c.kwargs.get("caller") == f"app:{APP}" and c.kwargs.get("outcome") == "denied"
    ]
    assert denied, "the refusal is audited under the APP's identity"
    assert "owner-only" in denied[0].kwargs["error"]


@pytest.mark.asyncio
async def test_reading_your_corrections_is_still_the_allowlists_business(tmp_path) -> None:
    with _home(tmp_path) as home:
        _install(home, APP, {"api": ["/api/lexicon"]})
        status = await _call(APP, "GET", "/api/lexicon/corrections", "/api/lexicon/corrections")
    assert status == 200

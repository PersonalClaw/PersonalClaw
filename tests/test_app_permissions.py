"""App permission enforcement (A5) — server-side defense-in-depth.

Covers the PermissionChecker decision logic (api prefix/wildcard, events,
mcpTools, memory tiers, coarse flags) and the enforcement middleware: an
app-identified request to an undeclared API path is 403'd, a declared one
passes, the app's own backend-proxy path is always allowed, and an owner request
(no app identity) is unaffected.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from unittest.mock import patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.apps import manager
from personalclaw.apps.manifest import Permissions
from personalclaw.apps.permissions import PermissionChecker, checker_for


def _checker(**perms) -> PermissionChecker:
    return PermissionChecker(app_name="demo", permissions=Permissions(**perms))


class TestCheckerLogic:
    def test_api_prefix_allows_declared_only(self):
        c = _checker(api=["/api/notes", "/api/tags/*"])
        assert c.can_use_api("/api/notes")
        assert c.can_use_api("/api/notes/123")  # under the declared prefix
        assert c.can_use_api("/api/tags/anything")  # wildcard
        assert not c.can_use_api("/api/secrets")  # undeclared

    def test_no_api_scope_denies_all_gateway_api(self):
        c = _checker()
        assert not c.can_use_api("/api/notes")

    def test_own_backend_proxy_always_allowed(self):
        c = _checker()  # no api scope at all
        assert c.can_use_api("/apps/demo/api/anything")
        assert not c.can_use_api("/apps/other/api/x")  # not its own backend

    def test_events_and_mcptools(self):
        c = _checker(events=["note.*"], mcpTools=["fs_read"])
        assert c.can_use_event("note.created")
        assert not c.can_use_event("chat.message")
        assert c.can_use_mcp_tool("fs_read")
        assert not c.can_use_mcp_tool("fs_write")

    def test_memory_tiers(self):
        assert not _checker(memory="").can_use_memory("app-scoped")
        appc = _checker(memory="app-scoped")
        assert appc.can_use_memory("app-scoped") and not appc.can_use_memory("shared")
        sharedc = _checker(memory="shared")
        assert sharedc.can_use_memory("app-scoped") and sharedc.can_use_memory("shared")

    def test_coarse_flags(self):
        c = _checker(cron=True, network=True, storage=False)
        assert c.can_use_cron() and c.can_use_network() and not c.can_use_storage()


# ── middleware enforcement (HTTP) ──


async def _ok(request: web.Request) -> web.Response:
    return web.json_response({"ok": True})


@asynccontextmanager
async def _client(tmp_path, *, app_identity: str, permissions: dict):
    """A minimal app with the A5 middleware, simulating an app-scoped request by
    setting request['app'] in a stub middleware ahead of enforcement."""
    name = "demo"
    appdir = tmp_path / "apps" / name
    appdir.mkdir(parents=True)
    (appdir / "app.json").write_text(
        json.dumps(
            {
                "name": name,
                "version": "1.0.0",
                "displayName": "Demo",
                "description": "x",
                "permissions": permissions,
            }
        ),
        encoding="utf-8",
    )

    @web.middleware
    async def stub_identity(request, handler):
        request["app"] = app_identity
        return await handler(request)

    # Re-create the enforcement middleware standalone (mirrors server.py).
    @web.middleware
    async def app_permission_middleware(request, handler):
        app_name = request.get("app", "")
        if app_name and request.path.startswith(("/api/", "/apps/")):
            c = checker_for(app_name)
            if c is not None and not c.can_use_api(request.path):
                raise web.HTTPForbidden(text="denied")
        return await handler(request)

    with (
        patch("personalclaw.config.loader.config_dir", return_value=tmp_path),
        patch.object(manager, "config_dir", return_value=tmp_path),
    ):
        app = web.Application(middlewares=[stub_identity, app_permission_middleware])
        app.router.add_get("/api/notes", _ok)
        app.router.add_get("/api/secrets", _ok)
        app.router.add_get("/apps/demo/api/ping", _ok)
        async with TestClient(TestServer(app)) as client:
            yield client


@pytest.mark.asyncio
async def test_middleware_allows_declared_denies_undeclared(tmp_path):
    async with _client(tmp_path, app_identity="demo", permissions={"api": ["/api/notes"]}) as c:
        assert (await c.get("/api/notes")).status == 200
        assert (await c.get("/api/secrets")).status == 403
        assert (await c.get("/apps/demo/api/ping")).status == 200  # own backend


@pytest.mark.asyncio
async def test_middleware_no_app_identity_passes(tmp_path):
    # Empty app identity = owner/dashboard request → enforcement is a no-op.
    async with _client(tmp_path, app_identity="", permissions={"api": []}) as c:
        assert (await c.get("/api/secrets")).status == 200


def test_checker_for_unknown_app_is_none(tmp_path):
    with (
        patch("personalclaw.config.loader.config_dir", return_value=tmp_path),
        patch.object(manager, "config_dir", return_value=tmp_path),
    ):
        assert checker_for("ghost") is None
        assert checker_for("") is None


# ── AuthMode.NONE app-identity adoption (dev-mode sandbox parity) ──
# In none-mode token_auth is skipped entirely, so request["app"] was never set
# from an app-scoped Bearer token — which silently disabled the WHOLE app
# permission sandbox in dev mode (any app token reached any /api path). The
# _dev_user_middleware must adopt the token's app claim exactly like token_auth
# does, so enforcement behaves identically in both auth modes.


@asynccontextmanager
async def _none_mode_client(tmp_path, *, permissions: dict):
    """Mirror server.py's none-mode chain: _dev_user_middleware (with the app-claim
    adoption) + the real enforcement middleware."""
    from personalclaw.dashboard.token_auth import validate_token_with_app

    name = "demo"
    appdir = tmp_path / "apps" / name
    appdir.mkdir(parents=True)
    (appdir / "app.json").write_text(
        json.dumps(
            {
                "name": name,
                "version": "1.0.0",
                "displayName": "Demo",
                "description": "x",
                "permissions": permissions,
            }
        ),
        encoding="utf-8",
    )

    @web.middleware
    async def dev_user_middleware(request, handler):
        request["user"] = request.get("user") or "dev-local"
        if not request.get("app"):
            app_token = ""
            _auth = request.headers.get("Authorization", "")
            if _auth.startswith("Bearer "):
                app_token = _auth[7:].strip()
            if not app_token:
                app_token = request.query.get("app_token", "")
            if app_token:
                a_valid, _u, _r, a_app = validate_token_with_app(app_token)
                if a_valid and a_app:
                    request["app"] = a_app
        return await handler(request)

    @web.middleware
    async def app_permission_middleware(request, handler):
        app_name = request.get("app", "")
        if app_name and request.path.startswith(("/api/", "/apps/")):
            c = checker_for(app_name)
            if c is not None and not c.can_use_api(request.path):
                raise web.HTTPForbidden(text="denied")
        return await handler(request)

    with (
        patch("personalclaw.config.loader.config_dir", return_value=tmp_path),
        patch.object(manager, "config_dir", return_value=tmp_path),
    ):
        app = web.Application(middlewares=[dev_user_middleware, app_permission_middleware])
        app.router.add_get("/api/notes", _ok)
        app.router.add_get("/api/secrets", _ok)
        async with TestClient(TestServer(app)) as client:
            yield client


@pytest.mark.asyncio
async def test_none_mode_adopts_app_claim_and_enforces(tmp_path):
    from personalclaw.dashboard.token_auth import generate_token

    async with _none_mode_client(tmp_path, permissions={"api": ["/api/notes"]}) as c:
        token = generate_token("dev-local", ttl_seconds=60, app="demo")
        hdr = {"Authorization": f"Bearer {token}"}
        # App-scoped request: declared path passes, undeclared is 403.
        assert (await c.get("/api/notes", headers=hdr)).status == 200
        assert (await c.get("/api/secrets", headers=hdr)).status == 403
        # ?app_token= (the WS handshake form) is adopted too.
        assert (await c.get(f"/api/secrets?app_token={token}")).status == 403
        # No token → owner request, unrestricted.
        assert (await c.get("/api/secrets")).status == 200
        # Garbage token → no identity adopted (fails closed to owner, not crash).
        assert (await c.get("/api/secrets", headers={"Authorization": "Bearer junk"})).status == 200

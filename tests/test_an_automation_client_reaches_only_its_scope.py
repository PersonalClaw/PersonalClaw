"""An automation client reaches only what its scope allows (scoped-pairing design §3, C2).

A scoped session's every request meets ``device_scopes.device_scope_middleware``, the gate after
token auth: one table (``DEVICE_SCOPE_ROUTES``) names every route a scope may reach, keyed on
the canonical route template, and anything else — a route outside the scope, one the table holds
as outside every scope, a 404, a 405 — gets the one 403 body, which names the scopes and never the
route. A body or query that gives consent is refused on every row but those that say otherwise.
The shell, the status cut and the loops reads are this release's rows; every steer, answer and
approval stays outside until the client's own principal exists.
"""

from __future__ import annotations

import json
import time
from typing import Any
from unittest.mock import MagicMock

import pytest
from aiohttp import DummyCookieJar, web
from aiohttp.test_utils import TestClient, TestServer
from signed_in_sessions import ORIGIN, sign_in

from personalclaw.auth import pairing
from personalclaw.dashboard import session_store as ss
from personalclaw.dashboard import token_auth
from personalclaw.dashboard.handlers import auth as auth_h
from personalclaw.dashboard.handlers import devices as devices_h

PORT = 10000
COOKIE = f"pc_token_{PORT}"
DAY = 86400


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    import personalclaw.config.loader as loader

    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(pairing, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(ss, "config_dir", lambda: tmp_path, raising=False)
    monkeypatch.setattr("personalclaw.loop.files.config_dir", lambda: tmp_path)
    monkeypatch.delenv("PERSONALCLAW_BYPASS_LOCAL_NETWORKS", raising=False)
    (tmp_path / "config.json").write_text(json.dumps({"auth": {}}), encoding="utf-8")
    token_auth.use_persistent_secret()
    token_auth.revoke_all_sessions()
    auth_h.reset_lockouts()
    yield tmp_path
    token_auth.revoke_all_sessions()
    auth_h.reset_lockouts()


@pytest.fixture()
def sel_events(monkeypatch) -> list[dict[str, Any]]:
    import personalclaw.sel as sel_mod

    events: list[dict[str, Any]] = []
    recorder = MagicMock()
    recorder.log_api_access.side_effect = lambda **kw: events.append(kw)
    monkeypatch.setattr(sel_mod, "sel", lambda: recorder)
    return events


def _scopes():
    from personalclaw.dashboard import device_scopes

    return device_scopes


def _scoped(scope=("loops",)):
    device = ss.DeviceInfo(id=ss.new_device_id(), name="Nightly runner", kind="automation")
    return token_auth.mint_session(
        devices_h.PAIRED_DEVICE_USER,
        30 * DAY,
        issuer=ss.ISSUER_PAIR,
        device=device,
        bound=True,
        scope=tuple(scope),
    )


def _headers(minted, **extra: str) -> dict[str, str]:
    return {
        "Cookie": f"{COOKIE}={minted.token}",
        "X-PersonalClaw-Pair": minted.pair,
        "Origin": ORIGIN,
        **extra,
    }


#: Every route these tests serve, at the gateway's own templates. Each answers which ran, so a
#: test sees whether the gate let the request through.
SERVED: list[tuple[str, str]] = [
    ("GET", "/api/loops"),
    ("GET", "/api/loops/{id}"),
    ("GET", "/api/loops/{id}/report"),
    ("PATCH", "/api/loops/{id}"),
    ("POST", "/api/loops/{id}/nudge"),
    ("POST", "/api/loops/{id}/merge"),
    ("DELETE", "/api/loops/{id}"),
    ("POST", "/api/loops"),
    ("GET", "/api/approvals"),
    ("POST", "/api/approvals/{id}/{action}"),
    ("GET", "/api/settings"),
    ("GET", "/api/devices"),
    ("POST", "/api/devices/pair/start"),
    ("POST", "/api/auth/confirm"),
    ("POST", "/api/logout"),
    ("GET", "/api/chat/sessions"),
    ("GET", "/api/chat/sessions/{session}"),
    ("POST", "/api/auth/logout"),
    ("GET", "/api/auth-status"),
    ("GET", "/api/design/tokens/default"),
    # Two routes that mint a credential: an app's token and a device code. Every other is
    # outside every family, so no row admits it.
    ("POST", "/api/apps/{name}/token"),
    ("POST", "/api/auth/enroll/start"),
]


def _gated_app(ran: list[str]) -> web.Application:
    """The production order: token auth, then the scope gate, then consent and the boundary."""
    from personalclaw.auth.modes import AuthConfig, AuthMode
    from personalclaw.dashboard.consent_ask import consent_ask_middleware
    from personalclaw.dashboard.request_boundary import request_boundary_middleware

    app = web.Application(
        middlewares=[
            token_auth.token_auth_middleware(port=PORT),
            _scopes().device_scope_middleware,
            consent_ask_middleware,
            request_boundary_middleware(),
        ]
    )
    app["port"] = PORT
    app["allowed_origins"] = {ORIGIN}
    app["auth_cfg"] = AuthConfig(mode=AuthMode.LOCAL_TOKEN)

    def served(method: str, template: str):
        async def handler(request: web.Request) -> web.Response:
            ran.append(f"{method} {template}")
            return web.json_response({"ran": f"{method} {template}"})

        return handler

    for method, template in SERVED:
        app.router.add_route(method, template, served(method, template))
    return app


def _loop_row() -> str:
    from personalclaw.loop import store
    from personalclaw.loop.loop import Loop

    return store.create(Loop(id="", name="nightly docs", kind="general", task="tidy the docs")).id


async def _call(app, method: str, path: str, minted, **kw) -> tuple[int, Any, bytes]:
    async with TestClient(TestServer(app), cookie_jar=DummyCookieJar()) as client:
        resp = await client.request(method, path, headers=_headers(minted), **kw)
        body = await resp.read()
        return resp.status, resp.headers, body


# ── In scope it works; out of it, the one refusal ───────────────────────


@pytest.mark.asyncio
async def test_a_loops_read_on_a_loop_row_reaches_its_route() -> None:
    ran: list[str] = []
    lid = _loop_row()
    status, _h, body = await _call(_gated_app(ran), "GET", f"/api/loops/{lid}", _scoped())
    assert status == 200, body
    assert ran == ["GET /api/loops/{id}"]


@pytest.mark.asyncio
async def test_a_route_outside_the_scope_is_refused_before_it_runs(sel_events) -> None:
    ran: list[str] = []
    lid = _loop_row()
    status, headers, body = await _call(
        _gated_app(ran), "GET", f"/api/loops/{lid}", _scoped(("status",))
    )
    assert status == 403 and ran == []
    error = json.loads(body)["error"]
    assert error["code"] == "device_scope_refused"
    assert "status" in error["message"] and "/api/" not in error["message"]
    assert "X-Auth-Required" not in headers


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("PATCH", "/api/loops/{lid}"),
        ("POST", "/api/loops/{lid}/nudge"),
        ("POST", "/api/loops/{lid}/merge"),
        ("DELETE", "/api/loops/{lid}"),
        ("POST", "/api/loops"),
        ("GET", "/api/approvals"),
        ("POST", "/api/approvals/a1/approve"),
        ("GET", "/api/settings"),
        ("GET", "/api/devices"),
        ("POST", "/api/devices/pair/start"),
        ("POST", "/api/auth/confirm"),
        ("GET", "/api/chat/sessions"),
        ("GET", "/api/chat/sessions/loop-x"),
        ("POST", "/api/apps/mail/token"),
        ("POST", "/api/auth/enroll/start"),
    ],
)
async def test_what_no_scope_in_this_release_reaches_is_refused(method, path) -> None:
    """Steer, answer, approvals, create, land, devices, pairing, renew, settings and every chat:
    outside, even to a session holding every grantable scope. (The CLI's sign-everyone-out,
    ``POST /api/logout``, authenticates itself with the local secret, which no client holds.)"""
    ran: list[str] = []
    lid = _loop_row()
    minted = _scoped(_scopes().GRANTABLE)
    status, _h, body = await _call(_gated_app(ran), method, path.format(lid=lid), minted)
    assert status == 403, (method, path, body)
    assert json.loads(body)["error"]["code"] == "device_scope_refused"
    assert ran == []


@pytest.mark.asyncio
async def test_a_404_a_405_and_a_refused_route_give_the_same_bytes() -> None:
    """The refusal gives a client nothing to probe the router with (F13)."""
    ran: list[str] = []
    app = _gated_app(ran)
    minted = _scoped(("status",))
    lid = _loop_row()
    bodies = set()
    for method, path in (
        ("GET", "/api/no-such-route"),
        ("PUT", "/api/auth-status"),
        ("GET", f"/api/loops/{lid}"),
        ("GET", "/api/settings"),
    ):
        status, headers, body = await _call(app, method, path, minted)
        assert status == 403, (method, path)
        bodies.add((body, headers.get("Content-Type")))
    assert len(bodies) == 1, bodies
    assert ran == []


@pytest.mark.asyncio
async def test_a_run_backed_id_is_refused() -> None:
    """A loop id that is no loops-table row (a workflow run started as a loop) fails closed."""
    ran: list[str] = []
    status, _h, _b = await _call(_gated_app(ran), "GET", "/api/loops/run-abc123", _scoped())
    assert status == 403 and ran == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kw",
    [
        {"json": {"confirm": True}},
        {"json": {"confirm_cascade": True}},
        {"params": {"confirm": "true"}},
    ],
)
async def test_consent_from_a_scoped_session_is_refused(kw) -> None:
    """Its `confirm` is never the owner's (F3): no row in this release admits one."""
    ran: list[str] = []
    lid = _loop_row()
    status, _h, _b = await _call(
        _gated_app(ran), "GET", f"/api/loops/{lid}/report", _scoped(), **kw
    )
    assert status == 403 and ran == []


# ── The shell ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/api/auth/logout"),
        ("GET", "/api/auth-status"),
        ("GET", "/api/design/tokens/default"),
    ],
)
async def test_the_shell_and_its_own_sign_out_are_reachable(method, path) -> None:
    ran: list[str] = []
    status, _h, body = await _call(_gated_app(ran), method, path, _scoped(("status",)))
    if path == "/api/design/tokens/default":
        assert status == 403, "the loops scope's, not the shell's"
        return
    assert status == 200, body
    assert len(ran) == 1


def test_an_apps_ui_file_is_no_part_of_the_shell() -> None:
    """The shell is the data-less build output a browser loads by itself, less the installed
    apps' page files, which say which apps are installed."""
    from aiohttp.test_utils import make_mocked_request

    gate = _scopes()
    assert gate.is_shell(make_mocked_request("GET", "/"))
    assert gate.is_shell(make_mocked_request("GET", "/manifest.webmanifest"))
    assert not gate.is_shell(make_mocked_request("GET", "/apps/mail/ui/index.js"))
    assert not gate.is_shell(make_mocked_request("POST", "/"))


# ── The cut views ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_status_is_cut_for_a_scoped_session() -> None:
    from personalclaw.dashboard import handlers_system

    app = web.Application(
        middlewares=[token_auth.token_auth_middleware(port=PORT), _scopes().device_scope_middleware]
    )
    app["port"] = PORT
    app["allowed_origins"] = {ORIGIN}
    app["state"] = MagicMock(start_time=time.time() - 60)
    app.router.add_get("/api/status", handlers_system.api_status)
    status, _h, body = await _call(app, "GET", "/api/status", _scoped(("status",)))
    assert status == 200, body
    data = json.loads(body)
    assert set(data) == {"version", "uptime_secs", "healthy", "update_available"}
    assert data["uptime_secs"] >= 59


@pytest.mark.asyncio
async def test_the_account_view_is_cut_for_a_scoped_session() -> None:
    app = web.Application(
        middlewares=[token_auth.token_auth_middleware(port=PORT), _scopes().device_scope_middleware]
    )
    app["port"] = PORT
    app["allowed_origins"] = {ORIGIN}
    app.router.add_get("/api/auth/session", auth_h.api_auth_session)
    minted = _scoped(("status", "loops"))
    status, _h, body = await _call(app, "GET", "/api/auth/session", minted)
    assert status == 200, body
    data = json.loads(body)
    assert set(data) == {"signed_in", "scope", "expires_at"}
    assert data["signed_in"] is True and data["scope"] == ["loops", "status"]
    assert abs(data["expires_at"] - minted.expires_at) < 2


@pytest.mark.asyncio
async def test_the_loops_list_holds_only_loop_rows_for_a_scoped_session(monkeypatch) -> None:
    from personalclaw.dashboard.handlers import loop_routes
    from personalclaw.workflows import loop_view

    lid = _loop_row()
    monkeypatch.setattr(
        loop_view,
        "list_loop_views",
        lambda **_kw: [{"id": "run-1", "run_id": "run-1", "created_at": 1}],
    )
    app = web.Application(
        middlewares=[token_auth.token_auth_middleware(port=PORT), _scopes().device_scope_middleware]
    )
    app["port"] = PORT
    app["allowed_origins"] = {ORIGIN}
    app.router.add_get("/api/loops", loop_routes.api_loop_list)
    status, _h, body = await _call(app, "GET", "/api/loops", _scoped())
    assert status == 200, body
    assert [row["id"] for row in json.loads(body)["loops"]] == [lid]
    owner = sign_in()
    async with TestClient(TestServer(app), cookie_jar=DummyCookieJar()) as client:
        resp = await client.get("/api/loops", headers={**owner.headers})
        ids = [row["id"] for row in (await resp.json())["loops"]]
    assert set(ids) == {lid, "run-1"}, "the owner's list is unchanged"


# ── What the security log says ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_refusals_are_one_row_a_minute_a_session_naming_templates_not_paths(
    sel_events,
) -> None:
    ran: list[str] = []
    app = _gated_app(ran)
    minted = _scoped(("status",))
    async with TestClient(TestServer(app), cookie_jar=DummyCookieJar()) as client:
        for path in ("/api/settings", "/api/loops/secret-loop-id", "/api/settings"):
            assert (await client.get(path, headers=_headers(minted))).status == 403
    rows = [e for e in sel_events if e.get("operation") == "device_scope_refused"]
    assert len(rows) == 1, rows
    assert "secret-loop-id" not in json.dumps(sel_events)
    assert "GET /api/settings" in rows[0]["resources"]


# ── Bypass and auth-off narrow by the scope too (F10) ───────────────────


@pytest.mark.asyncio
async def test_under_the_local_network_bypass_a_scoped_cookie_is_still_narrowed(
    monkeypatch,
) -> None:
    monkeypatch.setenv("PERSONALCLAW_BYPASS_LOCAL_NETWORKS", "1")
    ran: list[str] = []
    status, _h, _b = await _call(_gated_app(ran), "GET", "/api/settings", _scoped(("status",)))
    assert status == 403 and ran == []


@pytest.mark.asyncio
async def test_under_the_bypass_an_ended_scoped_cookie_is_not_the_owner(monkeypatch) -> None:
    """Its session is over, but the cookie still names a scope this gateway signed: narrowed."""
    monkeypatch.setenv("PERSONALCLAW_BYPASS_LOCAL_NETWORKS", "1")
    minted = _scoped(("status",))
    token_auth.sign_out([minted.nonce], ss.END_SIGNED_OUT_ELSEWHERE, actor="owner")
    ran: list[str] = []
    status, _h, _b = await _call(_gated_app(ran), "GET", "/api/settings", minted)
    assert status == 403 and ran == []


@pytest.mark.asyncio
async def test_an_unscoped_session_is_never_gated() -> None:
    ran: list[str] = []
    owner = sign_in()
    async with TestClient(TestServer(_gated_app(ran)), cookie_jar=DummyCookieJar()) as client:
        resp = await client.get("/api/settings", headers={**owner.headers, "Origin": ORIGIN})
        assert resp.status == 200
        resp = await client.post(
            "/api/loops", headers={**owner.headers, "Origin": ORIGIN}, json={"confirm": True}
        )
        assert resp.status == 200
    assert ran == ["GET /api/settings", "POST /api/loops"]


def test_the_gate_sits_after_token_auth_and_the_app_gate_and_before_consent() -> None:
    from pathlib import Path

    import personalclaw.dashboard.server as server

    text = Path(server.__file__).read_text(encoding="utf-8")
    order = [
        text.index("token_auth_middleware(\n"),
        text.index("        app_permission_middleware,\n"),
        text.index("        device_scope_middleware,\n"),
        text.index("        consent_ask_middleware,\n"),
    ]
    assert order == sorted(order)

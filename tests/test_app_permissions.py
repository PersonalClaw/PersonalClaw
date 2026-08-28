"""App permission enforcement (A5) — server-side defense-in-depth.

Covers the PermissionChecker decision logic (api prefix/wildcard, events,
mcpTools, memory tiers, coarse flags) and the enforcement middleware: an
app-identified request to an undeclared API path is 403'd, a declared one
passes, the app's own backend-proxy path is always allowed, and an owner request
(no app identity) is unaffected.
"""

from __future__ import annotations

import json
import re
from contextlib import asynccontextmanager, contextmanager
from dataclasses import MISSING, fields
from pathlib import Path
from unittest.mock import patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.apps import manager
from personalclaw.apps.manifest import Permissions
from personalclaw.apps.permissions import (
    APP_SCOPED_PREFIXES,
    PermissionChecker,
    app_request_denial,
    checker_for,
)


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

    def test_network_declaration_reaches_the_consent_wire(self):
        """EI-12 D2. ``network`` is unenforced, so the ONLY thing it does is reach the
        Store's install-consent surface — the advisory row there is rendered from this
        dict (``handlers/apps.py`` → ``AppPermissionsWire`` → ``PermissionList``). A
        declared flag must appear; a declining app must omit the key, because the UI
        distinguishes "declared" from "not declared" and would otherwise mislabel it."""
        assert Permissions.from_dict({"network": True}).to_dict()["network"] is True
        assert "network" not in Permissions.from_dict({"network": False}).to_dict()
        assert "network" not in Permissions.from_dict({}).to_dict()


# ── APE-12: the consent wire declares every permission this dict can emit ──

_API_TS = Path(__file__).resolve().parent.parent / "web" / "src" / "lib" / "api.ts"


def _permissions_with_every_field_set() -> Permissions:
    """A ``Permissions`` whose every field is truthy, so ``to_dict`` emits every key it
    can. Derived from the dataclass rather than a hand-written list — a field added
    without a wire declaration is exactly the defect this rail exists to catch."""
    kwargs: dict[str, object] = {}
    for f in fields(Permissions):
        if f.name == "proposals":  # INU-7: a list of typed entries, not of name strings
            from personalclaw.apps.manifest import ProposalKind

            kwargs[f.name] = [ProposalKind(kind_suffix="x", label="X")]
        elif f.default_factory is not MISSING:  # the list scopes
            kwargs[f.name] = ["x"]
        elif isinstance(f.default, bool):
            kwargs[f.name] = True
        elif isinstance(f.default, str):
            kwargs[f.name] = "shared"
        else:  # pragma: no cover — a new field shape must be taught to this rail
            raise AssertionError(f"unhandled Permissions field shape: {f.name}")
    return Permissions(**kwargs)  # type: ignore[arg-type]


def _wire_declared_keys() -> set[str]:
    """The optional fields of ``AppPermissionsWire`` in web/src/lib/api.ts."""
    src = _API_TS.read_text(encoding="utf-8")
    m = re.search(r"export interface AppPermissionsWire \{(.*?)\n\}", src, re.S)
    assert m, "AppPermissionsWire not found in web/src/lib/api.ts"
    body = re.sub(r"//[^\n]*", "", m.group(1))  # drop comments before scanning
    return set(re.findall(r"(\w+)\?:", body))


def test_consent_wire_declares_exactly_the_permissions_the_server_emits():
    """APE-12. The defect: ``Permissions.to_dict()`` emitted ``appMessaging``,
    ``GET /api/apps`` returned it, and ``AppPermissionsWire`` never declared it — so the
    Store dropped it on the floor and install consent never named the apps an app may
    message, while ``manifest.py`` claimed that surface existed.

    Pinned in BOTH directions on purpose. Server-side keys with no wire field are
    invisible to the user (the bug); wire fields with no server key are a consent
    surface promising something nothing ever sends. Adding a permission now reds here
    until it is disclosed."""
    emitted = set(_permissions_with_every_field_set().to_dict())
    declared = _wire_declared_keys()
    assert emitted == declared, (
        f"server-only (never disclosed): {sorted(emitted - declared)}; "
        f"wire-only (nothing sends them): {sorted(declared - emitted)}"
    )
    assert "appMessaging" in emitted  # the rail is not vacuously comparing empty sets


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
    (appdir / "installed.json").write_text(
        # An app dir with no `installed.json` is NOT installed (`_read_installed`), and the
        # boundary now refuses that — so a fixture without one models a partial install and
        # would be denied before any permission check ran.
        json.dumps({"name": name, "version": "1.0.0", "enabled": True}),
        encoding="utf-8",
    )

    @web.middleware
    async def stub_identity(request, handler):
        request["app"] = app_identity
        return await handler(request)

    # Re-create the enforcement middleware standalone (mirrors server.py).
    @web.middleware
    async def app_permission_middleware(request, handler):
        # Calls the REAL decision (`permissions.app_request_denial`) rather than
        # re-deriving it. The old copy inlined `if c is not None and ...`, which is the
        # fail-open shape the boundary itself had: a mirror that reproduces the bug it is
        # meant to catch. Only the logging/response half is local, as in `server.py`.
        app_name = request.get("app", "")
        if app_name and request.path.startswith(APP_SCOPED_PREFIXES):
            if app_request_denial(app_name, request.path):
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
    (appdir / "installed.json").write_text(
        # An app dir with no `installed.json` is NOT installed (`_read_installed`), and the
        # boundary now refuses that — so a fixture without one models a partial install and
        # would be denied before any permission check ran.
        json.dumps({"name": name, "version": "1.0.0", "enabled": True}),
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
        # Calls the REAL decision (`permissions.app_request_denial`) rather than
        # re-deriving it. The old copy inlined `if c is not None and ...`, which is the
        # fail-open shape the boundary itself had: a mirror that reproduces the bug it is
        # meant to catch. Only the logging/response half is local, as in `server.py`.
        app_name = request.get("app", "")
        if app_name and request.path.startswith(APP_SCOPED_PREFIXES):
            if app_request_denial(app_name, request.path):
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


# ── APE-1: backgroundTasks + eventSubscriptions — declared here, enforced by nothing ──
#
# These two follow the same to_dict/from_dict parity pattern as every permission above,
# and differ from all of them in one honest respect: NOTHING ENFORCES THEM TODAY. No core
# code hosts an app worker (APE-3 does) and no platform event is delivered to any app,
# declared or not (APE-2's ``app_events.py`` registry does not exist). So this section
# pins the round trip and the consent leg, and deliberately adds no ``can_use_*``
# accessor: an accessor with no call site would be an enforcement point that enforces
# nothing, and the atom that builds the runtime should add the check WHERE it gates.


def test_background_and_event_grants_round_trip():
    """Parity: a declaration survives ``to_dict`` → ``from_dict`` → ``to_dict``
    unchanged, and the second round trip is a fixed point."""
    p = Permissions(
        backgroundTasks=True,
        eventSubscriptions=["session.created", "task.completed"],
    )
    d = p.to_dict()
    assert d["backgroundTasks"] is True
    assert d["eventSubscriptions"] == ["session.created", "task.completed"]
    back = Permissions.from_dict(d)
    assert back.backgroundTasks is True
    assert back.eventSubscriptions == ["session.created", "task.completed"]
    assert back.to_dict() == d


def test_undeclared_and_empty_background_grants_emit_no_key():
    """The omission half, and it is not cosmetic: the consent surface distinguishes
    "declared" from "did not declare" (EI-12 D2), so a spurious ``backgroundTasks: false``
    would render as a grant the app never asked for."""
    for data in ({}, {"backgroundTasks": False, "eventSubscriptions": []}):
        d = Permissions.from_dict(data).to_dict()
        assert "backgroundTasks" not in d
        assert "eventSubscriptions" not in d
        assert Permissions.from_dict(d).to_dict() == d  # still a fixed point


def test_event_subscription_names_survive_verbatim():
    """The names are APE-2's registry vocabulary, matched exactly and with no wildcard
    (like ``desktop``, unlike ``appMessaging``) — so ``session.created`` must arrive with
    its dot intact. Falsy entries drop, like every other list scope here."""
    p = Permissions.from_dict({"eventSubscriptions": ["session.created", "", None, "a.b"]})
    assert p.eventSubscriptions == ["session.created", "a.b"]
    assert p.to_dict()["eventSubscriptions"] == ["session.created", "a.b"]


def test_event_subscriptions_do_not_widen_the_ws_event_allowlist():
    """The two vocabularies stay separate on purpose. ``events`` is the gateway's WS
    event-type allowlist (``can_use_event``); ``eventSubscriptions`` is the platform
    registry APE-2 will own. Declaring a platform subscription must not silently grant the
    WS event type of the same name, or APE-2's filter would inherit a second, wider path
    to the same data."""
    c = _checker(eventSubscriptions=["session.created"])
    assert not c.can_use_event("session.created")
    # ...and the reverse: a WS grant is not a platform subscription.
    assert _checker(events=["session.created"]).permissions.eventSubscriptions == []


def test_declared_grants_reach_the_pre_install_consent_payload():
    """APE-12's leg, for the new grants: the Store's PRE-install panel renders
    ``CatalogEntry.permissions`` built by ``catalog._manifest_consent``, so a grant has to
    survive THAT extraction, not just ``Permissions.to_dict()``."""
    from personalclaw.apps.catalog import _manifest_consent
    from personalclaw.apps.manifest import AppManifest

    m = AppManifest.from_dict(
        {
            "name": "worker-app",
            "version": "1.0.0",
            "displayName": "Worker App",
            "description": "x",
            "permissions": {
                "backgroundTasks": True,
                "eventSubscriptions": ["session.created"],
            },
        }
    )
    perms, _crons = _manifest_consent(m)
    assert perms["backgroundTasks"] is True
    assert perms["eventSubscriptions"] == ["session.created"]


@pytest.mark.asyncio
async def test_declared_grants_reach_the_installed_app_consent_wire(tmp_path, monkeypatch):
    """The other surface ``PermissionList`` serves is the installed-app panel, fed by
    ``GET /api/apps``. A component test alone would pass through the APE-12 defect shape
    (server emits it, endpoint drops it), so the HTTP payload is pinned too — including
    the declining app, which must send NEITHER key."""
    from personalclaw.apps import app_manager
    from personalclaw.dashboard.handlers.apps import register_app_routes

    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    with (
        patch("personalclaw.config.loader.config_dir", return_value=tmp_path),
        patch.object(manager, "config_dir", return_value=tmp_path),
    ):
        for name, perms in (
            ("worker-app", {"backgroundTasks": True, "eventSubscriptions": ["task.completed"]}),
            ("quiet-app", None),
        ):
            d = tmp_path / "src" / name
            d.mkdir(parents=True)
            mani: dict = {
                "name": name,
                "version": "1.0.0",
                "displayName": name,
                "description": "x",
            }
            if perms is not None:
                mani["permissions"] = perms
            (d / "app.json").write_text(json.dumps(mani), encoding="utf-8")
            res = app_manager.install(d)
            assert res.ok, res.error

        app = web.Application()
        register_app_routes(app)
        async with TestClient(TestServer(app)) as client:
            r = await client.get("/api/apps")
            assert r.status == 200, await r.text()
            apps = {a["name"]: a for a in (await r.json())["apps"]}

    assert apps["worker-app"]["permissions"]["backgroundTasks"] is True
    assert apps["worker-app"]["permissions"]["eventSubscriptions"] == ["task.completed"]
    assert "backgroundTasks" not in apps["quiet-app"]["permissions"]
    assert "eventSubscriptions" not in apps["quiet-app"]["permissions"]


# ── the boundary FAILS CLOSED (#410 residual) ─────────────────────────────────
#
# `app_request_denial` is the whole decision, so these drive it directly. Every check
# used to read `if checker is not None and not checker.can_use_...`, which skipped ALL
# of them when the manifest could not be resolved — so an app-scoped token for an
# uninstalled app reached any path at all, including `/api/security/credentials`.


def _install_on_disk(tmp_path, name, *, permissions, enabled=True, manifest_text=None):
    """An app as it exists on disk: `app.json` (permissions) + `installed.json` (lifecycle).

    Both files matter and they answer different questions. `checker_for` reads only
    `app.json`, and enable/disable writes only `installed.json`, which is why a
    permission check alone cannot see a disabled app.
    """
    appdir = tmp_path / "apps" / name
    appdir.mkdir(parents=True, exist_ok=True)
    body = (
        manifest_text
        if manifest_text is not None
        else json.dumps(
            {
                "name": name,
                "version": "1.0.0",
                "displayName": name,
                "description": "x",
                "permissions": permissions,
            }
        )
    )
    (appdir / "app.json").write_text(body, encoding="utf-8")
    (appdir / "installed.json").write_text(
        json.dumps({"name": name, "version": "1.0.0", "enabled": enabled}), encoding="utf-8"
    )
    return appdir


@contextmanager
def _isolated_apps(tmp_path):
    # `manager.config_dir` is the only patch point that matters: `app_manager` never
    # imports it and resolves every path through `manager.app_dir`. Patching a name a
    # module does not have raises, and ASSIGNING one silently creates a no-op.
    with (
        patch("personalclaw.config.loader.config_dir", return_value=tmp_path),
        patch.object(manager, "config_dir", return_value=tmp_path),
    ):
        yield


#: Paths an app in this test declares nothing for. `/api/security/credentials` is the
#: point: the fail-open hole was not scoped to harmless routes.
_OFF_LIMITS = ("/api/memory/all", "/api/security/credentials", "/api/apps/other/agent-run")


def test_a_declared_path_is_still_allowed(tmp_path):
    """The floor. If this breaks, the tests below prove nothing about failing closed."""
    with _isolated_apps(tmp_path):
        _install_on_disk(tmp_path, "demo", permissions={"api": ["/api/notes"]})
        assert app_request_denial("demo", "/api/notes") == ""
        assert app_request_denial("demo", "/api/notes/sub") == ""
        assert app_request_denial("demo", "/api/secrets") == "api path not in declared permissions"


def test_an_uninstalled_app_is_refused_rather_than_unscoped(tmp_path):
    """The owner's own remediation was the escalation. App tokens carry the app claim in
    the token and live an hour, so uninstalling cannot revoke one — and with the manifest
    gone every check was skipped, leaving the app MORE reach than it declared."""
    with _isolated_apps(tmp_path):
        for path in _OFF_LIMITS:
            assert app_request_denial("ghost", path) == "app is not installed"


def test_an_app_that_corrupts_its_own_manifest_does_not_escape_its_sandbox(tmp_path):
    """Self-inflicted escalation: an app that can write its install dir could unparse its
    own `app.json` and, under the old predicate, be scoped by nothing."""
    with _isolated_apps(tmp_path):
        _install_on_disk(tmp_path, "broken", permissions={}, manifest_text="{ not json")
        for path in _OFF_LIMITS:
            assert app_request_denial("broken", path) == "app manifest could not be read"


def test_disabling_an_app_takes_effect_on_the_next_request(tmp_path):
    """`enable`/`disable` write `installed.json`; `checker_for` reads `app.json`. So a
    disabled app's permissions were unchanged, and while minting refuses a disabled app,
    a token minted before the flip kept working for the rest of its hour."""
    with _isolated_apps(tmp_path):
        _install_on_disk(tmp_path, "off", permissions={"api": ["/api/notes"]}, enabled=False)
        assert app_request_denial("off", "/api/notes") == "app is disabled"
        for path in _OFF_LIMITS:
            assert app_request_denial("off", path) == "app is disabled"


def test_an_owner_request_carries_no_app_identity_and_is_not_scoped(tmp_path):
    """The distinction the old code could not make: no app identity is the OWNER, and an
    unresolvable app identity is a refusal. Both used to reach the handler."""
    with _isolated_apps(tmp_path):
        assert app_request_denial("", "/api/security/credentials") == ""


def test_lifecycle_is_reported_before_capability(tmp_path):
    """An app that is gone should say so, not "path not declared" — the reason is what a
    SEL row records and what the owner reads when an app stops working."""
    with _isolated_apps(tmp_path):
        _install_on_disk(tmp_path, "off", permissions={"api": []}, enabled=False)
        assert app_request_denial("off", "/api/anything") == "app is disabled"

"""Every route that mints a lasting credential or changes how the owner signs in asks one function
first — ``owner_presence.require_owner_presence`` — and containment never does.

The defect this rail exists for: four routes minted credentials for any live session, however old
(a device pairing code, a device sign-in code, an integration token, a channel owner's code), and
the password route replaced the owner's password for it. Fixing four routes leaves the fifth, the
one added next month. So this reads the REAL route table the gateway serves
(``routes.register_dashboard_routes`` and the MCP-tool routes beside it) and, for every route,
follows its handler's calls — into the module's own helpers and the dashboard's — resolving each
name to the function it is, not to a spelling of it. A route that reaches a minting function
(:func:`_minting`) must reach the gate too, or be a sign-in door: a route that redeems a
credential of its own and so is the sign-in the window counts from (:data:`SIGN_IN_DOORS`, each
with its reason). A route that changes how the owner signs in through a general write, which no
minting function names, is listed (:data:`CHANGES_SIGN_IN`) and must reach the gate as well.

The other half: containment — signing a device out, signing out every other device, replacing the
signing key, revoking an integration token, turning incident mode on, signing out — must work when
something is wrong, so its handlers never reach the gate (:data:`CONTAINMENT`). The one revoke
whose handler is shared with a mint (an integration client's ``DELETE``) is driven instead, from a
session days old, by ``test_minting_a_credential_needs_a_recent_sign_in``.

A planted route proves the scan sees what it is for, directly and through a helper, and the floors
prove it read a real table.
"""

from __future__ import annotations

import ast
import importlib.util
import inspect
import textwrap
import types
from collections.abc import Callable
from pathlib import Path
from typing import Any

from aiohttp import web

from personalclaw.dashboard import owner_presence

#: Routes that mint a session and are the sign-in the presence window counts from: each one
#: redeems a credential of its own, so asking it for a recent sign-in would be circular.
SIGN_IN_DOORS: dict[tuple[str, str], str] = {
    ("POST", "/api/auth/login"): "a password sign-in: the password is the proof",
    ("POST", "/api/auth/enroll/complete"): (
        "redeems a device sign-in code, which only a present owner could mint"
    ),
    ("POST", "/api/devices/pair/complete"): (
        "redeems a pairing code, which only a present owner could mint"
    ),
    ("GET", "/api/token/local"): "the local machine secret is the proof",
}

#: Routes that hand an app the token that narrows a live session to that app, for an hour: no new
#: way in (an app's token never stands for the owner — ``owner_presence`` refuses it), and the
#: app permission check keeps every credential route from an app.
APP_NARROWING: dict[tuple[str, str], str] = {
    ("POST", "/api/apps/{name}/token"): "the app SDK's token, narrowing the page's session",
    ("*", "/apps/{name}/api/{tail}"): "the token the proxy hands an app's backend for one request",
}

#: Routes that change how the owner signs in through a general write no minting function names.
CHANGES_SIGN_IN: dict[tuple[str, str], str] = {
    ("PATCH", "/api/config/personalclaw"): "a write that makes sign-in less strict",
    ("POST", "/api/secrets"): (
        "stores a secret under the name it is sent, and the gateway reads some of those names "
        "from its environment to decide who can sign in"
    ),
}

#: What the owner reaches for when something is wrong. These never ask.
CONTAINMENT: frozenset[tuple[str, str]] = frozenset(
    {
        ("POST", "/api/devices/{id}/revoke"),
        ("POST", "/api/devices/revoke-others"),
        ("POST", "/api/auth/rotate-key"),
        ("POST", "/api/devices/integrations/{id}/revoke"),
        ("POST", "/api/external-access/clients/{client_id}/disabled"),
        ("POST", "/api/incident"),
        ("POST", "/api/auth/logout"),
        ("DELETE", "/api/channels/{name}/owner/pairing"),
    }
)

#: The routes this change gates. If one stops reading as a mint, the scan has gone blind.
GATED: frozenset[tuple[str, str]] = frozenset(
    {
        ("POST", "/api/devices/pair/start"),
        ("POST", "/api/auth/enroll/start"),
        ("POST", "/api/external-access/clients"),
        ("POST", "/api/channels/{name}/owner/pairing"),
        ("POST", "/api/auth/password"),
        ("POST", "/api/auth/confirm"),
    }
)


def _minting() -> dict[Any, str]:
    """Each function that mints a lasting credential or changes how the owner signs in."""
    from personalclaw import channel_trust
    from personalclaw.auth import credentials, enrollment, pairing
    from personalclaw.dashboard import token_auth
    from personalclaw.inbound import auth as inbound_auth
    from personalclaw.inbound import clients

    return {
        token_auth.mint_session: "a session",
        token_auth.generate_token: "a session",
        token_auth.owner_sign_in_token: "a session",
        token_auth.renew_sign_in: "a session",
        token_auth.app_session_token: "an app's session token",
        pairing.issue_code: "a device pairing code",
        enrollment.issue_code: "a device sign-in code",
        clients.create_client: "an integration token",
        inbound_auth.create_surface_token: "a surface token",
        channel_trust.create_owner_pairing_code: "a channel owner's code",
        credentials.set_password: "the sign-in password",
        credentials.clear_credentials: "the sign-in password",
        credentials.set_totp_secret: "the authenticator secret",
        credentials.disable_totp: "the authenticator",
    }


# ── the scan ────────────────────────────────────────────────────────────


def _namespace(fn: Any, tree: ast.AST) -> dict[str, Any]:
    """The names *fn*'s body can call: its module's, its closure's, and its own imports."""
    names: dict[str, Any] = dict(getattr(fn, "__globals__", {}))
    try:
        names.update(inspect.getclosurevars(fn).nonlocals)
    except (TypeError, ValueError):
        pass
    package = (getattr(fn, "__module__", "") or "").rpartition(".")[0]
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                try:
                    module = importlib.import_module(alias.name)
                except Exception:  # noqa: BLE001 — a module that will not import names nothing
                    continue
                if alias.asname:
                    names[alias.asname] = module
                else:
                    names[alias.name.split(".")[0]] = importlib.import_module(
                        alias.name.split(".")[0]
                    )
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                parts = package.split(".")
                parent = ".".join(parts[: len(parts) - node.level + 1])
                base = f"{parent}.{base}" if base else parent
            try:
                module = importlib.import_module(base)
            except Exception:  # noqa: BLE001
                continue
            for alias in node.names:
                value = getattr(module, alias.name, None)
                if value is None:
                    try:
                        value = importlib.import_module(f"{base}.{alias.name}")
                    except Exception:  # noqa: BLE001
                        continue
                names[alias.asname or alias.name] = value
    return names


def _resolve(expr: ast.expr, names: dict[str, Any]) -> Any:
    """What a called expression is: a name, or an attribute of a module or class it names."""
    if isinstance(expr, ast.Name):
        return names.get(expr.id)
    if isinstance(expr, ast.Attribute):
        base = _resolve(expr.value, names)
        if isinstance(base, (types.ModuleType, type)):
            return getattr(base, expr.attr, None)
    return None


def _followed(target: Any, origin: Any) -> bool:
    """Whether the scan reads into *target*: a function of the route's own module, or of the
    dashboard's (the HTTP surface's helpers) — never the gate, which it only notes."""
    if not inspect.isfunction(target) or target is owner_presence.require_owner_presence:
        return False
    module = getattr(target, "__module__", "") or ""
    return module == getattr(origin, "__module__", None) or module.startswith(
        "personalclaw.dashboard"
    )


def reached(fn: Any, *, depth: int = 0, seen: set[Any] | None = None) -> set[Any]:
    """Every function *fn* calls, and what the helpers it calls call, as the objects they are."""
    seen = set() if seen is None else seen
    fn = inspect.unwrap(getattr(fn, "func", fn))
    if depth > 5 or fn in seen:
        return set()
    seen.add(fn)
    try:
        tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    except (OSError, TypeError, SyntaxError):
        return set()
    names = _namespace(fn, tree)
    found: set[Any] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        target = _resolve(node.func, names)
        if target is None:
            continue
        try:
            found.add(target)
        except TypeError:  # an unhashable callable is no function the census looks for
            continue
        if _followed(target, fn):
            found |= reached(target, depth=depth + 1, seen=seen)
    return found


def _routes(app: web.Application) -> dict[tuple[str, str], Callable[..., Any]]:
    table: dict[tuple[str, str], Callable[..., Any]] = {}
    for route in app.router.routes():
        if route.resource is None or route.method == "HEAD":
            continue
        table[(route.method, route.resource.canonical)] = route.handler
    return table


def minting_routes(app: web.Application) -> dict[tuple[str, str], tuple[set[str], bool]]:
    """Each route that reaches a minting function: ``{route: (what it mints, asks first)}``."""
    minting = _minting()
    out: dict[tuple[str, str], tuple[set[str], bool]] = {}
    for key, handler in _routes(app).items():
        calls = reached(handler)
        mints = {minting[c] for c in calls if c in minting}
        if mints:
            out[key] = (mints, owner_presence.require_owner_presence in calls)
    return out


def ungated(app: web.Application) -> list[str]:
    """The routes that mint a credential without asking for presence, and are no sign-in door."""
    exempt = {**SIGN_IN_DOORS, **APP_NARROWING}
    return sorted(
        f"{method} {path} mints {', '.join(sorted(mints))}"
        for (method, path), (mints, gated) in minting_routes(app).items()
        if not gated and (method, path) not in exempt
    )


def _the_gateways_routes() -> web.Application:
    from personalclaw.dashboard import server
    from personalclaw.dashboard.routes import register_dashboard_routes

    app = web.Application()
    register_dashboard_routes(app)
    server._register_mcp_routes(app)
    return app


_APP: web.Application | None = None


def _app() -> web.Application:
    global _APP
    if _APP is None:
        _APP = _the_gateways_routes()
    return _APP


# ── the rail ────────────────────────────────────────────────────────────


def test_every_route_that_mints_a_credential_asks_for_the_owner_first():
    assert not ungated(_app()), (
        "These routes mint a lasting credential for any live session, however old. Call "
        "owner_presence.require_owner_presence(request, <action>) before minting, or — only for a "
        "route that redeems a credential of its own — list it in SIGN_IN_DOORS with the reason:\n  "
        + "\n  ".join(ungated(_app()))
    )


def test_a_general_write_that_changes_how_the_owner_signs_in_asks_too():
    table = _routes(_app())
    for key, why in CHANGES_SIGN_IN.items():
        assert key in table, f"{key} is listed ({why}) but the gateway serves no such route"
        assert owner_presence.require_owner_presence in reached(
            table[key]
        ), f"{key[0]} {key[1]} — {why} — does not ask for the owner"


def test_containment_never_asks():
    table = _routes(_app())
    for key in sorted(CONTAINMENT):
        assert key in table, f"{key} is listed as containment but the gateway serves no such route"
        assert owner_presence.require_owner_presence not in reached(
            table[key]
        ), f"{key[0]} {key[1]} is containment: it must work from a session that is not recent"


def test_the_scan_reads_the_gated_routes_and_the_doors_as_mints():
    """Vacuity: a scan that resolved nothing would find no mint anywhere, and call it clean."""
    found = minting_routes(_app())
    for key in sorted(GATED):
        assert key in found, f"the scan no longer sees {key} as a mint — it has gone blind"
        assert found[key][1], f"{key} mints without the gate"
    for key, why in sorted(SIGN_IN_DOORS.items()):
        assert key in found, f"{key} is a listed sign-in door ({why}) that mints nothing now"
        assert not found[key][1], f"{key} is a door: it is the sign-in, and asks for none"
    for key in APP_NARROWING:
        assert key in found, f"{key} is listed as an app's narrowing but mints nothing"


def test_the_route_table_is_the_real_one():
    """Floor: the table is the gateway's, hundreds of routes, not an empty app."""
    assert len(_routes(_app())) > 400


# ── the planted route ───────────────────────────────────────────────────

_PLANTED = """
from aiohttp import web

from personalclaw.auth import pairing
from personalclaw.dashboard.owner_presence import require_owner_presence


async def mints_bare(request):
    code, expires_at = pairing.issue_code()
    return web.json_response({"code": code})


def _issue():
    from personalclaw.inbound import clients as inbound_clients

    return inbound_clients.create_client("planted", surfaces=["mcp"])


async def mints_through_a_helper(request):
    return web.json_response({"token": _issue()[1]})


async def mints_asking_first(request):
    refused = require_owner_presence(request, "Planting a code")
    if refused is not None:
        return refused
    code, expires_at = pairing.issue_code()
    return web.json_response({"code": code})


async def mints_nothing(request):
    return web.json_response({"ok": True})
"""


def _planted(tmp_path: Path) -> types.ModuleType:
    path = tmp_path / "planted_minting_routes.py"
    path.write_text(_PLANTED, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("planted_minting_routes", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_a_planted_route_that_mints_without_asking_is_found(tmp_path):
    planted = _planted(tmp_path)
    app = web.Application()
    app.router.add_post("/api/planted/bare", planted.mints_bare)
    app.router.add_post("/api/planted/helper", planted.mints_through_a_helper)
    app.router.add_post("/api/planted/asks", planted.mints_asking_first)
    app.router.add_post("/api/planted/nothing", planted.mints_nothing)

    assert ungated(app) == [
        "POST /api/planted/bare mints a device pairing code",
        "POST /api/planted/helper mints an integration token",
    ]

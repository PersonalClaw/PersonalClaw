"""``permissions.api`` prefix matching must not hand out a strictly stronger door (#2964).

**The defect.** ``permissions.api`` is a path-PREFIX allowlist and ``/api/ws`` is a prefix
of ``/api/ws/terminal/{session_id}`` — the built-in CLI panel's PTY. So an app that
declared the event socket also got an interactive shell running as the owner, in the
owner's ``$HOME``, with the real ``~/.personalclaw`` credential store readable from it.
``api_terminal_ws`` authorized on ``request.get("user")`` plus the feature flag and never
consulted ``request["app"]``, both of which an app-scoped request satisfies. The shipped
first-party ``menu-bar-companion`` declares exactly ``["/api/loops", "/api/approvals",
"/api/ws"]``, so this was not a third-party hypothetical — and install consent showed the
user the string ``/api/ws``, with nothing on that screen saying "and a terminal". That
makes it a CONSENT-INTEGRITY defect, not merely an over-broad grant.

**The fix under test** is ``apps/permissions.OWNER_ONLY_API_PATHS`` + the middleware's
refusal, and this suite pins it in three layers:

1. the decision function (``app_request_denial``) refuses, with the reason naming the
   capability rather than "not in declared permissions" — a developer who reads the wrong
   reason adds the path to their manifest and files a bug;
2. the manifest REFUSES TO INSTALL when it literally names an owner-only root, so an author
   learns at validate time rather than at first 403;
3. a PROPERTY rail over every route the source registers, not an enumerated list — see
   :func:`test_no_registered_route_under_an_owner_only_root_is_app_reachable` and its
   stated blind spot.
"""

from __future__ import annotations

import ast
import json
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest

import personalclaw.dashboard.server as server_mod
from personalclaw.apps import manager
from personalclaw.apps.manifest import AppManifest, Permissions
from personalclaw.apps.permissions import (
    OWNER_ONLY_API_PATHS,
    PermissionChecker,
    app_request_denial,
    owner_only_api_reason,
)

# The path the issue measured, and the declaration that reached it.
_PTY = "/api/ws/terminal/qa-pty"
_EVENT_SOCKET = "/api/ws"


def _checker(*api: str) -> PermissionChecker:
    return PermissionChecker(app_name="nosy-app", permissions=Permissions(api=list(api)))


# ── layer 1: the decision ────────────────────────────────────────────────────


def test_the_event_socket_declaration_no_longer_reaches_the_pty():
    """#2964's exact repro, at the decision that granted it.

    The FLOOR is the second assertion: ``/api/ws`` must still work. A fix that closed the
    terminal by breaking the event socket would break ``menu-bar-companion`` and every
    other app that legitimately watches events, so both halves are the test.
    """
    checker = _checker(_EVENT_SOCKET)
    assert not checker.can_use_api(_PTY)
    assert checker.can_use_api(_EVENT_SOCKET), "the event socket itself must still be granted"


def test_a_bare_wildcard_does_not_reach_an_owner_only_path():
    """``"*"`` short-circuited ``_matches_any`` before any path check ran, so the broadest
    declaration was also the one nothing could bound. Deny-by-CONSTRUCTION means the
    wildcard is no exception."""
    checker = _checker("*")
    assert not checker.can_use_api(_PTY)
    assert checker.can_use_api("/api/knowledge"), "the wildcard must still grant ordinary paths"


def test_naming_the_terminal_exactly_still_does_not_grant_it():
    """The registry is a CLASS rule, not a specificity rule. An app holding a shell as the
    owner has every permission in its manifest and then some, so there is nothing left to
    scope — which is why "declare it precisely" is not the escape hatch here."""
    assert not _checker("/api/ws/terminal").can_use_api(_PTY)
    assert not _checker("/api/ws/terminal/*").can_use_api(_PTY)


@pytest.mark.parametrize("root", sorted(OWNER_ONLY_API_PATHS))
def test_every_registry_root_is_refused_for_itself_and_its_children(root: str):
    """Whole-registry coverage, parametrized off the registry itself so a row added later
    is tested without editing this file."""
    checker = _checker("*")
    assert not checker.can_use_api(root)
    assert not checker.can_use_api(root + "/child/deeper")


def test_matching_is_segment_aware_in_both_directions():
    """``/api/auth`` must cover ``/api/auth/password`` and must NOT cover
    ``/api/auth-status`` — a raw string prefix would have swallowed the sibling, which is
    the same sloppiness (a prefix that is not a boundary) that caused the bug."""
    assert owner_only_api_reason("/api/auth/password")
    assert not owner_only_api_reason("/api/auth-status")
    assert not owner_only_api_reason("/api/system")  # only /api/system/restart is owner-only
    assert owner_only_api_reason("/api/system/restart")


def test_the_denial_reason_names_the_capability_not_the_allowlist(tmp_path):
    """A refusal a developer cannot act on is a refusal that gets worked around.

    ``"api path not in declared permissions"`` would be actively misleading here: the path
    IS within the app's declared prefix by the grammar, and what refused it is that the
    capability is not app-grantable at all.
    """
    with _isolated_apps(tmp_path):
        _install(tmp_path, "nosy-app", {"api": [_EVENT_SOCKET]})
        reason = app_request_denial("nosy-app", _PTY)
        assert "owner-only" in reason
        assert "shell" in reason
        assert reason != "api path not in declared permissions"
        # The event socket is still granted through the same decision.
        assert app_request_denial("nosy-app", _EVENT_SOCKET) == ""


def test_lifecycle_still_outranks_the_owner_only_check(tmp_path):
    """Ordering is deliberate: an uninstalled app must hear "app is not installed", not a
    capability lecture, or the reason names the wrong problem."""
    with _isolated_apps(tmp_path):
        assert app_request_denial("ghost", _PTY) == "app is not installed"


def test_an_owner_request_is_untouched():
    """The registry scopes APP identities only. The owner's CLI panel is the whole reason
    the PTY exists, and it must keep working — a control that took the terminal away from
    its user would be the outage the refusal was meant to prevent."""
    assert app_request_denial("", _PTY) == ""


# ── layer 1b: the PTY seam refuses on its own, not only via the middleware ───


@pytest.mark.asyncio
async def test_the_pty_handler_itself_refuses_an_app_identity(monkeypatch, tmp_path):
    """Defence in depth AT the seam, driven with no app-permission middleware present.

    The handler is the thing the issue named: it authorized on ``request.get("user")``
    plus the feature flag and never looked at ``request["app"]``. The registry closes the
    door in the middleware, but a shell is not a capability to lose on a middleware-ordering
    refactor — so this drives the handler with the middleware deliberately absent, which is
    the only way to tell the two layers apart.
    """
    import json as _json
    from unittest.mock import MagicMock

    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer

    from personalclaw.dashboard.handlers import terminal

    cfg = tmp_path / "config.json"
    cfg.write_text(_json.dumps({"dashboard": {"terminal": {"enabled": True}}}))
    monkeypatch.setattr(terminal, "config_path", lambda: cfg)
    monkeypatch.setattr(terminal, "_sel", lambda: MagicMock())

    @web.middleware
    async def app_scoped_auth(request, handler):
        request["user"] = "testuser"  # what the PTY used to authorize on, and still does
        request["app"] = "nosy-app"  # ...plus the app identity it never consulted
        return await handler(request)

    application = web.Application(middlewares=[app_scoped_auth])
    application["state"] = MagicMock()
    application["state"]._terminal_sessions = {}
    application.router.add_get("/api/ws/terminal/{session_id}", terminal.api_terminal_ws)

    async with TestClient(TestServer(application)) as client:
        response = await client.get("/api/ws/terminal/qa-pty")
        assert response.status == 403, "the PTY handshake must not upgrade for an app token"
        assert "owner-only" in (await response.text()).lower()


# ── layer 2: the manifest refuses to install a dishonest declaration ─────────


def test_a_manifest_naming_an_owner_only_path_fails_validation():
    """The author learns at validate time. Leaving the declaration in place would ship a
    consent screen advertising reach the app will never have — the same dishonest-consent
    failure from the other direction."""
    manifest = AppManifest(
        name="nosy-app",
        version="0.2.0",
        displayName="Nosy App",
        description="x",
        permissions=Permissions(api=["/api/ws/terminal"]),
    )
    errors = manifest.validate()
    assert any("owner-only" in e and "/api/ws/terminal" in e for e in errors), errors
    assert any("backend" in e or "desktop" in e for e in errors), (
        "the error must name the sanctioned alternative — a refusal with no way out "
        f"is the outage: {errors}"
    )


def test_every_shipped_manifest_still_validates():
    """The corpus floor. ``menu-bar-companion`` declares ``/api/ws``; if the validation
    rule caught a shorter prefix it would refuse to install a first-party app."""
    roots = [Path(server_mod.__file__).parents[2], Path(server_mod.__file__).parents[4] / "apps"]
    checked = 0
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("app.json"):
            if "node_modules" in path.parts:
                continue
            try:
                manifest = AppManifest.from_json_file(path)
            except Exception:  # noqa: BLE001 — fixtures deliberately include broken manifests
                continue
            offending = [e for e in manifest.validate() if "owner-only" in e]
            assert not offending, f"{path}: {offending}"
            checked += 1
    assert checked >= 20, f"only {checked} manifests scanned — the corpus floor is vacuous"


# ── layer 3: a PROPERTY over discovered routes, not an enumerated list ───────

_ADD_VERBS = {"add_get", "add_post", "add_put", "add_delete", "add_patch", "add_head", "add_route"}


def _registered_api_paths() -> set[str]:
    """Every literal route path registered under ``dashboard/``, by AST.

    Static rather than booting the gateway, following the house precedent in
    ``test_api_manifest_drift._literal_route_paths``: startup has security-critical side
    effects (extension load, binding migration) that a rail must not need.
    """
    dash_dir = Path(server_mod.__file__).parent
    paths: set[str] = set()
    for py in dash_dir.rglob("*.py"):
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            index = 1 if node.func.attr == "add_route" else 0
            if node.func.attr not in _ADD_VERBS or len(node.args) <= index:
                continue
            arg = node.args[index]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                paths.add(arg.value)
    return {p for p in paths if p.startswith("/api")}


def test_no_registered_route_under_an_owner_only_root_is_app_reachable():
    """The PROPERTY, asserted over every route the source registers — including the route
    someone adds tomorrow.

    An enumerated rail ("the terminal is refused") cannot see a fifth ``/api/auth/*`` route
    or a second PTY endpoint. This one derives its population from the route
    registrations, so a new child of an already-classified root is covered the moment it is
    written, with no test edit.

    **What it CANNOT see, stated plainly:** a newly added route that is dangerous and whose
    root nobody put in the registry. Classification is a human judgement and no rail
    substitutes for it. What this rail does guarantee is that classification, once made,
    cannot be silently escaped by adding a sub-path.
    """
    paths = _registered_api_paths()
    assert len(paths) > 300, f"AST scan found only {len(paths)} /api routes — vacuous"
    checker = _checker("*")  # the most permissive declaration expressible
    reachable = sorted(p for p in paths if owner_only_api_reason(p) and checker.can_use_api(p))
    assert not reachable, f"owner-only routes reachable by an app declaring '*': {reachable}"


def test_no_registry_root_is_a_fiction():
    """Every registry root matches at least one route that actually registers.

    A denylist that names a path nothing serves is a comment pretending to be a control —
    and the first sign of one is a typo nobody noticed.
    """
    paths = _registered_api_paths()
    assert len(paths) > 300, f"AST scan found only {len(paths)} /api routes — vacuous"
    dead = sorted(
        root
        for root in OWNER_ONLY_API_PATHS
        if not any(p == root or p.startswith(root + "/") for p in paths)
    )
    assert not dead, f"OWNER_ONLY_API_PATHS roots with no registered route: {dead}"


# ── shared install helpers (mirrors test_app_permissions.py) ─────────────────


def _install(tmp_path: Path, name: str, permissions: dict) -> None:
    appdir = tmp_path / "apps" / name
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
def _isolated_apps(tmp_path: Path):
    with (
        patch("personalclaw.config.loader.config_dir", return_value=tmp_path),
        patch.object(manager, "config_dir", return_value=tmp_path),
    ):
        yield

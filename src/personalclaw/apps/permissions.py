"""App permission enforcement (A5) — make declared ``Permissions`` real.

An app's manifest declares a permission scope (``permissions``: api / events /
mcpTools / storage / network / memory / cron). Until now that was declarative
only. This module turns it into an enforced boundary, server-side — the
defense-in-depth half of the plan (the SDK enforces client-side too in A6, but a
client check is bypassable, so the gateway must reject independently).

Identity: a request carrying an app-scoped token has ``request["app"]`` set to
the app name (minted in :mod:`token_auth`). When that's present, the app
enforcement middleware checks the request path against the app's
``permissions.api`` allowlist — an undeclared path is rejected ``403`` before the
handler runs. A request with no app identity (the owner/dashboard) is unaffected.

The checker is the seam every capability enforcement consults (untrusted-app
sandbox). Enforcement status of each method:

* ``can_use_api``   — app-permission middleware (server.py), 403 on undeclared path.
* ``can_use_agent`` — the app agent-run endpoint (handlers/apps.py).
* ``can_use_event`` — WS fan-out (state.broadcast_ws) filters an app connection's
  events to its declared set.
* ``can_use_mcp_tool`` — the direct tool-invoke endpoint (handlers/tools.py).
* ``can_use_memory`` — app-permission middleware gates any ``/api/memory`` path.
* ``can_use_cron``  — app-declared manifest crons are registered only when held
  (apps/app_crons.reconcile_app_crons).
* ``can_use_storage`` — the backend launcher hands the app its DATA_DIR only when
  held (apps/backend_runtime).
* ``can_use_network`` — **DECLARATION-ONLY (unenforced by design)**. An app
  backend is an isolated subprocess with its own OS-level network stack; there is
  no in-process egress hook the gateway can intercept. The flag records INTENT so
  the Store can surface it (install consent lists "network access: yes/no") and a
  future OS-level isolation layer (cgroups/nftables/seccomp) can enforce it. Every
  gateway-MEDIATED reach is already bounded by ``can_use_api`` — a ``network:false``
  app can still reach the internet through its own subprocess; the plan is to
  tighten this via egress sandboxing in a future milestone. Until then, treat
  ``network: true`` as an honest declaration, not a security boundary.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from personalclaw.apps.manifest import Permissions

logger = logging.getLogger(__name__)


@dataclass
class PermissionChecker:
    """Decides whether an app may reach a given resource, per its declared scope.

    Matching is prefix-based for API paths and exact/prefix for the list scopes,
    mirroring how the manifest declares them (``api`` = allowed path prefixes,
    ``events``/``mcpTools`` = allowed names, with a trailing ``*`` wildcard)."""

    app_name: str
    permissions: Permissions

    # -- API path allowlist ----------------------------------------------
    def can_use_api(self, path: str) -> bool:
        """An app may call an API path only if it matches a declared prefix.

        An app with no declared ``api`` scope can reach NO gateway API (deny by
        default). Its own backend proxy route (``/apps/{name}/api/*``) is always
        allowed — that's the app talking to itself, not the gateway API."""
        if path.startswith(f"/apps/{self.app_name}/api"):
            return True
        return _matches_any(path, self.permissions.api)

    # -- event subscriptions ---------------------------------------------
    def can_use_event(self, event_type: str) -> bool:
        return _matches_any(event_type, self.permissions.events)

    # -- MCP tools --------------------------------------------------------
    def can_use_mcp_tool(self, tool_name: str) -> bool:
        return _matches_any(tool_name, self.permissions.mcpTools)

    # -- coarse capability flags -----------------------------------------
    def can_use_memory(self, scope: str = "app-scoped") -> bool:
        """``memory:""`` → no memory; ``app-scoped`` → only app-scoped; ``shared``
        → both app-scoped and shared."""
        declared = self.permissions.memory
        if not declared:
            return False
        if declared == "shared":
            return True
        return scope == "app-scoped"

    def can_use_cron(self) -> bool:
        return self.permissions.cron

    def can_use_network(self) -> bool:
        return self.permissions.network

    def can_use_storage(self) -> bool:
        return self.permissions.storage

    def can_use_agent(self) -> bool:
        """May the app run background agent tasks (headless subagent runs)?"""
        return self.permissions.agent


def _matches_any(value: str, patterns: list[str]) -> bool:
    """Prefix/wildcard match: ``"a/b/*"`` matches any ``"a/b/..."``; an exact
    string matches itself or anything under it as a path prefix."""
    for pat in patterns:
        if pat == "*":
            return True
        if pat.endswith("*"):
            if value.startswith(pat[:-1]):
                return True
        elif value == pat or value.startswith(pat.rstrip("/") + "/"):
            return True
    return False


def checker_for(app_name: str) -> PermissionChecker | None:
    """Build a :class:`PermissionChecker` for an installed app, or ``None`` if the
    app/manifest can't be resolved (caller treats that as no-app-identity)."""
    if not app_name:
        return None
    try:
        from personalclaw.apps.app_manager import _manifest_of

        manifest = _manifest_of(app_name)
    except Exception:
        logger.debug("permission checker: manifest load failed for %s", app_name, exc_info=True)
        return None
    if manifest is None:
        return None
    return PermissionChecker(app_name=app_name, permissions=manifest.permissions)

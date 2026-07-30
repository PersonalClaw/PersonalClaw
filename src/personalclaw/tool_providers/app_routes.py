"""Auto-surfaced app backend routes (§4.2) — ONE generic tool provider.

An app declares its agent-callable backend surface STATICALLY in ``app.json``
(``backend.routes[]``), readable WITHOUT executing app code (the manifest
module's design rule). This module turns those declarations into a live agent
tool surface with **one** generic provider — never N generated ones:

* :class:`AppRoutesToolProvider` exposes ``app_<name>_<op>`` tools for every
  ENABLED app's ``agentCallable`` routes and invokes them through the SAME
  reverse proxy the dashboard uses (``/apps/{name}/api/*`` →
  ``backend_runtime`` subprocess) under the ``LOOPBACK_INTERNAL`` egress stance,
  with a fresh app-scoped token — so a tool call is bounded to exactly the
  reach the owner's proxy grants, nothing more. Registered once via
  :func:`tool_providers.registry.register_provider`; it re-reads the installed
  apps live on every ``list_tools`` so enable/disable/``/update`` resync for free.

* :func:`app_surfaces` renders the same declarations for ``/api/manifest`` §1's
  ``app_surfaces[]`` — one source (the manifest ``routes[]``), two renderings.

* :func:`note_proxy_status` closes the drift loop the manifest-vs-UI audit keeps
  reopening: when a declared route is proxied and the backend answers **404**,
  the route is DEAD-DECLARED (declared but not live) — a deduped warning
  notification fires once so the operator fixes the manifest. This is the plan's
  "match on first proxy 404" signal: runtime, zero false positives (real args
  resolve real path params), and it needs no app-side introspection endpoint.

The companion ``call-app-route`` action provider (``action_providers/``) drives
the same routes from hooks/crons/event-triggers; both share
:func:`resolve_route` so the agentCallable gate + path/query/body routing live
in one place.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Iterator

from personalclaw import notification_kinds
from personalclaw.apps.manifest import AppManifest, RouteEntry
from personalclaw.errors import AgentError
from personalclaw.tool_providers.base import RiskLevel, ToolDefinition, ToolProvider, ToolResult

logger = logging.getLogger(__name__)

PROVIDER_NAME = "app-routes"

# Methods with no host side effects are SAFE; a mutating verb is CAUTION; a
# delete is DESTRUCTIVE. Advisory metadata (the native loop's approval gate keys
# off it); matches the InProcessMcpToolProvider risk-by-name discipline.
_METHOD_RISK: dict[str, RiskLevel] = {
    "GET": RiskLevel.SAFE,
    "HEAD": RiskLevel.SAFE,
    "OPTIONS": RiskLevel.SAFE,
    "POST": RiskLevel.CAUTION,
    "PUT": RiskLevel.CAUTION,
    "PATCH": RiskLevel.CAUTION,
    "DELETE": RiskLevel.DESTRUCTIVE,
}


def tool_name_for(app_name: str, op: str) -> str:
    """The stable tool name for one declared route: ``app_<name>_<op>``.

    App names are kebab-case and ``op`` ids snake_case; both keep their separators
    (``-``/``_`` are valid in tool names across providers). Callers never PARSE this
    back — :meth:`AppRoutesToolProvider.invoke` resolves via a fresh name→route map
    so a hyphen/underscore in either half is never ambiguous."""
    return f"app_{app_name}_{op}"


def _iter_enabled_apps() -> Iterator[tuple[str, AppManifest]]:
    """(name, manifest) for every ENABLED installed app (native apps included —
    they're seeded as real installed apps). Best-effort per app: a manifest that
    fails to parse is skipped, never breaks the surface for the others."""
    from personalclaw.apps.manager import list_apps

    for app_info in list_apps():
        if not app_info.get("enabled", False):
            continue
        data = app_info.get("manifest", {})
        if not data.get("backend", {}).get("routes"):
            continue
        try:
            yield str(app_info.get("name", "")), AppManifest.from_dict(data)
        except Exception:
            logger.debug("app-routes: skipping unparseable manifest for %r", app_info.get("name"))


def iter_app_routes() -> Iterator[tuple[str, RouteEntry]]:
    """(app_name, route) for every AGENT-CALLABLE route of every enabled app.

    The one place both the tool surface and ``app_surfaces[]`` enumerate from, so
    a declared-but-not-callable route documents the surface (in the manifest)
    without ever surfacing as a tool."""
    for name, manifest in _iter_enabled_apps():
        for route in manifest.backend.routes:
            if route.agentCallable and route.op and route.path:
                yield name, route


def _path_placeholders(path: str) -> list[str]:
    """The ``{name}`` placeholder names in a route path, in order."""
    out: list[str] = []
    depth_open = 0
    buf: list[str] = []
    for ch in path:
        if ch == "{":
            depth_open, buf = 1, []
        elif ch == "}" and depth_open:
            depth_open = 0
            name = "".join(buf).split(":", 1)[0].strip()  # tolerate {id:\\d+}
            if name:
                out.append(name)
        elif depth_open:
            buf.append(ch)
    return out


def _as_props(hint: Any) -> dict[str, Any]:
    """Coerce a ``params``/``body`` hint into a JSON-schema ``properties`` map.

    An app may declare either a full object schema (``{"type":"object",
    "properties":{...}}``) or a bare property map (``{"limit":{"type":"integer"}}``)
    — accept both. Non-dict hints yield ``{}`` (documented but unschematized)."""
    if not isinstance(hint, dict):
        return {}
    if "properties" in hint and isinstance(hint["properties"], dict):
        return dict(hint["properties"])
    return {k: v for k, v in hint.items() if k not in ("type", "required")}


def parameters_schema(route: RouteEntry) -> dict[str, Any]:
    """The JSON-schema ``parameters`` for a route's tool — path placeholders
    (required strings) unioned with declared query ``params`` + request ``body``
    property hints. Faithful to what the app declared; no invented fields."""
    props: dict[str, Any] = {}
    required: list[str] = []
    for ph in _path_placeholders(route.path):
        props[ph] = {"type": "string", "description": f"path parameter {ph!r}"}
        required.append(ph)
    props.update(_as_props(route.params))
    props.update(_as_props(route.body))
    schema: dict[str, Any] = {"type": "object", "properties": props}
    if required:
        schema["required"] = required
    return schema


class RouteResolution:
    """A route call resolved to a concrete HTTP request (path/query/body split)."""

    __slots__ = ("app", "route", "path", "query", "body")

    def __init__(
        self,
        app: str,
        route: RouteEntry,
        path: str,
        query: dict[str, Any],
        body: dict[str, Any] | None,
    ) -> None:
        self.app = app
        self.route = route
        self.path = path
        self.query = query
        self.body = body


def resolve_route(app_name: str, op: str, arguments: dict[str, Any]) -> RouteResolution:
    """Resolve ``(app, op, args)`` to a concrete request against the app backend.

    Raises :class:`RouteError` (carrying an :class:`AgentError`) when the app has
    no such agent-callable op — the ONE gate: an op that isn't declared
    ``agentCallable`` (or doesn't exist) can't be driven, from a tool OR from
    ``call-app-route``. Path placeholders are substituted from ``arguments``;
    remaining args route to the query string (safe verbs) or the JSON body
    (mutating verbs), with declared ``params`` keys always going to the query."""
    route = _find_route(app_name, op)
    args = dict(arguments or {})

    # Substitute + consume path placeholders.
    path = route.path
    for ph in _path_placeholders(route.path):
        if ph not in args:
            raise RouteError(
                AgentError(
                    code="ERR_APP_ROUTE_UNKNOWN",
                    what=f"app route {op!r} of app {app_name!r} is missing path parameter {ph!r}",
                    why="the declared path has a placeholder no argument supplied",
                    fix=f"pass {ph!r} in the arguments",
                )
            )
        value = str(args.pop(ph))
        # Substitute both the plain ``{ph}`` form and a declared regex placeholder
        # ``{ph:regex}`` the app's aiohttp route may carry.
        path = path.replace("{" + ph + "}", value)
        path = _replace_regex_placeholder(path, ph, value)

    method = (route.method or "GET").upper()
    query_keys = set(_as_props(route.params).keys())
    if method in ("GET", "HEAD", "DELETE", "OPTIONS"):
        return RouteResolution(app_name, route, path, args, None)
    query = {k: args.pop(k) for k in list(args) if k in query_keys}
    return RouteResolution(app_name, route, path, query, args)


def _replace_regex_placeholder(path: str, name: str, value: str) -> str:
    """Replace a ``{name:regex}`` segment (already-substituted ``{name}`` is a
    no-op here). Kept tiny — apps rarely declare regex paths, but the proxy's
    aiohttp routes may."""
    import re

    return re.sub(r"\{" + re.escape(name) + r":[^{}]+\}", value, path)


class RouteError(Exception):
    """A route could not be resolved/driven; carries the WHAT/WHY/FIX envelope."""

    def __init__(self, agent_error: AgentError) -> None:
        super().__init__(agent_error.what)
        self.agent_error = agent_error


def _find_route(app_name: str, op: str) -> RouteEntry:
    for name, route in iter_app_routes():
        if name == app_name and route.op == op:
            return route
    # Not found (or not agentCallable) — suggest the app's callable ops.
    callable_ops = sorted({r.op for n, r in iter_app_routes() if n == app_name})
    raise RouteError(
        AgentError(
            code="ERR_APP_ROUTE_UNKNOWN",
            what=f"app {app_name!r} has no agent-callable route {op!r}",
            why=(
                "the op is not declared in the app's backend.routes[], or is "
                "declared with agentCallable:false (documented, not drivable)"
            ),
            fix="call one of the app's declared agent-callable ops, or declare this op",
            suggestions=tuple(callable_ops),
        )
    )


async def call_app_route(resolution: RouteResolution) -> ToolResult:
    """Proxy a resolved route to the app backend through the loopback egress rail.

    Mints a fresh app-scoped token (identity bounded to the app's own declared
    permissions, exactly like the dashboard reverse proxy) and dials the backend's
    ``127.0.0.1:{port}`` under ``LOOPBACK_INTERNAL``. A backend that isn't running
    → a coded ``ERR_APP_BACKEND_UNAVAILABLE`` the agent can act on; a 404 →
    dead-declared drift (recorded once) surfaced as ``ERR_APP_ROUTE_UNKNOWN``."""
    from personalclaw.apps.backend_runtime import get_backend_supervisor
    from personalclaw.dashboard.token_auth import generate_token
    from personalclaw.net import LOOPBACK_INTERNAL, EgressBlocked, fetch

    app_name, route = resolution.app, resolution.route
    rb = get_backend_supervisor().get(app_name)
    if rb is None:
        return ToolResult(
            success=False,
            agent_error=AgentError(
                code="ERR_APP_BACKEND_UNAVAILABLE",
                what=f"app {app_name!r} backend is not running",
                why="the app is disabled, has no backend, or its subprocess crashed",
                fix=f"enable {app_name!r} in the App Library, or check its backend logs",
            ),
        )

    url = f"{rb.base_url}/{resolution.path.lstrip('/')}"
    method = (route.method or "GET").upper()
    headers = {
        "Authorization": f"Bearer {generate_token('dashboard', ttl_seconds=3600, app=app_name)}",
        "X-PersonalClaw-App": app_name,
    }
    data: bytes | None = None
    if resolution.body is not None:
        data = json.dumps(resolution.body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if resolution.query:
        from urllib.parse import urlencode

        url = f"{url}?{urlencode({k: str(v) for k, v in resolution.query.items()})}"

    try:
        resp = await fetch(url, policy=LOOPBACK_INTERNAL, method=method, headers=headers, data=data)
    except EgressBlocked as exc:
        return ToolResult(
            success=False,
            agent_error=AgentError(
                code="ERR_APP_BACKEND_UNAVAILABLE",
                what=f"call to app {app_name!r} route {route.op!r} was blocked by egress",
                why=str(exc),
                fix="this is an internal loopback call; check the backend port binding",
            ),
        )

    note_proxy_status(app_name, route.op, resp.status)
    if resp.status == 404:
        return ToolResult(
            success=False,
            agent_error=AgentError(
                code="ERR_APP_ROUTE_UNKNOWN",
                what=f"app {app_name!r} route {route.op!r} ({method} {route.path}) returned 404",
                why="the route is declared in app.json but the backend serves no such path",
                fix="fix the declared path in the app's backend.routes[], or add the route",
            ),
        )
    body_text = resp.text
    if resp.status >= 400:
        return ToolResult(
            success=False,
            output=body_text,
            error=f"app backend returned HTTP {resp.status}",
        )
    return ToolResult(success=True, output=body_text, metadata={"status": resp.status})


# ── Drift: dead-declared routes surfaced on first proxy 404 ─────────────────
# Deduped so one dead route files ONE warning, not one per call. In-memory,
# process-lifetime — a restart re-arms it (a fixed manifest simply never 404s).
_drift_seen: set[tuple[str, str]] = set()


def note_proxy_status(app_name: str, op: str, status: int) -> None:
    """Record a proxy result; on the FIRST 404 for a declared route, warn once.

    This is the drift half of §4.2 — the manifest-vs-UI dead-path audit as a live
    signal instead of a later manual pass. A 404 means the app DECLARED a route
    its backend doesn't serve; the operator gets a single actionable notification."""
    if status != 404:
        return
    key = (app_name, op)
    if key in _drift_seen:
        return
    _drift_seen.add(key)
    try:
        # Reuse the process-wide dashboard state hook meant exactly for code paths
        # that run OUTSIDE an HTTP request (a tool invocation is one) — None until
        # the gateway registers it, in which case the drift is simply not surfaced.
        from personalclaw.inbox_providers.native_source import get_dashboard_state

        state = get_dashboard_state()
        if state is None:
            return
        state.notify(
            notification_kinds.APP_ROUTE_DRIFT,
            f"App {app_name!r} declares a dead route",
            f"Route {op!r} is declared in {app_name}'s app.json but its backend "
            f"returns 404. Fix the declared path in backend.routes[] or add the route.",
            meta={"app": app_name, "op": op},
        )
    except Exception:
        logger.debug("app-routes: drift notify failed for %s/%s", app_name, op, exc_info=True)


def reset_drift_state() -> None:
    """Clear the deduped drift set (tests + a clean ``/update`` re-arm)."""
    _drift_seen.clear()


# ── The provider ────────────────────────────────────────────────────────────


class AppRoutesToolProvider(ToolProvider):
    """One provider surfacing every enabled app's declared agent-callable routes.

    Stateless: ``list_tools`` re-reads the installed apps each call, so enabling/
    disabling/updating an app resyncs its tool surface with no registration churn.
    """

    @property
    def name(self) -> str:
        return PROVIDER_NAME

    @property
    def display_name(self) -> str:
        return "App Backend Routes"

    async def list_tools(self) -> list[ToolDefinition]:
        defs: list[ToolDefinition] = []
        for app_name, route in iter_app_routes():
            method = (route.method or "GET").upper()
            desc = route.summary or f"{method} {route.path} on app {app_name!r}"
            defs.append(
                ToolDefinition(
                    name=tool_name_for(app_name, route.op),
                    description=desc,
                    provider=self.name,
                    parameters=parameters_schema(route),
                    requires_approval=True,
                    risk_level=_METHOD_RISK.get(method, RiskLevel.CAUTION),
                )
            )
        return defs

    async def invoke(self, tool_name: str, arguments: dict[str, Any]) -> ToolResult:
        # Resolve WITHOUT parsing the composite name: find the (app, route) whose
        # generated tool name matches, so a hyphen/underscore in either half is
        # never ambiguous.
        for app_name, route in iter_app_routes():
            if tool_name_for(app_name, route.op) == tool_name:
                try:
                    resolution = resolve_route(app_name, route.op, arguments)
                except RouteError as exc:
                    return ToolResult(success=False, agent_error=exc.agent_error)
                return await call_app_route(resolution)
        return ToolResult(
            success=False,
            agent_error=AgentError(
                code="ERR_APP_ROUTE_UNKNOWN",
                what=f"no enabled app exposes the tool {tool_name!r}",
                why="the app is disabled/uninstalled, or the route is not agentCallable",
                fix="check the app is installed + enabled and the route is declared agentCallable",
            ),
        )


def register() -> None:
    """Register the single app-routes provider (idempotent). Called at startup."""
    from personalclaw.tool_providers.registry import get_provider, register_provider

    if get_provider(PROVIDER_NAME) is None:
        register_provider(AppRoutesToolProvider())


def app_surfaces() -> list[dict[str, Any]]:
    """``app_surfaces[]`` for ``/api/manifest`` §1 — per enabled app with declared
    routes: its route table + the generated tool name for each agent-callable one.

    Declared data only (no app code executed): the same ``backend.routes[]`` the
    tools are generated from, so the manifest and the live tool surface never drift
    from each other. A declared-but-not-callable route appears here (documented)
    with ``tool: null`` (not drivable)."""
    surfaces: list[dict[str, Any]] = []
    for name, manifest in _iter_enabled_apps():
        routes_out: list[dict[str, Any]] = []
        for route in manifest.backend.routes:
            if not route.op or not route.path:
                continue
            routes_out.append(
                {
                    "op": route.op,
                    "method": (route.method or "GET").upper(),
                    "path": route.path,
                    "summary": route.summary,
                    "agent_callable": route.agentCallable,
                    "tool": tool_name_for(name, route.op) if route.agentCallable else None,
                }
            )
        if routes_out:
            surfaces.append({"app": name, "routes": routes_out})
    surfaces.sort(key=lambda s: s["app"])
    return surfaces

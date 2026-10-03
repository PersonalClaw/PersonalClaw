"""Dashboard aiohttp application factory and startup."""

import asyncio
import errno
import logging
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from aiohttp import web

from personalclaw.config import config_dir
from personalclaw.dashboard import chat, handlers, housekeeping
from personalclaw.dashboard.handlers.knowledge import setup_knowledge_routes
from personalclaw.dashboard.handlers.research_reports import setup_research_report_routes
from personalclaw.dashboard.origin import build_allowed_origins, check_origin, resolve_bind_host
from personalclaw.dashboard.state import _DEFAULT_PORT, DashboardState
from personalclaw.dashboard.token_auth import token_auth_middleware
from personalclaw.hooks import ScriptHookStore, set_global_hook_store

if TYPE_CHECKING:
    from personalclaw.dashboard._types import (  # noqa: F401
        ContextBuilder,
        ConversationLog,
        HistoryConsolidator,
        SessionManager,
        SubagentManager,
    )

logger = logging.getLogger(__name__)


def _single_post_ceiling() -> int:
    """Body-size ceiling for the MAIN + API apps.

    These apps carry only small single-POST uploads (≤ the policy's single-POST
    threshold) + every non-upload endpoint, so their ceiling tracks the threshold
    + multipart overhead — kept deliberately tight. Large media uploads go through
    the resumable protocol on the dedicated 2 GB upload sub-app, never these apps."""
    from personalclaw.uploads import single_post_threshold

    return single_post_threshold() + 16 * 1024 * 1024  # threshold + multipart overhead


_DIST_DIR = Path(__file__).resolve().parent.parent / "static" / "dist"

#: How long a request still being answered when the gateway stops is given to finish, before the
#: server cancels it (and as long again to leave once cancelled). Every event stream ends the moment
#: the stop begins (`sse`), so what is left is a request waiting on something, most of all a model
#: call, whose answer would reach nobody: the page that asked loses its connection to this process
#: either way, and a restart does not wait on it. The server's own default was a minute, past the
#: stop's whole time limit.
OPEN_REQUEST_GRACE_SECS = 1.0


def _precompute_telemetry(state: "DashboardState") -> None:
    """Pre-compute telemetry data (blocking I/O — call before server starts)."""
    from personalclaw.dashboard.handlers_system import _get_owner_hash, _get_static_system_info

    _log = logging.getLogger(__name__)
    try:
        _get_owner_hash(state)
    except Exception:
        _log.warning("Failed to pre-compute owner hash", exc_info=True)
    try:
        _get_static_system_info()
    except Exception:
        _log.warning("Failed to pre-compute system info", exc_info=True)


def _register_mcp_routes(app: web.Application) -> None:
    """Register API routes used by MCP tools (spawn, lessons, crons, etc.)."""
    app.router.add_post("/api/spawn", handlers.api_spawn)
    app.router.add_post("/api/spawn/cancel-fanout", handlers.api_spawn_cancel_fanout)
    app.router.add_get("/api/spawn", handlers.api_spawn_list)
    app.router.add_get("/api/spawn/{agent_id}", handlers.api_spawn_status)
    app.router.add_delete("/api/spawn/{agent_id}", handlers.api_spawn_delete)
    app.router.add_delete("/api/spawn", handlers.api_spawn_clear)
    app.router.add_get("/api/lessons", handlers.api_lessons)
    app.router.add_post("/api/lessons", handlers.api_lessons_create)
    app.router.add_delete("/api/lessons", handlers.api_lessons_delete)
    # Unified Triggers (schedule + lifecycle) — facade over the schedule service
    # + the script-hook store (see dashboard/handlers/triggers.py).
    from personalclaw.dashboard.handlers.triggers import register_trigger_routes

    register_trigger_routes(app)
    app.router.add_post("/api/send-message", handlers.api_send_message)
    app.router.add_post("/api/session-keepalive", handlers.api_session_keepalive)
    app.router.add_get("/api/session-tool-policy", handlers.api_session_tool_policy)
    app.router.add_post("/api/channel/profile", handlers.api_channel_profile)
    app.router.add_get("/api/notifications", handlers.api_notifications)
    app.router.add_post("/api/notifications/clear", handlers.api_notifications_clear)

    # Auto-nudge (feature-flagged ON by default — returns 503 when PERSONALCLAW_AUTONUDGE=0)
    from personalclaw.dashboard.handlers.autonudge import (
        api_autonudge_delete,
        api_autonudge_get,
        api_autonudge_list,
        api_autonudge_start,
        api_autonudge_update,
    )

    app.router.add_get("/api/autonudge", api_autonudge_list)
    app.router.add_post("/api/autonudge", api_autonudge_start)
    app.router.add_get("/api/autonudge/session/{session_name}", api_autonudge_get)
    app.router.add_patch("/api/autonudge/{loop_id}", api_autonudge_update)
    app.router.add_delete("/api/autonudge/{loop_id}", api_autonudge_delete)


async def _start_site(site: web.TCPSite, port: int) -> None:
    """Start *site*, translating EADDRINUSE into an actionable message."""
    try:
        await site.start()
    except OSError as exc:
        if exc.errno == errno.EADDRINUSE:
            hint = (
                f"Port {port} already in use — is another PersonalClaw gateway running?\n"
                f"Stop it with: personalclaw stop  or  sudo systemctl stop personalclaw"
            )
            logger.error(hint)
            raise SystemExit(1) from exc
        raise


def _write_secret_file(secret_path: Path, secret: str) -> None:
    """Write *secret* to *secret_path* with mode 0o600.

    On failure the (possibly truncated) file is removed and the original
    ``OSError`` is re-raised.  Caller is responsible for any further
    cleanup (e.g. tearing down the app runner).
    """
    try:
        fd = os.open(str(secret_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        os.fchmod(fd, 0o600)  # enforce perms even if file already exists
        with os.fdopen(fd, "w") as f:
            f.write(secret)
    except OSError:
        try:
            secret_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _apply_startup_yolo(state: DashboardState, cfg: Any) -> None:
    """Enable dashboard YOLO at startup if ``agent.yolo=true`` in config.

    Mirrors the channel gateway's startup behavior and
    emits an SEL audit event so config-driven permission changes are captured
    in the audit trail, matching the UI-toggle path in ``chat.py``.
    """
    if not cfg.agent.yolo:
        return
    try:
        from personalclaw.sel import sel

        sel().log_api_access(
            caller="dashboard:startup",
            operation="mode_change:yolo",
            outcome="enabled",
            resources="config:agent.yolo",
        )
    except Exception:
        logger.error("SEL audit failed; refusing to enable YOLO mode from config", exc_info=True)
        return
    state.enable_yolo(from_config=True)
    logger.info("YOLO mode enabled at startup (agent.yolo=true)")


def _ws_csp_sources() -> str:
    """Extra `connect-src` entries for an internet-exposed instance (T4.1).

    Returns "" unless `dashboard.public_url` is set, so a normal local install keeps a
    byte-identical CSP. When set, both `wss://host` and `https://host` are added: the page is
    served over TLS through the tunnel, so the browser opens the WebSocket against the public
    origin rather than localhost, and a policy that omits it produces a dashboard that renders
    but never receives an event.
    """
    try:
        from personalclaw.dashboard.exposure import public_host

        host = public_host()
        if not host:
            return ""
        return f" wss://{host} https://{host}"
    except Exception:  # noqa: BLE001
        logger.debug("could not resolve the public host for the CSP", exc_info=True)
        return ""


# Content-hashed Vite output (`/assets/AgentsSection-<hash>.js`): the URL itself
# changes whenever the file's content does (Vite's own cache-busting), so the
# response can be cached forever — this is the ONLY prefix this middleware treats
# as immutable. Deliberately narrower than token_auth._BYPASS_PREFIXES: `/fonts/`,
# `/sprites/` and `/vendor/` also skip auth but are STABLE-named (unhashed), so
# long-lived caching them would serve stale content past a rebuild (#2933).
_IMMUTABLE_ASSET_PREFIX = "/assets/"

#: Response headers every dashboard response carries, unless the handler already set a
#: STRICTER value of its own (hence ``setdefault`` at the call site — artifact responses
#: pass through this middleware and deliberately send ``Referrer-Policy: no-referrer``).
#:
#: Secure-header practice sets these on ALL responses, not only on the risky ones. Before
#: #2735 they were applied ad hoc on specific artifact/file responses
#: (``artifacts/deploy.py``, ``artifacts/handlers.py``, ``dashboard/handlers/files.py``,
#: ``dashboard/session_starters.py``) and so were absent from dashboard responses
#: generally; promoting them here gives the posture one owner.
#:
#: ``SAMEORIGIN`` rather than ``DENY``: the dashboard frames its own artifact pane, and
#: ``DENY`` would break that in-app open. It is the legacy fallback for user agents that
#: predate ``frame-ancestors`` — both are shipped, because shipping only one of them is
#: how this class of gap survives a review.
SECURITY_HEADERS: dict[str, str] = {
    "X-Frame-Options": "SAMEORIGIN",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "X-Content-Type-Options": "nosniff",
}


def dashboard_csp(port: int | None = None) -> str:
    """The dashboard's Content-Security-Policy.

    Defense-in-depth layer. The primary control against markup injection is the web app's
    one renderer for text it did not write (``web/src/ui/Markdown.tsx``): embedded HTML is
    shown as text, bar attribute-free formatting tags, links open only for http/https/mailto,
    and no other path in the app turns a string into live markup
    (``tests/test_rendering_registry_parity.py``). This policy is what still holds if that
    ever slips.

    ``script-src`` must allow ``'unsafe-inline'``: widget iframes (blob: documents) inherit
    this policy, and a widget's own scripts and handlers are inline; the sign-in and pairing
    pages carry inline scripts too. Widget isolation is enforced by
    ``sandbox="allow-scripts"`` (an opaque origin: no parent DOM, cookies or storage) + a
    widget-level CSP meta (``connect-src 'none'``).

    No directive that loads code, a stylesheet or a font names another origin: the dashboard
    runs locally and fetches nothing from a third party. A widget's Tailwind CSS and a react
    artifact's React ride inside the widget's own document (``web/src/ui/widget/widgetStyles.ts``,
    ``reactFrameRuntime.ts``), so no CDN is owed an allowance. ``img-src https:`` is the one
    remote allowance, for the images in a rendered body.

    ``connect-src`` names the loopback WebSocket at *port* — the port this page was served
    on, the only one the app ever opens a socket to (``location.host``) — rather than every
    local port, which would let the page reach any other local service's socket. With no port
    known, ``'self'`` alone carries the app's own socket.

    ``form-action 'self'``: the app submits no form anywhere, and the directive does not fall
    back to ``default-src``, so without it a form in the page could post to any origin.

    A function rather than a constant because ``_ws_csp_sources()`` depends on
    ``dashboard.public_url``, which is config the operator can change without a restart, and
    the port is the served request's.
    """
    loopback_ws = f" ws://localhost:{port} ws://127.0.0.1:{port}" if port else ""
    return (
        "default-src 'self'; "
        # blob: in script-src enables dynamic ESM module loading for contributed
        # app UI bundles: the host rewrites a bundle's bare import specifiers
        # (react / @personalclaw/app-sdk / …) to same-origin-derived blob modules
        # that re-export the host's singletons. Blobs are origin-scoped; apps are
        # still gated by the permission system + SkillScanner at install.
        "script-src 'self' 'unsafe-inline' blob:; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: blob: https:; "
        # Monaco (locally bundled) inlines its codicon icon font as a data: URI;
        # without font-src the default-src 'self' fallback blocks it.
        "font-src 'self' data:; "
        # Behind a TLS-terminating tunnel the page is https,
        # so the browser upgrades the WS to wss:// against the PUBLIC host — which
        # this policy must name, or the dashboard loads and then silently has no
        # live connection (the worst failure shape: it looks fine and does nothing).
        # `_ws_csp_sources()` returns "" for a normal local install, leaving the
        # policy byte-identical to before.
        f"connect-src 'self'{loopback_ws}{_ws_csp_sources()}; "
        # What this page may FRAME (its artifact pane + blob: widget iframes).
        "frame-src 'self' blob:; "
        # Who may frame THIS page — the opposite question, and the one #2735 found
        # unanswered. `frame-ancestors` is NOT in CSP L3 the fallback list, so
        # `default-src 'self'` above does not cover it and its absence meant no
        # restriction at all. `'self'` matches artifacts/deploy.py's spelling and its
        # reasoning ("embeddable in the dashboard's own pane, nowhere else"); behind a
        # reverse proxy the document's origin IS `dashboard.public_url`, so `'self'`
        # already names the public host and no allowlist is owed.
        "frame-ancestors 'self'; "
        "worker-src 'self' blob:; "
        "form-action 'self'; "
        "object-src 'none'; base-uri 'self'"
    )


def _served_port(request: web.Request) -> int | None:
    """The port *request* reached this server on, read off the listening socket — never off
    the ``Host`` header, which the client writes."""
    sock = request.transport.get_extra_info("sockname") if request.transport else None
    if isinstance(sock, tuple) and len(sock) >= 2 and isinstance(sock[1], int) and sock[1] > 0:
        return sock[1]
    return None


@web.middleware  # type: ignore[misc]
async def _security_headers_middleware(
    request: web.Request,
    handler: object,
) -> web.StreamResponse:
    """Cache policy + the security headers every dashboard response carries.

    The outermost middleware, so it sees every handler's response — including the static
    handlers' and the artifact routes' — before anything else can react to it. Every
    header is a ``setdefault``: a handler that chose a STRICTER value keeps it, so a
    hardening change here can never downgrade a response that was already tighter.
    """
    # Read before the handler runs: a finished response may already have lost its transport.
    port = _served_port(request)
    resp = await handler(request)  # type: ignore[operator]
    if hasattr(resp, "headers"):
        if request.path.startswith(_IMMUTABLE_ASSET_PREFIX):
            # #2933: these bundles are content-addressed, so `no-store` bought
            # nothing but a full re-download of ~22 MB of JS/CSS on every load.
            # `public` is safe here — the route is unauthenticated (see
            # token_auth._BYPASS_PREFIXES) and carries no per-user data.
            resp.headers.setdefault("Cache-Control", "public, max-age=31536000, immutable")
        else:
            resp.headers.setdefault(
                "Cache-Control", "no-store, no-cache, must-revalidate, max-age=0"
            )
            resp.headers.setdefault("Pragma", "no-cache")
            resp.headers.setdefault("Expires", "0")
        resp.headers.setdefault("Content-Security-Policy", dashboard_csp(port))
        for name, value in SECURITY_HEADERS.items():
            resp.headers.setdefault(name, value)
    return resp  # type: ignore[return-value]


# SPA fallback: serve index.html for client-side React Router paths, and normalize
# the router's two refusals into the one wire envelope for /api/*.
@web.middleware  # type: ignore[misc]
async def spa_fallback(
    request: web.Request,
    handler: object,
) -> web.StreamResponse:
    try:
        return await handler(request)  # type: ignore[operator]
    except web.HTTPNotFound:
        # An unmatched /api/* route must answer in the one wire envelope — a JSON
        # client that mistypes or hits a removed route cannot parse aiohttp's
        # text/plain default, and so cannot tell "route gone" from "server broke".
        # Handlers that ANSWER 404 (rather than raising) are untouched here.
        if request.path.startswith("/api/"):
            from personalclaw.http_errors import json_error

            return json_error("not_found", status=404)
        # `/icons/` is excluded for the PWA: a manifest icon that resolves to
        # index.html is an invalid icon, and the only symptom is an install
        # prompt that never appears. A 404 is diagnosable; HTML is not.
        if request.method == "GET" and not request.path.startswith(
            ("/assets/", "/icons/", "/sprites/", "/vendor/")
        ):
            return await handlers.index(request)
        raise
    except web.HTTPMethodNotAllowed as exc:
        # A wrong method on a REAL /api/* route raises HTTPMethodNotAllowed, which
        # otherwise sails past the 404 branch and answers the very text/plain default
        # that branch exists to prevent. Normalize it to the same wire envelope. The
        # `Allow` header the router set (the methods that WOULD work) is preserved so
        # a client can still discover them.
        if request.path.startswith("/api/"):
            from personalclaw.http_errors import json_error

            allow = exc.headers.get("Allow")
            return json_error(
                "method_not_allowed",
                status=405,
                headers={"Allow": allow} if allow else None,
            )
        raise


@web.middleware  # type: ignore[misc]
async def app_permission_middleware(
    request: web.Request,
    handler: object,
) -> web.StreamResponse:
    """Enforce an app's declared ``permissions.api`` allowlist (A5).

    Only acts on requests carrying an app identity (``request["app"]`` set
    from an app-scoped token). A path the app didn't declare is rejected
    403 before the handler runs — the half an app's own BACKEND cannot talk its
    way past, since its token is the only credential it holds. Owner/dashboard
    requests (no app identity) pass.

    🪤 That is not a boundary on an app's FRONTEND (#492). An app's UI bundle is
    imported into the dashboard page itself, so a bare ``fetch`` from it carries
    the owner's cookie and no app identity, arrives indistinguishable from the
    dashboard's own request, and passes here by the rule above. Nothing on this
    side can tell the two apart — separating them needs a distinct ORIGIN for app
    bundles, which is why this is a disclosed limitation
    (``docs/security/limitations.md`` §4, surfaced at install consent) rather than
    a check that could be added here.

    The decision itself is ``permissions.app_request_denial``, not inline here, and this
    is a module-level function rather than a closure inside :func:`start_dashboard` so a
    test drives THIS middleware instead of a mirror of it (a mirror is free to drift from
    the boundary it claims to test). This half owns logging the refusal and shaping the
    response; the module owns what is refused.

    It hands the decision the matched route's canonical template as well as the path,
    because the per-route declarations (``permissions.ROUTE_AUTHZ``) are keyed on it:
    ``POST /api/triggers`` and ``POST /api/triggers/{id}/run`` share a prefix and not a
    verdict. A row that carries ``owns`` is then held to what is the calling app's own — the
    conversations it started, and the app itself (:func:`_ownership_denial`). An allowed app
    request runs inside ``scoped_to_app``, so a seam with no request in hand (the file
    explorer's root list) still knows who is asking."""
    from personalclaw.apps.permissions import (
        APP_SCOPED_PREFIXES,
        app_request_denial,
        scoped_to_app,
    )
    from personalclaw.request_validation import RequestValidationError

    app_name = request.get("app", "")
    if app_name and request.path.startswith(APP_SCOPED_PREFIXES):

        def _deny(reason: str) -> web.StreamResponse:
            from personalclaw.sel import sel

            try:
                sel().log_api_access(
                    caller=f"app:{app_name}",
                    operation=f"{request.method} {request.path}",
                    outcome="denied",
                    source="app_permissions",
                    resources=request.path,
                    error=reason,
                )
            except Exception:
                pass
            raise web.HTTPForbidden(
                # The reason rides in the body, so a developer reads the policy — "owner-only
                # capability …: the MCP servers this gateway launches" — rather than a bare 403.
                text=f"app {app_name!r} not permitted to access {request.path}: {reason}",
                content_type="text/plain",
            )

        resource = request.match_info.route.resource
        route = resource.canonical if resource is not None else ""
        reason = app_request_denial(app_name, request.path, method=request.method, route=route)
        if not reason:
            try:
                reason = await _ownership_denial(request, app_name, route)
            except RequestValidationError as exc:
                # A body the ownership check cannot read names nothing it could admit. This
                # middleware sits outside `request_boundary`, so it answers the refusal itself,
                # in the same envelope, before the handler runs.
                return exc.response
        if reason:
            return _deny(reason)
    if app_name:
        with scoped_to_app(app_name):
            return await handler(request)  # type: ignore[operator]
    return await handler(request)  # type: ignore[operator]


async def _ownership_denial(request: web.Request, app_name: str, route: str) -> str:
    """Why an app's request names something that is not the app's own, or ``""``.

    The ``owns`` half of a ``ROUTE_AUTHZ`` row (``permissions.OwnedTarget``): every target the row
    lists must name a conversation whose creating app is the caller
    (``DashboardState.session_creating_app``), or, for a target that names an app, the caller itself
    (a provider is registered under its app's name). Decided here, before the handler, for the
    reason the route table exists at all — the ownership check used to be copied into a dozen
    handlers, keyed on an origin tag an app could share by its name, and missing from thirty more —
    and so that a refused request loads nothing: the creator is read without rehydrating the
    conversation, and another app's settings are never opened.

    A body target reads the JSON body through ``json_object_body``, and aiohttp keeps the bytes,
    so the handler reads the same body after. An empty body names nothing, so an optional target
    passes. A body that is not a JSON object raises ``RequestValidationError``, which
    :func:`app_permission_middleware` answers: it runs outside ``request_boundary``.
    """
    from personalclaw.apps.permissions import AppMay, route_authz
    from personalclaw.request_validation import json_object_body

    authz = route_authz(request.method, route)
    if not isinstance(authz, AppMay) or not authz.owns:
        return ""
    body: dict = {}
    if any(target.in_body for target in authz.owns):
        body = await json_object_body(request)
    state = request.app.get("state")
    for target in authz.owns:
        named = body.get(target.field) if target.in_body else request.match_info.get(target.field)
        if target.app:
            if named != app_name:
                shown = named if isinstance(named, str) else f"a {type(named).__name__}"
                return f"{shown!r} is not this app — an app reaches only its own, never another's"
            continue
        if named is None or named == "":
            if target.optional:
                continue
            return (
                f"the request must name a conversation the app started in {target.field!r} — "
                "without one it reaches every conversation you have"
            )
        if (
            not isinstance(named, str)
            or state is None
            or state.session_creating_app(named) != app_name
        ):
            shown = named if isinstance(named, str) else f"a {type(named).__name__}"
            return (
                f"{shown!r} is not a conversation this app started — an app reaches only the "
                "conversations it started"
            )
    return ""


# ── What the gateway's internal credential opens ──────────────────────────────────────────────
#
# PersonalClaw's own processes call the gateway over loopback with its internal credential, the
# `X-Internal-Secret` the gateway writes to `<home>/.local_secret` each time it starts: an agent's
# tools (in the gateway, and in the `mcp-core` server an agent CLI runs), a scheduled script, the
# CLI. The credential opens exactly the operations below, each one method on one route written in
# the router's own syntax (`token_auth.InternalRoute`), and no other: an operation missing here is
# a tool that fails on every gateway that asks for a sign-in, and an entry nothing calls is a door
# the credential holds open for nobody. The rail
# `tests/test_every_internal_call_names_an_operation_that_takes_it.py` holds the list to the calls
# in the source, both ways.

#: Called only by PersonalClaw's processes, so from off this computer they are refused outright.
INTERNAL_ROUTES: frozenset[str] = frozenset(
    {
        "POST /api/send-message",  # `notify`, and a scheduled script's ctx.notify
        "POST /api/workflows/batches",  # `subagent_run` starts a batch it compiled
        "POST /api/workflows/agent-saves",  # `workflow_author` hands over a save it cannot make
        "POST /api/session-keepalive",  # `wait`
        "GET /api/session-tool-policy",  # an MCP server's per-session tool policy
        "GET /api/chat/sessions/model-reach",  # whether an `mcp-core` tool's chat keeps nothing
        # A webhook, relayed on this computer (docs/architecture/security.md#webhook-auth); the
        # route then checks the webhook's own token.
        "POST /api/hooks/agent",
        "POST /api/outbox/notify",  # `notify_attachment`
        "POST /api/channel/upload-file",  # `notify_attachment`
        "POST /api/tools/invoke",  # a scheduled script's ctx.call_tool
        # The computer-use shim in the `mcp-core` process. Deliberately not mixed: no browser
        # surface drives the desktop, and admitting cookie auth on this one route would put the
        # operator's keyboard behind the weakest browser path.
        "POST /api/computer-use/dispatch",
    }
)

#: Called by those processes AND by the dashboard, so a browser's session is judged here from any
#: address (a browser reaching the gateway through a forward or a container's port is not on
#: loopback), and a wrong credential beside it is still refused.
MIXED_INTERNAL_ROUTES: frozenset[str] = frozenset(
    {
        "GET /api/spawn",  # `subagent_list`
        "POST /api/spawn",  # `subagent_run`
        "GET /api/spawn/{agent_id}",  # `subagent_status`
        "GET /api/lessons",  # `memory_list`
        "POST /api/lessons",  # `memory_remember`
        "DELETE /api/lessons",  # `memory_forget`
        "GET /api/memory/recall",  # `memory_recall`
        "GET /api/memory/approval-rules",  # `triage_rules_list`
        "POST /api/memory/approval-rules",  # `triage_rules` add
        "DELETE /api/memory/approval-rules/{key:.+}",  # `triage_rules` revoke
        "POST /api/prompts/{name:.+}/render",  # `prompt_render`
        "GET /api/autonudge/session/{session_name}",  # `loop_nudge_stop` finds its loop ...
        "DELETE /api/autonudge/{loop_id}",  # ... and stops it
        "GET /api/chat/sessions/bound-project",  # the artifact tools' project, in `mcp-core`
        "GET /api/context",  # `get_context`
        "POST /api/triggers/{id}/run",  # `automation_run`, `personalclaw cron trigger`
        "POST /api/auth/rotate-key",  # `personalclaw auth rotate-key`
    }
)


async def start_dashboard(
    sessions: "SessionManager",
    port: int = _DEFAULT_PORT,
    subagents: "SubagentManager | None" = None,
    context_builder: "ContextBuilder | None" = None,
    conversation_log: "ConversationLog | None" = None,
    consolidator: "HistoryConsolidator | None" = None,
    local_only: bool = True,
    configured_host: str = "",
    dashboard_url: str = "",
    owner_id: str = "",
) -> tuple[web.AppRunner, DashboardState]:
    """Start the dashboard web server.  Returns ``(runner, state)``."""
    # Auto-create consolidator if conversation_log available but no consolidator
    if consolidator is None and conversation_log is not None:
        try:
            from personalclaw import history as _hist_mod
            from personalclaw.memory import MemoryStore

            memory = context_builder.memory if context_builder else MemoryStore()
            if not context_builder:
                memory.init()
            consolidator = _hist_mod.HistoryConsolidator(
                log=conversation_log,
                memory=memory,
            )
            logger.info("Auto-created HistoryConsolidator for dashboard")
        except Exception:
            logger.debug("Could not create consolidator", exc_info=True)

    # Extract skills from a session one last time when it idles out, then evict its
    # per-session MCP connections (rel-mcp-server-pooling #46). Composed so each
    # step runs alongside the others, none replacing consolidation.
    #
    # The session-scoped workflow sweep that sat between them died with the old
    # feature (WORKFLOWS-V2 Phase 1): ephemeral session-scoped SOPs were a property
    # of embedding-surfaced definitions. v2 runs are durable and engine-owned, so a
    # session expiring must NOT delete them — if a v2 cleanup hook is ever needed it
    # belongs on run retention, not session expiry.
    if sessions is not None:
        from personalclaw.mcp_client import with_mcp_session_eviction

        prior = consolidator.consolidate_session if consolidator is not None else None
        sessions.set_session_expire_callback(with_mcp_session_eviction(prior))

    state = DashboardState(
        sessions=sessions,
        start_time=time.time(),
        subagents=subagents,
        context_builder=context_builder,
        conversation_log=conversation_log,
        consolidator=consolidator,
        owner_id=owner_id,
    )

    # Initialize script hook store
    state._hook_store = ScriptHookStore()
    set_global_hook_store(state._hook_store)

    # Wire the always-on native inbox sink so agent post_to_inbox writes + pushes
    # through this state (works even with no polling inbox service).
    from personalclaw.inbox_providers.native_source import set_dashboard_state as _set_inbox_state

    _set_inbox_state(state)

    # Wire the native hook providers' service accessor (notify/send-message/
    # create-task reach DashboardState through it, invoke-agent/run-prompt the subagents).
    from personalclaw.action_providers.services import ActionServices, set_action_services

    set_action_services(ActionServices(state=state, subagents=state.subagents))

    # Wire script hooks into subagent tool execution path
    if state.subagents is not None:
        state.subagents.hook_store = state._hook_store

    # Visible notice + pct reset when auto-compaction fires on a dashboard session
    state.wire_session_compact_callback()

    app = web.Application(
        client_max_size=_single_post_ceiling()
    )  # small single-POST uploads only; large media → resumable upload sub-app
    app["state"] = state
    state.load_folders()
    state.load_tags()
    app["port"] = port
    from personalclaw.auth.modes import AuthConfig as _AuthConfig

    app["auth_cfg"] = _AuthConfig.from_env()

    _precompute_telemetry(state)

    # MCP tool routes (shared with start_api_server)
    _register_mcp_routes(app)

    # Install persistent log ring buffer (captures logs even when Logs page is closed)
    ring_handler = handlers.install_log_ring_handler()
    if ring_handler:
        ring_handler.set_state(state)

    # The page and API routes, in the order the router matches them (`dashboard/routes.py`).
    from personalclaw.dashboard.routes import register_dashboard_routes

    register_dashboard_routes(app)

    # Extension system — discover and register provider extensions
    from personalclaw.providers.entity_routes import register_entity_routes
    from personalclaw.providers.instance_routes import register_instance_routes
    from personalclaw.providers.loader import load_all_extensions
    from personalclaw.providers.routes import register_routes as register_extension_routes

    load_all_extensions()
    # Every tool provider just registered has its tool names read once, in the background: the
    # one-name-one-provider rule (`tool_providers.registry`) refuses one offering a name another
    # provider holds, and that refusal belongs on its status from start-up, not from whenever
    # an agent turn first lists it. Not awaited: reading the MCP servers' lists connects to them.
    from personalclaw.tool_providers.registry import admit as _admit_tool_names

    await _admit_tool_names(wait=0)
    # Move any secret an earlier release left inline in a settings file (a provider key or the
    # webhook token in config.json, an app's tokens in its data/config.json, an instance's key,
    # an MCP server's env and headers in mcp.json and the agent config) into the
    # credential store. HERE: after extensions load, so every app's declared-sensitive fields
    # are known, and before the registry sync below reads config.json. Idempotent and
    # fail-safe per file — a key it cannot move keeps working where it is.
    from personalclaw.config.secret_refs import migrate_plaintext_secrets

    try:
        migrate_plaintext_secrets()
    except Exception:  # noqa: BLE001 — never block boot; the next start retries
        logger.warning("moving plaintext secrets into the credential store failed", exc_info=True)
    # And `credentials.json`, the second store an earlier release kept, before the registry
    # sync below resolves a provider entry's `credential` by name. Deleted only once every value
    # in it reads back from the store; what it cannot settle, the Doctor lists.
    from personalclaw.llm.credentials import move_credentials_file

    try:
        move_credentials_file()
    except Exception:  # noqa: BLE001 — never block boot; the next start retries
        logger.warning("moving credentials.json into the credential store failed", exc_info=True)
    # And the browse "profile keys" an earlier release minted and nothing ever used, which the
    # Secrets page would list and could not delete (`browse.handoff.forget_unused_profile_keys`).
    from personalclaw.browse.handoff import forget_unused_profile_keys

    try:
        forget_unused_profile_keys()
    except Exception:  # noqa: BLE001 — never block boot; the next start retries
        logger.warning("deleting the unused browse profile keys failed", exc_info=True)
    # Sync config.json provider entries into the LLM registry IMMEDIATELY after
    # extensions load (types are now registered). Must happen BEFORE any handler
    # resolves a provider (e.g. embedding/knowledge auto-embed at boot).
    from personalclaw.llm.registry import sync_entries_from_config
    from personalclaw.providers.use_cases import migrate_legacy_bindings

    try:
        migrate_legacy_bindings()
    except Exception:
        pass
    sync_entries_from_config()
    register_extension_routes(app)
    register_instance_routes(app)
    register_entity_routes(app)

    # Knowledge Library
    setup_knowledge_routes(app)
    setup_research_report_routes(app)

    # The gateway's start and stop hooks, in the order they run (`dashboard/lifecycle_hooks.py`),
    # appended before `runner.setup()` freezes `on_startup` and `on_cleanup`.
    from personalclaw.dashboard.lifecycle_hooks import register_lifecycle_hooks

    register_lifecycle_hooks(app)

    # Chunk the items that predate chunking from the graph-maintenance host, NOT a
    # boot hook. Chunk-level retrieval only reaches items that HAVE chunks, and a hook only
    # fires at gateway start — so on a gateway that stays up for a week, every item ingested
    # afterwards would never gain deep-document recall. Registering is synchronous
    # and free; the work itself runs bounded per batch on every due maintenance tick.
    from personalclaw.dashboard.embedding_reindex import register_chunk_backfill_pass

    register_chunk_backfill_pass()

    # Static files — React build under /assets, packaged static assets under /static
    if _DIST_DIR.is_dir():
        app.router.add_static(
            "/assets",
            _DIST_DIR / "assets" if (_DIST_DIR / "assets").is_dir() else _DIST_DIR,
            show_index=False,
            append_version=True,
        )
        if (_DIST_DIR / "sprites").is_dir():
            app.router.add_static("/sprites", _DIST_DIR / "sprites", show_index=False)
        # Web fonts referenced at the absolute path /fonts/*.woff2 by fonts.css. Without
        # this route they fell through to the SPA catch-all (→ index.html, decoded as a
        # font → "invalid sfntVersion"), so the app silently rendered in system-font
        # fallbacks instead of Google Sans Flex/Code (incl. the code editor's mono). A
        # dedicated handler (not add_static) so the Content-Type is stated, never guessed
        # — aiohttp's FileResponse defaults .woff2 to application/octet-stream (#2916).
        app.router.add_get("/fonts/{name}", handlers.font_asset)
        # PWA app icons the manifest declares at stable, unhashed paths (they are
        # referenced from JSON, so they cannot carry a content hash). Also listed in
        # spa_fallback's exclusions below: a missing icon must 404, because HTML
        # returned for an icon URL makes the manifest entry invalid and the install
        # prompt then just never appears.
        if (_DIST_DIR / "icons").is_dir():
            app.router.add_static(
                "/icons",
                _DIST_DIR / "icons",
                show_index=False,
                append_version=False,  # stable URLs — the manifest names them literally
            )
        # Vendor shims for the app import map (react, react-dom, react/jsx-runtime)
        if (_DIST_DIR / "vendor").is_dir():
            app.router.add_static(
                "/vendor",
                _DIST_DIR / "vendor",
                show_index=False,
                append_version=False,  # stable URLs, no cache-busting
            )
        logger.info("Serving React build from %s", _DIST_DIR)

    # ── Middleware ────────────────────────────────────────────────────────────

    # CSRF: block state-mutating requests from cross-origin pages
    _safe_methods = {"GET", "HEAD", "OPTIONS"}

    # SEL: log mutating API operations
    _sel_log_methods = {"POST", "PUT", "DELETE", "PATCH"}

    @web.middleware  # type: ignore[misc]
    async def sel_audit_middleware(
        request: web.Request,
        handler: object,
    ) -> web.StreamResponse:
        if request.method in _sel_log_methods and request.path.startswith("/api/"):
            from personalclaw.sel import sel

            try:
                resp = await handler(request)  # type: ignore[operator]
                sel().log_api_access(
                    caller="dashboard_user",
                    operation=f"{request.method} {request.path}",
                    outcome="ok" if resp.status < 400 else "error",
                    resources=request.path,
                )
                return resp  # type: ignore[return-value]
            except Exception as exc:
                sel().log_api_access(
                    caller="dashboard_user",
                    operation=f"{request.method} {request.path}",
                    outcome="error",
                    resources=request.path,
                    error=str(exc)[:200],
                )
                raise
        return await handler(request)  # type: ignore[operator]

    app["allowed_origins"] = build_allowed_origins(port, local_only, configured_host)

    @web.middleware  # type: ignore[misc]
    async def csrf_middleware(
        request: web.Request,
        handler: object,
    ) -> web.StreamResponse:
        if request.method not in _safe_methods:
            if not check_origin(request, require=True, fallback_header="Referer"):
                # The one wire error envelope, not plain text: the login page (and the
                # FE error funnel generally) branches on {"error": {"code"}}, and a
                # text body parsed as JSON became {} — which the login page then
                # reported as "Wrong username or password." for a correct password
                # from any non-loopback origin. Same code the auth routes return for
                # their own origin rejections; `origin_refusal` says what was refused.
                from personalclaw.dashboard.origin import origin_refusal

                return origin_refusal(request)
        return await handler(request)  # type: ignore[operator]

    # Generate per-session secret for local app / IPC authentication.
    # NOTE: file write deferred until after port bind succeeds to avoid
    # poisoning the secret file when a second instance fails to start.
    _secret_path = config_dir() / ".local_secret"
    _secret_path.parent.mkdir(parents=True, exist_ok=True)
    _internal_secret = os.urandom(16).hex()
    app["local_secret"] = _internal_secret

    # AuthMode.NONE (PERSONALCLAW_AUTH_MODE=none) — dev convenience: skip the CSRF +
    # token-auth middlewares so localhost needs no token. effective_bind() forces the
    # bind to loopback in this mode, so the gateway stays unreachable off-host.
    from personalclaw.auth.modes import AuthMode as _AuthMode

    _no_auth = app["auth_cfg"].mode == _AuthMode.NONE
    if _no_auth:
        logger.warning("PERSONALCLAW_AUTH_MODE=none — token auth DISABLED (loopback only)")

    @web.middleware
    async def _dev_user_middleware(request: web.Request, handler: object) -> web.StreamResponse:
        # In AuthMode.NONE the token-auth middleware is skipped, but many handlers
        # (terminal, loops, durability, core) authenticate by reading request["user"]
        # which that middleware normally sets. Populate it so they don't 401.
        request["user"] = request.get("user") or "dev-local"
        # App identity must survive none-mode too: token_auth normally adopts the
        # ``app`` claim from an app-scoped token (Authorization: Bearer for fetch,
        # ?app_token= for the WS handshake) so app_permission_middleware + the WS
        # event filter can scope the request. Skipping this here silently DISABLED
        # the entire app permission sandbox in none-mode (an app-scoped request
        # reached ANY /api path). The app token only NARROWS the dev owner's reach.
        # Session identity must survive none-mode too, for the same reason the app claim
        # must: token_auth normally records WHICH session authorized the request, and
        # `_paired_device` / the `/api/ws` origin-less upgrade read nothing else. Skipping
        # it here silently disabled EVERY paired-device distinction in none-mode — pairing
        # succeeded and `POST /api/browse/connector` then refused that device's own cookie
        # as unpaired. Only a token that fully validates names a session.
        if not request.get("session_nonce"):
            from personalclaw.dashboard.token_auth import presented_session_nonce

            request["session_nonce"] = presented_session_nonce(request, port)
        if not request.get("app"):
            from personalclaw.dashboard.token_auth import validate_token_with_app

            app_token = ""
            _auth = request.headers.get("Authorization", "")
            if _auth.startswith("Bearer "):
                app_token = _auth[7:].strip()
            if not app_token:
                app_token = request.query.get("app_token", "")
            if app_token:
                a_valid, _a_user, _reason, a_app = validate_token_with_app(app_token)
                if a_valid and a_app:
                    request["app"] = a_app
        return await handler(request)  # type: ignore[operator]

    # The ONE place a client's declared API version is compared against the
    # supported window. Placed immediately after no_cache and BEFORE csrf/token auth
    # deliberately: a stale cached bundle whose session cookie is still valid should
    # read "your build is too old, reload" rather than a 403 from a layer it would
    # actually pass. It publishes nothing to a caller who declares nothing — the
    # refusal only fires on an explicit out-of-window declaration — and its exemption
    # list (healthz, the manifest, the pre-session front door, WS upgrades, and
    # everything outside /api/) is enumerated with reasons in api_version_gate.py.
    from personalclaw.dashboard.api_version_gate import api_version_middleware
    from personalclaw.dashboard.consent_ask import consent_ask_middleware
    from personalclaw.dashboard.invalid_id_gate import invalid_id_middleware
    from personalclaw.dashboard.memory_write_gate import memory_write_middleware
    from personalclaw.dashboard.request_boundary import request_boundary_middleware

    # Explicit middleware ordering — self-documenting and immune to future insertions
    app.middlewares[:] = [
        _security_headers_middleware,
        api_version_middleware(),
        *(
            [_dev_user_middleware]
            if _no_auth
            else [
                csrf_middleware,
                token_auth_middleware(
                    internal_routes=INTERNAL_ROUTES,
                    mixed_internal_routes=MIXED_INTERNAL_ROUTES,
                    internal_secret=_internal_secret,
                    port=port,
                    local_only=local_only,
                ),
            ]
        ),
        app_permission_middleware,
        # The owner's consent question as an answer to a client that asks one, and as the 400
        # refusal to any other. OUTSIDE the security log's audit, which records the refusal as the
        # handler made it. See consent_ask.py.
        consent_ask_middleware,
        sel_audit_middleware,
        memory_write_middleware(),  # a session's request writes as that session (see its module)
        # Maps an unguarded request-shape fault (a non-object JSON body, a non-numeric
        # query/path param) raised by the handler to the one 400 wire envelope, so a
        # malformed request never escapes as aiohttp's bare `500 text/plain`. Sits just
        # OUTSIDE invalid_id so that gate (whose UnsafeRecordId is not a ValueError) still
        # runs closest to the handler and is never shadowed. See request_boundary.py.
        request_boundary_middleware(),
        # INNERMOST: wraps the handler and nothing else, so it maps a store's refusal of
        # an unsafe record id to a 400 without also catching one raised by a middleware
        # (which would be a bug, not a client error). See invalid_id_gate.py.
        invalid_id_middleware(),
        spa_fallback,
    ]

    # Verify security invariant: if dashboard_url expands the CSRF origin
    # set for a remote URL, token auth middleware MUST be active.
    if dashboard_url:
        _has_token_auth = any(getattr(mw, "_is_token_auth", False) for mw in app.middlewares)
        if _has_token_auth:
            app["allowed_origins"] = build_allowed_origins(
                port, local_only, configured_host, dashboard_url
            )
            logger.info(
                "dashboard_url=%s: added to CSRF allowed origins (token auth verified)",
                dashboard_url,
            )
        else:
            logger.error(
                "dashboard_url=%s requires token auth — refusing to start without it. "
                "Connect a channel or remove dashboard.url from config.",
                dashboard_url,
            )
            raise RuntimeError("dashboard_url requires token auth middleware")

    runner = web.AppRunner(app, shutdown_timeout=OPEN_REQUEST_GRACE_SECS)
    await runner.setup()
    # Bind decision: prefer the explicit PERSONALCLAW_BIND_HOST env var
    # (corp-host / DevSpaces escape hatch); otherwise derive from the
    # caller's local_only flag (the loopback invariant in effective_bind()
    # makes AuthMode.NONE override this).
    _bind_host = resolve_bind_host()
    if _bind_host == "127.0.0.1" and not local_only:
        _bind_host = "0.0.0.0"
    # AuthMode.NONE invariant: an unauthenticated gateway must never leave loopback.
    if _no_auth:
        _bind_host = "127.0.0.1"
    site = web.TCPSite(runner, _bind_host, port)
    await _start_site(site, port)

    # Port bind succeeded — now safe to write the secret file
    try:
        _write_secret_file(_secret_path, _internal_secret)
    except OSError:
        await runner.cleanup()
        raise

    # Optional LAN discovery. STARTED here, after the site is up, because
    # the bind host is an OUTCOME (env var, then local_only, then the AuthMode.NONE loopback
    # invariant above) rather than a config value — the advertiser must be told where the
    # gateway actually landed, not guess. Off unless companion.discovery_enabled; a
    # loopback-only bind is a deliberate no-op with a log line naming the fix. The matching
    # shutdown is registered in the app factory, since the app is frozen by runner.setup().
    try:
        from personalclaw.companion import discovery as _discovery

        _discovery.set_gateway_bind(_bind_host, port)
        _discovery.reconcile()
    except Exception:
        # Discovery is a convenience over a path that already works (type the URL). It may
        # never be the reason a gateway fails to start.
        logger.warning("LAN discovery failed to start", exc_info=True)

    # Fire background MCP probe at startup (non-blocking)
    asyncio.create_task(handlers._bg_mcp_probe())

    # Start the MCP idle-connection sweeper (rel-mcp-server-pooling #46): reaps
    # connections unused past the TTL so resident MCP memory tracks active use.
    try:
        from personalclaw.mcp_client import get_mcp_client_registry

        get_mcp_client_registry().start_sweeper()
    except Exception:
        logger.debug("MCP idle sweeper start skipped", exc_info=True)

    # Start terminal orphan reaper (kills PTYs with no WS for >5 min)
    _reaper = asyncio.create_task(handlers.reap_orphaned_terminals(app))
    _reaper.add_done_callback(lambda t: t.result() if not t.cancelled() else None)
    state._terminal_reaper = _reaper  # prevent GC

    # Apply the security-event log's retention at startup + periodically (its size is bounded
    # by rotation; see `housekeeping.sel_prune_loop`).
    state._sel_prune_task = asyncio.create_task(housekeeping.sel_prune_loop())  # prevent GC

    # Sweep abandoned resumable-upload session dirs (partial parts) so a never-
    # finished large upload can't pin disk forever.
    state._upload_sweep_task = asyncio.create_task(housekeeping.upload_sweep_loop())  # prevent GC

    # Scheduled backups: nightly snapshot with tiered
    # retention, hourly incremental shard export, monthly restore drill. Started
    # here so durability never depends on remembering to run a command; the drill
    # reports through state.notify so a FAILED one is a warning the user sees.
    try:
        from personalclaw.durability.service import DurabilityService

        state._durability_svc = DurabilityService(notifier=state.notify)  # prevent GC
        await state._durability_svc.start()
    except Exception:
        logger.warning("Durability service failed to start", exc_info=True)

    # Watched-source poll engine: the single re-armed loop that
    # polls enrolled poll-capable knowledge providers on schedule and writes new items
    # through the one ingest path. Started here so it recovers pending ingestion on boot;
    # fully best-effort — a source-engine fault never blocks or crashes startup.
    try:
        from personalclaw.knowledge.source_engine import SourceEngine
        from personalclaw.knowledge_providers.dir_source import DirSourceProvider
        from personalclaw.knowledge_providers.feed_source import FeedSourceProvider
        from personalclaw.knowledge_providers.registry import register_provider
        from personalclaw.knowledge_providers.web_source import WebSourceProvider

        # Watched local directories are a CORE source kind, so the
        # observer is registered here rather than through an app: without this the engine
        # would enrol no provider for a `watched-dir` source and every dir source the user
        # created would sit permanently unpolled.
        register_provider(DirSourceProvider(state.knowledge_store))
        # Watched feeds are core for the same reason — a `watched-feed` source with no
        # enrolled provider is an inert row, so the provider ships registered or not at all.
        register_provider(FeedSourceProvider(state.knowledge_store))
        # Watched pages — the five-detector kind. Same reasoning: a `watched-page` row
        # with no enrolled provider would be a source the user created and nothing polls.
        register_provider(WebSourceProvider(state.knowledge_store))
        state._source_engine = SourceEngine(  # prevent GC
            state.knowledge_store,
            state.knowledge_ingest_queue(),
        )
        state._source_engine.start()
    except Exception:
        logger.warning("Source engine failed to start", exc_info=True)

    # Artifacts as an indexed knowledge source. Separate
    # try-block from the poll engine on purpose: the mirror is event-driven and enrolls no
    # poll-capable provider, so a source-engine fault must not take the mirror down with it
    # (and vice versa). Held on `state` so the change subscription is not garbage-collected.
    try:
        from personalclaw.knowledge import artifact_ingest

        # The mirror follows artifacts on its own, so its items wait in the background lane.
        state._artifact_indexer = artifact_ingest.start(
            state.knowledge_store,
            enqueue=state.knowledge_ingest_queue().enqueue_background,
        )
    except Exception:
        logger.warning("Artifact knowledge mirror failed to start", exc_info=True)

    # Every open page that shows an artifact re-reads it once the store has changed it, whoever
    # changed it (`DashboardState.announce_artifact_change`). Unsubscribed on stop by
    # `_artifact_refresh_shutdown`, so a later gateway in the same process is the one told.
    from personalclaw.artifacts import changes as artifact_changes

    artifact_changes.subscribe(state.announce_artifact_change)

    # Entries a watched feed or page stored before they were converted on the way in are
    # still their markup; convert them once, a batch at a time, without holding up the start.
    # Keyed on what each body still holds, so every later start finds nothing to do.
    async def _convert_stored_markup() -> None:
        from personalclaw.knowledge import stored_markup

        try:
            converted = await stored_markup.convert_in_batches(state.knowledge_store)
        except Exception:
            logger.warning("Converting watched items stored as markup failed", exc_info=True)
            return
        if converted:
            logger.info("Knowledge: converted %d watched item(s) stored as markup", converted)

    state._stored_markup_task = asyncio.create_task(_convert_stored_markup())  # prevent GC

    # Start periodic flush loop for crash protection (saves dirty sessions every 5s)
    state.start_flush_loop()

    # Restore sessions — always restore foldered/pinned sessions; optionally restore recent ones.
    # NOTE: Even with restore_sessions=false, foldered and pinned sessions are restored
    # so the Explorer tree stays populated.  Users can unpin or remove from folder to dismiss.
    from personalclaw.config.loader import AppConfig

    cfg = AppConfig.load()
    _apply_startup_yolo(state, cfg)
    restored = chat.restore_recent_sessions(
        state,
        cfg.dashboard.restore_window_minutes if cfg.dashboard.restore_sessions else 0,
        folders_only=not cfg.dashboard.restore_sessions,
    )
    if restored:
        logger.info("Restored %d session(s)", restored)

    return runner, state

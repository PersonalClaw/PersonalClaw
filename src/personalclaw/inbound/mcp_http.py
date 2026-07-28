"""`POST /mcp` — JSON-RPC 2.0 over HTTP (MCP-READONLY-INBOUND §C2).

The minimum an MCP client needs to connect and enumerate: `initialize`,
`tools/list`, `tools/call`. Deliberately NOT implemented:

* **No SSE stream** (`GET /mcp` → 405). The spec permits a POST-only server, and a
  long-lived stream is a second lifecycle to get right for no v1 benefit.
* **No batch requests.** A batch multiplies one authenticated request into N
  handler invocations, which complicates every cap (rate, deadline, result size).
  Refused with a clear JSON-RPC error rather than half-supported.

Order of checks matters and is deliberate: enablement → peer → token → rate →
concurrency → body size → parse. Each rejects with the least information that
still lets a legitimate client fix its call, and every rejection is audited.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from aiohttp import web

from personalclaw.inbound import audit as audit_mod
from personalclaw.inbound import auth
from personalclaw.inbound import caps as caps_mod

logger = logging.getLogger(__name__)

SURFACE = "mcp"
# The MCP protocol revision this surface implements.
PROTOCOL_VERSION = "2024-11-05"

# JSON-RPC 2.0 error codes (the spec's reserved range).
_PARSE_ERROR = -32700
_INVALID_REQUEST = -32600
_METHOD_NOT_FOUND = -32601
_INVALID_PARAMS = -32602
_INTERNAL_ERROR = -32603


def _rpc_error(request_id: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _rpc_result(request_id: Any, result: Any) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _json(payload: dict, status: int = 200) -> web.Response:
    # no-store on every response: an inbound answer may contain the user's own
    # data and must not sit in an intermediary cache.
    return web.json_response(payload, status=status, headers={"Cache-Control": "no-store"})


def enablement_problem() -> str | None:
    """Why the surface must not mount, or None when it may.

    Fail-CLOSED: a missing or unreadable `inbound.mcp.enabled` reads DISABLED. An
    inbound network surface that turns itself on because config was unparseable is
    the wrong direction to fail, so this returns a reason instead of a default-on.
    """
    try:
        from personalclaw.config.loader import AppConfig

        cfg = AppConfig.load()
        enabled = bool(getattr(getattr(cfg.inbound, SURFACE, None), "enabled", False))
    except Exception:  # noqa: BLE001
        return "config unreadable (inbound stays off — the safe state)"
    if not enabled:
        return "inbound.mcp.enabled is off"
    return auth.token_problem(SURFACE)


async def handle_mcp_get(request: web.Request) -> web.Response:
    """`GET /mcp` → 405. No SSE stream in v1 (spec-permitted)."""
    audit_mod.audit(SURFACE, route="GET /mcp", status=405, refused="GET not supported")
    return _json(
        {"error": "This MCP surface is POST-only (no SSE stream). Use POST /mcp."},
        status=405,
    )


async def handle_mcp(request: web.Request) -> web.Response:
    started = time.monotonic()
    bytes_in = 0

    def _done(status: int, payload: dict, refused: str = "", tool: str = "") -> web.Response:
        body = json.dumps(payload)
        audit_mod.audit(
            SURFACE,
            route="POST /mcp",
            status=status,
            bytes_in=bytes_in,
            bytes_out=len(body),
            duration_ms=int((time.monotonic() - started) * 1000),
            refused=refused,
            tool=tool,
        )
        return _json(payload, status=status)

    # 1) Enablement — re-checked per request, so flipping the config kill switch
    #    takes effect on the next call rather than needing a restart.
    problem = enablement_problem()
    if problem:
        return _done(404, {"error": "not found"}, refused=f"disabled: {problem}")

    # 2) Peer, then 3) token. Peer first: a non-loopback caller shouldn't get to
    #    probe token validity at all.
    peer_ok, peer_reason = auth.peer_allowed(request, SURFACE)
    if not peer_ok:
        return _done(403, {"error": "forbidden"}, refused=peer_reason)

    presented = ""
    header = request.headers.get("Authorization", "")
    if header.startswith("Bearer "):
        presented = header[len("Bearer ") :].strip()
    if not auth.verify_bearer(SURFACE, presented):
        return _done(401, {"error": "unauthorized"}, refused="bad or missing bearer token")

    # 4) Rate, keyed per peer so one noisy client can't starve another.
    client_key = request.headers.get("Host", "") + "|" + (request.remote or "")
    if not caps_mod.check_rate(SURFACE, client_key):
        payload = {"error": "rate limited"}
        body = json.dumps(payload)
        audit_mod.audit(
            SURFACE,
            route="POST /mcp",
            status=429,
            bytes_in=bytes_in,
            bytes_out=len(body),
            duration_ms=int((time.monotonic() - started) * 1000),
            refused="rate limit",
        )
        return web.json_response(
            payload,
            status=429,
            headers={
                "Cache-Control": "no-store",
                "Retry-After": str(caps_mod.retry_after_secs(SURFACE)),
            },
        )

    # 5) Concurrency.
    if not caps_mod.acquire_slot(SURFACE):
        return _done(503, {"error": "too many concurrent requests"}, refused="concurrency cap")

    try:
        # 6) Body size, checked BEFORE reading the whole body where possible.
        declared = request.content_length or 0
        if declared > caps_mod.DEFAULT_CAPS.body_bytes:
            return _done(413, {"error": "request too large"}, refused="body cap (declared)")
        raw = await request.content.read(caps_mod.DEFAULT_CAPS.body_bytes + 1)
        bytes_in = len(raw)
        if bytes_in > caps_mod.DEFAULT_CAPS.body_bytes:
            return _done(413, {"error": "request too large"}, refused="body cap")

        # 7) Parse.
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return _done(200, _rpc_error(None, _PARSE_ERROR, "invalid JSON"), refused="parse error")

        if isinstance(payload, list):
            return _done(
                200,
                _rpc_error(None, _INVALID_REQUEST, "batch requests are not supported"),
                refused="batch rejected",
            )
        if not isinstance(payload, dict):
            return _done(
                200,
                _rpc_error(None, _INVALID_REQUEST, "request must be a JSON-RPC object"),
                refused="non-object request",
            )

        request_id = payload.get("id")
        method = str(payload.get("method", ""))
        params: Any = payload.get("params") or {}
        if not isinstance(params, dict):
            return _done(200, _rpc_error(request_id, _INVALID_PARAMS, "params must be an object"))

        if method == "initialize":
            return _done(
                200,
                _rpc_result(
                    request_id,
                    {
                        "protocolVersion": PROTOCOL_VERSION,
                        "capabilities": {"tools": {}},
                        "serverInfo": _server_info(),
                    },
                ),
            )

        if method == "tools/list":
            from personalclaw.inbound.tools import list_tools

            return _done(200, _rpc_result(request_id, {"tools": list_tools()}))

        if method == "tools/call":
            from personalclaw.inbound.tools import call_tool

            name = str(params.get("name", ""))
            arguments = params.get("arguments") or {}
            if not isinstance(arguments, dict):
                return _done(
                    200, _rpc_error(request_id, _INVALID_PARAMS, "arguments must be an object")
                )
            try:
                result = await call_tool(name, arguments, request.app.get("state"))
            except KeyError:
                return _done(
                    200,
                    _rpc_error(request_id, _METHOD_NOT_FOUND, f"unknown tool {name!r}"),
                    refused="unknown tool",
                    tool=name,
                )
            except ValueError as exc:
                return _done(200, _rpc_error(request_id, _INVALID_PARAMS, str(exc)), tool=name)
            except Exception:  # noqa: BLE001
                logger.warning("inbound: tool %r failed", name, exc_info=True)
                return _done(
                    200,
                    _rpc_error(request_id, _INTERNAL_ERROR, "tool execution failed"),
                    tool=name,
                )
            return _done(200, _rpc_result(request_id, result), tool=name)

        # Notifications (no id) for unknown methods get no error body per spec.
        if request_id is None:
            return _done(200, {"jsonrpc": "2.0", "result": None, "id": None})
        return _done(
            200,
            _rpc_error(request_id, _METHOD_NOT_FOUND, f"unknown method {method!r}"),
            refused=f"unknown method {method!r}",
        )
    finally:
        caps_mod.release_slot(SURFACE)


def _server_info() -> dict:
    try:
        from personalclaw import __version__

        version = __version__
    except Exception:  # noqa: BLE001
        version = "0"
    return {"name": "personalclaw", "version": version}


def mount(app: web.Application) -> bool:
    """Mount `/mcp` when enablement passes. Returns whether it mounted.

    A refusal logs ONE line naming the failing condition — "inbound disabled" with
    no cause is the kind of message that costs an hour of debugging.
    """
    problem = enablement_problem()
    if problem:
        logger.info("inbound: /mcp NOT mounted — %s", problem)
        return False
    app.router.add_post("/mcp", handle_mcp)
    app.router.add_get("/mcp", handle_mcp_get)
    logger.info("inbound: /mcp mounted (loopback-only unless allow_remote + public_url)")
    return True

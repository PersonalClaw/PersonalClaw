"""Auto-nudge HTTP API — list / start / stop / update loops for chat sessions."""

import logging
from dataclasses import asdict
from pathlib import Path
from typing import Any

from aiohttp import web

from personalclaw.autonudge import get_instance as _autonudge_get
from personalclaw.config.loader import config_dir, workspace_root
from personalclaw.dashboard.state import DashboardState
from personalclaw.security import is_sensitive_path
from personalclaw.sel import sel

logger = logging.getLogger(__name__)


def resolve_stop_sentinel(session_name: str, cwd: str = "") -> str:
    """Compute the per-session stop-sentinel path.

    Placed in the session's working directory when one is set, else under the
    workspace root, so the agent can detect a stop request via the filesystem.
    """
    base = Path(cwd) if cwd else workspace_root()
    if not base.is_dir():
        base = config_dir()
    safe_key = session_name.replace("/", "_").replace(":", "_")
    return str(base / f".stop-{safe_key}")


def render_nudge_message(message: str, stop_sentinel_path: str | None) -> str:
    """Replace {{STOP_FILE}} template with the resolved sentinel path."""
    return message.replace("{{STOP_FILE}}", stop_sentinel_path or "")


def _serialize(loop: Any) -> dict:
    return asdict(loop)


async def api_autonudge_list(request: web.Request) -> web.Response:
    """GET /api/autonudge — list all active loops."""
    svc = _autonudge_get()
    if svc is None:
        return web.json_response({"enabled": False, "loops": []})
    return web.json_response({"enabled": True, "loops": [_serialize(lp) for lp in svc.list_all()]})


async def api_autonudge_get(request: web.Request) -> web.Response:
    """GET /api/autonudge/{session_name} — loop bound to this session (or null)."""
    svc = _autonudge_get()
    session_name = request.match_info["session_name"]
    if svc is None:
        return web.json_response({"enabled": False, "loop": None})
    loop = svc.get_by_session(session_name)
    return web.json_response({"enabled": True, "loop": _serialize(loop) if loop else None})


async def api_autonudge_start(request: web.Request) -> web.Response:
    """POST /api/autonudge — start or replace a loop on a session.

    Body: { session_name, message, idle_secs?, max_cycles?, stop_sentinel_path? }
    """
    svc = _autonudge_get()
    if svc is None:
        return web.json_response(
            {"error": "auto-nudge disabled (PERSONALCLAW_AUTONUDGE=0)"}, status=503
        )
    state: DashboardState = request.app["state"]
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)
    session_name = (body.get("session_name") or "").strip()
    message = (body.get("message") or "").strip()
    if not session_name or not message:
        return web.json_response({"error": "session_name and message required"}, status=400)
    if session_name not in state._sessions:
        return web.json_response({"error": f"unknown session {session_name}"}, status=404)
    if len(message) > 8000:
        return web.json_response({"error": "message too long (max 8000 chars)"}, status=400)
    stop_sentinel_path = (body.get("stop_sentinel_path") or "").strip()
    if stop_sentinel_path and is_sensitive_path(stop_sentinel_path):
        return web.json_response(
            {"error": "stop_sentinel_path points to a sensitive location"}, status=400
        )
    # Auto-default: per-session sentinel so multiple loops don't clash
    if not stop_sentinel_path:
        session = state._sessions.get(session_name)
        if session:
            stop_sentinel_path = resolve_stop_sentinel(
                session_name, getattr(session, "workspace_dir", "")
            )
            Path(stop_sentinel_path).unlink(missing_ok=True)
    loop = await svc.add(
        session_name=session_name,
        message=message,
        idle_secs=int(body.get("idle_secs", 60)),
        max_cycles=int(body.get("max_cycles", 0)),
        stop_sentinel_path=stop_sentinel_path,
    )
    sel().log_tool_invocation(
        session_key=session_name,
        source="dashboard",
        tool_name="autonudge_start",
        outcome="success",
        metadata={
            "loop_id": loop.id,
            "idle_secs": loop.idle_secs,
            "max_cycles": loop.max_cycles,
            "caller": request.remote or "",
        },
    )
    return web.json_response({"ok": True, "loop": _serialize(loop)})


async def api_autonudge_update(request: web.Request) -> web.Response:
    """PATCH /api/autonudge/{loop_id} — update message / idle_secs / active."""
    svc = _autonudge_get()
    if svc is None:
        return web.json_response({"error": "auto-nudge disabled"}, status=503)
    loop_id = request.match_info["loop_id"]
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)
    if "message" in body and len(body["message"]) > 8000:
        return web.json_response({"error": "message too long"}, status=400)
    loop = await svc.update(
        loop_id,
        message=body.get("message"),
        idle_secs=body.get("idle_secs"),
        max_cycles=body.get("max_cycles"),
        active=body.get("active"),
    )
    if loop is None:
        return web.json_response({"error": "loop not found"}, status=404)
    sel().log_tool_invocation(
        session_key=loop.session_name,
        source="dashboard",
        tool_name="autonudge_update",
        outcome="success",
        metadata={
            "loop_id": loop_id,
            "fields": [k for k in ("message", "idle_secs", "max_cycles", "active") if k in body],
            "caller": request.remote or "",
        },
    )
    return web.json_response({"ok": True, "loop": _serialize(loop)})


async def api_autonudge_delete(request: web.Request) -> web.Response:
    """DELETE /api/autonudge/{loop_id} — stop and remove a loop."""
    svc = _autonudge_get()
    if svc is None:
        return web.json_response({"error": "auto-nudge disabled"}, status=503)
    loop_id = request.match_info["loop_id"]
    # Capture session_name for audit before removal (loop is gone after remove()).
    existing = next((lp for lp in svc.list_all() if lp.id == loop_id), None)
    await svc.remove(loop_id)
    sel().log_tool_invocation(
        session_key=existing.session_name if existing else "",
        source="dashboard",
        tool_name="autonudge_delete",
        outcome="success" if existing else "noop",
        metadata={"loop_id": loop_id, "caller": request.remote or ""},
    )
    return web.json_response({"ok": True})

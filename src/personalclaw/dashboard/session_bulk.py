"""Bulk session operations + lifecycle routes (SESSION-MANAGEMENT S2, T2.2/T2.3).

One endpoint for "do this to these conversations", because doing it one request at a
time is what makes decluttering 100 sessions unappealing enough that nobody does it.

Every op is per-key best-effort with a per-key result: a bulk call over 40 sessions
must not fail wholesale because one key went away between the user's selection and
their click. The response reports what happened to each key so the UI can say
"38 archived, 2 not found" instead of a bare ok.

``delete`` is deliberately NOT part of this endpoint. Bulk-deleting conversations is
irreversible and the existing single-session DELETE already carries its own
confirmation path; folding it in beside archive — which is reversible — would put a
destructive action one mis-click from a safe one.
"""

from __future__ import annotations

import logging

from aiohttp import web

from personalclaw.config.loader import AppConfig
from personalclaw.dashboard.chat_persistence import _save_session_to_history, resolve_session
from personalclaw.dashboard.session_lifecycle import (
    LIFECYCLE_ACTIVE,
    LIFECYCLE_ARCHIVED,
    run_auto_archive,
    set_lifecycle,
    stale_session_keys,
)
from personalclaw.dashboard.state import DashboardState
from personalclaw.sel import sel

logger = logging.getLogger(__name__)

# Bulk ops the endpoint accepts. `delete` is excluded on purpose — see the module
# docstring.
_OPS = frozenset({"archive", "restore", "tag", "untag", "folder", "never_archive"})

# A selection larger than this is almost certainly a client bug rather than an intent,
# and a runaway bulk write over every session is worth refusing rather than serving.
_MAX_KEYS = 500


async def api_chat_sessions_bulk(request: web.Request) -> web.Response:
    """POST /api/chat/sessions/bulk — apply one op to many sessions.

    Body: ``{"op": "archive", "keys": ["chat-1-...", ...], ...op args}``

    Op args: ``tag``/``untag`` take ``tag_id``; ``folder`` takes ``folder_id`` (empty
    string un-files); ``never_archive`` takes ``value`` (bool).
    """
    state: DashboardState = request.app["state"]
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "body must be an object"}, status=400)

    op = str(body.get("op") or "")
    if op not in _OPS:
        return web.json_response(
            {
                "error": {
                    "code": "unknown_op",
                    "message": f"op must be one of {sorted(_OPS)}",
                    "received": op,
                }
            },
            status=400,
        )
    raw_keys = body.get("keys")
    if not isinstance(raw_keys, list) or not raw_keys:
        return web.json_response(
            {
                "error": {
                    "code": "keys_required",
                    "message": "keys must be a non-empty list of session keys",
                }
            },
            status=400,
        )
    if len(raw_keys) > _MAX_KEYS:
        return web.json_response(
            {"error": f"at most {_MAX_KEYS} sessions per bulk call"}, status=400
        )
    keys = [str(k) for k in raw_keys]

    tag_id = str(body.get("tag_id") or "")
    if op in ("tag", "untag") and not tag_id:
        return web.json_response({"error": f"{op} requires tag_id"}, status=400)
    folder_id = str(body.get("folder_id") or "")
    never_value = bool(body.get("value", True))

    request_app = request.get("app", "")
    changed: list[str] = []
    unchanged: list[str] = []
    missing: list[str] = []

    for key in keys:
        session = resolve_session(state, key)
        if session is None:
            missing.append(key)
            continue
        # App Kit ownership isolation, mirroring the cleanup endpoint: an app caller
        # may only touch its own sessions; a dashboard user (no request app) may touch
        # any. Without this an app could archive the user's conversations.
        if request_app and getattr(session, "_app", "") != request_app:
            missing.append(key)
            continue

        did = False
        if op == "archive":
            did = set_lifecycle(session, LIFECYCLE_ARCHIVED)
        elif op == "restore":
            did = set_lifecycle(session, LIFECYCLE_ACTIVE)
        elif op == "tag":
            if tag_id not in session.tags:
                session.tags.append(tag_id)
                did = True
        elif op == "untag":
            if tag_id in session.tags:
                session.tags.remove(tag_id)
                did = True
        elif op == "folder":
            if session.folder_id != folder_id:
                session.folder_id = folder_id
                did = True
        elif op == "never_archive":
            if bool(getattr(session, "never_archive", False)) != never_value:
                session.never_archive = never_value
                did = True

        if did:
            _save_session_to_history(state, session, force=True)
            changed.append(key)
        else:
            unchanged.append(key)

    if changed:
        state.push_sessions_update()
    sel().log_api_access(
        caller="dashboard",
        operation=f"chat.sessions_bulk.{op}",
        outcome="allowed",
        source="dashboard",
        resources=f"changed={len(changed)} unchanged={len(unchanged)} missing={len(missing)}",
    )
    return web.json_response(
        {
            "ok": True,
            "op": op,
            "changed": changed,
            "unchanged": unchanged,
            "missing": missing,
        }
    )


async def api_chat_sessions_auto_archive(request: web.Request) -> web.Response:
    """POST /api/chat/sessions/auto-archive — run (or preview) the auto-archive rule.

    Body: ``{"dry_run": true, "active_session": "chat-1-..."}``. ``dry_run`` returns
    the exact keys a real run would archive and changes nothing, so the UI can say
    what it is about to do before doing it.
    """
    state: DashboardState = request.app["state"]
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    dry_run = bool(body.get("dry_run", False))
    active_session = str(body.get("active_session") or "")

    days = int(AppConfig.load().session.auto_archive_days)
    if days <= 0:
        return web.json_response(
            {"ok": True, "enabled": False, "days": days, "keys": [], "count": 0}
        )

    if dry_run:
        keys = stale_session_keys(state, days=days, active_session=active_session)
        return web.json_response(
            {
                "ok": True,
                "enabled": True,
                "dry_run": True,
                "days": days,
                "keys": keys,
                "count": len(keys),
            }
        )

    keys = run_auto_archive(state, days=days, active_session=active_session)
    for key in keys:
        session = state._sessions.get(key)
        if session is not None:
            _save_session_to_history(state, session, force=True)
    if keys:
        state.push_sessions_update()
    sel().log_api_access(
        caller="dashboard",
        operation="chat.sessions_auto_archive",
        outcome="allowed",
        source="dashboard",
        resources=f"count={len(keys)} threshold={days}d",
    )
    return web.json_response(
        {"ok": True, "enabled": True, "days": days, "keys": keys, "count": len(keys)}
    )


async def api_chat_session_lifecycle(request: web.Request) -> web.Response:
    """PATCH /api/chat/sessions/{session}/lifecycle — archive/restore one session.

    Body: ``{"lifecycle": "archived"}`` or ``{"never_archive": true}`` (either or both).
    """
    state: DashboardState = request.app["state"]
    name = request.match_info["session"]
    session = resolve_session(state, name)
    if session is None:
        return web.json_response({"error": "not found"}, status=404)
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "body must be an object"}, status=400)
    if "lifecycle" not in body and "never_archive" not in body:
        return web.json_response(
            {
                "error": {
                    "code": "nothing_to_set",
                    "message": "body must include 'lifecycle' and/or 'never_archive'",
                }
            },
            status=400,
        )

    if "lifecycle" in body:
        try:
            set_lifecycle(session, str(body["lifecycle"]))
        except ValueError as exc:
            return web.json_response({"error": str(exc)}, status=400)
    if "never_archive" in body:
        session.never_archive = bool(body["never_archive"])
        session._dirty = True

    _save_session_to_history(state, session, force=True)
    state.push_sessions_update()
    sel().log_api_access(
        caller="dashboard",
        operation="chat.session_lifecycle",
        outcome="allowed",
        source="dashboard",
        resources=f"{name}:{session.lifecycle}:never_archive={session.never_archive}",
    )
    return web.json_response(
        {
            "ok": True,
            "lifecycle": session.lifecycle,
            "never_archive": session.never_archive,
        }
    )


def register_routes(app: web.Application) -> None:
    """Wire the S2 bulk + lifecycle routes."""
    app.router.add_post("/api/chat/sessions/bulk", api_chat_sessions_bulk)
    app.router.add_post("/api/chat/sessions/auto-archive", api_chat_sessions_auto_archive)
    app.router.add_patch("/api/chat/sessions/{session}/lifecycle", api_chat_session_lifecycle)

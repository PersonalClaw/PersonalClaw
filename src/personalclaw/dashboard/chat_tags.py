"""Session tags — dynamic vocabulary CRUD, session assignment, and sidebar columns.

Tags are user-defined labels (id/name/color/status) attached to dashboard chat
sessions. Sessions can carry multiple tags. The sidebar renders as a horizontal
strip of user-configurable columns; each column filters the session list by a
set of tags (any/all/none mode). Dragging a session card between columns that
carry a single status tag as filter reassigns the card's status tag.
"""

import logging
import re
import uuid
from typing import Any

from aiohttp import web

from personalclaw.dashboard.chat_persistence import _save_session_to_history, resolve_session
from personalclaw.dashboard.state import DashboardState
from personalclaw.sel import sel

logger = logging.getLogger(__name__)

_NAME_MAX = 60
_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
_DEFAULT_COLOR = "#6b7280"
_VALID_MODES = {"any", "all", "none"}

# Palette for AI-created tags — deterministic pick by name hash so repeated
# runs color the same tag identically. Hues match the default status tags.
_AUTO_TAG_COLORS = (
    "#3b82f6", "#8b5cf6", "#f59e0b", "#10b981", "#ef4444",
    "#06b6d4", "#ec4899", "#84cc16",
)


def _auto_color(name: str) -> str:
    """Deterministic palette color for an AI-created tag name."""
    return _AUTO_TAG_COLORS[sum(ord(c) for c in name.lower()) % len(_AUTO_TAG_COLORS)]


def _valid_color(value: str) -> str:
    return value if _COLOR_RE.match(value) else _DEFAULT_COLOR


def _tag_by_id(state: DashboardState, tag_id: str) -> dict | None:
    return next((t for t in state._tags if t.get("id") == tag_id), None)


# ── Tag vocabulary ─────────────────────────────────────────────────────────

def find_tag_by_name(state: DashboardState, name: str) -> dict | None:
    """Case-insensitive lookup of a tag definition by display name."""
    needle = (name or "").strip().lower()
    if not needle:
        return None
    return next(
        (t for t in state._tags if str(t.get("name", "")).strip().lower() == needle), None
    )


def create_tag(
    state: DashboardState, name: str, *, color: str | None = None, status: bool = False
) -> dict | None:
    """Create a tag definition — the SAME path the UI's POST /api/chat/tags uses.

    Shared by the HTTP handler and the AI auto-tagger so programmatically
    created tags get identical ids/colors/order semantics. Returns the tag
    dict, or None for an empty name. Persists the vocabulary; callers push
    the sessions update / SEL entry themselves.
    """
    cleaned = (name or "").strip()[:_NAME_MAX]
    if not cleaned:
        return None
    tag = {
        "id": uuid.uuid4().hex[:12],
        "name": cleaned,
        "color": _valid_color(str(color or _DEFAULT_COLOR)),
        "order": len(state._tags),
        "status": bool(status),
    }
    state._tags.append(tag)
    state.save_tags()
    return tag


async def api_chat_tags(request: web.Request) -> web.Response:
    """GET /api/chat/tags — list all tag definitions."""
    state: DashboardState = request.app["state"]
    return web.json_response(sorted(state._tags, key=lambda t: t.get("order", 0)))


async def api_chat_tag_create(request: web.Request) -> web.Response:
    """POST /api/chat/tags — create a new tag."""
    state: DashboardState = request.app["state"]
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    tag = create_tag(
        state,
        str(body.get("name") or ""),
        color=str(body.get("color") or _DEFAULT_COLOR),
        status=bool(body.get("status", False)),
    )
    if tag is None:
        return web.json_response({"error": "name required"}, status=400)
    state.push_sessions_update()
    sel().log_api_access(
        caller="dashboard", operation="chat.tag_create",
        outcome="allowed", source="dashboard", resources=str(tag["id"]),
    )
    return web.json_response(tag, status=201)


async def api_chat_tag_update(request: web.Request) -> web.Response:
    """PATCH /api/chat/tags/{id} — rename / recolor / reorder."""
    state: DashboardState = request.app["state"]
    tid = request.match_info["id"]
    tag = _tag_by_id(state, tid)
    if not tag:
        return web.json_response({"error": "not found"}, status=404)
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if "name" in body:
        new_name = str(body["name"]).strip()[:_NAME_MAX]
        if not new_name:
            return web.json_response({"error": "name required"}, status=400)
        tag["name"] = new_name
    if "color" in body:
        tag["color"] = _valid_color(str(body["color"]))
    if "order" in body:
        try:
            tag["order"] = int(body["order"])
        except (TypeError, ValueError):
            pass
    if "status" in body:
        tag["status"] = bool(body["status"])
    state.save_tags()
    state.push_sessions_update()
    sel().log_api_access(
        caller="dashboard", operation="chat.tag_update",
        outcome="allowed", source="dashboard", resources=tid,
    )
    return web.json_response(tag)


async def api_chat_tag_delete(request: web.Request) -> web.Response:
    """DELETE /api/chat/tags/{id} — delete a tag; strip it from all sessions."""
    state: DashboardState = request.app["state"]
    tid = request.match_info["id"]
    if not _tag_by_id(state, tid):
        return web.json_response({"error": "not found"}, status=404)
    state._tags = [t for t in state._tags if t.get("id") != tid]
    # Strip from sessions
    for session in state._sessions.values():
        if tid in session.tags:
            session.tags = [t for t in session.tags if t != tid]
            _save_session_to_history(state, session, force=True)
    # Strip from sidebar columns (flat list of column dicts).
    changed_boards = False
    for col in state._tag_boards:
        tag_ids = col.get("tag_ids") or []
        filtered = [t for t in tag_ids if t != tid]
        if len(filtered) != len(tag_ids):
            col["tag_ids"] = filtered
            changed_boards = True
    if changed_boards:
        state.save_tag_boards()
    state.save_tags()
    state.push_sessions_update()
    sel().log_api_access(
        caller="dashboard", operation="chat.tag_delete",
        outcome="allowed", source="dashboard", resources=tid,
    )
    return web.json_response({"ok": True})


# ── Session tag assignment ────────────────────────────────────────────────────

async def api_chat_session_tags(request: web.Request) -> web.Response:
    """PUT /api/chat/sessions/{session}/tags — replace the session's tag list."""
    state: DashboardState = request.app["state"]
    name = request.match_info["session"]
    session = resolve_session(state, name)
    if not session:
        return web.json_response({"error": "not found"}, status=404)
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    raw_ids = body.get("tags")
    if not isinstance(raw_ids, list):
        return web.json_response({"error": "tags must be an array"}, status=400)
    valid_ids = {t.get("id") for t in state._tags}
    new_tags: list[str] = []
    for tid in raw_ids:
        if isinstance(tid, str) and tid in valid_ids and tid not in new_tags:
            new_tags.append(tid)
    session.tags = new_tags
    _save_session_to_history(state, session, force=True)
    state.push_sessions_update()
    sel().log_api_access(
        caller="dashboard", operation="chat.session_tags",
        outcome="allowed", source="dashboard", resources=name,
    )
    return web.json_response({"ok": True, "tags": session.tags})


# ── Sidebar columns (Trello-style filtered lanes) ──────────────────────────

def _normalize_column(state: DashboardState, raw: Any, *, existing: dict | None = None) -> dict | None:
    """Validate + coerce a column payload. Returns None if invalid."""
    if not isinstance(raw, dict):
        return None
    valid_ids = {t.get("id") for t in state._tags}
    cleaned: dict[str, Any] = dict(existing or {})
    if "tag_ids" in raw:
        tag_ids = raw.get("tag_ids") or []
        if not isinstance(tag_ids, list):
            return None
        cleaned["tag_ids"] = [str(t) for t in tag_ids if isinstance(t, str) and t in valid_ids]
    if "mode" in raw:
        mode = str(raw.get("mode") or "any")
        if mode not in _VALID_MODES:
            return None
        cleaned["mode"] = mode
    if "name" in raw:
        cleaned["name"] = str(raw.get("name") or "").strip()[:_NAME_MAX]
    if "order" in raw:
        order_val = raw.get("order")
        if order_val is not None:
            try:
                cleaned["order"] = int(order_val)
            except (TypeError, ValueError):
                pass
    if "include_untagged" in raw:
        cleaned["include_untagged"] = bool(raw.get("include_untagged"))
    cleaned.setdefault("mode", "any")
    cleaned.setdefault("tag_ids", [])
    cleaned.setdefault("name", "")
    cleaned.setdefault("order", 0)
    cleaned.setdefault("include_untagged", False)
    return cleaned


async def api_chat_tag_columns(request: web.Request) -> web.Response:
    """GET /api/chat/tag-columns — list sidebar column layout."""
    state: DashboardState = request.app["state"]
    return web.json_response(sorted(state._tag_boards, key=lambda c: c.get("order", 0)))


async def api_chat_tag_column_create(request: web.Request) -> web.Response:
    """POST /api/chat/tag-columns — append a new sidebar column.

    Requires a non-empty ``name``. A nameless column renders indistinguishably
    as "ALL SESSIONS", so an empty or whitespace-only name is rejected here
    rather than persisted.
    """
    state: DashboardState = request.app["state"]
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)
    raw_name = body.get("name", "")
    if not isinstance(raw_name, str) or not raw_name.strip():
        return web.json_response(
            {"error": "tag column name is required and must be a non-empty string"},
            status=400,
        )
    column = _normalize_column(state, {**body, "order": len(state._tag_boards)})
    if column is None:
        return web.json_response({"error": "invalid column payload"}, status=400)
    column["id"] = uuid.uuid4().hex[:12]
    state._tag_boards.append(column)
    state.save_tag_boards()
    sel().log_api_access(
        caller="dashboard", operation="chat.tag_column_create",
        outcome="allowed", source="dashboard", resources=str(column["id"]),
    )
    return web.json_response(column, status=201)


async def api_chat_tag_column_update(request: web.Request) -> web.Response:
    """PATCH /api/chat/tag-columns/{id} — rename / retag / reorder."""
    state: DashboardState = request.app["state"]
    cid = request.match_info["id"]
    column = next((c for c in state._tag_boards if c.get("id") == cid), None)
    if not column:
        return web.json_response({"error": "not found"}, status=404)
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    merged = _normalize_column(state, body, existing=column)
    if merged is None:
        return web.json_response({"error": "invalid column payload"}, status=400)
    column.update(merged)
    state.save_tag_boards()
    sel().log_api_access(
        caller="dashboard", operation="chat.tag_column_update",
        outcome="allowed", source="dashboard", resources=cid,
    )
    return web.json_response(column)


async def api_chat_tag_column_delete(request: web.Request) -> web.Response:
    """DELETE /api/chat/tag-columns/{id} — remove a column."""
    state: DashboardState = request.app["state"]
    cid = request.match_info["id"]
    if not any(c.get("id") == cid for c in state._tag_boards):
        return web.json_response({"error": "not found"}, status=404)
    state._tag_boards = [c for c in state._tag_boards if c.get("id") != cid]
    state.save_tag_boards()
    sel().log_api_access(
        caller="dashboard", operation="chat.tag_column_delete",
        outcome="allowed", source="dashboard", resources=cid,
    )
    return web.json_response({"ok": True})


async def api_chat_tag_columns_reorder(request: web.Request) -> web.Response:
    """PUT /api/chat/tag-columns/order — reorder columns by id list."""
    state: DashboardState = request.app["state"]
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    ids = body.get("ids")
    if not isinstance(ids, list):
        return web.json_response({"error": "ids must be an array"}, status=400)
    order_map = {str(cid): i for i, cid in enumerate(ids)}
    # Push columns not present in the reorder payload past the explicit
    # ordering so they don't collide with the new sequential indices.
    next_order = len(order_map)
    for col in state._tag_boards:
        cid = col.get("id")
        if cid in order_map:
            col["order"] = order_map[cid]
        else:
            col["order"] = next_order
            next_order += 1
    state._tag_boards.sort(key=lambda c: c.get("order", 0))
    state.save_tag_boards()
    sel().log_api_access(
        caller="dashboard", operation="chat.tag_columns_reorder",
        outcome="allowed", source="dashboard", resources=",".join(str(x) for x in ids[:10]),
    )
    return web.json_response({"ok": True})


# ── Drag-drop: move a session between columns (reassigns status tags) ──────

async def api_chat_session_drop(request: web.Request) -> web.Response:
    """POST /api/chat/sessions/{session}/drop — move a session into a column.

    Destination rule: if the target column's tag_ids contains exactly one
    *status* tag, strip every status tag from the session and add that one.
    Non-status tags are preserved. Any other configuration is a no-op so users
    can have filter-only columns without accidental data loss.
    """
    state: DashboardState = request.app["state"]
    name = request.match_info["session"]
    session = state._sessions.get(name)
    if not session:
        return web.json_response({"error": "not found"}, status=404)
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    column_id = str(body.get("column_id") or "")
    column = next((c for c in state._tag_boards if c.get("id") == column_id), None)
    if not column:
        return web.json_response({"error": "column not found"}, status=404)
    tag_index = {t["id"]: t for t in state._tags}
    col_tags = [tag_index[t] for t in column.get("tag_ids") or [] if t in tag_index]
    status_tags = [t for t in col_tags if t.get("status")]
    if len(status_tags) != 1:
        # Column doesn't carry exactly one status tag — there's no
        # unambiguous status to assign on drop, so this is a visual no-op
        # (covers both the unfiltered and the multi-status filter cases).
        # Audit the rejected drop so the SEL trail captures every attempt.
        sel().log_api_access(
            caller="dashboard", operation="chat.session_drop",
            outcome="rejected", source="dashboard",
            resources=f"{name}->{column_id}", error="column is not a status lane",
        )
        return web.json_response({"ok": False, "reason": "column is not a status lane", "tags": session.tags})
    target_id = status_tags[0]["id"]
    kept = [t for t in session.tags if t in tag_index and not tag_index[t].get("status")]
    session.tags = kept + [target_id]
    _save_session_to_history(state, session, force=True)
    state.push_sessions_update()
    sel().log_api_access(
        caller="dashboard", operation="chat.session_drop",
        outcome="allowed", source="dashboard", resources=f"{name}->{column_id}",
    )
    return web.json_response({"ok": True, "tags": session.tags})

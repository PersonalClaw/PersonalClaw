"""Folder management — CRUD, pin, assignment, icon generation."""

import asyncio
import logging
import unicodedata
import uuid

from aiohttp import web

from personalclaw.dashboard.chat_persistence import resolve_session, save_session_to_history
from personalclaw.dashboard.state import DashboardState
from personalclaw.llm.base import EVENT_COMPLETE, EVENT_PERMISSION_REQUEST, EVENT_TEXT_CHUNK
from personalclaw.request_validation import (
    RequestValidationError,
    json_object_body,
    require_string,
)
from personalclaw.security import redact_credentials, redact_exfiltration_urls
from personalclaw.sel import sel
from personalclaw.session import BACKGROUND_KEY

logger = logging.getLogger(__name__)

_folder_icon_lock = asyncio.Lock()


def folder_exists(state, folder_id: str) -> bool:
    """Whether ``folder_id`` names a real folder. Empty means "ungrouped", which is valid.

    🔴 The single-session path (`PATCH /sessions/{s}/folder`) rejected an unknown id with 400
    while `POST /sessions/bulk` set it unconditionally, so bulk persisted a dangling folder_id
    (#771). The list treats an unknown folder as ungrouped, so it looked fine and the stored
    state was wrong.
    """
    if not folder_id:
        return True
    return any(f["id"] == folder_id for f in state._folders)


async def _generate_folder_icon(state: DashboardState, folder: dict) -> None:
    """Background task: ask LLM for a single emoji for the folder name.

    Serialized via a module-level lock so concurrent folder creations don't
    interleave streams on the shared BACKGROUND_KEY session.
    """

    # The instruction lives in the prompt system (bundled ``task-folder-icon``).
    from personalclaw.prompt_providers.runtime import render_use_case_prompt

    prompt = render_use_case_prompt("folder_icon", {"folder_name": folder["name"]}) or ""

    async def _stream(client) -> str:  # type: ignore[no-untyped-def]
        t = ""
        async for event in client.stream(prompt):
            if event.kind == EVENT_TEXT_CHUNK:
                t += event.text
            elif event.kind == EVENT_PERMISSION_REQUEST:
                await client.reject_tool(event.request_id)
            elif event.kind == EVENT_COMPLETE:
                break
        return t

    text = ""
    async with _folder_icon_lock:
        # 🔴 `get_or_create` is what resolves the `background` use case, so on a provider-less
        # install — the state every new install starts in — it raises. It used to sit OUTSIDE
        # this try, so the one failure that is GUARANTEED on a fresh install was the one the
        # "best-effort" guard did not cover: the error escaped the fire-and-forget task and
        # asyncio logged 37 unretrieved lines per folder created (#2978). Acquire inside the
        # guard, and release only a client we actually got.
        acquired = False
        try:
            client, _is_new, _resumed = await state.sessions.get_or_create(BACKGROUND_KEY)
            acquired = True
            text = await asyncio.wait_for(_stream(client), timeout=30)
        except Exception:  # noqa: BLE001 — best-effort background task
            text = ""
        finally:
            if acquired:
                state.sessions.release(BACKGROUND_KEY)
    icon = text.strip()
    icon, _ = redact_exfiltration_urls(icon)
    icon, _ = redact_credentials(icon)
    # Validate: must be a single emoji (1-2 code points, symbol category or high-plane emoji)
    if (
        icon
        and len(icon) <= 3
        and all(
            unicodedata.category(c).startswith("So") or ord(c) > 0x1F000 or c in "\ufe0f\u200d"
            for c in icon
        )
    ):
        if any(f["id"] == folder["id"] for f in state._folders):
            folder["icon"] = icon
            state.save_folders()
            state.push_sessions_update()


async def api_chat_folders(request: web.Request) -> web.Response:
    """GET /api/chat/folders — list all project folders."""
    state: DashboardState = request.app["state"]
    return web.json_response(state._folders)


async def api_chat_folder_create(request: web.Request) -> web.Response:
    """POST /api/chat/folders — create a project folder."""
    state: DashboardState = request.app["state"]
    # Shared validator, this module's envelope — see `lexicon/handlers.py` for the reasoning.
    # This module answers flat in fourteen places and uses `json_error` in none.
    try:
        body = await json_object_body(request)
        name = require_string(body, "name")[:100]
    except RequestValidationError as exc:
        return web.json_response({"error": exc.message}, status=exc.status)
    parent_id = str(body.get("parent_id") or "")
    if not folder_exists(state, parent_id):
        return web.json_response({"error": "parent folder not found"}, status=400)
    folder = {
        "id": uuid.uuid4().hex[:12],
        "name": name,
        "order": len(state._folders),
        "collapsed": False,
        "parent_id": parent_id,
    }
    state._folders.append(folder)
    state.save_folders()
    state.push_sessions_update()
    # Generate icon in background — don't block the response
    task = asyncio.ensure_future(_generate_folder_icon(state, folder))
    state._background_tasks.add(task)
    task.add_done_callback(state._background_tasks.discard)
    sel().log_api_access(
        caller="dashboard",
        operation="chat.folder_create",
        outcome="allowed",
        source="dashboard",
        resources=str(folder["id"]),
    )
    return web.json_response(folder, status=201)


async def api_chat_folder_update(request: web.Request) -> web.Response:
    """PATCH /api/chat/folders/{id} — rename or reorder a folder."""
    state: DashboardState = request.app["state"]
    fid = request.match_info["id"]
    folder = next((f for f in state._folders if f["id"] == fid), None)
    if not folder:
        return web.json_response({"error": "not found"}, status=404)
    try:
        body = await json_object_body(request)
        new_name = require_string(body, "name")[:100] if "name" in body else None
    except RequestValidationError as exc:
        return web.json_response({"error": exc.message}, status=exc.status)
    if new_name is not None:
        folder["name"] = new_name
    if "collapsed" in body:
        folder["collapsed"] = bool(body["collapsed"])
    if "order" in body:
        # Coerce-or-ignore, matching `chat_tags.py`'s tag and column updates verbatim. A bare
        # `int()` here raised `ValueError` on an unparseable order and answered a 500, while the
        # identical payload against the sibling tag route answered 200 and ignored it (#770). One
        # of the two was wrong about the same field, and the sibling is the one with the precedent.
        try:
            folder["order"] = int(body["order"])
        except (TypeError, ValueError):
            pass
    state.save_folders()
    state.push_sessions_update()
    sel().log_api_access(
        caller="dashboard",
        operation="chat.folder_update",
        outcome="allowed",
        source="dashboard",
        resources=fid,
    )
    return web.json_response(folder)


async def api_chat_folder_delete(request: web.Request) -> web.Response:
    """DELETE /api/chat/folders/{id} — delete a folder, ungroup its sessions."""

    state: DashboardState = request.app["state"]
    fid = request.match_info["id"]
    if not any(f["id"] == fid for f in state._folders):
        return web.json_response({"error": "not found"}, status=404)
    for f in state._folders:
        if f.get("parent_id") == fid:
            f["parent_id"] = ""
    state._folders = [f for f in state._folders if f["id"] != fid]
    for session in state._sessions.values():
        if session.folder_id == fid:
            session.folder_id = ""
            save_session_to_history(state, session, force=True)
    state.save_folders()
    state.push_sessions_update()
    sel().log_api_access(
        caller="dashboard",
        operation="chat.folder_delete",
        outcome="allowed",
        source="dashboard",
        resources=fid,
    )
    return web.json_response({"ok": True})


async def api_chat_session_folder(request: web.Request) -> web.Response:
    """PATCH /api/chat/sessions/{session}/folder — assign session to a folder."""

    state: DashboardState = request.app["state"]
    name = request.match_info["session"]
    session = resolve_session(state, name)
    if not session:
        return web.json_response({"error": "not found"}, status=404)
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "body must be an object"}, status=400)
    # ABSENT IS NOT "CLEAR" (#2970). `""` is a legitimate explicit un-folder, and
    # `folder_exists(state, "")` passes, so an EMPTY body used to walk straight through to
    # the write and remove the session from its folder at `200 {"ok": true}`. Requiring the
    # key keeps the explicit clear and refuses the accidental one — the same line the
    # /lifecycle sibling draws with its `nothing_to_set` 400.
    if "folder_id" not in body:
        return web.json_response(
            {"error": "body must include 'folder_id' (use \"\" to remove from its folder)"},
            status=400,
        )
    folder_id = str(body.get("folder_id") or "")
    if not folder_exists(state, folder_id):
        return web.json_response({"error": "folder not found"}, status=400)
    session.folder_id = folder_id
    save_session_to_history(state, session, force=True)
    state.push_sessions_update()
    sel().log_api_access(
        caller="dashboard",
        operation="chat.session_folder",
        outcome="allowed",
        source="dashboard",
        resources=name,
    )
    return web.json_response({"ok": True, "folder_id": session.folder_id})


async def api_chat_session_pin(request: web.Request) -> web.Response:
    """PATCH /api/chat/sessions/{session}/pin — toggle pinned state."""

    state: DashboardState = request.app["state"]
    name = request.match_info["session"]
    session = resolve_session(state, name)
    if not session:
        return web.json_response({"error": "not found"}, status=404)
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "body must be an object"}, status=400)
    # ABSENT IS NOT "UNPIN" (#2970). `bool(body.get("pinned", False))` made an empty body
    # indistinguishable from `{"pinned": false}`, so `PATCH .../pin {}` silently dropped the
    # pin at `200 {"ok": true}` — and a pin is the user saying *keep this*.
    if "pinned" not in body:
        return web.json_response({"error": "body must include 'pinned'"}, status=400)
    session.pinned = bool(body.get("pinned", False))
    save_session_to_history(state, session, force=True)
    state.push_sessions_update()
    sel().log_api_access(
        caller="dashboard",
        operation="chat.session_pin",
        outcome="allowed",
        source="dashboard",
        resources=name,
    )
    return web.json_response({"ok": True, "pinned": session.pinned})

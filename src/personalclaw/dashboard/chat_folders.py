"""Folder management — CRUD, pin, assignment, icon generation."""

import asyncio
import logging
import unicodedata
import uuid

from aiohttp import web

from personalclaw.dashboard.chat_persistence import _save_session_to_history, resolve_session
from personalclaw.dashboard.state import DashboardState
from personalclaw.llm.base import EVENT_COMPLETE, EVENT_PERMISSION_REQUEST, EVENT_TEXT_CHUNK
from personalclaw.security import redact_credentials, redact_exfiltration_urls
from personalclaw.sel import sel
from personalclaw.session import BACKGROUND_KEY

logger = logging.getLogger(__name__)

_folder_icon_lock = asyncio.Lock()


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
        client, _is_new, _resumed = await state.sessions.get_or_create(BACKGROUND_KEY)
        try:
            text = await asyncio.wait_for(_stream(client), timeout=30)
        except Exception:  # noqa: BLE001 — best-effort background task
            text = ""
        finally:
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
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    name = (body.get("name") or "").strip()[:100]
    if not name:
        return web.json_response({"error": "name required"}, status=400)
    parent_id = str(body.get("parent_id") or "")
    if parent_id and not any(f["id"] == parent_id for f in state._folders):
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
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if "name" in body:
        new_name = str(body["name"]).strip()[:100]
        if not new_name:
            return web.json_response({"error": "name required"}, status=400)
        folder["name"] = new_name
    if "collapsed" in body:
        folder["collapsed"] = bool(body["collapsed"])
    if "order" in body:
        folder["order"] = int(body["order"])
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
            _save_session_to_history(state, session, force=True)
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
    folder_id = str(body.get("folder_id") or "")
    if folder_id and not any(f["id"] == folder_id for f in state._folders):
        return web.json_response({"error": "folder not found"}, status=400)
    session.folder_id = folder_id
    _save_session_to_history(state, session, force=True)
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
    session.pinned = bool(body.get("pinned", False))
    _save_session_to_history(state, session, force=True)
    state.push_sessions_update()
    sel().log_api_access(
        caller="dashboard",
        operation="chat.session_pin",
        outcome="allowed",
        source="dashboard",
        resources=name,
    )
    return web.json_response({"ok": True, "pinned": session.pinned})

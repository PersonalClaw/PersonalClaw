"""Routes for session templates + conversation export/share (SESSION-MANAGEMENT S3).

Three S3 capabilities that share nothing but their place in the session lifecycle, kept in
one route module because each is thin HTTP over a pure module beside them
(``session_templates``, ``session_export``, ``session_share``).

Route placement note: the template routes use the literal path segment ``templates``
under ``/api/chat/sessions/``, so they MUST register before ``server.py``'s
``/api/chat/sessions/{session}`` patterns or aiohttp captures "templates" as a session
name. ``register_routes`` is therefore called from the same pre-``{session}`` block that
already wires ``session_bulk`` for exactly this reason.
"""

from __future__ import annotations

import logging

from aiohttp import web

from personalclaw import session_search
from personalclaw.artifacts import registry
from personalclaw.dashboard import session_export, session_share, session_templates
from personalclaw.dashboard.chat_utils import _history_key_for, resolve_history_key
from personalclaw.dashboard.state import DashboardState
from personalclaw.sel import sel

logger = logging.getLogger(__name__)


async def api_session_templates_list(request: web.Request) -> web.Response:
    """GET /api/chat/sessions/templates — every saved session starter."""
    return web.json_response({"templates": session_templates.list_templates()})


async def api_session_templates_create(request: web.Request) -> web.Response:
    """POST /api/chat/sessions/templates — save a chat setup as a reusable starter.

    Body: ``{"name","agent","model","reasoning_effort","first_prompt"}``. Only ``name``
    is required; an empty agent/model means "whatever the default is at use time", which
    is what makes a template survive the user changing their default model.
    """
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        return web.json_response({"error": "expected a JSON object"}, status=400)

    tid, err = session_templates.save_template(body)
    if err:
        return web.json_response({"error": err}, status=400)
    template = session_templates.get_template(tid)
    sel().log_api_access(
        caller="dashboard",
        operation="chat.template_create",
        outcome="allowed",
        source="dashboard",
        resources=f"id={tid}",
    )
    return web.json_response({"ok": True, "template": template}, status=201)


async def api_session_template_update(request: web.Request) -> web.Response:
    """PUT /api/chat/sessions/templates/{template} — replace a starter's fields."""
    tid = request.match_info["template"]
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        return web.json_response({"error": "expected a JSON object"}, status=400)

    err = session_templates.update_template(tid, body)
    if err == "not found":
        return web.json_response({"error": "template not found"}, status=404)
    if err:
        return web.json_response({"error": err}, status=400)
    return web.json_response({"ok": True, "template": session_templates.get_template(tid)})


async def api_session_template_delete(request: web.Request) -> web.Response:
    """DELETE /api/chat/sessions/templates/{template} — remove a starter.

    Deleting a template removes a shortcut, never a conversation — the sessions created
    from it are untouched and keep working.
    """
    tid = request.match_info["template"]
    if not session_templates.delete_template(tid):
        return web.json_response({"error": "template not found"}, status=404)
    sel().log_api_access(
        caller="dashboard",
        operation="chat.template_delete",
        outcome="allowed",
        source="dashboard",
        resources=f"id={tid}",
    )
    return web.json_response({"ok": True})


def _read_transcript(
    state: DashboardState, name: str, *, what: str
) -> tuple[str, str, dict, list[dict], web.Response | None]:
    """``(key, title, meta, messages, error)`` for one session, or an error response.

    Shared by export and share (SM-9) so the two can never disagree about which history
    key a session name resolves to, or about what "conversation not found" means.
    """
    log = state.conversation_log
    if log is None:
        return "", "", {}, [], web.json_response({"error": "history is unavailable"}, status=503)

    # Resolve provider-agnostically: a channel thread is canonical under its own bare
    # key, a dashboard chat under the `dashboard:` namespace.
    key = resolve_history_key(log, name) or _history_key_for(name)
    try:
        messages = log.read_messages_chained(key)
        meta = log.get_metadata(key)
    except Exception:
        logger.exception("%s: reading transcript failed for %r", what, key)
        return (
            key,
            "",
            {},
            [],
            web.json_response({"error": "could not read this conversation"}, status=500),
        )

    if not messages and not meta:
        return (
            key,
            "",
            {},
            [],
            web.json_response({"error": "conversation not found"}, status=404),
        )

    session = state._sessions.get(name)
    title = str(meta.get("title") or getattr(session, "title", "") or name)
    return key, title, meta, messages, None


async def api_session_export(request: web.Request) -> web.Response:
    """GET /api/chat/sessions/{session}/export?format=md|json — download a transcript.

    Always redacted, including the user's own messages: the write path exempts
    ``user``/``system`` roles from redaction, so this is the only pass those ever get
    before the text leaves the machine. See ``session_export``'s module docstring.
    """
    state: DashboardState = request.app["state"]
    name = request.match_info["session"]
    fmt = (request.query.get("format") or "md").lower()
    if fmt not in session_export.VALID_FORMATS:
        return web.json_response(
            {"error": f"format must be one of {sorted(session_export.VALID_FORMATS)}"},
            status=400,
        )

    key, title, meta, messages, err = _read_transcript(state, name, what="export")
    if err is not None:
        return err

    text, content_type = session_export.render(
        fmt, title=title, key=key, meta=meta, messages=messages
    )
    filename = session_export.export_filename(title, name, fmt)

    sel().log_api_access(
        caller="dashboard",
        operation="chat.session_export",
        outcome="allowed",
        source="dashboard",
        resources=f"format={fmt} messages={len(messages)}",
    )
    body = text.encode("utf-8")
    return web.Response(
        body=body,
        content_type=content_type,
        charset="utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Length": str(len(body)),
            # A transcript is user content being served back; keep sniffing off.
            "X-Content-Type-Options": "nosniff",
        },
    )


async def api_session_share(request: web.Request) -> web.Response:
    """POST /api/chat/sessions/{session}/share — a redacted, read-only artifact of a chat.

    POST, not GET: this creates durable state, and a GET that writes is a side effect on a
    method browsers, prefetchers and link previews treat as safe — a hovered link would
    publish a conversation. It carries the same session auth as every other route and is
    NOT on any auth-bypass allowlist: "share" here means *into the owner's own artifact
    library*, never onto the internet (see ``session_share``'s module docstring).

    Refuses an incognito/temporary chat (403). Export doesn't — a download is the user
    holding their own text for a moment — but an artifact is durable library state, which
    is exactly what a restricted session promises not to leave behind.
    """
    state: DashboardState = request.app["state"]
    name = request.match_info["session"]

    key, title, meta, messages, err = _read_transcript(state, name, what="share")
    if err is not None:
        return err

    session = state._sessions.get(name)
    memory_mode = str(meta.get("memory_mode") or getattr(session, "memory_mode", "") or "")
    if session_search.is_restricted(key, memory_mode=memory_mode) or getattr(
        session, "is_restricted", False
    ):
        sel().log_api_access(
            caller="dashboard",
            operation="chat.session_share",
            outcome="denied",
            source="dashboard",
            resources="restricted_session",
        )
        return web.json_response(
            {"error": "this chat is restricted — it cannot be shared as an artifact"},
            status=403,
        )

    provider = registry.get_provider("native")
    if provider is None:
        return web.json_response({"error": "artifacts are unavailable"}, status=503)

    try:
        art = session_share.share_session(
            provider,
            key=key,
            title=title,
            meta=meta,
            messages=messages,
            session_id=name,
        )
    except Exception:
        logger.exception("share: creating the artifact failed for %r", key)
        return web.json_response({"error": "could not create the shared artifact"}, status=500)

    sel().log_api_access(
        caller="dashboard",
        operation="chat.session_share",
        outcome="allowed",
        source="dashboard",
        resources=f"slug={art.slug} messages={len(messages)}",
    )
    return web.json_response(
        {
            "ok": True,
            "slug": art.slug,
            "name": art.name,
            "kind": art.kind,
            "readonly": art.readonly,
            "redacted": True,
        },
        status=201,
    )


def register_routes(app: web.Application) -> None:
    """Wire the S3 template + export + share routes.

    The literal `templates` paths must be registered before `/api/chat/sessions/{session}`
    (see the module docstring).
    """
    app.router.add_get("/api/chat/sessions/templates", api_session_templates_list)
    app.router.add_post("/api/chat/sessions/templates", api_session_templates_create)
    app.router.add_put("/api/chat/sessions/templates/{template}", api_session_template_update)
    app.router.add_delete("/api/chat/sessions/templates/{template}", api_session_template_delete)
    app.router.add_get("/api/chat/sessions/{session}/export", api_session_export)
    app.router.add_post("/api/chat/sessions/{session}/share", api_session_share)

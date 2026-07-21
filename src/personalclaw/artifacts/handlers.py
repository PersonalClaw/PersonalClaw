"""HTTP handlers for /api/artifacts — provider-agnostic artifact entity endpoints.

Every handler resolves a provider via ``registry.get_provider(?provider)`` and
calls ``provider.<method>(...)`` — never a singleton. All LLM-authored string
fields are redacted on the way out (``_serialize``); mutations are gated against
restricted (incognito/guest) sessions and SEL-audited.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from aiohttp import web

from personalclaw.artifacts import registry
from personalclaw.artifacts.models import Artifact
from personalclaw.dashboard.handlers._shared import _is_restricted_session
from personalclaw.security import redact_credentials, redact_exfiltration_urls
from personalclaw.sel import sel

logger = logging.getLogger(__name__)

# Browser client's literal session key — never a real chat session for the
# timeline deep-link, so drop it to None.
_UI_SESSION_KEY = "dashboard:ui"


def _redact(text: str) -> str:
    clean, _ = redact_exfiltration_urls(text or "")
    clean, _ = redact_credentials(clean)
    return clean


def _serialize(art: Artifact, *, include_content: bool = False) -> dict[str, Any]:
    """Serialize an artifact for the API, redacting every LLM-authored field."""
    d = art.to_dict(persist=False)
    d["name"] = _redact(d.get("name", ""))
    d["description"] = _redact(d.get("description", ""))
    d["tags"] = [_redact(t) for t in d.get("tags", [])]
    if include_content and d.get("content") is not None:
        d["content"] = _redact(d["content"])
    else:
        d.pop("content", None)
    return d


def _session_key(request: web.Request) -> str | None:
    sk = request.headers.get("X-Session-Key", "")
    if not sk or sk == _UI_SESSION_KEY:
        return None
    return sk.split(":", 1)[-1] if ":" in sk else sk


def _audit(request: web.Request, operation: str, outcome: str, resources: str = "") -> None:
    try:
        sel().log_api_access(
            caller=request.headers.get("X-Session-Key", "") or "dashboard:ui",
            operation=operation,
            outcome=outcome,
            source="dashboard",
            resources=resources,
        )
    except Exception:
        logger.debug("SEL audit failed for %s", operation, exc_info=True)


def _provider(request: web.Request):
    return registry.get_provider(request.query.get("provider") or "native")


async def api_artifacts_list(request: web.Request) -> web.Response:
    """GET /api/artifacts — list (no content). Filters: tag, kind, q, source, source_path, project_id."""  # noqa: E501
    prov = _provider(request)
    if prov is None:
        return web.json_response({"error": "unknown provider"}, status=400)
    arts = prov.list(
        tag=request.query.get("tag"),
        kind=request.query.get("kind"),
        q=request.query.get("q"),
        source=request.query.get("source"),
        source_path=request.query.get("source_path"),
        project_id=request.query.get("project_id"),
    )
    return web.json_response({"artifacts": [_serialize(a) for a in arts]})


async def api_artifacts_create(request: web.Request) -> web.Response:
    """POST /api/artifacts — create (or bump an existing file-backed artifact)."""
    state = request.app["state"]
    if _is_restricted_session(state, request):
        _audit(request, "artifact.create", "denied", "restricted_session")
        return web.json_response({"error": "restricted session"}, status=403)
    prov = _provider(request)
    if prov is None:
        return web.json_response({"error": "unknown provider"}, status=400)
    if prov.readonly:
        return web.json_response({"error": f"provider '{prov.name}' is read-only"}, status=400)
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)
    name = str(body.get("name", "")).strip()
    if not name:
        return web.json_response({"error": "name required"}, status=400)
    content = str(body.get("content", ""))
    source_path = str(body.get("source_path", "")).strip()
    session_id = _session_key(request)

    # Dedup by source_path: re-saving a file-backed artifact bumps the existing
    # one rather than creating a duplicate.
    if source_path:
        existing = prov.find_by_source_path(source_path)
        if existing is not None:
            updated = prov.update(
                existing.slug,
                content=content,
                snapshot=False,
                actor="user",
                session_id=session_id,
            )
            _audit(request, "artifact.update", "ok", f"slug={existing.slug}")
            return web.json_response(
                _serialize(updated, include_content=True) if updated else {}, status=200
            )

    requested_slug = str(body.get("slug", "")).strip() or None
    if requested_slug and prov.get(requested_slug) is not None:
        return web.json_response({"error": "slug already exists"}, status=409)
    try:
        art = prov.create(
            name=name,
            content=content,
            kind=str(body.get("kind", "widget")),
            source=str(body.get("source", "chat")),
            slug=requested_slug,
            source_path=source_path,
            description=str(body.get("description", "")),
            tags=body.get("tags"),
            actor="user",
            session_id=session_id,
            project_id=str(body.get("project_id", "")).strip(),
        )
    except (ValueError, PermissionError) as e:
        return web.json_response({"error": str(e)}, status=400)
    _audit(request, "artifact.create", "ok", f"slug={art.slug}")
    return web.json_response(_serialize(art, include_content=True), status=201)


async def api_artifact_detail(request: web.Request) -> web.Response:
    """GET /api/artifacts/{slug} — full content (live-pointer read for file-backed).

    With ``?probe=1`` it's an existence check: returns 200 ``{exists: bool}``
    (never 404), so callers that only ask "is this saved?" (e.g. the widget
    bookmark toggle) don't spam the browser console with expected 404s.
    """
    prov = _provider(request)
    if prov is None:
        return web.json_response({"error": "unknown provider"}, status=400)
    slug = request.match_info["slug"]
    try:
        art = prov.get(slug)
    except ValueError:
        return web.json_response({"error": "invalid slug"}, status=400)
    if request.query.get("probe"):
        return web.json_response({"exists": art is not None})
    if art is None:
        return web.json_response({"error": "not found"}, status=404)
    return web.json_response(_serialize(art, include_content=True))


async def api_artifact_update(request: web.Request) -> web.Response:
    """PATCH /api/artifacts/{slug} — save (silent) or snapshot; or metadata-only."""
    state = request.app["state"]
    if _is_restricted_session(state, request):
        _audit(request, "artifact.update", "denied", "restricted_session")
        return web.json_response({"error": "restricted session"}, status=403)
    prov = _provider(request)
    if prov is None:
        return web.json_response({"error": "unknown provider"}, status=400)
    if prov.readonly:
        return web.json_response({"error": f"provider '{prov.name}' is read-only"}, status=400)
    slug = request.match_info["slug"]
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)
    # A revert is its own operation: restore version N's body (text or binary)
    # server-side rather than round-tripping content from the client (which can't
    # carry binary bytes — the FE only holds a raw-URL ref).
    if body.get("event_type") == "reverted":
        try:
            from_version = int(body.get("from_version") or 0)
        except (TypeError, ValueError):
            return web.json_response({"error": "from_version must be an integer"}, status=400)
        if from_version <= 0:
            return web.json_response({"error": "from_version is required to revert"}, status=400)
        try:
            art = prov.revert(slug, from_version, actor="user", session_id=_session_key(request))
        except (ValueError, PermissionError, NotImplementedError) as e:
            return web.json_response({"error": str(e)}, status=400)
        if art is None:
            return web.json_response({"error": "not found"}, status=404)
        _audit(request, "artifact.update", "ok", f"slug={slug} reverted->{from_version}")
        return web.json_response(_serialize(art, include_content=True))
    try:
        art = prov.update(
            slug,
            content=body.get("content"),
            snapshot=bool(body.get("snapshot", False)),
            event_type=body.get("event_type"),
            actor="user",
            session_id=_session_key(request),
            name=body.get("name"),
            description=body.get("description"),
            tags=body.get("tags"),
        )
    except (ValueError, PermissionError) as e:
        return web.json_response({"error": str(e)}, status=400)
    if art is None:
        return web.json_response({"error": "not found"}, status=404)
    _audit(request, "artifact.update", "ok", f"slug={slug}")
    return web.json_response(_serialize(art, include_content=True))


async def api_artifact_delete(request: web.Request) -> web.Response:
    """DELETE /api/artifacts/{slug}."""
    state = request.app["state"]
    if _is_restricted_session(state, request):
        _audit(request, "artifact.delete", "denied", "restricted_session")
        return web.json_response({"error": "restricted session"}, status=403)
    prov = _provider(request)
    if prov is None:
        return web.json_response({"error": "unknown provider"}, status=400)
    if prov.readonly:
        return web.json_response({"error": f"provider '{prov.name}' is read-only"}, status=400)
    slug = request.match_info["slug"]
    try:
        deleted = prov.delete(slug)
    except ValueError:
        return web.json_response({"error": "invalid slug"}, status=400)
    if not deleted:
        return web.json_response({"error": "not found"}, status=404)
    _audit(request, "artifact.delete", "ok", f"slug={slug}")
    return web.json_response({"ok": True})


async def api_artifact_raw(request: web.Request) -> web.Response:
    """GET /api/artifacts/{slug}/raw — stream a binary artifact's bytes.

    Backs ``kind:image`` rendering: the JSON ``content`` carries this URL (not the
    bytes), and the renderer/<img> fetches the actual image here. ``?version=N``
    serves an immutable snapshot. Bytes are NOT redacted (they're not LLM text).
    """
    prov = _provider(request)
    if prov is None:
        return web.json_response({"error": "unknown provider"}, status=400)
    slug = request.match_info["slug"]
    version: int | None = None
    if request.query.get("version"):
        try:
            version = int(request.query["version"])
        except ValueError:
            return web.json_response({"error": "invalid version"}, status=400)
    try:
        result = prov.raw_bytes(slug, version=version)
    except ValueError:
        return web.json_response({"error": "invalid slug"}, status=400)
    if result is None:
        return web.json_response({"error": "not found"}, status=404)
    data, mime = result
    # Immutable per (slug, version): a versioned read is content-addressable, the
    # live body changes only on edit — cache the version hard, revalidate live.
    cache = "public, max-age=31536000, immutable" if version is not None else "no-cache"
    return web.Response(
        body=data,
        content_type=mime or "application/octet-stream",
        headers={"Cache-Control": cache, "X-Content-Type-Options": "nosniff"},
    )


def _recover_image_gen_args(session_key: str, slug: str) -> dict[str, str] | None:
    """Recover the original image_generate args (prompt/size) for *slug* from a
    session's history — the tool record whose output names this slug.

    The transcript records each ``image_generate`` call with ``meta.input`` (the
    JSON args) and ``meta.output`` (which contains ``slug: <slug>``). We scan
    newest-first so the most recent call that produced this slug wins. Returns
    ``{"prompt", "size"}`` or None if not found.
    """
    import json as _json

    from personalclaw.dashboard.chat_utils import _history_key_for
    from personalclaw.history import ConversationLog

    try:
        # The FE passes the dashboard session id (e.g. "chat-1-…"); the history file
        # is keyed "dashboard:chat-1-…". Normalize so the lookup hits the log.
        msgs = ConversationLog()._read_messages(
            _history_key_for(session_key)
        )  # noqa: SLF001 — read-only history access
    except Exception:
        return None
    # Prefer the GENERATION record that created this slug ("Generated image …") over
    # an EDIT record ("Edited image artifact …"): regenerating recreates the original
    # from scratch, so an edit's incremental instruction ("add a glow") is the wrong
    # prompt — it assumes a base image. Fall back to an edit prompt only if no
    # generation record is found. Scan newest-first within each class.
    edit_match: dict[str, str] | None = None
    for m in reversed(msgs):
        meta = m.get("meta") if isinstance(m, dict) else None
        if not isinstance(meta, dict):
            continue
        out = str(meta.get("output", ""))
        if f"slug: {slug}" not in out and f"slug:{slug}" not in out:
            continue
        raw_in = meta.get("input", "")
        try:
            args = _json.loads(raw_in) if isinstance(raw_in, str) else (raw_in or {})
        except (ValueError, TypeError):
            args = {}
        if not isinstance(args, dict):
            continue
        prompt = str(args.get("prompt", "")).strip()
        if not prompt:
            continue
        entry = {"prompt": prompt, "size": str(args.get("size", "")).strip()}
        is_edit = bool(str(args.get("edit_artifact", "")).strip()) or out.lstrip().startswith(
            "Edited"
        )
        if is_edit:
            edit_match = edit_match or entry
        else:
            return entry  # newest generation record wins
    return edit_match


async def api_artifact_regenerate(request: web.Request) -> web.Response:
    """POST /api/artifacts/{slug}/regenerate — re-run image generation at this slug.

    Backs the chat placeholder's "Regenerate" button for a deleted/missing inline
    image. Recovers the original prompt from the session's tool-call history (or a
    ``prompt`` in the body as fallback) and re-runs generation, landing the bytes
    back at the SAME slug so the transcript's existing ``/raw`` reference resolves —
    no new chat message, no LLM turn. Mutating → gated + audited.
    """
    state = request.app["state"]
    if _is_restricted_session(state, request):
        _audit(request, "artifact.regenerate", "denied", "restricted_session")
        return web.json_response({"error": "restricted session"}, status=403)
    prov = _provider(request)
    if prov is None:
        return web.json_response({"error": "unknown provider"}, status=400)
    if prov.readonly:
        return web.json_response({"error": f"provider '{prov.name}' is read-only"}, status=400)
    slug = request.match_info["slug"]
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}

    session_key = str(body.get("session", "")).strip()
    args = _recover_image_gen_args(session_key, slug) if session_key else None
    # Fallback: the FE may pass the prompt (the placeholder's caption) when history
    # lookup misses (e.g. an old/rotated log).
    prompt = (args or {}).get("prompt") or str(body.get("prompt", "")).strip()
    size = (args or {}).get("size", "") or str(body.get("size", "")).strip()
    if not prompt:
        _audit(request, "artifact.regenerate", "error", f"slug={slug} no prompt recoverable")
        return web.json_response({"error": "could not recover the original prompt"}, status=422)

    from personalclaw.mcp_artifacts import regenerate_image_at_slug

    ok, msg = await asyncio.to_thread(
        regenerate_image_at_slug,
        prov,
        slug,
        prompt,
        size=size,
        session_id=session_key or None,
    )
    if not ok:
        _audit(request, "artifact.regenerate", "error", f"slug={slug}: {msg}")
        return web.json_response({"error": msg}, status=502)
    _audit(request, "artifact.regenerate", "ok", f"slug={slug}")
    return web.json_response({"ok": True, "slug": slug})


async def api_artifact_versions(request: web.Request) -> web.Response:
    """GET /api/artifacts/{slug}/versions."""
    prov = _provider(request)
    if prov is None:
        return web.json_response({"error": "unknown provider"}, status=400)
    slug = request.match_info["slug"]
    try:
        versions = prov.list_versions(slug)
    except ValueError:
        return web.json_response({"error": "invalid slug"}, status=400)
    return web.json_response({"slug": slug, "versions": versions})


async def api_artifact_version_detail(request: web.Request) -> web.Response:
    """GET /api/artifacts/{slug}/versions/{version} — immutable historical content."""
    prov = _provider(request)
    if prov is None:
        return web.json_response({"error": "unknown provider"}, status=400)
    slug = request.match_info["slug"]
    try:
        version = int(request.match_info["version"])
    except ValueError:
        return web.json_response({"error": "invalid version"}, status=400)
    try:
        art = prov.get(slug, version=version)
    except ValueError:
        return web.json_response({"error": "invalid slug"}, status=400)
    if art is None:
        return web.json_response({"error": "not found"}, status=404)
    return web.json_response(_serialize(art, include_content=True))


async def api_artifact_events(request: web.Request) -> web.Response:
    """GET /api/artifacts/{slug}/events — activity timeline (drops dashboard:ui)."""
    prov = _provider(request)
    if prov is None:
        return web.json_response({"error": "unknown provider"}, status=400)
    slug = request.match_info["slug"]
    try:
        art = prov.get(slug)
    except ValueError:
        return web.json_response({"error": "invalid slug"}, status=400)
    if art is None:
        return web.json_response({"error": "not found"}, status=404)
    events = []
    for e in art.events:
        d = e.to_dict()
        if d.get("session_id") == _UI_SESSION_KEY:
            d["session_id"] = ""
        events.append(d)
    return web.json_response({"slug": slug, "events": events})


async def api_artifact_record_event(request: web.Request) -> web.Response:
    """POST /api/artifacts/{slug}/events — record a 'referenced' impression."""
    state = request.app["state"]
    if _is_restricted_session(state, request):
        return web.json_response({"error": "restricted session"}, status=403)
    prov = _provider(request)
    if prov is None:
        return web.json_response({"error": "unknown provider"}, status=400)
    slug = request.match_info["slug"]
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict) or body.get("type") != "referenced":
        return web.json_response({"error": "only type 'referenced' allowed"}, status=400)
    try:
        art, appended = prov.record_impression(
            slug,
            by="user",
            session_id=_session_key(request),
            message_ts=str(body.get("message_ts", "")) or None,
            widget_index=body.get("widget_index"),
        )
    except ValueError:
        return web.json_response({"error": "invalid slug"}, status=400)
    if art is None:
        return web.json_response({"error": "not found"}, status=404)
    return web.json_response({"ok": True, "appended": appended})


def register_artifact_routes(app: web.Application) -> None:
    """Register /api/artifacts/* routes. The native provider self-registers
    lazily via the registry; no startup registration needed."""
    app.router.add_get("/api/artifacts", api_artifacts_list)
    app.router.add_post("/api/artifacts", api_artifacts_create)
    app.router.add_get("/api/artifacts/{slug}", api_artifact_detail)
    app.router.add_patch("/api/artifacts/{slug}", api_artifact_update)
    app.router.add_delete("/api/artifacts/{slug}", api_artifact_delete)
    app.router.add_get("/api/artifacts/{slug}/raw", api_artifact_raw)
    app.router.add_post("/api/artifacts/{slug}/regenerate", api_artifact_regenerate)
    app.router.add_get("/api/artifacts/{slug}/versions", api_artifact_versions)
    app.router.add_get("/api/artifacts/{slug}/versions/{version}", api_artifact_version_detail)
    app.router.add_get("/api/artifacts/{slug}/events", api_artifact_events)
    app.router.add_post("/api/artifacts/{slug}/events", api_artifact_record_event)

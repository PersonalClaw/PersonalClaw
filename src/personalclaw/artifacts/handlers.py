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
from personalclaw.artifacts.folders import ArtifactFolder, ArtifactFolderStore, delete_folder
from personalclaw.artifacts.models import Artifact, ext_for_mime
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
    d["collection"] = _redact(d.get("collection", ""))
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
        collection=request.query.get("collection"),
        # `.get` (not `.get(..., "")`): an ABSENT ?folder means every folder, while
        # an empty `?folder=` means the unfiled bucket. See provider.list.
        folder=request.query.get("folder"),
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
    if requested_slug:
        try:
            existing = prov.get(requested_slug)
        except ValueError:
            return web.json_response({"error": "invalid slug"}, status=400)
        if existing is not None:
            return web.json_response({"error": "slug already exists"}, status=409)
    # Server-backed dedup hint (ARTIFACTS S1): a fresh save (no slug, no source_path)
    # whose name matches an existing artifact 409s with the existing slug so the UI can
    # offer "open it / save anyway". `?force=1` bypasses (mint a new artifact anyway).
    force = request.query.get("force") in ("1", "true")
    if not requested_slug and not source_path and not force:
        similar = prov.find_similar(name, kind=str(body.get("kind", "widget")))
        if similar is not None:
            _audit(request, "artifact.create", "deduped", f"similar={similar.slug}")
            return web.json_response(
                {
                    "error": "similar_artifact_exists",
                    "similar": {"slug": similar.slug, "name": similar.name, "kind": similar.kind},
                },
                status=409,
            )
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
            collection=str(body.get("collection", "")).strip(),
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
            collection=body.get("collection"),
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


#: Extracted-text preview cap. A generated document can be long, and this powers a
#: PREVIEW beside a download — not the authoritative read path (that is the knowledge
#: reader, which stores the whole thing).
_EXTRACT_PREVIEW_CHARS = 20_000


async def api_artifact_extract(request: web.Request) -> web.Response:
    """GET /api/artifacts/{slug}/extract — extracted text for a binary document artifact.

    Backs the honest "text preview — download for full formatting" surface for generated
    docx/xlsx/pdf. Reuses the SAME reader that ingests uploaded documents rather than a
    second extraction path, so a generated file is read exactly like a user's own.
    """
    prov = _provider(request)
    if prov is None:
        return web.json_response({"error": "unknown provider"}, status=400)
    slug = request.match_info["slug"]
    try:
        result = prov.raw_bytes(slug)
    except ValueError:
        return web.json_response({"error": "invalid slug"}, status=400)
    if result is None:
        return web.json_response({"error": "not found"}, status=404)
    data, mime = result
    ext = ext_for_mime(mime or "")
    if not ext:
        return web.json_response(
            {"error": {"code": "not_extractable", "message": f"no reader for {mime!r}"}},
            status=400,
        )

    import tempfile
    from pathlib import Path as _Path

    from personalclaw.knowledge.readers import FileReader

    # The readers take a path, so the bytes land in a temp file that is removed
    # immediately — the artifact store stays the only durable copy.
    text = ""
    with tempfile.TemporaryDirectory() as tmp:
        scratch = _Path(tmp) / f"artifact.{ext}"
        scratch.write_bytes(data)
        try:
            text, _meta = FileReader().read(str(scratch))
        except Exception:  # noqa: BLE001 — an unreadable document is a 400, not a 500
            logger.info("artifact extract failed for %s", slug, exc_info=True)
            return web.json_response(
                {"error": {"code": "extract_failed", "message": "could not read the document"}},
                status=400,
            )
    truncated = len(text) > _EXTRACT_PREVIEW_CHARS
    # Redacted like every other LLM-adjacent text surface: a generated document can
    # contain whatever was in the prompt that produced it.
    body, _ = redact_credentials(text[:_EXTRACT_PREVIEW_CHARS])
    return web.json_response({"slug": slug, "text": body, "truncated": truncated})


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


async def api_artifacts_pinned(request: web.Request) -> web.Response:
    """GET /api/artifacts/pinned — the dashboard pin list (WORK-CONTAINERS §6.5d).

    REFERENCES only. Each row is a slug plus when it was pinned; the widget resolves the artifact
    itself through the list route. Returning denormalized names here would go stale on the next
    rename, and a dashboard card showing a title the artifact no longer has is confidently wrong.
    """
    from personalclaw.workflows import pinned

    return web.json_response({"pins": pinned.list_pins()})


async def api_artifacts_pin(request: web.Request) -> web.Response:
    """POST /api/artifacts/{slug}/pin — pin or unpin (``{"pinned": bool}``).

    ONE route for both directions rather than a pin route and an unpin route: it is one piece of
    state with two values, and two endpoints would be two places to keep the cap and the
    dedup-by-slug rule right.

    Gated like every other artifact mutation — a restricted (incognito/guest) session must not
    write durable dashboard state, which is exactly what a pin is.
    """
    from personalclaw.workflows import pinned

    state = request.app.get("state")
    if _is_restricted_session(state, request):
        _audit(request, "artifact.pin", "denied", "restricted_session")
        return web.json_response({"error": "restricted session"}, status=403)
    slug = request.match_info.get("slug", "")
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    # Default TRUE: the route is reached from a Pin control, so the common call carries no body.
    # Defaulting to unpin would make the bodyless call silently do the opposite of its name.
    want = bool(body.get("pinned", True))
    pins = pinned.pin(slug, run_id=str(body.get("run_id") or "")) if want else pinned.unpin(slug)
    _audit(request, "artifact.pin" if want else "artifact.unpin", "allowed", slug)
    return web.json_response({"ok": True, "pinned": want, "pins": pins})


def _folder_store(prov: Any) -> ArtifactFolderStore:
    """A folder store rooted at the SAME tree the provider owns, so a provider on a
    custom root (tests, a dev home) never writes folders into the default home."""
    return ArtifactFolderStore(getattr(prov, "root", None))


def _serialize_folder(folder: ArtifactFolder) -> dict[str, Any]:
    d = folder.to_dict()
    d["name"] = _redact(d.get("name", ""))
    return d


async def api_artifact_folders(request: web.Request) -> web.Response:
    """GET /api/artifacts/folders — the library folder tree (flat, parent_id-linked)."""
    prov = _provider(request)
    if prov is None:
        return web.json_response({"error": "unknown provider"}, status=400)
    folders = _folder_store(prov).list()
    return web.json_response({"folders": [_serialize_folder(f) for f in folders]})


async def api_artifact_folder_create(request: web.Request) -> web.Response:
    """POST /api/artifacts/folders — create a folder (``{name, parent_id?, icon?}``)."""
    state = request.app["state"]
    if _is_restricted_session(state, request):
        _audit(request, "artifact.folder_create", "denied", "restricted_session")
        return web.json_response({"error": "restricted session"}, status=403)
    prov = _provider(request)
    if prov is None:
        return web.json_response({"error": "unknown provider"}, status=400)
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)
    try:
        folder = _folder_store(prov).create(
            str(body.get("name", "")),
            parent_id=str(body.get("parent_id", "") or ""),
            icon=str(body.get("icon", "") or ""),
        )
    except ValueError as exc:
        _audit(request, "artifact.folder_create", "denied", str(exc))
        return web.json_response({"error": str(exc)}, status=400)
    _audit(request, "artifact.folder_create", "allowed", folder.id)
    return web.json_response(_serialize_folder(folder), status=201)


async def api_artifact_folder_update(request: web.Request) -> web.Response:
    """PATCH /api/artifacts/folders/{id} — rename / re-nest / reorder. No artifact is touched."""
    state = request.app["state"]
    if _is_restricted_session(state, request):
        _audit(request, "artifact.folder_update", "denied", "restricted_session")
        return web.json_response({"error": "restricted session"}, status=403)
    prov = _provider(request)
    if prov is None:
        return web.json_response({"error": "unknown provider"}, status=400)
    fid = request.match_info["id"]
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)
    try:
        folder = _folder_store(prov).update(
            fid,
            name=str(body["name"]) if "name" in body else None,
            parent_id=str(body["parent_id"] or "") if "parent_id" in body else None,
            order=int(body["order"]) if "order" in body else None,
            icon=str(body["icon"] or "") if "icon" in body else None,
        )
    except (ValueError, TypeError) as exc:
        _audit(request, "artifact.folder_update", "denied", str(exc))
        return web.json_response({"error": str(exc)}, status=400)
    if folder is None:
        return web.json_response({"error": "not found"}, status=404)
    _audit(request, "artifact.folder_update", "allowed", fid)
    return web.json_response(_serialize_folder(folder))


async def api_artifact_folder_delete(request: web.Request) -> web.Response:
    """DELETE /api/artifacts/folders/{id} — members fall back to unfiled; nothing is destroyed."""
    state = request.app["state"]
    if _is_restricted_session(state, request):
        _audit(request, "artifact.folder_delete", "denied", "restricted_session")
        return web.json_response({"error": "restricted session"}, status=403)
    prov = _provider(request)
    if prov is None:
        return web.json_response({"error": "unknown provider"}, status=400)
    if prov.readonly:
        return web.json_response({"error": f"provider '{prov.name}' is read-only"}, status=400)
    fid = request.match_info["id"]
    deleted, unfiled = delete_folder(_folder_store(prov), prov, fid)
    if not deleted:
        return web.json_response({"error": "not found"}, status=404)
    _audit(request, "artifact.folder_delete", "allowed", fid)
    return web.json_response({"ok": True, "unfiled": unfiled})


async def api_artifact_set_folder(request: web.Request) -> web.Response:
    """PATCH /api/artifacts/{slug}/folder — file an artifact (``{folder_id}``; "" = unfiled).

    Its own route rather than a field on PATCH /{slug}: that handler routes through
    ``provider.update``, which bumps ``updated_at``. Filing must not.
    """
    state = request.app["state"]
    if _is_restricted_session(state, request):
        _audit(request, "artifact.set_folder", "denied", "restricted_session")
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
    folder_id = str(body.get("folder_id", "") or "").strip()
    if folder_id and not _folder_store(prov).exists(folder_id):
        return web.json_response({"error": "folder not found"}, status=400)
    try:
        art = prov.set_folder(slug, folder_id)
    except (ValueError, PermissionError) as exc:
        _audit(request, "artifact.set_folder", "denied", slug)
        return web.json_response({"error": str(exc)}, status=400)
    if art is None:
        return web.json_response({"error": "not found"}, status=404)
    _audit(request, "artifact.set_folder", "allowed", slug)
    return web.json_response({"ok": True, "folder_id": art.folder_id})


def register_artifact_routes(app: web.Application) -> None:
    """Register /api/artifacts/* routes. The native provider self-registers
    lazily via the registry; no startup registration needed."""
    app.router.add_get("/api/artifacts", api_artifacts_list)
    app.router.add_post("/api/artifacts", api_artifacts_create)
    # BEFORE `/{slug}`: aiohttp matches in registration order, so a dynamic `{slug}` registered
    # first would swallow the literal `pinned` path and answer it as an artifact named "pinned".
    app.router.add_get("/api/artifacts/pinned", api_artifacts_pinned)
    # Same reason as `pinned`: the literal `folders` paths must precede `{slug}`, and
    # `/folders/{id}` must precede `/{slug}/folder` so a 12-hex folder id is never
    # read as an artifact slug.
    app.router.add_get("/api/artifacts/folders", api_artifact_folders)
    app.router.add_post("/api/artifacts/folders", api_artifact_folder_create)
    app.router.add_patch("/api/artifacts/folders/{id}", api_artifact_folder_update)
    app.router.add_delete("/api/artifacts/folders/{id}", api_artifact_folder_delete)
    app.router.add_get("/api/artifacts/{slug}", api_artifact_detail)
    app.router.add_patch("/api/artifacts/{slug}", api_artifact_update)
    app.router.add_delete("/api/artifacts/{slug}", api_artifact_delete)
    app.router.add_get("/api/artifacts/{slug}/raw", api_artifact_raw)
    app.router.add_get("/api/artifacts/{slug}/extract", api_artifact_extract)
    app.router.add_post("/api/artifacts/{slug}/regenerate", api_artifact_regenerate)
    app.router.add_get("/api/artifacts/{slug}/versions", api_artifact_versions)
    app.router.add_get("/api/artifacts/{slug}/versions/{version}", api_artifact_version_detail)
    app.router.add_get("/api/artifacts/{slug}/events", api_artifact_events)
    app.router.add_post("/api/artifacts/{slug}/events", api_artifact_record_event)
    app.router.add_post("/api/artifacts/{slug}/pin", api_artifacts_pin)
    app.router.add_patch("/api/artifacts/{slug}/folder", api_artifact_set_folder)

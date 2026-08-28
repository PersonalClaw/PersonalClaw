"""Knowledge Library API handlers."""

import asyncio
import json
import logging
import re
import shutil
import tempfile
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from aiohttp import web

if TYPE_CHECKING:  # pragma: no cover - typing only
    from personalclaw.knowledge.restructure import RestructureError

from personalclaw.dashboard.sse import stream_response
from personalclaw.http_errors import json_error
from personalclaw.knowledge.artifact_ingest import ARTIFACT_ITEM_TYPE, ARTIFACT_SOURCE_PROVIDER
from personalclaw.knowledge.embedder import create_embedder_from_config, floats_to_bytes
from personalclaw.knowledge.llm_pool import LLMPool
from personalclaw.knowledge.media import classify, guess_mime, make_image_thumbnail
from personalclaw.knowledge.retrieval import HybridRetriever, _bytes_to_floats
from personalclaw.knowledge.semantics import DEFAULT_LIST_EXCLUDED_KINDS
from personalclaw.knowledge.staleness import is_synthesized, staleness_for
from personalclaw.security import redact_credentials, redact_exfiltration_urls
from personalclaw.sel import sel

logger = logging.getLogger(__name__)


def _redact(text: str | None) -> str | None:
    """Redact exfiltration URLs + credentials from LLM-derived text before it's
    returned to the client (entity/relation fields, retrieval previews)."""
    if not text:
        return text
    text, _ = redact_exfiltration_urls(text)
    text, _ = redact_credentials(text)
    return text


def _serialize_entity(row) -> dict:
    """API shape for an entity row: aliases parsed to a real array (not the stored
    JSON string — same contract as item tags), and the LLM-derived description
    scrubbed of credentials/exfiltration URLs before it reaches the client."""
    d = dict(row)
    raw_aliases = d.get("aliases")
    if isinstance(raw_aliases, str):
        try:
            d["aliases"] = json.loads(raw_aliases) if raw_aliases else []
        except (json.JSONDecodeError, ValueError):
            d["aliases"] = []
    elif raw_aliases is None:
        d["aliases"] = []
    if "description" in d:
        d["description"] = _redact(d.get("description"))
    return d


def _sel_log(tool: str, **kwargs: object) -> None:
    """Emit SEL audit event for knowledge API mutations."""
    sel().log_tool_invocation(
        session_key="dashboard",
        agent="knowledge-api",
        tool_name=f"knowledge.{tool}",
        outcome=str(kwargs.pop("outcome", "completed")),
        resources=str(kwargs) if kwargs else "",
    )


def _store(request: web.Request):
    return request.app["state"].knowledge_store


def _create_embedder(app):
    """Create embedder from PersonalClaw config. Returns None if disabled/unavailable."""
    from personalclaw.config.loader import config_path

    cfg_path = config_path()
    try:
        cfg = json.loads(cfg_path.read_text()) if cfg_path.exists() else {}
    except Exception:
        cfg = {}
    return create_embedder_from_config(cfg)


def _get_embedder(request_or_app):
    """Resolve the active embedder dynamically — never stale.

    Checks the boot-time cached instance first (fast path); if absent, tries to
    build one on demand from the current model binding. This means setting an
    embedding model in Settings → Models takes effect immediately without a
    gateway restart."""
    app = request_or_app if isinstance(request_or_app, dict) else request_or_app.app
    embedder = app.get("knowledge_embedder")
    if embedder is not None:
        return embedder
    embedder = _create_embedder(app)
    if embedder is not None:
        app["knowledge_embedder"] = embedder
    return embedder


# ---------- Namespaces ----------


async def list_tags(request: web.Request) -> web.Response:
    """GET /api/knowledge/tags -- distinct tags (frequency-ordered) for autocomplete."""
    return web.json_response({"tags": _store(request).all_tags()})


# The list/search views only render a short one-line snippet from content, so the
# list endpoint ships a truncated preview instead of every item's full body — a big
# payload win for libraries with large documents/transcripts. The detail view fetches
# full content via GET /items/{id}.
_LIST_CONTENT_PREVIEW = 280


def _list_item(store, row) -> dict:
    """Serialize an item for the LIST view with content trimmed to a preview."""
    item = store._serialize_item(row)
    content = item.get("content") or ""
    if len(content) > _LIST_CONTENT_PREVIEW:
        item["content"] = content[:_LIST_CONTENT_PREVIEW]
        item["content_truncated"] = True
    return item


# ---------- Items ----------


async def list_items(request: web.Request) -> web.Response:
    """GET /api/knowledge/items -- list/search with pagination."""
    store = _store(request)
    q = request.query.get("q")
    item_type = request.query.get("type")
    # `kind` is the TAXONOMY axis and stays separate from `type` (`item_type`, the ingestion
    # graph's route). Overloading `?type=` to also accept a kind would make one param answer
    # "how did this arrive" and "what sort of knowledge is it" — the exact conflation
    # `semantics.KINDS` exists to prevent.
    kind = request.query.get("kind")
    status = request.query.get("status")
    provider = request.query.get("provider")
    try:
        page = max(1, int(request.query.get("page", 1)))
        limit = min(100, max(1, int(request.query.get("limit", 20))))
    except ValueError:
        return web.json_response({"error": "invalid page/limit"}, status=400)

    if q:
        # Use hybrid search: FTS5 keyword + graph traversal + optional vector + RRF fusion
        embedder = _get_embedder(request)
        embed_fn = embedder.embed if embedder and embedder.is_available() else None
        retriever = HybridRetriever(store, embedder=embed_fn)
        # Searching WITHIN the Archived view must find archived items (the no-query
        # Archived list shows them; a search there should too). Default hides them.
        include_archived = request.query.get("include_archived") in ("1", "true", "yes")
        all_results = retriever.search(
            q, limit=limit * 3, include_archived=include_archived
        )  # over-fetch to allow filtering
        # Batch fetch all candidate items (avoid N+1)
        result_ids = [r["id"] for r in all_results]
        if result_ids:
            placeholders = ",".join("?" * len(result_ids))
            rows = store.db.execute(
                f"SELECT * FROM items WHERE id IN ({placeholders})", result_ids  # noqa: S608
            ).fetchall()
            items_by_id = {row["id"]: _list_item(store, row) for row in rows}
        else:
            items_by_id = {}
        filtered = []
        for r in all_results:
            item = items_by_id.get(r["id"])
            if not item:
                continue
            if status and item.get("status") != status:
                continue
            if item_type and item.get("item_type") != item_type:
                continue
            if kind and item.get("kind") != kind:
                continue
            if provider and (item.get("provider") or "native") != provider:
                continue
            item["_score"] = r["score"]
            item["_match_type"] = r["match_type"]
            filtered.append(item)
        total = len(filtered)
        offset = (page - 1) * limit
        items = filtered[offset : offset + limit]
        return web.json_response({"items": items, "total": total, "page": page, "limit": limit})
    else:
        where, params = ["1=1"], []  # type: list[str], list[object]
        # PEP-7: mirrored artifacts are INDEXED, not LISTED. They are found by the search
        # branch above (which does not filter them) and excluded here, because an artifact
        # already has its own library — listing it again would double every count and put two
        # rows on screen for one thing. Skipped when the caller asks for that type explicitly:
        # an explicit `?type=artifact` is a deliberate question, and a filter that silently
        # returns nothing is worse than one that answers.
        if item_type != ARTIFACT_ITEM_TYPE:
            where.append("i.item_type != ?")
            params.append(ARTIFACT_ITEM_TYPE)
        if item_type:
            where.append("i.item_type = ?")
            params.append(item_type)
        # WF2KNO-12: the same bargain one axis over. `semantics.DEFAULT_LIST_EXCLUDED_KINDS`
        # is INDEXED, not LISTED — a scheduled report's findings are retrievable material,
        # not library rows the owner has to scroll past, and a weekly report would otherwise
        # bury everything hand-authored inside a month. Search (above) does not filter them,
        # and naming one in `?kind=` drops it from the exclusion, because an explicit filter
        # that silently returns nothing is worse than one that answers.
        listed_excluded = sorted(DEFAULT_LIST_EXCLUDED_KINDS - {kind or ""})
        if listed_excluded:
            marks = ",".join("?" * len(listed_excluded))
            where.append(f"COALESCE(i.kind, '') NOT IN ({marks})")  # noqa: S608
            params.extend(listed_excluded)
        if kind:
            where.append("i.kind = ?")
            params.append(kind)
        if status:
            where.append("i.status = ?")
            params.append(status)
        if provider:
            # A NULL provider is treated as the native default (matches the API shape).
            where.append("COALESCE(i.provider, 'native') = ?")
            params.append(provider)
        # Archived items are hidden from the default list; an Archived view passes
        # include_archived=1 to see them.
        if request.query.get("include_archived") not in ("1", "true", "yes"):
            where.append("COALESCE(i.is_archived, 0) = 0")
        where_clause = " AND ".join(where)
        total = store.db.execute(
            f"SELECT COUNT(*) FROM items i WHERE {where_clause}", params  # noqa: S608
        ).fetchone()[0]
        offset = (page - 1) * limit
        # Pinned items float to top; then most-recently-updated. (Native items have
        # no source row, so order by the item's own timestamp, not the source's.)
        rows = store.db.execute(
            f"SELECT i.* FROM items i WHERE {where_clause} ORDER BY COALESCE(i.is_pinned, 0) DESC, i.updated_at DESC LIMIT ? OFFSET ?",  # noqa: S608, E501
            [*params, limit, offset],
        ).fetchall()
        items = [_list_item(store, r) for r in rows]
        return web.json_response({"items": items, "total": total, "page": page, "limit": limit})


# The 13 typed item kinds (knowledge-entity-vision). text-ish types author content
# directly; bookmark records a url; media types arrive via /ingest (file upload);
# `decision` is authored by `log_decision` (PROACTIVE-ASSISTANT §2.2).
_KNOWLEDGE_TYPES = {
    "note",
    "fleeting",
    "journal",
    "gist",
    "bookmark",
    "decision",
    "image",
    "audio",
    "video",
    "pdf",
    "document",
    "sheet",
    "slides",
}
# Types authorable via JSON create (text bodies + a bookmark URL). Media/document
# types carry file bytes, so they can ONLY be created through /ingest — creating one
# here would yield a broken item with no file. `decision` is excluded for the same
# reason with a different missing half: logging a decision also mints its one-shot
# review trigger, so an item authored here would be a decision that never comes back.
_AUTHORABLE_TYPES = {"note", "fleeting", "journal", "gist", "bookmark"}

# Non-authorable types and the path that DOES create them. Kept beside the sets above so
# the refusal names the right door: telling a caller to upload a file to /ingest in order
# to create a decision would send them somewhere that cannot make one.
_CREATION_PATH: dict[str, str] = {
    "decision": "the `log_decision` chat tool, which also schedules the review",
}
_DEFAULT_CREATION_PATH = "uploading a file to /ingest"


async def create_item(request: web.Request) -> web.Response:
    """POST /api/knowledge/items -- author a typed item directly (note/gist/
    bookmark/…). A bookmark records its URL on the item. Media types are uploaded
    via /ingest instead. Returns the created item."""
    store = _store(request)
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)

    item_type = str(body.get("type") or body.get("item_type") or "note").strip()
    if item_type not in _KNOWLEDGE_TYPES:
        return web.json_response({"error": f"unknown type {item_type!r}"}, status=400)
    if item_type not in _AUTHORABLE_TYPES:
        via = _CREATION_PATH.get(item_type, _DEFAULT_CREATION_PATH)
        return web.json_response(
            {"error": f"'{item_type}' items are created by {via}, not authored directly"},
            status=400,
        )
    title = str(body.get("title") or "").strip()
    content = str(body.get("content") or "")
    url = str(body.get("url") or "").strip()
    if item_type == "bookmark":
        if not url:
            return web.json_response({"error": "bookmark requires a url"}, status=400)
        # A bookmark is a WEB page — only http(s). Reject javascript:/data:/file:/… both
        # because they can't be scraped and because a stored javascript:/data: URL is an
        # XSS vector if ever rendered as a clickable link.
        from urllib.parse import urlsplit

        try:
            scheme = urlsplit(url).scheme.lower()
        except ValueError:
            scheme = ""
        if scheme not in ("http", "https"):
            return web.json_response(
                {"error": "bookmark url must be an http(s) web address"}, status=400
            )
    if not title and not content.strip() and not url:
        return web.json_response({"error": "title, content, or url required"}, status=400)
    if not title:
        if item_type == "journal":
            # Journals are date-driven records — enrichment never AI-titles them, so a
            # blank title becomes the entry's date rather than a truncated content slug.
            from datetime import datetime

            title = datetime.now().strftime("%B %-d, %Y")
        else:
            title = url or content[:60].strip() or "Untitled"

    tags = body.get("tags") if isinstance(body.get("tags"), list) else []
    # Bookmark dedup: re-saving a URL already in this space returns the existing item
    # rather than creating a duplicate (a common double-save). Other types aren't
    # URL-keyed, so they're never deduped.
    if item_type == "bookmark":
        existing = store.find_active_by_url(url)
        if existing:
            _sel_log("item.create.dedup", item_id=existing["id"], url=url)
            return web.json_response(existing, status=200)
    # Route through the native provider so the item is registered into the library
    # AND enqueued for node-graph ingestion (#30): graph → extracted-content pool →
    # insights → embed, with live per-item SSE progress. The provider's enqueue
    # replaces the old create-fast/_schedule_intelligence path (the graph's terminal
    # stages now own insights + embed).
    provider = request.app["state"].knowledge_provider()
    item_id = provider.create_typed(
        item_type=item_type,
        title=title,
        content=content,
        tags=tags,
        url=url,
        summary=str(body.get("summary") or ""),
        gist_language=str(body.get("gist_language") or "") if item_type == "gist" else "",
    )
    _sel_log("item.create", item_id=item_id, type=item_type)
    item = store.get_item(item_id)
    return web.json_response(item, status=201)


async def generate_intelligence(request: web.Request) -> web.Response:
    """POST /api/knowledge/items/{id}/generate-intelligence -- (re)run the FULL
    ingestion node-graph over this item by re-enqueueing it, so a single-item
    "Regenerate" refreshes EVERYTHING (insights, entities, intents, AI tags/title,
    embedding) — the same complete refresh as a content edit or the batch regen, not
    a narrower insights-only pass that left the embedding/graph stale."""
    store = _store(request)
    item_id = request.match_info["id"]
    if not store.get_item(item_id):
        return web.json_response({"error": "not found"}, status=404)
    # Status-only transition for a re-enrich — not a user edit, so don't touch updated_at.
    store.update_item(item_id, processing_status="queued", touch=False)
    try:
        request.app["state"].knowledge_ingest_queue().enqueue(item_id)
    except Exception:
        logger.debug("regen enqueue failed for %s", item_id, exc_info=True)
    _sel_log("item.generate_intelligence", item_id=item_id)
    return web.json_response(store.get_item(item_id))


async def regenerate_intelligence(request: web.Request) -> web.Response:
    """POST /api/knowledge/regenerate-intelligence -- re-run the full ingestion
    node-graph (extraction → insights → entities → intents → embed) over a batch of
    items by re-enqueueing them. Body/query: ``scope`` ('missing' (default) = items
    with no insights yet, or 'all'). Returns the count queued.
    """
    store = _store(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    scope = str(body.get("scope") or request.query.get("scope") or "missing").strip()

    # Archived items are excluded — batch re-enrichment shouldn't spend model calls on
    # content the user has put away (consistent with retrieval hiding archived items).
    where, params = [
        "status = 'active'",
        "COALESCE(is_archived, 0) = 0",
    ], []  # type: list[str], list[object]
    if scope == "missing":
        # Items whose intelligence never landed (empty/absent insights JSON).
        where.append("(insights IS NULL OR insights = '' OR insights = '{}')")
    where_clause = " AND ".join(where)
    rows = store.db.execute(
        f"SELECT id FROM items WHERE {where_clause}",  # noqa: S608
        params,
    ).fetchall()

    queue = request.app["state"].knowledge_ingest_queue()
    n = 0
    for r in rows:
        store.update_item(r["id"], processing_status="queued", touch=False)
        queue.enqueue(r["id"])
        n += 1
    store.db.commit()
    _sel_log("knowledge.regenerate_intelligence", scope=scope, queued=n)
    return web.json_response({"queued": n, "scope": scope})


def _hash_file(path) -> str:
    """SHA-256 of a file's bytes (streamed), or '' on error. Used to dedup uploads."""
    import hashlib

    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 16), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return ""


def _store_file_item(
    store, tmp_path: str, filename: str, mime: str | None = None
) -> tuple[dict | None, bool]:
    """Persist an uploaded file under the knowledge files dir as ONE logical-doc
    typed item (image/audio/video/pdf/document/sheet/slides) pointing at it (+ a
    thumbnail for images), queued for node-graph ingestion. One item = one file —
    document text extraction + chunking happen inside the graph/embedder, never as
    separate item rows. ``mime`` (the upload's content-type) disambiguates ambiguous
    extensions like .webm (a browser audio recording is audio/webm, not video)."""
    from personalclaw.knowledge import knowledge_files_dir
    from personalclaw.knowledge.media import code_language

    item_type = classify(filename, mime) or "image"

    # A source-code upload is a gist (code), stored as a text-backed item whose content
    # IS the code — read inline, language stamped for syntax highlighting + the
    # "Gist · <Language>" label, routed through the passthrough graph (no file on disk,
    # one logical doc). Dedup on the content hash, same as binary files.
    lang = code_language(filename)
    if item_type == "gist" and lang:
        try:
            code = Path(tmp_path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            code = ""
        content_hash = _hash_file(tmp_path)
        Path(tmp_path).unlink(missing_ok=True)
        if content_hash:
            existing = store.find_active_by_file_hash(content_hash)
            if existing:
                return existing, False
        new_id = store.create_typed_item(
            item_type="gist",
            title=filename,
            content=code,
            extra={
                "gist_language": lang,
                "file_metadata": {"content_hash": content_hash} if content_hash else {},
                "processing_status": "queued",
            },
        )
        return store.get_item(new_id), True
    # Pick a mime_type consistent with the resolved item_type: a .webm recording is
    # classified audio via its upload mime, but guess_mime(name) → video/webm; honor
    # the upload mime when its top-level matches the item_type so the stored mime (and
    # the metadata chip) say audio/webm, not video/webm.
    guessed = guess_mime(filename)
    mime_type = mime if (mime and mime.split("/", 1)[0].lower() == item_type) else guessed
    item_id = str(uuid4())
    files_dir = Path(knowledge_files_dir())
    ext = Path(filename).suffix.lower()
    dest = files_dir / f"{item_id}{ext}"
    shutil.move(tmp_path, dest)
    size = dest.stat().st_size

    # Content-hash dedup: re-uploading byte-identical content into the same space
    # returns the existing item instead of a duplicate (the file analog of bookmark
    # URL dedup). Hash is stored in file_metadata so the check is exact, not by name.
    content_hash = _hash_file(dest)
    if content_hash:
        existing = store.find_active_by_file_hash(content_hash)
        if existing:
            dest.unlink(missing_ok=True)  # drop the redundant copy we just saved
            return existing, False  # (item, is_new) — dedup hit

    thumb_path = ""
    if item_type == "image":
        thumb = files_dir / f"{item_id}.thumb.webp"
        if make_image_thumbnail(str(dest), str(thumb)):
            thumb_path = str(thumb)

    new_id = store.create_typed_item(
        item_type=item_type,
        title=filename,
        content="",
        extra={
            "file_path": str(dest),
            "mime_type": mime_type,
            "file_size": size,
            "thumbnail_path": thumb_path,
            # original_filename lets enrichment tell a filename-seeded title (fair game
            # for AI-title promotion) from a user-authored one (never clobbered).
            "file_metadata": {
                **({"content_hash": content_hash} if content_hash else {}),
                "original_filename": filename,
            },
            # Queue for node-graph ingestion (Image/Audio/Video graph): exif, OCR,
            # vision, transcription, … The caller enqueues after this returns.
            "processing_status": "queued",
        },
    )
    return store.get_item(new_id), True  # (item, is_new)


def _serve_item_path(store, item_id: str, *, thumbnail: bool) -> tuple[Path | None, str]:
    """Resolve + guard the on-disk path for an item's file or thumbnail. Returns
    (path, mime) or (None, '') when missing/out-of-bounds."""
    from personalclaw.knowledge import knowledge_files_dir

    item = store.get_item(item_id)
    if not item:
        return None, ""
    raw = item.get("thumbnail_path") if thumbnail else item.get("file_path")
    if not raw:
        return None, ""
    files_root = Path(knowledge_files_dir()).resolve()
    resolved = Path(raw).resolve()
    # Path-safety: only ever serve from inside the knowledge files dir.
    if not resolved.is_relative_to(files_root) or not resolved.is_file():
        return None, ""
    # Serve the canonical web MIME from the file extension rather than a possibly
    # legacy stored value (older items stored audio/x-wav, audio/mp4a-latm, … which
    # browsers won't play inline). guess_mime normalizes these. Exception: an extension
    # whose top-level kind is ambiguous (.webm = audio OR video) — trust the item's
    # stored mime_type when it pins a different top-level (a recorded audio/webm), so
    # the <audio> element gets an audio/* source instead of video/webm.
    if thumbnail:
        return resolved, "image/webp"
    mime = guess_mime(resolved.name)
    stored = (item.get("mime_type") or "").strip()
    if (
        stored
        and stored.split("/", 1)[0] in ("audio", "video")
        and stored.split("/", 1)[0] != mime.split("/", 1)[0]
    ):
        mime = stored
    return resolved, mime


async def get_item_file(request: web.Request) -> web.StreamResponse:
    """GET /api/knowledge/items/{id}/file -- serve a media item's original bytes."""
    path, mime = _serve_item_path(_store(request), request.match_info["id"], thumbnail=False)
    if path is None:
        return web.json_response({"error": "not found"}, status=404)
    return web.FileResponse(path, headers={"Content-Type": mime})


async def get_item_thumbnail(request: web.Request) -> web.StreamResponse:
    """GET /api/knowledge/items/{id}/thumbnail -- serve a generated thumbnail (image/webp)."""
    path, mime = _serve_item_path(_store(request), request.match_info["id"], thumbnail=True)
    if path is None:
        return web.json_response({"error": "not found"}, status=404)
    return web.FileResponse(path, headers={"Content-Type": mime})


async def list_providers(request: web.Request) -> web.Response:
    """GET /api/knowledge/providers -- registered knowledge providers (native
    always-on + any external). Mirrors the inbox source S4 pattern."""
    from personalclaw.knowledge_providers.registry import list_provider_info

    return web.json_response({"providers": list_provider_info()})


async def list_source_recipes(request: web.Request) -> web.Response:
    """GET /api/knowledge/source-recipes -- the bundled source-recipe directory.

    With ``?url=`` it answers the create flow's first question — "is this site already
    covered?" — returning each matching recipe with its spec ALREADY resolved from the URL's
    capture groups, so the caller saves what it was shown rather than re-deriving it. Without
    a URL it returns the whole directory for browsing (WATCHED-SOURCES §7.2).
    """
    from personalclaw.knowledge.source_recipes import list_recipes, recipes_for_url

    url = (request.query.get("url") or "").strip()
    payload: dict[str, object] = {"recipes": [r.to_dict() for r in list_recipes()]}
    if url:
        payload["url"] = url
        payload["matches"] = [m.to_dict() for m in recipes_for_url(url)]
    return web.json_response(payload)


async def get_item(request: web.Request) -> web.Response:
    """GET /api/knowledge/items/{id} -- single item with its entities + relations."""
    store = _store(request)
    item_id = request.match_info["id"]
    item = store.get_item(item_id)
    if not item:
        return web.json_response({"error": "not found"}, status=404)

    mentions = store.db.execute(
        "SELECT entity_id, context FROM mentions WHERE item_id = ?", (item_id,)
    ).fetchall()
    entity_ids = [m["entity_id"] for m in mentions]
    entities = []
    for eid in entity_ids:
        row = store.db.execute("SELECT * FROM entities WHERE id = ?", (eid,)).fetchone()
        if row:
            # _serialize_entity parses aliases → array (not the raw JSON string) and
            # redacts the LLM-derived description, matching the /entities endpoint.
            entities.append(_serialize_entity(row))

    relations = []
    seen_ids = set()
    for eid in entity_ids:
        for row in store.db.execute(
            "SELECT * FROM entity_relations WHERE source_id = ? OR target_id = ?", (eid, eid)
        ):
            r = dict(row)
            if r["id"] not in seen_ids:
                seen_ids.add(r["id"])
                # Resolve entity names for display
                src = store.db.execute(
                    "SELECT name FROM entities WHERE id = ?", (r["source_id"],)
                ).fetchone()
                tgt = store.db.execute(
                    "SELECT name FROM entities WHERE id = ?", (r["target_id"],)
                ).fetchone()
                r["source_name"] = src["name"] if src else r["source_id"]
                r["target_name"] = tgt["name"] if tgt else r["target_id"]
                # A relation's description is LLM-derived — scrub credentials/exfil URLs.
                r["description"] = _redact(r.get("description"))
                relations.append(r)

    return web.json_response({**item, "entities": entities, "relations": relations})


async def update_item(request: web.Request) -> web.Response:
    """PATCH /api/knowledge/items/{id} -- update fields."""
    store = _store(request)
    item_id = request.match_info["id"]
    existing = store.get_item(item_id)
    if not existing:
        return web.json_response({"error": "not found"}, status=404)
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)
    allowed = {
        "tags",
        "item_type",
        "status",
        "title",
        "summary",
        "content",
        "url",
        "url_title",
        "url_description",
        "is_pinned",
        "is_archived",
        "gist_language",
    }
    fields = {k: v for k, v in body.items() if k in allowed}
    # A url edit must stay an http(s) web address — same guard as create, so an edit
    # can't smuggle in a javascript:/data:/file: URL (XSS vector / unscrapeable) that
    # create rejects.
    if "url" in fields and str(fields["url"]).strip():
        from urllib.parse import urlsplit

        try:
            scheme = urlsplit(str(fields["url"]).strip()).scheme.lower()
        except ValueError:
            scheme = ""
        if scheme not in ("http", "https"):
            return web.json_response({"error": "url must be an http(s) web address"}, status=400)
    # Journal immutability (knowledge-entity vision): a journal is an append-only
    # record — its body can be edited on its creation day, but not after. Reject a
    # content/title edit to a journal whose creation day has passed. Pin/archive/tags
    # (curation metadata, not the record itself) stay editable.
    if (existing.get("item_type") or existing.get("type")) == "journal" and (
        "content" in fields or "title" in fields
    ):
        created = str(existing.get("created_at") or "")[:10]
        from datetime import datetime

        today = datetime.now().isoformat()[:10]
        if created and created != today:
            return web.json_response(
                {"error": "this journal entry is immutable — its creation day has passed"},
                status=403,
            )
    # The API exposes the type discriminator as `type`; map to the storage column.
    if "type" in body and "item_type" not in fields:
        fields["item_type"] = body["type"]
    # Guard against incoherent type changes: a file/media type needs file bytes, a
    # bookmark needs a url. Don't let an item become a media/document/bookmark type it
    # can't satisfy (would render a broken card/preview).
    if "item_type" in fields:
        new_type = str(fields["item_type"])
        needs_file = new_type in ("image", "audio", "video", "pdf", "document", "sheet", "slides")
        if needs_file and not existing.get("file_path"):
            return web.json_response(
                {"error": f"cannot change to '{new_type}': that type requires an uploaded file"},
                status=400,
            )
        if new_type == "bookmark" and not (existing.get("url") or fields.get("url")):
            return web.json_response(
                {"error": "cannot change to 'bookmark': that type requires a url"},
                status=400,
            )
    # Booleans persist as 0/1.
    for b in ("is_pinned", "is_archived"):
        if b in fields:
            fields[b] = 1 if fields[b] else 0
    if not fields:
        return web.json_response({"error": "no valid fields"}, status=400)
    store.update_item(item_id, **fields)
    # Editing the text/url re-runs the ingestion node-graph so insights, entities, the
    # embedding, and intent outcomes stay consistent with the new content — matching
    # the agent knowledge_update tool and the create→enrich contract. Curation-only
    # edits (tags/pin/archive/title) don't need re-extraction. The client may opt OUT
    # via reingest=false (e.g. a quick content typo-fix that shouldn't burn a model
    # pass); default is to re-enrich on a content/url change.
    reingest_requested = body.get("reingest", True) is not False
    reenrich = reingest_requested and ("content" in fields or "url" in fields)
    if reenrich:
        # The user edit above already touched updated_at; this is just the status flip.
        store.update_item(item_id, processing_status="queued", touch=False)
        try:
            request.app["state"].knowledge_ingest_queue().enqueue(item_id)
        except Exception:
            logger.debug("re-enrich enqueue failed for %s", item_id, exc_info=True)
    _sel_log("item.update", item_id=item_id, fields=list(fields))
    return web.json_response({"ok": True, "reenriching": reenrich})


async def delete_item(request: web.Request) -> web.Response:
    """DELETE /api/knowledge/items/{id}."""
    store = _store(request)
    item_id = request.match_info["id"]
    item = store.get_item(item_id)
    if not item:
        return web.json_response({"error": "not found"}, status=404)
    store.delete_item(item_id)
    # Clean up every on-disk file this item owned — but only inside the knowledge files
    # dir (defense-in-depth, matching the serve guard) so a corrupt path can never unlink
    # something outside it. Two sources: (1) the tracked source + thumbnail columns, and
    # (2) DERIVED media-pipeline artifacts, which the av_split/frame_extract nodes write
    # as "<item_id>.audio.wav" / "<item_id>.frame_NNN.jpg" / "<item_id>.dense*" straight
    # into the files dir and are tracked in NO column — so a plain file_path unlink leaked
    # a video's frames + split audio on every delete. Sweep by the "<item_id>." prefix.
    from personalclaw.knowledge import knowledge_files_dir

    files_root = Path(knowledge_files_dir()).resolve()
    victims = [item.get("file_path"), item.get("thumbnail_path")]
    try:
        victims += [str(p) for p in files_root.glob(f"{item_id}.*")]
    except OSError:
        logger.debug("derived-artifact scan failed for %s", item_id, exc_info=True)
    for raw in victims:
        if not raw:
            continue
        try:
            resolved = Path(raw).resolve()
            if resolved.is_relative_to(files_root) and resolved.is_file():
                resolved.unlink(missing_ok=True)
        except (OSError, ValueError):
            logger.debug("delete cleanup skipped for %s", raw, exc_info=True)
    _sel_log("item.delete", item_id=item_id)
    return web.json_response({"ok": True})


async def get_item_content(request: web.Request) -> web.Response:
    """GET /api/knowledge/items/{id}/content -- plain text for clipboard."""
    store = _store(request)
    item = store.get_item(request.match_info["id"])
    if not item:
        return web.Response(text="not found", status=404)
    return web.Response(text=item["content"], content_type="text/plain")


# ---------- Entities ----------


async def list_entities(request: web.Request) -> web.Response:
    """GET /api/knowledge/entities."""
    store = _store(request)
    etype = request.query.get("type")
    q = request.query.get("q")
    try:
        limit = min(500, max(1, int(request.query.get("limit", 100) or 100)))
    except ValueError:
        return web.json_response({"error": "invalid limit"}, status=400)

    where, params = ["1=1"], []  # type: list[str], list[object]
    if etype:
        where.append("entity_type = ?")
        params.append(etype)
    if q:
        where.append("name LIKE ?")
        params.append(f"%{q}%")
    params.append(limit)
    rows = store.db.execute(
        f"SELECT * FROM entities WHERE {' AND '.join(where)} ORDER BY name LIMIT ?", params
    ).fetchall()  # noqa: S608
    return web.json_response([_serialize_entity(r) for r in rows])


async def get_entity_graph(request: web.Request) -> web.Response:
    """GET /api/knowledge/entities/{id}/graph -- D3-compatible subgraph."""
    store = _store(request)
    entity_id = request.match_info["id"]
    try:
        depth = min(5, max(1, int(request.query.get("depth", 2) or 2)))
    except ValueError:
        return web.json_response({"error": "invalid depth"}, status=400)
    if not store.graph.has_node(entity_id):
        return web.json_response({"error": "entity not found"}, status=404)
    return web.json_response(store.get_entity_subgraph(entity_id, depth))


async def get_entity_related(request: web.Request) -> web.Response:
    """GET /api/knowledge/entities/by-name/{name}/related -- entities directly connected
    to this one in the graph, with the relation type + direction. Powers the entity
    sidebar's 'Connected to' section."""
    store = _store(request)
    name = request.match_info["name"]
    ent = store.find_entity(name)
    if not ent:
        return web.json_response({"related": []})
    eid = ent["id"]
    out = []
    seen: set = set()
    for row in store.db.execute(
        "SELECT * FROM entity_relations WHERE source_id = ? OR target_id = ?", (eid, eid)
    ):
        d = dict(row)
        other_id = d["target_id"] if d["source_id"] == eid else d["source_id"]
        outgoing = d["source_id"] == eid
        other = store.db.execute(
            "SELECT name, entity_type FROM entities WHERE id = ?", (other_id,)
        ).fetchone()
        if not other:
            continue
        key = (other_id, d.get("relation_type"), outgoing)
        if key in seen:
            continue
        seen.add(key)
        out.append(
            {
                "name": other["name"],
                "entity_type": other["entity_type"],
                "relation_type": d.get("relation_type") or "related",
                "outgoing": outgoing,
            }
        )
    return web.json_response({"related": out})


async def get_entity_items(request: web.Request) -> web.Response:
    """GET /api/knowledge/entities/by-name/{name}/items -- items that MENTION the entity.

    Sourced from the mentions table (the entity graph's own item↔entity links), not an
    FTS5 text match — so it stays consistent with the graph + related-items, and avoids
    both false positives (items that merely contain the word) and misses (an item whose
    text used a variant/alias the extractor canonicalized)."""
    store = _store(request)
    name = request.match_info["name"]
    ent = store.find_entity(name)
    if not ent:
        return web.json_response([])
    rows = store.db.execute(
        "SELECT i.* FROM items i JOIN mentions m ON i.id = m.item_id "
        "WHERE m.entity_id = ? AND i.status = 'active' AND COALESCE(i.is_archived, 0) = 0 "
        "ORDER BY i.updated_at DESC LIMIT 50",
        (ent["id"],),
    ).fetchall()
    return web.json_response([store._serialize_item(r) for r in rows])


async def get_item_duplicates(request: web.Request) -> web.Response:
    """GET /api/knowledge/items/{id}/duplicates — near-duplicates, best match first.

    Surfacing only. Merging is a separate POST, because a merge DELETES one of the two and
    that must be a deliberate act, never a side effect of looking.
    """
    store = _store(request)
    item_id = request.match_info["id"]
    if store.get_item(item_id) is None:
        return web.json_response({"error": "not found"}, status=404)
    try:
        limit = min(50, max(1, int(request.query.get("limit", 25) or 25)))
    except ValueError:
        return web.json_response({"error": "invalid limit"}, status=400)
    return web.json_response({"duplicates": store.find_duplicates(item_id, limit=limit)})


async def merge_items(request: web.Request) -> web.Response:
    """POST /api/knowledge/items/{id}/merge — fold another item into this one.

    ``{id}`` is the SURVIVOR and the body names the loser, so the destructive half is never
    the path parameter a client might reuse from a list view by mistake. ``confirm: true`` is
    required: this deletes an item, and an accidental double-post shouldn't.
    """
    store = _store(request)
    keep_id = request.match_info["id"]
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "body must be an object"}, status=400)
    merge_id = str(body.get("merge_id") or "").strip()
    if not merge_id:
        return web.json_response({"error": "merge_id is required"}, status=400)
    if not body.get("confirm"):
        return web.json_response(
            {"error": "merging deletes an item — pass confirm: true"}, status=400
        )
    try:
        moved = store.merge_items(keep_id, merge_id)
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    try:
        sel().log_tool_invocation(
            session_key="dashboard:knowledge",
            tool_name="knowledge_merge_items",
            outcome="success",
            request_id=keep_id,
            source="dashboard",
        )
    except Exception:
        logger.warning("SEL audit failed for knowledge merge", exc_info=True)
    return web.json_response({"ok": True, "kept": keep_id, "merged": merge_id, "moved": moved})


async def list_item_annotations(request: web.Request) -> web.Response:
    """GET /api/knowledge/items/{id}/annotations — the item's reading highlights."""
    store = _store(request)
    item_id = request.match_info["id"]
    if store.get_item(item_id) is None:
        return web.json_response({"error": "not found"}, status=404)
    return web.json_response({"annotations": store.list_annotations(item_id)})


async def add_item_annotation(request: web.Request) -> web.Response:
    """POST /api/knowledge/items/{id}/annotations — keep a highlighted passage.

    `occurrence` disambiguates identical quotes within one document; the reader supplies
    it because only the reader knows which rendered instance the user selected.
    """
    store = _store(request)
    item_id = request.match_info["id"]
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "body must be an object"}, status=400)
    try:
        occurrence = int(body.get("occurrence", 0) or 0)
    except (TypeError, ValueError):
        return web.json_response({"error": "occurrence must be an integer"}, status=400)
    try:
        row = store.add_annotation(
            item_id,
            str(body.get("quote") or ""),
            occurrence=occurrence,
            note=str(body.get("note") or ""),
        )
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    if row is None:
        return web.json_response({"error": "item not found"}, status=404)
    return web.json_response({"ok": True, "annotation": row})


async def delete_item_annotation(request: web.Request) -> web.Response:
    """DELETE /api/knowledge/annotations/{id} — drop one highlight.

    Keyed by the annotation's own id rather than nested under the item: a highlight is
    identified globally, and repeating the item id would let a client delete row A while
    naming item B.
    """
    if not _store(request).delete_annotation(request.match_info["id"]):
        return web.json_response({"error": "not found"}, status=404)
    return web.json_response({"ok": True})


# How far past `limit` to ask the similarity pass for. Neighbours are scored over
# content, but archived/non-active items are excluded HERE (the edge table carries no
# lifecycle column), so filtering happens after scoring. Without an over-fetch a single
# archived neighbour would silently shorten a list the caller asked to be `limit` long.
_RELATED_OVERFETCH = 3


def _shared_entity_counts(store, item_id: str, others: list[str]) -> dict[str, int]:
    """How many distinct entities each of ``others`` shares with ``item_id``.

    One query for the whole result set, not one per neighbour. This no longer *ranks*
    anything -- KL-13 ranks by similarity score -- it annotates an already-chosen
    neighbour so a reader can be told why two items sit next to each other in entity
    terms. Neighbours sharing nothing are absent from the result and read as 0.
    """
    if not others:
        return {}
    placeholders = ",".join("?" * len(others))
    return {
        str(r["other"]): int(r["shared"])
        for r in store.db.execute(
            "SELECT m2.item_id AS other, COUNT(DISTINCT m2.entity_id) AS shared "  # noqa: S608
            "FROM mentions m1 JOIN mentions m2 ON m1.entity_id = m2.entity_id "
            f"WHERE m1.item_id = ? AND m2.item_id IN ({placeholders}) GROUP BY m2.item_id",
            [item_id, *others],
        ).fetchall()
    }


async def get_related_items(request: web.Request) -> web.Response:
    """GET /api/knowledge/items/{id}/related -- nearest neighbours by embedding similarity.

    KL-13 serves this from the precomputed similarity-edge table behind a real score floor
    (``knowledge.similarity_min_score``), replacing the unthresholded
    ``COUNT(DISTINCT entity_id)`` overlap it ranked by before. That count had no floor at
    all, so one incidentally shared entity -- a common tag, a person named once in passing
    -- ranked level with a genuine topical neighbour, and the endpoint returned *something*
    for very nearly any item. "Related" therefore carried no information.

    The honest consequence of a real floor: an item whose nearest neighbour scores below it
    now returns ``[]`` where it used to return weak matches. That empty answer is correct,
    and it is deliberately distinguishable from an unknown item, which is a 404 -- with a
    floor in place ``[]`` is common, so conflating "nothing is close enough" with "no such
    item" would be least legible exactly where it matters most.

    ``shared_entities`` survives in the payload but has changed job: it is descriptive
    (explaining a neighbour the score already chose) rather than the ranking key. The
    Related chip in ``web/src/pages/knowledge/KnowledgeDetailPage.tsx`` renders it, so
    dropping the key would have silently emptied a live surface.

    ``chunk_index``/``neighbour_chunk_index`` carry the store's provenance through
    unrenamed. They are oriented to the item asked about -- ``chunk_index`` always names a
    chunk of *this* item -- because the edge table stores one row per unordered pair under
    a canonical (min, max) ordering, so "source" and "target" are storage legs and not
    roles. Renaming them ``source_``/``target_`` here would re-import exactly the confusion
    that orientation exists to remove.
    """
    from personalclaw.config.loader import AppConfig

    store = _store(request)
    item_id = request.match_info["id"]
    # Read defensively: these two fields land with a sibling KL-13 change, and the
    # fallbacks are the contract's own defaults rather than a guess.
    knowledge_cfg = getattr(AppConfig.load(), "knowledge", None)
    top_k = int(getattr(knowledge_cfg, "similarity_top_k", 8) or 8)
    min_score = float(getattr(knowledge_cfg, "similarity_min_score", 0.55))
    try:
        limit = min(20, max(1, int(request.query.get("limit", top_k) or top_k)))
    except ValueError:
        return web.json_response({"error": "invalid limit"}, status=400)

    if store.db.execute("SELECT 1 FROM items WHERE id = ?", (item_id,)).fetchone() is None:
        return web.json_response({"error": "item not found"}, status=404)

    # `similar_items` reads both legs and normalises each row to this item's point of view,
    # so one call returns every neighbour regardless of which side of the edge it is stored
    # on. `item_id` in each row is the NEIGHBOUR's id, not the one asked about.
    edges: dict[str, dict] = {}
    for edge in store.similar_items(item_id, limit=limit * _RELATED_OVERFETCH, min_score=min_score):
        other = str(edge.get("item_id") or "")
        if other and other != item_id and other not in edges:
            edges[other] = edge
    if not edges:
        return web.json_response([])

    # Lifecycle filtering is the handler's, not the edge table's: the pass scores content,
    # while archiving is a later and reversible act on the item.
    placeholders = ",".join("?" * len(edges))
    rows = {
        str(r["id"]): r
        for r in store.db.execute(
            f"SELECT * FROM items WHERE id IN ({placeholders}) "  # noqa: S608
            "AND status = 'active' AND COALESCE(is_archived, 0) = 0",
            list(edges),
        ).fetchall()
    }
    if not rows:
        return web.json_response([])

    overlap = _shared_entity_counts(store, item_id, list(rows))
    # Stable sort on descending score: ties keep the store's own ordering.
    ordered = sorted(
        (e for oid, e in edges.items() if oid in rows),
        key=lambda e: -float(e.get("score") or 0.0),
    )
    return web.json_response(
        [
            {
                **store._serialize_item(rows[str(edge["item_id"])]),
                "score": round(float(edge.get("score") or 0.0), 6),
                "chunk_index": edge.get("chunk_index"),
                "neighbour_chunk_index": edge.get("neighbour_chunk_index"),
                "shared_entities": overlap.get(str(edge["item_id"]), 0),
            }
            for edge in ordered[:limit]
        ]
    )


# ---------- Full graph payload (KL-17) ----------
#
# The graph payload keeps EVERY entity and thins EDGES. The retired shape sorted nodes by
# degree and sliced the top `limit` (default 100, hard cap 200): a library with 3,000
# entities rendered 200 of them and the other 2,800 were not "collapsed" or "hidden", they
# were absent from the response with nothing in it saying so. Thinning edges bounds the
# payload without losing a single node.
#
# Two controls, both live query params:
#   * a WEIGHT FLOOR  -- drop relations weaker than `min_weight`
#   * a TOP-K-PER-NODE keep -- each node keeps its K strongest incident edges, then the
#     keeps are UNIONED. Deliberately not a global top-K: a handful of hub entities would
#     eat the entire global budget and every peripheral node would ship edgeless, which is
#     the node cap again wearing a different hat.
#
# 🔴 MEASURED, and the reason `min_weight` defaults to 0.0: the only production writer of
# `entity_relations` (`knowledge/pipeline/runner.py:619`) never passes `weight`, so every
# relation in a real library carries the column default 1.0. With one distinct weight in
# the data ANY floor is either a no-op (<= 1.0) or total data loss (> 1.0) -- there is no
# default that makes the floor bite without emptying the graph. So it ships at 0.0: live,
# tested against graded weights, and honestly not binding until some writer grades them.
# Today's real thinning is done entirely by the top-K keep.
_GRAPH_MIN_WEIGHT = 0.0
_GRAPH_TOP_K_PER_NODE = 6
#: Smallest connected component that earns a cluster id + a label. Below this, "cluster"
#: is noise -- a two-entity component labelled from one item's dominant tag says nothing.
_GRAPH_CLUSTER_MIN_SIZE = 3
#: The invalidation DEBOUNCE. The memo notices a content change immediately but refuses to
#: recompute more often than this, so importing 500 items costs one reprojection, not 500.
#: The interval is the whole mechanism: without it a "cache" keyed on content is just a
#: recompute-every-write with extra steps.
_GRAPH_MEMO_DEBOUNCE_SECS = 30.0
#: (db_path, min_weight, top_k) -> {"signature", "payload", "computed_at"}. The db path is
#: part of the key on purpose: a process-global cache keyed on content alone would serve
#: one entity's graph to another home, and would leak between tests.
_graph_memo: dict[tuple, dict] = {}


def _load_project_2d():
    """Resolve the 2-D projection primitive.

    Imported through this one seam rather than at module scope so the projection module is
    a runtime dependency of this endpoint alone, and so a test can substitute a spy to
    prove the memo really is not recomputing.
    """
    from personalclaw.knowledge.projection import project_2d

    return project_2d


def _graph_signature(store) -> tuple:
    """A content digest of everything the payload is derived from, in ONE query.

    This is what the memo is keyed on -- deliberately not a wall-clock timestamp (which
    never hits) and not the entity count alone (which serves a stale layout forever after
    an in-place edit). Covers: the nodes, the edges and their weights, the item<->entity
    mentions that place the nodes, the tags that label the clusters, and the embeddings
    themselves. `MAX(items.updated_at)` catches an edit that changes no count.
    """
    row = store.db.execute(
        "SELECT (SELECT COUNT(*) FROM entities) AS e, "
        "(SELECT COUNT(*) FROM entity_relations) AS r, "
        "(SELECT COALESCE(SUM(weight), 0) FROM entity_relations) AS rw, "
        "(SELECT COUNT(*) FROM mentions) AS m, "
        "(SELECT COUNT(*) FROM item_tags) AS t, "
        "(SELECT COUNT(*) FROM items WHERE embedding IS NOT NULL) AS v, "
        "(SELECT COALESCE(SUM(LENGTH(embedding)), 0) FROM items WHERE embedding IS NOT NULL) "
        "AS vb, "
        "(SELECT COALESCE(MAX(updated_at), '') FROM items) AS ts"
    ).fetchone()
    return tuple(row)


def _thin_edges(graph, *, min_weight: float, top_k: int) -> tuple[list[dict], int]:
    """Thin the edge set by a weight floor plus a top-K-per-node keep.

    Returns the kept edges and how many there were before thinning, so the payload can
    say "340 of 1,200" instead of silently under-drawing.

    Order-independent by construction: the keep is a SET union of per-node selections and
    the result is sorted on (-weight, source, target, type), so the answer does not depend
    on the order the graph happens to enumerate its edges in.
    """
    raw: list[tuple[float, str, str, str]] = []
    for u, v, d in graph.edges(data=True):
        w = d.get("weight")
        # None -> 1.0 mirrors the `weight REAL DEFAULT 1.0` column default; treating it as
        # 0.0 instead would let any positive floor silently delete an ungraded graph.
        raw.append((1.0 if w is None else float(w), u, v, d.get("relation_type") or ""))
    incident: dict[str, list[tuple[float, str, str, str]]] = defaultdict(list)
    for edge in raw:
        if edge[0] >= min_weight:
            incident[edge[1]].append(edge)
            incident[edge[2]].append(edge)
    keep: set[tuple[float, str, str, str]] = set()
    for edges in incident.values():
        edges.sort(key=lambda e: (-e[0], e[1], e[2], e[3]))
        keep.update(edges[:top_k])
    ordered = sorted(keep, key=lambda e: (-e[0], e[1], e[2], e[3]))
    kept = [{"source": u, "target": v, "type": t or None, "weight": w} for w, u, v, t in ordered]
    return kept, len(raw)


def _entity_positions(
    store, entity_ids: list[str]
) -> tuple[dict[str, tuple[float, float]], set[str]]:
    """Position every entity at the CENTROID of the projected items that mention it.

    KL-17 clause 1 says "a 2-D projection of item embeddings" while the graph's nodes are
    entities, and entities carry no embedding of their own. The reading under which both
    are true: entity nodes stay the nodes, and each is placed by projecting the embeddings
    of the ITEMS THAT MENTION IT. An entity mentioned by several items sits at their
    centroid; an entity with no usable item vector goes to the origin, matching the
    projection's own unplaceable rule.

    Returns the ids that were genuinely placed alongside the positions, because the origin
    is NOT a reliable test for it. Measured against the real projection: an entity
    mentioned by one item in each of two opposed clusters has a centroid of (0, 0) -- so a
    canvas labelling "at the origin" as "no embedding yet" would mislabel it. The set is
    what the per-node `placed` flag is derived from.

    Three queries, none of them per-entity. The N+1 this avoids is the obvious shape --
    "for each entity, select its mentions" -- which over a library of thousands of
    entities is thousands of round trips for data two scans already hold.

    Returns a position for EVERY requested id, plus the subset genuinely placed.
    """
    wanted = set(entity_ids)
    vectors: dict[str, list[float]] = {}
    dims: Counter = Counter()
    for row in store.db.execute(
        "SELECT id, embedding FROM items WHERE status = 'active' AND embedding IS NOT NULL"
    ):
        vec = _bytes_to_floats(row["embedding"])
        if vec:
            vectors[row["id"]] = vec
            dims[len(vec)] += 1
    if dims:
        # Keep only the DOMINANT dimension -- items embedded under a previous model are
        # vector-dead. The projection also resolves a basis dimension and returns the
        # origin for anything off it, so this is NOT the same filter twice: dropping them
        # HERE keeps an unplaceable sentinel out of a centroid AVERAGE. An entity
        # mentioned by one live item and one stale one must sit on the live item, not
        # half-way between it and the middle of the canvas. Ties break to the larger
        # dimension so the choice is deterministic rather than dict-order.
        active_dim = max(dims.items(), key=lambda kv: (kv[1], kv[0]))[0]
        vectors = {k: v for k, v in vectors.items() if len(v) == active_dim}

    by_entity: dict[str, list[str]] = defaultdict(list)
    for row in store.db.execute("SELECT entity_id, item_id FROM mentions"):
        if row["entity_id"] in wanted and row["item_id"] in vectors:
            by_entity[row["entity_id"]].append(row["item_id"])

    # Project only the items that actually place a node. The projection is a fit over its
    # whole input, so feeding it items no entity mentions would move every node for no
    # reason -- and it is the expensive step.
    used = sorted({iid for ids in by_entity.values() for iid in ids})
    points: dict[str, tuple[float, float]] = (
        _load_project_2d()({iid: vectors[iid] for iid in used}) if used else {}
    )

    positions: dict[str, tuple[float, float]] = {}
    placed: set[str] = set()
    for eid in entity_ids:
        pts = [points[i] for i in sorted(by_entity.get(eid, [])) if i in points]
        if not pts:
            positions[eid] = (0.0, 0.0)
            continue
        placed.add(eid)
        positions[eid] = (
            sum(p[0] for p in pts) / len(pts),
            sum(p[1] for p in pts) / len(pts),
        )
    return positions, placed


def _graph_components(node_ids: list[str], edges: list[dict]) -> list[list[str]]:
    """Connected components of the THINNED, direction-ignoring edge set.

    "Cluster" is defined from what the payload actually contains rather than from a
    clustering the store does not compute: two entities are in one cluster when the
    shipped edges connect them. Defining it over the unthinned graph would label groups
    the user cannot see the connection between.

    Deterministic: nodes are visited in sorted order and components come back sorted by
    (size desc, first id), so cluster ids are stable across calls.
    """
    adj: dict[str, set[str]] = defaultdict(set)
    for e in edges:
        adj[e["source"]].add(e["target"])
        adj[e["target"]].add(e["source"])
    seen: set[str] = set()
    comps: list[list[str]] = []
    for nid in sorted(node_ids):
        if nid in seen:
            continue
        comp: set[str] = set()
        stack = [nid]
        while stack:
            cur = stack.pop()
            if cur in comp:
                continue
            comp.add(cur)
            seen.add(cur)
            stack.extend(adj.get(cur, set()) - comp)
        comps.append(sorted(comp))
    comps.sort(key=lambda c: (-len(c), c[0]))
    return comps


def _cluster_labels(store, graph, comps: list[list[str]]) -> tuple[list[dict], dict[str, int]]:
    """Label each big-enough cluster from the dominant tag of its entities' items.

    One query for the whole graph's entity -> tag names, joined through `mentions`; the
    per-cluster tally is then done in memory. Ties break on the tag name ascending -- an
    unstable label is the same defect as an unstable layout, so every fallback below is
    total and deterministic:

        dominant tag -> dominant entity_type -> first entity name -> first entity id

    `label_source` reports which rung was used, so "labelled from their dominant tags" is
    an assertion a test can make rather than a claim the docstring makes.
    """
    tags_by_entity: dict[str, list[str]] = defaultdict(list)
    for row in store.db.execute(
        "SELECT m.entity_id AS entity_id, t.name AS name FROM mentions m "
        "JOIN item_tags it ON it.item_id = m.item_id "
        "JOIN tags t ON t.id = it.tag_id"
    ):
        tags_by_entity[row["entity_id"]].append(row["name"])

    clusters: list[dict] = []
    membership: dict[str, int] = {}
    for cid, comp in enumerate(c for c in comps if len(c) >= _GRAPH_CLUSTER_MIN_SIZE):
        tally: Counter = Counter()
        for eid in comp:
            tally.update(tags_by_entity.get(eid, []))
        if tally:
            label = min(tally.items(), key=lambda kv: (-kv[1], kv[0]))[0]
            source = "tag"
        else:
            types: Counter = Counter(
                t for t in (graph.nodes[e].get("entity_type") for e in comp) if t
            )
            if types:
                label = min(types.items(), key=lambda kv: (-kv[1], kv[0]))[0]
                source = "entity_type"
            else:
                names = sorted(n for n in (graph.nodes[e].get("name") for e in comp) if n)
                label, source = (names[0], "entity_name") if names else (comp[0], "entity_id")
        clusters.append({"id": cid, "label": label, "label_source": source, "size": len(comp)})
        for eid in comp:
            membership[eid] = cid
    return clusters, membership


def _graph_payload_shell(min_weight: float, top_k: int, **over: object) -> dict:
    """The response shape, in ONE place, so the empty-graph answer is the same shape as a
    populated one. A consumer that has to branch on which keys exist is a consumer that
    will crash on the empty library."""
    shell: dict = {
        "nodes": [],
        "edges": [],
        "clusters": [],
        "thinning": {
            "min_weight": min_weight,
            "top_k": top_k,
            "edges_total": 0,
            "edges_kept": 0,
        },
        "layout": {
            "placed": 0,
            "unplaceable": 0,
            "cluster_min_size": _GRAPH_CLUSTER_MIN_SIZE,
        },
    }
    shell.update(over)
    return shell


def _build_graph_payload(store, *, min_weight: float, top_k: int) -> dict:
    """Compose the whole payload: every node, thinned edges, positions, cluster labels."""
    node_ids = sorted(store.graph.nodes)
    edges, edges_total = _thin_edges(store.graph, min_weight=min_weight, top_k=top_k)
    positions, placed = _entity_positions(store, node_ids)
    comps = _graph_components(node_ids, edges)
    clusters, membership = _cluster_labels(store, store.graph, comps)
    nodes = [
        {
            "id": n,
            "name": store.graph.nodes[n].get("name"),
            "type": store.graph.nodes[n].get("entity_type"),
            "x": positions[n][0],
            "y": positions[n][1],
            # Whether (x, y) is a real position or the unplaceable sentinel. Carried as a
            # flag because the coordinates cannot answer it: a centroid over two opposed
            # clusters is legitimately (0, 0), so "at the origin" and "not placed" are
            # different questions and a canvas must not infer one from the other.
            "placed": n in placed,
            # Degree in the FULL graph, not the thinned one: it is what the node's
            # importance actually is, and a node drawn with 6 edges but a degree of 40 is
            # a hub whose relations were thinned -- worth being able to say.
            "degree": store.graph.degree(n),
            "cluster": membership.get(n),
        }
        for n in node_ids
    ]
    return _graph_payload_shell(
        min_weight,
        top_k,
        nodes=nodes,
        edges=edges,
        clusters=clusters,
        thinning={
            "min_weight": min_weight,
            "top_k": top_k,
            "edges_total": edges_total,
            "edges_kept": len(edges),
        },
        layout={
            "placed": len(placed),
            "unplaceable": len(node_ids) - len(placed),
            "cluster_min_size": _GRAPH_CLUSTER_MIN_SIZE,
        },
    )


async def get_full_graph(request: web.Request) -> web.Response:
    """GET /api/knowledge/graph -- the whole entity graph, positioned and edge-thinned.

    Every entity ships. The old `?limit=` node cap is gone: it answered a big library with
    its 200 best-connected entities and no indication the rest existed. The payload is
    bounded by thinning EDGES instead -- `?min_weight=` drops weak relations, `?top_k=`
    keeps each node's K strongest and unions the keeps, so a hub cannot starve peripheral
    nodes of theirs. `thinning.edges_total` vs `edges_kept` reports what that cost.

    Nodes carry `x`/`y` from a 2-D projection of the embeddings of the items that mention
    them (centroid when several do, origin when none is usable), `placed` -- whether that
    position is real, which the coordinates cannot tell you since a centroid over opposed
    clusters is legitimately the origin -- `degree` in the full graph, and `cluster`, the id
    of their connected component in the thinned graph, set only for components of at least
    `layout.cluster_min_size`. `clusters` names each one from its entities' dominant item
    tag.

    Memoized per (library, min_weight, top_k) on a content signature of the tables the
    payload derives from, with the invalidation DEBOUNCED: a changed signature is served
    from cache (flagged `stale: true`) until `_GRAPH_MEMO_DEBOUNCE_SECS` has passed since
    the last compute, so a bulk import reprojects once rather than once per item.
    """
    store = _store(request)
    try:
        min_weight = float(request.query.get("min_weight") or _GRAPH_MIN_WEIGHT)
        top_k = int(request.query.get("top_k") or _GRAPH_TOP_K_PER_NODE)
    except (TypeError, ValueError):
        return web.json_response({"error": "invalid min_weight or top_k"}, status=400)
    if top_k < 1:
        return web.json_response({"error": "invalid min_weight or top_k"}, status=400)
    if not store.graph.nodes:
        return web.json_response(_graph_payload_shell(min_weight, top_k, stale=False))

    key = (str(store.db_path), min_weight, top_k)
    signature = _graph_signature(store)
    now = time.monotonic()
    entry = _graph_memo.get(key)
    if entry is not None and (
        entry["signature"] == signature or now - entry["computed_at"] < _GRAPH_MEMO_DEBOUNCE_SECS
    ):
        return web.json_response({**entry["payload"], "stale": entry["signature"] != signature})
    payload = _build_graph_payload(store, min_weight=min_weight, top_k=top_k)
    _graph_memo[key] = {"signature": signature, "payload": payload, "computed_at": now}
    return web.json_response({**payload, "stale": False})


# ---------- Stats ----------


def _stale_embedding_count(store, embedder) -> int:
    """How many active items hold a vector whose dimension != the ACTIVE model's — i.e.
    embedded under a previous model and now vector-dead (retrieval skips dimension
    mismatches). 0 when embeddings are off/unavailable or the model is unchanged. The
    dimension is the stored blob's byte-length / 4 (32-bit floats)."""
    if not (embedder and embedder.is_available()):
        return 0
    active_dim = embedder.dim()
    if not active_dim:
        return 0
    row = store.db.execute(
        "SELECT COUNT(*) as c FROM items WHERE status = 'active' "
        "AND embedding IS NOT NULL AND LENGTH(embedding) != ?",
        (active_dim * 4,),
    ).fetchone()
    return row["c"] if row else 0


async def get_stats(request: web.Request) -> web.Response:
    """GET /api/knowledge/stats."""
    store = _store(request)
    stats = store.get_stats()
    embedder = _get_embedder(request)
    if embedder:
        embedded_count = store.db.execute(
            "SELECT COUNT(*) FROM items WHERE embedding IS NOT NULL"
        ).fetchone()[0]
        stats["embeddings"] = {
            "enabled": True,
            "provider": type(embedder).__name__.lower().replace("embedder", ""),
            # UnifiedEmbedder has no `.model` (it wraps an embed_fn); read the active
            # embedding model id from its model_name property (the Settings→Models
            # selection). Using `.model` here raised AttributeError → /api/knowledge/
            # stats 500 → the FE header fell back to "semantic search off" even though
            # embeddings were live. (split-era regression: embedder was unified.)
            "model": embedder.model_name,
            "available": embedder.is_available(),
            "embedded_items": embedded_count,
            "stale_items": _stale_embedding_count(store, embedder),
        }
    else:
        stats["embeddings"] = {"enabled": False}
    return web.json_response(stats)


# ---------- Ingestion ----------


async def ingest_file(request: web.Request) -> web.Response:
    """POST /api/knowledge/ingest -- multipart file upload. Each file becomes ONE
    logical-document typed item run through its node-graph."""
    # A non-multipart body (wrong/absent Content-Type) makes multipart()/next() raise —
    # that's a malformed request (400), not a server fault (500).
    try:
        reader = await request.multipart()
        field = await reader.next()
    except Exception:
        return web.json_response({"error": "expected a multipart 'file' upload"}, status=400)
    if not field or not hasattr(field, "read_chunk") or field.name != "file":  # type: ignore[union-attr]  # noqa: E501
        return web.json_response({"error": "missing 'file' field"}, status=400)

    filename = getattr(field, "filename", None) or "upload"
    # The browser's declared content-type — disambiguates .webm/.ogg (audio vs video).
    upload_mime = (getattr(field, "headers", {}) or {}).get("Content-Type") or None
    suffix = Path(filename).suffix
    # Per-filetype cap from the shared upload policy (video 2 GB, audio 1 GB, image
    # 200 MB, …) — the browser mime disambiguates .webm/.ogg for the right category.
    from personalclaw.uploads import check_upload

    _limit = check_upload(filename, upload_mime).limit
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix, prefix="kn_")
    try:
        total_size = 0
        while True:
            chunk = await field.read_chunk()  # type: ignore[union-attr]
            if not chunk:
                break
            total_size += len(chunk)
            if total_size > _limit:
                tmp.close()
                Path(tmp.name).unlink(missing_ok=True)
                return web.json_response(
                    {"error": check_upload(filename, upload_mime, size=total_size).reason},
                    status=413,
                )
            tmp.write(chunk)
        tmp.close()

        # An empty upload has nothing to store, preview, or enrich — reject it cleanly
        # rather than creating a 0-byte item.
        if total_size == 0:
            Path(tmp.name).unlink(missing_ok=True)
            return web.json_response({"error": "uploaded file is empty"}, status=400)

        store = _store(request)

        # Every uploaded file becomes ONE logical-document item, stored under the
        # knowledge files dir and run through its node-graph (Image/Audio/Video or
        # Document graph → extracted-content pool → insights → embed). Text
        # extraction + chunking live inside the graph/embedder — never as separate
        # item rows. Model-backed nodes degrade gracefully without a model.
        if classify(filename, upload_mime) is None:
            Path(tmp.name).unlink(missing_ok=True)
            return web.json_response({"error": f"unsupported file type: {filename}"}, status=415)
        item, is_new = _store_file_item(store, tmp.name, filename, mime=upload_mime)
        Path(tmp.name).unlink(missing_ok=True)
        if item is None:
            return web.json_response({"error": "failed to store item"}, status=500)
        if is_new:
            # Only a freshly-stored file needs ingestion; a dedup hit is already enriched.
            try:
                request.app["state"].knowledge_ingest_queue().enqueue(item["id"])
            except Exception:
                logger.debug("file enqueue failed for %s", item["id"], exc_info=True)
        _sel_log("ingest", filename=filename, item_id=item["id"], deduped=not is_new)
        return web.json_response(
            {
                "item_id": item["id"],
                "type": item["type"],
                "status": "processing" if is_new else (item.get("processing_status") or "done"),
                "deduped": not is_new,
            }
        )
    except Exception:
        logger.exception("Ingestion failed for %s", filename)
        Path(tmp.name).unlink(missing_ok=True)
        return web.json_response({"error": "internal server error"}, status=500)


# ---------- Route registration ----------


async def get_embedding_status(request: web.Request) -> web.Response:
    """GET /api/knowledge/embedding/status -- embedding config and progress."""
    store = _store(request)
    embedder = _get_embedder(request)
    total = store.db.execute("SELECT COUNT(*) as c FROM items WHERE status = 'active'").fetchone()[
        "c"
    ]
    embedded = store.db.execute(
        "SELECT COUNT(*) as c FROM items WHERE status = 'active' AND embedding IS NOT NULL"
    ).fetchone()["c"]
    # Stale-model items count as 'embedded' but are vector-dead until re-embedded (their
    # vector dimension != the active model's; retrieval skips mismatches). Surface the
    # count so the UI can prompt a re-embed after a model switch.
    return web.json_response(
        {
            "enabled": embedder is not None,
            "available": embedder.is_available() if embedder else False,
            # UnifiedEmbedder exposes model_name (the active embedding selection), not
            # a `.model` attribute — same fix as get_stats (split-era embedder unify).
            "model": embedder.model_name if embedder else None,
            "total_items": total,
            "embedded_items": embedded,
            "stale_items": _stale_embedding_count(store, embedder),
        }
    )


async def batch_embed_items(request: web.Request) -> web.Response:
    """POST /api/knowledge/embedding/generate -- embed all unembedded items (or re-embed all)."""
    store = _store(request)
    embedder = _get_embedder(request)
    if not embedder:
        return web.json_response({"error": "Embedding not enabled"}, status=400)
    if not embedder.is_available():
        # Provider-blind: the UnifiedEmbedder wraps whatever model is bound to the
        # embedding use-case (native, ollama, openai-compatible, …) — never name one.
        return web.json_response({"error": "Embedding model not available"}, status=503)

    body = await request.json() if request.can_read_body else {}
    rebuild = body.get("rebuild", False)

    if rebuild:
        rows = store.db.execute(
            "SELECT id, title, summary, content FROM items WHERE status = 'active'"
        ).fetchall()
    else:
        rows = store.db.execute(
            "SELECT id, title, summary, content FROM items WHERE status = 'active' AND embedding IS NULL"  # noqa: E501
        ).fetchall()

    loop = asyncio.get_running_loop()
    embedded = 0
    failed = 0
    for row in rows:
        vec = await loop.run_in_executor(
            None, embedder.embed_for_item, row["title"], row["summary"], row["content"]
        )
        if vec:
            store.db.execute(
                "UPDATE items SET embedding = ? WHERE id = ?", (floats_to_bytes(vec), row["id"])
            )
            embedded += 1
        else:
            # Surface which item failed to embed — a silent skip here left items
            # permanently stale (embedded with an old-dimension vector) after a
            # model switch, with no signal to the user or logs about why.
            failed += 1
            logger.warning(
                "batch_embed: no vector for item %s (title=%r) — skipped",
                row["id"],
                (row["title"] or "")[:60],
            )

    store.db.commit()
    _sel_log("batch_embed", count=embedded, rebuild=rebuild, failed=failed)
    return web.json_response({"embedded": embedded, "total": len(rows), "failed": failed})


# ---------- Knowledge Fetch (for chat context injection) ----------

KNOWLEDGE_FETCH_TOP_N = 3
KNOWLEDGE_FETCH_MAX_TOKENS = 4096
# Hard ceiling for a per-request ?max_tokens override (guards against an unbounded
# context dump regardless of what a caller asks for).
_CONTEXT_MAX_TOKENS_CEILING = 32000


def _estimate_tokens(text: str) -> int:
    """Rough token estimate: ~4 chars per token for English text."""
    return len(text) // 4


async def search_for_context(request: web.Request) -> web.Response:
    """GET /api/knowledge/search-for-context?q=...&limit=N&max_tokens=N

    Returns top results formatted for chat injection cards. Each result includes a
    token count so the frontend can show budget. ``limit`` and ``max_tokens`` override
    the configured defaults (``max_tokens`` is clamped to a hard ceiling so a caller
    can't request an unbounded dump).
    """
    store = _store(request)
    q = request.query.get("q", "").strip()
    if not q:
        return web.json_response({"error": "q parameter required"}, status=400)

    from personalclaw.config.loader import config_path

    cfg_path = config_path()
    try:
        cfg = json.loads(cfg_path.read_text()) if cfg_path.exists() else {}
    except Exception:
        cfg = {}
    top_n = cfg.get("knowledge", {}).get("fetch_top_n", KNOWLEDGE_FETCH_TOP_N)
    max_tokens = cfg.get("knowledge", {}).get("fetch_max_tokens", KNOWLEDGE_FETCH_MAX_TOKENS)

    try:
        limit = int(request.query.get("limit", top_n))
    except ValueError:
        limit = top_n
    # Optional per-request token budget override, clamped to a hard ceiling so a
    # caller can't request an unbounded context dump.
    try:
        max_tokens = max(
            1, min(int(request.query.get("max_tokens", max_tokens)), _CONTEXT_MAX_TOKENS_CEILING)
        )
    except ValueError:
        pass

    embedder = _get_embedder(request)
    embed_fn = embedder.embed if embedder and embedder.is_available() else None
    retriever = HybridRetriever(store, embedder=embed_fn)
    results = retriever.search(q, limit=limit)

    cards = []
    total_tokens = 0
    for idx, r in enumerate(results):
        # _redact() calls redact_exfiltration_urls() + redact_credentials() (see ingestion.py)
        content = _redact(r.get("content", "")) or ""
        tokens = _estimate_tokens(content)
        remaining_budget = max_tokens - total_tokens
        if remaining_budget <= 0:
            break
        # Don't let one large item monopolize the budget and starve the other relevant
        # matches: cap each card at an even share of the budget remaining across the
        # still-unprocessed results. The last result may use all that's left.
        remaining_results = len(results) - idx
        per_card_cap = (
            max(1, remaining_budget // remaining_results)
            if remaining_results > 1
            else remaining_budget
        )
        allowed = min(remaining_budget, per_card_cap)
        if tokens > allowed:
            content = content[: allowed * 4]
            tokens = allowed
        cards.append(
            {
                "id": r["id"],
                "title": _redact(r["title"]) or "(untitled)",
                "provider": r.get("provider", "native"),
                "match_type": r.get("match_type", "keyword"),
                "tokens": tokens,
                "summary": _redact(r.get("summary")) or content[:200],
                "content": content,
                # P12 per-item citation locator — so a chat-injection card can deep-link + cite
                # where in the source the match sits, not just name the document.
                "source_type": r.get("source_type"),
                "section": r.get("section"),
                "line_range": r.get("line_range"),
                "deep_link": r.get("deep_link"),
            }
        )
        total_tokens += tokens

    _sel_log("search_for_context", query=q, results=len(cards))
    return web.json_response(
        {
            "query": q,
            "results": cards,
            "total_tokens": total_tokens,
            "max_tokens": max_tokens,
        }
    )


def _intent_store(request: web.Request):
    from pathlib import Path

    from personalclaw.knowledge.intents import IntentStore

    db_path = getattr(_store(request), "db_path", "") or ""
    path = Path(db_path).parent / "intents.json" if db_path else Path("intents.json")
    return IntentStore(path)


def _intents_payload(request: web.Request) -> list[dict]:
    """Intent dicts decorated with their recorded-outcome counts (for list badges)."""
    intents = _intent_store(request).load()
    counts = _store(request).intent_outcome_counts()
    return [{**i.to_dict(), "outcome_count": counts.get(i.id, 0)} for i in intents]


async def list_intents(request: web.Request) -> web.Response:
    """GET /api/knowledge/intents -- natural-language intents (Tier 3) + outcome counts."""
    return web.json_response({"intents": _intents_payload(request)})


async def upsert_intent(request: web.Request) -> web.Response:
    """POST /api/knowledge/intents -- create or update an intent."""
    from personalclaw.knowledge.intents import Intent

    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)
    if not str(body.get("goal") or body.get("description") or "").strip():
        return web.json_response({"error": "goal required"}, status=400)
    # An explicit id means the caller is EDITING that row, so a goal change is the edit
    # being saved. No id means a create whose id `from_dict` derives from the goal, and
    # only that path can land on a stranger's intent — which used to overwrite it and
    # answer 201 (#758). `api.ts` already documents this exact split: "New intents omit
    # id (the backend derives the slug from the goal); edits send it."
    replace = bool(str(body.get("id") or "").strip())
    try:
        # The id is derived from the goal when absent (the user never types one) —
        # from_dict owns the slug, so a caller may send only {goal}.
        intent = Intent.from_dict(body)
        store = _intent_store(request)
        store.upsert(intent, replace=replace)
    except ValueError as e:
        detail = str(e)
        if detail.startswith("intent_id_taken:"):
            # 409, not 400: the body is well-formed and the request would be valid at any
            # other moment. Name the OTHER intent's goal, because the id is derived and
            # invisible in the UI, so "intent-a1b2c3d4 is taken" would be unactionable.
            existing = _intent_store(request).get(detail.split(":", 1)[1])
            return json_error(
                "intent_id_taken",
                message=(
                    "An intent for “"
                    + ((existing.goal if existing else "").strip() or "another goal")
                    + "” already covers this. Edit that one, or reword this goal."
                ),
                status=409,
            )
        return web.json_response({"error": detail}, status=400)
    _sel_log("intent.upsert", intent_id=intent.id)
    return web.json_response({"intents": _intents_payload(request), "id": intent.id}, status=201)


async def delete_intent(request: web.Request) -> web.Response:
    """DELETE /api/knowledge/intents/{id} -- removes the intent and its outcomes."""
    store = _intent_store(request)
    intent_id = request.match_info["id"]
    if not store.delete(intent_id):
        return web.json_response({"error": "not found"}, status=404)
    _store(request).delete_intent_outcomes(intent_id)
    _sel_log("intent.delete", intent_id=intent_id)
    return web.json_response({"intents": _intents_payload(request)})


async def list_intent_outcomes(request: web.Request) -> web.Response:
    """GET /api/knowledge/intents/{id}/outcomes -- everything this intent has gathered,
    stored by value (survives source-item deletion). Each links back to its source
    item by id when that item still exists."""
    intent_id = request.match_info["id"]
    intent = _intent_store(request).get(intent_id)
    if intent is None:
        return web.json_response({"error": "not found"}, status=404)
    return web.json_response(
        {
            "intent": intent.to_dict(),
            "outcomes": _store(request).outcomes_for_intent(intent_id),
        }
    )


async def list_item_intents(request: web.Request) -> web.Response:
    """GET /api/knowledge/items/{id}/intents -- the intents this item contributed to
    (bidirectional link from the item side)."""
    store = _store(request)
    item_id = request.match_info["id"]
    if not store.get_item(item_id):
        return web.json_response({"error": "not found"}, status=404)
    return web.json_response({"outcomes": store.outcomes_for_item(item_id)})


def _consolidated_text(store, item: dict) -> str:
    """Best available text for matching: pooled extracted contents, else item content.

    Slice rows (WATCHED-SOURCES §5) are EXCLUDED. A slice is a role-sized view of text
    already in the pool, so concatenating them alongside the extraction they were cut from
    sends the same document two or three times — a silently multiplied token bill on a
    model call, and duplicated evidence for the intent matching it feeds.
    """
    from personalclaw.knowledge.slicing import is_slice_row

    parts = [
        ec.get("text") or ""
        for ec in store.get_extracted_contents(item["id"])
        if not is_slice_row(ec.get("node_type") or "")
    ]
    pooled = "\n\n".join(p for p in parts if p.strip())
    return pooled if pooled.strip() else (item.get("content") or "")


async def _run_intent_retroactive(app: web.Application, intent_id: str) -> dict:
    """Run one intent against every existing active item; record relevant outcomes.
    Returns {matched, new}: total items that matched, and how many were NEW matches
    (didn't already have an outcome for this intent) — so a re-run reports honestly."""
    from pathlib import Path

    from personalclaw.knowledge.intents import IntentStore, match_intent

    store = app["state"].knowledge_store
    db_path = getattr(store, "db_path", "") or ""
    ipath = Path(db_path).parent / "intents.json" if db_path else Path("intents.json")
    intent = IntentStore(ipath).get(intent_id)
    if intent is None:
        return {"matched": 0, "new": 0, "errors": 0, "evaluated": 0}
    # Items that already have an outcome for this intent (to tell new from re-matched).
    prior = {o["item_id"] for o in store.outcomes_for_intent(intent_id) if o.get("item_id")}
    pool = app.get("knowledge_llm_pool")
    rows = store.db.execute(
        "SELECT * FROM items WHERE status = 'active' AND COALESCE(is_archived,0)=0"
    ).fetchall()
    candidates = [store._serialize_item(r) for r in rows]
    candidates = [
        it for it in candidates if intent.applies_to(it.get("item_type") or it.get("type") or "")
    ]

    # Match items concurrently (bounded) instead of one sequential LLM call per item —
    # a retroactive run over a large library would otherwise be O(N) round-trips.
    sem = asyncio.Semaphore(6)

    async def _match(it: dict):
        async with sem:
            # raise_on_error: a model failure (cold pool, timeout) must be counted as
            # an error, not silently folded into "not relevant" → a misleading 0-match.
            return it, await match_intent(
                intent,
                _consolidated_text(store, it),
                pool=pool,
                raise_on_error=True,
            )

    results = await asyncio.gather(*(_match(it) for it in candidates), return_exceptions=True)

    # Record sequentially — the store's single sqlite connection isn't concurrency-safe.
    matched = 0
    new = 0
    errors = 0
    for res in results:
        if isinstance(res, BaseException):
            errors += 1
            continue
        it, match = res
        if match is None:
            continue
        store.record_intent_outcome(
            intent.id,
            intent_name=intent.goal,
            item_id=it["id"],
            item_title=it.get("title") or it.get("ai_title") or "",
            takeaway=match.takeaway,
            fields=match.fields,
        )
        matched += 1
        if it["id"] not in prior:
            new += 1
    return {"matched": matched, "new": new, "errors": errors, "evaluated": len(candidates)}


async def run_intent(request: web.Request) -> web.Response:
    """POST /api/knowledge/intents/{id}/run -- retroactively run an intent against all
    already-ingested items, recording outcomes for the matches."""
    intent_id = request.match_info["id"]
    if _intent_store(request).get(intent_id) is None:
        return web.json_response({"error": "not found"}, status=404)
    counts = await _run_intent_retroactive(request.app, intent_id)
    _sel_log("intent.run", intent_id=intent_id, **counts)
    return web.json_response(
        {
            # `recorded` kept as an alias of total matched (back-compat); `new`/`matched`
            # let the UI report new-vs-re-matched honestly on a re-run. `errors`/`evaluated`
            # distinguish "model couldn't evaluate" (e.g. cold pool) from "nothing matched".
            "recorded": counts["matched"],
            "matched": counts["matched"],
            "new": counts["new"],
            "errors": counts.get("errors", 0),
            "evaluated": counts.get("evaluated", 0),
            "outcomes": _store(request).outcomes_for_intent(intent_id),
        }
    )


def _slugify_intent(intent_id: str, goal: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", (intent_id or goal).lower()).strip("-")[:40]
    return base or "intent-skill"


def _parse_skill_sections(resp: str) -> dict:
    """Parse the DESCRIPTION/TRIGGERS/PROCEDURE delimited skill-synthesis response.

    Tolerant of surrounding prose, markdown-bold headers, and a model that echoes the
    template more than once: description/triggers come from their labeled lines, and
    the procedure is everything after the LAST ``PROCEDURE:`` header (so a leading
    prose copy or a re-stated template never doubles the body).
    """
    out: dict = {}
    m = re.search(r"\**DESCRIPTION\**:\s*(.+)", resp, re.I)
    if m:
        out["description"] = m.group(1).strip().strip("*").strip()
    m = re.search(r"\**TRIGGERS\**:\s*(.+)", resp, re.I)
    if m:
        out["triggers"] = m.group(1).strip().strip("*").strip()
    procs = list(re.finditer(r"\**PROCEDURE\**:\s*\n?", resp, re.I))
    if procs:
        out["procedure"] = resp[procs[-1].end() :].strip()
    elif not out.get("description") and resp.strip():
        # No headers at all → treat the whole response as the procedure body.
        out["procedure"] = resp.strip()
    return out


async def generate_skill_from_intent(request: web.Request) -> web.Response:
    """POST /api/knowledge/intents/{id}/generate-skill -- synthesize a reusable skill
    from what this intent has gathered so far. The user opts in per-generation (this
    is the action behind an intent's ``propose_skill`` flag — never auto-created).

    Distills the intent's goal + its recorded outcomes into a SKILL.md procedure via
    the knowledge LLM pool, then writes it as an ``auto/<slug>`` skill.
    """
    intent_id = request.match_info["id"]
    intent = _intent_store(request).get(intent_id)
    if intent is None:
        return web.json_response({"error": "not found"}, status=404)
    store = _store(request)
    outcomes = store.outcomes_for_intent(intent_id)
    if not outcomes:
        return web.json_response(
            {
                "error": "Nothing gathered yet — run the intent over your items first, then generate a skill."  # noqa: E501
            },
            status=400,
        )

    # Short-circuit if this intent's skill already exists: skip a wasted ~180s model
    # call and give a precise, actionable message (the prior ambiguous 409 conflated
    # "already exists" with "invalid name").
    from personalclaw.skills.loader import AUTO_SKILL_NAMESPACE, SkillsLoader

    _loader = SkillsLoader()
    _slug = _slugify_intent(intent_id, intent.goal)
    _existing = f"{AUTO_SKILL_NAMESPACE}/{_slug}"
    if (_loader._dir / _existing).exists():
        return web.json_response(
            {
                "error": f'A skill for this intent already exists — find "{_existing}" under Skills.',  # noqa: E501
                "skill": _existing,
                "already_exists": True,
            },
            status=409,
        )

    # Build a compact digest of what the intent has captured to ground the synthesis.
    lines = []
    for o in outcomes[:30]:
        flds = "; ".join(f"{f.get('name')}={f.get('value')}" for f in (o.get("fields") or [])[:6])
        lines.append(f"- {o.get('takeaway', '')}" + (f" ({flds})" if flds else ""))
    digest = "\n".join(lines)
    # A delimited-section contract (not JSON): the procedure is multi-line markdown,
    # which an LLM routinely emits with raw newlines that break strict JSON parsing.
    # The instruction is the native-knowledge app's ``knowledge_skill_synthesis``
    # prompt (bindable in Settings → Prompts), rendered with the goal + digest.
    from personalclaw.prompt_providers.runtime import render_use_case_prompt

    prompt = (
        render_use_case_prompt(
            "knowledge_skill_synthesis",
            {
                "goal": intent.goal,
                "digest": digest,
            },
        )
        or ""
    )
    pool = request.app.get("knowledge_llm_pool")
    if not pool:
        return web.json_response({"error": "No model available to synthesize a skill."}, status=503)
    try:
        resp = await pool.send(prompt, timeout=180.0)
    except Exception:
        logger.debug("skill synthesis failed for intent %s", intent_id, exc_info=True)
        resp = ""
    parts = _parse_skill_sections(resp or "")
    if not parts.get("procedure"):
        return web.json_response(
            {"error": "Could not synthesize a skill from the gathered outcomes."}, status=502
        )

    from datetime import datetime

    from personalclaw.skills.loader import AutoSkillProvenance

    now = datetime.now().isoformat()
    name = _loader.create_auto_skill(
        _slug,
        description=(_redact(parts.get("description") or intent.goal) or "")[:200],
        triggers=parts.get("triggers", ""),
        procedure_md=_redact(parts["procedure"]) or "",
        provenance=AutoSkillProvenance(session_key=f"intent:{intent_id}", created_at=now),
    )
    if name is None:
        # Existence was pre-checked above, so this is an invalid slug or an oversized
        # procedure — a synthesis-quality problem, not a duplicate.
        return web.json_response(
            {
                "error": "Couldn't save the skill — the synthesized procedure was invalid or too large."  # noqa: E501
            },
            status=422,
        )
    _sel_log("intent.generate_skill", intent_id=intent_id, skill=name)
    return web.json_response(
        {"skill": name, "description": parts.get("description", "")}, status=201
    )


async def get_extracted_contents(request: web.Request) -> web.Response:
    """GET /api/knowledge/items/{id}/extracted -- the per-item extracted-content
    pool (one row per ingestion node output: transcript, video-text, pdf-table…).
    Drill-down for the detail view's processing transparency (#30)."""
    store = _store(request)
    item_id = request.match_info["id"]
    if not store.get_item(item_id):
        return web.json_response({"error": "not found"}, status=404)
    return web.json_response({"contents": store.get_extracted_contents(item_id)})


# The runner's terminal stages run AFTER the type's graph (in this order) and emit
# the same per-node SSE phase events, but they aren't part of the PipelineGraph. The
# mini-DAG view appends them so the progress graph reflects the whole pipeline.
_TERMINAL_STAGES = ("insights", "entities", "intents", "embed")


async def get_item_graph(request: web.Request) -> web.Response:
    """GET /api/knowledge/items/{id}/graph -- the ingestion node-graph SHAPE for this
    item's type (nodes + edges + terminal stages), so the UI can render a mini-DAG and
    overlay live per-node status. Pure structure; live phases come over the SSE feed."""
    item = _store(request).get_item(request.match_info["id"])
    if not item:
        return web.json_response({"error": "not found"}, status=404)
    item_type = item.get("item_type") or item.get("type") or "note"
    try:
        from personalclaw.knowledge.pipeline import ensure_nodes_registered
        from personalclaw.knowledge.pipeline.graphs import graph_for

        ensure_nodes_registered()
        g = graph_for(item_type)
    except Exception:
        logger.debug("graph shape lookup failed for %s", item_type, exc_info=True)
        return web.json_response({"item_type": item_type, "nodes": [], "edges": []})

    nodes = [
        {
            "node_type": ns.node_type,
            "backend": ns.backend,
            "model_backed": ns.uses_use_case is not None,
        }
        for ns in g.nodes.values()
    ]
    # Dedup edges by (from, to): a node can be reached by multiple conditional routes
    # (e.g. video_classify→vision for both 'visual' and 'talking-head' verdicts), which
    # the shape view collapses to one line (the `when` condition isn't surfaced here).
    # Dedup by (from, to, loop): a forward conditional edge and a loop back-edge can
    # share endpoints but are distinct. Surface loop/when/max_iters so the UI can draw
    # the bounded back-edge as a loop arrow with its iteration cap.
    seen_edges: set = set()
    edges = []
    for e in g.edges:
        key = (e.from_node, e.to_node, e.loop)
        if key in seen_edges:
            continue
        seen_edges.add(key)
        ed: dict = {"from": e.from_node, "to": e.to_node}
        if e.loop:
            ed["loop"] = True
            ed["max_iters"] = e.max_iters
        if e.when:
            ed["when"] = e.when
        edges.append(ed)
    # Chain the terminal stages after the graph's leaf nodes (no out-edges).
    leaves = [nt for nt in g.nodes if not g.successors(nt)]
    prev_leaves = leaves or list(g.nodes)
    for stage in _TERMINAL_STAGES:
        nodes.append(
            {
                "node_type": stage,
                "backend": "",
                "model_backed": stage in ("insights", "entities", "intents"),
                "terminal": True,
            }
        )
        for p in prev_leaves:
            edges.append({"from": p, "to": stage})
        prev_leaves = [stage]
    # Ground-truth per-node phases persisted at ingest end (done/failed/skipped) — the
    # UI uses these on reload instead of reconstructing from processing_error, so a
    # skipped node reads as skipped (not falsely 'done'). Absent until first ingest.
    node_phases = (item.get("file_metadata") or {}).get("node_phases") or {}
    return web.json_response(
        {
            "item_type": item_type,
            "nodes": nodes,
            "edges": edges,
            "processing_status": item.get("processing_status", ""),
            "node_phases": node_phases,
        }
    )


async def stream_item_ingest(request: web.Request) -> web.StreamResponse:
    """GET /api/knowledge/items/{id}/ingest/stream -- per-item node-graph ingestion
    progress over SSE (queued→running→done per node, + ingest_complete). Per-resource
    feed ``knowledge:ingest:<id>`` (transport doctrine)."""
    from personalclaw.knowledge.pipeline.runner import progress_feed

    item_id = request.match_info["id"]
    registry = request.app["state"].knowledge_ingest_sse()
    feed = progress_feed(item_id)
    item = _store(request).get_item(item_id)
    pstatus = (item or {}).get("processing_status", "")
    snapshot = [("status", {"item_id": item_id, "processing_status": pstatus})]
    # Terminal-state items emit no further events — send the snapshot and close rather
    # than holding the connection open forever (a leak per already-done item opened).
    terminal = pstatus in ("done", "partial", "failed")
    return await stream_response(
        request,
        registry.hub(feed),
        on_connect=snapshot,
        registry_evict=(registry, feed),
        close_after_connect=terminal,
    )


# ---------- Collections (KNOWLEDGE-LIBRARY S1, contract C3) ----------


async def list_collections(request: web.Request) -> web.Response:
    """GET /api/knowledge/collections — every shelf in rail order."""
    return web.json_response({"collections": _store(request).list_collections()})


# ---------- Library home (KNOWLEDGE-LIBRARY S3, T3.3) ----------

#: How many items a home shelf carries. A shelf is a glance, not a list view — the library
#: list is one segment away and paginates properly.
_HOME_SHELF_LIMIT = 8
_HOME_SHELF_MAX = 24
#: A SMART shelf's count is `len(resolve_collection(...))` — the same call that produces the
#: items when the shelf is opened, so the number beside a shelf cannot disagree with what
#: opening it shows. That resolve runs hybrid retrieval, so it is capped, and the cap is
#: REPORTED (`count_capped`) rather than rendered as a smaller number pretending to be a total.
_HOME_SMART_COUNT_CAP = 200
#: Ordering clauses. Literals, never interpolated from a request: the home surface picks the
#: shelf, the caller never picks the ORDER BY.
_HOME_ORDER_ADDED = "i.created_at DESC, i.id DESC"
_HOME_ORDER_TOUCHED = "i.updated_at DESC, i.id DESC"


def _listable_where() -> tuple[str, list[object]]:
    """The library's own "should a user see this row" predicate, as SQL over alias ``i``.

    Mirrors `GET /api/knowledge/items`' no-query branch: mirrored artifacts and
    `DEFAULT_LIST_EXCLUDED_KINDS` are INDEXED, not LISTED, and an archived item is the user
    saying "not in my active library". A home shelf that disagreed with the list it links
    into would put rows on the landing surface that vanish the moment you click through.
    """
    where = ["i.item_type != ?", "COALESCE(i.is_archived, 0) = 0"]
    params: list[object] = [ARTIFACT_ITEM_TYPE]
    excluded = sorted(DEFAULT_LIST_EXCLUDED_KINDS)
    if excluded:
        marks = ",".join("?" * len(excluded))
        where.append(f"COALESCE(i.kind, '') NOT IN ({marks})")  # noqa: S608
        params.extend(excluded)
    return " AND ".join(where), params


def _home_shelf(store, extra_where: str, order: str, limit: int) -> list[dict]:
    """One shelf's rows, list-serialized exactly like the library list serializes them."""
    base, params = _listable_where()
    clause = f"{base} AND {extra_where}" if extra_where else base
    rows = store.db.execute(
        f"SELECT i.* FROM items i WHERE {clause} ORDER BY {order} LIMIT ?",  # noqa: S608
        [*params, limit],
    ).fetchall()
    return [_list_item(store, r) for r in rows]


def _collection_count(store, coll: dict) -> tuple[int, bool]:
    """A shelf's live count, and whether it hit the smart-resolve cap.

    🔑 DERIVED FROM THE SAME QUERY THAT PRODUCES THE ITEMS, per kind — a count kept beside a
    table is a count that can disagree with it, and `list_collections`' own `item_count` is
    already an instance: it counts every membership row, INCLUDING archived items, while
    `resolve_collection` excludes them. So the rail number can exceed what opening the shelf
    shows. Here manual counts repeat `resolve_collection`'s manual WHERE (join + the archived
    exclusion, no LIMIT, so it is exact) and smart counts ARE `resolve_collection`.
    """
    if coll.get("kind") == "smart":
        n = len(store.resolve_collection(coll["id"], limit=_HOME_SMART_COUNT_CAP))
        return n, n >= _HOME_SMART_COUNT_CAP
    row = store.db.execute(
        "SELECT COUNT(*) FROM collection_items ci JOIN items i ON i.id = ci.item_id "
        "WHERE ci.collection_id = ? AND COALESCE(i.is_archived, 0) = 0",
        (coll["id"],),
    ).fetchone()
    return int(row[0]), False


async def library_home(request: web.Request) -> web.Response:
    """GET /api/knowledge/library-home — the four shelves of the library landing surface.

    ONE request, not four: the home is a glance surface, and four independent fetches would
    make it four independent failure modes on one screen (three shelves populated, the fourth
    blank for a reason the user cannot see). One read means one error envelope, so the client
    can say "this whole surface failed" instead of rendering a shelf that is empty-looking and
    broken at the same time.

    The three item shelves are the library's own listable set (`_listable_where`) narrowed by
    one column each: newest by `created_at`, `read_state='reading'`, `favorited`. The shelves
    are deliberately NOT search results — a landing surface must not depend on the embedder or
    the retriever being up.
    """
    store = _store(request)
    try:
        limit = min(_HOME_SHELF_MAX, max(1, int(request.query.get("limit", _HOME_SHELF_LIMIT))))
    except ValueError:
        return json_error("invalid_limit", status=400)
    collections = []
    for coll in store.list_collections():
        count, capped = _collection_count(store, coll)
        collections.append(
            {
                "id": coll["id"],
                "name": coll.get("name") or "",
                "kind": coll.get("kind") or "manual",
                "icon": coll.get("icon") or "",
                "position": coll.get("position"),
                "count": count,
                "count_capped": capped,
            }
        )
    return web.json_response(
        {
            "recently_added": _home_shelf(store, "", _HOME_ORDER_ADDED, limit),
            # `read_state` is NULL for rows written before the column existed; the store
            # normalizes that to 'unread' on read, and only an explicit 'reading' belongs here.
            "continue_reading": _home_shelf(
                store, "i.read_state = 'reading'", _HOME_ORDER_TOUCHED, limit
            ),
            "favorites": _home_shelf(
                store, "COALESCE(i.favorited, 0) = 1", _HOME_ORDER_TOUCHED, limit
            ),
            "collections": collections,
        }
    )


def _collection_clash_response(detail: str) -> web.Response | None:
    """A `collection_name_taken:<name>` from the store, as a 409 that names the shelf.

    409 and not 400: the body is well-formed and the request would be valid at any other
    moment. Names the existing shelf because "already taken" without saying WHICH is not
    actionable when the rail is a row of chips — the same reasoning as `intent_id_taken`.
    """
    if not detail.startswith("collection_name_taken:"):
        return None
    taken = detail.split(":", 1)[1]
    return json_error(
        "collection_name_taken",
        message=(
            f"A shelf called \u201c{taken}\u201d already exists. "
            "Open that one, or pick a different name."
        ),
        status=409,
    )


async def create_collection(request: web.Request) -> web.Response:
    """POST /api/knowledge/collections — create a manual or smart shelf."""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)
    try:
        cid = _store(request).create_collection(
            name=str(body.get("name") or ""),
            kind=str(body.get("kind") or "manual"),
            query=str(body.get("query") or ""),
            icon=str(body.get("icon") or ""),
        )
    except ValueError as exc:
        # The store's own validation is the single source of truth for what a valid
        # shelf is; the handler surfaces it rather than duplicating the rules.
        clash = _collection_clash_response(str(exc))
        return clash or web.json_response({"error": str(exc)}, status=400)
    coll = _store(request).get_collection(cid)
    return web.json_response({"ok": True, "collection": coll}, status=201)


async def update_collection(request: web.Request) -> web.Response:
    """PATCH /api/knowledge/collections/{id} — rename / re-icon / re-query / reorder."""
    cid = request.match_info["id"]
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)
    store = _store(request)
    if not store.get_collection(cid):
        return web.json_response({"error": "collection not found"}, status=404)
    fields = {k: v for k, v in body.items() if k in ("name", "kind", "query", "icon", "position")}
    if not fields:
        return web.json_response(
            {
                "error": {
                    "code": "nothing_to_update",
                    "message": "supply at least one of name, kind, query, icon, position",
                }
            },
            status=400,
        )
    try:
        store.update_collection(cid, **fields)
    except ValueError as exc:
        clash = _collection_clash_response(str(exc))
        return clash or web.json_response({"error": str(exc)}, status=400)
    return web.json_response({"ok": True, "collection": store.get_collection(cid)})


async def delete_collection(request: web.Request) -> web.Response:
    """DELETE /api/knowledge/collections/{id} — remove the shelf, keep the items."""
    cid = request.match_info["id"]
    if not _store(request).delete_collection(cid):
        return web.json_response({"error": "collection not found"}, status=404)
    return web.json_response({"ok": True})


async def get_collection_items(request: web.Request) -> web.Response:
    """GET /api/knowledge/collections/{id}/items — resolve the shelf.

    Manual shelves join their membership; smart shelves run their stored query, so the
    result reflects the library right now.
    """
    cid = request.match_info["id"]
    store = _store(request)
    coll = store.get_collection(cid)
    if not coll:
        return web.json_response({"error": "collection not found"}, status=404)
    try:
        limit = min(200, max(1, int(request.query.get("limit", 50))))
    except ValueError:
        return web.json_response({"error": "invalid limit"}, status=400)
    items = store.resolve_collection(cid, limit=limit)
    return web.json_response({"collection": coll, "items": items, "count": len(items)})


async def add_collection_items(request: web.Request) -> web.Response:
    """POST /api/knowledge/collections/{id}/items — shelve one or many items.

    Per-item results, so shelving 30 items doesn't fail wholesale because one was
    deleted in another tab.
    """
    cid = request.match_info["id"]
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)
    store = _store(request)
    coll = store.get_collection(cid)
    if not coll:
        return web.json_response({"error": "collection not found"}, status=404)
    if coll.get("kind") == "smart":
        # Adding to a smart shelf is a category error: its contents come from its
        # query, so a membership row would be silently ignored on every read.
        return web.json_response(
            {
                "error": {
                    "code": "smart_collection_immutable",
                    "message": (
                        "A smart collection's contents come from its query — edit the "
                        "query instead of adding items."
                    ),
                }
            },
            status=400,
        )
    raw = body.get("item_ids")
    if raw is None and body.get("item_id"):
        raw = [body["item_id"]]
    if not isinstance(raw, list) or not raw:
        return web.json_response(
            {"error": {"code": "item_ids_required", "message": "supply item_ids: [...]"}},
            status=400,
        )
    added: list[str] = []
    missing: list[str] = []
    for iid in (str(x) for x in raw):
        (added if store.add_to_collection(cid, iid) else missing).append(iid)
    return web.json_response({"ok": True, "added": added, "missing": missing})


async def remove_collection_item(request: web.Request) -> web.Response:
    """DELETE /api/knowledge/collections/{id}/items/{item_id} — unshelve one item."""
    cid = request.match_info["id"]
    iid = request.match_info["item_id"]
    if not _store(request).remove_from_collection(cid, iid):
        return web.json_response({"error": "not on that collection"}, status=404)
    return web.json_response({"ok": True})


async def set_item_read_state(request: web.Request) -> web.Response:
    """POST /api/knowledge/items/{id}/read-state — unread | reading | read."""
    iid = request.match_info["id"]
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict) or "state" not in body:
        return web.json_response(
            {"error": {"code": "state_required", "message": "supply state: unread|reading|read"}},
            status=400,
        )
    store = _store(request)
    try:
        ok = store.set_read_state(iid, str(body["state"]))
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    if not ok:
        return web.json_response({"error": "item not found"}, status=404)
    return web.json_response({"ok": True, "read_state": store.get_item(iid)["read_state"]})


async def set_item_favorited(request: web.Request) -> web.Response:
    """POST /api/knowledge/items/{id}/favorite — star or unstar."""
    iid = request.match_info["id"]
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    store = _store(request)
    if not store.set_favorited(iid, bool(body.get("value", True))):
        return web.json_response({"error": "item not found"}, status=404)
    return web.json_response({"ok": True, "favorited": store.get_item(iid)["favorited"]})


# A selection larger than this is a client bug rather than an intent, and a runaway
# bulk write over an entire library is worth refusing rather than serving. Mirrors
# `session_bulk._MAX_KEYS`.
_BULK_MAX_ITEMS = 500


# ---------- Tag taxonomy (KNOWLEDGE-LIBRARY S2, T2.2) ----------


def _tag_id(request: web.Request) -> int | None:
    """Parse the {id} path segment as a tag id. Tag ids are integers (a surrogate key,
    so a rename is one row) — a non-numeric segment is a 404, not a 500."""
    try:
        return int(request.match_info["id"])
    except (KeyError, TypeError, ValueError):
        return None


async def list_tag_taxonomy(request: web.Request) -> web.Response:
    """GET /api/knowledge/tag-tree — every tag with its parent and live usage count.

    Distinct from `GET /api/knowledge/tags`, which stays a flat frequency-ordered
    `list[str]` for the ChipInput autocomplete. This one is the management surface, so it
    carries ids, parents and counts.
    """
    return web.json_response({"tags": _store(request).list_tags()})


async def list_conflicts(request: web.Request) -> web.Response:
    """GET /api/knowledge/conflicts — every recorded disagreement in the store.

    A read surface, not a resolution one. Conflicts are flagged at INGEST (§3.2) and both claims
    are always kept; this route exists so the flag is visible to a human rather than sitting in
    `file_metadata` where only the next synthesis would see it. Deciding a conflict is a judgement
    about which source to trust, which is the owner's call — so there is deliberately no
    "resolve" endpoint that would let the system pick a winner on its own.

    `basis` rides along on every row because a deterministic finding and a model's opinion warrant
    different confidence, and a reader cannot tell them apart from the claim text alone.
    """
    store = _store(request)
    limit = _int_param(request, "limit", 100, low=1, high=500)
    rows: list[dict] = []
    try:
        # A LIKE prefilter before parsing: `file_metadata` is a JSON blob on every row, and
        # json-loading the whole store to find the few items with conflicts would make this route
        # scale with library size rather than with the number of conflicts.
        candidates = store.db.execute(
            "SELECT id, title, kind, file_metadata FROM items "
            "WHERE is_archived = 0 AND file_metadata LIKE '%\"conflicts\"%' "
            "ORDER BY updated_at DESC LIMIT ?",
            (limit,),
        )
        for row in candidates:
            try:
                meta = json.loads(row["file_metadata"] or "{}")
            except (TypeError, ValueError):
                continue
            entries = meta.get("conflicts") if isinstance(meta, dict) else None
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if isinstance(entry, dict):
                    rows.append({"item_id": row["id"], "item_title": row["title"], **entry})
    except Exception:
        logger.warning("could not read knowledge conflicts", exc_info=True)
        return web.json_response({"conflicts": [], "count": 0, "error": "unreadable"})
    return web.json_response({"conflicts": rows[:limit], "count": len(rows)})


async def list_item_relations(request: web.Request) -> web.Response:
    """GET /api/knowledge/items/{id}/relations — the typed edges into and out of one item.

    Both directions, labelled. A one-directional view would hide the more useful half: what
    SUPERSEDES this item is usually what a reader actually wants, and that is an inbound edge.
    """
    item_id = str(request.match_info.get("id", "") or "")
    if not item_id:
        return web.json_response({"error": "item id required"}, status=400)
    store = _store(request)
    out: dict[str, list[dict]] = {"outbound": [], "inbound": []}
    try:
        for direction, sql in (
            (
                "outbound",
                "SELECT r.target_item_id AS other, r.relation_type, r.confidence, r.provenance, "
                "i.title FROM item_relations r LEFT JOIN items i ON i.id = r.target_item_id "
                "WHERE r.source_item_id = ?",
            ),
            (
                "inbound",
                "SELECT r.source_item_id AS other, r.relation_type, r.confidence, r.provenance, "
                "i.title FROM item_relations r LEFT JOIN items i ON i.id = r.source_item_id "
                "WHERE r.target_item_id = ?",
            ),
        ):
            out[direction] = [
                {
                    "item_id": r["other"],
                    "title": r["title"] or "",
                    "relation": r["relation_type"],
                    "confidence": r["confidence"],
                    "provenance": r["provenance"],
                }
                for r in store.db.execute(sql, (item_id,))
            ]
    except Exception:
        # An older store has no `item_relations` table. Empty lists rather than a 500: a reader
        # asking for relations on a store that has none should see none, not an error page.
        logger.debug("item_relations unavailable", exc_info=True)
    return web.json_response(out)


async def get_item_staleness(request: web.Request) -> web.Response:
    """GET /api/knowledge/items/{id}/staleness — has the corpus moved under this synthesis?

    The count, not just a boolean: "3 new source items" is something a reader can weigh
    against clicking regenerate, where a bare "may be out of date" is a shrug. ``scope``
    names what was counted, so the number is arguable rather than oracular. The rules live
    in :mod:`personalclaw.knowledge.staleness`.
    """
    store = _store(request)
    item_id = request.match_info["id"]
    try:
        report = staleness_for(store, item_id)
    except KeyError:
        return web.json_response({"error": "not found"}, status=404)
    return web.json_response(report.to_dict())


async def regenerate_item(request: web.Request) -> web.Response:
    """POST /api/knowledge/items/{id}/regenerate — the one action the staleness banner offers.

    It RECOMPUTES the synthesis from the sources the item cites and files that as a PROPOSAL
    (``auto_accept=False``) rather than overwriting in place: a synthesis the reader may already
    have acted on should not change under them without a review step. The recompute itself lives
    in :func:`personalclaw.knowledge.updates.regenerate_synthesis` — this route is the HTTP
    shape around it and owns no synthesis of its own.

    ``ok`` reports whether a proposal was actually FILED, so the two ways a regeneration can
    honestly come back empty are legible instead of dressed as success:

    * nothing to work with or nothing to change (no cited sources, prose identical to the
      stored item, a validation refusal, a queue SKIP) — ``200`` with ``ok: false`` and the
      layer's own sentence under ``proposal.reason``, which the banner renders verbatim;
    * no model produced a synthesis — ``503`` with ``reason: "model_unavailable"``, the same
      shape as a missing update pipeline below, because both mean the action cannot run here.

    Idempotency belongs to the proposal layer, which owns the pending row; this route only
    re-surfaces what that layer reports. ``already_pending`` is ``true``/``false`` when
    :func:`personalclaw.knowledge.updates.propose_update` says so under that key and ``null``
    when it says nothing — so a second click is never reported as a second proposal created
    on the strength of a guess here.
    """
    store = _store(request)
    item_id = request.match_info["id"]
    item = store.get_item(item_id)
    if not item:
        return web.json_response({"error": "not found"}, status=404)
    if not is_synthesized(str(item.get("item_type") or "")):
        return web.json_response(
            {"error": "only a synthesized item can be regenerated"}, status=400
        )
    try:
        from personalclaw.knowledge.updates import SynthesisUnavailable, regenerate_synthesis
    except ImportError:
        # The update pipeline is a separate module; without it there is no regenerate action
        # to perform. An explicit "unavailable" beats a traceback on a button the banner
        # itself offered the reader.
        logger.warning("knowledge update pipeline unavailable", exc_info=True)
        return web.json_response(
            {
                "error": "regeneration is unavailable: the knowledge update pipeline is "
                "not installed",
                "reason": "updates_unavailable",
            },
            status=503,
        )
    try:
        result = await regenerate_synthesis(store, item_id)
    except SynthesisUnavailable as exc:
        # No model, no synthesis, nothing filed. Said out loud on the same rail as the missing
        # pipeline above — the alternative (200 with a cheerful body) is the inert control this
        # route used to be.
        logger.warning("knowledge regenerate could not run: %s", exc)
        return web.json_response({"error": str(exc), "reason": "model_unavailable"}, status=503)
    if not isinstance(result, dict):  # pragma: no cover — the outcome contract is a dict
        result = {}
    # FILED, not merely "the call returned". `pending` is the proposal layer's own word for a
    # row waiting on the owner and `applied` for a landed write; anything else queued nothing,
    # which is exactly what `ok` has to say.
    filed = bool(result.get("pending") or result.get("applied"))
    already = result.get("already_pending")
    try:
        sel().log_tool_invocation(
            session_key="dashboard:knowledge",
            tool_name="knowledge_regenerate_item",
            outcome="success" if filed else "skip",
            request_id=item_id,
            source="dashboard",
            error="" if filed else str(result.get("reason") or ""),
        )
    except Exception:
        logger.warning("SEL audit failed for knowledge regenerate", exc_info=True)
    return web.json_response(
        {"ok": filed, "item_id": item_id, "already_pending": already, "proposal": result}
    )


def _int_param(request: web.Request, name: str, default: int, *, low: int, high: int) -> int:
    raw = request.query.get(name)
    try:
        value = int(str(raw))
    except (TypeError, ValueError):
        return default
    return max(low, min(high, value))


async def rename_tag(request: web.Request) -> web.Response:
    """PATCH /api/knowledge/tags/{id} — rename, or re-parent via `parent_id`."""
    tid = _tag_id(request)
    if tid is None:
        return web.json_response({"error": "tag not found"}, status=404)
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)
    store = _store(request)
    if "name" not in body and "parent_id" not in body:
        return web.json_response(
            {
                "error": {
                    "code": "nothing_to_update",
                    "message": "supply name and/or parent_id",
                }
            },
            status=400,
        )
    try:
        if "name" in body:
            if not store.rename_tag(tid, str(body["name"])):
                return web.json_response({"error": "tag not found"}, status=404)
        if "parent_id" in body:
            raw = body["parent_id"]
            # null / "" both mean "make this a root tag".
            parent = None if raw in (None, "") else int(raw)
            if not store.set_tag_parent(tid, parent):
                return web.json_response({"error": "tag not found"}, status=404)
    except (TypeError, ValueError) as exc:
        raw = str(exc)
        # `tag_cycle` and `tag_name_taken:<name>` are the typed codes the store raises; anything
        # else is a plain argument problem.
        #
        # 🔴 `message` USED TO BE THE CODE ITSELF. That was a backend degrading its own sentence to
        # work around a frontend limitation: `TagManager` matched `msg.includes('tag_cycle')`, so
        # the message had to BE the token for the match to fire, and the fallback then rendered
        # `Couldn't update the tag: tag_name_taken:archive` — a machine token shown to a person.
        # `ApiError` now carries `code` and `lib/api.hasApiCode` exists so a caller keys on the
        # code, and its own docstring states the rule: "Match on the code, NEVER on `.message`:
        # the message is human copy that gets reworded, the code is the registry key." So the code
        # goes in `code` and a sentence goes in `message`, composed HERE — one source for the
        # wording, rather than the same two sentences in the handler and the panel.
        if raw.startswith("tag_cycle"):
            code = "tag_cycle"
            message = "That would make a tag its own ancestor. Pick a different parent."
        elif raw.startswith("tag_name_taken"):
            code = "tag_name_taken"
            # The store appends the colliding name; it is the most useful half of the sentence.
            taken = raw.split(":", 1)[1] if ":" in raw else ""
            message = (
                f'A tag named "{taken}" already exists. Merge them instead of renaming.'
                if taken
                else "A tag with that name already exists. Merge them instead of renaming."
            )
        else:
            code = "invalid_tag_update"
            # Deliberately NOT the raw exception text. A `TypeError`/`ValueError` string is Python's
            # words, never user copy, and it was reaching the notification verbatim.
            message = "That tag update wasn't valid."
        return web.json_response({"error": {"code": code, "message": message}}, status=400)
    _sel_log("tag_update", tag_id=tid, fields=sorted(body.keys()))
    return web.json_response({"ok": True, "tags": store.list_tags()})


async def merge_tag(request: web.Request) -> web.Response:
    """POST /api/knowledge/tags/{id}/merge {into} — fold this tag into another."""
    tid = _tag_id(request)
    if tid is None:
        return web.json_response({"error": "tag not found"}, status=404)
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict) or body.get("into") in (None, ""):
        return web.json_response(
            {"error": {"code": "into_required", "message": "supply into: <tag id>"}},
            status=400,
        )
    store = _store(request)
    try:
        result = store.merge_tags(tid, int(body["into"]))
    except (TypeError, ValueError) as exc:
        return web.json_response(
            {"error": {"code": "invalid_merge", "message": str(exc)}}, status=400
        )
    _sel_log("tag_merge", source=tid, target=body["into"], **result)
    return web.json_response({"ok": True, **result, "tags": store.list_tags()})


async def delete_tag(request: web.Request) -> web.Response:
    """DELETE /api/knowledge/tags/{id} — remove a tag from the taxonomy and every item.

    Children are re-parented to root, not deleted: removing a parent should never
    silently destroy the branch beneath it.
    """
    tid = _tag_id(request)
    if tid is None:
        return web.json_response({"error": "tag not found"}, status=404)
    store = _store(request)
    if not store.delete_tag(tid):
        return web.json_response({"error": "tag not found"}, status=404)
    _sel_log("tag_delete", tag_id=tid)
    return web.json_response({"ok": True, "tags": store.list_tags()})


async def bulk_items(request: web.Request) -> web.Response:
    """POST /api/knowledge/bulk — apply one curation op to many items.

    Body: ``{"op": "collect", "item_ids": [...], ...op args}``.

    Per-item best-effort with per-item results (``changed``/``unchanged``/``missing``),
    following `session_bulk.py`: a selection can go stale between the click and the
    request, so "38 shelved, 2 not found" beats a wholesale failure. Argument problems
    are a typed 400 — a caller that forgot `collection_id` should hear about it rather
    than get a silent no-op across the whole selection.
    """
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "body must be an object"}, status=400)

    store = _store(request)
    op = str(body.get("op") or "")
    if op not in store.BULK_OPS:
        return web.json_response(
            {
                "error": {
                    "code": "unknown_op",
                    "message": f"op must be one of {sorted(store.BULK_OPS)}",
                    "received": op,
                }
            },
            status=400,
        )
    raw = body.get("item_ids")
    if not isinstance(raw, list) or not raw:
        return web.json_response(
            {
                "error": {
                    "code": "item_ids_required",
                    "message": "item_ids must be a non-empty list of knowledge item ids",
                }
            },
            status=400,
        )
    if len(raw) > _BULK_MAX_ITEMS:
        return web.json_response(
            {
                "error": {
                    "code": "too_many_items",
                    "message": f"at most {_BULK_MAX_ITEMS} items per bulk call",
                    "received": len(raw),
                }
            },
            status=400,
        )

    args = {k: v for k, v in body.items() if k not in ("op", "item_ids")}
    try:
        result = store.bulk_apply(op, [str(i) for i in raw], **args)
    except ValueError as exc:
        # `smart_collection_immutable` is a typed code the frontend keys on; the rest
        # are argument problems whose message names the missing/invalid field.
        code = str(exc)
        return web.json_response(
            {
                "error": {
                    "code": code if code == "smart_collection_immutable" else "invalid_bulk_args",
                    "message": code,
                }
            },
            status=400,
        )

    _sel_log(
        f"bulk.{op}",
        changed=len(result["changed"]),
        unchanged=len(result["unchanged"]),
        missing=len(result["missing"]),
    )
    return web.json_response({"ok": True, "op": op, **result})


# ── Watched sources: the create/tune/inspect surface (WATCHED-SOURCES §2.4/§6.3/§12) ──
#
# WS-2..WS-5 shipped the store, the poll engine and three providers, and `create_source`
# had ZERO non-test callers — there was no route, no CLI and no UI through which a user
# could create a watched source of any kind. This is that surface.
#
# Two disciplines run through the whole block:
#
#   * Every closed vocabulary and every remediation string is read from the PROVIDER, not
#     retyped here or in TypeScript. The health statuses come from `base.SOURCE_HEALTH`,
#     the detector list from `web_source.DETECTOR_ORDER`, the feed presets from
#     `feed_source.PRESETS`, and the two opposite remediations from
#     `LISTING_PAGE_GUIDANCE` / `RENDER_TIER_GUIDANCE`. A copy of any of those in the UI
#     would be a second artifact that drifts from the thing that actually enforces it.
#   * A spec is never trusted from the client. Each provider's own `validate_spec` decides,
#     so save-time validation is byte-identical to the poll-time re-validation WS-3/WS-5
#     already do — one validator, not a client-side approximation of it.


#: The remediation a source needs, when it needs one. Two kinds, deliberately NOT collapsed
#: into a single "found nothing" message: WS-3 measures the discrimination (a page that
#: rendered plenty of text and yielded no items is the WRONG URL; a page carrying script
#: with almost no visible text is a JS SHELL) precisely because the two fixes are opposite —
#: point at a listing page vs. turn on the render tier. One message would send half the
#: users the wrong way, which is the entire reason `HEALTH_NEEDS_RENDER` exists as a status
#: distinct from `degraded`.
_REMEDIATION_LISTING_PAGE = "listing_page"
_REMEDIATION_RENDER_TIER = "render_tier"


def _source_providers() -> list:
    """Every registered POLL-CAPABLE knowledge provider — the ones a source row can name.

    Read through the SAME registry seam the engine enrolls from
    (:meth:`SourceEngine.enrolled_provider_names`), so a provider this endpoint offers is
    exactly a provider that will actually poll. Offering a kind nothing polls would let a
    user create a source that sits inert forever.
    """
    from personalclaw.knowledge_providers.base import KnowledgeSourceProvider
    from personalclaw.knowledge_providers.registry import list_providers

    return [p for p in list_providers() if isinstance(p, KnowledgeSourceProvider)]


def _kind_descriptor(provider) -> dict:
    """The UI-facing shape of ONE source kind, keyed on the provider's CLASS.

    Class rather than name string on purpose: `"watched-page"` written here would be a
    third copy of a name the provider already owns and the store already persists. An
    app-contributed source provider (WS-8's connector packs) matches none of the three and
    gets the generic descriptor — a spec editor and no bespoke form — rather than being
    dropped from the catalog, because a kind the create flow refuses to show is a kind
    nobody can use.
    """
    from personalclaw.knowledge_providers import dir_source, feed_source, web_source

    if isinstance(provider, web_source.WebSourceProvider):
        return {
            "kind": "web_page",
            "form": "web_page",
            "default_item_type": "bookmark",
            "detectors": list(web_source.DETECTOR_ORDER),
            "max_requests": web_source.DEFAULT_MAX_REQUESTS,
            "guidance": {
                _REMEDIATION_LISTING_PAGE: web_source.LISTING_PAGE_GUIDANCE,
                _REMEDIATION_RENDER_TIER: web_source.RENDER_TIER_GUIDANCE,
            },
        }
    if isinstance(provider, feed_source.FeedSourceProvider):
        return {
            "kind": "feed",
            "form": "feed",
            "default_item_type": "bookmark",
            "formats": sorted(feed_source.FEED_KINDS),
            "presets": sorted(feed_source.PRESETS),
        }
    if isinstance(provider, dir_source.DirSourceProvider):
        return {
            "kind": "dir",
            "form": "dir",
            "default_item_type": "note",
            "default_include": list(dir_source.DEFAULT_INCLUDE),
            "max_files": dir_source.MAX_FILES_PER_SOURCE,
        }
    return {"kind": "external", "form": "spec", "default_item_type": "bookmark"}


def _source_kinds() -> list[dict]:
    """The create flow's catalog: one entry per registered poll-capable provider.

    ``previewable`` is measured, not declared. WS-3 deliberately kept ``preview`` OFF the
    :class:`KnowledgeSourceProvider` ABC — a feed's or a directory's preview IS its poll, so
    an abstract ``preview`` would have been a stub on two of three providers. The asymmetry
    is therefore real, and the honest thing is to report it so the UI can offer a paste-URL
    preview where one exists and say plainly where one does not, rather than fake a uniform
    dry run by half-polling a feed.
    """
    out: list[dict] = []
    for prov in _source_providers():
        out.append(
            {
                "provider": prov.name,
                "display_name": getattr(prov, "display_name", prov.name),
                "poll_interval_secs": int(getattr(prov, "poll_interval_seconds", 3600) or 3600),
                "previewable": callable(getattr(prov, "preview", None)),
                **_kind_descriptor(prov),
            }
        )
    return sorted(out, key=lambda k: k["display_name"])


def _remediation(source: dict) -> dict:
    """What the user can DO about this source's last poll, or an empty verdict.

    Derived from what the engine already persisted (WS-3 writes ``health_status`` and
    ``last_error_summary`` on every poll, success and failure alike) — never recomputed by
    re-polling, which would make opening a page a fetch at someone else's server.

    ``last_error_summary`` is the provider's guidance CLIPPED to 200 chars by
    ``record_poll``, and ``LISTING_PAGE_GUIDANCE`` is longer than that, so the match is
    "the stored summary is a prefix of this guidance" rather than equality — equality would
    silently never fire for exactly the longer of the two messages.

    ``detail`` carries the stored summary only when it says something the guidance does not
    (a render tier that raised, or is allowed but not installed). Echoing a prefix of the
    guidance back above the guidance would be the same sentence twice.
    """
    from personalclaw.knowledge_providers.base import HEALTH_NEEDS_RENDER
    from personalclaw.knowledge_providers.web_source import (
        LISTING_PAGE_GUIDANCE,
        RENDER_TIER_GUIDANCE,
    )

    health = str(source.get("health_status") or "")
    summary = str(source.get("last_error_summary") or "")
    allow_render = bool((source.get("budget") or {}).get("allow_render"))

    def _detail(guidance: str) -> str:
        return "" if summary and guidance.startswith(summary) else summary

    if health == HEALTH_NEEDS_RENDER:
        return {
            "kind": _REMEDIATION_RENDER_TIER,
            "guidance": RENDER_TIER_GUIDANCE,
            "detail": _detail(RENDER_TIER_GUIDANCE),
            # The knob is the fix only while it is OFF. Allowed-but-failing (a render that
            # raised, or the `js-render` extra not installed) is advice, not a button — and
            # a button that re-sets a flag already set is a lie about what would happen.
            "action": "" if allow_render else "allow_render",
        }
    if summary and LISTING_PAGE_GUIDANCE.startswith(summary):
        return {
            "kind": _REMEDIATION_LISTING_PAGE,
            "guidance": LISTING_PAGE_GUIDANCE,
            "detail": "",
            "action": "edit_url",
        }
    if summary and RENDER_TIER_GUIDANCE.startswith(summary):
        # The render tier was allowed and the budget ran out before it could be used, so the
        # health is a plain `degraded` — but the page still needs JavaScript, and raising
        # `budget.max_requests` is the fix rather than a different URL.
        return {
            "kind": _REMEDIATION_RENDER_TIER,
            "guidance": RENDER_TIER_GUIDANCE,
            "detail": "",
            "action": "" if allow_render else "allow_render",
        }
    return {"kind": "", "guidance": "", "detail": summary, "action": ""}


def _serialize_source(source: dict, enrolled: set[str]) -> dict:
    """A source row for the client: the stored row plus the three things it cannot derive.

    ``enrolled`` answers "will anything actually poll this?" BEFORE the first poll — the
    engine records the not-enrolled case as a health error, but only once it has run, and a
    row that has never been polled would otherwise read as healthy.

    ``event_driven`` is the honest answer for a source nothing polls BY DESIGN (PEP-7's
    ``artifact://`` mirror, which is fed by an in-process change listener). Without it that
    row reads "No provider · never polled · every 1h" — three true-of-a-poller statements
    that are all wrong about this one, and the loudest of them is a danger chip telling the
    user a working mechanism is broken. ``enrolled`` stays FALSE rather than being faked:
    nothing IS enrolled to poll it, and lying there would hide a genuinely orphaned row of
    some future kind.
    """
    return {
        **source,
        "enrolled": source.get("provider") in enrolled,
        "event_driven": source.get("provider") == ARTIFACT_SOURCE_PROVIDER,
        "remediation": _remediation(source),
    }


async def list_watched_sources(request: web.Request) -> web.Response:
    """GET /api/knowledge/sources — the watched sources, with health, plus the kind catalog.

    One route because the list page and the create page are one surface, and a second round
    trip to learn which kinds exist would just make the create form flash.
    """
    from personalclaw.knowledge_providers.base import ENRICHMENT_RAW, SOURCE_HEALTH

    kinds = _source_kinds()
    enrolled = {k["provider"] for k in kinds}
    return web.json_response(
        {
            "sources": [_serialize_source(s, enrolled) for s in _store(request).list_sources()],
            "kinds": kinds,
            # The closed vocabularies, shipped rather than retyped in TypeScript. The UI
            # needs a per-status label and tone, and a hardcoded list there would silently
            # fall through its default branch the day a sixth status is added.
            "health_statuses": sorted(SOURCE_HEALTH),
            "raw_enrichment": ENRICHMENT_RAW,
        }
    )


def _validated_spec(provider, spec: dict) -> str:
    """The provider's own verdict on a spec, or '' when it has none to give."""
    validate = getattr(provider, "validate_spec", None)
    if not callable(validate):
        return ""
    ok, err = validate(spec)
    return "" if ok else (err or "invalid spec")


async def create_watched_source(request: web.Request) -> web.Response:
    """POST /api/knowledge/sources — save a source, after its provider validates the spec.

    The provider must be registered and poll-capable: creating a row nothing polls is the
    inert-source failure this endpoint exists to end.
    """
    from personalclaw.knowledge_providers.base import ENRICHMENTS

    body = await request.json()
    name = str(body.get("name") or "").strip()
    provider_name = str(body.get("provider") or "").strip()
    if not name:
        return web.json_response({"error": "name is required"}, status=400)
    provider = next((p for p in _source_providers() if p.name == provider_name), None)
    if provider is None:
        known = ", ".join(sorted(p.name for p in _source_providers())) or "none registered"
        return web.json_response(
            {"error": f"unknown source provider {provider_name!r} (known: {known})"}, status=400
        )
    enrichment = str(body.get("enrichment") or "full")
    if enrichment not in ENRICHMENTS:
        return web.json_response(
            {"error": f"enrichment must be one of {sorted(ENRICHMENTS)}"}, status=400
        )
    descriptor = _kind_descriptor(provider)
    # The third enum on this body, checked like the two above it. Unvalidated it was the
    # only one that outlived the request: the store persists it on the row and every poll
    # hands it to `create_typed_item` (source_engine), so a typo here becomes a permanent
    # property of an unattended timer rather than one rejected call. Two steps, exactly as
    # /api/knowledge/items does: an unknown type is a typo (and keeps the synthesized kinds
    # -- `artifact` -- unauthorable through the API, which artifact_ingest relies on), while
    # a known media type is a knowledge type a POLL cannot produce: `SourceItem` carries no
    # bytes and the engine sets no `file_path`, so it would mint file-less items forever.
    item_type = str(body.get("item_type") or "").strip() or descriptor["default_item_type"]
    if item_type not in _KNOWLEDGE_TYPES:
        return web.json_response({"error": f"unknown type {item_type!r}"}, status=400)
    if item_type not in _AUTHORABLE_TYPES:
        return web.json_response(
            {
                "error": f"a watched source cannot poll '{item_type}' items; item_type must be one of {sorted(_AUTHORABLE_TYPES)}"  # noqa: E501
            },
            status=400,
        )
    spec = body.get("spec") if isinstance(body.get("spec"), dict) else {}
    err = _validated_spec(provider, spec)
    if err:
        return web.json_response({"error": err}, status=400)

    store = _store(request)
    sid = store.create_source(
        name=name,
        provider=provider.name,
        kind=str(body.get("kind") or descriptor["kind"]),
        spec=spec,
        enrichment=enrichment,
        poll_interval_secs=int(
            body.get("poll_interval_secs") or getattr(provider, "poll_interval_seconds", 3600)
        ),
        budget=body.get("budget") if isinstance(body.get("budget"), dict) else {},
        item_type=item_type,
    )
    _sel_log("sources.create", source_id=sid, provider=provider.name, enrichment=enrichment)
    created = store.get_source(sid)
    return web.json_response(
        {"source": _serialize_source(created or {}, {p.name for p in _source_providers()})},
        status=201,
    )


async def update_watched_source(request: web.Request) -> web.Response:
    """PATCH /api/knowledge/sources/{id} — apply a remediation, rename, or pause a source.

    This is what makes the guidance on a failing source ACTIONABLE rather than advisory:
    `needs render tier` is fixed by `budget.allow_render`, and the listing-page failure by
    a different `spec.url`. A spec edit is re-validated by the provider, exactly as a create
    is — an edit that could bypass the save-time guard would leave the poll-time
    re-validation as the only thing standing between a hand-edited row and an arbitrary
    fetch target on a timer.
    """
    from personalclaw.knowledge_providers.base import ENRICHMENTS

    store = _store(request)
    source_id = request.match_info["id"]
    current = store.get_source(source_id)
    if current is None:
        return web.json_response({"error": "not found"}, status=404)
    body = await request.json()

    fields: dict = {}
    if "name" in body:
        name = str(body.get("name") or "").strip()
        if not name:
            return web.json_response({"error": "name cannot be empty"}, status=400)
        fields["name"] = name
    if "enabled" in body:
        fields["enabled"] = bool(body["enabled"])
    if "enrichment" in body:
        enrichment = str(body.get("enrichment") or "")
        if enrichment not in ENRICHMENTS:
            return web.json_response(
                {"error": f"enrichment must be one of {sorted(ENRICHMENTS)}"}, status=400
            )
        fields["enrichment"] = enrichment
    if "poll_interval_secs" in body:
        try:
            interval = int(body["poll_interval_secs"])
        except (TypeError, ValueError):
            return web.json_response({"error": "poll_interval_secs must be an integer"}, status=400)
        if interval < 1:
            return web.json_response({"error": "poll_interval_secs must be positive"}, status=400)
        fields["poll_interval_secs"] = interval
    if "budget" in body:
        if not isinstance(body["budget"], dict):
            return web.json_response({"error": "budget must be an object"}, status=400)
        fields["budget"] = body["budget"]
    if "spec" in body:
        if not isinstance(body["spec"], dict):
            return web.json_response({"error": "spec must be an object"}, status=400)
        provider = next((p for p in _source_providers() if p.name == current["provider"]), None)
        if provider is None:
            return web.json_response(
                {"error": f"provider {current['provider']!r} is not registered"}, status=400
            )
        err = _validated_spec(provider, body["spec"])
        if err:
            return web.json_response({"error": err}, status=400)
        fields["spec"] = body["spec"]
    if not fields:
        return web.json_response({"error": "no editable fields in request"}, status=400)

    updated = store.update_source(source_id, **fields)
    if updated is None:
        return web.json_response({"error": "not found"}, status=404)
    _sel_log("sources.update", source_id=source_id, fields=sorted(fields))
    return web.json_response(
        {"source": _serialize_source(updated, {p.name for p in _source_providers()})}
    )


#: Preview item snippets are UNTRUSTED scraped bytes. They are clipped hard and rendered as
#: TEXT by the client (never as markup) — the preview's job is "would this spec find the
#: right things", which a headline and a link answer, and shipping a page's full body into a
#: create form would be carrying an injection payload for no product reason.
_PREVIEW_SNIPPET_CHARS = 240


def _snippet(content: str) -> str:
    """One preview item's body as plain, single-line text.

    An item's ``content`` is sanitized MARKUP (WS-3 moves ``sanitize_html`` onto exactly this
    field), and the client renders the snippet as text — correctly, since rendering scraped
    bytes as markup is the injection surface this whole path avoids. So the conversion has to
    happen HERE or every preview row reads ``<p>See how four…</p>`` with ``&#8217;`` for its
    apostrophes. Measured on ``github.blog/changelog`` before this: all 20 rows did.

    Converted through the app's ONE html→text seam (``connectors.base.html_to_text``, which is
    also what web-source's own ``html_to_markdown`` post-process calls), so a preview snippet
    and an ingested item read the same way rather than diverging on a second stripper.
    """
    from personalclaw.knowledge.connectors.base import html_to_text

    text = html_to_text(content) if "<" in (content or "") else (content or "")
    return " ".join(text.split())[:_PREVIEW_SNIPPET_CHARS]


async def preview_watched_source(request: web.Request) -> web.Response:
    """POST /api/knowledge/sources/preview — §2.4's dry run for the paste-URL create flow.

    Persists nothing (no item, no cursor, no seen-set row) and spends the spec's request
    budget, because it is a real fetch at somebody else's server. Only the web kind has a
    preview at all — see :func:`_source_kinds` — so a provider without one is refused with
    the reason rather than answered with an empty item list that reads like a failure.

    The egress posture is the ENGINE's (`SourceEngine.egress_policy`), not one resolved
    here: the preview fetches the same targets a poll does, and two postures for one act is
    how the tuning loop becomes the hole in the `SOURCE` profile.
    """
    from personalclaw.knowledge.source_engine import SourceEngine

    body = await request.json()
    provider_name = str(body.get("provider") or "").strip()
    provider = next((p for p in _source_providers() if p.name == provider_name), None)
    if provider is None:
        return web.json_response(
            {"error": f"unknown source provider {provider_name!r}"}, status=400
        )
    preview = getattr(provider, "preview", None)
    if not callable(preview):
        return web.json_response(
            {
                "error": (
                    f"{provider.display_name} has no preview — its first poll is its preview. "
                    "Save the source and check its health."
                )
            },
            status=400,
        )
    spec = body.get("spec") if isinstance(body.get("spec"), dict) else {}
    budget = body.get("budget") if isinstance(body.get("budget"), dict) else {}
    result = await preview(spec, budget=budget, policy=SourceEngine.egress_policy())
    _sel_log(
        "sources.preview",
        provider=provider.name,
        items=len(result.items),
        detector=result.detector,
        requests=result.requests_used,
        outcome="completed" if not result.error else "failed",
    )
    return web.json_response(
        {
            "items": [
                {
                    "guid": i.guid,
                    "title": i.title,
                    "url": i.url,
                    "published_at": i.published_at,
                    "snippet": _snippet(i.content),
                }
                for i in result.items
            ],
            "detector": result.detector,
            "escalations": result.escalations,
            "requests_used": result.requests_used,
            "guidance": result.guidance,
            "health_status": result.health_status,
            "error": result.error,
        }
    )


# -- Structural editing verbs (KL-19) --------------------------------------------
#
# These four handlers use the platform's NESTED error envelope
# (`{"error": {"code", "message"}}`) rather than this module's prevailing flat string, and
# that is deliberate rather than drift. The envelope is the declared contract for NEW routes
# (AGENTS.md §Shared conventions), and a two-phase flow specifically NEEDS the machine-readable
# `code`: the frontend has to distinguish "your preview went stale, here is the new one" from
# "that span is invalid" in order to re-render the preview instead of showing an error, and a
# flat sentence cannot carry that distinction. The neighbouring flat-string handlers are left
# exactly as they are — standardizing them retroactively is not this atom's business.

#: Refusals that are not the caller's malformed input. Everything else is a 400.
_RESTRUCTURE_STATUS = {
    "item_not_found": 404,
    "unknown_verb": 404,
    "preview_stale": 409,
    "unknown_undo_token": 409,
}


def _restructure_refusal(exc: "RestructureError") -> web.Response:
    """One refusal → the nested envelope, plus whatever context the verb attached.

    A `preview_stale` carries the FRESH plan alongside the error, copying the durability
    precedent: the client can re-render the new preview immediately instead of making a second
    round trip to discover what changed.
    """
    body: dict = {"error": {"code": exc.code, "message": exc.message}}
    body.update(exc.detail)
    return web.json_response(body, status=_RESTRUCTURE_STATUS.get(exc.code, 400))


async def _restructure_body(request: web.Request) -> tuple[dict, web.Response | None]:
    try:
        body = await request.json()
    except Exception:
        return {}, web.json_response(
            {"error": {"code": "bad_body", "message": "body must be JSON"}}, status=400
        )
    if not isinstance(body, dict):
        return {}, web.json_response(
            {"error": {"code": "bad_body", "message": "body must be an object"}}, status=400
        )
    return body, None


async def get_item_sections(request: web.Request) -> web.Response:
    """GET /api/knowledge/items/{id}/sections — the section boundaries a split may cut on.

    Served from the same heading rule the chunker sections on, so the outline a reader picks a
    split point from is the outline the halves will actually be re-chunked along.
    """
    from personalclaw.knowledge import restructure

    store = _store(request)
    item = store.get_item(request.match_info["id"])
    if not item:
        return web.json_response(
            {"error": {"code": "item_not_found", "message": "no such item"}}, status=404
        )
    content = str(item.get("content") or "")
    return web.json_response({"sections": restructure.sections(content), "length": len(content)})


async def restructure_item(request: web.Request) -> web.Response:
    """POST /api/knowledge/items/{id}/restructure/{verb} — preview, then apply, a restructure.

    Without ``confirm`` this returns the PREVIEW and touches nothing: what the verb would do,
    which inbound references it would break, whether the store can relink them, and the
    ``token`` a confirm must echo. With ``confirm: true`` it requires that token to still be
    the one a fresh preview would issue, so a preview the user read cannot be applied to an
    item that moved underneath it (409 ``preview_stale``, with the new preview attached).

    ``relink: false`` declines the repair the preview offered — the break then simply happens,
    which is a choice a user may legitimately make and the reason the offer is an offer.
    Re-submitting the same token is idempotent: it replays the first application's result.
    """
    from personalclaw.knowledge import restructure

    store = _store(request)
    item_id = request.match_info["id"]
    verb = str(request.match_info.get("verb") or "").strip()
    body, err = await _restructure_body(request)
    if err is not None:
        return err
    params = body.get("params")
    if params is None:
        # The verb's arguments may be sent flat for convenience; `params` is the explicit form.
        params = {k: v for k, v in body.items() if k not in ("confirm", "token", "relink")}
    if not isinstance(params, dict):
        return web.json_response(
            {"error": {"code": "bad_body", "message": "params must be an object"}}, status=400
        )
    try:
        plan = restructure.plan(store, verb, item_id, params)
    except restructure.RestructureError as exc:
        return _restructure_refusal(exc)

    if not body.get("confirm"):
        return web.json_response({"confirmed": False, "token": plan.token, "plan": plan.to_dict()})

    try:
        result = restructure.apply(
            store,
            verb,
            item_id,
            params,
            token=str(body.get("token") or ""),
            relink=body.get("relink", True) is not False,
        )
    except restructure.RestructureError as exc:
        _sel_log("restructure", verb=verb, item_id=item_id, outcome="denied")
        return _restructure_refusal(exc)
    _sel_log("restructure", verb=verb, item_id=item_id, outcome="completed")
    return web.json_response({"ok": True, "confirmed": True, **result})


async def undo_restructure(request: web.Request) -> web.Response:
    """POST /api/knowledge/restructure/undo — reverse one applied restructure.

    Restores the prior state of every item the verb touched, RELATIONS INCLUDED, and then
    re-invalidates the derived layer so the restored bodies are re-chunked and re-embedded
    rather than keeping vectors computed against text that has just been rolled back.
    """
    from personalclaw.knowledge import restructure

    body, err = await _restructure_body(request)
    if err is not None:
        return err
    token = str(body.get("token") or "").strip()
    if not token:
        return web.json_response(
            {"error": {"code": "token_required", "message": "supply the token to undo"}},
            status=400,
        )
    try:
        result = restructure.undo(_store(request), token)
    except restructure.RestructureError as exc:
        _sel_log("restructure_undo", outcome="denied")
        return _restructure_refusal(exc)
    _sel_log("restructure_undo", verb=result["verb"], item_id=result["item_id"])
    return web.json_response(result)


async def list_restructure_undo(request: web.Request) -> web.Response:
    """GET /api/knowledge/restructure/undo — restructures that are still reversible.

    The apply response carries its own undo token, but a reader who navigates away or reloads
    has nowhere to get it back from — and an undo the user cannot find is not an undo they can
    rely on before a destructive restructure.
    """
    return web.json_response({"undoable": _store(request).list_undo()})


def setup_knowledge_routes(app: web.Application) -> None:
    # One ingestion path: the node-graph queue. Every item (typed-create, file
    # upload, bookmark) is created via the native provider and enqueued here;
    # there is no separate connector/sync/chunk pipeline.
    if "knowledge_llm_pool" not in app:
        pool = LLMPool()
        app["knowledge_llm_pool"] = pool
        app["knowledge_embedder"] = _create_embedder(app)
        # Wire the node-graph ingest queue's insights pool to the shared LLM pool,
        # and (re)start the queue now that the event loop is running (#30).
        try:
            queue = app["state"].knowledge_ingest_queue()
            queue._insights_pool = pool
            queue.start()
            app["state"].knowledge_provider()  # register native provider
        except Exception:
            logger.debug("knowledge ingest queue wiring skipped", exc_info=True)

    app.router.add_get("/api/knowledge/library-home", library_home)
    app.router.add_get("/api/knowledge/collections", list_collections)
    app.router.add_post("/api/knowledge/collections", create_collection)
    app.router.add_get("/api/knowledge/collections/{id}/items", get_collection_items)
    app.router.add_post("/api/knowledge/collections/{id}/items", add_collection_items)
    app.router.add_delete("/api/knowledge/collections/{id}/items/{item_id}", remove_collection_item)
    app.router.add_patch("/api/knowledge/collections/{id}", update_collection)
    app.router.add_delete("/api/knowledge/collections/{id}", delete_collection)
    app.router.add_post("/api/knowledge/items/{id}/read-state", set_item_read_state)
    app.router.add_post("/api/knowledge/items/{id}/favorite", set_item_favorited)
    app.router.add_post("/api/knowledge/bulk", bulk_items)
    # Tag taxonomy (S2 T2.2). `/tag-tree` rather than `/tags` because the flat
    # `GET /tags` list[str] contract is consumed by the ChipInput autocomplete and by
    # the agent tools — it stays exactly as it is.
    app.router.add_get("/api/knowledge/tag-tree", list_tag_taxonomy)
    # KNOWLEDGE-SYNTHESIS §3.2: contradictions are flagged at ingest and both claims kept, so the
    # flag needs a place a human can see it. Read-only by design — deciding which source to trust
    # is the owner's judgement, not something an endpoint should let the system settle.
    app.router.add_get("/api/knowledge/conflicts", list_conflicts)
    app.router.add_get("/api/knowledge/items/{id}/relations", list_item_relations)
    app.router.add_get("/api/knowledge/items/{id}/staleness", get_item_staleness)
    # KL-19 — the structural editing verbs. Registered BEFORE the `/items/{id}` catch-alls for
    # readability only; aiohttp matches on the full path, so order does not disambiguate them.
    # The undo pair is a sibling collection rather than `/items/{id}/undo`, because an undo may
    # have to resurrect an item the merge deleted — there is no live `{id}` to hang it off.
    app.router.add_get("/api/knowledge/restructure/undo", list_restructure_undo)
    app.router.add_post("/api/knowledge/restructure/undo", undo_restructure)
    app.router.add_get("/api/knowledge/items/{id}/sections", get_item_sections)
    app.router.add_post("/api/knowledge/items/{id}/restructure/{verb}", restructure_item)
    app.router.add_post("/api/knowledge/items/{id}/regenerate", regenerate_item)
    app.router.add_patch("/api/knowledge/tags/{id}", rename_tag)
    app.router.add_post("/api/knowledge/tags/{id}/merge", merge_tag)
    app.router.add_delete("/api/knowledge/tags/{id}", delete_tag)
    app.router.add_get("/api/knowledge/items", list_items)
    app.router.add_post("/api/knowledge/items", create_item)
    app.router.add_get("/api/knowledge/tags", list_tags)
    app.router.add_get("/api/knowledge/providers", list_providers)
    # WATCHED-SOURCES §7.2: the recipe directory the create flow consults before anyone tunes
    # a selector. Read-only shipped data, so no auth beyond the gateway's own.
    app.router.add_get("/api/knowledge/source-recipes", list_source_recipes)
    app.router.add_get("/api/knowledge/stats", get_stats)
    app.router.add_get("/api/knowledge/entities", list_entities)
    app.router.add_get("/api/knowledge/graph", get_full_graph)
    app.router.add_post("/api/knowledge/ingest", ingest_file)
    app.router.add_get("/api/knowledge/items/{id}", get_item)
    app.router.add_patch("/api/knowledge/items/{id}", update_item)
    app.router.add_delete("/api/knowledge/items/{id}", delete_item)
    app.router.add_get("/api/knowledge/items/{id}/content", get_item_content)
    app.router.add_post("/api/knowledge/items/{id}/generate-intelligence", generate_intelligence)
    app.router.add_post("/api/knowledge/regenerate-intelligence", regenerate_intelligence)
    app.router.add_get("/api/knowledge/items/{id}/file", get_item_file)
    app.router.add_get("/api/knowledge/items/{id}/thumbnail", get_item_thumbnail)
    app.router.add_get("/api/knowledge/items/{id}/extracted", get_extracted_contents)
    app.router.add_get("/api/knowledge/items/{id}/graph", get_item_graph)
    app.router.add_get("/api/knowledge/items/{id}/ingest/stream", stream_item_ingest)
    app.router.add_get("/api/knowledge/intents", list_intents)
    app.router.add_post("/api/knowledge/intents", upsert_intent)
    app.router.add_delete("/api/knowledge/intents/{id}", delete_intent)
    app.router.add_get("/api/knowledge/intents/{id}/outcomes", list_intent_outcomes)
    app.router.add_post("/api/knowledge/intents/{id}/run", run_intent)
    app.router.add_post("/api/knowledge/intents/{id}/generate-skill", generate_skill_from_intent)
    app.router.add_get("/api/knowledge/items/{id}/intents", list_item_intents)
    app.router.add_get("/api/knowledge/items/{id}/related", get_related_items)
    app.router.add_get("/api/knowledge/items/{id}/duplicates", get_item_duplicates)
    app.router.add_post("/api/knowledge/items/{id}/merge", merge_items)
    # Reading highlights (S3 T3.1). Listing/creating is per-item; deleting is keyed by the
    # highlight's own id, so `/annotations/{id}` is a sibling of `/items`, not nested.
    app.router.add_get("/api/knowledge/items/{id}/annotations", list_item_annotations)
    app.router.add_post("/api/knowledge/items/{id}/annotations", add_item_annotation)
    app.router.add_delete("/api/knowledge/annotations/{id}", delete_item_annotation)
    app.router.add_get("/api/knowledge/entities/by-name/{name}/items", get_entity_items)
    app.router.add_get("/api/knowledge/entities/by-name/{name}/related", get_entity_related)
    app.router.add_get("/api/knowledge/entities/{id}/graph", get_entity_graph)
    app.router.add_get("/api/knowledge/embedding/status", get_embedding_status)
    app.router.add_post("/api/knowledge/embedding/generate", batch_embed_items)
    app.router.add_get("/api/knowledge/search-for-context", search_for_context)
    # WATCHED-SOURCES §2.4/§6.3/§12 (WS-9): the create/tune/inspect surface. `/preview` is
    # registered before `/{id}` for legibility only — they differ by method, so aiohttp
    # could not confuse them either way.
    app.router.add_get("/api/knowledge/sources", list_watched_sources)
    app.router.add_post("/api/knowledge/sources", create_watched_source)
    app.router.add_post("/api/knowledge/sources/preview", preview_watched_source)
    app.router.add_patch("/api/knowledge/sources/{id}", update_watched_source)

    # Pool lifecycle: lazy start on first request, shutdown on app exit
    async def _shutdown_pool(app: web.Application) -> None:
        pool = app.get("knowledge_llm_pool")
        if pool:
            await pool.shutdown()

    app.on_cleanup.append(_shutdown_pool)

"""Knowledge Library API handlers."""

import asyncio
import json
import logging
import re
import shutil
import tempfile
from pathlib import Path
from uuid import uuid4

from aiohttp import web

from personalclaw.knowledge.embedder import create_embedder_from_config, floats_to_bytes
from personalclaw.knowledge.llm_pool import LLMPool
from personalclaw.knowledge.media import classify, guess_mime, make_image_thumbnail
from personalclaw.knowledge.retrieval import HybridRetriever
from personalclaw.dashboard.sse import stream_response
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
        session_key="dashboard", agent="knowledge-api",
        tool_name=f"knowledge.{tool}", outcome=str(kwargs.pop("outcome", "completed")),
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
        all_results = retriever.search(q, limit=limit * 3, include_archived=include_archived)  # over-fetch to allow filtering
        # Batch fetch all candidate items (avoid N+1)
        result_ids = [r["id"] for r in all_results]
        if result_ids:
            placeholders = ",".join("?" * len(result_ids))
            rows = store.db.execute(
                f"SELECT * FROM items WHERE id IN ({placeholders})",  # noqa: S608
                result_ids
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
            if provider and (item.get("provider") or "native") != provider:
                continue
            item["_score"] = r["score"]
            item["_match_type"] = r["match_type"]
            filtered.append(item)
        total = len(filtered)
        offset = (page - 1) * limit
        items = filtered[offset:offset + limit]
        return web.json_response({"items": items, "total": total, "page": page, "limit": limit})
    else:
        where, params = ["1=1"], []  # type: list[str], list[object]
        if item_type:
            where.append("i.item_type = ?")
            params.append(item_type)
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
        where_clause = ' AND '.join(where)
        total = store.db.execute(
            f"SELECT COUNT(*) FROM items i WHERE {where_clause}",  # noqa: S608
            params).fetchone()[0]
        offset = (page - 1) * limit
        # Pinned items float to top; then most-recently-updated. (Native items have
        # no source row, so order by the item's own timestamp, not the source's.)
        rows = store.db.execute(
            f"SELECT i.* FROM items i WHERE {where_clause} ORDER BY COALESCE(i.is_pinned, 0) DESC, i.updated_at DESC LIMIT ? OFFSET ?",  # noqa: S608, E501
            [*params, limit, offset]).fetchall()
        items = [_list_item(store, r) for r in rows]
        return web.json_response({"items": items, "total": total, "page": page, "limit": limit})


# The 12 typed item kinds (knowledge-entity-vision). text-ish types author content
# directly; bookmark records a url; media types arrive via /ingest (file upload).
_KNOWLEDGE_TYPES = {
    "note", "fleeting", "journal", "gist", "bookmark",
    "image", "audio", "video", "pdf", "document", "sheet", "slides",
}
# Types authorable via JSON create (text bodies + a bookmark URL). Media/document
# types carry file bytes, so they can ONLY be created through /ingest — creating one
# here would yield a broken item with no file.
_AUTHORABLE_TYPES = {"note", "fleeting", "journal", "gist", "bookmark"}


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
        return web.json_response(
            {"error": f"'{item_type}' items are created by uploading a file to /ingest, not authored directly"},
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
                {"error": "bookmark url must be an http(s) web address"}, status=400)
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
        item_type=item_type, title=title, content=content, tags=tags, url=url,
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
    where, params = ["status = 'active'", "COALESCE(is_archived, 0) = 0"], []  # type: list[str], list[object]
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


def _store_file_item(store, tmp_path: str, filename: str, mime: str | None = None) -> dict:
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
            item_type="gist", title=filename, content=code,
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
        item_type=item_type, title=filename, content="",
        extra={
            "file_path": str(dest), "mime_type": mime_type,
            "file_size": size, "thumbnail_path": thumb_path,
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
    if stored and stored.split("/", 1)[0] in ("audio", "video") and stored.split("/", 1)[0] != mime.split("/", 1)[0]:
        mime = stored
    return resolved, mime


async def get_item_file(request: web.Request) -> web.Response:
    """GET /api/knowledge/items/{id}/file -- serve a media item's original bytes."""
    path, mime = _serve_item_path(_store(request), request.match_info["id"], thumbnail=False)
    if path is None:
        return web.json_response({"error": "not found"}, status=404)
    return web.FileResponse(path, headers={"Content-Type": mime})


async def get_item_thumbnail(request: web.Request) -> web.Response:
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


async def get_item(request: web.Request) -> web.Response:
    """GET /api/knowledge/items/{id} -- single item with its entities + relations."""
    store = _store(request)
    item_id = request.match_info["id"]
    item = store.get_item(item_id)
    if not item:
        return web.json_response({"error": "not found"}, status=404)

    mentions = store.db.execute("SELECT entity_id, context FROM mentions WHERE item_id = ?", (item_id,)).fetchall()
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
                "SELECT * FROM entity_relations WHERE source_id = ? OR target_id = ?", (eid, eid)):
            r = dict(row)
            if r["id"] not in seen_ids:
                seen_ids.add(r["id"])
                # Resolve entity names for display
                src = store.db.execute("SELECT name FROM entities WHERE id = ?", (r["source_id"],)).fetchone()
                tgt = store.db.execute("SELECT name FROM entities WHERE id = ?", (r["target_id"],)).fetchone()
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
        "tags", "item_type", "status", "title", "summary", "content",
        "url", "url_title", "url_description", "is_pinned", "is_archived", "gist_language",
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
            return web.json_response(
                {"error": "url must be an http(s) web address"}, status=400)
    # Journal immutability (knowledge-entity vision): a journal is an append-only
    # record — its body can be edited on its creation day, but not after. Reject a
    # content/title edit to a journal whose creation day has passed. Pin/archive/tags
    # (curation metadata, not the record itself) stay editable.
    if (existing.get("item_type") or existing.get("type")) == "journal" and ("content" in fields or "title" in fields):
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
        f"SELECT * FROM entities WHERE {' AND '.join(where)} ORDER BY name LIMIT ?", params).fetchall()  # noqa: S608
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
        other = store.db.execute("SELECT name, entity_type FROM entities WHERE id = ?", (other_id,)).fetchone()
        if not other:
            continue
        key = (other_id, d.get("relation_type"), outgoing)
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "name": other["name"], "entity_type": other["entity_type"],
            "relation_type": d.get("relation_type") or "related", "outgoing": outgoing,
        })
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


async def get_related_items(request: web.Request) -> web.Response:
    """GET /api/knowledge/items/{id}/related -- items sharing entities with given item."""
    store = _store(request)
    item_id = request.match_info["id"]
    try:
        limit = min(20, max(1, int(request.query.get("limit", 8) or 8)))
    except ValueError:
        return web.json_response({"error": "invalid limit"}, status=400)

    # Find entities mentioned in this item
    entity_ids = [r["entity_id"] for r in store.db.execute(
        "SELECT entity_id FROM mentions WHERE item_id = ?", (item_id,)).fetchall()]
    if not entity_ids:
        return web.json_response([])

    # Find other items that mention the same entities, ranked by overlap count
    placeholders = ",".join("?" * len(entity_ids))
    rows = store.db.execute(
        f"SELECT i.*, COUNT(DISTINCT m.entity_id) as shared_entities "  # noqa: S608
        f"FROM items i JOIN mentions m ON i.id = m.item_id "
        f"WHERE m.entity_id IN ({placeholders}) AND i.id != ? AND i.status = 'active' "
        f"AND COALESCE(i.is_archived, 0) = 0 "
        f"GROUP BY i.id ORDER BY shared_entities DESC LIMIT ?",
        [*entity_ids, item_id, limit]
    ).fetchall()
    return web.json_response([{**store._serialize_item(r), "shared_entities": r["shared_entities"]} for r in rows])


async def get_full_graph(request: web.Request) -> web.Response:
    """GET /api/knowledge/graph -- full entity graph (top N by connections)."""
    store = _store(request)
    try:
        limit = min(200, max(1, int(request.query.get("limit", 100) or 100)))
    except ValueError:
        return web.json_response({"error": "invalid limit"}, status=400)
    nodes_by_degree = sorted(store.graph.nodes, key=lambda n: store.graph.degree(n), reverse=True)[:limit]
    if not nodes_by_degree:
        return web.json_response({"nodes": [], "edges": []})
    node_set = set(nodes_by_degree)
    nodes = [{"id": n, "name": store.graph.nodes[n].get("name"), "type": store.graph.nodes[n].get("entity_type")}
             for n in node_set]
    edges = [{"source": u, "target": v, "type": d.get("relation_type"), "weight": d.get("weight")}
             for u, v, d in store.graph.edges(data=True) if u in node_set and v in node_set]
    return web.json_response({"nodes": nodes, "edges": edges})


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
        embedded_count = store.db.execute("SELECT COUNT(*) FROM items WHERE embedding IS NOT NULL").fetchone()[0]
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
    if not field or not hasattr(field, "read_chunk") or field.name != "file":  # type: ignore[union-attr]
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
                    {"error": check_upload(filename, upload_mime, size=total_size).reason}, status=413)
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
        if is_new:
            # Only a freshly-stored file needs ingestion; a dedup hit is already enriched.
            try:
                request.app["state"].knowledge_ingest_queue().enqueue(item["id"])
            except Exception:
                logger.debug("file enqueue failed for %s", item["id"], exc_info=True)
        _sel_log("ingest", filename=filename, item_id=item["id"], deduped=not is_new)
        return web.json_response({
            "item_id": item["id"], "type": item["type"],
            "status": "processing" if is_new else (item.get("processing_status") or "done"),
            "deduped": not is_new,
        })
    except Exception:
        logger.exception("Ingestion failed for %s", filename)
        Path(tmp.name).unlink(missing_ok=True)
        return web.json_response({"error": "internal server error"}, status=500)


# ---------- Route registration ----------


async def get_embedding_status(request: web.Request) -> web.Response:
    """GET /api/knowledge/embedding/status -- embedding config and progress."""
    store = _store(request)
    embedder = _get_embedder(request)
    total = store.db.execute(
        "SELECT COUNT(*) as c FROM items WHERE status = 'active'"
    ).fetchone()["c"]
    embedded = store.db.execute(
        "SELECT COUNT(*) as c FROM items WHERE status = 'active' AND embedding IS NOT NULL"
    ).fetchone()["c"]
    # Stale-model items count as 'embedded' but are vector-dead until re-embedded (their
    # vector dimension != the active model's; retrieval skips mismatches). Surface the
    # count so the UI can prompt a re-embed after a model switch.
    return web.json_response({
        "enabled": embedder is not None,
        "available": embedder.is_available() if embedder else False,
        # UnifiedEmbedder exposes model_name (the active embedding selection), not
        # a `.model` attribute — same fix as get_stats (split-era embedder unify).
        "model": embedder.model_name if embedder else None,
        "total_items": total,
        "embedded_items": embedded,
        "stale_items": _stale_embedding_count(store, embedder),
    })


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
            "SELECT id, title, summary, content FROM items WHERE status = 'active' AND embedding IS NULL"
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
                "UPDATE items SET embedding = ? WHERE id = ?",
                (floats_to_bytes(vec), row["id"]))
            embedded += 1
        else:
            # Surface which item failed to embed — a silent skip here left items
            # permanently stale (embedded with an old-dimension vector) after a
            # model switch, with no signal to the user or logs about why.
            failed += 1
            logger.warning(
                "batch_embed: no vector for item %s (title=%r) — skipped",
                row["id"], (row["title"] or "")[:60],
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
        max_tokens = max(1, min(int(request.query.get("max_tokens", max_tokens)), _CONTEXT_MAX_TOKENS_CEILING))
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
        per_card_cap = max(1, remaining_budget // remaining_results) if remaining_results > 1 else remaining_budget
        allowed = min(remaining_budget, per_card_cap)
        if tokens > allowed:
            content = content[:allowed * 4]
            tokens = allowed
        cards.append({
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
        })
        total_tokens += tokens

    _sel_log("search_for_context", query=q, results=len(cards))
    return web.json_response({
        "query": q,
        "results": cards,
        "total_tokens": total_tokens,
        "max_tokens": max_tokens,
    })


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
    try:
        # The id is derived from the goal when absent (the user never types one) —
        # from_dict owns the slug, so a caller may send only {goal}.
        intent = Intent.from_dict(body)
        store = _intent_store(request)
        store.upsert(intent)
    except ValueError as e:
        return web.json_response({"error": str(e)}, status=400)
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
    return web.json_response({
        "intent": intent.to_dict(),
        "outcomes": _store(request).outcomes_for_intent(intent_id),
    })


async def list_item_intents(request: web.Request) -> web.Response:
    """GET /api/knowledge/items/{id}/intents -- the intents this item contributed to
    (bidirectional link from the item side)."""
    store = _store(request)
    item_id = request.match_info["id"]
    if not store.get_item(item_id):
        return web.json_response({"error": "not found"}, status=404)
    return web.json_response({"outcomes": store.outcomes_for_item(item_id)})


def _consolidated_text(store, item: dict) -> str:
    """Best available text for matching: pooled extracted contents, else item content."""
    parts = [ec.get("text") or "" for ec in store.get_extracted_contents(item["id"])]
    pooled = "\n\n".join(p for p in parts if p.strip())
    return pooled if pooled.strip() else (item.get("content") or "")


async def _run_intent_retroactive(app: web.Application, intent_id: str) -> dict:
    """Run one intent against every existing active item; record relevant outcomes.
    Returns {matched, new}: total items that matched, and how many were NEW matches
    (didn't already have an outcome for this intent) — so a re-run reports honestly."""
    from personalclaw.knowledge.intents import IntentStore, match_intent
    from pathlib import Path

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
    candidates = [it for it in candidates if intent.applies_to(it.get("item_type") or it.get("type") or "")]

    # Match items concurrently (bounded) instead of one sequential LLM call per item —
    # a retroactive run over a large library would otherwise be O(N) round-trips.
    sem = asyncio.Semaphore(6)

    async def _match(it: dict):
        async with sem:
            # raise_on_error: a model failure (cold pool, timeout) must be counted as
            # an error, not silently folded into "not relevant" → a misleading 0-match.
            return it, await match_intent(
                intent, _consolidated_text(store, it), pool=pool, raise_on_error=True,
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
            intent.id, intent_name=intent.goal, item_id=it["id"],
            item_title=it.get("title") or it.get("ai_title") or "",
            takeaway=match.takeaway, fields=match.fields,
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
    return web.json_response({
        # `recorded` kept as an alias of total matched (back-compat); `new`/`matched`
        # let the UI report new-vs-re-matched honestly on a re-run. `errors`/`evaluated`
        # distinguish "model couldn't evaluate" (e.g. cold pool) from "nothing matched".
        "recorded": counts["matched"], "matched": counts["matched"], "new": counts["new"],
        "errors": counts.get("errors", 0), "evaluated": counts.get("evaluated", 0),
        "outcomes": _store(request).outcomes_for_intent(intent_id),
    })


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
        out["procedure"] = resp[procs[-1].end():].strip()
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
            {"error": "Nothing gathered yet — run the intent over your items first, then generate a skill."},
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
            {"error": f"A skill for this intent already exists — find \"{_existing}\" under Skills.",
             "skill": _existing, "already_exists": True},
            status=409,
        )

    # Build a compact digest of what the intent has captured to ground the synthesis.
    lines = []
    for o in outcomes[:30]:
        flds = "; ".join(f"{f.get('name')}={f.get('value')}" for f in (o.get("fields") or [])[:6])
        lines.append(f"- {o.get('takeaway','')}" + (f" ({flds})" if flds else ""))
    digest = "\n".join(lines)
    # A delimited-section contract (not JSON): the procedure is multi-line markdown,
    # which an LLM routinely emits with raw newlines that break strict JSON parsing.
    # The instruction is the native-knowledge app's ``knowledge_skill_synthesis``
    # prompt (bindable in Settings → Prompts), rendered with the goal + digest.
    from personalclaw.prompt_providers.runtime import render_use_case_prompt

    prompt = render_use_case_prompt("knowledge_skill_synthesis", {
        "goal": intent.goal,
        "digest": digest,
    }) or ""
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
        return web.json_response({"error": "Could not synthesize a skill from the gathered outcomes."}, status=502)

    from datetime import datetime

    from personalclaw.skills.loader import AutoSkillProvenance
    now = datetime.now().isoformat()
    name = _loader.create_auto_skill(
        _slug,
        description=_redact(parts.get("description") or intent.goal)[:200],
        triggers=parts.get("triggers", ""),
        procedure_md=_redact(parts["procedure"]),
        provenance=AutoSkillProvenance(session_key=f"intent:{intent_id}", created_at=now),
    )
    if name is None:
        # Existence was pre-checked above, so this is an invalid slug or an oversized
        # procedure — a synthesis-quality problem, not a duplicate.
        return web.json_response(
            {"error": "Couldn't save the skill — the synthesized procedure was invalid or too large."},
            status=422,
        )
    _sel_log("intent.generate_skill", intent_id=intent_id, skill=name)
    return web.json_response({"skill": name, "description": parts.get("description", "")}, status=201)


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

    nodes = [{"node_type": ns.node_type, "backend": ns.backend, "model_backed": ns.uses_use_case is not None}
             for ns in g.nodes.values()]
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
        nodes.append({"node_type": stage, "backend": "", "model_backed": stage in ("insights", "entities", "intents"), "terminal": True})
        for p in prev_leaves:
            edges.append({"from": p, "to": stage})
        prev_leaves = [stage]
    # Ground-truth per-node phases persisted at ingest end (done/failed/skipped) — the
    # UI uses these on reload instead of reconstructing from processing_error, so a
    # skipped node reads as skipped (not falsely 'done'). Absent until first ingest.
    node_phases = (item.get("file_metadata") or {}).get("node_phases") or {}
    return web.json_response({
        "item_type": item_type, "nodes": nodes, "edges": edges,
        "processing_status": item.get("processing_status", ""),
        "node_phases": node_phases,
    })


async def stream_item_ingest(request: web.Request) -> web.Response:
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
    return await stream_response(request, registry.hub(feed), on_connect=snapshot,
                                 registry_evict=(registry, feed),
                                 close_after_connect=terminal)


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

    app.router.add_get("/api/knowledge/items", list_items)
    app.router.add_post("/api/knowledge/items", create_item)
    app.router.add_get("/api/knowledge/tags", list_tags)
    app.router.add_get("/api/knowledge/providers", list_providers)
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
    app.router.add_get("/api/knowledge/entities/by-name/{name}/items", get_entity_items)
    app.router.add_get("/api/knowledge/entities/by-name/{name}/related", get_entity_related)
    app.router.add_get("/api/knowledge/entities/{id}/graph", get_entity_graph)
    app.router.add_get("/api/knowledge/embedding/status", get_embedding_status)
    app.router.add_post("/api/knowledge/embedding/generate", batch_embed_items)
    app.router.add_get("/api/knowledge/search-for-context", search_for_context)

    # Pool lifecycle: lazy start on first request, shutdown on app exit
    async def _shutdown_pool(app: web.Application) -> None:
        pool = app.get("knowledge_llm_pool")
        if pool:
            await pool.shutdown()

    app.on_cleanup.append(_shutdown_pool)

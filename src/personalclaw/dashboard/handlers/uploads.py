"""Resumable upload HTTP handlers (``/api/uploads/*``).

The transport half of the large-file rethink: ``init`` validates the declared
file against the size policy up front, ``part`` streams fixed-size chunks to the
:class:`~personalclaw.uploads.store.UploadStore` (idempotent = resume), ``status``
reports what landed, and ``complete`` assembles + runs a bounded content scan then
hands the finished file to the SAME per-target finalize the single-POST paths use
(chat attachment / knowledge ingest / workspace) — no duplicate destination logic.

These routes live on a dedicated 2 GB sub-app (see server.py) so the main + API
apps keep a tight body ceiling; large media never touches them.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from aiohttp import web

from personalclaw.uploads.policy import limits_table, single_post_threshold
from personalclaw.uploads.store import UploadError, UploadStore

logger = logging.getLogger(__name__)

_VALID_TARGETS = ("attachment", "knowledge", "workspace")

# Bounded content scan at complete: media isn't grepped line-by-line (a 2 GB video
# won't be scanned for `rm -rf` economically), so scan only a head+tail window for
# text/code/archive-ish categories where injection/exfil patterns matter.
_SCAN_WINDOW = 256 * 1024
_SCANNABLE_CATEGORIES = {"document", "archive", "other"}


def _store(request: web.Request) -> UploadStore:
    st = request.app.get("upload_store")
    if st is None:
        # Sub-app + main app share the same DashboardState; root the store under
        # the same uploads dir the single-POST paths use.
        from personalclaw.dashboard.handlers.files import _UPLOAD_DIR

        st = UploadStore(Path(_UPLOAD_DIR) / ".parts")
        request.app["upload_store"] = st
    return st


async def api_uploads_limits(request: web.Request) -> web.Response:
    """GET /api/uploads/limits — per-category caps + the single-POST threshold, so
    the client can pre-check + choose single-POST vs chunked before uploading."""
    return web.json_response(
        {
            "limits": limits_table(),
            "single_post_threshold": single_post_threshold(),
        }
    )


async def api_uploads_init(request: web.Request) -> web.Response:
    """POST /api/uploads/init {filename, size, mime, target[, path]} → session."""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)

    filename = str(body.get("filename") or "").strip()
    if not filename:
        return web.json_response({"error": "filename required"}, status=400)
    _size = body.get("size")
    if _size is None:
        return web.json_response({"error": "size (bytes) required"}, status=400)
    try:
        size = int(_size)
    except (TypeError, ValueError):
        return web.json_response({"error": "size (bytes) required"}, status=400)
    mime = str(body.get("mime") or "") or None
    target = str(body.get("target") or "attachment")
    if target not in _VALID_TARGETS:
        return web.json_response({"error": f"target must be one of {_VALID_TARGETS}"}, status=400)

    # Workspace target: validate the destination dir NOW (same gate as single-POST).
    target_dir = ""
    if target == "workspace":
        from personalclaw.dashboard.handlers.files import _validate_dashboard_path

        target_dir = _validate_dashboard_path(str(body.get("path") or "")) or ""
        if not target_dir or not os.path.isdir(target_dir):
            return web.json_response({"error": "invalid or forbidden directory"}, status=400)

    try:
        sess = _store(request).init(
            filename=filename,
            size=size,
            mime=mime or "",
            target=target,
            target_dir=target_dir,
        )
    except UploadError as exc:
        return web.json_response({"error": exc.message}, status=exc.status)

    return web.json_response(
        {
            "uploadId": sess.id,
            "partSize": sess.part_size,
            "totalParts": sess.total_parts,
            "category": sess.category,
        }
    )


async def api_uploads_part(request: web.Request) -> web.Response:
    """PUT /api/uploads/{id}/part?index=N — stream one part to disk (idempotent)."""
    sid = request.match_info["id"]
    try:
        index = int(request.query.get("index", ""))
    except (TypeError, ValueError):
        return web.json_response({"error": "index query param required"}, status=400)

    store = _store(request)
    # The part body is the raw bytes (application/octet-stream) — stream request
    # content directly, no multipart overhead per part.
    try:
        sess = await store.write_part(sid, index, request.content)
    except UploadError as exc:
        return web.json_response({"error": exc.message}, status=exc.status)
    except Exception:
        logger.exception("write_part failed for %s idx=%s", sid, index)
        return web.json_response({"error": "failed to write part"}, status=500)

    return web.json_response(
        {
            "received": sess.received,
            "totalParts": sess.total_parts,
            "complete": store.is_complete(sess),
        }
    )


async def api_uploads_status(request: web.Request) -> web.Response:
    """GET /api/uploads/{id} — which parts landed (drives client resume)."""
    store = _store(request)
    try:
        sess = store.get(request.match_info["id"])
    except UploadError as exc:
        return web.json_response({"error": exc.message}, status=exc.status)
    return web.json_response(
        {
            "uploadId": sess.id,
            "filename": sess.filename,
            "size": sess.size,
            "received": sess.received,
            "totalParts": sess.total_parts,
            "complete": store.is_complete(sess),
            "completed": sess.completed,
        }
    )


async def api_uploads_complete(request: web.Request) -> web.Response:
    """POST /api/uploads/{id}/complete — assemble + scan + hand off to the target."""
    sid = request.match_info["id"]
    store = _store(request)
    try:
        final_path, sess = await store.assemble(sid)
    except UploadError as exc:
        return web.json_response({"error": exc.message}, status=exc.status)
    except Exception:
        logger.exception("assemble failed for %s", sid)
        return web.json_response({"error": "failed to assemble upload"}, status=500)

    # Bounded content scan (never load the whole file). Media is skipped — only
    # text-ish categories get the head/tail injection/exfil scan.
    scan_err = _bounded_scan(final_path, sess.category)
    if scan_err:
        store.cleanup(sid)
        return web.json_response({"error": scan_err}, status=422)

    try:
        result = await _finalize_target(request, sess, final_path)
    except UploadError as exc:
        store.cleanup(sid)
        return web.json_response({"error": exc.message}, status=exc.status)
    except Exception:
        logger.exception("finalize failed for %s target=%s", sid, sess.target)
        store.cleanup(sid)
        return web.json_response({"error": "failed to finalize upload"}, status=500)

    store.cleanup(sid)
    return web.json_response(result)


def _bounded_scan(path: Path, category: str) -> str | None:
    """Scan a head+tail window for injection/exfil/destructive patterns. Returns an
    error message if the content is dangerous, else None. Media categories skip the
    scan (a 2 GB video isn't grepped line-by-line).

    An uploaded file is UNTRUSTED content, so it gets BOTH scanner surfaces: ``script``
    (the destructive-script ruleset — curl|sh, base64|bash, rm -rf, the S3 skill-install
    gate) AND ``manifest`` (prose-injection + invisible-char rules, the S5 memory-write
    gate). Scanning only ``manifest`` (the old bug) let classic shell payloads through as
    CLEAN — the ``script`` ruleset is exactly the one that flags them."""
    if category not in _SCANNABLE_CATEGORIES:
        return None
    try:
        from personalclaw.supply_chain import SkillScanner, Verdict

        with open(path, "rb") as fh:
            head = fh.read(_SCAN_WINDOW)
            size = path.stat().st_size
            if size > 2 * _SCAN_WINDOW:
                fh.seek(size - _SCAN_WINDOW)
                tail = fh.read(_SCAN_WINDOW)
            else:
                tail = b""
        window = head + b"\n" + tail
        # A binary file (archive, or a mis-categorised binary) is meaningless to a
        # text scanner: its compressed/random bytes can't reveal an embedded payload
        # AND random byte runs false-positive on the DANGEROUS regexes (a /dev/urandom
        # zip tripped the scan). NUL bytes are the reliable binary signal — skip those.
        if b"\x00" in window:
            return None
        text = window.decode("utf-8", errors="replace")
        scanner = SkillScanner()
        for surface in ("script", "manifest"):
            if scanner.scan_text(text, surface=surface).verdict is Verdict.DANGEROUS:
                return "upload rejected: content failed the safety scan"
    except Exception:
        logger.debug("bounded scan errored (fail-open for non-scannable)", exc_info=True)
    return None


async def _finalize_target(request: web.Request, sess, final_path: Path) -> dict:
    """Hand the assembled file to the same finalize the single-POST path uses."""
    import shutil
    import uuid

    if sess.target == "attachment":
        from personalclaw.dashboard.attachment_extract import get_extractor
        from personalclaw.dashboard.handlers.files import _UPLOAD_DIR

        _UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        import re

        safe = re.sub(r"[^\w.\-]", "_", Path(sess.filename).name)
        dest = _UPLOAD_DIR / f"{uuid.uuid4().hex}_{safe}"
        shutil.move(str(final_path), str(dest))
        os.chmod(dest, 0o600)
        # Kick content extraction now (mirrors api_upload_file), so the attachment's
        # text is ready by the time the turn runs.
        try:
            import mimetypes as _mt

            get_extractor().start(str(dest), sess.mime or _mt.guess_type(str(dest))[0])
        except Exception:
            logger.debug("attachment extract kickoff failed", exc_info=True)
        return {"paths": [str(dest)]}

    if sess.target == "knowledge":
        from personalclaw.dashboard.handlers.knowledge import _store as _kn_store
        from personalclaw.dashboard.handlers.knowledge import _store_file_item
        from personalclaw.knowledge.media import classify

        if classify(sess.filename, sess.mime or None) is None:
            raise UploadError(f"unsupported file type: {sess.filename}", 415)
        store = _kn_store(request)
        item, is_new = _store_file_item(
            store, str(final_path), sess.filename, mime=sess.mime or None
        )
        if item is None:
            raise UploadError("failed to store item", 500)
        if is_new:
            try:
                request.app["state"].knowledge_ingest_queue().enqueue(item["id"])
            except Exception:
                logger.debug("knowledge enqueue failed for %s", item["id"], exc_info=True)
        return {
            "item_id": item["id"],
            "type": item["type"],
            "status": "processing" if is_new else (item.get("processing_status") or "done"),
            "deduped": not is_new,
        }

    if sess.target == "workspace":
        from personalclaw.dashboard.handlers.files import _validate_dashboard_path

        wdest = _validate_dashboard_path(os.path.join(sess.target_dir, Path(sess.filename).name))
        if not wdest:
            raise UploadError(f"forbidden filename: {sess.filename}", 400)
        if os.path.exists(wdest):
            raise UploadError(f"already exists: {sess.filename}", 409)
        shutil.move(str(final_path), wdest)
        return {"paths": [wdest]}

    raise UploadError(f"unknown target: {sess.target}", 400)

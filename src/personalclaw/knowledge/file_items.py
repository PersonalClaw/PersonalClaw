"""A file kept in the library: how a file becomes ONE Knowledge item, read by its kind's reader.

Every door that takes a file into the library comes through here. The Knowledge upload (sent in
one request, or a resumable upload when it completes) hands :func:`store_file_item` a file the
content scan (:mod:`personalclaw.uploads.content_scan`) has passed. A file dropped in the memory
vault's ``raw/`` folder has no request and no one to answer, so :func:`take_file` runs the same
checks the upload route runs before it (the kinds the library takes, the size the upload policy
allows each, the content scan of its bytes) and files each refusal as a failed item that says why.

Either way the file becomes one item of the kind ``media.classify`` names: a code file a ``gist``
whose content is its text (read here, by the rule every reader of a file as text shares, and
scanned here), anything else a file the library keeps under its own files folder, read when the
item is ingested by the graph for its kind (a document's reader, whose text the ingest scans; a
picture's, a recording's).
"""

from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from personalclaw.knowledge.media import classify, guess_mime, make_image_thumbnail


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


def _take_gist(tmp_path: str) -> tuple[str | None, str]:
    """Read an uploaded source file's code and its content hash, and remove the upload. The code
    is ``None`` when the file is not text (``knowledge.readers.file_text``): a file named as code
    whose bytes are binary is not code, and is not decoded into text."""
    from personalclaw.knowledge.readers import NotText, file_text

    code: str | None
    try:
        code = file_text(tmp_path)
    except NotText:
        code = None
    except OSError:
        code = ""
    content_hash = _hash_file(tmp_path)
    Path(tmp_path).unlink(missing_ok=True)
    return code, content_hash


def _take_file(tmp_path: str, dest: Path, thumb: Path | None) -> tuple[int, str, str]:
    """Move an upload to *dest* (a copy when it is on another disk), and make its thumbnail at
    *thumb* when one is asked for. Returns its size, its content hash and the thumbnail's path
    (``""`` when none was made)."""
    shutil.move(tmp_path, dest)
    made = thumb is not None and make_image_thumbnail(str(dest), str(thumb))
    return dest.stat().st_size, _hash_file(dest), str(thumb) if made else ""


def _discard_file(dest: Path, thumb_path: str) -> None:
    """Remove a stored upload that turned out to be a duplicate, and its thumbnail."""
    dest.unlink(missing_ok=True)
    if thumb_path:
        Path(thumb_path).unlink(missing_ok=True)


async def store_file_item(
    store, tmp_path: str, filename: str, mime: str | None = None, *, tags: list[str] | None = None
) -> tuple[dict | None, bool]:
    """Persist an uploaded file under the knowledge files dir as ONE logical-doc
    typed item (image/audio/video/pdf/document/sheet/slides) pointing at it (+ a
    thumbnail for images), queued for node-graph ingestion. One item = one file —
    document text extraction + chunking happen inside the graph/embedder, never as
    separate item rows. ``mime`` (the upload's content-type) disambiguates ambiguous
    extensions like .webm (a browser audio recording is audio/webm, not video). *tags*
    file the new item under those tags (a dedup hit keeps its own).

    What reads or writes the whole file (the move, which copies when the upload sits on another
    disk, the hash, the thumbnail, a gist's read) runs in a worker thread: on the event loop,
    hashing a 512 MB upload stopped every other request for 0.22 s. What decides the item (the
    duplicate check and the insert) stays on the loop, so two uploads of the same bytes that
    finish together still make one item."""
    from personalclaw.knowledge import knowledge_files_dir
    from personalclaw.knowledge.media import code_language

    item_type = classify(filename, mime) or "image"

    # A source-code upload is a gist (code), stored as a text-backed item whose content
    # IS the code — read inline, language stamped for syntax highlighting + the
    # "Gist · <Language>" label, routed through the passthrough graph (no file on disk,
    # one logical doc). Dedup on the content hash, same as binary files.
    lang = code_language(filename)
    if item_type == "gist" and lang:
        from personalclaw.uploads.content_scan import scan_text
        from personalclaw.uploads.store import UploadError

        code, content_hash = await asyncio.to_thread(_take_gist, tmp_path)
        if code is None:
            raise UploadError(f"{filename} is not a text file, so it cannot be kept as code", 415)
        # Its text is the item's content, read here, so it is scanned here: the scan of the
        # file's bytes skips a window that holds a stray NUL byte. Refused, it raises.
        await scan_text(code, surface="knowledge")
        if content_hash:
            existing = store.find_active_by_file_hash(content_hash)
            if existing:
                return existing, False
        new_id = store.create_typed_item(
            item_type="gist",
            title=filename,
            content=code,
            tags=tags,
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
    thumb = files_dir / f"{item_id}.thumb.webp" if item_type == "image" else None
    size, content_hash, thumb_path = await asyncio.to_thread(_take_file, tmp_path, dest, thumb)

    # Content-hash dedup: re-uploading byte-identical content into the same space
    # returns the existing item instead of a duplicate (the file analog of bookmark
    # URL dedup). Hash is stored in file_metadata so the check is exact, not by name.
    # Nothing is awaited between this check and the insert below.
    if content_hash:
        existing = store.find_active_by_file_hash(content_hash)
        if existing:
            # Drop the redundant copy we just saved.
            await asyncio.to_thread(_discard_file, dest, thumb_path)
            return existing, False  # (item, is_new) — dedup hit

    new_id = store.create_typed_item(
        item_type=item_type,
        title=filename,
        content="",
        tags=tags,
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


# ── a file nobody uploaded ───────────────────────────────────────────────────


@dataclass(frozen=True)
class Taken:
    """What :func:`take_file` made of a file.

    ``item`` is the item it is in the library (``is_new`` when it was made now and waits to be
    read; else an item that already holds the same bytes), or, when it was refused, the failed
    item saying why (``refused``, that item's status line). ``item`` is ``None`` for an empty
    file: there is nothing to take yet."""

    item: dict | None
    is_new: bool = False
    refused: str = ""


#: Read at a time while a file is copied to be scanned and kept.
_CHUNK = 1 << 20


def _copy_capped(path: Path, limit: int) -> str | None:
    """A private copy of the file at *path*, in a temporary file the caller removes, or ``None``
    when it holds more than *limit* bytes (it grew since it was measured). Opened without following
    a link put in its place since the folder was listed: what a link names is not taken."""
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with (
        os.fdopen(fd, "rb") as src,
        tempfile.NamedTemporaryFile(delete=False, suffix=path.suffix.lower(), prefix="kn_") as dst,
    ):
        total = 0
        while chunk := src.read(_CHUNK):
            total += len(chunk)
            if total > limit:
                break
            dst.write(chunk)
    if total > limit:
        Path(dst.name).unlink(missing_ok=True)
        return None
    return dst.name


def _refused(store, name: str, item_type: str, reason: str, tags: list[str] | None) -> Taken:
    """File a refused file as a failed item that says why: no text, no file, nothing to read. It
    records the refusal (``file_metadata.refused``), so a re-run of its ingest says why again
    (``pipeline.runner.ingest_item``), and each step of its graph says it does not run."""
    from personalclaw.knowledge.pipeline.runner import refused_phases

    item_id = store.create_typed_item(
        item_type=item_type,
        title=name,
        content="",
        tags=tags,
        extra={
            "processing_status": "failed",
            "processing_error": reason,
            "file_metadata": {"refused": reason, "node_phases": refused_phases(item_type, reason)},
        },
    )
    return Taken(store.get_item(item_id) if item_id else None, refused=reason)


async def take_file(store, path: Path, *, surface: str, tags: list[str] | None = None) -> Taken:
    """Take the file at *path* into the library as the Knowledge page takes an upload, for a door
    with no one to answer (*surface* names the door, for the security event log).

    The upload route's checks, in its order: an empty file has nothing to store (it is left
    where it is, and taken once it holds something); then the kinds the library takes
    (``media.classify``) and the size the upload policy allows each; then the content scan of its
    bytes (``scan_upload``); then :func:`store_file_item`, which reads a code file's text and
    scans that. What a check refuses, which an upload answers to the browser, is filed as a failed
    item that says why and keeps no text and no file (the scan's refusal in the words an item says
    when the scan withheld its file's text, ``content_scan.nothing_made``). The file at *path* is
    neither moved nor changed: a copy of it is what is scanned and kept."""
    from personalclaw.uploads import check_upload
    from personalclaw.uploads.content_scan import ContentRefused, nothing_made, scan_upload
    from personalclaw.uploads.store import UploadError

    name = path.name
    size = path.lstat().st_size
    if size == 0:
        return Taken(None)
    item_type = classify(name)
    if item_type is None:
        why = "Knowledge does not take this kind of file"
        return _refused(store, name, "document", nothing_made(why), tags)
    policy = check_upload(name, size=size)
    if not policy.ok:
        return _refused(store, name, item_type, nothing_made(policy.reason), tags)
    tmp = await asyncio.to_thread(_copy_capped, path, policy.limit)
    if tmp is None:
        too_large = check_upload(name, size=policy.limit + 1).reason
        return _refused(store, name, item_type, nothing_made(too_large), tags)
    try:
        await scan_upload(Path(tmp), policy.category, surface=surface)
        item, is_new = await store_file_item(store, tmp, name, tags=tags)
    except ContentRefused as exc:
        return _refused(store, name, item_type, exc.nothing_made, tags)
    except UploadError as exc:  # a file named as code whose bytes are not text
        return _refused(store, name, item_type, f"{exc.message}.", tags)
    finally:
        Path(tmp).unlink(missing_ok=True)
    return Taken(item, is_new)

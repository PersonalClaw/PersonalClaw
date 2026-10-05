"""A file kept in the library: how a file becomes ONE Knowledge item, read by its kind's reader.

Every door that takes a file into the library comes through here. The Knowledge upload (sent in
one request, or a resumable upload when it completes) hands :func:`store_file_item` a file the
content scan (:mod:`personalclaw.uploads.content_scan`) has passed. Two doors have no request and
no one to answer: a file dropped in the memory vault's ``raw/`` folder (:func:`take_file`) and a
file in a folder Knowledge watches (:func:`take_watched_file`). Each runs the checks the upload
route runs before it (the kinds the library takes, the size the upload policy allows each, the
content scan of its bytes) and files each refusal as a failed item that says why. Neither takes a
file that is still being written: it waits for a file to hold still (:func:`still`), and a copy
that changes while it is made is not kept.

Either way the file becomes one item of the kind ``media.classify`` names: a code file a ``gist``
whose content is its text (read here, by the rule every reader of a file as text shares, and
scanned here), anything else a file the library keeps under its own files folder, read when the
item is ingested by the graph for its kind (a document's reader, whose text the ingest scans; a
picture's, a recording's).
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import shutil
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from personalclaw.knowledge.media import classify, guess_mime, make_image_thumbnail

logger = logging.getLogger(__name__)


def _hash_file(path) -> str:
    """SHA-256 of a file's bytes (streamed), or '' on error. Used to dedup uploads."""
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


def _take_file(
    tmp_path: str, dest: Path, thumb: Path | None, content_hash: str = ""
) -> tuple[int, str, str]:
    """Move an upload to *dest* (a copy when it is on another disk), and make its thumbnail at
    *thumb* when one is asked for. Returns its size, its content hash (*content_hash* when the
    caller hashed the bytes as it copied them, else read here) and the thumbnail's path (``""``
    when none was made)."""
    shutil.move(tmp_path, dest)
    made = thumb is not None and make_image_thumbnail(str(dest), str(thumb))
    return dest.stat().st_size, content_hash or _hash_file(dest), str(thumb) if made else ""


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


#: How long a file must hold still before a door with no one to answer takes it: its size, its
#: modified time and its status-change time the same at the start and at the end of it. A file
#: still being copied in, or downloaded, moves within it, and taking it then would make an item
#: of the part written so far: it is left for the next pass. Two seconds is past the one-second
#: clock of the coarsest file system a home folder sits on, so a write within it always shows.
SETTLE_SECS = 2.0


def _stamp(st: os.stat_result) -> tuple[int, int, int]:
    """What shows that a file changed: its size, its modified time and its status-change time,
    which every write moves, even one followed by putting back an older modified time (a copy that
    keeps the original's date). Reading a file moves none of them. A change of the file's
    attributes alone moves the last too, and only delays the file by one pass: the pass that saw
    it changing took nothing."""
    return (st.st_size, st.st_mtime_ns, st.st_ctime_ns)


def _stamps(paths: Sequence[Path]) -> list[tuple[int, int, int] | None]:
    """Each file's :func:`_stamp`, not followed through a link; ``None`` for one that is gone."""
    out: list[tuple[int, int, int] | None] = []
    for path in paths:
        try:
            out.append(_stamp(path.lstat()))
        except OSError:
            out.append(None)
    return out


async def _settle() -> None:
    """Wait out one settle window (:data:`SETTLE_SECS`)."""
    await asyncio.sleep(SETTLE_SECS)


async def still(paths: Sequence[Path]) -> list[Path]:
    """The files among *paths* that hold still across one settle window (:data:`SETTLE_SECS`), in
    their order: one window for all of them, so a pass over many files waits once. A file whose
    size or times moved within it is still being written, and is left for the next pass, as is
    one that is gone."""
    if not paths:
        return []
    before = await asyncio.to_thread(_stamps, paths)
    await _settle()
    after = await asyncio.to_thread(_stamps, paths)
    return [path for path, was, now in zip(paths, before, after) if was is not None and was == now]


@dataclass(frozen=True)
class Taken:
    """What :func:`take_file` or :func:`take_watched_file` made of a file.

    ``item`` is the item it is in the library (``is_new`` when it holds what was taken now and
    waits to be read; else an item that already holds the same bytes), or, when it was refused,
    the failed item saying why (``refused``, that item's status line). ``item`` is ``None`` when
    there is nothing to take yet: an empty file, or one that changed while it was copied
    (``changing``: it is still being written, and is taken at the next pass)."""

    item: dict | None
    is_new: bool = False
    refused: str = ""
    changing: bool = False


#: Read at a time while a file is copied to be scanned and kept.
_CHUNK = 1 << 20


class _Changed(Exception):
    """The file changed while it was copied: it is still being written."""


def _written(st: os.stat_result) -> tuple[int, int]:
    """What a write to a file moves: its size and its modified time. Its status-change time is
    left out here: a change of the file's attributes alone moves only that, and leaves its bytes
    as they were (and a folder that watches the file sees no change after it to take it again)."""
    return (st.st_size, st.st_mtime_ns)


def _copy_capped(path: Path, limit: int) -> tuple[str, str] | None:
    """A private copy of the file at *path*, in a temporary file the caller removes, and the
    SHA-256 of its bytes; ``None`` when it holds more than *limit* bytes (it grew since it was
    measured). Opened without following a link put in its place since the folder was listed: what
    a link names is not taken. Raises :class:`_Changed` when the file was written to while it was
    copied (:func:`_written`): the copy would be of a file written part-way."""
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    digest = hashlib.sha256()
    with (
        os.fdopen(fd, "rb") as src,
        tempfile.NamedTemporaryFile(delete=False, suffix=path.suffix.lower(), prefix="kn_") as dst,
    ):
        before = _written(os.fstat(src.fileno()))
        total = 0
        while chunk := src.read(_CHUNK):
            total += len(chunk)
            if total > limit:
                break
            dst.write(chunk)
            digest.update(chunk)
        changed = _written(os.fstat(src.fileno())) != before
    if changed or total > limit:
        Path(dst.name).unlink(missing_ok=True)
        if changed:
            raise _Changed
        return None
    return dst.name, digest.hexdigest()


@dataclass(frozen=True)
class _Checked:
    """What the upload route's checks made of a file nobody uploaded (:func:`_check`): the private
    ``copy`` they passed (the caller removes it), with its kind and the hash of its bytes; or why
    they refused it (``refused``, the status line of the item that says so); or neither, when there
    is nothing to take yet (an empty file, or one ``changing`` while it was copied)."""

    item_type: str = ""
    copy: str = ""
    content_hash: str = ""
    refused: str = ""
    changing: bool = False


async def _check(path: Path, *, surface: str) -> _Checked:
    """The upload route's checks of the file at *path*, in its order, for a door with no one to
    answer (*surface* names the door, for the security event log): an empty file has nothing to
    store; then the kinds the library takes (``media.classify``) and the size the upload policy
    allows each; then the content scan of a private copy of its bytes (``scan_upload``). The file
    at *path* is neither moved nor changed: the copy is what is scanned and kept."""
    from personalclaw.uploads.content_scan import ContentRefused, nothing_made, scan_upload
    from personalclaw.uploads.policy import check_upload

    name = path.name
    size = path.lstat().st_size
    if size == 0:
        return _Checked()
    item_type = classify(name)
    if item_type is None:
        why = "Knowledge does not take this kind of file"
        return _Checked("document", refused=nothing_made(why))
    policy = check_upload(name, size=size)
    if not policy.ok:
        return _Checked(item_type, refused=nothing_made(policy.reason))
    try:
        copied = await asyncio.to_thread(_copy_capped, path, policy.limit)
    except _Changed:
        return _Checked(item_type, changing=True)
    if copied is None:
        too_large = check_upload(name, size=policy.limit + 1).reason
        return _Checked(item_type, refused=nothing_made(too_large))
    copy, content_hash = copied
    try:
        await scan_upload(Path(copy), policy.category, surface=surface)
    except ContentRefused as exc:
        Path(copy).unlink(missing_ok=True)
        return _Checked(item_type, refused=exc.nothing_made)
    except BaseException:
        Path(copy).unlink(missing_ok=True)
        raise
    return _Checked(item_type, copy, content_hash)


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

    The upload route's checks (:func:`_check`), then :func:`store_file_item`, which reads a code
    file's text and scans that. An empty file is left where it is, and taken once it holds
    something; one that changed while it was copied is taken at the next pass (``changing``). What
    a check refuses, which an upload answers to the browser, is filed as a failed item that says
    why and keeps no text and no file (the scan's refusal in the words an item says when the scan
    withheld its file's text, ``content_scan.nothing_made``)."""
    from personalclaw.uploads.content_scan import ContentRefused
    from personalclaw.uploads.store import UploadError

    name = path.name
    checked = await _check(path, surface=surface)
    if checked.refused:
        return _refused(store, name, checked.item_type, checked.refused, tags)
    if not checked.copy:
        return Taken(None, changing=checked.changing)
    try:
        item, is_new = await store_file_item(store, checked.copy, name, tags=tags)
    except ContentRefused as exc:
        return _refused(store, name, checked.item_type, exc.nothing_made, tags)
    except UploadError as exc:  # a file named as code whose bytes are not text
        return _refused(store, name, checked.item_type, f"{exc.message}.", tags)
    finally:
        Path(checked.copy).unlink(missing_ok=True)
    return Taken(item, is_new)


# ── a watched folder's file ──────────────────────────────────────────────────

#: The door a watched folder's files come through, as the security event log names it.
WATCHED_SURFACE = "watched_folder"

#: What an item holds of a file, cleared when it is remade of another kind or of nothing.
_NO_FILE: dict = {
    "file_path": "",
    "thumbnail_path": "",
    "mime_type": "",
    "file_size": 0,
    "gist_language": "",
}


def _holds(item: dict, checked: _Checked) -> bool:
    """Whether *item*, made of the file before, already holds the bytes *checked* passed: their
    hash, kept and not refused, of the same kind, in the library (not archived)."""
    meta = item.get("file_metadata") or {}
    kept = str(item.get("file_path") or "")
    return (
        bool(checked.content_hash)
        and meta.get("content_hash") == checked.content_hash
        and not meta.get("refused")
        and not item.get("is_archived")
        and (item.get("item_type") or item.get("type")) == checked.item_type
        and (checked.item_type == "gist" or (bool(kept) and Path(kept).is_file()))
    )


async def _keep(checked: _Checked, name: str) -> dict:
    """What an item made of the checked copy holds, as its fields, as :func:`store_file_item` makes
    an upload: a code file's text (read by the rule every reader of a file as text shares, and
    scanned: a binary file named as code raises ``UploadError``, text the scan refuses
    ``ContentRefused``), or else the copy moved under the library's own files (beside a picture's
    thumbnail), which the graph for its kind reads when it is ingested."""
    from personalclaw.knowledge import knowledge_files_dir
    from personalclaw.knowledge.media import code_language
    from personalclaw.uploads.content_scan import scan_text
    from personalclaw.uploads.store import UploadError

    language = code_language(name)
    if checked.item_type == "gist" and language:
        code, _hash = await asyncio.to_thread(_take_gist, checked.copy)
        if code is None:
            raise UploadError(f"{name} is not a text file, so it cannot be kept as code", 415)
        await scan_text(code, surface="knowledge")
        return {
            "item_type": "gist",
            "content": code,
            "gist_language": language,
            "file_metadata": {"content_hash": checked.content_hash},
        }
    files_dir = Path(knowledge_files_dir())
    stem = str(uuid4())
    dest = files_dir / f"{stem}{Path(name).suffix.lower()}"
    thumb = files_dir / f"{stem}.thumb.webp" if checked.item_type == "image" else None
    size, content_hash, thumb_path = await asyncio.to_thread(
        _take_file, checked.copy, dest, thumb, checked.content_hash
    )
    return {
        "item_type": checked.item_type,
        "content": "",
        "file_path": str(dest),
        "mime_type": guess_mime(name),
        "file_size": size,
        "thumbnail_path": thumb_path,
        "file_metadata": {"content_hash": content_hash, "original_filename": name},
    }


def _drop_stored(item: dict, kept: dict) -> None:
    """Remove the file and the thumbnail *item* kept under the library's own files before it was
    remade, unless it keeps them still (*kept*, its fields now). Only inside that folder, as a
    delete of an item removes them (``dashboard.handlers.knowledge.delete_item``)."""
    from personalclaw.knowledge import knowledge_files_dir

    root = Path(knowledge_files_dir()).resolve()
    for key in ("file_path", "thumbnail_path"):
        old = str(item.get(key) or "")
        if not old or old == str(kept.get(key) or ""):
            continue
        try:
            resolved = Path(old).resolve()
            if resolved.is_relative_to(root) and resolved.is_file():
                resolved.unlink()
        except (OSError, ValueError):
            logger.debug("the file %s an item kept before stays", old, exc_info=True)


def _refuse_watched(
    store,
    name: str,
    item_type: str,
    reason: str,
    *,
    source: dict,
    guid: str,
    existing: dict | None,
) -> Taken:
    """File a watched folder's file the library refused (*reason*, the status line), as
    :func:`_refused` files a dropped one: a failed item that says why and keeps no text and no
    file, made through the source's novelty gate; or *existing*, what an earlier pass made of the
    file, which then keeps nothing of what it held (its text, its pool, its passages, its insights,
    its vector and its kept file)."""
    from personalclaw.knowledge.pipeline.runner import refused_phases

    meta = {"refused": reason, "node_phases": refused_phases(item_type, reason)}
    if existing is None:
        item_id = store.create_typed_item(
            item_type=item_type,
            title=name,
            content="",
            provider=source["provider"],
            source_id=source["id"],
            guid=guid,
            extra={
                "processing_status": "failed",
                "processing_error": reason,
                "file_metadata": meta,
            },
        )
        return Taken(store.get_item(item_id) if item_id else None, refused=reason)
    item_id = existing["id"]
    store.clear_extracted_contents(item_id)
    store.clear_chunks(item_id)
    store.update_item(
        item_id,
        **_NO_FILE,
        item_type=item_type,
        title=name,
        content="",
        insights={},
        embedding=None,
        is_archived=0,
        file_metadata=meta,
        processing_status="failed",
        processing_error=reason,
    )
    _drop_stored(existing, {})
    return Taken(store.get_item(item_id), refused=reason)


async def take_watched_file(
    store, path: Path, *, source: dict, guid: str, existing: dict | None
) -> Taken:
    """Take the file at *path*, which the watched folder *source* holds at *guid* (its path inside
    the folder), into the library as the Knowledge page takes an upload.

    The upload route's checks (:func:`_check`): the kinds the library takes, the size the upload
    policy allows each, the content scan of a private copy of its bytes; then a code file's text,
    read and scanned. The item is the file's own kind: a code file a ``gist`` holding its text,
    anything else the copy kept in the library's own files, which the reader for its kind reads
    when the item is ingested (and the text a document's reader makes is scanned then). What a
    check refuses is a failed item that says why and keeps no text and no file.

    *existing* is the item an earlier pass made of this file, which its change remakes rather than
    a second: of what the file holds now, or, refused, of nothing. One that already holds these
    bytes is left as it is (``is_new`` false), so a file whose time moved but whose bytes did not
    is not read again. With no *existing*, the item is made through the source's novelty gate
    (``create_typed_item``), which makes none for a path the source has seen (``item`` ``None``).

    ``item`` is ``None`` too when there is nothing to take yet: an empty file, or one that changed
    while it was copied (``changing``). The caller hands an item that waits to be read
    (``is_new``) to the ingest queue."""
    from personalclaw.uploads.content_scan import ContentRefused
    from personalclaw.uploads.store import UploadError

    name = path.name

    def refuse(item_type: str, reason: str) -> Taken:
        return _refuse_watched(
            store, name, item_type, reason, source=source, guid=guid, existing=existing
        )

    checked = await _check(path, surface=WATCHED_SURFACE)
    if checked.refused:
        return refuse(checked.item_type, checked.refused)
    if not checked.copy:
        return Taken(None, changing=checked.changing)
    try:
        if existing is not None and _holds(existing, checked):
            return Taken(existing)
        kept = await _keep(checked, name)
    except ContentRefused as exc:
        return refuse(checked.item_type, exc.nothing_made)
    except UploadError as exc:  # a file named as code whose bytes are not text
        return refuse(checked.item_type, f"{exc.message}.")
    finally:
        Path(checked.copy).unlink(missing_ok=True)
    fields = {key: value for key, value in kept.items() if key not in ("item_type", "content")}
    if existing is None:
        item_id = store.create_typed_item(
            item_type=kept["item_type"],
            title=name,
            content=kept["content"],
            provider=source["provider"],
            source_id=source["id"],
            guid=guid,
            extra={**fields, "processing_status": "queued"},
        )
        if item_id is None:  # the source has seen this path: no second item is made of it
            if kept.get("file_path"):
                await asyncio.to_thread(
                    _discard_file, Path(kept["file_path"]), kept.get("thumbnail_path", "")
                )
            return Taken(None)
        return Taken(store.get_item(item_id), is_new=True)
    store.update_item(
        existing["id"],
        **{**_NO_FILE, **fields},
        item_type=kept["item_type"],
        title=name,
        content=kept["content"],
        is_archived=0,
        processing_status="queued",
        processing_error=None,
    )
    _drop_stored(existing, kept)
    return Taken(store.get_item(existing["id"]), is_new=True)

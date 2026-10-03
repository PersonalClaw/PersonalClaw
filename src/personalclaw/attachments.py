"""The files that came with a message, and where core keeps them.

A mail's attachment is a file, not words. The message carries its text; each attached file is
listed beside it with its name, its type and its size, and kept here so the owner can download it
and an agent reading the message can read it. Before this, a mail source turned a PDF into text
and appended it to the body, so the Inbox showed the attachment's bytes as the message and listed
no attachment at all.

A source hands core an :class:`Attachment` (the name and type the message gave the file, and its
bytes) on the message it polled or received (``IncomingMessage.files``,
``ChannelMessage.files``). Core keeps each one under ``<home>/attachments/<owner>/``, where
*owner* is the row the files belong to (an Inbox item's id), and records what is listed about it
on that row. :func:`keep` never trusts what a message says about its file: the name is only ever
shown (the stored file is named from a sanitised form of it), the declared type is only ever shown
(a download is served as bytes to save, never as the type the sender claimed), and a file larger
than :data:`MAX_BYTES`, or past the :data:`MAX_COUNT`-th, is listed with why it was not kept.

What an agent reads of one (:func:`reading`) is its text, read the way a chat attachment is read,
and fenced as data: the sender wrote it. An image is listed and not read here, since reading it
would take an image model's call for a message nobody asked about.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: The home folder the kept files live in, one folder per owner row.
ATTACHMENTS_DIR = "attachments"

#: The largest file kept. A mail server's own limit is about this, and a file past it is listed
#: with why it was not kept rather than dropped without a word.
MAX_BYTES = 25 * 1024 * 1024

#: The most files kept from one message; the rest are listed as not kept.
MAX_COUNT = 20

#: The most of one attachment's text an agent is handed beside the message.
TEXT_CAP = 20_000

#: What an owner key may be: an Inbox item's id (``mail_<hex>_<ts>``, ``someone_new_<hex>_<ts>``).
#: Nothing that could name a parent folder or another path.
_OWNER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$")

_NAME_CAP = 200
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


@dataclass(frozen=True)
class Attachment:
    """A file that came with a message: the name and type the message gave it, and its bytes.

    Both the name and the type are what the sender wrote, so neither is trusted: core shows
    them, and names the stored file and serves its download without them.
    """

    name: str
    mimetype: str = ""
    data: bytes = field(default=b"", repr=False)


def attachments_root() -> Path:
    from personalclaw.config.loader import config_dir

    return Path(config_dir()) / ATTACHMENTS_DIR


def _owner_dir(owner: str) -> Path:
    """The folder *owner*'s files live in. Raises ValueError for a key that is not an owner key."""
    key = str(owner or "")
    if not _OWNER_RE.fullmatch(key) or ".." in key:
        raise ValueError(f"{owner!r} is not a key attachments can be kept under")
    return attachments_root() / key


def shown_name(name: str) -> str:
    """The name as it is listed: the sender's, without control characters, never empty."""
    cleaned = _CONTROL_RE.sub("", str(name or "")).strip()
    return cleaned[:_NAME_CAP] or "attachment"


def _stored_name(index: int, name: str) -> str:
    """``<index>-<stem>.<ext>``: the file name on disk, built from letters, digits, ``-``, ``_``
    and ``.`` only, so no name a sender writes can reach another folder or hide itself."""
    base = os.path.basename(name.replace("\\", "/"))
    stem, ext = os.path.splitext(base)
    safe_stem = re.sub(r"[^A-Za-z0-9_-]+", "-", stem).strip("-")[:80] or "attachment"
    safe_ext = re.sub(r"[^A-Za-z0-9]+", "", ext)[:12]
    return f"{index}-{safe_stem}" + (f".{safe_ext}" if safe_ext else "")


def human_size(size: int) -> str:
    """``21.5 KB``: the size an attachment is listed with."""
    from personalclaw.durability.footprint import human_bytes

    return human_bytes(size)


def keep(owner: str, files: Iterable[Attachment]) -> list[dict[str, Any]]:
    """Keep each of *files* under *owner* and return what is listed about each, in order.

    A record is ``{"id", "name", "mimetype", "size"}`` with ``"file"`` (the stored name) when it
    was kept, or ``"not_kept"`` (why, as a sentence's end) when it was not. A file the disk
    refuses is listed the same way, so a message never loses mention of a file it carried.
    Raises ValueError for an *owner* that is not an owner key.
    """
    folder = _owner_dir(owner)  # a key that is not an owner key is the caller's mistake: raised
    made = False
    records: list[dict[str, Any]] = []
    for index, item in enumerate(files, start=1):
        data = bytes(item.data or b"")
        record: dict[str, Any] = {
            "id": str(index),
            "name": shown_name(item.name),
            "mimetype": str(item.mimetype or "").strip().lower() or "application/octet-stream",
            "size": len(data),
        }
        if index > MAX_COUNT:
            record["not_kept"] = f"a message keeps at most {MAX_COUNT} attachments"
        elif len(data) > MAX_BYTES:
            record["not_kept"] = (
                f"it is larger than {human_size(MAX_BYTES)}, the most an attachment may be"
            )
        else:
            stored = _stored_name(index, record["name"])
            try:
                from personalclaw.atomic_write import atomic_write_bytes, ensure_private_dir

                if not made:
                    ensure_private_dir(attachments_root())
                    ensure_private_dir(folder)
                    made = True
                atomic_write_bytes(folder / stored, data)
                record["file"] = stored
            except OSError:
                logger.warning("attachment %s of %s could not be kept", index, owner, exc_info=True)
                record["not_kept"] = "it could not be written to disk"
        records.append(record)
    return records


def keep_for_chat(files: Iterable[Attachment]) -> list[str]:
    """Keep *files* the way a chat upload is kept (``<home>/uploads/<uuid>_<name>``) and return
    their paths, for a message that becomes a turn in a chat: the turn carries them as its
    attached files, so the chat shows each one and the agent reads it as it reads an upload. A
    file too large, or past the :data:`MAX_COUNT`-th, is left out and logged."""
    import uuid

    from personalclaw.atomic_write import atomic_write_bytes, ensure_private_dir
    from personalclaw.config.loader import config_dir

    folder = Path(config_dir()) / "uploads"
    paths: list[str] = []
    for index, item in enumerate(files, start=1):
        data = bytes(item.data or b"")
        if index > MAX_COUNT or len(data) > MAX_BYTES:
            logger.warning("a %d-byte attachment was not kept for the chat", len(data))
            continue
        safe = re.sub(r"[^\w.\-]", "_", os.path.basename(shown_name(item.name).replace("\\", "/")))
        try:
            ensure_private_dir(folder)
            dest = folder / f"{uuid.uuid4().hex}_{safe}"
            atomic_write_bytes(dest, data)
        except OSError:
            logger.warning("an attachment could not be kept for the chat", exc_info=True)
            continue
        paths.append(str(dest))
    return paths


def path_of(owner: str, record: Mapping[str, Any]) -> Path | None:
    """The kept file *record* names under *owner*, or None when it was not kept or is gone."""
    stored = str(record.get("file") or "")
    if not stored or os.path.basename(stored) != stored or stored in (".", ".."):
        return None
    try:
        folder = _owner_dir(owner)
    except ValueError:
        return None
    path = folder / stored
    try:
        real, base = path.resolve(), folder.resolve()
    except OSError:
        return None
    if real.parent != base or not real.is_file():
        return None
    return real


def find(records: Iterable[Mapping[str, Any]], attachment_id: str) -> Mapping[str, Any] | None:
    """The record listed under *attachment_id*, or None."""
    for record in records or ():
        if isinstance(record, Mapping) and str(record.get("id") or "") == str(attachment_id):
            return record
    return None


def forget(owner: str) -> None:
    """Remove every file kept under *owner* (its row is gone). Missing is fine."""
    try:
        shutil.rmtree(_owner_dir(owner))
    except FileNotFoundError:
        return
    except (OSError, ValueError):
        logger.warning("could not remove the attachments of %s", owner, exc_info=True)


def listing(records: Iterable[Mapping[str, Any]]) -> str:
    """One line per attachment: name, type and size, and why one was not kept. Empty when there
    are none. The name and type are the sender's words, so this goes inside a fence: into text
    that is fenced as a whole (an Inbox message read for a draft, an Investigate snapshot)."""
    lines = []
    for record in records or ():
        line = (
            f"- {record.get('name') or 'attachment'} ({record.get('mimetype') or 'unknown type'}, "
            f"{human_size(int(record.get('size') or 0))})"
        )
        if record.get("not_kept"):
            line += f": not kept, because {record['not_kept']}"
        lines.append(line)
    return "\n".join(lines)


def is_image(record: Mapping[str, Any]) -> bool:
    mimetype = str(record.get("mimetype") or "")
    ext = os.path.splitext(str(record.get("name") or ""))[1].lower()
    return mimetype.startswith("image/") or ext in (
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
        ".bmp",
        ".tif",
        ".tiff",
        ".heic",
    )


async def text_of(owner: str, record: Mapping[str, Any]) -> tuple[str, str]:
    """``(text, note)`` for one kept attachment: its text, read as a chat attachment is read (the
    content scan included), up to :data:`TEXT_CAP` characters, or ``""`` and a note saying why
    there is none. The cap sits inside the first window the scan reads of a long text
    (``uploads.content_scan.scanned_head``), so nothing handed on went unread."""
    path = path_of(owner, record)
    if path is None:
        return "", f"it was not kept ({record.get('not_kept') or 'it is not on this machine'})"
    if is_image(record):
        return "", "it is an image, which is not read here; the owner can open it from the Inbox"
    from personalclaw.knowledge.extract import extract_file, withheld

    try:
        got = await extract_file(
            str(path),
            str(record.get("mimetype") or "") or None,
            name=str(record.get("name")),
            surface="inbox",
        )
    except Exception:  # noqa: BLE001 - an unreadable attachment is said, never raised
        logger.warning("reading attachment %s of %s failed", record.get("id"), owner, exc_info=True)
        return "", "it could not be read"
    if why := withheld(got.unread):
        return "", why
    text = (got.text or "").strip() if got.read else ""
    if not text:
        return "", "no text could be read from it"
    if len(text) > TEXT_CAP:
        text = text[:TEXT_CAP] + f"\n…[cut at {TEXT_CAP:,} characters]"
    return text, ""


async def raw_reading(owner: str, records: Iterable[Mapping[str, Any]]) -> str:
    """Each attachment listed with the text that could be read from it, UNFENCED: for text that is
    fenced as a whole (an Investigate snapshot). Empty when there are none."""
    parts: list[str] = []
    for record in records or ():
        text, note = await text_of(owner, record)
        head = listing([record])
        parts.append(
            f"{head}\nIts text:\n{text}" if text else f"{head}\n({note[:1].upper()}{note[1:]}.)"
        )
    return "\n\n".join(parts)


async def reading(owner: str, records: Iterable[Mapping[str, Any]], *, source: str) -> str:
    """What an agent reading the message is told of its attachments, as standalone text: for each,
    one fence (as data from *source*) holding its name, its type and the text read from it, and
    beside the fence, in core's own words, its size and why any text is missing."""
    from personalclaw.security import fence_untrusted, redact_for_model

    parts: list[str] = []
    for position, record in enumerate(records or (), start=1):
        text, note = await text_of(owner, record)
        facts = f"name: {record.get('name') or 'attachment'}\ntype: {record.get('mimetype') or ''}"
        fenced = fence_untrusted(
            f"{facts}\n\n{redact_for_model(text)}" if text else facts,
            source=source,
            source_type="attachment",
            source_id=f"{owner}:{record.get('id')}",
            transformation_path="extract" if text else "list",
        )
        size = human_size(int(record.get("size") or 0))
        said = "its name, its type and its text" if text else f"its name and its type; {note}"
        parts.append(f"Attachment {position} ({size}), {said}:\n{fenced}")
    return "\n\n".join(parts)

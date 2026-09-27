"""A file as the file explorer reads it — the one projection its read, watch and save share.

``GET /api/file-read`` serves it, ``GET /api/file-watch`` streams it, and a save names the
revision of it (`personalclaw/stale_write.py`) — from the file viewer (``POST /api/file-write``)
or from **Save as artifact** over the same file (``POST /api/artifacts``). A revision is only
comparable when every side takes it of the same text, so all of them read through here: the
first :data:`FILE_READ_CAP` bytes, binary detected the way git does, masked for display
(`security.redact_for_display`). That mask is also the one a save puts back
(`security.keep_masked_spans`), so a page's copy never saves a marker over the key it hides.

It lives below the HTTP surface because two surfaces need it — the file handlers and the
artifact handlers — and a core module may not import a dashboard one.
"""

from __future__ import annotations

from personalclaw.security import redact_for_display

__all__ = ["FILE_READ_CAP", "file_as_read", "read_head", "whole_text"]

#: How much of a file the explorer reads. A longer file is served TRUNCATED, and the editor opens a
#: truncated file read-only, so a save cannot cut off the part it never loaded.
FILE_READ_CAP = 512_000


def read_head(path: str) -> bytes:
    """The first ``FILE_READ_CAP + 1`` bytes of *path* — the extra byte shows a truncation."""
    with open(path, "rb") as f:
        return f.read(FILE_READ_CAP + 1)


def file_as_read(raw: bytes) -> tuple[str, bool, bool]:
    """``(text, truncated, binary)`` — a file's head (:func:`read_head`) as the explorer serves it.

    A NUL byte in the head is git's own binary heuristic, and a binary file's text is ``""``:
    decoded it would be a wall of replacement characters, not something a user reads or edits.
    Text is masked for display (credentials, exfiltration URLs) before it leaves the gateway.

    ONE projection for ``file-read``, ``file-watch`` and ``file-write``'s precondition, because a
    revision is only comparable when every side takes it of the same text. The watch used to read
    in text mode — universal newlines and a character cap — so a CRLF file reached the page one
    way from the read and another way from the watch.
    """
    truncated = len(raw) > FILE_READ_CAP
    raw = raw[:FILE_READ_CAP]
    if b"\x00" in raw[:8192]:
        return "", truncated, True
    return redact_for_display(raw.decode("utf-8", errors="replace")), truncated, False


def whole_text(raw: bytes) -> str | None:
    """The file's text when a read hands out ALL of it, else ``None``.

    That text is the document a page edits and saves, and its ``revision_of`` is the revision
    the read reports (`personalclaw/stale_write.py`) — of the REDACTED text, so a revision never
    encodes more than its reader could see. A truncated or binary read is no copy of the file, so
    it carries no revision and can be no save's base: ``None`` matches no revision a read hands
    out.
    """
    text, truncated, binary = file_as_read(raw)
    return None if truncated or binary else text

"""Document comments — the annotation layer over files, artifacts and planning docs.

🔴 WHY THIS MODULE EXISTS. The layer shipped browser-only: ``commentStore.ts`` read and
wrote one global ``localStorage`` key (``doc-comments-v1``) and nothing else. So the only
copy of user-authored annotations lived in one browser profile, which meant

1. clearing site data destroyed them, with nothing to restore from — everything else the
   user creates in this app survives that;
2. ``personalclaw snapshot`` could not carry them, because the server never saw them. The
   pre-1.0 release notes advise taking a snapshot before an upgrade, so the one action a
   user takes to protect their work silently excluded their annotations;
3. they were invisible on a second device, and in the desktop app vs the browser, even
   though the underlying documents were the same.

Task comments, by contrast, have always been a real server-side store
(``tasks/_comments_<id>.json``). Two comment systems with opposite durability guarantees,
and nothing told the user which one they were using.

**Shape: ONE json file, not a per-document sidecar.** The deck is deliberately
cross-document — the UI collects comments over every file and artifact into a single
list and renders it under whichever preview is open — so the store that backs it is the
same single list. A per-document sidecar (the task-comments shape) would force a scan of
every sidecar to answer the one question the UI actually asks ("what is in the deck?"),
and ``docId`` is an arbitrary absolute path rather than a safe record id, so each sidecar
name would have to be a hash with no way back to the document.

**No localStorage cache remains.** Keeping one would leave two writers over one list and
no rule for which wins after an edit on a second device; the store is the single source
of truth and the frontend reads it.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

from personalclaw.atomic_write import atomic_write
from personalclaw.config import loader as config_loader


def config_dir() -> Path:
    """The active home, re-resolved per call — see :func:`personalclaw.config.loader.config_dir`.

    DEFINED here rather than imported: this module is imported lazily by the handlers, and
    an import-time binding captures whatever the name pointed at on first use (#2443).
    """
    return config_loader.config_dir()


logger = logging.getLogger(__name__)

_STORE_FILENAME = "doc_comments.json"

#: Bound on one comment's body and on the deck. A comment is a note on a passage, not a
#: document; the cap is what stops an unbounded field from becoming the store's size.
MAX_COMMENT_CHARS = 10_000
MAX_QUOTE_CHARS = 4_000
MAX_COMMENTS = 5_000


def store_path() -> Path:
    return config_dir() / _STORE_FILENAME


@dataclass
class DocComment:
    """One comment anchored to a passage of a file/artifact preview.

    ``line``/``column``/``context`` are the anchor the frontend resolves at comment time
    (1-based, plus a ~20-char snippet each side) so the assistant can find the exact
    occurrence of a short or repeated quote. They are carried, not recomputed: the server
    does not have the rendered document the selection was made against.
    """

    id: str
    doc_id: str
    doc_label: str = ""
    doc_path: str = ""
    quote: str = ""
    comment: str = ""
    line: int | None = None
    column: int | None = None
    context: str = ""
    ts: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


def _clamp(value: object, limit: int) -> str:
    return str(value or "")[:limit]


def _int_or_none(value: object) -> int | None:
    """A 1-based anchor coordinate, or ``None`` for anything that is not one.

    ``bool`` is excluded explicitly because it IS an ``int`` in Python, and a ``True``
    silently stored as line 1 is a wrong anchor rather than an absent one.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, (str, float)):
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
    return None


def _from_dict(d: object) -> DocComment | None:
    """Parse one stored row. A row with no ``id`` or no ``doc_id`` is dropped.

    FAIL-OPEN per the storage convention: an unreadable row costs that annotation, while
    raising would cost the whole deck — and this is the only copy of the user's notes.
    """
    if not isinstance(d, dict):
        return None
    cid = str(d.get("id") or "").strip()
    doc_id = str(d.get("doc_id") or "").strip()
    if not cid or not doc_id:
        return None
    return DocComment(
        id=cid,
        doc_id=doc_id,
        doc_label=_clamp(d.get("doc_label"), 500),
        doc_path=_clamp(d.get("doc_path"), 4_000),
        quote=_clamp(d.get("quote"), MAX_QUOTE_CHARS),
        comment=_clamp(d.get("comment"), MAX_COMMENT_CHARS),
        line=_int_or_none(d.get("line")),
        column=_int_or_none(d.get("column")),
        context=_clamp(d.get("context"), 1_000),
        ts=float(d.get("ts") or 0.0),
    )


def load() -> list[DocComment]:
    """Every comment, oldest first. A missing or unreadable store reads as empty."""
    p = store_path()
    if not p.exists():
        return []
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.warning("%s unreadable; treating as empty", _STORE_FILENAME, exc_info=True)
        return []
    rows = raw.get("comments") if isinstance(raw, dict) else raw
    if not isinstance(rows, list):
        return []
    out = [c for c in (_from_dict(r) for r in rows) if c is not None]
    out.sort(key=lambda c: c.ts)
    return out


def _save(comments: list[DocComment]) -> None:
    payload = {"comments": [c.to_dict() for c in comments]}
    atomic_write(store_path(), json.dumps(payload, indent=2) + "\n")


def list_comments() -> list[dict]:
    """The ``GET /api/doc-comments`` payload."""
    return [c.to_dict() for c in load()]


def add(
    *,
    doc_id: str,
    doc_label: str = "",
    doc_path: str = "",
    quote: str = "",
    comment: str = "",
    line: object = None,
    column: object = None,
    context: str = "",
) -> DocComment:
    """Append one comment and return it, id and timestamp assigned server-side."""
    doc_id = str(doc_id or "").strip()
    if not doc_id:
        raise ValueError("doc_id is required")
    body = _clamp(comment, MAX_COMMENT_CHARS)
    if not body.strip():
        raise ValueError("comment is required")
    rows = load()
    if len(rows) >= MAX_COMMENTS:
        raise ValueError(f"the comment deck is full ({MAX_COMMENTS} comments)")
    row = DocComment(
        id=f"c-{uuid.uuid4().hex[:12]}",
        doc_id=doc_id,
        doc_label=_clamp(doc_label, 500),
        doc_path=_clamp(doc_path, 4_000),
        quote=_clamp(quote, MAX_QUOTE_CHARS),
        comment=body,
        line=_int_or_none(line),
        column=_int_or_none(column),
        context=_clamp(context, 1_000),
        ts=time.time(),
    )
    rows.append(row)
    _save(rows)
    return row


def update(comment_id: str, *, comment: str) -> DocComment | None:
    """Edit one comment's body. ``None`` when the id is unknown."""
    rows = load()
    body = _clamp(comment, MAX_COMMENT_CHARS)
    if not body.strip():
        raise ValueError("comment is required")
    found: DocComment | None = None
    for row in rows:
        if row.id == comment_id:
            row.comment = body
            found = row
            break
    if found is None:
        return None
    _save(rows)
    return found


def remove(ids: list[str]) -> int:
    """Delete by id. Returns how many rows went away.

    Takes a LIST because the deck's own verbs are one-and-many (dismiss a card, or clear
    everything handed to the assistant); one endpoint that accepts both is what keeps a
    bulk dismiss from being N round trips that can half-fail.
    """
    wanted = {str(i) for i in ids if str(i).strip()}
    if not wanted:
        return 0
    rows = load()
    kept = [r for r in rows if r.id not in wanted]
    removed = len(rows) - len(kept)
    if removed:
        _save(kept)
    return removed


def clear() -> int:
    """Empty the deck. Returns how many rows went away."""
    rows = load()
    if rows:
        _save([])
    return len(rows)

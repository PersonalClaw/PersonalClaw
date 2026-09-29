"""A watched feed's or page's entries stored before HTML was converted on the way in.

New entries are stored as their words (``connectors.base.readable_text``). An entry stored
before that holds the feed's markup — an Atom ``type="html"`` content, an RSS description, a
WordPress excerpt — and the library renders a body as markdown only, so such an item would read
as its tags. This converts those bodies once, through the same conversion a new entry takes.

**Keyed on the data, so it is idempotent.** The one predicate is "the body still holds raw HTML
outside code" (:func:`holds_raw_html`), and the conversion leaves none, so a converted body never
matches again and a second pass writes nothing. There is no flag or version to get out of step
with the rows.

**Only what a watched feed or page wrote.** Those bodies are the source's markup by
construction. A body a person wrote — a note with a tag in it — is hers and is never rewritten;
the renderer is what keeps its markup inert.

**The derived layer follows.** The extracted-content pool's copy of the old body is replaced
with the new one, and the chunks and the whole-item vector are invalidated
(:func:`~personalclaw.knowledge.restructure.refresh_derived`) for the maintenance host to
rebuild. No model is called, whatever the source's enrichment: the words are the same words.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)

#: The sources whose bodies were stored as the entry's markup before the conversion existed.
WATCHED_MARKUP_PROVIDERS = ("watched-feed", "watched-page")

#: Items per batch. Each batch runs on the event loop and yields between batches, so it is
#: bounded to keep a start responsive while a large library converts.
BATCH_SIZE = 50


def holds_raw_html(text: str) -> bool:
    """Whether *text* still holds raw HTML outside code — the predicate the conversion keys on."""
    from personalclaw.knowledge.connectors.base import without_raw_html

    body = text or ""
    return without_raw_html(body) != body


def convert_batch(
    store: Any, *, after_rowid: int = 0, limit: int = BATCH_SIZE
) -> tuple[list[str], int | None]:
    """Convert one batch of watched items whose body still holds raw HTML.

    Returns the ids converted and the rowid to resume after — None once the scan has passed
    the last candidate. A row that fails to write is logged and skipped; the cursor still
    moves past it, so one bad row cannot hold the scan in place.
    """
    from personalclaw.knowledge.connectors.base import readable_text
    from personalclaw.knowledge.restructure import refresh_derived

    marks = ",".join("?" for _ in WATCHED_MARKUP_PROVIDERS)
    # `LIKE '%<%'` is only the cheap pre-filter: a body with no "<" at all holds no tag.
    rows = store.db.execute(
        "SELECT i.rowid AS rowid, i.id AS id, i.content AS content FROM items i "  # noqa: S608
        f"JOIN sources s ON s.id = i.source_id WHERE s.provider IN ({marks}) "
        "AND i.content LIKE '%<%' AND i.rowid > ? ORDER BY i.rowid LIMIT ?",
        (*WATCHED_MARKUP_PROVIDERS, after_rowid, limit),
    ).fetchall()
    converted: list[str] = []
    for row in rows:
        body = row["content"] or ""
        if not holds_raw_html(body):
            continue
        text = readable_text(body)
        try:
            store.update_item(row["id"], content=text, touch=False)
            # The pool row that IS the old body (the passthrough copy) becomes the new body.
            store.db.execute(
                "UPDATE extracted_contents SET text = ? WHERE item_id = ? AND text = ?",
                (text, row["id"], body),
            )
        except Exception:  # noqa: BLE001 — one bad row must not abandon the rest
            logger.warning(
                "could not convert the stored markup of item %s", row["id"], exc_info=True
            )
            continue
        converted.append(str(row["id"]))
    if converted:
        refresh_derived(store, converted, reason="stored markup converted")
    cursor = int(rows[-1]["rowid"]) if len(rows) == limit else None
    return converted, cursor


async def convert_in_batches(store: Any) -> int:
    """Convert every watched item still stored as markup; return how many were converted.

    One batch at a time, yielding the loop between batches. On the event loop rather than in a
    thread on purpose — the store's one sqlite connection is shared with the ingest worker, and
    two threads' transactions on one connection collide."""
    total = 0
    cursor: int | None = 0
    while cursor is not None:
        done, cursor = convert_batch(store, after_rowid=cursor)
        total += len(done)
        await asyncio.sleep(0)
    return total

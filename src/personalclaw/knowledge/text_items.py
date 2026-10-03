"""Text from outside, kept in the library: the one rule every door that is not a file follows.

Knowledge keeps text that came from outside the gateway: a watched source's entries (a feed's, a
page's, an app's source such as a repository's files, a shared store's), the page or the paper it
fetches for a bookmark, a web watch's new items, the notes an app, the agent or a workflow writes
into it, and an edit made to a note's page in the knowledge vault, which is a file. That text is
searched and recalled into prompts, so it is read by the content scan before it is stored or
ingested (``uploads.content_scan.scan_text``: the rules an upload's text is read by, which refuse a
destructive script, an injection phrase, an invisible character). The scan reads an item's text,
its title and its body; its link and its identity are where it came from, as a file's name is.

What the owner writes herself in the app is her own words, as what she writes in the chat is, and
the chat never scans her message: a note she writes or edits in Knowledge is kept as she wrote it.
A file is scanned at every door, whoever made it (``knowledge.file_items``), and so is the text of
a vault page, which any program on the machine can write (``knowledge.vault``).

A door with someone to answer (an app's request, the agent's tool, a workflow's step) refuses with
the scan's answer, and nothing is made or changed. A door with no one to answer (a watched source,
a web watch) files a refusal as a failed item that keeps no text and says why
(:func:`refused_fields`); the vault leaves the note as it was and says why in the page. A scan that
could not run refuses as well: a check that did not run is never read as a pass. An item a source
made of a sighting the scan could not check is read again when the source offers it again
(:func:`unchecked`).

A source's text is read when it is new or when it changed (:func:`digest`, kept on the item): a
source that offers what it offered before is not read again.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from personalclaw.uploads.content_scan import ContentRefused

#: How many of a batch's scans run at once (:func:`refusals`). Each scan is a child process, and
#: most of its time is the child's start, which runs side by side with the others' (measured: one
#: took 0.77 s on a loaded host, six at once 0.88 s together).
SCANS_AT_ONCE = 8


def text_of(*parts: str) -> str:
    """An item's text as the scan reads it: each of *parts* that holds any (its title, its
    summary, its body), a blank line apart."""
    return "\n\n".join(part for part in parts if part and part.strip())


def digest(*parts: str) -> str:
    """The SHA-256 of an item's text (:func:`text_of`). Kept on an item a source made, as
    ``file_metadata.content_hash``, so a sighting whose text did not change is not read again."""
    return hashlib.sha256(text_of(*parts).encode("utf-8", errors="replace")).hexdigest()


async def refusal(*parts: str, surface: str) -> ContentRefused | None:
    """The content scan's refusal of an item's text from outside (*parts*, :func:`text_of`), or
    ``None`` when it may be kept. A scan that could not run is a refusal too.

    Each door says it in the scan's own words for what it does: an item made of nothing says
    ``nothing_made``, an edit not made says ``withheld``. *surface* names the door, for the
    security event log, which records each refusal and each scan that could not run."""
    from personalclaw.uploads.content_scan import ContentRefused, scan_text

    try:
        await scan_text(text_of(*parts), surface=surface)
    except ContentRefused as exc:
        return exc
    return None


async def refusals(items: Sequence[Sequence[str]], *, surface: str) -> list[ContentRefused | None]:
    """:func:`refusal` of each of *items* (each one's text parts), in their order, with at most
    :data:`SCANS_AT_ONCE` scans running at once: for a door that takes many items in one go, which
    one scan after another would hold for most of a second each."""
    import asyncio

    gate = asyncio.Semaphore(SCANS_AT_ONCE)

    async def one(parts: Sequence[str]) -> ContentRefused | None:
        async with gate:
            return await refusal(*parts, surface=surface)

    return list(await asyncio.gather(*(one(parts) for parts in items)))


def refused_fields(item_type: str, reason: str, meta: dict | None = None) -> dict:
    """The fields of an item made of nothing, that says why (*reason*, its status line): failed,
    with the refusal recorded on its metadata (*meta*, kept beside it), so a re-run of its ingest
    says why again (``pipeline.runner.ingest_item``), and each step of its graph says it does not
    run."""
    from personalclaw.knowledge.pipeline.runner import refused_phases

    return {
        "processing_status": "failed",
        "processing_error": reason,
        "file_metadata": {
            **(meta or {}),
            "refused": reason,
            "node_phases": refused_phases(item_type, reason),
        },
    }


def unchecked(item: dict) -> bool:
    """Whether *item* was made of nothing because the scan could not run on its text: a check
    still owed, so the source's next sighting of it is read again, where one the scan refused for
    what it said is not."""
    from personalclaw.uploads.content_scan import UNCHECKED_CODE, WITHHELD, nothing_made

    refused = (item.get("file_metadata") or {}).get("refused")
    return refused == nothing_made(WITHHELD[UNCHECKED_CODE])

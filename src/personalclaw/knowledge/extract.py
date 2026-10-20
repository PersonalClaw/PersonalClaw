"""Standalone file content extraction — the knowledge ingestion EXTRACTION graph
ONLY, with none of the terminal/enrichment stages.

``ingest_item`` (runner.py) runs a file's node-graph AND then insights, entity,
chunk+embed, title, tags over a knowledge ``store``. Chat attachments want just
the first half: turn an uploaded file into its extracted TEXT so it can be
injected into the chat context — no DB item, no intelligence, no embeddings, no
tags/title. This helper reuses the exact same graphs/nodes (text-read,
PDF/docx/sheet readers, OCR, ASR, ffmpeg a/v split + frame extract) but stops at
the consolidated text.

For a plain-text file this is just "read the file"; for audio/video it's ASR
(+ ffmpeg + frame OCR/vision for video); for an image it's OCR/vision — exactly
as knowledge ingestion does, because it IS the same node graph. A code or config file is
read as the text it is, by the document reader (``pipeline.file_graph_for``): the library
calls one a gist, whose own graph hands on text the library read into the item, and here
there is only the file.

The text the document reader makes of the file is scanned before it is returned
(``uploads.content_scan.scan_text``): the upload scan reads the file's bytes, and a document's
text, or text beside a stray NUL byte, is not in what it reads. Text that fails the scan, or that
the scan could not check, is not returned, and ``unread`` says which.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

logger = logging.getLogger(__name__)

#: ``Extracted.unread`` when reading the file needed an image model and none is set up: nothing
#: is bound to image understanding and the chat model takes no images. A stable value — the
#: attachment chip and the sent turn's preview branch on it to say so and link Settings → Models.
UNREAD_NO_IMAGE_MODEL = "no_image_model"
#: ``Extracted.unread`` when the text a reader made of the file failed the content scan, and when
#: the scan could not check it: the text is withheld. Stable values, which the sent turn's preview
#: branches on to say why.
UNREAD_REFUSED = "refused"
UNREAD_UNCHECKED = "unchecked"


@dataclass(frozen=True)
class Extracted:
    """What extraction got from a file.

    ``text`` is what a consumer may show or send. ``read`` says what that text IS: True when it
    was read from the file's content (its text, OCR, a vision description, a transcript), False
    when it is only the structural descriptor ("Image: x.png (800×600, PNG) — no extractable text
    content") or nothing at all. A surface that tells a user what a model will be given reads
    ``read``, so "the text read from the image" is never said of a descriptor.

    ``unread`` says why nothing was read, when that is known: :data:`UNREAD_NO_IMAGE_MODEL`,
    :data:`UNREAD_REFUSED` or :data:`UNREAD_UNCHECKED`, or ``""`` — the reading ran and found
    nothing, or the file needed no model.
    """

    text: str
    read: bool
    unread: str = ""


async def extract_file(
    file_path: str, mime: str | None = None, *, name: str = "", surface: str
) -> Extracted:
    """Run the knowledge EXTRACTION graph for *file_path* and return what it got.

    ``name`` is what the text calls the file when it can only describe it (its size and format),
    for a caller that stores a file under a name of its own: a chat upload is saved as
    ``<uuid-hex>_<name>``, and that stored name reached the sent turn's preview and the model.
    Defaults to the file's own name. ``surface`` is where the text is going (``attachment``,
    ``inbox``), which a refusal's security event names.

    Never raises — an empty, unread result if extraction yields nothing (caller decides how to
    surface that). Pure extraction: no store, no insights/entities/embeddings/tags/title.
    """
    if not file_path or not os.path.isfile(file_path):
        return Extracted("", False)

    from personalclaw.knowledge import media
    from personalclaw.knowledge.pipeline import (
        NodeContext,
        ensure_nodes_registered,
        file_graph_for,
    )
    from personalclaw.knowledge.pipeline.executor import PipelineExecutor

    ensure_nodes_registered()

    # Route by the same classifier knowledge uses (ext + mime hint). Unknown → 'document', so
    # the document reader reads it when it is text and says so when it is not.
    item_type = media.classify(os.path.basename(file_path), mime) or "document"

    try:
        graph = file_graph_for(item_type)
    except Exception:
        logger.warning("extract: graph build failed for type=%s", item_type, exc_info=True)
        return Extracted("", False)

    ctx = NodeContext(
        item_id=f"attachment:{os.path.basename(file_path)}",
        item_type=item_type,
        file_path=file_path,
        content="",
        url="",
    )
    try:
        result = await PipelineExecutor(graph).run(ctx)
    except Exception:
        logger.warning("extract: graph run failed for %s", file_path, exc_info=True)
        return Extracted("", False)

    # Consolidated text = the 'consolidate' node's merged bundle when present,
    # else the first pooled text. (Mirrors runner.ingest_item's consolidation.)
    if "consolidate" in result.outputs and result.outputs["consolidate"].success:
        text = result.outputs["consolidate"].text or ""
    else:
        pooled = result.pooled_outputs()
        text = pooled[0].text if pooled else ""
    text = text.strip()
    if text:
        return await _scanned(text, result, surface)

    # No extractable text (e.g. an image nothing is set up to read, or a text-free media
    # file). Fall back to a structural descriptor from the exif/media metadata so the agent
    # at least knows WHAT was attached (format, size, dimensions, duration) rather than a
    # content-less blank — mirrors the graceful-degradation in runner._structural_descriptor.
    # An image skipped for want of an image model was never looked at, so the descriptor says
    # that instead of claiming it holds no text — when it is true that none is set up (a chosen
    # model that cannot run right now is a different sentence).
    from personalclaw.knowledge.pipeline.outcomes import SKIPPED
    from personalclaw.providers.image_input import IMAGE_USE_CASE, NO_IMAGE_MODEL

    needed_reader = any(
        o.status == SKIPPED and IMAGE_USE_CASE in o.needs for o in result.outcomes.values()
    )
    unread = UNREAD_NO_IMAGE_MODEL if needed_reader and NO_IMAGE_MODEL in _reasons(result) else ""
    descriptor = _structural_descriptor(
        file_path, item_type, result, unread, name or os.path.basename(file_path)
    )
    return Extracted(descriptor, False, unread)


def withheld(unread: str) -> str:
    """Why a file's text is withheld, as the end of a sentence about the file, for an ``unread``
    of :data:`UNREAD_REFUSED` or :data:`UNREAD_UNCHECKED` (the scan's own words,
    ``uploads.content_scan.WITHHELD``); ``""`` for any other."""
    from personalclaw.uploads.content_scan import REFUSED_CODE, UNCHECKED_CODE, WITHHELD

    code = {UNREAD_REFUSED: REFUSED_CODE, UNREAD_UNCHECKED: UNCHECKED_CODE}.get(unread, "")
    return WITHHELD.get(code, "")


async def _scanned(text: str, result, surface: str) -> Extracted:
    """*text*, read from the file, as it may be handed on: withheld when the document reader made
    it and the content scan refuses it or cannot check it.

    Only a reader's text is scanned. What a model wrote of a picture, a recording or a scanned
    page (OCR, a description, a transcript) is that model's reading of pixels or sound, which
    carries none of the characters the scan's rules are about, and a screenshot of a terminal
    would be refused for showing an ordinary command."""
    from personalclaw.knowledge.pipeline.nodes.text_nodes import reader_text
    from personalclaw.uploads.content_scan import REFUSED_CODE, ContentRefused, scan_text

    if not reader_text(result):
        return Extracted(text, True)
    try:
        await scan_text(text, surface=surface)
    except ContentRefused as exc:
        return Extracted(
            "", False, UNREAD_REFUSED if exc.code == REFUSED_CODE else UNREAD_UNCHECKED
        )
    return Extracted(text, True)


def _reasons(result) -> set[str]:
    """Every sentence the run's outcomes give as a cause: a step that waited on another, or
    that two backends could have run, carries each root reason as its own sentence."""
    return {c for o in result.outcomes.values() for c in (o.causes or (o.reason,))}


def _structural_descriptor(file_path: str, item_type: str, result, unread: str, name: str) -> str:
    """A one-line 'Image: foo.png (800×600, PNG)' style descriptor from the
    non-pooled structural metadata, when no text was extracted. ``name`` is what it calls
    the file."""
    meta: dict = {}
    for out in result.outputs.values():
        if out.metadata:
            meta.update(out.metadata)
    bits: list[str] = []
    if meta.get("width") and meta.get("height"):
        bits.append(f"{meta['width']}×{meta['height']}")
    if meta.get("format"):
        bits.append(str(meta["format"]))
    if meta.get("page_count"):
        bits.append(f"{meta['page_count']} pages")
    if meta.get("duration_seconds"):
        bits.append(f"{round(float(meta['duration_seconds']))}s")
    try:
        kb = os.path.getsize(file_path) / 1024
        bits.append(f"{kb:.0f} KB" if kb < 1024 else f"{kb / 1024:.1f} MB")
    except OSError:
        pass
    if not bits:
        return ""
    label = (item_type or "file").capitalize()
    tail = (
        "not read: no image model is set up."
        if unread == UNREAD_NO_IMAGE_MODEL
        else "no extractable text content."
    )
    return f"{label}: {name} ({', '.join(bits)}) — {tail}"

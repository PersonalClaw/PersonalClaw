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
as knowledge ingestion does, because it IS the same node graph.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


async def extract_file_content(file_path: str, mime: str | None = None) -> str:
    """Run the knowledge EXTRACTION graph for *file_path* and return the
    consolidated extracted text. Never raises — returns "" if extraction yields
    nothing (caller decides how to surface that). Pure extraction: no store, no
    insights/entities/embeddings/tags/title.
    """
    if not file_path or not os.path.isfile(file_path):
        return ""

    from personalclaw.knowledge import media
    from personalclaw.knowledge.pipeline import (
        NodeContext,
        ensure_nodes_registered,
        graph_for,
    )
    from personalclaw.knowledge.pipeline.executor import PipelineExecutor

    ensure_nodes_registered()

    # Route by the same classifier knowledge uses (ext + mime hint). Unknown →
    # 'document' so the reader stack still tries (degrades to raw bytes/utf-8).
    item_type = media.classify(os.path.basename(file_path), mime) or "document"

    try:
        graph = graph_for(item_type)
    except Exception:
        logger.warning("extract: graph build failed for type=%s", item_type, exc_info=True)
        return ""

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
        return ""

    # Consolidated text = the 'consolidate' node's merged bundle when present,
    # else the first pooled text. (Mirrors runner.ingest_item's consolidation.)
    if "consolidate" in result.outputs and result.outputs["consolidate"].success:
        text = result.outputs["consolidate"].text or ""
    else:
        pooled = result.pooled_outputs()
        text = pooled[0].text if pooled else ""
    text = text.strip()
    if text:
        return text

    # No extractable text (e.g. an image with no OCR/vision model configured, or a
    # text-free media file). Fall back to a structural descriptor from the exif/
    # media metadata so the agent at least knows WHAT was attached (format, size,
    # dimensions, duration) rather than a content-less blank — mirrors the
    # graceful-degradation in runner._structural_descriptor.
    return _structural_descriptor(file_path, item_type, result)


def _structural_descriptor(file_path: str, item_type: str, result) -> str:
    """A one-line 'Image: foo.png (800×600, PNG)' style descriptor from the
    non-pooled structural metadata, when no text was extracted."""
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
    return (
        f"{label}: {os.path.basename(file_path)} ({', '.join(bits)}) — no extractable text content."
    )

"""Pure-python text/document nodes (#30 Task A) — no model needed.

These cover the text-backed types that ship usable without any extraction
model-provider:
- ``passthrough`` — note/gist/bookmark/journal/fleeting: the raw content IS the
  extracted text.
- ``document_read`` — pdf/docx/sheet/slides/document: extract text via the existing
  ``readers.FileReader`` (pdfplumber/python-docx/python-pptx/html2text).
- ``consolidate`` — fan-in: merge multiple upstream text outputs into one (header-
  concat; no LLM in Task A — the reasoning-LLM consolidation is Task B/#47).
"""

from __future__ import annotations

import logging

from personalclaw.knowledge.pipeline.registry import register_node
from personalclaw.knowledge.pipeline.types import NodeContext, NodeOutput, PoolRow

logger = logging.getLogger(__name__)


class PassthroughNode:
    """The item's raw content is its extracted text (typed text items)."""

    node_type = "passthrough"
    backend = "native"
    uses_use_case = None

    async def run(self, inputs: dict[str, NodeOutput], ctx: NodeContext) -> NodeOutput:
        text = ctx.content or ""
        return NodeOutput(
            node_type=self.node_type,
            backend=self.backend,
            text=text,
            metadata={"chars": len(text)},
        )


class DocumentReadNode:
    """Extract text from a file via the existing reader stack (no model)."""

    node_type = "document_read"
    backend = "native"
    uses_use_case = None

    async def run(self, inputs: dict[str, NodeOutput], ctx: NodeContext) -> NodeOutput:
        if not ctx.file_path:
            # No file (e.g. a typed item routed here by mistake) → fall back to content.
            return NodeOutput(
                node_type=self.node_type, backend=self.backend, text=ctx.content or ""
            )
        import asyncio

        from personalclaw.knowledge.readers import FileReader

        reader = FileReader()
        loop = asyncio.get_running_loop()
        text, meta = await loop.run_in_executor(None, reader.read, ctx.file_path)
        if meta.get("format") == "error":
            return NodeOutput(
                node_type=self.node_type,
                backend=self.backend,
                success=False,
                error=str(meta.get("error", "read failed")),
            )
        # The reader derives `title` from the on-disk file stem, which for uploads is
        # the internal UUID filename — meaningless noise in the pool drill-down. Drop
        # it; the item's own title is the source of truth.
        meta.pop("title", None)
        return NodeOutput(
            node_type=self.node_type, backend=self.backend, text=text or "", metadata=meta
        )


class DocumentSliceNode:
    """Shape a document into role-sized slices (WATCHED-SOURCES §5) — no model, ever.

    Reads the upstream extraction (``document_read`` for an uploaded file,
    ``bookmark_scrape`` for a fetched paper) plus the file itself, runs the deterministic
    section cascade, and contributes ``slice:brief``/``slice:body``/``slice:meta`` rows to
    the item's pool. Its OWN output is not pooled: the reader's text is already there and
    a fourth copy of it would be noise in the drill-down.

    A document with no paper structure — a .txt, a spreadsheet, a link to a blog post —
    yields no slices and reports ``sliced: False`` at SUCCESS. That is not a failure: "no
    canonical sections here" is a true and useful answer, and marking it failed would push
    every non-paper document in the library to ``partial``.
    """

    node_type = "document_slice"
    backend = "native"
    uses_use_case = None

    async def run(self, inputs: dict[str, NodeOutput], ctx: NodeContext) -> NodeOutput:
        import asyncio

        from personalclaw.knowledge.slicing import reference_metadata, slice_document, slice_rows

        upstream = inputs.get("document_read") or inputs.get("bookmark_scrape")
        text = (getattr(upstream, "text", "") or "") or (ctx.content or "")
        # A fetched paper's bytes live in the sha256 source cache, not on the item, so the
        # scrape node hands their path over in metadata; an upload's path is on the item.
        cached = str((getattr(upstream, "metadata", None) or {}).get("source_path") or "")
        path = cached or (ctx.file_path or "")
        if not path and not text.strip():
            return NodeOutput(
                node_type=self.node_type,
                backend=self.backend,
                pooled=False,
                metadata={"sliced": False, "reason": "no document to slice"},
            )
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(None, lambda: slice_document(file_path=path, text=text))
        rows = slice_rows(result)
        if not rows:
            return NodeOutput(
                node_type=self.node_type,
                backend=self.backend,
                pooled=False,
                metadata={"sliced": False, "reason": "no canonical sections detected"},
            )
        return NodeOutput(
            node_type=self.node_type,
            backend=self.backend,
            pooled=False,
            pool_rows=[
                PoolRow(
                    node_type=row["node_type"],
                    text=row["text"],
                    metadata=row["metadata"],
                    backend=self.backend,
                )
                for row in rows
            ],
            metadata={"sliced": True, **reference_metadata(result)},
        )


class BookmarkScrapeNode:
    """Scrape a bookmark's URL → its extracted text (one logical doc).

    A bookmark item carries only a ``url`` at create time; this node fetches the
    page (reusing the web_url connector), returns the readable text for the pool,
    and surfaces a derived ``url_title``/``url_description`` in metadata for the
    runner to persist onto the item. If the item already has typed content (the
    user pasted text), that passes through and no fetch happens.

    A URL that is a DOCUMENT rather than a page (an arXiv id, a DOI, a ``.pdf``) takes
    the fetch-and-slice route instead: the HTML scraper on a PDF produces binary noise,
    which is what saving an arXiv link used to yield. That route reads through the sha256
    source cache, so re-saving or regenerating the same paper costs no network at all.
    """

    node_type = "bookmark_scrape"
    backend = "web"
    uses_use_case = None

    async def run(self, inputs: dict[str, NodeOutput], ctx: NodeContext) -> NodeOutput:
        # User-authored content wins — don't overwrite it with a scrape.
        if (ctx.content or "").strip():
            return NodeOutput(node_type=self.node_type, backend=self.backend, text=ctx.content)
        if not (ctx.url or "").strip():
            return NodeOutput(node_type=self.node_type, backend=self.backend, text="")
        document = await self._fetch_document(ctx)
        if document is not None:
            return document
        from personalclaw.knowledge.connectors.web_url import WebUrlConnector

        text, meta = await WebUrlConnector().fetch({"uri": ctx.url})
        if meta.get("error"):
            # Carry the error_kind ('unreachable' for network/DNS/timeout/HTTP-error) in
            # metadata so the runner can mark a reachability failure 'unreachable'
            # (retryable, URL still saved) rather than a hard 'failed'.
            return NodeOutput(
                node_type=self.node_type,
                backend=self.backend,
                success=False,
                error=str(meta["error"]),
                metadata={"error_kind": meta.get("error_kind") or "error"},
            )
        text = (text or "").strip()
        out_meta: dict = {"url": ctx.url}
        # Prefer the page's real <title>/og:title + meta description (from the HTML
        # head); fall back to a body-text heuristic when the page exposes neither.
        page_title = (meta.get("page_title") or "").strip()
        if not page_title:
            first_line = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
            page_title = first_line.lstrip("#").strip()
        if page_title:
            out_meta["url_title"] = page_title[:200]
        page_desc = (meta.get("page_description") or "").strip()
        if not page_desc:
            page_desc = " ".join(text.split())[:300]
        if page_desc:
            out_meta["url_description"] = page_desc
        return NodeOutput(
            node_type=self.node_type, backend=self.backend, text=text, metadata=out_meta
        )

    async def _fetch_document(self, ctx: NodeContext) -> NodeOutput | None:
        """Fetch a paper-shaped URL through the sha256 source cache, or None to fall
        through to the HTML scraper.

        None (not an empty output) for a plain web page: this is a routing decision, and
        a routing decision that returned an empty result would silently blank every
        bookmark the moment the sniffer changed its mind about a URL.
        """
        import asyncio

        from personalclaw.knowledge.slicing import (
            SOURCE_ARXIV,
            SOURCE_DOI,
            SOURCE_PDF,
            SourceFetchError,
            fetch_source,
            sniff_source,
        )

        ref = sniff_source(ctx.url)
        if ref is None or ref.kind not in (SOURCE_ARXIV, SOURCE_DOI, SOURCE_PDF):
            return None
        try:
            fetched = await fetch_source(ref)
        except (SourceFetchError, OSError, ValueError) as exc:
            # Same posture as a failed page scrape: 'unreachable' is retryable and keeps
            # the URL, rather than a hard failure on a link that may work tomorrow.
            return NodeOutput(
                node_type=self.node_type,
                backend=self.backend,
                success=False,
                error=f"could not fetch {ref.kind} source: {exc}"[:200],
                metadata={"error_kind": "unreachable"},
            )
        except Exception as exc:  # noqa: BLE001 — egress denial and transport faults alike
            return NodeOutput(
                node_type=self.node_type,
                backend=self.backend,
                success=False,
                error=f"could not fetch {ref.kind} source: {exc}"[:200],
                metadata={"error_kind": "unreachable"},
            )
        from personalclaw.knowledge.readers import FileReader

        loop = asyncio.get_running_loop()
        text, meta = await loop.run_in_executor(None, FileReader().read, str(fetched.path))
        if meta.get("format") == "error":
            return NodeOutput(
                node_type=self.node_type,
                backend=self.backend,
                success=False,
                error=str(meta.get("error", "read failed")),
                metadata={"error_kind": "error"},
            )
        out_meta: dict = {
            "url": ctx.url,
            "source_kind": ref.kind,
            "source_identifier": ref.identifier,
            "source_sha256": fetched.sha256,
            "source_from_cache": fetched.from_cache,
            # The slicer needs the BYTES, which live in the cache and not on the item.
            "source_path": str(fetched.path),
            "page_count": meta.get("page_count"),
        }
        first_line = next((ln.strip() for ln in (text or "").splitlines() if ln.strip()), "")
        if first_line:
            out_meta["url_title"] = first_line.lstrip("#").strip()[:200]
        return NodeOutput(
            node_type=self.node_type, backend=self.backend, text=text or "", metadata=out_meta
        )


class ConsolidateNode:
    """Fan-in: merge upstream text outputs into one document (header-concat).

    Task A is no-LLM — multiple texts are joined under labeled headers. The
    reasoning-LLM merge (consolidation_reasoning use-case) lands in Task B/#47.
    """

    node_type = "consolidate"
    backend = "concat"
    uses_use_case = None

    async def run(self, inputs: dict[str, NodeOutput], ctx: NodeContext) -> NodeOutput:
        texts = [(nt, o.text) for nt, o in inputs.items() if o.success and o.text]
        if not texts:
            return NodeOutput(
                node_type=self.node_type, backend=self.backend, text=ctx.content or ""
            )
        if len(texts) == 1:
            # Single upstream (e.g. document_read → consolidate): the text is identical
            # to what that node already pooled. Still expose it as the consolidated
            # output (the runner reads it for insights/embed), but keep it out of the
            # extracted-content pool so the drill-down doesn't show a duplicate entry.
            return NodeOutput(
                node_type=self.node_type, backend=self.backend, text=texts[0][1], pooled=False
            )
        parts = [f"## {nt}\n\n{txt}" for nt, txt in texts]
        return NodeOutput(
            node_type=self.node_type,
            backend=self.backend,
            text="\n\n".join(parts),
            metadata={"merged": [nt for nt, _ in texts]},
        )


def register() -> None:
    register_node(PassthroughNode())
    register_node(DocumentReadNode())
    register_node(DocumentSliceNode())
    register_node(BookmarkScrapeNode())
    register_node(ConsolidateNode())

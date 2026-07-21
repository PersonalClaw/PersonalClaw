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
from personalclaw.knowledge.pipeline.types import NodeContext, NodeOutput

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


class BookmarkScrapeNode:
    """Scrape a bookmark's URL → its extracted text (one logical doc).

    A bookmark item carries only a ``url`` at create time; this node fetches the
    page (reusing the web_url connector), returns the readable text for the pool,
    and surfaces a derived ``url_title``/``url_description`` in metadata for the
    runner to persist onto the item. If the item already has typed content (the
    user pasted text), that passes through and no fetch happens.
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
    register_node(BookmarkScrapeNode())
    register_node(ConsolidateNode())

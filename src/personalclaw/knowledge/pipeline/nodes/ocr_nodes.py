"""The no-model OCR path: rasterize a text-less PDF, then read it with an OCR engine.

Two nodes, both vendor-free.

``pdf_rasterize`` (pure-python, pypdfium2 — already a core dependency) turns the pages of
a PDF with NO text layer into page images. It only ever runs behind ``document_read``'s
``no-text-layer`` classification, so a normal PDF never reaches it, and it enforces the
rasterize ceilings (ARCC ``cnt_eMkU5kkpTaEk65``, "enforce file upload size limits"): a
bounded page count and a bounded pixel budget per page, so a PDF declaring ten thousand
pages or a gigapixel MediaBox is capped, not an OOM.

``ocr`` / ``engine`` is the second backend for the ``ocr`` node type. It resolves whatever
engine an installed ``ocr`` app registered and holds no engine itself — with no app
installed it reports unavailable and the executor skips the node, which is exactly the
pre-seam behaviour. Its sibling ``ocr`` / ``vision-llm`` (media_nodes) stays the default;
the executor prefers it whenever a vision model IS bound and only reaches for this one
when that route cannot run.
"""

from __future__ import annotations

import asyncio
import logging
import os

from personalclaw.knowledge.pipeline.registry import register_node
from personalclaw.knowledge.pipeline.types import NodeContext, NodeOutput

logger = logging.getLogger(__name__)

#: Hard ceiling on pages rasterized for OCR in one item. A scanned book is not an
#: ingestion unit; the cap keeps a pathological or hostile PDF bounded and is REPORTED
#: (``pages_capped``) so a truncated read is never silently presented as a whole one.
MAX_OCR_PAGES = 40

#: Pixel budget per rendered page. ~2480×3508 is A4 at 300 dpi, the resolution OCR
#: engines are tuned for; the scale is computed DOWN from the page's own size to land
#: under this, so a page declaring absurd dimensions renders small rather than enormous.
MAX_PAGE_PIXELS = 2480 * 3508

#: Upper bound on the render scale. Guards the other direction: a tiny MediaBox would
#: otherwise be scaled up without limit trying to reach the pixel budget.
MAX_RENDER_SCALE = 4.0


class PdfRasterizeNode:
    """Render a text-less PDF's pages to PNGs for OCR. Pure-python, no model, capped."""

    node_type = "pdf_rasterize"
    backend = "pypdfium2"
    uses_use_case = None

    def available(self) -> bool:
        """Live probe: pypdfium2 importable. It is a core dependency, so this is True in a
        normal install — but a source checkout missing the optional wheel must degrade to a
        skip rather than raise on the ingest path."""
        try:
            import pypdfium2  # noqa: F401
        except Exception:
            return False
        return True

    async def run(self, inputs: dict[str, NodeOutput], ctx: NodeContext) -> NodeOutput:
        if not ctx.file_path:
            return NodeOutput(
                node_type=self.node_type, backend=self.backend, success=False, error="no file"
            )
        loop = asyncio.get_running_loop()
        try:
            paths, meta = await loop.run_in_executor(
                None, self._render, ctx.file_path, ctx.work_dir
            )
        except Exception as exc:  # noqa: BLE001 - a broken PDF must not fail the whole item
            logger.info("pdf rasterize failed for %s", ctx.file_path, exc_info=True)
            return NodeOutput(
                node_type=self.node_type,
                backend=self.backend,
                success=False,
                error=f"rasterize failed: {exc}",
                pooled=False,
            )
        if not paths:
            return NodeOutput(
                node_type=self.node_type,
                backend=self.backend,
                success=False,
                error="no pages rendered",
                pooled=False,
            )
        return NodeOutput(
            node_type=self.node_type,
            backend=self.backend,
            artifacts=paths,
            metadata=meta,
            # Structural, like the a/v split and frame-extract: it produces inputs for the
            # next node, not text for the item's pool.
            pooled=False,
        )

    @staticmethod
    def _render(pdf_path: str, work_dir: str) -> tuple[list[str], dict]:
        """Render up to :data:`MAX_OCR_PAGES` pages, each under :data:`MAX_PAGE_PIXELS`.

        Synchronous (called in an executor). Returns the page image paths in page order
        plus what was capped, so the caller can report a truncated read honestly.
        """
        import pypdfium2

        out_dir = work_dir or os.path.dirname(pdf_path)
        os.makedirs(out_dir, exist_ok=True)
        paths: list[str] = []
        doc = pypdfium2.PdfDocument(pdf_path)
        try:
            declared = len(doc)
            limit = min(declared, MAX_OCR_PAGES)
            for index in range(limit):
                page = doc[index]
                width, height = page.get_size()
                scale = _render_scale(width, height)
                bitmap = page.render(scale=scale)
                target = os.path.join(out_dir, f"ocr_page_{index + 1:04d}.png")
                bitmap.to_pil().save(target, format="PNG")
                paths.append(target)
        finally:
            doc.close()
        return paths, {
            "page_count": declared,
            "pages_rasterized": len(paths),
            "pages_capped": declared > limit,
            "page_cap": MAX_OCR_PAGES,
        }


def _render_scale(width: float, height: float) -> float:
    """The scale that keeps a rendered ``width × height`` page under the pixel budget.

    A page whose declared size is already over budget at 1× renders BELOW 1× — that is the
    gigapixel-page cap, and it is arithmetic rather than a guess, so there is no page size
    for which this returns something unbounded. A degenerate (zero/negative) size falls back
    to 1×: pypdfium2 will refuse it, and refusing is the right outcome for a malformed page.

    The scale is NOT the plain square root of the budget ratio. The renderer rounds each
    dimension UP to a whole pixel, so the exact square root overshoots — measured at
    2950×2950 = 8,702,500 against an 8,699,840 budget for a 20,000-point page. Bounding the
    rounded bitmap instead means bounding ``(w·s + 1)(h·s + 1)``, which is a quadratic in
    ``s`` with one positive root; using it makes the ceiling a real bound rather than one
    that holds to within a rounding error.
    """
    if width <= 0 or height <= 0:
        return 1.0
    area = width * height
    # (area)·s² + (width + height)·s + (1 − budget) ≤ 0  →  s ≤ the positive root.
    discriminant = (width + height) ** 2 + 4 * area * (MAX_PAGE_PIXELS - 1)
    scale = (-(width + height) + discriminant**0.5) / (2 * area)
    return min(scale, MAX_RENDER_SCALE)


class OcrEngineNode:
    """Read text from images with the OCR engine an installed ``ocr`` app registered.

    Second backend for the ``ocr`` node type. Holds no engine and names no vendor: it asks
    :func:`personalclaw.ocr.registry.active_ocr` and delegates. Every input passes core's
    true-type gate before the engine sees it, so a decoder is never handed bytes that only
    claim to be an image.
    """

    node_type = "ocr"
    backend = "engine"
    uses_use_case = None

    def available(self) -> bool:
        """Live probe of the REGISTRY, not of a symbol: True only when a registered OCR
        provider's own ``available()`` says it can run now. With no app installed this is
        False and the executor skips the node — today's behaviour, unchanged."""
        from personalclaw.ocr.registry import ocr_available

        return ocr_available()

    async def run(self, inputs: dict[str, NodeOutput], ctx: NodeContext) -> NodeOutput:
        from personalclaw.knowledge.pipeline.nodes.media_nodes import _images_from
        from personalclaw.ocr.filetype import TrueTypeRejected, assert_image
        from personalclaw.ocr.provider import OcrError
        from personalclaw.ocr.registry import active_ocr

        provider = active_ocr()
        if provider is None:
            # Reachable only if the app was disabled between the availability probe and
            # here. An explicit signal, not a crash.
            return NodeOutput(
                node_type=self.node_type,
                backend=self.backend,
                success=False,
                error="no OCR engine available",
                metadata={"ocr": "unavailable"},
            )
        images = _images_from(inputs, ctx)
        if not images:
            return NodeOutput(
                node_type=self.node_type, backend=self.backend, success=False, error="no image"
            )
        accepted: list[str] = []
        rejected: list[str] = []
        for path in images:
            try:
                assert_image(path)
            except TrueTypeRejected as exc:
                logger.info("OCR refused %s: %s", path, exc)
                rejected.append(str(exc))
                continue
            accepted.append(path)
        if not accepted:
            return NodeOutput(
                node_type=self.node_type,
                backend=self.backend,
                success=False,
                error="; ".join(rejected) or "no accepted image",
                metadata={"ocr": "rejected", "ocr_rejected": rejected},
            )
        try:
            result = await provider.recognize(accepted)
        except OcrError as exc:
            return NodeOutput(
                node_type=self.node_type,
                backend=self.backend,
                success=False,
                error=str(exc),
                metadata={"ocr": "failed"},
            )
        meta: dict = {
            "ocr": "engine",
            "ocr_engine": result.engine or provider.engine_id,
            "ocr_pages": len(result.pages) or len(accepted),
        }
        if rejected:
            meta["ocr_rejected"] = rejected
        meta.update(result.metadata or {})
        return NodeOutput(
            node_type=self.node_type,
            backend=self.backend,
            text=result.text,
            metadata=meta,
            classification="text-heavy",
        )


def register() -> None:
    for node in (PdfRasterizeNode(), OcrEngineNode()):
        register_node(node)

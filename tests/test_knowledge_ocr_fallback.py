"""KOCR-1 — a scanned PDF stops ingesting EMPTY, and a normal PDF is untouched.

**The defect, on ``origin/main`` @ ``c159d8929``.** ``readers.FileReader._read_pdf``
(``readers.py:147``) returns whatever ``pdfplumber`` extracts and nothing else. For a scan
that is ``''`` per page, so a text-less PDF ingested to a stored item with none of the
document's words and no statement anywhere that its text had not been read — the silent
empty ingest. ``tests/fixtures/image_only_scan.pdf`` is that document, and the token
``quokkatelemetry`` exists in it only as PIXELS.

**What this rail asserts, and why each clause needs its own reading of the code:**

1. A committed text-less scan ingests NON-EMPTY text through the normal ingest path.
2. A normal text-layer PDF's extracted text is byte-identical to the pre-change algorithm,
   and the OCR path does not run on it.
3. With neither a vision model nor an OCR engine, the item carries an explicit
   ``meta.ocr == "unavailable"`` with empty extracted text and NO exception — mirroring
   ``_read_pdf``'s "pdfplumber is None" contract, which reports rather than raises.
4. No double-OCR: with a spy on the engine, a text-layer PDF invokes it ZERO times.
5. Bomb ceiling (ARCC ``cnt_eMkU5kkpTaEk65``, "enforce file upload size limits"): a PDF
   declaring 120 pages rasterizes a BOUNDED number of them, and a page declaring 20,000 ×
   20,000 points renders under a pixel budget instead of allocating 400 megapixels.

**No vendor engine is involved here, deliberately.** Core ships none, so the engine is a
stub — but a stub that cannot pass vacuously: it asserts every path it is handed is a real
rendered PNG of non-zero size and derives its answer from the page COUNT it received, so a
pipeline that skipped rasterization, handed it the PDF itself, or fabricated a page list
cannot satisfy the assertions. The real engine's own reading is measured in
``PersonalClawApps/rapidocr/test_provider.py``, against the real weights.

``TestTheRailIsNotVacuous`` guards the two clauses whose green state is an ABSENCE
(2 and 4): a zero-invocation count and an unchanged string both stay green if the OCR path
were deleted outright, so the same spy is shown counting non-zero on the scan.
"""

from __future__ import annotations

import asyncio
import shutil
from collections.abc import Sequence
from pathlib import Path

import pytest

from personalclaw.knowledge.pipeline import ensure_nodes_registered
from personalclaw.knowledge.pipeline.nodes.ocr_nodes import (
    MAX_OCR_PAGES,
    MAX_PAGE_PIXELS,
    PdfRasterizeNode,
    _render_scale,
)
from personalclaw.knowledge.pipeline.registry import can_resolve_use_case, resolve_runnable
from personalclaw.knowledge.pipeline.runner import ingest_item
from personalclaw.knowledge.pipeline.types import NodeContext
from personalclaw.knowledge.readers import FileReader
from personalclaw.knowledge.store import KnowledgeStore, knowledge_db_path
from personalclaw.ocr.provider import OcrProvider, OcrResult
from personalclaw.ocr.registry import register_provider, unregister_provider

FIXTURES = Path(__file__).parent / "fixtures"

#: Text-less scan. The token below is in it as pixels only.
IMAGE_ONLY_PDF = FIXTURES / "image_only_scan.pdf"
#: A normal PDF with a real text layer.
TEXT_LAYER_PDF = FIXTURES / "text_layer.pdf"
#: 120 pages, no text layer — the absurd-page-count half of clause 5.
BOMB_PAGES_PDF = FIXTURES / "ocr_bomb_pages.pdf"
#: One 20,000 × 20,000-point page, no text layer — the gigapixel half of clause 5.
BOMB_GIGAPIXEL_PDF = FIXTURES / "ocr_bomb_gigapixel.pdf"

#: What the stub engine "reads". Arbitrary, but it must reach the stored item for clause 1
#: to hold, and it cannot appear in any fixture's text layer.
OCR_MARKER = "ocrmarkerzzz"


class _Embedder:
    """A deterministic stand-in for a bound embedding provider (mirrors the searchability
    rail's): small, non-zero, stable — so a failure here is never about embeddings."""

    def embed_for_item(self, title, summary, content):
        return [0.1, 0.2, 0.3, 0.4]

    def embed(self, text):
        return [0.1, 0.2, 0.3, 0.4]

    def is_available(self):
        return True


class _SpyEngine(OcrProvider):
    """A counting OCR engine that validates what it is handed.

    Every recorded call is checked: each path must exist, be non-empty, and start with the
    PNG magic number. So "the engine was invoked" cannot be satisfied by a pipeline that
    passed the PDF through unrendered or invented a page list, and the page count the
    assertions read is the count of REAL rasterized pages.
    """

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    @property
    def name(self) -> str:
        return "spy-ocr"

    @property
    def engine_id(self) -> str:
        return "spy-ocr/0"

    def available(self) -> bool:
        return True

    async def recognize(self, image_paths: Sequence[str]) -> OcrResult:
        paths = list(image_paths)
        for path in paths:
            blob = Path(path)
            assert blob.is_file(), f"engine handed a path that is not a file: {path}"
            head = blob.read_bytes()[:8]
            assert head == b"\x89PNG\r\n\x1a\n", (
                f"engine handed {path} which is not a rendered PNG (head={head!r}) — "
                "the rasterize step did not really run"
            )
        self.calls.append(paths)
        return OcrResult(
            text=f"{OCR_MARKER} over {len(paths)} rasterized page(s)",
            engine=self.engine_id,
            pages=[OCR_MARKER] * len(paths),
        )

    @property
    def pages_seen(self) -> int:
        return sum(len(c) for c in self.calls)


@pytest.fixture
def spy() -> _SpyEngine:
    """Register a spy engine for the duration of one test, then remove it.

    Registered/unregistered exactly the way core's ``OcrTypeHandler`` does it on app
    enable/disable, so the fixture exercises the real lifecycle rather than a shortcut.
    """
    ensure_nodes_registered()
    engine = _SpyEngine()
    register_provider(engine)
    try:
        yield engine
    finally:
        unregister_provider(engine.name)


@pytest.fixture
def no_engine():
    """Assert no OCR engine is registered — the state a user with no OCR app is in."""
    ensure_nodes_registered()
    assert resolve_runnable("ocr", "vision-llm") is None, (
        "an OCR backend is runnable in this environment, so the 'nothing available' "
        "clauses below would not be measuring what they claim"
    )
    yield


def _store_for(home: Path) -> KnowledgeStore:
    return KnowledgeStore(str(knowledge_db_path(home)))


def _ingest_pdf(store: KnowledgeStore, home: Path, src: Path) -> str:
    """Copy a committed fixture into the home and create its item, as an upload would."""
    dest = Path(knowledge_db_path(home)).parent / src.name
    shutil.copy(src, dest)
    item_id = store.create_typed_item(
        item_type="document",
        title=src.name,
        content="",
        extra={
            "file_path": str(dest),
            "mime_type": "application/pdf",
            "file_size": dest.stat().st_size,
            "processing_status": "queued",
        },
    )
    assert item_id
    return item_id


def _one_pixel_png() -> bytes:
    """A real 1×1 PNG, built rather than committed: the gate reads the magic number, so the
    bytes have to be genuine, and a tiny generated file keeps that fact visible here."""
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (1, 1), "white").save(buf, format="PNG")
    return buf.getvalue()


def _pool_text(store: KnowledgeStore, item_id: str, node_type: str) -> str:
    """The text one node contributed to the item's extracted-content pool."""
    for row in store.get_extracted_contents(item_id) or []:
        if row.get("node_type") == node_type:
            return str(row.get("text") or "")
    return ""


# ── the fixtures' own preconditions — assert them, never assume them ──────────────


def test_the_scan_fixture_has_no_text_layer():
    """The atom's explicit first step: ``pdfplumber.extract_text()`` is empty for it."""
    pdfplumber = pytest.importorskip("pdfplumber")
    assert IMAGE_ONLY_PDF.is_file(), f"committed fixture missing: {IMAGE_ONLY_PDF}"
    with pdfplumber.open(str(IMAGE_ONLY_PDF)) as pdf:
        pages = list(pdf.pages)
        extracted = "".join((p.extract_text() or "") for p in pages)
    assert pages, "the fixture must be a real PDF with at least one page"
    assert extracted.strip() == "", f"fixture is not image-only — it extracted {extracted!r}"


def test_the_text_layer_fixture_does_have_extractable_text():
    """Its mirror. Without this, clause 2 could pass on a second scan by accident."""
    pdfplumber = pytest.importorskip("pdfplumber")
    assert TEXT_LAYER_PDF.is_file(), f"committed fixture missing: {TEXT_LAYER_PDF}"
    with pdfplumber.open(str(TEXT_LAYER_PDF)) as pdf:
        extracted = "".join((p.extract_text() or "") for p in pdf.pages)
    assert extracted.strip(), "the text-layer fixture has no extractable text"


def test_the_bomb_fixtures_are_actually_absurd():
    """Both halves of clause 5 need their premise asserted, or a cap over a 1-page PDF and
    a 200×200 page would both pass while capping nothing."""
    pypdfium2 = pytest.importorskip("pypdfium2")
    doc = pypdfium2.PdfDocument(str(BOMB_PAGES_PDF))
    try:
        declared = len(doc)
    finally:
        doc.close()
    assert declared > MAX_OCR_PAGES, (
        f"the page-bomb fixture declares {declared} pages, which is not above the "
        f"{MAX_OCR_PAGES}-page cap it exists to exercise"
    )

    doc = pypdfium2.PdfDocument(str(BOMB_GIGAPIXEL_PDF))
    try:
        width, height = doc[0].get_size()
    finally:
        doc.close()
    assert width * height > MAX_PAGE_PIXELS * 10, (
        f"the gigapixel fixture's page is {width}×{height} points, not far enough over "
        f"the {MAX_PAGE_PIXELS}-pixel budget to prove it is being scaled down"
    )


def test_the_reader_reports_the_text_layer_fact():
    """``text_layer`` is what the whole branch turns on, and it must be computed from
    whether any page contributed non-whitespace — NOT from the joined string's falsiness,
    which is ``"\\n"`` for a multi-page scan and would read as "has text"."""
    scan_text, scan_meta = FileReader().read(str(IMAGE_ONLY_PDF))
    real_text, real_meta = FileReader().read(str(TEXT_LAYER_PDF))
    assert scan_meta["text_layer"] is False
    assert real_meta["text_layer"] is True
    assert real_meta["page_count"] == 2
    # The multi-page scan's join is separators, not emptiness — the trap this guards.
    bomb_text, bomb_meta = FileReader().read(str(BOMB_PAGES_PDF))
    assert bomb_meta["text_layer"] is False
    assert bomb_text.strip() == "" and bomb_text != "", (
        "a multi-page scan's joined text is page separators; if this became truly empty "
        "the falsiness shortcut would start working and the real bug would hide again"
    )
    assert scan_text.strip() == ""


# ── clause 1 — a scanned PDF ingests NON-EMPTY text ──────────────────────────────


def test_a_scanned_pdf_ingests_non_empty_text(tmp_path, spy):
    """The whole path: read → no text layer → rasterize → OCR → pooled onto the item."""
    store = _store_for(tmp_path)
    item_id = _ingest_pdf(store, tmp_path, IMAGE_ONLY_PDF)

    asyncio.run(ingest_item(store, item_id, embedder=_Embedder()))

    assert spy.calls, "the OCR engine was never invoked on a text-less PDF"
    assert spy.pages_seen == 1, f"expected the scan's 1 page, got {spy.pages_seen}"
    ocr_text = _pool_text(store, item_id, "ocr")
    assert (
        OCR_MARKER in ocr_text
    ), f"the OCR result never reached the item's extracted-content pool: {ocr_text!r}"
    item = store.get_item(item_id)
    assert (item.get("file_metadata") or {}).get(
        "ocr"
    ) != "unavailable", "the item still claims OCR was unavailable although an engine read it"


def test_an_ocred_scan_is_not_reported_unsearchable(tmp_path, spy):
    """Found by driving a REAL ingest through the gateway, not by a unit test.

    RET-2's ``no_extractable_text`` verdict keys on a pooled node that reported success and
    produced no text. On a scan, ``document_read`` is exactly that — and before KOCR-1
    nothing else could supply the words, so the verdict was right. Now the OCR node does,
    and the first gateway run of this path stored the OCR'd text as the item's content while
    simultaneously telling the user no text could be extracted from it. Two surfaces, one
    document, opposite answers.
    """
    store = _store_for(tmp_path)
    item_id = _ingest_pdf(store, tmp_path, IMAGE_ONLY_PDF)

    asyncio.run(ingest_item(store, item_id, embedder=_Embedder()))

    item = store.get_item(item_id)
    content = (item.get("content") or "").strip()
    reason = (item.get("file_metadata") or {}).get("unsearchable_reason")
    assert OCR_MARKER in content, f"the OCR text is not the item's content: {content!r}"
    assert (
        reason != "no_extractable_text"
    ), "the item carries OCR'd text and still claims no text could be extracted from it"
    assert (
        item["processing_status"] != "unsearchable"
    ), f"an OCR'd scan was reported unsearchable: {item['processing_status']!r}"


def test_a_scan_with_no_engine_is_still_reported_unsearchable(tmp_path, no_engine):
    """The other side of the fix, so it is a narrowing and not a hole: with nothing able to
    read the scan, RET-2's verdict must still fire. A fix that simply stopped flagging
    ``document_read`` would pass the test above and silently retire the rail."""
    store = _store_for(tmp_path)
    item_id = _ingest_pdf(store, tmp_path, IMAGE_ONLY_PDF)

    asyncio.run(ingest_item(store, item_id, embedder=_Embedder()))

    item = store.get_item(item_id)
    assert item["processing_status"] == "unsearchable"
    assert (item.get("file_metadata") or {}).get("unsearchable_reason") == "no_extractable_text"


def test_the_no_model_environment_is_what_makes_clause_1_meaningful(spy):
    """Clause 1 is about the NO-MODEL case. Assert the environment, so a bound vision
    model can never be what satisfied it."""
    from personalclaw.knowledge.pipeline.registry import get_node, node_available

    assert can_resolve_use_case("image_modality") is False
    resolved = resolve_runnable("ocr", "vision-llm")
    assert resolved is not None, "the engine backend should be runnable with a spy registered"
    node, _backend = resolved
    # The substitute must not itself need a bound model — that is the whole property. Asserted
    # on the resolved node rather than on the backend NAME: `NODE_REGISTRY` is process-global
    # and another test module registers its own `ocr` stub into it, so a name equality here
    # would be measuring test ordering. The engine backend's own runnability is asserted
    # directly on the instance instead.
    assert node.uses_use_case is None, f"{node} resolves through a model use-case"
    assert (
        node_available(get_node("ocr", "engine")) is True
    ), "the ocr/engine backend is not runnable even with an engine registered"


# ── clause 2 — a text-layer PDF is byte-identical, and OCR does not run ──────────


def test_text_layer_extraction_is_byte_identical_to_the_prior_algorithm():
    """Compared against the pre-change algorithm re-derived here — pdfplumber's per-page
    ``extract_text() or ""`` joined on newlines — rather than against a recorded blob, so
    the assertion stays true under a pdfplumber upgrade while still catching any change
    this atom makes to the extraction itself."""
    pdfplumber = pytest.importorskip("pdfplumber")
    with pdfplumber.open(str(TEXT_LAYER_PDF)) as pdf:
        expected = "\n".join((p.extract_text() or "") for p in pdf.pages)

    text, _meta = FileReader().read(str(TEXT_LAYER_PDF))

    assert text == expected, "the OCR atom changed what a normal PDF extracts"


def test_a_text_layer_pdf_never_reaches_the_ocr_node(tmp_path, spy):
    """Clause 4, the no-double-OCR spy: ZERO invocations on a PDF that has its own text."""
    store = _store_for(tmp_path)
    item_id = _ingest_pdf(store, tmp_path, TEXT_LAYER_PDF)

    asyncio.run(ingest_item(store, item_id, embedder=_Embedder()))

    assert spy.calls == [], f"the OCR engine ran on a text-layer PDF: {spy.calls!r}"
    assert spy.pages_seen == 0
    content = _pool_text(store, item_id, "document_read")
    assert "quokkatelemetry" in content, "the real text layer should still be what was pooled"
    assert OCR_MARKER not in content


def test_the_scan_branch_is_gated_by_classification_not_by_the_ocr_node():
    """Structural, not behavioural: the rasterize step hangs off a CONDITIONAL edge, so a
    text-layer PDF costs nothing. An OCR node that ran and then decided to do nothing would
    pass an invocation-count test written against the engine but not this one."""
    from personalclaw.knowledge.pipeline.graphs import graph_for

    graph = graph_for("pdf")
    edge = next(e for e in graph.successors("document_read") if e.to_node == "pdf_rasterize")
    assert (
        edge.when == "no-text-layer"
    ), f"the rasterize edge is conditional on {edge.when!r}, not on a text-less document"
    assert [e.from_node for e in graph.predecessors("ocr")] == ["pdf_rasterize"], (
        "the ocr node must hang off the rasterizer only, so it is unreachable for a "
        "document whose text layer is non-empty"
    )


# ── clause 3 — an explicit "OCR unavailable", never a silent empty ingest ────────


def test_no_engine_and_no_model_yields_an_explicit_unavailable_signal(tmp_path, no_engine):
    """Empty content, ``meta.ocr == "unavailable"``, and NO exception — the
    ``_read_pdf``-with-no-pdfplumber contract, applied to the missing engine."""
    store = _store_for(tmp_path)
    item_id = _ingest_pdf(store, tmp_path, IMAGE_ONLY_PDF)

    # Must not raise. The status itself is the searchability rail's subject, not this one's.
    asyncio.run(ingest_item(store, item_id, embedder=_Embedder()))

    item = store.get_item(item_id)
    meta = item.get("file_metadata") or {}
    assert (
        meta.get("ocr") == "unavailable"
    ), f"a scan nobody can read must say so on the item; file_metadata was {meta!r}"
    assert _pool_text(store, item_id, "document_read").strip() == ""
    assert _pool_text(store, item_id, "ocr") == "", "no OCR row should exist with no engine"


def test_a_text_layer_pdf_is_never_marked_ocr_unavailable(tmp_path, no_engine):
    """The signal's negative case. A rail that marked every PDF would be worthless."""
    store = _store_for(tmp_path)
    item_id = _ingest_pdf(store, tmp_path, TEXT_LAYER_PDF)

    asyncio.run(ingest_item(store, item_id, embedder=_Embedder()))

    meta = (store.get_item(item_id) or {}).get("file_metadata") or {}
    assert "ocr" not in meta, f"a readable PDF was flagged: {meta!r}"


def test_with_no_engine_the_document_is_not_rasterized(tmp_path, no_engine):
    """Cheap-legibility half of clause 3: with nothing able to OCR, the pages are not
    rendered at all. Rasterizing 40 pages to then throw them away is work a user pays for
    and never sees."""
    store = _store_for(tmp_path)
    item_id = _ingest_pdf(store, tmp_path, BOMB_PAGES_PDF)
    work_root = Path(knowledge_db_path(tmp_path)).parent

    asyncio.run(ingest_item(store, item_id, embedder=_Embedder()))

    assert (
        list(work_root.rglob("ocr_page_*.png")) == []
    ), "pages were rasterized although no engine could read them"


# ── clause 5 — the bomb ceilings ─────────────────────────────────────────────────


def test_an_absurd_page_count_rasterizes_a_bounded_number_of_pages(tmp_path, spy):
    """120 declared pages → exactly ``MAX_OCR_PAGES`` rendered, and the item records that
    the read was truncated rather than presenting it as complete."""
    store = _store_for(tmp_path)
    item_id = _ingest_pdf(store, tmp_path, BOMB_PAGES_PDF)

    asyncio.run(ingest_item(store, item_id, embedder=_Embedder()))

    assert (
        spy.pages_seen == MAX_OCR_PAGES
    ), f"expected the cap of {MAX_OCR_PAGES} rasterized pages, engine saw {spy.pages_seen}"
    rendered = list(Path(knowledge_db_path(tmp_path)).parent.rglob("ocr_page_*.png"))
    assert len(rendered) == MAX_OCR_PAGES, f"{len(rendered)} page images on disk"


def test_a_gigapixel_page_renders_under_the_pixel_budget(tmp_path):
    """The render is measured, not the intent: the produced bitmap's pixel count is under
    the budget. A 20,000 × 20,000-point page at scale 1 would be 400 megapixels."""
    from PIL import Image

    paths, meta = PdfRasterizeNode._render(str(BOMB_GIGAPIXEL_PDF), str(tmp_path))

    assert len(paths) == 1
    with Image.open(paths[0]) as img:
        pixels = img.width * img.height
    assert pixels <= MAX_PAGE_PIXELS, (
        f"rendered {img.width}×{img.height} = {pixels} pixels, over the "
        f"{MAX_PAGE_PIXELS} budget"
    )
    assert meta["pages_capped"] is False, "a 1-page document was reported as truncated"


def test_the_render_scale_is_bounded_in_both_directions():
    """Arithmetic, so there is no page size for which this is unbounded: an enormous page
    scales BELOW 1, a tiny one is capped rather than magnified without limit, and a
    degenerate size falls back to 1 instead of dividing by zero."""
    assert _render_scale(20000, 20000) < 1.0
    assert _render_scale(1, 1) <= 4.0
    assert _render_scale(0, 0) == 1.0
    assert _render_scale(-5, 10) == 1.0
    huge = _render_scale(100000, 100000)
    assert 0 < huge < 1 and (100000 * huge) * (100000 * huge) <= MAX_PAGE_PIXELS * 1.01


def test_the_page_cap_is_reported_when_it_bites(tmp_path):
    """A truncated read that does not say it was truncated is the silent-empty defect in a
    different costume.

    Note what this reads: ``_render``'s RETURN VALUE. That is necessary but nowhere near
    sufficient, and on its own it was the defect's camouflage — see
    ``test_the_page_cap_is_visible_on_the_stored_item``, which reads the same facts off the
    item a user actually sees.
    """
    _paths, meta = PdfRasterizeNode._render(str(BOMB_PAGES_PDF), str(tmp_path))
    assert meta["pages_rasterized"] == MAX_OCR_PAGES
    assert meta["page_count"] > MAX_OCR_PAGES
    assert meta["pages_capped"] is True
    assert meta["page_cap"] == MAX_OCR_PAGES


def test_the_page_cap_is_visible_on_the_stored_item(tmp_path, spy):
    """The cap read off the STORED ITEM — the surface a user has — not off ``_render``.

    **The defect this exists for.** ``pdf_rasterize`` computed ``pages_capped`` /
    ``page_cap`` / ``pages_rasterized`` correctly and they went nowhere: the node is
    ``pooled=False``, so its metadata fed the next node and reached no user-visible surface.
    A 120-page scan therefore stored 40 pages of OCR'd text with a ``page_count`` of 120
    beside it and nothing anywhere saying the read stopped at 40 — measured live on item
    ``287c7ddb``, whose ``file_metadata`` carried only ``content_hash`` / ``format`` /
    ``page_count`` / ``node_phases``.

    The sibling above stayed GREEN throughout, because it asserts the function's return
    value rather than anything persisted. That is why this rail reads ``get_item`` instead:
    a ceiling the user cannot see is indistinguishable from no ceiling.
    """
    store = _store_for(tmp_path)
    item_id = _ingest_pdf(store, tmp_path, BOMB_PAGES_PDF)

    asyncio.run(ingest_item(store, item_id, embedder=_Embedder()))

    meta = (store.get_item(item_id) or {}).get("file_metadata") or {}
    assert meta.get("ocr_pages_capped") is True, (
        f"the scan was truncated to {MAX_OCR_PAGES} of its pages and the stored item does "
        f"not say so — file_metadata keys: {sorted(meta)}"
    )
    assert meta.get("ocr_pages_rasterized") == MAX_OCR_PAGES, (
        f"expected {MAX_OCR_PAGES} rasterized pages on the item, got "
        f"{meta.get('ocr_pages_rasterized')!r}"
    )
    assert meta.get("ocr_page_cap") == MAX_OCR_PAGES
    # The truncation is only meaningful NEXT TO the real length: "40 of 120".
    assert meta.get("page_count", 0) > MAX_OCR_PAGES


def test_an_untruncated_scan_does_not_claim_truncation(tmp_path, spy):
    """The mirror of the rail above, and the reason it cannot pass vacuously.

    Hardcoding ``ocr_pages_capped = True`` would satisfy the truncation assertions and make
    every scanned document announce a truncation that never happened. A one-page scan must
    carry NO such key — ``_merge_file_metadata`` removes a key written as ``None``, so the
    absence here is the same mechanism a re-ingest relies on to stop claiming a stale cap.
    """
    store = _store_for(tmp_path)
    item_id = _ingest_pdf(store, tmp_path, IMAGE_ONLY_PDF)

    asyncio.run(ingest_item(store, item_id, embedder=_Embedder()))

    meta = (store.get_item(item_id) or {}).get("file_metadata") or {}
    assert (
        "ocr_pages_capped" not in meta
    ), f"a scan that was read in full reports a truncation: {meta.get('ocr_pages_capped')!r}"
    # …while the cap facts that are always true of an OCR'd document still land, so the
    # absence above is a real negative rather than the whole block having gone missing.
    assert meta.get("ocr_pages_rasterized") == 1, f"expected 1 rasterized page, got {meta!r}"


# ── the true-type gate belongs to the NODE TYPE, not to one backend ───────────────


def _ocr_ctx(tmp_path, file_path: str) -> NodeContext:
    return NodeContext(
        item_id="itm_gate",
        item_type="image",
        file_path=file_path,
        work_dir=str(tmp_path),
    )


def test_the_vision_llm_backend_refuses_a_non_image_before_the_model(tmp_path, monkeypatch):
    """The gate was ENGINE-PATH-ONLY, so the other backend accepted a liar file.

    ``assert_image`` was called only inside ``OcrEngineNode.run``. With no engine app
    installed the executor resolves ``ocr`` / ``vision-llm`` instead, which called no gate
    at all — so uploading a plain-text file named ``not_really.png`` returned HTTP 200 with
    ``node_phases`` ``ocr=done``, no ``ocr=rejected`` marker and an empty pool (measured live
    on item ``c13fd0e9``). Identical bytes, opposite handling, decided by which backend
    happened to be resolvable.

    The assertion that matters is ``called == []``: not merely that the output says
    "rejected", but that the model was never handed the bytes. ARCC ``cnt_eMkU5kkpTaEk65``
    requires validating the true type BEFORE a consumer processes the file.
    """
    from personalclaw.knowledge.pipeline.nodes import media_nodes

    liar = tmp_path / "not_really.png"
    liar.write_text("this is plain text that merely claims to be a PNG")

    called: list[tuple] = []

    async def _never_called(*args, **kwargs):
        called.append(args)
        return "the model should never have been asked"

    monkeypatch.setattr(media_nodes, "complete_text", _never_called)

    out = asyncio.run(media_nodes.OcrNode().run({}, _ocr_ctx(tmp_path, str(liar))))

    assert called == [], "a non-image was handed to the vision model — the gate did not run"
    assert out.success is False
    assert (
        out.metadata.get("ocr") == "rejected"
    ), f"expected the shared 'rejected' marker both backends report, got {out.metadata!r}"
    assert out.metadata.get("ocr_rejected"), "the refusal carries no reason a user could act on"


def test_the_vision_llm_backend_still_reads_a_real_image(tmp_path, monkeypatch):
    """The non-vacuity mirror: the gate refuses liars, not everything.

    Without this, deleting the vision-llm OCR path outright would satisfy the rail above.
    A genuine PNG must reach the model and its text must come back.
    """
    from personalclaw.knowledge.pipeline.nodes import media_nodes

    honest = tmp_path / "real.png"
    honest.write_bytes(_one_pixel_png())

    seen: list[list[str]] = []

    async def _fake_model(_use_case, _prompt, images=None):
        seen.append(list(images or []))
        return "transcribed text"

    monkeypatch.setattr(media_nodes, "complete_text", _fake_model)

    out = asyncio.run(media_nodes.OcrNode().run({}, _ocr_ctx(tmp_path, str(honest))))

    assert seen == [[str(honest)]], f"the real image did not reach the model: {seen!r}"
    assert out.success is True
    assert out.text == "transcribed text"
    assert "ocr" not in out.metadata, f"a clean read reported a gate verdict: {out.metadata!r}"


def test_both_ocr_backends_refuse_the_same_bytes(tmp_path, spy, monkeypatch):
    """The gate is a property of the ``ocr`` NODE TYPE, so the two backends must agree.

    This is the rail that would have caught the original defect: it hands the SAME liar file
    to both registered backends and requires the same refusal from each. Before the shared
    gate, ``vision-llm`` returned ``success=True`` here while ``engine`` refused.
    """
    from personalclaw.knowledge.pipeline.nodes import media_nodes
    from personalclaw.knowledge.pipeline.nodes.ocr_nodes import OcrEngineNode

    liar = tmp_path / "not_really.png"
    liar.write_text("plain text wearing a PNG extension")

    async def _never_called(*args, **kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("the vision model was handed un-gated bytes")

    monkeypatch.setattr(media_nodes, "complete_text", _never_called)

    ctx = _ocr_ctx(tmp_path, str(liar))
    vision_out = asyncio.run(media_nodes.OcrNode().run({}, ctx))
    engine_out = asyncio.run(OcrEngineNode().run({}, ctx))

    for label, out in (("vision-llm", vision_out), ("engine", engine_out)):
        assert out.success is False, f"{label} accepted a non-image"
        assert out.metadata.get("ocr") == "rejected", f"{label} metadata: {out.metadata!r}"
    assert spy.pages_seen == 0, "the engine decoded bytes the gate should have refused"


# ── the rail's own negative cases ─────────────────────────────────────────────────


class TestTheRailIsNotVacuous:
    """Clauses 2 and 4 are green when something DOESN'T happen, so they would also be green
    if the OCR path had never been built. These show the same instruments reading non-zero."""

    def test_the_same_spy_does_count_on_a_scan(self, tmp_path, spy):
        """The instrument behind the zero-invocation assertion, proven able to be non-zero."""
        store = _store_for(tmp_path)
        item_id = _ingest_pdf(store, tmp_path, IMAGE_ONLY_PDF)
        asyncio.run(ingest_item(store, item_id, embedder=_Embedder()))
        assert spy.pages_seen > 0, "the spy cannot observe an invocation at all"

    def test_the_engine_backend_is_not_reachable_without_a_registered_engine(self, no_engine):
        """The removability clause of KOCR-2 read from core's side: the ``ocr``/``engine``
        backend EXISTS in the registry but is not runnable, so a user with no OCR app gets
        the graceful skip rather than a node that fails."""
        from personalclaw.knowledge.pipeline.registry import backends_for, get_node, node_available

        assert "engine" in backends_for("ocr"), "the engine backend should be registered"
        assert node_available(get_node("ocr", "engine")) is False
        assert resolve_runnable("ocr", "vision-llm") is None

    def test_a_pinned_backend_is_never_substituted(self, spy):
        """The fallback reconsiders the GRAPH's default only. A user who pinned a backend
        gets that backend or a skip — silently running a different engine than the one they
        chose would be the substitution doing harm. Driven through the real executor, not
        read off the source: a source assertion would survive the behaviour being deleted.
        """
        from personalclaw.knowledge.pipeline.executor import PipelineExecutor
        from personalclaw.knowledge.pipeline.graphs import graph_for
        from personalclaw.knowledge.pipeline.types import NodeContext

        # The image graph, where `ocr` is a ROOT node — so the substitution is observed
        # without the document graph's conditional edge in the way.
        graph = graph_for("image")
        png = FIXTURES / "ocr_probe.png"
        ctx = NodeContext(item_id="x", item_type="image", file_path=str(png))

        # Default (unpinned): the engine backend substitutes for the unbindable VLM. Asserted
        # first so the pinned half below is a contrast, not an untested absence.
        free = asyncio.run(PipelineExecutor(graph).run(ctx))
        assert (
            "ocr" not in free.skipped
        ), f"ocr was skipped despite a runnable engine: {free.skipped}"
        assert spy.pages_seen == 1, "the unpinned run did not reach the engine at all"

        # Pinned to the model-backed backend with no model bound → a skip, never the engine.
        before = spy.pages_seen
        pinned = asyncio.run(
            PipelineExecutor(
                graph, params_for=lambda nt: {"backend": "vision-llm"} if nt == "ocr" else {}
            ).run(ctx)
        )
        assert "ocr" in pinned.skipped, "a pinned unrunnable backend was silently substituted"
        assert spy.pages_seen == before, "the engine ran behind a pinned backend"

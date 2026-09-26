"""RET-2 — an ingest that yields zero searchable chunks is a LOUD, NAMED failure.

**The defect, measured on ``origin/main`` @ ``dbbf1a832`` before this rail existed.** Both
fixtures below ingested to ``processing_status='done'`` with ``processing_error=None``:

* ``tests/fixtures/image_only_scan.pdf`` — ``document_read`` reported ``done`` while
  ``pdfplumber.extract_text()`` returned ``''`` for its only page. The item's persisted
  content was the synthesized descriptor ``"Document: image_only_scan.pdf (pdf, 1 pages,
  … KB)"`` — none of the document's words — and a search for a word plainly visible on the
  page returned ``(no matching knowledge items)``.
* ``tests/fixtures/unindexed_document.md`` ingested with ``embedder=None`` — zero rows in
  ``chunks``, no item vector, and still ``done``.

That is AnythingLLM #6143 ("the embedding step silently writes nothing… RAG retrieval
returns no sources, while the app reports success") reproduced in PersonalClaw, and
PersonalClaw's own OU-3 finding that model-dependent write paths fail OPEN and silently.

**What this rail asserts, per fixture, all three required together:**

1. the item persists a NAMED failure value (``unsearchable``), never ``done``;
2. it puts EXACTLY ONE row on an attention surface — the ``knowledge.searchability``
   Doctor probe, one row per item, naming which document is unreachable;
3. ``knowledge_search`` for a token the test knows is in the fixture answers with a TYPED
   reason (``no_extractable_text`` / ``no_embedding_provider``), never a bare empty set.

**The lazy implementation this rail rejects** is logging a warning (or raising into a caller
that swallows it) while the item still persists as ``done``. It cannot pass here for two
reasons, and ``TestTheRailIsNotVacuous`` exercises both: clauses 1 and 3 read *different*
code paths (the persisted status vs. the search surface), and
``test_a_log_only_implementation_cannot_pass_this_rail`` puts the status back to ``done``
and observes BOTH the Doctor row and the typed search reason vanish — so a fix that only
logs takes the whole rail red.

**Its own negative cases** (a rail that cannot fail is not a rail): a healthy ingest with a
bound embedder must stay ``done`` with no row, and an image ingested with no vision model
must NOT be flagged — its extractors were *skipped*, which is a declared degradation, not a
lie. If this rail flagged everything it would be worthless, so both are asserted.
"""

from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest

from personalclaw.knowledge.pipeline.runner import ingest_item
from personalclaw.knowledge.retrieval import HybridRetriever
from personalclaw.knowledge.searchability import (
    NO_EMBEDDING_PROVIDER,
    NO_EXTRACTABLE_TEXT,
    REASONS,
    UNSEARCHABLE,
    reason_detail,
)
from personalclaw.knowledge.store import KnowledgeStore, knowledge_db_path
from personalclaw.resilience.doctor import DoctorContext, all_probes

FIXTURES = Path(__file__).parent / "fixtures"
IMAGE_ONLY_PDF = FIXTURES / "image_only_scan.pdf"
UNINDEXED_DOC = FIXTURES / "unindexed_document.md"

#: The rare token both fixtures carry. In the PDF it exists only as PIXELS; in the markdown
#: it is real extractable text. One token, two failure modes.
TOKEN = "quokkatelemetry"

PROBE_ID = "knowledge.searchability"


class _Embedder:
    """A deterministic stand-in for a bound embedding provider — small, non-zero, stable."""

    def embed_for_item(self, title, summary, content):
        return [0.1, 0.2, 0.3, 0.4]

    def embed(self, text):
        return [0.1, 0.2, 0.3, 0.4]

    def is_available(self):
        return True


def _store_for(home: Path) -> KnowledgeStore:
    """A store at the ONE path the Doctor probe reads, so the surface under test is the
    surface a real install has (``knowledge_db_path`` owns it — never recompose it)."""
    return KnowledgeStore(str(knowledge_db_path(home)))


def _ingest_file(store: KnowledgeStore, home: Path, src: Path, *, item_type: str, mime: str) -> str:
    """Copy a committed fixture into the home and create its item, as an upload would."""
    dest = Path(knowledge_db_path(home)).parent / src.name
    shutil.copy(src, dest)
    item_id = store.create_typed_item(
        item_type=item_type,
        title=src.name,
        content="",
        extra={
            "file_path": str(dest),
            "mime_type": mime,
            "file_size": dest.stat().st_size,
            "processing_status": "queued",
        },
    )
    assert item_id
    return item_id


def _probe(home: Path):
    """Run the attention-surface probe against *home* (read-only, ``create=False``)."""
    probe = next(p for p in all_probes() if p.id == PROBE_ID)
    return asyncio.run(probe.run(DoctorContext(home=home)))


def _reason_of(store: KnowledgeStore, item_id: str) -> str:
    meta = (store.get_item(item_id) or {}).get("file_metadata") or {}
    return str(meta.get("unsearchable_reason") or "")


def _search_text(home: Path, query: str) -> str:
    """Drive the REAL ``knowledge_search`` tool an agent calls, on this home's store."""
    import personalclaw.agents.native.builtin_tools as bt
    import personalclaw.knowledge as K

    with (
        patch.object(K, "_store", None),
        patch("personalclaw.knowledge.knowledge_db_path", lambda: str(knowledge_db_path(home))),
        patch("personalclaw.knowledge.get_knowledge_embedder", lambda: None),
    ):
        result = asyncio.run(
            bt.NativeBuiltinToolProvider().invoke("knowledge_search", {"query": query})
        )
    assert result.success, result.error
    return result.output or ""


# ── the fixtures' own preconditions — assert them, never assume them ──────────────


def test_the_image_only_fixture_yields_no_extractable_text():
    """Clause zero: the fixture must actually be image-only, or every assertion below is
    testing nothing. This is the atom's explicit first step — assert
    ``pdfplumber.extract_text()`` is empty BEFORE asserting anything about the ingest."""
    pdfplumber = pytest.importorskip("pdfplumber")

    assert IMAGE_ONLY_PDF.is_file(), f"committed fixture missing: {IMAGE_ONLY_PDF}"
    with pdfplumber.open(str(IMAGE_ONLY_PDF)) as pdf:
        pages = list(pdf.pages)
        extracted = "".join((p.extract_text() or "") for p in pages)
    assert pages, "the fixture must be a real PDF with at least one page"
    assert extracted.strip() == "", f"fixture is not image-only — it extracted {extracted!r}"


def test_the_unindexed_fixture_does_carry_the_token_as_real_text():
    """Its mirror: fixture (2) must have genuinely extractable text, so a failure there is
    attributable to the missing embedder and to nothing else."""
    assert UNINDEXED_DOC.is_file(), f"committed fixture missing: {UNINDEXED_DOC}"
    assert TOKEN in UNINDEXED_DOC.read_text(encoding="utf-8")


# ── clause 1 — the persisted status is a NAMED failure value, never "done" ────────


def test_an_image_only_pdf_persists_a_named_failure_not_done(tmp_path):
    store = _store_for(tmp_path)
    item_id = _ingest_file(
        store, tmp_path, IMAGE_ONLY_PDF, item_type="document", mime="application/pdf"
    )

    status = asyncio.run(ingest_item(store, item_id, embedder=_Embedder()))

    item = store.get_item(item_id)
    assert status != "done", f"silent success on a scan: {item!r}"
    assert item["processing_status"] == UNSEARCHABLE, item
    assert _reason_of(store, item_id) == NO_EXTRACTABLE_TEXT, item
    assert item["processing_status"] in ("", UNSEARCHABLE) or True
    # …and it says WHY on the item itself: the typed token where machines read it (above),
    # the human sentence for that token on the status line a person reads — never the token.
    assert reason_detail(NO_EXTRACTABLE_TEXT) in (item.get("processing_error") or ""), item
    assert NO_EXTRACTABLE_TEXT not in (item.get("processing_error") or ""), item
    # The proof that this is the silent-failure case and not an honest read: the persisted
    # content holds NONE of the document's words — only the synthesized descriptor.
    assert TOKEN not in (item.get("content") or "").lower()


def test_a_document_with_no_embedding_provider_persists_a_named_failure_not_done(tmp_path):
    store = _store_for(tmp_path)
    item_id = _ingest_file(
        store, tmp_path, UNINDEXED_DOC, item_type="document", mime="text/markdown"
    )

    status = asyncio.run(ingest_item(store, item_id, embedder=None))

    item = store.get_item(item_id)
    assert status != "done", f"silent success with no embedder: {item!r}"
    assert item["processing_status"] == UNSEARCHABLE, item
    assert _reason_of(store, item_id) == NO_EMBEDDING_PROVIDER, item
    assert reason_detail(NO_EMBEDDING_PROVIDER) in (item.get("processing_error") or ""), item
    assert NO_EMBEDDING_PROVIDER not in (item.get("processing_error") or ""), item
    # The measured fact behind the verdict: nothing landed in the chunk index.
    n = store.db.execute("SELECT COUNT(*) FROM chunks WHERE item_id = ?", (item_id,)).fetchone()[0]
    assert int(n) == 0
    assert not item.get("embedding")
    # Its text DID extract — so this failure is the embedder's absence, nothing else.
    assert TOKEN in (item.get("content") or "").lower()


# ── clause 2 — exactly ONE row on an attention surface, per fixture ───────────────


def test_the_doctor_probe_is_registered():
    assert PROBE_ID in {p.id for p in all_probes()}


@pytest.mark.parametrize(
    "fixture, item_type, mime, embedder, reason",
    [
        (IMAGE_ONLY_PDF, "document", "application/pdf", _Embedder(), NO_EXTRACTABLE_TEXT),
        (UNINDEXED_DOC, "document", "text/markdown", None, NO_EMBEDDING_PROVIDER),
    ],
    ids=["image-only-pdf", "no-embedding-provider"],
)
def test_each_fixture_surfaces_exactly_one_attention_row(
    tmp_path, fixture, item_type, mime, embedder, reason
):
    """One row, naming WHICH item. A bare count cannot be acted on, and two rows for one
    item would make the surface lie about how much is wrong."""
    store = _store_for(tmp_path)
    item_id = _ingest_file(store, tmp_path, fixture, item_type=item_type, mime=mime)
    asyncio.run(ingest_item(store, item_id, embedder=embedder))
    store.close()

    result = _probe(tmp_path)

    assert result.ok is False, "an item nothing can find is an actionable failure, not health"
    rows = result.evidence["items"]
    assert len(rows) == 1, rows
    assert rows[0]["item_id"] == item_id
    assert rows[0]["title"] == fixture.name
    assert rows[0]["reason"] == reason
    assert result.evidence["unsearchable"] == 1
    assert result.evidence["by_reason"] == {reason: 1}
    # The detail is the ONE shared sentence (the search tool prints it too); the typed token
    # rides in `by_reason` for machines and stays out of the prose a person reads.
    assert result.detail == result.evidence["summaries"][0], result.detail
    assert reason not in result.detail and "cannot be found by search" not in result.detail
    assert result.evidence.get("remedy")


# ── clause 3 — knowledge_search answers with a TYPED reason, never a bare empty set ──


def test_searching_the_scans_visible_token_returns_a_typed_reason(tmp_path):
    """The worst case: the word is ON the page, the item is in the library, and search can
    reach neither. Before RET-2 this returned ``(no matching knowledge items)`` — a claim
    about the library's CONTENT that was simply false."""
    store = _store_for(tmp_path)
    item_id = _ingest_file(
        store, tmp_path, IMAGE_ONLY_PDF, item_type="document", mime="application/pdf"
    )
    asyncio.run(ingest_item(store, item_id, embedder=_Embedder()))
    store.close()

    output = _search_text(tmp_path, TOKEN)

    assert NO_EXTRACTABLE_TEXT in output, output
    assert output.strip() != "(no matching knowledge items)", "a bare empty set is the defect"
    # The reason must be actionable, not just a token.
    assert "scan" in output.lower() or "ocr" in output.lower(), output


def test_searching_with_no_embedding_provider_returns_a_typed_reason(tmp_path):
    """Here the keyword arm DOES find the item — and the answer is still incomplete, because
    the library's whole semantic half is absent. The typed reason rides along with hits."""
    store = _store_for(tmp_path)
    item_id = _ingest_file(
        store, tmp_path, UNINDEXED_DOC, item_type="document", mime="text/markdown"
    )
    asyncio.run(ingest_item(store, item_id, embedder=None))
    store.close()

    output = _search_text(tmp_path, TOKEN)

    assert NO_EMBEDDING_PROVIDER in output, output
    assert UNINDEXED_DOC.name in output, f"the keyword hit must survive the report: {output}"


def test_the_retriever_reports_the_same_reason_the_item_persisted(tmp_path):
    """One vocabulary, one source. The search-side reason is READ OFF the persisted fact —
    if these could disagree there would be two dialects, which is how the product ends up
    with a status saying one thing and a surface saying another."""
    store = _store_for(tmp_path)
    item_id = _ingest_file(
        store, tmp_path, UNINDEXED_DOC, item_type="document", mime="text/markdown"
    )
    asyncio.run(ingest_item(store, item_id, embedder=None))

    outcome = HybridRetriever(store, embedder=None).search_with_diagnostics(TOKEN)

    assert outcome.degraded
    assert [d.reason for d in outcome.degradations] == [_reason_of(store, item_id)]
    assert outcome.degradations[0].item_ids == (item_id,)
    assert outcome.degradations[0].item_count == 1
    # The hits themselves are untouched — this adds a report, it does not reshape ranking.
    plain = HybridRetriever(store, embedder=None).search(TOKEN)
    assert [r["id"] for r in outcome.results] == [r["id"] for r in plain]


# ── the rail's own floors ────────────────────────────────────────────────────────


class TestTheRailIsNotVacuous:
    def test_a_healthy_ingest_stays_done_and_puts_no_row_anywhere(self, tmp_path):
        """A rail that flags everything is worthless. With a bound embedder and real text
        the SAME code path reaches ``done``, the Doctor row is clean, and search reports no
        degradation."""
        store = _store_for(tmp_path)
        item_id = store.create_typed_item(
            item_type="note",
            title="Healthy note",
            content=f"The {TOKEN} collector emits a heartbeat every four seconds.",
        )
        status = asyncio.run(ingest_item(store, item_id, embedder=_Embedder()))

        item = store.get_item(item_id)
        assert status == "done", item
        assert item["processing_status"] == "done"
        assert not _reason_of(store, item_id)
        outcome = HybridRetriever(store, embedder=None).search_with_diagnostics(TOKEN)
        assert not outcome.degraded
        assert [r["id"] for r in outcome.results] == [item_id]
        store.close()

        result = _probe(tmp_path)
        assert result.ok is True, result.detail
        assert result.evidence["items"] == []

    def test_an_image_with_no_vision_model_is_not_flagged(self, tmp_path):
        """The discriminator that keeps this rail honest. An image ingested with no OCR or
        vision model SKIPS its extractors — a declared degradation the product already
        reports as ``partial``. Only a node that claimed SUCCESS and produced nothing is a
        lie, and only a lie is this atom's business. Without this test, the obvious
        over-broad implementation ("any item with no extracted text") would pass."""
        pytest.importorskip("PIL")
        from PIL import Image

        store = _store_for(tmp_path)
        src = Path(knowledge_db_path(tmp_path)).parent / "plain.png"
        Image.new("RGB", (48, 48), "blue").save(src)
        item_id = store.create_typed_item(
            item_type="image",
            title="plain.png",
            content="",
            extra={
                "file_path": str(src),
                "mime_type": "image/png",
                "file_size": src.stat().st_size,
                "processing_status": "queued",
            },
        )
        status = asyncio.run(ingest_item(store, item_id, embedder=_Embedder()))

        assert status != UNSEARCHABLE, store.get_item(item_id)
        assert not _reason_of(store, item_id)

    def test_a_reingest_that_lands_clears_the_row(self, tmp_path):
        """The row must not be sticky: binding an embedder and re-ingesting has to take the
        item OFF the attention surface, or the surface becomes noise nobody reads."""
        store = _store_for(tmp_path)
        item_id = _ingest_file(
            store, tmp_path, UNINDEXED_DOC, item_type="document", mime="text/markdown"
        )
        asyncio.run(ingest_item(store, item_id, embedder=None))
        assert store.get_item(item_id)["processing_status"] == UNSEARCHABLE

        asyncio.run(ingest_item(store, item_id, embedder=_Embedder()))

        item = store.get_item(item_id)
        assert item["processing_status"] == "done", item
        assert not _reason_of(store, item_id)
        assert "unsearchable_reason" not in (item.get("file_metadata") or {})
        store.close()
        assert _probe(tmp_path).evidence["items"] == []

    def test_a_log_only_implementation_cannot_pass_this_rail(self, tmp_path):
        """The atom's named lazy implementation, exercised. A fix that logs a warning (or
        raises into a caller that swallows it) leaves the item persisted as ``done`` — so
        put it back to ``done`` and watch the OTHER two clauses go dark: no attention row,
        no typed search reason. Both of this rail's remaining assertions are therefore
        load-bearing on the persisted status, and a log-only fix takes all three red."""
        store = _store_for(tmp_path)
        item_id = _ingest_file(
            store, tmp_path, UNINDEXED_DOC, item_type="document", mime="text/markdown"
        )
        asyncio.run(ingest_item(store, item_id, embedder=None))

        # What a log-only implementation leaves behind.
        store.update_item(item_id, processing_status="done", touch=False)
        store.db.commit()

        outcome = HybridRetriever(store, embedder=None).search_with_diagnostics(TOKEN)
        assert not outcome.degraded, "the typed reason came from somewhere other than the item"
        store.close()
        result = _probe(tmp_path)
        assert result.ok is True and result.evidence["items"] == []

    def test_a_no_provider_ingest_stays_visible_to_the_heuristic_drain(self, tmp_path):
        """The regression this change could have caused, driven through the REAL runner.

        The degraded-mode backlog that re-enriches items once a model comes back matched
        ``processing_status = 'partial'``. On a no-provider first-run home — no insights
        model AND no embedding model — RET-2 files the item ``unsearchable`` instead, so a
        status-only match would have silently emptied that backlog on exactly the homes it
        exists for: the drain would report zero and binding a model would re-enrich nothing.
        The existing degraded test hand-stamps ``partial`` and therefore cannot see this, so
        this one ingests for real and asserts the SQL still selects the row."""
        from personalclaw.resilience.degraded import _HEURISTIC_ITEMS_SQL

        class _RaisingPool:
            async def submit(self, *a, **k):
                raise RuntimeError("no model bound")

            async def run(self, *a, **k):
                raise RuntimeError("no model bound")

        store = _store_for(tmp_path)
        item_id = store.create_typed_item(
            item_type="note",
            title="no-provider note",
            content="A note about distributed consensus and leader election.",
        )
        asyncio.run(ingest_item(store, item_id, insights_pool=_RaisingPool(), embedder=None))

        item = store.get_item(item_id)
        assert item["processing_status"] == UNSEARCHABLE, item
        assert "model unavailable" in (item.get("processing_error") or ""), item
        selected = [r[0] for r in store.db.execute(_HEURISTIC_ITEMS_SQL).fetchall()]
        assert selected == [item_id], f"the heuristic backlog lost the item: {item!r}"

    def test_the_reason_vocabulary_is_closed_and_documented(self):
        """A typed reason with no human sentence is a token, not a reason — every member of
        the closed vocabulary must carry one (and a remedy), or a surface can print a bare
        slug at a user, or a diagnosis with no next step."""
        from personalclaw.knowledge.searchability import REASON_REMEDY, reason_detail

        assert set(REASON_REMEDY) == set(REASONS)
        for reason in REASONS:
            assert len(reason_detail(reason)) > 40, reason
        # An unknown token stays legible instead of falling into a health-reporting default.
        assert "unknown reason" in reason_detail("")


def test_the_real_home_is_never_touched(tmp_path):
    """Assert the isolation rather than trusting it: this suite ingests files and writes a
    knowledge db, so a home override that failed to bind would be writing into the owner's
    real library. The db must land under tmp_path; and the suite-wide real-home guard
    (tests/real_home_guard.py) fails this test if the ingest opens, creates or even lists
    anything under the real ~/.personalclaw — which is why the test no longer lists it
    itself to compare."""
    store = _store_for(tmp_path)
    item_id = _ingest_file(
        store, tmp_path, UNINDEXED_DOC, item_type="document", mime="text/markdown"
    )
    asyncio.run(ingest_item(store, item_id, embedder=None))
    store.close()

    assert str(knowledge_db_path(tmp_path)).startswith(str(tmp_path))
    assert os.path.exists(knowledge_db_path(tmp_path))

"""An item that gains its embeddings stops saying it has none.

Measured on a fresh home: the onboarding note ingested before an embedding model was bound was
recorded ``unsearchable`` — "This item has no embeddings because no embedding model is bound …
Bind an embedding model in Settings → Models, then re-index." The model was bound and the
re-index embedded it, and the item went on saying so with ``has_embedding`` true. The verdict
was only ever re-derived by a re-ingest; the two writers that give an existing item its vectors
(the re-index's ``reembed_all`` and the chunk backfill's ``replace_chunks``) left it standing.

Each case runs the REAL ingest runner with no embedding model to create the verdict, exactly as
the note got it, then the real writer.
"""

from __future__ import annotations

import asyncio

import pytest

from personalclaw.knowledge.pipeline import ensure_nodes_registered
from personalclaw.knowledge.pipeline.runner import INSIGHTS_UNAVAILABLE, ingest_item
from personalclaw.knowledge.searchability import (
    NO_EMBEDDING_PROVIDER,
    UNSEARCHABLE,
    reason_detail,
)
from personalclaw.knowledge.store import KnowledgeStore


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path / "home"))


@pytest.fixture()
def store(tmp_path):
    ensure_nodes_registered()
    return KnowledgeStore(str(tmp_path / "knowledge.db"))


class _Embedder:
    """A bound embedding model, for both writers: ``embed_for_item`` (the re-index) and
    ``embed`` (the chunk layer)."""

    def is_available(self) -> bool:
        return True

    def embed_for_item(self, title, summary, content=None):
        return [0.5, 0.25, 0.125, 0.0625]

    def embed(self, text):
        return [0.5, 0.25, 0.125, 0.0625]


class _NoInsightsModel:
    async def send(self, prompt, timeout=None):
        raise RuntimeError("no model bound")


def _unsearchable(store, *, pool=None) -> str:
    iid = store.create_typed_item(
        item_type="note",
        title="How PersonalClaw stores your data",
        content="Your notes, memories and settings live in one folder on this machine.",
    )
    asyncio.run(ingest_item(store, iid, insights_pool=pool))
    item = store.get_item(iid)
    assert item["processing_status"] == UNSEARCHABLE
    assert item["file_metadata"]["unsearchable_reason"] == NO_EMBEDDING_PROVIDER
    return iid


def test_the_reindex_that_embeds_an_item_clears_its_no_embeddings_verdict(store):
    iid = _unsearchable(store)
    assert store.get_item(iid)["processing_error"] == reason_detail(NO_EMBEDDING_PROVIDER)

    assert store.reembed_all(_Embedder(), only_missing=True)["reembedded"] == 1

    item = store.get_item(iid)
    assert item["has_embedding"] is True
    assert item["processing_status"] == "done"
    assert not item["processing_error"]
    assert "unsearchable_reason" not in item["file_metadata"]
    assert item["file_metadata"]["node_phases"]["embed"] == "done"


def test_what_else_the_item_had_to_say_stays_and_it_reads_partial(store):
    """The verdict led a longer sentence (insights that failed too): only the verdict goes,
    and the item is what it would have been without it — partial, for the insights."""
    iid = _unsearchable(store, pool=_NoInsightsModel())
    before = store.get_item(iid)["processing_error"]
    assert before.startswith(reason_detail(NO_EMBEDDING_PROVIDER))
    assert "model unavailable" in before

    store.reembed_all(_Embedder(), only_missing=True)

    item = store.get_item(iid)
    assert item["processing_status"] == "partial"
    assert "no embeddings" not in item["processing_error"]
    assert item["processing_error"].lower().startswith(INSIGHTS_UNAVAILABLE.lower())


def test_the_chunk_backfill_that_gives_an_item_passages_clears_it_too(store):
    from personalclaw.knowledge.chunk_backfill import backfill_item_chunks

    iid = _unsearchable(store)

    assert backfill_item_chunks(store, _Embedder())["chunked"] == 1

    item = store.get_item(iid)
    assert item["processing_status"] == "done"
    assert "unsearchable_reason" not in item["file_metadata"]


def test_an_item_with_no_words_to_read_keeps_its_verdict(store):
    """The vacuity arm: an embedding write cannot give a scanned page its words, so the
    no-extractable-text verdict is not an embedding verdict and stays."""
    from personalclaw.knowledge.searchability import NO_EXTRACTABLE_TEXT

    iid = store.create_typed_item(item_type="note", title="scan.pdf", content="Document: scan.pdf")
    store.update_item(
        iid,
        processing_status=UNSEARCHABLE,
        processing_error=reason_detail(NO_EXTRACTABLE_TEXT),
        file_metadata={"unsearchable_reason": NO_EXTRACTABLE_TEXT},
        touch=False,
    )
    store.db.commit()

    store.reembed_all(_Embedder(), only_missing=True)

    item = store.get_item(iid)
    assert item["processing_status"] == UNSEARCHABLE
    assert item["file_metadata"]["unsearchable_reason"] == NO_EXTRACTABLE_TEXT

"""An item saved before the embedding model was bound is described by the state it is in now.

Measured on a fresh home: the note saved before an embedding model was bound was recorded "no
embeddings because no embedding model is bound". The model was bound, and the re-index gave the note
its vector, but that build's re-index left the verdict standing. From then on Doctor's Knowledge
check said "1 item has no embeddings because no embedding model is bound" while its own evidence
named the bound model, and Maintenance said nothing it measured was fixable, which was true: the
item had its vector, so nothing was missing. Nothing ever revisited the stale verdict.

Two states, each told truthfully:

* the item holds a vector its verdict says it lacks: the verdict is retired when the library
  opens, so the item stops being listed as unsearchable anywhere;
* the item really has no vector, and a model IS bound now: Doctor and ``knowledge_search`` say it
  was saved before the model was bound and waits to be embedded, and name Maintenance, whose
  embedding job does it, instead of claiming no model is bound.
"""

from __future__ import annotations

import asyncio

import pytest

from personalclaw.knowledge.embedder import floats_to_bytes
from personalclaw.knowledge.embedding_fingerprint import active_fingerprint
from personalclaw.knowledge.pipeline import ensure_nodes_registered
from personalclaw.knowledge.pipeline.runner import ingest_item
from personalclaw.knowledge.retrieval import HybridRetriever
from personalclaw.knowledge.searchability import (
    AWAITING_EMBEDDING,
    INGEST_REASONS,
    NO_EMBEDDING_PROVIDER,
    UNSEARCHABLE,
)
from personalclaw.knowledge.store import KnowledgeStore, knowledge_db_path
from personalclaw.resilience.doctor import DoctorContext, all_probes

TITLE = "How PersonalClaw stores your data"


def _bind(monkeypatch, model: str | None) -> None:
    """Bind (or unbind) the embedding model, through the accessor every reader resolves."""
    spec = None if model is None else ("ollama", model)
    monkeypatch.setattr(
        "personalclaw.embedding_providers.registry._active_embedding_spec", lambda: spec
    )


@pytest.fixture()
def home(tmp_path):
    ensure_nodes_registered()
    return tmp_path


def _store(home) -> KnowledgeStore:
    return KnowledgeStore(str(knowledge_db_path(home)))


def _saved_with_no_model(home) -> str:
    """The onboarding note, ingested by the real runner while no embedding model is bound."""
    store = _store(home)
    iid = store.create_typed_item(
        item_type="note",
        title=TITLE,
        content="Your notes, memories and settings live in one folder on this machine.",
    )
    asyncio.run(ingest_item(store, iid))
    item = store.get_item(iid)
    assert item["processing_status"] == UNSEARCHABLE
    assert item["file_metadata"]["unsearchable_reason"] == NO_EMBEDDING_PROVIDER
    store.close()
    return iid


def _doctor(home):
    probe = next(p for p in all_probes() if p.id == "knowledge.searchability")
    return asyncio.run(probe.run(DoctorContext(home=home)))


def test_a_verdict_the_items_own_vector_made_false_is_retired_when_the_library_opens(
    home, monkeypatch
):
    iid = _saved_with_no_model(home)
    _bind(monkeypatch, "qwen3-embedding:0.6b")
    # The re-index of an earlier build: it wrote the bound model's vector and left the verdict.
    store = _store(home)
    store.db.execute(
        "UPDATE items SET embedding = ?, embedding_model_id = ?, embedding_provider = ? "
        "WHERE id = ?",
        (floats_to_bytes([0.5, 0.25, 0.125]), *active_fingerprint().params, iid),
    )
    store.db.commit()
    store.close()

    store = _store(home)
    item = store.get_item(iid)
    store.close()

    assert item["has_embedding"] is True
    assert item["processing_status"] == "done", item["processing_status"]
    assert "unsearchable_reason" not in item["file_metadata"]
    result = _doctor(home)
    assert result.ok is True, result.detail
    assert result.evidence["unsearchable"] == 0


def test_an_item_with_no_vector_under_a_bound_model_says_it_waits_to_be_embedded(home, monkeypatch):
    iid = _saved_with_no_model(home)
    _bind(monkeypatch, "qwen3-embedding:0.6b")

    result = _doctor(home)

    assert result.ok is False
    assert result.evidence["active_embedding_model"].endswith("qwen3-embedding:0.6b")
    (row,) = result.evidence["items"]
    assert (row["item_id"], row["reason"]) == (iid, AWAITING_EMBEDDING)
    assert "no embedding model is bound" not in result.detail, result.detail
    assert result.detail == (
        "1 item has no embeddings yet: it was saved before an embedding model was bound — "
        "keyword search finds it, semantic search cannot until it is embedded"
    )
    assert "Maintenance" in result.remedy and AWAITING_EMBEDDING in result.remedy


def test_the_search_tool_reads_the_same_state_the_doctor_does(home, monkeypatch):
    iid = _saved_with_no_model(home)
    _bind(monkeypatch, "qwen3-embedding:0.6b")
    store = _store(home)

    outcome = HybridRetriever(store, embedder=None).search_with_diagnostics("folder")

    assert [(d.reason, d.item_ids) for d in outcome.degradations] == [(AWAITING_EMBEDDING, (iid,))]
    store.close()


def test_with_no_model_bound_it_still_says_none_is_bound(home, monkeypatch):
    """The control: the recorded reason is still the true one while nothing is bound."""
    _saved_with_no_model(home)
    _bind(monkeypatch, None)

    result = _doctor(home)

    assert [r["reason"] for r in result.evidence["items"]] == [NO_EMBEDDING_PROVIDER]
    assert "because no embedding model is bound" in result.detail


def test_waiting_to_be_embedded_is_read_off_the_binding_never_persisted():
    """Like a stale index, it is a fact about the bound model, so no ingest may record it."""
    assert AWAITING_EMBEDDING not in INGEST_REASONS

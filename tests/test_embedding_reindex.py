"""Embedding re-index on model change (#51).

Switching the active embedding model clears the now-incompatible vectors and
re-embeds both stores. Pins the store-level re-embed primitives and the readiness
gate (the change is refused when the new model can't produce vectors).
"""

from __future__ import annotations

import pytest

from personalclaw.knowledge.store import KnowledgeStore
from personalclaw.vector_memory import VectorMemoryStore

# ── Knowledge store ──


def _kstore(tmp_path) -> KnowledgeStore:
    return KnowledgeStore(str(tmp_path / "k.db"))


def _add(store, title, content, summary="", embedding=None):
    """Create one logical-doc item, optionally with a raw embedding blob."""
    iid = store.create_typed_item(item_type="note", title=title, content=content, summary=summary)
    if embedding is not None:
        store.db.execute("UPDATE items SET embedding = ? WHERE id = ?", (embedding, iid))
        store.db.commit()
    return iid


def test_knowledge_clear_and_reembed(tmp_path):
    store = _kstore(tmp_path)
    _add(store, "Title A", "content a", summary="sum a", embedding=b"\x00\x00")
    _add(store, "Title B", "content b", summary="sum b", embedding=b"\x11\x11")

    assert store.count_items_to_reembed() == 2
    cleared = store.clear_embeddings()
    assert cleared == 2
    # All embeddings now NULL.
    rows = store.db.execute("SELECT embedding FROM items").fetchall()
    assert all(r["embedding"] is None for r in rows)

    # A fake embedder that returns a vector per item. embed_for_item takes the same
    # (title, summary, content) shape the real embedder + reembed_all use.
    class _Emb:
        def embed_for_item(self, title, summary, content=None):
            return [0.1, 0.2, 0.3]

    res = store.reembed_all(_Emb())
    assert res == {"reembedded": 2, "failed": 2 - 2, "total": 2}
    rows = store.db.execute("SELECT embedding FROM items").fetchall()
    assert all(r["embedding"] is not None for r in rows)


def test_count_items_missing_embedding_detects_interrupted_reindex(tmp_path):
    """The boot-time auto-resume signal: after clear_embeddings() (start of a re-index)
    but before reembed_all() finishes, text-bearing items report as missing so the
    gateway can auto-resume. A whole store reports 0; a text-less item never counts."""
    store = _kstore(tmp_path)
    _add(store, "Has text A", "content a", embedding=b"\x00\x00")
    _add(store, "Has text B", "content b", embedding=b"\x11\x11")
    assert store.count_items_missing_embedding() == 0  # whole store → nothing to resume

    store.clear_embeddings()  # re-index begins → vectors nulled
    assert store.count_items_missing_embedding() == 2  # interrupted signature

    # A text-less item must NOT trigger a phantom resume.
    store.create_typed_item(item_type="note", title="", content="")
    assert store.count_items_missing_embedding() == 2  # still just the 2 text-bearing


def test_count_items_needing_reembed_detects_stale_dim(tmp_path):
    """Boot auto-resume must also recover from a model SWAP that was orphaned mid-flight:
    items keep an OLD wrong-dimension vector (so missing-count is 0) yet are vector-dead
    against the new model. count_items_needing_reembed(active_dim) catches missing OR
    stale-dim; the missing-only signal would leave the store silently unsearchable."""
    store = _kstore(tmp_path)
    # 384-dim vectors (384 floats * 4 bytes = 1536 bytes) from a previous model.
    v384 = b"\x00" * (384 * 4)
    _add(store, "Item A", "content a", embedding=v384)
    _add(store, "Item B", "content b", embedding=v384)
    # missing-only sees a "whole" store (vectors present) — the gap the old hook had.
    assert store.count_items_missing_embedding() == 0
    # But against the ACTIVE model's 768 dim, both are stale → need re-embed.
    assert store.count_items_needing_reembed(768) == 2
    # Same dim → nothing needs re-embedding.
    assert store.count_items_needing_reembed(384) == 0
    # Unknown active dim (embedder not ready) → falls back to missing-only (0 here).
    assert store.count_items_needing_reembed(None) == 0
    # A NULL vector counts as needing re-embed regardless of dim.
    store.clear_embeddings()
    assert store.count_items_needing_reembed(768) == 2


def test_knowledge_reembed_tolerates_failure(tmp_path):
    store = _kstore(tmp_path)
    _add(store, "T", "c")

    class _NullEmb:
        def embed_for_item(self, title, summary, content=None):
            return None  # model unavailable for this item

    res = store.reembed_all(_NullEmb())
    assert res["reembedded"] == 0 and res["failed"] == 1 and res["total"] == 1


# ── Vector (episodic) memory ──


def test_memory_reembed_episodic(tmp_path, monkeypatch):
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    store = VectorMemoryStore(db_path=tmp_path / "v.db")
    store.init()
    # Seed episodic rows WITHOUT embeddings (text preserved).
    store.embed_fn = None
    assert store.write_episodic("the user prefers dark mode in the editor", conversation_id="c1")
    assert store.write_episodic(
        "the project deadline is the end of the quarter", conversation_id="c1"
    )
    assert store.count_episodic_to_reembed() == 2

    # Now wire an embed_fn and re-embed.
    store.embed_fn = lambda text: [0.5, 0.5, 0.5]
    store._embedding_dim = 3
    res = store.reembed_all()
    assert res["reembedded"] == 2 and res["total"] == 2
    rows = store.db.execute(
        "SELECT embedding FROM episodic_memories WHERE is_deleted = 0"
    ).fetchall()
    assert all(r["embedding"] is not None for r in rows)


def test_memory_reembed_noop_without_embed_fn(tmp_path, monkeypatch):
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    store = VectorMemoryStore(db_path=tmp_path / "v.db")
    store.init()
    store.embed_fn = None
    store.write_episodic(
        "a sufficiently long episodic memory to pass length checks", conversation_id="c1"
    )
    res = store.reembed_all()
    assert res == {"reembedded": 0, "failed": 0, "total": 0}


# ── Readiness gate (handler refuses to wipe when model not ready) ──


@pytest.mark.asyncio
async def test_reindex_start_blocks_when_model_not_ready(monkeypatch):
    import json
    from types import SimpleNamespace

    from aiohttp.test_utils import make_mocked_request

    from personalclaw.dashboard.handlers import embedding_reindex as H

    # No active embedding model / not downloaded → get_active_embed_fn returns None.
    monkeypatch.setattr(
        "personalclaw.embedding_providers.registry.get_active_embed_fn", lambda: None
    )
    monkeypatch.setattr(
        "personalclaw.embedding_providers.registry._active_embedding_spec", lambda: None
    )

    state = SimpleNamespace(embedding_reindex=lambda: SimpleNamespace())
    req = make_mocked_request("POST", "/api/models/embedding/reindex")
    req.app["state"] = state

    resp = await H.api_reindex_start(req)
    assert resp.status == 409
    assert json.loads(resp.body)["code"] == "model_not_ready"


# ── KL-12: the chunk-backfill boot hook ──────────────────────────────────────
#
# The backfill's product surface is that it runs by itself: a user who upgrades gets deep
# recall over the library they already have without knowing to ask for it. So the hook is
# tested as a hook — that it fires, that it is cheap when there is nothing to do, and that
# something in the app actually registers it.


class _ChunkEmbedder:
    def embed(self, text):
        return [1.0, 0.0, 0.0, 0.0]

    def embed_for_item(self, title, summary, content=None):
        return [1.0, 0.0, 0.0, 0.0]


def _app_with(store):
    class _State:
        knowledge_store = store

    return {"state": _State()}


def _stub_resolve(monkeypatch, embedder):
    from personalclaw.dashboard.handlers import embedding_reindex as handler

    monkeypatch.setattr(handler, "_resolve_embed", lambda app: (embedder, None, "stub:model"))


@pytest.mark.asyncio
async def test_start_chunk_backfill_chunks_the_pre_existing_library(tmp_path, monkeypatch):
    from personalclaw.dashboard.embedding_reindex import start_chunk_backfill

    store = _kstore(tmp_path)
    for i in range(3):
        _add(store, f"doc {i}", f"# H{i}\n\nbody of document {i}\n")
    assert store.count_items_missing_chunks() == 3
    _stub_resolve(monkeypatch, _ChunkEmbedder())

    task = await start_chunk_backfill(_app_with(store))
    assert task is not None
    await task
    assert store.count_items_missing_chunks() == 0
    assert all(store.get_chunks(r["id"]) for r in store.db.execute("SELECT id FROM items"))


@pytest.mark.asyncio
async def test_start_chunk_backfill_is_a_cheap_no_op_on_a_chunked_library(tmp_path, monkeypatch):
    """It runs on EVERY boot, so "nothing to do" must not resolve a model or start a task."""
    from personalclaw.dashboard.embedding_reindex import start_chunk_backfill

    store = _kstore(tmp_path)
    _add(store, "blank", "")  # no content — never in the backlog
    calls = []
    from personalclaw.dashboard.handlers import embedding_reindex as handler

    monkeypatch.setattr(handler, "_resolve_embed", lambda app: calls.append(1) or (None, None, ""))
    assert await start_chunk_backfill(_app_with(store)) is None
    assert calls == [], "the embedder must not be resolved when the backlog is empty"


@pytest.mark.asyncio
async def test_start_chunk_backfill_defers_when_no_model_is_ready(tmp_path, monkeypatch):
    from personalclaw.dashboard.embedding_reindex import start_chunk_backfill

    store = _kstore(tmp_path)
    _add(store, "doc", "# H\n\nreal content\n")
    _stub_resolve(monkeypatch, None)
    assert await start_chunk_backfill(_app_with(store)) is None
    assert store.count_items_missing_chunks() == 1, "still pending, for the next boot"


@pytest.mark.asyncio
async def test_start_chunk_backfill_never_raises_into_startup(tmp_path, monkeypatch):
    from personalclaw.dashboard.embedding_reindex import start_chunk_backfill

    class _Exploding:
        @property
        def knowledge_store(self):
            raise RuntimeError("forced: store unavailable")

    assert await start_chunk_backfill({"state": _Exploding()}) is None


def test_the_gateway_registers_the_chunk_backfill_startup_hook():
    """The function above is only worth anything if something calls it — assert the CALL
    SITE, not just the mechanism."""
    import ast
    import pathlib

    src = pathlib.Path(
        __import__("personalclaw.dashboard.server", fromlist=["x"]).__file__
    ).read_text()
    tree = ast.parse(src)
    appended = {
        node.args[0].id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "append"
        and isinstance(node.func.value, ast.Attribute)
        and node.func.value.attr == "on_startup"
        and node.args
        and isinstance(node.args[0], ast.Name)
    }
    assert "_backfill_item_chunks_startup" in appended
    assert "start_chunk_backfill" in src

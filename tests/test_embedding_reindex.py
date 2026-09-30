"""Embedding re-index on model change (#51).

Switching the active embedding model re-embeds, in both stores, what the new model has not
embedded (clearing a knowledge item's incompatible vector first). Pins the store-level re-embed
primitives and the readiness gate (the change is refused when the new model can't produce
vectors).
"""

from __future__ import annotations

import pytest

from personalclaw.knowledge.embedding_fingerprint import EmbeddingFingerprint
from personalclaw.knowledge.store import KnowledgeStore
from personalclaw.vector_memory import VectorMemoryStore

# ── Knowledge store ──


def _kstore(tmp_path) -> KnowledgeStore:
    return KnowledgeStore(str(tmp_path / "k.db"))


BOUND = EmbeddingFingerprint(model_id="emb-new", provider="rec")


@pytest.fixture
def bound(monkeypatch):
    """Embedding bound to ``rec:emb-new``, as the knowledge store reads the binding."""
    monkeypatch.setattr("personalclaw.knowledge.store.active_fingerprint", lambda: BOUND)
    return BOUND


def _add(store, title, content, summary="", embedding=None, model=None):
    """Create one logical-doc item, optionally with a raw embedding blob and the model that
    wrote it (``(model_id, provider)``; None records none, as before items recorded one)."""
    iid = store.create_typed_item(item_type="note", title=title, content=content, summary=summary)
    if embedding is not None:
        store.db.execute(
            "UPDATE items SET embedding = ?, embedding_model_id = ?, embedding_provider = ? "
            "WHERE id = ?",
            (embedding, *(model or (None, None)), iid),
        )
        store.db.commit()
    return iid


def _vectors(store) -> dict[str, tuple]:
    rows = store.db.execute(
        "SELECT title, embedding, embedding_model_id, embedding_provider FROM items"
    ).fetchall()
    return {
        r["title"]: (r["embedding"], r["embedding_model_id"], r["embedding_provider"]) for r in rows
    }


def test_knowledge_reembeds_what_the_bound_model_has_not(tmp_path, bound):
    """🔴 Red on main: the re-index cleared and re-embedded every item, the ones the bound model
    had embedded included, where memory re-embeds only what the model has not."""
    store = _kstore(tmp_path)
    _add(store, "Kept", "content a", summary="sum a", embedding=b"\x00" * 12, model=bound.params)
    _add(store, "Another model's", "content b", embedding=b"\x11" * 12, model=("emb-old", "rec"))
    _add(store, "Unrecorded", "content c", embedding=b"\x22" * 12)
    _add(store, "Never embedded", "content d")

    assert store.count_items_to_reembed() == 3
    assert store.clear_stale_embeddings() == 2, "the two vectors the bound model did not write"
    assert _vectors(store)["Kept"][0] == b"\x00" * 12, "the bound model's vector is kept"

    # A fake embedder that returns a vector per item. embed_for_item takes the same
    # (title, summary, content) shape the real embedder + reembed_all use.
    class _Emb:
        def embed_for_item(self, title, summary, content=None):
            return [0.1, 0.2, 0.3]

    res = store.reembed_all(_Emb(), only_missing=True)
    assert res == {"reembedded": 3, "failed": 0, "total": 3}
    after = _vectors(store)
    assert after["Kept"][0] == b"\x00" * 12, "and never re-embedded"
    assert {title: v[1:] for title, v in after.items()} == dict.fromkeys(after, bound.params)
    assert store.count_items_to_reembed() == 0, "a second pass has nothing left to do"


def test_nothing_is_stale_while_no_model_is_bound(tmp_path, monkeypatch):
    monkeypatch.setattr("personalclaw.knowledge.store.active_fingerprint", lambda: None)
    store = _kstore(tmp_path)
    _add(store, "Unrecorded", "content c", embedding=b"\x22" * 12)
    assert store.count_items_to_reembed() == 0
    assert store.clear_stale_embeddings() == 0


def test_reembed_all_cannot_use_a_bare_callable(tmp_path):
    """The contract a caller must not "simplify" away (#1782).

    `reembed_all` needs the embedder OBJECT, not a plain embedding function:
    `active_batch_embed_fn` gates the batch path on `isinstance(embedder, UnifiedEmbedder)`
    and `_item_embed_one` looks for `.embed` then `.embed_for_item`. A function has none of
    those, so both resolve to None and every item is left vector-less — with no exception
    raised. The Doctor's backfill job passed exactly this and reported a clean zero for
    however long it shipped.

    Pinned here rather than only at the call site because the failure is SILENT: a future
    caller handing over `get_active_embed_fn()` gets a plausible-looking report whose
    `reembedded` is 0, and nothing else in the system objects.
    """
    from personalclaw.knowledge.embedder import UnifiedEmbedder

    store = _kstore(tmp_path)
    _add(store, "Title A", "content a")
    _add(store, "Title B", "content b")

    def embed(text):
        return [0.1, 0.2, 0.3]

    assert store.reembed_all(embed) == {"reembedded": 0, "failed": 2, "total": 2}
    assert store.count_items_missing_embedding() == 2, "nothing was embedded"

    # The same function, wrapped in the embedder the working callers pass.
    assert store.reembed_all(UnifiedEmbedder(embed)) == {
        "reembedded": 2,
        "failed": 0,
        "total": 2,
    }
    assert store.count_items_missing_embedding() == 0


def test_count_items_missing_embedding_detects_interrupted_reindex(tmp_path, bound):
    """The backlog signal: after clear_stale_embeddings() (start of a re-index) but before
    reembed_all() finishes, text-bearing items report as missing. A whole store reports 0; a
    text-less item never counts."""
    store = _kstore(tmp_path)
    _add(store, "Has text A", "content a", embedding=b"\x00\x00")
    _add(store, "Has text B", "content b", embedding=b"\x11\x11")
    assert store.count_items_missing_embedding() == 0  # whole store → nothing to resume

    store.clear_stale_embeddings()  # re-index begins → the unrecorded vectors are nulled
    assert store.count_items_missing_embedding() == 2  # interrupted signature

    # A text-less item must NOT trigger a phantom resume.
    store.create_typed_item(item_type="note", title="", content="")
    assert store.count_items_missing_embedding() == 2  # still just the 2 text-bearing


def test_count_items_to_reembed_detects_stale_dim(tmp_path, bound):
    """Boot auto-resume must also recover from a model whose output width changed: items keep a
    vector of the bound model at the old width (so missing-count is 0) yet are vector-dead
    against the new query width. count_items_to_reembed(active_dim) catches missing, another
    model's, or stale-dim; the missing-only signal would leave the store silently unsearchable."""
    store = _kstore(tmp_path)
    # 384-dim vectors (384 floats * 4 bytes = 1536 bytes).
    v384 = b"\x00" * (384 * 4)
    _add(store, "Item A", "content a", embedding=v384, model=bound.params)
    _add(store, "Item B", "content b", embedding=v384, model=bound.params)
    # missing-only sees a "whole" store (vectors present) — the gap the old hook had.
    assert store.count_items_missing_embedding() == 0
    # But against the ACTIVE model's 768 dim, both are stale → need re-embed.
    assert store.count_items_to_reembed(768) == 2
    # Same dim → nothing needs re-embedding.
    assert store.count_items_to_reembed(384) == 0
    # Unknown active dim (embedder not ready) → the model alone decides (0 here).
    assert store.count_items_to_reembed(None) == 0
    # A NULL vector counts as needing re-embed regardless of dim.
    store.clear_stale_embeddings(768)
    assert store.count_items_to_reembed(768) == 2


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
    assert store.count_to_reembed() == 0, "nothing embeds, so there is nothing to re-embed with"

    # Now pin an embed_fn and re-embed: both memories are ones it has not embedded.
    store.embed_fn = lambda text: [0.5, 0.5, 0.5]
    assert store.count_to_reembed() == 2
    res = store.reembed_stale()
    assert res["reembedded"] == 2 and res["total"] == 2
    rows = store.db.execute(
        "SELECT embedding FROM episodic_memories WHERE is_deleted = 0"
    ).fetchall()
    assert all(r["embedding"] is not None for r in rows)
    assert store.count_to_reembed() == 0, "a second pass has nothing left to do"
    assert store.index_state()["dim"] == 3 and len(store.index_state()["ids"]) == 2


def test_memory_reembed_noop_without_embed_fn(tmp_path, monkeypatch):
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    store = VectorMemoryStore(db_path=tmp_path / "v.db")
    store.init()
    store.embed_fn = None
    store.write_episodic(
        "a sufficiently long episodic memory to pass length checks", conversation_id="c1"
    )
    res = store.reembed_stale()
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


@pytest.mark.asyncio
async def test_a_refused_reindex_says_the_bound_providers_reason(monkeypatch):
    """🔴 Red before: "The selected embedding model is not available (download it or check the
    provider connection…)" for a bound model whose provider knew it could not sign in."""
    import json
    from types import SimpleNamespace

    from aiohttp.test_utils import make_mocked_request

    from personalclaw.dashboard.handlers import embedding_reindex as H
    from personalclaw.embedding_providers import registry
    from personalclaw.embedding_providers.base import EmbeddingProvider

    reason = "No credentials were found for this account. Sign in, then try again."

    class _SignedOut(EmbeddingProvider):
        @property
        def name(self) -> str:
            return "work-cloud"

        @property
        def display_name(self) -> str:
            return "Work cloud"

        async def is_available(self) -> bool:
            return False

        async def unavailable_reason(self) -> str:
            return reason

        async def embed(self, text: str, model: str = "") -> list[float] | None:
            return None

        async def embed_batch(self, texts: list[str], model: str = "") -> list[list[float]]:
            return [[] for _ in texts]

    monkeypatch.setattr(registry, "get_active_embed_fn", lambda: None)
    monkeypatch.setattr(registry, "_active_embedding_spec", lambda: ("work-cloud", "embed-v1"))
    monkeypatch.setattr(registry, "_ensure_scanned", lambda: None)
    monkeypatch.setitem(registry._providers, "work-cloud", _SignedOut())

    state = SimpleNamespace(embedding_reindex=lambda: SimpleNamespace())
    req = make_mocked_request("POST", "/api/models/embedding/reindex")
    req.app["state"] = state

    resp = await H.api_reindex_start(req)

    assert resp.status == 409
    assert json.loads(resp.body) == {"error": reason, "code": "model_not_ready"}


@pytest.mark.asyncio
async def test_a_provider_with_no_reason_leaves_the_reindex_its_own_words(monkeypatch):
    from personalclaw.embedding_providers import registry

    monkeypatch.setattr(registry, "_active_embedding_spec", lambda: ("absent", "embed-v1"))
    monkeypatch.setattr(registry, "_ensure_scanned", lambda: None)

    assert await registry.bound_unavailable_reason() == ""


# ── The chunk backfill as a graph-maintenance pass ───────────────────────────
#
# The backfill's product surface is that it runs by itself: a user who upgrades gets deep
# recall over the library they already have without knowing to ask for it. It used to run
# from a boot hook, which fires exactly once — so a gateway left up for a week never chunked
# anything ingested after start. It is now a maintenance pass, and is tested as one: that it
# chunks a pre-existing library, that it is cheap when there is nothing to do, that it defers
# when no model is bound, that ONE call is ONE bounded batch (the host owns the loop), and
# that something actually registers it.


class _ChunkEmbedder:
    def embed(self, text):
        return [1.0, 0.0, 0.0, 0.0]

    def embed_for_item(self, title, summary, content=None):
        return [1.0, 0.0, 0.0, 0.0]


def _stub_resolve(monkeypatch, embedder):
    from personalclaw.dashboard.handlers import embedding_reindex as handler

    monkeypatch.setattr(handler, "_resolve_embed", lambda app: (embedder, None, "stub:model"))


def _stub_store(monkeypatch, store):
    """Point the pass's process-wide store accessor at a tmp_path store.

    The pass runs from a tick and has no aiohttp app, so this accessor — not an app dict —
    is the seam. Patched so the real home is never opened.
    """
    import personalclaw.knowledge as knowledge

    monkeypatch.setattr(knowledge, "get_knowledge_store", lambda: store)


def test_chunk_backfill_pass_chunks_the_pre_existing_library(tmp_path, monkeypatch):
    from personalclaw.dashboard.embedding_reindex import chunk_backfill_pass

    store = _kstore(tmp_path)
    for i in range(3):
        _add(store, f"doc {i}", f"# H{i}\n\nbody of document {i}\n")
    assert store.count_items_missing_chunks() == 3
    _stub_store(monkeypatch, store)
    _stub_resolve(monkeypatch, _ChunkEmbedder())

    assert chunk_backfill_pass(batch_size=25) == 3
    assert store.count_items_missing_chunks() == 0
    assert all(store.get_chunks(r["id"]) for r in store.db.execute("SELECT id FROM items"))


def test_chunk_backfill_pass_is_a_cheap_no_op_on_a_chunked_library(tmp_path, monkeypatch):
    """It runs on EVERY tick, so "nothing to do" must not resolve a model (which probes the
    provider) and must report 0 so the host stops claiming sub-batches."""
    from personalclaw.dashboard.embedding_reindex import chunk_backfill_pass

    store = _kstore(tmp_path)
    _add(store, "blank", "")  # no content — never in the backlog
    calls = []
    from personalclaw.dashboard.handlers import embedding_reindex as handler

    _stub_store(monkeypatch, store)
    monkeypatch.setattr(handler, "_resolve_embed", lambda app: calls.append(1) or (None, None, ""))
    assert chunk_backfill_pass(batch_size=25) == 0
    assert calls == [], "the embedder must not be resolved when the backlog is empty"


def test_chunk_backfill_pass_defers_when_no_model_is_ready(tmp_path, monkeypatch):
    from personalclaw.dashboard.embedding_reindex import chunk_backfill_pass

    store = _kstore(tmp_path)
    _add(store, "doc", "# H\n\nreal content\n")
    _stub_store(monkeypatch, store)
    _stub_resolve(monkeypatch, None)
    assert chunk_backfill_pass(batch_size=25) == 0
    assert store.count_items_missing_chunks() == 1, "still pending, for the next tick"


def test_chunk_backfill_pass_claims_one_bounded_batch_per_call(tmp_path, monkeypatch):
    """The host loops until a pass returns 0, so ONE call must be ONE bounded batch. A pass
    that drained the whole library per call would hold the store for a big library and make
    `max_batches` meaningless."""
    from personalclaw.dashboard.embedding_reindex import chunk_backfill_pass

    store = _kstore(tmp_path)
    for i in range(3):
        _add(store, f"doc {i}", f"# H{i}\n\nbody of document {i}\n")
    _stub_store(monkeypatch, store)
    _stub_resolve(monkeypatch, _ChunkEmbedder())

    assert chunk_backfill_pass(batch_size=2) == 2
    assert store.count_items_missing_chunks() == 1, "the rest stays in the backlog"
    assert chunk_backfill_pass(batch_size=2) == 1
    assert store.count_items_missing_chunks() == 0
    assert chunk_backfill_pass(batch_size=2) == 0, "0 == nothing left, which stops the host"


def test_a_chunk_backfill_fault_does_not_take_down_the_maintenance_host(tmp_path, monkeypatch):
    """The pass propagates and the HOST isolates it. Asserted at the host rather than by
    swallowing inside the pass: a pass that ate its own faults would report success forever
    with the backlog untouched, and nothing downstream could tell."""
    import personalclaw.knowledge as knowledge
    from personalclaw.config import loader
    from personalclaw.dashboard.embedding_reindex import register_chunk_backfill_pass
    from personalclaw.knowledge import maintenance

    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)  # watermark file, not the home
    monkeypatch.setattr(maintenance, "_PASSES", {})

    def _boom():
        raise RuntimeError("forced: store unavailable")

    monkeypatch.setattr(knowledge, "get_knowledge_store", _boom)
    ran = []
    # Sorted after "chunk_backfill", so it only runs if the failing pass did not end the run.
    maintenance.register_pass("zz_other", lambda *, batch_size: ran.append(batch_size) or 0)
    register_chunk_backfill_pass()

    result = maintenance.execute(batch_size=5)
    assert "RuntimeError" in result.errors.get("chunk_backfill", "")
    assert ran == [5], "an independent pass must still get its cadence"


def test_the_gateway_registers_the_chunk_backfill_maintenance_pass(monkeypatch):
    """The pass is only worth anything if something registers it — assert the CALL SITE, not
    just the mechanism, and assert the boot hook it replaced is really gone."""
    import ast
    import pathlib

    from personalclaw.dashboard import lifecycle_hooks, server
    from personalclaw.dashboard.embedding_reindex import register_chunk_backfill_pass
    from personalclaw.knowledge import maintenance

    tree = ast.parse(pathlib.Path(server.__file__).read_text())
    called = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "register_chunk_backfill_pass" in called
    # The gateway's start hooks are appended in `dashboard/lifecycle_hooks.py`.
    hooks = ast.parse(pathlib.Path(lifecycle_hooks.__file__).read_text())
    appended = {
        node.args[0].id
        for node in ast.walk(hooks)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "append"
        and isinstance(node.func.value, ast.Attribute)
        and node.func.value.attr == "on_startup"
        and node.args
        and isinstance(node.args[0], ast.Name)
    }
    assert "_model_providers_startup" in appended, "the scan found the start hooks"
    assert "_backfill_item_chunks_startup" not in appended, "the boot hook must stay deleted"

    # …and the registrar really registers, under the name the host will look up.
    monkeypatch.setattr(maintenance, "_PASSES", {})
    register_chunk_backfill_pass()
    assert maintenance.registered_passes() == ["chunk_backfill"]

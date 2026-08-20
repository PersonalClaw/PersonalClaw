"""Batched chunk/item embedding at its two call sites (KL-15).

The spine (``knowledge.embed_batch``) owns the batch/retry/bisect algebra. This file pins the
two CALL SITES that were the atom's named defect:

- ``pipeline.runner.embed_item_chunks`` made one provider call per chunk inside a bare
  ``except Exception: vec = None`` — so a transient failure became a permanently vector-less
  chunk with no log line anywhere.
- ``store.reembed_all`` looped ``embed_for_item`` one item at a time over the whole library.

Every claim here is asserted by COUNTING calls on a fake and by reading the ``chunks`` /
``items`` rows the write actually produced — never by timing, and never from the fake's own
bookkeeping alone, because a fake that is never reached also reports "no failures".
"""

from __future__ import annotations

import pytest

from personalclaw.embedding_providers import registry
from personalclaw.knowledge import embed_batch
from personalclaw.knowledge.chunking import chunk_text
from personalclaw.knowledge.embedder import (
    UnifiedEmbedder,
    bytes_to_floats,
    compose_item_text,
    floats_to_bytes,
)
from personalclaw.knowledge.pipeline.runner import embed_item_chunks
from personalclaw.knowledge.store import KnowledgeStore

# Six markdown sections → six chunks. Six rather than five so a bisection of the failing
# group is an even 3+3 split and the arithmetic in the failure test is readable.
SIX_SECTIONS = "\n".join(
    f"## Section {i}\nBody text for section {i} with enough words to matter.\n" for i in range(6)
)


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    """Never touch the real ~/.personalclaw — `embed_batch` reads config for its batch size
    and retry budget, and `AppConfig.load()` would otherwise read the owner's own home.

    The retry budget is pinned to 1 so no test here sleeps through the spine's real backoff
    (0.5s then 1.0s per failing group, and a bisection produces several). The schedule itself
    is the spine's contract to prove, not this file's; what this file proves is where the
    results land.
    """
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    monkeypatch.setattr(embed_batch, "retry_budget_from_config", lambda: 1)


@pytest.fixture
def store(tmp_path):
    s = KnowledgeStore(str(tmp_path / "k.db"))
    try:
        yield s
    finally:
        s.close()


def _vec(text: str) -> list[float]:
    """A content-derived vector, so a stored embedding proves WHICH text produced it.

    A constant vector would pass an alignment test that shuffled every chunk's vector.
    """
    return [float(sum(ord(ch) for ch in text))]


def _chunk_rows(store, item_id: str) -> list[tuple[str, bytes | None]]:
    rows = store.db.execute(
        "SELECT text, embedding FROM chunks WHERE item_id = ? ORDER BY chunk_index",
        (item_id,),
    ).fetchall()
    return [(r["text"], r["embedding"]) for r in rows]


def _item(store, title="Batched", content=SIX_SECTIONS, summary="") -> str:
    return store.create_typed_item(item_type="note", title=title, content=content, summary=summary)


# ── embed_item_chunks: one call, not N ──


def test_all_chunks_of_an_item_embed_in_one_provider_call(store, monkeypatch):
    """The atom's headline claim, asserted by counting calls on the batch fn."""
    calls: list[list[str]] = []

    def _many(texts):
        calls.append(list(texts))
        return [_vec(t) for t in texts]

    monkeypatch.setattr(registry, "get_active_embed_many_fn", lambda: _many)
    per_text: list[str] = []
    embedder = UnifiedEmbedder(lambda t: per_text.append(t) or _vec(t))

    item_id = _item(store)
    assert _chunk_rows(store, item_id) == []  # nothing wrote chunks before us
    embed_item_chunks(store, item_id, SIX_SECTIONS, embedder)

    expected_texts = [c.text for c in chunk_text(SIX_SECTIONS)]
    assert len(expected_texts) == 6
    # ONE call carrying all six texts — not six calls, and not a batch that dropped any.
    assert len(calls) == 1
    assert calls[0] == expected_texts
    # The per-text path was not used at all; batching is not a wrapper around it here.
    assert per_text == []


def test_every_chunk_is_stored_with_its_own_aligned_vector(store, monkeypatch):
    """No chunk dropped, and no chunk wearing another chunk's vector."""
    monkeypatch.setattr(
        registry, "get_active_embed_many_fn", lambda: lambda texts: [_vec(t) for t in texts]
    )
    item_id = _item(store)
    embed_item_chunks(store, item_id, SIX_SECTIONS, UnifiedEmbedder(_vec))

    rows = _chunk_rows(store, item_id)
    assert len(rows) == 6
    for text, blob in rows:
        assert blob is not None
        assert bytes_to_floats(blob) == _vec(text)


def test_one_unembeddable_chunk_is_stored_vector_less_and_the_rest_keep_theirs(store, monkeypatch):
    """A single bad text costs that chunk its vector — not the item's whole chunk layer,
    and not the chunk row itself (a vector-less chunk is still keyword/FTS reachable)."""

    def _many(texts):
        # A batch-shaped failure: the group is refused as a group, so the spine bisects
        # until the offending text is alone.
        if any("Section 3" in t for t in texts):
            raise RuntimeError("provider refused the batch")
        return [_vec(t) for t in texts]

    monkeypatch.setattr(registry, "get_active_embed_many_fn", lambda: _many)
    item_id = _item(store)
    embed_item_chunks(store, item_id, SIX_SECTIONS, UnifiedEmbedder(_vec))

    rows = _chunk_rows(store, item_id)
    assert len(rows) == 6  # nothing dropped
    for text, blob in rows:
        if "Section 3" in text:
            assert blob is None
        else:
            assert blob is not None and bytes_to_floats(blob) == _vec(text)


# ── embed_item_chunks: the per-text fallback ──


def test_provider_without_a_batch_path_still_embeds_every_chunk(store, monkeypatch):
    """`get_active_embed_many_fn()` returning None is the "no batch path" signal. Embedding
    must still happen, per text, and every chunk must still get its vector."""
    monkeypatch.setattr(registry, "get_active_embed_many_fn", lambda: None)
    seen: list[str] = []

    def _one(text):
        seen.append(text)
        return _vec(text)

    item_id = _item(store)
    embed_item_chunks(store, item_id, SIX_SECTIONS, UnifiedEmbedder(_one))

    expected_texts = [c.text for c in chunk_text(SIX_SECTIONS)]
    assert seen == expected_texts  # six per-text calls, in order
    rows = _chunk_rows(store, item_id)
    assert len(rows) == 6
    for text, blob in rows:
        assert blob is not None and bytes_to_floats(blob) == _vec(text)


def test_a_caller_supplied_embedder_is_never_batched_through_the_registry(store, monkeypatch):
    """The registry's batch fn resolves the ACTIVE embedding selection. An embedder the
    caller handed in may be a different model, so it keeps going through its own `.embed`:
    otherwise chunk vectors from one model would land beside item vectors from another."""
    resolved: list[str] = []

    def _accessor():
        resolved.append("resolved")
        return lambda texts: [_vec(t) for t in texts]

    monkeypatch.setattr(registry, "get_active_embed_many_fn", _accessor)

    class _Stub:
        def __init__(self):
            self.calls = 0

        def embed(self, text):
            self.calls += 1
            return _vec(text)

    stub = _Stub()
    item_id = _item(store)
    embed_item_chunks(store, item_id, SIX_SECTIONS, stub)

    assert resolved == []  # the batch accessor was never even consulted
    assert stub.calls == 6
    assert all(blob is not None for _t, blob in _chunk_rows(store, item_id))


# ── embed_item_chunks: vacuity ──


def test_embedder_without_embed_reaches_no_embedding_path_at_all(store, monkeypatch):
    """Vacuity guard: with no `.embed` the function must not reach the batch path.

    NOTE the preserved shape — the `.embed` guard is an EARLY RETURN, so such an embedder
    writes no chunk rows either. That predates this atom (`embed_item_chunks`' docstring
    calls it "chunk embedding is skipped"), and changing it would silently reclassify every
    such item in `chunk_backfill`'s chunked/unchanged accounting, which is not this atom's
    scope. What this test guarantees is that the batching added here is unreachable when
    there is nothing to embed with.
    """
    resolved: list[str] = []
    monkeypatch.setattr(
        registry,
        "get_active_embed_many_fn",
        lambda: resolved.append("resolved") or (lambda texts: [_vec(t) for t in texts]),
    )

    class _NoEmbed:
        def embed_for_item(self, title, summary, content=None):  # pragma: no cover - unused
            raise AssertionError("embed_for_item is not the chunk path")

    item_id = _item(store)
    embed_item_chunks(store, item_id, SIX_SECTIONS, _NoEmbed())

    assert resolved == []
    assert _chunk_rows(store, item_id) == []


def test_an_empty_document_still_clears_previous_chunk_rows(store, monkeypatch):
    """`replace_chunks` must be reached even with zero chunks — it is what deletes a
    previous generation's rows. Guarding the whole write behind `if chunks:` would leave
    stale chunks behind after an edit emptied a document."""
    monkeypatch.setattr(
        registry, "get_active_embed_many_fn", lambda: lambda texts: [_vec(t) for t in texts]
    )
    embedder = UnifiedEmbedder(_vec)
    item_id = _item(store)
    embed_item_chunks(store, item_id, SIX_SECTIONS, embedder)
    assert len(_chunk_rows(store, item_id)) == 6

    embed_item_chunks(store, item_id, "   \n\n  ", embedder)
    assert _chunk_rows(store, item_id) == []


# ── reembed_all ──


def _add(store, title, content, summary=""):
    return store.create_typed_item(item_type="note", title=title, content=content, summary=summary)


def _item_blobs(store) -> dict[str, bytes | None]:
    rows = store.db.execute("SELECT id, embedding FROM items").fetchall()
    return {r["id"]: r["embedding"] for r in rows}


def test_reembed_all_embeds_the_library_in_one_batch_call(store, monkeypatch):
    calls: list[list[str]] = []

    def _many(texts):
        calls.append(list(texts))
        return [_vec(t) for t in texts]

    monkeypatch.setattr(registry, "get_active_embed_many_fn", lambda: _many)
    ids = [_add(store, f"Title {i}", f"content {i}", summary=f"sum {i}") for i in range(5)]
    progress: list[tuple[int, int]] = []

    res = store.reembed_all(UnifiedEmbedder(_vec), on_progress=lambda d, t: progress.append((d, t)))

    assert res == {"reembedded": 5, "failed": 0, "total": 5}
    assert len(calls) == 1 and len(calls[0]) == 5  # one call for five items
    # on_progress still fires once per item, in order — job-progress streaming is unchanged.
    assert progress == [(1, 5), (2, 5), (3, 5), (4, 5), (5, 5)]
    blobs = _item_blobs(store)
    for i, iid in enumerate(ids):
        expected = compose_item_text(f"Title {i}", f"sum {i}", f"content {i}")
        assert bytes_to_floats(blobs[iid]) == _vec(expected)


def test_reembed_all_vectors_match_the_per_item_embed_for_item_path(store):
    """The composition moved into `reembed_all`, so the vectors must be byte-identical to
    what `embedder.embed_for_item(title, summary, content)` produced per item before."""

    class _ComposeEmb:
        """Only `embed_for_item` — the shape `reembed_all`'s docstring promises and the
        re-index tests pass. No `.embed`, so this also exercises the adapter."""

        def embed_for_item(self, title, summary, content=None):
            text = compose_item_text(title, summary, content)
            return _vec(text) if text.strip() else None

    embedder = _ComposeEmb()
    cases = [("Alpha", "body alpha", "sum a"), ("", "body with no title at all", "")]
    ids = [_add(store, t, c, summary=s) for t, c, s in cases]

    res = store.reembed_all(embedder)
    assert res == {"reembedded": 2, "failed": 0, "total": 2}

    blobs = _item_blobs(store)
    for (title, content, summary), iid in zip(cases, ids):
        text_title = title or content[:200]
        expected = embedder.embed_for_item(text_title, summary or None, content)
        assert blobs[iid] == floats_to_bytes(expected)


def test_reembed_all_leaves_a_failing_item_vector_less_without_corrupting_the_rest(
    store, monkeypatch
):
    def _many(texts):
        if any("Bad" in t for t in texts):
            raise RuntimeError("provider refused the batch")
        return [_vec(t) for t in texts]

    monkeypatch.setattr(registry, "get_active_embed_many_fn", lambda: _many)
    good = _add(store, "Good one", "content good", summary="sum good")
    bad = _add(store, "Bad one", "content bad", summary="sum bad")
    progress: list[tuple[int, int]] = []

    res = store.reembed_all(UnifiedEmbedder(_vec), on_progress=lambda d, t: progress.append((d, t)))

    assert res == {"reembedded": 1, "failed": 1, "total": 2}
    assert progress == [(1, 2), (2, 2)]  # progress fires for the failure too
    blobs = _item_blobs(store)
    assert blobs[bad] is None  # left vector-less, not corrupted and not deleted
    assert bytes_to_floats(blobs[good]) == _vec(compose_item_text("Good one", "sum good"))
    # The row itself survives with its text intact — still keyword/FTS reachable.
    row = store.db.execute("SELECT title FROM items WHERE id = ?", (bad,)).fetchone()
    assert row["title"] == "Bad one"

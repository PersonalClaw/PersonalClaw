"""Differential refresh AT ITS CALL SITE (#1783, PR block 4).

`semantics.changed_sections` and the per-section digests were written, tested, and never
reached: the stored map loaded (`consolidation.Item.chunk_hashes`) and nothing ever wrote it, so
every re-ingest re-embedded every chunk of every item. Re-embedding a 40k-character report
because one paragraph changed is the cost the helper exists to avoid, and a helper nothing calls
avoids nothing.

So the assertions here are about REACHABILITY, which is the whole subject. Every one counts
provider calls on a fake or reads the `chunks` rows the write produced — a test that called
`changed_sections` directly would pass against the definition-only state this closes.
"""

from __future__ import annotations

import pytest

from personalclaw.embedding_providers import registry
from personalclaw.knowledge import embed_batch, semantics
from personalclaw.knowledge.chunking import chunk_text, section_key
from personalclaw.knowledge.pipeline.runner import embed_item_chunks
from personalclaw.knowledge.store import KnowledgeStore

#: Six markdown sections. The document a differential refresh is FOR: editing one of them must
#: cost one section's embedding, not six.
SECTIONS = [
    f"## Section {i}\nBody text for section {i} with enough words to matter.\n" for i in range(6)
]
SIX_SECTIONS = "\n".join(SECTIONS)

#: The same document with ONE section's body edited. Built by replacing an element rather than
#: by a string edit, so the change is provably confined to section 3.
EDITED = "\n".join(
    [
        *SECTIONS[:3],
        "## Section 3\nBody text for section 3 REWRITTEN with different words.\n",
        *SECTIONS[4:],
    ]
)

MODEL_A = ("native", "all-MiniLM-L6-v2")
MODEL_B = ("ollama", "bge-small-en")


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    """Never touch the real ~/.personalclaw — `embed_batch` reads config for its batch size."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    monkeypatch.setattr(embed_batch, "retry_budget_from_config", lambda: 1)


@pytest.fixture
def store(tmp_path):
    s = KnowledgeStore(str(tmp_path / "k.db"))
    try:
        yield s
    finally:
        s.close()


def _bind(monkeypatch, spec: tuple[str, str] | None) -> None:
    """Bind an embedding selection through the ONE accessor `active_fingerprint` resolves."""
    monkeypatch.setattr(
        "personalclaw.embedding_providers.registry._active_embedding_spec", lambda: spec
    )


def _vec(text: str) -> list[float]:
    """A content-derived vector, so a stored embedding proves WHICH text produced it."""
    return [float(sum(ord(ch) for ch in text))]


class _Provider:
    """Records every text it was asked to embed, across calls.

    `salt` makes a second embedding of the SAME text produce DIFFERENT bytes. Without it a
    deterministic fake cannot distinguish "the vector was carried forward" from "the text was
    re-embedded and happened to match", which is the claim these tests exist to make.
    """

    def __init__(self):
        self.texts: list[str] = []
        self.salt = 0.0

    def embed_many(self, texts):
        self.texts.extend(texts)
        return [[v + self.salt for v in _vec(t)] for t in texts]

    def embed(self, text):
        self.texts.append(text)
        return [v + self.salt for v in _vec(text)]


@pytest.fixture
def provider(monkeypatch):
    p = _Provider()
    monkeypatch.setattr(registry, "get_active_embed_many_fn", lambda: p.embed_many)
    return p


def _blobs(store, item_id: str) -> dict[str, bytes | None]:
    rows = store.db.execute(
        "SELECT text, embedding FROM chunks WHERE item_id = ? ORDER BY chunk_index", (item_id,)
    ).fetchall()
    return {r["text"]: r["embedding"] for r in rows}


def _stored_hashes(store, item_id: str) -> dict:
    return (store.get_item(item_id)["file_metadata"] or {}).get("chunk_hashes") or {}


def _item(store, content=SIX_SECTIONS) -> str:
    return store.create_typed_item(item_type="note", title="Report", content=content)


# ── the call site ──


def test_the_live_refresh_path_calls_changed_sections(store, provider, monkeypatch):
    """The reachability claim itself. `embed_item_chunks` is the ONE chunk-write unit (the ingest
    pipeline's embed node and `chunk_backfill` both go through it), so proving the comparison
    happens HERE is proving it happens on every refresh in the product."""
    calls: list[tuple[dict, dict]] = []
    real = semantics.changed_sections

    def _spy(stored, fresh):
        calls.append((dict(stored), dict(fresh)))
        return real(stored, fresh)

    monkeypatch.setattr(semantics, "changed_sections", _spy)

    item_id = _item(store)
    embed_item_chunks(store, item_id, SIX_SECTIONS, provider)  # first pass: nothing stored yet
    embed_item_chunks(store, item_id, EDITED, provider)  # the refresh

    assert len(calls) == 2
    first_stored, first_fresh = calls[0]
    assert first_stored == {}  # no prior generation
    assert len(first_fresh) == 6  # one digest per section
    second_stored, _second_fresh = calls[1]
    # The refresh compared against the digests the FIRST pass persisted — the loop is closed,
    # which is what "the stored side loads but nothing writes it" failed to be.
    assert second_stored == first_fresh


def test_a_single_section_edit_re_embeds_one_section_not_the_whole_item(store, provider):
    """The cost claim, counted at the provider."""
    item_id = _item(store)
    embed_item_chunks(store, item_id, SIX_SECTIONS, provider)
    assert len(provider.texts) == 6  # the first pass pays for everything, as before

    provider.texts.clear()
    embed_item_chunks(store, item_id, EDITED, provider)

    edited = [c for c in chunk_text(EDITED) if "REWRITTEN" in c.text]
    assert len(edited) == 1
    assert provider.texts == [edited[0].text]


def test_an_unchanged_section_keeps_the_EXACT_vector_it_already_had(store, provider):
    """Carrying a vector forward must carry the vector, not merely skip the call: a section
    that ended up vector-less would be silently unretrievable, which is worse than paying.

    The salt is what makes this an assertion about reuse. A re-embed of the same text now
    yields different bytes, so byte-equality can only come from the stored row."""
    item_id = _item(store)
    embed_item_chunks(store, item_id, SIX_SECTIONS, provider)
    before = _blobs(store, item_id)

    provider.salt = 1000.0
    embed_item_chunks(store, item_id, EDITED, provider)
    after = _blobs(store, item_id)

    assert len(after) == 6
    assert all(blob is not None for blob in after.values())
    carried = [text for text in after if text in before]
    assert len(carried) == 5  # five untouched sections
    for text in carried:
        assert after[text] == before[text]


def test_an_unedited_re_ingest_embeds_nothing_at_all(store, provider):
    """The degenerate refresh — a re-poll of a source that did not change — is the case that
    used to cost a full re-embed of the document every time."""
    item_id = _item(store)
    embed_item_chunks(store, item_id, SIX_SECTIONS, provider)
    provider.texts.clear()

    embed_item_chunks(store, item_id, SIX_SECTIONS, provider)

    assert provider.texts == []
    assert all(blob is not None for blob in _blobs(store, item_id).values())


# ── the stored side ──


def test_the_digests_are_written_keyed_by_section(store, provider):
    """The write path the issue found missing. Keyed by SECTION rather than position, so an
    inserted section does not renumber every key and force a full re-embed."""
    item_id = _item(store)
    embed_item_chunks(store, item_id, SIX_SECTIONS, provider)

    stored = _stored_hashes(store, item_id)
    assert set(stored) == {section_key(c) for c in chunk_text(SIX_SECTIONS)}
    assert stored == semantics.chunk_hashes(
        {section_key(c): c.text for c in chunk_text(SIX_SECTIONS)}
    )


def test_a_prepended_section_does_not_invalidate_the_ones_after_it(store, provider):
    """Position-keyed digests would report every section changed here."""
    item_id = _item(store)
    embed_item_chunks(store, item_id, SIX_SECTIONS, provider)
    provider.texts.clear()

    prepended = "## Section New\nA section added at the top of the document.\n\n" + SIX_SECTIONS
    embed_item_chunks(store, item_id, prepended, provider)

    assert len(provider.texts) == 1
    assert "Section New" in provider.texts[0]


def test_a_document_that_loses_all_its_text_drops_the_digests(store, provider):
    """An empty map would otherwise persist as a claim about a generation that no longer
    exists; the absence of the key is the honest record and re-embeds on the next pass."""
    item_id = _item(store)
    embed_item_chunks(store, item_id, SIX_SECTIONS, provider)
    assert _stored_hashes(store, item_id)

    embed_item_chunks(store, item_id, "", provider)

    assert _stored_hashes(store, item_id) == {}
    assert _blobs(store, item_id) == {}


# ── the fingerprint guard ──


def test_a_model_swap_re_embeds_every_section_even_though_none_changed(store, monkeypatch):
    """Reuse must never carry a vector across embedding models. `replace_chunks` stamps every
    row it writes with the model active NOW, so carrying MODEL_A's vector into a MODEL_B row
    would produce exactly the mixed-model corruption RET-4's fingerprint exists to prevent —
    a vector scored against another model's queries with every guard in the product passing."""
    _bind(monkeypatch, MODEL_A)
    a = _Provider()
    monkeypatch.setattr(registry, "get_active_embed_many_fn", lambda: a.embed_many)
    item_id = _item(store)
    embed_item_chunks(store, item_id, SIX_SECTIONS, a)
    assert len(a.texts) == 6

    _bind(monkeypatch, MODEL_B)
    b = _Provider()
    monkeypatch.setattr(registry, "get_active_embed_many_fn", lambda: b.embed_many)
    # Byte-identical content: the section digests all match, so ONLY the fingerprint guard can
    # send these to the provider again.
    embed_item_chunks(store, item_id, SIX_SECTIONS, b)

    assert len(b.texts) == 6
    rows = store.db.execute(
        "SELECT embedding_model_id FROM chunks WHERE item_id = ?", (item_id,)
    ).fetchall()
    assert {r["embedding_model_id"] for r in rows} == {MODEL_B[1]}


def test_a_vector_less_chunk_is_not_treated_as_reusable(store, monkeypatch):
    """A section whose embedding failed has no vector to carry, so an unchanged digest must not
    leave it vector-less for good — the silent direction, and the one a digest-only check would
    take."""
    refuse = {"Section 3"}

    class _Flaky(_Provider):
        """Refuses BOTH entry points. `embed_texts` bisects a failed batch down to single texts
        and then tries `embed_one`, so a fake that only refuses the batch path gets rescued and
        never produces the vector-less row this test is about."""

        def embed_many(self, texts):
            if any(any(m in t for m in refuse) for t in texts):
                raise RuntimeError("provider refused the batch")
            return super().embed_many(texts)

        def embed(self, text):
            if any(m in text for m in refuse):
                raise RuntimeError("provider refused the text")
            return super().embed(text)

    p = _Flaky()
    monkeypatch.setattr(registry, "get_active_embed_many_fn", lambda: p.embed_many)
    item_id = _item(store)
    embed_item_chunks(store, item_id, SIX_SECTIONS, p)
    assert [t for t, blob in _blobs(store, item_id).items() if blob is None]

    refuse.clear()
    p.texts.clear()
    embed_item_chunks(store, item_id, SIX_SECTIONS, p)

    # Unchanged content, so the digests say nothing changed — yet the vector-less chunk is
    # retried, and only it.
    assert len(p.texts) == 1
    assert "Section 3" in p.texts[0]
    assert all(blob is not None for blob in _blobs(store, item_id).values())

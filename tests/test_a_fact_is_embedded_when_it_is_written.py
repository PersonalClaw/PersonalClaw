"""A saved fact holds its vector, written with it, so a recall never embeds it again.

The fact and lesson ranking used to embed every stored row at every question: one recall over a
store of 32 facts and 6 lessons was 39 round trips to the embedding model. Episodes and lessons
were embedded when written; facts never were. Now a fact is embedded when it is written, as an
episode is, with the model that wrote its vector recorded beside it; the re-index embeds a fact
written while no model was bound (or that another model embedded); and every surface that counts
what search reads by keyword counts facts too, so the gateway's start finds them and embeds them.
"""

from __future__ import annotations

import hashlib
import math
import re

import pytest

from personalclaw.vector_memory import VectorMemoryStore


def _bag(text: str) -> list[float]:
    vec = [0.0] * 64
    for word in re.findall(r"\w+", text.lower()):
        bits = int(hashlib.sha256(word.encode()).hexdigest(), 16)
        for i in range(64):
            vec[i] += 1.0 if (bits >> i) & 1 else -1.0
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / norm for x in vec]


class _Binding:
    """What Settings → Models has bound: a model ref and its embed function, or nothing."""

    def __init__(self) -> None:
        self.ref: str | None = "test:bag"
        self.calls: list[str] = []
        self.fails = False

    def embed(self, text: str) -> list[float] | None:
        self.calls.append(text)
        return None if self.fails else _bag(text)

    def current(self):
        return (self.embed, self.ref) if self.ref else (None, None)


@pytest.fixture
def binding(monkeypatch) -> _Binding:
    b = _Binding()
    monkeypatch.setattr(VectorMemoryStore, "_embedder", lambda self: b.current())
    monkeypatch.setattr(VectorMemoryStore, "_embedding_ref", lambda self: b.ref)
    return b


@pytest.fixture
def store(tmp_path, binding):
    s = VectorMemoryStore(db_path=tmp_path / "memory.db")
    s.init()
    s._graph_enabled = False
    yield s
    s.close()


def _vector_of(store: VectorMemoryStore, key: str) -> tuple[bytes | None, str | None]:
    row = store.db.execute(
        "SELECT embedding, embedding_model FROM semantic_memory WHERE key = ?", (key,)
    ).fetchone()
    return row["embedding"], row["embedding_model"]


def test_a_fact_holds_a_vector_of_the_model_bound_when_it_was_written(store, binding):
    """🔴 Red before: a fact was stored with no vector, so every recall embedded it again."""
    assert store.set_semantic("pref.editor", "vim", 1.0, "user_explicit") is None

    blob, model = _vector_of(store, "pref.editor")
    assert model == "test:bag"
    assert blob is not None and len(blob) == 64 * 4
    assert binding.calls == ['pref.editor "vim"'], "embedded as the ranking compares it"


def test_a_recall_compares_a_fact_by_the_vector_it_holds(store, binding):
    """The ranking reads the stored vector: the question is the only text it embeds."""
    store.set_semantic("pref.kitchen_dishwasher_placement", "left of the sink", 1.0, "user")
    store.set_semantic("pref.editor", "vim", 1.0, "user")
    binding.calls.clear()

    rows = store.rank_semantic("where does the dishwasher go", limit=5, related_only=True)

    assert binding.calls == ["where does the dishwasher go"]
    assert [r["key"] for r in rows] == ["pref.kitchen_dishwasher_placement"]


def test_a_fact_written_while_nothing_was_bound_is_counted_and_embedded_by_the_reindex(
    store, binding
):
    """🔴 Red before: the re-index re-embedded only semantic rows that already held a vector, and
    the counts every surface reads counted episodes alone, so a fact with none stayed so."""
    binding.ref = None
    assert store.set_semantic("pref.editor", "vim", 1.0, "user_explicit") is None
    assert _vector_of(store, "pref.editor") == (None, None)

    binding.ref = "test:bag"
    stats = store.memory_stats()
    assert stats["unembedded"] == 1 and stats["read_by_keyword"] == 1, stats
    assert store.count_to_reembed() == 1

    assert store.reembed_stale()["reembedded"] == 1
    blob, model = _vector_of(store, "pref.editor")
    assert blob is not None and model == "test:bag"
    after = store.memory_stats()
    assert after["unembedded"] == 0 and after["embedded_count"] == 1, after


def test_a_fact_another_model_embedded_is_re_embedded_with_the_model_bound_now(store, binding):
    store.set_semantic("pref.editor", "vim", 1.0, "user_explicit")
    binding.ref = "test:other"

    assert store.memory_stats()["embedded_stale"] == 1
    assert store.reembed_stale()["reembedded"] == 1
    assert _vector_of(store, "pref.editor")[1] == "test:other"


def test_a_fact_whose_new_value_the_model_cannot_embed_keeps_no_vector_of_the_old_one(
    store, binding
):
    """A vector of the old value would rank the new one by words it no longer holds."""
    store.set_semantic("pref.editor", "vim", 1.0, "user_explicit")
    binding.fails = True
    assert store.set_semantic("pref.editor", "emacs", 1.0, "user_explicit") is None

    assert _vector_of(store, "pref.editor") == (None, None)
    assert store.memory_stats()["unembedded"] == 1, "and it is counted until the re-index"


def test_writing_a_fact_again_with_the_same_value_embeds_nothing(store, binding):
    store.set_semantic("pref.editor", "vim", 1.0, "user_explicit")
    binding.calls.clear()

    assert store.set_semantic("pref.editor", "vim", 1.0, "user_explicit") is None
    assert 'pref.editor "vim"' not in binding.calls, "the vector it holds is still this value's"
    assert _vector_of(store, "pref.editor")[1] == "test:bag"


def test_rows_that_are_not_facts_are_not_embedded_on_write(store, binding):
    """Only what the recall ranks holds a vector: a persona note or an approval rule has a writer
    and a reader of its own, and embedding it would be a model call for nothing."""
    assert store.set_semantic("user.persona.abc123", "prefers short replies", 1.0, "user") is None
    assert store.set_semantic("user.approval.sender", {"verdict": "deny"}, 1.0, "user") is None

    assert binding.calls == []
    assert store.memory_stats()["unembedded"] == 0

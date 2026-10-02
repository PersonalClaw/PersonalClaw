"""The memory search index takes its width from the vectors it holds, never from a default.

What a default install showed (no faiss: it is the ``[embeddings]`` extra): every episodic memory
embedded at 1024 by the bound model, and consolidation skipping all of them "embedded at another
width than the model now writes (384)". 384 was the store's constructor default. Without faiss the
store has no index at all, so nothing ever replaced that default, and consolidation read it as the
width the model writes. With faiss the same default was what an index built before its first
vector claimed, so an index that missed its first vectors (another store on the same database
wrote them) stayed empty.

The rules pinned here:

* an index holds no width until it holds a vector, and then holds every vector of the model at
  that width, whoever wrote them;
* a change of the embedding model re-indexes at the new model's width;
* consolidation reads every memory the bound model embedded at the width it writes now (its newest
  vector's), with or without faiss;
* with no faiss there is no index, so the stats count none.

Driven with fake embedding models of two widths, bound the way Settings → Models binds one.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pytest

from personalclaw import vector_memory
from personalclaw.vector_memory import VectorMemoryStore

ENTRY = "fake-widths"
WIDTHS = {"narrow": 384, "wide": 1024}

#: Six preferences said across three conversations: one pattern consolidation should promote.
PREFERENCES = (
    ("conv-1", "User prefers tea over coffee in the morning"),
    ("conv-1", "User prefers green tea when working late"),
    ("conv-2", "User prefers loose-leaf tea to tea bags"),
    ("conv-2", "User prefers tea brewed for three minutes"),
    ("conv-3", "User prefers a teapot over a mug for guests"),
    ("conv-3", "User prefers oolong tea on weekends"),
)
_AXIS = {text: i + 1 for i, (_conv, text) in enumerate(PREFERENCES)}


def _vector(width: int, text: str) -> list[float]:
    """One shared direction plus a direction of its own per preference: any two of them are
    similar enough to cluster (cosine 0.8 > 0.75) and not so similar that a write merges them
    (0.8 < 0.88). Any other text (a query, the re-index's width probe) is the shared direction."""
    vec = [0.0] * width
    vec[0] = 1.0
    if text in _AXIS:
        vec[_AXIS[text]] = 0.5
    return vec


class _FakeModel:
    """A built embedding provider whose model decides the width of every vector it returns, read
    at each call: a test can change a model's output width without changing the model."""

    def __init__(self, model: str) -> None:
        self._model = model

    async def start(self) -> None:
        return None

    async def embed(self, inputs: list[str]) -> list[list[float]]:
        return [_vector(WIDTHS[self._model], text) for text in inputs]


@pytest.fixture
def models(monkeypatch):
    """One configured instance of a fake provider type whose models are 384 and 1024 wide."""
    from personalclaw.config.loader import config_path
    from personalclaw.llm.capabilities import Capability, ProviderCapability
    from personalclaw.llm.registry import ProviderEntry, ProviderRegistry, set_default_registry

    registry = ProviderRegistry()

    def _factory(*, entry: ProviderEntry, session_key: str | None = None, **kwargs: Any):
        return _FakeModel(str(kwargs.get("embedding_model") or ""))

    registry.register_type(
        ProviderCapability(
            type=ENTRY,
            capabilities=frozenset({Capability.EMBEDDING}),
            supports_streaming=False,
            supports_tools=False,
            supports_embeddings=True,
            supports_vision=False,
            max_context_tokens=8192,
        ),
        _factory,
    )
    registry.register_entry(ProviderEntry(name=ENTRY, type=ENTRY, model="", options={}))
    set_default_registry(registry)  # conftest restores the singleton afterwards
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"providers": [{"name": ENTRY, "type": ENTRY, "model": ""}]}))


def _bind(model: str | None) -> None:
    """Bind Embedding the way Settings → Models' PUT does — or clear it (``None``)."""
    from personalclaw.providers.use_cases import load_active_models, save_active_models

    active = load_active_models()
    active["embedding"] = [f"{ENTRY}:{model}"] if model else []
    save_active_models(active)


def _faiss(monkeypatch, installed: bool) -> None:
    """Run the store with or without faiss. Without it is the default install's case."""
    if installed:
        pytest.importorskip("faiss")  # an [embeddings] extra; `[dev]` installs it
    else:
        monkeypatch.setattr(vector_memory, "_HAS_FAISS", False)


def _store(path: Path) -> VectorMemoryStore:
    store = VectorMemoryStore(db_path=path)  # the gateway's construction: no width given
    store.init()
    return store


def _vectors(store: VectorMemoryStore) -> set[tuple[int, str | None]]:
    """``{(width, model)}`` over every live episodic vector."""
    rows = store.db.execute(
        "SELECT length(embedding) / 4, embedding_model FROM episodic_memories "
        "WHERE is_deleted = 0 AND embedding IS NOT NULL"
    ).fetchall()
    return {(int(r[0]), r[1]) for r in rows}


def _live_ids(store: VectorMemoryStore) -> set[str]:
    rows = store.db.execute("SELECT id FROM episodic_memories WHERE is_deleted = 0").fetchall()
    return {r[0] for r in rows}


def _remember(store: VectorMemoryStore, items=PREFERENCES) -> None:
    for conversation, text in items:
        assert store.write_episodic(text, conversation_id=conversation, importance=0.9), text


def _consolidates_every_preference(store: VectorMemoryStore, caplog) -> None:
    """One consolidation pass promotes the six preferences into one fact, reading all six."""
    with caplog.at_level(logging.WARNING, logger="personalclaw.vector_memory"):
        promoted = store.promote_episodic_patterns()
    assert "Consolidation skipped" not in caplog.text, caplog.text
    assert promoted == 1, "the six preferences are one pattern"
    assert store.get_semantic("pref.general") is not None
    assert _live_ids(store) == set(), "the promoted memories are folded into the fact"


# ── a model change re-indexes at the new width ────────────────────────────────────────────


def _reindexed(path: Path, before: str | None) -> VectorMemoryStore:
    """Six memories under a 384-wide model (or none at all), then a 1024-wide one bound and the
    re-index Settings → Models starts after a rebind run on the store."""
    _bind(before)
    store = _store(path)
    _remember(store)
    assert _vectors(store) == ({(384, f"{ENTRY}:narrow")} if before else set())

    _bind("wide")
    assert store.reembed_stale() == {"reembedded": 6, "failed": 0, "total": 6}
    assert _vectors(store) == {(1024, f"{ENTRY}:wide")}
    return store


_CHANGES = pytest.mark.parametrize("before", ["narrow", None], ids=["from-384", "from-unembedded"])
_INSTALLS = pytest.mark.parametrize("installed", [True, False], ids=["faiss", "no-faiss"])


@_INSTALLS
@_CHANGES
def test_a_model_change_reindexes_at_the_new_width(
    models, tmp_path, monkeypatch, installed, before
):
    """After the re-index the index holds every vector at 1024. Without faiss there is no index,
    and the store's 384 default used to stand in for one, claiming a width no vector had."""
    _faiss(monkeypatch, installed)
    store = _reindexed(tmp_path / "memory.db", before)

    index = store.index_state()
    if installed:
        assert index["dim"] == 1024
        assert set(index["ids"]) == _live_ids(store), "the index holds every vector"
    else:
        assert index == {
            "dim": 0,
            "ids": [],
            "embedding_model": f"{ENTRY}:wide",
        }, "with no faiss there is no index, so nothing claims a width"
    store.close()


@_INSTALLS
@_CHANGES
def test_consolidation_reads_every_memory_the_new_model_embedded(
    models, tmp_path, monkeypatch, caplog, installed, before
):
    """Consolidation used to take the width the model writes from the index, and without faiss
    that was the store's 384 default: every memory the re-index had just embedded at 1024 was
    skipped "embedded at another width than the model now writes (384)"."""
    _faiss(monkeypatch, installed)
    store = _reindexed(tmp_path / "memory.db", before)
    _consolidates_every_preference(store, caplog)
    store.close()


@_INSTALLS
def test_the_memories_consolidation_cannot_read_are_the_ones_the_doctor_flags(
    models, monkeypatch, caplog, installed
):
    """A model whose output width changed leaves memories at the old width: consolidation cannot
    read them, and the Doctor's memory check must name exactly those, with its Fix. The two used
    to answer from different widths — consolidation from the index (without faiss, the store's 384
    default), the Doctor from the vectors — so a pass skipping every memory sat under "All
    systems healthy"."""
    import asyncio

    from personalclaw.config.loader import config_dir
    from personalclaw.resilience import doctor
    from personalclaw.resilience.doctor import DoctorContext

    _faiss(monkeypatch, installed)
    monkeypatch.setattr(
        "personalclaw.providers.provider_bridge.can_resolve_use_case", lambda use_case: True
    )
    home = config_dir()
    _bind("wide")
    monkeypatch.setitem(WIDTHS, "wide", 384)
    store = _store(home / "memory.db")
    store.serve_recall()
    _remember(store, PREFERENCES[:3])
    monkeypatch.setitem(WIDTHS, "wide", 1024)  # the model now writes 1024-wide vectors
    _remember(store, PREFERENCES[3:])

    with caplog.at_level(logging.WARNING, logger="personalclaw.vector_memory"):
        store.promote_episodic_patterns()
    assert (
        "Consolidation skipped 3 episodic memories embedded at another width than the model now "
        "writes (1024)" in caplog.text
    ), caplog.text

    check = asyncio.run(doctor._probe_memory(DoctorContext(home=home)))
    assert check.ok is False and check.fix_id == doctor.MEMORY_INDEX_FIX, check
    assert check.evidence["other_model"] == 3, "the same three, counted by the check"
    store.close()


# ── an index built empty takes its first vectors' width ───────────────────────────────────


def test_an_index_built_empty_has_no_width_until_its_first_vector(models, tmp_path):
    """The store's 384 default used to be the empty index's width, and every reader took it for
    the model's: an index holds a width only once it holds a vector."""
    pytest.importorskip("faiss")
    _bind("wide")
    store = _store(tmp_path / "memory.db")
    assert store.index_state()["dim"] == 0 and store.index_state()["ids"] == []

    _remember(store, PREFERENCES[:1])
    (only,) = _live_ids(store)
    assert store.index_state()["dim"] == 1024
    assert store.index_state()["ids"] == [only]
    store.close()


def test_an_empty_index_takes_in_the_vectors_another_store_wrote(models, tmp_path, caplog):
    """A second store on the same database (the CLI's one-shot extraction is one) writes the first
    vectors. The served store had already built its index with none, so its first own write made
    an index of one vector: the others were never in it, and semantic recall missed them."""
    pytest.importorskip("faiss")
    _bind("wide")
    served = _store(tmp_path / "memory.db")
    served.serve_recall()
    assert served.search_episodic(query_embedding=_vector(1024, "tea"), query_text="tea") == []

    other = _store(tmp_path / "memory.db")
    _remember(other, PREFERENCES[:5])
    other.close()

    _remember(served, PREFERENCES[5:])
    index = served.index_state()
    assert index["dim"] == 1024
    assert set(index["ids"]) == _live_ids(served), "every vector of the model, whoever wrote it"
    _consolidates_every_preference(served, caplog)
    served.close()


def test_a_wider_vector_of_the_same_model_rebuilds_the_index_at_its_width(tmp_path, caplog):
    """A model whose output width changed writes the width it writes now: the index follows the
    newest vector, the way the Memory page's count and keyword recall already do, instead of
    keeping the old vectors and leaving the newest memory out of it."""
    pytest.importorskip("faiss")
    store = _store(tmp_path / "memory.db")
    store.write_episodic("An early note embedded eight wide", embedding=[1.0] + [0.1] * 7)
    store.write_episodic("A second note from before the change", embedding=[0.1, 1.0] + [0.1] * 6)
    with caplog.at_level(logging.WARNING, logger="personalclaw.vector_memory"):
        store.write_episodic("The first note embedded sixteen wide", embedding=[1.0] + [0.1] * 15)
    newest = store.db.execute(
        "SELECT id FROM episodic_memories WHERE length(embedding) = 64"
    ).fetchone()[0]

    assert store.index_state()["dim"] == 16
    assert store.index_state()["ids"] == [newest]
    assert "16-dim" in caplog.text and "8-dim" in caplog.text and "re-embed" in caplog.text
    hits = store.search_episodic(query_embedding=[1.0] + [0.1] * 15, query_text="sixteen")
    assert hits and hits[0]["id"] == newest
    store.close()


def test_a_saved_index_that_holds_nothing_leaves_no_file_behind(models, tmp_path):
    """An index of no vectors has no width to save. A file an earlier index left describes another
    index, and neither the next open nor the Doctor's read of the files may take it for this one."""
    pytest.importorskip("faiss")
    _bind("wide")
    store = _store(tmp_path / "memory.db")
    _remember(store, PREFERENCES[:2])
    store.save_faiss_index()
    assert (tmp_path / "memory.faiss").exists()

    _bind(None)  # nothing is bound: the index points at the vectors that name no model — none
    assert store.rebuild_faiss_index()["indexed"] == 0
    assert not (tmp_path / "memory.faiss").exists()
    assert json.loads((tmp_path / "memory.ids.json").read_text(encoding="utf-8")) == []
    store.close()


# ── with no faiss there is no index to count ──────────────────────────────────────────────


@pytest.mark.parametrize("installed", [True, False], ids=["faiss", "no-faiss"])
def test_the_stats_count_an_index_only_where_there_is_one(models, tmp_path, monkeypatch, installed):
    """ "0 faiss index size" beside "40 embedded count" read as an index that lost every memory, on
    an install that has no index at all: without faiss, semantic recall compares the stored
    vectors directly, and finds them."""
    _faiss(monkeypatch, installed)
    _bind("wide")
    store = _store(tmp_path / "memory.db")
    _remember(store, PREFERENCES[:2])

    stats = store.memory_stats()
    assert stats["embedded_count"] == 2
    assert stats["episodes_embedded"] == 2
    if installed:
        assert stats["faiss_index_size"] == 2
    else:
        assert "faiss_index_size" not in stats
    hits = store.search_episodic(query_embedding=_vector(1024, "tea"), query_text="tea")
    assert {h["text"] for h in hits} == {text for _c, text in PREFERENCES[:2]}
    store.close()


def _vector_of(store: VectorMemoryStore, key: str) -> tuple:
    row = store.db.execute(
        "SELECT length(embedding) / 4, embedding_model FROM semantic_memory WHERE key = ?", (key,)
    ).fetchone()
    return tuple(row) if row else ()


def test_the_index_count_is_of_the_episodes_search_can_return(models, tmp_path, caplog):
    """A memory consolidation folds into a fact (or one deleted) keeps its vector in the index
    until the next build, and search skips it. Counted as index rows, the stats said "faiss index
    size 6" with no episode left; the count is of the live episodes the index holds.

    The fact the six became is embedded as it is written, so the Embedded count, which counts
    every memory recall compares by meaning, holds it. The index holds episodes only (a fact is
    compared by its own vector), so it is set beside the embedded EPISODES, never beside that
    count: "0 faiss index size · 1 embedded count" read as an index missing a memory."""
    pytest.importorskip("faiss")
    _bind("wide")
    store = _store(tmp_path / "memory.db")
    _remember(store)
    _consolidates_every_preference(store, caplog)

    stats = store.memory_stats()
    assert stats["faiss_index_size"] == 0
    assert stats["episodes_embedded"] == 0
    assert stats["embedded_count"] == 1, "the fact the six became"
    assert _vector_of(store, "pref.general") == (1024, f"{ENTRY}:wide")
    store.close()


def test_the_doctor_compares_the_index_with_the_embedded_episodes(models, monkeypatch):
    """The index holds episodes; facts and lessons hold vectors of their own and are in no index.
    With a fact embedded and every episode indexed the check is healthy, and with an episode the
    index does not hold, its sentence counts episodes: the numbers the Memory page shows beside the
    index, not the Embedded count, which also counts the fact."""
    import asyncio

    from personalclaw.config.loader import config_dir
    from personalclaw.resilience import doctor
    from personalclaw.resilience.doctor import DoctorContext

    pytest.importorskip("faiss")
    home = config_dir()
    _bind("wide")
    store = _store(home / "memory.db")
    store.serve_recall()
    _remember(store, PREFERENCES[:3])
    assert (
        store.set_semantic("pref.kettle", "descaler under the sink", 1.0, "user_explicit") is None
    )
    assert _vector_of(store, "pref.kettle") == (1024, f"{ENTRY}:wide")

    stats = store.memory_stats()
    assert (stats["embedded_count"], stats["episodes_embedded"], stats["faiss_index_size"]) == (
        4,
        3,
        3,
    )
    in_step = asyncio.run(doctor._probe_memory(DoctorContext(home=home)))
    assert in_step.ok is True, in_step

    other = _store(home / "memory.db")  # another store on the same database
    _remember(other, PREFERENCES[3:4])
    other.close()
    gap = asyncio.run(doctor._probe_memory(DoctorContext(home=home)))
    assert gap.ok is False and gap.fix_id == doctor.MEMORY_INDEX_FIX, gap
    assert gap.detail == "faiss index desync: 3 of 4 embedded episodes indexed"
    after = store.memory_stats()
    assert (after["episodes_embedded"], after["faiss_index_size"]) == (4, 3)
    store.close()

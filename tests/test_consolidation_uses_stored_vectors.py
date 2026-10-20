"""The consolidation pass clusters on the vectors items already have, and costs no model call.

The pass used to embed every item's whole text (title, summary and content) once per pairwise
comparison, on every run, and threw each answer away when the run ended. On a hosted embedding
model with an input limit, a long document was refused as too long, fell back to token
similarity, and was sent again at the next comparison and the next run: thousands of refused
calls an hour from a dry run that changes nothing. These tests drive the real provider over a
real store (in the test's own home) with a fake embedding model that counts what it is sent.

The unattended maintenance tick also used to re-plan the store every time anything was written,
because a dry run marks nothing consolidated and so the min-hours floor never closed for it; and
its calls reached the call log with no caller. Both are asserted here too.
"""

from __future__ import annotations

import asyncio
import json
from collections import Counter

import pytest

from personalclaw.action_providers import knowledge_maintain_provider as kmp
from personalclaw.action_providers.base import ActionContext
from personalclaw.embedding_providers import registry
from personalclaw.knowledge.embedder import floats_to_bytes

PROVIDER = "fake-embed"
MODEL = "fake-embed-small"

# Six items the token metric also groups into one cluster (see the cadence test's fixture),
# distinct enough that the near-duplicate pre-pass keeps all six.
_SHARED = (
    "postgres connection pooling pgbouncer transaction mode prepared statements "
    "session backend client ceiling idle rotation"
)
_TAILS = (
    "restarting drops idle clients",
    "server cursors break midway",
    "max conn binds first",
    "listen notify stops working",
    "advisory locks leak across",
    "temp tables vanish early",
)

#: Near one direction: every pair is far above the cosine cluster threshold.
_NEAR = [[1.0, 0.02 * n, 0.0, 0.0] for n in range(len(_TAILS))]


class _CountingModel:
    """The embedding model behind the real embed wrapper: counts every text it is sent."""

    def __init__(self, *, refuse: bool = False) -> None:
        self.sent: Counter[str] = Counter()
        self.refuse = refuse

    def __call__(self, text: str) -> list[float] | None:
        self.sent[text] += 1
        if self.refuse:
            raise ValueError("input is too long for this model")
        return [1.0, 0.0, 0.0, 0.0]

    @property
    def calls(self) -> int:
        return sum(self.sent.values())


@pytest.fixture
def model(monkeypatch):
    """A selected embedding model whose every call is counted and reaches the call log."""
    fake = _CountingModel()
    monkeypatch.setattr(registry, "_active_embedding_spec", lambda: (PROVIDER, MODEL))
    monkeypatch.setattr(registry, "_embed_fn_for", lambda provider, model_id: fake)
    return fake


@pytest.fixture
def store():
    from personalclaw.knowledge.store import KnowledgeStore, knowledge_db_path

    return KnowledgeStore(db_path=str(knowledge_db_path()))


def _add_items(store, *, vectors=None, model_id=MODEL, provider=PROVIDER) -> list[str]:
    ids: list[str] = []
    for n, tail in enumerate(_TAILS):
        item_id = store.create_typed_item(
            item_type="note",
            title=f"Pooling note {n}",
            summary=_SHARED,
            content=f"{_SHARED} {tail} " + "long body text " * 400,
        )
        assert item_id
        ids.append(item_id)
        if vectors is not None and vectors[n] is not None:
            store.db.execute(
                "UPDATE items SET embedding = ?, embedding_model_id = ?, embedding_provider = ? "
                "WHERE id = ?",
                (floats_to_bytes(vectors[n]), model_id, provider, item_id),
            )
    store.db.commit()
    return ids


def _add_chunks(store, item_id: str, vectors: list[list[float]]) -> None:
    for index, vector in enumerate(vectors):
        store.db.execute(
            "INSERT INTO chunks (id, item_id, chunk_index, text, embedding, "
            "embedding_model_id, embedding_provider) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                f"{item_id}-c{index}",
                item_id,
                index,
                "chunk",
                floats_to_bytes(vector),
                MODEL,
                PROVIDER,
            ),
        )
    store.db.commit()


def _owner_run() -> dict:
    """The owner's consolidation, as the panel or a workflow node starts it."""
    result = asyncio.run(
        kmp.KnowledgeConsolidateActionProvider().execute(
            {}, ActionContext(event="", context="", payload={})
        )
    )
    assert result.success, result.error
    return json.loads(result.stdout)


# ── no model calls ──


def test_two_dry_runs_over_stored_vectors_make_no_embed_calls(store, model):
    _add_items(store, vectors=_NEAR)

    first = _owner_run()
    second = _owner_run()

    assert model.calls == 0, f"the dry run embedded {model.calls} text(s)"
    # Not vacuous: the stored vectors did the clustering.
    assert [c["size"] for c in first["plan"]["clusters"]] == [6]
    assert second["plan"]["clusters"] == first["plan"]["clusters"]


def test_a_refused_text_is_sent_at_most_once_per_run(store, model):
    """No stored vectors and a model that refuses every text as too long."""
    model.refuse = True
    _add_items(store)

    payload = _owner_run()

    worst = max(model.sent.values(), default=0)
    assert worst <= 1, f"one refused text was sent {worst} times in one run"
    # With no vector anywhere the whole pass is on the token scale, and still finds the cluster.
    assert [c["size"] for c in payload["plan"]["clusters"]] == [6]
    assert payload["sat_out"] == 0


# ── which vectors the pass uses ──


def test_the_mean_of_chunk_vectors_stands_in_for_a_missing_item_vector(store, model):
    ids = _add_items(store)
    for n, item_id in enumerate(ids):
        _add_chunks(store, item_id, [[1.0, 0.0, 0.01 * n, 0.0], [1.0, 0.04, 0.0, 0.0]])

    payload = _owner_run()

    assert model.calls == 0
    assert [c["size"] for c in payload["plan"]["clusters"]] == [6]
    assert payload["sat_out"] == 0


def test_items_without_a_vector_sit_out_a_vector_pass(store, model, tmp_path):
    """Two scales are never mixed: an item without a vector is left out, and the count says so."""
    from personalclaw.config.loader import config_dir

    (config_dir() / "config.json").write_text(
        json.dumps({"knowledge": {"consolidate_min_cluster": 2}})
    )
    vectors = list(_NEAR[:4]) + [None, None]
    ids = _add_items(store, vectors=vectors)

    payload = _owner_run()

    assert model.calls == 0
    assert payload["sat_out"] == 2
    assert set(payload["plan"]["sat_out"]) == set(ids[4:])
    clustered = {i for c in payload["plan"]["clusters"] for i in c["ids"]}
    assert clustered == set(ids[:4])


def test_a_vector_another_model_wrote_is_not_compared(store, model):
    _add_items(store, vectors=_NEAR, model_id="another-model")

    payload = _owner_run()

    assert model.calls == 0
    # No vector of the selected model: the token scale for the whole pass, nothing sits out.
    assert payload["sat_out"] == 0
    assert [c["size"] for c in payload["plan"]["clusters"]] == [6]


# ── the unattended tick's own floor ──


def test_only_the_unattended_dry_run_is_held_back_by_its_last_run(store, model):
    """Both sides in one test: the tick waits out its floor, the owner's run never does."""
    _add_items(store, vectors=_NEAR)

    assert kmp.run_consolidation_pass(batch_size=0) == 1
    assert kmp.run_consolidation_pass(batch_size=0) == 0, "the tick re-planned within the floor"

    payload = _owner_run()

    assert payload["ran"] is True, "the owner's consolidation was held back by the tick's run"
    assert [c["size"] for c in payload["plan"]["clusters"]] == [6]


# ── attribution ──


def test_a_maintenance_embed_names_its_caller_in_the_call_log(model):
    """Driven the way the backup loop drives the tick: on an executor's worker thread."""
    import personalclaw.durability.service as ds
    from personalclaw.guardrails.audit import read_recent
    from personalclaw.knowledge import maintenance as m

    def _embedding_pass(*, batch_size: int = 0) -> int:
        embed = registry.embed_fn_for(PROVIDER, MODEL)
        assert embed is not None
        embed("a maintenance text")
        return 0

    m.clear_passes()
    m.set_in_flight_probe(lambda: 0)
    try:
        m.register_pass("embedding_probe", _embedding_pass)
        m.mark_dirty(now=1000.0)

        async def _loop() -> None:
            await asyncio.get_running_loop().run_in_executor(None, ds._tick_graph_maintenance)

        asyncio.run(_loop())
    finally:
        m.clear_passes()
        m.set_in_flight_probe(None)

    rows = [r for r in read_recent() if r.get("use_case") == "embedding"]
    assert model.calls == 1
    assert [r["caller"] for r in rows] == ["knowledge_maintenance"]

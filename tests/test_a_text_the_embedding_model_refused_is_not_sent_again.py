"""A text the embedding model refused for its size is not sent again until it or the model changes.

Two knowledge jobs drain the items that have no vector: the maintenance tick's derived refresh,
and the Doctor's "Backfill missing knowledge embeddings". The model refused a long item's text
("Too many input tokens"), and nothing remembered that: the refresh picks items with no vector in
id order, so the refused item came first and was sent again on every pass, and a batch's worth of
them at the front kept every item behind them waiting. The backfill raised when every item failed,
so it never took its cooldown and sent the same texts again on every remediation tick.

A size refusal of one text is now remembered for that text and that model, and passed over until
either changes; the backfill counts such items apart and takes its cooldown when only they remain.
"""

from __future__ import annotations

from collections import Counter

import pytest

from personalclaw.embedding_providers import registry
from personalclaw.knowledge.embed_batch import embed_texts

PROVIDER = "fake-embed"
MODEL = "fake-embed-small"
#: The model's input limit, in characters: a text past it is refused for its size.
LIMIT = 2000


class _Model:
    """An embedding model with an input limit, counting every text it is sent."""

    def __init__(self) -> None:
        self.sent: Counter[str] = Counter()

    def embed(self, text: str) -> list[float]:
        self.sent[text] += 1
        if len(text) > LIMIT:
            raise ValueError(
                "Too many input tokens. Max input tokens: 8192, request input token count: 11258"
            )
        return [1.0, 0.0, 0.0, 0.0]

    def long_sends(self) -> int:
        return sum(n for text, n in self.sent.items() if len(text) > LIMIT)


@pytest.fixture
def model(monkeypatch):
    fake = _Model()
    monkeypatch.setattr(registry, "_active_embedding_spec", lambda: (PROVIDER, MODEL))
    import personalclaw.knowledge as knowledge

    monkeypatch.setattr(knowledge, "get_knowledge_embedder", lambda: fake)
    return fake


@pytest.fixture
def store(monkeypatch):
    import personalclaw.knowledge as knowledge
    from personalclaw.knowledge.store import KnowledgeStore, knowledge_db_path

    opened = KnowledgeStore(db_path=str(knowledge_db_path()))
    monkeypatch.setattr(knowledge, "get_knowledge_store", lambda: opened)
    return opened


#: An item's vector is of its title and summary (`compose_item_text`): a summary past the limit.
_LONG = "allotment soil survey " * 200


def _item(store, title: str, summary: str = "") -> str:
    item_id = store.create_typed_item(
        item_type="note", title=title, summary=summary, content=f"{title}. {summary}"
    )
    assert item_id
    return item_id


def _vector_less(store) -> set[str]:
    return {
        r["id"] for r in store.db.execute("SELECT id FROM items WHERE embedding IS NULL").fetchall()
    }


def test_a_lone_text_refused_for_its_size_is_sent_once_and_named():
    model = _Model()
    refused: set[int] = set()

    got = embed_texts(
        ["short", "x" * (LIMIT + 1)], embed_one=model.embed, batch_size=1, refused=refused
    )

    assert got[0] is not None and got[1] is None
    assert refused == {1}
    assert model.long_sends() == 1, "a size refusal is no blip to retry"


def test_a_rate_limit_is_not_a_size_refusal():
    from personalclaw.knowledge.embed_batch import refuses_the_text

    assert refuses_the_text(ValueError("Too many input tokens. Max input tokens: 8192"))
    assert not refuses_the_text(ValueError("Too many requests, please slow down"))
    assert not refuses_the_text(ValueError("ThrottlingException: rate limit exceeded"))


@pytest.mark.parametrize(
    "said",
    ["upstream timed out after 4130 ms", "request exceeds the retry budget"],
)
def test_a_failure_that_only_looks_like_a_size_refusal_is_not_one(said):
    """A text called refused is held back until it or the model changes: a number holding 413, and
    "exceeds" with no size beside it, are no refusal."""
    from personalclaw.knowledge.embed_batch import refuses_the_text

    assert not refuses_the_text(RuntimeError(said))


@pytest.mark.parametrize(
    "said",
    [
        "413 Payload Too Large",
        "input length exceeds the context length",
        "Request exceeds max allowed tokens",
    ],
)
def test_a_size_refusal_in_other_words_is_still_one(said):
    from personalclaw.knowledge.embed_batch import refuses_the_text

    assert refuses_the_text(RuntimeError(said))


def test_the_derived_refresh_sends_a_refused_text_on_its_first_pass_only(store, model):
    """🔴 Red before: the refused item was sent again on every pass."""
    from personalclaw.knowledge.maintenance_passes import _derived_refresh_pass

    long_id = _item(store, "A long report", _LONG)
    short_ids = {_item(store, f"Bed {n}", f"the bed {n} gets morning sun") for n in range(3)}
    store.db.execute("UPDATE items SET embedding = NULL")
    store.db.commit()

    _derived_refresh_pass(batch_size=10)
    first = model.long_sends()
    for _ in range(2):  # the watermark moved: the host runs the pass again
        _derived_refresh_pass(batch_size=10)

    assert first >= 1
    assert model.long_sends() == first, "a refused text was sent again"
    assert _vector_less(store) == {long_id}, "the items behind it were held back"
    assert short_ids.isdisjoint(_vector_less(store))


def test_a_changed_text_is_asked_again(store, model):
    long_id = _item(store, "A long report", _LONG)
    store.db.execute("UPDATE items SET embedding = NULL")
    store.db.commit()
    assert store.reembed_all(model, only_missing=True)["refused"] == 1
    assert store.reembed_all(model, only_missing=True) == {
        "reembedded": 0,
        "failed": 0,
        "total": 0,
        "refused": 0,
        "held": 1,
    }

    store.db.execute(
        "UPDATE items SET summary = 'a short summary of the survey', "
        "updated_at = '2099-01-01T00:00:00Z' WHERE id = ?",
        (long_id,),
    )
    store.db.commit()

    assert store.reembed_all(model, only_missing=True)["reembedded"] == 1


def test_the_backfill_of_only_refused_items_takes_its_cooldown(store, model):
    """🔴 Red before: every item failing raised, so no cooldown applied and each remediation
    tick sent the same refused texts again."""
    from personalclaw.resilience import remediation

    for n in range(2):
        _item(store, f"Long report {n}", f"{n} {_LONG}")
    store.db.execute("UPDATE items SET embedding = NULL")
    store.db.commit()
    # Returning, not raising, is what records the job's success, and so its cooldown.
    detail = remediation._job_reindex_embeddings()
    sent = model.long_sends()
    again = remediation._job_reindex_embeddings()

    assert "2 item(s) the embedding model refused as too long" in detail
    assert model.long_sends() == sent, "the held items were sent again"
    assert "2 item(s) the embedding model refused as too long" in again

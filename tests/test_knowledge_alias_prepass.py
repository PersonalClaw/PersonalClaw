"""Deterministic mention linking for knowledge ingestion (MEMORY-GRAPH §1.3).

The gap being closed: the entities stage was **LLM-only**, so with no model bound a document
that plainly names a known entity produced ZERO mentions — the graph looked empty because the
extractor never ran, not because the document said nothing.

The two risks these tests target:

* **Silent no-op.** A pre-pass that runs but links nothing (empty index, matcher failure,
  malformed aliases) is indistinguishable from the bug it fixes.
* **Being erased by the stage it runs inside.** `clear_item_entities` deletes this item's
  mentions AND any entity left with no mentions at all — so a naive ordering loses the
  pre-pass's work on every item that DOES have a model, which is the harder case to notice.
"""

from __future__ import annotations

import pytest

from personalclaw.knowledge.alias_prepass import (
    MAX_MENTIONS_PER_ITEM,
    build_index,
    link_known_entities,
)


@pytest.fixture()
def store(tmp_path):
    from personalclaw.knowledge.store import KnowledgeStore

    # KnowledgeStore takes the db path directly (no config_dir seam to patch).
    return KnowledgeStore(tmp_path / "k.db")


def _item(store, content="some text", title="T") -> str:
    return store.create_typed_item(item_type="note", title=title, content=content)


def _linked_names(store, item_id) -> set[str]:
    rows = store.db.execute(
        "SELECT e.name FROM mentions m JOIN entities e ON e.id = m.entity_id "
        "WHERE m.item_id = ?",
        (item_id,),
    ).fetchall()
    return {r["name"] for r in rows}


def _mention_ids(store, item_id) -> set[str]:
    rows = store.db.execute(
        "SELECT entity_id FROM mentions WHERE item_id = ?", (item_id,)
    ).fetchall()
    return {r["entity_id"] for r in rows}


# ── The index ───────────────────────────────────────────────────────────


def test_index_is_empty_with_no_entities(store):
    """A fresh install must cost nothing — the caller no-ops on an empty index."""
    index, names = build_index(store)
    assert len(index) == 0
    assert names == {}


def test_index_covers_names_and_aliases(store):
    eid = store.add_entity(name="Sparrow", entity_type="project", aliases=["@sparrow", "SPRW"])
    index, names = build_index(store)
    assert names[eid] == "Sparrow"
    assert len(index) >= 3


def test_index_skips_malformed_aliases(store):
    """One bad row must not cost every other entity its links."""
    good = store.add_entity(name="Sparrow", entity_type="project")
    bad = store.add_entity(name="Kestrel", entity_type="project")
    store.db.execute("UPDATE entities SET aliases = ? WHERE id = ?", ("{not json", bad))
    store.db.commit()
    index, names = build_index(store)
    assert good in names and bad in names, "the entity still indexes under its NAME"
    assert len(index) >= 2


def test_index_skips_a_non_list_alias_blob(store):
    eid = store.add_entity(name="Sparrow", entity_type="project")
    store.db.execute("UPDATE entities SET aliases = ? WHERE id = ?", ('"a string"', eid))
    store.db.commit()
    index, names = build_index(store)
    assert eid in names


def test_index_survives_an_unreadable_table(store):
    """A db error during the entity read must degrade to "no links", not raise.

    Patching `sqlite3.Connection.execute` directly is impossible (read-only attribute), so
    this uses a stand-in whose db raises — the same shape the code sees.
    """

    class _BrokenDb:
        def execute(self, *a, **k):
            raise RuntimeError("gone")

    class _BrokenStore:
        db = _BrokenDb()

    index, names = build_index(_BrokenStore())
    assert len(index) == 0 and names == {}


# ── Linking ─────────────────────────────────────────────────────────────


def test_links_a_known_entity_by_name(store):
    eid = store.add_entity(name="Sparrow", entity_type="project")
    item_id = _item(store, "The Sparrow release ships Friday.")
    assert link_known_entities(store, item_id, "The Sparrow release ships Friday.") == 1
    assert _mention_ids(store, item_id) == {eid}


def test_links_by_declared_alias(store):
    """The case a model most often misses: the doc uses the alias, not the canonical name."""
    eid = store.add_entity(name="Sparrow", entity_type="project", aliases=["SPRW"])
    item_id = _item(store)
    assert link_known_entities(store, item_id, "SPRW is on track.") == 1
    assert _mention_ids(store, item_id) == {eid}


def test_does_not_invent_entities(store):
    """The pre-pass can only LINK. Discovery is the extractor's job."""
    item_id = _item(store)
    assert link_known_entities(store, item_id, "Kestrel is brand new here.") == 0
    assert store.db.execute("SELECT COUNT(*) c FROM entities").fetchone()["c"] == 0


def test_one_mention_per_entity_however_many_hits(store):
    """Forty hits of one name is one (item, entity) row; recording forty is pure waste."""
    eid = store.add_entity(name="Sparrow", entity_type="project")
    text = " ".join(["Sparrow"] * 40)
    item_id = _item(store)
    assert link_known_entities(store, item_id, text) == 1
    assert _mention_ids(store, item_id) == {eid}


def test_links_several_distinct_entities(store):
    a = store.add_entity(name="Sparrow", entity_type="project")
    b = store.add_entity(name="Kestrel", entity_type="project")
    item_id = _item(store)
    assert link_known_entities(store, item_id, "Sparrow depends on Kestrel.") == 2
    assert _mention_ids(store, item_id) == {a, b}


def test_records_context_so_the_link_is_explainable(store):
    """A reader must see WHY an item was linked without opening the document."""
    store.add_entity(name="Sparrow", entity_type="project")
    item_id = _item(store)
    link_known_entities(store, item_id, "Long preamble. The Sparrow release ships Friday.")
    row = store.db.execute("SELECT context FROM mentions WHERE item_id = ?", (item_id,)).fetchone()
    assert row["context"] and "Sparrow" in row["context"]


def test_is_idempotent(store):
    """add_mention is INSERT OR IGNORE; re-running must not duplicate."""
    store.add_entity(name="Sparrow", entity_type="project")
    item_id = _item(store)
    link_known_entities(store, item_id, "Sparrow ships.")
    link_known_entities(store, item_id, "Sparrow ships.")
    rows = store.db.execute(
        "SELECT COUNT(*) c FROM mentions WHERE item_id = ?", (item_id,)
    ).fetchone()
    assert rows["c"] == 1


def test_caps_distinct_mentions_per_item(store):
    """A glossary page must not attach itself to the entire graph."""
    for i in range(MAX_MENTIONS_PER_ITEM + 10):
        store.add_entity(name=f"Entity{i:03d}", entity_type="concept")
    text = " ".join(f"Entity{i:03d}" for i in range(MAX_MENTIONS_PER_ITEM + 10))
    item_id = _item(store)
    assert link_known_entities(store, item_id, text) == MAX_MENTIONS_PER_ITEM


@pytest.mark.parametrize("text", ["", "   ", "\n\n"])
def test_empty_text_links_nothing(store, text):
    store.add_entity(name="Sparrow", entity_type="project")
    item_id = _item(store)
    assert link_known_entities(store, item_id, text) == 0


def test_missing_item_id_links_nothing(store):
    store.add_entity(name="Sparrow", entity_type="project")
    assert link_known_entities(store, "", "Sparrow ships.") == 0


def test_a_matcher_failure_is_survivable(store, monkeypatch):
    """Linking is an enhancement; a failure must not break ingestion."""
    store.add_entity(name="Sparrow", entity_type="project")
    item_id = _item(store)
    import personalclaw.memory_graph as mg

    monkeypatch.setattr(
        mg.AliasIndex, "find", lambda self, text: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    assert link_known_entities(store, item_id, "Sparrow ships.") == 0


def test_uses_the_same_matcher_as_the_memory_store(store):
    """One matcher, so the knowledge graph and the push reflex agree what a mention is.

    Two matchers would drift, and the symptom is a document that links in one surface and not
    the other — with nothing to point at.
    """
    from personalclaw.memory_graph import AliasIndex

    index, _ = build_index(store)
    assert isinstance(index, AliasIndex)


def test_short_single_token_entities_are_not_matched(store):
    """Inherits the matcher's ambiguity floor rather than re-deciding it here."""
    store.add_entity(name="Go", entity_type="concept")
    item_id = _item(store)
    assert link_known_entities(store, item_id, "Go to the store and go home.") == 0


def test_longest_match_wins(store):
    """ "Sparrow Release" beats a bare "Sparrow" when both are known entities."""
    short = store.add_entity(name="Sparrow", entity_type="project")
    long = store.add_entity(name="Sparrow Release", entity_type="concept")
    item_id = _item(store)
    link_known_entities(store, item_id, "The Sparrow Release ships Friday.")
    linked = _mention_ids(store, item_id)
    assert long in linked
    assert short not in linked, "the longer surface form should have consumed the tokens"


# ── The pipeline seam ───────────────────────────────────────────────────


class TestEntitiesStage:
    """The stage's own contract — where the ordering bug would live."""

    @pytest.mark.asyncio
    async def test_links_with_no_model_bound(self, store):
        """THE headline fix: pool=None used to mean zero mentions, forever."""
        from personalclaw.knowledge.pipeline.runner import _run_entities_stage

        eid = store.add_entity(name="Sparrow", entity_type="project")
        item_id = _item(store)
        await _run_entities_stage(store, item_id, "The Sparrow release ships Friday.", None)
        assert _mention_ids(store, item_id) == {eid}

    @pytest.mark.asyncio
    async def test_prepass_links_survive_the_extractor_clear(self, store, monkeypatch):
        """`clear_item_entities` wipes this item's mentions before the extraction write.

        Without re-linking after that clear, every item WITH a model silently loses its
        deterministic links — the pre-pass would appear to work only in the no-model case,
        which is much harder to notice.
        """
        from personalclaw.knowledge.pipeline import runner

        store.add_entity(name="Sparrow", entity_type="project")
        item_id = _item(store)

        class _FakeExtractor:
            def __init__(self, pool=None):
                pass

            async def extract(self, content):
                # The model finds something DIFFERENT — the realistic case.
                return {"entities": [{"name": "Kestrel", "type": "project"}], "relations": []}

        monkeypatch.setattr("personalclaw.knowledge.extractor.EntityExtractor", _FakeExtractor)
        await runner._run_entities_stage(store, item_id, "Sparrow and something new.", object())

        # Asserted by NAME, not id. `clear_item_entities` deletes an entity that loses its
        # last mention, so the restored Sparrow is a NEW row with a new id — the link is what
        # must survive, not the identifier. Asserting the old id here would fail on correct
        # behavior and send the next reader hunting a phantom bug.
        assert _linked_names(store, item_id) == {"Sparrow", "Kestrel"}

    @pytest.mark.asyncio
    async def test_an_entity_found_by_both_yields_one_mention(self, store, monkeypatch):
        from personalclaw.knowledge.pipeline import runner

        store.add_entity(name="Sparrow", entity_type="project")
        item_id = _item(store)

        class _FakeExtractor:
            def __init__(self, pool=None):
                pass

            async def extract(self, content):
                return {"entities": [{"name": "Sparrow", "type": "project"}], "relations": []}

        monkeypatch.setattr("personalclaw.knowledge.extractor.EntityExtractor", _FakeExtractor)
        await runner._run_entities_stage(store, item_id, "Sparrow ships.", object())
        rows = store.db.execute(
            "SELECT COUNT(*) c FROM mentions WHERE item_id = ?", (item_id,)
        ).fetchone()
        assert rows["c"] == 1

    @pytest.mark.asyncio
    async def test_extraction_failure_leaves_the_prepass_links(self, store, monkeypatch):
        """A model error must not cost the deterministic links."""
        from personalclaw.knowledge.pipeline import runner

        eid = store.add_entity(name="Sparrow", entity_type="project")
        item_id = _item(store)

        class _Boom:
            def __init__(self, pool=None):
                pass

            async def extract(self, content):
                raise RuntimeError("model down")

        monkeypatch.setattr("personalclaw.knowledge.extractor.EntityExtractor", _Boom)
        await runner._run_entities_stage(store, item_id, "Sparrow ships.", object())
        assert _mention_ids(store, item_id) == {eid}

    @pytest.mark.asyncio
    async def test_empty_content_is_a_no_op(self, store):
        from personalclaw.knowledge.pipeline.runner import _run_entities_stage

        store.add_entity(name="Sparrow", entity_type="project")
        item_id = _item(store)
        await _run_entities_stage(store, item_id, "   ", None)
        assert _mention_ids(store, item_id) == set()

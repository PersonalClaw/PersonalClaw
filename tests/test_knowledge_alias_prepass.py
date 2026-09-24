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

import json

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


# ── The producer: where the aliases the index reads come FROM (#1779) ───────────
#
# The pre-pass, `find_entity`'s alias fallback, `memory_linker.seed_from_knowledge` and the
# STT lexicon's `rebuild_from_graph` all read `entities.aliases`. Nothing WROTE it: the column,
# `add_entity`'s `aliases=` parameter and the tests above all shipped in the initial public
# commit, and the only production caller of `add_entity` (the extraction writer) never passed
# it. So the pre-pass indexed canonical names only — measured on origin/main: a model that
# volunteered two aliases produced `'[]'`, the index held 1 surface form for 1 entity, a
# document naming only the alias linked 0 entities, `find_entity('SPRW')` returned None, and
# the lexicon boosted 0 alias surfaces. The whole "written as a declared alias rather than its
# canonical name" case this module exists for was unreachable outside these tests.


def _stored_aliases(store, name: str) -> list[str]:
    row = store.db.execute("SELECT aliases FROM entities WHERE name = ?", (name,)).fetchone()
    return json.loads(row["aliases"] or "[]") if row else []


def _extractor(entities, monkeypatch):
    """Bind a fake `EntityExtractor` returning *entities* verbatim."""

    class _Fake:
        def __init__(self, pool=None):
            pass

        async def extract(self, content):
            return {"entities": entities, "relations": []}

    monkeypatch.setattr("personalclaw.knowledge.extractor.EntityExtractor", _Fake)


class TestAliasProducer:
    """Extraction is the store's only entity writer, so it is the only place an alias can
    enter — and the model's alias list is a proposal, never taken on trust."""

    @pytest.mark.asyncio
    async def test_extraction_writes_the_aliases_the_chunk_itself_uses(self, store, monkeypatch):
        from personalclaw.knowledge.pipeline import runner

        text = "Sparrow ships Tuesday. Sparrow is written SPRW in the tracker."
        _extractor([{"name": "Sparrow", "type": "project", "aliases": ["SPRW"]}], monkeypatch)
        await runner._run_entities_stage(store, _item(store, text), text, object())
        assert _stored_aliases(store, "Sparrow") == ["SPRW"]

    @pytest.mark.asyncio
    async def test_an_alias_absent_from_the_text_is_refused(self, store, monkeypatch):
        """The guard that makes writing aliases safe at all.

        `find_entity` resolves BY alias, so an invented surface does not merely add noise —
        it silently folds the next distinct entity that happens to bear that name into this
        one. Only surfaces the document itself uses are kept.
        """
        from personalclaw.knowledge.pipeline import runner

        text = "Sparrow ships Tuesday."
        _extractor(
            [{"name": "Sparrow", "type": "project", "aliases": ["SPRW", "Project Falcon"]}],
            monkeypatch,
        )
        await runner._run_entities_stage(store, _item(store, text), text, object())
        assert _stored_aliases(store, "Sparrow") == []

    @pytest.mark.asyncio
    async def test_an_alias_only_beyond_the_model_s_own_window_is_refused(self, store, monkeypatch):
        """Grounded against the slice the model actually READ, not the whole document.

        `EntityExtractor` sends only the leading `MAX_EXTRACTION_CHARS`. A surface found past
        that boundary cannot have come from the model's reading of the item, so accepting it
        would let a long document's unrelated tail launder an invented alias.
        """
        from personalclaw.knowledge.extractor import MAX_EXTRACTION_CHARS
        from personalclaw.knowledge.pipeline import runner

        text = "Sparrow ships Tuesday. " + ("filler word " * 2000) + " tracked as SPRW."
        assert len(text) > MAX_EXTRACTION_CHARS, "the fixture must straddle the window"
        assert "SPRW" not in text[:MAX_EXTRACTION_CHARS]
        _extractor([{"name": "Sparrow", "type": "project", "aliases": ["SPRW"]}], monkeypatch)
        await runner._run_entities_stage(store, _item(store, text), text, object())
        assert _stored_aliases(store, "Sparrow") == []

    @pytest.mark.asyncio
    async def test_a_later_document_enriches_an_existing_entity(self, store, monkeypatch):
        """After the first ingest the entity ALWAYS exists, so a create-only alias write
        could never learn the spelling a later document introduces — the same reason
        `backfill_entity_description` exists."""
        from personalclaw.knowledge.pipeline import runner

        first = "Sparrow ships Tuesday."
        _extractor([{"name": "Sparrow", "type": "project"}], monkeypatch)
        await runner._run_entities_stage(store, _item(store, first), first, object())
        assert _stored_aliases(store, "Sparrow") == []

        second = "The SPRW cutover slipped. Sparrow is now Thursday."
        _extractor([{"name": "Sparrow", "type": "project", "aliases": ["SPRW"]}], monkeypatch)
        await runner._run_entities_stage(store, _item(store, second), second, object())
        assert _stored_aliases(store, "Sparrow") == ["SPRW"]

    @pytest.mark.asyncio
    async def test_established_aliases_survive_a_re_ingest(self, store, monkeypatch):
        """`clear_item_entities` deletes an entity that loses its last mention, so the
        restore path must carry the aliases too — otherwise a re-ingest silently erases the
        surface that made the link and the NEXT ingest matches nothing."""
        from personalclaw.knowledge.pipeline import runner

        text = "Sparrow ships Tuesday, tracked as SPRW."
        item_id = _item(store, text)
        _extractor([{"name": "Sparrow", "type": "project", "aliases": ["SPRW"]}], monkeypatch)
        await runner._run_entities_stage(store, item_id, text, object())

        # Re-ingest: the model now reports a DIFFERENT entity, so nothing re-creates Sparrow.
        _extractor([{"name": "Kestrel", "type": "project"}], monkeypatch)
        await runner._run_entities_stage(store, item_id, text, object())
        assert _stored_aliases(store, "Sparrow") == ["SPRW"]

    @pytest.mark.asyncio
    async def test_the_prepass_then_links_a_document_that_only_uses_the_alias(
        self, store, monkeypatch
    ):
        """The end-to-end point of the whole module, with no test-only seeding: one ingest
        establishes the alias, a second ingest WITH NO MODEL links through it."""
        from personalclaw.knowledge.pipeline import runner

        first = "Sparrow ships Tuesday. Sparrow is written SPRW in the tracker."
        _extractor([{"name": "Sparrow", "type": "project", "aliases": ["SPRW"]}], monkeypatch)
        await runner._run_entities_stage(store, _item(store, first), first, object())

        second = "The SPRW cutover slipped to Thursday."
        later = _item(store, second)
        await runner._run_entities_stage(store, later, second, None)
        assert _linked_names(store, later) == {"Sparrow"}

    @pytest.mark.asyncio
    async def test_find_entity_then_resolves_the_variant_instead_of_minting_a_second_row(
        self, store, monkeypatch
    ):
        """Duplicate PREVENTION is the other half of what the column buys: a later document
        whose canonical name is the alias resolves to the entity that already exists."""
        from personalclaw.knowledge.pipeline import runner

        first = "Sparrow ships Tuesday. Sparrow is written SPRW in the tracker."
        _extractor([{"name": "Sparrow", "type": "project", "aliases": ["SPRW"]}], monkeypatch)
        await runner._run_entities_stage(store, _item(store, first), first, object())

        second = "SPRW slipped to Thursday."
        _extractor([{"name": "SPRW", "type": "project"}], monkeypatch)
        await runner._run_entities_stage(store, _item(store, second), second, object())
        names = [r["name"] for r in store.db.execute("SELECT name FROM entities")]
        assert names == ["Sparrow"]


class TestAliasSurfaceHygiene:
    """`_clean_alias_surfaces` is the one funnel every write path goes through."""

    def test_the_name_is_not_stored_as_its_own_alias(self, store):
        store.add_entity(name="Sparrow", entity_type="project", aliases=["sparrow", "SPRW"])
        assert _stored_aliases(store, "Sparrow") == ["SPRW"]

    def test_case_insensitive_duplicates_collapse(self, store):
        store.add_entity(name="Sparrow", entity_type="project", aliases=["SPRW", "sprw", " SPRW "])
        assert _stored_aliases(store, "Sparrow") == ["SPRW"]

    def test_the_set_is_capped(self, store):
        from personalclaw.knowledge.store import MAX_ENTITY_ALIASES

        store.add_entity(
            name="Sparrow",
            entity_type="project",
            aliases=[f"S{i:02d}" for i in range(MAX_ENTITY_ALIASES + 5)],
        )
        assert len(_stored_aliases(store, "Sparrow")) == MAX_ENTITY_ALIASES

    def test_non_string_entries_are_dropped_not_stringified(self, store):
        store.add_entity(name="Sparrow", entity_type="project", aliases=[{"a": 1}, ["x"], "SPRW"])
        assert _stored_aliases(store, "Sparrow") == ["SPRW"]

    def test_merge_never_removes_an_established_surface(self, store):
        eid = store.add_entity(name="Sparrow", entity_type="project", aliases=["SPRW"])
        assert store.merge_entity_aliases(eid, ["@sparrow"]) == 1
        assert _stored_aliases(store, "Sparrow") == ["SPRW", "@sparrow"]
        assert store.merge_entity_aliases(eid, ["SPRW"]) == 0, "already present — no write"
        assert store.merge_entity_aliases(eid, []) == 0

    def test_merge_on_a_missing_entity_is_a_no_op(self, store):
        assert store.merge_entity_aliases("nope", ["SPRW"]) == 0

    def test_a_full_alias_set_is_not_truncated_by_a_later_merge(self, store):
        """The cap must never become a way to LOSE an established surface."""
        from personalclaw.knowledge.store import MAX_ENTITY_ALIASES

        full = [f"S{i:02d}" for i in range(MAX_ENTITY_ALIASES)]
        eid = store.add_entity(name="Sparrow", entity_type="project", aliases=full)
        assert store.merge_entity_aliases(eid, ["SPRW"]) == 0
        assert _stored_aliases(store, "Sparrow") == full

    def test_an_over_cap_row_is_not_silently_trimmed(self, store):
        """Reachable whenever `MAX_ENTITY_ALIASES` is LOWERED, or a backup taken under a
        higher one is restored. Dropping surfaces the user already had because a constant
        changed is data loss, so a merge that cannot add anything writes nothing at all."""
        from personalclaw.knowledge.store import MAX_ENTITY_ALIASES

        eid = store.add_entity(name="Sparrow", entity_type="project")
        over = [f"S{i:02d}" for i in range(MAX_ENTITY_ALIASES + 3)]
        store.db.execute("UPDATE entities SET aliases = ? WHERE id = ?", (json.dumps(over), eid))
        store.db.commit()
        assert store.merge_entity_aliases(eid, ["SPRW"]) == 0
        assert _stored_aliases(store, "Sparrow") == over


#: Probe value + the assertion for each OPTIONAL `add_entity` write field. Keyed by the
#: parameter name; the rail below derives the field list from the signature and asserts this
#: mapping covers it exactly, so adding a column with a write parameter that the extraction
#: writer silently drops (which is precisely how the alias column stayed empty from v0.1.0)
#: fails here until someone either proves it is forwarded or names it in `_NOT_FROM_EXTRACTION`.
_WRITE_FIELD_PROBES: dict = {
    "description": (
        "the cutover programme",
        lambda row: row["description"] == "the cutover programme",
    ),
    # The probe surface must appear in the item text — see `_grounded_aliases`.
    "aliases": (["SPRW"], lambda row: json.loads(row["aliases"] or "[]") == ["SPRW"]),
}

#: Write fields extraction legitimately cannot supply → parameter name: why.
_NOT_FROM_EXTRACTION: dict[str, str] = {}


@pytest.mark.asyncio
async def test_every_add_entity_write_field_is_forwarded_by_the_extraction_writer(
    store, monkeypatch
):
    """The rail. Extraction is the ONLY production caller of `add_entity`, so any write
    field it does not forward is inert by construction — a column with four readers and no
    writer. The field list is DERIVED from the signature, never restated here."""
    import inspect

    from personalclaw.knowledge.pipeline import runner
    from personalclaw.knowledge.store import KnowledgeStore

    fields = [
        p
        for p in inspect.signature(KnowledgeStore.add_entity).parameters
        if p not in {"self", "name", "entity_type"}
    ]
    assert fields, "add_entity has no optional write fields — this rail guards nothing"
    assert set(fields) == set(_WRITE_FIELD_PROBES) | set(_NOT_FROM_EXTRACTION), (
        "add_entity's write fields changed: give the new one a probe (proving the extraction "
        f"writer forwards it) or name it in _NOT_FROM_EXTRACTION with a reason. Fields: {fields}"
    )

    entity = {"name": "Sparrow", "type": "project"}
    for field, (probe, _) in _WRITE_FIELD_PROBES.items():
        entity[field] = probe
    text = "Sparrow ships Tuesday. Sparrow is written SPRW in the tracker."
    _extractor([entity], monkeypatch)
    await runner._run_entities_stage(store, _item(store, text), text, object())

    row = store.db.execute("SELECT * FROM entities WHERE name = 'Sparrow'").fetchone()
    assert row is not None, "the extraction writer created no entity at all"
    for field, (_, holds) in _WRITE_FIELD_PROBES.items():
        assert holds(row), f"{field!r} was not forwarded from the extraction to the stored row"


def test_the_extraction_prompt_asks_for_every_field_the_writer_forwards():
    """The other half of the rail — and the half that decides whether this works in
    PRODUCTION rather than only under an injected payload.

    The writer can only forward what the model was asked for. A prompt edit that drops a
    field from the entity shape would re-create exactly the #1779 shape: every test above
    still passes (they inject the extraction dict directly) while the real pipeline goes back
    to writing nothing. Fields come from `_WRITE_FIELD_PROBES`, so the two halves cannot
    disagree, and the assertion is against the entity SHAPE line specifically — prose
    elsewhere in the prompt that merely mentions the word must not satisfy it.
    """
    import yaml

    from personalclaw.providers.loader import BUNDLED_DIR

    path = BUNDLED_DIR / "native-knowledge" / "prompts" / "knowledge-extraction.yaml"
    content = yaml.safe_load(path.read_text())["content"]
    shape = next(
        (ln for ln in content.splitlines() if ln.strip().startswith("- entities: list of")), ""
    )
    assert shape, "the extraction prompt no longer declares an entities shape"
    for field in _WRITE_FIELD_PROBES:
        assert f'"{field}"' in shape, (
            f"the extraction prompt's entity shape does not ask for {field!r}, so the model "
            f"never returns it and the writer forwards nothing: {shape!r}"
        )

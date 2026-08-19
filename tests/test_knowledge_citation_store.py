"""Persisted per-marker citations (WF2KNO-11) -- the `item_citations` rows.

Two things are pinned here that a naive implementation gets wrong. First, a re-synthesis must
REPLACE the citation set: appending leaves the previous generation's marker 2 next to the new
one, so `[2]` resolves to two sources and the stale one reads as equally attributed. Second,
the table must appear on an EXISTING knowledge.db, because every library predates it -- the
store's own `CREATE TABLE IF NOT EXISTS` ladder in `_init_schema` is what makes that true, and
it runs on every open.
"""

from __future__ import annotations

import sqlite3

import pytest

from personalclaw.knowledge.citations import Citation, register_sources, resolve
from personalclaw.knowledge.store import KnowledgeStore


@pytest.fixture
def store(tmp_path):
    return KnowledgeStore(tmp_path / "k.db")


def _item(store, title: str) -> str:
    return store.create_typed_item(item_type="note", title=title, content="body")


# ── the schema arrives on an existing database ────────────────────────────────


def test_a_pre_citations_db_gains_the_table_on_open(tmp_path):
    """Opening the store must add the table to a library written before it existed. There is
    no schema-version counter in knowledge.db -- `_init_schema`'s IF NOT EXISTS ladder, run
    from __init__ on every open, IS the migration.

    The pre-citations library is built by dropping the table back out of a real store rather
    than by hand-rolling a partial `items` table: a hand-rolled one diverges from the real
    schema in ways that make this test fail for reasons having nothing to do with citations.
    """
    dbp = tmp_path / "old.db"
    seeded = KnowledgeStore(dbp)
    kept = _item(seeded, "kept")
    seeded.db.execute("DROP INDEX idx_item_citations_source")
    seeded.db.execute("DROP TABLE item_citations")
    seeded.db.commit()
    assert not _tables(seeded.db) & {"item_citations"}, "the fixture must start without it"
    seeded.db.close()

    store = KnowledgeStore(dbp)
    assert "item_citations" in _tables(store.db)
    assert store.get_item(kept)["title"] == "kept"
    # And it is usable immediately, not merely present.
    store.set_item_citations(kept, [Citation(marker=1, item_id="a")])
    assert [r["marker"] for r in store.item_citations(kept)] == [1]


def _tables(db: sqlite3.Connection) -> set[str]:
    return {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def test_the_source_reverse_index_exists(tmp_path):
    """ "What else cites this item" has no covering index from the primary key."""
    store = KnowledgeStore(tmp_path / "k.db")
    names = {r[0] for r in store.db.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    assert "idx_item_citations_source" in names


# ── replace, never append ─────────────────────────────────────────────────────


def test_a_resynthesis_replaces_rather_than_accumulating_stale_markers(store):
    """The claim this table exists to keep honest. After a re-synthesis, marker 2 must name
    exactly one source -- the new one."""
    citing = _item(store, "synthesis")
    store.set_item_citations(
        citing,
        [
            Citation(marker=1, item_id="old-a"),
            Citation(marker=2, item_id="old-b"),
            Citation(marker=3, item_id="old-c"),
        ],
    )
    written = store.set_item_citations(
        citing, [Citation(marker=1, item_id="new-a"), Citation(marker=2, item_id="new-b")]
    )

    assert written == 2
    rows = store.item_citations(citing)
    assert [r["marker"] for r in rows] == [1, 2], "marker 3 from the old set must be gone"
    assert [r["source_item_id"] for r in rows] == ["new-a", "new-b"]


def test_replacing_with_an_empty_set_clears_the_item(store):
    citing = _item(store, "synthesis")
    store.set_item_citations(citing, [Citation(marker=1, item_id="a")])
    assert store.set_item_citations(citing, []) == 0
    assert store.item_citations(citing) == []


def test_replacing_one_item_leaves_another_items_citations_alone(store):
    first = _item(store, "first")
    second = _item(store, "second")
    store.set_item_citations(first, [Citation(marker=1, item_id="a")])
    store.set_item_citations(second, [Citation(marker=1, item_id="b")])
    store.set_item_citations(first, [Citation(marker=1, item_id="a2")])
    assert [r["source_item_id"] for r in store.item_citations(second)] == ["b"]


# ── read shape ────────────────────────────────────────────────────────────────


def test_rows_come_back_ascending_by_marker_whatever_order_they_were_written(store):
    citing = _item(store, "synthesis")
    store.set_item_citations(
        citing,
        [
            Citation(marker=12, item_id="l"),
            Citation(marker=2, item_id="b"),
            Citation(marker=1, item_id="a"),
        ],
    )
    assert [r["marker"] for r in store.item_citations(citing)] == [1, 2, 12]


def test_each_row_carries_exactly_the_documented_keys(store):
    citing = _item(store, "synthesis")
    store.set_item_citations(
        citing, [Citation(marker=1, item_id="a", chunk_index=3, excerpt="the passage")]
    )
    (row,) = store.item_citations(citing)
    assert row == {
        "marker": 1,
        "source_item_id": "a",
        "chunk_index": 3,
        "excerpt": "the passage",
    }


def test_an_unknown_item_has_no_citations_rather_than_raising(store):
    assert store.item_citations("never-written") == []


# ── the dict boundary (the store must not import citations.py) ────────────────


def test_dicts_are_accepted_naming_the_source_either_way(store):
    """The boundary is plain dicts so the schema and the marker parser stay unaware of each
    other. A dict may name the cited source `source_item_id` (the column) or `item_id` (the
    Citation field)."""
    citing = _item(store, "synthesis")
    store.set_item_citations(
        citing,
        [
            {"marker": 1, "source_item_id": "column-name", "chunk_index": 2},
            {"marker": 2, "item_id": "dataclass-name"},
        ],
    )
    rows = store.item_citations(citing)
    assert [(r["marker"], r["source_item_id"]) for r in rows] == [
        (1, "column-name"),
        (2, "dataclass-name"),
    ]
    assert rows[1]["chunk_index"] == -1, "an unstated chunk means the whole item"


def test_chunk_zero_persists_as_zero_not_as_whole_item(store):
    """`raw or -1` would silently relabel every first chunk as "the whole item"."""
    citing = _item(store, "synthesis")
    store.set_item_citations(citing, [{"marker": 1, "item_id": "a", "chunk_index": 0}])
    assert store.item_citations(citing)[0]["chunk_index"] == 0


def test_a_repeated_marker_collapses_instead_of_violating_the_primary_key(store):
    """The caller's list comes from prose, where a model can restate a marker."""
    citing = _item(store, "synthesis")
    written = store.set_item_citations(
        citing, [Citation(marker=1, item_id="first"), Citation(marker=1, item_id="second")]
    )
    assert written == 1
    assert [r["source_item_id"] for r in store.item_citations(citing)] == ["second"]


def test_a_citation_to_a_deleted_source_stays_readable(store):
    """No REFERENCES on source_item_id on purpose: with foreign_keys=ON, an FK here would
    either refuse the source's deletion or erase the evidence that the claim was attributed."""
    citing = _item(store, "synthesis")
    source = _item(store, "source")
    store.set_item_citations(citing, [Citation(marker=1, item_id=source)])
    store.delete_item(source)
    assert [r["source_item_id"] for r in store.item_citations(citing)] == [source]


# ── end to end: prose in, rows out ────────────────────────────────────────────


def test_resolved_prose_persists_one_row_per_marker(store):
    citing = _item(store, "synthesis")
    sources = register_sources(
        [
            {"item_id": "alpha", "content": "first source"},
            {"item_id": "beta", "content": "second source"},
        ]
    )
    res = resolve("Beta says so [2]; alpha agrees [1]; nobody said this [8].", sources)
    written = store.set_item_citations(citing, res.citations)

    assert written == 2
    assert res.dropped == (8,)
    rows = store.item_citations(citing)
    assert [(r["marker"], r["source_item_id"], r["excerpt"]) for r in rows] == [
        (1, "alpha", "first source"),
        (2, "beta", "second source"),
    ]

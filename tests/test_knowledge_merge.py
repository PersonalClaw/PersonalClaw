"""Near-duplicate surfacing + merge (KNOWLEDGE-LIBRARY T3.2).

A merge DELETES one of two items, so the tests are mostly about what must NOT be lost:

* **Curation.** The survivor inherits both items' collections, tags and mentions. A merge
  that dropped the losing copy's shelf membership would quietly undo work the user did.
* **The stronger signal.** Merging a `read`+favorited copy into an `unread` one must not
  demote it — the user's engagement is the thing worth keeping.
* **The search index.** `items_fts` is an EXTERNAL-CONTENT table: a plain `DELETE` is a
  silent no-op and a mismatched `'delete'` corrupts the posting list without raising. The
  merge reuses `_delete_item_cascade`, which owns that contract.
* **Itself.** A self-merge would run the cascade delete on the survivor.
"""

from __future__ import annotations

import pytest


@pytest.fixture()
def store(tmp_path):
    from personalclaw.knowledge.store import KnowledgeStore

    return KnowledgeStore(tmp_path / "k.db")


def _item(store, title="T", content="body text") -> str:
    return store.create_typed_item(item_type="note", title=title, content=content)


def _collections_of(store, item_id) -> set[str]:
    rows = store.db.execute(
        "SELECT collection_id FROM collection_items WHERE item_id = ?", (item_id,)
    ).fetchall()
    return {r["collection_id"] for r in rows}


def _tag_ids_of(store, item_id) -> set[int]:
    rows = store.db.execute("SELECT tag_id FROM item_tags WHERE item_id = ?", (item_id,)).fetchall()
    return {r["tag_id"] for r in rows}


def _mention_entities(store, item_id) -> set[str]:
    rows = store.db.execute(
        "SELECT entity_id FROM mentions WHERE item_id = ?", (item_id,)
    ).fetchall()
    return {r["entity_id"] for r in rows}


# ── Guard rails ─────────────────────────────────────────────────────────


def test_self_merge_is_refused(store):
    """It would run the cascade delete on the survivor — destroying what it kept."""
    a = _item(store)
    with pytest.raises(ValueError):
        store.merge_items(a, a)
    assert store.get_item(a) is not None


@pytest.mark.parametrize("bad", [("", "x"), ("x", ""), ("", "")])
def test_missing_ids_are_refused(store, bad):
    with pytest.raises(ValueError):
        store.merge_items(*bad)


def test_unknown_item_is_refused(store):
    a = _item(store)
    with pytest.raises(ValueError, match="no such item"):
        store.merge_items(a, "nope")
    with pytest.raises(ValueError, match="no such item"):
        store.merge_items("nope", a)
    assert store.get_item(a) is not None


# ── What the survivor inherits ──────────────────────────────────────────


def test_the_loser_is_deleted_and_the_survivor_kept(store):
    keep, merge = _item(store, "Keep"), _item(store, "Merge")
    store.merge_items(keep, merge)
    assert store.get_item(keep) is not None
    assert store.get_item(merge) is None


def test_survivor_inherits_collection_memberships(store):
    keep, merge = _item(store, "Keep"), _item(store, "Merge")
    shelf = store.create_collection(name="Reading")
    other = store.create_collection(name="Archive")
    store.add_to_collection(shelf, keep)
    store.add_to_collection(other, merge)
    moved = store.merge_items(keep, merge)
    assert _collections_of(store, keep) == {shelf, other}
    assert moved["collections"] == 1


def test_a_shared_collection_does_not_duplicate(store):
    """Both on one shelf ⇒ one row; the composite PK would reject a blind redirect."""
    keep, merge = _item(store, "Keep"), _item(store, "Merge")
    shelf = store.create_collection(name="Reading")
    store.add_to_collection(shelf, keep)
    store.add_to_collection(shelf, merge)
    store.merge_items(keep, merge)
    rows = store.db.execute(
        "SELECT COUNT(*) c FROM collection_items WHERE item_id = ?", (keep,)
    ).fetchone()
    assert rows["c"] == 1


def test_survivor_inherits_tags(store):
    keep = store.create_typed_item(item_type="note", title="Keep", content="x", tags=["rust"])
    merge = store.create_typed_item(item_type="note", title="Merge", content="y", tags=["async"])
    store.merge_items(keep, merge)
    assert len(_tag_ids_of(store, keep)) == 2


def test_a_shared_tag_does_not_duplicate(store):
    keep = store.create_typed_item(item_type="note", title="Keep", content="x", tags=["rust"])
    merge = store.create_typed_item(item_type="note", title="Merge", content="y", tags=["rust"])
    store.merge_items(keep, merge)
    assert len(_tag_ids_of(store, keep)) == 1


def test_survivor_inherits_entity_mentions(store):
    keep, merge = _item(store, "Keep"), _item(store, "Merge")
    e1 = store.add_entity(name="Sparrow", entity_type="project")
    e2 = store.add_entity(name="Kestrel", entity_type="project")
    store.add_mention(keep, e1)
    store.add_mention(merge, e2)
    moved = store.merge_items(keep, merge)
    assert _mention_entities(store, keep) == {e1, e2}
    assert moved["mentions"] == 1


def test_a_shared_mention_does_not_duplicate(store):
    keep, merge = _item(store, "Keep"), _item(store, "Merge")
    eid = store.add_entity(name="Sparrow", entity_type="project")
    store.add_mention(keep, eid)
    store.add_mention(merge, eid)
    store.merge_items(keep, merge)
    rows = store.db.execute("SELECT COUNT(*) c FROM mentions WHERE item_id = ?", (keep,)).fetchone()
    assert rows["c"] == 1


def test_relations_discovered_from_the_loser_are_reattributed(store):
    """Otherwise the edge's provenance dangles at a deleted item."""
    keep, merge = _item(store, "Keep"), _item(store, "Merge")
    a = store.add_entity(name="Sparrow", entity_type="project")
    b = store.add_entity(name="Kestrel", entity_type="project")
    store.add_entity_relation(source_id=a, target_id=b, relation_type="uses", source_item_id=merge)
    store.merge_items(keep, merge)
    rows = store.db.execute("SELECT source_item_id FROM entity_relations").fetchall()
    assert [r["source_item_id"] for r in rows] == [keep]


# ── The stronger signal wins ────────────────────────────────────────────


@pytest.mark.parametrize(
    "keep_state,merge_state,expected",
    [
        ("unread", "read", "read"),
        ("read", "unread", "read"),
        ("unread", "reading", "reading"),
        ("reading", "read", "read"),
        ("read", "reading", "read"),
        ("unread", "unread", "unread"),
    ],
)
def test_the_stronger_read_state_survives(store, keep_state, merge_state, expected):
    """A merge must never demote something you'd already read."""
    keep, merge = _item(store, "Keep"), _item(store, "Merge")
    store.set_read_state(keep, keep_state)
    store.set_read_state(merge, merge_state)
    store.merge_items(keep, merge)
    assert store.get_item(keep)["read_state"] == expected


@pytest.mark.parametrize(
    "keep_fav,merge_fav,expected", [(0, 1, 1), (1, 0, 1), (1, 1, 1), (0, 0, 0)]
)
def test_favorited_survives_from_either_copy(store, keep_fav, merge_fav, expected):
    keep, merge = _item(store, "Keep"), _item(store, "Merge")
    store.update_item(keep, favorited=keep_fav, touch=False)
    store.update_item(merge, favorited=merge_fav, touch=False)
    store.db.commit()
    store.merge_items(keep, merge)
    assert (store.get_item(keep)["favorited"] or 0) == expected


# ── The search index ────────────────────────────────────────────────────


def test_search_no_longer_finds_the_merged_item(store):
    """`items_fts` is external-content: a plain DELETE is a silent no-op.

    If the merge didn't route through `_delete_item_cascade`, the loser's title would stay
    findable forever — search returning a row that no longer exists.
    """
    keep = _item(store, "Keep", "shared body")
    merge = _item(store, "Zzyzxdistinct", "unique marker phrase")
    assert any(r["id"] == merge for r in store.search_items_fts("Zzyzxdistinct"))
    store.merge_items(keep, merge)
    assert not any(r["id"] == merge for r in store.search_items_fts("Zzyzxdistinct"))


def test_search_still_finds_the_survivor(store):
    keep = _item(store, "Keepdistinctive", "body")
    merge = _item(store, "Other", "body")
    store.merge_items(keep, merge)
    assert any(r["id"] == keep for r in store.search_items_fts("Keepdistinctive"))


def test_a_failed_merge_leaves_both_items(store, monkeypatch):
    """The whole merge is one transaction — a partial merge is a corrupted library."""
    keep, merge = _item(store, "Keep"), _item(store, "Merge")
    monkeypatch.setattr(
        store,
        "_delete_item_cascade",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    with pytest.raises(RuntimeError):
        store.merge_items(keep, merge)
    assert store.get_item(keep) is not None
    assert store.get_item(merge) is not None, "the loser must survive a rolled-back merge"


# ── Duplicate surfacing ─────────────────────────────────────────────────


def test_no_duplicates_without_an_embedding(store):
    """An un-embedded item cannot be scored; guessing from titles proposes destruction."""
    a = _item(store, "Same Title")
    _item(store, "Same Title")
    assert store.find_duplicates(a) == []


def test_duplicates_of_an_unknown_item_is_empty(store):
    assert store.find_duplicates("nope") == []


def test_duplicates_never_return_the_embedding(store):
    """Megabytes of floats no caller needs, on a list endpoint."""
    import struct

    a = _item(store, "Doc A")
    vec = struct.pack("<3f", 1.0, 0.0, 0.0)
    store.db.execute("UPDATE items SET embedding = ? WHERE id = ?", (vec, a))
    store.db.commit()
    for row in store.find_duplicates(a):
        assert "embedding" not in row

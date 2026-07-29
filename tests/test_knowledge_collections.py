"""Knowledge collections + item curation (KNOWLEDGE-LIBRARY S1, C2).

Two shelf kinds with genuinely different semantics: a MANUAL collection holds an
explicit membership list, a SMART one stores a query re-run on every read. The
live-ness of the smart kind is the point — it stays current as items arrive, with no
backfill — and it is also why a smart shelf must refuse membership writes rather than
accept rows it will never read.

The migration half matters as much: this adds two item columns and two tables to an
EXISTING knowledge.db, using the store's own additive ladder (there is no `lifecycle/`
package — the deferred-governance premise in the plan is stale; same ruling as
Memory-Graph S1's v7).
"""

from __future__ import annotations

import sqlite3

import pytest

from personalclaw.knowledge.store import KnowledgeStore


@pytest.fixture
def store(tmp_path):
    return KnowledgeStore(tmp_path / "k.db")


def _item(store, title: str, content: str = "body") -> str:
    return store.create_typed_item(item_type="note", title=title, content=content)


# ── migration: an existing DB must upgrade in place ───────────────────────────


def test_a_pre_collections_db_upgrades_in_place_without_losing_items(tmp_path):
    """The upgrade path. A user's existing library predates these columns and tables;
    opening the store must add them and leave every item intact."""
    dbp = tmp_path / "old.db"
    con = sqlite3.connect(dbp)
    con.executescript("""
        CREATE TABLE items (id TEXT PRIMARY KEY, title TEXT NOT NULL, content TEXT NOT NULL,
          item_type TEXT NOT NULL, summary TEXT, tags TEXT DEFAULT '[]', embedding BLOB,
          status TEXT DEFAULT 'active', created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        INSERT INTO items VALUES
          ('i1','Old note','body','note',NULL,'[]',NULL,'active','2026-01-01','2026-01-01');
        """)
    con.commit()
    con.close()

    st = KnowledgeStore(dbp)
    cols = {r[1] for r in st.db.execute("PRAGMA table_info(items)").fetchall()}
    assert {"read_state", "favorited"} <= cols
    tables = {
        r[0] for r in st.db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert {"collections", "collection_items"} <= tables
    item = st.get_item("i1")
    assert item is not None and item["title"] == "Old note"
    # A pre-existing row has NULL read_state on disk; the API contract is the enum.
    assert item["read_state"] == "unread"
    assert item["favorited"] is False


def test_opening_twice_is_idempotent(tmp_path):
    dbp = tmp_path / "k.db"
    first = KnowledgeStore(dbp)
    iid = _item(first, "One")
    second = KnowledgeStore(dbp)
    assert second.get_item(iid) is not None


# ── manual collections ────────────────────────────────────────────────────────


def test_create_and_resolve_a_manual_shelf(store):
    iid = _item(store, "Pandoc guide")
    cid = store.create_collection(name="Reading")
    assert store.add_to_collection(cid, iid) is True
    assert [i["title"] for i in store.resolve_collection(cid)] == ["Pandoc guide"]


def test_shelving_twice_is_a_no_op_not_a_duplicate(store):
    iid = _item(store, "One")
    cid = store.create_collection(name="Shelf")
    store.add_to_collection(cid, iid)
    store.add_to_collection(cid, iid)
    assert len(store.resolve_collection(cid)) == 1


def test_shelving_a_missing_item_is_refused(store):
    """A membership row pointing at a nonexistent item would render as a phantom entry."""
    cid = store.create_collection(name="Shelf")
    assert store.add_to_collection(cid, "no-such-item") is False
    assert store.resolve_collection(cid) == []


def test_shelving_onto_a_missing_shelf_is_refused(store):
    iid = _item(store, "One")
    assert store.add_to_collection("no-such-shelf", iid) is False


def test_one_item_can_sit_on_many_shelves(store):
    iid = _item(store, "Shared")
    a = store.create_collection(name="A")
    b = store.create_collection(name="B")
    store.add_to_collection(a, iid)
    store.add_to_collection(b, iid)
    assert {c["name"] for c in store.collections_for_item(iid)} == {"A", "B"}


def test_unshelving_removes_only_the_membership(store):
    iid = _item(store, "One")
    cid = store.create_collection(name="Shelf")
    store.add_to_collection(cid, iid)
    assert store.remove_from_collection(cid, iid) is True
    assert store.resolve_collection(cid) == []
    assert store.get_item(iid) is not None, "the item itself must survive"


def test_deleting_a_shelf_keeps_its_items(store):
    """A shelf is a view onto the library, not a container that owns its contents."""
    iid = _item(store, "One")
    cid = store.create_collection(name="Shelf")
    store.add_to_collection(cid, iid)
    assert store.delete_collection(cid) is True
    assert store.get_item(iid) is not None
    assert store.collections_for_item(iid) == []


def test_deleting_a_missing_shelf_reports_false(store):
    assert store.delete_collection("nope") is False


def test_archived_items_do_not_appear_on_a_shelf(store):
    """An archive is the user saying "not in my active library"; a shelf is an
    active-library view."""
    iid = _item(store, "Old")
    cid = store.create_collection(name="Shelf")
    store.add_to_collection(cid, iid)
    store.update_item(iid, is_archived=1)
    assert store.resolve_collection(cid) == []


# ── smart collections ─────────────────────────────────────────────────────────


def test_a_smart_shelf_resolves_its_query(store):
    _item(store, "Pandoc guide", "converting markdown to pdf")
    _item(store, "Coffee notes", "the grinder settings")
    cid = store.create_collection(name="PDF stuff", kind="smart", query="markdown pdf")
    titles = [i["title"] for i in store.resolve_collection(cid)]
    assert "Pandoc guide" in titles


def test_a_smart_shelf_picks_up_new_items_with_no_backfill(store):
    """The live-ness that justifies the kind existing at all."""
    cid = store.create_collection(name="Rust", kind="smart", query="borrow checker")
    assert store.resolve_collection(cid) == []
    _item(store, "Rust ownership", "the borrow checker explained")
    assert [i["title"] for i in store.resolve_collection(cid)] == ["Rust ownership"]


def test_a_smart_shelf_requires_a_query(store):
    """Without one it would match nothing forever — a shelf that looks broken."""
    with pytest.raises(ValueError, match="requires a query"):
        store.create_collection(name="Empty", kind="smart")


def test_switching_a_shelf_to_smart_without_a_query_is_refused(store):
    cid = store.create_collection(name="Manual")
    with pytest.raises(ValueError, match="requires a query"):
        store.update_collection(cid, kind="smart")


def test_switching_to_smart_with_a_query_in_the_same_call_is_allowed(store):
    cid = store.create_collection(name="Manual")
    assert store.update_collection(cid, kind="smart", query="anything") is True
    assert store.get_collection(cid)["kind"] == "smart"


def test_an_unknown_kind_is_refused(store):
    with pytest.raises(ValueError, match="unknown collection kind"):
        store.create_collection(name="X", kind="magic")


def test_a_smart_and_a_manual_shelf_hand_back_the_same_shape(store):
    """The UI renders one row component for both, so a retrieval projection leaking
    through would break the smart view."""
    iid = _item(store, "Pandoc guide", "markdown to pdf")
    manual = store.create_collection(name="M")
    store.add_to_collection(manual, iid)
    smart = store.create_collection(name="S", kind="smart", query="markdown pdf")
    m = store.resolve_collection(manual)[0]
    s = next(i for i in store.resolve_collection(smart) if i["id"] == iid)
    assert set(m) == set(s)


# ── the rail ──────────────────────────────────────────────────────────────────


def test_new_shelves_go_to_the_end_of_the_rail(store):
    """The user's ordering is theirs; a create must not reshuffle it."""
    a = store.create_collection(name="First")
    b = store.create_collection(name="Second")
    order = [c["id"] for c in store.list_collections()]
    assert order == [a, b]


def test_reordering_is_persisted(store):
    a = store.create_collection(name="First")
    b = store.create_collection(name="Second")
    store.update_collection(a, position=5)
    assert [c["id"] for c in store.list_collections()] == [b, a]


def test_a_manual_shelf_reports_its_count_and_a_smart_one_reports_unknown(store):
    """A per-shelf search on every rail render would be a real cost; smart counts are
    deliberately deferred to the point the user opens the shelf."""
    iid = _item(store, "One")
    m = store.create_collection(name="M")
    store.add_to_collection(m, iid)
    store.create_collection(name="S", kind="smart", query="one")
    by_name = {c["name"]: c for c in store.list_collections()}
    assert by_name["M"]["item_count"] == 1
    assert by_name["S"]["item_count"] is None


def test_renaming_a_shelf(store):
    cid = store.create_collection(name="Old name")
    assert store.update_collection(cid, name="New name") is True
    assert store.get_collection(cid)["name"] == "New name"


def test_updating_with_no_recognized_field_reports_false(store):
    cid = store.create_collection(name="X")
    assert store.update_collection(cid, nonsense="y") is False


def test_a_nameless_shelf_is_refused(store):
    with pytest.raises(ValueError, match="name is required"):
        store.create_collection(name="   ")


# ── item curation ─────────────────────────────────────────────────────────────


def test_read_state_cycles_through_all_three(store):
    iid = _item(store, "One")
    assert store.get_item(iid)["read_state"] == "unread"
    for state in ("reading", "read", "unread"):
        assert store.set_read_state(iid, state) is True
        assert store.get_item(iid)["read_state"] == state


def test_an_unknown_read_state_is_refused(store):
    iid = _item(store, "One")
    with pytest.raises(ValueError, match="unknown read state"):
        store.set_read_state(iid, "skimmed")


def test_marking_read_does_not_bump_updated_at(store):
    """Marking something read is not editing it. If it touched updated_at, reading
    through a backlog would silently reorder a recency-sorted library."""
    iid = _item(store, "One")
    before = store.get_item(iid)["updated_at"]
    store.set_read_state(iid, "read")
    assert store.get_item(iid)["updated_at"] == before


def test_favoriting_round_trips_as_a_bool(store):
    iid = _item(store, "One")
    assert store.get_item(iid)["favorited"] is False
    store.set_favorited(iid, True)
    assert store.get_item(iid)["favorited"] is True
    store.set_favorited(iid, False)
    assert store.get_item(iid)["favorited"] is False


def test_favoriting_does_not_bump_updated_at(store):
    iid = _item(store, "One")
    before = store.get_item(iid)["updated_at"]
    store.set_favorited(iid, True)
    assert store.get_item(iid)["updated_at"] == before


def test_curating_a_missing_item_reports_false(store):
    assert store.set_read_state("nope", "read") is False
    assert store.set_favorited("nope", True) is False


# ── bulk curation (S2, T2.3) ──────────────────────────────────────────────────


def test_bulk_reports_changed_unchanged_and_missing_separately(store):
    """The whole reason the endpoint returns per-item results.

    A selection can go stale between the click and the request, and "already read" is
    not a failure — collapsing all three into one ok/error would make "38 of 40" and
    "everything broke" look identical to the UI.
    """
    a, b = _item(store, "A"), _item(store, "B")
    store.set_read_state(b, "read")  # already in the target state

    res = store.bulk_apply("read_state", [a, b, "ghost"], state="read")

    assert res["changed"] == [a]
    assert res["unchanged"] == [b]
    assert res["missing"] == ["ghost"]


def test_bulk_read_state_actually_persists(store):
    a, b = _item(store, "A"), _item(store, "B")
    store.bulk_apply("read_state", [a, b], state="reading")
    assert store.get_item(a)["read_state"] == "reading"
    assert store.get_item(b)["read_state"] == "reading"


def test_bulk_rejects_an_invalid_read_state_before_touching_anything(store):
    """A bad arg must be a typed refusal, not a silent no-op over the whole selection."""
    a = _item(store, "A")
    with pytest.raises(ValueError, match="read_state requires state"):
        store.bulk_apply("read_state", [a], state="skimmed")
    assert store.get_item(a)["read_state"] == "unread"


def test_bulk_favorite_and_unfavorite(store):
    a = _item(store, "A")
    assert store.bulk_apply("favorite", [a], value=True)["changed"] == [a]
    assert store.get_item(a)["favorited"] is True
    # Re-running is unchanged, not changed — the count a user sees stays honest.
    assert store.bulk_apply("favorite", [a], value=True)["unchanged"] == [a]
    assert store.bulk_apply("favorite", [a], value=False)["changed"] == [a]
    assert store.get_item(a)["favorited"] is False


def test_bulk_collect_shelves_many_items_at_once(store):
    a, b = _item(store, "A"), _item(store, "B")
    shelf = store.create_collection(name="Reading")

    res = store.bulk_apply("collect", [a, b], collection_id=shelf)

    assert sorted(res["changed"]) == sorted([a, b])
    assert {i["id"] for i in store.resolve_collection(shelf)} == {a, b}


def test_bulk_collect_is_idempotent_and_says_so(store):
    """`add_to_collection` uses INSERT OR IGNORE, so a repeat silently succeeds. The
    bulk path checks membership first, or re-shelving 40 items would report 40
    changes and no-ops indistinguishably."""
    a = _item(store, "A")
    shelf = store.create_collection(name="Reading")
    store.bulk_apply("collect", [a], collection_id=shelf)

    res = store.bulk_apply("collect", [a], collection_id=shelf)

    assert res["changed"] == []
    assert res["unchanged"] == [a]


def test_bulk_uncollect_removes_membership_only(store):
    a = _item(store, "A")
    shelf = store.create_collection(name="Reading")
    store.bulk_apply("collect", [a], collection_id=shelf)

    res = store.bulk_apply("uncollect", [a], collection_id=shelf)

    assert res["changed"] == [a]
    assert store.resolve_collection(shelf) == []
    assert store.get_item(a) is not None  # the item itself survives


def test_bulk_collect_refuses_a_smart_shelf(store):
    """A smart shelf resolves membership from its query at read time, so a stored row
    would be ignored by its own reads. Refuse loudly rather than write dead rows."""
    a = _item(store, "A")
    smart = store.create_collection(name="All notes", kind="smart", query="note")

    with pytest.raises(ValueError, match="smart_collection_immutable"):
        store.bulk_apply("collect", [a], collection_id=smart)


def test_bulk_collect_requires_a_real_collection(store):
    a = _item(store, "A")
    with pytest.raises(ValueError, match="no such collection"):
        store.bulk_apply("collect", [a], collection_id="nope")
    with pytest.raises(ValueError, match="requires collection_id"):
        store.bulk_apply("collect", [a])


def test_bulk_archive_and_restore_round_trip(store):
    a = _item(store, "A")
    assert store.bulk_apply("archive", [a])["changed"] == [a]
    assert store.get_item(a)["is_archived"] is True
    assert store.bulk_apply("archive", [a])["unchanged"] == [a]
    assert store.bulk_apply("restore", [a])["changed"] == [a]
    assert store.get_item(a)["is_archived"] is False


def test_bulk_pin(store):
    a = _item(store, "A")
    assert store.bulk_apply("pin", [a], value=True)["changed"] == [a]
    assert store.get_item(a)["is_pinned"] is True


def test_bulk_refuses_an_unknown_op(store):
    with pytest.raises(ValueError, match="unknown bulk op"):
        store.bulk_apply("obliterate", [_item(store, "A")])


def test_delete_is_not_a_bulk_op(store):
    """Deliberate exclusion, mirroring the chat bulk endpoint: every op here is
    reversible, and an irreversible one beside them is a mis-click from data loss."""
    assert "delete" not in store.BULK_OPS


def test_bulk_read_state_does_not_reorder_a_recency_sorted_library(store):
    """Marking a backlog read must not masquerade as editing every item — that would
    reshuffle a library sorted by `updated_at` out from under the user."""
    a = _item(store, "A")
    before = store.get_item(a)["updated_at"]

    store.bulk_apply("read_state", [a], state="read")

    assert store.get_item(a)["updated_at"] == before

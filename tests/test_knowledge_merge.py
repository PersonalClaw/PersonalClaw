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

import asyncio
import json
from types import SimpleNamespace

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request


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


def _embed(store, item_id, vec):
    """Write a raw embedding, the only way to make a scorable pair without a provider."""
    import struct

    store.db.execute(
        "UPDATE items SET embedding = ? WHERE id = ?",
        (struct.pack("<%df" % len(vec), *vec), item_id),
    )
    store.db.commit()


def test_a_real_near_duplicate_pair_IS_surfaced(store):
    """🔴 THE POSITIVE CASE, absent until KL-6 — and its absence hid a total outage.

    Every duplicate-surfacing test here was NEGATIVE (no embedding / unknown item) or vacuous
    (the embedding-leak loop below iterated zero rows), so `find_duplicates` reading a field
    `DupVerdict` does not have — `getattr(verdict, "is_duplicate", False)` instead of
    `verdict.is_dup` — read as covered while returning `[]` for every input in existence. A rail
    that only ever asserts emptiness cannot tell a working scorer from a disconnected one.
    """
    a = _item(store, "Rust async book notes")
    b = _item(store, "Rust async book notes")
    # Same title ⇒ filename_sim 1.0; near-parallel unit vectors ⇒ cosine ≈ 0.995 (floor 0.90);
    # no series-date token in either title ⇒ the date gate abstains. All three clauses agree.
    _embed(store, a, [1.0, 0.0])
    _embed(store, b, [0.995, 0.0999])

    found = store.find_duplicates(a)
    assert len(found) == 1, "the pair satisfies filename + cosine + date-gate"
    assert found[0]["id"] == b
    # The reason travels: it is what makes a destructive merge reviewable in the UI.
    assert found[0]["reason"], "a candidate must carry the scorer's account of the match"


def test_a_genuinely_different_item_is_not_surfaced(store):
    """The other half of the floor: the scorer must still SAY NO, or the fix above would be
    'always return every candidate' and the test above would not notice."""
    a = _item(store, "Rust async book notes")
    b = _item(store, "Sourdough starter log")
    _embed(store, a, [1.0, 0.0])
    _embed(store, b, [0.0, 1.0])
    assert store.find_duplicates(a) == []


def test_duplicates_never_return_the_embedding(store):
    """Megabytes of floats no caller needs, on a list endpoint.

    🪤 This loop used to run over ZERO rows — a single embedded item has no candidate, so it
    asserted nothing while reading as a passing guard on the leak. The pair below makes it
    iterate, and the length assertion is the vacuity floor that keeps it iterating.
    """
    a = _item(store, "Doc A")
    b = _item(store, "Doc A")
    _embed(store, a, [1.0, 0.0])
    _embed(store, b, [0.995, 0.0999])

    rows = store.find_duplicates(a)
    assert len(rows) == 1, "with no candidate this test asserts nothing at all"
    for row in rows:
        assert "embedding" not in row
        assert b == row["id"]


def _dated(store, item_id, created_at):
    """Pin `created_at` — the recency ORDER is the whole subject of the next test."""
    store.db.execute("UPDATE items SET created_at = ? WHERE id = ?", (created_at, item_id))
    store.db.commit()


def test_a_duplicate_OLDER_than_the_result_limit_is_still_surfaced(store):
    """🔴 THE SECOND SILENT-EMPTY PATH ON THIS SURFACE, and the reason the scan is no longer
    bounded by recency.

    `find_duplicates` used to delegate to `find_fuzzy_dup_candidates`, which is
    `ORDER BY created_at DESC LIMIT ?` — a correct bound for the INGEST path, where the anchor is
    the row being ingested and the cosine loop must stay cheap. Read on demand for the UI, that cap
    spends the caller's `limit` on *how many items get scored at all*, newest first. So the ONE
    shape this panel exists for — an old copy and a new copy of the same document, with the library
    grown since — is exactly the shape it could not see: the old copy sits below the recency window
    and is never compared. Measured before the fix, at this test's own corpus size: `[]`.

    It fails the same way the `is_duplicate` typo did, which is why it survived that fix: on a
    surface where "no duplicates" is the right answer for almost every item, a scan that never
    looked and a scan that found nothing print the same empty list.
    """
    old = _item(store, "Rust async book notes")
    _dated(store, old, "2020-01-01T00:00:00")
    _embed(store, old, [1.0, 0.0])

    # A library that has grown well past the default result limit since. Same type and embedded,
    # so every one of these outranks `old` in the old recency-ordered candidate window.
    filler = []
    for i in range(30):
        n = _item(store, f"Unrelated note {i}")
        _dated(store, n, f"2026-01-{i + 1:02d}T00:00:00")
        _embed(store, n, [0.0, 1.0])
        filler.append(n)

    new = _item(store, "Rust async book notes")
    _dated(store, new, "2026-08-01T00:00:00")
    _embed(store, new, [0.995, 0.0999])

    # 🪤 VACUITY FLOOR — without this the test stops testing the window the moment the default
    # limit rises above the corpus size, and would then pass for the wrong reason forever.
    default_limit = 25
    eligible = store.db.execute(
        "SELECT COUNT(*) AS c FROM items WHERE item_type = 'note' AND embedding IS NOT NULL"
    ).fetchone()["c"]
    assert eligible > default_limit, (
        f"{eligible} embedded candidates does not exceed the default limit of {default_limit} — "
        "this corpus no longer reaches past the window it exists to test"
    )
    assert len(filler) == 30

    found = store.find_duplicates(new)
    assert [r["id"] for r in found] == [
        old
    ], "the older copy is the duplicate; a recency-capped scan reports no duplicates at all"


def test_duplicates_are_ordered_STRONGEST_first_not_newest(store):
    """ "Best match first" was a docstring's word, never code: the rows came back in the candidate
    prefilter's `created_at DESC` order, so the weakest match could head a list the UI renders
    top-down next to a delete button. The near-identical copy is dated OLDEST here precisely so
    recency and strength disagree — under the old order this asserts backwards."""
    anchor = _item(store, "Kubernetes operator guide")
    _dated(store, anchor, "2026-03-01T00:00:00")
    _embed(store, anchor, [1.0, 0.0, 0.0])

    strong = _item(store, "Kubernetes operator guide")
    _dated(store, strong, "2020-01-01T00:00:00")  # oldest
    _embed(store, strong, [0.9999, 0.0141, 0.0])  # cosine ≈ 0.9999

    weak = _item(store, "Kubernetes operator guide")
    _dated(store, weak, "2026-02-01T00:00:00")  # newer than `strong`
    _embed(store, weak, [0.91, 0.4146, 0.0])  # cosine ≈ 0.910, just over the 0.90 floor

    found = store.find_duplicates(anchor)
    assert len(found) == 2, "both must clear the gates or the ordering claim is untested"
    assert [r["id"] for r in found] == [strong, weak]
    assert found[0]["similarity"] > found[1]["similarity"]


def test_each_candidate_carries_its_OWN_measured_similarity(store):
    """The reason is what the UI shows to justify a delete, and it used to be one constant.

    `DupVerdict.reason` on the positive branch is the literal "fuzzy dup
    (filename+cosine+date-gate)" — identical for a 0.90 match and a 1.00 one, naming the rule
    rather than the match. The verdict's two measured numbers were dropped on the floor. So the
    UI's stated purpose for rendering it ("the scorer's own account … so the claim is reviewable")
    was carried by text that reviews nothing. The floor here is DIFFERENCE: two candidates of
    visibly different strength must not read identically.
    """
    anchor = _item(store, "Kubernetes operator guide")
    _embed(store, anchor, [1.0, 0.0, 0.0])
    strong = _item(store, "Kubernetes operator guide")
    _embed(store, strong, [0.9999, 0.0141, 0.0])
    weak = _item(store, "Kubernetes operator guide")
    _embed(store, weak, [0.91, 0.4146, 0.0])

    found = store.find_duplicates(anchor)
    assert len(found) == 2
    reasons = [r["reason"] for r in found]
    assert len(set(reasons)) == 2, f"one constant for every row reviews nothing: {reasons}"
    # Both share the anchor's title exactly — the defining case, so it is named, not scored 1.00.
    assert all(r.startswith("Same title · content similarity ") for r in reasons), reasons
    assert reasons[0] == "Same title · content similarity 0.99"
    assert reasons[1] == "Same title · content similarity 0.91"
    # TRUNCATED, never rounded: 0.9999 must not print as 1.00 next to a delete button.
    assert found[0]["similarity"] > 0.999


def test_limit_caps_RESULTS_and_keeps_the_strongest(store):
    """`limit` is what the route clamps and the UI means: how many candidates to SHOW. It used to
    be spent on how many to look at, which is why it could return zero of three."""
    anchor = _item(store, "Kubernetes operator guide")
    _embed(store, anchor, [1.0, 0.0, 0.0])
    ranked = []
    for vec in ([0.9999, 0.0141, 0.0], [0.99, 0.1411, 0.0], [0.95, 0.3122, 0.0]):
        d = _item(store, "Kubernetes operator guide")
        _embed(store, d, vec)
        ranked.append(d)

    assert len(store.find_duplicates(anchor)) == 3, "all three must qualify, or the cap is untested"
    top2 = store.find_duplicates(anchor, limit=2)
    assert [r["id"] for r in top2] == ranked[:2]


def test_a_dissimilar_title_is_never_scored_however_close_the_vectors(store):
    """The counter-test for the unbounded scan: reach grew, the RULE did not. The filename leg is
    now the prefilter, so this also pins that phase 1 gates at the resolver's own threshold — a
    prefilter that let everything through would turn every embedded item into a cosine comparison
    and every close vector into a proposed deletion."""
    anchor = _item(store, "Rust async book notes")
    _embed(store, anchor, [1.0, 0.0])
    twin = _item(store, "Sourdough starter log")  # identical vector, unrelated title
    _embed(store, twin, [1.0, 0.0])

    assert store.find_duplicates(anchor) == []
    # Vacuity floor: the vectors really are identical, so only the title leg can be refusing.
    from personalclaw.knowledge.dedup import cosine_similarity

    assert cosine_similarity([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
    assert twin  # the candidate exists and is embedded; it is gated, not absent


# ── The HTTP routes the UI drives (KL-6) ────────────────────────────────
#
# Everything above proves the STORE. The frontend cannot call the store — it calls
# `GET /api/knowledge/items/{id}/duplicates` and `POST …/merge`, and until KL-6 there was no
# consumer of either, so neither route had a test. These cover the layer the merge button
# actually crosses:
#
#   * The survivor is the PATH id and the loser is the BODY id, in that direction. Swapping them
#     deletes the document the user was looking at, and a store-level test cannot catch it
#     because the store's own argument order would still be honoured.
#   * `confirm: true` is REQUIRED and its refusal is total — a 400 that had already deleted the
#     item would be worse than no gate at all.
#   * The loser 404s afterwards, read back through the same route the UI navigates to.


def _call(store, handler_name, method, path, *, match_info=None, body=None):
    app = web.Application()
    app["state"] = SimpleNamespace(knowledge_store=store)
    req = make_mocked_request(method, path, app=app, match_info=match_info or {})
    if body is not None:

        async def _json():
            return body

        req.json = _json
    from personalclaw.dashboard.handlers import knowledge as H

    resp = asyncio.new_event_loop().run_until_complete(getattr(H, handler_name)(req))
    return resp, json.loads(resp.body)


def _merge_via_route(store, keep, loser, **body):
    payload = {"merge_id": loser, "confirm": True}
    payload.update(body)
    return _call(
        store,
        "merge_items",
        "POST",
        f"/api/knowledge/items/{keep}/merge",
        match_info={"id": keep},
        body=payload,
    )


def test_the_route_merge_keeps_the_path_item_and_moves_both_sides_curation(store):
    """The atom's substance: after a UI-shaped merge the survivor carries BOTH items' rows."""
    keep, loser = _item(store, "Keep"), _item(store, "Loser")
    kept_shelf = store.create_collection(name="Reading")
    loser_shelf = store.create_collection(name="Archive")
    store.add_to_collection(kept_shelf, keep)
    store.add_to_collection(loser_shelf, loser)
    kept_ent = store.add_entity(name="Sparrow", entity_type="project")
    loser_ent = store.add_entity(name="Kestrel", entity_type="project")
    store.add_mention(keep, kept_ent)
    store.add_mention(loser, loser_ent)

    resp, data = _merge_via_route(store, keep, loser)

    assert resp.status == 200 and data["ok"] is True
    assert (data["kept"], data["merged"]) == (keep, loser)
    # Collection MEMBERSHIPS: both shelves, not just the survivor's own.
    assert _collections_of(store, keep) == {kept_shelf, loser_shelf}
    # MENTIONS: both entities.
    assert _mention_entities(store, keep) == {kept_ent, loser_ent}
    assert data["moved"]["collections"] == 1 and data["moved"]["mentions"] == 1


def test_the_route_leaves_the_loser_404ing(store):
    """Read back through the route the UI navigates to, not through the store."""
    keep, loser = _item(store, "Keep"), _item(store, "Loser")
    _merge_via_route(store, keep, loser)

    resp, _ = _call(
        store,
        "get_item",
        "GET",
        f"/api/knowledge/items/{loser}",
        match_info={"id": loser},
    )
    assert resp.status == 404
    # …and the survivor is still readable, so a 404 above means "the loser" not "both".
    resp, _ = _call(
        store,
        "get_item",
        "GET",
        f"/api/knowledge/items/{keep}",
        match_info={"id": keep},
    )
    assert resp.status == 200


def test_the_route_refuses_a_merge_without_confirm_and_deletes_nothing(store):
    """A rejected merge must be a NO-OP, not a partially applied one."""
    keep, loser = _item(store, "Keep"), _item(store, "Loser")
    shelf = store.create_collection(name="Archive")
    store.add_to_collection(shelf, loser)

    resp, data = _call(
        store,
        "merge_items",
        "POST",
        f"/api/knowledge/items/{keep}/merge",
        match_info={"id": keep},
        body={"merge_id": loser},  # no confirm
    )

    assert resp.status == 400 and "confirm" in data["error"]
    assert store.get_item(loser) is not None, "the loser must still exist"
    assert _collections_of(store, keep) == set(), "nothing may have moved"


def test_the_route_refuses_a_self_merge(store):
    """The path id and the body id being equal would cascade-delete the survivor."""
    keep = _item(store, "Keep")
    resp, _ = _merge_via_route(store, keep, keep)
    assert resp.status == 400
    assert store.get_item(keep) is not None


def test_the_duplicates_route_404s_for_an_unknown_item(store):
    """Distinct from "no duplicates": the UI must not render a clean list for a missing item."""
    resp, _ = _call(
        store,
        "get_item_duplicates",
        "GET",
        "/api/knowledge/items/nope/duplicates",
        match_info={"id": "nope"},
    )
    assert resp.status == 404


def test_the_duplicates_route_carries_a_real_candidate_to_the_frontend(store):
    """The whole UI path in one assertion: a real pair, through the route, under the key the
    frontend unwraps (`d.duplicates`).

    Vacuity floor for the two tests above — asserting only `isinstance(…, list)` would have gone
    green throughout the outage this atom found, because `[]` is a list.
    """
    a = _item(store, "Rust async book notes")
    b = _item(store, "Rust async book notes")
    _embed(store, a, [1.0, 0.0])
    _embed(store, b, [0.995, 0.0999])

    resp, data = _call(
        store,
        "get_item_duplicates",
        "GET",
        f"/api/knowledge/items/{a}/duplicates",
        match_info={"id": a},
    )
    assert resp.status == 200
    assert [r["id"] for r in data["duplicates"]] == [b]
    assert data["duplicates"][0]["reason"]
    assert "embedding" not in data["duplicates"][0]

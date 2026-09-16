"""Per-arm pre-fusion qualification in knowledge retrieval (#392).

This file replaces ``test_relevance_cliff.py``. The mechanism it tested — a consecutive-gap
cut applied to the RRF-fused score — was removed, because a fused score is
``Σ 1/(k + rank)`` and therefore a function of position and arm count only: it carries no
information about how good any single match is, so no threshold on it can separate a
relevant cluster from a weak tail. The tests below pin what replaced it:

* each arm qualifies its own hits on its own **absolute** scale, before fusion flattens
  them into ranks (:data:`ARM_QUALIFICATION`), and
* the completeness of that contract is derived from :data:`ARMS`, so a fourth arm cannot be
  added without declaring a qualification.
"""

import struct

import pytest

from personalclaw.knowledge import retrieval
from personalclaw.knowledge.retrieval import (
    _VECTOR_MIN_SIMILARITY,
    ARM_QUALIFICATION,
    ARMS,
    HybridRetriever,
)
from personalclaw.knowledge.store import KnowledgeStore


@pytest.fixture()
def store(tmp_path):
    s = KnowledgeStore(str(tmp_path / "arms.db"))
    yield s
    s.close()


def _item(store, title, content, *, tags=None, embedding=None):
    iid = store.create_typed_item(item_type="note", title=title, content=content, tags=tags or [])
    if embedding is not None:
        store.db.execute("UPDATE items SET embedding = ? WHERE id = ?", (embedding, iid))
        store.db.commit()
    return iid


def _v(*vals):
    return struct.pack(f"{len(vals)}f", *vals)


# ---------------------------------------------------------------------------
# The rail: the contract is keyed by the arm vocabulary, not by a hand-kept list
# ---------------------------------------------------------------------------


def test_every_arm_declares_a_prefusion_qualification():
    """Derived from ARMS: adding a fourth arm without a qualification reds here.

    This is the rail. The defect in #392 was an arm (graph) handing fusion its unbounded
    reach, on the assumption that a downstream threshold would trim it — which RRF makes
    impossible. Any new arm inherits that trap unless it says how it qualifies a hit.
    """
    assert set(ARM_QUALIFICATION) == set(ARMS)
    for arm in ARMS:
        assert ARM_QUALIFICATION[arm].strip(), f"arm {arm!r} declares an empty qualification"


def test_no_post_fusion_score_threshold_is_reintroduced():
    """No cut on the fused score, by name or by constant.

    Kept as an assertion rather than a comment because the removed cut looked reasonable
    (and its replacement, a floor relative to the top score, looks equally reasonable and
    is the same error — see ``test_rrf_score_carries_rank_only_not_match_quality``).
    """
    for gone in ("relevance_cliff_cut", "_RELEVANCE_CLIFF_GAP", "_CLIFF_MIN_RESULTS"):
        assert not hasattr(retrieval, gone), (
            f"{gone} is back: a threshold on an RRF-fused score is a threshold on POSITION, "
            "not on relevance. Qualify inside the arm instead (ARM_QUALIFICATION)."
        )


def test_rrf_score_carries_rank_only_not_match_quality():
    """Why no fused-score threshold can work — the root cause, executable.

    Two items retrieved by the same arms at the same ranks fuse to the identical score, so
    a threshold cannot tell them apart no matter how differently they match.
    """
    fuse = HybridRetriever._rrf_fuse
    perfect = dict(fuse([("perfect", 1)], [("perfect", 1)]))
    barely = dict(fuse([("barely", 1)], [("barely", 1)]))
    assert perfect["perfect"] == barely["barely"] == pytest.approx(2 / 61)

    # A single-arm hit is exactly 1/(60+rank): monotone in rank and nothing else. So a
    # "keep >= (1-gap) x top" floor on a single-arm list is a constant top-K.
    one_arm = dict(fuse([(f"i{r}", r) for r in range(1, 40)]))
    assert one_arm["i1"] == pytest.approx(1 / 61)
    assert one_arm["i30"] == pytest.approx(1 / 90)
    top = one_arm["i1"]
    kept = sum(1 for r in range(1, 40) if one_arm[f"i{r}"] >= 0.70 * top)
    assert kept == 27  # rank 28 is the first below the floor, on ANY corpus

    # ...while on a two-arm list that same 0.70 constant means something unrelated: the
    # best possible single-arm score is already below it, so every single-arm hit is cut.
    assert 1 / 61 < 0.70 * (2 / 61)


# ---------------------------------------------------------------------------
# ARM_GRAPH: hop distance is the arm's absolute scale
# ---------------------------------------------------------------------------


def _linked_graph(store):
    """Two related entities; one item mentions each. Returns (ids, entity ids)."""
    direct_item = _item(store, "Sequential rebuild internals", "How the rebuild proceeds.")
    neighbor_item = _item(store, "Unrelated kiln log", "Cone 6 oxidation, hold 15 minutes.")
    e_direct = store.add_entity("dRAID", "technology")
    e_neighbor = store.add_entity("Pottery", "topic")
    store.add_entity_relation(e_direct, e_neighbor, "related_to")
    store.add_mention(direct_item, e_direct)
    store.add_mention(neighbor_item, e_neighbor)
    store.db.commit()
    return direct_item, neighbor_item, e_direct, e_neighbor


def test_graph_arm_qualifies_on_direct_mention_not_traversal_reach(store):
    """An item reached only through the traversal is not a hit while a direct one exists.

    Before #392 both were counted identically, so the arm's output was the whole connected
    component — measured at 23 of 26 items for three unrelated queries on one library.
    """
    direct_item, neighbor_item, e_direct, e_neighbor = _linked_graph(store)
    r = HybridRetriever(store, embedder=None)

    hits = r._graph_search("dRAID", limit=20)
    assert [h[0] for h in hits] == [direct_item]

    # The traversal still REACHES the neighbour — it is qualification, not reach, that changed.
    assert e_neighbor in {n["id"] for n in store.get_neighbors(e_direct, depth=2)}
    # And the neighbour-only item is not in the fused result either.
    assert neighbor_item not in {x["id"] for x in r.search("dRAID", limit=20)}


def test_graph_arm_falls_back_to_traversal_when_no_direct_mention(store):
    """Recall floor: an entity that exists but is mentioned by nothing still traverses.

    This is the fallback half of the qualification — the same role
    ``_VECTOR_MIN_SIMILARITY`` gives a near-orthogonal neighbour. Without it, qualifying on
    direct mentions would silently delete the arm on a library whose entity rows outrun its
    mention rows (mid-extraction, or an alias resolved to an unmentioned canonical entity).
    """
    neighbor_item = _item(store, "Rebuild throttling", "Bounds outstanding I/O.")
    e_unmentioned = store.add_entity("dRAID", "technology")
    e_neighbor = store.add_entity("resilver", "concept")
    store.add_entity_relation(e_unmentioned, e_neighbor, "related_to")
    store.add_mention(neighbor_item, e_neighbor)  # nothing mentions dRAID itself
    store.db.commit()

    r = HybridRetriever(store, embedder=None)
    assert [h[0] for h in r._graph_search("dRAID", limit=20)] == [neighbor_item]


def test_graph_arm_limit_is_not_spent_on_traversal_only_items(store):
    """The SQL LIMIT must rank direct mentions first, or qualification is decided by chance.

    A neighbour-only item with many neighbour mentions used to outrank (and with a tight
    limit, evict) an item that mentions the query's own entity once.
    """
    hub = store.add_entity("dRAID", "technology")
    others = [store.add_entity(f"Neighbour{i}", "topic") for i in range(4)]
    for o in others:
        store.add_entity_relation(hub, o, "related_to")

    thin_direct = _item(store, "One dRAID mention", "Brief.")
    store.add_mention(thin_direct, hub)
    fat_neighbor = _item(store, "Mentions every neighbour", "Broad.")
    for o in others:
        store.add_mention(fat_neighbor, o)
    store.db.commit()

    r = HybridRetriever(store, embedder=None)
    assert [h[0] for h in r._graph_search("dRAID", limit=1)] == [thin_direct]


def test_common_term_no_longer_returns_a_term_absent_tail(store):
    """#392 end-to-end: a term present in part of a densely-linked library.

    The library is built the way the reported one was — a few topic hub entities that every
    on-topic item mentions, all linked to each other — so a depth-2 traversal reaches every
    item. Before the fix the fused result was the whole component, tail included.
    """
    hubs = {
        name: store.add_entity(name, "technology") for name in ("dRAID", "ZFS", "SMART", "scrub")
    }
    for a in hubs.values():
        for b in hubs.values():
            if a != b:
                store.add_entity_relation(a, b, "related_to")

    on_topic = [
        _item(store, f"dRAID note {i}", f"A draid vdev with distributed spares, {i}.")
        for i in range(4)
    ]
    for iid in on_topic:
        store.add_mention(iid, hubs["dRAID"])
        store.add_mention(iid, hubs["ZFS"])
    off_topic = [
        _item(store, "Pending sector watch", "smartctl attribute 197 climbs."),
        _item(store, "Scrub throughput note", "27TB scanned in six hours."),
        _item(store, "Resume tokens", "An interrupted send can be resumed."),
    ]
    for iid, hub in zip(off_topic, ("SMART", "scrub", "ZFS")):
        store.add_mention(iid, hubs[hub])
    store.db.commit()

    r = HybridRetriever(store, embedder=None)
    hits = r.search("draid", limit=50)
    assert hits, "a term with corpus presence must still return its matches"
    assert {h["id"] for h in hits} == set(on_topic)
    for h in hits:
        body = (h["title"] + h["content"]).lower()
        assert "draid" in body, f"term-absent item survived: {h['title']} ({h['match_type']})"
    # And the arm-attribution the UI reports is still honest about which arms fired.
    assert all("keyword" in h["match_type"] for h in hits)


# ---------------------------------------------------------------------------
# ARM_KEYWORD / ARM_VECTOR: their qualifications, pinned beside the graph arm's
# ---------------------------------------------------------------------------


def test_keyword_arm_qualification_requires_a_term_occurrence(store):
    """FTS5 MATCH is the keyword arm's qualification: no occurrence, no hit."""
    hit = _item(store, "dRAID basics", "A draid vdev has distributed spares.")
    _item(store, "Kiln firing schedule", "Cone 6 oxidation, hold 15 minutes.")
    store.db.commit()

    r = HybridRetriever(store, embedder=None)
    assert [h[0] for h in r._keyword_search("draid", limit=20)] == [hit]
    assert r._keyword_search("zzzznope99", limit=20) == []


def test_vector_arm_qualification_floor_is_absolute_and_prefusion(store):
    """The vector arm's floor is a cosine on its own scale, applied before fusion.

    Pinned here as well as in test_knowledge.py because this is the arm the other two were
    made to match: it is the one that already refused to hand fusion its unbounded top-K.
    """
    near = _item(store, "Aligned", "Body.", embedding=_v(1.0, 0.0, 0.0, 0.0))
    orthogonal = _item(store, "Orthogonal", "Body.", embedding=_v(0.0, 1.0, 0.0, 0.0))
    store.db.commit()

    r = HybridRetriever(store, embedder=lambda _q: [1.0, 0.0, 0.0, 0.0])
    ids = [h[0] for h in r._vector_search("anything", limit=20)]
    assert ids == [near]
    assert orthogonal not in ids  # cosine 0.0 < the floor
    assert _VECTOR_MIN_SIMILARITY > 0.0

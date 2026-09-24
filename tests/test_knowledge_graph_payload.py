"""KL-17 -- GET /api/knowledge/graph: every node ships, EDGES are thinned.

What changed, and why these tests exist: the endpoint used to answer a library of any size
with ``sorted(nodes, key=degree)[:limit]`` -- at most 200 entities, default 100. That is
data loss with nothing in the response saying so: the 2,800 entities of a 3,000-entity
library were not collapsed or summarised, they were simply absent, and a user navigating
the graph could not tell the difference between "this entity has no relations" and "this
entity was not in the top 200 by degree".

KL-17 replaces the node cap with edge thinning -- a weight floor plus a top-K-PER-NODE keep
-- so the payload stays bounded while every node survives. The tests below pin the four
properties that make that a real replacement rather than a rename:

* every node ships, including ones the old degree sort would have dropped;
* the top-K is per-node, so a hub cannot eat a global budget and leave the periphery
  edgeless (the node cap wearing a different hat);
* positions and cluster labels are deterministic -- an unstable layout or an unstable label
  teaches the user nothing, which is the whole point of computing them server-side;
* the memo really memoizes, and its invalidation really is debounced.

Every negative assertion here carries a vacuity guard and a positive control seeded in the
same test, so an empty graph, a broken fixture or a spy that never counts cannot make an
exclusion assertion pass trivially.

``project_2d`` (KL-17a) is substituted through the handler's ``_load_project_2d`` seam by a
spy that places an item at its first two vector components. That makes centroids exactly
predictable AND makes "the memo did not recompute" an assertion about a call count rather
than about elapsed time. The real function's contract -- a point for every input id, in
input order, normalized, off-basis vectors at the origin -- is what the handler codes
against; the spy honours it.
"""

from __future__ import annotations

import asyncio
import json
import os
from types import SimpleNamespace

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from personalclaw.dashboard.handlers import knowledge as H
from personalclaw.knowledge.embedder import floats_to_bytes
from personalclaw.knowledge.store import KnowledgeStore

#: The node cap this atom deletes. Fixtures that must exceed it are sized from here rather
#: than from a hardcoded 220, so the "above the old cap" claim stays true by construction.
OLD_NODE_CAP = 200


def _run(coro):
    return asyncio.run(coro)


class _ProjectionSpy:
    """Stand-in for ``knowledge.projection.project_2d``, counting its calls.

    Honours the real contract: one ``(x, y)`` for EVERY input id, never NaN. Places an item
    at its first two components so a centroid over two items is arithmetic a test can state
    in full. ``calls`` records the id set of each invocation -- so "served from the memo"
    means "the projection was not called again", not "it returned fast".
    """

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, vectors: dict[str, list[float]], *, seed: int = 0):
        self.calls.append(sorted(vectors))
        return {k: (float(v[0]), float(v[1])) for k, v in vectors.items()}


@pytest.fixture
def spy(monkeypatch) -> _ProjectionSpy:
    """Substitute the projection and clear the process-global memo.

    The memo is module state: without the clear, one test's payload can be served to the
    next whenever two tmp paths collide, and a memo bug would hide behind test ordering.
    """
    s = _ProjectionSpy()
    monkeypatch.setattr(H, "_load_project_2d", lambda: s)
    H._graph_memo.clear()
    yield s
    H._graph_memo.clear()


@pytest.fixture
def store(tmp_path, monkeypatch):
    """A real store on an isolated path, under an isolated home.

    ``PERSONALCLAW_HOME`` is redirected even though this endpoint does not load the config:
    the store's own path helper resolves ``config_dir()``, and the real-home rail fails the
    whole session over one stray write into the developer's ``~/.personalclaw``.
    """
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path / "home"))
    return KnowledgeStore(os.path.join(tmp_path, "k.db"))


def _get(store, query: str = ""):
    """Drive the real handler; return (status, decoded body)."""
    app = web.Application()
    app["state"] = SimpleNamespace(knowledge_store=store)
    req = make_mocked_request("GET", f"/api/knowledge/graph{query}", app=app)
    resp = _run(H.get_full_graph(req))
    return resp.status, json.loads(resp.body)


def _ok(store, query: str = "") -> dict:
    status, body = _get(store, query)
    assert status == 200, body
    return body


def _entity(store, name, entity_type="concept") -> str:
    return store.add_entity(name, entity_type)


def _item(store, title, *, vector=None, tags=None) -> str:
    """One note, optionally embedded.

    The vector is written through ``floats_to_bytes`` -- the same encoder the ingest path
    uses -- so the handler's decode is exercised for real. Four components minimum: the
    shared decoder rejects a blob under 16 bytes as damaged, and a 2-component fixture
    would decode to nothing and silently send every node to the origin.
    """
    iid = store.create_typed_item(item_type="note", title=title, content="body", tags=tags)
    if vector is not None:
        assert len(vector) >= 4, "a <16-byte blob decodes to nothing; see docstring"
        store.db.execute(
            "UPDATE items SET embedding = ? WHERE id = ?", (floats_to_bytes(vector), iid)
        )
    store.db.commit()
    return iid


def _mention(store, item_id, entity_id) -> None:
    store.add_mention(item_id, entity_id)


def _relate(store, a, b, weight=1.0, rtype="relates_to") -> None:
    store.add_entity_relation(source_id=a, target_id=b, relation_type=rtype, weight=weight)


# ---------- clause 1: every node ships ----------


def test_every_node_ships_when_the_graph_exceeds_the_old_node_cap(store, spy):
    """The clause's whole point. Above the retired cap, nothing is dropped -- including the
    two entities the old degree sort would have discarded FIRST."""
    total = OLD_NODE_CAP + 20
    ids = [_entity(store, f"E{i:04d}") for i in range(total)]
    # A hub plus a rim: ids[0] has high degree, ids[1:-2] have degree 1, and the last two
    # have degree 0 -- exactly the entities `sorted(..., key=degree)[:200]` truncated away.
    for other in ids[1:-2]:
        _relate(store, ids[0], other)
    orphans = ids[-2:]

    body = _ok(store)

    # Vacuity guard: the fixture must actually exceed the cap, or "every node ships" is a
    # claim about a graph the old code would have returned whole.
    assert total > OLD_NODE_CAP
    assert len(body["nodes"]) == total
    assert {n["id"] for n in body["nodes"]} == set(ids)
    # Positive control for the exclusion the old cap made: degree-0 entities are present,
    # and are reported as degree 0 rather than quietly given a fake degree.
    for orphan in orphans:
        node = next(n for n in body["nodes"] if n["id"] == orphan)
        assert node["degree"] == 0
        assert node["cluster"] is None


def test_a_limit_query_param_no_longer_caps_the_nodes(store, spy):
    """``limit`` is gone, not renamed. The shipped frontend still sends ``?limit=120``; an
    unknown param must be ignored, never honoured as a cap and never a 400."""
    ids = [_entity(store, f"E{i:03d}") for i in range(12)]
    for other in ids[1:]:
        _relate(store, ids[0], other)

    status, body = _get(store, "?limit=3")

    assert status == 200
    assert len(body["nodes"]) == len(ids) > 3  # the cap value is inert, and 3 < 12 proves it
    assert "limit" not in body["thinning"]


# ---------- clause 2: edges are thinned, by a floor and per-node ----------


def test_edges_are_thinned_by_the_weight_floor(store, spy):
    """A relation under ``min_weight`` is dropped; the identical call with no floor keeps
    it, which is what makes the exclusion non-vacuous."""
    a, b, c = (_entity(store, n) for n in ("A", "B", "C"))
    _relate(store, a, b, weight=0.9)
    _relate(store, a, c, weight=0.1)

    def pairs(body):
        return {(e["source"], e["target"]) for e in body["edges"]}

    floored = _ok(store, "?min_weight=0.5")
    H._graph_memo.clear()
    unfloored = _ok(store, "?min_weight=0.0")

    # Positive control: the weak edge exists in the fixture and ships without a floor.
    assert (a, c) in pairs(unfloored)
    assert (a, b) in pairs(unfloored)
    # The floor bites, and only on the weak edge.
    assert (a, c) not in pairs(floored)
    assert (a, b) in pairs(floored)
    assert floored["thinning"]["edges_total"] == 2
    assert floored["thinning"]["edges_kept"] == 1
    # Nodes are untouched by edge thinning -- C keeps its place in the graph with no edges.
    assert {n["id"] for n in floored["nodes"]} == {a, b, c}


def test_top_k_is_per_node_so_a_hub_cannot_starve_the_periphery(store, spy):
    """Per-node top-K, not global top-K.

    Fixture: a hub with twelve strong relations, plus one weak peripheral pair. Under a
    GLOBAL top-K of 3 the hub's three strongest edges would consume the whole budget and
    the peripheral pair -- the only edge those two nodes have -- would not ship. Under a
    per-node keep the periphery keeps its own best edge.
    """
    hub = _entity(store, "HUB")
    leaves = [_entity(store, f"L{i:02d}") for i in range(12)]
    for i, leaf in enumerate(leaves):
        _relate(store, hub, leaf, weight=0.90 + i * 0.001)
    p1, p2 = _entity(store, "P1"), _entity(store, "P2")
    _relate(store, p1, p2, weight=0.20)

    top_k = 3
    body = _ok(store, f"?top_k={top_k}")
    shipped = {(e["source"], e["target"]) for e in body["edges"]}

    # Vacuity guard: the peripheral edge must genuinely fall outside a global budget of
    # top_k, or "per-node beats global" is a distinction this fixture cannot show.
    all_edges = sorted(
        [(0.90 + i * 0.001, hub, leaf) for i, leaf in enumerate(leaves)] + [(0.20, p1, p2)],
        key=lambda e: -e[0],
    )
    assert (p1, p2) not in {(u, v) for _, u, v in all_edges[:top_k]}
    # The property: the periphery keeps its edge anyway.
    assert (p1, p2) in shipped
    # Positive control that thinning is happening at all rather than everything shipping:
    # the hub's own selection is bounded even though the leaves' keeps re-admit their edge.
    assert len(shipped) > top_k
    assert body["thinning"]["edges_kept"] == len(shipped)
    assert body["thinning"]["edges_total"] == len(leaves) + 1


def _reenumerate_graph_reversed(store) -> None:
    """Re-add the SAME library's nodes and edges in the opposite order.

    Order-independence is a property of one library, not of two. Measured while writing
    this: comparing two separately-built stores fails, because equal-weight edges tie-break
    on entity id and the two stores mint different uuids -- so that comparison asks whether
    two *different* libraries agree, which is not a property anything should have. What the
    thinning actually claims is that the answer does not depend on the order
    ``graph.edges(data=True)`` happens to yield, and insertion order is the only thing that
    decides that. So the rows are re-added backwards, ids unchanged.
    """
    ents = list(store.db.execute("SELECT id, name, entity_type FROM entities"))
    rels = list(
        store.db.execute(
            "SELECT id, source_id, target_id, relation_type, weight FROM entity_relations"
        )
    )
    store.graph.clear()
    for row in reversed(ents):
        store.graph.add_node(row["id"], name=row["name"], entity_type=row["entity_type"])
    for row in reversed(rels):
        store.graph.add_edge(
            row["source_id"],
            row["target_id"],
            id=row["id"],
            relation_type=row["relation_type"],
            weight=row["weight"],
        )


def test_thinning_is_order_independent(store, spy):
    """The keep is a set union, sorted on (-weight, source, target, type), so the answer
    cannot depend on the order the graph enumerates its edges in -- including for the
    equal-weight edges, where the enumeration order is the only thing that could leak."""
    names = [f"N{i:02d}" for i in range(8)]
    ids = {n: _entity(store, n) for n in names}
    for i in range(6):
        for j in range(i + 1, 7):
            # (i + j) collides for several pairs on purpose: ties are where an
            # order-dependent selection would show up.
            _relate(store, ids[names[i]], ids[names[j]], weight=0.5 + (i + j) / 100)

    forward = _ok(store, "?top_k=2")
    enumeration = [(u, v) for u, v, _ in store.graph.edges(data=True)]
    H._graph_memo.clear()
    _reenumerate_graph_reversed(store)
    backward = _ok(store, "?top_k=2")

    # Vacuity guard on the reversal itself: if the enumeration order did not change, this
    # test compares a payload with itself and can never fail.
    assert [(u, v) for u, v, _ in store.graph.edges(data=True)] != enumeration
    assert forward["edges"] == backward["edges"]
    assert forward["nodes"] == backward["nodes"]
    # And the comparison is over a genuinely thinned set, not an untouched one.
    assert 0 < forward["thinning"]["edges_kept"] < forward["thinning"]["edges_total"]


# ---------- clause 3: positions ----------


def test_positions_are_item_centroids_and_the_unplaceable_sit_at_the_origin(store, spy):
    """Entity nodes positioned by the embeddings of the ITEMS THAT MENTION THEM: the
    centroid when several do, the origin when none is usable."""
    two, one, none_ = (_entity(store, n) for n in ("TWO", "ONE", "NONE"))
    i1 = _item(store, "i1", vector=[1.0, 0.0, 0.0, 0.0])
    i2 = _item(store, "i2", vector=[0.0, 1.0, 0.0, 0.0])
    i3 = _item(store, "i3", vector=[0.5, 0.5, 0.0, 0.0])
    unembedded = _item(store, "i4")  # no embedding at all
    _mention(store, i1, two)
    _mention(store, i2, two)
    _mention(store, i3, one)
    _mention(store, unembedded, none_)
    _relate(store, two, one)

    body = _ok(store)
    at = {n["id"]: (n["x"], n["y"]) for n in body["nodes"]}

    # Centroid of (1,0) and (0,1); and the single-item entity sits exactly on its item.
    assert at[two] == pytest.approx((0.5, 0.5))
    assert at[one] == pytest.approx((0.5, 0.5))
    # Positive control for the origin assertion: something is genuinely placed away from
    # the origin, so "NONE is at (0,0)" is not "everything is at (0,0)".
    assert at[two] != (0.0, 0.0)
    assert at[none_] == (0.0, 0.0)
    assert body["layout"] == {"placed": 2, "unplaceable": 1, "cluster_min_size": 3}
    # The projection saw only the three items that place a node -- not the unembedded one,
    # and not a per-entity query's worth of duplicates.
    assert spy.calls == [sorted([i1, i2, i3])]


def test_placed_distinguishes_a_real_centroid_at_the_origin_from_an_unplaceable_node(store, spy):
    """The origin is not a usable test for "has no position".

    Found by driving the real ``project_2d``: an entity mentioned by one item in each of two
    opposed clusters has a centroid of exactly (0, 0). A canvas that labels "at the origin"
    as "no embedding yet" therefore mislabels it, so the payload carries the answer as a
    per-node flag instead of leaving it to be inferred from coordinates.
    """
    straddler, empty = _entity(store, "STRADDLER"), _entity(store, "EMPTY")
    left = _item(store, "left", vector=[-1.0, 0.0, 0.0, 0.0])
    right = _item(store, "right", vector=[1.0, 0.0, 0.0, 0.0])
    _mention(store, left, straddler)
    _mention(store, right, straddler)
    _mention(store, _item(store, "bare"), empty)
    _relate(store, straddler, empty)

    body = _ok(store)
    nodes = {n["id"]: n for n in body["nodes"]}

    # Vacuity guard: the two nodes really are at the SAME coordinates, so a coordinate-based
    # test genuinely cannot separate them and the flag is not redundant.
    assert (nodes[straddler]["x"], nodes[straddler]["y"]) == (0.0, 0.0)
    assert (nodes[empty]["x"], nodes[empty]["y"]) == (0.0, 0.0)
    # The flag separates them, and the aggregate agrees.
    assert nodes[straddler]["placed"] is True
    assert nodes[empty]["placed"] is False
    assert body["layout"] == {"placed": 1, "unplaceable": 1, "cluster_min_size": 3}


def test_a_stale_dimension_item_does_not_drag_a_centroid_to_the_middle(store, spy):
    """An entity mentioned by one live-dimension item and one embedded under a previous
    model sits ON the live item -- not half-way between it and the origin. The origin is an
    unplaceable sentinel; averaging a sentinel into a position is silent corruption."""
    mixed, plain = _entity(store, "MIXED"), _entity(store, "PLAIN")
    live = _item(store, "live", vector=[1.0, 1.0, 0.0, 0.0])
    also_live = _item(store, "also", vector=[1.0, 1.0, 0.0, 0.0])
    stale = _item(store, "stale", vector=[9.0, 9.0, 0.0, 0.0, 0.0])  # 5 dims = old model
    _mention(store, live, mixed)
    _mention(store, stale, mixed)
    _mention(store, also_live, plain)
    _relate(store, mixed, plain)

    body = _ok(store)
    at = {n["id"]: (n["x"], n["y"]) for n in body["nodes"]}

    # Vacuity guard: the dominant dimension is the 4-component one, so the 5-component
    # vector is the minority and the filter has something to exclude.
    assert spy.calls == [sorted([live, also_live])]
    assert at[mixed] == pytest.approx((1.0, 1.0))
    # Positive control: the stale item is excluded, not the whole entity -- MIXED is still
    # counted as placed, and is not at the origin.
    assert body["layout"]["placed"] == 2
    assert at[mixed] != (0.0, 0.0)


# ---------- clause 4: cluster labels from dominant tags ----------


def _cluster_of(body, node_id):
    return next(n["cluster"] for n in body["nodes"] if n["id"] == node_id)


def test_clusters_are_labelled_from_dominant_tags_only_above_the_minimum_size(store, spy):
    """A component of three or more earns an id and a label from its entities' most common
    item tag. A two-entity component does not -- and its nodes still ship."""
    big = [_entity(store, f"B{i}") for i in range(3)]
    small = [_entity(store, f"S{i}") for i in range(2)]
    _relate(store, big[0], big[1])
    _relate(store, big[1], big[2])
    _relate(store, small[0], small[1])
    for eid, tag in zip(big, ("physics", "physics", "chemistry")):
        _mention(store, _item(store, f"doc-{eid}", tags=[tag]), eid)
    for eid in small:
        _mention(store, _item(store, f"doc-{eid}", tags=["misc"]), eid)

    body = _ok(store)

    # Positive control: a cluster IS produced, so "the small one has none" is not "clusters
    # are broken and nothing is labelled".
    assert [(c["label"], c["label_source"], c["size"]) for c in body["clusters"]] == [
        ("physics", "tag", 3)
    ]
    assert {_cluster_of(body, e) for e in big} == {0}
    # Below the floor: no cluster id, but the nodes are still in the payload -- the vacuity
    # guard that "cluster is None" is not "the entities were dropped".
    assert {n["id"] for n in body["nodes"]} >= set(small)
    assert {_cluster_of(body, e) for e in small} == {None}
    # And "misc" -- the only tag in the small component -- never becomes a label.
    assert "misc" not in {c["label"] for c in body["clusters"]}


def test_cluster_labels_break_ties_deterministically_and_repeat_across_calls(store, spy):
    """An unstable label is the same defect as an unstable layout. An exact tag tie resolves
    on the name ascending, and a recomputed payload is identical to the first."""
    ids = [_entity(store, f"T{i}") for i in range(3)]
    _relate(store, ids[0], ids[1])
    _relate(store, ids[1], ids[2])
    # zeta first, so a dict-order or insertion-order tie-break would pick it. Both items are
    # embedded so the recompute assertion below has something to count -- measured while
    # writing this: with unembedded items the projection is never called at all and
    # `len(spy.calls) == 2` was asserting against a fixture that could only ever produce 0.
    _mention(store, _item(store, "d0", tags=["zeta"], vector=[0.2, 0.1, 0.0, 0.0]), ids[0])
    _mention(store, _item(store, "d1", tags=["alpha"], vector=[0.4, 0.3, 0.0, 0.0]), ids[1])

    first = _ok(store)
    H._graph_memo.clear()  # force a genuine recompute, not a memo replay
    second = _ok(store)

    assert first["clusters"][0]["label"] == "alpha"
    # Vacuity guard: the tie is real -- both tags are present with the same count.
    tallies = {
        row["name"]
        for row in store.db.execute(
            "SELECT t.name AS name FROM mentions m JOIN item_tags it ON it.item_id = m.item_id "
            "JOIN tags t ON t.id = it.tag_id"
        )
    }
    assert tallies == {"zeta", "alpha"}
    # The recompute really happened, and produced the same answer.
    assert len(spy.calls) == 2
    assert first["clusters"] == second["clusters"]
    assert first["nodes"] == second["nodes"]


def test_an_untagged_cluster_falls_back_to_its_dominant_entity_type(store, spy):
    """Every rung of the label chain is total and deterministic, so a cluster is never
    unlabelled. ``label_source`` is what makes the rung observable."""
    ids = [_entity(store, f"U{i}", entity_type="person") for i in range(2)]
    ids.append(_entity(store, "U2", entity_type="place"))
    _relate(store, ids[0], ids[1])
    _relate(store, ids[1], ids[2])

    body = _ok(store)

    assert [(c["label"], c["label_source"]) for c in body["clusters"]] == [
        ("person", "entity_type")
    ]
    # Vacuity guard: no tags exist at all, so this is the fallback and not a tag that
    # happens to be spelled "person".
    assert store.db.execute("SELECT COUNT(*) AS c FROM item_tags").fetchone()["c"] == 0


# ---------- clause 5: memoized with a debounced invalidation ----------


def test_a_second_call_is_served_from_the_memo_without_reprojecting(store, spy):
    """The projection over a whole library is the expensive step, so the second call must
    not repeat it."""
    a, b = _entity(store, "A"), _entity(store, "B")
    _relate(store, a, b)
    _mention(store, _item(store, "d", vector=[0.3, 0.4, 0.0, 0.0]), a)

    first = _ok(store)
    second = _ok(store)

    assert len(spy.calls) == 1
    assert second["nodes"] == first["nodes"]
    assert second["stale"] is False
    # Positive control: the spy does count, so "1" above is not a spy that never records.
    H._graph_memo.clear()
    _ok(store)
    assert len(spy.calls) == 2


def test_a_mutation_invalidates_the_memo_once_the_debounce_window_is_open(store, monkeypatch, spy):
    """With the window open, a content change is picked up on the next call."""
    monkeypatch.setattr(H, "_GRAPH_MEMO_DEBOUNCE_SECS", 0.0)
    a, b = _entity(store, "A"), _entity(store, "B")
    _relate(store, a, b)
    # Embedded, so "did it recompute" is countable: the projection is skipped entirely for a
    # library with no usable vectors, and a call count of 0 would compare equal to 0.
    _mention(store, _item(store, "d", vector=[0.1, 0.2, 0.0, 0.0]), a)

    before = _ok(store)
    c = _entity(store, "C")
    _relate(store, b, c)
    after = _ok(store)

    assert {n["id"] for n in before["nodes"]} == {a, b}
    assert {n["id"] for n in after["nodes"]} == {a, b, c}
    assert len(spy.calls) == 2
    assert after["stale"] is False
    # Positive control: it is the INVALIDATION that is undebounced here, not the memo. An
    # unchanged library is still served from cache with the window wide open.
    _ok(store)
    assert len(spy.calls) == 2


def test_the_invalidation_is_debounced_rather_than_immediate(store, monkeypatch, spy):
    """The debounce is the mechanism, so it is asserted as behaviour: inside the window a
    changed library is served from cache and FLAGGED stale, which is what keeps a 500-item
    import to one reprojection instead of 500."""
    a, b = _entity(store, "A"), _entity(store, "B")
    _relate(store, a, b)
    _mention(store, _item(store, "d", vector=[0.1, 0.2, 0.0, 0.0]), a)

    fresh = _ok(store)
    c = _entity(store, "C")
    _relate(store, b, c)
    debounced = _ok(store)

    assert fresh["stale"] is False
    # Inside the window: not recomputed, and honest about it.
    assert len(spy.calls) == 1
    assert debounced["stale"] is True
    assert c not in {n["id"] for n in debounced["nodes"]}
    # Positive control: the same mutation IS picked up once the window opens, so the
    # staleness above is a debounce and not a memo that can never invalidate.
    monkeypatch.setattr(H, "_GRAPH_MEMO_DEBOUNCE_SECS", 0.0)
    reopened = _ok(store)
    assert len(spy.calls) == 2
    assert reopened["stale"] is False
    assert c in {n["id"] for n in reopened["nodes"]}


def test_the_memo_key_separates_libraries_and_thinning_params(store, tmp_path, spy):
    """A key that ignores the library serves one home's graph to another; a key that
    ignores the thinning params serves a ``top_k=1`` payload to a ``top_k=6`` caller."""
    a, b = _entity(store, "A"), _entity(store, "B")
    _relate(store, a, b)
    other = KnowledgeStore(os.path.join(tmp_path, "other.db"))
    x, y, z = (_entity(other, n) for n in ("X", "Y", "Z"))
    # A TRIANGLE, not a path. Measured while writing this: on the path X-Y-Z, `top_k=1`
    # keeps both edges, because the union re-admits every leaf's single edge -- so a path
    # fixture cannot show a param difference at all. The triangle's weakest edge (X-Z) is
    # nobody's best, so it is the one a per-node budget of 1 genuinely drops.
    _relate(other, x, y, weight=0.9)
    _relate(other, y, z, weight=0.8)
    _relate(other, x, z, weight=0.7)

    first = _ok(store)
    second = _ok(other)

    # Two libraries, two answers -- the second is not the first's cached payload.
    assert len(first["nodes"]) == 2 and len(second["nodes"]) == 3
    assert {n["id"] for n in second["nodes"]} == {x, y, z}
    # Two params, two answers.
    wide = _ok(other, "?top_k=6")
    narrow = _ok(other, "?top_k=1")
    assert wide["thinning"]["top_k"] == 6 and narrow["thinning"]["top_k"] == 1
    # Vacuity guard: all three edges exist, so a narrower budget CAN drop one.
    assert wide["thinning"]["edges_total"] == 3 and wide["thinning"]["edges_kept"] == 3
    assert narrow["thinning"]["edges_kept"] == 2
    assert (x, z) not in {(e["source"], e["target"]) for e in narrow["edges"]}


# ---------- preserved contract: validation + the empty library ----------


@pytest.mark.parametrize("query", ["?top_k=abc", "?min_weight=xyz", "?top_k=0", "?top_k=-3"])
def test_invalid_thinning_params_are_a_400(store, spy, query):
    """Same shape as the retired ``limit`` validation: a malformed control is refused, not
    silently defaulted."""
    _relate(store, _entity(store, "A"), _entity(store, "B"))

    status, body = _get(store, query)

    assert status == 400
    assert "error" in body
    # Positive control: the same store answers 200 without the bad param, so the 400 is
    # about the param and not about the fixture.
    assert _get(store, "")[0] == 200


def test_an_empty_library_returns_the_same_shape_as_a_populated_one(store, spy):
    """The empty-graph early return is preserved -- and returns every key, so a consumer
    never has to branch on which fields exist."""
    empty = _ok(store)

    assert empty["nodes"] == [] and empty["edges"] == [] and empty["clusters"] == []
    assert empty["thinning"] == {
        "min_weight": H._GRAPH_MIN_WEIGHT,
        "top_k": H._GRAPH_TOP_K_PER_NODE,
        "edges_total": 0,
        "edges_kept": 0,
    }
    assert empty["layout"]["placed"] == 0 and empty["stale"] is False
    # The expensive step is never reached for an empty library.
    assert spy.calls == []
    # Positive control: a populated library answers with the same key set, so the shape
    # above is the real contract rather than an empty-only special case.
    _relate(store, _entity(store, "A"), _entity(store, "B"))
    assert set(_ok(store)) == set(empty)

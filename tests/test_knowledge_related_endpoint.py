"""KL-13 -- GET /api/knowledge/items/{id}/related, served from the similarity-edge table.

What changed, and why these tests exist: the endpoint used to rank neighbours by
``COUNT(DISTINCT entity_id)`` with *no floor at all*, so one incidentally shared entity
ranked level with a genuine topical neighbour and the endpoint answered with *something*
for very nearly any item. KL-13 replaces that with the precomputed similarity edges behind
a real score floor (``knowledge.similarity_min_score``).

The honest consequence, asserted below: an item whose nearest neighbour scores below the
floor now returns ``[]`` where it used to return weak matches -- and that empty answer is
kept distinguishable from an unknown item, which is a 404.

Every negative assertion here carries a positive control seeded in the same test, so an
empty database cannot make an exclusion assertion pass trivially.
"""

from __future__ import annotations

import asyncio
import json
import os
from types import SimpleNamespace

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from personalclaw.config.loader import KnowledgeConfig
from personalclaw.dashboard.handlers import knowledge as H
from personalclaw.knowledge.store import KnowledgeStore

# Read from the shipped config rather than hardcoded, so a taste change to the floor
# (it moved 0.35 -> 0.55 during this atom's assembly, when the pass author's measured
# value won over the config author's estimate) retunes these fixtures instead of
# failing them. A hardcoded copy here is a second source of truth for the same number.
DEFAULT_FLOOR = KnowledgeConfig().similarity_min_score
#: Comfortably above / below the floor whatever it is, so the seeded fixtures below
#: keep meaning "clears it" and "does not".
WELL_ABOVE = min(0.99, DEFAULT_FLOOR + 0.30)
JUST_ABOVE = min(0.98, DEFAULT_FLOOR + 0.05)
WELL_BELOW = max(0.01, DEFAULT_FLOOR - 0.25)
DEFAULT_TOP_K = 8


def _run(coro):
    return asyncio.run(coro)


class _EdgeStore(KnowledgeStore):
    """The real store, plus a stand-in for the ``similar_items`` KL-13a owns.

    Only that one method is substituted: items, mentions, the ``status``/``is_archived``
    columns and ``_serialize_item`` are all the real thing, so everything the handler
    itself is responsible for -- lifecycle filtering, score ordering, over-fetch trimming,
    provenance passthrough, the overlap annotation -- is genuinely exercised.

    The signature and the four returned keys are transcribed from the committed
    implementation, not from a written contract: ``item_id`` is the NEIGHBOUR's id, and
    ``chunk_index``/``neighbour_chunk_index`` are oriented to the item asked about. Getting
    those names wrong is silent -- the handler would find no ``item_id`` on any row, drop
    every neighbour, and answer ``[]`` for everything while every threshold test still
    passed -- so they are worth transcribing rather than assuming.

    The fake applies ``min_score`` and ``limit`` honestly, as the real one does in SQL. That
    alone would leave a hole: a handler passing ``min_score=0.0`` would still look correct,
    because the fake would be thresholding on its own initiative. So it also records the
    kwargs it was handed, letting a test assert the handler passed the *configured* floor.
    """

    def __init__(self, path):
        super().__init__(path)
        self.edges: dict[str, list[dict]] = {}
        self.calls: list[dict] = []

    def seed_edge(self, a, b, score, *, a_chunk=0, b_chunk=0):
        """Register one edge, readable from either leg with the right orientation.

        The real table stores one row per unordered pair under a canonical (min, max)
        ordering and normalises on read, so a caller gets an item's neighbours regardless of
        which side it is stored on -- and always sees its OWN chunk first.
        """
        for src, dst, src_chunk, dst_chunk in ((a, b, a_chunk, b_chunk), (b, a, b_chunk, a_chunk)):
            self.edges.setdefault(src, []).append(
                {
                    "item_id": dst,
                    "score": score,
                    "chunk_index": src_chunk,
                    "neighbour_chunk_index": dst_chunk,
                }
            )

    def similar_items(self, item_id: str, *, limit: int, min_score: float) -> list[dict]:
        self.calls.append({"item_id": item_id, "limit": limit, "min_score": min_score})
        rows = [e for e in self.edges.get(item_id, []) if float(e["score"]) >= min_score]
        rows.sort(key=lambda e: -float(e["score"]))
        return rows[: max(0, int(limit))]


@pytest.fixture
def store(tmp_path, monkeypatch):
    """An isolated store AND an isolated home.

    The handler calls ``AppConfig.load()``, which resolves ``config_dir()`` from
    ``PERSONALCLAW_HOME`` at call time -- without this redirect the suite would read the
    developer's real ``~/.personalclaw``.
    """
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path / "home"))
    return _EdgeStore(os.path.join(tmp_path, "k.db"))


def _req(store, item_id, query: str = ""):
    app = web.Application()
    app["state"] = SimpleNamespace(knowledge_store=store)
    return make_mocked_request(
        "GET",
        f"/api/knowledge/items/{item_id}/related{query}",
        app=app,
        match_info={"id": item_id},
    )


def _get(store, item_id, query: str = ""):
    """Drive the real handler; return (status, decoded body)."""
    resp = _run(H.get_related_items(_req(store, item_id, query)))
    return resp.status, json.loads(resp.body)


def _note(store, title, content="body text here"):
    iid = store.create_typed_item(item_type="note", title=title, content=content)
    store.db.commit()
    return iid


def _link(store, item_id, entity_name):
    """Attach one entity mention to an item (the old ranking signal)."""
    eid = store.add_entity(entity_name, "concept")
    store.add_mention(item_id, eid)
    store.db.commit()
    return eid


def _with_similarity_config(monkeypatch, *, min_score=None, top_k=None):
    """Make ``AppConfig.load()`` report the KL-13 similarity knobs.

    Both fields land with a sibling KL-13 change, so this proves the handler *reads* them
    without depending on that change having merged. Omitting a field leaves the handler's
    defensive ``getattr`` fallback on the same code path.
    """
    from personalclaw.config import loader

    knowledge = SimpleNamespace()
    if min_score is not None:
        knowledge.similarity_min_score = min_score
    if top_k is not None:
        knowledge.similarity_top_k = top_k
    monkeypatch.setattr(
        loader.AppConfig, "load", staticmethod(lambda: SimpleNamespace(knowledge=knowledge))
    )


# ── the threshold: the clause this atom exists for ──


class TestScoreFloor:
    def test_above_floor_neighbours_returned_ordered_by_score(self, store):
        a = _note(store, "A")
        near, mid, far = _note(store, "near"), _note(store, "mid"), _note(store, "far")
        # All three clear the floor — this test is about ORDERING, so a fixture that the floor
        # excludes would silently turn it into a floor test with a shorter list. Derived from
        # the shipped floor for that reason (they were literals until the floor moved).
        s_far, s_mid, s_near = JUST_ABOVE, min(0.97, JUST_ABOVE + 0.10), WELL_ABOVE
        assert s_far < s_mid < s_near, "the fixture scores stopped being strictly ordered"
        store.seed_edge(a, mid, s_mid)
        store.seed_edge(a, near, s_near)
        store.seed_edge(a, far, s_far)

        status, body = _get(store, a)
        assert status == 200
        assert [r["id"] for r in body] == [near, mid, far]
        # approx: the fixture scores are derived by arithmetic on the floor, so exact
        # equality would compare against float noise rather than against the ordering.
        assert [r["score"] for r in body] == pytest.approx([s_near, s_mid, s_far])

    def test_sub_threshold_neighbour_is_excluded(self, store):
        """The test that would have failed before KL-13: no floor existed, so a 0.10
        neighbour was as returnable as a 0.90 one."""
        a = _note(store, "A")
        strong, weak = _note(store, "strong"), _note(store, "weak")
        store.seed_edge(a, strong, 0.90)  # positive control / vacuity guard
        store.seed_edge(a, weak, WELL_BELOW)  # below the shipped default floor

        status, body = _get(store, a)
        assert status == 200
        ids = [r["id"] for r in body]
        assert strong in ids, "positive control missing -- the exclusion below is vacuous"
        assert weak not in ids
        assert len(body) == 1
        # And the floor came from config, not from the store deciding on its own.
        assert store.calls[-1]["min_score"] == DEFAULT_FLOOR

    def test_configured_floor_is_honoured(self, store, monkeypatch):
        """A raised floor must actually reach the store. Both edges clear the default
        default, so a handler ignoring config returns two rows here instead of one."""
        _with_similarity_config(monkeypatch, min_score=0.8)
        a = _note(store, "A")
        strong, middling = _note(store, "strong"), _note(store, "middling")
        store.seed_edge(a, strong, 0.9)
        store.seed_edge(a, middling, 0.5)

        status, body = _get(store, a)
        assert status == 200
        assert [r["id"] for r in body] == [strong]
        assert store.calls[-1]["min_score"] == 0.8

    def test_only_sub_threshold_neighbours_yields_empty_list(self, store):
        """The intended behaviour change: weak matches are no longer returned at all."""
        a = _note(store, "A")
        weak = _note(store, "weak")
        store.seed_edge(a, weak, 0.2)
        # Vacuity guard: an unrelated pair in the SAME database does return a neighbour,
        # so the empty answer below is the floor's doing and not a broken harness.
        c, d = _note(store, "C"), _note(store, "D")
        store.seed_edge(c, d, 0.9)
        assert [r["id"] for r in _get(store, c)[1]] == [d]

        status, body = _get(store, a)
        assert status == 200
        assert body == []

    def test_ranking_no_longer_follows_entity_overlap(self, store):
        """The 'replacing the unthresholded shared-entity COUNT' clause, directly.

        The low-similarity item shares three entities; the high-similarity item shares
        none. The old handler ranked the overlap-heavy item first (and it was the only one
        with a reason to appear at all); the score must now decide.
        """
        a = _note(store, "A")
        overlapping, similar = _note(store, "overlapping"), _note(store, "similar")
        for name in ("Shared1", "Shared2", "Shared3"):
            eid = _link(store, a, name)
            store.add_mention(overlapping, eid)
        store.db.commit()
        store.seed_edge(a, overlapping, JUST_ABOVE)
        store.seed_edge(a, similar, 0.95)

        status, body = _get(store, a)
        assert status == 200
        assert [r["id"] for r in body] == [similar, overlapping]
        assert body[0]["shared_entities"] == 0 and body[1]["shared_entities"] == 3


# ── provenance: a UI can explain *why* two items are related ──


class TestProvenance:
    def test_chunk_provenance_survives_to_the_response(self, store):
        a, b = _note(store, "A"), _note(store, "B")
        store.seed_edge(a, b, 0.77, a_chunk=2, b_chunk=5)

        status, body = _get(store, a)
        assert status == 200
        assert len(body) == 1
        assert body[0]["chunk_index"] == 2
        assert body[0]["neighbour_chunk_index"] == 5
        assert body[0]["score"] == 0.77

    def test_provenance_is_oriented_to_the_item_asked_about(self, store):
        """``chunk_index`` names a chunk of the item in the URL, whichever leg of the pair
        it is stored on. Asking from the other side must swap the two values, not repeat
        them -- a payload that leaked the raw storage legs would answer 2/5 both ways and a
        UI would point at the wrong passage for half its neighbours."""
        a, b = _note(store, "A"), _note(store, "B")
        store.seed_edge(a, b, 0.77, a_chunk=2, b_chunk=5)

        from_a = _get(store, a)[1][0]
        from_b = _get(store, b)[1][0]
        assert (from_a["chunk_index"], from_a["neighbour_chunk_index"]) == (2, 5)
        assert (from_b["chunk_index"], from_b["neighbour_chunk_index"]) == (5, 2)

    def test_shared_entities_still_reported_for_the_frontend_chip(self, store):
        """``shared_entities`` is kept in the payload deliberately.

        ``web/src/pages/knowledge/KnowledgeDetailPage.tsx:319`` renders it as the
        "N shared" chip behind a ``typeof r.shared_entities === 'number'`` guard, so
        dropping the key would have silently emptied a live surface rather than failing
        loudly. It is now descriptive rather than the ranking key.
        """
        a = _note(store, "A")
        with_entity, without_entity = _note(store, "with"), _note(store, "without")
        eid = _link(store, a, "SharedThing")
        store.add_mention(with_entity, eid)
        store.db.commit()
        store.seed_edge(a, with_entity, 0.9)
        store.seed_edge(a, without_entity, 0.8)

        status, body = _get(store, a)
        assert status == 200
        by_id = {r["id"]: r for r in body}
        assert by_id[with_entity]["shared_entities"] == 1
        assert by_id[without_entity]["shared_entities"] == 0


# ── preserved behaviour: lifecycle filtering, the limit clamp, the 400 ──


class TestPreservedBehaviour:
    def test_archived_neighbour_is_filtered(self, store):
        """Filtering is the handler's own (the edge table carries no lifecycle column)."""
        a = _note(store, "A")
        kept, archived = _note(store, "kept"), _note(store, "archived")
        store.seed_edge(a, kept, 0.9)
        store.seed_edge(a, archived, 0.8)
        assert len(_get(store, a)[1]) == 2, "both must start visible, else the drop is vacuous"

        store.update_item(archived, is_archived=1)
        store.db.commit()
        status, body = _get(store, a)
        assert status == 200
        assert [r["id"] for r in body] == [kept]

    def test_non_active_status_neighbour_is_filtered(self, store):
        a = _note(store, "A")
        kept, dropped = _note(store, "kept"), _note(store, "dropped")
        store.seed_edge(a, kept, 0.9)
        store.seed_edge(a, dropped, 0.8)
        assert len(_get(store, a)[1]) == 2, "positive control -- both visible while active"

        store.db.execute("UPDATE items SET status = 'deleted' WHERE id = ?", (dropped,))
        store.db.commit()
        status, body = _get(store, a)
        assert status == 200
        assert [r["id"] for r in body] == [kept]

    def test_archived_neighbour_does_not_shorten_the_list(self, store):
        """Scoring happens in the store, lifecycle filtering here, so the handler
        over-fetches: an archived neighbour must not eat one of the caller's slots."""
        a = _note(store, "A")
        others = [_note(store, f"n{i}") for i in range(4)]
        for i, oid in enumerate(others):
            store.seed_edge(a, oid, 0.9 - i * 0.05)
        store.update_item(others[0], is_archived=1)
        store.db.commit()

        status, body = _get(store, a, "?limit=2")
        assert status == 200
        assert len(body) == 2
        assert others[0] not in [r["id"] for r in body]

    def test_the_item_itself_is_never_its_own_neighbour(self, store):
        a, b = _note(store, "A"), _note(store, "B")
        store.seed_edge(a, a, 1.0)
        store.seed_edge(a, b, 0.9)

        status, body = _get(store, a)
        assert status == 200
        assert [r["id"] for r in body] == [b]

    def test_limit_is_clamped_to_twenty(self, store):
        a = _note(store, "A")
        for i in range(25):
            store.seed_edge(a, _note(store, f"n{i}"), 0.9)

        assert len(_get(store, a, "?limit=100")[1]) == 20
        assert len(_get(store, a, "?limit=5")[1]) == 5
        assert len(_get(store, a, "?limit=0")[1]) == 1  # max(1, ...) floor preserved

    def test_default_limit_comes_from_config(self, store, monkeypatch):
        a = _note(store, "A")
        for i in range(12):
            store.seed_edge(a, _note(store, f"n{i}"), 0.9)

        assert len(_get(store, a)[1]) == DEFAULT_TOP_K  # defensive fallback
        _with_similarity_config(monkeypatch, top_k=3)
        assert len(_get(store, a)[1]) == 3

    def test_non_integer_limit_is_400(self, store):
        a = _note(store, "A")
        store.seed_edge(a, _note(store, "B"), 0.9)
        assert _get(store, a)[0] == 200, "positive control -- the request works but for limit"

        status, body = _get(store, a, "?limit=abc")
        assert status == 400
        assert body["error"] == "invalid limit"


# ── "nothing is close enough" is not "no such item" ──


class TestEmptyVersusMissing:
    def test_existing_item_without_edges_is_empty_list(self, store):
        a = _note(store, "A")
        status, body = _get(store, a)
        assert status == 200
        assert body == []

    def test_unknown_item_is_404(self, store):
        # Vacuity guard: a real item on the same store answers 200, so the 404 below is
        # about the missing id and not a handler that fails every request.
        assert _get(store, _note(store, "real"))[0] == 200

        status, body = _get(store, "no-such-item-id")
        assert status == 404
        assert body["error"] == "item not found"

"""Structural retrieval (KL-18) — the questions similarity answers only by coincidence.

The store already held every relation these tests traverse; what it lacked was a way to ASK.
The five failure shapes, in order of how badly each bites:

* **A structural question answered by a similarity guess.** The load-bearing case:
  ``test_a_link_with_no_shared_vocabulary_is_found_where_similarity_gets_it_wrong`` builds a
  document that links to the target while sharing *none* of its words, and a decoy that
  shares its whole vocabulary and links to nothing. It asserts the traversal returns the
  linker and not the decoy — and, in the same test, MEASURES that the similarity arm returns
  the decoy and not the linker, so "similarity gets this wrong" is a recorded fact rather
  than a claim in a docstring.
* **An empty structural result that silently becomes a similarity result.** A fall-back makes
  "nothing links here" indistinguishable from "here are some things that read alike".
  ``test_no_inbound_link_is_a_named_reason_not_a_similarity_fallback`` stocks the corpus with
  strong semantic neighbours of the target first, so a fall-back would have plenty to return,
  and asserts the answer is empty with ``no_such_relation`` — including on the composed path
  where a ``rank_query`` is supplied.
* **An answer that cannot justify itself.** ``test_every_hit_carries_the_chain_that_reached_it``
  asserts the exact step sequence of a two-hop path, not merely that a path is non-empty: a
  return shape that dropped the path, or kept only the last step, fails it.
* **Ranking that decides membership.** The composition contract is restrict-then-rank in one
  declared order. ``test_a_subtree_restriction_applies_before_the_semantic_rank`` puts the
  single best semantic match OUTSIDE the subtree and asserts it never appears, that the
  ranked hit set is a permutation of the structural one, and that the order actually changed
  (otherwise the test would pass against a ranker that does nothing).
* **A traversal that trusts its rows.** ``item_citations`` carries no foreign key on purpose,
  so a citation can outlive the item it names. ``test_a_dangling_link_is_a_dead_end`` asserts
  the unresolvable end is dropped rather than surfacing as a titleless hit.

Every negative assertion carries a positive control in the same test: a query that returns
nothing must fail loudly instead of passing on an empty corpus.
"""

from __future__ import annotations

import asyncio

import pytest

from personalclaw.knowledge import structural as S

# ── fixtures ────────────────────────────────────────────────────────────────────


@pytest.fixture()
def store(tmp_path):
    """A store under ``tmp_path``, with its OWN MonkeyPatch.

    Its own, not the shared ``monkeypatch`` fixture: measured on KL-14, sharing it means a
    test calling ``monkeypatch.undo()`` for something of its own also undoes this ``setenv``,
    and the next store open lands in the developer's real ``~/.personalclaw``. Home isolation
    must not be revocable by a test.
    """
    mp = pytest.MonkeyPatch()
    mp.setenv("PERSONALCLAW_HOME", str(tmp_path))
    from personalclaw.knowledge.store import KnowledgeStore, knowledge_db_path

    try:
        yield KnowledgeStore(str(knowledge_db_path()))
    finally:
        mp.undo()


def _item(store, title: str, content: str = "", *, tags=None) -> str:
    item_id = store.create_typed_item(
        item_type="note", title=title, content=content or f"body of {title}", tags=tags
    )
    assert item_id, "fixture item was not created"
    return item_id


def _relate(store, src: str, tgt: str, relation: str, *, confidence: float = 1.0) -> None:
    store.db.execute(
        "INSERT OR REPLACE INTO item_relations "
        "(source_item_id, target_item_id, relation_type, confidence, provenance, created_at) "
        "VALUES (?, ?, ?, ?, 'extracted', '2026-01-01T00:00:00')",
        (src, tgt, relation, confidence),
    )
    store.db.commit()


def _cite(store, citing: str, cited: str, marker: int = 1) -> None:
    store.db.execute(
        "INSERT OR REPLACE INTO item_citations (item_id, marker, source_item_id, chunk_index, "
        "excerpt) VALUES (?, ?, ?, -1, 'quoted line')",
        (citing, marker, cited),
    )
    store.db.commit()


def _tag_id(store, name: str) -> int:
    row = store.db.execute("SELECT id FROM tags WHERE name = ?", (name,)).fetchone()
    assert row, f"tag {name!r} was not created by the fixture"
    return int(row["id"])


def _ids(answer: S.StructuralAnswer) -> set[str]:
    return {h.item_id for h in answer.hits}


# ── 1. the load-bearing case ────────────────────────────────────────────────────

# Two disjoint vocabularies. Nothing in the second appears in the first, so a
# keyword/lexical arm scores the linker at exactly zero against the target's words.
_TARGET_TEXT = "kubernetes ingress controller nginx annotation rewrite target namespace"
_ALIEN_TEXT = "sourdough levain hydration autolyse bulk fermentation banneton crumb"


def test_a_link_with_no_shared_vocabulary_is_found_where_similarity_gets_it_wrong(store):
    """ "What links here" is a graph question. Similarity answers it only by coincidence.

    `linker` cites the target and shares none of its words. `decoy` shares the target's whole
    vocabulary and links to nothing. A similarity arm ranks the decoy and cannot see the
    linker at all; the traversal returns exactly the opposite, which is the correct answer.
    """
    target = _item(store, "Ingress rewrite rules", _TARGET_TEXT)
    linker = _item(store, "Levain schedule", _ALIEN_TEXT)
    decoy = _item(store, "Nginx annotation notes", _TARGET_TEXT + " extra")
    _cite(store, linker, target)

    answer = S.StructuralRetriever(store).query(S.LINKS_TO, origin=target)
    assert _ids(answer) == {linker}, "traversal must return the linker and only the linker"
    assert decoy not in _ids(answer)
    assert answer.hits[0].path[0].edge == "cites"

    # The measurement that makes "similarity gets this wrong" a fact. Both arms of the
    # claim, so neither half can be satisfied vacuously.
    scores = S.lexical_scores(_TARGET_TEXT, {linker: _ALIEN_TEXT, decoy: _TARGET_TEXT})
    assert scores[linker] == 0.0, "the linker must share no vocabulary with the target"
    assert scores[decoy] > scores[linker], "the decoy must out-score the linker on similarity"

    from personalclaw.knowledge.retrieval import HybridRetriever

    ranked = [r["id"] for r in HybridRetriever(store).search(_TARGET_TEXT, limit=10)]
    assert decoy in ranked, "positive control: the similarity arm must find the decoy"
    assert linker not in ranked, "the similarity arm cannot reach the linker — that is the point"


# ── 2. an empty structural result is a statement ────────────────────────────────


def test_no_inbound_link_is_a_named_reason_not_a_similarity_fallback(store):
    """Empty means "no such relation", never "here are some things that read alike"."""
    target = _item(store, "Ingress rewrite rules", _TARGET_TEXT)
    # Stock the corpus with strong semantic neighbours, so a fall-back would have plenty
    # to return and an empty answer cannot be an artefact of an empty library.
    neighbours = [_item(store, f"Nginx notes {i}", _TARGET_TEXT) for i in range(3)]

    retriever = S.StructuralRetriever(store)
    answer = retriever.query(S.LINKS_TO, origin=target)
    assert answer.hits == ()
    assert answer.empty_reason == S.NO_SUCH_RELATION
    assert "no such relation" in S.empty_message(answer.empty_reason)
    assert not any(n in _ids(answer) for n in neighbours)

    # The composed path must not become the fall-back either: supplying a rank_query that
    # every neighbour would match cannot conjure a hit.
    composed = retriever.query(S.LINKS_TO, origin=target, rank_query=_TARGET_TEXT)
    assert composed.hits == ()
    assert composed.empty_reason == S.NO_SUCH_RELATION
    assert composed.composition == S.STRUCTURE_ONLY, "no rank runs when nothing survived"

    # Positive control: the similarity arm on that same text DOES return items, so "empty"
    # above is a property of the traversal and not of the corpus.
    from personalclaw.knowledge.retrieval import HybridRetriever

    assert HybridRetriever(store).search(_TARGET_TEXT, limit=10), "corpus is not searchable"


def test_a_missing_origin_is_distinguished_from_a_missing_relation(store):
    """ "No such item" and "nothing links here" are different facts and get different reasons."""
    present = _item(store, "Present", "content")
    retriever = S.StructuralRetriever(store)
    assert retriever.query(S.LINKS_TO, origin="kn_nope").empty_reason == S.NO_SUCH_ITEM
    assert retriever.query(S.LINKS_TO, origin=present).empty_reason == S.NO_SUCH_RELATION
    assert retriever.query(S.TAG_SUBTREE, origin="no-such-tag").empty_reason == S.NO_SUCH_TAG
    assert retriever.query("invented_verb", origin=present).empty_reason == S.BAD_REQUEST


# ── 3. the answer carries its own justification ─────────────────────────────────


def test_every_hit_carries_the_chain_that_reached_it(store):
    """A two-hop hit's path is the two edges that reached it, in order — not just the last."""
    a = _item(store, "Service A")
    b = _item(store, "Library B")
    c = _item(store, "Kernel C")
    _relate(store, a, b, "depends_on")
    _relate(store, b, c, "depends_on")

    answer = S.StructuralRetriever(store).query(S.DEPENDS_ON, origin=a, depth=2)
    by_id = {h.item_id: h for h in answer.hits}
    assert set(by_id) == {b, c}

    assert [(s.from_ref, s.edge, s.to_ref) for s in by_id[b].path] == [
        (S.item_ref(a), "relation:depends_on", S.item_ref(b))
    ]
    assert [(s.from_ref, s.edge, s.to_ref) for s in by_id[c].path] == [
        (S.item_ref(a), "relation:depends_on", S.item_ref(b)),
        (S.item_ref(b), "relation:depends_on", S.item_ref(c)),
    ]
    # The rendered justification names every node on the way, so a consumer can show WHY
    # without a second query.
    why = by_id[c].to_dict()["why"]
    for ref in (S.item_ref(a), S.item_ref(b), S.item_ref(c)):
        assert ref in why, f"{ref} missing from the rendered path: {why!r}"
    assert by_id[c].depth == 2
    assert "why:" in S.render_answer(answer)


def test_depends_on_is_transitive_where_the_single_hop_relations_read_is_not(store):
    """Traversal is the point: the detail page's one-hop relations read cannot see C."""
    a = _item(store, "Service A")
    b = _item(store, "Library B")
    c = _item(store, "Kernel C")
    _relate(store, a, b, "depends_on")
    _relate(store, b, c, "depends_on")

    retriever = S.StructuralRetriever(store)
    assert _ids(retriever.query(S.DEPENDS_ON, origin=a, depth=1)) == {b}
    assert _ids(retriever.query(S.DEPENDS_ON, origin=a, depth=2)) == {b, c}

    # The one-hop shape `/api/knowledge/items/{id}/relations` serves, for contrast.
    one_hop = {
        r["target_item_id"]
        for r in store.db.execute(
            "SELECT target_item_id FROM item_relations WHERE source_item_id = ?", (a,)
        )
    }
    assert one_hop == {b}, "positive control: the single-hop read stops at B"


def test_depends_on_does_not_widen_to_other_relation_verbs(store):
    """The verb means the verb. `part_of` is a different question and is not folded in."""
    a = _item(store, "Service A")
    dep = _item(store, "Declared dependency")
    part = _item(store, "Parent document")
    _relate(store, a, dep, "depends_on")
    _relate(store, a, part, "part_of")

    retriever = S.StructuralRetriever(store)
    assert _ids(retriever.query(S.DEPENDS_ON, origin=a)) == {dep}
    widened = retriever.query(S.DEPENDS_ON, origin=a, relations=("depends_on", "part_of"))
    assert _ids(widened) == {dep, part}, "an explicit widening is honoured"


def test_links_to_reads_both_typed_relations_and_citations(store):
    """Inbound means every kind of inbound link the store holds, labelled by which."""
    target = _item(store, "Target")
    superseder = _item(store, "Newer version")
    citer = _item(store, "Synthesis")
    unrelated = _item(store, "Unrelated")
    _relate(store, superseder, target, "supersedes")
    _cite(store, citer, target, marker=2)

    answer = S.StructuralRetriever(store).query(S.LINKS_TO, origin=target)
    edges = {h.item_id: h.path[-1].edge for h in answer.hits}
    assert edges == {superseder: "relation:supersedes", citer: "cites"}
    assert unrelated not in edges
    # Direction is recorded, so a renderer can say "supersedes THIS" rather than guessing.
    assert all(h.path[-1].direction == "inbound" for h in answer.hits)


# ── 4. tag subtree ──────────────────────────────────────────────────────────────


def test_tag_subtree_descends_the_taxonomy_and_names_the_intermediate_tag(store):
    """A hit two levels down says which child tag put it there."""
    top = _item(store, "Infra overview", tags=["infra"])
    mid = _item(store, "Cluster notes", tags=["k8s"])
    leaf = _item(store, "Ingress notes", tags=["ingress"])
    outside = _item(store, "Bread notes", tags=["baking"])
    store.set_tag_parent(_tag_id(store, "k8s"), _tag_id(store, "infra"))
    store.set_tag_parent(_tag_id(store, "ingress"), _tag_id(store, "k8s"))

    answer = S.StructuralRetriever(store).query(S.TAG_SUBTREE, origin="infra", depth=3)
    assert _ids(answer) == {top, mid, leaf}
    assert outside not in _ids(answer)

    by_id = {h.item_id: h for h in answer.hits}
    assert [(s.from_ref, s.edge, s.to_ref) for s in by_id[leaf].path] == [
        (S.tag_ref("infra"), "tag:child_of", S.tag_ref("k8s")),
        (S.tag_ref("k8s"), "tag:child_of", S.tag_ref("ingress")),
        (S.tag_ref("ingress"), "tag:tagged", S.item_ref(leaf)),
    ]
    assert [s.edge for s in by_id[top].path] == ["tag:tagged"]

    # Depth bounds the descent rather than being decoration, and counts hops in the recorded
    # path (the membership step into the item is one of them) so it means the same thing here
    # as it does for `links_to`.
    retriever = S.StructuralRetriever(store)
    assert _ids(retriever.query(S.TAG_SUBTREE, origin="infra", depth=1)) == {top}
    assert _ids(retriever.query(S.TAG_SUBTREE, origin="infra", depth=2)) == {top, mid}
    assert by_id[leaf].depth == len(by_id[leaf].path), "depth is the length of the path"


# ── 5. changed since ────────────────────────────────────────────────────────────


def test_changed_since_returns_only_what_moved_after_the_stamp(store):
    """The justification is the timestamp that satisfied the predicate."""
    old = _item(store, "Old note")
    new = _item(store, "New note")
    store.db.execute("UPDATE items SET updated_at = ? WHERE id = ?", ("2026-01-01T00:00:00", old))
    store.db.execute("UPDATE items SET updated_at = ? WHERE id = ?", ("2026-06-01T00:00:00", new))
    store.db.commit()

    retriever = S.StructuralRetriever(store)
    answer = retriever.query(S.CHANGED_SINCE, since="2026-03-01T00:00:00")
    assert _ids(answer) == {new}
    assert answer.hits[0].path[0].detail["updated_at"] == "2026-06-01T00:00:00"
    assert answer.hits[0].path[0].detail["since"] == "2026-03-01T00:00:00"

    # Positive control + the empty reason, so "nothing changed" is not indistinguishable
    # from a broken query.
    assert _ids(retriever.query(S.CHANGED_SINCE, since="2025-01-01T00:00:00")) == {old, new}
    nothing = retriever.query(S.CHANGED_SINCE, since="2027-01-01T00:00:00")
    assert nothing.hits == () and nothing.empty_reason == S.NO_CHANGE_SINCE


# ── 6. contradictions ───────────────────────────────────────────────────────────


def test_contradictions_pair_each_item_with_its_counterpart(store):
    """A contradiction is a pair, so each hit names the item it conflicts with."""
    left = _item(store, "Deploy on Friday")
    right = _item(store, "Never deploy on Friday")
    p = _item(store, "Old runbook")
    q = _item(store, "New runbook")
    _relate(store, left, right, "contradicts", confidence=0.8)
    _relate(store, p, q, "supersedes")

    retriever = S.StructuralRetriever(store)
    corpus = retriever.query(S.CONTRADICTIONS)
    assert _ids(corpus) == {right}, "one hit per edge, reported at its target"
    assert corpus.hits[0].path[0].from_ref == S.item_ref(left)
    assert corpus.hits[0].path[0].detail["confidence"] == pytest.approx(0.8)
    assert q not in _ids(corpus), "a `supersedes` edge is not a contradiction"

    # Item-scoped reads the counterpart from whichever leg the item sits on.
    assert _ids(retriever.query(S.CONTRADICTIONS, origin=left)) == {right}
    assert _ids(retriever.query(S.CONTRADICTIONS, origin=right)) == {left}
    clean = retriever.query(S.CONTRADICTIONS, origin=p)
    assert clean.hits == () and clean.empty_reason == S.NO_CONTRADICTION


# ── 7. composition: restrict first, rank second ─────────────────────────────────


def test_a_subtree_restriction_applies_before_the_semantic_rank(store):
    """The declared order, measured three ways: membership, permutation, and reordering."""
    rank_query = "rollback procedure steps"
    weak = _item(store, "Cluster inventory", "a list of node names and roles", tags=["k8s"])
    strong = _item(store, "Rollback procedure", "rollback procedure steps in order", tags=["k8s"])
    # The single best match for the query sits OUTSIDE the subtree. A rank-then-filter
    # design surfaces it (or drops a subtree member to make room for it); restrict-first
    # cannot see it at all.
    outsider = _item(
        store, "Rollback procedure steps", "rollback procedure steps rollback", tags=["baking"]
    )

    retriever = S.StructuralRetriever(store)
    structural = retriever.query(S.TAG_SUBTREE, origin="k8s", limit=10)
    composed = retriever.query(S.TAG_SUBTREE, origin="k8s", limit=10, rank_query=rank_query)

    assert outsider not in _ids(composed), "the restriction runs FIRST"
    assert _ids(composed) == _ids(structural), "ranking is a permutation, never a filter"
    assert composed.composition == S.RESTRICT_THEN_RANK
    assert composed.rank_mode == S.RANK_LEXICAL
    assert [h.item_id for h in composed.hits] == [strong, weak], "ranked, best first"

    # The rank must actually DRIVE the order. Comparing against the structure-only order
    # cannot show that — item ids are random UUIDs, so the unranked order coincides with the
    # ranked one about half the time and the assertion would pass by luck. Instead: the same
    # structural set under a second query that favours the other member must FLIP.
    flipped = retriever.query(
        S.TAG_SUBTREE, origin="k8s", limit=10, rank_query="node names and roles inventory"
    )
    assert _ids(flipped) == _ids(structural), "still a permutation"
    assert [h.item_id for h in flipped.hits] == [weak, strong], "a different query, a new order"
    assert all(h.score is not None for h in composed.hits)
    assert all(h.score is None for h in structural.hits), "an unranked hit has no score"
    # Every hit keeps its path through the composition — the justification is not a
    # casualty of ranking.
    assert all(h.path for h in composed.hits)

    # Positive control that the outsider really is the better semantic match, so its
    # absence above is the restriction and not a weak query.
    from personalclaw.knowledge.retrieval import HybridRetriever

    ranked = [r["id"] for r in HybridRetriever(store).search(rank_query, limit=10)]
    assert ranked and ranked[0] == outsider, f"expected the outsider to rank first, got {ranked}"


def test_the_composed_order_is_reproducible_and_reported(store):
    """Same store, same query, same order — and the answer says which order produced it."""
    _item(store, "Alpha rollback", "rollback rollback", tags=["ops"])
    _item(store, "Beta rollback", "rollback", tags=["ops"])
    _item(store, "Gamma", "unrelated words entirely", tags=["ops"])

    retriever = S.StructuralRetriever(store)
    runs = [
        [
            h.item_id
            for h in retriever.query(S.TAG_SUBTREE, origin="ops", rank_query="rollback").hits
        ]
        for _ in range(3)
    ]
    assert runs[0] == runs[1] == runs[2]
    payload = retriever.query(S.TAG_SUBTREE, origin="ops", rank_query="rollback").to_dict()
    assert payload["composition"] == S.RESTRICT_THEN_RANK
    assert payload["rank_mode"] == S.RANK_LEXICAL
    assert payload["hits"][0]["why"], "the serialised hit carries its justification"


# ── 8. traversal hygiene ────────────────────────────────────────────────────────


def test_a_dangling_link_is_a_dead_end(store):
    """`item_citations` carries no foreign key by design, so a citation can outlive its item.

    The traversal drops the unresolvable end rather than emitting a titleless hit. Noted, not
    fixed here: `item_relations` has no ON DELETE CASCADE on this branch either.
    """
    target = _item(store, "Target")
    live = _item(store, "Live citer")
    _cite(store, live, target, marker=1)
    _cite(store, "kn_deleted_long_ago", target, marker=2)

    answer = S.StructuralRetriever(store).query(S.LINKS_TO, origin=target)
    assert _ids(answer) == {live}, "the vanished citer is a dead end, not a hit"


def test_an_archived_item_is_not_traversed_unless_asked_for(store):
    archived = _item(store, "Archived linker")
    target = _item(store, "Target")
    _relate(store, archived, target, "derived_from")
    store.db.execute("UPDATE items SET is_archived = 1 WHERE id = ?", (archived,))
    store.db.commit()

    retriever = S.StructuralRetriever(store)
    assert retriever.query(S.LINKS_TO, origin=target).empty_reason == S.NO_SUCH_RELATION
    assert _ids(retriever.query(S.LINKS_TO, origin=target, include_archived=True)) == {archived}


def test_a_relation_cycle_terminates_and_records_the_shortest_path(store):
    a = _item(store, "A")
    b = _item(store, "B")
    _relate(store, a, b, "depends_on")
    _relate(store, b, a, "depends_on")

    answer = S.StructuralRetriever(store).query(S.DEPENDS_ON, origin=a, depth=4)
    assert _ids(answer) == {b}, "the origin is never its own hit"
    assert len(answer.hits[0].path) == 1, "the shortest path wins, not the loop"


# ── 9. the agent-facing surface ─────────────────────────────────────────────────


def test_the_agent_tool_exposes_every_structural_verb():
    """The model must be able to ASK a precise question, so the verbs are in the schema.

    The enum is read from the retriever's own vocabulary, so this also guards the drift a
    hand-copied list would allow.
    """
    from personalclaw.agents.native import builtin_tools as BT

    provider = BT.NativeBuiltinToolProvider(cwd="/tmp")
    tools = asyncio.get_event_loop().run_until_complete(provider.list_tools())
    tool = next((t for t in tools if t.name == "knowledge_structural"), None)
    assert tool is not None, "knowledge_structural is not registered"
    assert tool.parameters["properties"]["verb"]["enum"] == list(S.STRUCTURAL_VERBS)
    assert set(tool.parameters["properties"]) >= {"origin", "since", "depth", "limit", "rank_query"}
    assert BT._CATEGORY_OF["knowledge_structural"] == "knowledge"
    # The description must steer the model AWAY from answering a structural question with a
    # similarity search, which is the misuse the atom exists to remove.
    assert "similarity" in tool.description.lower()


@pytest.mark.asyncio
async def test_the_tool_refuses_a_verb_it_cannot_answer_rather_than_reporting_absence():
    """An unanswerable request and a genuinely absent relation are different facts."""
    from personalclaw.agents.native import builtin_tools as BT

    provider = BT.NativeBuiltinToolProvider(cwd="/tmp")
    bad_verb = await provider.invoke("knowledge_structural", {"verb": "vibes"})
    assert not bad_verb.success and "unknown verb" in (bad_verb.error or "")
    no_origin = await provider.invoke("knowledge_structural", {"verb": "links_to"})
    assert not no_origin.success and "origin" in (no_origin.error or "")
    no_since = await provider.invoke("knowledge_structural", {"verb": "changed_since"})
    assert not no_since.success and "since" in (no_since.error or "")


def test_render_answer_states_the_reason_when_there_is_nothing_to_show():
    empty = S.StructuralAnswer(
        S.LINKS_TO, S.item_ref("kn_x"), S.STRUCTURE_ONLY, empty_reason=S.NO_SUCH_RELATION
    )
    text = S.render_answer(empty)
    assert "no such relation" in text
    assert "links_to" in text

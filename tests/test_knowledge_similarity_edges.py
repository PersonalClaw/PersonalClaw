"""Item-similarity edges (KL-13) — the schema, the store queries and the kNN pass.

The store had no similarity-derived edge of any kind: the chunk vector index was queried at
read time and never materialised. These tests target the five ways a materialised similarity
graph goes quietly wrong, in order of how badly each bites:

* **A recompute that eats its neighbours' work.** Canonical (min, max) storage means an edge
  item B discovered is stored with A as its *source* whenever A < B, so a
  `DELETE WHERE source_item_id = A` before re-inserting destroys B's finding — and the
  both-legs delete destroys every neighbour's. `test_recompute_keeps_the_edge_the_other_pass_found`
  runs it in BOTH id orderings, because a fix that only works when A > B passes a single-ordering
  test by luck.
* **A backlog that cannot drain.** Keyed on "has no edges" it never terminates: an item may
  legitimately have no neighbour above the floor, so it never gains an edge, never leaves the
  backlog, and the host — which re-invokes a batched pass until it returns 0 — spends every
  sub-batch on the head of the library. `test_drain_reaches_zero_even_when_no_edge_is_ever_written`
  is that assertion, and it is deliberately run with a floor nothing clears.
* **A per-pass truncation posing as a cap.** Top-K bounds what ONE item writes and says
  nothing about 500 items each writing one edge at the same popular document.
  `test_degree_cap_bounds_inbound_edges_globally` measures the uncapped degree first, so the
  capped assertion cannot pass on a setup that never exceeded the cap.
* **A roll-up that is not MAX.** `test_item_pair_score_is_the_max_of_its_chunk_pairs` picks
  chunk scores whose mean and sum are both distinguishable from the max, and keeps the floor
  low enough that a mean implementation is not rescued by filtering.
* **CASCADE as decoration.** SQLite ignores foreign keys unless `PRAGMA foreign_keys=ON` is
  set per connection, so DDL that reads correctly can enforce nothing.
  `test_deleting_an_item_cascades_its_edges_with_no_application_code` asserts the pragma AND
  deletes the item with raw SQL, bypassing `delete_item` entirely.

Every negative assertion carries a positive control in the same test, so a query that returns
nothing fails loudly instead of passing on an empty set.
"""

from __future__ import annotations

import math

import pytest

from personalclaw.knowledge import similarity_edges

# ── fixtures ────────────────────────────────────────────────────────────────────


@pytest.fixture()
def store(tmp_path):
    """A store at the SAME path `similarity_pass` will open for itself.

    The pass takes no store argument (the maintenance host's shape is `(*, batch_size)`), so
    it resolves `knowledge_db_path()` -> `config_dir()` -> `PERSONALCLAW_HOME` on every call.
    Pointing that at `tmp_path` is what keeps the real `~/.personalclaw` out of this suite.

    🔴 Its OWN `MonkeyPatch`, not the shared `monkeypatch` fixture. Measured on KL-14: sharing
    it means a test that calls `monkeypatch.undo()` for something of its own also undoes this
    `setenv`, and the next `similarity_pass` opens and rewrites the developer's real library.
    Home isolation must not be revocable by a test.
    """
    mp = pytest.MonkeyPatch()
    mp.setenv("PERSONALCLAW_HOME", str(tmp_path))
    from personalclaw.knowledge.store import KnowledgeStore, knowledge_db_path

    try:
        yield KnowledgeStore(str(knowledge_db_path()))
    finally:
        mp.undo()


# ── vector helpers ──────────────────────────────────────────────────────────────
#
# Unit vectors with EXACTLY known cosines, so an assertion can name the number it expects
# instead of eyeballing whatever an embedder happened to produce. `floats_to_bytes` packs
# float32, so comparisons carry a tolerance for that round-trip and nothing more.

_DIM = 4


def _unit(*components: float) -> list[float]:
    """Zero-padded unit vector of length `_DIM` (or longer if given more components)."""
    vec = list(components) + [0.0] * max(0, _DIM - len(components))
    norm = math.sqrt(sum(x * x for x in vec))
    assert norm > 0, "degenerate test vector"
    return [x / norm for x in vec]


def _axis_pair(cos_to_first: float, slot: int, dim: int) -> list[float]:
    """A unit vector whose cosine with `[1, 0, ...]` is exactly *cos_to_first*, and whose
    cosine with any other vector built the same way at a different *slot* is its square.

    That second property is what lets a fan-in test give many sources the same similarity to
    one popular item while keeping them dissimilar to EACH OTHER, so the degree the cap has to
    bound is purely inbound rather than an artefact of a mutually-similar cluster.
    """
    vec = [0.0] * dim
    vec[0] = cos_to_first
    vec[slot] = math.sqrt(max(0.0, 1.0 - cos_to_first * cos_to_first))
    return vec


def _item(store, title: str) -> str:
    item_id = store.create_typed_item(item_type="note", title=title, content=f"body of {title}")
    assert item_id, "fixture item was not created"
    return item_id


def _embed_chunks(store, item_id: str, vectors: list[list[float]]) -> None:
    """Give *item_id* one embedded chunk per vector, chunk_index 0..N-1.

    Goes through `replace_chunks` rather than raw INSERTs so the ANN index is written the way
    ingest writes it — otherwise the ANN arm would have nothing to find and every test would
    silently measure the exact-scan fallback instead.
    """
    from personalclaw.knowledge.chunking import Chunk
    from personalclaw.knowledge.embedder import floats_to_bytes

    chunks = [
        Chunk(
            text=f"chunk {i} of {item_id}",
            section=None,
            line_start=1,
            line_end=1,
            chunk_index=i,
            embedding=floats_to_bytes(vec),
        )
        for i, vec in enumerate(vectors)
    ]
    written = store.replace_chunks(item_id, chunks)
    assert written == len(vectors), "fixture chunks were not written"


def _edge_rows(store) -> list[dict]:
    return [
        dict(r)
        for r in store.db.execute(
            "SELECT source_item_id, target_item_id, score, source_chunk_index, "
            "target_chunk_index, by_source, by_target FROM item_similarity_edges "
            "ORDER BY source_item_id, target_item_id"
        )
    ]


def _pairs(store) -> set[tuple[str, str]]:
    return {(r["source_item_id"], r["target_item_id"]) for r in _edge_rows(store)}


# ── the table: CASCADE is enforced, not decorative ──────────────────────────────


def test_the_connection_has_foreign_keys_on(store):
    """The precondition every CASCADE assertion below rests on.

    SQLite defaults `foreign_keys` to OFF and it is a per-CONNECTION pragma, so
    `ON DELETE CASCADE` in DDL enforces nothing unless it is set. Asserting it separately
    means a future change that drops the pragma fails HERE with an obvious reason, instead of
    turning every cascade test into a silent no-op.
    """
    assert store.db.execute("PRAGMA foreign_keys").fetchone()[0] == 1

    legs = {r[3]: r[6] for r in store.db.execute("PRAGMA foreign_key_list(item_similarity_edges)")}
    assert legs == {
        "source_item_id": "CASCADE",
        "target_item_id": "CASCADE",
    }, f"both legs must cascade, got {legs}"


def test_deleting_an_item_cascades_its_edges_with_no_application_code(store):
    """A raw `DELETE FROM items` — not `delete_item` — must take the edges with it.

    Going through `delete_item` would prove only that some Python line deletes edges, which is
    not what "schema-enforced" claims. This deletes the row with SQL the application never
    writes, so the only thing that can remove the edge is the foreign key.
    """
    a, b, c = _item(store, "A"), _item(store, "B"), _item(store, "C")
    store.upsert_similarity_edges(
        [
            {
                "source_item_id": a,
                "target_item_id": b,
                "score": 0.9,
                "source_chunk_index": 0,
                "target_chunk_index": 0,
            },
            {
                "source_item_id": b,
                "target_item_id": c,
                "score": 0.8,
                "source_chunk_index": 0,
                "target_chunk_index": 0,
            },
        ]
    )
    assert store.count_similarity_edges() == 2, "positive control: edges must exist to vanish"

    # Whichever leg `a` landed on after canonicalisation, deleting it must clear its edge.
    store.db.execute("DELETE FROM items WHERE id = ?", (a,))
    store.db.commit()

    remaining = _pairs(store)
    assert not any(a in pair for pair in remaining), f"edge survived the cascade: {remaining}"
    assert (
        store.count_similarity_edges() == 1
    ), "the cascade must remove only the deleted item's edges, not the table"


def test_deleting_an_item_cascades_its_sweep_marker(store):
    """The sweep marker dies with its item too, so a re-created id is not born pre-swept and
    orphan markers cannot accumulate for the life of the library."""
    a = _item(store, "A")
    store.record_similarity_sweep(a)
    assert (
        store.db.execute(
            "SELECT COUNT(*) FROM similarity_sweeps WHERE item_id = ?", (a,)
        ).fetchone()[0]
        == 1
    ), "positive control: the marker must exist first"

    store.db.execute("DELETE FROM items WHERE id = ?", (a,))
    store.db.commit()

    assert store.db.execute("SELECT COUNT(*) FROM similarity_sweeps").fetchone()[0] == 0


# ── the roll-up rule: MAX, not mean and not sum ─────────────────────────────────


def test_item_pair_score_is_the_max_of_its_chunk_pairs(store):
    """One chunk in A against two chunks in B scoring 0.9 and 0.3.

    max = 0.9, mean = 0.6, sum = 1.2 — three distinguishable numbers, and the floor is set to
    0.1 so BOTH chunk pairs clear it. Filtering the weak pair out would make mean and max
    agree and the test would pass for the wrong implementation.
    """
    a, b = _item(store, "A"), _item(store, "B")
    _embed_chunks(store, a, [_unit(1.0, 0.0)])
    _embed_chunks(
        store,
        b,
        [
            _unit(0.9, math.sqrt(1 - 0.81)),  # cosine 0.9 with A's chunk
            _unit(0.3, math.sqrt(1 - 0.09)),  # cosine 0.3 with A's chunk
        ],
    )

    written = similarity_edges.recompute_item_edges(store, a, top_k=5, min_score=0.1)
    assert written == 1, "positive control: the pair must be found at all"

    row = _edge_rows(store)[0]
    assert row["score"] == pytest.approx(0.9, abs=1e-5), (
        f"roll-up must keep the MAX chunk-pair score; mean would be 0.6, sum 1.2, got "
        f"{row['score']}"
    )


def test_provenance_names_the_winning_chunk_pair(store):
    """The edge stores WHICH chunks won, oriented to the ids it stores, so it can explain
    itself. A score with the wrong chunk indices is worse than no provenance at all."""
    a, b = _item(store, "A"), _item(store, "B")
    # A's chunk 1 is the one that matches; A's chunk 0 is orthogonal to everything in B.
    _embed_chunks(store, a, [_unit(0.0, 0.0, 1.0), _unit(1.0, 0.0)])
    # B's chunk 2 is the match; 0 and 1 are weak.
    _embed_chunks(
        store,
        b,
        [
            _unit(0.2, math.sqrt(1 - 0.04)),
            _unit(0.3, math.sqrt(1 - 0.09)),
            _unit(0.95, math.sqrt(1 - 0.9025)),
        ],
    )

    assert similarity_edges.recompute_item_edges(store, a, top_k=5, min_score=0.5) == 1

    row = _edge_rows(store)[0]
    a_index = row["source_chunk_index"] if row["source_item_id"] == a else row["target_chunk_index"]
    b_index = row["target_chunk_index"] if row["source_item_id"] == a else row["source_chunk_index"]
    assert (a_index, b_index) == (
        1,
        2,
    ), f"provenance must name A's chunk 1 and B's chunk 2, got A={a_index} B={b_index}"


# ── canonical ordering ──────────────────────────────────────────────────────────


def test_edges_are_stored_in_canonical_min_max_order(store):
    """Driven from the HIGHER id, the stored row must still be (min, max).

    Driving from the lower id would satisfy a no-op implementation, so this deliberately picks
    the recompute whose natural output is the wrong way round.
    """
    a, b = _item(store, "A"), _item(store, "B")
    lo, hi = min(a, b), max(a, b)
    _embed_chunks(store, lo, [_unit(1.0, 0.0)])
    _embed_chunks(store, hi, [_unit(0.95, math.sqrt(1 - 0.9025))])

    assert similarity_edges.recompute_item_edges(store, hi, top_k=5, min_score=0.5) == 1

    row = _edge_rows(store)[0]
    assert (row["source_item_id"], row["target_item_id"]) == (lo, hi)


def test_the_same_pair_never_becomes_two_rows(store):
    """Both endpoints recomputing must converge on ONE row. Without canonical ordering
    `UNIQUE(source, target)` cannot see A→B and B→A as the same pair."""
    a, b = _item(store, "A"), _item(store, "B")
    _embed_chunks(store, a, [_unit(1.0, 0.0)])
    _embed_chunks(store, b, [_unit(0.95, math.sqrt(1 - 0.9025))])

    similarity_edges.recompute_item_edges(store, a, top_k=5, min_score=0.5)
    similarity_edges.recompute_item_edges(store, b, top_k=5, min_score=0.5)

    assert store.count_similarity_edges() == 1, _edge_rows(store)


# ── the clause that bites: a recompute must not eat another pass's edge ─────────


@pytest.mark.parametrize("author_is_lower_id", [True, False])
def test_recompute_keeps_the_edge_the_other_pass_found(store, author_is_lower_id):
    """One pass writes the edge; the OTHER endpoint then recomputes and finds nothing.

    Run in both id orderings on purpose. Canonical storage puts the edge's `source_item_id` on
    whichever endpoint sorts lower, so `DELETE WHERE source_item_id = ?` destroys the other
    pass's finding in exactly one of the two orderings — a single-ordering test would call that
    implementation correct half the time.

    The recomputing item is starved with an unreachable floor rather than by deleting its
    chunks, so the pass genuinely runs its withdraw step instead of returning early.
    """
    a, b = _item(store, "A"), _item(store, "B")
    lo, hi = min(a, b), max(a, b)
    _embed_chunks(store, lo, [_unit(1.0, 0.0)])
    _embed_chunks(store, hi, [_unit(0.95, math.sqrt(1 - 0.9025))])

    author, other = (lo, hi) if author_is_lower_id else (hi, lo)

    assert (
        similarity_edges.recompute_item_edges(store, author, top_k=5, min_score=0.5) == 1
    ), "positive control: the author's pass must create the edge first"
    before = _pairs(store)
    assert before == {(lo, hi)}

    # `other`'s pass derives nothing at this floor, so its withdraw step runs over an empty
    # keep set — the exact situation in which a source-side or both-legs delete wipes the edge.
    similarity_edges.recompute_item_edges(store, other, top_k=5, min_score=0.999)

    assert _pairs(store) == before, (
        f"recomputing {'lower' if other == lo else 'higher'}-id item deleted the edge the "
        f"other pass created"
    )
    row = _edge_rows(store)[0]
    claim = row["by_source"] if author == lo else row["by_target"]
    assert claim == 1, "the surviving edge must still be claimed by the pass that found it"


def test_a_recompute_does_reclaim_its_own_stale_edge(store):
    """The complement, so survival is not just "nothing is ever deleted".

    An edge only its own author vouches for must go when that author stops deriving it —
    otherwise an item edited into a different topic keeps its old neighbours for good.
    """
    a, b = _item(store, "A"), _item(store, "B")
    _embed_chunks(store, a, [_unit(1.0, 0.0)])
    _embed_chunks(store, b, [_unit(0.95, math.sqrt(1 - 0.9025))])

    assert similarity_edges.recompute_item_edges(store, a, top_k=5, min_score=0.5) == 1
    assert store.count_similarity_edges() == 1, "positive control"

    similarity_edges.recompute_item_edges(store, a, top_k=5, min_score=0.999)

    assert (
        store.count_similarity_edges() == 0
    ), "an edge no writer claims any more must be reclaimed, not kept forever"


# ── the GLOBAL degree cap ───────────────────────────────────────────────────────


def test_degree_cap_bounds_inbound_edges_globally(store):
    """Ten sources each writing ONE edge at the same popular item.

    Per-pass top-K is 8 for every source and none of them ever writes more than one edge, so a
    truncation-only implementation leaves the popular item at degree 10. The uncapped run is
    measured FIRST as the vacuity guard: without it, a setup that never exceeded 3 would make
    the capped assertion pass while proving nothing.

    Sources are mutually dissimilar by construction (`_axis_pair`: cosine 0.8 to the popular
    item, 0.64 to each other, floor 0.7), so the degree under test is purely inbound.
    """
    dim = 16
    popular = _item(store, "popular")
    _embed_chunks(store, popular, [[1.0] + [0.0] * (dim - 1)])
    sources = [_item(store, f"src-{i}") for i in range(10)]
    for slot, src in enumerate(sources, start=1):
        _embed_chunks(store, src, [_axis_pair(0.8, slot, dim)])

    def _sweep_all(cap: int) -> None:
        for src in sources:
            similarity_edges.recompute_item_edges(
                store, src, top_k=8, min_score=0.7, degree_cap=cap
            )

    _sweep_all(cap=1000)
    uncapped = store.similarity_degree(popular)
    assert uncapped == len(
        sources
    ), f"vacuity guard: the fan-in must actually reach {len(sources)}, got {uncapped}"

    store.db.execute("DELETE FROM item_similarity_edges")
    store.db.commit()
    _sweep_all(cap=3)

    capped = store.similarity_degree(popular)
    assert capped == 3, (
        f"a GLOBAL cap must bound inbound accumulation; per-pass truncation leaves "
        f"{uncapped}, got {capped}"
    )


def test_degree_cap_evicts_the_lowest_scoring_edge(store):
    """Eviction order is by score, so the cap costs the weakest relationship, not the newest."""
    hub = _item(store, "hub")
    weak, mid, strong = (_item(store, n) for n in ("weak", "mid", "strong"))
    for other, score in ((weak, 0.6), (mid, 0.75), (strong, 0.95)):
        store.upsert_similarity_edges(
            [
                {
                    "source_item_id": hub,
                    "target_item_id": other,
                    "score": score,
                    "source_chunk_index": 0,
                    "target_chunk_index": 0,
                }
            ]
        )
    assert store.similarity_degree(hub) == 3, "positive control"

    assert store.enforce_similarity_degree_cap([hub], cap=2) == 1

    survivors = {n["item_id"] for n in store.similar_items(hub, limit=10, min_score=0.0)}
    assert survivors == {mid, strong}, f"the weakest edge must be the one evicted, kept {survivors}"


# ── the pass: resumable, drains, and never keyed on "has an edge" ───────────────


def test_drain_reaches_zero_even_when_no_edge_is_ever_written(store):
    """Five items, a floor nothing clears, `batch_size=2` — the drain must be [2, 2, 1, 0].

    This is the non-termination assertion in its strongest form. With the backlog keyed on
    "has no edges" every call re-claims the same head and the sequence is [2, 2, 2, 2, ...]
    forever, because no item here will ever gain an edge to leave on.
    """
    items = [_item(store, f"I-{i}") for i in range(5)]
    for slot, item_id in enumerate(items, start=1):
        _embed_chunks(store, item_id, [_axis_pair(0.1, slot, 16)])

    assert store.count_items_missing_similarity_sweep() == 5, "positive control: a real backlog"

    drain = []
    for _ in range(6):  # bounded, so a non-terminating backlog fails instead of hanging
        n = similarity_edges.similarity_pass(batch_size=2, min_score=0.999)
        drain.append(n)
        if n == 0:
            break

    assert drain == [2, 2, 1, 0], f"backlog did not drain: {drain}"
    assert store.count_similarity_edges() == 0, (
        "the floor was unreachable, so the drain above was achieved with zero edges — which is "
        "exactly what a backlog keyed on 'has edges' cannot do"
    )
    assert store.count_items_missing_similarity_sweep() == 0


def test_drain_writes_real_edges_at_a_reachable_floor(store):
    """The positive control for the test above: the same shape with a floor that IS reachable
    must both drain and leave edges behind, so "drains" never means "did nothing"."""
    items = [_item(store, f"I-{i}") for i in range(4)]
    for slot, item_id in enumerate(items, start=1):
        _embed_chunks(store, item_id, [_axis_pair(0.9, slot, 16)])

    drain = []
    for _ in range(5):
        n = similarity_edges.similarity_pass(batch_size=2, min_score=0.5)
        drain.append(n)
        if n == 0:
            break

    assert drain == [2, 2, 0], f"backlog did not drain: {drain}"
    assert store.count_similarity_edges() > 0, "a reachable floor must produce edges"


def test_one_call_claims_at_most_batch_size(store):
    """The host relies on the store lock being released between sub-batches, so one call must
    never walk the whole library."""
    for slot, item_id in enumerate([_item(store, f"I-{i}") for i in range(7)], start=1):
        _embed_chunks(store, item_id, [_axis_pair(0.5, slot, 16)])

    assert similarity_edges.similarity_pass(batch_size=3, min_score=0.5) == 3


def test_the_pass_refuses_a_library_with_only_one_embedded_item(store):
    """A precondition, not a per-item concern.

    A sweep is once-per-item, so sweeping a single-item library would mark it looked-at while
    there was nothing to compare against — and it could then never be compared once the
    library grew. The item must STAY in the backlog.
    """
    only = _item(store, "only")
    _embed_chunks(store, only, [_unit(1.0, 0.0)])
    assert store.count_items_missing_similarity_sweep() == 1, "positive control"

    assert similarity_edges.similarity_pass(batch_size=5) == 0
    assert (
        store.count_items_missing_similarity_sweep() == 1
    ), "the first document must not burn its one sweep against an empty library"

    # And it becomes work the moment a second embedded item exists.
    second = _item(store, "second")
    _embed_chunks(store, second, [_unit(0.95, math.sqrt(1 - 0.9025))])
    assert similarity_edges.similarity_pass(batch_size=5) == 2


def test_items_with_no_embedded_chunk_are_not_in_the_backlog(store):
    """Nothing to compare is not work that always finds nothing — the same exclusion the chunk
    backlog makes for text-less items. Otherwise an un-embeddable item is swept once, marked,
    and never reconsidered after it does get vectors."""
    bare = _item(store, "bare")
    assert store.count_items_missing_similarity_sweep() == 0

    _embed_chunks(store, bare, [_unit(1.0, 0.0)])
    assert (
        store.count_items_missing_similarity_sweep() == 1
    ), "an item must enter the backlog the moment its first chunk vector commits"


def test_rechunking_puts_an_item_back_in_the_backlog(store):
    """Its vectors changed, so the edges derived from them are stale. A sweep is once-per-item,
    so without clearing the marker a re-chunked item keeps its previous content's neighbours
    for the life of the library."""
    a = _item(store, "A")
    _embed_chunks(store, a, [_unit(1.0, 0.0)])
    store.record_similarity_sweep(a)
    assert store.count_items_missing_similarity_sweep() == 0, "positive control: swept"

    _embed_chunks(store, a, [_unit(0.0, 1.0)])
    assert store.count_items_missing_similarity_sweep() == 1


# ── ANN unavailable: fail soft to the exact scan ────────────────────────────────


def test_ann_unavailable_falls_soft_to_the_exact_scan(store):
    """`candidate_chunk_ids` returning `None` means "the index cannot serve this" — the pass
    must run the exact scan, exactly as the retrieval vector arm does, not crash and not
    silently return no edges.

    The ANN result is measured FIRST and the two are compared, so "fail soft" means the same
    answer more slowly rather than a different answer quietly.
    """
    a, b = _item(store, "A"), _item(store, "B")
    _embed_chunks(store, a, [_unit(1.0, 0.0)])
    _embed_chunks(store, b, [_unit(0.9, math.sqrt(1 - 0.81))])

    if not getattr(store.vec_index, "enabled", False):
        pytest.skip("sqlite-vec unavailable: the ANN arm cannot be measured to compare against")

    assert similarity_edges.recompute_item_edges(store, a, top_k=5, min_score=0.5) == 1
    with_ann = _edge_rows(store)
    assert with_ann, "positive control: the ANN arm must find the pair"

    store.db.execute("DELETE FROM item_similarity_edges")
    store.db.commit()

    calls: list[int] = []

    def _refuse(query_blob, dim, k):
        calls.append(k)
        return None

    mp = pytest.MonkeyPatch()
    mp.setattr(store.vec_index, "candidate_chunk_ids", _refuse)
    try:
        assert similarity_edges.recompute_item_edges(store, a, top_k=5, min_score=0.5) == 1
    finally:
        mp.undo()

    assert calls, "vacuity guard: the refusing stub was never called, so no fallback was taken"
    assert _edge_rows(store) == with_ann, "the exact scan must reach the same answer as the ANN arm"


def test_a_disabled_index_still_produces_edges(store):
    """The other unavailability shape: `enabled` False (extension missing on this build), which
    the pass must collapse to the same exact-scan branch."""
    a, b = _item(store, "A"), _item(store, "B")
    _embed_chunks(store, a, [_unit(1.0, 0.0)])
    _embed_chunks(store, b, [_unit(0.9, math.sqrt(1 - 0.81))])

    mp = pytest.MonkeyPatch()
    mp.setattr(type(store.vec_index), "enabled", property(lambda self: False))
    try:
        assert similarity_edges.recompute_item_edges(store, a, top_k=5, min_score=0.5) == 1
    finally:
        mp.undo()

    assert store.count_similarity_edges() == 1


def test_a_documents_own_chunks_never_become_its_neighbours(store):
    """A chunk's true nearest neighbours are the other chunks of its own document
    (self-similarity ~1.0), so a candidate budget that does not account for them is spent
    entirely on self-hits and a long document finds nobody."""
    a, b = _item(store, "A"), _item(store, "B")
    _embed_chunks(store, a, [_unit(1.0, 0.0)] * 12)  # more own chunks than the budget
    _embed_chunks(store, b, [_unit(0.9, math.sqrt(1 - 0.81))])

    assert similarity_edges.recompute_item_edges(store, a, top_k=2, min_score=0.5) == 1
    assert {r["item_id"] for r in store.similar_items(a, limit=10, min_score=0.5)} == {b}


# ── reading the table back ──────────────────────────────────────────────────────


def test_similar_items_reads_both_legs_and_applies_the_threshold(store):
    """Canonical storage puts the queried item on either leg, so a reader that checks one leg
    silently returns half its neighbours. The threshold is the point of the table — it replaces
    an unthresholded shared-entity COUNT."""
    a, b, c = _item(store, "A"), _item(store, "B"), _item(store, "C")
    store.upsert_similarity_edges(
        [
            {
                "source_item_id": a,
                "target_item_id": b,
                "score": 0.9,
                "source_chunk_index": 1,
                "target_chunk_index": 2,
            },
            {
                "source_item_id": a,
                "target_item_id": c,
                "score": 0.3,
                "source_chunk_index": 0,
                "target_chunk_index": 0,
            },
        ]
    )

    unfiltered = {n["item_id"] for n in store.similar_items(a, limit=10, min_score=0.0)}
    assert unfiltered == {
        b,
        c,
    }, f"positive control: both neighbours must be readable, got {unfiltered}"

    assert {n["item_id"] for n in store.similar_items(a, limit=10, min_score=0.5)} == {b}
    # Symmetry: B must see A whichever leg the canonical row put A on.
    assert {n["item_id"] for n in store.similar_items(b, limit=10, min_score=0.5)} == {a}

    from_a = store.similar_items(a, limit=10, min_score=0.5)[0]
    from_b = store.similar_items(b, limit=10, min_score=0.5)[0]
    assert (from_a["chunk_index"], from_a["neighbour_chunk_index"]) == (1, 2)
    assert (from_b["chunk_index"], from_b["neighbour_chunk_index"]) == (
        2,
        1,
    ), "provenance must be oriented to the item asked about, not to the stored leg"


def test_upsert_canonicalises_and_keeps_the_higher_score(store):
    """Two writers, opposite argument order, different scores: one row, the MAX score, and the
    provenance that belongs to that score."""
    a, b = _item(store, "A"), _item(store, "B")
    lo, hi = min(a, b), max(a, b)
    store.upsert_similarity_edges(
        [
            {
                "source_item_id": hi,
                "target_item_id": lo,
                "score": 0.6,
                "source_chunk_index": 7,
                "target_chunk_index": 8,
            }
        ]
    )
    store.upsert_similarity_edges(
        [
            {
                "source_item_id": lo,
                "target_item_id": hi,
                "score": 0.85,
                "source_chunk_index": 3,
                "target_chunk_index": 4,
            }
        ]
    )

    rows = _edge_rows(store)
    assert len(rows) == 1, rows
    row = rows[0]
    assert (row["source_item_id"], row["target_item_id"]) == (lo, hi)
    assert row["score"] == pytest.approx(0.85)
    assert (row["source_chunk_index"], row["target_chunk_index"]) == (
        3,
        4,
    ), "the winning score's provenance must travel with it"

    # A lower later score must not overwrite, and must not lose its provenance either.
    store.upsert_similarity_edges(
        [
            {
                "source_item_id": lo,
                "target_item_id": hi,
                "score": 0.4,
                "source_chunk_index": 9,
                "target_chunk_index": 9,
            }
        ]
    )
    row = _edge_rows(store)[0]
    assert row["score"] == pytest.approx(0.85)
    assert (row["source_chunk_index"], row["target_chunk_index"]) == (3, 4)


def test_a_self_edge_is_never_written(store):
    """An item is trivially similar to itself; storing that is noise every reader must filter."""
    a = _item(store, "A")
    assert (
        store.upsert_similarity_edges(
            [
                {
                    "source_item_id": a,
                    "target_item_id": a,
                    "score": 1.0,
                    "source_chunk_index": 0,
                    "target_chunk_index": 0,
                }
            ]
        )
        == 0
    )
    assert store.count_similarity_edges() == 0


def test_top_k_truncates_to_the_strongest_neighbours(store):
    """Per-pass truncation keeps the BEST k, not the first k the index happened to return."""
    dim = 16
    a = _item(store, "A")
    _embed_chunks(store, a, [[1.0] + [0.0] * (dim - 1)])
    # Descending similarity, so the expected survivors are unambiguous.
    others = []
    for slot, cos in enumerate([0.95, 0.9, 0.85, 0.8, 0.75], start=1):
        other = _item(store, f"O-{cos}")
        _embed_chunks(store, other, [_axis_pair(cos, slot, dim)])
        others.append((cos, other))

    assert similarity_edges.recompute_item_edges(store, a, top_k=2, min_score=0.7) == 2

    kept = {n["item_id"] for n in store.similar_items(a, limit=10, min_score=0.7)}
    assert kept == {others[0][1], others[1][1]}, f"top-K must keep the strongest pair, kept {kept}"


# ── tuning resolution ───────────────────────────────────────────────────────────


def test_defaults_resolve_without_config_and_an_override_wins(tmp_path):
    """The pass owns module defaults and an explicit argument beats everything.

    `_resolve_tuning` reading a config field that does not exist yet must be indistinguishable
    from it being unset — otherwise this module becomes a live reader of an unwritten key,
    which reads as wired and behaves as inert.
    """
    mp = pytest.MonkeyPatch()
    mp.setenv("PERSONALCLAW_HOME", str(tmp_path))
    try:
        resolved = similarity_edges._resolve_tuning()
        assert resolved["top_k"] == similarity_edges.DEFAULT_TOP_K
        assert resolved["min_score"] == pytest.approx(similarity_edges.DEFAULT_MIN_SCORE)
        assert resolved["candidate_multiple"] == similarity_edges.DEFAULT_CANDIDATE_MULTIPLE
        assert resolved["degree_cap"] == similarity_edges.DEFAULT_DEGREE_CAP

        overridden = similarity_edges._resolve_tuning(top_k=2, min_score=0.42, degree_cap=5)
        assert (overridden["top_k"], overridden["degree_cap"]) == (2, 5)
        assert overridden["min_score"] == pytest.approx(0.42)
        assert overridden["candidate_multiple"] == similarity_edges.DEFAULT_CANDIDATE_MULTIPLE
    finally:
        mp.undo()

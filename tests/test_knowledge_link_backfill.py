"""The entity-link backfill — KL-14 clause 7's third job (the "graph linker backfill").

`maintenance_passes.py` shipped the host with two of three jobs and recorded the third unmet
because no callable existed. `link_backfill.link_backfill_pass` is that callable, and it runs
under `batched=True`, meaning the host re-invokes it until it returns 0. That contract is what
these tests are really about.

The risks they target, in order of how badly each one bites:

* **A backlog that cannot drain.** The obvious backlog definition — "items with no `mentions`
  rows" — does not terminate: an item may legitimately name no known entity, so it never gains
  a mention, never leaves the backlog, and is re-claimed on every tick. The host would spend
  every sub-batch on the head of the library and never reach the tail.
  `test_unlinkable_item_still_leaves_the_backlog` and
  `test_repeated_calls_drain_the_backlog_then_return_zero` are that assertion.
* **Burning the backlog against an empty graph.** A sweep is once-per-item, so sweeping a
  fresh library before any entity exists would mark everything looked-at while linking nothing
  — and those items could then never be linked. `test_no_entities_writes_nothing_at_all`.
* **A silent no-op.** A pass that reports progress while writing no `mentions` rows is
  indistinguishable from the gap it closes, so the linking tests assert on the DB rows rather
  than on the return value.
* **Holding the store for a whole library.** `test_one_call_processes_at_most_batch_size`.
"""

from __future__ import annotations

import pytest

from personalclaw.knowledge import link_backfill


@pytest.fixture()
def store(tmp_path):
    """A store at the SAME path the pass will open for itself.

    The pass takes no store argument (the host's pass signature is `(*, batch_size)`), so it
    resolves `knowledge_db_path()` -> `config_dir()` -> `PERSONALCLAW_HOME` on every call.
    Pointing that at `tmp_path` is what keeps the real `~/.personalclaw` out of this suite.

    🔴 Its OWN `MonkeyPatch`, not the `monkeypatch` fixture. Measured: sharing it means a test
    that calls `monkeypatch.undo()` to restore something of its own also undoes this `setenv`,
    and the next `link_backfill_pass` call then opens and sweeps the developer's real library.
    The real-home rail caught exactly that. Home isolation must not be revocable by a test.
    """
    mp = pytest.MonkeyPatch()
    mp.setenv("PERSONALCLAW_HOME", str(tmp_path))
    from personalclaw.knowledge.store import KnowledgeStore, knowledge_db_path

    try:
        yield KnowledgeStore(str(knowledge_db_path()))
    finally:
        mp.undo()


#: Anchor items exist only to keep their entity alive (see `_entity`) and are pre-swept, so
#: every assertion helper below filters them out by title rather than by a tracked id set.
_ANCHOR_PREFIX = "anchor-"


def _item(store, *, title="T", content="some text", summary="") -> str:
    item_id = store.create_typed_item(
        item_type="note", title=title, content=content, summary=summary
    )
    assert item_id, "fixture item was not created"
    return item_id


def _entity(store, name: str) -> str:
    """Create an entity that SURVIVES the pass opening its own store.

    🔴 `KnowledgeStore.__init__` prunes every entity with no mentions and no relations on
    EVERY open (`store.py`, "Prune orphan entities"). The pass takes no store argument, so it
    opens one per call — and a bare `add_entity()` is therefore deleted before the linker can
    ever match it. A test built on bare `add_entity` measures that prune, not the backfill.

    Anchoring each entity to one already-swept item is also what a real library looks like: an
    entity exists because some document named it, and the historical items this backfill
    recovers are the OTHER documents that name it too.
    """
    eid = store.add_entity(name, "project")
    anchor = _item(store, title=f"{_ANCHOR_PREFIX}{name}", content=name)
    store.add_mention(anchor, eid, context=name)
    store.record_mention_sweep(anchor)  # pre-swept: the anchor is not part of any backlog
    return eid


def _mention_rows(store) -> set[tuple[str, str]]:
    rows = store.db.execute(
        "SELECT m.item_id, m.entity_id FROM mentions m JOIN items i ON i.id = m.item_id "
        "WHERE i.title NOT LIKE ?",
        (f"{_ANCHOR_PREFIX}%",),
    ).fetchall()
    return {(r["item_id"], r["entity_id"]) for r in rows}


def _swept_ids(store) -> set[str]:
    rows = store.db.execute(
        "SELECT s.item_id FROM mention_sweeps s JOIN items i ON i.id = s.item_id "
        "WHERE i.title NOT LIKE ?",
        (f"{_ANCHOR_PREFIX}%",),
    ).fetchall()
    return {r["item_id"] for r in rows}


def _linked_names(store, item_id) -> set[str]:
    rows = store.db.execute(
        "SELECT e.name FROM mentions m JOIN entities e ON e.id = m.entity_id WHERE m.item_id = ?",
        (item_id,),
    ).fetchall()
    return {r["name"] for r in rows}


# ── It links what was unlinked ──────────────────────────────────────────


def test_links_an_entity_named_in_the_body(store):
    """The payload: an item that plainly names a known entity gains the mention row.

    Asserted on the `mentions` rows, not the return value — a pass that claimed the item and
    wrote nothing would return exactly the same 1.
    """
    _entity(store, "Sparrow")
    item_id = _item(store, content="The Sparrow release ships Friday.")
    assert _mention_rows(store) == set(), "precondition: nothing linked yet"

    assert link_backfill.link_backfill_pass(batch_size=10) == 1

    assert _linked_names(store, item_id) == {"Sparrow"}


def test_links_an_entity_named_only_in_title_or_summary(store):
    """The composed text is title + summary + content, a SUPERSET of what ingest passes.

    `runner.py` hands `link_known_entities` the bare `content`, and
    `embedder.compose_item_text` (the vector text) is title + summary with content unused. An
    entity named only in a title or only in a summary is invisible to both, and is exactly the
    edge a backfill should recover.
    """
    _entity(store, "Kestrel")
    _entity(store, "Merlin")
    titled = _item(store, title="Kestrel roadmap", content="unrelated body text")
    summarized = _item(store, title="Q3", content="unrelated body text", summary="Merlin status")

    assert link_backfill.link_backfill_pass(batch_size=10) == 2

    assert _linked_names(store, titled) == {"Kestrel"}
    assert _linked_names(store, summarized) == {"Merlin"}


# ── It terminates ───────────────────────────────────────────────────────


def test_unlinkable_item_still_leaves_the_backlog(store):
    """🔴 The non-termination trap, asserted directly.

    An entity exists, but this item names none. Under a "no `mentions` rows" backlog the item
    would stay claimable forever and the host — which loops until 0 — would re-link it every
    sub-batch of every tick. The sweep row is what lets it leave having found nothing.
    """
    _entity(store, "Sparrow")
    item_id = _item(store, title="Unrelated", content="nothing here names a known entity")

    assert link_backfill.link_backfill_pass(batch_size=10) == 1
    assert _mention_rows(store) == set(), "nothing to link, so nothing linked"
    assert _swept_ids(store) == {item_id}, "but the linker LOOKED, and that must be recorded"

    assert link_backfill.link_backfill_pass(batch_size=10) == 0
    assert store.count_items_missing_mention_sweep() == 0


def test_repeated_calls_drain_the_backlog_then_return_zero(store):
    """Resumable and terminating: the host's loop must reach 0 on a mixed library.

    Half the items name a known entity and half do not, which is the shape that separates a
    real drain from one that only works when everything happens to link.
    """
    _entity(store, "Sparrow")
    linkable = [_item(store, title=f"L{i}", content="Sparrow again") for i in range(3)]
    inert = [_item(store, title=f"I{i}", content="nothing familiar") for i in range(2)]
    assert store.count_items_missing_mention_sweep() == 5

    returns = []
    for _ in range(10):  # a bound, so a non-terminating pass fails instead of hanging
        got = link_backfill.link_backfill_pass(batch_size=2)
        returns.append(got)
        if got == 0:
            break

    assert returns == [2, 2, 1, 0], f"expected a clean drain to 0, got {returns}"
    assert store.count_items_missing_mention_sweep() == 0
    assert _swept_ids(store) == set(linkable) | set(inert)
    assert len(_mention_rows(store)) == 3, "only the three linkable items produced mentions"


def test_empty_backlog_returns_zero_without_calling_the_linker(monkeypatch, store):
    """Vacuity: a drained library must cost the backlog query, not a linker invocation."""
    _entity(store, "Sparrow")
    _item(store, content="Sparrow ships Friday.")
    assert link_backfill.link_backfill_pass(batch_size=10) == 1

    from personalclaw.knowledge import alias_prepass

    calls = []

    def _boom(*args, **kwargs):
        calls.append(args)
        raise AssertionError("the linker must not be invoked on an empty backlog")

    monkeypatch.setattr(alias_prepass, "link_known_entities", _boom)
    assert link_backfill.link_backfill_pass(batch_size=10) == 0
    assert calls == []


# ── It is idempotent ────────────────────────────────────────────────────


def test_second_pass_adds_no_duplicate_mentions(store):
    """Running the sweep twice must not duplicate a single row.

    Two mechanisms guarantee it and this asserts both: the item has left the backlog, AND
    `add_mention` is `INSERT OR IGNORE`. The second half is checked by forcing a re-sweep —
    otherwise the test only proves the backlog predicate works and would still pass if the
    write path were duplicating rows.
    """
    _entity(store, "Sparrow")
    _entity(store, "Kestrel")
    item_id = _item(store, content="Sparrow depends on Kestrel.")

    assert link_backfill.link_backfill_pass(batch_size=10) == 1
    before = _mention_rows(store)
    assert len(before) == 2

    assert link_backfill.link_backfill_pass(batch_size=10) == 0
    assert _mention_rows(store) == before

    # Force the linker to run over THIS item again, bypassing the backlog predicate. Scoped to
    # the one id: clearing the table wholesale would also un-sweep the anchors and the pass
    # would then legitimately claim three items, which is not what this asserts.
    store.db.execute("DELETE FROM mention_sweeps WHERE item_id = ?", (item_id,))
    store.db.commit()
    assert link_backfill.link_backfill_pass(batch_size=10) == 1
    assert _mention_rows(store) == before, "re-linking must be INSERT OR IGNORE, not append"


# ── It is bounded ───────────────────────────────────────────────────────


def test_one_call_processes_at_most_batch_size(store):
    """One call claims one batch. This is what keeps a tick from holding the store for a
    whole library — the host releases the lock between sub-batches only because each call
    opens and closes its own work."""
    _entity(store, "Sparrow")
    for i in range(10):
        _item(store, title=f"N{i}", content="Sparrow again")

    assert link_backfill.link_backfill_pass(batch_size=3) == 3
    assert len(_swept_ids(store)) == 3
    assert store.count_items_missing_mention_sweep() == 7


def test_non_positive_batch_size_is_a_no_op(store):
    """A zero budget claims nothing rather than falling through to an unbounded fetch."""
    _entity(store, "Sparrow")
    _item(store, content="Sparrow again")

    assert link_backfill.link_backfill_pass(batch_size=0) == 0
    assert _swept_ids(store) == set()


# ── Vacuity: nothing to link against ────────────────────────────────────


def test_no_entities_writes_nothing_at_all(store):
    """🔴 An empty entity graph is a precondition, not a per-item concern.

    Sweeping here would mark the whole library looked-at while linking nothing, and since a
    sweep is once-per-item those items would never be linked once extraction finally creates
    entities. So the pass must return 0 AND leave the backlog intact.
    """
    for i in range(3):
        _item(store, title=f"N{i}", content="Sparrow would match, if it existed")

    assert link_backfill.link_backfill_pass(batch_size=10) == 0
    assert _mention_rows(store) == set()
    assert _swept_ids(store) == set(), "the backlog must survive for when entities appear"
    assert store.count_items_missing_mention_sweep() == 3


def test_empty_store_returns_zero(store):
    """A fresh install costs one COUNT and reports no work."""
    assert link_backfill.count_link_backlog() == 0
    assert link_backfill.link_backfill_pass(batch_size=10) == 0


# ── The backlog predicate ───────────────────────────────────────────────


def test_backlog_excludes_archived_and_textless_items(store):
    """Mirrors the chunk backlog (archived) and `count_items_missing_embedding` (text-less).

    A text-less item is excluded rather than swept: there is nothing to match, so it is not
    work — as opposed to work that always finds nothing.
    """
    _entity(store, "Sparrow")
    live = _item(store, title="Live", content="Sparrow again")
    archived = _item(store, title="Archived", content="Sparrow again")
    store.db.execute("UPDATE items SET is_archived = 1 WHERE id = ?", (archived,))
    empty = _item(store, title="", content="")
    store.db.commit()

    assert store.count_items_missing_mention_sweep() == 1
    assert [r["id"] for r in store.items_missing_mention_sweep(limit=10)] == [live]

    assert link_backfill.link_backfill_pass(batch_size=10) == 1
    assert _swept_ids(store) == {live}
    assert archived not in _swept_ids(store)
    assert empty not in _swept_ids(store)


def test_count_link_backlog_reports_the_pending_work(store):
    """Exposed so a caller can state the backlog without running it."""
    _entity(store, "Sparrow")
    for i in range(4):
        _item(store, title=f"N{i}", content="Sparrow again")

    assert link_backfill.count_link_backlog() == 4
    link_backfill.link_backfill_pass(batch_size=2)
    assert link_backfill.count_link_backlog() == 2


def test_deleting_an_item_drops_its_sweep_row(store):
    """The marker is per-item bookkeeping and must not outlive the item, or it accumulates
    for the life of the library (invisible to the backlog query, which selects FROM items)."""
    _entity(store, "Sparrow")
    item_id = _item(store, content="Sparrow again")
    assert link_backfill.link_backfill_pass(batch_size=10) == 1
    assert _swept_ids(store) == {item_id}

    store.delete_item(item_id)
    assert _swept_ids(store) == set()


# ── It never raises ─────────────────────────────────────────────────────


def test_a_failing_linker_costs_its_item_not_the_tick(store):
    """A linking hiccup must not break a maintenance tick, AND must not wedge the backlog.

    The item is still swept, so a permanently-failing item is skipped once rather than
    re-claimed on every tick — which would starve every item behind it.
    """
    from personalclaw.knowledge import alias_prepass

    _entity(store, "Sparrow")
    item_id = _item(store, content="Sparrow again")

    def _boom(*args, **kwargs):
        raise RuntimeError("matcher exploded")

    # A SCOPED context, not the `monkeypatch` fixture + `undo()`: `undo()` is all-or-nothing
    # over one MonkeyPatch instance, so restoring the linker that way would also drop the home
    # isolation and the call below would sweep the real library.
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(alias_prepass, "link_known_entities", _boom)
        assert link_backfill.link_backfill_pass(batch_size=10) == 1
        assert _swept_ids(store) == {item_id}
        assert _mention_rows(store) == set()

    assert link_backfill.link_backfill_pass(batch_size=10) == 0


def test_an_unopenable_store_reports_no_work(monkeypatch, tmp_path):
    """The pass is registered on a host whose whole point is unattended cadence, so an
    environmental failure reports "nothing to do" rather than raising into the tick."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))

    def _boom(*args, **kwargs):
        raise RuntimeError("no store here")

    monkeypatch.setattr(link_backfill, "_open_store", _boom)
    assert link_backfill.link_backfill_pass(batch_size=10) == 0
    assert link_backfill.count_link_backlog() == 0


def test_pass_signature_matches_the_hosts_batched_contract():
    """`maintenance._claim_batches` calls `p.run(batch_size=...)` and loops until 0, so the
    pass must be keyword-only on `batch_size` and return an int."""
    import inspect

    sig = inspect.signature(link_backfill.link_backfill_pass)
    assert list(sig.parameters) == ["batch_size"]
    assert sig.parameters["batch_size"].kind is inspect.Parameter.KEYWORD_ONLY

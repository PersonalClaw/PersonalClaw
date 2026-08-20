"""KL-14 — the WRITE side of the graph-maintenance watermark.

The clause these tests exist for is "a bulk import of N items performs ONE edge pass, not N".
That is a claim about the host's cadence, so it is asserted AT THE HOST — a counting pass is
registered and its invocation count is read — rather than inferred from the stamp file. A
watermark that coalesces perfectly and a host that still runs a pass per item would both
leave exactly the same `graph_maintenance.json`, and only the pass count can tell them apart.

Three properties, and each has a partner that makes it non-vacuous:

* **Coalescing.** N writes ⇒ 0 passes during the writes, 1 after one host run. The partner is
  the pass count itself: an inline-graph-work implementation fails at `calls == 0`, and a host
  that loops per item fails at `calls == 1`.
* **A mid-run write survives.** `execute` clears only up to the snapshot it read BEFORE
  running, so a write landing during the pass keeps the state dirty.
  :func:`test_the_mid_run_assertion_is_sensitive_to_the_write` is the partner: the identical
  shape with the mid-run write removed MUST go clean, which is what stops the first test from
  passing on a host that simply never clears anything.
* **Vacuity.** No write, a no-op `update_item`, and a curation-only PATCH all leave the index
  clean. Without these, a `mark_dirty` called unconditionally on every code path passes every
  other test in this file.

The clock is faked because the two stamp comparisons are strict inequalities. Under real
`time.time()` two writes inside the same microsecond read equal, and the mid-run assertion
would then flake in exactly the direction that HIDES a swallowed write.
"""

import pytest

from personalclaw.knowledge import maintenance
from personalclaw.knowledge.store import KnowledgeStore


class Clock:
    """A strictly-increasing fake clock for `maintenance`'s stamps: every read is distinct."""

    def __init__(self, start: float = 1_000_000.0) -> None:
        self.now = start

    def time(self) -> float:
        self.now += 1.0
        return self.now


class CountingPass:
    """A maintenance pass that records every invocation and claims nothing.

    `run` returns 0 so the host's `_claim_batches` stops after ONE call. That is deliberate:
    it makes "the pass ran once" an assertion about the host's cadence rather than about how
    many sub-batches a particular job chose to claim.
    """

    def __init__(self) -> None:
        self.calls = 0

    def run(self, *, batch_size: int = 0) -> int:
        self.calls += 1
        return 0


class WritesMidRun(CountingPass):
    """A pass whose own body marks the index dirty — i.e. a write landing mid-run."""

    def __init__(self, *, writes: bool) -> None:
        super().__init__()
        self.writes = writes

    def run(self, *, batch_size: int = 0) -> int:
        self.calls += 1
        if self.writes:
            maintenance.mark_dirty(reason="a write landed during the pass")
        return 0


@pytest.fixture()
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(maintenance, "time", c)
    return c


@pytest.fixture()
def store(tmp_path, monkeypatch, clock):
    """An isolated store AND an isolated home: the watermark lives in `config_dir()`, so
    without the env override this suite would write into the real ~/.personalclaw."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path / "home"))
    maintenance.clear_passes()
    s = KnowledgeStore(str(tmp_path / "knowledge.db"))
    yield s
    s.close()
    maintenance.clear_passes()


def make_clean(store):
    """Run the host once so the watermark is clean, and prove it before returning."""
    maintenance.execute()
    assert not maintenance.is_dirty(), "setup failed: the index was still dirty after a run"


def a_source(store) -> str:
    """A real `sources` row. `source_seen` carries a FOREIGN KEY to it, so a made-up
    `source_id` raises rather than exercising the novelty gate this suite is measuring."""
    return store.create_source(name="feed", provider="native", kind="rss")


# ── the clause: ONE edge pass, not N ────────────────────────────────────────────────


def test_bulk_import_of_n_items_performs_one_edge_pass(store):
    counter = CountingPass()
    maintenance.register_pass("counting", counter.run)

    n = 7
    first_stamp = None
    for i in range(n):
        assert store.create_typed_item(item_type="note", title=f"note {i}", content="body")
        if first_stamp is None:
            opening = maintenance.load_state()
            assert opening.get("dirty_since"), "the first create left no watermark at all"
            first_stamp = float(opening["dirty_since"])

    # No graph work happened INLINE. This is the half of the clause that a stamp file cannot
    # express: the writes did not each do a pass on the way past.
    assert counter.calls == 0, f"{counter.calls} edge passes ran inline during the import"

    state = maintenance.load_state()
    assert maintenance.is_dirty(state)
    # The watermark coalesced: `dirty_since` is still the FIRST write's stamp (one unit of
    # outstanding dirt, not seven), while `dirty_ts` moved to the last.
    assert float(state["dirty_since"]) == first_stamp
    assert float(state["dirty_ts"]) > first_stamp

    result = maintenance.execute()

    assert counter.calls == 1, f"{n} writes drove {counter.calls} edge passes; expected exactly 1"
    assert result.ran is True
    assert result.per_pass == {"counting": 0}
    assert not maintenance.is_dirty()


def test_a_second_host_run_over_an_unchanged_library_does_no_pass(store):
    """The other direction of coalescing: once clean, the host does not run again unasked."""
    counter = CountingPass()
    maintenance.register_pass("counting", counter.run)
    store.create_typed_item(item_type="note", title="one", content="body")

    assert maintenance.run_maintenance(in_flight=0).ran is True
    assert counter.calls == 1

    second = maintenance.run_maintenance(in_flight=0)
    assert second.ran is False
    assert second.reason == "clean"
    assert counter.calls == 1, "a clean index still drove an edge pass"


# ── a write landing mid-run is not swallowed ────────────────────────────────────────


def test_write_landing_mid_pass_is_not_swallowed(store):
    store.create_typed_item(item_type="note", title="seed", content="body")
    assert maintenance.is_dirty()

    p = WritesMidRun(writes=True)
    maintenance.register_pass("mid", p.run)
    result = maintenance.execute()

    assert p.calls == 1
    assert maintenance.is_dirty(), "the write that landed during the pass was cleared away"
    assert float(maintenance.load_state()["dirty_ts"]) > result.snapshot


def test_the_mid_run_assertion_is_sensitive_to_the_write(store):
    """The partner of the test above, with the ONLY difference being that the pass does not
    write. If this also stayed dirty, the previous test would be measuring a host that never
    clears anything rather than a snapshot boundary that is honoured."""
    store.create_typed_item(item_type="note", title="seed", content="body")
    p = WritesMidRun(writes=False)
    maintenance.register_pass("mid", p.run)

    maintenance.execute()

    assert p.calls == 1
    assert not maintenance.is_dirty()


# ── vacuity: what must NOT move the watermark ───────────────────────────────────────


def test_a_store_with_no_write_leaves_the_index_clean(store):
    counter = CountingPass()
    maintenance.register_pass("counting", counter.run)

    assert maintenance.load_state() == {}
    assert not maintenance.is_dirty()
    assert maintenance.is_due(in_flight=0) == (False, "clean")
    assert maintenance.run_maintenance(in_flight=0).ran is False
    assert counter.calls == 0


def test_an_empty_update_is_a_no_op_and_leaves_the_index_clean(store):
    item_id = store.create_typed_item(item_type="note", title="one", content="body")
    make_clean(store)

    store.update_item(item_id)

    assert not maintenance.is_dirty()
    assert maintenance.is_due(in_flight=0) == (False, "clean")


def test_a_curation_only_patch_is_not_index_affecting(store):
    """`favorited`/`read_state` change nothing any maintenance pass reads, so a user starring
    a note must not schedule a graph pass over their whole library."""
    item_id = store.create_typed_item(item_type="note", title="one", content="body")
    make_clean(store)

    store.update_item(item_id, favorited=1, read_state="read")

    assert not maintenance.is_dirty(), "a curation PATCH marked the index dirty"


def test_a_pass_output_write_does_not_re_dirty_the_index(store):
    """The deny side that matters: `embedding`, `insights` and `processing_status` are written
    BY the maintenance passes. If those marked dirty, every pass would re-dirty the index it
    just cleaned and the watermark could never go clean again."""
    item_id = store.create_typed_item(item_type="note", title="one", content="body")
    make_clean(store)

    store.update_item(item_id, embedding=b"\x00\x01", touch=False)
    store.update_item(item_id, insights='{"a": 1}', processing_status="done", touch=False)

    assert not maintenance.is_dirty(), "a maintenance pass's own write re-dirtied the index"


def test_an_unknown_field_alone_does_not_mark_dirty(store):
    """Nothing this call names is a column, so nothing is written — and an unwritten change
    is not an index change."""
    item_id = store.create_typed_item(item_type="note", title="one", content="body")
    make_clean(store)

    store.update_item(item_id, not_a_column="x", touch=False)

    assert not maintenance.is_dirty()


def test_a_rejected_source_sighting_does_not_mark_dirty(store):
    """The reason `mark_dirty` sits after the COMMIT and not on entry: a repeat `(source,
    guid)` sighting rolls back and writes NOTHING, so marking on entry would schedule a pass
    over an unchanged library on every poll, forever."""
    sid = a_source(store)
    assert store.create_typed_item(
        item_type="note", title="feed item", content="body", source_id=sid, guid="g1"
    )
    make_clean(store)

    assert (
        store.create_typed_item(
            item_type="note", title="feed item", content="body", source_id=sid, guid="g1"
        )
        is None
    )

    assert not maintenance.is_dirty(), "a rolled-back create still moved the watermark"


# ── and what MUST move it (the positive controls for the allowlist) ─────────────────


@pytest.mark.parametrize(
    "fields",
    [
        {"content": "a different body"},
        {"title": "a different title"},
        {"summary": "a summary"},
        {"tags": ["alpha", "beta"]},
        {"status": "archived"},
        {"url": "https://example.com/a"},
    ],
    ids=["content", "title", "summary", "tags", "status", "url"],
)
def test_an_index_affecting_update_marks_dirty(store, fields):
    item_id = store.create_typed_item(item_type="note", title="one", content="body")
    make_clean(store)

    store.update_item(item_id, **fields)

    assert maintenance.is_dirty(), f"{sorted(fields)} changed the index but left it clean"
    assert maintenance.is_due(in_flight=0) == (True, "queue drained")


def test_a_delete_marks_dirty(store):
    item_id = store.create_typed_item(item_type="note", title="one", content="body")
    make_clean(store)

    store.delete_item(item_id)

    assert maintenance.is_dirty()
    assert maintenance.load_state()["last_reason"] == "delete item"


def test_a_merge_marks_dirty(store):
    keep = store.create_typed_item(item_type="note", title="keep", content="body")
    merge = store.create_typed_item(item_type="note", title="merge", content="other")
    make_clean(store)

    store.merge_items(keep, merge)

    assert maintenance.is_dirty()
    assert maintenance.load_state()["last_reason"] == "merge items"


def test_forgetting_a_source_item_marks_dirty(store):
    sid = a_source(store)
    store.create_typed_item(
        item_type="note", title="feed item", content="body", source_id=sid, guid="g1"
    )
    make_clean(store)

    assert store.forget_source_item(sid, "g1") is True

    assert maintenance.is_dirty()
    assert maintenance.load_state()["last_reason"] == "forget source item"


def test_forgetting_a_source_item_that_removes_nothing_stays_clean(store):
    sid = a_source(store)
    store.create_typed_item(item_type="note", title="one", content="body")
    make_clean(store)

    assert store.forget_source_item(sid, "never-seen") is False

    assert not maintenance.is_dirty()

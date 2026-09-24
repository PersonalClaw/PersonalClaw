"""Tests for the staging tier — the append-only log and its outcome records.

The load-bearing property is the one in ``test_a_crashing_pass_leaves_an_error_record``:
a capture pass that throws inside a best-effort ``except`` used to be
indistinguishable from one that ran and found nothing. Every other guarantee here
exists to keep that distinction true.
"""

import pytest

from personalclaw.learning import staging
from personalclaw.learning.staging import FlushOutcome, StagingStore, input_hash


@pytest.fixture
def store(tmp_path):
    s = StagingStore(tmp_path)
    yield s
    s.close()


# ── bootstrap ──


def test_the_database_is_created_on_first_use(tmp_path):
    s = StagingStore(tmp_path)
    assert not s.path.exists()
    s.stage(cadence="per_turn", kind="lesson", content="something worth keeping")
    assert s.path.exists() and s.path.name == "learning.db"
    s.close()


def test_bootstrap_is_idempotent(tmp_path):
    first = StagingStore(tmp_path)
    first.stage(cadence="per_turn", kind="lesson", content="entry one")
    first.close()
    second = StagingStore(tmp_path)
    assert len(second.pending()) == 1
    assert second.stage(cadence="per_turn", kind="lesson", content="entry two")
    second.close()


# ── append ──


def test_staging_returns_an_id_and_the_entry_is_readable(store):
    entry_id = store.stage(
        cadence="per_turn", kind="lesson", content="Chose sqlite over json", session_key="s1"
    )
    assert entry_id > 0
    (entry,) = store.pending()
    assert entry.id == entry_id
    assert entry.kind == "lesson"
    assert entry.session_key == "s1"
    assert entry.consumed_by is None if hasattr(entry, "consumed_by") else True


def test_same_content_within_a_day_is_deduplicated(store):
    text = "the user prefers concise answers"
    assert store.stage(cadence="per_turn", kind="facet", content=text) > 0
    assert store.stage(cadence="per_turn", kind="facet", content=text) == 0
    assert len(store.pending()) == 1


def test_dedup_normalises_whitespace(store):
    assert store.stage(cadence="per_turn", kind="facet", content="Prefer   concise answers") > 0
    assert store.stage(cadence="per_turn", kind="facet", content="prefer concise\nanswers") == 0


def test_empty_content_is_not_staged(store):
    for content in ("", "   ", "\n"):
        assert store.stage(cadence="per_turn", kind="lesson", content=content) == 0
    assert store.pending() == []


def test_metadata_round_trips(store):
    store.stage(
        cadence="run_end",
        kind="outcome",
        content="run failed at the audit stage",
        meta={"run_id": "r-1", "attempt": 2},
    )
    (entry,) = store.pending()
    assert entry.meta == {"run_id": "r-1", "attempt": 2}


# ── outcome records: the observability floor ──


def test_a_pass_that_finds_nothing_still_leaves_a_record(store):
    with store.flush("per_turn"):
        pass
    health = store.health()
    assert health["passes"] == 1
    assert health["by_outcome"] == {FlushOutcome.FLUSH_OK.value: 1}


def test_a_productive_pass_is_distinguishable_from_a_quiet_one(store):
    with store.flush("per_turn") as result:
        result["staged"] = 2
    assert store.health()["by_outcome"] == {FlushOutcome.FLUSH_PRODUCED.value: 1}


def test_a_crashing_pass_leaves_an_error_record(store):
    """The whole point of the tier.

    Before this, an exception inside a best-effort capture went to a debug log and
    the pass looked exactly like a quiet day.
    """
    with pytest.raises(ValueError):
        with store.flush("session_end"):
            raise ValueError("read the wrong transcript")

    health = store.health()
    assert health["errors"] == 1
    assert health["by_outcome"] == {FlushOutcome.FLUSH_ERROR.value: 1}


def test_the_error_record_names_the_exception(store):
    with pytest.raises(RuntimeError):
        with store.flush("per_turn"):
            raise RuntimeError("embedder unavailable")
    with store._cursor() as cur:
        detail = cur.execute(
            "SELECT detail FROM flush_records ORDER BY id DESC LIMIT 1;"
        ).fetchone()[0]
    assert "RuntimeError" in detail and "embedder unavailable" in detail


def test_the_exception_still_propagates(store):
    """Recording an outcome must not swallow the failure — the caller's own
    error policy still applies."""
    with pytest.raises(KeyError):
        with store.flush("per_turn"):
            raise KeyError("nope")


def test_cost_is_metered_even_on_failure(store):
    with pytest.raises(ValueError):
        with store.flush("session_end") as result:
            result["cost_usd"] = 0.0042
            raise ValueError("failed after spending")
    assert store.health()["cost_usd"] == pytest.approx(0.0042)


def test_an_all_quiet_streak_is_reportable(store):
    """A week of "ran, found nothing" on an active system is the signature of a
    dead read — so it has to be visible as a streak, not just as N records."""
    for _ in range(5):
        with store.flush("per_turn"):
            pass
    assert store.health()["all_ok_streak"] == 5
    with store.flush("per_turn") as result:
        result["staged"] = 1
    assert store.health()["all_ok_streak"] == 0


def test_proposal_ids_are_recorded(store):
    store.record_flush(
        cadence="session_end",
        outcome=FlushOutcome.FLUSH_PRODUCED,
        proposal_ids=["p-1", "p-2"],
    )
    with store._cursor() as cur:
        raw = cur.execute("SELECT proposal_ids FROM flush_records;").fetchone()[0]
    assert "p-1" in raw and "p-2" in raw


# ── append-only discipline ──


def test_consumption_marks_a_pointer_and_never_edits_content(store):
    entry_id = store.stage(cadence="per_turn", kind="lesson", content="original wording")
    store.mark_consumed([entry_id], "batch-1")
    assert store.pending() == []
    with store._cursor() as cur:
        row = cur.execute("SELECT content, consumed_by FROM staging WHERE id = ?;", (entry_id,))
        content, marker = row.fetchone()
    assert content == "original wording"
    assert marker == "batch-1"


def test_consumed_entries_leave_the_pending_queue(store):
    ids = [store.stage(cadence="per_turn", kind="lesson", content=f"entry {i}") for i in range(3)]
    store.mark_consumed(ids[:2], "batch-1")
    remaining = store.pending()
    assert [e.id for e in remaining] == [ids[2]]


def test_provenance_pointers_survive_consumption(store):
    """A compiled proposal has to be traceable back to the turns that produced
    it — that is what makes a surprising proposal auditable."""
    ids = [store.stage(cadence="per_turn", kind="lesson", content=f"signal {i}") for i in range(2)]
    store.mark_consumed(ids, "batch-7")
    sources = store.sources_for(ids)
    assert len(sources) == 2
    assert all(s["cadence"] == "per_turn" for s in sources)


def test_pending_is_oldest_first(store):
    ids = [store.stage(cadence="per_turn", kind="lesson", content=f"n{i}") for i in range(4)]
    assert [e.id for e in store.pending()] == ids


def test_pending_can_filter_by_cadence(store):
    store.stage(cadence="per_turn", kind="lesson", content="from a turn")
    store.stage(cadence="run_end", kind="outcome", content="from a run")
    assert len(store.pending(cadence="run_end")) == 1
    assert len(store.pending()) == 2


# ── the batch gate ──


def test_the_batch_gate_needs_both_activity_and_the_window(store):
    assert not store.should_batch(min_entries=3)
    for i in range(3):
        store.stage(cadence="per_turn", kind="lesson", content=f"entry {i}")
    assert store.should_batch(min_entries=3)


def test_a_claimed_pass_resets_the_window(store):
    for i in range(5):
        store.stage(cadence="per_turn", kind="lesson", content=f"entry {i}")
    assert store.should_batch(min_entries=3)
    store.claim_batch(input_hash(["a", "b"]))
    assert not store.should_batch(min_entries=3, window_secs=900.0)
    assert store.should_batch(min_entries=3, window_secs=0.0)


def test_the_same_work_cannot_be_claimed_twice(store):
    ihash = input_hash(["entry-1", "entry-2"])
    assert store.claim_batch(ihash) is True
    assert store.claim_batch(ihash) is False


def test_input_hash_is_order_insensitive():
    """A re-run that reads its rows in a different order is the same work."""
    assert input_hash(["a", "b", "c"]) == input_hash(["c", "a", "b"])
    assert input_hash(["a", "b"]) != input_hash(["a", "b", "c"])


# ── maintenance ──


def test_pruning_removes_consumed_entries_past_retention(store):
    old = store.stage(cadence="per_turn", kind="lesson", content="ancient consumed")
    store.mark_consumed([old], "batch-old")
    with store._cursor() as cur:
        cur.execute("UPDATE staging SET created_ts = ? WHERE id = ?;", (1000.0, old))
    assert store.prune(retention_days=30) == 1
    assert store.sources_for([old]) == []


def test_pruning_never_drops_an_unconsumed_entry(store):
    """An unconsumed entry is still owed a batch pass; deleting it would lose the
    signal silently — the exact failure this module exists to prevent."""
    kept = store.stage(cadence="per_turn", kind="lesson", content="never consumed")
    with store._cursor() as cur:
        cur.execute("UPDATE staging SET created_ts = ? WHERE id = ?;", (1000.0, kept))
    assert store.prune(retention_days=1) == 0
    assert [e.id for e in store.pending()] == [kept]


def test_health_reports_a_window(store):
    store.stage(cadence="per_turn", kind="lesson", content="recent")
    with store.flush("per_turn") as result:
        result["staged"] = 1
    health = store.health(days=7)
    assert health["days"] == 7
    assert health["staged_entries"] == 1
    assert health["passes"] == 1


# ── the module accessor ──


def test_an_explicit_base_dir_bypasses_the_process_global(tmp_path):
    """A test pointing at tmp_path must not get a cached real-home instance, and
    must not poison the cache for anything else."""
    staging.reset_store()
    try:
        scoped = staging.get_store(tmp_path)
        assert scoped.path.parent == tmp_path
        assert staging.get_store(tmp_path) is not scoped
    finally:
        staging.reset_store()


def test_the_cached_store_is_shared(tmp_path, monkeypatch):
    monkeypatch.setattr(staging, "_default_home", lambda: tmp_path)
    staging.reset_store()
    try:
        assert staging.get_store() is staging.get_store()
    finally:
        staging.reset_store()

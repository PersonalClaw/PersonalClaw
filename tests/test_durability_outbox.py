"""DURABILITY-AND-SYNC §4.1 / DAS-6c-ii-b — the durable outbox + consumed-only cursor.

The durable-state half of the sync cycle: a push obligation survives a crash (one file per
(target, seq)); a push is discharged only by a real delivery or an explicit permanent
failure (transient/unexpected → retry, never drop); one target giving up never blocks
another; and the pull cursor advances only on consumed rows.
"""

from __future__ import annotations

from personalclaw.durability.cursor import (
    CONSUMED,
    PAYLOAD_BAD,
    PREREQ_ABSENT,
    Cursor,
)
from personalclaw.durability.outbox import (
    OUTCOME_DELIVERED,
    OUTCOME_PERMANENT,
    OUTCOME_TRANSIENT,
    STATUS_DELIVERED,
    STATUS_GIVEN_UP,
    STATUS_PENDING,
    Outbox,
    OutboxEntry,
    entry_id,
)


class TestOutboxEnqueue:
    def test_enqueue_creates_pending_entry(self, tmp_path):
        ob = Outbox(tmp_path)
        e = ob.enqueue("git-remote", 3, prefix="machines/m/seq-0003/", local_dir="/x", now="t")
        assert e.status == STATUS_PENDING and e.seq == 3
        assert ob.get(e.id) is not None  # durable on disk

    def test_enqueue_is_idempotent_on_target_seq(self, tmp_path):
        ob = Outbox(tmp_path)
        first = ob.enqueue("git-remote", 3, now="t1")
        ob.record_outcome(first.id, OUTCOME_DELIVERED, now="t2")
        # Re-enqueue the same (target, seq): must NOT reset the delivered entry.
        again = ob.enqueue("git-remote", 3, now="t3")
        assert again.status == STATUS_DELIVERED

    def test_entry_id_is_deterministic(self):
        assert entry_id("git-remote", 3) == entry_id("git-remote", 3)
        assert entry_id("a", 3) != entry_id("b", 3)

    def test_path_traversal_in_target_is_neutralized(self, tmp_path):
        ob = Outbox(tmp_path)
        e = ob.enqueue("../../etc", 1, now="t")
        # The id carries no path separators, so the entry file cannot escape the outbox.
        assert "/" not in e.id and "\\" not in e.id and ".." not in e.id
        # And it is genuinely readable back from inside the outbox dir.
        assert ob.get(e.id) is not None
        assert (tmp_path / "outbox" / f"{e.id}.json").is_file()


class TestOutboxOutcomes:
    def test_delivered_is_terminal(self, tmp_path):
        ob = Outbox(tmp_path)
        e = ob.enqueue("t", 1, now="t")
        ob.record_outcome(e.id, OUTCOME_DELIVERED, now="t2")
        assert ob.get(e.id).status == STATUS_DELIVERED
        assert ob.pending() == []  # discharged

    def test_permanent_becomes_given_up_terminal(self, tmp_path):
        ob = Outbox(tmp_path)
        e = ob.enqueue("t", 1, now="t")
        ob.record_outcome(e.id, OUTCOME_PERMANENT, now="t2", detail="bad auth")
        got = ob.get(e.id)
        assert got.status == STATUS_GIVEN_UP and got.detail == "bad auth"
        assert ob.pending() == []  # terminal — never drains again

    def test_transient_stays_pending_and_counts_attempts(self, tmp_path):
        ob = Outbox(tmp_path)
        e = ob.enqueue("t", 1, now="t")
        ob.record_outcome(e.id, OUTCOME_TRANSIENT, now="t2")
        got = ob.get(e.id)
        assert got.status == STATUS_PENDING and got.attempts == 1
        assert len(ob.pending()) == 1  # still owed

    def test_unclassified_outcome_is_treated_as_transient_never_dropped(self, tmp_path):
        # An unexpected throw the deliverer couldn't classify must not silently discharge.
        ob = Outbox(tmp_path)
        e = ob.enqueue("t", 1, now="t")
        ob.record_outcome(e.id, "kaboom-unexpected", now="t2")
        got = ob.get(e.id)
        assert got.status == STATUS_PENDING and got.attempts == 1

    def test_one_target_giving_up_does_not_block_others(self, tmp_path):
        ob = Outbox(tmp_path)
        a = ob.enqueue("remote-a", 1, now="t")
        ob.enqueue("remote-b", 1, now="t")
        ob.record_outcome(a.id, OUTCOME_PERMANENT, now="t2")
        pending_targets = {e.target for e in ob.pending()}
        assert pending_targets == {"remote-b"}  # b still drains, a is out of the way

    def test_record_outcome_missing_entry_returns_none(self, tmp_path):
        assert Outbox(tmp_path).record_outcome("nope", OUTCOME_DELIVERED) is None


class TestOutboxPersistenceAndStats:
    def test_entries_survive_a_fresh_handle(self, tmp_path):
        Outbox(tmp_path).enqueue("t", 1, now="t")
        assert len(Outbox(tmp_path).pending()) == 1  # reloaded from disk

    def test_stats_counts_by_status(self, tmp_path):
        ob = Outbox(tmp_path)
        d = ob.enqueue("a", 1, now="t")
        g = ob.enqueue("b", 1, now="t")
        ob.enqueue("c", 1, now="t")  # stays pending
        ob.record_outcome(d.id, OUTCOME_DELIVERED, now="t2")
        ob.record_outcome(g.id, OUTCOME_PERMANENT, now="t2")
        assert ob.stats() == {STATUS_PENDING: 1, STATUS_DELIVERED: 1, STATUS_GIVEN_UP: 1}

    def test_corrupt_entry_file_is_skipped_not_fatal(self, tmp_path):
        ob = Outbox(tmp_path)
        ob.enqueue("good", 1, now="t")
        (tmp_path / "outbox" / "broken__seq-0001.json").write_text("{not json", encoding="utf-8")
        # The good entry still reads; the broken one is skipped, not a crash.
        assert [e.target for e in ob.all_entries()] == ["good"]

    def test_entry_round_trips_through_dict(self):
        e = OutboxEntry(id="x", target="t", seq=2, prefix="p/", local_dir="/d", attempts=3)
        assert OutboxEntry.from_dict(e.to_dict()) == e


class TestCursor:
    def test_consumed_advances_the_mark(self, tmp_path):
        c = Cursor(tmp_path)
        assert c.record("peer", 1, CONSUMED) is True
        assert c.seq_of("peer") == 1

    def test_prerequisite_absent_holds(self, tmp_path):
        c = Cursor(tmp_path)
        assert c.record("peer", 1, PREREQ_ABSENT) is False
        assert c.seq_of("peer") == 0  # NOT advanced — retried next cycle

    def test_payload_bad_advances_past_poison(self, tmp_path):
        c = Cursor(tmp_path)
        assert c.record("peer", 1, PAYLOAD_BAD) is True
        assert c.seq_of("peer") == 1  # advanced so it can't wedge later seqs

    def test_advance_is_monotonic(self, tmp_path):
        c = Cursor(tmp_path)
        c.record("peer", 3, CONSUMED)
        assert c.record("peer", 2, CONSUMED) is False  # stale/replayed → no-op
        assert c.seq_of("peer") == 3

    def test_seen_map_shape_matches_registry(self, tmp_path):
        c = Cursor(tmp_path)
        c.record("p1", 2, CONSUMED)
        c.record("p2", 5, CONSUMED)
        assert c.seen() == {"p1": 2, "p2": 5}

    def test_seen_is_a_copy(self, tmp_path):
        c = Cursor(tmp_path)
        c.record("p", 1, CONSUMED)
        c.seen()["p"] = 99  # mutating the returned dict must not corrupt the cursor
        assert c.seq_of("p") == 1

    def test_unknown_verdict_holds(self, tmp_path):
        c = Cursor(tmp_path)
        assert c.record("p", 1, "mystery") is False
        assert c.seq_of("p") == 0

    def test_cursor_survives_a_fresh_handle(self, tmp_path):
        Cursor(tmp_path).record("p", 4, CONSUMED)
        assert Cursor(tmp_path).seq_of("p") == 4  # reloaded from disk

    def test_corrupt_cursor_file_degrades_to_empty(self, tmp_path):
        (tmp_path / "pull_cursor.json").write_text("{not json", encoding="utf-8")
        assert Cursor(tmp_path).seen() == {}  # never crashes a pull

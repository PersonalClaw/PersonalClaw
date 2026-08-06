"""DURABILITY-AND-SYNC §4 / DAS-6c-i — deterministic row-level merge.

The pure core of the sync cycle: never lose a row, never silently pick a loser,
and a re-merge of unchanged state is a no-op (the property the cycle's
free-retry-on-CAS-race depends on). No CRDTs — union + LWW + tombstones.
"""

from __future__ import annotations

import pytest

from personalclaw.durability import merge
from personalclaw.durability.inventory import (
    MERGE_APPEND_DEDUP,
    MERGE_LWW,
    MERGE_REPLACE_ONLY,
    MERGE_SQLITE_ATTACH_IGNORE,
    MERGE_UNION_BY_ID,
)


class TestUnionById:
    def test_rows_only_one_side_are_all_kept(self):
        local = [{"id": "a"}, {"id": "b"}]
        remote = [{"id": "b"}, {"id": "c"}]
        r = merge.merge_union_by_id(local, remote)
        assert [row["id"] for row in r.rows] == ["a", "b", "c"]  # sorted, union
        assert r.added == 1 and r.kept == 2  # c added; a,b kept

    def test_output_is_sorted_and_deterministic(self):
        local = [{"id": "z"}, {"id": "m"}]
        remote = [{"id": "a"}]
        r1 = merge.merge_union_by_id(local, remote)
        r2 = merge.merge_union_by_id(local, remote)
        assert [x["id"] for x in r1.rows] == ["a", "m", "z"]
        assert r1.rows == r2.rows  # deterministic

    def test_without_lww_or_tombstones_local_wins_a_collision(self):
        local = [{"id": "a", "v": "local"}]
        remote = [{"id": "a", "v": "remote"}]
        r = merge.merge_union_by_id(local, remote)
        assert r.rows == [{"id": "a", "v": "local"}] and r.kept == 1


class TestTombstones:
    def test_remote_tombstone_beats_local_live_row(self):
        local = [{"id": "a", "title": "alive"}]
        remote = [{"id": "a", "deleted_at": "2026-08-06T00:00:00Z"}]
        r = merge.merge_union_by_id(local, remote, tombstones=True)
        assert r.rows[0].get("deleted_at") and r.tombstoned == 1  # deletion survives

    def test_local_tombstone_survives_a_remote_live_row(self):
        # A task deleted on A stays deleted after B (which still has it live) syncs in.
        local = [{"id": "a", "deleted_at": "2026-08-06T00:00:00Z"}]
        remote = [{"id": "a", "title": "resurrected?"}]
        r = merge.merge_union_by_id(local, remote, tombstones=True)
        assert r.rows[0].get("deleted_at") and "title" not in r.rows[0]

    def test_later_deletion_wins_when_both_tombstoned(self):
        local = [{"id": "a", "deleted_at": "2026-08-01T00:00:00Z"}]
        remote = [{"id": "a", "deleted_at": "2026-08-06T00:00:00Z"}]
        r = merge.merge_union_by_id(local, remote, tombstones=True)
        assert r.rows[0]["deleted_at"] == "2026-08-06T00:00:00Z"

    def test_tombstones_disabled_falls_back_to_local(self):
        local = [{"id": "a", "title": "alive"}]
        remote = [{"id": "a", "deleted_at": "2026-08-06T00:00:00Z"}]
        r = merge.merge_union_by_id(local, remote, tombstones=False)
        assert r.rows[0] == {"id": "a", "title": "alive"}  # no tombstone precedence


class TestLww:
    def test_greater_updated_at_wins(self):
        local = [{"id": "a", "updated_at": "2026-08-01", "v": "old"}]
        remote = [{"id": "a", "updated_at": "2026-08-06", "v": "new"}]
        r = merge.merge_lww_by_updated_at(local, remote)
        assert r.rows[0]["v"] == "new" and r.updated == 1

    def test_local_wins_a_tie(self):
        local = [{"id": "a", "updated_at": "2026-08-06", "v": "local"}]
        remote = [{"id": "a", "updated_at": "2026-08-06", "v": "remote"}]
        r = merge.merge_lww_by_updated_at(local, remote)
        assert r.rows[0]["v"] == "local"  # stable: ties favor local

    def test_dated_row_beats_undated(self):
        local = [{"id": "a", "v": "undated"}]
        remote = [{"id": "a", "updated_at": "2026-08-06", "v": "dated"}]
        r = merge.merge_lww_by_updated_at(local, remote)
        assert r.rows[0]["v"] == "dated"

    def test_tombstone_beats_a_newer_live_row(self):
        # Deletion is not just another field-write — a tombstone wins even over a
        # live row with a later updated_at.
        local = [{"id": "a", "deleted_at": "2026-08-01", "updated_at": "2026-08-01"}]
        remote = [{"id": "a", "updated_at": "2026-08-09", "v": "edited-later"}]
        r = merge.merge_lww_by_updated_at(local, remote, tombstones=True)
        assert r.rows[0].get("deleted_at")


class TestAppendDedup:
    def test_new_remote_rows_appended_in_order(self):
        local = [{"id": "1"}, {"id": "2"}]
        remote = [{"id": "2"}, {"id": "3"}, {"id": "4"}]
        r = merge.merge_append_dedup(local, remote)
        assert [x["id"] for x in r.rows] == ["1", "2", "3", "4"]
        assert r.added == 2  # 3,4 (2 is a dup)

    def test_reimport_is_a_noop(self):
        local = [{"id": "1"}, {"id": "2"}]
        r = merge.merge_append_dedup(local, list(local))
        assert r.rows == local and r.added == 0  # stable ids → re-import adds nothing

    def test_keyless_rows_are_kept_not_deduped(self):
        local = [{"ts": "x"}]  # no id
        remote = [{"ts": "y"}]
        r = merge.merge_append_dedup(local, remote)
        assert len(r.rows) == 2  # can't dedup keyless rows — keep both

    def test_custom_dedup_key(self):
        local = [{"guid": "g1"}]
        remote = [{"guid": "g1"}, {"guid": "g2"}]
        r = merge.merge_append_dedup(local, remote, key="guid")
        assert [x["guid"] for x in r.rows] == ["g1", "g2"]


class TestDispatch:
    def test_merge_rows_routes_by_strategy(self):
        local = [{"id": "a"}]
        remote = [{"id": "b"}]
        assert len(merge.merge_rows(MERGE_UNION_BY_ID, local, remote).rows) == 2
        assert len(merge.merge_rows(MERGE_APPEND_DEDUP, local, remote).rows) == 2
        assert len(merge.merge_rows(MERGE_LWW, local, remote).rows) == 2

    def test_db_and_replace_only_strategies_raise(self):
        # These are not row-level merges — routing one here is a caller bug.
        with pytest.raises(ValueError, match="not a row-level merge"):
            merge.merge_rows(MERGE_SQLITE_ATTACH_IGNORE, [], [])
        with pytest.raises(ValueError, match="not a row-level merge"):
            merge.merge_rows(MERGE_REPLACE_ONLY, [], [])

    def test_unknown_strategy_raises(self):
        with pytest.raises(ValueError, match="unknown merge strategy"):
            merge.merge_rows("nonsense", [], [])


class TestConvergence:
    """The done-when property: two machines applying each other's rows converge to
    the SAME set — a task made on A and one on B both exist on both; a delete on A
    stays deleted on B."""

    def test_two_machines_converge_on_union(self):
        a = [{"id": "task-a", "updated_at": "1"}]
        b = [{"id": "task-b", "updated_at": "1"}]
        # A pulls B; B pulls A.
        a_after = merge.merge_union_by_id(a, b, tombstones=True)
        b_after = merge.merge_union_by_id(b, a, tombstones=True)
        assert (
            {x["id"] for x in a_after.rows}
            == {x["id"] for x in b_after.rows}
            == {"task-a", "task-b"}
        )

    def test_delete_on_a_stays_deleted_on_b_after_sync(self):
        # A deleted task-x (tombstone); B still has it live. After B merges A's rows,
        # task-x is tombstoned on B too — no resurrection.
        a = [{"id": "task-x", "deleted_at": "2026-08-06"}]
        b = [{"id": "task-x", "title": "still here"}]
        b_after = merge.merge_union_by_id(b, a, tombstones=True)
        row = next(r for r in b_after.rows if r["id"] == "task-x")
        assert row.get("deleted_at") and "title" not in row

    def test_merge_is_idempotent(self):
        local = [{"id": "a", "updated_at": "2"}, {"id": "b", "updated_at": "1"}]
        remote = [{"id": "b", "updated_at": "3"}, {"id": "c", "updated_at": "1"}]
        once = merge.merge_lww_by_updated_at(local, remote, tombstones=True)
        twice = merge.merge_lww_by_updated_at(once.rows, remote, tombstones=True)
        assert once.rows == twice.rows  # re-applying the same remote changes nothing

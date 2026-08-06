"""DURABILITY-AND-SYNC §4.1 / DAS-6c-ii-d — reconcile a peer's rows into the live store.

The bridge that composes read-local → merge → apply. The crown check is criterion 4 at this
layer: a task made on A and one made on B both exist on both after one reconcile each way, and
a delete on A stays deleted on B. Non-row kinds are declined (routed elsewhere), and a poison
entry yields a payload-bad verdict rather than aborting the pull.
"""

from __future__ import annotations

import json

from personalclaw.durability import inventory as inv
from personalclaw.durability import reconcile
from personalclaw.durability.cursor import CONSUMED, PAYLOAD_BAD
from personalclaw.durability.shards import _json_rows_from_entity_dir


def _entity_entry(**kw) -> inv.StateEntry:
    base = dict(
        id="tasks",
        kind=inv.KIND_JSON_ENTITY_DIR,
        path="tasks",
        domain="knowledge",
        merge=inv.MERGE_UNION_BY_ID,
        tombstones=True,
    )
    base.update(kw)
    return inv.StateEntry(**base)


def _write_entity(home, entry, rid, data):
    d = home / entry.path
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{rid}.json").write_text(json.dumps(data), encoding="utf-8")


class TestReconcileRowEntry:
    def test_remote_only_row_is_brought_in(self, tmp_path):
        home = tmp_path / "home"
        entry = _entity_entry()
        _write_entity(home, entry, "local1", {"title": "mine"})
        remote = [{"id": "remote1", "data": {"title": "theirs"}}]
        r = reconcile.reconcile_entry(home, entry, remote)
        assert r.handled and r.verdict == CONSUMED and r.added == 1
        ids = {row["id"] for row in _json_rows_from_entity_dir(home / "tasks")}
        assert ids == {"local1", "remote1"}  # union — nothing lost

    def test_empty_local_store_takes_all_remote(self, tmp_path):
        home = tmp_path / "home"
        entry = _entity_entry()
        remote = [{"id": "r1", "data": {}}, {"id": "r2", "data": {}}]
        r = reconcile.reconcile_entry(home, entry, remote)
        assert r.added == 2
        assert (home / "tasks" / "r1.json").exists()

    def test_tombstone_delete_propagates(self, tmp_path):
        home = tmp_path / "home"
        entry = _entity_entry()
        _write_entity(home, entry, "x", {"title": "here"})
        # Peer deleted x — reconcile its tombstone into our still-live store.
        r = reconcile.reconcile_entry(home, entry, [{"id": "x", "deleted_at": "2026-08-06"}])
        assert r.removed == 1
        assert not (home / "tasks" / "x.json").exists()  # deletion propagated


class TestConvergence:
    """Criterion 4 at the reconcile layer."""

    def test_two_machines_converge(self, tmp_path):
        entry = _entity_entry()
        a_home = tmp_path / "A"
        b_home = tmp_path / "B"
        _write_entity(a_home, entry, "task-a", {"t": "a"})
        _write_entity(b_home, entry, "task-b", {"t": "b"})
        # Each machine's rows as the other would receive them (export shape).
        a_rows = _json_rows_from_entity_dir(a_home / "tasks")
        b_rows = _json_rows_from_entity_dir(b_home / "tasks")
        # A pulls B; B pulls A.
        reconcile.reconcile_entry(a_home, entry, b_rows)
        reconcile.reconcile_entry(b_home, entry, a_rows)
        a_ids = {r["id"] for r in _json_rows_from_entity_dir(a_home / "tasks")}
        b_ids = {r["id"] for r in _json_rows_from_entity_dir(b_home / "tasks")}
        assert a_ids == b_ids == {"task-a", "task-b"}

    def test_delete_on_a_stays_deleted_on_b(self, tmp_path):
        entry = _entity_entry()
        b_home = tmp_path / "B"
        _write_entity(b_home, entry, "task-x", {"t": "live"})
        # A deleted task-x; its tombstone reaches B (which still has it live).
        reconcile.reconcile_entry(b_home, entry, [{"id": "task-x", "deleted_at": "2026-08-06"}])
        assert not (b_home / "tasks" / "task-x.json").exists()


class TestDeclineAndErrors:
    def test_sqlite_kind_is_declined_not_raised(self, tmp_path):
        entry = _entity_entry(
            id="memory_db",
            kind=inv.KIND_SQLITE,
            path="memory.db",
            merge=inv.MERGE_SQLITE_ATTACH_IGNORE,
        )
        r = reconcile.reconcile_entry(tmp_path, entry, [])
        assert r.handled is False  # routed to the DB path, not consumed here

    def test_tree_kind_is_declined(self, tmp_path):
        entry = _entity_entry(
            id="memory_faiss", kind=inv.KIND_TREE, path="memory.faiss", merge=inv.MERGE_REPLACE_ONLY
        )
        assert reconcile.reconcile_entry(tmp_path, entry, []).handled is False

    def test_handles_kind_predicate(self):
        assert reconcile.handles_kind(inv.KIND_JSON_ENTITY_DIR)
        assert reconcile.handles_kind(inv.KIND_JSONL_APPEND)
        assert not reconcile.handles_kind(inv.KIND_SQLITE)
        assert not reconcile.handles_kind(inv.KIND_TREE)

    def test_poison_entry_yields_payload_bad_not_a_crash(self, tmp_path, monkeypatch):
        # A merge that throws must be caught and reported as payload-bad so the cursor
        # advances past it rather than the whole pull aborting.
        entry = _entity_entry()

        def boom(*a, **k):
            raise RuntimeError("corrupt shard")

        monkeypatch.setattr(reconcile, "merge_rows", boom)
        r = reconcile.reconcile_entry(tmp_path, entry, [{"id": "x", "data": {}}])
        assert r.handled and r.verdict == PAYLOAD_BAD and "corrupt shard" in r.detail

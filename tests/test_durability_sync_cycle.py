"""DURABILITY-AND-SYNC §4.1 / DAS-6c-ii-i — one full sync cycle, and criterion 4 end to end.

run_sync_cycle assembles pull+merge+export+push against a shared transport. The crown test is
the plan's Success Criterion 4: two machines syncing through one shared object store converge —
a task made on A and a knowledge row on B both exist on both after a cycle each way, and a task
deleted on A stays deleted on B. Transport failures are contained, never raised.
"""

from __future__ import annotations

import json
import sqlite3

from personalclaw.durability.registry import REGISTRY_KEY
from personalclaw.durability.sync_cycle import read_registry, run_sync_cycle
from personalclaw.sync_transports.base import (
    ConnectionResult,
    PushResult,
    RemoteRef,
    SyncObject,
    SyncTransportProvider,
)


class SharedStore(SyncTransportProvider):
    """One in-memory object store two machines share — a stand-in for a git repo / synced folder."""

    name = "shared"

    def __init__(self):
        self.objects: dict[str, bytes] = {}

    def push(self, objects):
        for o in objects:
            self.objects.setdefault(o.key, o.data)  # insert-only, idempotent
        return PushResult(pushed=len(objects), outcome="delivered")

    def list_remote(self, prefix: str = ""):
        return [RemoteRef(key=k) for k in self.objects if k.startswith(prefix)]

    def pull(self, refs):
        return [
            SyncObject(key=r.key, data=self.objects[r.key]) for r in refs if r.key in self.objects
        ]

    def cas_registry(self, expected_sha, data):
        # Single shared registry object; last writer wins in this simple fake (the real CAS is
        # the transport's job — the cycle's retry loop is unit-tested in the push-engine tests).
        self.objects[REGISTRY_KEY] = data
        return True

    def test(self):  # pragma: no cover
        return ConnectionResult(ok=True)


class FailingStore(SharedStore):
    def list_remote(self, prefix: str = ""):
        raise RuntimeError("network down")


def _task(home, tid, data):
    d = home / "tasks"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{tid}.json").write_text(json.dumps(data), encoding="utf-8")


def _task_ids(home):
    d = home / "tasks"
    return {p.stem for p in d.glob("*.json")} if d.is_dir() else set()


class TestOneCycle:
    def test_first_publish_into_empty_store(self, tmp_path):
        home = tmp_path / "A"
        _task(home, "t1", {"id": "t1", "title": "hello"})
        store = SharedStore()
        report = run_sync_cycle(store, home, self_id="A", now="t")
        assert report.ok and report.seq_published == 1
        # The registry now knows A, and A's shard objects are in the store.
        assert read_registry(store).seq_of("A") == 1
        assert any("machines/A/seq-0001/" in k for k in store.objects)

    def test_transport_failure_is_contained(self, tmp_path):
        home = tmp_path / "A"
        _task(home, "t1", {"id": "t1"})
        report = run_sync_cycle(FailingStore(), home, self_id="A", now="t")
        assert report.ok is False and "network down" in report.error  # not raised


class TestCriterion4Convergence:
    """The plan's Success Criterion 4, end to end through run_sync_cycle."""

    def test_two_machines_converge_on_tasks(self, tmp_path):
        store = SharedStore()
        a_home = tmp_path / "A"
        b_home = tmp_path / "B"
        _task(a_home, "task-a", {"id": "task-a", "title": "from A"})
        _task(b_home, "task-b", {"id": "task-b", "title": "from B"})

        # Each machine runs a cycle: publish its own, pull the other's. Two rounds so both
        # publish before both pull (a shared store, one object namespace).
        run_sync_cycle(store, a_home, self_id="A", now="t1")  # A publishes seq 1
        run_sync_cycle(store, b_home, self_id="B", now="t2")  # B publishes + pulls A
        run_sync_cycle(store, a_home, self_id="A", now="t3")  # A pulls B

        assert _task_ids(a_home) == _task_ids(b_home) == {"task-a", "task-b"}

    def test_delete_on_a_stays_deleted_on_b(self, tmp_path):
        store = SharedStore()
        a_home = tmp_path / "A"
        b_home = tmp_path / "B"
        # Both start with task-x; use a tombstone-bearing entry (tasks has tombstones=True).
        _task(a_home, "task-x", {"id": "task-x", "title": "live"})
        # A deletes task-x → write a tombstone row in its place.
        (a_home / "tasks" / "task-x.json").write_text(
            json.dumps({"id": "task-x", "deleted_at": "2026-08-06"}), encoding="utf-8"
        )
        _task(b_home, "task-x", {"id": "task-x", "title": "still here on B"})

        run_sync_cycle(store, a_home, self_id="A", now="t1")  # A publishes the tombstone
        run_sync_cycle(store, b_home, self_id="B", now="t2")  # B pulls A's tombstone

        # On B, task-x is gone — the entity-dir apply removes the file on a tombstone, so a
        # deleted task is not left as a live row (no resurrection).
        assert not (b_home / "tasks" / "task-x.json").exists()


class TestDatabasesRideAlong:
    def test_sqlite_row_syncs_through_a_full_cycle(self, tmp_path):
        store = SharedStore()
        a_home = tmp_path / "A"
        a_home.mkdir()
        conn = sqlite3.connect(str(a_home / "memory.db"))
        conn.execute(
            "CREATE TABLE semantic_memory(key TEXT PRIMARY KEY, value_json TEXT, "
            "confidence REAL, source TEXT, created_at TEXT, updated_at TEXT, "
            "embedding BLOB, is_deleted INTEGER DEFAULT 0)"
        )
        conn.execute(
            "INSERT INTO semantic_memory(key, value_json, is_deleted) VALUES ('k','\"v\"',0)"
        )
        conn.commit()
        conn.close()
        b_home = tmp_path / "B"

        run_sync_cycle(store, a_home, self_id="A", now="t1")  # A publishes memory.db copy
        run_sync_cycle(store, b_home, self_id="B", now="t2")  # B pulls + DB-merges it

        merged = sqlite3.connect(str(b_home / "memory.db"))
        keys = [r[0] for r in merged.execute("SELECT key FROM semantic_memory")]
        merged.close()
        assert "k" in keys  # the peer's memory row is on B after one cycle each way


class TestReadRegistry:
    def test_empty_store_reads_empty_registry(self, tmp_path):
        assert read_registry(SharedStore()).machines == {}

    def test_reads_a_written_registry(self, tmp_path):
        home = tmp_path / "A"
        _task(home, "t", {"id": "t"})
        store = SharedStore()
        run_sync_cycle(store, home, self_id="A", now="t")
        assert read_registry(store).seq_of("A") == 1

"""DURABILITY-AND-SYNC §4.1 / DAS-6c-ii-e — the transport-driven pull half of the cycle.

Drives the pure pieces against a fake in-memory transport: walk the registry's unseen peer
seqs oldest-first, pull each, import + reconcile into the live store, and advance the cursor
only on a consumed seq. A DB entry with no merger seam holds the seq (re-pulled later, never
silently skipped); a held seq stops contiguity so seq+1 isn't pulled past its prerequisite.
"""

from __future__ import annotations

from personalclaw.durability import inventory as inv
from personalclaw.durability.cursor import CONSUMED, PREREQ_ABSENT, Cursor
from personalclaw.durability.pull_engine import pull_from_peers
from personalclaw.durability.registry import Registry, shard_prefix
from personalclaw.durability.shards import export_shards
from personalclaw.sync_transports.base import (
    ConnectionResult,
    PushResult,
    RemoteRef,
    SyncObject,
    SyncTransportProvider,
)


class FakeTransport(SyncTransportProvider):
    """An in-memory object store keyed by remote path — enough to exercise the engine."""

    name = "fake"

    def __init__(self):
        self.objects: dict[str, bytes] = {}

    def stage_export(self, home, peer_id: str, seq: int):
        """Export ``home``'s shards and stage them under the peer/seq prefix, as a push would."""
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            export_shards(home, out)
            prefix = shard_prefix(peer_id, seq)
            for p in out.rglob("*"):
                if p.is_file():
                    self.objects[prefix + p.relative_to(out).as_posix()] = p.read_bytes()

    def push(self, objects):  # pragma: no cover - not exercised by the pull engine
        for o in objects:
            self.objects.setdefault(o.key, o.data)
        return PushResult(pushed=len(objects))

    def list_remote(self, prefix: str = ""):
        return [
            RemoteRef(key=k, size=len(v)) for k, v in self.objects.items() if k.startswith(prefix)
        ]

    def pull(self, refs):
        return [
            SyncObject(key=r.key, data=self.objects[r.key]) for r in refs if r.key in self.objects
        ]

    def cas_registry(self, expected_sha, data):  # pragma: no cover
        return True

    def test(self):  # pragma: no cover
        return ConnectionResult(ok=True)


def _entity(home, rel_dir, rid, data):
    import json

    d = home / rel_dir
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{rid}.json").write_text(json.dumps(data), encoding="utf-8")


def _tasks_entry_present():
    """The real inventory must carry a row-kind 'tasks' entry for the convergence test."""
    e = inv.by_id("tasks")
    return e is not None and e.kind == inv.KIND_JSON_ENTITY_DIR


class TestPullSweep:
    def test_pulls_and_reconciles_a_peer_seq(self, tmp_path):
        assert _tasks_entry_present()
        peer_home = tmp_path / "peer"
        _entity(peer_home, "tasks", "peer-task", {"title": "from peer"})
        tr = FakeTransport()
        tr.stage_export(peer_home, "peerA", 1)

        reg = Registry()
        reg.bump("peerA", manifest_sha="s", now="t")  # peer published seq 1
        local = tmp_path / "local"
        cursor = Cursor(tmp_path / "sync")
        report = pull_from_peers(tr, local, reg, cursor, self_id="me")

        assert report.advanced == 1
        assert (local / "tasks" / "peer-task.json").exists()  # peer's row is now live
        assert cursor.seq_of("peerA") == 1

    def test_cursor_skips_already_seen(self, tmp_path):
        tr = FakeTransport()
        _entity(tmp_path / "peer", "tasks", "x", {})
        tr.stage_export(tmp_path / "peer", "peerA", 1)
        reg = Registry()
        reg.bump("peerA", manifest_sha="s", now="t")
        cursor = Cursor(tmp_path / "sync")
        cursor.record("peerA", 1, CONSUMED)  # already consumed seq 1
        report = pull_from_peers(tr, tmp_path / "local", reg, cursor, self_id="me")
        assert report.outcomes == []  # nothing new to pull

    def test_partial_push_holds_the_seq(self, tmp_path):
        # Registry says peerA is at seq 1, but no objects were staged (push not finished).
        tr = FakeTransport()
        reg = Registry()
        reg.bump("peerA", manifest_sha="s", now="t")
        cursor = Cursor(tmp_path / "sync")
        report = pull_from_peers(tr, tmp_path / "local", reg, cursor, self_id="me")
        assert report.advanced == 0 and report.held == 1
        assert report.outcomes[0].verdict == PREREQ_ABSENT
        assert cursor.seq_of("peerA") == 0  # not advanced — retried next cycle

    def test_held_seq_stops_contiguity(self, tmp_path):
        # peerA at seq 2: seq 1 staged, seq 2 NOT (partial). Pulling must consume 1 and stop.
        tr = FakeTransport()
        _entity(tmp_path / "peer", "tasks", "t1", {})
        tr.stage_export(tmp_path / "peer", "peerA", 1)
        reg = Registry()
        reg.bump("peerA", manifest_sha="s", now="t")
        reg.bump("peerA", manifest_sha="s2", now="t2")  # seq 2 announced, not staged
        cursor = Cursor(tmp_path / "sync")
        report = pull_from_peers(tr, tmp_path / "local", reg, cursor, self_id="me")
        assert cursor.seq_of("peerA") == 1  # consumed 1, held 2, did not skip to 2
        verdicts = [o.verdict for o in report.outcomes]
        assert verdicts == [CONSUMED, PREREQ_ABSENT]


class TestDbSeam:
    def test_db_entry_holds_without_a_merger(self, tmp_path):
        # Stage a peer export that includes a real sqlite entry (memory.db), no db_merger.
        import sqlite3

        peer_home = tmp_path / "peer"
        peer_home.mkdir()
        db = peer_home / "memory.db"
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
        conn.execute("INSERT INTO t VALUES (1, 'x')")
        conn.commit()
        conn.close()
        tr = FakeTransport()
        tr.stage_export(peer_home, "peerA", 1)
        reg = Registry()
        reg.bump("peerA", manifest_sha="s", now="t")
        cursor = Cursor(tmp_path / "sync")
        report = pull_from_peers(tr, tmp_path / "local", reg, cursor, self_id="me")
        assert report.advanced == 0  # held — a DB entry can't be row-merged
        assert "memory_db" in report.outcomes[0].deferred_db
        assert cursor.seq_of("peerA") == 0

    def test_db_merger_seam_lets_the_seq_advance(self, tmp_path):
        import sqlite3

        peer_home = tmp_path / "peer"
        peer_home.mkdir()
        db = peer_home / "memory.db"
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY)")
        conn.commit()
        conn.close()
        tr = FakeTransport()
        tr.stage_export(peer_home, "peerA", 1)
        reg = Registry()
        reg.bump("peerA", manifest_sha="s", now="t")
        cursor = Cursor(tmp_path / "sync")
        seen_entries = []

        def merger(entry, shard_dir):
            seen_entries.append(entry.id)
            return CONSUMED

        pull_from_peers(tr, tmp_path / "local", reg, cursor, self_id="me", db_merger=merger)
        assert cursor.seq_of("peerA") == 1  # advanced with the seam
        assert "memory_db" in seen_entries

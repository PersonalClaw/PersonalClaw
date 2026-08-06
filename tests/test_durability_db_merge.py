"""DURABILITY-AND-SYNC §4.1 / DAS-6c-ii-h — the sqlite DB-merge seam.

make_db_merger returns the db_merger callback the pull engine calls for sqlite/tree entries.
It ATTACH-merges the staged whole-DB copy into the live DB via the proven snapshot merge
functions: memory.db keeps its is_deleted=0 filter (no resurrection), other DBs use the generic
INSERT OR IGNORE. A first sync onto a fresh machine copies the DB wholesale; a missing copy holds;
a bad merge is payload-bad.
"""

from __future__ import annotations

import sqlite3

from personalclaw.durability import inventory as inv
from personalclaw.durability.cursor import CONSUMED, PAYLOAD_BAD, PREREQ_ABSENT
from personalclaw.durability.db_merge import make_db_merger


def _sqlite_entry(entry_id="learning_db", path="learning.db"):
    return inv.StateEntry(
        id=entry_id,
        kind=inv.KIND_SQLITE,
        path=path,
        domain="knowledge",
        merge=inv.MERGE_SQLITE_ATTACH_IGNORE,
    )


def _make_db(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
    conn.executemany("INSERT INTO t VALUES (?, ?)", rows)
    conn.commit()
    conn.close()


def _rows(path):
    conn = sqlite3.connect(str(path))
    out = conn.execute("SELECT id, v FROM t ORDER BY id").fetchall()
    conn.close()
    return out


def _stage_db_copy(shard_dir, entry_id, src_db):
    """Mimic the exporter's db/<entry>.db staging (DAS-6c-ii-g)."""
    import shutil

    dbdir = shard_dir / "db"
    dbdir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src_db, dbdir / f"{entry_id}.db")


class TestFreshMachine:
    def test_first_sync_copies_the_db_wholesale(self, tmp_path):
        home = tmp_path / "home"
        shard_dir = tmp_path / "shards"
        peer_db = tmp_path / "peer.db"
        _make_db(peer_db, [(1, "a"), (2, "b")])
        _stage_db_copy(shard_dir, "learning_db", peer_db)

        merger = make_db_merger(home)
        verdict = merger(_sqlite_entry(), shard_dir)
        assert verdict == CONSUMED
        assert _rows(home / "learning.db") == [(1, "a"), (2, "b")]  # lossless copy


class TestMergeIntoExisting:
    def test_insert_or_ignore_union(self, tmp_path):
        home = tmp_path / "home"
        _make_db(home / "learning.db", [(1, "local")])  # live already has row 1
        shard_dir = tmp_path / "shards"
        peer_db = tmp_path / "peer.db"
        _make_db(peer_db, [(1, "remote"), (2, "remote-only")])
        _stage_db_copy(shard_dir, "learning_db", peer_db)

        assert make_db_merger(home)(_sqlite_entry(), shard_dir) == CONSUMED
        rows = dict(_rows(home / "learning.db"))
        assert rows[1] == "local"  # local row kept (INSERT OR IGNORE)
        assert rows[2] == "remote-only"  # remote-only row brought in


class TestMemoryDbNoResurrection:
    def test_memory_db_routes_through_is_deleted_filter(self, tmp_path, monkeypatch):
        # memory.db must go through _merge_memory (is_deleted=0), NOT the generic path.
        home = tmp_path / "home"
        (home).mkdir()
        # A pre-existing live memory.db so the merge path (not the fresh-copy path) is taken.
        (home / "memory.db").write_bytes(b"placeholder")
        shard_dir = tmp_path / "shards"
        (shard_dir / "db").mkdir(parents=True)
        (shard_dir / "db" / "memory_db.db").write_bytes(b"src")

        called = {}

        def fake_merge_memory(src, dst):
            called["memory"] = (src, dst)

        def fake_attach(src, dst, label):
            called["attach"] = label

        from personalclaw import snapshot

        monkeypatch.setattr(snapshot, "_merge_memory", fake_merge_memory)
        monkeypatch.setattr(snapshot, "_merge_sqlite_attach", fake_attach)

        entry = inv.StateEntry(
            id="memory_db",
            kind=inv.KIND_SQLITE,
            path="memory.db",
            domain="memory",
            merge=inv.MERGE_SQLITE_ATTACH_IGNORE,
        )
        assert make_db_merger(home)(entry, shard_dir) == CONSUMED
        assert "memory" in called and "attach" not in called  # routed to _merge_memory


class TestVerdicts:
    def test_tree_entry_is_consumed_nothing_to_merge(self, tmp_path):
        entry = inv.StateEntry(
            id="memory_faiss",
            kind=inv.KIND_TREE,
            path="memory.faiss",
            domain="memory",
            merge=inv.MERGE_REPLACE_ONLY,
        )
        assert make_db_merger(tmp_path)(entry, tmp_path / "shards") == CONSUMED

    def test_missing_db_copy_holds(self, tmp_path):
        # A sqlite entry with no staged db/ copy → hold, don't advance past unmerged data.
        shard_dir = tmp_path / "shards"
        shard_dir.mkdir()
        assert make_db_merger(tmp_path / "home")(_sqlite_entry(), shard_dir) == PREREQ_ABSENT

    def test_corrupt_merge_is_payload_bad(self, tmp_path, monkeypatch):
        home = tmp_path / "home"
        _make_db(home / "learning.db", [(1, "x")])
        shard_dir = tmp_path / "shards"
        peer_db = tmp_path / "peer.db"
        _make_db(peer_db, [(2, "y")])
        _stage_db_copy(shard_dir, "learning_db", peer_db)

        from personalclaw import snapshot

        def boom(src, dst, label):
            raise sqlite3.OperationalError("disk I/O error")

        monkeypatch.setattr(snapshot, "_merge_sqlite_attach", boom)
        assert make_db_merger(home)(_sqlite_entry(), shard_dir) == PAYLOAD_BAD


class TestEndToEndThroughPullEngine:
    """The db_merger is the seam the pull engine calls — prove the whole path syncs a real DB."""

    def test_peer_sqlite_db_syncs_onto_a_fresh_machine(self, tmp_path):
        import tempfile
        from pathlib import Path

        from personalclaw.durability.cursor import Cursor
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

        # A peer home whose memory.db carries a real row.
        peer_home = tmp_path / "peer"
        peer_home.mkdir()
        conn = sqlite3.connect(str(peer_home / "memory.db"))
        conn.execute(
            "CREATE TABLE semantic_memory("
            "key TEXT PRIMARY KEY, value_json TEXT, confidence REAL, source TEXT, "
            "created_at TEXT, updated_at TEXT, embedding BLOB, is_deleted INTEGER DEFAULT 0)"
        )
        conn.execute(
            "INSERT INTO semantic_memory(key, value_json, is_deleted) VALUES ('pref.x','\"v\"',0)"
        )
        conn.commit()
        conn.close()

        class Tr(SyncTransportProvider):
            name = "fake"

            def __init__(self):
                self.objects = {}

            def stage(self, home, peer_id, seq):
                with tempfile.TemporaryDirectory() as t:
                    out = Path(t)
                    export_shards(home, out, include_databases=True)  # DB copies staged
                    pre = shard_prefix(peer_id, seq)
                    for p in out.rglob("*"):
                        if p.is_file():
                            self.objects[pre + p.relative_to(out).as_posix()] = p.read_bytes()

            def push(self, o):  # pragma: no cover
                return PushResult()

            def list_remote(self, prefix=""):
                return [RemoteRef(key=k) for k in self.objects if k.startswith(prefix)]

            def pull(self, refs):
                return [SyncObject(key=r.key, data=self.objects[r.key]) for r in refs]

            def cas_registry(self, e, d):  # pragma: no cover
                return True

            def test(self):  # pragma: no cover
                return ConnectionResult(ok=True)

        tr = Tr()
        tr.stage(peer_home, "peerA", 1)
        reg = Registry()
        reg.bump("peerA", manifest_sha="s", now="t")
        local = tmp_path / "local"
        cursor = Cursor(tmp_path / "sync")

        report = pull_from_peers(
            tr, local, reg, cursor, self_id="me", db_merger=make_db_merger(local)
        )
        assert report.advanced == 1  # the DB seq consumed, not held
        # The peer's memory row is now in the local DB (created fresh via wholesale copy).
        merged = sqlite3.connect(str(local / "memory.db"))
        keys = [r[0] for r in merged.execute("SELECT key FROM semantic_memory")]
        merged.close()
        assert "pref.x" in keys

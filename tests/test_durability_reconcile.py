"""Reconcile a peer's rows into the live store.

The bridge that composes read-local → merge → apply. The crown check is convergence at this
layer: a task made on A and one made on B both exist on both after one reconcile each way, and
a delete on A stays deleted on B. Non-row kinds are declined (routed elsewhere), and a poison
entry yields a payload-bad verdict rather than aborting the pull.
"""

from __future__ import annotations

import json

from personalclaw.durability import inventory as inv
from personalclaw.durability import reconcile
from personalclaw.durability.cursor import CONSUMED, PAYLOAD_BAD
from personalclaw.durability.shards import read_entity_dir


def _entity_entry(**kw) -> inv.StateEntry:
    base = dict(
        id="tasks",
        kind=inv.KIND_JSON_ENTITY_DIR,
        path="tasks",
        domain="knowledge",
        merge=inv.MERGE_UNION_BY_ID,
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
        ids = {row["id"] for row in read_entity_dir(entry, home / "tasks").rows}
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
    """Two-machine convergence at the reconcile layer."""

    def test_two_machines_converge(self, tmp_path):
        entry = _entity_entry()
        a_home = tmp_path / "A"
        b_home = tmp_path / "B"
        _write_entity(a_home, entry, "task-a", {"t": "a"})
        _write_entity(b_home, entry, "task-b", {"t": "b"})
        # Each machine's rows as the other would receive them (export shape).
        a_rows = read_entity_dir(entry, a_home / "tasks").rows
        b_rows = read_entity_dir(entry, b_home / "tasks").rows
        # A pulls B; B pulls A.
        reconcile.reconcile_entry(a_home, entry, b_rows)
        reconcile.reconcile_entry(b_home, entry, a_rows)
        a_ids = {r["id"] for r in read_entity_dir(entry, a_home / "tasks").rows}
        b_ids = {r["id"] for r in read_entity_dir(entry, b_home / "tasks").rows}
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


class TestDeletes:
    """A peer's delete, in either store shape, and what a read could not read."""

    def _tags(self, home, *records):
        home.mkdir(parents=True, exist_ok=True)
        (home / "tags.json").write_text(json.dumps(list(records)), encoding="utf-8")

    def _sha(self, entry, row):
        from personalclaw.durability import conflicts

        return conflicts.row_sha(conflicts.compared(entry, row))

    def test_a_peers_delete_takes_one_record_out_of_a_store_of_records(self, tmp_path):
        entry = inv.by_id("tags")
        home = tmp_path / "home"
        work, home_tag = {"id": "work", "name": "Work"}, {"id": "home", "name": "Home"}
        self._tags(home, work, home_tag)
        delete = {"id": "work", "deleted_at": "2026-09-02", "held": [self._sha(entry, work)]}
        document = {"id": "tags.json", "data": [home_tag]}

        r = reconcile.reconcile_entry(home, entry, [document, delete], peer="B", now="t")

        assert json.loads((home / "tags.json").read_text()) == [home_tag]
        assert (r.removed, r.updated, r.new_ancestors.get("work")) == (1, 0, None)
        assert r.deleted_there["work"].by == "B" and r.deleted_there["work"].at == "t"

    def test_taking_the_other_machines_delete_removes_the_record(self, tmp_path):
        tags = inv.by_id("tags")
        home = tmp_path / "home"
        self._tags(home, {"id": "work", "name": "Work"}, {"id": "home", "name": "Home"})
        delete = {"id": "work", "deleted_at": "2026-09-02"}

        applied, edited = reconcile.take_in(tags, home / "tags.json", "work", delete)

        assert (applied.removed, edited) == (1, False)
        assert json.loads((home / "tags.json").read_text()) == [{"id": "home", "name": "Home"}]
        entry = _entity_entry()
        _write_entity(home, entry, "x", {"t": "x"})
        applied, _ = reconcile.take_in(entry, home / "tasks", "x", {"id": "x", "deleted_at": "t"})
        assert applied.removed == 1 and not (home / "tasks" / "x.json").exists()

    def test_what_a_read_could_not_read_is_unread_and_not_deleted(self, tmp_path):
        from personalclaw.durability import shards

        entry = _entity_entry()
        unread = shards.unread(
            entry,
            shards.Read(
                left_out={"tasks/bad.json": "not valid JSON", "tasks/sub": shards.NOT_LISTED}
            ),
        )
        assert unread("bad") and unread("sub/x") and not unread("good") and not unread("subx")
        everything = shards.Read(left_out={"tasks": shards.NOT_LISTED})
        assert shards.unread(entry, everything)("anything")

    def test_a_store_that_cannot_be_read_whole_holds_nothing_known(self, tmp_path):
        home = tmp_path / "home"
        assert reconcile.held_shas(home, inv.by_id("tags")) is None  # not there
        home.mkdir()
        (home / "tags.json").write_text("{not json", encoding="utf-8")
        assert reconcile.held_shas(home, inv.by_id("tags")) is None
        (home / "tags.json").write_text('{"another": "shape"}', encoding="utf-8")
        assert reconcile.held_shas(home, inv.by_id("tags")) is None
        self._tags(home, {"id": "work", "name": "Work"})
        shas, unknown = reconcile.held_shas(home, inv.by_id("tags"))
        assert set(shas) == {"work"} and not unknown("work")

    def test_without_a_queue_the_peers_side_is_taken_and_only_its_deletes_are_its(self, tmp_path):
        """Nothing can hold a conflict without a queue, so each pair goes the peer's way: its
        edit of a record deleted here comes in, its delete of one edited here is applied. Only the
        second is the peer's delete."""
        entry = _entity_entry()
        home = tmp_path / "home"
        _write_entity(home, entry, "mine", {"t": "edited here"})
        rows = [
            {"id": "gone-here", "data": {"t": "edited there"}},
            {"id": "mine", "deleted_at": "2026-09-02", "held": ["a version never here"]},
        ]

        r = reconcile.reconcile_entry(
            home, entry, rows, history={"gone-here": ["the version deleted"]}, peer="B"
        )

        assert (home / "tasks" / "gone-here.json").exists(), "the peer's edit was dropped"
        assert not (home / "tasks" / "mine.json").exists()
        assert set(r.deleted_there) == {"mine"} and r.deleted_there["mine"].by == "B"

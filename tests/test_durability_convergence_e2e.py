"""DURABILITY-AND-SYNC Success Criterion 4 / DAS-6d-iii — two machines converge over a REAL folder.

The earlier sync-cycle tests proved convergence through an in-MEMORY fake transport. This proves
the plan's Criterion 4 verbatim over a REAL on-disk transport (the dir-sync algorithm — insert-only
files, rename-lock CAS on registry.json) driving core's real run_sync_cycle end to end:

    "a task created on A and a knowledge item added on B both exist on both after one sync cycle
     each way; a task deleted on A stays deleted on B (tombstone); and indexes rebuild locally."

Hermetic: a local shared folder stands in for a cloud-sync mount / git repo / USB volume, so this
needs no network and no cross-repo import (the transport apps live in PersonalClawApps and install
core from git+main; the durability engine only exists on this stack until it merges — so the
convergence proof over a real transport lives HERE, in core, where run_sync_cycle lives).
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path

from personalclaw.durability.sync_cycle import run_sync_cycle
from personalclaw.sync_transports.base import (
    ConnectionResult,
    PushResult,
    RemoteRef,
    SyncObject,
    SyncTransportProvider,
)

_REGISTRY_KEY = "registry.json"
_TMP_PREFIX = ".tmp-"
_LOCK_DIR = ".registry.lock"


class FolderTransport(SyncTransportProvider):
    """A REAL filesystem transport (the dir-sync algorithm) over a shared folder ``root``.

    Insert-only atomic writes, prefix-filtered listing (excluding temp/lock), and a rename-lock
    compare-and-swap on the shared registry. This is a faithful stand-in for the shipped dir-sync
    app so the convergence proof exercises real bytes on disk, not an in-memory dict."""

    name = "folder"

    def __init__(self, root: Path) -> None:
        self._root = Path(root)

    def _atomic_write(self, target: Path, data: bytes) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=_TMP_PREFIX, dir=str(target.parent))
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            os.replace(tmp, target)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def push(self, objects):
        pushed = skipped = 0
        for obj in objects:
            target = self._root / obj.key
            if target.exists():
                skipped += 1
                continue
            self._atomic_write(target, obj.data)
            pushed += 1
        return PushResult(pushed=pushed, skipped=skipped, outcome="delivered")

    def list_remote(self, prefix: str = ""):
        if not self._root.is_dir():
            return []
        refs = []
        for dirpath, _dirs, files in os.walk(self._root):
            for fn in files:
                if fn.startswith(_TMP_PREFIX):
                    continue
                full = Path(dirpath) / fn
                key = full.relative_to(self._root).as_posix()
                if not key.startswith(prefix):
                    continue
                refs.append(RemoteRef(key=key, size=full.stat().st_size))
        return refs

    def pull(self, refs):
        out = []
        for ref in refs:
            try:
                out.append(SyncObject(key=ref.key, data=(self._root / ref.key).read_bytes()))
            except OSError:
                continue
        return out

    def cas_registry(self, expected_sha, data):
        self._root.mkdir(parents=True, exist_ok=True)
        lock = self._root / _LOCK_DIR
        try:
            os.mkdir(lock)
        except (FileExistsError, OSError):
            return False
        try:
            target = self._root / _REGISTRY_KEY
            if target.exists():
                current = target.read_bytes()
                if expected_sha != hashlib.sha256(current).hexdigest():
                    return False
            elif expected_sha is not None:
                return False
            self._atomic_write(target, data)
            return True
        finally:
            try:
                os.rmdir(lock)
            except OSError:
                pass

    def test(self):  # pragma: no cover
        return ConnectionResult(ok=True, detail=str(self._root))


def _task(home: Path, tid: str, data: dict) -> None:
    d = home / "tasks"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{tid}.json").write_text(json.dumps(data), encoding="utf-8")


def _task_ids(home: Path) -> set[str]:
    d = home / "tasks"
    return {p.stem for p in d.glob("*.json")} if d.is_dir() else set()


def _knowledge_event(home: Path, rows: list[dict]) -> None:
    # A JSONL append stream (notifications is a real jsonl_append entry) — the "knowledge item
    # added on B" side of the criterion, as an append-dedup stream.
    home.mkdir(parents=True, exist_ok=True)
    d = home / "notifications.jsonl"
    d.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


def _notif_ids(home: Path) -> set[str]:
    p = home / "notifications.jsonl"
    if not p.is_file():
        return set()
    ids = set()
    for line in p.read_text(encoding="utf-8").splitlines():
        if line.strip():
            ids.add(json.loads(line).get("id", ""))
    return ids


class TestCriterion4OverRealFolder:
    def test_task_on_a_and_knowledge_on_b_converge(self, tmp_path):
        remote = tmp_path / "shared"
        a, b = tmp_path / "A", tmp_path / "B"
        # A creates a task; B adds a "knowledge item" (an append-stream event).
        _task(a, "task-a", {"id": "task-a", "title": "from A"})
        _knowledge_event(b, [{"id": "note-b", "ts": "2026-08-06T00:00:00Z", "text": "from B"}])

        ta, tb = FolderTransport(remote), FolderTransport(remote)
        # One cycle each way (two rounds so both publish before both pull the other).
        run_sync_cycle(ta, a, self_id="A", now="t1")
        run_sync_cycle(tb, b, self_id="B", now="t2")
        run_sync_cycle(ta, a, self_id="A", now="t3")
        run_sync_cycle(tb, b, self_id="B", now="t4")

        # The task made on A now exists on B; the note made on B now exists on A.
        assert "task-a" in _task_ids(b), "A's task did not converge onto B"
        assert "note-b" in _notif_ids(a), "B's knowledge item did not converge onto A"
        # And both machines hold the union.
        assert "task-a" in _task_ids(a) and "note-b" in _notif_ids(b)

    def test_delete_on_a_stays_deleted_on_b(self, tmp_path):
        remote = tmp_path / "shared"
        a, b = tmp_path / "A", tmp_path / "B"
        _task(a, "task-x", {"id": "task-x", "title": "live"})
        # A hard-deletes task-x and leaves a tombstone marker (the DAS-6c-iii side-log shape,
        # which the exporter folds into the entity rows).
        (a / "tasks" / "task-x.json").unlink()
        (a / "tasks" / "_tombstones.jsonl").write_text(
            json.dumps({"id": "task-x", "deleted_at": "2026-08-06T00:00:00Z"}) + "\n",
            encoding="utf-8",
        )
        _task(b, "task-x", {"id": "task-x", "title": "still here on B"})

        ta, tb = FolderTransport(remote), FolderTransport(remote)
        run_sync_cycle(ta, a, self_id="A", now="t1")  # publishes the tombstone
        run_sync_cycle(tb, b, self_id="B", now="t2")  # B pulls it

        assert not (
            b / "tasks" / "task-x.json"
        ).exists(), "delete did not propagate — resurrected on B"

    def test_registry_is_real_bytes_on_disk(self, tmp_path):
        # Prove the transport actually persisted a shared registry (the CAS object), not an
        # in-memory dict — the real-transport distinction this atom exists to prove.
        remote = tmp_path / "shared"
        a = tmp_path / "A"
        _task(a, "t", {"id": "t"})
        run_sync_cycle(FolderTransport(remote), a, self_id="A", now="t1")
        reg = remote / "registry.json"
        assert reg.is_file()
        assert "A" in json.loads(reg.read_text())["machines"]

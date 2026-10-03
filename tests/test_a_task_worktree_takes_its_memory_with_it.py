"""A task's worktree takes the memory its worker kept with it, and Doctor has nothing to report.

A Code loop runs each parallel task in a git worktree of its own, and the task's worker session
works there. Memory is partitioned by working folder (`config.loader.memory_dir_for_cwd`), so each
worker got a partition of its own: its memories, and beside them the evidence its lessons stand on
(`learning.db`, `VectorMemoryStore._lesson_evidence_store`). The manifest claimed a partition's
memory database and not that evidence, so Doctor read "undeclared databases — these are in NO
snapshot", and the loop removed each worktree after the merge and left its partition behind for
good: a folder nothing can run in again, kept by nothing that reads it.

Now a partition's lesson evidence is declared like its memory database, a snapshot carries it and a
merge restore merges it, and a folder of PersonalClaw's own that sessions run in (a task's worktree,
a loop's folder) takes its partition with it when it goes, and at the next start for one an earlier
version left behind.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sqlite3
import subprocess
import tarfile
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

from personalclaw.durability import inventory
from personalclaw.loop import worktree

PROJECT = "p-0a1b2c3d"
RULE = "Run the formatter before every commit"

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


@pytest.fixture(autouse=True)
def _no_gateway(monkeypatch):
    monkeypatch.setattr("personalclaw.snapshot._running_gateway", lambda: None)


def _make_home(base: Path, monkeypatch) -> Path:
    home = (base / "home").resolve()
    home.mkdir(mode=0o700)
    (home / "config.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: home)
    return home


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    return _make_home(tmp_path, monkeypatch)


@pytest.fixture
def short_home(monkeypatch) -> Iterator[Path]:
    """A home on a short path. A partition is named for its folder, and a name longer than the
    folder-name budget is shortened to a hash (`config.loader._slug_cwd`), which nothing can read a
    folder back out of: a test's own temporary folder is long enough to get there."""
    base = Path(tempfile.mkdtemp(prefix="pcwt-", dir="/tmp"))
    try:
        yield _make_home(base, monkeypatch)
    finally:
        shutil.rmtree(base, ignore_errors=True)


def _repo(base: Path) -> str:
    """The workspace a Code loop works on: a repository with one commit and its owner's identity."""
    ws = base / "src" / "newsletter"
    ws.mkdir(parents=True)
    for args in (
        ["init", "-q"],
        ["config", "user.name", "Ada Example"],
        ["config", "user.email", "ada@example.com"],
    ):
        subprocess.run(["git", *args], cwd=ws, check=True)
    (ws / "README.md").write_text("# newsletter\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=ws, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=ws, check=True)
    return str(ws)


def _work_in(folder: str) -> Path:
    """What a session working in *folder* keeps: the partition's store as the chat runner asks for
    it, its vector store as `context._attach_vector_store` opens it once an embedding model is
    bound, and a lesson, whose evidence goes beside the partition's memory database. Returns the
    partition."""
    from personalclaw.config.loader import memory_dir_for_cwd
    from personalclaw.context import ContextBuilder
    from personalclaw.memory_service import service_for
    from personalclaw.vector_memory import VectorMemoryStore

    partition = memory_dir_for_cwd(folder)
    memory = ContextBuilder.get_memory_for(folder)
    if memory.vector_store is None:
        vs = VectorMemoryStore(db_path=partition / "memory_index.db")
        vs.init()
        memory.vector_store = vs
    assert service_for(memory).write_lesson(RULE, source="user_explicit")
    assert (partition / "learning.db").is_file(), "the lesson's evidence went somewhere else"
    return partition


def _evidence(db: Path) -> dict[str, int]:
    conn = sqlite3.connect(str(db))
    try:
        rows = conn.execute("SELECT lesson_key, observations FROM lesson_evidence").fetchall()
    finally:
        conn.close()
    return {key: count for key, count in rows}


def _snapshot(out: Path) -> Path:
    from personalclaw.snapshot import snapshot_main

    assert snapshot_main([str(out)]) == 0
    (tarball,) = out.glob("personalclaw-snapshot-*.tar.gz")
    return tarball


def _close_open_stores() -> None:
    """What a gateway that stops does to the stores it holds open."""
    from personalclaw import context
    from personalclaw.learning import lesson_confidence

    for store in list(context._memory_stores.values()):
        if store.vector_store is not None:
            store.vector_store.close()
    context._memory_stores.clear()
    lesson_confidence.reset_store()


# ── the evidence is claimed, carried and merged ─────────────────────────────────────────────────


def test_a_partitions_lesson_evidence_is_claimed_so_doctor_has_nothing_to_report(home):
    """🔴 Red on integration: `audit_home` named `workspace/_ext/<folder>/learning.db` undeclared,
    which is Home's "Durability degraded" and Doctor's "in NO snapshot"."""
    partition = _work_in("/srv/example/newsletter")
    rel = (partition / "learning.db").relative_to(home).as_posix()

    audit = inventory.audit_home(home)

    assert rel not in audit.undeclared_dbs
    assert audit.ok, (audit.unclaimed, audit.undeclared_dbs)
    assert inventory.partition_entry(rel).id == "learning_db"


def test_the_snapshot_carries_a_partitions_lesson_evidence(home, tmp_path):
    """🔴 Red on integration: the archive held the partition's memory database and not the evidence
    its lessons stand on, so a restored lesson read "no recorded observation yet" and left the
    prompt."""
    partition = _work_in("/srv/example/newsletter")
    _close_open_stores()
    rel = (partition / "learning.db").relative_to(home).as_posix()

    with tarfile.open(_snapshot(tmp_path / "out")) as tar:
        (member,) = [m for m in tar.getmembers() if m.name.endswith("/" + rel)]
        copy = tmp_path / "copy.db"
        copy.write_bytes(tar.extractfile(member).read())  # type: ignore[union-attr]

    assert list(_evidence(copy).values()) == [1]


def test_a_replace_restore_brings_a_partitions_lesson_evidence_back(home, tmp_path):
    """🔴 Red on integration: the restore moved `workspace/` aside and the archive had no copy of
    the partition's evidence to put back."""
    from personalclaw.snapshot import restore_main

    partition = _work_in("/srv/example/newsletter")
    _close_open_stores()
    kept = tmp_path / "kept.tar.gz"
    shutil.copy2(_snapshot(tmp_path / "out"), kept)

    shutil.rmtree(home)
    home.mkdir(mode=0o700)
    assert restore_main([str(kept), "--mode", "replace"]) == 0

    assert list(_evidence(partition / "learning.db").values()) == [1]


def test_a_merge_restore_merges_a_partitions_lesson_evidence_as_it_merges_learning_db(
    home, tmp_path
):
    """A merge takes the archive's evidence into a partition this home also has, row by row as it
    takes `learning.db`'s in, and keeps what this home recorded since.

    🔴 Red on integration: the partition's own file was kept as it was, so a lesson the merge
    brought back into the partition's memory came without the evidence it stood on."""
    from personalclaw.snapshot import restore_main

    partition = _work_in("/srv/example/newsletter")
    _close_open_stores()
    kept = tmp_path / "kept.tar.gz"
    shutil.copy2(_snapshot(tmp_path / "out"), kept)
    (archived_key,) = _evidence(partition / "learning.db")

    conn = sqlite3.connect(str(partition / "learning.db"))
    conn.execute("DELETE FROM lesson_evidence")
    conn.execute(
        "INSERT INTO lesson_evidence (lesson_key, observations) VALUES ('lesson.since', 2)"
    )
    conn.commit()
    conn.close()

    assert restore_main([str(kept), "--mode", "merge"]) == 0

    assert _evidence(partition / "learning.db") == {archived_key: 1, "lesson.since": 2}


# ── a worktree takes its partition with it ──────────────────────────────────────────────────────


def test_removing_a_task_worktree_takes_the_memory_its_worker_kept(home, tmp_path):
    """🔴 Red on integration: the worktree went and its partition stayed."""
    from personalclaw import context

    ws = _repo(tmp_path)
    path = worktree.add_worktree(ws, "t-00000001", PROJECT)
    assert path and os.path.isdir(path)
    partition = _work_in(path)

    worktree.remove_worktree(ws, "t-00000001", PROJECT)

    assert not os.path.exists(path)
    assert not partition.exists()
    assert str(partition) not in context._memory_stores, "a closed store is still handed out"
    assert inventory.audit_home(home).ok


def test_the_end_of_a_run_takes_the_partitions_of_the_worktrees_it_removes(home, tmp_path):
    """The end-of-run sweep removes each worktree that holds nothing unmerged, and its partition
    with it; one that holds work nobody merged stays, and so does its partition.

    🔴 Red on integration: the swept worktree's partition stayed."""
    ws = _repo(tmp_path)
    done = worktree.add_worktree(ws, "t-00000001", PROJECT)
    unmerged = worktree.add_worktree(ws, "t-00000002", PROJECT)
    assert done and unmerged
    done_partition, unmerged_partition = _work_in(done), _work_in(unmerged)
    Path(unmerged, "draft.md").write_text("not merged yet\n", encoding="utf-8")

    kept = worktree.sweep_finished(ws, ["t-00000001", "t-00000002"], PROJECT)

    assert [work.task_id for work in kept] == ["t-00000002"]
    assert not done_partition.exists()
    assert unmerged_partition.is_dir()


def test_doctors_durability_check_passes_after_a_loop_run(home, tmp_path):
    """A Code loop's run as its workers leave it: the loop's own session in the workspace, two
    task workers in their worktrees, each worker's change merged and its worktree removed.

    🔴 Red on integration: Doctor's tier-3 row failed with the workspace partition's evidence
    and both workers' as undeclared databases."""
    from personalclaw.resilience.doctor import DoctorContext, _probe_state_inventory

    ws = _repo(tmp_path)
    _work_in(ws)
    for task_id in ("t-00000001", "t-00000002"):
        path = worktree.add_worktree(ws, task_id, PROJECT)
        assert path
        _work_in(path)
        Path(path, f"{task_id}.md").write_text(f"done by {task_id}\n", encoding="utf-8")
        assert worktree.merge_worktree(ws, task_id, PROJECT).ok

    result = asyncio.run(_probe_state_inventory(DoctorContext(home=home)))

    assert result.ok, result.detail
    from personalclaw.config.loader import memory_dir_for_cwd

    left = sorted(p.name for p in (home / "workspace" / "_ext").iterdir())
    assert left == [memory_dir_for_cwd(ws).name], "a worker's partition outlived its worktree"


def test_deleting_a_project_takes_its_worktrees_partitions(home, tmp_path):
    """A project's delete removes its folder, the task worktrees in it included.

    🔴 Red on integration: their partitions stayed."""
    from personalclaw.tasks.hierarchy import HierarchyStore

    store = HierarchyStore()
    project = store.create_project(name="Newsletter")
    ws = _repo(tmp_path)
    path = worktree.add_worktree(ws, "t-00000001", project.id)
    assert path
    partition = _work_in(path)

    assert store.delete_project(project.id)

    assert not partition.exists()


def test_deleting_a_loop_takes_the_partition_of_its_folder(home):
    """A planner with no workspace works in its loop's own folder (`planning.runner`), and the
    loop's delete removes that folder.

    🔴 Red on integration: its partition stayed."""
    from personalclaw.loop import files as loop_files
    from personalclaw.loop import store as loop_store
    from personalclaw.loop.loop import Loop

    loop = loop_store.create(
        Loop(id="", name="Plan the launch", kind="research", task="Plan the spring launch")
    )
    folder = loop_files.loop_dir(loop.id)
    assert folder is not None
    partition = _work_in(str(folder))

    assert loop_store.delete(loop.id)

    assert not folder.exists()
    assert not partition.exists()


# ── what an earlier version left behind goes at the next start ─────────────────────────────────


class _State:
    def __init__(self) -> None:
        self._sessions: dict = {}

    def push_sessions_update(self) -> None:
        pass


class _Svc:
    def list_all(self) -> list:
        return []

    def get_by_session(self, name):
        return None


def _boot() -> None:
    from personalclaw.loop import watchdog

    asyncio.run(watchdog.LoopWatchdog(_State(), _Svc())._boot_sweep())


def test_the_next_start_removes_the_partitions_of_folders_that_are_gone(short_home):
    """Partitions a worktree, a project-less worktree and a loop's folder left behind when an
    earlier version removed them go at the next start; every other partition stays: a worktree
    still there, a project's own folder, a folder of the owner's, the shared one. A second start
    finds nothing more.

    🔴 Red on integration: every partition stayed."""
    home = short_home
    gone = [
        home / "projects" / PROJECT / "worktrees" / "t-00000001",
        home / "code" / "worktrees" / "0123456789ab" / "t-00000002",
        home / "loop" / "0a1b2c3d",
    ]
    live_worktree = home / "projects" / PROJECT / "worktrees" / "t-00000003"
    live_worktree.mkdir(parents=True)
    staying = [
        live_worktree,
        home / "projects" / PROJECT / "context",
        Path("/srv/example/newsletter"),
    ]
    gone_partitions = [_work_in(str(folder)) for folder in gone]
    staying_partitions = [_work_in(str(folder)) for folder in staying]
    _close_open_stores()
    shared = home / "workspace" / "_ext" / "_default"
    shared.mkdir()

    _boot()

    assert [p for p in gone_partitions if p.exists()] == []
    assert [p for p in staying_partitions if not p.is_dir()] == []
    assert shared.is_dir()

    _boot()

    assert [p for p in staying_partitions if not p.is_dir()] == []


def test_the_next_start_makes_a_partition_an_earlier_version_left_readable_private(home):
    """An earlier version made a partition's folders 0755 and its files 0644; the start makes
    them what a partition is from its first byte now.

    🔴 Red on integration: they stayed readable by every account on the machine."""
    partition = _work_in("/srv/example/newsletter")
    _close_open_stores()
    tree = [partition.parent, partition, *partition.rglob("*")]
    for path in tree:
        os.chmod(path, 0o755 if path.is_dir() else 0o644)

    _boot()

    loose = {
        str(p): oct(p.stat().st_mode & 0o777)
        for p in tree
        if p.stat().st_mode & 0o777 != (0o700 if p.is_dir() else 0o600)
    }
    assert loose == {}


def test_a_partition_whose_name_says_too_little_is_left(home):
    """A folder name past the length budget is shortened to a hash, which names no folder: such a
    partition is left, never guessed at."""
    folder = home / "projects" / PROJECT / "worktrees" / ("t-" + "0" * 62)
    partition = _work_in(str(folder))
    _close_open_stores()
    # A shortened name is the first 107 characters, an underscore and a 12-character hash.
    assert len(partition.name) == 120, "the test home is too short for the name to be shortened"

    _boot()

    assert partition.is_dir()


def test_the_folders_whose_partitions_go_are_the_folders_sessions_run_in(home):
    """The globs the start-up pass reads name exactly the folders the loop makes for a session:
    a task's worktree, with a project and without one, and a loop's own folder."""
    from personalclaw.loop import files as loop_files

    folders = (
        worktree.worktree_path("/srv/example/newsletter", "t-00000001", PROJECT),
        worktree.worktree_path("/srv/example/newsletter", "t-00000001"),
        str(loop_files.loops_root() / "0a1b2c3d"),
    )
    globs = worktree.SESSION_FOLDERS + loop_files.SESSION_FOLDERS
    for folder in folders:
        rel = Path(folder).relative_to(home).as_posix()
        assert any(inventory._matches_partition(rel, glob) for glob in globs), rel


def test_an_open_store_on_a_removed_partition_is_closed_with_it(home, tmp_path):
    """The store a worker's partition had open is closed and forgotten with the partition, so
    the next turn in the same folder starts a partition of its own rather than writing into one
    already removed."""
    from personalclaw.learning import lesson_confidence

    ws = _repo(tmp_path)
    path = worktree.add_worktree(ws, "t-00000001", PROJECT)
    assert path
    partition = _work_in(path)
    evidence = lesson_confidence.get_store(partition)

    worktree.remove_worktree(ws, "t-00000001", PROJECT)

    assert not partition.exists()
    assert lesson_confidence.get_store(partition) is not evidence

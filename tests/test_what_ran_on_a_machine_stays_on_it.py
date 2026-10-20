"""A workflow run, and a loop, stay on the machine that ran them.

🔴 A device sync carried a run's records: the run ledger (``workflows/runs.db``, merged row by row)
and each run's own folder (``workflows/runs/``, as files of ``workflows/``). The workflow watchdog
adopts every active run it finds, on every poll, and resumes it from its journal
(``workflows.watchdog._poll_once``), so another machine's running run executed a second time here,
on this machine's files, with the posture its steps had there. The merge never updates a row, so a
run the other machine then finished stayed running here too. A loop's records came the same way
(``loop/loops.db``), and the loop watchdog's first poll re-arms every running loop it finds
(``loop.watchdog._boot_sweep``). A merge restore, or an import of another machine's archive, brought
them in as well.

Each is now ``machine_local`` and not ``merged_in``: a sync never sends them, a pull never takes
another machine's in (even from a build that still sends them), and a merge restore or an import
leaves the archive's out. A backup carries them, and a replace restore brings them back with the
whole home.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import sqlite3
import tarfile
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from personalclaw.durability import inventory as inv
from personalclaw.durability.cursor import CONSUMED
from personalclaw.durability.db_merge import make_db_merger
from personalclaw.durability.shards import export_shards
from personalclaw.durability.sync_cycle import run_sync_cycle
from tests.test_durability_sync_cycle import SharedStore

RAN_HERE = (
    "workflow_runs_db",
    "workflow_runs",
    "loops_db",
    "loop",
    "workflow_workspaces",
    "subagents",
)


def _as(monkeypatch, home: Path) -> Path:
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    return home


def _running_run() -> str:
    from personalclaw.workflows import store as wf_store
    from personalclaw.workflows.models import RunStatus, WorkflowRun

    run = wf_store.create(WorkflowRun(id="", workflow_name="digest", status=RunStatus.RUNNING))
    wf_store.write_spec(run.id, {"name": "digest", "version": 1, "root": {"kind": "sequence"}})
    wf_store.write_state(run.id, {})
    return run.id


def _running_loop() -> str:
    from personalclaw.loop import files as loop_files
    from personalclaw.loop import store as loop_store
    from personalclaw.loop.loop import Loop, LoopStatus

    loop = loop_store.create(Loop(id="", name="Scan", kind="research", task="scan the market"))
    loop_store.update_status(loop.id, LoopStatus.RUNNING)
    folder = loop_files.loops_root() / loop.id
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "FINDINGS.md").write_text("what it found there\n", encoding="utf-8")
    return loop.id


def _a_machine_that_is_running_both(monkeypatch, home: Path) -> tuple[str, str]:
    _as(monkeypatch, home)
    return _running_run(), _running_loop()


def _what_would_resume_here(monkeypatch, home: Path) -> tuple[list[str], list[str]]:
    """The runs this machine's workflow watchdog adopts on a poll, and the loops its loop
    watchdog re-arms on its first one — each asked of the watchdog itself."""
    from personalclaw.loop.watchdog import LoopWatchdog
    from personalclaw.workflows.watchdog import WorkflowWatchdog

    _as(monkeypatch, home)
    adopted: list[str] = []
    rearmed: list[str] = []

    async def adopt(run) -> None:
        adopted.append(run.id)

    async def rearm(loop) -> bool:
        rearmed.append(loop.id)
        return True

    runs = WorkflowWatchdog()
    monkeypatch.setattr(runs, "_adopt", adopt)
    loops = LoopWatchdog(SimpleNamespace(_sessions={}), None)
    monkeypatch.setattr(loops, "_rearm_running", rearm)

    async def poll() -> None:
        await runs._poll_once()
        await loops._boot_sweep()

    asyncio.run(poll())
    return adopted, rearmed


def test_the_control_would_resume_both_on_the_machine_that_runs_them(tmp_path, monkeypatch):
    # The watchdogs are what makes this matter: on the machine that runs them, they pick both up.
    run_id, loop_id = _a_machine_that_is_running_both(monkeypatch, tmp_path / "A")
    assert _what_would_resume_here(monkeypatch, tmp_path / "A") == ([run_id], [loop_id])


@pytest.mark.parametrize("entry_id", RAN_HERE)
def test_each_is_declared_to_stay_where_it_ran(entry_id):
    entry = inv.by_id(entry_id)
    assert entry is not None and entry.machine_local and not entry.merged_in


def test_a_sync_sends_neither_a_run_nor_a_loop(tmp_path, monkeypatch):
    run_id, _loop = _a_machine_that_is_running_both(monkeypatch, tmp_path / "A")
    out = tmp_path / "sync"
    result = export_shards(tmp_path / "A", out, for_sync=True)
    sent = {s.path.split("/", 1)[0] for s in result.shards}
    assert not sent & {"workflow_runs_db", "loops_db", "workflow_runs", "loop"}
    assert not {d.entry_id for d in result.databases} & {"workflow_runs_db", "loops_db"}
    workflow_files = "".join(p.read_text() for p in (out / "workflows").glob("*.jsonl"))
    assert run_id not in workflow_files, "a run's own folder went out as files of workflows/"
    backup = export_shards(tmp_path / "A", tmp_path / "backup")
    assert {"workflow_runs_db", "loops_db"} <= {s.path.split("/", 1)[0] for s in backup.shards}


def test_another_machines_running_run_and_loop_never_resume_here(tmp_path, monkeypatch):
    store = SharedStore()
    _a_machine_that_is_running_both(monkeypatch, tmp_path / "A")
    assert run_sync_cycle(store, tmp_path / "A", self_id="A", now="t1").ok
    _as(monkeypatch, tmp_path / "B")
    report = run_sync_cycle(store, tmp_path / "B", self_id="B", now="t2")
    assert report.ok, report.error
    assert _what_would_resume_here(monkeypatch, tmp_path / "B") == ([], [])
    assert not (tmp_path / "B" / "workflows" / "runs").exists()


def test_a_peer_that_still_sends_a_run_ledger_changes_nothing_here(tmp_path, monkeypatch):
    # A build from before this change still stages the run ledger in its export.
    _a_machine_that_is_running_both(monkeypatch, tmp_path / "A")
    shard_dir = tmp_path / "their-export"
    (shard_dir / "db").mkdir(parents=True)
    shutil.copy2(tmp_path / "A" / "workflows" / "runs.db", shard_dir / "db" / "workflow_runs_db.db")
    shutil.copy2(tmp_path / "A" / "loop" / "loops.db", shard_dir / "db" / "loops_db.db")
    b = _as(monkeypatch, tmp_path / "B")
    merge = make_db_merger(b)
    assert merge(inv.by_id("workflow_runs_db"), shard_dir) == CONSUMED
    assert merge(inv.by_id("loops_db"), shard_dir) == CONSUMED
    assert not (b / "workflows" / "runs.db").exists() and not (b / "loop" / "loops.db").exists()
    assert _what_would_resume_here(monkeypatch, b) == ([], [])


def _snapshot_of(monkeypatch, home: Path, out: Path) -> Path:
    from personalclaw.snapshot import snapshot_main

    _as(monkeypatch, home)
    assert snapshot_main([str(out)]) in (0, None)
    (tarball,) = sorted(out.glob("personalclaw-snapshot-*.tar.gz"))
    return tarball


def test_a_merge_restore_of_another_machines_snapshot_resumes_nothing(tmp_path, monkeypatch):
    from personalclaw.snapshot import restore_main

    run_id, loop_id = _a_machine_that_is_running_both(monkeypatch, tmp_path / "A")
    tarball = _snapshot_of(monkeypatch, tmp_path / "A", tmp_path / "snaps")
    with tarfile.open(tarball) as tar:
        names = tar.getnames()
    assert any(n.endswith("workflows/runs.db") for n in names), "the archive carries the run"
    assert any(n.endswith("loop/loops.db") for n in names), "and the loop"

    b = _as(monkeypatch, tmp_path / "B")
    assert restore_main([str(tarball), "--mode", "merge"]) == 0
    assert _what_would_resume_here(monkeypatch, b) == ([], [])
    assert not (b / "workflows" / "runs" / run_id).exists()
    assert not (b / "loop" / loop_id).exists()


def test_the_merge_plan_says_what_it_leaves_out(tmp_path, monkeypatch):
    from personalclaw.snapshot import merge_plan

    _a_machine_that_is_running_both(monkeypatch, tmp_path / "A")
    rows = {r["path"]: r for r in merge_plan(tmp_path / "A", tmp_path / "B", None)}
    for path in ("workflows/runs.db", "workflows/runs", "loop/loops.db", "loop"):
        assert rows[path]["action"] == "skip", path
        assert rows[path]["detail"] == "this machine's own: the archive's is left out"


def test_an_import_of_another_machines_archive_resumes_nothing(tmp_path, monkeypatch):
    from personalclaw.portability import apply_import_zip

    _run, loop_id = _a_machine_that_is_running_both(monkeypatch, tmp_path / "A")
    a = tmp_path / "A"
    for db in (a / "workflows" / "runs.db", a / "loop" / "loops.db"):
        with sqlite3.connect(db) as conn:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    archive = tmp_path / "export.zip"
    root = "personalclaw-export-20260901T000000Z"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(f"{root}/MANIFEST.json", json.dumps({"version": 2, "contents": {}}))
        for path in sorted(p for p in a.rglob("*") if p.is_file()):
            if not path.name.endswith(("-wal", "-shm")):
                zf.writestr(f"{root}/{path.relative_to(a).as_posix()}", path.read_bytes())
        zf.writestr(f"{root}/workflows/workspaces/shared/notes.txt", "their working files\n")
        zf.writestr(f"{root}/workflows/defs/digest/workflow.json", json.dumps({"name": "digest"}))
    names = zipfile.ZipFile(archive).namelist()
    assert f"{root}/workflows/runs.db" in names and f"{root}/loop/loops.db" in names

    b = _as(monkeypatch, tmp_path / "B")
    monkeypatch.setattr("personalclaw.portability.config_dir", lambda: b)
    apply_import_zip(archive, mode="merge")
    assert (b / "workflows" / "defs" / "digest" / "workflow.json").is_file(), "the rest comes in"
    assert not (b / "workflows" / "runs.db").exists()
    assert not (b / "workflows" / "runs").exists()
    assert not (b / "workflows" / "workspaces").exists()
    assert not (b / "loop" / "loops.db").exists() and not (b / "loop" / loop_id).exists()
    assert _what_would_resume_here(monkeypatch, b) == ([], [])

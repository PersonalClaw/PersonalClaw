"""A replace restore holds every workflow run, loop and agent its archive had working.

🔴 A snapshot is a moment in the past, and a replace restore wrote it back whole: the workflow runs
it held running, waiting on a gate or queued to start, the loops it held running or planning, and
the folders of the agents it held running. At the next start the workflow watchdog adopted every
active run and resumed it from its journal, and drained every queued start; the loop watchdog
re-armed every running loop and re-kicked every planning one; the start settled every agent folder
without a tombstone as one the last gateway left running, and killed a live process under its
recorded pid that had started before the agent did (where ``/proc`` says when). So each went on
from that moment, repeating what it did after it — here, and on the machine it ran on as well when
the archive came from another one — and for another machine's archive, the process that pid names
here is one of this machine's own.

Each is held now (``snapshot._hold_what_was_in_flight``): a run is paused, with the pause intent the
watchdog honours and the reason as its error; a loop waits for its owner, asking to be resumed; an
agent's folder is closed as restored. Whichever machine took the archive: this machine's own run
went on after its snapshot too. Resume takes each on from where the snapshot left it.

And the pre-restore copy a replace makes of this home kept the snapshot's run ledger instead of
this home's: the snapshot's copy of ``workflows/`` was moved over it.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from personalclaw.sqlite_compat import sqlite3

SPEC = {"name": "digest", "version": 1, "root": {"kind": "sequence", "id": "main", "children": []}}


def _as(monkeypatch, home: Path) -> Path:
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    return home


def _run(status, name: str = "digest", **extra_fields) -> str:
    from personalclaw.workflows import store as wf_store
    from personalclaw.workflows.models import WorkflowRun

    run = wf_store.create(WorkflowRun(id="", workflow_name=name, status=status, **extra_fields))
    wf_store.write_spec(run.id, {**SPEC, "name": name})
    wf_store.write_state(run.id, {})
    return run.id


def _loop(status=None) -> str:
    from personalclaw.loop import store as loop_store
    from personalclaw.loop.loop import Loop

    loop = loop_store.create(Loop(id="", name="Scan", kind="research", task="scan the market"))
    if status is not None:
        loop_store.update_status(loop.id, status)
    return loop.id


def _agent(home: Path, agent_id: str) -> Path:
    """An agent folder as a running agent leaves it: its state, no tombstone."""
    folder = home / "subagents" / agent_id
    folder.mkdir(parents=True)
    state = {
        "id": agent_id,
        "task": "summarize the inbox",
        "pid": os.getpid(),  # a live process, as a running agent's is
        "started": time.time() - 3600,
        "pid_recorded_at": time.time() - 3600,
    }
    (folder / "state.json").write_text(json.dumps(state), encoding="utf-8")
    return folder


def _a_machine_with_work_in_flight(monkeypatch, home: Path) -> dict[str, str]:
    from personalclaw.loop.loop import LoopStatus
    from personalclaw.workflows import store as wf_store
    from personalclaw.workflows.models import RunStatus
    from personalclaw.workflows.overlap import queued_extra

    _as(monkeypatch, home)
    ids = {
        "running": _run(RunStatus.RUNNING),
        "waiting": _run(RunStatus.NEEDS_INPUT),
        # Queued behind nothing of its own workflow, so the next start would launch it.
        "queued": _run(RunStatus.DRAFT, "weekly", extra=queued_extra()),
        # Paused by its owner, with the intent that keeps the watchdog off it.
        "paused": _run(RunStatus.PAUSED, error_message="paused for the review"),
        "draft": _run(RunStatus.DRAFT),
        "done": _run(RunStatus.COMPLETE),
        "loop_running": _loop(LoopStatus.RUNNING),
        "loop_planning": _loop(LoopStatus.PLANNING),
        "loop_paused": _loop(LoopStatus.PAUSED),
    }
    wf_store.request_pause(ids["paused"])
    _agent(home, "a1b2c3d4")
    return ids


def _snapshot(monkeypatch, home: Path, out: Path) -> Path:
    from personalclaw.snapshot import snapshot_main

    _as(monkeypatch, home)
    assert snapshot_main([str(out)]) in (0, None)
    (tarball,) = sorted(out.glob("personalclaw-snapshot-*.tar.gz"))
    return tarball


def _replace(monkeypatch, tarball: Path, home: Path) -> None:
    from personalclaw.snapshot import restore_main

    _as(monkeypatch, home)
    assert restore_main([str(tarball), "--mode", "replace"]) == 0


def _what_the_start_picks_up(monkeypatch, home: Path) -> dict[str, list[str]]:
    """What this home's next start would drive: the runs the workflow watchdog adopts or launches
    on its first poll, the loops the loop watchdog re-arms or re-kicks, and the agents the start
    would settle as left running — each asked of the thing that does it, nothing run."""
    from personalclaw.loop.watchdog import LoopWatchdog
    from personalclaw.subagent_persistence import list_orphans
    from personalclaw.workflows.watchdog import WorkflowWatchdog

    _as(monkeypatch, home)
    picked: dict[str, list[str]] = {"runs": [], "loops": [], "agents": []}

    async def adopt(run) -> None:
        picked["runs"].append(run.id)

    async def launch(run, spec, **_kw):
        picked["runs"].append(run.id)

    async def rearm(loop) -> bool:
        picked["loops"].append(loop.id)
        return True

    runs = WorkflowWatchdog()
    monkeypatch.setattr(runs, "_adopt", adopt)
    monkeypatch.setattr(runs, "launch", launch)
    loops = LoopWatchdog(SimpleNamespace(_sessions={}), None)
    monkeypatch.setattr(loops, "_rearm_running", rearm)
    monkeypatch.setattr(loops, "_rekick_planning", rearm)

    async def start() -> None:
        await runs._poll_once()
        await loops._boot_sweep()

    asyncio.run(start())
    picked["agents"] = [state.get("id", "") for state in list_orphans()]
    return picked


def test_the_control_the_start_picks_all_of_it_up(tmp_path, monkeypatch):
    ids = _a_machine_with_work_in_flight(monkeypatch, tmp_path / "A")
    picked = _what_the_start_picks_up(monkeypatch, tmp_path / "A")
    assert sorted(picked["runs"]) == sorted([ids["running"], ids["waiting"], ids["queued"]])
    assert sorted(picked["loops"]) == sorted([ids["loop_running"], ids["loop_planning"]])
    assert picked["agents"] == ["a1b2c3d4"]


@pytest.mark.parametrize("onto", ["another machine", "the machine that took it"])
def test_a_replace_restore_holds_what_its_snapshot_had_working(tmp_path, monkeypatch, onto):
    from personalclaw.loop import files as loop_files
    from personalclaw.loop import store as loop_store
    from personalclaw.workflows import store as wf_store

    ids = _a_machine_with_work_in_flight(monkeypatch, tmp_path / "A")
    tarball = _snapshot(monkeypatch, tmp_path / "A", tmp_path / "snaps")
    home = tmp_path / ("B" if onto == "another machine" else "A")
    _replace(monkeypatch, tarball, home)

    assert _what_the_start_picks_up(monkeypatch, home) == {"runs": [], "loops": [], "agents": []}

    for key in ("running", "waiting", "queued"):
        run = wf_store.get(ids[key])
        assert run is not None and run.status.value == "paused", key
        assert wf_store.pause_requested(ids[key]), f"{key} has no pause the watchdog honours"
    assert wf_store.get(ids["running"]).error_message == wf_store.RESTORED_RUNNING
    assert wf_store.get(ids["waiting"]).error_message == wf_store.RESTORED_RUNNING
    assert wf_store.get(ids["queued"]).error_message == wf_store.RESTORED_QUEUED
    # What was not working is left as the snapshot had it, a run its owner paused included.
    assert wf_store.get(ids["draft"]).status.value == "draft"
    assert wf_store.get(ids["done"]).status.value == "complete"
    paused = wf_store.get(ids["paused"])
    assert (paused.status.value, paused.error_message) == ("paused", "paused for the review")
    assert loop_store.get(ids["loop_paused"]).status == "paused"

    for key in ("loop_running", "loop_planning"):
        assert loop_store.get(ids[key]).status == "needs_input", key
        assert loop_files.pending_question(ids[key])["question"] == loop_store.RESTORED_WORKING
        status = json.loads((home / "loop" / ids[key] / "status.json").read_text())
        assert status["status"] == "needs_input", "the gate its worker reads says so too"

    tombstone = json.loads((home / "subagents" / "a1b2c3d4" / "tombstone.json").read_text())
    assert tombstone["cause"] == "restored"


def test_resume_takes_a_held_run_on(tmp_path, monkeypatch):
    from personalclaw.workflows import store as wf_store

    ids = _a_machine_with_work_in_flight(monkeypatch, tmp_path / "A")
    tarball = _snapshot(monkeypatch, tmp_path / "A", tmp_path / "snaps")
    _replace(monkeypatch, tarball, tmp_path / "B")
    wf_store.clear_pause(ids["running"])  # what Resume does
    assert _what_the_start_picks_up(monkeypatch, tmp_path / "B")["runs"] == [ids["running"]]


def test_an_import_that_replaces_holds_them_too(tmp_path, monkeypatch):
    from personalclaw.portability import apply_import_zip, create_export_zip
    from personalclaw.workflows import store as wf_store

    ids = _a_machine_with_work_in_flight(monkeypatch, tmp_path / "A")
    data, _manifest = create_export_zip()
    archive = tmp_path / "export.zip"
    archive.write_bytes(data)
    home = _as(monkeypatch, tmp_path / "B")
    monkeypatch.setattr("personalclaw.portability.config_dir", lambda: home)
    summary = apply_import_zip(archive, mode="replace")
    assert sorted(summary["runs_held"]) == sorted([ids["running"], ids["waiting"], ids["queued"]])
    assert sorted(summary["loops_held"]) == sorted([ids["loop_running"], ids["loop_planning"]])
    assert wf_store.pause_requested(ids["running"])
    assert _what_the_start_picks_up(monkeypatch, home)["runs"] == []


def test_the_pre_restore_copy_keeps_this_homes_run_ledger(tmp_path, monkeypatch):
    from personalclaw.workflows.models import RunStatus

    _a_machine_with_work_in_flight(monkeypatch, tmp_path / "A")
    tarball = _snapshot(monkeypatch, tmp_path / "A", tmp_path / "snaps")
    _as(monkeypatch, tmp_path / "B")
    mine = _run(RunStatus.COMPLETE)
    _replace(monkeypatch, tarball, tmp_path / "B")
    (backup,) = sorted((tmp_path / "B").glob("pre-restore-*"))
    with sqlite3.connect(backup / "workflows" / "runs.db") as conn:
        kept = [row[0] for row in conn.execute("SELECT id FROM runs")]
    assert kept == [mine], "the pre-restore copy holds this home's runs"


def test_a_merge_restore_brings_no_agent_folder_in(tmp_path, monkeypatch):
    from personalclaw.snapshot import restore_main

    _a_machine_with_work_in_flight(monkeypatch, tmp_path / "A")
    tarball = _snapshot(monkeypatch, tmp_path / "A", tmp_path / "snaps")
    home = _as(monkeypatch, tmp_path / "B")
    (home / "config.json").write_text("{}", encoding="utf-8")
    (home / "tasks").mkdir()
    (home / "tasks" / "mine.json").write_text(json.dumps({"id": "mine"}))
    assert restore_main([str(tarball), "--mode", "merge"]) == 0
    assert not (home / "subagents").exists()

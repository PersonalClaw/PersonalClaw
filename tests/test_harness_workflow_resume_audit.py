"""Tests for the SV-6 workflow-run resume-audit — byte-equal frontier reconstruction.

The workflow half of the fresh-session resumability audit (§2.4, Success Criterion #5):
a persisted workflow run is KILLED and resumed from DISK ALONE, and the frontier the
resumed engine reconstructs must be byte-equal to the pre-kill snapshot. Independently, the
run's journal is folded through SV-5's event-fold law and checked against the persisted node
states, so a divergent replay (a corrupted or truncated journal) fails the audit even when
the state file alone looks intact.

These prove both halves of the "Done when":

1. resume-audit kills + resumes a persisted run from disk and verifies the journal replay
   reconstructs frontier state byte-equal to the pre-kill snapshot (the POSITIVE case,
   including a mid-flight kill while a node is still running);
2. a divergent replay — a corrupted journal / a dropped node event — FAILS the byte-equal
   check (the NEGATIVE case).
"""

from __future__ import annotations

import asyncio
import json

import pytest

from harness import resume_audit
from personalclaw.workflows import store
from personalclaw.workflows.controller import EngineServices, RunController
from personalclaw.workflows.models import WorkflowRun

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    """Every test gets its own config dir — a destructive-by-nature audit must never see a
    real home (destructive-test-isolation rule)."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: home)
    return home


_SEQ_SPEC = {
    "name": "seq",
    "root": {
        "kind": "sequence",
        "id": "s",
        "children": [
            {"kind": "transform", "id": "seed", "config": {"expr": {"n": 7}}},
            {"kind": "transform", "id": "double", "config": {"expr": {"n": 14}}},
            {"kind": "transform", "id": "finish", "config": {"expr": {"done": True}}},
        ],
    },
}


def _make_run(spec: dict, **kw) -> WorkflowRun:
    run = store.create(WorkflowRun(id="", workflow_name=spec.get("name", "wf"), **kw))
    store.write_spec(run.id, spec)
    return run


async def _drive_to_completion(spec: dict) -> str:
    run = _make_run(spec)
    c = RunController(run, spec, services=EngineServices())
    assert (await c.run_to_completion(timeout=20)).value == "complete"
    return run.id


# ── 1. missing run is not answerable ──────────────────────────────────────────


def test_missing_run_is_not_answerable() -> None:
    r = resume_audit.audit_workflow_run("nonexistent")
    assert not r.exists
    assert not r.ok
    assert "not found" in r.failures()[0]


# ── 2. a completed run resumes byte-equal from disk (SC#5) ─────────────────────


async def test_completed_run_resumes_byte_equal_from_disk() -> None:
    run_id = await _drive_to_completion(_SEQ_SPEC)
    r = resume_audit.audit_workflow_run(run_id)
    assert r.ok, r.failures()
    assert r.frontier_byte_equal
    assert r.fold_matches_state
    # A completed run reconstructs a complete, no-work frontier and its journal folds to the
    # same three done nodes on disk.
    assert r.detail["node_count"] == 3
    assert r.detail["fold_status"] == "complete"
    snap = json.loads(r.resumed_frontier)
    assert snap["complete"] is True
    assert snap["ready"] == []
    assert all(state == "done" for state in snap["nodes"].values())


# ── 3. a mid-flight KILL resumes byte-equal (the flagship case) ───────────────


async def test_mid_flight_kill_resumes_byte_equal() -> None:
    """Kill a run while a node is still RUNNING, resume from disk alone, assert the frontier
    is byte-equal to the pre-kill snapshot. This is the historical dead-resume bug shape:
    the second node hangs, the process is killed, and a fresh engine must reconstruct the
    exact same 'node 0 done, node 1 running' scheduling decision from state.json."""

    async def hang(prompt, *, use_case="background", output_type=None):
        await asyncio.sleep(30)
        return "never"

    # The middle node is an `infer` so the injected `completion` actually blocks it — a
    # transform completes instantly and there is no running node to catch.
    hang_spec = {
        "name": "hang",
        "root": {
            "kind": "sequence",
            "id": "s",
            "children": [
                {"kind": "transform", "id": "a", "config": {"expr": {"n": 1}}},
                {"kind": "infer", "id": "b", "config": {"prompt": "think"}},
                {"kind": "transform", "id": "c", "config": {"expr": {"n": 3}}},
            ],
        },
    }
    run = _make_run(hang_spec)
    live = RunController(run, hang_spec, services=EngineServices(completion=hang))
    await live.start()
    # Let node 0 complete and node 1 start (and hang).
    for _ in range(50):
        await asyncio.sleep(0.05)
        if any(i.state.value == "running" for i in live.instances.values()):
            break
    pre_kill = resume_audit._frontier_snapshot(live)
    await live.stop()  # THE KILL — leaves the run resumable, not failed.

    r = resume_audit.audit_workflow_run(run.id, pre_kill_frontier=pre_kill)
    assert r.ok, r.failures()
    # The reconstructed frontier equals the live pre-kill snapshot, character for character.
    assert r.resumed_frontier == pre_kill
    snap = json.loads(r.resumed_frontier)
    assert snap["nodes"]["root.children[0]"] == "done"
    assert snap["nodes"]["root.children[1]"] == "running"
    assert snap["running"] == ["root.children[1]"]
    assert snap["complete"] is False


# ── 4. idempotent reconstruction when no live snapshot is captured ────────────


async def test_reconstruction_is_idempotent_without_a_live_snapshot() -> None:
    """When only the run's files survive (killed out of band), the persisted state IS the
    pre-kill truth — two independent disk-only reconstructions must agree byte-for-byte."""
    run_id = await _drive_to_completion(_SEQ_SPEC)
    r = resume_audit.audit_workflow_run(run_id)  # no pre_kill_frontier passed
    assert r.ok
    assert r.pre_kill_frontier == r.resumed_frontier


# ── 5. a divergent replay (corrupted journal) FAILS the audit ─────────────────


async def test_corrupted_journal_fails_the_fold_check() -> None:
    """A journal whose folded node states diverge from the persisted state fails the audit:
    the event-fold law says the journal must be able to REBUILD the frontier, so a corrupted
    ledger (a node recorded done that the state file has as done, flipped to failed) is a
    real resume defect, not a pass on the state file alone."""
    run_id = await _drive_to_completion(_SEQ_SPEC)
    journal_path = store.run_dir(run_id) / "journal.jsonl"
    lines = [ln for ln in journal_path.read_text().splitlines() if ln.strip()]
    corrupted = []
    for ln in lines:
        rec = json.loads(ln)
        if rec.get("kind") == "step_completed" and rec.get("node_id") == "seed":
            rec["state"] = "failed"  # the divergence a format/replay break produces
        corrupted.append(json.dumps(rec))
    journal_path.write_text("\n".join(corrupted) + "\n", encoding="utf-8")

    r = resume_audit.audit_workflow_run(run_id)
    assert not r.ok
    assert not r.fold_matches_state
    # The persisted state.json still reads seed=done, so the disk-only frontier is unchanged —
    # only the fold check catches the divergence, which is exactly its job.
    assert r.frontier_byte_equal
    assert any("event-fold" in f for f in r.failures())


async def test_dropped_node_event_fails_the_fold_check() -> None:
    """A truncated journal — the last node's completion event dropped (a crash mid-write, or
    a format change that stopped emitting it) — folds to fewer done nodes than the state file
    holds, and the audit catches it."""
    run_id = await _drive_to_completion(_SEQ_SPEC)
    journal_path = store.run_dir(run_id) / "journal.jsonl"
    lines = [ln for ln in journal_path.read_text().splitlines() if ln.strip()]
    kept = [
        ln
        for ln in lines
        if not (
            json.loads(ln).get("kind") == "step_completed"
            and json.loads(ln).get("node_id") == "finish"
        )
    ]
    journal_path.write_text("\n".join(kept) + "\n", encoding="utf-8")

    r = resume_audit.audit_workflow_run(run_id)
    assert not r.ok
    assert not r.fold_matches_state


# ── 6. the CLI exposes the workflow-resume-audit command ──────────────────────


async def test_cli_workflow_resume_audit_reports_green(capsys) -> None:
    from harness.cli import main

    run_id = await _drive_to_completion(_SEQ_SPEC)
    rc = main(["workflow-resume-audit", run_id])
    out = capsys.readouterr().out
    assert rc == 0
    assert "byte-equal" in out


def test_cli_workflow_resume_audit_missing_run(capsys) -> None:
    from harness.cli import main

    rc = main(["workflow-resume-audit", "nope"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "not found" in out

"""Unit tests for the Schedule run history (ScheduleRun + ScheduleRunStore)
and ScheduleService run recording.

Covers: the run store round-trips records (per-job file + cross-job index),
rotation caps, path-traversal rejection, lock-free partial-line tolerance, the
TaskProvider-shaped (rows, total) read API, and that ScheduleService.run_job
records a ScheduleRun tagged trigger="manual", refuses to double-fire, and
exposes is_running/running_since.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from personalclaw.schedule import ScheduleJob, ScheduleService, make_agent_action
from personalclaw.schedule_history import (
    _MAX_RECORDS_PER_JOB,
    ScheduleRun,
    ScheduleRunStore,
)

# ── ScheduleRunStore ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_append_and_roundtrip(tmp_path: Path) -> None:
    store = ScheduleRunStore(base_dir=tmp_path)
    run = ScheduleRun(
        job_id="abc123",
        trigger="manual",
        status="success",
        summary="hello",
        trace="full output here",
        duration_ms=42,
    )
    await store.append(run)

    # Per-job list (no trace in rows) + total.
    rows, total = await store.list_for_job("abc123")
    assert total == 1
    assert rows[0]["run_id"] == run.run_id
    assert rows[0]["trigger"] == "manual"
    assert "trace" not in rows[0]  # list rows drop trace

    # Cross-job index list.
    all_rows, all_total = await store.list_all()
    assert all_total == 1
    assert all_rows[0]["job_id"] == "abc123"

    # Full record (with trace) via get_run.
    full = await store.get_run("abc123", run.run_id)
    assert full is not None
    assert full["trace"] == "full output here"


@pytest.mark.asyncio
async def test_caps_summary_and_trace(tmp_path: Path) -> None:
    store = ScheduleRunStore(base_dir=tmp_path)
    run = ScheduleRun(job_id="j1", summary="x" * 9999, trace="y" * 99_999)
    await store.append(run)
    full = await store.get_run("j1", run.run_id)
    assert full is not None
    assert len(full["summary"]) == 200
    assert len(full["trace"]) == 50_000


@pytest.mark.asyncio
async def test_rotation_caps_per_job(tmp_path: Path) -> None:
    store = ScheduleRunStore(base_dir=tmp_path)
    for i in range(_MAX_RECORDS_PER_JOB + 25):
        await store.append(ScheduleRun(job_id="rot", summary=f"run {i}"))
    rows, total = await store.list_for_job("rot", offset=0, limit=1000)
    assert total == _MAX_RECORDS_PER_JOB  # trimmed to the cap
    # Newest-first: the most recent appended run is first.
    assert rows[0]["summary"] == f"run {_MAX_RECORDS_PER_JOB + 24}"


@pytest.mark.asyncio
async def test_path_traversal_rejected(tmp_path: Path) -> None:
    store = ScheduleRunStore(base_dir=tmp_path)
    with pytest.raises(ValueError):
        store._job_path("../../etc/passwd")
    with pytest.raises(ValueError):
        await store.list_for_job("../escape")


@pytest.mark.asyncio
async def test_lock_free_read_tolerates_partial_line(tmp_path: Path) -> None:
    store = ScheduleRunStore(base_dir=tmp_path)
    await store.append(ScheduleRun(job_id="p", summary="good"))
    # Simulate a torn final line from a concurrent append.
    job_file = tmp_path / "cron-history" / "p.jsonl"
    with job_file.open("a", encoding="utf-8") as fh:
        fh.write('{"run_id": "partial", "job_id": "p"')  # no newline, invalid JSON
    rows, total = await store.list_for_job("p")
    assert total == 1  # the partial line is silently skipped
    assert rows[0]["summary"] == "good"


@pytest.mark.asyncio
async def test_delete_for_job(tmp_path: Path) -> None:
    store = ScheduleRunStore(base_dir=tmp_path)
    await store.append(ScheduleRun(job_id="d1", summary="a"))
    await store.append(ScheduleRun(job_id="d2", summary="b"))
    await store.delete_for_job("d1")
    rows, total = await store.list_for_job("d1")
    assert total == 0
    # d2 survives, and the index no longer references d1.
    _, d2_total = await store.list_for_job("d2")
    assert d2_total == 1
    all_rows, _ = await store.list_all()
    assert all(r["job_id"] != "d1" for r in all_rows)


# ── ScheduleService recording ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_job_records_manual_run(tmp_path: Path) -> None:
    seen: list[str] = []

    async def on_job(job):
        seen.append(job.id)
        return "did the thing"

    svc = ScheduleService(base_dir=tmp_path, on_job=on_job)
    svc._load()
    job = svc.add_job(name="t", action=make_agent_action(message="hi"), every_secs=300)

    ok = await svc.run_job(job.id)
    assert ok is True
    assert seen == [job.id]

    rows, total = await svc.list_runs(job.id)
    assert total == 1
    assert rows[0]["trigger"] == "manual"
    assert rows[0]["status"] == "success"
    assert rows[0]["duration_ms"] >= 0


@pytest.mark.asyncio
async def test_run_job_records_failure(tmp_path: Path) -> None:
    async def on_job(job):
        raise RuntimeError("boom")

    svc = ScheduleService(base_dir=tmp_path, on_job=on_job)
    svc._load()
    job = svc.add_job(name="f", action=make_agent_action(message="hi"), every_secs=300)
    await svc.run_job(job.id)
    rows, _ = await svc.list_runs(job.id)
    assert rows[0]["status"] == "failure"
    assert "boom" in (rows[0]["summary"] or rows[0].get("error", ""))


@pytest.mark.asyncio
async def test_double_fire_guard(tmp_path: Path) -> None:
    svc = ScheduleService(base_dir=tmp_path)
    svc._load()
    job = svc.add_job(name="g", action=make_agent_action(message="hi"), every_secs=300)
    # Mark as executing → run_job must refuse.
    svc._executing.add(job.id)
    assert svc.is_running(job.id) is True
    ok = await svc.run_job(job.id)
    assert ok is False


@pytest.mark.asyncio
async def test_refresh_callback_fires_on_record(tmp_path: Path) -> None:
    hints: list[str] = []
    svc = ScheduleService(base_dir=tmp_path, on_job=lambda job: _coro("ok"))
    svc._load()
    svc.set_refresh_callback(lambda kind: hints.append(kind))
    job = svc.add_job(name="r", action=make_agent_action(message="hi"), every_secs=300)
    await svc.run_job(job.id)
    assert "cron_history" in hints


async def _coro(v: str) -> str:
    return v


# ── _record_run status mapping (T7 honest "started ≠ succeeded") ──


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "last_status,last_error,last_outcome,expected",
    [
        ("ok", None, "", "success"),
        ("error", "boom", "", "failure"),
        ("error", "Timed out after 30s", "", "timeout"),
        # A fire-and-forget action only LAUNCHED a background turn — honest status.
        ("ok", None, "launched", "launched"),
    ],
)
async def test_record_run_status_mapping(
    tmp_path: Path, last_status, last_error, last_outcome, expected
) -> None:
    svc = ScheduleService(base_dir=tmp_path)
    svc._load()
    job = svc.add_job(name="m", action=make_agent_action(message="hi"), every_secs=300)
    job.last_status = last_status
    job.last_error = last_error
    job.last_outcome = last_outcome
    await svc._record_run(job, started_at=0.0, trigger="scheduled")
    rows, _ = await svc.list_runs(job.id)
    assert rows[0]["status"] == expected


@pytest.mark.asyncio
async def test_last_run_status_reads_newest_record(tmp_path: Path) -> None:
    """last_run_status() returns the newest run record's status (persistent,
    honest) — the source for the UI badge, surviving restart unlike last_outcome.
    A launched run must report 'launched', not the job's 'ok' last_status."""

    async def _launch(job: ScheduleJob) -> str:
        job.last_status = "ok"
        job.last_outcome = "launched"  # fire-and-forget
        return "ok"

    svc = ScheduleService(base_dir=tmp_path, on_job=_launch)
    svc._load()
    job = svc.add_job(name="L", action=make_agent_action(message="hi"), every_secs=300)
    assert svc.last_run_status(job.id) == ""  # no runs yet
    await svc.run_job(job.id)
    assert svc.last_run_status(job.id) == "launched"  # honest, not "ok"
    # Survives a fresh service (persistent run record, not runtime last_outcome).
    svc2 = ScheduleService(base_dir=tmp_path)
    svc2._load()
    assert svc2.last_run_status(job.id) == "launched"


@pytest.mark.asyncio
async def test_failed_action_status_not_clobbered_to_ok(tmp_path: Path) -> None:
    """A callback that self-reports last_status='error' (the action path on a
    failed action) must NOT be overwritten with 'ok' by _execute — else a failed
    run records as success (the honest-status bug T7 fixes)."""

    async def _failing_action(job: ScheduleJob) -> str | None:
        job.last_status = "error"
        job.last_error = "rendered empty"
        return None

    svc = ScheduleService(base_dir=tmp_path, on_job=_failing_action)
    svc._load()
    job = svc.add_job(name="f", action=make_agent_action(message="hi"), every_secs=300)
    await svc.run_job(job.id)
    rows, _ = await svc.list_runs(job.id)
    assert rows[0]["status"] == "failure"  # NOT "success"


@pytest.mark.asyncio
async def test_agent_path_defaults_to_ok(tmp_path: Path) -> None:
    """A callback that does NOT self-report status (the agent path) still defaults
    to 'ok' on a clean return."""

    async def _agent(job: ScheduleJob) -> str:
        return "did the thing"  # never touches last_status

    svc = ScheduleService(base_dir=tmp_path, on_job=_agent)
    svc._load()
    job = svc.add_job(name="a", action=make_agent_action(message="hi"), every_secs=300)
    await svc.run_job(job.id)
    rows, _ = await svc.list_runs(job.id)
    assert rows[0]["status"] == "success"


@pytest.mark.asyncio
async def test_replay_run_tags_replay_and_does_not_merge(tmp_path: Path) -> None:
    """A dry-run replay records a run tagged trigger='replay', sets job.dry_run
    during the callback, and does NOT merge job state to disk."""
    saw_dry_run: list[bool] = []

    async def _cb(job: ScheduleJob) -> str:
        saw_dry_run.append(job.dry_run)
        job.last_result = "REPLAYED"
        return "ok"

    svc = ScheduleService(base_dir=tmp_path, on_job=_cb)
    svc._load()
    job = svc.add_job(name="r", action=make_agent_action(message="hi"), every_secs=300)
    ok = await svc.replay_run(job.id)
    assert ok is True
    assert saw_dry_run == [True]  # the callback ran in dry-run mode
    assert job.dry_run is False  # cleared after the run
    rows, _ = await svc.list_runs(job.id)
    assert rows[0]["trigger"] == "replay"  # tagged distinctly from manual/scheduled
    # Dry run changed no real state: the on-disk job has no last_result merged.
    svc2 = ScheduleService(base_dir=tmp_path)
    svc2._load()
    persisted = next(j for j in svc2._jobs if j.id == job.id)
    assert (persisted.last_result or "") == ""


@pytest.mark.asyncio
async def test_launched_status_not_leaked_to_next_run(tmp_path: Path) -> None:
    """_execute resets last_outcome so a prior 'launched' can't make a later
    synchronous success report as 'launched'."""
    svc = ScheduleService(base_dir=tmp_path, on_job=lambda job: _coro("ok"))
    svc._load()
    job = svc.add_job(name="r", action=make_agent_action(message="hi"), every_secs=300)
    job.last_outcome = "launched"  # stale from a prior run
    await svc._execute(job)
    assert job.last_outcome == ""  # reset before the callback ran

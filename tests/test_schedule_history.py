"""Unit tests for the schedule run history (`ScheduleRun` + `ScheduleRunStore`).

Covers: the store round-trips records (per-job file + cross-job index), rotation caps,
path-traversal rejection, lock-free partial-line tolerance, and the TaskProvider-shaped
`(rows, total)` read API.

🔴 The `ScheduleService` recording + `_record_run` status-mapping sections retired with the class
(S112). The T7 contract they pinned — an honest `launched` that does NOT claim success — lives in
the substrate now: `test_triggers_executor.py` covers `classify()` and
`test_triggers_facade_store.py` covers the badge. `ScheduleRunStore` is unchanged, which is what
remains here.
"""

from __future__ import annotations

from pathlib import Path

import pytest

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


# 🔴 The `ScheduleService` recording + `_record_run` status-mapping sections retired with the
# class (S112). The T7 contract they pinned — an honest `launched` that does NOT claim success —
# lives in the substrate now and is covered by `test_triggers_executor.py`'s `classify()` tests
# and `test_triggers_facade_store.py`'s badge assertions. `ScheduleRunStore` itself is unchanged,
# which is what the section above exercises.

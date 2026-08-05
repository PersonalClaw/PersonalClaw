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


# ── count_since: the windowed query three rate caps waited on (S152) ──


@pytest.mark.asyncio
async def test_count_since_counts_only_the_window(tmp_path: Path) -> None:
    """🔴 `rate_cap`, `max_runs_per_hour` and `max_actions_per_hour` were all validated, carried and
    enforced by NOTHING because this read did not exist — `list_for_job` is offset/limit only, so a
    caller could page rows but not ask "how many in the last hour"."""
    store = ScheduleRunStore(tmp_path)
    now = 1_700_000_000.0
    for offset in (-10, -100, -3000, -7200):
        await store.append(ScheduleRun(job_id="j", trigger="scheduled", started_at=now + offset))
    assert await store.count_since("j", now - 3600.0) == 3
    assert await store.count_since("j", 0) == 4
    assert await store.count_since("j", now + 1) == 0


@pytest.mark.asyncio
async def test_a_MANUAL_fire_is_excluded_by_default(tmp_path: Path) -> None:
    """§3.6: "manual fires bypass the hourly cap". The cap exists to stop the MACHINE running away,
    and a person clicking Run is not the machine running away — counting their clicks would let a
    user lock themselves out of their own automation."""
    store = ScheduleRunStore(tmp_path)
    now = 1_700_000_000.0
    await store.append(ScheduleRun(job_id="j", trigger="scheduled", started_at=now - 10))
    await store.append(ScheduleRun(job_id="j", trigger="manual", started_at=now - 20))
    assert await store.count_since("j", now - 3600.0) == 1
    assert await store.count_since("j", now - 3600.0, manual=True) == 2


@pytest.mark.asyncio
async def test_a_malformed_row_is_skipped_not_counted(tmp_path: Path) -> None:
    """Counting it would let ONE bad line push a trigger over its cap and suppress real work.
    Skipping can only under-count, and the cap's purpose still holds: a runaway writes many
    well-formed rows."""
    store = ScheduleRunStore(tmp_path)
    now = 1_700_000_000.0
    await store.append(ScheduleRun(job_id="j", trigger="scheduled", started_at=now - 10))
    (tmp_path / "cron-history" / "j.jsonl").open("a", encoding="utf-8").write(
        '{"job_id": "j", "started_at": "soon"}\n'
    )
    assert await store.count_since("j", now - 3600.0) == 1


@pytest.mark.asyncio
async def test_an_unknown_job_counts_zero_rather_than_raising(tmp_path: Path) -> None:
    """A trigger that has never fired has no history file; that is normal, not an error."""
    assert await ScheduleRunStore(tmp_path).count_since("never-ran", 0) == 0


# ── 🔴 a suppression is not a fire (S171) ──


@pytest.mark.asyncio
async def test_a_SUPPRESSION_does_not_count_toward_the_RATE_WINDOW(tmp_path: Path):
    """🔴 A second-order defect S171 introduced and this closes. Once suppressed fires began
    persisting their typed row here (§7 crit 8's "zero silent drops"), `count_since` counted them —
    measured, 5 quiet-hours skips read as 5 fires.

    That inverts the cap: a trigger held by its OWN quiet window would consume the hourly
    allowance it never used, then be refused for "running away". The cap exists to bound
    work the machine DID.
    """
    store = ScheduleRunStore(tmp_path)
    now = 1_800_000_000.0
    for i in range(5):
        await store.append(
            ScheduleRun(
                run_id=f"skip-{i}",
                job_id="j",
                trigger="skipped_gate",
                started_at=now,
                status="skipped_gate",
            )
        )
    assert await store.count_since("j", now - 10) == 0


@pytest.mark.asyncio
async def test_a_REAL_fire_and_a_FAILED_one_both_still_count(tmp_path: Path):
    """The control case. A failure IS work the machine did — it woke, it ran, it broke — so the cap
    must still bound it. Excluding failures would let a crash-looping trigger fire forever."""
    store = ScheduleRunStore(tmp_path)
    now = 1_800_000_000.0
    await store.append(
        ScheduleRun(run_id="r1", job_id="j", trigger="ok", started_at=now, status="success")
    )
    await store.append(
        ScheduleRun(run_id="r2", job_id="j", trigger="failed", started_at=now, status="failure")
    )
    assert await store.count_since("j", now - 10) == 2


@pytest.mark.asyncio
async def test_the_MANUAL_exclusion_still_holds_alongside(tmp_path: Path):
    """S152's rule is unchanged: §3.6 says manual fires bypass the hourly cap, because a person
    clicking Run is not the machine running away. Both exclusions now apply and neither shadows the
    other."""
    store = ScheduleRunStore(tmp_path)
    now = 1_800_000_000.0
    await store.append(
        ScheduleRun(run_id="a", job_id="j", trigger="ok", started_at=now, status="success")
    )
    await store.append(
        ScheduleRun(run_id="b", job_id="j", trigger="manual", started_at=now, status="success")
    )
    await store.append(
        ScheduleRun(
            run_id="c", job_id="j", trigger="skipped_quiet", started_at=now, status="skipped_gate"
        )
    )
    assert await store.count_since("j", now - 10) == 1
    assert await store.count_since("j", now - 10, manual=True) == 2


@pytest.mark.asyncio
async def test_the_exclusion_reads_the_SHARED_inert_set(tmp_path: Path):
    """Keyed on `INERT_OUTCOMES` — the same set `history.is_inert` uses — not a local list of
    `skipped_*` strings. One definition of "this did nothing" for both surfaces, so a new
    inert outcome cannot start counting toward a rate cap because nobody updated a second
    list."""
    from personalclaw.triggers.models import INERT_OUTCOMES

    store = ScheduleRunStore(tmp_path)
    now = 1_800_000_000.0
    for i, outcome in enumerate(sorted(INERT_OUTCOMES)):
        await store.append(
            ScheduleRun(run_id=f"x{i}", job_id="j", trigger=outcome, started_at=now, status=outcome)
        )
    assert await store.count_since("j", now - 10) == 0, "every inert outcome must be excluded"

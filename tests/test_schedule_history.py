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

import json
from pathlib import Path

import pytest

from personalclaw.schedule_history import (
    _MAX_INDEX_PER_JOB,
    _MAX_INDEX_RECORDS,
    _MAX_RECORDS_PER_JOB,
    _MAX_SUPPRESSED_PER_JOB,
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


# ── 🔴 a suppression storm evicted the real runs (S173) ──


@pytest.mark.asyncio
async def test_a_SUPPRESSION_STORM_does_not_evict_the_real_run(tmp_path: Path):
    """🔴 A regression S171 introduced and this closes. That session began persisting suppressed
    fires (criterion 8's "zero silent drops"), and rotation kept a flat `[-100:]` tail.

    A minutely trigger held by quiet hours writes 1440 skips a day — `RunWeight`'s own
    docstring names that number. Measured against the flat tail: **1 real backup run plus
    129 quiet-hours skips evicted the backup entirely**, so the 100-row window held ~100
    MINUTES of history instead of ~100 runs, and the rows a user opens the history FOR were
    the first to go.
    """
    store = ScheduleRunStore(tmp_path)
    now = 1_800_000_000.0
    await store.append(
        ScheduleRun(
            run_id="REAL-backup",
            job_id="clock:m",
            trigger="ok",
            started_at=now,
            status="success",
            summary="backed up 4.2 GB",
        )
    )
    for i in range(1, 130):
        await store.append(
            ScheduleRun(
                run_id=f"skip-{i}",
                job_id="clock:m",
                trigger="skipped_gate",
                started_at=now + i * 60,
                status="skipped_gate",
                error="inside a quiet window",
            )
        )
    rows, _total = await store.list_for_job("clock:m", 0, 300)
    assert "REAL-backup" in [r["run_id"] for r in rows]


@pytest.mark.asyncio
async def test_a_job_that_NEVER_suppresses_keeps_its_FULL_window(tmp_path: Path):
    """The compatibility guarantee. The work quota is the REMAINDER of the total, not a separate
    smaller cap, so a trigger that never suppresses retains exactly what it did before S173."""
    store = ScheduleRunStore(tmp_path)
    now = 1_800_000_000.0
    for i in range(150):
        await store.append(
            ScheduleRun(
                run_id=f"r{i}", job_id="work", trigger="ok", started_at=now + i, status="success"
            )
        )
    _rows, total = await store.list_for_job("work", 0, 300)
    assert total > _MAX_RECORDS_PER_JOB * 0.9, f"a work-only job must keep its window, got {total}"


@pytest.mark.asyncio
async def test_work_SURVIVES_a_storm_many_times_its_size(tmp_path: Path):
    """90 real runs against 200 skips: the work quota holds most of the window rather than being
    crowded out proportionally."""
    store = ScheduleRunStore(tmp_path)
    now = 1_800_000_000.0
    for i in range(90):
        await store.append(
            ScheduleRun(
                run_id=f"w{i}", job_id="mix", trigger="ok", started_at=now + i, status="success"
            )
        )
    for i in range(200):
        await store.append(
            ScheduleRun(
                run_id=f"k{i}",
                job_id="mix",
                trigger="skipped_gate",
                started_at=now + 1000 + i,
                status="skipped_gate",
            )
        )
    rows, _total = await store.list_for_job("mix", 0, 300)
    work = [r for r in rows if r["status"] == "success"]
    assert len(work) >= _MAX_RECORDS_PER_JOB - _MAX_SUPPRESSED_PER_JOB - 1, len(work)


@pytest.mark.asyncio
async def test_rotation_PRESERVES_file_ORDER(tmp_path: Path):
    """Order is load-bearing: `list_for_job` reverses the file for newest-first and `count_since`
    walks it. Partitioning by class to decide what survives, then writing the survivors grouped by
    class, would make both misread the file — so the kept rows are re-merged by original position.
    """
    store = ScheduleRunStore(tmp_path)
    now = 1_800_000_000.0
    for i in range(120):
        inert = i % 2 == 0
        await store.append(
            ScheduleRun(
                run_id=f"x{i}",
                job_id="ord",
                trigger="skipped_gate" if inert else "ok",
                started_at=now + i,
                status="skipped_gate" if inert else "success",
            )
        )
    rows, _t = await store.list_for_job("ord", 0, 300)
    # `list_for_job` returns newest-first, so started_at must be monotonically DECREASING.
    stamps = [float(r["started_at"]) for r in rows]
    assert stamps == sorted(stamps, reverse=True), "rotation must not reorder the file"


# ── 🔴 one noisy job owned the whole cross-job index (S174) ──


@pytest.mark.asyncio
async def test_a_NOISY_job_does_not_evict_every_OTHER_automation(tmp_path: Path):
    """🔴 THE DEFECT, and the cross-job half of S173. The index is SHARED — it backs the dashboard's
    "recent runs across all schedules" — and a flat tail lets the loudest writer own all of it.

    Measured after S171 began persisting suppressions: three well-behaved automations with one run
    each, plus 1.5 days of one minutely trigger's quiet-hours skips, and the index held **2000 rows
    from that single trigger and nothing else**. Every other automation vanished from the only
    cross-job view there is.
    """
    store = ScheduleRunStore(tmp_path)
    now = 1_800_000_000.0
    for name in ("nightly-backup", "weekly-report", "deploy-watch"):
        await store.append(
            ScheduleRun(
                run_id=f"REAL-{name}", job_id=name, trigger="ok", started_at=now, status="success"
            )
        )
    for i in range(1, 2100):
        await store.append(
            ScheduleRun(
                run_id=f"skip-{i}",
                job_id="clock:noisy",
                trigger="skipped_gate",
                started_at=now + i * 60,
                status="skipped_gate",
            )
        )
    rows, _total = await store.list_all(0, 5000)
    jobs = {r["job_id"] for r in rows}
    for name in ("nightly-backup", "weekly-report", "deploy-watch"):
        assert name in jobs, f"{name} was evicted by the noisy job"


@pytest.mark.asyncio
async def test_no_job_exceeds_its_SHARE_after_a_trim(tmp_path: Path):
    """The invariant is per-TRIM, not per-append.

    Rotation fires only above the global cap, so between trims a job may exceed its share and
    is cut back on the next one. What must hold is that after a trim, no job monopolises the
    index."""
    store = ScheduleRunStore(tmp_path)
    now = 1_800_000_000.0
    await store.append(
        ScheduleRun(run_id="REAL", job_id="backup", trigger="ok", started_at=now, status="success")
    )
    for i in range(_MAX_INDEX_RECORDS + 50):
        await store.append(
            ScheduleRun(
                run_id=f"s{i}",
                job_id="noisy",
                trigger="skipped_gate",
                started_at=now + i + 1,
                status="skipped_gate",
            )
        )
    rows, _total = await store.list_all(0, 5000)
    noisy = [r for r in rows if r["job_id"] == "noisy"]
    # Trimmed to the share, then regrown by the appends since — bounded either way.
    assert len(noisy) <= _MAX_INDEX_PER_JOB + 60, len(noisy)
    assert "REAL" in [r["run_id"] for r in rows], "the quiet job's run must survive"


@pytest.mark.asyncio
async def test_a_store_UNDER_the_cap_is_untouched(tmp_path: Path):
    """The compatibility guarantee: the per-job bound applies only when trimming is needed, so an
    install that never hits the global cap keeps every row exactly as before."""
    store = ScheduleRunStore(tmp_path)
    now = 1_800_000_000.0
    for i in range(50):
        await store.append(
            ScheduleRun(
                run_id=f"a{i}", job_id="small", trigger="ok", started_at=now + i, status="success"
            )
        )
    _rows, total = await store.list_all(0, 5000)
    assert total == 50


@pytest.mark.asyncio
async def test_the_index_stays_NEWEST_FIRST(tmp_path: Path):
    """Order is load-bearing: `list_all` reverses the file, so a file regrouped by job would
    render the cross-schedule view out of order. Kept rows are re-merged by position."""
    store = ScheduleRunStore(tmp_path)
    now = 1_800_000_000.0
    for i in range(_MAX_INDEX_RECORDS + 200):
        await store.append(
            ScheduleRun(
                run_id=f"r{i}",
                job_id=f"job{i % 7}",
                trigger="ok",
                started_at=now + i,
                status="success",
            )
        )
    rows, _total = await store.list_all(0, 5000)
    stamps = [float(r["started_at"]) for r in rows]
    assert stamps == sorted(stamps, reverse=True), "index rotation must not reorder the file"


# ── 🔴 the BOOT rotation reverted the append rotation (S175) ──


def _write_legacy(store: ScheduleRunStore, job_id: str, rows: list[dict]) -> None:
    """Write a job file DIRECTLY, bypassing append-time rotation.

    That is the state a PRE-S173 install has on disk when the new build first boots — which is the
    only way to reach the boot trim with an over-cap file, and therefore the only way this defect is
    observable. Appending the same rows would rotate them on the way in and hide it.
    """
    path = store._job_path(job_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


def _row(run_id: str, job_id: str, status: str, started: float) -> dict:
    return {
        "run_id": run_id,
        "job_id": job_id,
        "trigger": "ok" if status == "success" else status,
        "started_at": started,
        "finished_at": started,
        "duration_ms": 0,
        "status": status,
        "summary": "",
        "error": "",
        "trace": "",
    }


@pytest.mark.asyncio
async def test_BOOT_rotation_does_not_evict_what_APPEND_rotation_protects(tmp_path: Path):
    """🔴 THE DEFECT. `_rotate_all_sync` carried its own inlined `rows[-_MAX_RECORDS_PER_JOB:]` — the
    pre-S173 flat tail — so the BOOT path undid what the append path protects.

    Measured on the realistic case, an existing install's first boot on the new build: a
    200-row legacy file (1 real run + 199 quiet-hours skips) came back as 100 rows with the
    real run **evicted**, while appending those same rows keeps it. A duplicated policy is
    how two paths start disagreeing, and this copy reverted the other at exactly the moment
    a user upgrades.
    """
    store = ScheduleRunStore(tmp_path)
    now = 1_800_000_000.0
    rows = [_row("REAL-backup", "clock:m", "success", now)]
    rows += [_row(f"skip-{i}", "clock:m", "skipped_gate", now + i * 60) for i in range(1, 200)]
    _write_legacy(store, "clock:m", rows)

    await store.rotate_all()
    kept, _total = await store.list_for_job("clock:m", 0, 300)
    assert "REAL-backup" in [r["run_id"] for r in kept]


@pytest.mark.asyncio
async def test_BOOT_rotation_still_caps_a_WORK_only_file(tmp_path: Path):
    """The compatibility half: boot rotation must still enforce the window, just fairly. A work-only
    legacy file is trimmed to the cap exactly as before."""
    store = ScheduleRunStore(tmp_path)
    now = 1_800_000_000.0
    _write_legacy(store, "work", [_row(f"r{i}", "work", "success", now + i) for i in range(150)])
    await store.rotate_all()
    _kept, total = await store.list_for_job("work", 0, 300)
    assert total == _MAX_RECORDS_PER_JOB


@pytest.mark.asyncio
async def test_BOOT_rotation_visits_EVERY_job_file(tmp_path: Path):
    """It globs the directory, so a bug that rotated only the first file would leave later jobs
    unbounded. Two over-cap files, both trimmed."""
    store = ScheduleRunStore(tmp_path)
    now = 1_800_000_000.0
    for job in ("alpha", "beta"):
        _write_legacy(store, job, [_row(f"{job}{i}", job, "success", now + i) for i in range(140)])
    await store.rotate_all()
    for job in ("alpha", "beta"):
        _kept, total = await store.list_for_job(job, 0, 300)
        assert total == _MAX_RECORDS_PER_JOB, job


@pytest.mark.asyncio
async def test_BOOT_rotation_delegates_rather_than_DUPLICATING(tmp_path: Path):
    """Pinned by source, because the defect WAS the duplication: two copies of a retention policy
    drift, and the second silently reverted the first. One trim function, called from both paths."""
    import inspect

    from personalclaw import schedule_history

    source = inspect.getsource(schedule_history.ScheduleRunStore._rotate_all_sync)
    assert "self._rotate_job_locked(" in source
    # The docstring names the constant when explaining the defect, so assert on the CODE: no slice
    # of `rows` may be written here. That is the shape the duplication took.
    body = source.split('"""')[-1]
    assert "_MAX_RECORDS_PER_JOB" not in body, "the trim must not be re-implemented here"
    assert "_write_jsonl" not in body, "boot rotation must not write job files itself"

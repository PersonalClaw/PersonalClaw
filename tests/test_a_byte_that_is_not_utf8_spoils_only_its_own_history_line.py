"""A byte in an automation's run history that is not UTF-8 spoils only its own line.

🔴 The defect (Backlog: "A byte that is not UTF-8 in an automation's run history breaks its History
and every later run's record"). A run history is one JSON-lines file per automation in
``cron-history/``, plus the index across them. The store's reader skipped a line that is not JSON
(a torn append), but it read the file as text, so one byte that is not UTF-8 (an append a crash
cut inside a character, a damaged disk block) raised out of the whole read. From then on:
- the automation's History did not load, and the run feed across automations answered 400;
- each later run was appended and then failed at the trim that reads the file again, so its
  recording stopped there (status, failure streak, a parked run's question);
- a run's ending could not be written on its row;
- the hourly cap could not count the automation's fires, and let them through.

Now the line is skipped on a read, said once in the log, and written back as it was by every
rewrite: the settle of a run, the start-up pass over every row, the trims, a delete. Driven
through the real store over a scratch home, and the real hourly-cap read.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from personalclaw import schedule_history
from personalclaw.schedule_history import _MAX_RECORDS_PER_JOB, ScheduleRun, ScheduleRunStore
from personalclaw.triggers import service

#: A row an append cut inside "é": ``\xc3`` then ``"`` is not UTF-8.
DAMAGED = b'{"run_id": "cut", "job_id": "j", "summary": "caf\xc3'


def _files(home: Path) -> tuple[Path, Path]:
    return home / "cron-history" / "j.jsonl", home / "cron-history" / "_index.jsonl"


async def _a_history_with_a_damaged_line(home: Path) -> ScheduleRunStore:
    store = ScheduleRunStore(base_dir=home)
    await store.append(ScheduleRun(job_id="j", summary="before", started_at=time.time() - 60))
    for path in _files(home):
        with path.open("ab") as fh:
            fh.write(DAMAGED + b"\n")
    return store


def _still_holds_it(home: Path) -> None:
    for path in _files(home):
        assert DAMAGED + b"\n" in path.read_bytes(), f"{path.name} lost the line"


@pytest.mark.asyncio
async def test_the_history_and_the_run_feed_read_past_it(tmp_path: Path) -> None:
    """🔴 Before: both reads raised UnicodeDecodeError."""
    store = await _a_history_with_a_damaged_line(tmp_path)
    rows, total = await store.list_for_job("j")
    assert (total, [r["summary"] for r in rows]) == (1, ["before"])
    rows, total = await store.list_all()
    assert (total, [r["summary"] for r in rows]) == (1, ["before"])


@pytest.mark.asyncio
async def test_a_later_run_is_recorded_and_the_line_is_kept(tmp_path: Path) -> None:
    """🔴 Before: the append wrote its row, then raised at the trim that reads the file again."""
    store = await _a_history_with_a_damaged_line(tmp_path)
    await store.append(ScheduleRun(job_id="j", summary="after", started_at=time.time()))
    rows, total = await store.list_for_job("j")
    assert (total, [r["summary"] for r in rows]) == (2, ["after", "before"])
    _still_holds_it(tmp_path)


@pytest.mark.asyncio
async def test_the_hourly_cap_still_counts_its_fires(tmp_path: Path) -> None:
    """🔴 Before: the count raised, the read answered None, and the cap let the fire through."""
    await _a_history_with_a_damaged_line(tmp_path)
    capped = SimpleNamespace(id="j", gates={"max_runs_per_hour": 5})
    assert await service._fires_in_window(capped, now=time.time(), base_dir=tmp_path) == 1


@pytest.mark.asyncio
async def test_every_rewrite_writes_it_back_as_it_was(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # An index of 2000 rows trims itself; 50 lets this one reach its trim too.
    monkeypatch.setattr(schedule_history, "_MAX_INDEX_RECORDS", 50)
    store = await _a_history_with_a_damaged_line(tmp_path)

    # The ending of a run its fire launched, on its row in both files.
    await store.append(
        ScheduleRun(job_id="j", status="launched", work_id="w", started_at=time.time())
    )
    assert store.settle_sync("j", "w", status="success", summary="done")
    assert (await store.list_for_job("j", limit=1))[0][0]["summary"] == "done"
    _still_holds_it(tmp_path)

    # The start-up pass over every row (it takes stored secrets' values out).
    assert store.rewrite_sync(lambda row: {**row, "summary": row.get("summary", "").upper()})
    assert (await store.list_for_job("j", limit=1))[0][0]["summary"] == "DONE"
    _still_holds_it(tmp_path)

    # The trims, of the automation's file and of the index.
    for n in range(_MAX_RECORDS_PER_JOB + 5):
        await store.append(ScheduleRun(job_id="j", summary=f"run {n}", started_at=time.time() + n))
    await store.rotate_all()
    assert (await store.list_for_job("j"))[1] == _MAX_RECORDS_PER_JOB
    assert (await store.list_all(limit=500))[1] == 50
    _still_holds_it(tmp_path)

    # A delete of another automation's history leaves the index's damaged line, which names no one.
    await store.append(ScheduleRun(job_id="other", summary="gone soon", started_at=time.time()))
    await store.delete_for_job("other")
    assert all(r["job_id"] == "j" for r in (await store.list_all(limit=500))[0])
    _still_holds_it(tmp_path)


@pytest.mark.asyncio
async def test_the_store_says_once_that_the_file_holds_it(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    store = await _a_history_with_a_damaged_line(tmp_path)
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="personalclaw.schedule_history"):
        for _ in range(3):
            await store.list_for_job("j")
    said = [r for r in caplog.records if "not UTF-8" in r.getMessage()]
    assert len(said) == 1, [r.getMessage() for r in said]
    assert "j.jsonl" in said[0].getMessage()


@pytest.mark.asyncio
async def test_a_torn_append_is_still_left_out_of_a_rewrite(tmp_path: Path) -> None:
    """A line that is UTF-8 but not JSON is a torn append, skipped on a read as before, and not one
    a rewrite keeps: it holds no row a person could recover."""
    store = ScheduleRunStore(base_dir=tmp_path)
    await store.append(ScheduleRun(job_id="j", status="launched", work_id="w", started_at=1.0))
    job, _index = _files(tmp_path)
    with job.open("a", encoding="utf-8") as fh:
        fh.write('{"run_id": "torn", "job_id": "j"\n')
    assert store.settle_sync("j", "w", status="success")
    assert b'"torn"' not in job.read_bytes()

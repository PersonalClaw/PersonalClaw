"""An automation's run history is read only from inside the history's own folder.

Every file the store reads is a job's (``ScheduleRunStore._job_path``, which refuses a job id that
would leave the folder) or the index across them. The read itself checks the very path it opens,
by where that path really is: a path outside the folder (by ``..``, by an absolute path, a sibling
folder whose name only starts the same, or a link inside the folder that points out) reads as a
file that is not there, and nothing outside is opened.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from personalclaw.schedule_history import ScheduleRun, ScheduleRunStore

#: A JSON-lines row that is no part of the history.
OUTSIDE = b'{"run_id": "elsewhere", "job_id": "j", "summary": "not the history\'s"}\n'


async def _a_history_and_a_file_beside_it(home: Path) -> tuple[ScheduleRunStore, Path, Path]:
    store = ScheduleRunStore(base_dir=home)
    await store.append(ScheduleRun(job_id="j", summary="mine", started_at=time.time()))
    elsewhere = home / "elsewhere.jsonl"
    elsewhere.write_bytes(OUTSIDE)
    return store, home / "cron-history", elsewhere


@pytest.mark.asyncio
async def test_a_path_outside_the_folder_reads_as_a_file_that_is_not_there(tmp_path: Path) -> None:
    """🔴 Before: the read opened whatever path it was handed."""
    store, folder, elsewhere = await _a_history_and_a_file_beside_it(tmp_path)
    sibling = tmp_path / "cron-history-beside" / "j.jsonl"
    sibling.parent.mkdir()
    sibling.write_bytes(OUTSIDE)
    for path in (folder / ".." / "elsewhere.jsonl", elsewhere.resolve(), sibling):
        assert store._read(path).rows == [], path
    assert [row["summary"] for row in store._read(folder / "j.jsonl").rows] == ["mine"]


@pytest.mark.asyncio
async def test_a_link_in_the_folder_that_points_out_is_not_followed(tmp_path: Path) -> None:
    store, folder, elsewhere = await _a_history_and_a_file_beside_it(tmp_path)
    (folder / "linked.jsonl").symlink_to(elsewhere)
    assert store._read(folder / "linked.jsonl").rows == []


@pytest.mark.asyncio
async def test_the_index_and_the_folder_walks_still_read_past_a_link_that_points_out(
    tmp_path: Path,
) -> None:
    """The index and the walks over the folder's files read as before, and a link among those
    files that points out is passed over, not followed and not a failure of the walk."""
    store, folder, elsewhere = await _a_history_and_a_file_beside_it(tmp_path)
    (folder / "linked.jsonl").symlink_to(elsewhere)
    rows, total = await store.list_all()
    assert (total, [row["summary"] for row in rows]) == (1, ["mine"])
    assert store.rewrite_sync(lambda row: {**row, "summary": row["summary"].upper()}) is True
    assert [row["summary"] for row in store._read(folder / "j.jsonl").rows] == ["MINE"]
    assert elsewhere.read_bytes() == OUTSIDE, "the file the link points at was read or written"
    assert store.left_unsettled(work_prefix="w") == []


@pytest.mark.parametrize("job_id", ["../elsewhere", "a/../../elsewhere", "/etc/hosts"])
def test_a_job_id_that_would_leave_the_folder_is_refused(tmp_path: Path, job_id: str) -> None:
    with pytest.raises(ValueError, match="unsafe job_id"):
        ScheduleRunStore(base_dir=tmp_path)._job_path(job_id)

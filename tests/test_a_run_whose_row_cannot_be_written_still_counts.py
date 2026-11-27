"""A run whose row the run history cannot write still counts among its automation's runs.

🔴 The defect. The run history writes each run's row to its automation's file, and a write that
fails there (a full disk, a history folder that cannot be written) raised out of the write. The
recorder, which never raises, dropped the run with a debug line: the run had happened, and no
count of its automation's runs saw it.

Now the history keeps such a run, in this process, among its automation's runs for as long as it
can fall in a window (`ScheduleRunStore.count_since` adds it in), and says once in the log why the
automation's runs are not being recorded. A row that did land on the automation's file is counted
from the file, never twice. Driven through the real fire recorder (`run_record.record_run`), the
real run history and the real count of an automation's runs in the window, in a home of the
test's own.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from pathlib import Path

import pytest

from personalclaw.action_providers.base import ActionResult
from personalclaw.schedule_history import ScheduleRunStore
from personalclaw.triggers import run_source, service
from personalclaw.triggers.models import Trigger
from personalclaw.triggers.run_record import record_run
from personalclaw.triggers.store import TriggerStore

TID = "clock:nightly-export"


@pytest.fixture()
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: home)
    return home


def _automation(home: Path) -> Trigger:
    trigger = Trigger(
        id=TID,
        name="Nightly export",
        kind="clock",
        spec={"kind": "cron", "expr": "0 9 * * *"},
        gates={"max_runs_per_hour": 2},
    )
    TriggerStore(base_dir=home).upsert(trigger)
    return trigger


def _ran(trigger: Trigger, *, source: str = "schedule") -> str:
    result = ActionResult(success=True, exit_code=0, stdout="exported", summary="exported")
    return asyncio.run(record_run(trigger, started_at=time.time(), result=result, source=source))


def _counted(trigger: Trigger, home: Path, *, since: float = 0.0) -> int:
    since = since or time.time() - 3600.0
    return asyncio.run(ScheduleRunStore(home).count_since(trigger.id, since))


def _in_the_window(trigger: Trigger, home: Path) -> int | None:
    return asyncio.run(service._fires_in_window(trigger, now=time.time(), base_dir=home))


@pytest.fixture()
def unwritable(home: Path):
    """The history folder, which cannot be written while the test runs."""
    folder = home / "cron-history"
    folder.mkdir()
    folder.chmod(0o500)
    yield folder
    folder.chmod(0o700)


def test_a_run_with_no_row_is_counted(home, unwritable):
    """🔴 Before: three runs, no row each, and a count of 0."""
    trigger = _automation(home)
    assert [_ran(trigger) for _ in range(3)] == ["", "", ""], "the folder took no row"
    assert _counted(trigger, home) == 3
    assert _in_the_window(trigger, home) == 3


def test_it_is_said_once_in_the_log(home, unwritable, caplog):
    trigger = _automation(home)
    with caplog.at_level(logging.WARNING, logger="personalclaw.schedule_history"):
        for _ in range(3):
            _ran(trigger)
    said = [r.getMessage() for r in caplog.records if "could not be recorded" in r.getMessage()]
    assert len(said) == 1, said
    assert TID in said[0] and str(unwritable) in said[0]


def test_a_run_of_yours_is_left_out_as_its_row_would_be(home, unwritable):
    """The count passes over a run of yours, written or not."""
    trigger = _automation(home)
    _ran(trigger, source=run_source.YOU)
    assert _counted(trigger, home) == 0


def test_it_counts_only_inside_its_window(home, unwritable):
    trigger = _automation(home)
    _ran(trigger)
    assert _counted(trigger, home, since=time.time() + 60) == 0


def test_a_row_that_landed_is_counted_once(home):
    """The automation's own row is written and the index after it is not: the row is counted
    from the file, and not again."""
    trigger = _automation(home)
    folder = home / "cron-history"
    folder.mkdir()
    index = folder / "_index.jsonl"
    index.write_text("")
    index.chmod(0o400)
    try:
        _ran(trigger)
    finally:
        index.chmod(0o600)
    assert _counted(trigger, home) == 1


def test_runs_written_again_are_counted_with_the_ones_that_were_not(home, unwritable):
    """Once the folder can be written, each run is counted from its row, beside the earlier
    ones that have none: nothing is counted twice, and nothing is let go."""
    trigger = _automation(home)
    _ran(trigger)
    _ran(trigger)
    unwritable.chmod(0o700)
    assert _ran(trigger), "this one has its row"
    assert _counted(trigger, home) == 3


def test_a_run_whose_row_cannot_even_be_made_is_counted(home, monkeypatch):
    """A row that cannot be made (`ScheduleRun.to_dict` raising) is one that cannot be read for
    the rule either: the run still ran, so it is counted."""
    from personalclaw.schedule_history import ScheduleRun

    def _no_row(self, *, include_trace: bool = True):
        raise TypeError("a field that is not JSON")

    trigger = _automation(home)
    with monkeypatch.context() as patched:
        patched.setattr(ScheduleRun, "to_dict", _no_row)
        assert _ran(trigger) == ""
    assert _counted(trigger, home) == 1


def test_another_homes_history_is_not_counted_here(home, unwritable, tmp_path):
    """Kept by history folder: a run this home could not write is not another home's run."""
    trigger = _automation(home)
    _ran(trigger)
    other = tmp_path / "other"
    other.mkdir()
    assert asyncio.run(ScheduleRunStore(other).count_since(TID, time.time() - 3600.0)) == 0


def test_the_folder_really_could_not_be_written(home, unwritable):
    """A guard on the test itself: as this user, the folder refuses a new file."""
    if os.geteuid() == 0:
        pytest.skip("root writes through a folder's mode")
    with pytest.raises(PermissionError):
        (unwritable / "probe.jsonl").write_text("{}\n")

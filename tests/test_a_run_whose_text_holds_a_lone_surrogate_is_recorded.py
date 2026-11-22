"""A run whose text holds a lone surrogate is recorded, and its automation's hourly cap counts it.

🔴 The defect (found in review of c8f22d1d2). A JSON ``\\ud800`` escape, in a webhook body or any
payload an action hands on, decodes to a lone surrogate, which UTF-8 cannot encode. The run
history wrote its rows as UTF-8 text, so the write raised; the recorder, which never raises,
dropped the run with a debug line. The run had happened and was nowhere: not in its History,
and not in the count the hourly cap reads, so a fire carrying such text was never counted.

Now every row is written with the characters past ASCII escaped, which is plain JSON and reads
back to the same text. Driven through the real fire recorder (`run_record.record_run`), the real
run history and the real hourly-cap read, in a home of the test's own.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from personalclaw.action_providers.base import ActionResult
from personalclaw.schedule_history import ScheduleRun, ScheduleRunStore
from personalclaw.triggers import service
from personalclaw.triggers.models import Trigger
from personalclaw.triggers.run_record import record_run
from personalclaw.triggers.store import TriggerStore

#: What a payload's ``"\\ud800"`` decodes to, inside the words an action wrote from it.
SAID = "hook said: caf\ud800 is open"


@pytest.fixture()
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: home)
    return home


def _fired(home: Path, result: ActionResult, trigger_id: str = "clock:hook") -> Trigger:
    trigger = Trigger(
        id=trigger_id,
        name="Hook relay",
        kind="clock",
        spec={"kind": "cron", "expr": "0 9 * * *"},
        gates={"max_runs_per_hour": 5},
    )
    TriggerStore(base_dir=home).upsert(trigger)
    asyncio.run(record_run(trigger, started_at=time.time(), result=result, source="schedule"))
    return trigger


def test_the_run_is_in_its_history_with_its_own_words(home: Path) -> None:
    """🔴 Before: the write raised UnicodeEncodeError and the run had no row."""
    _fired(home, ActionResult(success=True, exit_code=0, stdout=SAID, summary=SAID))
    runs = ScheduleRunStore(home)
    (row,), total = asyncio.run(runs.list_for_job("clock:hook", 0, 5))
    assert total == 1
    assert row["summary"] == SAID
    full = asyncio.run(runs.get_run("clock:hook", row["run_id"]))
    assert full is not None and full["trace"] == SAID


def test_the_hourly_cap_counts_it(home: Path) -> None:
    """🔴 Before: the cap's count passed over the run, which had no row."""
    trigger = _fired(home, ActionResult(success=True, exit_code=0, stdout=SAID, summary=SAID))
    assert asyncio.run(service._fires_in_window(trigger, now=time.time(), base_dir=home)) == 1


def test_a_rewrite_keeps_it(home: Path) -> None:
    """The settle of a launched run, and the start-up pass over every row, write the file whole."""
    runs = ScheduleRunStore(home)
    runs.append_sync(ScheduleRun(job_id="j", status="launched", work_id="w", started_at=1.0))
    assert runs.settle_sync("j", "w", status="success", summary=SAID)
    assert runs.rewrite_sync(lambda row: {**row, "error": SAID})
    (row,), _total = asyncio.run(runs.list_for_job("j", 0, 5))
    assert (row["summary"], row["error"]) == (SAID, SAID)


def test_words_past_ascii_read_back_as_they_were(home: Path) -> None:
    """Escaped on disk, the same text when read: an accent, an emoji, a script past Latin."""
    words = "café ✅ 日本語 🧭"
    runs = ScheduleRunStore(home)
    runs.append_sync(ScheduleRun(job_id="j", summary=words, started_at=1.0))
    (row,), _total = asyncio.run(runs.list_for_job("j", 0, 5))
    assert row["summary"] == words
    assert (home / "cron-history" / "j.jsonl").read_bytes().isascii()


def test_the_capped_read_is_this_homes(home: Path) -> None:
    """A guard on the test itself: the count above is the run this home recorded, not zero from
    another home's empty history."""
    capped = SimpleNamespace(id="clock:none", gates={"max_runs_per_hour": 5})
    assert asyncio.run(service._fires_in_window(capped, now=time.time(), base_dir=home)) == 0

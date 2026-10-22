"""An automation's run history is newest first by each run's own start time, ties by run id (#3816).

The Automations list's right pane lists the selected automation's runs as `GET
/api/triggers/{id}/history` serves them. The order is the route's to state: newest first by when
each run started, whatever order the history file holds them in (a run settled late is rewritten
in place, and a merged home's file is in no particular order), and two runs that started at the same
moment by their run id, so a reread never swaps them.
"""

from __future__ import annotations

import json
import random

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from personalclaw.dashboard.handlers import triggers as T
from personalclaw.schedule_history import ScheduleRun, ScheduleRunStore
from personalclaw.triggers.models import Trigger
from personalclaw.triggers.store import TriggerStore

AT = 1_800_000_000.0


@pytest.fixture
def home(tmp_path, monkeypatch):
    import personalclaw.config.loader as loader

    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(T, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(T, "_trigger_store", lambda: TriggerStore(base_dir=tmp_path))
    TriggerStore(base_dir=tmp_path).upsert(
        Trigger(
            id="nightly",
            name="Nightly",
            kind="clock",
            spec={"kind": "interval", "interval_secs": 3600},
        )
    )
    return tmp_path


#: (run id, started at): two pairs started at the same moment.
RUNS = [
    ("r-03", AT + 300),
    ("r-10", AT + 100),
    ("r-07", AT + 200),
    ("r-02", AT + 200),
    ("r-01", AT),
    ("r-99", AT + 100),
]

NEWEST_FIRST = ["r-03", "r-02", "r-07", "r-10", "r-99", "r-01"]


async def _history(seed: int, home) -> list[str]:
    runs = list(RUNS)
    random.Random(seed).shuffle(runs)
    store = ScheduleRunStore(home)
    for run_id, started in runs:
        store.append_sync(
            ScheduleRun(run_id=run_id, job_id="nightly", started_at=started, status="success")
        )
    app = web.Application()
    app["state"] = object()
    req = make_mocked_request(
        "GET",
        "/api/triggers/schedule:nightly/history?limit=20",
        match_info={"id": "schedule:nightly"},
        app=app,
    )
    body = json.loads((await T.api_trigger_history(req)).body.decode())
    return [r["run_id"] for r in body["runs"]]


@pytest.mark.asyncio
@pytest.mark.parametrize("seed", range(5))
async def test_runs_are_newest_first_by_their_own_start_ties_by_run_id(seed, home):
    assert await _history(seed, home) == NEWEST_FIRST


@pytest.mark.asyncio
async def test_a_run_with_no_start_comes_after_the_timed_ones_the_last_written_first(home):
    store = ScheduleRunStore(home)
    for run_id, started in (("u-1", 0.0), ("t-1", AT), ("u-2", 0.0), ("t-2", AT + 1)):
        store.append_sync(
            ScheduleRun(run_id=run_id, job_id="nightly", started_at=started, status="success")
        )
    rows, _total = await store.list_for_job("nightly", 0, 10)
    assert [r["run_id"] for r in rows] == ["t-2", "t-1", "u-2", "u-1"]

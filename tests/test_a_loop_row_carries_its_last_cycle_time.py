"""A loop row says when the loop's newest cycle ran (`last_cycle_at`).

The sidebar orders a project's threads by their latest activity, and a loop's is its newest
cycle's time. `GET /api/loops` carried no cycle time, so the sidebar stood in with the latest
activity of the loop's worker chats, else when the loop completed, started or was created. Each row
now carries `last_cycle_at`, in the epoch seconds the row's other times are in, and `null` while
the loop has run no cycle, whichever home backs the loop:

- a loops-table loop: the newest cycle on its ledger, a finding the worker filed or a cycle its
  owner ended with a Deny. A breaker trip or a judge's verdict is about a cycle, not one of its
  own.
- a run-backed loop: the newest iteration of its root loop, which is what its cycles are. A nested
  loop's iterations are steps inside one cycle.

The list and the detail view carry the same value, so a row and its page agree.
"""

from __future__ import annotations

import asyncio
import calendar
import json
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from personalclaw.dashboard.handlers import loop_routes as H
from personalclaw.ledger import writer
from personalclaw.loop import files as loop_files
from personalclaw.loop import store as loop_store
from personalclaw.loop.journal import LoopJournal
from personalclaw.loop.loop import Loop
from personalclaw.workflows import journal as journal_mod
from personalclaw.workflows import store
from personalclaw.workflows.bundled_defs import read_template
from personalclaw.workflows.models import RunStatus, WorkflowRun


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: home)
    monkeypatch.setattr("personalclaw.workflows.leases.config_dir", lambda: home)
    monkeypatch.setattr("personalclaw.loop.files.config_dir", lambda: home)
    return home


@pytest.fixture
def stamp(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[str]]:
    """The stamp the ledger writes on its next records: set ``stamp[0]`` before each write."""
    at = ["2026-10-01T09:00:00Z"]
    monkeypatch.setattr(writer, "now", lambda: at[0])
    yield at


def _epoch(ts: str) -> float:
    return float(calendar.timegm(time.strptime(ts, "%Y-%m-%dT%H:%M:%SZ")))


def _call(handler: Any, path: str, match: dict[str, str] | None = None) -> Any:
    app = web.Application()
    request = make_mocked_request("GET", path, app=app, match_info=match or {})
    request["user"] = "owner"
    response = asyncio.run(handler(request))
    assert response.status == 200, response.body
    return json.loads(response.body.decode())


def _row(loop_id: str) -> dict[str, Any]:
    rows = [r for r in _call(H.api_loop_list, "/api/loops")["loops"] if r["id"] == loop_id]
    assert len(rows) == 1, rows
    return rows[0]


def _detail(loop_id: str) -> dict[str, Any]:
    return _call(H.api_loop_get, f"/api/loops/{loop_id}", {"id": loop_id})


def _goal_loop() -> Loop:
    return loop_store.create(Loop(id="", name="Packing", kind="goal", task="find the best packing"))


def _loop_run() -> WorkflowRun:
    run = store.create(
        WorkflowRun(
            id="",
            workflow_name="general-project",
            status=RunStatus.RUNNING,
            inputs={"task": "write a three-item checklist", "exit_condition": "it exists"},
            policy_overrides={"attended": False},
            loop_kind="general",
            title="Weekly checklist",
            created_at="2026-10-01T08:00:00Z",
            started_at="2026-10-01T08:00:05Z",
        )
    )
    store.write_spec(run.id, read_template("general-project").to_dict())
    return run


def test_a_loops_table_row_carries_its_newest_cycles_time(stamp: list[str]) -> None:
    loop = _goal_loop()
    journal = LoopJournal(loop.id)
    for n, ts in enumerate(["2026-10-01T09:00:00Z", "2026-10-01T09:20:00Z"], start=1):
        stamp[0] = ts
        journal.cycle(n, {"cycle": n, "summary": f"cycle {n}"})
    # About a cycle, not cycles of their own.
    stamp[0] = "2026-10-01T10:00:00Z"
    journal.verdict({"cycle": 2, "verdict": "continue", "done": False})
    journal.breaker_trip(2, "identical output")

    assert _row(loop.id)["last_cycle_at"] == _epoch("2026-10-01T09:20:00Z")
    assert _detail(loop.id)["last_cycle_at"] == _epoch("2026-10-01T09:20:00Z")


def test_a_cycle_its_owner_ended_with_a_deny_is_its_newest_cycle(stamp: list[str]) -> None:
    loop = _goal_loop()
    stamp[0] = "2026-10-01T09:00:00Z"
    LoopJournal(loop.id).cycle(1, {"cycle": 1, "summary": "the first"})
    stamp[0] = "2026-10-01T09:45:00Z"
    loop_files.record_declined_cycle(loop.id, 2, task_id="", reason="Not that.", steps=["shell"])

    assert _row(loop.id)["last_cycle_at"] == _epoch("2026-10-01T09:45:00Z")


def test_a_loop_that_has_run_no_cycle_says_none() -> None:
    loop = _goal_loop()
    run = _loop_run()
    for loop_id in (loop.id, run.id):
        row = _row(loop_id)
        assert "last_cycle_at" in row and row["last_cycle_at"] is None, row
        assert _detail(loop_id)["last_cycle_at"] is None


def test_a_run_backed_row_carries_its_root_loops_newest_iteration(stamp: list[str]) -> None:
    run = _loop_run()
    journal = journal_mod.Journal(run.id)
    stamp[0] = "2026-10-01T09:00:00Z"
    journal.iteration("root", "project", iteration=0, outcome="continue")
    stamp[0] = "2026-10-01T09:30:00Z"
    journal.iteration("root", "project", iteration=1, outcome="continue")
    # A loop nested inside a cycle iterates within it: its rows are steps of the root's cycle.
    stamp[0] = "2026-10-01T09:50:00Z"
    journal.iteration("root/review", "review", iteration=0, outcome="continue")

    assert _row(run.id)["last_cycle_at"] == _epoch("2026-10-01T09:30:00Z")
    assert _detail(run.id)["last_cycle_at"] == _epoch("2026-10-01T09:30:00Z")

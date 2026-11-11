"""Every row of a project's work board says when it last changed (`updated_at`).

A project's page sorts the work that is no longer live by when it last moved, and its run rows
come from the board (`GET /api/projects/{id}/work`). The board said what each row was and what
state it was in, never when, so finished runs could not be placed among the project's chats. Each
row now carries `updated_at`, in the `%Y-%m-%dT%H:%M:%SZ` spelling the run and task stores write:

- a run's newest stamp of when it completed, started or was created, and for a run started as a
  loop its newest cycle, as for a loop;
- a loop's newest cycle, else the newest of when it completed, started or was created;
- a task's own `updated_at`.

A ledger that cannot be read leaves a loop its stored times. The board regroups its rows from
their dict form (`_as_board_row`), so the field is read off the grouped board, where a row that
lost it on the way would show.
"""

from __future__ import annotations

import calendar
import time
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.ledger import writer
from personalclaw.tasks import registry
from personalclaw.tasks.handlers import register_task_routes

_SPELLING = "%Y-%m-%dT%H:%M:%SZ"


@asynccontextmanager
async def _client(tmp_path):
    """The task routes over one temp home: every store the board reads points at it."""
    registry._providers.clear()
    with (
        patch("personalclaw.tasks.native.config_dir", return_value=tmp_path),
        patch("personalclaw.tasks.hierarchy.config_dir", return_value=tmp_path),
        patch("personalclaw.workflows.store.config_dir", return_value=tmp_path),
        patch("personalclaw.workflows.leases.config_dir", return_value=tmp_path),
        patch("personalclaw.concurrency.config_dir", return_value=tmp_path),
        patch("personalclaw.loop.files.config_dir", return_value=tmp_path),
    ):
        from personalclaw.dashboard.request_boundary import request_boundary_middleware

        app = web.Application(middlewares=[request_boundary_middleware()])
        register_task_routes(app)
        async with TestClient(TestServer(app)) as client:
            yield client
    registry._providers.clear()


def _epoch(stamp: str) -> float:
    return float(calendar.timegm(time.strptime(stamp, _SPELLING)))


async def _board_rows(client, pid: str) -> dict[str, dict]:
    r = await client.get(f"/api/projects/{pid}/work")
    assert r.status == 200, await r.text()
    body = await r.json()
    assert body["completeness"] == "complete", body["sections"]
    return {row["title"]: row for group in body["board"] for row in group["rows"]}


@pytest.mark.asyncio
async def test_each_source_says_when_its_row_last_changed(tmp_path, monkeypatch):
    from personalclaw.loop import store as loop_store
    from personalclaw.loop.journal import LoopJournal
    from personalclaw.loop.loop import Loop
    from personalclaw.workflows import journal as journal_mod
    from personalclaw.workflows import store as run_store
    from personalclaw.workflows.bundled_defs import read_template
    from personalclaw.workflows.models import RunStatus, WorkflowRun

    stamp = ["2026-10-01T09:00:00Z"]
    monkeypatch.setattr(writer, "now", lambda: stamp[0])
    clock = ["2026-10-01T10:00:00Z"]
    monkeypatch.setattr("personalclaw.tasks.native._now_iso", lambda: clock[0])

    async with _client(tmp_path) as client:
        r = await client.post("/api/projects", json={"name": "Garden"})
        pid = (await r.json())["id"]

        for name, status, stamps in (
            ("Soil report", RunStatus.COMPLETE, ("08:00:00", "08:00:05", "08:30:00")),
            ("Running report", RunStatus.RUNNING, ("06:00:00", "06:10:00", "")),
            ("Paused report", RunStatus.PAUSED, ("07:00:00", "", "")),
        ):
            created, started, completed = (f"2026-10-01T{s}Z" if s else "" for s in stamps)
            run_store.create(
                WorkflowRun(
                    id="",
                    workflow_name=name,
                    status=status,
                    project_id=pid,
                    created_at=created,
                    started_at=started or None,
                    completed_at=completed or None,
                )
            )
        looped = run_store.create(
            WorkflowRun(
                id="",
                workflow_name="general-project",
                status=RunStatus.RUNNING,
                project_id=pid,
                inputs={"task": "a weekly checklist", "exit_condition": "it exists"},
                loop_kind="general",
                title="Weekly checklist",
                created_at="2026-10-01T05:00:00Z",
                started_at="2026-10-01T05:00:05Z",
            )
        )
        run_store.write_spec(looped.id, read_template("general-project").to_dict())
        stamp[0] = "2026-10-01T11:30:00Z"
        journal_mod.Journal(looped.id).iteration("root", "project", iteration=0, outcome="continue")

        cycled = loop_store.create(
            Loop(
                id="", kind="goal", name="Spring beds", task="plan the spring beds", project_id=pid
            )
        )
        journal = LoopJournal(cycled.id)
        for n, ts in enumerate(["2026-10-01T09:00:00Z", "2026-10-01T09:20:00Z"], start=1):
            stamp[0] = ts
            journal.cycle(n, {"cycle": n, "summary": f"cycle {n}"})
        for name, stamps in (
            ("Seed order", ("06:00:00", "06:10:00", "06:20:00")),
            ("Bed layout", ("05:00:00", "05:30:00", "")),
        ):
            created, started, completed = (
                _epoch(f"2026-10-01T{s}Z") if s else None for s in stamps
            )
            loop_store.create(
                Loop(
                    id="",
                    kind="goal",
                    name=name,
                    task=name.lower(),
                    project_id=pid,
                    created_at=created,
                    started_at=started,
                    completed_at=completed,
                )
            )

        task = await (
            await client.post("/api/tasks", json={"title": "Order the soil", "project_id": pid})
        ).json()
        clock[0] = "2026-10-01T11:00:00Z"
        r = await client.put(f"/api/tasks/{task['id']}", json={"status": "in_progress"})
        assert r.status == 200, await r.text()

        rows = await _board_rows(client, pid)

    assert rows["Soil report"]["updated_at"] == "2026-10-01T08:30:00Z"
    assert rows["Running report"]["updated_at"] == "2026-10-01T06:10:00Z"
    assert rows["Paused report"]["updated_at"] == "2026-10-01T07:00:00Z"
    assert rows["general-project"]["updated_at"] == "2026-10-01T11:30:00Z"
    assert rows["Spring beds"]["updated_at"] == "2026-10-01T09:20:00Z"
    assert rows["Seed order"]["updated_at"] == "2026-10-01T06:20:00Z"
    assert rows["Bed layout"]["updated_at"] == "2026-10-01T05:30:00Z"
    assert task["created_at"] == "2026-10-01T10:00:00Z"
    assert rows["Order the soil"]["updated_at"] == "2026-10-01T11:00:00Z"


@pytest.mark.asyncio
async def test_an_unreadable_loop_ledger_leaves_the_loop_its_stored_times(tmp_path, monkeypatch):
    from personalclaw.loop import store as loop_store
    from personalclaw.loop.loop import Loop

    def unreadable(loop_id: str):
        raise OSError("events.jsonl is unreadable")

    async with _client(tmp_path) as client:
        pid = (await (await client.post("/api/projects", json={"name": "Garden"})).json())["id"]
        loop_store.create(
            Loop(
                id="",
                kind="goal",
                name="Seed order",
                task="order the seeds",
                project_id=pid,
                created_at=_epoch("2026-10-01T06:00:00Z"),
            )
        )
        monkeypatch.setattr("personalclaw.loop.files.findings_and_last_cycle", unreadable)
        rows = await _board_rows(client, pid)

    assert rows["Seed order"]["updated_at"] == "2026-10-01T06:00:00Z"


def test_a_row_with_no_time_says_none_rather_than_a_guess():
    """`""`, never "now": a row that cannot say when it changed must not sort as the newest."""
    from personalclaw.tasks import hierarchy_handlers
    from personalclaw.workflows import containers
    from personalclaw.workflows.models import RunStatus, WorkflowRun

    row = containers.BoardRow(run_id="r1", title="t", state=containers.BoardState.QUEUED)
    assert row.to_dict()["updated_at"] == ""
    bare = WorkflowRun(id="r2", workflow_name="w", status=RunStatus.DRAFT, created_at="")
    assert containers.board_row(bare).to_dict()["updated_at"] == ""
    odd = WorkflowRun(id="r3", workflow_name="w", status=RunStatus.DRAFT, created_at="not a time")
    assert containers.board_row(odd).to_dict()["updated_at"] == ""
    bare_task = SimpleNamespace(id="t1", title="Bare task")
    assert hierarchy_handlers._task_rows([bare_task], "p1")[0]["updated_at"] == ""

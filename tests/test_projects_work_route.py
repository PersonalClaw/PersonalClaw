"""HTTP tests for GET /api/projects/{id}/work + the claim/release POSTs
(WORK-CONTAINERS §1/§5.2/§6.1).

The load-bearing claims:

* the board is grouped in BOARD_ORDER with needs-input pinned first;
* runs, legacy loops and standalone tasks all appear on ONE board;
* PER-SECTION ISOLATION — a failing source degrades ITS section (`status:"error"`) while
  the others stay `ok` and the board still renders, with `completeness:"partial"`;
* a missing project is a 404, not an empty board;
* the claim/release routes drive the flock-backed lease and a second holder is refused.
"""

from contextlib import asynccontextmanager
from unittest.mock import patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.tasks import registry
from personalclaw.tasks.handlers import register_task_routes


@asynccontextmanager
async def _client(tmp_path):
    """Task routes over isolated stores — every config_dir the /work handler reaches
    (run store, leases, the flock, the loop store) points at one temp home."""
    registry._providers.clear()
    with (
        patch("personalclaw.tasks.native.config_dir", return_value=tmp_path),
        patch("personalclaw.tasks.hierarchy.config_dir", return_value=tmp_path),
        patch("personalclaw.workflows.store.config_dir", return_value=tmp_path),
        patch("personalclaw.workflows.leases.config_dir", return_value=tmp_path),
        patch("personalclaw.concurrency.config_dir", return_value=tmp_path),
        patch("personalclaw.loop.files.config_dir", return_value=tmp_path),
    ):
        # Installed because `dashboard/server.py` installs it on the real gateway: a route
        # reading its body through `personalclaw.request_validation` raises
        # `RequestValidationError` and answers its 400 THERE, so a test app without it
        # does not model the gateway and reports a deliberate refusal as a 500.
        from personalclaw.dashboard.request_boundary import request_boundary_middleware

        app = web.Application(middlewares=[request_boundary_middleware()])
        register_task_routes(app)
        async with TestClient(TestServer(app)) as client:
            yield client
    registry._providers.clear()


async def _mk_project(client, name="Board") -> str:
    r = await client.post("/api/projects", json={"name": name})
    return (await r.json())["id"]


@pytest.mark.asyncio
async def test_missing_project_is_404(tmp_path):
    async with _client(tmp_path) as client:
        assert (await client.get("/api/projects/p-nope/work")).status == 404


@pytest.mark.asyncio
async def test_empty_project_returns_a_complete_empty_board(tmp_path):
    async with _client(tmp_path) as client:
        pid = await _mk_project(client)
        r = await client.get(f"/api/projects/{pid}/work")
        assert r.status == 200
        body = await r.json()
        assert body["board"] == []
        assert body["completeness"] == "complete"
        assert {s["name"] for s in body["sections"]} == {"runs", "loops", "tasks"}
        assert all(s["status"] == "ok" for s in body["sections"])


@pytest.mark.asyncio
async def test_runs_loops_and_tasks_all_appear_on_one_board(tmp_path):
    from personalclaw.loop import store as loop_store
    from personalclaw.loop.loop import Loop
    from personalclaw.workflows import store as run_store
    from personalclaw.workflows.models import RunStatus, WorkflowRun

    async with _client(tmp_path) as client:
        pid = await _mk_project(client)
        # a WF2 run bound to the project
        run_store.create(
            WorkflowRun(id="", workflow_name="analyze", status=RunStatus.RUNNING, project_id=pid)
        )
        # a legacy loop under the project
        loop_store.create(Loop(id="", kind="goal", name="Loopy", task="g" * 30, project_id=pid))
        # a standalone task under the project (via project_id → its General list, so the
        # native provider's derived project label matches the /work source's NAME lookup)
        await client.post("/api/tasks", json={"title": "Do the thing", "project_id": pid})

        r = await client.get(f"/api/projects/{pid}/work")
        body = await r.json()
        assert body["completeness"] == "complete"
        titles = {row["title"] for group in body["board"] for row in group["rows"]}
        assert {"analyze", "Loopy", "Do the thing"} <= titles


@pytest.mark.asyncio
async def test_every_done_row_says_how_it_ended(tmp_path):
    """`done` is the board's "nothing left to do" group, and a cancelled task belongs in it —
    but measured on a project page, a cancelled task sat there with the same glyph as the
    completed task beside it, so the board said the press kit had shipped. Each DONE row now
    carries its OWN ending, from every source, and a row that has not ended carries none."""
    from personalclaw.loop import store as loop_store
    from personalclaw.loop.loop import Loop, LoopStatus, LoopStopReason
    from personalclaw.workflows import store as run_store
    from personalclaw.workflows.models import RunStatus, WorkflowRun

    async with _client(tmp_path) as client:
        pid = await _mk_project(client)
        for title, status in (
            ("Draft copy", "done"),
            ("Press kit", "cancelled"),
            ("Optional teaser", "skipped"),
            ("Pricing sheet", "open"),
        ):
            r = await client.post("/api/tasks", json={"title": title, "project_id": pid})
            tid = (await r.json())["id"]
            if status != "open":
                r = await client.put(f"/api/tasks/{tid}", json={"status": status})
                assert r.status == 200, await r.text()
        for name, status, reason in (
            ("Genuine", LoopStatus.COMPLETE, LoopStopReason.DONE),
            ("Out of budget", LoopStatus.COMPLETE, LoopStopReason.CYCLE_BUDGET),
            ("Halted", LoopStatus.STOPPED, LoopStopReason.USER),
            ("Crashed", LoopStatus.FAILED, LoopStopReason.WORKER_FAILED),
        ):
            lp = loop_store.create(
                Loop(id="", kind="goal", name=name, task=name * 5, project_id=pid)
            )
            loop_store.update_status(lp.id, LoopStatus.RUNNING)
            loop_store.update_status(lp.id, status, stop_reason=reason)
        for name, status in (
            ("run-ok", RunStatus.COMPLETE),
            ("run-bad", RunStatus.FAILED),
            ("run-off", RunStatus.CANCELLED),
        ):
            run_store.create(WorkflowRun(id="", workflow_name=name, status=status, project_id=pid))

        body = await (await client.get(f"/api/projects/{pid}/work")).json()
        by_state = {
            g["state"]: {row["title"]: row["outcome"] for row in g["rows"]} for g in body["board"]
        }
        assert by_state["done"] == {
            "Draft copy": "completed",
            "Press kit": "cancelled",
            "Optional teaser": "skipped",
            "Genuine": "completed",
            "Out of budget": "ended_early",
            "Halted": "stopped",
            "Crashed": "failed",
            "run-ok": "completed",
            "run-bad": "failed",
            "run-off": "cancelled",
        }
        # A row that has not ended claims no ending.
        assert by_state["queued"] == {"Pricing sheet": ""}


@pytest.mark.asyncio
async def test_needs_input_group_is_pinned_first(tmp_path):
    from personalclaw.loop import store as loop_store
    from personalclaw.loop.loop import Loop, LoopStatus
    from personalclaw.workflows import store as run_store
    from personalclaw.workflows.models import RunStatus, WorkflowRun

    async with _client(tmp_path) as client:
        pid = await _mk_project(client)
        # a WORKING run + a NEEDS_INPUT-mapped loop (blocked)
        run_store.create(
            WorkflowRun(
                id="",
                workflow_name="w",
                status=RunStatus.RUNNING,
                project_id=pid,
                started_at="2026-01-01T00:00:00Z",
            )
        )
        lp = loop_store.create(
            Loop(id="", kind="goal", name="Blocked", task="b" * 30, project_id=pid)
        )
        loop_store.update_status(lp.id, LoopStatus.BLOCKED)  # DB row, not just status.json

        body = await (await client.get(f"/api/projects/{pid}/work")).json()
        states = [g["state"] for g in body["board"]]
        assert states[0] == "needs_input"  # pinned first, unconditionally
        assert body["attention"] >= 1


@pytest.mark.asyncio
async def test_a_failing_source_degrades_only_its_section(tmp_path):
    """Per-section isolation: break the tasks source; runs/loops stay ok, board renders,
    completeness is partial, and the tasks section carries status:error."""
    from personalclaw.workflows import store as run_store
    from personalclaw.workflows.models import RunStatus, WorkflowRun

    async with _client(tmp_path) as client:
        pid = await _mk_project(client)
        run_store.create(
            WorkflowRun(id="", workflow_name="alive", status=RunStatus.RUNNING, project_id=pid)
        )

        # Make the async task list blow up — the tasks section must absorb it alone.
        async def boom(*a, **k):
            raise RuntimeError("task provider down")

        # The route reads the whole set (`collect_tasks`) rather than one window: its tasks
        # section derives dependency state, and a page cannot answer that. Patched where the
        # route actually reads.
        with patch("personalclaw.tasks.registry.collect_tasks", boom):
            r = await client.get(f"/api/projects/{pid}/work")
        body = await r.json()
        assert body["completeness"] == "partial"
        by_name = {s["name"]: s for s in body["sections"]}
        assert by_name["tasks"]["status"] == "error"
        assert by_name["runs"]["status"] == "ok"
        assert by_name["loops"]["status"] == "ok"
        # the board still rendered the runs section's row
        titles = {row["title"] for group in body["board"] for row in group["rows"]}
        assert "alive" in titles


@pytest.mark.asyncio
async def test_claim_and_release_round_trip_and_refusal(tmp_path):
    async with _client(tmp_path) as client:
        pid = await _mk_project(client)
        # claim a target for worker-1
        r = await client.post(
            f"/api/projects/{pid}/work/claim", json={"target_id": "run-1", "holder": "worker-1"}
        )
        assert r.status == 200
        body = await r.json()
        assert body["granted"] is True and body["claim"]["holder"] == "worker-1"
        # a second holder is refused while the first holds
        r2 = await client.post(
            f"/api/projects/{pid}/work/claim", json={"target_id": "run-1", "holder": "worker-2"}
        )
        b2 = await r2.json()
        assert b2["granted"] is False and "worker-1" in b2["reason"]
        # the holder releases it
        r3 = await client.post(
            f"/api/projects/{pid}/work/release", json={"target_id": "run-1", "holder": "worker-1"}
        )
        assert (await r3.json())["released"] is True
        # now worker-2 can take it
        r4 = await client.post(
            f"/api/projects/{pid}/work/claim", json={"target_id": "run-1", "holder": "worker-2"}
        )
        assert (await r4.json())["granted"] is True


@pytest.mark.asyncio
async def test_claim_requires_target_and_holder(tmp_path):
    async with _client(tmp_path) as client:
        pid = await _mk_project(client)
        r = await client.post(f"/api/projects/{pid}/work/claim", json={"target_id": "x"})
        assert r.status == 400


@pytest.mark.asyncio
async def test_claim_on_missing_project_is_404(tmp_path):
    async with _client(tmp_path) as client:
        r = await client.post(
            "/api/projects/p-nope/work/claim", json={"target_id": "x", "holder": "y"}
        )
        assert r.status == 404

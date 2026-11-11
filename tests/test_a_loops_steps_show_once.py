"""A loop's own steps belong to the loop, so a project's work board lists them once.

A loop that plans its work keeps each phase as a task list under its project
(`loop.tasks_link.ensure_phase_lists`, recorded on the loop as `task_list_ids`), and a goal loop
links its sub-goals (`linked_task_ids`) in a Sub-goals list that its project's goal loops share
(`decompose_sub_goals` finds it by name). Those lists read like any of the project's lists, so a
project showed the loop on its board, and then each of the loop's steps again as a task of its own.
Now:

- each row of `GET /api/task-lists` names the loop whose phase list it is (`loop_id`, `""` for any
  other list, the shared Sub-goals list included), with or without a `project_id` filter, and a
  phase list keeps its loop when it moves to another project;
- the project's work board (`GET /api/projects/{id}/work`) lists neither a phase task nor a linked
  sub-goal of a loop it lists: the loop's own row stands for them. Any other task in the shared
  Sub-goals list stays, and so does every step of a loop whose row the board could not make.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.tasks import registry
from personalclaw.tasks.handlers import register_task_routes


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


async def _json(resp):
    assert resp.status in (200, 201), await resp.text()
    return await resp.json()


async def _code_loop_with_a_step(client):
    """Garden, a code loop in it with one phase list holding one step, and a task of its own."""
    from personalclaw.loop import store as loop_store
    from personalclaw.loop.loop import Loop

    pid = (await _json(await client.post("/api/projects", json={"name": "Garden"})))["id"]
    loop = loop_store.create(
        Loop(
            id="", kind="code", name="Irrigation tests", task="fix the sensor tests", project_id=pid
        )
    )
    phase = await _json(
        await client.post(
            "/api/task-lists", json={"name": f"Implementation #{loop.id}", "project_id": pid}
        )
    )
    loop_store.set_tasks_links(loop.id, tasks_project_id=pid, task_list_ids={"impl": phase["id"]})
    await _json(
        await client.post(
            "/api/tasks", json={"title": "Mock the moisture probe", "task_list_id": phase["id"]}
        )
    )
    own = await _json(
        await client.post("/api/tasks", json={"title": "Order the soil", "project_id": pid})
    )
    return pid, loop, phase, own


async def _board(client, pid: str) -> tuple[dict, set[str], list[str]]:
    board = await _json(await client.get(f"/api/projects/{pid}/work"))
    rows = [row for group in board["board"] for row in group["rows"]]
    tasks = {row["title"] for row in rows if row["source"] == "task"}
    loops = [row["title"] for row in rows if row["source"] == "loop"]
    return board, tasks, loops


@pytest.mark.asyncio
async def test_a_loops_phase_lists_name_their_loop_and_its_steps_show_once(tmp_path):
    async with _client(tmp_path) as client:
        pid, loop, phase, own = await _code_loop_with_a_step(client)
        scoped = (await _json(await client.get(f"/api/task-lists?project_id={pid}")))["task_lists"]
        everything = (await _json(await client.get("/api/task-lists")))["task_lists"]
        board, tasks, loops = await _board(client, pid)

    by_id = {tl["id"]: tl for tl in scoped}
    assert by_id[phase["id"]]["loop_id"] == loop.id
    assert by_id[own["task_list_id"]]["loop_id"] == ""
    assert {tl["id"]: tl["loop_id"] for tl in everything}[phase["id"]] == loop.id

    assert board["completeness"] == "complete", board["sections"]
    assert "Order the soil" in tasks
    assert "Mock the moisture probe" not in tasks, "a loop's step was listed beside its loop"
    assert loops == ["Irrigation tests"]


@pytest.mark.asyncio
async def test_the_goal_loops_shared_sub_goals_list_is_no_loops_own(tmp_path):
    from personalclaw.loop import store as loop_store
    from personalclaw.loop import tasks_link
    from personalclaw.loop.loop import Loop

    async with _client(tmp_path) as client:
        pid = (await _json(await client.post("/api/projects", json={"name": "Garden"})))["id"]
        beds, seeds = (
            loop_store.create(
                Loop(
                    id="",
                    kind="goal",
                    name=name,
                    task=name.lower(),
                    project_id=pid,
                    kind_config={"sub_goals": [sub_goal]},
                )
            )
            for name, sub_goal in (
                ("Plan the beds", "Measure the beds"),
                ("Order seeds", "Pick a vendor"),
            )
        )
        assert await tasks_link.decompose_sub_goals(beds.id)
        assert await tasks_link.decompose_sub_goals(seeds.id)
        shared = loop_store.get(beds.id).task_list_ids["sub_goals"]
        assert loop_store.get(seeds.id).task_list_ids["sub_goals"] == shared, "not one shared list"
        await _json(
            await client.post("/api/tasks", json={"title": "Buy stakes", "task_list_id": shared})
        )
        scoped = (await _json(await client.get(f"/api/task-lists?project_id={pid}")))["task_lists"]
        _, tasks, loops = await _board(client, pid)

    assert {tl["id"]: tl["loop_id"] for tl in scoped}[shared] == "", "one loop named a shared list"
    assert "Buy stakes" in tasks, "the owner's own task in the shared list left the board"
    assert not tasks & {
        "Measure the beds",
        "Pick a vendor",
    }, "a sub-goal was listed beside its loop"
    assert sorted(loops) == ["Order seeds", "Plan the beds"]


@pytest.mark.asyncio
async def test_a_moved_phase_list_keeps_its_loop_and_its_tasks_show_where_it_went(tmp_path):
    async with _client(tmp_path) as client:
        pid, loop, phase, _ = await _code_loop_with_a_step(client)
        shed = (await _json(await client.post("/api/projects", json={"name": "Shed"})))["id"]
        await _json(await client.put(f"/api/task-lists/{phase['id']}", json={"project_id": shed}))
        moved = (await _json(await client.get(f"/api/task-lists?project_id={shed}")))["task_lists"]
        _, shed_tasks, shed_loops = await _board(client, shed)
        _, garden_tasks, garden_loops = await _board(client, pid)

    assert {tl["id"]: tl["loop_id"] for tl in moved}[phase["id"]] == loop.id
    assert shed_loops == [] and "Mock the moisture probe" in shed_tasks, "no row stands for it"
    assert garden_loops == ["Irrigation tests"] and "Mock the moisture probe" not in garden_tasks


@pytest.mark.asyncio
async def test_unreadable_loops_leave_the_steps_listed_and_both_reads_answering(
    tmp_path, monkeypatch
):
    from personalclaw.loop import store as loop_store

    def unreadable(*_args, **_kwargs):
        raise OSError("loops.db is unreadable")

    async with _client(tmp_path) as client:
        pid, _, phase, _ = await _code_loop_with_a_step(client)
        # The loops section fails, so no loop row stands for the step: it stays listed.
        monkeypatch.setattr(loop_store, "list_for_project", unreadable)
        board, tasks, loops = await _board(client, pid)
        # No loop can be read at all: the task lists still answer, naming no loop.
        monkeypatch.setattr(loop_store, "list_all", unreadable)
        scoped = (await _json(await client.get(f"/api/task-lists?project_id={pid}")))["task_lists"]

    assert board["completeness"] == "partial", board["sections"]
    assert loops == [] and {"Mock the moisture probe", "Order the soil"} <= tasks
    assert scoped and all(tl["loop_id"] == "" for tl in scoped)

"""HTTP-level: the count badge finally has an emitter, and a bad name is a 400 (#514, #456).

Part 1 -- the dead badge. `TaskListRow` has read `task_count ?? count` since the initial public
commit while NO endpoint ever emitted either key, so `typeof count === 'number'` was always false
and the badge was silently absent. Measured on `origin/main`::

    GET /api/task-lists?project_id=... -> each list carries exactly
      ['agent_instructions_template', 'created_at', 'id', 'name', 'project_id', 'updated_at']

The sibling PROJECT row does the same thing correctly with `task_list_count`, which really is
emitted -- so the pattern was proven and the task-list endpoint just never adopted it.

Part 2 -- the store now raises `ValueError` for a wrong-typed or over-long name, and these routes
already map that to a 400. Asserted at the HTTP boundary because that mapping is the actual fix
users see: before, a non-string name was an unhandled 500 on POST and a silent coercion on PUT.
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
    registry._providers.clear()
    with (
        patch("personalclaw.tasks.native.config_dir", return_value=tmp_path),
        patch("personalclaw.tasks.hierarchy.config_dir", return_value=tmp_path),
    ):
        # The create door refuses a wrong type through `request_validation.require_string`,
        # which raises `RequestValidationError` — a formed 400 that only the request boundary
        # serves. Mounting the routes bare turns that refusal into a 500, so the middleware is
        # part of the contract under test, exactly as `test_projects_work_route` mounts it.
        from personalclaw.dashboard.request_boundary import request_boundary_middleware

        app = web.Application(middlewares=[request_boundary_middleware()])
        register_task_routes(app)
        async with TestClient(TestServer(app)) as client:
            yield client
    registry._providers.clear()


async def _lists(client, project_id):
    r = await client.get(f"/api/task-lists?project_id={project_id}")
    assert r.status == 200
    return (await r.json())["task_lists"]


# ── part 1: the count is emitted, and is right ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_task_count_is_emitted_and_counts_only_its_own_list(tmp_path):
    async with _client(tmp_path) as client:
        pid = (await (await client.post("/api/projects", json={"name": "Hub"})).json())["id"]
        a = (
            await (
                await client.post("/api/task-lists", json={"name": "A", "project_id": pid})
            ).json()
        )["id"]
        b = (
            await (
                await client.post("/api/task-lists", json={"name": "B", "project_id": pid})
            ).json()
        )["id"]
        for title in ("t1", "t2"):
            r = await client.post("/api/tasks", json={"title": title, "task_list_id": a})
            assert r.status in (200, 201), await r.text()
        await client.post("/api/tasks", json={"title": "solo", "task_list_id": b})

        by_id = {tl["id"]: tl for tl in await _lists(client, pid)}
        # 🔑 The field the hub has always read, now actually present.
        assert by_id[a]["task_count"] == 2, by_id[a]
        assert by_id[b]["task_count"] == 1, by_id[b]


@pytest.mark.asyncio
async def test_an_empty_list_reports_zero_rather_than_omitting(tmp_path):
    # 0 is a real reading here — the count IS known. Absence means something different (below).
    async with _client(tmp_path) as client:
        pid = (await (await client.post("/api/projects", json={"name": "Hub"})).json())["id"]
        await client.post("/api/task-lists", json={"name": "Empty", "project_id": pid})
        assert (await _lists(client, pid))[0]["task_count"] == 0


@pytest.mark.asyncio
async def test_the_field_is_OMITTED_when_the_count_cannot_be_computed(tmp_path):
    """A provider failure must not fabricate `0`, and must not fail the request.

    The hub renders the badge only for a number, so omitting hides it — while a 0 would assert
    "this list is empty", which is a wrong answer dressed as a real one.
    """
    async with _client(tmp_path) as client:
        pid = (await (await client.post("/api/projects", json={"name": "Hub"})).json())["id"]
        await client.post("/api/task-lists", json={"name": "A", "project_id": pid})
        with patch.object(registry, "list_all_tasks", side_effect=RuntimeError("provider down")):
            lists = await _lists(client, pid)
        assert lists, "the lists themselves must still be served"
        assert "task_count" not in lists[0], lists[0]


# ── part 2: a bad name is a 400 at the boundary, on both verbs and both nouns ───────────────


@pytest.mark.parametrize("bad", [123, None, {"a": 1}, ["a"], True])
@pytest.mark.asyncio
async def test_project_create_and_update_reject_a_non_string_name_with_400(tmp_path, bad):
    async with _client(tmp_path) as client:
        # POST used to be an unhandled 500 (AttributeError: 'int' object has no attribute 'strip').
        r = await client.post("/api/projects", json={"name": bad})
        assert r.status == 400, f"POST {bad!r} -> {r.status}"

        pid = (await (await client.post("/api/projects", json={"name": "Real"})).json())["id"]
        # PUT used to answer 200 and persist str(bad) — 'None', "{'a': 1}".
        r = await client.put(f"/api/projects/{pid}", json={"name": bad})
        assert r.status == 400, f"PUT {bad!r} -> {r.status}"
        assert (await (await client.get(f"/api/projects/{pid}")).json())["name"] == "Real"


@pytest.mark.parametrize("bad", [123, None, {"a": 1}])
@pytest.mark.asyncio
async def test_task_list_create_and_update_reject_a_non_string_name_with_400(tmp_path, bad):
    async with _client(tmp_path) as client:
        pid = (await (await client.post("/api/projects", json={"name": "Hub"})).json())["id"]
        r = await client.post("/api/task-lists", json={"name": bad, "project_id": pid})
        assert r.status == 400, f"POST {bad!r} -> {r.status}"

        lid = (
            await (
                await client.post("/api/task-lists", json={"name": "Real", "project_id": pid})
            ).json()
        )["id"]
        r = await client.put(f"/api/task-lists/{lid}", json={"name": bad})
        assert r.status == 400, f"PUT {bad!r} -> {r.status}"


@pytest.mark.asyncio
async def test_an_over_long_name_is_a_400_on_both_verbs(tmp_path):
    long = "K" * 3000
    async with _client(tmp_path) as client:
        r = await client.post("/api/projects", json={"name": long})
        assert r.status == 400
        assert "200" in (await r.json())["error"], "the message must name the limit"

        pid = (await (await client.post("/api/projects", json={"name": "Real"})).json())["id"]
        assert (await client.put(f"/api/projects/{pid}", json={"name": long})).status == 400
        assert (await (await client.get(f"/api/projects/{pid}")).json())["name"] == "Real"


@pytest.mark.asyncio
async def test_a_normal_project_still_creates_and_renames(tmp_path):
    # Vacuity guard: the ordinary path must be untouched by all of the above.
    async with _client(tmp_path) as client:
        r = await client.post("/api/projects", json={"name": "PhD Applications"})
        assert r.status == 201
        pid = (await r.json())["id"]
        r = await client.put(f"/api/projects/{pid}", json={"name": "PhD Applications 2026"})
        assert r.status == 200
        assert (await r.json())["name"] == "PhD Applications 2026"

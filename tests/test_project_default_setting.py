"""The default project is the user's preference, so it is stored where the user is.

Measured across two browsers on one gateway: a project made the default in the first read
"Make default" in the second, and a new task there started in Personal — the pointer was a
``localStorage`` key while the project page called it "Default project". It now lives in
``entity_settings/projects.json`` behind ``GET/PUT /api/projects/settings``, the entity-settings
home AGENTS.md's config round-trip contract names for per-entity preferences.

What these pin:

* the route round-trips the value through the entity-settings file;
* ``""`` clears it and an empty body does not (a client that sends nothing must not wipe it);
* it can only name a project new work can start in — not an unknown id, not an archived one;
* the READ resolves against the live store, so a delete, an archive or a hand-edited file can
  never make it name a project new work cannot start in;
* an unreadable file fails OPEN to "no default" (its only effect is a pre-selection);
* ``/api/projects/settings`` is not captured by the ``/api/projects/{project_id}`` matcher.
"""

from __future__ import annotations

import json
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
        patch("personalclaw.providers.entity_routes.config_dir", return_value=tmp_path),
    ):
        from personalclaw.dashboard.request_boundary import request_boundary_middleware

        app = web.Application(middlewares=[request_boundary_middleware()])
        register_task_routes(app)
        async with TestClient(TestServer(app)) as client:
            yield client
    registry._providers.clear()


async def _project(client, name: str) -> str:
    r = await client.post("/api/projects", json={"name": name})
    assert r.status == 201, await r.text()
    return (await r.json())["id"]


async def _default(client) -> str:
    r = await client.get("/api/projects/settings")
    assert r.status == 200, await r.text()
    return (await r.json())["default_project_id"]


def _stored(tmp_path) -> dict:
    return json.loads((tmp_path / "entity_settings" / "projects.json").read_text())


@pytest.mark.asyncio
async def test_a_fresh_home_has_no_default(tmp_path):
    async with _client(tmp_path) as client:
        assert await _default(client) == ""


@pytest.mark.asyncio
async def test_the_default_round_trips_through_the_entity_settings_file(tmp_path):
    async with _client(tmp_path) as client:
        pid = await _project(client, "Q4 Launch Plan")
        r = await client.put("/api/projects/settings", json={"default_project_id": pid})
        assert r.status == 200
        assert (await r.json())["default_project_id"] == pid
        assert await _default(client) == pid
    assert _stored(tmp_path)["default_project_id"] == pid


@pytest.mark.asyncio
async def test_blank_clears_it_but_an_empty_body_does_not(tmp_path):
    async with _client(tmp_path) as client:
        pid = await _project(client, "Q4 Launch Plan")
        await client.put("/api/projects/settings", json={"default_project_id": pid})

        r = await client.put("/api/projects/settings", json={})
        assert r.status == 400
        assert (await r.json())["error"]["code"] == "field_required"
        assert await _default(client) == pid, "a body naming nothing must not wipe the choice"

        r = await client.put("/api/projects/settings", json={"default_project_id": ""})
        assert r.status == 200
        assert await _default(client) == ""


@pytest.mark.asyncio
async def test_it_refuses_a_project_new_work_cannot_start_in(tmp_path):
    async with _client(tmp_path) as client:
        r = await client.put("/api/projects/settings", json={"default_project_id": "p-nope"})
        assert r.status == 400
        assert (await r.json())["error"]["code"] == "project_not_found"

        r = await client.put("/api/projects/settings", json={"default_project_id": 7})
        assert r.status == 400
        assert (await r.json())["error"]["code"] == "field_not_a_string"

        pid = await _project(client, "Old")
        await client.put(f"/api/projects/{pid}", json={"status": "archived"})
        r = await client.put("/api/projects/settings", json={"default_project_id": pid})
        assert r.status == 409
        assert (await r.json())["error"]["code"] == "project_archived"
        assert await _default(client) == ""


@pytest.mark.asyncio
async def test_the_read_follows_the_project_through_archive_restore_and_delete(tmp_path):
    async with _client(tmp_path) as client:
        pid = await _project(client, "Q4 Launch Plan")
        await client.put("/api/projects/settings", json={"default_project_id": pid})

        await client.put(f"/api/projects/{pid}", json={"status": "archived"})
        assert await _default(client) == "", "an archived project is off every picker"
        await client.put(f"/api/projects/{pid}", json={"status": "active"})
        assert await _default(client) == pid, "restoring it restores the user's last choice"

        r = await client.delete(f"/api/projects/{pid}")
        assert r.status == 200, await r.text()
        assert await _default(client) == ""


@pytest.mark.asyncio
async def test_an_unreadable_file_reads_as_no_default_and_a_write_repairs_it(tmp_path):
    (tmp_path / "entity_settings").mkdir(parents=True)
    (tmp_path / "entity_settings" / "projects.json").write_text("{not json")
    async with _client(tmp_path) as client:
        assert await _default(client) == ""
        pid = await _project(client, "Q4 Launch Plan")
        r = await client.put("/api/projects/settings", json={"default_project_id": pid})
        assert r.status == 200
        assert await _default(client) == pid


@pytest.mark.asyncio
async def test_a_hand_edited_id_that_is_not_one_path_segment_reads_as_no_default(tmp_path):
    (tmp_path / "entity_settings").mkdir(parents=True)
    (tmp_path / "entity_settings" / "projects.json").write_text(
        json.dumps({"default_project_id": "../../etc"})
    )
    async with _client(tmp_path) as client:
        assert await _default(client) == ""


@pytest.mark.asyncio
async def test_the_settings_path_is_not_read_as_a_project_id(tmp_path):
    async with _client(tmp_path) as client:
        r = await client.get("/api/projects/settings")
        assert r.status == 200
        assert set(await r.json()) == {"default_project_id"}

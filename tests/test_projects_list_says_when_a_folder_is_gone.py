"""The Projects list says when a project's folder is gone, from one field on the list read.

Each ``GET /api/projects`` row carries ``folder_state``, judged by the rule the folder picker
(``GET /api/browse-dirs``) answers a path with: a path that is not there, or is a file, is
``missing`` (the picker's 404); one the picker will not open, a protected location or one it
cannot reach, is ``unreadable`` (its 403 and 400); a folder it would open is ``ok``; a project
bound to no folder reads ``""``. One field on the read, so the list draws its "Folder missing"
chip without a probe per row.

An app's read of the list carries no ``folder_state``: whether a folder is there is the owner's
to read, as a project's ``folder`` is.
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

# It reads an app's view of the list (`apps.permissions`), so it carries the security mark.
pytestmark = pytest.mark.security


@asynccontextmanager
async def _client(tmp_path, app: str = ""):
    """The task routes over ``tmp_path/home``, read by the owner, or by *app* when it is named."""
    registry._providers.clear()
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    with (
        patch("personalclaw.tasks.native.config_dir", return_value=home),
        patch("personalclaw.tasks.hierarchy.config_dir", return_value=home),
        patch("personalclaw.providers.entity_routes.config_dir", return_value=home),
    ):
        from personalclaw.apps.permissions import scoped_to_app
        from personalclaw.dashboard.request_boundary import request_boundary_middleware

        @web.middleware
        async def caller(request: web.Request, handler):
            # What the gateway's app-permission middleware does around an app's request.
            if app:
                request["app"] = app
            with scoped_to_app(app):
                return await handler(request)

        gw = web.Application(middlewares=[caller, request_boundary_middleware()])
        register_task_routes(gw)
        async with TestClient(TestServer(gw)) as client:
            yield client
    registry._providers.clear()


async def _project(client, name: str, folder=None) -> str:
    body = {"name": name, **({"workspace_dir": str(folder)} if folder else {})}
    r = await client.post("/api/projects", json=body)
    assert r.status == 201, await r.text()
    return (await r.json())["id"]


async def _states(client) -> dict[str, str]:
    r = await client.get("/api/projects")
    assert r.status == 200, await r.text()
    return {row["name"]: row.get("folder_state") for row in (await r.json())["projects"]}


def _rebind(tmp_path, pid: str, folder: str) -> None:
    """Point a project's record at *folder* as a hand edit, or a folder that became protected
    after it was bound, would: the store refuses such a folder at bind time."""
    path = tmp_path / "home" / "projects" / pid / "project.json"
    record = json.loads(path.read_text())
    record["workspace_dir"] = folder
    path.write_text(json.dumps(record))


@pytest.mark.asyncio
async def test_each_row_says_whether_its_folder_is_there(tmp_path):
    present = tmp_path / "garden"
    present.mkdir()
    gone = tmp_path / "taxes"
    gone.mkdir()
    a_file = tmp_path / "trip"
    a_file.mkdir()
    async with _client(tmp_path) as client:
        await _project(client, "Garden", present)
        await _project(client, "Taxes", gone)
        await _project(client, "Trip", a_file)
        protected = await _project(client, "Root", present)
        await _project(client, "Notes")
        gone.rmdir()
        a_file.rmdir()
        a_file.write_text("not a folder")
        _rebind(tmp_path, protected, "/")
        states = await _states(client)
        assert states["Garden"] == "ok"
        assert states["Taxes"] == "missing", "a deleted folder"
        assert states["Trip"] == "missing", "a file where the folder was"
        assert states["Root"] == "unreadable", "a location the picker will not open"
        assert states["Notes"] == "", "no folder bound"


@pytest.mark.asyncio
async def test_a_folder_reached_through_a_link_is_judged_where_it_lands(tmp_path):
    real = tmp_path / "garden"
    real.mkdir()
    link = tmp_path / "garden-link"
    link.symlink_to(real)
    async with _client(tmp_path) as client:
        await _project(client, "Garden", link)
        assert (await _states(client))["Garden"] == "ok"
        real.rmdir()
        assert (await _states(client))["Garden"] == "missing"


@pytest.mark.asyncio
async def test_an_apps_read_of_the_list_carries_no_folder_state(tmp_path):
    present = tmp_path / "garden"
    present.mkdir()
    async with _client(tmp_path) as client:
        await _project(client, "Garden", present)
        assert (await _states(client))["Garden"] == "ok"
    async with _client(tmp_path, app="minutes") as client:
        r = await client.get("/api/projects")
        assert r.status == 200, await r.text()
        (garden,) = [row for row in (await r.json())["projects"] if row["name"] == "Garden"]
    assert "folder_state" not in garden


@pytest.mark.asyncio
async def test_a_folder_path_the_os_cannot_take_reads_unreadable_and_the_list_still_answers(
    tmp_path,
):
    present = tmp_path / "garden"
    present.mkdir()
    async with _client(tmp_path) as client:
        await _project(client, "Garden", present)
        broken = await _project(client, "Broken", present)
        _rebind(tmp_path, broken, str(present) + "\x00x")
        states = await _states(client)
        assert states["Broken"] == "unreadable"
        assert states["Garden"] == "ok", "one bad row leaves the rest of the list answering"

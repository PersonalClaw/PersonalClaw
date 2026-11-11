"""A project's read says whether its folder is trusted, judged by the folder's real path.

What automations may do in a project's folder is the trust decision recorded for it
(`guardrails.project_trust`), keyed by the folder's resolved path, so `~/code/garden`, a symlink to
it and its real path share one decision. A page can only match the decisions list by string, which
misses all but the real path, so it would show a trusted folder as undecided. `GET
/api/projects/{id}` (and the `PUT` that answers with the same project) now carries
`folder: {resolved, trust}` for the bound folder, resolved and decided on the gateway by the same
two functions the guardrail uses (`resolve_dir`, `project_decision`). A project with no folder
carries `folder: null`, a decision that cannot be read reads `unknown`, and an app's own read
carries no `folder`: the decision is the owner's to read, as the decisions route is.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from unittest.mock import patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.guardrails import project_trust
from personalclaw.tasks import registry
from personalclaw.tasks.handlers import register_task_routes

pytestmark = pytest.mark.security


@asynccontextmanager
async def _client(home):
    registry._providers.clear()
    with (
        patch("personalclaw.tasks.native.config_dir", return_value=home),
        patch("personalclaw.tasks.hierarchy.config_dir", return_value=home),
        patch("personalclaw.config.loader.config_dir", return_value=home),
    ):
        from personalclaw.dashboard.request_boundary import request_boundary_middleware

        app = web.Application(middlewares=[request_boundary_middleware()])
        register_task_routes(app)
        async with TestClient(TestServer(app)) as client:
            yield client
    registry._providers.clear()


async def _project(client, name: str, folder: str = "") -> str:
    r = await client.post("/api/projects", json={"name": name})
    assert r.status in (200, 201), await r.text()
    pid = (await r.json())["id"]
    if folder:
        r = await client.put(f"/api/projects/{pid}", json={"workspace_dir": folder})
        assert r.status == 200, await r.text()
    return pid


async def _folder(client, pid: str):
    r = await client.get(f"/api/projects/{pid}")
    assert r.status == 200, await r.text()
    return (await r.json())["folder"]


@pytest.mark.asyncio
async def test_every_spelling_of_a_folder_reads_its_one_decision(tmp_path, monkeypatch):
    user = tmp_path / "user"
    garden = user / "code" / "garden"
    garden.mkdir(parents=True)
    link = user / "garden-link"
    link.symlink_to(garden)
    shed = user / "code" / "shed"
    shed.mkdir()
    fresh = user / "code" / "fresh"
    fresh.mkdir()
    monkeypatch.setenv("HOME", str(user))
    real_garden = os.path.realpath(garden)

    async with _client(tmp_path / "home") as client:
        project_trust.record_project_trust(real_garden, True)
        project_trust.record_project_trust(str(shed), False)
        by_tilde = await _project(client, "Garden", "~/code/garden")
        by_link = await _project(client, "Garden link", str(link))
        preview = await _project(client, "Shed", str(shed))
        undecided = await _project(client, "Fresh", str(fresh))
        no_folder = await _project(client, "Notes")

        assert await _folder(client, by_tilde) == {"resolved": real_garden, "trust": "trusted"}
        assert await _folder(client, by_link) == {"resolved": real_garden, "trust": "trusted"}
        assert await _folder(client, preview) == {
            "resolved": os.path.realpath(shed),
            "trust": "preview",
        }
        assert await _folder(client, undecided) == {
            "resolved": os.path.realpath(fresh),
            "trust": "unknown",
        }
        assert await _folder(client, no_folder) is None

        # The PUT answers with the same project, so a page that keeps its answer stays right.
        r = await client.put(f"/api/projects/{undecided}", json={"workspace_dir": str(link)})
        assert (await r.json())["folder"] == {"resolved": real_garden, "trust": "trusted"}


@pytest.mark.asyncio
async def test_the_list_does_not_read_the_trust_store_per_row(tmp_path, monkeypatch):
    """The decision is a detail of one project's read; the list stays one pass over projects."""
    garden = tmp_path / "code" / "garden"
    garden.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(tmp_path))
    reads: list[int] = []
    read_store = project_trust._read_store
    monkeypatch.setattr(project_trust, "_read_store", lambda: reads.append(1) or read_store())
    async with _client(tmp_path / "home") as client:
        await _project(client, "Garden", str(garden))
        await _project(client, "Notes")
        reads.clear()
        r = await client.get("/api/projects")
        rows = (await r.json())["projects"]
    assert len(rows) >= 2 and all("folder" not in row for row in rows)
    assert reads == [], "the list read the trust store"


@pytest.mark.asyncio
async def test_an_apps_read_carries_no_folder_and_an_unreadable_decision_reads_unknown(
    tmp_path, monkeypatch
):
    from personalclaw.apps.permissions import scoped_to_app
    from personalclaw.tasks.hierarchy import HierarchyStore
    from personalclaw.tasks.hierarchy_handlers import _project_folder

    garden = tmp_path / "code" / "garden"
    garden.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(tmp_path))
    async with _client(tmp_path / "home") as client:
        pid = await _project(client, "Garden", str(garden))
        store = HierarchyStore()
        project = store.get_project(pid)
        assert _project_folder(project)["folder"]["trust"] == "unknown"
        with scoped_to_app("an-app"):
            assert _project_folder(project) == {}

        def unreadable(cwd: str) -> str:
            raise PermissionError("project_trust.json")

        monkeypatch.setattr(project_trust, "project_decision", unreadable)
        folder = await _folder(client, pid)
        assert folder["trust"] == "unknown" and folder["resolved"], folder

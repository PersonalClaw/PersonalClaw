"""A file saved as an artifact from a project's own directory belongs to that project.

Measured on a project with a bound workspace: Files → Save as artifact on
``<workspace>/launch-brief.md`` created an artifact the project's own page never listed. The agent's
``artifact_save`` stamps the turn's bound project; the dashboard's three manual file saves (Files,
a chat's file panel, the Code cockpit) send no ``project_id`` at all, because none of them knows
about projects. The create route now resolves one from where the file lives, and only when the
caller names none — an explicit ``project_id``, including ``""``, is obeyed as before.

``HierarchyStore.project_for_path`` is the rule; the route tests pin that it reaches the wire and
the project's ``/linked`` inventory.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.artifacts import registry
from personalclaw.artifacts.handlers import register_artifact_routes
from personalclaw.artifacts.native import NativeArtifactProvider
from personalclaw.tasks.hierarchy import HierarchyStore


@pytest.fixture
def home(tmp_path):
    with patch("personalclaw.tasks.hierarchy.config_dir", return_value=tmp_path / "home"):
        yield tmp_path


@pytest.fixture
def store(home):
    return HierarchyStore()


def _workspace(home, *parts: str):
    path = home.joinpath("work", *parts)
    path.mkdir(parents=True, exist_ok=True)
    return path


class TestProjectForPath:
    def test_a_file_in_a_bound_workspace_belongs_to_that_project(self, store, home):
        ws = _workspace(home, "q4-launch")
        project = store.create_project("Q4 Launch Plan", workspace_dir=str(ws))
        assert store.project_for_path(str(ws / "launch-brief.md")).id == project.id

    def test_the_workspace_directory_itself_resolves(self, store, home):
        ws = _workspace(home, "q4-launch")
        project = store.create_project("Q4 Launch Plan", workspace_dir=str(ws))
        assert store.project_for_path(str(ws)).id == project.id

    def test_a_file_in_a_projects_context_directory_belongs_to_it(self, store):
        project = store.create_project("Q4 Launch Plan")
        context = store.context_dir(project.id)
        assert store.project_for_path(str(context / "notes.md")).id == project.id

    def test_the_most_specific_workspace_wins(self, store, home):
        outer = store.create_project("Monorepo", workspace_dir=str(_workspace(home, "repo")))
        inner = store.create_project(
            "Docs site", workspace_dir=str(_workspace(home, "repo", "docs"))
        )
        assert store.project_for_path(str(home / "work" / "repo" / "docs" / "a.md")).id == inner.id
        assert store.project_for_path(str(home / "work" / "repo" / "src" / "b.py")).id == outer.id

    def test_two_projects_claiming_one_folder_is_ambiguous_and_resolves_to_none(self, store, home):
        ws = _workspace(home, "shared")
        store.create_project("One", workspace_dir=str(ws))
        store.create_project("Two", workspace_dir=str(ws))
        assert store.project_for_path(str(ws / "file.md")) is None

    def test_an_archived_project_is_not_chosen_for_new_work(self, store, home):
        ws = _workspace(home, "old")
        project = store.create_project("Old", workspace_dir=str(ws))
        store.update_project(project.id, status="archived")
        assert store.project_for_path(str(ws / "file.md")) is None

    def test_a_path_outside_every_project_resolves_to_none(self, store, home):
        store.create_project("Q4", workspace_dir=str(_workspace(home, "q4")))
        assert store.project_for_path(str(home / "elsewhere" / "file.md")) is None
        assert store.project_for_path("") is None

    def test_a_sibling_whose_name_merely_starts_with_the_workspace_does_not_match(
        self, store, home
    ):
        # A string-prefix test would call `/work/q4-launch-archive` part of `/work/q4-launch`.
        store.create_project("Q4", workspace_dir=str(_workspace(home, "q4-launch")))
        sibling = _workspace(home, "q4-launch-archive")
        assert store.project_for_path(str(sibling / "file.md")) is None


def _app() -> web.Application:
    app = web.Application()
    state = MagicMock()
    state.is_restricted_session.return_value = False
    app["state"] = state
    register_artifact_routes(app)
    return app


@pytest.fixture
def artifacts(home, monkeypatch):
    provider = NativeArtifactProvider(root=home / "artifacts")
    monkeypatch.setitem(registry._providers, "native", provider)
    return provider


class TestCreateRouteLinksTheSavedFileToItsProject:
    @pytest.mark.asyncio
    async def test_a_save_from_the_workspace_is_stamped_with_its_project(
        self, store, home, artifacts
    ):
        ws = _workspace(home, "q4-launch")
        project = store.create_project("Q4 Launch Plan", workspace_dir=str(ws))
        # Exactly the body Files → Save as artifact sends: no project_id.
        body = {
            "name": "Q4 Launch Brief",
            "content": "# Q4",
            "source": "manual",
            "source_path": str(ws / "launch-brief.md"),
            "kind": "markdown",
        }
        async with TestClient(TestServer(_app())) as client:
            r = await client.post("/api/artifacts", json=body)
            assert r.status == 201, await r.text()
            created = await r.json()
        assert created["project_id"] == project.id
        assert artifacts.get(created["slug"]).project_id == project.id

    @pytest.mark.asyncio
    async def test_an_explicit_project_id_is_obeyed_even_when_it_is_blank(
        self, store, home, artifacts
    ):
        ws = _workspace(home, "q4-launch")
        store.create_project("Q4 Launch Plan", workspace_dir=str(ws))
        other = store.create_project("Other")
        async with TestClient(TestServer(_app())) as client:
            named = await (
                await client.post(
                    "/api/artifacts",
                    json={
                        "name": "A",
                        "content": "a",
                        "kind": "markdown",
                        "source_path": str(ws / "a.md"),
                        "project_id": other.id,
                    },
                )
            ).json()
            unscoped = await (
                await client.post(
                    "/api/artifacts",
                    json={
                        "name": "B",
                        "content": "b",
                        "kind": "markdown",
                        "source_path": str(ws / "b.md"),
                        "project_id": "",
                    },
                )
            ).json()
        assert named["project_id"] == other.id
        assert unscoped["project_id"] == ""

    @pytest.mark.asyncio
    async def test_a_save_from_outside_every_project_stays_unscoped(self, store, home, artifacts):
        store.create_project("Q4 Launch Plan", workspace_dir=str(_workspace(home, "q4")))
        async with TestClient(TestServer(_app())) as client:
            created = await (
                await client.post(
                    "/api/artifacts",
                    json={
                        "name": "Loose",
                        "content": "x",
                        "kind": "markdown",
                        "source_path": str(home / "downloads" / "loose.md"),
                    },
                )
            ).json()
        # Never a default project: a save the path cannot place stays where it always was.
        assert created["project_id"] == ""

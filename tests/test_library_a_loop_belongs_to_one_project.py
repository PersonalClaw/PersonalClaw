"""A loop is in one project, and so is what it made.

A loop holds two project keys: its own ``project_id`` and the ``tasks_project_id`` a project-less
launch is given. Either one used to place it, so a loop holding both was listed under two projects,
and so was every artifact its ``loop:<id>`` tag files. ``loop_project_id`` gives the one answer the
library and ``GET /api/projects/{id}/linked`` both read: the loop's own project when that exists,
else its backing project when that does, else none. An artifact's own ``project_id`` wins over its
loop's tag.
"""

from __future__ import annotations

import pytest
from library_shared import artifacts, client, isolate, loop, project


@pytest.fixture
def home(tmp_path, monkeypatch):
    return isolate(tmp_path, monkeypatch)


def test_a_loops_project_is_its_own_then_its_backing_one_then_none(home):
    from personalclaw.loop import store as loop_store

    garden, homelab = project("Garden"), project("Homelab")
    exists = {garden, homelab}.__contains__

    def one(**keys: str) -> str:
        return loop_store.loop_project_id(loop_store.get(loop("l", **keys)), exists)

    assert one(project_id=garden, tasks_project_id=homelab) == garden
    assert one(project_id="p-gone", tasks_project_id=homelab) == homelab
    assert one(tasks_project_id=homelab) == homelab
    assert one(project_id="p-gone", tasks_project_id="p-also-gone") == ""
    assert one() == ""


@pytest.mark.asyncio
async def test_linked_lists_a_loop_under_its_one_project_only(home):
    garden, homelab = project("Garden"), project("Homelab")
    both = loop("Prune the roses", project_id=garden, tasks_project_id=homelab)
    async with client(home) as c:
        in_garden = await (await c.get(f"/api/projects/{garden}/linked")).json()
        in_homelab = await (await c.get(f"/api/projects/{homelab}/linked")).json()
    assert [r["id"] for r in in_garden["loops"]] == [both]
    assert [r["id"] for r in in_homelab["loops"]] == []


@pytest.mark.asyncio
async def test_an_artifacts_own_project_wins_over_its_loops_tag(home):
    garden, homelab = project("Garden"), project("Homelab")
    nas = loop("Back up the NAS", project_id=homelab)
    prov = artifacts()
    filed = prov.create(
        name="Seed list",
        content="# Seeds",
        kind="markdown",
        project_id=garden,
        tags=[f"loop:{nas}"],
    )
    deliverable = prov.create(
        name="Backup plan", content="# Plan", kind="markdown", tags=[f"loop:{nas}"]
    )
    async with client(home) as c:
        in_garden = await (await c.get(f"/api/projects/{garden}/linked")).json()
        in_homelab = await (await c.get(f"/api/projects/{homelab}/linked")).json()
        library = await (await c.get("/api/library")).json()
    assert [a["slug"] for a in in_garden["artifacts"]] == [filed.slug]
    assert [a["slug"] for a in in_homelab["artifacts"]] == [deliverable.slug]
    rows = {r["ref"]: r for r in library["items"]}
    assert rows[f"artifact:{filed.slug}"]["project"] == {"id": garden, "name": "Garden"}
    assert rows[f"artifact:{deliverable.slug}"]["project"] == {"id": homelab, "name": "Homelab"}
    assert library["facets"]["project"] == {garden: 1, homelab: 1}

"""``PATCH /api/artifacts/{slug}`` moves an artifact into a project and out of one, and pins or
archives it in the library, without moving its place in Recent activity.

``project_id`` files it under a project, and ``""`` takes it out. A loop's deliverable carries an
empty ``project_id`` and a ``loop:<id>`` tag, which files it under its loop's project, so ``""``
alone would change nothing: the PATCH stores ``project_cleared``, and the tag no longer files it
until a later ``project_id`` does. ``pinned`` and ``archived`` are kept and read back. Each is
written as a folder is filed, with no ``updated_at`` bump, and the agent's own artifact list is
unchanged.
"""

from __future__ import annotations

import pytest
from library_shared import artifacts, client, isolate, loop, project


@pytest.fixture
def home(tmp_path, monkeypatch):
    return isolate(tmp_path, monkeypatch)


async def _rows(c, query: str = "") -> dict:
    return {r["ref"]: r for r in (await (await c.get(f"/api/library{query}")).json())["items"]}


@pytest.mark.asyncio
async def test_a_patch_files_an_artifact_and_takes_it_out(home):
    garden = project("Garden")
    page = artifacts().create(name="Seed list", content="# Seeds", kind="markdown")
    ref = f"artifact:{page.slug}"
    async with client(home) as c:
        before = (await _rows(c))[ref]
        moved = await c.patch(f"/api/artifacts/{page.slug}", json={"project_id": garden})
        assert moved.status == 200, await moved.text()
        filed = (await _rows(c))[ref]
        out = await c.patch(f"/api/artifacts/{page.slug}", json={"project_id": ""})
        assert out.status == 200
        after = (await _rows(c))[ref]
        unknown = await c.patch(f"/api/artifacts/{page.slug}", json={"project_id": "p-0deadbeef"})
        refusal = await unknown.json()
    assert filed["project"] == {"id": garden, "name": "Garden"}
    assert after["project"] is None
    assert unknown.status == 404 and "unknown_project" in str(refusal), refusal
    assert (
        before["activity_at"] == filed["activity_at"] == after["activity_at"]
    ), "filing reorders nothing"


@pytest.mark.asyncio
async def test_pinned_and_archived_are_kept(home):
    page = artifacts().create(name="Lisbon trip plan", content="# Day one", kind="markdown")
    ref = f"artifact:{page.slug}"
    async with client(home) as c:
        assert (await c.patch(f"/api/artifacts/{page.slug}", json={"pinned": True})).status == 200
        pinned = (await _rows(c))[ref]
        assert (await c.patch(f"/api/artifacts/{page.slug}", json={"archived": True})).status == 200
        hidden = await _rows(c)
        archived = (await _rows(c, "?state=archived"))[ref]
        bad = await c.patch(f"/api/artifacts/{page.slug}", json={"pinned": "yes"})
        listed = await (await c.get("/api/artifacts")).json()
    assert pinned["pinned"] is True and pinned["archived"] is False
    assert ref not in hidden, "an archived thing waits for the Archived filter"
    assert archived["pinned"] is True and archived["archived"] is True
    assert bad.status == 400
    row = next(a for a in listed["artifacts"] if a["slug"] == page.slug)
    assert "pinned" not in row and "archived" not in row, "the artifact list answers as before"


@pytest.mark.asyncio
async def test_a_loops_deliverable_leaves_its_project_when_taken_out(home):
    garden, homelab = project("Garden"), project("Homelab")
    nas = loop("Back up the NAS", project_id=homelab)
    plan = artifacts().create(
        name="Backup plan", content="# Plan", kind="markdown", tags=[f"loop:{nas}"]
    )
    ref = f"artifact:{plan.slug}"

    async def where(c) -> tuple:
        rows = await _rows(c, f"?project={homelab}")
        linked = await (await c.get(f"/api/projects/{homelab}/linked")).json()
        return ref in rows, plan.slug in {a["slug"] for a in linked["artifacts"]}

    async with client(home) as c:
        assert await where(c) == (True, True), "its loop's tag files it under Homelab"
        await c.patch(f"/api/artifacts/{plan.slug}", json={"project_id": ""})
        assert await where(c) == (False, False), "taken out, whatever its tag"
        await c.patch(f"/api/artifacts/{plan.slug}", json={"project_id": garden})
        await c.patch(f"/api/artifacts/{plan.slug}", json={"project_id": ""})
        assert await where(c) == (False, False), "a move to Garden and out leaves it out"
        await c.patch(f"/api/artifacts/{plan.slug}", json={"project_id": homelab})
        assert await where(c) == (True, True)
    assert artifacts().get(plan.slug).project_cleared is False, "a later project id clears the flag"

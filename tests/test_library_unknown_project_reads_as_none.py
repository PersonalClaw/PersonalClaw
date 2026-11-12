"""A thing whose project id names no project reads as in no project.

A project can be deleted while what was filed under it is kept, and an artifact can carry an id no
project has. Such a row has ``project: null``, counts under No project, and is listed by
``project=none``: it keeps its id, and draws no project's name.
"""

from __future__ import annotations

import pytest
from library_shared import artifacts, client, filed, isolate, project


@pytest.fixture
def home(tmp_path, monkeypatch):
    return isolate(tmp_path, monkeypatch)


@pytest.mark.asyncio
async def test_an_id_naming_no_project_reads_as_none(home):
    garden = project("Garden")
    orphan = await filed("Filed under a deleted project", "p-0deadbeef")
    stray = artifacts().create(
        name="A stray page", content="# P", kind="markdown", project_id="p-0deadbeef"
    )
    kept = artifacts().create(
        name="Seed list", content="# Seeds", kind="markdown", project_id=garden
    )
    async with client(home) as c:
        body = await (await c.get("/api/library")).json()
        nowhere = await (await c.get("/api/library?project=none")).json()
    rows = {r["ref"]: r for r in body["items"]}
    assert rows[f"knowledge:{orphan}"]["project"] is None
    assert rows[f"artifact:{stray.slug}"]["project"] is None
    assert rows[f"artifact:{kept.slug}"]["project"] == {"id": garden, "name": "Garden"}
    assert body["facets"]["project"] == {"none": 2, garden: 1}
    assert {r["ref"] for r in nowhere["items"]} == {f"knowledge:{orphan}", f"artifact:{stray.slug}"}

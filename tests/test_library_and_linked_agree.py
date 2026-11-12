"""``GET /api/projects/{id}/linked`` and ``GET /api/library?project=<id>`` list the same things.

Both read one project rule (``personalclaw.project_items``): a loop's deliverable by its loop's one
project, an artifact by its own id first, and every knowledge item the project holds, its own and
other projects' shared ones, with no cap (``/linked`` used to stop at 25). ``store=`` narrows the
library's rows to one store, and the store facet's counts equal ``/linked``'s list lengths.
"""

from __future__ import annotations

import pytest
from library_shared import artifacts, client, filed, isolate, loop, project


@pytest.fixture
def home(tmp_path, monkeypatch):
    return isolate(tmp_path, monkeypatch)


@pytest.mark.asyncio
async def test_the_library_and_linked_list_the_same_things(home):
    garden, homelab = project("Garden"), project("Homelab")
    ours = [await filed(f"Garden note {i}", garden) for i in range(30)]
    shared = await filed("A homelab note Garden may read", homelab, policy="shared")
    await filed("A homelab note Garden may not read", homelab)
    roses = loop("Prune the roses", project_id=garden)
    prov = artifacts()
    filed_art = prov.create(name="Seed list", content="# Seeds", kind="markdown", project_id=garden)
    deliverable = prov.create(
        name="Pruning plan", content="# Plan", kind="markdown", tags=[f"loop:{roses}"]
    )
    prov.create(name="NAS plan", content="# NAS", kind="markdown", project_id=homelab)
    async with client(home) as c:
        linked = await (await c.get(f"/api/projects/{garden}/linked")).json()
        library = await (await c.get(f"/api/library?project={garden}")).json()
        only_artifacts = await (
            await c.get(f"/api/library?project={garden}&store=artifacts")
        ).json()
        only_knowledge = await (
            await c.get(f"/api/library?project={garden}&store=knowledge")
        ).json()
    linked_refs = {f"artifact:{a['slug']}" for a in linked["artifacts"]} | {
        f"knowledge:{k['id']}" for k in linked["knowledge"]
    }
    assert linked_refs == {r["ref"] for r in library["items"]}
    assert len(linked["knowledge"]) == 31, "no 25 cap"
    assert {f"knowledge:{i}" for i in [*ours, shared]} == {
        r["ref"] for r in library["items"] if r["store"] == "knowledge"
    }
    assert {a["slug"] for a in linked["artifacts"]} == {filed_art.slug, deliverable.slug}
    assert {r["store"] for r in only_artifacts["items"]} == {"artifacts"}
    assert {r["store"] for r in only_knowledge["items"]} == {"knowledge"}
    assert library["facets"]["store"] == {
        "artifacts": len(linked["artifacts"]),
        "knowledge": len(linked["knowledge"]),
    }
    shared_row = next(r for r in library["items"] if r["ref"] == f"knowledge:{shared}")
    assert shared_row["project"] == {"id": homelab, "name": "Homelab"}, "it is from its own project"

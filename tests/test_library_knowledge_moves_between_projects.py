"""A knowledge item moves into a project, and out of one, in one write.

``POST /api/knowledge/items`` and ``POST /api/knowledge/ingest`` take ``project_id`` and file the
item under it, private, with the project's tag (the tag the project's brief reads it by). A PATCH
with a ``project_id`` moves it: the old project's brief and ``/linked`` no longer have it, the new
one's do, and ``""`` clears its project, its policy and its tag. A move is a filing, so the item
keeps its place in Recent activity, while an edit of its text moves it.
"""

from __future__ import annotations

import pytest
from aiohttp import FormData
from library_shared import client, isolate, knowledge_store, metadata, project

from personalclaw.knowledge.session_brief import load_items, project_tag


@pytest.fixture
def home(tmp_path, monkeypatch):
    return isolate(tmp_path, monkeypatch)


def _tags(item_id: str) -> list[str]:
    return knowledge_store()._tags_for_item(item_id)


async def _linked(c, pid: str) -> set[str]:
    return {
        k["id"] for k in (await (await c.get(f"/api/projects/{pid}/linked")).json())["knowledge"]
    }


@pytest.mark.asyncio
async def test_a_created_or_uploaded_item_is_filed_under_its_project(home):
    homelab = project("Homelab")
    async with client(home) as c:
        made = await c.post(
            "/api/knowledge/items",
            json={"title": "NAS notes", "content": "RAID 1", "project_id": homelab},
        )
        assert made.status == 201, await made.text()
        note_id = (await made.json())["id"]
        form = FormData()
        form.add_field(
            "file", b"The UPS lasts twenty minutes.", filename="ups.txt", content_type="text/plain"
        )
        uploaded = await c.post(f"/api/knowledge/ingest?project_id={homelab}", data=form)
        assert uploaded.status == 200, await uploaded.text()
        file_id = (await uploaded.json())["item_id"]
        refused = await c.post(
            "/api/knowledge/items", json={"title": "x", "content": "y", "project_id": "p-0deadbeef"}
        )
        linked = await _linked(c, homelab)
    for item_id in (note_id, file_id):
        meta = metadata(item_id)
        assert meta["project_id"] == homelab and meta["sharing_policy"] == "private", meta
        assert project_tag(homelab) in _tags(item_id)
    assert {note_id, file_id} <= linked
    assert metadata(note_id)["origin"] == "owner"
    assert refused.status == 404


@pytest.mark.asyncio
async def test_a_patch_moves_an_item_and_empty_takes_it_out(home):
    homelab, garden = project("Homelab"), project("Garden")
    store = knowledge_store()
    async with client(home, knowledge=store) as c:
        item_id = (
            await (
                await c.post(
                    "/api/knowledge/items",
                    json={"title": "Seed notes", "content": "Basil", "project_id": homelab},
                )
            ).json()
        )["id"]
        store.update_item(item_id, tags=[project_tag(homelab), "herbs"], tag_source="ai")
        before = store.get_item(item_id)["updated_at"]
        moved = await c.patch(f"/api/knowledge/items/{item_id}", json={"project_id": garden})
        assert moved.status == 200, await moved.text()
        assert [i.item_id for i in load_items(store, project_id=homelab)] == []
        assert [i.item_id for i in load_items(store, project_id=garden)] == [item_id]
        assert item_id not in await _linked(c, homelab) and item_id in await _linked(c, garden)
        assert store.get_item(item_id)["updated_at"] == before, "a move keeps its activity"
        out = await c.patch(f"/api/knowledge/items/{item_id}", json={"project_id": ""})
        assert out.status == 200
        edited = await c.patch(
            f"/api/knowledge/items/{item_id}", json={"title": "Seed and herb notes"}
        )
        assert edited.status == 200
    meta = metadata(item_id)
    assert "project_id" not in meta and "sharing_policy" not in meta, meta
    assert set(_tags(item_id)) == {"herbs"}, "only the project's tag goes"
    (source,) = (
        knowledge_store()
        .db.execute(
            "SELECT it.source FROM item_tags it JOIN tags t ON t.id = it.tag_id "
            "WHERE it.item_id = ? AND t.name = 'herbs'",
            (item_id,),
        )
        .fetchone()
    )
    assert source == "ai", "every other tag keeps the source that wrote it"
    assert store.get_item(item_id)["updated_at"] > before, "an edit of its title moves it"

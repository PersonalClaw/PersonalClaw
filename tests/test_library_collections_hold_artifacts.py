"""A manual collection holds its knowledge members and the artifacts labelled with its name: Add
to collection on an artifact sets the label, and renaming or deleting the collection relabels or
clears it. A smart collection holds what its search finds in both stores."""

from __future__ import annotations

import pytest
from library_shared import artifacts, client, isolate, note


@pytest.fixture
def home(tmp_path, monkeypatch):
    return isolate(tmp_path, monkeypatch)


async def _in(c, collection_id: str) -> set[str]:
    body = await (await c.get(f"/api/library?collection={collection_id}")).json()
    return {r["ref"] for r in body["items"]}


@pytest.mark.asyncio
async def test_a_manual_collection_holds_the_artifacts_labelled_with_it(home):
    kept = note("Lisbon tram routes", "Line 28")
    prov = artifacts()
    page = prov.create(name="Lisbon trip plan", content="# Day one", kind="markdown")
    async with client(home) as c:
        made = await (
            await c.post("/api/knowledge/collections", json={"name": "Trips", "kind": "manual"})
        ).json()
        cid = made.get("id") or made["collection"]["id"]
        assert (
            await c.post(f"/api/knowledge/collections/{cid}/items", json={"item_ids": [kept]})
        ).ok
        collected = await c.post(
            "/api/library/bulk",
            json={"op": "collect", "refs": [f"artifact:{page.slug}"], "collection_id": cid},
        )
        assert collected.status == 200, await collected.text()
        held = await _in(c, cid)
        renamed = await c.patch(f"/api/knowledge/collections/{cid}", json={"name": "Travel"})
        assert renamed.status == 200, await renamed.text()
        label_after_rename = prov.get(page.slug).collection
        still = await _in(c, cid)
        assert (await c.delete(f"/api/knowledge/collections/{cid}")).ok
        label_after_delete = prov.get(page.slug).collection
    assert held == {f"knowledge:{kept}", f"artifact:{page.slug}"}
    assert label_after_rename == "Travel" and still == held
    assert not label_after_delete


@pytest.mark.asyncio
async def test_a_smart_collection_holds_what_its_search_finds_in_both_stores(home):
    kept = note("Lisbon tram routes", "Line 28 climbs to the castle")
    note("Bread", "Feed the starter")
    page = artifacts().create(name="Lisbon trip plan", content="# Lisbon, day one", kind="markdown")
    async with client(home) as c:
        made = await (
            await c.post(
                "/api/knowledge/collections",
                json={"name": "Lisbon", "kind": "smart", "query": "Lisbon"},
            )
        ).json()
        cid = made.get("id") or made["collection"]["id"]
        held = await _in(c, cid)
    assert {f"knowledge:{kept}", f"artifact:{page.slug}"} <= held
    assert all("Bread" not in ref for ref in held)

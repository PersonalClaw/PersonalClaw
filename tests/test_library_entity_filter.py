"""``entity=<name>`` lists exactly the things the entity is mentioned in, as the entity's own read
(``GET /api/knowledge/entities/by-name/{name}/items``) names them."""

from __future__ import annotations

import pytest
from library_shared import client, isolate, knowledge_store, note


@pytest.fixture
def home(tmp_path, monkeypatch):
    return isolate(tmp_path, monkeypatch)


@pytest.mark.asyncio
async def test_the_entity_filter_lists_what_mentions_it(home):
    backup, layout, other = note("Backup plan"), note("Rack layout"), note("Seed list")
    store = knowledge_store()
    nas = store.add_entity("NAS", "device")
    store.add_mention(backup, nas)
    store.add_mention(layout, nas)
    async with client(home) as c:
        body = await (await c.get("/api/library?entity=NAS")).json()
        named = await (await c.get("/api/knowledge/entities/by-name/NAS/items")).json()
        nothing = await (await c.get("/api/library?entity=Nobody")).json()
    assert {r["ref"] for r in body["items"]} == {f"knowledge:{i['id']}" for i in named}
    assert {r["ref"] for r in body["items"]} == {f"knowledge:{backup}", f"knowledge:{layout}"}
    assert f"knowledge:{other}" not in {r["ref"] for r in body["items"]}
    assert nothing["items"] == [] and nothing["total"] == 0

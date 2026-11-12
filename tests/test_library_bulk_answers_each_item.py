"""``POST /api/library/bulk`` answers each item, ``{ref, ok, error?}``, across both stores, and
never stops at the first failure. A delete is taken only with the confirmed count, the number of
refs the request names."""

from __future__ import annotations

import pytest
from library_shared import artifacts, client, isolate, knowledge_store, note, project


@pytest.fixture
def home(tmp_path, monkeypatch):
    return isolate(tmp_path, monkeypatch)


async def _bulk(c, body: dict) -> tuple[int, dict]:
    out = await c.post("/api/library/bulk", json=body)
    return out.status, await out.json()


@pytest.mark.asyncio
async def test_bulk_answers_each_item_and_goes_on_past_a_failure(home):
    garden = project("Garden")
    kept = note("Seed list")
    page = artifacts().create(name="Bed layout", content="# Beds", kind="markdown")
    refs = [f"knowledge:{kept}", "knowledge:gone", f"artifact:{page.slug}", "artifact:gone"]
    async with client(home) as c:
        pinned = await _bulk(c, {"op": "pin", "refs": refs, "value": True})
        filed = await _bulk(c, {"op": "project", "refs": refs, "project_id": garden})
        read = await _bulk(c, {"op": "read_state", "refs": refs, "state": "read"})
        rows = {r["ref"]: r for r in (await (await c.get("/api/library")).json())["items"]}
    for status, body in (pinned, filed, read):
        assert status == 200
        assert [r["ref"] for r in body["results"]] == refs, "one answer per ref, in order"
    assert [r["ok"] for r in pinned[1]["results"]] == [True, False, True, False]
    assert [r["ok"] for r in filed[1]["results"]] == [True, False, True, False]
    assert read[1]["results"][2] == {
        "ref": f"artifact:{page.slug}",
        "ok": False,
        "error": "not_applicable",
    }
    assert rows[f"knowledge:{kept}"]["pinned"] and rows[f"artifact:{page.slug}"]["pinned"]
    assert rows[f"knowledge:{kept}"]["project"] == {"id": garden, "name": "Garden"}
    assert rows[f"artifact:{page.slug}"]["project"] == {"id": garden, "name": "Garden"}


@pytest.mark.asyncio
async def test_a_delete_needs_the_confirmed_count(home):
    one, two = note("One"), note("Two")
    page = artifacts().create(name="A page", content="# P", kind="markdown")
    refs = [f"knowledge:{one}", f"knowledge:{two}", f"artifact:{page.slug}"]
    async with client(home) as c:
        unconfirmed = await _bulk(c, {"op": "delete", "refs": refs})
        miscounted = await _bulk(c, {"op": "delete", "refs": refs, "expect": 2})
        assert knowledge_store().get_item(one) is not None
        done = await _bulk(c, {"op": "delete", "refs": refs, "expect": 3})
    assert unconfirmed[0] == 400 and miscounted[0] == 400
    assert done[0] == 200 and [r["ok"] for r in done[1]["results"]] == [True, True, True]
    assert knowledge_store().get_item(one) is None
    assert artifacts().get(page.slug) is None

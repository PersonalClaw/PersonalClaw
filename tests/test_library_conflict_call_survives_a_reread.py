"""The owner's call on a conflict is kept by the pair's id, outside the list each reading
replaces. So it holds when the item that holds the entry is read again, and when the other item
is read and holds an entry of its own with the sides swapped: ``prefer`` as served names the
chosen item on both."""

from __future__ import annotations

import pytest
from library_shared import client, conflict_id, conflict_pass, isolate, key_points, note


@pytest.fixture
def home(tmp_path, monkeypatch):
    return isolate(tmp_path, monkeypatch)


async def _read(c) -> tuple[dict, list[dict]]:
    rows = {r["ref"]: r for r in (await (await c.get("/api/library")).json())["items"]}
    listed = (await (await c.get("/api/knowledge/conflicts")).json())["conflicts"]
    return rows, listed


@pytest.mark.asyncio
async def test_the_call_holds_through_both_items_being_read_again(home):
    eight = note("NAS notes", "Old spec sheet")
    twelve = note("NAS upgrade", "New spec sheet")
    key_points(eight, "The NAS is 8 TB")
    key_points(twelve, "The NAS is 12 TB")
    conflict_pass(twelve)
    pair = conflict_id(eight, twelve)
    async with client(home) as c:
        made = await c.post(f"/api/knowledge/conflicts/{pair}/decision", json={"choice": eight})
        assert made.status == 200, await made.text()
        key_points(twelve, "The NAS is 12 TB", "It has four bays")
        assert conflict_pass(twelve), "the holder, read again"
        assert conflict_pass(eight), "and the other item, read on its own"
        rows, listed = await _read(c)
    sides = {e["item_id"]: e for e in listed}
    assert set(sides) == {eight, twelve}
    assert {e["id"] for e in listed} == {pair} and {e["choice"] for e in listed} == {eight}
    for holder, entry in sides.items():
        named = entry["left_item"] if entry["prefer"] == "left" else entry["right_item"]
        assert named == eight, (holder, entry)
    assert sides[twelve]["prefer"] == "right" and sides[eight]["prefer"] == "left"
    assert rows[f"knowledge:{eight}"]["conflicts"] == [] == rows[f"knowledge:{twelve}"]["conflicts"]

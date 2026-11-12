"""A reading writes its key points afresh, so the claims of two items that disagree come back in
other words. The pair keeps its id, since the id is the two items', and the owner's call holds."""

from __future__ import annotations

import pytest
from library_shared import client, conflict_id, conflict_pass, isolate, key_points, note


@pytest.fixture
def home(tmp_path, monkeypatch):
    return isolate(tmp_path, monkeypatch)


@pytest.mark.asyncio
async def test_the_pair_keeps_its_id_and_its_call_in_other_words(home):
    eight = note("NAS notes", "Old spec sheet")
    twelve = note("NAS upgrade", "New spec sheet")
    key_points(eight, "The NAS is 8 TB")
    key_points(twelve, "The NAS is 12 TB")
    conflict_pass(twelve)
    pair = conflict_id(eight, twelve)
    async with client(home) as c:
        made = await c.post(f"/api/knowledge/conflicts/{pair}/decision", json={"choice": twelve})
        assert made.status == 200, await made.text()
        key_points(eight, "The NAS is 8 terabytes")
        key_points(twelve, "The NAS is 12 terabytes")
        assert conflict_pass(eight) and conflict_pass(twelve)
        rows = {r["ref"]: r for r in (await (await c.get("/api/library")).json())["items"]}
        listed = (await (await c.get("/api/knowledge/conflicts")).json())["conflicts"]
    assert {e["id"] for e in listed} == {pair}
    assert {e["choice"] for e in listed} == {twelve}
    for entry in listed:
        assert (entry["left_item"] if entry["prefer"] == "left" else entry["right_item"]) == twelve
    assert rows[f"knowledge:{eight}"]["conflicts"] == [] == rows[f"knowledge:{twelve}"]["conflicts"]

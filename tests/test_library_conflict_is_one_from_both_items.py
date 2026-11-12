"""Two items that disagree are one conflict, with one id, a hash of their two ids: both items'
rows carry it while it is undecided, whichever item holds the entry. A call on an id that names no
pair in disagreement now, or whose choice is neither item nor ``both``, is refused and writes
nothing."""

from __future__ import annotations

import pytest
from library_shared import (
    client,
    conflict_id,
    conflict_pass,
    isolate,
    key_points,
    knowledge_store,
    note,
)


@pytest.fixture
def home(tmp_path, monkeypatch):
    return isolate(tmp_path, monkeypatch)


async def _rows(c) -> dict:
    return {r["ref"]: r for r in (await (await c.get("/api/library")).json())["items"]}


def _decided() -> int:
    return knowledge_store().db.execute("SELECT COUNT(*) FROM conflict_decisions").fetchone()[0]


@pytest.mark.asyncio
async def test_both_items_carry_one_id_whichever_holds_the_entry(home):
    eight = note("NAS notes", "Old spec sheet")
    twelve = note("NAS upgrade", "New spec sheet")
    key_points(eight, "The NAS is 8 TB")
    key_points(twelve, "The NAS is 12 TB")
    pair = conflict_id(eight, twelve)
    assert conflict_pass(twelve), "the pass found the disagreement"
    async with client(home) as c:
        held_by_twelve = await _rows(c)
        listed = (await (await c.get("/api/knowledge/conflicts")).json())["conflicts"]
    store = knowledge_store()
    store.db.execute("UPDATE items SET file_metadata = '{}' WHERE id = ?", (twelve,))
    store.db.commit()
    assert conflict_pass(eight)
    async with client(home) as c:
        held_by_eight = await _rows(c)
    for rows in (held_by_twelve, held_by_eight):
        assert rows[f"knowledge:{eight}"]["conflicts"] == [pair]
        assert rows[f"knowledge:{twelve}"]["conflicts"] == [pair]
    assert [(e["id"], e["choice"]) for e in listed] == [(pair, "")]


@pytest.mark.asyncio
async def test_a_call_names_a_real_pair_and_one_of_its_items(home):
    eight = note("NAS notes", "Old spec sheet")
    twelve = note("NAS upgrade", "New spec sheet")
    other = note("Router", "Router notes")
    key_points(eight, "The NAS is 8 TB")
    key_points(twelve, "The NAS is 12 TB")
    conflict_pass(twelve)
    pair = conflict_id(eight, twelve)
    async with client(home) as c:
        unknown = await c.post(
            f"/api/knowledge/conflicts/{conflict_id(eight, other)}/decision", json={"choice": eight}
        )
        stranger = await c.post(f"/api/knowledge/conflicts/{pair}/decision", json={"choice": other})
        shapeless = await c.post(f"/api/knowledge/conflicts/{pair}/decision", json={"choice": 3})
        assert _decided() == 0
        both = await c.post(f"/api/knowledge/conflicts/{pair}/decision", json={"choice": "both"})
        assert both.status == 200, await both.text()
        rows = await _rows(c)
        listed = (await (await c.get("/api/knowledge/conflicts")).json())["conflicts"]
    assert unknown.status == 404
    assert stranger.status == 400 and shapeless.status == 400
    assert _decided() == 1
    assert rows[f"knowledge:{eight}"]["conflicts"] == [] == rows[f"knowledge:{twelve}"]["conflicts"]
    assert [(e["id"], e["choice"], e["prefer"]) for e in listed] == [(pair, "both", "")]

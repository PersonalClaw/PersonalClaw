"""A memory entity can be removed, and the removal STICKS (issue 524).

Entities were create-only from the outside: `MemoryGraph.delete_entity` has existed since the
graph landed, with **zero non-test callers** — no route, no client wrapper, no control. So a
hand-declared entity was permanent, on a panel that actively proposes NEW entities to accept: a
list the product grows and the user cannot shrink. The type select defaults to `person` and
applies silently, so one untouched dropdown made a market a person forever.

Two things had to be true for a delete to be worth shipping, and only one of them was:

* **the blast radius has to be settled** — it is: `delete_entity` tombstones the entity, drops the
  links pointing AT it, and leaves the records those links came from alone. The UI's job is to say
  so, which is what the confirm dialog now does. (The panel's own comment used the unsettledness as
  the reason to withhold the action.)
* **an automatic pass must not undo it** — it did. `memory_linker`'s facet and knowledge seeders
  call `upsert_entity`, which matched live rows only, so Health's rebuild re-created a deleted
  entity under a fresh id. A delete that the next rebuild reverses is not a delete, so the
  tombstone now outranks every automatic source; only an explicit user declaration brings the name
  back.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.dashboard.handlers import memory as mem_handlers
from personalclaw.memory_linker import seed_from_knowledge, seed_from_memory_facts
from personalclaw.memory_service import MemoryService
from personalclaw.vector_memory import VectorMemoryStore


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    s = VectorMemoryStore(db_path=tmp_path / "m.db", embedding_dim=3)
    s.init()
    # Pinned rather than left to config: `graph_enabled` reads AppConfig live, and these tests
    # must not depend on the ambient home's memory settings.
    s.graph_enabled = True
    return s


@pytest.fixture
def graph(store):
    return store.graph


def _fact(store, key: str, value: dict) -> None:
    store.db.execute(
        "INSERT INTO semantic_memory (key, value_json, scope, source, created_at, updated_at, "
        "is_deleted) VALUES (?, ?, 'global', 'test', '2026-01-01', '2026-01-01', 0)",
        (key, json.dumps(value)),
    )
    store.db.commit()


def _knowledge_db(tmp_path: Path, name: str, entity_type: str = "org") -> Path:
    path = tmp_path / "knowledge.db"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE entities (id TEXT, name TEXT, entity_type TEXT, aliases TEXT)")
    db.execute("INSERT INTO entities VALUES ('k-1', ?, ?, '[]')", (name, entity_type))
    db.commit()
    db.close()
    return path


# ── the tombstone outranks an automatic source ────────────────────────────────────────────────


def test_the_facet_seeder_does_not_resurrect_a_deleted_entity(store, graph, tmp_path):
    """🔑 THE DURABILITY CLAIM. Health's rebuild runs this seeder, so without the tombstone rule a
    deleted entity reappears — with a NEW id, which is worse than no delete at all: the row the
    user removed comes back looking like a different entity that they never declared."""
    _fact(store, "project.kettle-creek.note", {"text": "Kettle Creek Market notes"})
    assert seed_from_memory_facts(graph) == 1
    entity = graph.entity_by_name("kettle-creek")
    assert entity is not None

    assert graph.delete_entity(entity.id) is True

    seed_from_memory_facts(graph)  # the rebuild the user would run next
    assert graph.entity_by_name("kettle-creek") is None
    assert [e.name for e in graph.entities()] == []


def test_the_knowledge_seeder_does_not_resurrect_one_either(store, graph, tmp_path):
    """The SECOND automatic source. Fixing one and not the other would leave the same defect
    behind a different button — `seed_all` runs both."""
    kdb = _knowledge_db(tmp_path, "Ashfield Winter Market")
    assert seed_from_knowledge(graph, kdb) == 1
    entity = graph.entity_by_name("Ashfield Winter Market")
    assert entity is not None

    assert graph.delete_entity(entity.id) is True

    seed_from_knowledge(graph, kdb)
    assert graph.entity_by_name("Ashfield Winter Market") is None


def test_the_seeder_still_creates_an_entity_it_has_never_seen(store, graph):
    """Vacuity guard: the tombstone rule must not stop seeding in general."""
    _fact(store, "project.harbour-line.note", {"text": "Harbour Line"})
    assert seed_from_memory_facts(graph) == 1
    assert graph.entity_by_name("harbour-line") is not None


def test_the_seeder_still_merges_aliases_into_a_LIVE_entity(store, graph):
    """The other half of the vacuity floor — idempotent re-seeding is what `upsert_entity` is for,
    and the tombstone branch must not shadow the live-row branch."""
    eid = graph.upsert_entity("PersonalClaw", "project", aliases=["pclaw"])
    again = graph.upsert_entity("PersonalClaw", "project", aliases=["claw"], source="facet")
    assert again == eid
    assert set(graph.entity_by_name("PersonalClaw").aliases) == {"pclaw", "claw"}


# ── the user can still bring it back, deliberately ────────────────────────────────────────────


def test_a_user_may_re_declare_a_deleted_name(graph):
    """The recovery path for the mistyped entity: delete the person, declare the place. A new row
    with a new id, and the tombstone stays put so the seeders keep their hands off."""
    wrong = graph.upsert_entity("Kettle Creek Market", "person")
    assert graph.delete_entity(wrong) is True

    right = graph.upsert_entity("Kettle Creek Market", "place", source="user")

    assert right != wrong
    entity = graph.entity_by_name("Kettle Creek Market")
    assert entity is not None and entity.entity_type == "place"
    assert len(graph.entities(include_deleted=True)) == 2, "the tombstone is kept, not reused"


def test_accepting_a_proposal_brings_a_deleted_name_back(graph):
    """The other deliberate route in, and the reason blocking the seeders is not a trap: a deleted
    name is unknown again, so it can recur, be proposed, and be accepted BY HAND."""
    eid = graph.upsert_entity("Ana", "person")
    assert graph.delete_entity(eid) is True
    graph.tally_proposal("Ana", "semantic:note.1")

    new_id = graph.accept_proposal("Ana", "person")

    assert new_id != eid
    assert graph.entity_by_name("Ana") is not None


# ── deleting stops the matching, not just the listing ─────────────────────────────────────────


def test_a_deleted_entity_stops_matching_text(store, graph):
    eid = graph.upsert_entity("Kettle Creek Market", "place", aliases=["KCM"])
    store.invalidate_alias_index()
    assert store.alias_index.find("met KCM today")

    MemoryService.over_vector_store(store).graph_delete_entity(eid)

    assert store.alias_index.find("met KCM today") == [], (
        "the alias index is a cached snapshot — without invalidation the deleted entity keeps "
        "matching, and keeps being linked to new records, until the process restarts"
    )


def test_the_service_reports_a_no_op_rather_than_claiming_a_delete(store):
    svc = MemoryService.over_vector_store(store)
    assert svc.graph_delete_entity("ent_nope") is False


def test_the_service_delete_is_false_when_the_graph_is_off(store):
    store.graph_enabled = False
    assert MemoryService.over_vector_store(store).graph_delete_entity("ent_x") is False


def test_entity_by_name_ignores_a_tombstone(graph):
    eid = graph.upsert_entity("X Corp", "org")
    assert graph.entity_by_name("x corp") is not None, "the lookup is case-insensitive"
    graph.delete_entity(eid)
    assert graph.entity_by_name("X Corp") is None


# ── the HTTP surface that was missing entirely ────────────────────────────────────────────────


async def _client(monkeypatch, store) -> TestClient:
    svc = MemoryService.over_vector_store(store)
    monkeypatch.setattr(mem_handlers, "_get_service", lambda _state: svc)
    app = web.Application()
    app["state"] = object()
    app.router.add_get("/api/memory/entities", mem_handlers.api_memory_entities)
    app.router.add_post("/api/memory/entities", mem_handlers.api_memory_entity_create)
    app.router.add_delete("/api/memory/entities/{entity_id}", mem_handlers.api_memory_entity_delete)
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


@pytest.mark.asyncio
async def test_delete_removes_the_entity_from_the_list(monkeypatch, store, graph):
    eid = graph.upsert_entity("Kettle Creek Market", "place")
    client = await _client(monkeypatch, store)
    try:
        r = await client.delete(f"/api/memory/entities/{eid}")
        assert r.status == 200
        assert (await r.json())["ok"] is True
        body = await (await client.get("/api/memory/entities")).json()
        assert [e["name"] for e in body["entities"]] == []
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_delete_of_an_unknown_id_is_404(monkeypatch, store):
    client = await _client(monkeypatch, store)
    try:
        assert (await client.delete("/api/memory/entities/ent_nope")).status == 404
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_a_second_delete_is_404_not_a_reported_success(monkeypatch, store, graph):
    """A double-submit, or two open copies of the panel. `200 {ok: false}` would let the second
    one report a deletion it did not perform — the shape #636 closed for the sibling deletes."""
    eid = graph.upsert_entity("X Corp", "org")
    client = await _client(monkeypatch, store)
    try:
        assert (await client.delete(f"/api/memory/entities/{eid}")).status == 200
        assert (await client.delete(f"/api/memory/entities/{eid}")).status == 404
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_delete_is_409_when_the_graph_is_disabled(monkeypatch, store):
    """Matching the create route's guard: with the graph off there is no entity set to act on, and
    404 would say "no such entity" about a whole feature that is switched off."""
    store.graph_enabled = False
    client = await _client(monkeypatch, store)
    try:
        r = await client.delete("/api/memory/entities/ent_x")
        assert r.status == 409
        assert "disabled" in (await r.json())["error"]
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_the_links_go_and_the_memories_stay(monkeypatch, store, graph):
    """🔑 The sentence the confirm dialog promises, asserted. This is the reasoning the panel was
    waiting on before it would offer the control."""
    _fact(store, "note.market", {"text": "Kettle Creek Market opens at nine"})
    eid = graph.upsert_entity("Kettle Creek Market", "place")
    graph.add_link(
        from_kind="semantic", from_ref="note.market", to_entity=eid, link_type="mentions"
    )
    client = await _client(monkeypatch, store)
    try:
        assert (await client.delete(f"/api/memory/entities/{eid}")).status == 200
    finally:
        await client.close()

    assert graph.backlinks(eid) == []
    kept = store.db.execute(
        "SELECT COUNT(*) FROM semantic_memory WHERE key = 'note.market' AND is_deleted = 0"
    ).fetchone()[0]
    assert kept == 1, "deleting an entity must never delete the memories that mentioned it"


# ── declaring a name that exists, with a different type ───────────────────────────────────────


@pytest.mark.asyncio
async def test_re_declaring_with_a_different_type_is_REFUSED(monkeypatch, store, graph):
    """🔑 The trap the issue describes from the other side. `upsert_entity` matches on name and
    never changes the type, so re-declaring the mistyped market as a place returned
    `{ok: true, id}` and changed nothing — telling the person who had just spotted the mistake
    that it was fixed. It now names the existing type and the way out."""
    eid = graph.upsert_entity("Ashfield Winter Market", "person")
    client = await _client(monkeypatch, store)
    try:
        r = await client.post(
            "/api/memory/entities",
            json={"name": "Ashfield Winter Market", "entity_type": "place"},
        )
        assert r.status == 409
        body = await r.json()
        assert "already exists as a person" in body["error"]
        assert "delete it first" in body["error"].lower()
        assert body["id"] == eid
    finally:
        await client.close()

    assert graph.entity_by_name("Ashfield Winter Market").entity_type == "person"


@pytest.mark.asyncio
async def test_re_declaring_with_the_SAME_type_stays_idempotent(monkeypatch, store, graph):
    """Vacuity guard, and the reason the refusal is narrow: same name + same type is how alias
    merging and re-seeding work. Refusing that would break the create form's own repeat use."""
    eid = graph.upsert_entity("PersonalClaw", "project", aliases=["pclaw"])
    client = await _client(monkeypatch, store)
    try:
        r = await client.post(
            "/api/memory/entities",
            json={"name": "PersonalClaw", "entity_type": "project", "aliases": ["claw"]},
        )
        assert r.status == 200
        assert (await r.json())["id"] == eid
    finally:
        await client.close()
    assert set(graph.entity_by_name("PersonalClaw").aliases) == {"pclaw", "claw"}


@pytest.mark.asyncio
async def test_a_new_name_is_still_created(monkeypatch, store, graph):
    client = await _client(monkeypatch, store)
    try:
        r = await client.post(
            "/api/memory/entities", json={"name": "Harbour Line", "entity_type": "place"}
        )
        assert r.status == 200
    finally:
        await client.close()
    assert graph.entity_by_name("Harbour Line") is not None


@pytest.mark.asyncio
async def test_the_refusal_does_not_fire_for_a_TOMBSTONED_name(monkeypatch, store, graph):
    """The recovery path end to end: delete the mistyped person, then declare the place. If the
    conflict check consulted tombstones it would refuse the very correction it recommends."""
    eid = graph.upsert_entity("Ashfield Winter Market", "person")
    client = await _client(monkeypatch, store)
    try:
        assert (await client.delete(f"/api/memory/entities/{eid}")).status == 200
        r = await client.post(
            "/api/memory/entities",
            json={"name": "Ashfield Winter Market", "entity_type": "place"},
        )
        assert r.status == 200
    finally:
        await client.close()
    assert graph.entity_by_name("Ashfield Winter Market").entity_type == "place"

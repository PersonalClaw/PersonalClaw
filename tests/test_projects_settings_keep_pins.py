"""The Projects list's pins are the owner's, stored beside the default project.

The list shows the projects the owner pinned first, in the order they pinned them, then the rest
by activity. The pins live where the default does, in ``entity_settings/projects.json`` behind
``GET/PUT /api/projects/settings``, so they follow the owner to every browser, the desktop app and
the phone.

What these pin:

* the pins round-trip in their order, and a pin twice is one pin;
* a ``PUT`` with only ``pinned_project_ids`` keeps the default, and one with only
  ``default_project_id`` keeps the pins: either key alone is a whole request;
* a ``PUT`` with neither key is refused, so a client that sends nothing wipes nothing;
* a pin can only name a project that exists and is not archived when it is written, by an id of
  the store's shape;
* a pin change while a pinned project is archived keeps that pin in its place for the restore;
* the read resolves against the live store, as the default's does: a pin whose project was
  archived or deleted since drops out, and a hand-edited entry that is not a project id reads as
  no pin;
* the read answers exactly its two keys.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from unittest.mock import patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.tasks import registry
from personalclaw.tasks.handlers import register_task_routes


@asynccontextmanager
async def _client(tmp_path):
    registry._providers.clear()
    with (
        patch("personalclaw.tasks.native.config_dir", return_value=tmp_path),
        patch("personalclaw.tasks.hierarchy.config_dir", return_value=tmp_path),
        patch("personalclaw.providers.entity_routes.config_dir", return_value=tmp_path),
    ):
        from personalclaw.dashboard.request_boundary import request_boundary_middleware

        app = web.Application(middlewares=[request_boundary_middleware()])
        register_task_routes(app)
        async with TestClient(TestServer(app)) as client:
            yield client
    registry._providers.clear()


async def _project(client, name: str) -> str:
    r = await client.post("/api/projects", json={"name": name})
    assert r.status == 201, await r.text()
    return (await r.json())["id"]


async def _settings(client) -> dict:
    r = await client.get("/api/projects/settings")
    assert r.status == 200, await r.text()
    return await r.json()


def _stored(tmp_path) -> dict:
    return json.loads((tmp_path / "entity_settings" / "projects.json").read_text())


@pytest.mark.asyncio
async def test_a_fresh_home_has_no_pins(tmp_path):
    async with _client(tmp_path) as client:
        assert (await _settings(client))["pinned_project_ids"] == []


@pytest.mark.asyncio
async def test_pins_round_trip_in_their_order_and_a_pin_twice_is_one(tmp_path):
    async with _client(tmp_path) as client:
        garden = await _project(client, "Garden")
        taxes = await _project(client, "Taxes")
        trip = await _project(client, "Trip")
        r = await client.put(
            "/api/projects/settings", json={"pinned_project_ids": [trip, garden, trip, taxes]}
        )
        assert r.status == 200, await r.text()
        assert (await r.json())["pinned_project_ids"] == [trip, garden, taxes]
        assert (await _settings(client))["pinned_project_ids"] == [trip, garden, taxes]
        assert _stored(tmp_path)["pinned_project_ids"] == [trip, garden, taxes]


@pytest.mark.asyncio
async def test_either_key_alone_keeps_the_other(tmp_path):
    async with _client(tmp_path) as client:
        garden = await _project(client, "Garden")
        taxes = await _project(client, "Taxes")
        r = await client.put("/api/projects/settings", json={"default_project_id": garden})
        assert r.status == 200, await r.text()
        r = await client.put("/api/projects/settings", json={"pinned_project_ids": [taxes]})
        assert r.status == 200, await r.text()
        assert await _settings(client) == {
            "default_project_id": garden,
            "pinned_project_ids": [taxes],
        }
        r = await client.put("/api/projects/settings", json={"default_project_id": ""})
        assert r.status == 200, await r.text()
        assert await _settings(client) == {"default_project_id": "", "pinned_project_ids": [taxes]}


@pytest.mark.asyncio
async def test_a_put_with_neither_key_is_refused_and_wipes_nothing(tmp_path):
    async with _client(tmp_path) as client:
        garden = await _project(client, "Garden")
        r = await client.put(
            "/api/projects/settings",
            json={"default_project_id": garden, "pinned_project_ids": [garden]},
        )
        assert r.status == 200, await r.text()
        r = await client.put("/api/projects/settings", json={})
        assert r.status == 400
        assert (await r.json())["error"]["code"] == "field_required"
        assert await _settings(client) == {
            "default_project_id": garden,
            "pinned_project_ids": [garden],
        }


@pytest.mark.asyncio
async def test_a_pin_must_name_a_project_new_work_can_start_in(tmp_path):
    async with _client(tmp_path) as client:
        garden = await _project(client, "Garden")
        r = await client.put("/api/projects/settings", json={"pinned_project_ids": "garden"})
        assert r.status == 400, "a pin list that is not a list was taken"
        r = await client.put("/api/projects/settings", json={"pinned_project_ids": [garden, 7]})
        assert r.status == 400, "a pin that is not an id was taken"
        r = await client.put("/api/projects/settings", json={"pinned_project_ids": ["p-nope"]})
        assert r.status == 400
        assert (await r.json())["error"]["code"] == "project_not_found"
        r = await client.put(f"/api/projects/{garden}", json={"status": "archived"})
        assert r.status == 200, await r.text()
        r = await client.put("/api/projects/settings", json={"pinned_project_ids": [garden]})
        assert r.status == 409
        assert (await r.json())["error"]["code"] == "project_archived"
        assert (await _settings(client))["pinned_project_ids"] == []


@pytest.mark.asyncio
async def test_a_pin_archived_or_deleted_since_drops_out_at_read(tmp_path):
    async with _client(tmp_path) as client:
        garden = await _project(client, "Garden")
        taxes = await _project(client, "Taxes")
        trip = await _project(client, "Trip")
        r = await client.put(
            "/api/projects/settings", json={"pinned_project_ids": [garden, taxes, trip]}
        )
        assert r.status == 200, await r.text()
        r = await client.put(f"/api/projects/{taxes}", json={"status": "archived"})
        assert r.status == 200, await r.text()
        r = await client.delete(f"/api/projects/{trip}")
        assert r.status in (200, 204), await r.text()
        assert (await _settings(client))["pinned_project_ids"] == [garden]
        # The stored list is left as written: a restored project is pinned again where it was.
        assert _stored(tmp_path)["pinned_project_ids"] == [garden, taxes, trip]
        r = await client.put(f"/api/projects/{taxes}", json={"status": "active"})
        assert r.status == 200, await r.text()
        assert (await _settings(client))["pinned_project_ids"] == [garden, taxes]


@pytest.mark.asyncio
async def test_a_hand_edited_pin_that_is_not_a_project_id_reads_as_no_pin(tmp_path):
    (tmp_path / "entity_settings").mkdir(parents=True)
    (tmp_path / "entity_settings" / "projects.json").write_text(
        json.dumps({"pinned_project_ids": ["../../etc", 3, None, ""]})
    )
    async with _client(tmp_path) as client:
        assert (await _settings(client))["pinned_project_ids"] == []


@pytest.mark.asyncio
async def test_the_settings_read_answers_its_two_keys(tmp_path):
    async with _client(tmp_path) as client:
        assert set(await _settings(client)) == {"default_project_id", "pinned_project_ids"}


@pytest.mark.asyncio
async def test_a_pin_change_while_a_pin_is_archived_keeps_the_archived_ones_place(tmp_path):
    # The page writes back the list it read, which hides an archived pin; the write keeps it, so
    # restoring the project pins it again where it was.
    async with _client(tmp_path) as client:
        garden = await _project(client, "Garden")
        taxes = await _project(client, "Taxes")
        trip = await _project(client, "Trip")
        r = await client.put("/api/projects/settings", json={"pinned_project_ids": [garden, taxes]})
        assert r.status == 200, await r.text()
        r = await client.put(f"/api/projects/{garden}", json={"status": "archived"})
        assert r.status == 200, await r.text()
        r = await client.put("/api/projects/settings", json={"pinned_project_ids": [taxes, trip]})
        assert r.status == 200, await r.text()
        assert (await r.json())["pinned_project_ids"] == [taxes, trip]
        r = await client.put(f"/api/projects/{garden}", json={"status": "active"})
        assert r.status == 200, await r.text()
        assert (await _settings(client))["pinned_project_ids"] == [garden, taxes, trip]


@pytest.mark.asyncio
async def test_a_pin_change_passes_over_a_stored_entry_that_is_no_project(tmp_path):
    # A hand-edited entry and a deleted project's id sit in the stored list: a change still
    # answers, and keeps the pins it was given.
    async with _client(tmp_path) as client:
        garden = await _project(client, "Garden")
        gone = await _project(client, "Gone")
        r = await client.delete(f"/api/projects/{gone}")
        assert r.status in (200, 204), await r.text()
        (tmp_path / "entity_settings").mkdir(parents=True, exist_ok=True)
        (tmp_path / "entity_settings" / "projects.json").write_text(
            json.dumps({"pinned_project_ids": ["../../etc", gone, garden]})
        )
        r = await client.put("/api/projects/settings", json={"pinned_project_ids": [garden]})
        assert r.status == 200, await r.text()
        assert (await r.json())["pinned_project_ids"] == [garden]


@pytest.mark.asyncio
async def test_a_pin_that_is_no_project_id_shape_is_refused_as_an_invalid_id(tmp_path):
    async with _client(tmp_path) as client:
        r = await client.put("/api/projects/settings", json={"pinned_project_ids": ["../x"]})
        assert r.status == 400, await r.text()
        assert (await r.json())["error"]["code"] == "invalid_id"

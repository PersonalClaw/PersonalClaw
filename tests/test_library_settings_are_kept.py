"""The library's own settings: ``power_user_view``, read and written at ``/api/library/settings``
and kept in ``entity_settings/library.json``. A body that names none of its keys is refused."""

from __future__ import annotations

import json

import pytest
from library_shared import client, isolate


@pytest.fixture
def home(tmp_path, monkeypatch):
    return isolate(tmp_path, monkeypatch)


@pytest.mark.asyncio
async def test_power_user_view_is_kept_and_an_empty_body_is_refused(home):
    async with client(home) as c:
        first = await (await c.get("/api/library/settings")).json()
        put = await c.put("/api/library/settings", json={"power_user_view": True})
        assert put.status == 200, await put.text()
        saved = await put.json()
        after = await (await c.get("/api/library/settings")).json()
        empty = await c.put("/api/library/settings", json={})
        stranger = await c.put("/api/library/settings", json={"theme": "dark"})
        shapeless = await c.put("/api/library/settings", json={"power_user_view": "yes"})
        kept = await (await c.get("/api/library/settings")).json()
    assert first == {"power_user_view": False}
    assert saved == after == kept == {"power_user_view": True}
    assert empty.status == 400 and stranger.status == 400 and shapeless.status == 400
    stored = json.loads((home / "entity_settings" / "library.json").read_text())
    assert stored == {"power_user_view": True}

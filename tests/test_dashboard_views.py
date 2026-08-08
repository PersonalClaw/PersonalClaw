"""AMBIENT-SURFACES AS-1 — the dashboard-as-views registry + agent-propose tool.

Covers the atom's safety contract: the Overview preset is read-only, an EMPTY
registry renders exactly today's fixed core layout (byte-identical safety), a tile
carries a ref + size + order and NEVER a coordinate, pinning POSTs a tile, and the
``dashboard_tile_propose`` tool writes an ``added_by:agent`` row.

Isolation: every test binds an isolated home via ``PERSONALCLAW_HOME`` (the robust
lever — ``config_dir()`` reads the env var per call) AND monkeypatches the store's
imported ``config_dir`` symbol, so nothing touches the real ``~/.personalclaw``.
"""

from __future__ import annotations

import json
from dataclasses import fields

import pytest

from personalclaw.dashboard import views_store as store


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    monkeypatch.setattr("personalclaw.dashboard.views_store.config_dir", lambda: home)
    return home


# ── The Overview preset ───────────────────────────────────────────────────────


def test_overview_preset_exists_and_is_locked():
    views = store.load_views()
    overview = next(v for v in views if v.id == store.PRESET_OVERVIEW_ID)
    assert overview.preset is True
    assert overview.name == "Overview"


def test_empty_registry_renders_todays_core_layout_byte_identical():
    """The critical safety property: a fresh install's Overview = today's eight
    widgets in today's order, and NO artifact tiles. The registry is additive over
    the fixed layout — an empty store adds nothing."""
    overview = store.get_view(store.PRESET_OVERVIEW_ID)
    core_refs = [t.ref for t in overview.tiles]
    assert core_refs == list(store._OVERVIEW_CORE_REFS)
    # No artifact tiles on a fresh install — nothing overlaid.
    assert all(r.startswith("core:") for r in core_refs)
    # And these are exactly the eight hard-imported widgets in DashboardPage order.
    assert core_refs == [
        "core:hero-pulse",
        "core:action-center",
        "core:active-work",
        "core:tasks",
        "core:suggestions",
        "core:discover",
        "core:schedule",
        "core:system-health",
    ]


def test_preset_refuses_edit():
    with pytest.raises(store.PresetLockedError):
        store.update_view(store.PRESET_OVERVIEW_ID, {"name": "Renamed"})


def test_preset_refuses_delete():
    with pytest.raises(store.PresetLockedError):
        store.delete_view(store.PRESET_OVERVIEW_ID)


# ── The tile schema — refs + size + order, NEVER coordinates ────────────────────


def test_tile_has_no_coordinate_fields():
    """A tile is a ref + size + order + added_by — the coordinate grid is retired."""
    names = {f.name for f in fields(store.DashboardTile)}
    assert names == {"ref", "size", "order", "added_by"}
    for banned in ("x", "y", "w", "h", "col", "row", "width", "height"):
        assert banned not in names


def test_pin_posts_an_artifact_tile_to_a_view():
    view = store.add_tile(store.PRESET_OVERVIEW_ID, "artifact:sales-board", size="l")
    tiles = [t for t in view.tiles if t.ref == "artifact:sales-board"]
    assert len(tiles) == 1
    assert tiles[0].size == "l"
    assert tiles[0].added_by == "user"
    # Persisted as overlay on disk — not folded into the locked core refs.
    disk = json.loads(store.views_path().read_text())
    assert disk["overlay"][store.PRESET_OVERVIEW_ID][0]["ref"] == "artifact:sales-board"
    assert "x" not in disk["overlay"][store.PRESET_OVERVIEW_ID][0]


def test_core_refs_cannot_be_pinned():
    """Only artifact:<slug> tiles are addable — first-party widgets stay hard imports."""
    with pytest.raises(ValueError):
        store.add_tile(store.PRESET_OVERVIEW_ID, "core:hero-pulse")


def test_pin_is_idempotent():
    store.add_tile(store.PRESET_OVERVIEW_ID, "artifact:board")
    store.add_tile(store.PRESET_OVERVIEW_ID, "artifact:board")
    overview = store.get_view(store.PRESET_OVERVIEW_ID)
    assert len([t for t in overview.tiles if t.ref == "artifact:board"]) == 1


def test_tile_cap_enforced(monkeypatch):
    monkeypatch.setattr(store, "_max_tiles", lambda: 2)
    store.add_tile(store.PRESET_OVERVIEW_ID, "artifact:a")
    store.add_tile(store.PRESET_OVERVIEW_ID, "artifact:b")
    with pytest.raises(ValueError):
        store.add_tile(store.PRESET_OVERVIEW_ID, "artifact:c")


# ── User views ──────────────────────────────────────────────────────────────


def test_create_update_delete_user_view():
    view = store.create_view("My Board", icon="LayoutGrid")
    assert not view.preset
    updated = store.update_view(view.id, {"name": "Renamed", "nav_pinned": True})
    assert updated.name == "Renamed" and updated.nav_pinned is True
    store.delete_view(view.id)
    assert store.get_view(view.id) is None


# ── Agent proposals: accept / dismiss ───────────────────────────────────────


def test_proposed_tile_carries_added_by_agent():
    store.add_tile(store.PRESET_OVERVIEW_ID, "artifact:proposed", added_by="agent")
    overview = store.get_view(store.PRESET_OVERVIEW_ID)
    proposed = next(t for t in overview.tiles if t.ref == "artifact:proposed")
    assert proposed.added_by == "agent"


def test_accept_flips_proposal_to_user():
    store.add_tile(store.PRESET_OVERVIEW_ID, "artifact:p", added_by="agent")
    store.resolve_tile(store.PRESET_OVERVIEW_ID, "artifact:p", keep=True)
    overview = store.get_view(store.PRESET_OVERVIEW_ID)
    tile = next(t for t in overview.tiles if t.ref == "artifact:p")
    assert tile.added_by == "user"


def test_dismiss_removes_the_tile():
    store.add_tile(store.PRESET_OVERVIEW_ID, "artifact:p", added_by="agent")
    store.resolve_tile(store.PRESET_OVERVIEW_ID, "artifact:p", keep=False)
    overview = store.get_view(store.PRESET_OVERVIEW_ID)
    assert not any(t.ref == "artifact:p" for t in overview.tiles)


# ── The dashboard_tile_propose tool ─────────────────────────────────────────


def test_tile_propose_tool_writes_agent_row():
    from personalclaw import mcp_core

    out = mcp_core._call_tool("dashboard_tile_propose", {"slug": "live-board", "size": "full"})
    assert "accept/dismiss chip" in out
    overview = store.get_view(store.PRESET_OVERVIEW_ID)
    tile = next(t for t in overview.tiles if t.ref == "artifact:live-board")
    assert tile.added_by == "agent"
    assert tile.size == "full"


def test_tile_propose_tool_is_registered():
    from personalclaw import mcp_core

    assert "dashboard_tile_propose" in [t["name"] for t in mcp_core._list_tools()]


# ── HTTP boundary: /api/dashboard/views CRUD (presets read-only) ────────────


def _run(coro):
    import asyncio

    return asyncio.new_event_loop().run_until_complete(coro)


def _req(method, path, *, match=None, body=None):
    from aiohttp import web
    from aiohttp.test_utils import make_mocked_request

    request = make_mocked_request(method, path, match_info=match or {}, app=web.Application())

    # The handlers read the body via ``await request.json()``; inject it directly
    # rather than plumbing a payload stream (which needs a live protocol).
    async def _json():
        return body or {}

    request.json = _json  # type: ignore[method-assign]
    return request


def _body(resp):
    import json as _json

    return _json.loads(resp.body.decode())


def test_route_lists_views_with_overview_preset():
    from personalclaw.dashboard.handlers import views as H

    resp = _run(H.api_dashboard_views(_req("GET", "/api/dashboard/views")))
    assert resp.status == 200
    ids = [v["id"] for v in _body(resp)["views"]]
    assert "overview" in ids


def test_route_refuses_preset_edit_with_403():
    from personalclaw.dashboard.handlers import views as H

    resp = _run(
        H.api_dashboard_view_detail(
            _req(
                "PUT",
                "/api/dashboard/views/overview",
                match={"view_id": "overview"},
                body={"name": "X"},
            )
        )
    )
    assert resp.status == 403


def test_route_refuses_preset_delete_with_403():
    from personalclaw.dashboard.handlers import views as H

    resp = _run(
        H.api_dashboard_view_detail(
            _req("DELETE", "/api/dashboard/views/overview", match={"view_id": "overview"})
        )
    )
    assert resp.status == 403


def test_route_pins_a_tile():
    from personalclaw.dashboard.handlers import views as H

    resp = _run(
        H.api_dashboard_view_tiles(
            _req(
                "POST",
                "/api/dashboard/views/overview/tiles",
                match={"view_id": "overview"},
                body={"slug": "board", "size": "l"},
            )
        )
    )
    assert resp.status == 201
    refs = [t["ref"] for t in _body(resp)["view"]["tiles"]]
    assert "artifact:board" in refs

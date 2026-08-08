"""Dashboard-views CRUD endpoints (AMBIENT-SURFACES §1 / A2-1).

``/api/dashboard/views`` over the ``dashboard_views.json`` registry. Locked presets
(Overview) are read-only: a PUT/DELETE that targets a preset is refused with 403, and
POST creates only user views. Tiles are pinned/proposed/resolved through the tile
sub-routes; artifact tiles carry a ref + size + order, never coordinates.
"""

import logging

from aiohttp import web

from personalclaw.dashboard import views_store as store

logger = logging.getLogger(__name__)


async def _json_body(request: web.Request) -> dict:
    try:
        body = await request.json()
    except Exception:
        return {}
    return body if isinstance(body, dict) else {}


async def api_genui_library(request: web.Request) -> web.Response:
    """GET /api/genui/library — the generative-UI component catalog + the mechanically
    derived authoring prompt (AMBIENT-SURFACES §5.2).

    Served so the visual-output skill / workflow node prompts / the FE embed the
    CURRENT registry rather than a hand-maintained copy that drifts. Read-only.
    """
    from personalclaw.genui import library_manifest

    return web.json_response(library_manifest())


async def api_dashboard_views(request: web.Request) -> web.Response:
    """GET /api/dashboard/views — every view (locked presets first, then user views).

    POST /api/dashboard/views {name, icon?} — create a user view (presets are code-only).
    """
    if request.method == "POST":
        body = await _json_body(request)
        name = str(body.get("name", "")).strip()
        if not name:
            return web.json_response({"error": "name is required"}, status=400)
        try:
            view = store.create_view(name, icon=(str(body["icon"]) if body.get("icon") else None))
        except ValueError as exc:
            return web.json_response({"error": str(exc)}, status=400)
        from dataclasses import asdict

        return web.json_response({"view": asdict(view)}, status=201)
    return web.json_response({"views": store.list_views()})


async def api_dashboard_view_detail(request: web.Request) -> web.Response:
    """GET/PUT/DELETE /api/dashboard/views/{view_id} — read, edit, or delete a view.

    PUT/DELETE on a locked preset return 403 (presets refuse edit/delete).
    """
    view_id = request.match_info["view_id"]
    if request.method == "GET":
        view = store.get_view(view_id)
        if view is None:
            return web.json_response({"error": "view not found"}, status=404)
        from dataclasses import asdict

        return web.json_response({"view": asdict(view)})

    if request.method == "PUT":
        body = await _json_body(request)
        try:
            view = store.update_view(view_id, body)
        except store.PresetLockedError as exc:
            return web.json_response({"error": str(exc)}, status=403)
        except store.ViewNotFoundError:
            return web.json_response({"error": "view not found"}, status=404)
        from dataclasses import asdict

        return web.json_response({"view": asdict(view)})

    # DELETE
    try:
        store.delete_view(view_id)
    except store.PresetLockedError as exc:
        return web.json_response({"error": str(exc)}, status=403)
    except store.ViewNotFoundError:
        return web.json_response({"error": "view not found"}, status=404)
    return web.json_response({"ok": True})


async def api_dashboard_view_tiles(request: web.Request) -> web.Response:
    """POST /api/dashboard/views/{view_id}/tiles {slug, size?} — pin an artifact tile.

    The pin-to-dashboard endpoint. ``slug`` is an artifact slug; it is stored as an
    ``artifact:<slug>`` ref with ``added_by: user``. Pinning to a preset writes the
    view's overlay (the locked core composition is never touched).
    """
    view_id = request.match_info["view_id"]
    body = await _json_body(request)
    slug = str(body.get("slug", "")).strip()
    if not slug:
        return web.json_response({"error": "slug is required"}, status=400)
    ref = slug if slug.startswith("artifact:") else f"artifact:{slug}"
    size = str(body.get("size", "m"))
    try:
        view = store.add_tile(view_id, ref, size=size, added_by="user")
    except store.ViewNotFoundError:
        return web.json_response({"error": "view not found"}, status=404)
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    from dataclasses import asdict

    return web.json_response({"view": asdict(view)}, status=201)


async def api_dashboard_view_tile_resolve(request: web.Request) -> web.Response:
    """POST /api/dashboard/views/{view_id}/tiles/resolve {ref, keep} — accept/dismiss/unpin.

    ``keep: true`` accepts an agent-proposed tile (it stops rendering as a proposal);
    ``keep: false`` removes the tile (dismiss a proposal, or unpin a user tile).
    """
    view_id = request.match_info["view_id"]
    body = await _json_body(request)
    ref = str(body.get("ref", "")).strip()
    if not ref:
        return web.json_response({"error": "ref is required"}, status=400)
    keep = bool(body.get("keep", False))
    try:
        view = store.resolve_tile(view_id, ref, keep=keep)
    except store.ViewNotFoundError:
        return web.json_response({"error": "view or tile not found"}, status=404)
    from dataclasses import asdict

    return web.json_response({"view": asdict(view)})

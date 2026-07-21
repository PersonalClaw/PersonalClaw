"""HTTP handlers for /api/lexicon/* — the user-facing Vocabulary surface (core LEX.6).

List terms (source-badged: graph / manual / learned), add a manual term (+aliases), prune
(disable) / delete, rebuild from the knowledge graph, and view + toggle learned corrections'
auto_apply. The Minutes app's transcript-edit UX also POSTs corrections here (LEX.5), gated
by its ``/api/lexicon`` permission.
"""

from __future__ import annotations

import logging

from aiohttp import web

from personalclaw.lexicon import get_lexicon_service

logger = logging.getLogger(__name__)


def _term_dict(t) -> dict:
    return {
        "id": t.id,
        "canonical": t.canonical,
        "aliases": t.aliases,
        "entity_type": t.entity_type,
        "weight": t.weight,
        "source": t.source,
        "enabled": t.enabled,
    }


def _corr_dict(c) -> dict:
    return {
        "id": c.id,
        "heard": c.heard,
        "meant": c.meant,
        "count": c.count,
        "auto_apply": c.auto_apply,
        "last_seen": c.last_seen,
    }


async def api_lexicon_terms(request: web.Request) -> web.Response:
    """GET /api/lexicon/terms?source=&search= — list vocabulary terms."""
    svc = get_lexicon_service()
    source = request.query.get("source", "")
    search = request.query.get("search", "")
    terms = svc.list_terms(source=source, search=search)
    return web.json_response(
        {
            "terms": [_term_dict(t) for t in terms],
            "total": svc.store.count_terms(),
        }
    )


async def api_lexicon_add_term(request: web.Request) -> web.Response:
    """POST /api/lexicon/terms {canonical, aliases?} — add a manual term."""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    canonical = (body.get("canonical") or "").strip()
    if not canonical:
        return web.json_response({"error": "canonical is required"}, status=400)
    aliases = body.get("aliases") or []
    if isinstance(aliases, str):
        aliases = [a.strip() for a in aliases.split(",") if a.strip()]
    svc = get_lexicon_service()
    term_id = svc.add_manual_term(canonical, aliases=list(aliases))
    return web.json_response({"ok": True, "id": term_id})


async def api_lexicon_update_term(request: web.Request) -> web.Response:
    """PATCH /api/lexicon/terms/{id} {enabled?} — enable/disable (prune) a term."""
    term_id = request.match_info["id"]
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    svc = get_lexicon_service()
    if "enabled" in body:
        if not svc.store.set_enabled(term_id, bool(body["enabled"])):
            return web.json_response({"error": "term not found"}, status=404)
    return web.json_response({"ok": True})


async def api_lexicon_delete_term(request: web.Request) -> web.Response:
    """DELETE /api/lexicon/terms/{id} — remove a term entirely."""
    svc = get_lexicon_service()
    if not svc.store.delete_term(request.match_info["id"]):
        return web.json_response({"error": "term not found"}, status=404)
    return web.json_response({"ok": True})


async def api_lexicon_rebuild(request: web.Request) -> web.Response:
    """POST /api/lexicon/rebuild — resync graph-sourced terms from knowledge entities
    (upserts current ones, prunes graph terms whose entity left the graph)."""
    try:
        from personalclaw.knowledge import get_knowledge_store

        store = get_knowledge_store()
        import json as _json

        entities = []
        for r in store.db.execute("SELECT id, name, entity_type, aliases FROM entities"):
            try:
                aliases = _json.loads(r["aliases"] or "[]")
            except Exception:
                aliases = []
            entities.append(
                {
                    "id": r["id"],
                    "name": r["name"],
                    "entity_type": r["entity_type"],
                    "aliases": aliases,
                }
            )
    except Exception:
        # Don't rebuild against a failed read — the resync now PRUNES absent graph
        # terms, so treating a read failure as "no entities" would wipe them all.
        logger.warning("lexicon rebuild: could not read entities", exc_info=True)
        return web.json_response({"error": "could not read knowledge entities"}, status=500)
    svc = get_lexicon_service()
    n = svc.rebuild_from_graph(entities)
    return web.json_response({"ok": True, "synced": n, "total": svc.store.count_terms()})


async def api_lexicon_corrections(request: web.Request) -> web.Response:
    """GET /api/lexicon/corrections — list learned corrections (most-corrected first)."""
    svc = get_lexicon_service()
    return web.json_response({"corrections": [_corr_dict(c) for c in svc.list_corrections()]})


async def api_lexicon_add_correction(request: web.Request) -> web.Response:
    """POST /api/lexicon/corrections {heard, meant, always?} — record a learned fix
    (LEX.5). Called by the Vocabulary UI + the Minutes transcript-edit flow."""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    heard = (body.get("heard") or "").strip()
    meant = (body.get("meant") or "").strip()
    if not heard or not meant:
        return web.json_response({"error": "heard and meant are required"}, status=400)
    svc = get_lexicon_service()
    svc.learn_correction(heard, meant, always=bool(body.get("always")))
    return web.json_response({"ok": True})


async def api_lexicon_update_correction(request: web.Request) -> web.Response:
    """PATCH /api/lexicon/corrections/{id} {auto_apply} — toggle 'always fix this'."""
    corr_id = request.match_info["id"]
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    svc = get_lexicon_service()
    if "auto_apply" in body:
        if not svc.store.set_correction_auto_apply(corr_id, bool(body["auto_apply"])):
            return web.json_response({"error": "correction not found"}, status=404)
    return web.json_response({"ok": True})


async def api_lexicon_reset(request: web.Request) -> web.Response:
    """POST /api/lexicon/reset — drop all terms + corrections (rebuild repopulates graph)."""
    svc = get_lexicon_service()
    svc.store.reset()
    return web.json_response({"ok": True})


def register_lexicon_routes(app: web.Application) -> None:
    """Register /api/lexicon/* — the Vocabulary panel + Minutes correction seam."""
    app.router.add_get("/api/lexicon/terms", api_lexicon_terms)
    app.router.add_post("/api/lexicon/terms", api_lexicon_add_term)
    app.router.add_patch("/api/lexicon/terms/{id}", api_lexicon_update_term)
    app.router.add_delete("/api/lexicon/terms/{id}", api_lexicon_delete_term)
    app.router.add_post("/api/lexicon/rebuild", api_lexicon_rebuild)
    app.router.add_get("/api/lexicon/corrections", api_lexicon_corrections)
    app.router.add_post("/api/lexicon/corrections", api_lexicon_add_correction)
    app.router.add_patch("/api/lexicon/corrections/{id}", api_lexicon_update_correction)
    app.router.add_post("/api/lexicon/reset", api_lexicon_reset)

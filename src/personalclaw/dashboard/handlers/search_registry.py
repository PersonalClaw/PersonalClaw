"""Search entity registry routes — the API behind Settings → Search.

Mirrors the Model registry (``model_registry.py``) for the Search entity: list the
registered search providers (with disclosed capabilities + live availability), read
the per-use-case bindings, and set which provider serves a use-case.

    GET  /api/search/providers          — registered search providers + capabilities
    GET  /api/search/active             — bound provider per use-case
    PUT  /api/search/active/{use_case}  — bind a provider to a use-case

Provider *configuration* (endpoint/api-key) is the generic extension flow
(``/api/providers`` + ``/api/providers/{name}/config``); this module owns only the
search-specific binding + capability surface.
"""

import asyncio
import logging

from aiohttp import web

from personalclaw.search_providers.registry import list_providers
from personalclaw.search_providers.use_cases import (
    SEARCH_USE_CASES,
    VALID_SEARCH_USE_CASES,
    load_active_search_providers,
    set_active_search_provider,
)

logger = logging.getLogger(__name__)


def _sel_log(op: str, outcome: str, resources: str, request: web.Request, error: str = "") -> None:
    """Audit a search-binding mutation (#45). Best-effort."""
    try:
        from personalclaw.sel import sel as _s

        _s().log_api_access(
            caller=request.get("user", "dashboard"),
            operation=op,
            outcome=outcome,
            source="search",
            resources=resources,
            error=error,
        )
    except Exception:
        pass


async def api_search_providers(request: web.Request) -> web.Response:
    """GET /api/search/providers — registered providers + capabilities + availability.

    Returns ``{providers: [{name, display_name, capabilities{}, available}]}``.
    Availability is probed concurrently (credential/endpoint resolution) so the
    panel can show which providers are ready to bind.
    """
    providers = list_providers()

    async def _probe(p):
        try:
            return await p.is_available()
        except Exception:
            logger.debug("search provider %r availability probe failed", p.name, exc_info=True)
            return False

    avail = await asyncio.gather(*[_probe(p) for p in providers]) if providers else []
    result = [
        {
            "name": p.name,
            "display_name": p.display_name,
            "capabilities": p.capabilities().to_dict(),
            "available": bool(a),
        }
        for p, a in zip(providers, avail)
    ]
    return web.json_response({"providers": result})


async def api_search_active(request: web.Request) -> web.Response:
    """GET /api/search/active — bound provider name per use-case.

    Returns ``{use_cases: {search-general: [name], search-news: [...], ...}}`` — a
    list per use-case for shape-parity with the Models endpoint, though search is
    single-active per use-case.
    """
    active = load_active_search_providers()
    normalized = {uc: active.get(uc, []) for uc in SEARCH_USE_CASES}
    return web.json_response({"use_cases": normalized})


async def api_search_active_set(request: web.Request) -> web.Response:
    """PUT /api/search/active/{use_case} — bind a provider to a use-case.

    Body: ``{providers: ["<provider_name>"]}`` (single-active; an empty list clears
    the binding so the use-case falls back to the general binding).
    """
    use_case = request.match_info["use_case"]
    if use_case not in VALID_SEARCH_USE_CASES:
        return web.json_response(
            {"error": f"Invalid search use case: {use_case!r}; valid: {list(SEARCH_USE_CASES)}"},
            status=400,
        )

    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON body"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)

    providers = body.get("providers", [])
    if not isinstance(providers, list):
        return web.json_response({"error": "providers must be a list"}, status=400)
    if len(providers) > 1:
        return web.json_response(
            {"error": f"Search use case {use_case!r} supports one active provider"},
            status=400,
        )

    # Reject a binding to a provider that isn't registered — fail-fast rather than
    # silently stranding the use-case on a dead provider name (same footgun as the
    # model active-model setter, bug #16). An empty list (clear → fall back to the
    # general binding) is always allowed.
    if providers:
        name = str(providers[0])
        known = {p.name for p in list_providers()}
        if name not in known:
            _sel_log(
                "search.active_set",
                "error",
                f"{use_case}:{name}",
                request,
                error=f"unknown search provider {name!r}",
            )
            return web.json_response(
                {
                    "error": f"Unknown search provider {name!r}. Install/enable it first, or pick a "  # noqa: E501
                    f"registered one. Known: {sorted(known)}"
                },
                status=400,
            )

    set_active_search_provider(use_case, str(providers[0]) if providers else "")
    bound = load_active_search_providers().get(use_case, [])
    # Audit the search-binding change (#45).
    _sel_log("search.active_set", "ok", f"{use_case}={','.join(bound) or '(cleared)'}", request)
    return web.json_response(
        {
            "ok": True,
            "use_case": use_case,
            "providers": bound,
        }
    )


def register_search_registry_routes(app: web.Application) -> None:
    """Register Search entity registry routes."""
    app.router.add_get("/api/search/providers", api_search_providers)
    app.router.add_get("/api/search/active", api_search_active)
    app.router.add_put("/api/search/active/{use_case}", api_search_active_set)

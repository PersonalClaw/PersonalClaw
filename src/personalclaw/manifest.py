"""Self-description manifest — one JSON document describing PersonalClaw's own
tool, route, and provider surface, GENERATED from the live registries that own
each part (never a parallel hand-maintained inventory).

The point of a generated manifest is drift: an agent that builds on or drives
PersonalClaw reads this instead of guessing signatures, and a tool/route added
without a description **fails the drift test** (:mod:`tests.test_api_manifest_drift`)
rather than becoming a silent dead path (the manifest-vs-UI audit, made a build
step). The two facts the registries don't carry — a response-type discriminator
and one/two examples per tool — live in :mod:`personalclaw.manifest_meta`, the
one hand-maintained map the drift test audits.

Two renderings, one source: the gateway serves this at ``GET /api/manifest``
(walking the live aiohttp route table), and the build-time offline reference
(S3) renders the same ``build_manifest()`` output — the CLI-as-truth rule.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from personalclaw.api_version import API_VERSION
from personalclaw.manifest_meta import TOOL_META, is_excluded_route

if TYPE_CHECKING:
    from aiohttp import web

logger = logging.getLogger(__name__)

# `API_VERSION` is re-exported from :mod:`personalclaw.api_version`, which is its ONE
# origin: the same number the version gate negotiates against and the SPA declares.
# It used to be defined here as a literal and only ever emitted, so nothing compared
# it. The bump rule — what counts as a breaking wire change — is stated there and
# nowhere else; do not restate it here, and do not re-declare the literal.
__all__ = ["API_VERSION", "build_manifest"]


async def _tools_section() -> list[dict[str, Any]]:
    """``tools[]`` — every registered in-process tool, from the ONE aggregation
    seam (:func:`tool_providers.registry.list_all_tools`, which already unions
    ``personalclaw-core`` (= ``mcp_core``) with the entity providers). Enriched
    with the response-type discriminator + examples from :data:`TOOL_META`.

    The session-coupled PLATFORM provider (filesystem/shell, built per-runtime and
    cwd-bound) is deliberately absent: it is not in the registry, so it is not a
    *registered* tool. The Tools page enumerates it separately; the manifest
    describes the stable registered surface.
    """
    from personalclaw.tool_providers.registry import list_all_tools

    tools = await list_all_tools()
    out: list[dict[str, Any]] = []
    for t in tools:
        # The generic external-MCP fan-in ("mcp" provider) is per-install and not
        # part of the stable self-description — it varies by the user's mcp.json.
        if t.provider == "mcp":
            continue
        meta = TOOL_META.get(t.name, {})
        out.append(
            {
                "name": t.name,
                "provider": t.provider,
                "description": t.description,
                "parameters": t.parameters,
                "requires_approval": t.requires_approval,
                "risk_level": getattr(t.risk_level, "value", t.risk_level) or "safe",
                "response_type": meta.get("response_type", ""),
                "error_codes": list(meta.get("error_codes", ())),
                "examples": list(meta.get("examples", ())),
            }
        )
    out.sort(key=lambda d: (d["provider"], d["name"]))
    return out


def _routes_section(app: "web.Application | None") -> list[dict[str, Any]]:
    """``routes[]`` — the live aiohttp route table walked at request time.

    aiohttp auto-adds a HEAD companion for every GET and mounts static resources
    with a synthetic ``_handle``; both are filtered. Everything else must be in
    the manifest or in :data:`MANIFEST_EXCLUDE` — the drift test asserts it.
    ``app_callable`` marks a route an agent may drive (``/api/*``, non-excluded);
    the flag is advisory metadata, not an authorization gate.
    """
    if app is None:
        return []
    seen: set[tuple[str, str]] = set()
    out: list[dict[str, Any]] = []
    for route in app.router.routes():
        method = route.method
        if method == "HEAD":  # auto-companion of the GET route
            continue
        resource = route.resource
        path = getattr(resource, "canonical", None)
        if not path:
            continue
        handler = route.handler
        name = getattr(handler, "__name__", "")
        # Static file mounts register a synthetic bound `_handle`; not an API route.
        if name == "_handle":
            continue
        key = (method, path)
        if key in seen:
            continue
        seen.add(key)
        if is_excluded_route(method, path):
            continue
        doc = (handler.__doc__ or "").strip()
        summary = doc.splitlines()[0].strip() if doc else ""
        out.append(
            {
                "method": method,
                "path": path,
                "summary": summary,
                "agent_callable": path.startswith("/api/") and not path.startswith("/api/ws"),
            }
        )
    out.sort(key=lambda d: (d["path"], d["method"]))
    return out


def _providers_section() -> dict[str, Any]:
    """``providers{}`` — the extension-provider taxonomy + registered instances,
    from :func:`providers.registry.get_provider_registry` and ``PROVIDER_TYPES``.
    """
    from personalclaw.apps.manifest import PROVIDER_TYPES
    from personalclaw.providers.registry import get_provider_registry

    reg = get_provider_registry()
    registered: list[dict[str, Any]] = []
    for ext in reg.list_extensions():
        cfg = ext.provider_config
        registered.append(
            {
                "app": ext.name,
                "type": cfg.type,
                "provider_type": cfg.providerType,
                "capabilities": list(cfg.capabilities),
                "enabled": bool(ext.enabled),
                "error": ext.error,
            }
        )
    registered.sort(key=lambda d: (d["type"], d["app"], d["provider_type"]))
    return {"types": sorted(PROVIDER_TYPES), "registered": registered}


async def build_manifest(app: "web.Application | None" = None) -> dict[str, Any]:
    """Assemble the full manifest document.

    ``app`` supplies the live route table (the gateway passes ``request.app``);
    omit it for tool/provider-only renderings (the build-time reference resolves
    routes from the AST, not a running app). Each section is generated from the
    registry that owns it — the only hand-maintained inputs are :data:`TOOL_META`
    and :data:`MANIFEST_EXCLUDE`, exactly what the drift test audits.
    """
    return {
        "apiVersion": API_VERSION,
        "tools": await _tools_section(),
        "routes": _routes_section(app),
        # app_surfaces[] — every enabled app's declared route table + the generated
        # tool name for each agent-callable route (§4.2). Same source the
        # AppRoutesToolProvider generates tools from, so the manifest and the live
        # tool surface can't drift from each other.
        "app_surfaces": _app_surfaces_section(),
        "providers": _providers_section(),
    }


def _app_surfaces_section() -> list[dict[str, Any]]:
    """``app_surfaces[]`` — delegated to the module that owns the route→tool
    mapping (:mod:`tool_providers.app_routes`); best-effort so a broken app
    manifest never sinks the whole manifest."""
    try:
        from personalclaw.tool_providers.app_routes import app_surfaces

        return app_surfaces()
    except Exception:
        logger.debug("app_surfaces generation failed", exc_info=True)
        return []

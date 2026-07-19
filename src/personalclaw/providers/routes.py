"""HTTP API routes for the extension system.

Provides endpoints for:
- Listing extensions with status and type filtering
- Reading/writing per-extension config
- Fetching settings schemas for dynamic UI rendering
- Enabling/disabling extensions at runtime
"""

import logging
from typing import Any

from aiohttp import web

from personalclaw.providers.registry import get_provider_registry
from personalclaw.providers.settings import ProviderSettings

logger = logging.getLogger(__name__)


def register_routes(app: web.Application) -> None:
    app.router.add_get("/api/providers", handle_list_extensions)
    app.router.add_get("/api/providers/{name}", handle_get_extension)
    app.router.add_get("/api/providers/{name}/schema", handle_get_schema)
    app.router.add_get("/api/providers/{name}/config", handle_get_config)
    app.router.add_patch("/api/providers/{name}/config", handle_patch_config)
    app.router.add_post("/api/providers/{name}/enable", handle_enable)
    app.router.add_post("/api/providers/{name}/disable", handle_disable)


async def handle_list_extensions(request: web.Request) -> web.Response:
    registry = get_provider_registry()
    type_filter = request.query.get("type")

    extensions = registry.list_extensions()
    if type_filter:
        extensions = [e for e in extensions if e.provider_config.type == type_filter]

    from personalclaw.providers.loader import load_availability

    result: list[dict[str, Any]] = []
    for ext in extensions:
        # A bundle may declare itself unusable on this machine (e.g. its binary
        # isn't installed). Default available=True when no hook is exported.
        available, unavailable_reason = True, ""
        probe = load_availability(ext)
        if probe is not None:
            try:
                available, unavailable_reason = probe()
            except Exception:
                logger.debug("availability() raised for %s", ext.name, exc_info=True)
        result.append({
            "name": ext.name,
            "displayName": ext.manifest.displayName,
            "description": ext.manifest.description,
            "version": ext.manifest.version,
            "author": ext.manifest.author,
            "enabled": ext.enabled,
            "error": ext.error,
            "available": available,
            "unavailableReason": unavailable_reason,
            # A "managed" provider is a user-lifecycle app (first/third-party: install/
            # uninstall is its on/off); a native app is locked-on (no
            # toggle — mandatory). Lets Settings>Providers show the right control:
            # install/uninstall state vs an always-on native badge.
            "managed": not bool(ext.manifest.native),
            "provider": {
                "type": ext.provider_config.type,
                "entity": ext.provider_config.entity,
                "capabilities": ext.provider_config.capabilities,
                "multiInstance": ext.provider_config.multiInstance,
                # True only when the provider declares configurable fields, so the UI
                # can hide the "Configure" expander for schema-less providers (which
                # would otherwise open to an empty form and look broken).
                "hasConfigSchema": bool((ext.provider_config.settingsSchema or {}).get("properties")),
            },
            "tags": ext.manifest.tags,
        })

    # UT6: surface the always-on PLATFORM tool provider (filesystem + shell) so
    # Settings>Providers shows the WHOLE tool universe — it's not a registered
    # extension (it's built per-session in the runtime, being cwd-coupled), so it
    # would otherwise be the one tool provider missing from this list while
    # appearing on the Tools page. Synthesized here as an always-on, non-managed,
    # non-removable card (mirrors how the Tools page marks it 'platform/required').
    if not type_filter or type_filter == "tool":
        result.append({
            "name": "personalclaw-filesystem",
            "displayName": "Filesystem & Shell Tools",
            "description": "The always-on platform tools — read/write/edit/list/glob/grep/repo_map, "
                           "bash, and full-result retrieval. Required by the agent; can't be disabled.",
            "version": "1.0.0",
            "author": "PersonalClaw",
            "enabled": True,
            "error": "",
            "available": True,
            "unavailableReason": "",
            "managed": False,
            "platform": True,  # non-removable, non-disableable platform provider
            "provider": {
                "type": "tool",
                "entity": "tool",
                "capabilities": ["filesystem", "shell"],
                "multiInstance": False,
                "hasConfigSchema": False,
            },
            "tags": ["tool", "bundled", "platform"],
        })

    return web.json_response({"providers": result})


async def handle_get_extension(request: web.Request) -> web.Response:
    name = request.match_info["name"]
    registry = get_provider_registry()
    ext = registry.get(name)
    if not ext:
        return web.json_response({"error": f"Extension {name!r} not found"}, status=404)

    return web.json_response({
        "name": ext.name,
        "displayName": ext.manifest.displayName,
        "description": ext.manifest.description,
        "version": ext.manifest.version,
        "author": ext.manifest.author,
        "enabled": ext.enabled,
        "error": ext.error,
        "provider": ext.provider_config.to_dict(),
        "manifest": ext.manifest.to_dict(),
    })


async def handle_get_schema(request: web.Request) -> web.Response:
    name = request.match_info["name"]
    registry = get_provider_registry()
    ext = registry.get(name)
    if not ext:
        return web.json_response({"error": f"Extension {name!r} not found"}, status=404)

    return web.json_response({
        "name": name,
        "schema": ext.provider_config.settingsSchema,
    })


async def handle_get_config(request: web.Request) -> web.Response:
    name = request.match_info["name"]
    registry = get_provider_registry()
    ext = registry.get(name)
    if not ext:
        return web.json_response({"error": f"Extension {name!r} not found"}, status=404)

    config = ProviderSettings.load(name)
    return web.json_response({"name": name, "config": config})


async def handle_patch_config(request: web.Request) -> web.Response:
    name = request.match_info["name"]
    registry = get_provider_registry()
    ext = registry.get(name)
    if not ext:
        return web.json_response({"error": f"Extension {name!r} not found"}, status=404)

    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON body"}, status=400)

    if not isinstance(body, dict):
        return web.json_response({"error": "Body must be a JSON object"}, status=400)

    schema = ext.provider_config.settingsSchema
    errors = ProviderSettings.validate(body, schema)
    if errors:
        return web.json_response({"error": "Validation failed", "details": errors}, status=422)

    updated = ProviderSettings.update(name, body)

    # A provider instance is built from its config at enable-time and cached in the
    # typed registry; a config change (e.g. a new API key) wouldn't otherwise take
    # effect until restart. Re-cycle an enabled provider so it re-reads the saved
    # config now, and drop the typed media registries' transient adapters so the
    # next resolution rebuilds from current config.
    if ext.enabled:
        registry.disable(name)
        registry.enable(name)
    try:
        from personalclaw.dashboard.handlers.providers import _refresh_media_registries
        _refresh_media_registries()
    except Exception:  # noqa: BLE001 — refresh is best-effort, never block a save
        pass
    return web.json_response({"name": name, "config": updated})


async def handle_enable(request: web.Request) -> web.Response:
    name = request.match_info["name"]
    registry = get_provider_registry()
    ext = registry.get(name)
    if not ext:
        return web.json_response({"error": f"Extension {name!r} not found"}, status=404)

    success = registry.enable(name)
    if not success:
        return web.json_response(
            {"error": f"Failed to enable: {ext.error}"}, status=500
        )

    return web.json_response({"name": name, "enabled": True})


async def handle_disable(request: web.Request) -> web.Response:
    name = request.match_info["name"]
    registry = get_provider_registry()
    ext = registry.get(name)
    if not ext:
        return web.json_response({"error": f"Extension {name!r} not found"}, status=404)

    registry.disable(name)
    return web.json_response({"name": name, "enabled": False})

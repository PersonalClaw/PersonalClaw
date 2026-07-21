"""HTTP API routes for multi-instance extension management and use-case settings.

Endpoints:
  GET    /api/providers/{name}/instances          — list instances
  POST   /api/providers/{name}/instances          — create instance
  GET    /api/providers/{name}/instances/{id}     — get instance
  PUT    /api/providers/{name}/instances/{id}     — update instance
  DELETE /api/providers/{name}/instances/{id}     — delete instance
  POST   /api/providers/{name}/instances/{id}/test — test instance connectivity

  GET    /api/models/use-cases/{use_case}/settings — get per-use-case settings
  PUT    /api/models/use-cases/{use_case}/settings — update per-use-case settings

Which model serves a use case is the active selection in ``active_models.json``
(``/api/models/active`` — see ``dashboard/handlers/model_registry.py``); these
``/settings`` routes carry only provider-agnostic behavior (e.g. auto-speak).
"""

import logging

from aiohttp import web

from personalclaw.providers import mcp_instances as _mcp

logger = logging.getLogger(__name__)


def _rebuild_agent_config_safe() -> None:
    """Best-effort agent-config rebuild after an instance mutation, so the change
    reaches PersonalClaw sessions without a restart. Never raises."""
    try:
        from personalclaw.agent import rebuild_agent_config

        rebuild_agent_config()
    except Exception:
        logger.warning("rebuild_agent_config failed after instance change", exc_info=True)


def _refresh_tool_provider_safe(name: str) -> None:
    """Re-register a generic multiInstance TOOL provider after its instance set
    changed, so newly-added/edited/removed instances become live tool providers
    without a restart. mcp-tools has its own path (live mcp.json registry); this
    covers the OTHER tool apps (e.g. openai-tools) whose ToolTypeHandler.create
    rebuilds one provider per enabled instance. disable→enable re-runs create()
    against the current on-disk instance set (disk = source of truth), then the
    agent config is rebuilt. Best-effort; never raises."""
    try:
        from personalclaw.providers.registry import get_provider_registry

        registry = get_provider_registry()
        ext = registry.get(name)
        if not ext or ext.provider_config.type != "tool" or not ext.provider_config.multiInstance:
            return
        if ext.enabled:
            registry.disable(name)
        registry.enable(name)
        _rebuild_agent_config_safe()
    except Exception:
        logger.warning(
            "tool-provider refresh failed after instance change for %s", name, exc_info=True
        )


def register_instance_routes(app: web.Application) -> None:
    """Register instance CRUD and per-use-case settings routes."""
    # Instance management
    app.router.add_get("/api/providers/{name}/instances", handle_list_instances)
    app.router.add_post("/api/providers/{name}/instances", handle_create_instance)
    app.router.add_get("/api/providers/{name}/instances/{id}", handle_get_instance)
    app.router.add_put("/api/providers/{name}/instances/{id}", handle_update_instance)
    app.router.add_delete("/api/providers/{name}/instances/{id}", handle_delete_instance)
    app.router.add_post("/api/providers/{name}/instances/{id}/test", handle_test_instance)

    # Per-use-case behavior settings (provider-agnostic). The active *model* for
    # a use case is set via /api/models/active (model_registry), not here.
    app.router.add_get("/api/models/use-cases/{use_case}/settings", handle_get_use_case_settings)
    app.router.add_put("/api/models/use-cases/{use_case}/settings", handle_set_use_case_settings)


# ── Instance Management ──────────────────────────────────────────────────────


async def handle_list_instances(request: web.Request) -> web.Response:
    """GET /api/providers/{name}/instances"""
    from personalclaw.providers.instances import list_instances
    from personalclaw.providers.registry import get_provider_registry

    name = request.match_info["name"]
    registry = get_provider_registry()
    ext = registry.get(name)
    if not ext:
        return web.json_response({"error": f"Extension {name!r} not found"}, status=404)
    if not ext.provider_config.multiInstance:
        return web.json_response({"error": f"Extension {name!r} is not multi-instance"}, status=400)

    # The mcp-tools card reads/writes the ONE store the native loop consumes
    # (~/.personalclaw/mcp.json), not the generic instance store.
    if name == _mcp.MCP_TOOLS_EXTENSION:
        return web.json_response({"instances": [i.to_dict() for i in _mcp.list_instances()]})

    instances = list_instances(name)
    return web.json_response(
        {
            "instances": [inst.to_dict() for inst in instances],
        }
    )


async def handle_create_instance(request: web.Request) -> web.Response:
    """POST /api/providers/{name}/instances"""
    from personalclaw.providers.instances import create_instance
    from personalclaw.providers.registry import get_provider_registry
    from personalclaw.providers.settings import ProviderSettings

    name = request.match_info["name"]
    registry = get_provider_registry()
    ext = registry.get(name)
    if not ext:
        return web.json_response({"error": f"Extension {name!r} not found"}, status=404)
    if not ext.provider_config.multiInstance:
        return web.json_response({"error": f"Extension {name!r} is not multi-instance"}, status=400)

    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON body"}, status=400)

    display_name = str(body.get("display_name", "")).strip()
    config = body.get("config", {})
    if not isinstance(config, dict):
        return web.json_response({"error": "config must be a JSON object"}, status=400)

    # Validate against schema
    schema = ext.provider_config.settingsSchema
    errors = ProviderSettings.validate(config, schema)
    if errors:
        return web.json_response({"error": "Validation failed", "details": errors}, status=422)

    if name == _mcp.MCP_TOOLS_EXTENSION:
        try:
            inst = _mcp.create_instance(display_name, config)
        except ValueError as exc:
            return web.json_response({"error": str(exc)}, status=400)
        _rebuild_agent_config_safe()
        return web.json_response({"instance": inst.to_dict()}, status=201)

    inst = create_instance(name, display_name=display_name, config=config)
    _refresh_tool_provider_safe(name)
    return web.json_response({"instance": inst.to_dict()}, status=201)


async def handle_get_instance(request: web.Request) -> web.Response:
    """GET /api/providers/{name}/instances/{id}"""
    from personalclaw.providers.instances import get_instance

    name = request.match_info["name"]
    instance_id = request.match_info["id"]
    if name == _mcp.MCP_TOOLS_EXTENSION:
        inst = _mcp.get_instance(instance_id)
        if not inst:
            return web.json_response({"error": "Instance not found"}, status=404)
        return web.json_response({"instance": inst.to_dict()})
    inst = get_instance(name, instance_id)
    if not inst:
        return web.json_response({"error": "Instance not found"}, status=404)
    return web.json_response({"instance": inst.to_dict()})


async def handle_update_instance(request: web.Request) -> web.Response:
    """PUT /api/providers/{name}/instances/{id}"""
    from personalclaw.providers.instances import update_instance
    from personalclaw.providers.registry import get_provider_registry
    from personalclaw.providers.settings import ProviderSettings

    name = request.match_info["name"]
    instance_id = request.match_info["id"]

    registry = get_provider_registry()
    ext = registry.get(name)
    if not ext:
        return web.json_response({"error": f"Extension {name!r} not found"}, status=404)

    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON body"}, status=400)

    # Validate config if provided
    config = body.get("config")
    if config is not None:
        if not isinstance(config, dict):
            return web.json_response({"error": "config must be a JSON object"}, status=400)
        schema = ext.provider_config.settingsSchema
        errors = ProviderSettings.validate(config, schema)
        if errors:
            return web.json_response({"error": "Validation failed", "details": errors}, status=422)

    if name == _mcp.MCP_TOOLS_EXTENSION:
        inst = _mcp.update_instance(instance_id, config=config, enabled=body.get("enabled"))
        if not inst:
            return web.json_response({"error": "Instance not found"}, status=404)
        _rebuild_agent_config_safe()
        return web.json_response({"instance": inst.to_dict()})

    inst = update_instance(
        name,
        instance_id,
        display_name=body.get("display_name"),
        config=config,
        enabled=body.get("enabled"),
    )
    if not inst:
        return web.json_response({"error": "Instance not found"}, status=404)
    _refresh_tool_provider_safe(name)
    return web.json_response({"instance": inst.to_dict()})


async def handle_delete_instance(request: web.Request) -> web.Response:
    """DELETE /api/providers/{name}/instances/{id}"""
    from personalclaw.providers.instances import delete_instance

    name = request.match_info["name"]
    instance_id = request.match_info["id"]
    if name == _mcp.MCP_TOOLS_EXTENSION:
        if not _mcp.delete_instance(instance_id):
            return web.json_response({"error": "Instance not found"}, status=404)
        _rebuild_agent_config_safe()
        return web.json_response({"ok": True})
    deleted = delete_instance(name, instance_id)
    if not deleted:
        return web.json_response({"error": "Instance not found"}, status=404)
    _refresh_tool_provider_safe(name)
    return web.json_response({"ok": True})


async def handle_test_instance(request: web.Request) -> web.Response:
    """POST /api/providers/{name}/instances/{id}/test — test connectivity."""
    from personalclaw.providers.instances import get_instance
    from personalclaw.providers.registry import get_provider_registry

    name = request.match_info["name"]
    instance_id = request.match_info["id"]

    registry = get_provider_registry()
    ext = registry.get(name)
    if not ext:
        return web.json_response({"error": f"Extension {name!r} not found"}, status=404)

    # mcp-tools instances live in ~/.personalclaw/mcp.json — probe the real
    # server (spawn → initialize → tools/list) for a true connectivity check.
    if name == _mcp.MCP_TOOLS_EXTENSION:
        from personalclaw.mcp_discovery import list_servers, probe_server

        target = next((s for s in list_servers() if s.name == instance_id), None)
        if target is None:
            return web.json_response({"error": "Instance not found"}, status=404)
        try:
            probed = await probe_server(target)
        except Exception as exc:
            return web.json_response({"ok": False, "message": str(exc)[:200]})
        if probed.status in ("ok", "ready", "connected"):
            return web.json_response(
                {"ok": True, "message": f"Connected — {len(probed.tools)} tool(s)"}
            )
        return web.json_response(
            {"ok": False, "message": probed.error or f"Server status: {probed.status}"}
        )

    inst = get_instance(name, instance_id)
    if not inst:
        return web.json_response({"error": "Instance not found"}, status=404)

    # For model-type extensions with an endpoint, do HTTP connectivity check
    endpoint = inst.config.get("endpoint", "")
    if endpoint and ext.provider_config.type == "model":
        return await _test_model_connectivity(endpoint)

    # For other types, try the extension factory
    try:
        from personalclaw.providers.loader import load_factory

        factory = load_factory(ext)
        provider = factory(inst.config)
        if hasattr(provider, "is_available"):

            available = await provider.is_available()
            if available:
                return web.json_response({"ok": True, "message": "Provider available"})
            return web.json_response({"ok": False, "message": "Provider not available"})
        return web.json_response({"ok": True, "message": "Provider created successfully"})
    except Exception as exc:
        return web.json_response({"ok": False, "message": str(exc)[:200]})


async def _test_model_connectivity(endpoint: str) -> web.Response:
    """Test connectivity to a model endpoint (Ollama, vLLM, etc.)."""
    import aiohttp

    endpoint = endpoint.rstrip("/")
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{endpoint}/api/tags",
                timeout=aiohttp.ClientTimeout(total=10),
            ) as r:
                if r.status == 200:
                    data = await r.json()
                    models = data.get("models", [])
                    return web.json_response(
                        {
                            "ok": True,
                            "message": f"Connected — {len(models)} model(s) available",
                        }
                    )
                return web.json_response(
                    {
                        "ok": False,
                        "message": f"Endpoint returned HTTP {r.status}",
                    }
                )
    except aiohttp.ClientConnectorError:
        return web.json_response(
            {
                "ok": False,
                "message": f"Cannot connect to {endpoint}",
            }
        )
    except Exception as exc:
        return web.json_response({"ok": False, "message": str(exc)[:200]})


# ── Per-Use-Case Settings (provider-agnostic behavior) ───────────────────────


async def handle_get_use_case_settings(request: web.Request) -> web.Response:
    """GET /api/models/use-cases/{use_case}/settings"""
    from personalclaw.providers.use_cases import VALID_USE_CASES, load_use_case_settings

    use_case = request.match_info["use_case"]
    if use_case not in VALID_USE_CASES:
        return web.json_response({"error": f"Invalid use case: {use_case!r}"}, status=400)

    settings = load_use_case_settings(use_case)
    return web.json_response({"use_case": use_case, "settings": settings})


async def handle_set_use_case_settings(request: web.Request) -> web.Response:
    """PUT /api/models/use-cases/{use_case}/settings"""
    from personalclaw.providers.use_cases import VALID_USE_CASES, save_use_case_settings

    use_case = request.match_info["use_case"]
    if use_case not in VALID_USE_CASES:
        return web.json_response({"error": f"Invalid use case: {use_case!r}"}, status=400)

    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON body"}, status=400)

    if not isinstance(body, dict):
        return web.json_response({"error": "Body must be a JSON object"}, status=400)

    save_use_case_settings(use_case, body)
    return web.json_response({"ok": True, "use_case": use_case, "settings": body})

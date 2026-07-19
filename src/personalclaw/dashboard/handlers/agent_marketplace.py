"""Agent marketplace API handlers.

Exposes a CRUD surface for user-authored agent definitions backed by the
``AgentMarketplaceRegistry`` from ``agents/marketplace.py``.  The default
marketplace is ``"local"`` (``~/.personalclaw/agents/``).  Additional marketplaces
can be registered at process start and become available under the same routes
via the ``?marketplace=<name>`` query parameter.

Routes (all registered in ``dashboard/server.py``):

    GET    /api/agent-marketplace/marketplaces
    GET    /api/agent-marketplace/agents
    GET    /api/agent-marketplace/agents/:name
    POST   /api/agent-marketplace/agents
    PUT    /api/agent-marketplace/agents/:name
    DELETE /api/agent-marketplace/agents/:name
    POST   /api/agent-marketplace/agents/:name/activate
    POST   /api/agent-marketplace/agents/:name/test
"""

import logging
from typing import Any

from aiohttp import web

from personalclaw.agents.marketplace import AgentDefinition, get_default_agent_registry
from personalclaw.sel import sel as _sel_fn

logger = logging.getLogger(__name__)

_DEFAULT_MARKETPLACE = "local"


def _marketplace(request: web.Request):
    """Resolve the marketplace from the ``?marketplace=`` query param (default: local)."""
    name = request.rel_url.query.get("marketplace", _DEFAULT_MARKETPLACE)
    try:
        return get_default_agent_registry().get(name)
    except KeyError:
        raise web.HTTPNotFound(reason=f"Marketplace '{name}' not registered")


def _sel_log(operation: str, outcome: str, resources: str, request: web.Request) -> None:
    try:
        _sel_fn().log_api_access(
            caller=request.get("user", "dashboard"),
            operation=operation,
            outcome=outcome,
            source="agent_marketplace",
            resources=resources,
        )
    except Exception:
        logger.debug("SEL log failed for %s", operation, exc_info=True)


# ── Marketplace list ──────────────────────────────────────────────────────────


async def api_agent_marketplace_list_marketplaces(request: web.Request) -> web.Response:
    """GET /api/agent-marketplace/marketplaces — list registered marketplaces."""
    return web.json_response(get_default_agent_registry().info())


# ── Agent CRUD ────────────────────────────────────────────────────────────────


async def api_agent_marketplace_list(request: web.Request) -> web.Response:
    """GET /api/agent-marketplace/agents — list agents from a marketplace."""
    mp = _marketplace(request)
    agents = [a.to_dict() for a in mp.list()]
    return web.json_response({"agents": agents, "marketplace": request.rel_url.query.get("marketplace", _DEFAULT_MARKETPLACE)})


async def api_agent_marketplace_get(request: web.Request) -> web.Response:
    """GET /api/agent-marketplace/agents/:name — get one agent definition."""
    name = request.match_info["name"]
    mp = _marketplace(request)
    agent = mp.get(name)
    if agent is None:
        return web.json_response({"error": f"Agent '{name}' not found"}, status=404)
    return web.json_response(agent.to_dict())


async def api_agent_marketplace_create(request: web.Request) -> web.Response:
    """POST /api/agent-marketplace/agents — create a new agent definition."""
    try:
        body: dict[str, Any] = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)

    name = str(body.get("name", "")).strip()
    if not name:
        return web.json_response({"error": "name is required"}, status=400)

    marketplace_name = body.pop("marketplace", _DEFAULT_MARKETPLACE)
    try:
        mp = get_default_agent_registry().get(str(marketplace_name))
    except KeyError:
        return web.json_response({"error": f"Marketplace '{marketplace_name}' not registered"}, status=400)

    defn = AgentDefinition(
        name=name,
        description=str(body.get("description", "")),
        model=str(body.get("model", "")),
        system_prompt=str(body.get("system_prompt", "")),
        skills=list(body.get("skills") or []),
        provider_entry=str(body.get("provider_entry", "")),
        provider=str(body.get("provider", "")),
        mcp_servers=dict(body.get("mcp_servers") or {}),
        source="local",
    )
    try:
        created = mp.create(defn)
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    except FileExistsError:
        return web.json_response({"error": f"Agent '{name}' already exists"}, status=409)

    _sel_log("agent_marketplace.create", "ok", name, request)
    return web.json_response(created.to_dict(), status=201)


async def api_agent_marketplace_update(request: web.Request) -> web.Response:
    """PUT /api/agent-marketplace/agents/:name — update agent fields (partial)."""
    name = request.match_info["name"]
    try:
        body: dict[str, Any] = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)

    mp = _marketplace(request)
    try:
        updated = mp.update(name, body)
    except KeyError:
        return web.json_response({"error": f"Agent '{name}' not found"}, status=404)
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)

    _sel_log("agent_marketplace.update", "ok", name, request)
    return web.json_response(updated.to_dict())


async def api_agent_marketplace_delete(request: web.Request) -> web.Response:
    """DELETE /api/agent-marketplace/agents/:name — delete an agent definition."""
    name = request.match_info["name"]
    mp = _marketplace(request)
    try:
        mp.delete(name)
    except KeyError:
        return web.json_response({"error": f"Agent '{name}' not found"}, status=404)

    _sel_log("agent_marketplace.delete", "ok", name, request)
    return web.json_response({"ok": True})


# ── Activate ──────────────────────────────────────────────────────────────────


async def api_agent_marketplace_activate(request: web.Request) -> web.Response:
    """POST /api/agent-marketplace/agents/:name/activate

    Promotes the agent definition into ``config.json``'s ``agents`` map so it
    becomes selectable in the chat UI as a named persona.  The agent's
    ``system_prompt`` is written to ``~/.personalclaw/agents/<name>/prompt.md``
    and the agent entry's ``provider_agent`` field is left empty (meaning the chat
    runner uses the default provider with a session-scoped system prompt).
    """
    name = request.match_info["name"]
    mp = _marketplace(request)
    defn = mp.get(name)
    if defn is None:
        return web.json_response({"error": f"Agent '{name}' not found"}, status=404)

    from personalclaw.config.loader import AgentProfile, AppConfig, config_dir

    # Write system prompt to disk so the agent session can load it via file://
    agent_dir = config_dir() / "agents" / name
    agent_dir.mkdir(parents=True, exist_ok=True)
    prompt_path = agent_dir / "prompt.md"
    if defn.system_prompt:
        prompt_path.write_text(defn.system_prompt, encoding="utf-8")
    elif prompt_path.exists():
        prompt_path.unlink()

    # Persist into config.json agents map
    cfg = AppConfig.load()
    cfg.agents[name] = AgentProfile(
        provider_agent="",
        description=defn.description,
        model=defn.model,
        source="local",
    )
    cfg.save()

    # Re-install agent config so the prompt file is picked up by the ACP provider
    try:
        from personalclaw.agent import rebuild_agent_config
        rebuild_agent_config()
    except Exception as exc:
        logger.warning("rebuild_agent_config failed after activate: %s", exc)

    _sel_log("agent_marketplace.activate", "ok", name, request)
    state = request.app.get("state")
    if state:
        try:
            state.push_refresh("agents")
        except Exception:
            pass

    return web.json_response({"ok": True, "name": name, "prompt_path": str(prompt_path) if defn.system_prompt else ""})


# ── Test ──────────────────────────────────────────────────────────────────────


async def api_agent_marketplace_test(request: web.Request) -> web.Response:
    """POST /api/agent-marketplace/agents/:name/test

    Runs a one-turn test chat using the agent definition.  The request body
    may contain ``{"prompt": "..."}`` to override the default test prompt.
    Returns ``{ok, response, elapsed_ms}``.

    The test uses the default provider from the running gateway.  If the
    gateway has no active session manager, returns 503.
    """
    name = request.match_info["name"]
    mp = _marketplace(request)
    defn = mp.get(name)
    if defn is None:
        return web.json_response({"error": f"Agent '{name}' not found"}, status=404)

    try:
        body: dict[str, Any] = await request.json()
    except Exception:
        body = {}

    test_prompt = str(body.get("prompt", "")).strip() or "Hello! Please introduce yourself in one sentence."

    state = request.app.get("state")
    if state is None or state.sessions is None:
        return web.json_response({"error": "No active session manager; start the gateway first"}, status=503)

    import time
    start = time.monotonic()

    # Build a combined prompt: inject system prompt as a preamble if defined
    full_prompt = test_prompt
    if defn.system_prompt:
        full_prompt = (
            f"<system>\n{defn.system_prompt[:2000]}\n</system>\n\n{test_prompt}"
        )

    try:
        from personalclaw.llm_helpers import ToolApprovalPolicy, stream_and_collect

        session_key = f"agent_marketplace_test:{name}"
        client, _is_new, _resumed = await state.sessions.get_or_create(
            session_key,
            agent=defn.provider_entry or None,
        )
        try:
            response = await stream_and_collect(
                client,
                full_prompt,
                approval_policy=ToolApprovalPolicy.REJECT_ALL,
            )
        finally:
            state.sessions.release(session_key)
    except Exception as exc:
        logger.warning("Agent test failed for %s: %s", name, exc)
        return web.json_response({"error": str(exc)[:500]}, status=500)

    elapsed_ms = int((time.monotonic() - start) * 1000)
    _sel_log("agent_marketplace.test", "ok", f"{name} ({elapsed_ms}ms)", request)
    return web.json_response({"ok": True, "response": response[:4000], "elapsed_ms": elapsed_ms})

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
import uuid
from typing import Any

from aiohttp import web

from personalclaw import session_keys
from personalclaw.agents.instructions import AgentInstructions
from personalclaw.agents.marketplace import AgentDefinition, get_default_agent_registry
from personalclaw.providers.failure_copy import relayed_failure_copy
from personalclaw.request_validation import json_object_body, require_string, string_field
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
    return web.json_response(
        {
            "agents": agents,
            "marketplace": request.rel_url.query.get("marketplace", _DEFAULT_MARKETPLACE),
        }
    )


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

    name = require_string(body, "name")

    marketplace_name = body.pop("marketplace", _DEFAULT_MARKETPLACE)
    try:
        mp = get_default_agent_registry().get(str(marketplace_name))
    except KeyError:
        return web.json_response(
            {"error": f"Marketplace '{marketplace_name}' not registered"}, status=400
        )

    defn = AgentDefinition(
        name=name,
        description=string_field(body, "description"),
        model=string_field(body, "model"),
        system_prompt=string_field(body, "system_prompt", strip=False),
        voice=string_field(body, "voice", strip=False),
        skills=list(body.get("skills") or []),
        provider_entry=string_field(body, "provider_entry"),
        provider=string_field(body, "provider"),
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
    becomes selectable in the chat UI as a named persona, on the default provider
    (``provider_agent`` empty). The definition's instructions and voice go into the
    profile, which is where every path that runs the agent reads them from
    (``agents.instructions.agent_instructions``) and what the Agents page shows: they
    used to be written to a ``prompt.md`` beside the definition that nothing read, so
    an agent made this way ran on no instructions of its own anywhere.
    """
    name = request.match_info["name"]
    mp = _marketplace(request)
    defn = mp.get(name)
    if defn is None:
        return web.json_response({"error": f"Agent '{name}' not found"}, status=404)

    from personalclaw.config.loader import AgentProfile, AppConfig

    cfg = AppConfig.load()
    cfg.agents[name] = AgentProfile(
        provider_agent="",
        description=defn.description,
        system_prompt=defn.system_prompt,
        voice=defn.voice,
        model=defn.model,
        # Natural voice must cross into the config profile here or an agent
        # activated from the marketplace loses the preference its definition carries —
        # the field exists so it TRAVELS with the agent.
        natural_voice=defn.natural_voice,
        source="local",
    )
    cfg.save()

    _sel_log("agent_marketplace.activate", "ok", name, request)
    state = request.app.get("state")
    if state:
        try:
            state.push_refresh("agents")
        except Exception:
            pass

    return web.json_response({"ok": True, "name": name})


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

    body: dict[str, Any] = await json_object_body(request)

    test_prompt = (
        str(body.get("prompt", "")).strip() or "Hello! Please introduce yourself in one sentence."
    )

    state = request.app.get("state")
    if state is None or state.sessions is None:
        return web.json_response(
            {"error": "No active session manager; start the gateway first"}, status=503
        )

    import time

    start = time.monotonic()

    # The definition's instructions and voice as a preamble, whole, as the agent activated from it
    # is handed them: cut to 2,000 characters with its voice left out, a test of a longer
    # definition answered from words an activated agent never runs on.
    own = AgentInstructions(defn.system_prompt, defn.voice).composed()
    full_prompt = f"<system>\n{own}\n</system>\n\n{test_prompt}" if own else test_prompt

    try:
        from personalclaw.llm_helpers import ToolApprovalPolicy, stream_and_collect
        from personalclaw.usage_ledger import Attribution, recorder

        # A one-turn chat: a session of this test's own, ended with its reply, so a test is sent
        # its prompt and nothing of an earlier test of the same agent.
        session_key = session_keys.AGENT_TEST.key(f"{name}:{uuid.uuid4().hex}")
        acquired = False
        try:
            client, _is_new, _resumed = await state.sessions.get_or_create(
                session_key,
                agent=defn.provider_entry or None,
            )
            acquired = True
            # You asked for it and read the answer: a turn of yours with the agent under test.
            who = Attribution(
                source="chat", session_key=f"agent_marketplace_test:{name}", agent=name
            )
            response = await stream_and_collect(
                client,
                full_prompt,
                approval_policy=ToolApprovalPolicy.REJECT_ALL,
                on_complete=recorder(client, who),
            )
        finally:
            if acquired:
                state.sessions.release(session_key)
            await state.sessions.reset(session_key)
    except Exception as exc:
        logger.warning("Agent test failed for %s: %s", name, exc)
        return web.json_response({"error": relayed_failure_copy(exc)}, status=500)

    elapsed_ms = int((time.monotonic() - start) * 1000)
    _sel_log("agent_marketplace.test", "ok", f"{name} ({elapsed_ms}ms)", request)
    return web.json_response({"ok": True, "response": response[:4000], "elapsed_ms": elapsed_ms})

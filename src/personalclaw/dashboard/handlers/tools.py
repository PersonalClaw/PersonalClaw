"""Aggregated tools listing + invocation endpoints — surface and run tools
from all registered tool providers (the Tool entity)."""

import asyncio
import logging

from aiohttp import web

from personalclaw.security import redact_credentials, redact_exfiltration_urls

logger = logging.getLogger(__name__)

# Per-server budget for enumerating MCP tools in the catalog. The live client's
# own connect timeout is long (built for actual tool calls); the catalog must
# stay responsive, so a slow/dead server is skipped this round and surfaces on a
# later poll once its background connection warms up.
_MCP_LIST_TIMEOUT_SECS = 5.0


def _sel():
    """Late-binding sel() — allows monkeypatching at parent package level."""
    import personalclaw.dashboard.handlers as _pkg
    return _pkg.sel()


def _audit_toggle(request: web.Request, op: str, ok: bool, resources: str, error: str = "") -> None:
    """Audit a tool/provider enable-disable toggle (#45) — it changes the agent's
    available capability surface, a security-relevant state change. Best-effort."""
    try:
        _sel().log_api_access(
            caller=request.get("user", "dashboard"), operation=op,
            outcome="ok" if ok else "error", source="tools",
            resources=resources, error=error,
        )
    except Exception:
        pass


async def api_tools_list(request: web.Request) -> web.Response:
    """GET /api/tools — Return all tools from all active tool sources.

    There are exactly three sources, and they don't overlap:
    1. The session-coupled PLATFORM provider (filesystem + shell + tool_result_get).
       It's built per-runtime (cwd-bound) so it is NOT in the registry — enumerated
       directly here.
    2. The tool-provider REGISTRY (``list_all_tools``) — every registered in-process
       provider, which already includes ``personalclaw-core``/``personalclaw-schedule``
       (registered via their bundled app.json → the same ``InProcessMcpToolProvider``
       the native loop uses) plus the entity categories (memory/artifacts/…). The
       generic ``mcp`` provider is skipped here — source 3 emits external MCP tools
       labeled per-server.
    3. External MCP servers from the LIVE in-process client registry — probed, not
       static, so it can't be a registry read.

    Tools are deduplicated by ``(provider, name)`` pair (defence-in-depth; the three
    sources are already disjoint by construction).
    """
    from personalclaw.tool_providers.registry import (
        clear_load_failures,
        get_load_failures,
        list_all_tools,
        record_failure,
    )

    # Fresh failure list per catalog build — surfaces broken sources rather than
    # leaving the operator to guess why a tool is missing (project_native_mcp_gap).
    clear_load_failures()

    from personalclaw.tool_providers import tool_prefs

    disabled_keys = tool_prefs.load_disabled()
    disabled_provs = tool_prefs.load_disabled_providers()

    tools_out: list[dict] = []
    seen: set[tuple[str, str]] = set()

    def _add(name: str, description: str, provider: str, parameters: dict, requires_approval: bool = True, risk_level: object = "safe") -> None:
        key = (provider, name)
        if key in seen or not name:
            return
        seen.add(key)
        locked = tool_prefs.is_locked(name)
        prov_off = provider in disabled_provs
        tools_out.append({
            "name": name,
            "description": description,
            "provider": provider,
            "parameters": parameters,
            "requires_approval": requires_approval,
            # Declared risk gradient (safe|caution|destructive) — a user-facing
            # indicator on the Tools page; the approval gate resolves per-invocation
            # effective risk from it. External tools with no declaration read 'safe'
            # here (static default); the gate treats unclassified non-reads as caution.
            "risk_level": getattr(risk_level, "value", risk_level) or "safe",
            # PT3/UT4: user enable/disable state. A locked tool is always enabled and
            # not user-toggleable. `disabled` is true if the tool is off individually
            # OR its whole provider is off; `providerDisabled` distinguishes the two
            # so the UI can show "off because the provider is off".
            "locked": locked,
            "providerDisabled": prov_off,
            "disabled": (not locked) and (prov_off or tool_prefs.key_for(provider, name) in disabled_keys),
        })

    # Source 1: the always-on PLATFORM tool provider (filesystem + shell + the
    # tool_result_get affordance). This is built per-session in the runtime (it's
    # cwd-coupled), so it is NOT in the registry — enumerate it directly here so the
    # Tools page shows the agent's foundational capabilities. The session-coupled native
    # categories (knowledge/tasks/loops/inbox) are bundled-app providers and come
    # from the registry (Source 2) under their own provider names — listing the
    # platform slice ONLY here is what stops them double-appearing under "builtin".
    try:
        from personalclaw.agents.native.builtin_tools import create_platform_tools_provider
        _platform = create_platform_tools_provider()
        for t in await _platform.list_tools():
            _add(t.name, t.description, getattr(t, "provider", "") or _platform.name,
                 t.parameters, getattr(t, "requires_approval", True), getattr(t, "risk_level", "safe"))
    except Exception as exc:
        logger.warning("Failed to enumerate native platform tools", exc_info=True)
        record_failure("personalclaw-filesystem", str(exc))

    # Source 2: the tool-provider REGISTRY — every registered in-process provider.
    # This already includes personalclaw-core + personalclaw-schedule (registered via
    # their bundled app.json as InProcessMcpToolProvider, which applies the same
    # infer_risk_from_name classification) plus the entity categories, so there is no
    # separate hardcoded core/schedule enumeration. Skip the generic "mcp" provider —
    # Source 3 emits external MCP tools labeled per-server; re-adding them here under
    # provider="mcp" would produce a phantom duplicate group (the _add dedup keys on
    # provider).
    try:
        registry_tools = await list_all_tools()
        for t in registry_tools:
            if t.provider == "mcp":
                continue
            _add(t.name, t.description, t.provider, t.parameters, t.requires_approval, getattr(t, "risk_level", "safe"))
    except Exception as exc:
        logger.warning("Failed to list tools from registry", exc_info=True)
        record_failure("tool-registry", str(exc))

    # Dict-defined external MCP tools declare no risk_level, so infer a declared risk
    # from the tool name for the Tools-page indicator — matching what the MCP adapter
    # feeds the approval gate. Read tools stay safe.
    from personalclaw.task_modes import infer_risk_from_name

    # Source 3: External MCP servers from the LIVE in-process client registry —
    # exactly the tools the native loop can actually call (no catalog/loop
    # divergence). Empty when the optional 'mcp' SDK isn't installed.
    #
    # Servers are probed CONCURRENTLY with a short per-server timeout: one slow
    # or unreachable server must not stall the whole catalog (and with it the
    # Tools page), which it would under a sequential await across the registry.
    try:
        from personalclaw.mcp_client import get_mcp_client_registry

        registry = get_mcp_client_registry()
        if registry is not None:
            conns = list(registry.items())

            async def _list_one(name: str, conn) -> tuple[str, list]:
                try:
                    tools = await asyncio.wait_for(conn.list_tools(), timeout=_MCP_LIST_TIMEOUT_SECS)
                    return name, list(tools)
                except (asyncio.TimeoutError, Exception):  # noqa: BLE001
                    logger.debug("MCP server '%s' tool listing skipped (slow/unreachable)", name, exc_info=True)
                    return name, []

            results = await asyncio.gather(*(_list_one(n, c) for n, c in conns))
            for server_name, tools in results:
                for tool in tools:
                    _add(
                        f"mcp/{server_name}/{tool.name}",
                        tool.description,
                        server_name,
                        tool.input_schema,
                        risk_level=infer_risk_from_name(tool.name),
                    )
    except Exception as exc:
        logger.warning("Failed to list tools from MCP client registry", exc_info=True)
        record_failure("mcp", str(exc))

    # Surface load failures so a broken provider is operator-visible on the Tools
    # page instead of silently contributing zero tools (per-provider failures are
    # recorded inside list_all_tools; catalog-source failures are recorded above).
    return web.json_response({"tools": tools_out, "load_failures": get_load_failures()})


async def api_tool_invoke(request: web.Request) -> web.Response:
    """POST /api/tools/invoke — execute one tool through the Tool entity.

    Internal-only (loopback + X-Internal-Secret): used by zero-token cron
    scripts so a sandboxed subprocess gets the same MCP+native tool surface
    the agent has, without importing the in-process registry. Body:
    ``{"tool": str, "arguments": dict, "provider"?: str}``. Returns
    ``{ok, output, error}``.
    """
    from personalclaw.tool_providers.registry import get_provider, list_providers

    try:
        body = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "invalid JSON body"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"ok": False, "error": "body must be a JSON object"}, status=400)
    tool_name = body.get("tool")
    if not isinstance(tool_name, str) or not tool_name:
        return web.json_response({"ok": False, "error": "tool name required"}, status=400)
    arguments = body.get("arguments") or {}
    if not isinstance(arguments, dict):
        return web.json_response({"ok": False, "error": "arguments must be an object"}, status=400)

    # Untrusted-app sandbox (P3): an app-identified caller may invoke a tool only if
    # it declares it in permissions.mcpTools. Owner/internal callers (no app identity)
    # are unaffected. This gates the direct /api/tools/invoke path an app backend uses.
    app_name = request.get("app", "")
    if app_name:
        from personalclaw.apps.permissions import checker_for
        checker = checker_for(app_name)
        if checker is None or not checker.can_use_mcp_tool(str(body.get("tool") or "")):
            try:
                _sel().log_tool_invocation(
                    session_key=f"app:{app_name}", agent="", source="tool_invoke",
                    tool_name=str(body.get("tool") or ""), tool_kind="", outcome="denied",
                    error="tool not in app's declared mcpTools",
                )
            except Exception:
                pass
            return web.json_response(
                {"ok": False, "error": f"app {app_name!r} not permitted to invoke {tool_name!r} — declare it in permissions.mcpTools"},
                status=403,
            )

    provider_name = body.get("provider") or ""

    # Resolve the provider: explicit name, else the first provider advertising the tool.
    provider = None
    if provider_name:
        provider = get_provider(provider_name)
        if provider is None:
            return web.json_response({"ok": False, "error": f"unknown tool provider: {provider_name}"}, status=404)
    else:
        for p in list_providers():
            try:
                if any(t.name == tool_name for t in await p.list_tools()):
                    provider = p
                    break
            except Exception:
                continue
    if provider is None:
        return web.json_response({"ok": False, "error": f"tool not found: {tool_name}"}, status=404)

    # Effective risk of this direct invocation, for the SEL — so this path (cron
    # scripts + the inspector "Try it") is as auditable as the chat gate ("what
    # destructive tool ran"). Resolve the declared risk from the provider's tool
    # def, then downgrade per-invocation (a read-only bash call is safe).
    from personalclaw.task_modes import resolve_effective_risk
    _declared = ""
    try:
        _declared = next((getattr(t, "risk_level", "") for t in await provider.list_tools() if t.name == tool_name), "")
    except Exception:
        _declared = ""
    _risk = resolve_effective_risk(_declared, tool_name, "", arguments)

    caller = request.headers.get("X-Session-Key", "") or "internal"
    try:
        result = await provider.invoke(tool_name, arguments)
    except Exception as exc:
        _sel().log_tool_invocation(
            session_key=caller, agent="", source="tool_invoke",
            tool_name=tool_name, tool_kind=provider.name, outcome="error",
            error=str(exc)[:200], metadata={"risk": _risk},
        )
        return web.json_response({"ok": False, "error": str(exc)[:500]}, status=500)

    _sel().log_tool_invocation(
        session_key=caller, agent="", source="tool_invoke",
        tool_name=tool_name, tool_kind=provider.name,
        outcome="completed" if result.success else "error",
        metadata={"risk": _risk},
    )
    out, _ = redact_exfiltration_urls(result.output or "")
    out, _ = redact_credentials(out)
    err, _ = redact_exfiltration_urls(result.error or "")
    err, _ = redact_credentials(err)
    return web.json_response({"ok": bool(result.success), "output": out, "error": err})


async def api_tools_toggle(request: web.Request) -> web.Response:
    """POST /api/tools/toggle — enable/disable a native-provider tool.

    Body ``{"provider": str, "name": str, "enabled": bool}``. Writes
    ``~/.personalclaw/tool_prefs.json``; the native runtime drops disabled tools at
    assembly. Core-locked tools are rejected (4xx). MCP tools use
    ``/api/mcp/toggle-tool`` (which writes mcp.json) — the page routes by provider.
    """
    from personalclaw.tool_providers import tool_prefs

    try:
        body = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "invalid JSON body"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"ok": False, "error": "body must be a JSON object"}, status=400)
    provider = str(body.get("provider", "")).strip()
    name = str(body.get("name", "")).strip()
    enabled = bool(body.get("enabled", True))
    if not name:
        return web.json_response({"ok": False, "error": "name is required"}, status=400)
    result = tool_prefs.set_enabled(provider, name, enabled)
    _audit_toggle(request, "tools.toggle", result.get("ok", False),
                  f"{provider}:{name}={'on' if enabled else 'off'}", result.get("error", ""))
    if not result.get("ok"):
        # locked-tool rejection → 409 Conflict (a real, expected denial, not a bug).
        return web.json_response(result, status=409 if result.get("locked") else 400)
    return web.json_response(result)


async def api_providers_toggle(request: web.Request) -> web.Response:
    """POST /api/tools/provider-toggle — enable/disable a whole NATIVE tool provider.

    Body ``{"provider": str, "enabled": bool}``. Writes
    ``tool_prefs.json``'s ``disabledProviders``; the runtime skips a disabled
    provider's entire toolset. The locked platform provider is rejected (409). MCP
    servers use ``/api/mcp/toggle`` (mcp.json); the Tools page routes by kind.
    """
    from personalclaw.tool_providers import tool_prefs

    try:
        body = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "invalid JSON body"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"ok": False, "error": "body must be a JSON object"}, status=400)
    provider = str(body.get("provider", "")).strip()
    enabled = bool(body.get("enabled", True))
    if not provider:
        return web.json_response({"ok": False, "error": "provider is required"}, status=400)
    result = tool_prefs.set_provider_enabled(provider, enabled)
    _audit_toggle(request, "tools.provider_toggle", result.get("ok", False),
                  f"{provider}={'on' if enabled else 'off'}", result.get("error", ""))
    if not result.get("ok"):
        return web.json_response(result, status=409 if result.get("locked") else 400)
    return web.json_response(result)

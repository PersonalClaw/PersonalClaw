"""Aggregated tools listing + invocation endpoints — surface and run tools
from all registered tool providers (the Tool entity)."""

import asyncio
import logging
from typing import TYPE_CHECKING

from aiohttp import web

from personalclaw.http_errors import json_error
from personalclaw.providers.failure_copy import relayed_failure_copy
from personalclaw.request_validation import require_string
from personalclaw.security import redact_credentials, redact_exfiltration_urls

if TYPE_CHECKING:
    from personalclaw.tool_providers.base import ToolDefinition, ToolProvider

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
            caller=request.get("user", "dashboard"),
            operation=op,
            outcome="ok" if ok else "error",
            source="tools",
            resources=resources,
            error=error,
        )
    except Exception:
        pass


def _provider_trust_tiers() -> dict[str, str]:
    """provider name → supply-chain trust tier, for tool providers an APP contributes.

    #2627. The Tools page badge used to be a binary — ``platform`` when the provider was
    locked, ``built-in`` otherwise — so an installed community bundle (native kind, not
    locked) was labelled with the same word core's own first-party providers get. The
    install dialog had just disclosed "Unsigned — community tier" and the user consented
    to *that*; the page a user later audits from then said shipped-with-the-product. The
    fix is provenance on the wire, so the badge derives from where the provider came from
    rather than from whether it happens to be locked.

    A provider absent from this map is CORE (the cwd-coupled platform provider, the entity
    categories, anything registered by a factory rather than by an app) and the caller
    labels it ``builtin``. Every app-contributed provider is keyed twice — under the app
    name and under each live instance's own ``name`` — because an app that declares several
    provider instances registers them as ``{app}:{instance}``, which is the string the
    catalog tags its tools with and therefore the string the page groups by.

    Never raises: a broken registry read must not empty the Tools page, and the caller's
    ``builtin`` default is the pre-#2627 behaviour, so the worst case is the old label.
    """
    try:
        from personalclaw.apps.app_manager import trust_tier_of
        from personalclaw.apps.manager import list_apps
        from personalclaw.providers.registry import get_provider_registry

        tier_by_app = {
            str(app.get("name", "")): trust_tier_of(str(app.get("name", "")))
            for app in list_apps()
            if app.get("name")
        }
        out: dict[str, str] = {}
        for ext in get_provider_registry().list_by_type("tool"):
            tier = tier_by_app.get(ext.name)
            if not tier:
                continue
            out[ext.name] = tier
            instance = ext.provider_instance
            for provider in instance if isinstance(instance, list) else [instance]:
                inst_name = str(getattr(provider, "name", "") or "")
                if inst_name:
                    out[inst_name] = tier
        return out
    except Exception:
        logger.warning("Failed to resolve tool-provider trust tiers", exc_info=True)
        return {}


async def api_tools_list(request: web.Request) -> web.Response:
    """GET /api/tools — Return all tools from all active tool sources.

    There are exactly three sources, and they don't overlap:
    1. The session-coupled PLATFORM provider (filesystem + shell + tool_result_get).
       It's built per-runtime (cwd-bound) so it is NOT in the registry — enumerated
       directly here.
    2. The tool-provider REGISTRY (``list_all_tools``) — every registered in-process
       provider, which already includes ``personalclaw-core``
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

    from personalclaw.tool_providers.groups import CORE_GROUP, group_name_for_provider

    def _group_of(name: str, provider: str) -> str:
        # Mirrors tool_providers.groups.group_of_tool over the catalog's (name,
        # provider) pair: a core-locked name is always core, wherever it lives.
        return CORE_GROUP if tool_prefs.is_locked(name) else group_name_for_provider(provider)

    # #2627: where each provider came from, so the page's provenance badge is derived
    # from that rather than from whether the provider is locked. Resolved once per catalog
    # build (it is a directory scan plus a registry walk), not per tool.
    provider_tiers = _provider_trust_tiers()

    tools_out: list[dict] = []
    seen: set[tuple[str, str]] = set()

    def _add(
        name: str,
        description: str,
        provider: str,
        parameters: dict,
        requires_approval: bool = True,
        risk_level: object = "safe",
        *,
        default_tier: str = "builtin",
    ) -> None:
        key = (provider, name)
        if key in seen or not name:
            return
        seen.add(key)
        locked = tool_prefs.is_locked(name)
        prov_off = provider in disabled_provs
        tools_out.append(
            {
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
                "disabled": (not locked)
                and (prov_off or tool_prefs.key_for(provider, name) in disabled_keys),
                # CONTEXT-ECONOMY §5: which activation GROUP this tool belongs to
                # (derived from its provider; core-locked names are always "core").
                # Read-only here — activation is per-session runtime state, not a pref.
                "group": _group_of(name, provider),
                # #2627: the PROVENANCE of the provider behind this tool — the same
                # `supply_chain.TrustTier` the install dialog disclosed. A NATIVE provider
                # no installed app contributed is the platform itself, hence `builtin`; the
                # Tools page spells this through the one shared label map so its badge and
                # the install dialog cannot describe the same bundle differently.
                #
                # 🪤 An EXTERNAL MCP server gets "" rather than `builtin` (its caller passes
                # `default_tier=""`). It never went through the supply-chain gate, so it has
                # no tier to report, and claiming the platform's own is exactly the
                # reassuring-direction lie this field exists to remove. The page renders
                # live health for those groups, not provenance, so nothing reads it.
                "tier": provider_tiers.get(provider, default_tier),
            }
        )

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
            _add(
                t.name,
                t.description,
                getattr(t, "provider", "") or _platform.name,
                t.parameters,
                getattr(t, "requires_approval", True),
                getattr(t, "risk_level", "safe"),
            )
    except Exception as exc:
        logger.warning("Failed to enumerate native platform tools", exc_info=True)
        record_failure("personalclaw-filesystem", str(exc))

    # Source 2: the tool-provider REGISTRY — every registered in-process provider.
    # This already includes personalclaw-core (registered via
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
            _add(
                t.name,
                t.description,
                t.provider,
                t.parameters,
                t.requires_approval,
                getattr(t, "risk_level", "safe"),
            )
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
                    tools = await asyncio.wait_for(
                        conn.list_tools(), timeout=_MCP_LIST_TIMEOUT_SECS
                    )
                    return name, list(tools)
                except (asyncio.TimeoutError, Exception):  # noqa: BLE001
                    logger.debug(
                        "MCP server '%s' tool listing skipped (slow/unreachable)",
                        name,
                        exc_info=True,
                    )
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
                        # An external MCP server has no supply-chain tier — see the `tier`
                        # note in `_add`. "" is the honest answer, `builtin` would be a lie.
                        default_tier="",
                    )
    except Exception as exc:
        logger.warning("Failed to list tools from MCP client registry", exc_info=True)
        record_failure("mcp", str(exc))

    # Surface load failures so a broken provider is operator-visible on the Tools
    # page instead of silently contributing zero tools (per-provider failures are
    # recorded inside list_all_tools; catalog-source failures are recorded above).
    return web.json_response({"tools": tools_out, "load_failures": get_load_failures()})


def _platform_provider_for_invoke() -> "tuple[ToolProvider | None, str]":
    """The cwd-coupled PLATFORM provider for one ``/api/tools/invoke`` call.

    Returns ``(provider, refusal)`` — exactly one is truthy.

    #3310. ``create_platform_tools_provider`` had four consumers and three of them
    prepended it: ``GET /api/tools`` (so the page LISTS all nine filesystem/shell tools),
    the group partition (so ``core`` reports honestly), and the native bridge (so the agent
    can call them). The fourth — this route, the only one that EXECUTES — resolved through
    ``get_provider``/``list_providers`` alone, and the provider is deliberately not in the
    registry, so every one of the nine 404'd. The Tools page listed a tool its own "Try it"
    button could not run, and ``ScriptContext.call_tool``'s two documented examples
    (``bash`` with ``rm -rf`` and with ``ls``) both answered ``404 tool not found: bash``,
    leaving the zero-token ``script`` mode with no filesystem or shell reach at all.

    Staying OUT of the registry is the right call and is not what changed here: the provider
    is cwd-coupled for workspace path confinement (``provider_bridge`` builds one per
    session for exactly that reason), so a process-wide singleton would have to pick one
    workspace for every caller. Prepending a per-call instance is what the other three
    consumers already do.

    The cwd is ``default_workspace_dir()``, the same root a new chat session gets. Its
    empty return is a REFUSAL, not an unknown — the resolved root is either not a usable
    directory or is a sensitive (credential) location — and it must not be laundered into
    ``Path.cwd()``, which is whatever directory the gateway was started in. That is the
    laundering ``session._resolve_acp_spawn_cwd`` already refuses for a CLI spawn, and the
    stakes here are the same: shell and file tools confined to a path the user cannot
    predict. So an unresolved workspace refuses loudly instead, at the last point that can
    still stop the call.
    """
    from personalclaw.agents.native.builtin_tools import create_platform_tools_provider
    from personalclaw.config.loader import default_workspace_dir

    try:
        root = str(default_workspace_dir() or "").strip()
    except Exception:  # noqa: BLE001 — an unreadable workspace is a refusal, not a 500
        logger.warning("Failed to resolve the workspace root for a tool invocation", exc_info=True)
        root = ""
    if not root:
        return None, (
            "No usable workspace directory resolved, so the filesystem and shell tools have "
            "no folder to run in. Running them in the gateway's own working directory is "
            "refused. Set a workspace root (PERSONALCLAW_WORKSPACE, or the workspace "
            "directory in Settings)."
        )
    try:
        return create_platform_tools_provider(cwd=root), ""
    except Exception as exc:  # noqa: BLE001 — same: a refusal the caller can report
        logger.warning("Failed to build the platform tool provider", exc_info=True)
        return None, f"The filesystem and shell tools could not be prepared: {exc}"


async def _resolve_tool(
    surface: "list[ToolProvider]", tool_name: str, prefer: str
) -> "tuple[ToolProvider, ToolDefinition] | None":
    """The provider on *surface* that serves *tool_name*, with that tool's definition, or None.

    A provider serves a tool when its ``list_tools()`` advertises the name, which is how an agent
    turn finds one too. Among several, the one named *prefer* is tried first (the provider the
    Tools page showed the tool under), then the rest in surface order, platform first. A named
    provider that serves no such tool is passed over, never handed the call. One that fails to
    list serves nothing this call, and the next is asked.
    """
    for provider in sorted(surface, key=lambda p: p.name != prefer):
        try:
            tools = await provider.list_tools()
        except Exception:  # noqa: BLE001 — a broken provider must not turn into a 500 here
            logger.debug("tool provider %r failed to list its tools", provider.name, exc_info=True)
            continue
        tool = next((t for t in tools if t.name == tool_name), None)
        if tool is not None:
            return provider, tool
    return None


async def api_tool_invoke(request: web.Request) -> web.Response:
    """POST /api/tools/invoke — execute one tool through the Tool entity.

    Internal-only (loopback + X-Internal-Secret): used by zero-token cron
    scripts so a sandboxed subprocess gets the same MCP+native tool surface
    the agent has, without importing the in-process registry. Body:
    ``{"tool": str, "arguments": dict, "provider"?: str, "confirm_risk"?: str}``.
    Returns ``{ok, output, error}``.

    "The same surface the agent has" is literal: the tool is resolved by name
    (``_resolve_tool``) over ``tool_providers.registry.tool_surface``, the list
    ``provider_bridge`` builds an agent's tools from. ``provider`` only says which provider
    serving that name to try first. So an external MCP server's tool runs through the provider
    that puts it on an agent's surface, and is out of reach when an agent could not reach it
    either. The surface starts with the nine filesystem/shell tools — see
    ``_platform_provider_for_invoke`` for why that provider is not registered and what #3310
    measured when this route skipped it.

    "The same surface the agent has" includes the user's tool preferences: a tool disabled
    on the Tools page is refused here with ``403 tool_disabled``, exactly as the runtime
    drops it at schema assembly. Core-locked tools and the locked platform provider are
    exempt (``tool_prefs.is_disabled`` handles that), so the primitives stay reachable.

    It includes the agent's hard deny-list: a tool name ``security.is_denied`` refuses is
    refused here with ``403 tool_denied_by_policy``, as the runtime refuses it before asking
    for any approval.

    It also includes the risk tier (#506). A call whose EFFECTIVE risk resolves as
    ``destructive`` is refused with ``403 risk_confirmation_required`` unless the body
    names the tier in ``confirm_risk``. ``safe`` and ``caution`` are unchanged, and the
    per-invocation downgrade keeps a read-only ``bash`` in the free tier — see the gate
    itself for why the scope stops exactly there.
    """
    from personalclaw.agents.native.builtin_tools import PLATFORM_TOOL_NAMES
    from personalclaw.tool_providers.registry import tool_surface

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
                    session_key=f"app:{app_name}",
                    agent="",
                    source="tool_invoke",
                    tool_name=str(body.get("tool") or ""),
                    tool_kind="",
                    outcome="denied",
                    error="tool not in app's declared mcpTools",
                )
            except Exception:
                pass
            return web.json_response(
                {
                    "ok": False,
                    "error": f"app {app_name!r} not permitted to invoke {tool_name!r} — declare it in permissions.mcpTools",  # noqa: E501
                },
                status=403,
            )

    provider_raw = body.get("provider")
    if provider_raw is not None and not isinstance(provider_raw, str):
        return web.json_response({"ok": False, "error": "provider must be a string"}, status=400)

    # The cwd-coupled platform provider, built for this call — see
    # `_platform_provider_for_invoke` for why it is not in the registry and why this route
    # has to prepend it (#3310). A cheap constructor (no I/O beyond resolving the workspace root).
    platform, platform_refusal = _platform_provider_for_invoke()

    # The tool, resolved the way an agent turn resolves it: by NAME, over the agent's own
    # surface. The named `provider` only says which of the providers serving that name to try
    # first; it is never a key of its own. It was, and "Try it" failed for every external MCP
    # tool: the Tools page labels one with its SERVER, which no registry holds, so this answered
    # `404 unknown tool provider: <server>` while an agent called the same tool through the `mcp`
    # provider that serves it. It could also hand a call to a provider that serves no such tool.
    resolved = await _resolve_tool(tool_surface(platform), tool_name, provider_raw or "")
    if resolved is None:
        # A tool the platform bundle owns is missing because the workspace is: say so.
        if platform_refusal and tool_name in PLATFORM_TOOL_NAMES:
            return json_error("workspace_unresolved", message=platform_refusal, status=503)
        return web.json_response({"ok": False, "error": f"tool not found: {tool_name}"}, status=404)
    provider, _tool_def = resolved

    # The user-disabled gate. This route executes a tool, so the Tools page toggle has to
    # reach it: the toggle presents itself as "this tool is off", and it was honored only
    # by the native runtime, which drops disabled tools at schema assembly. Everything
    # arriving here bypassed it — including `schedule_script.py`, which posts to this route
    # specifically so a cron script "gets the same MCP+native tool surface the agent has".
    # That surface is the agent's surface AFTER the user's preferences, so applying them is
    # what makes the two actually the same (#437).
    #
    # Same guards as the runtime, via the same `tool_prefs` helpers rather than a second
    # reading of the file: `is_disabled` exempts core-locked tools and the locked platform
    # provider on its own, so `bash`/`read_file`/the filesystem provider stay reachable and
    # a cron script cannot be locked out of the primitives.
    #
    # The provider KEY is the tool's own `provider` tag with the instance name as the
    # fallback — the same resolution `GET /api/tools` and the runtime use. Keying on the
    # instance name alone would miss a tool whose tag differs, i.e. it would report the
    # tool as enabled on exactly the tools the UI wrote a disable row for.
    from personalclaw.tool_providers import tool_prefs

    _pkey = (getattr(_tool_def, "provider", "") or "") or provider.name
    if tool_prefs.is_disabled(_pkey, tool_name):
        try:
            _sel().log_tool_invocation(
                session_key=request.headers.get("X-Session-Key", "") or "internal",
                agent="",
                source="tool_invoke",
                tool_name=tool_name,
                tool_kind=provider.name,
                outcome="denied",
                error="tool is disabled by the user",
            )
        except Exception:  # noqa: BLE001 — an unaudited refusal is still a refusal
            pass
        return json_error(
            "tool_disabled",
            message=(f"{tool_name!r} is disabled — re-enable it on the Tools page to invoke it"),
            status=403,
        )

    # The agent's hard deny-list, by tool NAME, the check `NativeAgentRuntime._guard_and_invoke`
    # makes before any approval is asked for. It was never made here, and external MCP servers
    # are where its names live: a cron script (or "Try it", once it could reach them) ran an
    # `mcp/<server>/delete_stack` that no agent can run, whatever it is approved for.
    from personalclaw import security

    denied = security.is_denied(tool_name)
    if denied:
        try:
            _sel().log_tool_invocation(
                session_key=request.headers.get("X-Session-Key", "") or "internal",
                agent="",
                source="tool_invoke",
                tool_name=tool_name,
                tool_kind=provider.name,
                outcome="denied",
                error=denied,
            )
        except Exception:  # noqa: BLE001 — an unaudited refusal is still a refusal
            pass
        return json_error(
            "tool_denied_by_policy",
            message=f"{denied}. No agent may run {tool_name!r}, and neither may this request.",
            status=403,
        )

    # Effective risk of this direct invocation, for the SEL — so this path (cron
    # scripts + the inspector "Try it") is as auditable as the chat gate ("what
    # destructive tool ran"). Resolve the declared risk from the provider's tool
    # def, then downgrade per-invocation (a read-only bash call is safe).
    from personalclaw.task_modes import resolve_effective_risk

    _declared = getattr(_tool_def, "risk_level", "")
    _risk = resolve_effective_risk(_declared, tool_name, "", arguments)

    caller = request.headers.get("X-Session-Key", "") or "internal"

    # The risk GATE (#506). Until this existed, `_risk` was resolved here and spent
    # entirely on the SEL rows below — `provider.invoke` ran one line later whatever it
    # said. A risk level that only changes what the audit RECORDS about a call, never
    # whether the call happens, is an indicator; the taxonomy had no consumer anywhere on
    # this path that could refuse. So the fix is not a louder warning: it is this refusal.
    #
    # Scoped to effective-DESTRUCTIVE on purpose, and the scope is the load-bearing part:
    #
    #  · EFFECTIVE, not declared — `resolve_effective_risk` downgrades a read-only
    #    invocation, so `bash "ls"` stays free and only the call that actually mutates has
    #    to name itself. Gating on the DECLARED tier would 403 every cron script that
    #    reads through bash, which is a worse outage than the bug.
    #  · destructive ONLY — the 26 caution tools include the ones cron scripts write
    #    through (`task_create`, `knowledge_create`). Requiring an acknowledgement from
    #    them would break working automations to no security end; caution escalates in the
    #    UI (a modal instead of an inline step) where the cost lands on a human who is
    #    already looking at the screen. The ladder's rungs are deliberately unequal.
    #  · The way out is ONE field, and it must NAME the tier. A truthy `confirm_risk: true`
    #    would be pasted in once and stop being read; requiring the resolved tier makes the
    #    caller state what it believes it is doing, and a disagreement fails closed.
    #    `schedule_script.ScriptContext.call_tool` takes it as a keyword for cron authors.
    #
    # What this buys, stated exactly: a destructive tool can no longer be executed by a
    # DEFAULT-SHAPED request. It is not an authorization boundary — this route is already
    # loopback + internal-secret, so every caller here is the owner or something the owner
    # installed. It removes the silent default, and it makes the inspector's ceremony a
    # wire requirement rather than a local boolean the UI could be bypassed by omitting.
    if _risk == "destructive" and str(body.get("confirm_risk") or "") != "destructive":
        try:
            _sel().log_tool_invocation(
                session_key=caller,
                agent="",
                source="tool_invoke",
                tool_name=tool_name,
                tool_kind=provider.name,
                outcome="denied",
                error="destructive risk not acknowledged",
                metadata={"risk": _risk},
            )
        except Exception:  # noqa: BLE001 — an unaudited refusal is still a refusal
            pass
        return json_error(
            "risk_confirmation_required",
            message=(
                f"{tool_name!r} resolves as a DESTRUCTIVE call. Re-send with "
                '"confirm_risk": "destructive" to run it.'
            ),
            status=403,
            # The tier travels in the envelope so a client can escalate to the right
            # ceremony instead of dead-ending on the message. The inspector reads this to
            # ask for a typed confirmation on a tool whose DECLARED tier looked lower —
            # which happens whenever name inference or an absent shell command floors the
            # effective tier above the declaration.
            error_extra={"risk": _risk, "confirm_field": "confirm_risk"},
        )
    try:
        result = await provider.invoke(tool_name, arguments)
    except Exception as exc:
        _sel().log_tool_invocation(
            session_key=caller,
            agent="",
            source="tool_invoke",
            tool_name=tool_name,
            tool_kind=provider.name,
            outcome="error",
            error=str(exc)[:200],
            metadata={"risk": _risk},
        )
        # Raw text stays in the SEL record above; the wire speaks guidance (failure_copy).
        return web.json_response({"ok": False, "error": relayed_failure_copy(exc)}, status=500)

    _sel().log_tool_invocation(
        session_key=caller,
        agent="",
        source="tool_invoke",
        tool_name=tool_name,
        tool_kind=provider.name,
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
    ``~/.personalclaw/tool_prefs.json``, which BOTH execution paths honor: the native
    runtime drops disabled tools at schema assembly (the model cannot see or call them),
    and ``POST /api/tools/invoke`` refuses them with ``403 tool_disabled``. This docstring
    used to promise only the first, accurately — and the toggle's UI presented itself as
    the tool's on/off switch while a second path executed it anyway (#437).

    Core-locked tools are rejected (4xx). MCP tools use ``/api/mcp/toggle-tool`` (which
    writes mcp.json) — the page routes by provider.
    """
    from personalclaw.tool_providers import tool_prefs
    from personalclaw.tool_providers.registry import list_all_tools

    try:
        body = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "invalid JSON body"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"ok": False, "error": "body must be a JSON object"}, status=400)
    provider = str(body.get("provider", "")).strip()
    name = require_string(body, "name")
    enabled = body.get("enabled", True)
    if not isinstance(enabled, bool):
        # A non-bool ``enabled`` (e.g. the JSON string "false", which is truthy)
        # would silently INVERT the toggle under bool() coercion — reject it.
        return web.json_response({"ok": False, "error": "enabled must be a boolean"}, status=400)
    # Refuse to persist junk: the name must be a tool some registered provider
    # actually exposes, else the toggle writes a dead key to tool_prefs.json.
    if name not in {t.name for t in await list_all_tools()}:
        return web.json_response({"ok": False, "error": f"unknown tool {name!r}"}, status=404)
    result = tool_prefs.set_enabled(provider, name, enabled)
    _audit_toggle(
        request,
        "tools.toggle",
        result.get("ok", False),
        f"{provider}:{name}={'on' if enabled else 'off'}",
        result.get("error", ""),
    )
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
    enabled = body.get("enabled", True)
    if not isinstance(enabled, bool):
        # Same coercion trap as the per-tool toggle: reject a non-bool rather
        # than let bool("false") silently enable the provider.
        return web.json_response({"ok": False, "error": "enabled must be a boolean"}, status=400)
    if not provider:
        return web.json_response({"ok": False, "error": "provider is required"}, status=400)
    result = tool_prefs.set_provider_enabled(provider, enabled)
    _audit_toggle(
        request,
        "tools.provider_toggle",
        result.get("ok", False),
        f"{provider}={'on' if enabled else 'off'}",
        result.get("error", ""),
    )
    if not result.get("ok"):
        return web.json_response(result, status=409 if result.get("locked") else 400)
    return web.json_response(result)


async def api_tools_savings(request: web.Request) -> web.Response:
    """GET /api/tools/savings — the TokenJuice savings (counterfactual) summary.

    Read-only aggregate from ``~/.personalclaw/tokenjuice_savings.json`` (Context Economy
    §1.3): estimated tokens saved by output projection, the top compressor, and a
    per-compressor breakdown. Tokens are estimated (``chars/4``, flagged ``estimated``) —
    this is the *savings* ledger, not authoritative spend metering. Never raises (an
    absent/corrupt file returns an empty summary)."""
    from personalclaw.tool_providers import savings

    return web.json_response(savings.summary())


async def api_tool_groups(request: web.Request) -> web.Response:
    """GET /api/tools/groups — the tool-GROUP partition (Context Economy §5).

    Groups are *derived* from the registered tool providers, so this reports the
    same partition the native runtime assembles: one entry per group with its tool
    count, whether it's always-on (``core``), whether its declared capability
    resolves (``offerable``), and the per-surface activation defaults.

    Read-only by design. Activation is **per-session runtime state** (seeded from
    the defaults, changed by the agent's ``reset_tools``), not a stored preference
    — so there is nothing here to toggle. What the user configures is the feature
    flag (``tools.groups_enabled``) and the per-surface defaults
    (``tools.group_defaults``), both via the config API.
    """
    from personalclaw.tool_providers import groups as groups_mod
    from personalclaw.tool_providers.registry import list_all_tools

    defs: list = []
    try:
        defs = [t for t in await list_all_tools() if t.provider != "mcp"]
    except Exception:
        logger.warning("Failed to list tools for the group partition", exc_info=True)
    # The cwd-coupled platform provider isn't in the registry (same reason the
    # catalog enumerates it separately) — include it so `core` reports honestly.
    try:
        from personalclaw.agents.native.builtin_tools import create_platform_tools_provider

        defs = list(await create_platform_tools_provider().list_tools()) + defs
    except Exception:
        logger.warning("Failed to enumerate platform tools for groups", exc_info=True)

    # The CONFIGURED defaults, not the runtime answer (issue 573). `resolve_default_groups`
    # short-circuits to `None` while the feature flag is off, and `or set()` then flattened
    # that into `[]` — which this response documents as "every group". So with groups off
    # (the shipped default) the API asserted that no surface has a default, while three of
    # them ship one. `enabled` below is what says whether this map is in effect; the map
    # itself has to stay readable, because "would anything change if I turned this on?" is
    # the whole question a reader brings here.
    surfaces = {
        key: sorted(value)
        for key, value in (
            (surface, groups_mod.configured_default_groups(surface) or set())
            for surface in ("chat", "background", "loops", "orchestration")
        )
    }
    out = []
    for group in groups_mod.partition(defs):
        out.append(
            {
                "name": group.name,
                "display": group.display,
                "alwaysOn": group.always_on,
                "toolCount": len(group.tools),
                "tools": list(group.tools),
                "capability": group.capability,
                "offerable": groups_mod.offerable(group),
                "instructions": group.instructions,
            }
        )
    return web.json_response(
        {
            "enabled": groups_mod.groups_enabled(),
            "groups": out,
            # Per surface: the groups CONFIGURED to start active. An empty list means "every
            # group" (no per-surface default configured — chat's deliberate case). Reported
            # whether or not `enabled` is true: with the feature off nothing is filtered, and
            # `enabled` is the field that says so.
            "surfaceDefaults": surfaces,
        }
    )

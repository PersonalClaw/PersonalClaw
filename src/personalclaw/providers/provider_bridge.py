"""Provider Bridge — resolves a use case to a live ModelProvider instance.

The model is:

1. Read the active selection for the use case from ``active_models.json``
   (Settings → Models) — a ``"<provider_name>:<model_id>"`` ref.
2. Resolve that provider from the config.json ``providers[]`` registry
   (``default_registry``), pinning to the selected model.
3. Fall back to the first configured provider declaring the capability when no
   model is selected.

The bridge exports a single function ``create_provider_factory()`` that returns
a callable matching the factory signature::

    factory(session_key=None, agent=None, model_override=None, ...) -> ModelProvider
"""

import json
import logging
from collections.abc import Callable
from typing import Any

from personalclaw.errors import AgentError
from personalclaw.llm.base import ModelProvider

logger = logging.getLogger(__name__)

ProviderFactory = Callable[..., ModelProvider]

# The Settings→Models capability names (parent_capability output) don't all match the
# provider-type Capability enum 1:1. Media-understanding roles map onto the single
# VISION capability the provider types advertise. Without this, Capability("image_modality")
# raises ValueError and every vision/ocr resolution fails even with a vision model bound.
_CAPABILITY_TO_ENUM = {
    "image_modality": "vision",
    "video_modality": "vision",
    # audio_modality has no provider-type Capability yet — resolution falls through to the
    # active-model ref, which is what STT/audio use; leave unmapped (returns None cleanly).
}


def _log_chain_skip(use_case: str, ref: str, reason: str) -> None:
    """SEL-record one fallback-chain entry skip (MODEL-USE-CASES-V2) — the audit
    trail for "why did my default model not serve this call". Best-effort."""
    try:
        from personalclaw.sel import sel

        sel().log_api_access(
            caller="system",
            operation="model.chain_skip",
            outcome="success",
            source="provider_bridge",
            resources=f"{use_case}:{ref}:{reason}",
        )
    except Exception:  # noqa: BLE001 — audit must never break resolution
        logger.debug("chain-skip SEL record failed", exc_info=True)


def _capability_enum(capability: str):
    """Map a Settings→Models capability string to the provider Capability enum, or None
    if it isn't a provider-type capability (caller then can't match by capability)."""
    from personalclaw.llm.capabilities import Capability

    try:
        return Capability(_CAPABILITY_TO_ENUM.get(capability, capability))
    except ValueError:
        return None


class ProviderResolutionError(Exception):
    """Raised when a provider cannot be resolved from extension instances.

    PLATFORM-LEGIBILITY §2: may carry an optional ``agent_error`` WHAT/WHY/FIX
    envelope. When present, its ``render()`` string IS this exception's message,
    so every place that already surfaces ``str(exc)`` into a turn (a background
    turn that dies on a stale pin, the mid-turn factory) shows the coded,
    actionable failure — no parallel structure, and no dead field.
    """

    def __init__(self, message: str, agent_error: "AgentError | None" = None):
        self.agent_error = agent_error
        super().__init__(agent_error.render() if agent_error is not None else message)


def _agent_provider_kind(agent: str | None) -> str:
    """Return the agent-runtime kind for ``agent``: ``"native"`` or ``"acp"``.

    Precedence:
      1. the agent profile's own ``provider`` field;
      2. the global ``cfg.agent.provider``;
      3. ``"native"`` (the in-process loop is the default runtime).
    A value like ``"acp:claude-code"`` (or bare ``"acp"``) is treated as ACP;
    everything else — including empty/unset — resolves to ``native``. ACP must be
    opted into explicitly (a per-agent ``provider`` or the global default set to
    ``acp``); an agent with no runtime declared is NEVER silently routed to an
    external CLI.
    """
    try:
        from personalclaw.config.loader import AppConfig

        cfg = AppConfig.load()
        prof = (cfg.agents or {}).get(agent) if agent else None
        kind = (
            (getattr(prof, "provider", "") if prof else "")
            or getattr(cfg.agent, "provider", "")
            or "native"
        )
    except Exception:
        kind = "native"
    return "acp" if str(kind).startswith("acp") else "native"


def _build_acp_runtime(
    runtime_id: str,
    *,
    session_key: str | None,
    agent: str | None,
    model_override: str | None,
    cwd: str | None,
    channel_id: str | None,
    **kwargs: Any,
) -> "ModelProvider":
    """Build the ``acp:<cli>`` agent runtime the caller NAMED, per session.

    The per-session axes (agent/persona, model, cwd, channel) are threaded as kwargs
    because they are properties of the SESSION, not of the global runtime entry — which
    is exactly the contract ``acp_agent._factory`` already documents for each of them.
    A missing or non-``acp_agent`` entry raises rather than falling back to a model:
    silently answering a "run my CLI" request with a different runtime is the failure
    this function exists to remove.
    """
    from personalclaw.llm.acp_agent import ACP_AGENT_CAPABILITY  # register_type() too
    from personalclaw.llm.registry import get_default_registry

    registry = get_default_registry()
    try:
        entry = registry.get_entry(runtime_id)
    except Exception as exc:
        raise ProviderResolutionError(
            f"WHAT: the session is bound to agent runtime {runtime_id!r}, which is not "
            f"registered\nWHY: its agent app is not installed or failed to register "
            f"(the CLI may be missing from this machine)\nFIX: install/enable the "
            f"matching agent app in the App Store, or rebind the session's runtime"
        ) from exc
    if entry.type != ACP_AGENT_CAPABILITY.type:
        raise ProviderResolutionError(
            f"WHAT: provider entry {runtime_id!r} is type {entry.type!r}, not an agent "
            f"runtime\nWHY: only an {ACP_AGENT_CAPABILITY.type!r} entry can serve an "
            f"``acp:`` binding\nFIX: rebind the session to a registered agent runtime"
        )
    return registry.build(
        runtime_id,
        session_key=session_key,
        agent=agent or "",
        model=model_override or "",
        cwd=cwd or "",
        channel_id=channel_id or "",
        **kwargs,
    )


def _provider_entry_name(provider: "ModelProvider | None", *, use_case: str = "chat") -> str:
    """Best-effort name of the provider ENTRY a resolved ModelProvider came from.

    Used to keep ``_fallback_chat_model`` in agreement with the inner provider the
    native runtime already resolved. Providers don't reliably carry their entry
    name, so derive it from the FIRST resolvable ref of the GOVERNING axis's chain
    (``use_case`` — the sub-category the inner resolve used, falling back to chat
    when unbound via ``active_model_refs``) — deterministically the same ref the
    inner resolver (``resolve_provider_for_use_case`` → the chain walk) picks
    first, since both walk the refs in order and take the first whose provider is
    configured. Returns "" when indeterminate (then ``_fallback_chat_model`` uses
    its own ordered fallback)."""
    del provider  # entry name isn't stamped on the instance; use the ref order.
    try:
        from personalclaw.providers.use_cases import active_model_refs, split_ref

        for ref in active_model_refs(use_case):
            parsed = split_ref(ref)
            if not parsed:
                continue
            ref_provider, _model_id = parsed
            # The inner resolver builds from the first ref whose provider is
            # resolvable; mirror that with a cheap can-build probe.
            if ref_provider and _provider_is_configured(ref_provider):
                return ref_provider
    except Exception:
        logger.debug("provider entry-name derivation failed", exc_info=True)
    return ""


def _provider_is_configured(provider_name: str) -> bool:
    """True when a provider entry of this name is present in the config registry
    (its app is installed/configured) — a cheap mirror of what the inner resolver
    requires to build from a ref."""
    try:
        from personalclaw.providers.use_cases import _known_provider_names

        known = _known_provider_names()
        if known:
            return provider_name in known
    except Exception:
        logger.debug("provider-configured probe failed", exc_info=True)
    # Indeterminate → assume configured so the hint still constrains the fallback.
    return True


def _fallback_chat_model(provider_hint: str | None = None, *, use_case: str = "chat") -> str:
    """A concrete model id to use when an agent declares no model of its own.

    ``use_case`` names the governing axis (MODEL-USE-CASES-V2): the id comes from
    THAT axis's chain (``active_model_refs`` falls back to chat when the
    sub-category is unbound, preserving today's behavior until the user binds it).

    Background agents (``personalclaw-lite`` for suggestions + consolidation) and
    any agent whose ``model`` is empty would otherwise pass ``model=""`` down to
    the OpenAI-compatible client, which rejects it ("length of model should be
    between 1 and 512").

    CRITICAL — provider/model agreement: for a native agent this id becomes
    ``AgentRuntimeDefinition.model`` and is passed to the *already-resolved* inner
    ModelProvider's ``complete(model=…)``, OVERRIDING that provider's own pinned
    id. So the returned model MUST belong to the SAME provider the inner resolver
    picked, or the model of one provider gets sent to another (e.g. Alibaba's
    ``glm-5.2`` handed to the Bedrock client → "The provided model identifier is
    invalid", which failed every background suggestions turn). ``provider_hint``
    is the resolved inner provider's entry name — when given, pick the active chat
    ref for THAT provider so they agree.

    Resolve, in order:
    1. When ``provider_hint`` is set: the first active chat ref whose provider
       matches the hint (keeps model + provider consistent).
    2. The configured default agent's model — ONLY when its provider matches the
       hint (or no hint) — else it could name a different provider.
    3. The first active chat model (Settings → Models) — mirrors the inner
       resolver's own "first resolvable ref" order.
    4. ``""`` (caller falls back to the provider's own configured model).
    """
    from personalclaw.providers.use_cases import active_model_refs, split_ref

    def _ref_provider_matches(ref_provider: str) -> bool:
        return not provider_hint or ref_provider == provider_hint

    # 1. When we know which provider the inner resolver picked, take the model
    #    from the matching active ref of the governing axis so model + provider agree.
    if provider_hint:
        try:
            for ref in active_model_refs(use_case):
                parsed = split_ref(ref)
                if not parsed:
                    continue
                ref_provider, model_id = parsed
                if model_id and ref_provider == provider_hint:
                    return model_id
        except Exception:
            logger.debug("fallback model: provider-hint match failed", exc_info=True)

    # 2. Default agent's model — but only if it doesn't disagree with the hint.
    try:
        from personalclaw.agents.defaults import default_agent_name
        from personalclaw.config.loader import AppConfig

        cfg = AppConfig.load()
        prof = (cfg.agents or {}).get(default_agent_name(cfg))
        # Reconcile first: a default-agent pin naming an uninstalled provider
        # (e.g. a stale "Bedrock:…" after the provider was removed) must NOT be
        # returned — it would be handed to whatever provider actually resolves
        # (→ wrong-provider 404). Reconcile drops it to "" so we fall through to
        # the active chat selection below.
        raw = _reconcile_agent_model(getattr(prof, "model", "") or "") if prof else ""
        if raw:
            parsed = split_ref(str(raw))
            ref_provider = parsed[0] if parsed else ""
            # A "<provider>:model" pin must name the SAME provider the inner
            # resolver picked — else its bare id would be sent to the wrong
            # client. A bare pin (no provider prefix) has no provider to
            # disagree, so it passes through.
            if not ref_provider or _ref_provider_matches(ref_provider):
                return _strip_provider_prefix(str(raw))
    except Exception:
        logger.debug("fallback model: default-agent lookup failed", exc_info=True)

    # 3. First active model of the governing axis (strip the "provider:" prefix the
    #    store keeps). Prefer a hint-matching ref; otherwise the first ref (mirrors
    #    the inner resolver's "first resolvable ref" order).
    try:
        for ref in active_model_refs(use_case):
            parsed = split_ref(ref)
            if not parsed:
                model_id, ref_provider = ref, ""
            else:
                ref_provider, model_id = parsed
            if model_id and _ref_provider_matches(ref_provider):
                return model_id
    except Exception:
        logger.debug("fallback model: active-models lookup failed", exc_info=True)
    return ""


def _active_chat_model_ids() -> set[str]:
    """The model ids (without the ``provider:`` prefix) currently active for chat."""
    out: set[str] = set()
    try:
        from personalclaw.providers.use_cases import active_model_refs, split_ref

        for ref in active_model_refs("chat"):
            parsed = split_ref(ref)
            mid = parsed[1] if parsed else ref
            if mid:
                out.add(mid)
                out.add(ref)  # also accept a fully-qualified "provider:model" pin
    except Exception:
        logger.debug("active chat model lookup failed", exc_info=True)
    return out


def _strip_provider_prefix(model: str) -> str:
    """Strip a leading ``<provider>:`` from a model ref so the bare id reaches
    the SDK. A chat session stores its model as the active_models ref form
    (``"Bedrock:global.anthropic.claude-opus-4-8"``); handed verbatim to the
    provider it becomes an invalid model id (AWS: "model identifier is invalid").
    Colons are ambiguous — Bedrock ids contain them (``…-v1:0``) — so split on the
    FIRST colon ONLY when the prefix matches a known provider entry name.
    """
    if not model or ":" not in model:
        return model
    prefix = model.split(":", 1)[0]
    try:
        from personalclaw.llm.registry import get_default_registry

        registry = get_default_registry()
        if any(e.name == prefix for e in registry.list_entries()):
            return model.split(":", 1)[1]
    except Exception:
        logger.debug("provider-prefix strip check failed", exc_info=True)
    # The live ModelProvider registry doesn't always have the CONFIG providers
    # loaded in this call path (their register_type() side-effects are lazy), so a
    # ref like "OpenAI:gpt-5.4" would slip through unstripped and reach the SDK as a
    # literal model id → 404. Fall back to the authoritative config-provider name set
    # (config.json providers[] + bundled + media) — the same source the active_models
    # refs are formed from — so a config-qualified prefix is stripped regardless.
    try:
        from personalclaw.providers.use_cases import _known_provider_names

        known = _known_provider_names()
        if known and prefix in known:
            return model.split(":", 1)[1]
    except Exception:
        logger.debug("provider-prefix strip via known-names failed", exc_info=True)
    return model


def _reconcile_agent_model(model: str) -> str:
    """Heal a stale agent model pin.

    An agent may pin an explicit model that the user later removes from the
    active set (Settings → Models). Rather than hand that dead id to the client
    (→ 400 / unresolved provider), treat it as unset so the caller falls back to
    the chat-use-case binding. Empty (inherit) and still-active pins pass through.
    """
    if not model:
        return ""
    active = _active_chat_model_ids()
    # No active chat models configured yet → don't second-guess the pin.
    if not active or model in active:
        return model
    logger.info("Agent model %r no longer active; falling back to chat binding", model)
    return ""


def _build_native_runtime(
    *,
    use_case: str,
    session_key: str | None,
    agent: str | None,
    model_override: str | None,
    cwd: str | None,
    extra_tool_roots: list | None = None,
    unattended: bool = False,
    dry_run: bool = False,
    reasoning_effort: str = "",
    project_id: str = "",
    model_axis: str = "",
    tool_groups: list | None = None,
    **kwargs: Any,
) -> ModelProvider:
    """Construct a :class:`NativeAgentRuntime` for a ``native`` agent.

    Its inference ModelProvider is resolved through the SAME active-model
    selection (Settings → Models). ``model_axis`` names the chat sub-category
    whose CHAIN governs the inner model (MODEL-USE-CASES-V2): "background" for
    the lite factory, "loops" for loop workers, "orchestration" for model-less
    subagent spawns, else the session's own use case — so a sub-category
    binding governs native agents too (previously the inner model hardcoded
    "chat", making e.g. a code_tools binding cosmetic). Tools come from the
    in-process core provider.
    """
    from pathlib import Path

    from personalclaw.agents.native.builtin_tools import (
        PLATFORM_CATEGORIES,
        NativeBuiltinToolProvider,
    )
    from personalclaw.agents.native.runtime import NativeAgentRuntime
    from personalclaw.agents.provider import AgentRuntimeDefinition

    # Heal a stale per-turn override BEFORE it threads into the inner provider
    # resolution. A chat session persists its model as a "<provider>:model" ref;
    # after that provider is uninstalled the ref is dead. If we passed it through,
    # the inner resolver would override the active binding's model id with the dead
    # one — sending e.g. "Bedrock:…claude-opus-4-8" to the OpenAI provider → 404.
    # Reconcile it to "" so the active chat binding fully governs the model.
    model_override = _reconcile_agent_model(model_override or "") or None

    # The inner ModelProvider — resolve the governing axis's chain WITHOUT
    # recursing into the native branch (pass a sentinel kwarg the factory honors).
    # ``model_axis`` (a chat sub-category, or "chat") picks WHICH chain governs:
    # an unbound sub-category falls back to the chat chain via active_model_refs,
    # so the default behavior is unchanged until the user binds the axis.
    # ``_model_axis_only`` additionally excludes agent-runtime (acp_agent)
    # registry entries from resolution: the native loop calls
    # ``ModelProvider.complete()``, which an AgentProvider (ACP) does not
    # implement. Without this, a stack whose ``chat`` use case resolves to an
    # ACP entry would hand the native loop an AcpAgentProvider and blow up with
    # "'AcpAgentProvider' object has no attribute 'complete'".
    from personalclaw.providers.use_cases import CHAT_SUBCATEGORIES

    inner_axis = model_axis if model_axis in CHAT_SUBCATEGORIES else "chat"
    model_provider = resolve_provider_for_use_case(
        inner_axis,
        session_key=session_key,
        agent=agent,
        model_override=model_override,
        cwd=cwd,
        _force_model_axis=True,
        _model_axis_only=True,
        **kwargs,
    )
    name = agent or "PersonalClaw"
    if not hasattr(model_provider, "complete"):
        raise ProviderResolutionError(
            f"Native agent {name!r} resolved its inference model to "
            f"{type(model_provider).__name__}, which is not a ModelProvider "
            f"(no complete()). Bind the 'chat' use case to a model provider "
            f"(Settings → Models), not an ACP agent runtime."
        )

    # Pull the agent's model/tools/skills from its profile when present. Its PROMPT is
    # not read here: the system prompt reaches the model through the turn's assembled
    # context (``ContextBuilder.build_message``), the one place it is resolved.
    # Strip any "<provider>:" prefix so the bare model id reaches complete()
    # (the inner ModelProvider is resolved above; this is the id label the SDK
    # call uses — a "Bedrock:…" ref here means an invalid AWS model identifier).
    # Reconcile the per-turn override too (not just the profile pin): a chat
    # session persists its model as a "<provider>:model" ref, and after a
    # provider is uninstalled that ref is stale. Healing it to "" lets the
    # chat-binding fallback pick a live model, instead of stripping the prefix
    # and handing a dead model id to whatever provider resolution lands on
    # (the "sent Bedrock:… to OpenAI → 404" bug).
    model = _strip_provider_prefix(_reconcile_agent_model(model_override or ""))
    tools: list[str] = []
    skills: list[str] = []
    hook_ids: list[str] = []
    try:
        from personalclaw.config.loader import AppConfig

        cfg = AppConfig.load()
        prof = (cfg.agents or {}).get(agent) if agent else None
        if prof is not None:
            # Heal a stale pin: an explicit agent model (or per-turn override)
            # that's no longer active reconciles to "" → the chat-binding
            # fallback below. Both the override and the profile pin may be the
            # "<provider>:model" ref a chat session stores; reconcile BOTH so a
            # ref naming an uninstalled provider doesn't slip through as a bare
            # (dead) model id.
            model = _strip_provider_prefix(
                _reconcile_agent_model(model_override or "")
            ) or _strip_provider_prefix(_reconcile_agent_model(getattr(prof, "model", "") or ""))
            tools = list(getattr(prof, "tools", []) or [])
            skills = list(getattr(prof, "skills", []) or [])
            hook_ids = list(getattr(prof, "triggers", []) or [])
    except Exception:
        pass

    # An agent with no model of its own (the hidden ``personalclaw-lite``
    # background agent, the goal loop worker's "inherit chat" default, or any
    # user agent left on "Agent default") would otherwise hand the OpenAI client
    # an empty model string and 400. Resolve a concrete chat model in that case.
    #
    # The model id is passed to the ALREADY-RESOLVED ``model_provider`` above and
    # overrides its own pinned id, so it MUST name the same provider — pass that
    # provider's entry name as the hint. ``_provider_entry_name`` derives it from
    # the resolved provider (its bound entry name), falling back to the first
    # active chat ref's provider (the ref the inner resolver picks first). Without
    # this the fallback could return another provider's model (e.g. Alibaba's
    # ``glm-5.2`` sent to the Bedrock client → "model identifier is invalid",
    # which failed every background suggestions/consolidation turn).
    if not model:
        model = _fallback_chat_model(
            provider_hint=_provider_entry_name(model_provider, use_case=inner_axis),
            use_case=inner_axis,
        )

    definition = AgentRuntimeDefinition(
        name=name,
        provider="native",
        model=model,
        tools=tools,
        skills=skills,
        workspace_dir=cwd or "",
    )
    _cwd = Path(cwd) if cwd else None

    # E3 agent-scoped triggers: the native loop's PreToolUse seam fires ONLY the
    # lifecycle triggers this agent references (AgentProfile.triggers), never the
    # global set. An agent with none (the seeded default) gets no callable → fires nothing.
    hook_fire = None
    if hook_ids:

        async def hook_fire(tool_name: str, args_json: str | None) -> list[str]:  # noqa: F811
            from personalclaw.hooks import HOOK_EVENT_PRE_TOOL_USE, get_global_hook_store

            store = get_global_hook_store()
            if store is None:
                return []
            try:
                tool_input = json.loads(args_json) if args_json else None
            except (ValueError, TypeError):
                tool_input = None
            results = await store.fire_for_ids(
                HOOK_EVENT_PRE_TOOL_USE,
                hook_ids,
                tool_name=tool_name,
                tool_input=tool_input,
            )
            # Mirror chat_runner._fire's contract: exit-2 → BLOCKED sentinel,
            # exit-0 stdout → context injection.
            out: list[str] = []
            for r in results:
                if r.exit_code == 2:
                    out.append(f"BLOCKED:{r.hook_name}:{(r.stderr or 'hook denied')[:200]}")
                elif r.exit_code == 0 and r.stdout:
                    out.append(r.stdout)
            return out

    # Tool surface = the always-on PLATFORM provider (filesystem + shell + the
    # tool_result_get affordance, cwd-confined to THIS session) + EVERY registered
    # bundled tool provider (the registry is the single source of truth: the
    # in-process category providers — knowledge/tasks/loops/inbox/subagents/memory/
    # artifacts/workflows — the web tools, schedule, and the external MCP/OpenAI
    # adapters). Sourcing the rest from the registry (not a hardcoded list) means a
    # newly-installed or split-out tool provider reaches the native agent
    # automatically, with no drift. The platform provider is built per-session here
    # because it's cwd-coupled (workspace path confinement); the session-coupled app
    # providers are registry singletons that resolve this turn via contextvars
    # (runtime._invoke binds them).
    from personalclaw.tool_providers.registry import list_providers as _list_tool_providers

    platform = NativeBuiltinToolProvider(
        cwd=_cwd,
        agent=name or "",
        session_key=session_key or "",
        extra_roots=[Path(r) for r in (extra_tool_roots or [])],
        categories=PLATFORM_CATEGORIES,
        provider_name="personalclaw-filesystem",
        display="Filesystem & Shell Tools",
    )
    tool_providers = [platform, *_list_tool_providers()]

    return NativeAgentRuntime(  # type: ignore[return-value]  # CI-2
        definition=definition,
        model_provider=model_provider,  # type: ignore[arg-type]
        tool_providers=tool_providers,
        cwd=_cwd,
        session_key=session_key or "",
        hook_fire=hook_fire,
        unattended=unattended,
        dry_run=dry_run,
        reasoning_effort=reasoning_effort,
        project_id=project_id,
        # Tool groups (CONTEXT-ECONOMY §5). ``surface`` is the session class whose
        # per-surface defaults seed activation — the SAME axis label that governs
        # the inner model, so "background"/"loops"/"orchestration" runs start
        # focused while interactive chat keeps every group active (zero change).
        # ``tool_groups`` is the explicit per-template override (the engine's
        # stage-spawn seam): when given it wins over the surface default.
        tool_groups=list(tool_groups) if tool_groups is not None else None,
        surface=inner_axis,
    )


def _model_app_for_provider_type(provider_type: str) -> tuple[str, bool] | None:
    """``(app_name, enabled)`` of the INSTALLED model app registering ``provider_type``.

    ``None`` when no installed app claims it — which is the difference between "enable
    the app you already have" and "install one", and therefore the difference between an
    actionable fix and a dead end. Mirrors how ``GET /api/model-provider-types`` derives
    the pair (``providerType``, else the app-name stem), which is the only type→app
    mapping in the tree.
    """
    try:
        from personalclaw.providers.registry import get_provider_registry

        for ext in get_provider_registry().list_by_type("model"):
            name = getattr(getattr(ext, "manifest", None), "name", "") or ext.name
            declared = str(getattr(ext.provider_config, "providerType", "") or "")
            if (declared or name.replace("-models", "")) == provider_type:
                return name, bool(getattr(ext, "enabled", False))
    except Exception:  # noqa: BLE001 — a diagnosis must never raise over the failure it explains
        logger.debug("could not map provider type %r to an app", provider_type, exc_info=True)
    return None


def _credential_is_missing(name: str) -> bool:
    """Whether ``name`` names a credential the store cannot produce a secret for."""
    try:
        from personalclaw.config.loader import config_dir
        from personalclaw.llm.credentials import CredentialStore

        store = CredentialStore(config_dir())
        if not store.has(name):
            return True
        return str(getattr(store.resolve(name), "source", "") or "none") == "none"
    except Exception:  # noqa: BLE001 — an unreadable store is not evidence of a missing key
        return False


def _diagnose_unbuildable_ref(
    provider_name: str, model_id: str, use_case: str, capability: str
) -> tuple[str, str]:
    """``(why, fix)`` for an active ref whose provider the config registry could not build.

    ``_resolve_from_config_registry`` returns a bare ``None`` for every cause, and the
    raise below used to state ONE of them unconditionally — *"absent from config.json (its
    app isn't installed or configured)"* — including for causes where ``config.json``
    plainly contains the entry. A missing type factory was reported as a missing app, and
    the fix told the user to install their own provider ENTRY name in the App Store, which
    is not a thing that exists there (#3408).

    Each branch below asks a question the next one is allowed to assume, so the answer
    names the cause that actually fired:

    1. ``config.json`` unreadable — say so rather than guess either way.
    2. no entry by that name in config.json — the original sentence, now only when true.
    3. in config.json but not in the live registry — the boot/sync gap.
    4. its ``type`` has no registered factory — app not installed, or installed but
       DISABLED, which are different fixes and are distinguished here. Names the **type**,
       because that is the token the Store and ``POST /api/providers`` speak; the entry
       name is the user's own label and matches nothing installable.
    5. entry + factory present, but its declared capabilities do not cover the use case.
    6. the credential it names has no secret in the credential store.
    7. anything left — which it states as *unsure*, with the model id to check and the log
       line to read, rather than asserting a specific wrong cause.

    "The model name is not offered by that provider" is deliberately NOT a branch:
    resolution passes ``model_override`` through to the factory unvalidated, so a wrong
    model id does not make this function return ``None``. When a factory rejects it, the
    build failure lands in branch 7, which is why that branch names the model id.
    """
    from personalclaw.providers.use_cases import _known_provider_names

    rebind = f"rebind {use_case!r} to an available model in Settings → Models"
    target_cap = _capability_enum(capability)
    if target_cap is None:
        return (
            f"use case {use_case!r} maps to no provider capability, so no configured "
            f"provider can satisfy it",
            f"{rebind} — and report use case {use_case!r} as unmappable",
        )
    try:
        from personalclaw.llm.registry import get_default_registry

        registry = get_default_registry()
        entries = {e.name: e for e in registry.list_entries()}
    except Exception:  # noqa: BLE001 — never let the diagnosis outrank the failure
        logger.debug("provider registry unreadable while diagnosing %r", provider_name)
        return (
            f"the provider registry could not be read, so why {provider_name!r} cannot be "
            f"built is unknown",
            f"check the gateway log, or {rebind}",
        )

    entry = entries.get(provider_name)
    if entry is None:
        configured = _known_provider_names()
        if configured is None:
            return (
                f"config.json could not be read, so whether {provider_name!r} is still "
                f"configured is unknown",
                f"repair config.json (see the gateway log), or {rebind}",
            )
        if provider_name not in configured:
            return (
                f"no provider named {provider_name!r} is in config.json — the entry was "
                f"renamed or removed, or its app was uninstalled",
                f"re-add {provider_name!r} in Settings → Providers, or {rebind}",
            )
        return (
            f"provider {provider_name!r} IS in config.json but is not registered in the "
            f"running gateway, so nothing can build it",
            f"re-save {provider_name!r} in Settings → Providers to register it now, or "
            f"restart the gateway to replay config.json",
        )

    try:
        type_capabilities = registry.capability_of(entry.type).capabilities
    except Exception:  # noqa: BLE001 — the documented "is this type registered?" probe
        app = _model_app_for_provider_type(entry.type)
        if app is None:
            return (
                f"provider {provider_name!r} declares type {entry.type!r}, and no installed "
                f"app registers that type",
                f"install an app that provides {entry.type!r} in the App Store, or change "
                f"{provider_name!r}'s type in Settings → Providers",
            )
        app_name, enabled = app
        if not enabled:
            return (
                f"provider {provider_name!r} declares type {entry.type!r}, whose app "
                f"{app_name!r} is installed but DISABLED, so the type is not registered",
                f"enable {app_name!r} on the Apps page",
            )
        return (
            f"provider {provider_name!r} declares type {entry.type!r} and its app "
            f"{app_name!r} is installed and enabled, but the type never registered — the "
            f"app failed to load",
            f"check the gateway log for {app_name!r}'s import error, or {rebind}",
        )

    if target_cap not in (entry.declared_capabilities or type_capabilities):
        return (
            f"provider {provider_name!r} (type {entry.type!r}) does not declare the "
            f"{capability!r} capability that use case {use_case!r} needs",
            f"{rebind}, or bind {use_case!r} to a provider that declares {capability!r}",
        )

    credential = str(entry.credential or "")
    if credential and _credential_is_missing(credential):
        return (
            f"provider {provider_name!r} needs credential {credential!r}, which has no "
            f"secret in the credential store",
            f"set {credential!r} in Settings → Providers, or {rebind}",
        )

    return (
        f"provider {provider_name!r} (type {entry.type!r}) is configured and its type is "
        f"registered, so the cause is not visible from here — building it failed",
        f'check the gateway log for "failed to build provider {provider_name}", confirm '
        f"{model_id!r} is a model {provider_name!r} offers, or {rebind}",
    )


def resolve_provider_for_use_case(
    use_case: str,
    *,
    session_key: str | None = None,
    agent: str | None = None,
    model_override: str | None = None,
    cwd: str | None = None,
    **kwargs: Any,
) -> ModelProvider:
    """Resolve a use case to a live ModelProvider instance.

    Resolution order:
    1. The active model selected for ``use_case`` in ``active_models.json``
       (Settings → Models) — a ``"<provider_name>:<model_id>"`` ref that pins
       resolution to that configured provider + model. A chat sub-category
       (``reasoning`` / ``code_tools``) with no model of its own borrows the parent
       ``chat`` selection.
    2. Implicit fallback: any configured provider (config.json ``providers[]``)
       declaring the requested capability — picks the first. Avoids forcing the
       user to set a selection when only one sensible provider exists.
    """
    from personalclaw.providers.use_cases import (
        VALID_USE_CASES,
        active_model_refs,
        parent_capability,
        split_ref,
    )

    if use_case not in VALID_USE_CASES:
        raise ProviderResolutionError(f"Unknown use case: {use_case!r}")

    # ── Native AgentProvider branch (E2-P4) ──
    # For an agentic chat use case whose agent's provider is "native", build the
    # in-process NativeAgentRuntime instead of an ACP/model provider.
    # ``_force_model_axis`` (set when the native builder resolves its INNER
    # ModelProvider) bypasses this so we never recurse. Pop it unconditionally so
    # it never leaks into the downstream model-axis resolvers.
    _force_model_axis = kwargs.pop("_force_model_axis", False)
    # The caller (chat_runner) resolves the agent's runtime kind from its actual
    # PROFILE (resolve_agent_bindings.provider) and threads it here as
    # ``provider_kind``. Honor it directly — re-deriving from ``agent`` is unsafe
    # because the value passed as ``agent`` is the ACP-internal provider_agent
    # name (e.g. "personalclaw"), which does NOT match the agent profile key.
    # ACP is opt-in: only an explicit ``acp``/``acp:<cli>`` routes to a CLI;
    # everything else (including empty) is the native in-process loop.
    _provider_kind = kwargs.pop("provider_kind", "") or ""
    # Extra directories the native file tools may read/write outside cwd (a Code/
    # Goal-Loop worker's project files dir). Pop it unconditionally so it never leaks
    # into the model-axis resolvers (ACP / config-registry), which don't expect it;
    # it's meaningful only to the native runtime builder below.
    _extra_tool_roots = kwargs.pop("extra_tool_roots", None)
    # Unattended run mode (scheduled run-prompt/run-workflow, Goal/Code loop cycle,
    # dry-run replay): strips interactive tools + fails the approval gate fast so a
    # background turn can't wedge waiting for a human (T5). Popped here so it never
    # leaks into the MODEL-axis resolvers (which don't expect it) and re-injected
    # below for the ACP branch — ACP consumes it too as of §2.3 (it is what lets the
    # acp_agent factory pair a Zed dialect's ``bypassPermissions`` with host-side
    # fail-fast; before that it was popped and DISCARDED, so an unattended ACP loop
    # got neither the mode nor the fail-fast). The "auto"/"yolo" approval policy is a
    # separate, complementary lever (it auto-approves) — unattended is about never
    # blocking, set independently.
    _unattended = bool(kwargs.pop("unattended", False))
    # Dry-run replay (T9): observe-mode — write-capable tools return a synthetic
    # observation instead of executing. Pop unconditionally (native-only).
    _dry_run = bool(kwargs.pop("dry_run", False))
    # The Project this session's work scopes under. Pop unconditionally so it never
    # leaks into the model-axis resolvers; meaningful only to the native builder,
    # which binds it per-turn so artifact_save can stamp the artifact's project_id (S5).
    _project_id = str(kwargs.pop("project_id", "") or "")
    # The chat sub-category whose CHAIN governs this session's INNER model
    # (MODEL-USE-CASES-V2 T2.x): the _bg factory passes "background", loop worker
    # sessions "loops", model-less subagent spawns "orchestration". Defaults to the
    # outer use_case itself (chat sessions → the chat chain; code_tools sessions →
    # the code_tools chain — previously the inner model hardcoded "chat", making a
    # code_tools binding cosmetic for native agents). Pop unconditionally so it
    # never leaks into the model-axis resolvers.
    _model_axis = str(kwargs.pop("model_axis", "") or "")
    # Explicit per-template tool-group activation (CONTEXT-ECONOMY §5.4 — the
    # WORKFLOWS-V2 stage-spawn seam). Pop unconditionally: native-only, and the
    # model-axis resolvers don't expect it.
    _tool_groups = kwargs.pop("tool_groups", None)
    # Per-turn reasoning effort. The native builder consumes it (forwarded to the
    # model's complete()); the ACP path reads reasoning_effort_override from kwargs
    # in its own factory, so DON'T pop it here for ACP — peek without removing.
    _reasoning_effort = str(kwargs.get("reasoning_effort_override") or "")
    _kind = (
        ("acp" if str(_provider_kind).startswith("acp") else "native")
        if _provider_kind
        else _agent_provider_kind(agent)
    )
    # §2.3 (gap 3): re-inject ``unattended`` for the ACP branch. Only the acp_agent
    # factory sees these kwargs on that branch, and it is the one place that can
    # honour the flag — it hands it to AcpClient, which is what lets sanitize_mode
    # accept ``bypassPermissions`` for a genuinely unattended run while every
    # interactive session stays clamped (AAP-5). Restricted to _kind == "acp" on
    # purpose: a native turn already took the explicit-argument path above, and the
    # MODEL-axis resolvers must never see this key.
    if _kind == "acp" and _unattended:
        kwargs["unattended"] = True
    # An explicit ``acp:<cli>`` NAMES the runtime to build — honour it here. Until now
    # ``_kind`` was only ever read to SKIP the native builder below, and an ACP kind then
    # fell through into the MODEL-axis resolution, which deliberately excludes
    # ``acp_agent`` entries — so a session bound to ``acp:<cli>`` silently resolved the
    # pinned chat model instead of its CLI. It hid because ``SessionManager``'s ACP
    # connection-pool claim normally answers first and uses ``provider_kind`` directly;
    # that claim is SKIPPED exactly when a resume id exists (a pooled connection has no
    # prior session), so the ONE path that needs a real, resumable ACP client was the one
    # path that never got one — this is gap 6's actual content (`G158`).
    if _kind == "acp" and _provider_kind.startswith("acp:"):
        return _build_acp_runtime(
            _provider_kind,
            session_key=session_key,
            agent=agent,
            model_override=model_override,
            cwd=cwd,
            # Arrives in kwargs (not a named parameter); popped so it is passed once.
            channel_id=kwargs.pop("channel_id", None),
            **kwargs,
        )
    if not _force_model_axis and use_case in ("chat", "code_tools") and _kind == "native":
        # reasoning_effort_override is meaningful to the native runtime as the
        # per-turn effort, but the native builder's downstream (model-axis resolver)
        # doesn't expect it — pop it and pass as the explicit reasoning_effort arg.
        kwargs.pop("reasoning_effort_override", None)
        return _build_native_runtime(
            use_case=use_case,
            session_key=session_key,
            agent=agent,
            model_override=model_override,
            cwd=cwd,
            extra_tool_roots=_extra_tool_roots,
            unattended=_unattended,
            dry_run=_dry_run,
            reasoning_effort=_reasoning_effort,
            project_id=_project_id,
            model_axis=_model_axis or use_case,
            tool_groups=_tool_groups,
            **kwargs,
        )

    # Provider-qualified model routes DIRECTLY to the named provider, bypassing
    # the stored active selection. Two spellings are provider-qualified:
    #   • "Provider/model" — the slash form.
    #   • "Provider:model" — the canonical active_models ref form the composer's
    #     model picker and chat-session model store emit (split_ref parses it).
    # For the colon form we MUST route to the prefixed provider (not just override
    # the id): the picker offers models from EVERY active chat provider, so a user
    # picking "OpenAI:gpt-5.4" while the first active ref is "Anthropic:…" would
    # otherwise send the literal "OpenAI:gpt-5.4" as a model id to the Anthropic
    # client → 404. Only treat the prefix as a provider when it actually names a
    # registered entry (else it's a bare id that happens to contain a colon, e.g.
    # "gpt-oss:20b").
    capability = parent_capability(use_case)
    # Model-call guard (AUTONOMY-GUARDRAILS §2): every NON-INTERACTIVE text axis
    # (``reasoning`` / ``background`` / ``loops`` / ``orchestration`` — backing
    # one_shot_completion, the lite background factory, loop workers/judges/gates,
    # and model-less subagent spawns) routes every resolved provider through
    # ModelCallGuard (per-provider circuit breaker + hard wall-clock timeout +
    # attempt-level JSONL audit) — so the breaker and the audit see the TRUE axis
    # (MODEL-USE-CASES-V2). The interactive chat/code_tools stream stays OUT OF
    # SCOPE: it returns above via _build_native_runtime (native) or resolves an
    # ACP CLI — both human-watched (its INNER model resolves with
    # _force_model_axis under its own axis). Thread the flag through kwargs so all
    # resolution attempts below wrap identically; _resolve_from_config_registry
    # pops it (never reaches the build factory) and wraps at the single point
    # where the entry name + model are known.
    if use_case in ("reasoning", "background", "loops", "orchestration"):
        kwargs["_guard_use_case"] = use_case
    # A colon-qualified "Provider:model" ref is tried FIRST (below) because its
    # model_id can itself contain a slash (e.g. "nvidia:meta/llama-3.1-8b"); the
    # slash-form resolver would otherwise mis-split it. The config registry returns
    # None when the colon prefix isn't a real provider, so a bare "gpt-oss:20b"
    # still falls through to the slash block.
    if model_override and "/" in model_override and ":" not in model_override:
        direct = _resolve_from_config_registry(
            capability,
            session_key=session_key,
            agent=agent,
            model_override=model_override,
            cwd=cwd,
            **kwargs,
        )
        if direct is not None:
            return direct
    if model_override and (":" in model_override or "/" in model_override):
        # Hand the colon-qualified ref to the config-registry resolver AS-IS — it
        # (and only it) parses "Provider:model" against the fully-populated config
        # registry (after its lazy register_type() imports), routes to the named
        # provider via provider_hint, and strips the prefix for the SDK. Doing the
        # prefix check HERE would query a registry that isn't populated yet for
        # config providers (the "OpenAI known=False" false-negative) → the ref would
        # fall through to the active-refs loop and be sent to the FIRST active
        # provider (e.g. picking OpenAI:gpt-5.4 → sent to the Anthropic client → 404).
        # Returns None when the prefix isn't a real provider (a bare id with a colon,
        # e.g. "gpt-oss:20b"), so we fall through to normal resolution.
        direct = _resolve_from_config_registry(
            capability,
            session_key=session_key,
            agent=agent,
            model_override=model_override,
            cwd=cwd,
            **kwargs,
        )
        if direct is not None:
            return direct

    # The active selection (Settings → Models) is an ordered fallback CHAIN
    # (MODEL-USE-CASES-V2): position 0 is the default, 1..n are the user's
    # declared fallbacks. Resolution walks the chain in order: an entry whose
    # provider's circuit breaker is OPEN is skipped (routed around a known-down
    # provider); an entry whose provider can't be built is skipped-with-warning
    # ONLY when a later entry exists — a chain whose entries ALL fail preserves
    # the stale-pin rule and raises (block, don't silently degrade past the
    # user's whole declared chain into implicit fallback). A one-entry chain
    # therefore behaves exactly as the single binding always did. A chat
    # sub-category with no chain of its own borrows the parent ``chat`` chain
    # (active_model_refs handles that).
    _refs = list(active_model_refs(use_case))
    # ── Step (2) routing seam (MODEL-ROUTING-TELEMETRY §3.2, MRT-4) ──
    # ONE call, ONE site, immediately before the active-ref loop: route_refs is a PURE REORDER of
    # the refs the user bound (§3.1) — it never invents, adds, or drops a candidate, so everything
    # below (the breaker skip, the provider_hint build, the pinned-ref-raises rule) is untouched;
    # only the order it walks them in changes. Both earlier steps bypass routing structurally
    # because they already returned: step (0) is the native-agent branch (interactive chat is
    # human-watched and out of scope v1) and step (1) is an explicit model_override (a caller's
    # explicit choice always wins). Enabled per use case only — with routing off, ``_refs`` is the
    # bound order byte-for-byte and nothing here changes latency or semantics.
    # ``routing_query_class`` is the class of THIS request when a caller knows it (the guard
    # classifies from the prompt, which resolution doesn't have); absent, the use-case-level
    # ordering applies. Popped unconditionally so it never leaks into the build kwargs.
    _query_class = str(kwargs.pop("routing_query_class", "") or "")
    _routed = False
    try:
        from personalclaw.routing.policy import route_refs, routing_active

        _routed = routing_active(use_case)
        if _routed:
            _refs = route_refs(use_case, _query_class, _refs)
    except Exception:  # noqa: BLE001 — routing must never break resolution (fail-open, §3.1)
        logger.debug("routing seam skipped for %s", use_case, exc_info=True)
        _routed = False
    _last_dead: tuple[str, str] | None = None  # (ref, provider_name) of a dead entry
    for i, ref in enumerate(_refs):
        parsed = split_ref(ref)
        if not parsed:
            continue
        provider_name, model_id = parsed
        has_later = i + 1 < len(_refs)
        # Breaker-OPEN skip: the guard's per-provider breaker already knows this
        # provider is down — don't burn a build + timeout to rediscover it.
        try:
            from personalclaw.guardrails.breaker import get_breaker

            if get_breaker(provider_name).is_open() and has_later:
                logger.warning(
                    "chain skip: %s entry %d (%s) — provider breaker OPEN", use_case, i, ref
                )
                _log_chain_skip(use_case, ref, "breaker_open")
                continue
        except Exception:  # noqa: BLE001 — breaker introspection must never break resolution
            pass
        # Routing provenance (§3.3): a routed resolution stamps ``routed`` on every attempt, and
        # one that landed on a LATER entry — because the routed-first candidate's breaker was OPEN
        # or it wasn't buildable — stamps ``routed_fallback`` too. That is the cloud-rescue signal,
        # and it is deliberately DISTINCT from ``degraded``: degraded says "a fallback ref served
        # this", routed_fallback says "the ordering the ROUTER chose didn't hold". Attribution
        # needs both, because a cloud rescue of a router's local-first bet is a routing outcome,
        # not a user-chain outcome. No extra attempt is made and no timeout is stacked: this rides
        # the existing chain walk, which has already skipped the dead entry.
        _rk = dict(kwargs)
        if _routed:
            _rk["_guard_routed"] = True
            if i > 0:
                _rk["_guard_routed_fallback"] = True
        pinned = _resolve_from_config_registry(
            capability,
            session_key=session_key,
            agent=agent,
            model_override=model_id,
            cwd=cwd,
            provider_hint=provider_name,
            **_rk,
        )
        if pinned is not None:
            return pinned
        # This entry names a provider the config registry can't build — its app
        # isn't installed / configured. With a later entry declared, skip it
        # (the user opted into fallback by ADDING entries); with none, fall
        # through to the stale-pin raise below.
        _last_dead = (ref, provider_name)
        if has_later:
            logger.warning(
                "chain skip: %s entry %d (%s) — provider not buildable", use_case, i, ref
            )
            _log_chain_skip(use_case, ref, "unbuildable")
            continue
    if _last_dead is not None:
        # The chain exhausted with at least one unbuildable entry. Per the
        # "block, don't silently fall back" rule (a stale Bedrock pin must NOT be
        # handed to Ollama as a literal model id → 404), raise a clear, actionable
        # error instead of the implicit fallback. The user fixes it by installing
        # the provider or picking another in Settings → Models.
        ref, provider_name = _last_dead
        # The why/fix pair is DERIVED from the cause that actually fired. It used to state
        # "absent from config.json (its app isn't installed or configured)" for every
        # cause, so the primary remediation surface for a total chat outage asserted a
        # wrong cause and offered an unactionable fix (#3408).
        _why, _fix = _diagnose_unbuildable_ref(
            provider_name, (split_ref(ref) or (provider_name, ref))[1], use_case, capability
        )
        raise ProviderResolutionError(
            f"The model selected for {use_case!r} ({ref!r}) isn't available. {_fix}.",
            AgentError(
                code="ERR_MODEL_UNRESOLVED",
                what=(f"the model pinned for use case {use_case!r} ({ref!r}) cannot be built"),
                why=(
                    _why + (" — every other chain entry was skipped too" if len(_refs) > 1 else "")
                ),
                fix=_fix,
            ),
        )

    # No active selection → implicit fallback: first configured provider declaring
    # the capability (avoids forcing a selection when only one sensible provider
    # exists). This only applies when the user has made NO selection at all.
    fallback = _resolve_from_config_registry(
        capability,
        session_key=session_key,
        agent=agent,
        model_override=model_override,
        cwd=cwd,
        **kwargs,
    )
    if fallback is not None:
        return fallback

    raise ProviderResolutionError(
        f"No provider configured for use case {use_case!r}. "
        f"Add a model provider in Settings → Providers.",
        AgentError(
            code="ERR_MODEL_UNRESOLVED",
            what=f"no model provider resolves for use case {use_case!r}",
            why="no provider in config.json declares the capability this use case needs",
            fix=f"add a model provider in Settings → Providers, then bind {use_case!r} to it",
        ),
    )


def can_resolve_use_case(use_case: str) -> bool:
    """Cheaply report whether a ModelProvider for ``use_case`` is resolvable
    *right now*, without building one.

    This is the single source of truth behind both the onboarding ``needs_model``
    signal and the background-session spawn guard — so the dashboard's "add a
    model" nudge and what the bridge can actually resolve never disagree (the
    coarse capability-only probe they used before could diverge from real
    resolution; see F1).

    Resolution for chat-class use cases succeeds when EITHER an active model is
    selected for the use case (Settings → Models) OR a configured provider
    (config.json ``providers[]`` → ``default_registry``) declares the matching
    capability. The native default agent inferences through a ModelProvider too,
    so "no model" ⇒ chat cannot run regardless of the agent-runtime kind. We
    deliberately do NOT instantiate a provider here (no subprocess/socket side
    effects) — this runs on a hot GET.
    """
    try:
        from personalclaw.providers.use_cases import (
            VALID_USE_CASES,
            active_model_refs,
            parent_capability,
        )
    except Exception:
        return False
    if use_case not in VALID_USE_CASES:
        return False

    # 1. An active selection wins (matches resolve_provider_for_use_case order).
    #    active_model_refs applies the chat sub-category → parent fallback.
    try:
        if active_model_refs(use_case):
            return True
    except Exception:
        logger.debug("can_resolve: active-model probe failed", exc_info=True)

    capability = parent_capability(use_case)

    # 2. Implicit fallback: any registry entry declaring the capability. Mirrors
    #    _resolve_from_config_registry's capability match WITHOUT building.
    try:
        # Trigger provider modules' register_type() side effects (idempotent).
        import personalclaw.llm.acp_agent  # noqa: F401
        from personalclaw.llm.registry import get_default_registry

        target_cap = _capability_enum(capability)
        if target_cap is None:
            return False

        registry = get_default_registry()
        for entry in registry.list_entries():
            # An agent-runtime entry (acp_agent) is not a model provider.
            if entry.type == "acp_agent":
                continue
            caps = entry.declared_capabilities
            if not caps:
                try:
                    caps = registry.capability_of(entry.type).capabilities
                except Exception:
                    caps = frozenset()
            if target_cap in caps:
                return True
    except Exception:
        logger.debug("can_resolve: registry probe failed", exc_info=True)
    return False


def _resolve_from_config_registry(
    use_case: str,
    *,
    session_key: str | None = None,
    agent: str | None = None,
    model_override: str | None = None,
    cwd: str | None = None,
    provider_hint: str | None = None,
    **kwargs: Any,
) -> ModelProvider | None:
    """Fallback: resolve via the ProviderEntry registry.

    Walks ``config.json``'s ``providers[]`` entries, picks the first whose
    declared capabilities cover ``use_case``, and builds a ModelProvider via the
    registry's registered type factory (``registry.build`` → the provider module's
    or app's ``register_type`` factory). Returns ``None`` when no compatible provider
    is configured.
    """
    try:
        # Trigger provider modules' register_type() side effects so the
        # registry can resolve types loaded lazily.
        import personalclaw.llm.acp_agent  # noqa: F401
        from personalclaw.llm.registry import get_default_registry
    except Exception:
        return None

    target_cap = _capability_enum(use_case)
    if target_cap is None:
        return None

    # When resolving the native loop's inner inference model, agent-runtime
    # entries (acp_agent) are not valid candidates — they implement the
    # AgentProvider axis (stream/turn), not ModelProvider.complete(). Pop the
    # sentinel so it never leaks into provider config below.
    model_axis_only = bool(kwargs.pop("_model_axis_only", False))
    # The non-interactive-text guard flag (set by resolve_provider_for_use_case for
    # the ``reasoning`` axis). Pop it unconditionally so it never leaks into the
    # build kwargs / factory; when set, the built provider is wrapped in a
    # ModelCallGuard just before return (§2 chokepoint).
    guard_use_case = str(kwargs.pop("_guard_use_case", "") or "")
    # Routing provenance flags (§3.3). Popped UNCONDITIONALLY — like _guard_use_case — so they can
    # never leak into the build kwargs / factory of a provider that knows nothing about routing.
    guard_routed = bool(kwargs.pop("_guard_routed", False))
    guard_routed_fallback = bool(kwargs.pop("_guard_routed_fallback", False))

    registry = get_default_registry()
    entries = list(registry.list_entries())
    if not entries:
        return None

    # If model_override is provider-qualified, route to that provider and strip
    # the prefix so the bare model id reaches the SDK. Two qualified shapes:
    #   • "ProviderName/model"  (slash) — legacy composer form.
    #   • "ProviderName:model"  (colon) — the active_models.json ref form a chat
    #     session stores (e.g. "Bedrock:global.anthropic.claude-opus-4-8").
    # Colons are ambiguous — Bedrock model ids themselves contain them
    # (…-v1:0) — so split on the FIRST colon ONLY when the prefix matches a
    # known provider entry name. Otherwise leave the override untouched.
    # Order matters: check the COLON form FIRST when its prefix names a known
    # provider. A "Provider:model_id" ref can carry a model id that itself
    # contains a slash (NVIDIA "nvidia:meta/llama-3.1-8b-instruct", OpenRouter
    # "or:meta-llama/llama-3.3"), so splitting on "/" first would mis-parse the
    # provider as "nvidia:meta" → unknown → wrong provider (fell back to Bedrock).
    if (
        model_override
        and ":" in model_override
        and any(e.name == model_override.split(":", 1)[0] for e in entries)
    ):
        _hint, model_override = model_override.split(":", 1)
        provider_hint = provider_hint or _hint
    elif model_override and "/" in model_override:
        _hint, model_override = model_override.split("/", 1)
        provider_hint = provider_hint or _hint
    elif model_override and ":" in model_override:
        _maybe_provider = model_override.split(":", 1)[0]
        if any(e.name == _maybe_provider for e in entries):
            _hint, model_override = model_override.split(":", 1)
            provider_hint = provider_hint or _hint

    candidate = None
    # A zero-config FLOOR entry sorts LAST among candidates. Registration order would
    # otherwise decide this the wrong way round: an app that registers a floor does so while
    # its module is imported (``register_extension_providers``), which runs BEFORE
    # ``sync_entries_from_config()`` replays the user's own ``config.json`` rows — so the
    # floor would be "the first entry declaring the capability" and would beat every provider
    # the user actually configured. ``sorted`` is stable, so non-floor entries keep their
    # registration order exactly. Same rule, same reason, as the search registry's keyless
    # floor (``search_providers/registry.py``: "a provider that declares itself ``keyless``
    # sorts last among candidates so a user-configured/keyed provider always wins").
    for entry in sorted(entries, key=lambda e: getattr(e, "floor", False)):
        # Skip agent-runtime entries when only a ModelProvider will do.
        if model_axis_only and entry.type == "acp_agent":
            continue
        caps = entry.declared_capabilities
        if not caps:
            try:
                caps = registry.capability_of(entry.type).capabilities
            except Exception:
                caps = frozenset()
        if target_cap not in caps:
            continue
        if provider_hint and entry.name != provider_hint:
            continue
        candidate = entry
        break

    if candidate is None:
        return None

    config: dict[str, Any] = {
        "model": candidate.model,
        **(candidate.options or {}),
    }
    if model_override:
        config["model"] = model_override
    if cwd:
        config["cwd"] = cwd
    if session_key:
        config["session_key"] = session_key
    if agent:
        config["agent"] = agent
    for k, v in kwargs.items():
        config.setdefault(k, v)

    # A config.json registry entry resolves through the registry's registered TYPE
    # factory — the same factory the provider's module (core-native ollama, or an
    # installed model APP: openai/anthropic/vllm/bedrock) registers via
    # register_type(...). This is the single path for both agent-runtime and model
    # providers now that the per-type hardcoded branches are gone. When the entry's
    # type isn't registered (e.g. its app isn't installed) registry.build raises and
    # we return None (no provider resolves) rather than crash.
    #
    # A model_override (a specific model pinned for this turn — e.g. the active
    # model an axis resolved from active_models.json) must win over the entry's
    # stored model. Thread it as the ``model`` build kwarg: every model provider's
    # register_type factory honors ``model`` over ``entry.model`` (registry.build
    # forwards kwargs to the factory). This replaces an older entry-replace dance
    # that relied on register_entry being overwrite-idempotent — it isn't (it raises
    # on a duplicate name), so that path silently no-op'd and the override was lost.
    build_kwargs = dict(kwargs)
    if model_override:
        build_kwargs["model"] = model_override
    if "credential_store" not in build_kwargs and candidate.credential:
        try:
            from personalclaw.config import config_dir
            from personalclaw.llm.credentials import CredentialStore

            build_kwargs["credential_store"] = CredentialStore(config_dir())
        except Exception:
            pass
    # When options carry an inline api_key (set by the "Add instance" UI form)
    # but no credential is linked, synthesize a Credential so the factory gets
    # it without requiring a credentials.json entry.
    if "credential_store" not in build_kwargs and not candidate.credential:
        inline_key = (candidate.options or {}).get("api_key")
        if inline_key and isinstance(inline_key, str):
            from personalclaw.llm.credentials import Credential

            _synth = Credential(
                name=candidate.name, kind="api_key", secret=inline_key, source="file"
            )
            build_kwargs["_inline_credential"] = _synth
    try:
        built = registry.build(
            candidate.name, session_key=session_key, cwd=cwd, agent=agent, **build_kwargs
        )
    except Exception:
        logger.exception(
            "Config-registry fallback failed to build provider %r for %s",
            candidate.name,
            use_case,
        )
        return None
    # The ONE point that knows both halves of the ref this provider serves — the entry it was
    # built from and the model it was built for — so it is recorded here rather than re-derived
    # downstream. The window resolver reads it to name the model that actually answers a turn,
    # including the zero-config floor, which is a registry entry and never a binding.
    served_model = str(config.get("model") or candidate.model or "")
    served_ref = f"{candidate.name}:{served_model}" if served_model else candidate.name

    # §2 chokepoint: wrap the resolved provider for the non-interactive text axis
    # (breaker + hard timeout + audit + day-budget + outbound scan). Config-derived
    # tuning is read fail-open — a broken config must never wedge resolution.
    if guard_use_case:
        from personalclaw.guardrails import wrap_model_call_guard
        from personalclaw.guardrails.breaker import get_breaker
        from personalclaw.guardrails.budgets import budget_from_config, run_budget_from_config

        scan_mode = "warn"
        breaker = None
        budget = None
        # `max_tokens_per_run` is a user-facing config field with a PATCH allowlist entry
        # and a builder (`run_budget_from_config`) that had NO production caller — so the
        # ceiling loaded and bound nothing. Read here beside the day budget because this is
        # the one seam that already turns guardrails config into a guard (S154).
        run_budget = None
        try:
            from personalclaw.config.loader import AppConfig

            gr = AppConfig.load().guardrails
            scan_mode = gr.scan_mode
            breaker = get_breaker(
                candidate.name,
                threshold=gr.breaker.failure_threshold,
                recovery_secs=gr.breaker.recovery_secs,
            )
            budget = budget_from_config()
            run_budget = run_budget_from_config()
        except Exception:
            logger.debug("guardrails config read failed; using safe defaults", exc_info=True)

        # §4.1: a ROUTED local attempt runs under ``routing.local_timeout_secs`` instead of the
        # guard's generic default — the whole point of ordering a local model first is that it is
        # cheap to *try*, which is only true if a stalled local model gives up quickly and lets the
        # chain reach the cloud ref. ONE timeout, on the one attempt: nothing is stacked, because
        # this replaces the guard's default rather than adding to it, and only for the local leg.
        _timeout_kw: dict[str, Any] = {}
        if guard_routed:
            try:
                from personalclaw.routing.policy import is_local_ref, local_timeout_secs

                if is_local_ref(candidate.name):
                    _secs = local_timeout_secs()
                    if _secs > 0:
                        _timeout_kw["timeout_secs"] = _secs
            except Exception:  # noqa: BLE001 — fail-open to the guard's own default
                logger.debug("routing local timeout read failed", exc_info=True)
        guarded = wrap_model_call_guard(
            built,
            use_case=guard_use_case,
            provider_name=candidate.name,
            model=served_model,
            budget=budget,
            run_budget=run_budget,
            scan_mode=scan_mode,
            breaker=breaker,
            routed=guard_routed,
            routed_fallback=guard_routed_fallback,
            **_timeout_kw,
        )
        _stamp_served_ref(guarded, served_ref)
        return guarded
    _stamp_served_ref(built, served_ref)
    return built


def _stamp_served_ref(provider: object, ref: str) -> None:
    """Record ``ModelProvider.served_ref`` on a freshly built provider.

    An object that refuses the attribute keeps no stamp, and the window resolver then names the
    model from the chat binding instead — a less exact answer, never a failed resolution.
    """
    try:
        provider.served_ref = ref  # type: ignore[attr-defined]
    except (AttributeError, TypeError):
        logger.debug("%s does not accept a served_ref stamp", type(provider).__name__)


def create_provider_factory(default_use_case: str = "chat") -> ProviderFactory:
    """Return a factory function matching the SessionManager contract.

    The returned factory signature is:
        factory(session_key=None, agent=None, model_override=None,
                cwd=None, channel_id=None, **kwargs) -> ModelProvider
    """

    def _factory(
        session_key: str | None = None,
        agent: str | None = None,
        model_override: str | None = None,
        cwd: str | None = None,
        channel_id: str | None = None,
        **kwargs: Any,
    ) -> ModelProvider:
        return resolve_provider_for_use_case(
            default_use_case,
            session_key=session_key,
            agent=agent,
            model_override=model_override,
            cwd=cwd,
            channel_id=channel_id,
            **kwargs,
        )

    return _factory

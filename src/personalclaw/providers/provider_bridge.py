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


def _capability_enum(capability: str):
    """Map a Settings→Models capability string to the provider Capability enum, or None
    if it isn't a provider-type capability (caller then can't match by capability)."""
    from personalclaw.llm.capabilities import Capability
    try:
        return Capability(_CAPABILITY_TO_ENUM.get(capability, capability))
    except ValueError:
        return None


class ProviderResolutionError(Exception):
    """Raised when a provider cannot be resolved from extension instances."""


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
        kind = (getattr(prof, "provider", "") if prof else "") or getattr(cfg.agent, "provider", "") or "native"
    except Exception:
        kind = "native"
    return "acp" if str(kind).startswith("acp") else "native"


def _provider_entry_name(provider: "ModelProvider | None") -> str:
    """Best-effort name of the provider ENTRY a resolved ModelProvider came from.

    Used to keep ``_fallback_chat_model`` in agreement with the inner provider the
    native runtime already resolved. Providers don't reliably carry their entry
    name, so derive it from the FIRST resolvable active chat ref — deterministically
    the same ref the inner resolver (``resolve_provider_for_use_case`` → the
    ``active_model_refs`` loop) picks first, since both walk the refs in order and
    take the first whose provider is configured. Returns "" when indeterminate
    (then ``_fallback_chat_model`` uses its own ordered fallback)."""
    del provider  # entry name isn't stamped on the instance; use the ref order.
    try:
        from personalclaw.providers.use_cases import active_model_refs, split_ref

        for ref in active_model_refs("chat"):
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


def _fallback_chat_model(provider_hint: str | None = None) -> str:
    """A concrete chat model to use when an agent declares no model of its own.

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
    #    from the matching active chat ref so model + provider agree.
    if provider_hint:
        try:
            for ref in active_model_refs("chat"):
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

    # 3. First active chat model (strip the "provider:" prefix the store keeps).
    #    Prefer a hint-matching ref; otherwise the first ref (mirrors the inner
    #    resolver's "first resolvable ref" order).
    try:
        for ref in active_model_refs("chat"):
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
    **kwargs: Any,
) -> ModelProvider:
    """Construct a :class:`NativeAgentRuntime` for a ``native`` agent.

    Its inference ModelProvider is resolved through the SAME active-model
    selection (Settings → Models), so the native agent's model is governed
    exactly like every other ``chat`` consumer. Tools come from the in-process
    core provider.
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

    # The inner ModelProvider — resolve the chat binding WITHOUT recursing into
    # the native branch (pass a sentinel kwarg the factory honors).
    # ``_model_axis_only`` additionally excludes agent-runtime (acp_agent)
    # registry entries from resolution: the native loop calls
    # ``ModelProvider.complete()``, which an AgentProvider (ACP) does not
    # implement. Without this, a stack whose ``chat`` use case resolves to an
    # ACP entry would hand the native loop an AcpAgentProvider and blow up with
    # "'AcpAgentProvider' object has no attribute 'complete'".
    model_provider = resolve_provider_for_use_case(
        "chat",
        session_key=session_key,
        agent=agent,
        model_override=model_override,
        cwd=cwd,
        _force_model_axis=True,
        _model_axis_only=True,
        **kwargs,
    )
    if not hasattr(model_provider, "complete"):
        raise ProviderResolutionError(
            f"Native agent {name!r} resolved its inference model to "
            f"{type(model_provider).__name__}, which is not a ModelProvider "
            f"(no complete()). Bind the 'chat' use case to a model provider "
            f"(Settings → Models), not an ACP agent runtime."
        )

    # Pull the agent's persona/model/tools/skills from its profile when present.
    # Strip any "<provider>:" prefix so the bare model id reaches complete()
    # (the inner ModelProvider is resolved above; this is the id label the SDK
    # call uses — a "Bedrock:…" ref here means an invalid AWS model identifier).
    name = agent or "PersonalClaw"
    system_prompt = ""
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
            # Voice layer (#42): WHO the agent is, injected HIGH-PRIORITY (before the
            # operating rules) so its personality survives a long system prompt.
            from personalclaw.config.loader import _compose_voice

            system_prompt = _compose_voice(getattr(prof, "voice", ""), getattr(prof, "system_prompt", "") or "")
            # Heal a stale pin: an explicit agent model (or per-turn override)
            # that's no longer active reconciles to "" → the chat-binding
            # fallback below. Both the override and the profile pin may be the
            # "<provider>:model" ref a chat session stores; reconcile BOTH so a
            # ref naming an uninstalled provider doesn't slip through as a bare
            # (dead) model id.
            model = _strip_provider_prefix(_reconcile_agent_model(model_override or "")) \
                or _strip_provider_prefix(_reconcile_agent_model(getattr(prof, "model", "") or ""))
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
        model = _fallback_chat_model(provider_hint=_provider_entry_name(model_provider))

    definition = AgentRuntimeDefinition(
        name=name,
        provider="native",
        system_prompt=system_prompt,
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
        async def hook_fire(tool_name: str, args_json: str | None) -> list[str]:
            from personalclaw.hooks import HOOK_EVENT_PRE_TOOL_USE, get_global_hook_store

            store = get_global_hook_store()
            if store is None:
                return []
            try:
                tool_input = json.loads(args_json) if args_json else None
            except (ValueError, TypeError):
                tool_input = None
            results = await store.fire_for_ids(
                HOOK_EVENT_PRE_TOOL_USE, hook_ids,
                tool_name=tool_name, tool_input=tool_input,
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
        cwd=_cwd, agent=name or "", session_key=session_key or "",
        extra_roots=[Path(r) for r in (extra_tool_roots or [])],
        categories=PLATFORM_CATEGORIES,
        provider_name="personalclaw-filesystem",
        display="Filesystem & Shell Tools",
    )
    tool_providers = [platform, *_list_tool_providers()]

    return NativeAgentRuntime(
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
    # background turn can't wedge waiting for a human (T5). Pop unconditionally so
    # it never leaks into the model-axis resolvers; meaningful only to the native
    # builder. The "auto"/"yolo" approval policy is a separate, complementary lever
    # (it auto-approves) — unattended is about never blocking, set independently.
    _unattended = bool(kwargs.pop("unattended", False))
    # Dry-run replay (T9): observe-mode — write-capable tools return a synthetic
    # observation instead of executing. Pop unconditionally (native-only).
    _dry_run = bool(kwargs.pop("dry_run", False))
    # The Project this session's work scopes under. Pop unconditionally so it never
    # leaks into the model-axis resolvers; meaningful only to the native builder,
    # which binds it per-turn so artifact_save can stamp the artifact's project_id (S5).
    _project_id = str(kwargs.pop("project_id", "") or "")
    # Per-turn reasoning effort. The native builder consumes it (forwarded to the
    # model's complete()); the ACP path reads reasoning_effort_override from kwargs
    # in its own factory, so DON'T pop it here for ACP — peek without removing.
    _reasoning_effort = str(kwargs.get("reasoning_effort_override") or "")
    _kind = (
        ("acp" if str(_provider_kind).startswith("acp") else "native")
        if _provider_kind
        else _agent_provider_kind(agent)
    )
    if (
        not _force_model_axis
        and use_case in ("chat", "code_tools")
        and _kind == "native"
    ):
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

    # The active selection (Settings → Models) pins resolution to a specific
    # configured provider + model. A chat sub-category with no model of its own
    # borrows the parent ``chat`` selection (active_model_refs handles that).
    _refs = list(active_model_refs(use_case))
    for ref in _refs:
        parsed = split_ref(ref)
        if not parsed:
            continue
        provider_name, model_id = parsed
        pinned = _resolve_from_config_registry(
            capability,
            session_key=session_key,
            agent=agent,
            model_override=model_id,
            cwd=cwd,
            provider_hint=provider_name,
            **kwargs,
        )
        if pinned is not None:
            return pinned
        # The selection names a provider the config registry can't build — its app
        # isn't installed / configured. Per the "block, don't silently fall back"
        # rule (a stale Bedrock pin must NOT be handed to Ollama as a literal model
        # id → 404), raise a clear, actionable error instead of the implicit
        # fallback. The user fixes it by installing the provider or picking another
        # in Settings → Models.
        raise ProviderResolutionError(
            f"The model selected for {use_case!r} ({ref!r}) isn't available — its "
            f"provider {provider_name!r} isn't installed or configured. Install it "
            f"in the App Store, or pick a different model in Settings → Models."
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
        f"Add a model provider in Settings → Providers."
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
    if model_override and ":" in model_override and any(
        e.name == model_override.split(":", 1)[0] for e in entries
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
    for entry in entries:
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
            from personalclaw.llm.credentials import CredentialStore
            from personalclaw.config import config_dir
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
            _synth = Credential(name=candidate.name, kind="api_key", secret=inline_key, source="file")
            build_kwargs["_inline_credential"] = _synth
    try:
        return registry.build(candidate.name, session_key=session_key, cwd=cwd, agent=agent, **build_kwargs)
    except Exception:
        logger.exception(
            "Config-registry fallback failed to build provider %r for %s",
            candidate.name, use_case,
        )
        return None


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

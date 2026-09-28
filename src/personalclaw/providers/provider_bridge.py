"""Provider Bridge — resolves a use case to a live ModelProvider instance.

The model is:

1. Read the active selection for the use case from ``active_models.json``
   (Settings → Models) — a ``"<provider_name>:<model_id>"`` ref.
2. Resolve that provider from the config.json ``providers[]`` registry
   (``default_registry``), pinning to the selected model.
3. Fall back to the first configured provider declaring the capability when no
   model is selected, built with its own model (``ProviderEntry.own_model``). One that
   names no model is never that fallback: the call is refused, never sent an empty model
   or served by a model its provider picked.

The bridge exports a single function ``create_provider_factory()`` that returns
a callable matching the factory signature::

    factory(session_key=None, agent=None, model_override=None, ...) -> ModelProvider
"""

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from personalclaw.errors import AgentError
from personalclaw.llm.base import ModelProvider, ModelSubstitution

if TYPE_CHECKING:
    from personalclaw.agents.native.failover import ModelFailover

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
        # A default-agent pin that cannot serve (a stale "Bedrock:…" after the provider was
        # removed) must NOT be returned — it would be handed to whatever provider actually
        # resolves (→ wrong-provider 404). The one availability rule decides, and a pin it
        # refuses falls through to the active chat selection below. Nothing is substituted for
        # a choice here: this picks a model for an agent that named NONE.
        pin = str(getattr(prof, "model", "") or "") if prof else ""
        raw = pin if pin and named_model_problem(pin) is None else ""
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


def named_model_problem(ref: str, *, use_case: str = "chat") -> tuple[str, str] | None:
    """Why the model ``ref`` names cannot serve ``use_case`` now, as ``(why, fix)``; else ``None``.

    THE rule for a model someone chose by name — an agent's pin, a chat's own pick. It replaces a
    heal that turned such a pin into "" with a log line, so the chat binding answered while every
    surface went on showing the model the user chose. Two questions, the second allowed to assume
    the first:

    1. Is it one of the chat models set up in Settings → Models — the list the Agents page and the
       composer offer? A pin outside it names a model the user removed or renamed, or never had,
       and handing it to a provider is the 400/404 the heal existed to avoid. Not asked while no
       chat model is set up: there is no list to be outside of.
    2. Can its provider serve it now? The entry exists, its type is registered, it declares the
       capability, its readiness probe agrees and its credential has a secret: the questions the
       resolver asks before it builds, answered in ``_diagnose_unbuildable_ref``'s words, so this
       answer and a refusal at resolution time cannot disagree.

    ``fix`` is about the model, not about whoever pinned it; callers add their own "pick another
    model on the Agents page".
    """
    if not ref:
        return None
    active = _active_chat_model_ids()
    if active and ref not in active:
        return (
            "it is not one of the chat models set up in Settings → Models",
            "add it in Settings → Models",
        )
    qualified = _qualified_chat_ref(ref)
    parsed = qualified.split(":", 1) if ":" in qualified else None
    if not parsed:
        return None
    provider_name, model_id = parsed
    from personalclaw.providers.use_cases import parent_capability

    capability = parent_capability(use_case)
    can_serve = _named_entry_can_serve(provider_name, capability)
    # No entry by that name is only innocent for a string that is not a ref at all (a bare id
    # holding a colon, while nothing is set up to qualify it). A chat chain entry IS a ref, so its
    # provider being gone is the reason it cannot serve.
    if can_serve is True or (can_serve is None and qualified not in active):
        return None
    return _diagnose_unbuildable_ref(provider_name, model_id, use_case, capability)


def agent_model_problem(profile: Any, cfg: Any) -> dict[str, str] | None:
    """``{why, fix}`` when the agent ``profile``'s pinned model cannot run, else ``None``.

    For the surfaces that show an agent's pin — the Agents page and a room's members panel — so
    they can say it cannot run where the pin is shown. The same rule the runtime applies
    (:func:`named_model_problem`), so a surface and a turn cannot disagree about one pin. An ACP
    agent's model is its CLI's own id and outside this rule; no pin, no problem.
    """
    pin = str(getattr(profile, "model", "") or "")
    runtime = str(
        getattr(profile, "provider", "")
        or getattr(getattr(cfg, "agent", None), "provider", "")
        or ""
    )
    if not pin or runtime.startswith("acp"):
        return None
    try:
        problem = named_model_problem(pin)
    except Exception:  # noqa: BLE001 — a read surface must not fail over one agent's diagnosis
        logger.debug("could not check agent model %r", pin, exc_info=True)
        return None
    if problem is None:
        return None
    why, fix = problem
    return {"why": why, "fix": fix}


def _qualified_chat_ref(ref: str) -> str:
    """``ref`` as the ``"<entry>:<model>"`` ref of the chat chain entry it names.

    A pin written before refs were qualified is a bare model id; the chain entry carrying that id
    says which provider it belongs to, and without it the id would be sent to whichever provider
    heads the chain. A ref that is already qualified, or that no chain entry carries, is returned
    as it is.
    """
    try:
        from personalclaw.llm.registry import get_default_registry
        from personalclaw.providers.use_cases import active_model_refs, split_ref

        if ":" in ref and any(
            e.name == ref.split(":", 1)[0] for e in get_default_registry().list_entries()
        ):
            return ref
        for chain_ref in active_model_refs("chat"):
            parsed = split_ref(chain_ref)
            if parsed and parsed[1] == ref:
                return chain_ref
    except Exception:  # noqa: BLE001 — an unreadable chain leaves the ref as written
        logger.debug("could not qualify model ref %r", ref, exc_info=True)
    return ref


def _named_entry_can_serve(
    provider_name: str, capability: str, *, model_axis_only: bool = False
) -> bool | None:
    """Can the registry entry ``provider_name`` serve ``capability`` now, WITHOUT building it?

    ``None`` when no entry has that name: then ``provider_name`` is not a provider at all, and a
    ``"<x>:<y>"`` string is a bare model id that happens to hold a colon (``gpt-oss:20b``). The
    conditions are ``_resolve_from_config_registry``'s own for a named candidate, plus the
    credential, so a caller asking here and a resolution that builds agree about the same entry.
    """
    try:
        from personalclaw.llm.registry import get_default_registry

        registry = get_default_registry()
        entry = next((e for e in registry.list_entries() if e.name == provider_name), None)
    except Exception:  # noqa: BLE001 — an unreadable registry cannot name a provider
        logger.debug("registry unreadable while checking %r", provider_name, exc_info=True)
        return None
    if entry is None:
        return None
    target_cap = _capability_enum(capability)
    return (
        target_cap is not None
        and not (model_axis_only and entry.type == "acp_agent")
        and target_cap in _entry_capabilities(registry, entry)
        and registry.not_ready(entry, implicit=False) is None
        and not (entry.credential and _credential_is_missing(str(entry.credential)))
    )


def _named_override_refusal(
    model_override: str, use_case: str, capability: str
) -> "ProviderResolutionError | None":
    """The refusal for a provider-qualified ``model_override`` its named provider cannot serve.

    A caller that NAMES a model gets that model or a refusal that names it. Resolution used to
    fall through to the use case's chain instead, so a pinned judge ran on the head of the chain —
    the family its isolation excluded — and each entry of a chain walk that could not be built was
    silently re-served by the chain from its head. ``None`` when the prefix names no provider
    entry: then the override is a bare model id containing ``:`` or ``/``, and resolving it
    through the chain is what it has always meant.
    """
    for sep in (":", "/"):
        if sep not in model_override:
            continue
        provider_name, model_id = model_override.split(sep, 1)
        if _named_entry_can_serve(provider_name, capability) is None:
            continue
        why, fix = _diagnose_unbuildable_ref(provider_name, model_id, use_case, capability)
        return ProviderResolutionError(
            f"The model {model_override!r} isn't available. {fix}.",
            AgentError(
                code="ERR_MODEL_UNRESOLVED",
                what=f"the model {model_override!r} was asked for by name and cannot be built",
                why=why,
                fix=fix,
            ),
        )
    return None


def substitution_reason(exc: BaseException) -> tuple[str, str]:
    """``(why, fix)`` for a named model that failed to serve, from the failure itself."""
    agent_error = getattr(exc, "agent_error", None)
    why = str(getattr(agent_error, "why", "") or "")
    if why:
        return why, str(getattr(agent_error, "fix", "") or "")
    text = str(exc).strip() or type(exc).__name__
    return f"it failed ({type(exc).__name__}: {text})"[:300], ""


def stamp_substitution(provider: object, substitution: ModelSubstitution | None) -> None:
    """Stamp ``substituted_for`` on a provider serving in a named model's place.

    A plain attribute, like ``served_ref``'s stamp, and NOT a field of the SDK's ``ModelProvider``:
    the guard declares it (``ModelCallGuard.substituted_for``) and copies it onto every call it
    records, and the native runtime reads it off its inner provider. An object that refuses the
    attribute keeps no stamp, and its calls simply name the model that served.
    """
    if substitution is None:
        return
    try:
        provider.substituted_for = substitution  # type: ignore[attr-defined]
    except (AttributeError, TypeError):
        logger.debug("%s does not accept a substitution stamp", type(provider).__name__)


@dataclass(frozen=True)
class ResolutionBasis:
    """What a native runtime's model was resolved from — what a rebind or an instance edit changes.

    A runtime is built once and then cached per session (``SessionManager``), and it fixes its
    model when it is built. So without this, rebinding chat in Settings → Models reached a new
    chat and nothing already open: the persistent background session kept the model chat was
    bound to when the gateway started, for every title, follow-up, suggestion, history
    compression and memory consolidation after it, and an open chat that has no model of its
    own kept answering on the old binding. ``SessionManager`` asks :meth:`holds` when it reuses
    a runtime, and rebuilds one whose basis moved, at its next acquire.

    ``chains`` are the Settings → Models chains the resolution read: the runtime's own axis
    (``active_model_refs`` — the chat chain while that axis is unbound) and chat's, which a
    chat's or an agent's own pick is checked against (``named_model_problem``). ``entry`` is the
    registry entry the model is served from, compared by IDENTITY: an edit in Settings →
    Providers (a new Default Model, endpoint or key) re-registers the entry, and a removal
    drops it, so either reads as moved.
    """

    axis: str
    chains: tuple[tuple[str, ...], tuple[str, ...]]
    entry: object | None = None

    @classmethod
    def read(cls, axis: str) -> "ResolutionBasis":
        """The chains as they read now, for a runtime about to resolve on ``axis``."""
        return cls(axis=axis, chains=_basis_chains(axis))

    def served_from(self, served_ref: str) -> "ResolutionBasis":
        """This basis, pinned to the registry entry ``served_ref`` names (``""`` pins none)."""
        name = served_ref.partition(":")[0]
        if not name:
            return self
        try:
            from personalclaw.llm.registry import get_default_registry

            entry = next((e for e in get_default_registry().list_entries() if e.name == name), None)
        except Exception:  # noqa: BLE001 — an unreadable registry pins nothing
            logger.debug("resolution basis: registry unreadable for %r", name, exc_info=True)
            entry = None
        return ResolutionBasis(axis=self.axis, chains=self.chains, entry=entry)

    def holds(self) -> bool:
        """Whether a resolution now would read what this one read. A probe that fails holds:
        a broken read must not rebuild every open session."""
        try:
            if _basis_chains(self.axis) != self.chains:
                return False
            if self.entry is None:
                return True
            from personalclaw.llm.registry import get_default_registry

            return any(e is self.entry for e in get_default_registry().list_entries())
        except Exception:  # noqa: BLE001 — see the docstring
            logger.debug("resolution basis probe failed for %s", self.axis, exc_info=True)
            return True


def _basis_chains(axis: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    from personalclaw.providers.use_cases import active_model_refs

    return tuple(active_model_refs(axis)), tuple(active_model_refs("chat"))


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
    the lite factory, "loops" for a loop's worker and planner, "orchestration"
    for subagent spawns and the other agent turns nobody typed, else the
    session's own use case — so a sub-category binding governs native agents too
    (previously the inner model hardcoded "chat", making e.g. a code_tools binding
    cosmetic). A model the caller names still serves; it rides BESIDE the axis,
    because the axis also decides whether the inner model is metered — only the
    non-interactive axes are wrapped by the spend guard (below, in
    ``resolve_provider_for_use_case``). Tools come from the in-process core provider.
    """
    from pathlib import Path

    from personalclaw.agents.native.builtin_tools import (
        PLATFORM_CATEGORIES,
        PLATFORM_DISPLAY_NAME,
        PLATFORM_PROVIDER_NAME,
        NativeBuiltinToolProvider,
    )
    from personalclaw.agents.native.runtime import NativeAgentRuntime
    from personalclaw.agents.provider import AgentRuntimeDefinition

    name = agent or "PersonalClaw"
    # The agent's profile, read ONCE: its pin decides which provider is resolved below, and its
    # tools, skills and triggers ride the definition. Its PROMPT is not read here: the system
    # prompt reaches the model through the turn's assembled context
    # (``ContextBuilder.build_message``), the one place it is resolved.
    prof = None
    try:
        from personalclaw.config.loader import AppConfig

        prof = (AppConfig.load().agents or {}).get(agent) if agent else None
    except Exception:  # noqa: BLE001 — an unreadable config leaves the agent unpinned
        logger.debug("agent profile unreadable for %r", agent, exc_info=True)

    # The model this runtime was ASKED for, in precedence order: the chat's own pick, then the
    # agent's pin. The first that can serve is the one it runs on, provider included: a pin naming
    # the SECOND provider of the chat chain used to be served by the chain's head with the pin's
    # model id borrowed, a model that provider does not offer. A choice that cannot serve is SAID,
    # never healed away: an agent has no strict setting, so the turn runs on the chat binding and
    # the runtime carries the substitution for the chat, the room and the Agents page to show.
    choices: list[tuple[str, str, str]] = []
    if model_override:
        choices.append(
            (
                model_override,
                "this chat's model",
                "pick another model for this chat in the composer",
            )
        )
    pin = str(getattr(prof, "model", "") or "") if prof is not None else ""
    if pin:
        choices.append(
            (pin, f"{name}'s model", f"pick another model for {name} on the Agents page")
        )
    chosen = ""
    missed: tuple[str, str, str, str] | None = None  # (requested, who, why, fix)
    for ref, who, pick_fix in choices:
        problem = named_model_problem(ref)
        if problem is None:
            chosen = _qualified_chat_ref(ref)
            break
        if missed is None:
            why, fix = problem
            missed = (ref, who, why, f"{pick_fix}, or {fix}")

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
    # Read BEFORE resolving: a rebind that lands while this builds then reads as moved, and the
    # runtime is rebuilt at its next acquire rather than kept on what it was built from.
    basis = ResolutionBasis.read(inner_axis)

    def _resolve(override: str | None) -> ModelProvider:
        return resolve_provider_for_use_case(
            inner_axis,
            session_key=session_key,
            agent=agent,
            model_override=override,
            cwd=cwd,
            _force_model_axis=True,
            _model_axis_only=True,
            **kwargs,
        )

    try:
        model_provider = _resolve(chosen or None)
    except ProviderResolutionError as exc:
        if not chosen:
            raise
        # The chosen model passed every question that can be asked without building it, and the
        # build still refused. Said the same way, and served by the chat binding.
        why, fix = substitution_reason(exc)
        who, pick_fix = next((w, f) for r, w, f in choices if _qualified_chat_ref(r) == chosen)
        if missed is None:
            missed = (chosen, who, why, f"{pick_fix}, or {fix}" if fix else pick_fix)
        chosen = ""
        model_provider = _resolve(None)
    if not hasattr(model_provider, "complete"):
        raise ProviderResolutionError(
            f"Native agent {name!r} resolved its inference model to "
            f"{type(model_provider).__name__}, which is not a ModelProvider "
            f"(no complete()). Bind the 'chat' use case to a model provider "
            f"(Settings → Models), not an ACP agent runtime."
        )

    # The id ``complete()`` is sent: the chosen ref without its "<provider>:" prefix (a
    # "Bedrock:…" ref here is an invalid AWS model identifier), the provider above being the one
    # that ref names.
    model = _strip_provider_prefix(chosen) if chosen else ""
    tools: list[str] = list(getattr(prof, "tools", []) or []) if prof is not None else []
    skills: list[str] = list(getattr(prof, "skills", []) or []) if prof is not None else []
    hook_ids: list[str] = list(getattr(prof, "triggers", []) or []) if prof is not None else []

    # An agent with no model of its own (the hidden ``personalclaw-lite``
    # background agent, the goal loop worker's "inherit chat" default, or any
    # user agent left on "Agent default") would otherwise hand the OpenAI client
    # an empty model string and 400. Resolve a concrete chat model in that case.
    #
    # The model id is passed to the ALREADY-RESOLVED ``model_provider`` above and
    # overrides its own pinned id, so it MUST name the same provider. The ref the seam
    # built it for says both halves (``served_ref``, stamped where they are known): after
    # the chain walk skipped the head (an open breaker, an entry that cannot be built),
    # that is the LATER entry, and "the first ref of the chain" named the head's model
    # to it. Only an unstamped provider falls back to that ordering
    # (``_provider_entry_name``). Without agreement the model of one provider is sent to
    # another (e.g. Alibaba's ``glm-5.2`` to the Bedrock client → "model identifier is
    # invalid", which failed every background suggestions/consolidation turn).
    if not model:
        served_entry, _, served_model = str(
            getattr(model_provider, "served_ref", "") or ""
        ).partition(":")
        model = served_model or _fallback_chat_model(
            provider_hint=served_entry or _provider_entry_name(model_provider, use_case=inner_axis),
            use_case=inner_axis,
        )

    cwd = _native_session_cwd(cwd)
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
    #
    # Except the lite agent, which gets NONE. It runs the background chores (titles, follow-ups,
    # suggestions, folder icons, history compression, memory consolidation, the prompt optimizer),
    # each of which answers in text from what its prompt carries, and that prompt quotes chats,
    # pages and messages nobody vetted. With a tool surface, text planted in a chat could make a
    # title turn record a decision, write a file or run a command. With none, there is nothing to
    # call: the model is offered no tools, and a call it makes anyway names a tool that does not
    # exist.
    from personalclaw.agents.defaults import LITE_AGENT_NAME
    from personalclaw.tool_providers.registry import tool_surface

    tool_providers: list[Any] = []
    if name != LITE_AGENT_NAME:
        platform = NativeBuiltinToolProvider(
            cwd=_cwd,
            agent=name or "",
            session_key=session_key or "",
            extra_roots=[Path(r) for r in (extra_tool_roots or [])],
            categories=PLATFORM_CATEGORIES,
            provider_name=PLATFORM_PROVIDER_NAME,
            display=PLATFORM_DISPLAY_NAME,
        )
        tool_providers = tool_surface(platform)

    runtime = NativeAgentRuntime(
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
        # Tool groups. ``surface`` is the session class whose
        # per-surface defaults seed activation — the SAME axis label that governs
        # the inner model, so "background"/"loops"/"orchestration" runs start
        # focused while interactive chat keeps every group active (zero change).
        # ``tool_groups`` is the explicit per-template override (the engine's
        # stage-spawn seam): when given it wins over the surface default.
        tool_groups=list(tool_groups) if tool_groups is not None else None,
        surface=inner_axis,
        # A workflow stage's lineage and posture (`engine.leaf_spawn_env`), which a CLI runtime
        # gives its tool server as the environment. This runtime's tools run in-process, so it
        # binds them per call instead; without them a native stage was no leaf to its own tools.
        leaf_lineage=kwargs.get("extra_env") or None,
    )
    # What serves in place of a choice, named with the ref that actually answers — the runtime's
    # own ``served_model_ref``, so the sentence cannot name a model the turn did not run on. With
    # no choice missed, a chain entry serving in place of the chain's head (stamped on the inner
    # provider by the chain walk) is carried instead: it is the user's configured fallback, and
    # it is said the same way.
    if missed is not None:
        requested, who, why, fix = missed
        runtime.model_substitution = ModelSubstitution(
            requested=requested,
            served=runtime.served_model_ref or "the chat binding",
            why=why,
            fix=fix,
            who=who,
        )
    else:
        stamped = getattr(model_provider, "substituted_for", None)
        runtime.model_substitution = stamped if isinstance(stamped, ModelSubstitution) else None
    # Where a turn may go when this model fails before any output. Not for an unattended run:
    # nobody reads a live line there, and #3648's rule is that a substitute is always said.
    if not unattended:
        runtime.failover = _turn_failover(inner_axis, choices, runtime.served_model_ref, _resolve)
    runtime.resolved_from = basis.served_from(runtime.served_model_ref)
    return runtime  # type: ignore[return-value]  # CI-2


def _turn_failover(
    axis: str,
    choices: list[tuple[str, str, str]],
    served: str,
    resolve: Callable[[str], ModelProvider],
) -> "ModelFailover | None":
    """The models a turn on ``served`` may fall back to when it fails before any output.

    Those after it in the order a native runtime picks its model: the chat's own pick, the agent's
    pin (each only while it can run, :func:`named_model_problem`), then the use case's chain in
    Settings → Models. ``None`` when nothing comes after it, or ``served`` is not in that order at
    all (a provider built outside the resolution seam), so nothing is guessed.
    """
    from personalclaw.agents.native.failover import ModelFailover
    from personalclaw.llm_helpers import failure_clause, use_case_chain

    order: list[str] = []
    whose: dict[str, str] = {}
    for ref, who, _fix in choices:
        qualified = _qualified_chat_ref(ref)
        if qualified not in order and named_model_problem(ref) is None:
            order.append(qualified)
            whose[qualified] = who
    order.extend(ref for ref in use_case_chain(axis) if ref not in order)
    if not served or served not in order:
        return None
    later = tuple(order[order.index(served) + 1 :])
    if not later:
        return None

    async def takes_images(ref: str) -> bool:
        from personalclaw.providers.image_input import image_input

        return (await image_input(ref)).accepted

    return ModelFailover(
        requested=served,
        who=whose.get(served, ""),
        candidates=later,
        build=lambda ref: (resolve(ref), _strip_provider_prefix(ref)),
        describe=failure_clause,
        takes_images=takes_images,
    )


#: The folder in the home's own workspace that holds a scratch folder per session with no usable
#: workspace (``_native_session_cwd``).
_NO_WORKSPACE_SCRATCH = "scratch"


def _native_session_cwd(cwd: str | None) -> str:
    """The directory a native session's file and shell tools are rooted in — never an ambient one.

    🔴 With no explicit ``cwd`` the platform tool provider fell back to ``Path.cwd()``: the
    GATEWAY PROCESS's own working directory. Every native session created without one — a
    workflow stage's subagent on a project-less run, a background session — therefore read and
    wrote relative to wherever the gateway happened to be started. Measured on a General loop run
    unattended: its worker wrote ``checklist.md`` into the repository checkout the gateway was
    launched from, while the run page said it had looked in the run's own directory. For a gateway
    started from the home directory that is ``~``; for a service manager it can be ``/``. Either
    way the loop's result lands somewhere the user cannot find, which is the family the ACP spawn
    path already refuses (``session._acp_spawn_cwd``).

    So a native session defaults to the same place an ACP one does: the configured workspace root
    (``default_workspace_dir()`` — validated to exist and not to be a sensitive path), which is
    also where a new chat, the Terminal and the Files page open. When no safe workspace resolves,
    a fresh private scratch directory stands in rather than the process cwd, and the reason is
    logged — a session whose tools are rooted in an empty directory is recoverable; files written
    into an ambient one are not. It is in the home's own workspace (``workspace/scratch``), which
    the durability inventory claims, so what a session writes there is backed up with the rest of
    the workspace and goes with the home.
    """
    explicit = str(cwd or "").strip()
    if explicit:
        return explicit
    from personalclaw.config.loader import default_workspace_dir

    default = str(default_workspace_dir() or "").strip()
    if default:
        return default
    import tempfile

    from personalclaw.config.loader import default_workspace_root

    # Inside the home's own workspace, where the durability inventory claims it and the home's
    # removal takes it: a folder in the system temp folder outlived every session made there.
    parent = default_workspace_root() / _NO_WORKSPACE_SCRATCH
    parent.mkdir(parents=True, exist_ok=True)
    scratch = tempfile.mkdtemp(prefix="no-workspace-", dir=parent)
    logger.warning(
        "native session: no usable workspace root resolved (PERSONALCLAW_WORKSPACE, or the "
        "workspace directory in Settings); its tools are rooted in the scratch directory %s "
        "instead of the gateway's own working directory",
        scratch,
    )
    return scratch


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
    from personalclaw.config.loader import config_dir
    from personalclaw.llm.credentials import CredentialStore

    try:
        CredentialStore(config_dir()).resolve(name)
    except KeyError:  # not stored, or an owned key nothing reads by name
        return True
    except Exception:  # noqa: BLE001 — an unreadable store is not evidence of a missing key
        return False
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
    7. the ref names no model, and the entry names none of its own.
    8. anything left — which it states as *unsure*, with the model id to check and the log
       line to read, rather than asserting a specific wrong cause.

    "The model name is not offered by that provider" is deliberately NOT a branch:
    resolution passes ``model_override`` through to the factory unvalidated, so a wrong
    model id does not make this function return ``None``. When a factory rejects it, the
    build failure lands in branch 8, which is why that branch names the model id.
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
                f"the type of {provider_name!r} in Settings → Providers",
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
            f"check the gateway log for the import error that stopped {app_name!r} loading, "
            f"or {rebind}",
        )

    if target_cap not in (entry.declared_capabilities or type_capabilities):
        return (
            f"provider {provider_name!r} (type {entry.type!r}) does not declare the "
            f"{capability!r} capability that use case {use_case!r} needs",
            f"{rebind}, or bind {use_case!r} to a provider that declares {capability!r}",
        )

    # The provider type's OWN answer to "can this entry serve right now?" — e.g. a model that is
    # registered and configured but has not been downloaded yet. Its words, not a paraphrase:
    # the type is the only thing that knows what is missing and how to get it.
    unready = registry.not_ready(entry, implicit=False)
    if unready is not None:
        return unready

    credential = str(entry.credential or "")
    if credential and _credential_is_missing(credential):
        return (
            f"provider {provider_name!r} needs credential {credential!r}, which has no "
            f"secret in the credential store",
            f"store {credential!r} in Settings → Secrets, or {rebind}",
        )

    # The ref names no model (a stored ``"<entry>:"``) and the entry names none of its own, so
    # there was nothing to build it for (``_served_model``).
    if _served_model(entry, model_id) is None:
        from personalclaw.llm.registry import no_model_chosen

        return no_model_chosen(provider_name)

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
       declaring the requested capability and naming a model of its own — picks the
       first, built with that model. Avoids forcing the user to set a selection when
       only one sensible provider exists; a provider that names no model refuses the
       call instead (``_implicit_unready``). Never for
       image understanding, which no provider-type declaration can pick a model for
       (``_implicit_candidates``): an unbound image reader is resolved by name through
       :func:`personalclaw.providers.image_input.resolve_image_reader`.
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
    # which binds it per-turn so artifact_save can stamp the artifact's project_id.
    _project_id = str(kwargs.pop("project_id", "") or "")
    # The chat sub-category whose CHAIN governs this session's INNER model
    # (MODEL-USE-CASES-V2 T2.x): the _bg factory passes "background", a loop's worker
    # and planner "loops", subagent spawns and webhook agent turns "orchestration" (a
    # model the caller names rides beside the axis, never instead). Defaults to the
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
    # interactive session stays clamped. Restricted to _kind == "acp" on
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
    # path that never got one — this is gap 6's actual content.
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
    # Model-call guard: every NON-INTERACTIVE text axis
    # (``reasoning`` / ``background`` / ``loops`` / ``orchestration`` — backing
    # one_shot_completion, the lite background factory, loop workers/judges/gates,
    # and every subagent spawn and agent turn nobody typed — the census of those is
    # tests/test_automation_spend_is_metered.py) routes every resolved provider through
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
        # None with a prefix that DOES name a provider entry: that provider cannot serve the model
        # the caller named. Refused by name — falling through would serve the chain's head instead,
        # which is how a pinned judge ran on the model its isolation excluded.
        refusal = _named_override_refusal(model_override, use_case, capability)
        if refusal is not None:
            raise refusal

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
    _bound_head = _refs[0] if _refs else ""
    # ── Step (2) routing seam ──
    # ONE call, ONE site, immediately before the active-ref loop: route_refs is a PURE REORDER of
    # the refs the user bound — it never invents, adds, or drops a candidate, so everything
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
    # The chain HEAD the user bound, before any routing reorder, and why it was skipped when it
    # was. A later entry that serves after the head was skipped serves in its place, and says so
    # ("ran on <entry> instead of <head>"): the user configured that fallback, and the step and
    # Introspect otherwise named only the entry that answered. A reorder the ROUTER chose is not a
    # substitution — the head served nothing because it was never tried first.
    _head = _bound_head
    _head_skipped: tuple[str, str] | None = None  # (why, fix)
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
                if ref == _head:
                    _head_skipped = (
                        f"calls to {provider_name!r} kept failing, so its circuit breaker is open",
                        "it is tried again automatically once the breaker recovers",
                    )
                continue
        except Exception:  # noqa: BLE001 — breaker introspection must never break resolution
            pass
        # Routing provenance: a routed resolution stamps ``routed`` on every attempt, and
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
            if _head_skipped is not None and ref != _head:
                why, fix = _head_skipped
                stamp_substitution(
                    pinned,
                    ModelSubstitution(
                        requested=_head,
                        served=str(getattr(pinned, "served_ref", "") or ref),
                        why=why,
                        fix=fix,
                    ),
                )
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
            if ref == _head:
                _head_skipped = _diagnose_unbuildable_ref(
                    provider_name, model_id, use_case, capability
                )
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

    # Image understanding with nothing bound: no implicit pick can name a model that reads images
    # (see ``_implicit_candidates``), and the chat-model fallback is ``image_input.image_reader``'s
    # to make, by name. Reaching here means neither holds, so the refusal says what does.
    from personalclaw.llm.capabilities import Capability

    if _capability_enum(capability) is Capability.VISION:
        raise ProviderResolutionError(
            "No image model is set up. Choose one in Settings → Models.",
            AgentError(
                code="ERR_MODEL_UNRESOLVED",
                what=f"no model is set up to read images (use case {use_case!r})",
                why=(
                    "nothing is bound to image understanding, and a provider that carries images "
                    "does not say which of its models reads them"
                ),
                fix=(
                    "choose an image-understanding model in Settings → Models, or chat with a "
                    "model that takes images"
                ),
            ),
        )

    # Nothing READY declares the capability. When something does declare it but cannot serve
    # (it names no model of its own, or its type says a model is not downloaded yet), that is
    # the true cause and the only one with a fix a user can act on — "no provider declares the
    # capability" would be false about a home that has one. ``what`` stays the no-model sentence
    # on purpose: from the user's side no model is set up yet, and the chat surface's calm setup
    # state keys on it (and names the provider when the cause is that none is chosen for it).
    unready = _first_unready_candidate(capability)
    raise ProviderResolutionError(
        f"No provider configured for use case {use_case!r}. "
        f"Add a model provider in Settings → Providers.",
        AgentError(
            code="ERR_MODEL_UNRESOLVED",
            what=f"no model provider resolves for use case {use_case!r}",
            why=(
                unready[0]
                if unready
                else "no provider in config.json declares the capability this use case needs"
            ),
            fix=(
                unready[1]
                if unready
                else f"add a model provider in Settings → Providers, then bind {use_case!r} to it"
            ),
        ),
    )


def _entry_capabilities(registry: Any, entry: Any) -> frozenset:
    """What ``entry`` can do: its own declaration, else its registered type's descriptor.

    The fail-soft read every candidate walk uses — an entry whose type is not registered (its
    app loads later on some boot paths) declares nothing rather than raising.
    """
    caps = entry.declared_capabilities
    if caps:
        return frozenset(caps)
    try:
        return frozenset(registry.capability_of(entry.type).capabilities)
    except Exception:
        return frozenset()


def _implicit_candidates(registry: Any, target_cap: Any, *, skip_agent_runtimes: bool) -> list[Any]:
    """The entries the implicit "nothing is bound" fallback may choose, in the order it tries.

    ONE walk for the resolver (:func:`_resolve_from_config_registry`), the no-instantiate probe
    (:func:`can_resolve_use_case`) and :func:`serving_entry`, so the probe cannot call a use case
    resolvable through an entry the resolver would refuse. An entry is a candidate when it
    declares the capability AND its type reports it ready for implicit use
    (:meth:`~personalclaw.llm.registry.ProviderRegistry.not_ready`).

    A zero-config FLOOR entry sorts LAST. Registration order would otherwise decide this the
    wrong way round: an app that registers a floor does so while its module is imported
    (``register_extension_providers``), which runs BEFORE ``sync_entries_from_config()`` replays
    the user's own ``config.json`` rows — so the floor would be "the first entry declaring the
    capability" and would beat every provider the user actually configured. ``sorted`` is
    stable, so non-floor entries keep their registration order exactly. Same rule, same reason,
    as the search registry's keyless floor (``search_providers/registry.py``: "a provider that
    declares itself ``keyless`` sorts last among candidates so a user-configured/keyed provider
    always wins").

    Image understanding has NO implicit candidate. Reading an image is a property of a MODEL: a
    type that declares vision says its wire can carry an image, not which of its models reads one,
    so this walk could only hand back an entry built with its own ``model`` — empty for an instance
    saved from the Add-instance form, which Ollama refused ("model is required"), and a text-only
    model otherwise. With nothing bound, the chat model reads images when it takes them:
    :func:`personalclaw.providers.image_input.image_reader` asks, and resolves it by name.

    Nor is an entry that names no model of its own a candidate (:func:`_implicit_unready`).
    """
    from personalclaw.llm.capabilities import Capability

    if target_cap is Capability.VISION:
        return []
    out = []
    for entry in sorted(registry.list_entries(), key=lambda e: getattr(e, "floor", False)):
        if skip_agent_runtimes and entry.type == "acp_agent":
            continue
        if target_cap not in _entry_capabilities(registry, entry):
            continue
        if _implicit_unready(registry, entry) is not None:
            continue
        out.append(entry)
    return out


def _implicit_unready(registry: Any, entry: Any) -> tuple[str, str] | None:
    """Why ``entry`` cannot be the implicit pick when nothing is bound, or ``None`` when it can.

    First its type's own answer (``registry.not_ready``), which is the more specific one when it
    has one: a bundled-model row with no weight on disk is "not downloaded yet", not "no model
    chosen". Then it must name a model of its own (:attr:`~personalclaw.llm.registry.
    ProviderEntry.own_model`): nothing names one for an unbound call, so an instance saved from
    the Add-instance form with no Default Model could only be sent an empty model (Ollama
    answered every chat turn and background chore ``400 model is required``) or have its provider
    pick one of its own (the one-shot path took the first model the endpoint listed, the Groq app
    its discovery's first chat model). An agent runtime runs its CLI's own model, so it names
    none by design.
    """
    from personalclaw.llm.registry import no_model_chosen

    unready = registry.not_ready(entry, implicit=True)
    if unready is not None:
        return unready
    if entry.type != "acp_agent" and not entry.own_model:
        return no_model_chosen(entry.name)
    return None


def _first_unready_candidate(capability: str) -> tuple[str, str] | None:
    """``(why, fix)`` of the first model entry that declares ``capability`` but cannot serve.

    Only consulted once resolution has already found nothing ready, to say WHY — in the words of
    the one answer the implicit walk asks (:func:`_implicit_unready`). ``None`` when no entry
    declares the capability at all — the plain "no provider" case.
    """
    try:
        from personalclaw.llm.registry import get_default_registry

        target_cap = _capability_enum(capability)
        if target_cap is None:
            return None
        registry = get_default_registry()
        for entry in sorted(registry.list_entries(), key=lambda e: getattr(e, "floor", False)):
            if entry.type == "acp_agent":
                continue
            if target_cap not in _entry_capabilities(registry, entry):
                continue
            unready = _implicit_unready(registry, entry)
            if unready is not None:
                return unready
    except Exception:  # noqa: BLE001 — a diagnosis must never raise over the failure it explains
        logger.debug("could not diagnose an unready %r provider", capability, exc_info=True)
    return None


def _ref_can_serve(registry: Any, entries: dict[str, Any], ref: str) -> bool:
    """Whether one bound ref can be served, as far as a no-instantiate probe can tell.

    ``False`` only when the ref names a registry entry whose type reports it NOT READY — the
    single fact this probe learns without building. A ref naming no LLM-registry entry stays
    ``True``: embedding / speech / media refs resolve through their own registries, and a
    stale model ref is the build check's to judge (``/api/onboarding/model-check``), not this
    hot GET's.
    """
    from personalclaw.providers.use_cases import split_ref

    parsed = split_ref(ref)
    if not parsed:
        return True
    entry = entries.get(parsed[0])
    if entry is None:
        return True
    return _served_model(entry, parsed[1]) is not None and (
        registry.not_ready(entry, implicit=False) is None
    )


def _served_model(entry: Any, named: str | None) -> str | None:
    """The model a call on ``entry`` is served by: the one it ``named``, else the entry's own.

    ``None`` when neither names one: the entry cannot serve that call, and resolution refuses it
    rather than build a provider that would send an empty model or pick its own. ``""`` only for
    an agent runtime, which runs its CLI's own model.
    """
    model = str(named or "").strip() or entry.own_model
    if model or entry.type == "acp_agent":
        return model
    return None


def model_chosen(use_case: str) -> bool:
    """Whether a model is chosen for ``use_case`` at all, whether or not it can serve now.

    A chain in Settings → Models chooses one; with none, a configured instance that serves the
    use case and names a model of its own (``ProviderEntry.own_model``, its Default Model)
    does, because resolution falls back to it. ``False`` is the state an instance saved from the
    Add-instance form without a Default Model sits in with nothing bound: nothing broke, no
    model has been chosen yet, and a surface on its floor for that reason is waiting on a
    choice rather than degraded. Walks what :func:`can_resolve_use_case` walks, building
    nothing; an agent runtime runs its CLI's own model and chooses none here.
    """
    from personalclaw.llm.registry import get_default_registry
    from personalclaw.providers.use_cases import active_model_refs, parent_capability

    if active_model_refs(use_case):
        return True
    target_cap = _capability_enum(parent_capability(use_case))
    if target_cap is None:
        return False
    registry = get_default_registry()
    return any(
        entry.type != "acp_agent"
        and bool(entry.own_model)
        and target_cap in _entry_capabilities(registry, entry)
        for entry in registry.list_entries()
    )


def use_case_problem(use_case: str) -> tuple[str, str] | None:
    """``(why, fix)`` for a use case :func:`can_resolve_use_case` reads as unable to serve now.

    The same walk, building nothing: the head of its chain when no entry of the chain can serve
    (in the words resolution refuses it with, ``_diagnose_unbuildable_ref``), else — with nothing
    bound — the first candidate that declares the capability and cannot serve
    (``_first_unready_candidate``). ``None`` when it can serve, or when the walk names no cause.
    What the degraded report says a surface is waiting on, so the chip and the notice about it
    say what is wrong rather than that "no model" exists.
    """
    from personalclaw.llm.registry import get_default_registry
    from personalclaw.providers.use_cases import active_model_refs, parent_capability, split_ref

    capability = parent_capability(use_case)
    try:
        refs = active_model_refs(use_case)
        if not refs:
            return _first_unready_candidate(capability)
        registry = get_default_registry()
        entries = {e.name: e for e in registry.list_entries()}
        if any(_ref_can_serve(registry, entries, ref) for ref in refs):
            return None
        parsed = split_ref(refs[0])
        if parsed is None:
            return None
        return _diagnose_unbuildable_ref(parsed[0], parsed[1], use_case, capability)
    except Exception:  # noqa: BLE001 — a diagnosis must never raise over the state it explains
        logger.debug("could not diagnose use case %r", use_case, exc_info=True)
        return None


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

    🔴 **Declaring a capability is not being able to serve it.** A provider type whose model is
    not on disk yet registers, declares ``chat`` and BUILDS — its first turn is what fails. This
    probe used to answer "resolvable" for that state, so onboarding said "you're ready" while the
    degraded chip, in the same second, said chat had no model. Both read this function; it now
    asks the type through :meth:`~personalclaw.llm.registry.ProviderRegistry.not_ready`, so a
    binding to such an entry, or an implicit fallback onto one, reads unresolvable here exactly
    as it does in :func:`resolve_provider_for_use_case`.
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

    try:
        # Trigger provider modules' register_type() side effects (idempotent).
        import personalclaw.llm.acp_agent  # noqa: F401
        from personalclaw.llm.registry import get_default_registry

        registry = get_default_registry()
    except Exception:
        logger.debug("can_resolve: registry unavailable", exc_info=True)
        return False

    # 1. An active selection wins (matches resolve_provider_for_use_case order).
    #    active_model_refs applies the chat sub-category → parent fallback. A chain whose every
    #    entry names a model that cannot serve does NOT fall through to implicit fallback, for
    #    the same reason resolution refuses to: "block, don't silently fall back" past the
    #    user's declared chain.
    try:
        refs = active_model_refs(use_case)
    except Exception:
        logger.debug("can_resolve: active-model probe failed", exc_info=True)
        refs = []
    if refs:
        try:
            entries = {e.name: e for e in registry.list_entries()}
            return any(_ref_can_serve(registry, entries, ref) for ref in refs)
        except Exception:
            logger.debug("can_resolve: binding probe failed", exc_info=True)
            return True

    capability = parent_capability(use_case)

    # 2. Implicit fallback: any READY registry entry declaring the capability — the same walk
    #    _resolve_from_config_registry takes, WITHOUT building. An agent-runtime entry
    #    (acp_agent) is not a model provider.
    try:
        target_cap = _capability_enum(capability)
        if target_cap is None:
            return False
        return bool(_implicit_candidates(registry, target_cap, skip_agent_runtimes=True))
    except Exception:
        logger.debug("can_resolve: registry probe failed", exc_info=True)
    return False


def serving_entry(use_case: str) -> Any:
    """The model-registry entry resolution would serve ``use_case`` from, WITHOUT building it.

    The bound chain's first entry that can serve, else — with nothing bound — the implicit
    fallback's first candidate; ``None`` when neither exists. Read by the surfaces that must say
    WHAT is answering rather than merely whether something is: onboarding's
    ``chat_is_bundled_floor`` and the model check's ``floor``, so a model the user explicitly
    bound is still named as the small floor model when that is what it is. Same readiness
    authority, same order, as :func:`can_resolve_use_case`; circuit-breaker skips and routing
    reorders are deliberately not modelled — this names the configured answer, not one turn's.
    """
    from personalclaw.llm.registry import get_default_registry
    from personalclaw.providers.use_cases import (
        VALID_USE_CASES,
        active_model_refs,
        parent_capability,
        split_ref,
    )

    if use_case not in VALID_USE_CASES:
        return None
    registry = get_default_registry()
    refs = active_model_refs(use_case)
    if refs:
        entries = {e.name: e for e in registry.list_entries()}
        for ref in refs:
            parsed = split_ref(ref)
            entry = entries.get(parsed[0]) if parsed else None
            if (
                entry is not None
                and parsed is not None
                and _served_model(entry, parsed[1]) is not None
                and registry.not_ready(entry, implicit=False) is None
            ):
                return entry
        return None
    target_cap = _capability_enum(parent_capability(use_case))
    if target_cap is None:
        return None
    candidates = _implicit_candidates(registry, target_cap, skip_agent_runtimes=True)
    return candidates[0] if candidates else None


def expected_served_ref(model: str) -> str:
    """The ``"<entry>:<model>"`` a native chat turn would be served by, WITHOUT building it.

    For a surface that must answer before a runtime exists (the attachment chip on a new
    chat). ``model`` is the session's selection: a ``"<entry>:<model>"`` ref naming a
    registered entry stands as given; a bare id is served by the entry the ``chat`` binding
    resolves to (:func:`serving_entry`); ``""``/``"auto"`` takes that entry's model the way a
    runtime picks it (:func:`_fallback_chat_model`, else the entry's own model). ``""`` when
    nothing would serve chat.
    """
    from personalclaw.llm.registry import get_default_registry
    from personalclaw.providers.use_cases import split_ref

    chosen = (model or "").strip()
    parsed = split_ref(chosen)
    if parsed and parsed[0] in {e.name for e in get_default_registry().list_entries()}:
        return chosen
    entry = serving_entry("chat")
    if entry is None:
        return ""
    if chosen and chosen.lower() != "auto":
        return f"{entry.name}:{chosen}"
    picked = _fallback_chat_model(entry.name) or entry.own_model
    return f"{entry.name}:{picked}" if picked else ""


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
    # Routing provenance flags. Popped UNCONDITIONALLY — like _guard_use_case — so they can
    # never leak into the build kwargs / factory of a provider that knows nothing about routing.
    guard_routed = bool(kwargs.pop("_guard_routed", False))
    guard_routed_fallback = bool(kwargs.pop("_guard_routed_fallback", False))

    registry = get_default_registry()
    entries = list(registry.list_entries())
    if not entries:
        return None

    # A ``provider_hint`` means the caller already split the ref (the chain walk names the entry
    # and passes the model id alone), so the override IS the model id, whole. Parsing it again
    # split any id with a slash in it on the slash branch below: a chat bound to
    # "groq:meta-llama/llama-4-scout-17b-16e-instruct" was sent to Groq as
    # "llama-4-scout-17b-16e-instruct", and every OpenRouter, Together and NVIDIA id the same way.
    #
    # Only an override that names no provider is read for one, in two shapes:
    #   • "ProviderName:model"  (colon) — the active_models.json ref form a chat
    #     session stores (e.g. "Bedrock:global.anthropic.claude-opus-4-8").
    #   • "ProviderName/model"  (slash) — legacy composer form.
    # Colons are ambiguous — Bedrock model ids themselves contain them
    # (…-v1:0) — so split on the FIRST colon ONLY when the prefix matches a
    # known provider entry name. The colon form goes FIRST because its model id
    # can itself contain a slash (NVIDIA "nvidia:meta/llama-3.1-8b-instruct",
    # OpenRouter "or:meta-llama/llama-3.3"), so splitting on "/" first would name
    # the provider "nvidia:meta" → unknown → wrong provider.
    if model_override and not provider_hint:
        if ":" in model_override and any(
            e.name == model_override.split(":", 1)[0] for e in entries
        ):
            provider_hint, model_override = model_override.split(":", 1)
        elif "/" in model_override:
            provider_hint, model_override = model_override.split("/", 1)

    candidate = None
    if provider_hint:
        # A binding (or a provider-qualified override) NAMES the entry: it is the only
        # candidate, and it must be able to serve as a bound model. An entry that cannot is
        # None here, which the chain walk reports through ``_diagnose_unbuildable_ref`` in the
        # type's own words rather than handing a turn to a model that is not there.
        named = next((e for e in entries if e.name == provider_hint), None)
        if (
            named is not None
            and not (model_axis_only and named.type == "acp_agent")
            and target_cap in _entry_capabilities(registry, named)
            and registry.not_ready(named, implicit=False) is None
        ):
            candidate = named
    else:
        # Nothing names an entry: the implicit fallback's ONE ordered walk (floor last, ready
        # only) — shared with ``can_resolve_use_case`` so the probe and this can never disagree.
        # Agent-runtime entries are skipped when only a ModelProvider will do.
        implicit = _implicit_candidates(registry, target_cap, skip_agent_runtimes=model_axis_only)
        candidate = implicit[0] if implicit else None

    if candidate is None:
        return None

    # The model this call is served by: the one the caller named (a binding's ref, a chat's own
    # pick, a pinned judge), else the entry's own (``ProviderEntry.own_model``: its model, else
    # the Default Model the Add-instance form saves). An entry that names none is not built for a
    # call that names none: every factory would send the model empty or have its provider pick
    # one. The implicit walk above already passes such an entry over; this is the named case
    # (a stored ``"<entry>:"`` ref), which the chain walk then reports in the same words.
    served_model = _served_model(candidate, model_override)
    if served_model is None:
        return None

    # A config.json registry entry resolves through the registry's registered TYPE
    # factory — the same factory the provider's module (core-native ollama, or an
    # installed model APP: openai/anthropic/vllm/bedrock) registers via
    # register_type(...). This is the single path for both agent-runtime and model
    # providers now that the per-type hardcoded branches are gone. When the entry's
    # type isn't registered (e.g. its app isn't installed) registry.build raises and
    # we return None (no provider resolves) rather than crash.
    #
    # The served model rides as the ``model`` build kwarg, always, so no factory is left to
    # re-derive the entry's model its own way (``registry.build`` forwards kwargs to it). This
    # replaces an older entry-replace dance that relied on register_entry being
    # overwrite-idempotent — it isn't (it raises on a duplicate name), so that path silently
    # no-op'd and the override was lost.
    build_kwargs = dict(kwargs)
    if served_model:
        build_kwargs["model"] = served_model
    if "credential_store" not in build_kwargs and candidate.credential:
        try:
            from personalclaw.config import config_dir
            from personalclaw.llm.credentials import CredentialStore

            build_kwargs["credential_store"] = CredentialStore(config_dir())
        except Exception:
            pass
    # When options carry an inline api_key (set by the "Add instance" UI form)
    # but no credential is linked, synthesize a Credential so the factory gets
    # it without a named credential in the store.
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
        # the one seam that already turns guardrails config into a guard.
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

        # A ROUTED local attempt runs under ``routing.local_timeout_secs`` instead of the
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
            # Read again at each call: the values above are only where the guard starts.
            budget_source=budget_from_config,
            run_budget_source=run_budget_from_config,
            scan_mode_source=_scan_mode_now,
            **_timeout_kw,
        )
        _stamp_served_ref(guarded, served_ref)
        return guarded
    _stamp_served_ref(built, served_ref)
    return built


def _scan_mode_now() -> str:
    """``guardrails.scan_mode`` as it reads now, for a guard that outlives a Settings change."""
    from personalclaw.config.loader import AppConfig

    return str(AppConfig.load().guardrails.scan_mode or "")


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

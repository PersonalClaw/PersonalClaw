"""Extension Registry — unified facade over per-type provider registries.

Maps extension names to their provider instances and bridges the app system
(enable/disable) with the domain-specific registries (embedding, STT, task, etc.).

The registry does NOT own provider instances; it delegates to the appropriate
per-type registry for actual provider storage and lookup.  This means existing
code paths (e.g. ``embedding_providers.registry.get_active_embed_fn()``) continue
to work unchanged.
"""

import logging
from dataclasses import dataclass, field
from typing import Any

from personalclaw.apps.manifest import AppManifest, ProviderConfig

logger = logging.getLogger(__name__)


@dataclass
class RegisteredProvider:
    """Runtime state for ONE provider an extension contributes.

    An app may register several providers (of the same or different kinds). Each
    is its own ``RegisteredProvider`` record: the app's FIRST provider is the
    primary, stored in the registry under the app name; any additional ones live
    in the primary's :attr:`extra` list and are enabled/disabled/listed alongside
    it. All share the same ``name`` (the app name) + ``manifest``; they differ in
    ``provider_config`` (and thus type/implementation/instance)."""

    name: str
    manifest: AppManifest
    provider_config: ProviderConfig
    provider_instance: Any = None
    enabled: bool = False
    error: str = ""
    # Additional providers this same app contributes (empty for a single-provider
    # app). Only set on the PRIMARY record; an extra record's own list stays empty.
    extra: list["RegisteredProvider"] = field(default_factory=list)

    def chain(self) -> list["RegisteredProvider"]:
        """This record followed by its extras — the full provider set for the app."""
        return [self, *self.extra]


class ProviderRegistry:
    """Central registry tracking all extensions that provide pluggable providers.

    Lifecycle:
    1. ``register()`` — Records extension metadata (called during startup scan)
    2. ``enable()`` — Loads the provider implementation and registers with per-type registry
    3. ``disable()`` — Deregisters from per-type registry and drops the instance
    """

    def __init__(self) -> None:
        self._extensions: dict[str, RegisteredProvider] = {}
        self._type_handlers: dict[str, "_TypeHandler"] = {}

    def register_type_handler(self, provider_type: str, handler: "_TypeHandler") -> None:
        self._type_handlers[provider_type] = handler

    def register(self, manifest: AppManifest, *, enabled: bool = False) -> None:
        provider_configs = manifest.all_providers()
        if not provider_configs:
            return
        primary = RegisteredProvider(
            name=manifest.name,
            manifest=manifest,
            provider_config=provider_configs[0],
            enabled=False,
        )
        # Additional providers this same app contributes hang off the primary.
        primary.extra = [
            RegisteredProvider(
                name=manifest.name, manifest=manifest,
                provider_config=cfg, enabled=False,
            )
            for cfg in provider_configs[1:]
        ]
        self._extensions[manifest.name] = primary
        if enabled:
            self.enable(manifest.name)

    def _enable_one(self, ext: RegisteredProvider) -> bool:
        """Enable a single provider record (one entry in an app's chain)."""
        if ext.enabled:
            return True
        handler = self._type_handlers.get(ext.provider_config.type)
        if not handler:
            ext.error = f"No type handler for provider type: {ext.provider_config.type}"
            logger.warning(ext.error)
            return False
        try:
            instance = handler.create(ext)
            if instance is not None:
                handler.register(ext, instance)
            ext.provider_instance = instance
            ext.enabled = True
            ext.error = ""
            logger.info(
                "Enabled extension %s (type=%s)", ext.name, ext.provider_config.type
            )
            return True
        except Exception as exc:
            ext.error = str(exc)
            logger.exception("Failed to enable extension %s", ext.name)
            return False

    def enable(self, name: str) -> bool:
        primary = self._extensions.get(name)
        if not primary:
            logger.warning("Cannot enable unknown extension: %s", name)
            return False
        # Enable every provider in the app's chain — attempt ALL (one failing must
        # not skip the rest); success = all enabled. Materialize before reducing so
        # short-circuit can't drop an enable.
        results = [self._enable_one(rec) for rec in primary.chain()]
        return all(results)

    def _disable_one(self, ext: RegisteredProvider) -> None:
        """Tear down a single provider record's live instance."""
        if not ext.enabled:
            return
        handler = self._type_handlers.get(ext.provider_config.type)
        if handler and ext.provider_instance:
            try:
                handler.deregister(ext, ext.provider_instance)
            except Exception:
                logger.exception("Error deregistering extension %s", ext.name)
        ext.provider_instance = None
        ext.enabled = False
        logger.info("Disabled extension %s (type=%s)", ext.name, ext.provider_config.type)

    def disable(self, name: str) -> bool:
        primary = self._extensions.get(name)
        if not primary:
            return True
        for rec in primary.chain():
            self._disable_one(rec)
        return True

    def deregister(self, name: str) -> bool:
        """Disable AND forget an extension entirely (for uninstall).

        ``disable`` only tears down the live instance but keeps the registration
        so it can be re-enabled; an uninstalled app must vanish from the registry
        completely, else it lingers as a disabled ghost in the providers list
        until the next gateway restart. Returns True if it was present."""
        if name not in self._extensions:
            return False
        self.disable(name)
        del self._extensions[name]
        logger.info("Deregistered extension %s", name)
        return True

    def get(self, name: str) -> RegisteredProvider | None:
        """The app's PRIMARY provider record (its first). Additional providers
        the app contributes are reachable via the record's ``extra``/``chain()``."""
        return self._extensions.get(name)

    def list_extensions(self) -> list[RegisteredProvider]:
        """Every registered provider — each app's full chain flattened, so an app
        that contributes multiple providers surfaces one entry per provider."""
        out: list[RegisteredProvider] = []
        for primary in self._extensions.values():
            out.extend(primary.chain())
        return out

    def list_by_type(self, provider_type: str) -> list[RegisteredProvider]:
        return [
            ext for ext in self.list_extensions()
            if ext.provider_config.type == provider_type
        ]


class _TypeHandler:
    """Interface for per-type provider registration handlers.

    Each provider type (embedding, STT, task, etc.) implements this to bridge
    between the extension system and its domain-specific registry.
    """

    def create(self, ext: RegisteredProvider) -> Any:
        raise NotImplementedError

    def register(self, ext: RegisteredProvider, instance: Any) -> None:
        raise NotImplementedError

    def deregister(self, ext: RegisteredProvider, instance: Any) -> None:
        raise NotImplementedError


# Type handlers for each provider type

class TaskTypeHandler(_TypeHandler):
    def create(self, ext: RegisteredProvider) -> Any:
        from personalclaw.providers.loader import load_factory
        from personalclaw.providers.settings import ProviderSettings
        config = ProviderSettings.load(ext.name)
        factory = load_factory(ext)
        return factory(config)

    def register(self, ext: RegisteredProvider, instance: Any) -> None:
        from personalclaw.tasks.registry import register_provider
        register_provider(instance)

    def deregister(self, ext: RegisteredProvider, instance: Any) -> None:
        from personalclaw.tasks.registry import unregister_provider
        unregister_provider(instance.name)


class WorkflowTypeHandler(_TypeHandler):
    def create(self, ext: RegisteredProvider) -> Any:
        from personalclaw.providers.loader import load_factory
        from personalclaw.providers.settings import ProviderSettings
        config = ProviderSettings.load(ext.name)
        factory = load_factory(ext)
        return factory(config)

    def register(self, ext: RegisteredProvider, instance: Any) -> None:
        from personalclaw.workflows.registry import register_provider
        register_provider(instance)

    def deregister(self, ext: RegisteredProvider, instance: Any) -> None:
        from personalclaw.workflows.registry import unregister_provider
        unregister_provider(instance.name)


class ToolTypeHandler(_TypeHandler):
    def create(self, ext: RegisteredProvider) -> Any:
        from personalclaw.providers.loader import load_factory
        from personalclaw.providers.settings import ProviderSettings
        factory = load_factory(ext)
        # A multiInstance tool app (e.g. openai-tools) builds ONE provider per
        # configured instance — the app-level singleton config is empty, so the
        # single-config path would yield a hollow provider that surfaces no tools.
        # Mirror ModelTypeHandler: iterate the enabled instances, build+tag each,
        # and return the LIST (register/deregister below normalize a list). Without
        # this, instances added via "Add instance" never become live tool providers.
        if ext.provider_config.multiInstance:
            from personalclaw.providers.instances import list_instances
            enabled = [i for i in list_instances(ext.name) if i.enabled]
            if not enabled:
                logger.info("Tool extension %s has no enabled instances", ext.name)
                return None
            providers: list[Any] = []
            for inst in enabled:
                try:
                    provider = factory(inst.config)
                    if not hasattr(provider, "name"):
                        provider.name = f"{ext.name}:{inst.id}"
                    if not hasattr(provider, "instance_id"):
                        provider.instance_id = inst.id
                    if not hasattr(provider, "display_name"):
                        provider.display_name = inst.display_name or inst.id
                    providers.append(provider)
                except Exception:
                    logger.warning(
                        "Failed to create instance %s of %s", inst.id, ext.name, exc_info=True
                    )
            return providers or None

        config = ProviderSettings.load(ext.name)
        return factory(config)

    def register(self, ext: RegisteredProvider, instance: Any) -> None:
        from personalclaw.tool_providers.registry import register_provider
        providers = instance if isinstance(instance, list) else [instance]
        for provider in providers:
            register_provider(provider)

    def deregister(self, ext: RegisteredProvider, instance: Any) -> None:
        from personalclaw.tool_providers.registry import unregister_provider
        providers = instance if isinstance(instance, list) else [instance]
        for provider in providers:
            unregister_provider(getattr(provider, "name", ext.name))


class SearchTypeHandler(_TypeHandler):
    """Handler for ``provider.type == 'search'`` extensions (the Search entity).

    Builds a SearchProvider via the manifest factory and registers it in the
    ``search_providers`` registry so ``web_search`` / ``web_fetch`` + the research
    loop can resolve it by use-case. One source of truth: enabling a search
    extension registers its provider; disabling it unregisters.
    """

    def create(self, ext: RegisteredProvider) -> Any:
        from personalclaw.providers.loader import load_factory
        from personalclaw.providers.settings import ProviderSettings
        config = ProviderSettings.load(ext.name)
        factory = load_factory(ext)
        return factory(config)

    def register(self, ext: RegisteredProvider, instance: Any) -> None:
        from personalclaw.search_providers.registry import register_provider
        register_provider(instance)

    def deregister(self, ext: RegisteredProvider, instance: Any) -> None:
        from personalclaw.search_providers.registry import unregister_provider
        unregister_provider(getattr(instance, "name", ext.name))


class ActionTypeHandler(_TypeHandler):
    """Handler for ``provider.type == 'action'`` extensions.

    Builds an ActionProvider via the manifest factory, then registers it in
    the action_providers registry so triggers can dispatch to it by name.
    """

    def create(self, ext: RegisteredProvider) -> Any:
        from personalclaw.providers.loader import load_factory
        from personalclaw.providers.settings import ProviderSettings
        config = ProviderSettings.load(ext.name)
        factory = load_factory(ext)
        return factory(config)

    def register(self, ext: RegisteredProvider, instance: Any) -> None:
        from personalclaw.action_providers.registry import register_action_provider
        register_action_provider(instance)

    def deregister(self, ext: RegisteredProvider, instance: Any) -> None:
        from personalclaw.action_providers.registry import _providers
        _providers.pop(getattr(instance, "name", ""), None)


class ChannelTypeHandler(_TypeHandler):
    """Handler for ``provider.type == 'channel'`` extensions (comms transports).

    Builds a ChannelTransportProvider via the manifest factory and registers it
    in the ``channel_transports`` registry so the Channels page + comms manager
    can list/manage it. Enabling ``slack-channel`` registers the Slack
    transport; disabling it unregisters it — one source of truth, no parallel
    startup path.
    """

    def create(self, ext: RegisteredProvider) -> Any:
        from personalclaw.providers.loader import load_factory
        from personalclaw.providers.settings import ProviderSettings
        config = ProviderSettings.load(ext.name)
        factory = load_factory(ext)
        return factory(config)

    def register(self, ext: RegisteredProvider, instance: Any) -> None:
        from personalclaw.channel_transports import register_transport
        register_transport(instance)

    def deregister(self, ext: RegisteredProvider, instance: Any) -> None:
        from personalclaw.channel_transports import unregister_transport
        unregister_transport(getattr(instance, "name", ext.name))


class PromptTypeHandler(_TypeHandler):
    """Handler for ``provider.type == 'prompt'`` extensions.

    Builds a PromptProvider via the manifest factory and registers it in
    the prompt_providers registry so the dashboard handlers + chat runner
    can dispatch to it by name.
    """

    def create(self, ext: RegisteredProvider) -> Any:
        from personalclaw.providers.loader import load_factory
        from personalclaw.providers.settings import ProviderSettings
        config = ProviderSettings.load(ext.name)
        factory = load_factory(ext)
        return factory(config)

    def register(self, ext: RegisteredProvider, instance: Any) -> None:
        from personalclaw.prompt_providers.registry import register_prompt_provider
        register_prompt_provider(instance)

    def deregister(self, ext: RegisteredProvider, instance: Any) -> None:
        from personalclaw.prompt_providers.registry import _providers
        _providers.pop(getattr(instance, "name", ""), None)


class MemoryTypeHandler(_TypeHandler):
    def create(self, ext: RegisteredProvider) -> Any:
        from personalclaw.providers.loader import load_factory
        from personalclaw.providers.settings import ProviderSettings
        config = ProviderSettings.load(ext.name)
        factory = load_factory(ext)
        return factory(config)

    def register(self, ext: RegisteredProvider, instance: Any) -> None:
        from personalclaw.memory_providers.registry import register_provider
        provider_name = getattr(instance, "name", ext.name)
        register_provider(provider_name, instance)

    def deregister(self, ext: RegisteredProvider, instance: Any) -> None:
        from personalclaw.memory_providers.registry import unregister_provider
        provider_name = getattr(instance, "name", ext.name)
        unregister_provider(provider_name)


class EntitySeamHandler(_TypeHandler):
    """Handler for provider types that are *enable/disable + Settings* seams only.

    For these types the extension system owns visibility and on/off state, but
    the real entity is owned by a separate subsystem — named in
    ``source_of_truth`` at each registration — that manages its own lifecycle
    via import-time self-registration and/or entry-point discovery, NOT through
    this seam. ``create()`` still runs the manifest factory (several return
    ``None`` by design; :meth:`ProviderRegistry.enable` only calls
    ``register()`` for non-None instances), so enable/disable + error surfacing
    behave exactly as for the real handlers above.

    ``register()``/``deregister()`` are *intentional* no-ops, and that is the
    honest design — not a stub. Every domain registry these types could target
    is either (a) consumed by no one (``knowledge_providers.registry``),
    or (b) populated by its own subsystem on
    import / entry-point. Wiring an instance in here would manufacture a SECOND
    source of truth that nothing reads — the exact split this project removed
    for Bedrock. Where a type has a factory↔registry *contract mismatch*
    (noted in ``source_of_truth``), that is flagged for the owning feature to
    reconcile the contract — do NOT paper it over with a no-op that pretends
    the instance was registered.
    """

    def __init__(self, *, source_of_truth: str) -> None:
        # Human-readable name of where this entity actually lives + is consumed
        # (and, for the two mismatch cases, which feature owns reconciling it).
        # Introspectable so a test/debug surface can assert the seam is honest.
        self.source_of_truth = source_of_truth

    def create(self, ext: RegisteredProvider) -> Any:
        from personalclaw.providers.loader import load_factory
        from personalclaw.providers.settings import ProviderSettings
        config = ProviderSettings.load(ext.name)
        factory = load_factory(ext)
        return factory(config)

    def register(self, ext: RegisteredProvider, instance: Any) -> None:
        # Intentional no-op — the real entity lives in ``self.source_of_truth``,
        # which owns its own registration. See the class docstring.
        return None

    def deregister(self, ext: RegisteredProvider, instance: Any) -> None:
        return None


# ── Module-level singleton ────────────────────────────────────────────────

_registry: ProviderRegistry | None = None


class ModelTypeHandler(_TypeHandler):
    """Handler for model-type extensions (LLM, embedding, STT, TTS).

    Model extensions may provide multiple capabilities. The handler inspects
    ``capabilities`` to determine which sub-registries to populate.

    For multi-instance extensions, creates one provider per enabled instance.
    For singleton extensions, creates one provider from the extension config.
    """

    def create(self, ext: RegisteredProvider) -> Any:
        from personalclaw.providers.loader import load_factory
        from personalclaw.providers.settings import ProviderSettings

        factory = load_factory(ext)

        if ext.provider_config.multiInstance:
            from personalclaw.providers.instances import list_instances
            instances = list_instances(ext.name)
            enabled = [i for i in instances if i.enabled]
            if not enabled:
                logger.info("Model extension %s has no enabled instances", ext.name)
                return None
            providers = []
            for inst in enabled:
                try:
                    provider = factory(inst.config)
                    if not hasattr(provider, "name"):
                        provider.name = f"{ext.name}:{inst.id}"
                    if not hasattr(provider, "instance_id"):
                        provider.instance_id = inst.id
                    if not hasattr(provider, "display_name"):
                        provider.display_name = inst.display_name or inst.id
                    providers.append(provider)
                except Exception:
                    logger.warning(
                        "Failed to create instance %s of %s",
                        inst.id, ext.name, exc_info=True
                    )
            return providers if providers else None

        config = ProviderSettings.load(ext.name)
        provider = factory(config)
        return provider

    def register(self, ext: RegisteredProvider, instance: Any) -> None:
        caps = ext.provider_config.capabilities
        providers = instance if isinstance(instance, list) else [instance]
        for provider in providers:
            # Management axis (uniform): any model provider that owns local downloadable
            # models joins the one local-model registry that drives the download surface
            # + availability. Duck-typed — core knows no concrete provider. Orthogonal to
            # the per-capability inference registration below.
            from personalclaw.local_models.registry import (
                is_local_model_provider,
                register_provider as reg_local,
            )
            if is_local_model_provider(provider, capabilities=list(caps)):
                # Key by the APP name (ext.name) — matches the Providers UI + the
                # existing ``provider:model`` binding refs, even when the provider's
                # internal .name differs (faster_whisper / native / piper).
                reg_local(provider, capabilities=list(caps), name=ext.name)
            # Each capability registers ONLY the provider that implements its
            # interface. An app whose factory returns SEVERAL providers (e.g. a
            # multi-usecase Bedrock instance → [chat, embedding, image, video, stt])
            # must not cross-register — the chat provider is not an embedding
            # provider, etc. Every branch is isinstance-guarded against its base.
            if "embedding" in caps:
                from personalclaw.embedding_providers.base import EmbeddingProvider as _EMB
                if isinstance(provider, _EMB):
                    from personalclaw.embedding_providers.registry import register_provider as reg_emb
                    reg_emb(provider)
            if "stt" in caps:
                from personalclaw.stt.provider import SttProvider as _STT
                if isinstance(provider, _STT):
                    from personalclaw.stt.registry import register_provider as reg_stt
                    reg_stt(provider)
            if "tts" in caps:
                from personalclaw.tts.provider import TtsProvider as _TTS
                if isinstance(provider, _TTS):
                    from personalclaw.tts.registry import register_provider as reg_tts
                    reg_tts(provider)
            if "diarization" in caps:
                from personalclaw.diarization.provider import DiarizationProvider as _DIA
                if isinstance(provider, _DIA):
                    from personalclaw.diarization.registry import register_provider as reg_diar
                    reg_diar(provider)
            if "image_gen" in caps:
                from personalclaw.image_gen.provider import ImageGenProvider as _IGP
                if isinstance(provider, _IGP):
                    from personalclaw.image_gen.registry import register_provider as reg_img
                    reg_img(provider)
            if "video_gen" in caps:
                from personalclaw.video_gen.provider import VideoGenProvider as _VGP
                if isinstance(provider, _VGP):
                    from personalclaw.video_gen.registry import register_provider as reg_vid
                    reg_vid(provider)

    def deregister(self, ext: RegisteredProvider, instance: Any) -> None:
        caps = ext.provider_config.capabilities
        providers = instance if isinstance(instance, list) else [instance]
        for provider in providers:
            provider_name = getattr(provider, "name", ext.name)
            # Local-model registry is keyed by the APP name (see register()).
            from personalclaw.local_models.registry import unregister_provider as unreg_local
            unreg_local(ext.name)
            if "embedding" in caps:
                from personalclaw.embedding_providers.registry import unregister_provider as unreg_emb
                unreg_emb(provider_name)
            if "stt" in caps:
                from personalclaw.stt.registry import unregister_provider as unreg_stt
                unreg_stt(provider_name)
            if "tts" in caps:
                from personalclaw.tts.registry import unregister_provider as unreg_tts
                unreg_tts(provider_name)
            if "diarization" in caps:
                from personalclaw.diarization.registry import unregister_provider as unreg_diar
                unreg_diar(provider_name)
            if "image_gen" in caps:
                from personalclaw.image_gen.registry import unregister_provider as unreg_img
                unreg_img(provider_name)
            if "video_gen" in caps:
                from personalclaw.video_gen.registry import unregister_provider as unreg_vid
                unreg_vid(provider_name)


def get_provider_registry() -> ProviderRegistry:
    global _registry
    if _registry is None:
        _registry = ProviderRegistry()
        _registry.register_type_handler("model", ModelTypeHandler())
        _registry.register_type_handler("task", TaskTypeHandler())
        _registry.register_type_handler("workflow", WorkflowTypeHandler())
        _registry.register_type_handler("memory", MemoryTypeHandler())
        _registry.register_type_handler("tool", ToolTypeHandler())
        _registry.register_type_handler("search", SearchTypeHandler())
        _registry.register_type_handler("action", ActionTypeHandler())
        _registry.register_type_handler("prompt", PromptTypeHandler())
        # Enable/disable + Settings seams only — each names where the entity
        # actually lives. See EntitySeamHandler: registering an instance here
        # would create a second source of truth nothing reads (the Bedrock trap).
        _registry.register_type_handler("agent", EntitySeamHandler(
            source_of_truth="config.json agents{} (AgentProfile); marketplaces "
            "self-register in agents.marketplace + entry-points. Factory returns "
            "None by design (agents are config-based, not instances).",
        ))
        _registry.register_type_handler("knowledge", EntitySeamHandler(
            source_of_truth="personalclaw.knowledge.* store/pipeline/retrieval "
            "(dashboard.handlers.knowledge). Factory returns None by design; "
            "knowledge_providers.registry has no consumer.",
        ))
        _registry.register_type_handler("inbox", EntitySeamHandler(
            source_of_truth="entry-point discovery (personalclaw.message_source_"
            "providers) read by inbox.py / handlers_inbox; no in-memory registry.",
        ))
        _registry.register_type_handler("notification", EntitySeamHandler(
            source_of_truth="delivery preferences live in entity_settings/"
            "notifications.json, enforced by DashboardState.notify()'s gate "
            "(providers.entity_routes.notification_allowed). No provider "
            "declares type=notification; pluggable delivery backends remain "
            "a future design.",
        ))
        _registry.register_type_handler("channel", ChannelTypeHandler())
        # NOTE: there is intentionally NO "space" provider type. Multi-agent
        # native feature (one engine + a switchable orchestration strategy), not
        # a pluggable provider family — so Spaces config lives under Settings >
        # Spaces, not Settings > Providers. the goal loop orchestrator drives personas directly
        # to the filesystem under the config dir; it never flows through this
        # extension registry.
        _registry.register_type_handler("skills", EntitySeamHandler(
            source_of_truth="MISMATCH (owner: S1/E11 skills): factory returns a "
            "SkillsLoader (built ad-hoc by ~8 call sites), but SkillsRegistry "
            "holds SkillsMarketplace; native/installed marketplaces self-register "
            "in skills.native on import. Reconcile the contract before wiring.",
        ))
    return _registry


def reset_provider_registry() -> None:
    global _registry
    _registry = None

"""Provider registry: maps types to factories and named entries to instances.

Holds two pieces of state:

1. A mapping from provider *type* (``"openai"``, ``"anthropic"``, ``"acp_agent"``,
   ...) to a ``(ProviderCapability, ProviderFactory)`` pair, registered at
   import time by the concrete provider modules.
2. A mapping from configured Provider_Entry *name* to the corresponding
   :class:`ProviderEntry` instance, registered after config load.

The registry validates two invariants at registration time:

* The entry's ``type`` is a known type (Requirement R1.3).
* The entry's ``declared_capabilities`` are a subset of the type's
  registered capability set (Requirement R1.4).

It does NOT instantiate providers eagerly; :meth:`ProviderRegistry.build`
invokes the registered factory on demand. This module is loaded as a side
effect of importing :mod:`personalclaw.providers` and MUST NOT import any
provider SDK (``anthropic``, ``openai``, ``httpx``); Property 11
(Provider SDK Lazy Import) depends on this guarantee.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field

from personalclaw.llm.base import ModelProvider
from personalclaw.llm.capabilities import Capability, ProviderCapability
from personalclaw.llm.catalog import ModelCatalog

logger = logging.getLogger(__name__)


ProviderFactory = Callable[..., ModelProvider]

CatalogFactory = Callable[..., ModelCatalog]
"""Catalog factory signature: ``create_catalog(options: dict, *, model="") -> ModelCatalog``.

A provider optionally registers one per type via
:meth:`ProviderRegistry.register_catalog`. It builds the provider's discovery/
management object from the entry's stored options — WITHOUT opening a live
inference session (no ``start()``, no ``session_key``)."""
"""Factory signature: ``factory(*, entry, session_key=None, **kwargs) -> ModelProvider``.

Concrete provider modules register a factory per type. The factory is
expected to construct an :class:`ModelProvider` whose declared capability
set is a superset of ``entry.declared_capabilities`` (Requirement R1.7).
"""


class ProviderResolutionError(Exception):
    """Raised when a provider cannot be resolved or registered.

    Used for unknown entry name, unknown type at registration, declared
    capability not supported by the type, duplicate registration, and
    all-fallbacks-failed at build time.
    """


class CredentialMissing(ProviderResolutionError):
    """Raised when a required credential is not configured.

    Concrete provider factories raise this when their
    ``ProviderEntry.credential`` cannot be resolved by the credential
    store. Defined here so the credential store and providers can share
    the symbol without a circular import.
    """


@dataclass(frozen=True)
class ProviderEntry:
    """A configured provider description, not yet instantiated.

    The dataclass is frozen so entries can be safely shared across the
    registry, router, and dashboard handlers. ``options`` is a mutable
    dict by design — ``frozen=True`` does not deep-freeze nested
    containers, and concrete providers may need to read mutable option
    bags (e.g. ACP launch ``command`` lists).
    """

    name: str
    type: str
    model: str
    options: dict[str, object] = field(default_factory=dict)
    credential: str | None = None
    declared_capabilities: frozenset[Capability] = field(default_factory=frozenset)


class ProviderRegistry:
    """In-memory registry of provider type factories and named entries.

    Registration order is:

    1. Provider modules call :meth:`register_type` at import time, supplying
       a :class:`ProviderCapability` and a :data:`ProviderFactory`.
    2. Config-load code calls :meth:`register_entry` once per configured
       Provider_Entry, after which :meth:`build` can instantiate the
       provider on demand.

    Both registration steps validate invariants and raise
    :class:`ProviderResolutionError` on violation.
    """

    def __init__(self) -> None:
        self._factories: dict[str, ProviderFactory] = {}
        self._capabilities: dict[str, ProviderCapability] = {}
        self._entries: dict[str, ProviderEntry] = {}
        # Per-type CATALOG factories (the discovery/management axis, distinct from
        # the inference _factories above). A provider optionally registers one via
        # register_catalog(); catalog_of() resolves it fail-soft. Keyed by the same
        # provider type string as _factories.
        self._catalog_factories: dict[str, CatalogFactory] = {}

    # ── Registration ──────────────────────────────────────────────────

    def register_type(self, cap: ProviderCapability, factory: ProviderFactory) -> None:
        """Register a provider type with its capability descriptor and factory.

        Raises :class:`ProviderResolutionError` if the type is already
        registered; silent overwrite would mask accidental double
        registration during package import.
        """
        type_ = cap.type
        if type_ in self._factories:
            raise ProviderResolutionError(f"provider type {type_!r} is already registered")
        self._factories[type_] = factory
        self._capabilities[type_] = cap
        logger.debug(
            "registered provider type %r with capabilities %s",
            type_,
            sorted(c.value for c in cap.capabilities),
        )

    def register_catalog(self, type_: str, factory: "CatalogFactory") -> None:
        """Register a provider type's optional CATALOG factory (discovery/management).

        The catalog axis is independent of the inference type registration: a
        provider may register a catalog without a type (unusual) or a type without
        a catalog (it simply has no discovery). Last registration wins — unlike
        register_type this is NOT strict about duplicates, so a module reload in
        tests (or re-enabling an app) re-registers cleanly.
        """
        self._catalog_factories[type_] = factory
        logger.debug("registered catalog factory for provider type %r", type_)

    def catalog_of(self, type_: str) -> "CatalogFactory | None":
        """Return the catalog factory registered for ``type_``, or ``None``.

        Fail-soft by contract: an unregistered type (its app not loaded, or a
        provider with no discovery) yields ``None`` — callers treat that as "no
        catalog" (empty model list / management-unsupported), never an error.
        """
        return self._catalog_factories.get(type_)

    def register_entry(self, entry: ProviderEntry) -> None:
        """Register a configured Provider_Entry by name.

        If the provider's type is already registered, validates capabilities
        (R1.3/R1.4). If the type ISN'T registered yet (the app that owns it
        may load after sync_entries_from_config in some boot paths), the entry
        is still stored — the type will be available by inference time. A
        duplicate name is a no-op (idempotent).
        """
        if entry.name in self._entries:
            return  # idempotent

        if entry.type in self._capabilities:
            cap = self._capabilities[entry.type]
            if not entry.declared_capabilities.issubset(cap.capabilities):
                offending = sorted(c.value for c in entry.declared_capabilities - cap.capabilities)
                raise ProviderResolutionError(
                    f"provider entry {entry.name!r} declares capabilities "
                    f"{offending} not supported by type {entry.type!r}"
                )
        else:
            logger.debug(
                "register_entry: type %r not yet registered for %r; storing entry anyway",
                entry.type,
                entry.name,
            )

        self._entries[entry.name] = entry
        logger.debug(
            "registered provider entry %r (type=%s, model=%s)", entry.name, entry.type, entry.model
        )

    # ── Lookup ────────────────────────────────────────────────────────

    def unregister_entry(self, name: str) -> None:
        """Remove a provider entry by name. No-op if not found."""
        self._entries.pop(name, None)

    def list_entries(self) -> list[ProviderEntry]:
        """Return all registered entries in insertion order."""
        return list(self._entries.values())

    def get_entry(self, name: str) -> ProviderEntry:
        """Return the entry registered under ``name``.

        Raises :class:`ProviderResolutionError` if ``name`` is unknown
        (Requirement R1.6).
        """
        try:
            return self._entries[name]
        except KeyError as exc:
            raise ProviderResolutionError(
                f"unknown provider entry {name!r}; " f"known entries: {sorted(self._entries)}"
            ) from exc

    def capability_of(self, type_: str) -> ProviderCapability:
        """Return the :class:`ProviderCapability` for ``type_``.

        Raises :class:`ProviderResolutionError` if ``type_`` was never
        registered.
        """
        try:
            return self._capabilities[type_]
        except KeyError as exc:
            raise ProviderResolutionError(
                f"unknown provider type {type_!r}; " f"known types: {sorted(self._capabilities)}"
            ) from exc

    # ── Factory invocation ────────────────────────────────────────────

    def build(
        self,
        name: str,
        *,
        session_key: str | None = None,
        **kwargs: object,
    ) -> ModelProvider:
        """Instantiate the entry by name via the registered factory.

        The factory is invoked with ``entry=entry``, ``session_key=session_key``
        and any additional keyword arguments. Per Requirement R1.7 the
        factory is expected to return an :class:`ModelProvider` whose
        declared capability set is a superset of
        ``entry.declared_capabilities``; the registry trusts factories
        registered at import time and does not re-validate the returned
        instance.

        Raises :class:`ProviderResolutionError` for an unknown name
        (Requirement R1.6).
        """
        entry = self.get_entry(name)
        factory = self._factories[entry.type]
        return factory(entry=entry, session_key=session_key, **kwargs)

    def build_catalog(self, entry: ProviderEntry) -> "ModelCatalog | None":
        """Build the discovery/management catalog for ``entry``, or ``None``.

        Resolves the catalog factory registered for ``entry.type`` (via
        :meth:`register_catalog`) and invokes it with the entry's stored options +
        pinned model — NO live session is opened (this is the discovery axis, not
        inference). Fail-soft: a type with no catalog registered, or a factory that
        raises, yields ``None`` so the caller degrades to "no discovery" rather than
        erroring. Unlike :meth:`build` this takes the entry directly (discovery
        handlers already hold it) and never raises for an unknown type.
        """
        factory = self._catalog_factories.get(entry.type)
        if factory is None:
            return None
        try:
            return factory(dict(entry.options or {}), model=entry.model)
        except Exception:  # noqa: BLE001 — a catalog build never breaks a hot GET
            logger.debug("catalog factory for type %r failed", entry.type, exc_info=True)
            return None


# ── Module-level default registry singleton ──────────────────────────────
#
# Concrete provider modules register their type with the default registry on
# module import (see e.g. ``providers/openai.py``). Without a singleton, every
# such module would need to be passed a registry instance, which does not work
# for import-time side effects.
#
# Tests that need an isolated registry construct their own ``ProviderRegistry()``;
# the default singleton is independent of those instances and harmless.

_default_registry: ProviderRegistry | None = None


def get_default_registry() -> ProviderRegistry:
    """Return the process-wide default :class:`ProviderRegistry`.

    Lazily creates the singleton on first call. Provider modules call
    ``get_default_registry().register_type(...)`` at import time so that
    ``import personalclaw.llm`` is sufficient to wire the type into the
    registry without the SDK side-effect of the provider module itself.
    """
    global _default_registry
    if _default_registry is None:
        _default_registry = ProviderRegistry()
    return _default_registry


def set_default_registry(registry: ProviderRegistry) -> None:
    """Replace the default registry.

    Intended for tests that need to swap in a freshly-built registry while
    still exercising provider modules' ``register_type`` side effects via
    ``importlib.reload``.
    """
    global _default_registry
    _default_registry = registry


def reset_default_registry() -> None:
    """Clear the default registry singleton.

    The next call to :func:`get_default_registry` will create a new empty
    instance. Intended for tests only.
    """
    global _default_registry
    _default_registry = None


# Config-type → base-registry-type aliases. EMPTY after the model-provider-as-app
# migration (Phase B): every provider type — the two generic protocols
# (``openai_compatible``/``anthropic_compatible``, registered by the generic apps,
# installed by default) AND every branded provider (together/groq/deepseek/mistral/
# google/…, each its own installed app) — now registers its OWN type, so a config
# ``type`` maps to itself. Kept as an (empty) single source of truth in case a future
# provider needs an alias; ``canonical_provider_type`` is the one call site.
_CONFIG_TYPE_MAP: dict[str, str] = {}


def canonical_provider_type(ptype: str) -> str:
    """Return the base registry type for a config.json provider ``type``.

    Now an identity map (see ``_CONFIG_TYPE_MAP``): each provider type is registered
    by its own app, so there are no aliases to collapse. Retained as the single hook
    the config sync + create handler + discovery handlers all route through, so a
    future alias only needs adding to ``_CONFIG_TYPE_MAP``."""
    return _CONFIG_TYPE_MAP.get(ptype, ptype)


def sync_entries_from_config() -> int:
    """Register every ``config.json`` ``providers[]`` entry into the default registry.

    Provider entries are persisted to ``config.json`` by the create/update
    handlers, but on a fresh process start nothing replays them into the
    in-memory :class:`ProviderRegistry` — so a configured provider is invisible
    to ``resolve_provider_for_use_case`` (chat can't find a model) until it is
    re-created via the API. This idempotent sync, called at gateway startup,
    closes that gap. Returns the number of entries registered.

    Provider TYPES (openai/anthropic/vllm/bedrock/ollama/…) are registered by
    their standalone apps when the app loader loads them (before this sync in the
    startup order); and every ``capability_of`` caller falls back to the entry's own
    manifest-``declared_capabilities`` when a type isn't registered, so a not-yet-loaded
    provider app degrades gracefully rather than failing this sync.
    """
    import json

    try:
        from personalclaw.config.loader import config_path
    except Exception:  # pragma: no cover - defensive
        logger.debug("sync_entries_from_config: imports failed", exc_info=True)
        return 0

    path = config_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except Exception:
        logger.debug("sync_entries_from_config: cannot read %s", path, exc_info=True)
        return 0

    providers = data.get("providers") or []
    if not isinstance(providers, list):
        return 0

    registry = get_default_registry()
    count = 0
    for p in providers:
        if not isinstance(p, dict):
            continue
        name = str(p.get("name") or "").strip()
        ptype = str(p.get("type") or "").strip()
        if not name or not ptype or name in registry._entries:  # noqa: SLF001 - same module
            continue
        registry_type = canonical_provider_type(ptype)
        try:
            cap = registry.capability_of(registry_type)
        except Exception:
            # Type not yet registered (app loads after sync on some boot paths).
            # Still register the entry with an empty capability set — the entry
            # becomes resolvable by name (chat resolution uses it), and the type
            # will be available by the time inference runs.
            cap = None
            logger.debug(
                "sync_entries_from_config: type %r not registered yet for %r; registering entry anyway",  # noqa: E501
                ptype,
                name,
            )
        options = dict(p.get("options") or {})
        if ptype != registry_type:
            options["_original_type"] = ptype
        try:
            registry.register_entry(
                ProviderEntry(
                    name=name,
                    type=registry_type,
                    model=str(p.get("model") or ""),
                    options=options,
                    credential=p.get("credential"),
                    declared_capabilities=cap.capabilities if cap else frozenset(),
                )
            )
            count += 1
        except ProviderResolutionError:
            logger.debug("sync_entries_from_config: skip %r (already/invalid)", name)
    if count:
        logger.info(
            "Registered %d provider entr%s from config", count, "y" if count == 1 else "ies"
        )
    return count

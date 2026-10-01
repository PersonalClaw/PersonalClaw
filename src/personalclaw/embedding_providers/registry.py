"""Embedding resolution.

Embedding is unified onto the same pluggable Model providers as chat/vision: the
active embedding model is chosen in Settings > Models (``active_models.json`` as
``"provider:model"``) and the whole system embeds with it.

- sentence-transformers models embed **in-process** (no URL) via the local
  ``NativeEmbeddingProvider``.
- every other provider (ollama, openai-compatible, vLLM, …) is a configured Model
  provider that runs externally; its embedding is performed through the LLM provider
  registry's ``embed()`` using the user-supplied endpoint/credential.
"""

import logging
import threading
import time
from collections.abc import Callable
from typing import Any

from personalclaw.embedding_providers.base import (
    EmbeddingModel,
    EmbeddingProvider,
    run_embed_sync,
)

logger = logging.getLogger(__name__)

# Only the in-process native provider lives here. Remote providers are resolved
# through the LLM provider registry (see _llm_embed_fn).
_providers: dict[str, EmbeddingProvider] = {}

# The in-process native embedding provider goes by several names depending on
# where the ref was written: the bundled extension/manifest name is hyphenated
# (`sentence-transformers`), older refs use the underscore form, and `native` is
# the generic alias. All map to the one in-process provider.
_NATIVE_NAMES = ("sentence_transformers", "sentence-transformers", "native")


#: The adapters app scanners built (:func:`_ensure_scanned`), by name: the ones a scan may replace
#: or drop. A provider registered by name (the in-process native one) is never among them.
_scanned: dict[str, EmbeddingProvider] = {}
#: The scanners' :func:`~personalclaw.providers.media_scanners.generation` the last scan read.
_scanned_generation = -1
_scan_lock = threading.RLock()


def register_provider(provider: EmbeddingProvider) -> None:
    _providers[provider.name] = provider


def unregister_provider(name: str) -> None:
    _providers.pop(name, None)
    _scanned.pop(name, None)


def _ensure_scanned() -> None:
    """Reconcile the app-built embedding adapters with what the apps' scanners build now: one per
    config entry of a type core doesn't know (e.g. Bedrock), whose app registered a scanner on
    import.

    An adapter stays while its app builds one of the same class under its name, because what holds
    an embed function compares its sources by identity (:func:`same_basis`) and the adapter keeps
    why its last embedding failed. One of another class replaces it: the app was updated, so the
    code it runs now is the update's. One no scanner builds any more is dropped: its instance was
    removed, or its app taken out. An edit of an instance's settings rebuilds them all
    (:func:`refresh_providers`). A provider registered by name keeps its name."""
    global _scanned_generation
    with _scan_lock:
        try:
            from personalclaw.providers import media_scanners

            generation = media_scanners.generation()
            fresh = media_scanners.scan("embedding")
        except Exception:  # noqa: BLE001 — what was built stays; the next call scans again
            logger.debug("embedding scanner pass failed", exc_info=True)
            return
        built: set[str] = set()
        for prov in fresh:
            nm = getattr(prov, "name", "")
            if not nm:
                continue
            built.add(nm)
            held = _providers.get(nm)
            ours = held is not None and _scanned.get(nm) is held
            if held is None or (ours and type(held) is not type(prov)):
                _providers[nm] = prov
                _scanned[nm] = prov
        for nm in [n for n in _scanned if n not in built]:
            if _providers.get(nm) is _scanned[nm]:
                del _providers[nm]
            del _scanned[nm]
        _scanned_generation = generation


def refresh_providers() -> None:
    """Drop the app-built adapters, so the next resolution builds them from the settings saved
    now: what adding, editing or removing an instance in Settings → Providers calls."""
    global _scanned_generation
    with _scan_lock:
        for nm, prov in list(_scanned.items()):
            if _providers.get(nm) is prov:
                del _providers[nm]
        _scanned.clear()
        _scanned_generation = -1


def _scanners_changed() -> bool:
    """Whether an app's scanner was registered or taken back since the last scan (an update does
    both), so the adapters held may be the previous version's."""
    from personalclaw.providers import media_scanners

    return media_scanners.generation() != _scanned_generation


def get_provider(name: str) -> EmbeddingProvider | None:
    _ensure_scanned()
    return _providers.get(name)


def list_providers() -> list[EmbeddingProvider]:
    _ensure_scanned()
    return list(_providers.values())


def direct_provider(provider_name: str) -> EmbeddingProvider | None:
    """The embedding provider a ``provider:model`` ref embeds through DIRECTLY: the in-process
    native one (by any of its names), or an app's own adapter (Bedrock's). None when the ref
    embeds through a configured model provider's ``embed()`` instead (Ollama, an
    OpenAI-compatible endpoint), or names nothing that embeds."""
    if provider_name in _NATIVE_NAMES:
        ensure_registered()
        return _providers.get("native")
    _ensure_scanned()
    return _providers.get(provider_name)


def native_provider() -> EmbeddingProvider | None:
    """The registered in-process native embedding provider (the sentence-transformers
    app), or None when that app isn't installed/enabled. Core handlers that manage
    LOCAL embedding models (the Settings download UI) go through this rather than
    importing the app's substrate — so core stays torch-free and degrades gracefully
    when the app is absent."""
    return _providers.get("native")


async def list_native_models() -> list[EmbeddingModel]:
    """The local embedding-model catalog from the native provider (empty when the
    sentence-transformers app isn't installed)."""
    provider = native_provider()
    if provider is None:
        return []
    try:
        # ASYNC: the callers are aiohttp handlers running inside the gateway's
        # event loop, so this must be awaited — a prior asyncio.run() here raised
        # "cannot be called from a running event loop", was swallowed by the
        # except, and returned [] → the model always looked "not downloaded".
        return await provider.list_models()  # type: ignore[attr-defined]  # CI-3
    except Exception:
        logger.debug("list_native_models failed", exc_info=True)
        return []


async def is_native_model_downloaded(model_name: str) -> bool:
    """Whether a local (native) embedding model is downloaded — via the registered
    provider's catalog. False when the sentence-transformers app isn't installed."""
    return any(m.name == model_name and m.downloaded for m in await list_native_models())


async def delete_native_model(model_name: str) -> bool:
    """Delete a downloaded local embedding model via the native provider. False when
    the app isn't installed or the model isn't present."""
    provider = native_provider()
    if provider is None:
        return False
    try:
        return await provider.delete_model(model_name)  # type: ignore[attr-defined]  # CI-3
    except Exception:
        logger.debug("delete_native_model failed", exc_info=True)
        return False


def ensure_registered() -> None:
    """No-op retained for callers. The in-process native embedding provider now
    ships as the ``sentence-transformers`` APP — the app loader registers it (via the
    ModelTypeHandler ``embedding``-capability seam) when it's installed + enabled. When
    the app isn't installed, no native provider exists and embedding gracefully
    degrades (``get_active_embed_fn`` returns None for a native binding with no
    registered provider)."""
    return None


# ── Active model helpers (read from Settings > Models active_models.json) ──


def _active_embedding_spec() -> tuple[str, str] | None:
    """Parse the active embedding model reference from active_models.json.

    Returns ``(provider_name, model_id)`` or None if no embedding model is
    active. The model ref format is ``"provider_name:model_id"``.
    """
    from personalclaw.providers.use_cases import active_model_refs, split_ref

    refs = active_model_refs("embedding")
    if not refs:
        return None
    return split_ref(refs[0])


def _llm_embed_provider(provider_name: str, model_id: str) -> object | None:
    """The configured LLM Model provider that embeds with ``model_id``, or None.

    The provider (e.g. an ollama or openai-compatible endpoint the user
    configured in Settings > Models) performs the embedding through its own
    ``embed()`` using its endpoint/credential. Returns None if the provider
    can't be built or doesn't support embeddings.

    The BOUND embedding model (the ``embedding`` use-case selection) is threaded
    as the ``embedding_model`` build kwarg — the provider is CONFIGURED with it
    at construction, so ``embed()`` needs no per-call model input and no
    vendor-specific hardcoded default.
    """
    from personalclaw.llm.registry import ProviderResolutionError, get_default_registry

    # A provider that cannot be RESOLVED — no entry by that name, or an entry whose type no loaded
    # app provides — is a state, not a crash, so it is logged as one sentence that says which. Only
    # an unexpected exception (a factory that raised) keeps its traceback.
    try:
        registry = get_default_registry()
        provider = registry.build(provider_name, embedding_model=model_id)
    except ProviderResolutionError as unresolved:
        # The entry isn't in the registry yet. Config-defined `providers[]` entries are
        # replayed into the process-wide registry by `sync_entries_from_config()`, which
        # only the GATEWAY boot path calls — so any other entry point (a CLI command, a
        # worker, a test, a background pass that ran before boot finished) sees an empty
        # `_entries` and the embed silently returns None forever. Chat never hit this
        # because chat resolution runs after boot; the knowledge/memory embed pass does.
        #
        # The sync is idempotent (`register_entry` no-ops on a duplicate name) and reads
        # one JSON file, so replaying it here self-heals rather than failing. Retried
        # ONCE: a genuinely unknown provider must still fail loudly instead of looping.
        try:
            from personalclaw.llm.registry import sync_entries_from_config

            synced = sync_entries_from_config()
        except Exception:
            logger.warning(
                "Could not build embedding provider %r and config sync failed",
                provider_name,
                exc_info=True,
            )
            return None
        if not synced:
            logger.warning(
                "Embedding provider %r cannot be built (%s), and replaying config.json's "
                "providers added nothing — semantic embeddings stay off until it resolves",
                provider_name,
                unresolved,
            )
            return None
        logger.info(
            "Replayed %d config provider entr%s to resolve embedding provider %r",
            synced,
            "y" if synced == 1 else "ies",
            provider_name,
        )
        try:
            provider = registry.build(provider_name, embedding_model=model_id)
        except ProviderResolutionError as still_unresolved:
            logger.warning(
                "Embedding provider %r cannot be built (%s) — semantic embeddings stay off "
                "until it resolves",
                provider_name,
                still_unresolved,
            )
            return None
        except Exception:
            logger.warning(
                "Could not build embedding provider %r after config sync",
                provider_name,
                exc_info=True,
            )
            return None
    except Exception:
        logger.warning(
            "Could not build embedding provider %r from LLM registry", provider_name, exc_info=True
        )
        return None

    if getattr(provider, "embed", None) is None:
        logger.warning("Provider %r does not support embeddings", provider_name)
        return None
    return provider


def _llm_embed_fn(provider_name: str, model_id: str) -> Callable[[str], list[float] | None] | None:
    """A sync embed fn backed by a configured LLM Model provider (:func:`_llm_embed_provider`)."""
    provider: Any = _llm_embed_provider(provider_name, model_id)
    if provider is None:
        return None

    def _sync_embed(text: str) -> list[float] | None:
        async def _run() -> list[float] | None:
            await provider.start()
            vecs = await provider.embed([text])
            return list(vecs[0]) if vecs else None

        try:
            return run_embed_sync(_run, timeout=60)
        except Exception:
            logger.debug("Remote embed failed", exc_info=True)
            return None

    return _sync_embed


def _batch_timeout(texts: list[str]) -> float:
    """How long one batch may take: longer for more texts, because a provider may embed a batch
    one text after another (Bedrock's ``embed_batch`` loops ``embed()``)."""
    return max(60.0, 5.0 * len(texts))


def get_active_embed_fn() -> Callable[[str], list[float] | None] | None:
    """Return an embedding fn for the Settings > Models active selection.

    Returns None if no embedding model is active. The fn is built for the selection as it reads
    NOW and keeps it: a caller that holds one past a rebind holds the old model, which is why
    every long-lived store holds :func:`bound_embedding` instead.
    """
    spec = _active_embedding_spec()
    if not spec:
        return None
    return embed_fn_for(*spec)


def embed_fn_for(provider_name: str, model_id: str) -> Callable[[str], list[float] | None] | None:
    """A sync embed fn for ``provider_name``'s ``model_id``, or None when it cannot be built."""
    if provider_name in _NATIVE_NAMES:
        provider = direct_provider(provider_name)
        return provider.get_embed_fn(model_id) if provider else None

    # A directly-registered EmbeddingProvider (e.g. Bedrock, which has its own
    # embed() implementation via boto3) takes priority over the LLM-registry path.
    direct = direct_provider(provider_name)
    if direct is not None:

        def _direct_embed(text: str) -> list[float] | None:
            try:
                return run_embed_sync(lambda: direct.embed(text, model=model_id), timeout=60)
            except Exception:
                logger.debug("Direct embed provider %r failed", provider_name, exc_info=True)
                return None

        return _direct_embed

    return _llm_embed_fn(provider_name, model_id)


async def bound_unavailable_reason() -> str:
    """Why the bound embedding model cannot embed, in its provider's own words.

    The :meth:`~personalclaw.embedding_providers.base.EmbeddingProvider.unavailable_reason` of
    the in-process or app-registered provider behind the binding. ``""`` when nothing is bound,
    when the binding embeds through a configured model provider's own ``embed()`` (which has no
    such channel), or when the provider cannot say — and the caller uses its own words.
    """
    spec = _active_embedding_spec()
    if not spec:
        return ""
    provider = direct_provider(spec[0])
    reason = getattr(provider, "unavailable_reason", None)
    if reason is None:
        return ""
    try:
        return str(await reason() or "").strip()
    except Exception:  # noqa: BLE001 — a reason is best-effort; the caller has its own words
        logger.debug(
            "embedding provider %r could not say why it is unavailable", spec[0], exc_info=True
        )
        return ""


# ── The embedding a long-lived holder keeps: the model bound NOW ──

#: How long a binding whose provider could not be built waits before the next build attempt. A
#: provider an app registers after boot is found on the next call past this; a genuinely missing
#: one is not rebuilt, and warned about, on every embedding call.
_RETRY_UNBUILT_SECS = 30.0


def _embedding_sources(provider_name: str) -> tuple[object | None, ...]:
    """What an embed fn for ``provider_name`` embeds THROUGH, as it stands now.

    The in-process native provider, an app's directly registered adapter, or the configured
    instance's registry entry together with its type's registration. An edit in Settings →
    Providers re-registers the entry (a new endpoint, key or Default Model) and rebuilds the
    app-built adapters, an app that loads after boot registers the type, and an app update's
    scanner builds its adapter from the update's code, so each reads as a different source;
    ``None`` where there is none yet.
    """
    if provider_name in _NATIVE_NAMES:
        return (_providers.get("native"),)
    if _scanners_changed():
        _ensure_scanned()
    direct = _providers.get(provider_name)
    if direct is not None:
        return (direct,)
    from personalclaw.llm.registry import ProviderResolutionError, get_default_registry

    registry = get_default_registry()
    try:
        entry = registry.get_entry(provider_name)
    except ProviderResolutionError:
        return (None, None)
    try:
        registered = registry.capability_of(entry.type)
    except ProviderResolutionError:
        registered = None
    return (entry, registered)


def embedding_basis() -> tuple[object, ...] | None:
    """What embedding with the bound model is built from: its ``provider:model`` ref, then its
    sources (:func:`_embedding_sources`), or ``None`` when no embedding model is bound.

    #3719's rule for a runtime, applied to embedding: a holder rebuilds what it embeds with when
    this no longer reads the same (:func:`same_basis`) — a rebind, a clear, an edit of the
    instance, or its app registering its type.
    """
    spec = _active_embedding_spec()
    if not spec or not spec[1]:
        return None
    return (f"{spec[0]}:{spec[1]}", *_embedding_sources(spec[0]))


def same_basis(a: object, b: object) -> bool:
    """Whether two :func:`embedding_basis` reads are the same basis: one ref, and the SAME source
    objects. By identity, not equality: an entry re-registered with equal fields is still a new
    registration (its app may have been reloaded), and a provider built from the old one is not
    what the binding builds now."""
    if not isinstance(a, tuple) or not isinstance(b, tuple):
        return a is None and b is None
    return len(a) == len(b) and a[0] == b[0] and all(x is y for x, y in zip(a[1:], b[1:]))


class BoundEmbedding:
    """The embedding function every long-lived store holds: the model bound NOW, at each call.

    A store is built once and kept (the gateway's main memory for the process's life, a
    directory's memory from the first time the directory is opened), so a function built when
    the store was built is the model bound THEN: a rebind reached new stores only, and clearing
    Embedding reached none. This reads the binding at each call instead and rebuilds when its
    :func:`embedding_basis` moved, so a rebind, a clear or an instance edit reaches every holder
    at its next use.

    :meth:`current` also names the model it embeds with, because a vector is only comparable
    with vectors of the same model, and two models can share a width.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._basis: tuple[object, ...] | None = None
        self._fn: Callable[[str], list[float] | None] | None = None
        self._failed_at = 0.0

    @staticmethod
    def ref() -> str | None:
        """The ``provider:model`` ref bound now, or ``None``. Reads the binding; builds nothing."""
        spec = _active_embedding_spec()
        if not spec or not spec[1]:
            return None
        return f"{spec[0]}:{spec[1]}"

    def current(self) -> tuple[Callable[[str], list[float] | None] | None, str | None]:
        """``(embed fn, ref)`` for the model bound now.

        ``(None, None)`` when nothing is bound; ``(None, ref)`` when the bound model's provider
        cannot be built yet, which embeds nothing and is tried again after a short wait.
        """
        basis = embedding_basis()
        if basis is None:
            return None, None
        ref = str(basis[0])
        provider_name, _, model_id = ref.partition(":")
        with self._lock:
            due = self._fn is None and time.monotonic() - self._failed_at >= _RETRY_UNBUILT_SECS
            if not same_basis(basis, self._basis) or due:
                self._fn = embed_fn_for(provider_name, model_id)
                # Read the sources AFTER the build: building can register the entry it was
                # missing (`_llm_embed_fn` replays config.json), and recording the pre-build
                # "no source" would rebuild on the very next call.
                self._basis = (ref, *_embedding_sources(provider_name))
                self._failed_at = 0.0 if self._fn is not None else time.monotonic()
            return self._fn, ref


_bound_embedding = BoundEmbedding()


def bound_embedding() -> BoundEmbedding:
    """The process-wide :class:`BoundEmbedding` every memory store embeds through."""
    return _bound_embedding


def get_active_embed_many_fn() -> Callable[[list[str]], list[list[float] | None]] | None:
    """A BATCH embedding fn for the active selection, or None when the provider has no batch path.

    Returns None rather than a per-text shim when there is no batch path: `embed_batch.embed_texts`
    already falls back to the single-text fn, and a shim here would make "this provider batches"
    unanswerable — the caller could not tell 32 real batch calls from 32 sequential ones.
    """
    spec = _active_embedding_spec()
    if not spec:
        return None
    return embed_many_fn_for(*spec)


def embed_many_fn_for(
    provider_name: str, model_id: str
) -> Callable[[list[str]], list[list[float] | None]] | None:
    """A BATCH embed fn for ``provider_name``'s ``model_id``: one call for a group of texts.

    Every kind of provider has one. The in-process and app-registered ones through
    `EmbeddingProvider.embed_batch` (declared on the ABC, so a provider that never overrode it
    inherits a loop over ``embed()`` — still one call per group for the caller). A configured
    Model provider through its own ``embed(inputs)``, which takes a list: Ollama's ``/api/embed``
    and an OpenAI-compatible ``/embeddings`` answer a whole group in one request. Before this, that
    kind had none, so a provider-backed binding embedded every text of a group as its own request.

    Raises what the provider raised, unlike the single-text fn: `embed_batch.embed_texts` retries
    and splits a failed group on the exception, and a swallowed one would read as success.

    One bridged call per BATCH, through `run_embed_sync` so it works from sync code with or
    without a running loop (a raw `asyncio.run()` raises inside one — the ingest and chunk-backfill
    paths run there).
    """
    direct = direct_provider(provider_name)
    if direct is not None:
        batch = getattr(direct, "embed_batch", None)
        if not callable(batch):
            return None

        def _direct_many(texts: list[str]) -> list[list[float] | None]:
            return list(
                run_embed_sync(lambda: batch(texts, model=model_id), timeout=_batch_timeout(texts))
                or []
            )

        return _direct_many
    if provider_name in _NATIVE_NAMES:
        return None
    provider: Any = _llm_embed_provider(provider_name, model_id)
    if provider is None:
        return None

    def _llm_many(texts: list[str]) -> list[list[float] | None]:
        async def _run() -> list[list[float]]:
            await provider.start()
            return await provider.embed(list(texts))

        return [list(v) for v in run_embed_sync(_run, timeout=_batch_timeout(texts)) or []]

    return _llm_many


def get_active_embedding_dim() -> int | None:
    """Return the dimension for the active embedding model, or None."""
    spec = _active_embedding_spec()
    if not spec:
        return None
    provider_name, model_id = spec

    if provider_name in _NATIVE_NAMES:
        # Ask the registered native provider (the sentence-transformers app) for its
        # model catalog; a match gives the exact dimension without loading the model.
        provider = _providers.get("native")
        if provider is not None:
            try:
                # This sync helper is called from BOTH sync (CLI, context builder)
                # and async (the memory handler) contexts. A bare asyncio.run()
                # raises inside a running loop, so the shared bridge runs list_models()
                # off-loop when one is already active (mirrors get_active_embed_fn).
                async def _list():
                    return await provider.list_models()  # type: ignore[attr-defined]  # CI-3

                models = run_embed_sync(_list, timeout=30)
                for m in models:
                    if m.name == model_id:
                        return m.dimension
            except Exception:
                logger.debug("native dim lookup via provider.list_models failed", exc_info=True)
        # Fall through to a probe if the catalog didn't resolve it.

    # Any provider (native without a catalog hit, or remote): discover the dimension
    # by probing a sample embedding through the resolved embed fn.
    fn = get_active_embed_fn()
    if fn:
        vec = fn("dimension probe")
        if vec:
            return len(vec)
    return None

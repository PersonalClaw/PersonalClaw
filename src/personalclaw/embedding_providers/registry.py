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

import asyncio
import logging
from collections.abc import Callable

from personalclaw.embedding_providers.base import EmbeddingModel, EmbeddingProvider

logger = logging.getLogger(__name__)

# Only the in-process native provider lives here. Remote providers are resolved
# through the LLM provider registry (see _llm_embed_fn).
_providers: dict[str, EmbeddingProvider] = {}

# The in-process native embedding provider goes by several names depending on
# where the ref was written: the bundled extension/manifest name is hyphenated
# (`sentence-transformers`), older refs use the underscore form, and `native` is
# the generic alias. All map to the one in-process provider.
_NATIVE_NAMES = ("sentence_transformers", "sentence-transformers", "native")


def register_provider(provider: EmbeddingProvider) -> None:
    _providers[provider.name] = provider


def unregister_provider(name: str) -> None:
    _providers.pop(name, None)


def _ensure_scanned() -> None:
    """Build app-contributed embedding adapters (e.g. Bedrock) for config entries
    of types core doesn't know — the app registered a scanner on import. Idempotent
    (dedupes by ``provider.name``)."""
    try:
        from personalclaw.providers.media_scanners import scan
        for prov in scan("embedding"):
            nm = getattr(prov, "name", "")
            if nm and nm not in _providers:
                _providers[nm] = prov
    except Exception:  # noqa: BLE001
        logger.debug("embedding scanner pass failed", exc_info=True)


def get_provider(name: str) -> EmbeddingProvider | None:
    _ensure_scanned()
    return _providers.get(name)


def list_providers() -> list[EmbeddingProvider]:
    _ensure_scanned()
    return list(_providers.values())


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
        return await provider.list_models()
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
        return await provider.delete_model(model_name)
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


def _llm_embed_fn(provider_name: str, model_id: str) -> Callable[[str], list[float] | None] | None:
    """Build a sync embed fn backed by a configured LLM Model provider.

    The provider (e.g. an ollama or openai-compatible endpoint the user
    configured in Settings > Models) performs the embedding through its own
    ``embed()`` using its endpoint/credential. Returns None if the provider
    can't be built or doesn't support embeddings.

    The BOUND embedding model (the ``embedding`` use-case selection) is threaded
    as the ``embedding_model`` build kwarg — the provider is CONFIGURED with it
    at construction, so ``embed()`` needs no per-call model input and no
    vendor-specific hardcoded default.
    """
    from personalclaw.llm.registry import get_default_registry

    try:
        registry = get_default_registry()
        provider = registry.build(provider_name, embedding_model=model_id)
    except Exception:
        logger.warning("Could not build embedding provider %r from LLM registry", provider_name, exc_info=True)
        return None

    embed = getattr(provider, "embed", None)
    if embed is None:
        logger.warning("Provider %r does not support embeddings", provider_name)
        return None

    def _sync_embed(text: str) -> list[float] | None:
        async def _run() -> list[float] | None:
            await provider.start()
            vecs = await embed([text])
            return list(vecs[0]) if vecs else None

        try:
            asyncio.get_running_loop()
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor() as pool:
                return pool.submit(asyncio.run, _run()).result(timeout=60)
        except RuntimeError:
            return asyncio.run(_run())
        except Exception:
            logger.debug("Remote embed failed", exc_info=True)
            return None

    return _sync_embed


def get_active_embed_fn() -> Callable[[str], list[float] | None] | None:
    """Return an embedding fn for the Settings > Models active selection.

    Returns None if no embedding model is active.
    """
    spec = _active_embedding_spec()
    if not spec:
        return None
    provider_name, model_id = spec

    if provider_name in _NATIVE_NAMES:
        ensure_registered()
        provider = _providers.get("native")
        return provider.get_embed_fn(model_id) if provider else None

    # A directly-registered EmbeddingProvider (e.g. Bedrock, which has its own
    # embed() implementation via boto3) takes priority over the LLM-registry path.
    _ensure_scanned()
    direct = _providers.get(provider_name)
    if direct is not None:
        def _direct_embed(text: str) -> list[float] | None:
            try:
                asyncio.get_running_loop()
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    return pool.submit(
                        asyncio.run, direct.embed(text, model=model_id)
                    ).result(timeout=60)
            except RuntimeError:
                return asyncio.run(direct.embed(text, model=model_id))
            except Exception:
                logger.debug("Direct embed provider %r failed", provider_name, exc_info=True)
                return None
        return _direct_embed

    return _llm_embed_fn(provider_name, model_id)


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
                import asyncio

                # This sync helper is called from BOTH sync (CLI, context builder)
                # and async (the memory handler) contexts. A bare asyncio.run()
                # raises inside a running loop, so run list_models() on a worker
                # thread when a loop is already active (mirrors get_active_embed_fn).
                async def _list():
                    return await provider.list_models()
                try:
                    asyncio.get_running_loop()
                    import concurrent.futures
                    with concurrent.futures.ThreadPoolExecutor() as pool:
                        models = pool.submit(asyncio.run, _list()).result(timeout=30)
                except RuntimeError:
                    models = asyncio.run(_list())
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

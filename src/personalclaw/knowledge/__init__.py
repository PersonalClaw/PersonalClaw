"""Knowledge Library -- personal knowledge graph for PersonalClaw."""

import os

from personalclaw.knowledge.store import KnowledgeStore, normalize_url

__all__ = [
    "KnowledgeStore",
    "normalize_url",
    "knowledge_db_path",
    "knowledge_files_dir",
    "get_knowledge_store",
    "get_knowledge_llm_pool",
    "get_knowledge_embedder",
]

_store: "KnowledgeStore | None" = None
_llm_pool = None  # lazy process-wide LLMPool for callers without a gateway handle
_embedder = None  # cached process-wide embedder for callers without a gateway handle
_embedder_basis: object = False  # what the cache was built from (sentinel: not yet built)


def knowledge_db_path() -> str:
    """The canonical knowledge DB path (the dashboard state opens the same file)."""
    from personalclaw.config.loader import config_dir

    db_dir = os.path.join(str(config_dir()), "workspace", "knowledge")
    os.makedirs(db_dir, exist_ok=True)
    return os.path.join(db_dir, "knowledge.db")


def knowledge_files_dir() -> str:
    """Directory holding uploaded media/document originals + generated thumbnails.

    Sits beside the knowledge DB so previewable items can serve their bytes back
    via ``GET /api/knowledge/items/{id}/file``."""
    from personalclaw.config.loader import config_dir

    files_dir = os.path.join(str(config_dir()), "workspace", "knowledge", "files")
    os.makedirs(files_dir, exist_ok=True)
    return files_dir


def get_knowledge_store() -> "KnowledgeStore":
    """Process-wide KnowledgeStore at the canonical path — for callers without a
    dashboard-state handle (e.g. the native agent ``knowledge_*`` tools). Opens
    the same DB the dashboard uses, so reads/writes are consistent."""
    global _store
    if _store is None:
        _store = KnowledgeStore(knowledge_db_path())
    return _store


def get_knowledge_llm_pool():
    """Process-wide LLMPool for callers without a gateway handle (the native agent
    ``knowledge_*`` tools). Routes through the same use-case model resolver the
    gateway uses, so inline enrichment (insights/entities) works for agent-created
    items. Lazy — only spun up on first agent write."""
    global _llm_pool
    if _llm_pool is None:
        from personalclaw.knowledge.llm_pool import LLMPool

        _llm_pool = LLMPool()
    return _llm_pool


def get_knowledge_embedder():
    """Process-wide knowledge embedder for callers without a gateway handle (the native
    agent ``knowledge_*`` tools). Built from the Settings > Models active embedding
    selection — the SAME config the gateway's ingest queue + context-search use — so an
    agent that creates a knowledge item gets it embedded (vector-searchable by everyone)
    and an agent search gets full hybrid (keyword+graph+vector) retrieval, not the
    degraded keyword-only path. Returns None when embeddings are disabled/unavailable.

    Cached, but keyed on what embedding with the bound model is built from
    (``embedding_providers.registry.embedding_basis``): the binding (provider:model) and the
    provider instance it embeds through. A rebind, a clear, or an edit of that instance in
    Settings → Providers rebuilds it at the next call — never serving a stale embedder that
    would write vectors of the wrong model/dimension into the shared store, or keep calling an
    endpoint the user replaced. It used to be keyed on the binding alone, so an edited instance
    kept the provider built from it before the edit. The native sentence-transformers model is
    expensive to load, so we don't rebuild while the basis is unchanged."""
    global _embedder, _embedder_basis
    from personalclaw.embedding_providers.registry import embedding_basis, same_basis

    basis = embedding_basis()
    if _embedder_basis is False or not same_basis(basis, _embedder_basis):
        try:
            import json as _json

            from personalclaw.config.loader import config_path
            from personalclaw.knowledge.embedder import create_embedder_from_config

            cfg_path = config_path()
            cfg = _json.loads(cfg_path.read_text()) if cfg_path.exists() else {}
            _embedder = create_embedder_from_config(cfg)
        except Exception:
            _embedder = None
        # Read after the build: building can register the instance it embeds through
        # (`_llm_embed_fn` replays config.json), and the pre-build read would rebuild next call.
        _embedder_basis = embedding_basis()
    return _embedder

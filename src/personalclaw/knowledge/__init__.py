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
_embedder_spec: object = (
    False  # the embedding selection the cache was built for (sentinel: not yet built)
)


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

    Cached, but keyed on the active embedding selection (provider:model): if the user
    switches embedding models in Settings, the next call rebuilds — never serving a stale
    embedder that would write vectors of the wrong model/dimension into the shared store.
    The native sentence-transformers model is expensive to load, so we don't rebuild when
    the selection is unchanged."""
    global _embedder, _embedder_spec
    try:
        from personalclaw.embedding_providers.registry import _active_embedding_spec

        spec = _active_embedding_spec()
    except Exception:
        spec = None
    if spec != _embedder_spec:
        try:
            import json as _json

            from personalclaw.config.loader import config_path
            from personalclaw.knowledge.embedder import create_embedder_from_config

            cfg_path = config_path()
            cfg = _json.loads(cfg_path.read_text()) if cfg_path.exists() else {}
            _embedder = create_embedder_from_config(cfg)
        except Exception:
            _embedder = None
        _embedder_spec = spec
    return _embedder

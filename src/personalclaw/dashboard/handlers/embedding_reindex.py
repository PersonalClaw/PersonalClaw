"""HTTP API for embedding re-index jobs — /api/models/embedding/reindex/*.

Switching the active embedding model invalidates every stored vector. A POST
here re-resolves the (already-applied) active embedding model, gates on its
availability, and starts a background re-index of the knowledge store and every
memory store with SSE progress. Mirrors the download-job route shape.
"""

from __future__ import annotations

import logging
from typing import Any

from aiohttp import web

logger = logging.getLogger(__name__)


def _registry(request: web.Request):
    return request.app["state"].embedding_reindex()


def _resolve_embed(app) -> tuple[object | None, object | None, str]:
    """Resolve the NEW active embedding model into (embedder, embed_fn, model).

    ``embedder`` is the knowledge-side embedder (``embed_for_item``); ``embed_fn``
    proves the model answers (one probe). Either is None when the selected model
    can't produce vectors (not downloaded / unreachable) — the caller treats that as
    "not ready" and refuses to wipe the vectors. Memory stores embed with the bound
    model on their own (``VectorMemoryStore.embed_fn``), so none is handed to them.
    """
    from personalclaw.embedding_providers.registry import (
        _active_embedding_spec,
        get_active_embed_fn,
    )

    spec = _active_embedding_spec()
    model = f"{spec[0]}:{spec[1]}" if spec else ""

    embed_fn = get_active_embed_fn()
    # Probe once — a returned fn that yields None means the model is unreachable.
    ready = False
    if embed_fn is not None:
        try:
            ready = bool(embed_fn("readiness probe"))
        except Exception:
            ready = False

    embedder = None
    if ready:
        import json

        from personalclaw.config.loader import config_path
        from personalclaw.knowledge.embedder import create_embedder_from_config

        try:
            cfg_path = config_path()
            cfg = json.loads(cfg_path.read_text()) if cfg_path.exists() else {}
        except Exception:
            cfg = {}
        try:
            embedder = create_embedder_from_config(cfg)
        except Exception:
            embedder = None
    return (embedder if ready else None), (embed_fn if ready else None), model


async def api_reindex_list(request: web.Request) -> web.Response:
    """GET /api/models/embedding/reindex — live + recently-finished jobs."""
    reg = _registry(request)
    return web.json_response(
        {
            "jobs": [j.to_dict() for j in reg.list()],
            "active": (reg.active().to_dict() if reg.active() else None),
        }
    )


async def api_reindex_start(request: web.Request) -> web.Response:
    """POST /api/models/embedding/reindex — start a re-index of all embeddings.

    Call AFTER the active embedding model has been changed
    (``PUT /api/models/active/embedding``). Returns ``202`` with the job, or
    ``409`` if the newly-selected model isn't ready (so the caller can keep the
    old vectors instead of wiping them with no way to rebuild).
    """
    state = request.app["state"]
    embedder, embed_fn, model = _resolve_embed(request.app)
    if embed_fn is None:
        return web.json_response(
            {
                "error": "The selected embedding model is not available (download it "
                "or check the provider connection before re-indexing).",
                "code": "model_not_ready",
            },
            status=409,
        )

    from personalclaw.dashboard.handlers.memory import _get_provider

    job, error = _registry(request).start(
        model=model,
        knowledge_store=getattr(state, "knowledge_store", None),
        memory_store=_get_provider(state),
        embedder=embedder,
    )
    if error is not None:
        return web.json_response({"error": error}, status=400)
    return web.json_response(job.to_dict(), status=202)


async def api_reindex_stream(request: web.Request) -> web.StreamResponse:
    """GET /api/models/embedding/reindex/{id}/stream — per-job progress SSE."""
    from personalclaw.dashboard.embedding_reindex import registry_key
    from personalclaw.dashboard.sse import stream_response

    reg = _registry(request)
    job_id = request.match_info["id"]
    job = reg.get(job_id)
    if job is None:
        return web.json_response({"error": "Not found"}, status=404)

    key = registry_key(job_id)
    hub = reg.sse.hub(key)
    return await stream_response(
        request,
        hub,
        on_connect=[("snapshot", job.to_dict())],
        registry_evict=(reg.sse, key),
    )


def resume_interrupted_reindex(app) -> Any:
    """At the gateway's start, run the re-index an interrupted or orphaned one left to do.

    Knowledge: an item with text and no vector, or one at another width than the model bound now
    (the gateway died between a model change and the end of its re-index). Memory: a vector in
    any memory store that is not the bound model's — another model's, from the same kind of
    stop, or one written before each vector recorded its model, which after the update that
    started recording it is every memory vector. Until re-embedded they are read by keyword.

    Knowledge is left out of a job only memory needs: the job re-embeds every knowledge item it
    is handed. Returns the job started, or None. Best-effort: it logs and returns on any refusal.
    """
    from personalclaw.context import every_memory_vector_store
    from personalclaw.dashboard.handlers.memory import _get_provider

    state = app["state"]
    ks = getattr(state, "knowledge_store", None)
    # The model's width decides which knowledge vectors are stale, so resolve it before counting.
    embedder, embed_fn, model = _resolve_embed(app)
    _dim = getattr(embedder, "dim", None) if embedder is not None else None
    active_dim = _dim() if callable(_dim) else None
    knowledge = ks.count_items_needing_reembed(active_dim) if ks is not None else 0
    main = _get_provider(state)
    with every_memory_vector_store(main) as stores:
        memory = sum(_stale_vectors(store) for store in stores)
    if knowledge <= 0 and memory <= 0:
        return None  # every store is whole (or empty)
    if embed_fn is None:
        logger.warning(
            "Embedding re-index needed (%d knowledge item(s), %d memory vector(s)), but the bound "
            "embedding model (%s) isn't ready: they stay keyword-searchable until it is.",
            knowledge,
            memory,
            model or "none",
        )
        return None
    job, error = state.embedding_reindex().start(
        model=model,
        knowledge_store=ks if knowledge > 0 else None,
        memory_store=main,
        embedder=embedder,
    )
    if error:
        logger.warning("Auto-resume re-index refused: %s", error)
        return None
    logger.info(
        "Auto-resuming embedding re-index (%d knowledge item(s), %d memory vector(s)) with model "
        "%s [job %s]",
        knowledge,
        memory,
        model,
        getattr(job, "id", "?"),
    )
    return job


def _stale_vectors(store: Any) -> int:
    """``store``'s vectors the bound model did not write (0 for a store that cannot be read)."""
    try:
        return int(store.memory_stats().get("embedded_stale") or 0)
    except Exception:  # noqa: BLE001 — the job, if one runs, names the store it cannot read
        return 0


def register_embedding_reindex_routes(app: web.Application) -> None:
    """Register /api/models/embedding/reindex/* routes."""
    app.router.add_get("/api/models/embedding/reindex", api_reindex_list)
    app.router.add_post("/api/models/embedding/reindex", api_reindex_start)
    app.router.add_get("/api/models/embedding/reindex/{id}/stream", api_reindex_stream)

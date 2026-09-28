"""HTTP API for embedding re-index jobs — /api/models/embedding/reindex/*.

Switching the active embedding model invalidates every stored vector. A POST
here re-resolves the (already-applied) active embedding model, gates on its
availability, and starts a background re-index of the knowledge store and every
memory store with SSE progress. Mirrors the download-job route shape.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
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
    model = f"{spec[0]}:{spec[1]}" if spec and spec[1].strip() else ""

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

    Knowledge: an item the model bound now has not embedded — no vector, one another model wrote,
    or one written before items recorded their model — or one at another width than the model
    writes (the gateway died between a model change and the end of its re-index), and a passage
    vector another model wrote (it died in the re-index's passage phase, after the items). Memory: a
    vector in any memory store that is not the bound model's — another model's, from the same
    kind of stop, or one written before each vector recorded its model — and a memory no model
    embedded, written while none was bound. Until embedded they are read by keyword.

    Returns the job started, or None. Blocking (it probes the model once), so the start runs it
    before serving; a binding runs :func:`reindex_after_binding` instead. Best-effort: it logs and
    returns on any refusal.
    """
    return _start_pending(app, _pending_reembed(app))


async def reindex_after_binding(app) -> Any:
    """Once Embedding is bound to a model (``PUT /api/models/active/embedding``), embed what it
    has not: every memory no model embedded joins semantic search then, not at the next re-index
    someone starts. The probe and the counts run off the event loop. Returns the job, or None,
    and never raises: the binding it follows has already been saved.
    """
    try:
        return _start_pending(app, await asyncio.to_thread(_pending_reembed, app))
    except Exception:  # noqa: BLE001 — the start and the next re-index retry; the log says why
        logger.warning("The re-index after an Embedding binding could not start", exc_info=True)
        return None


@dataclass(frozen=True)
class _Pending:
    """What the model bound now has not embedded, and whether it can embed it."""

    model: str
    embedder: object | None
    ready: bool
    knowledge: int
    memory: int
    #: Passage (chunk) vectors another model wrote. Counted apart from the items: the re-index
    #: re-embeds the items first, so one stopped in its passage phase leaves every item current
    #: and only these to do, and a start that counted items alone never finished them.
    passages: int = 0


def _pending_reembed(app) -> _Pending:
    """Count what the bound model has not embedded (blocking: resolving it probes it once)."""
    from personalclaw.context import every_memory_vector_store
    from personalclaw.dashboard.handlers.memory import _get_provider

    state = app["state"]
    ks = getattr(state, "knowledge_store", None)
    # The model's width decides which knowledge vectors are stale, so resolve it before counting.
    embedder, embed_fn, model = _resolve_embed(app)
    if not model:
        return _Pending(model="", embedder=None, ready=False, knowledge=0, memory=0)
    _dim = getattr(embedder, "dim", None) if embedder is not None else None
    active_dim = _dim() if callable(_dim) else None
    knowledge = ks.count_items_to_reembed(active_dim) if ks is not None else 0
    passages = ks.count_stale_chunk_vectors() if ks is not None else 0
    with every_memory_vector_store(_get_provider(state)) as stores:
        memory = sum(_not_embedded(store) for store in stores)
    return _Pending(
        model=model,
        embedder=embedder,
        ready=embed_fn is not None,
        knowledge=knowledge,
        memory=memory,
        passages=passages,
    )


def _start_pending(app, pending: _Pending) -> Any:
    """Start the re-index *pending* calls for, on the event loop's thread; None when none is."""
    from personalclaw.dashboard.handlers.memory import _get_provider

    knowledge = pending.knowledge > 0 or pending.passages > 0
    if not knowledge and pending.memory <= 0:
        return None  # nothing bound, or every store is whole (or empty)
    if not pending.ready:
        logger.warning(
            "Embedding re-index needed (%d knowledge item(s), %d passage vector(s), %d memory "
            "vector(s)), but the bound embedding model (%s) isn't ready: they stay "
            "keyword-searchable until it is.",
            pending.knowledge,
            pending.passages,
            pending.memory,
            pending.model,
        )
        return None
    state = app["state"]
    ks = getattr(state, "knowledge_store", None)
    job, error = state.embedding_reindex().start(
        model=pending.model,
        knowledge_store=ks if knowledge else None,
        memory_store=_get_provider(state),
        embedder=pending.embedder,
    )
    if error:
        logger.warning("Embedding re-index refused: %s", error)
        return None
    logger.info(
        "Embedding re-index (%d knowledge item(s), %d passage vector(s), %d memory vector(s)) "
        "with model %s [job %s]",
        pending.knowledge,
        pending.passages,
        pending.memory,
        pending.model,
        getattr(job, "id", "?"),
    )
    return job


def _not_embedded(store: Any) -> int:
    """``store``'s memories the bound model did not embed — another model's vectors, and the
    memories no model embedded — as every surface counts them (``read_by_keyword``); 0 for a store
    that cannot be read."""
    try:
        return int(store.memory_stats().get("read_by_keyword") or 0)
    except Exception:  # noqa: BLE001 — the job, if one runs, names the store it cannot read
        return 0


def register_embedding_reindex_routes(app: web.Application) -> None:
    """Register /api/models/embedding/reindex/* routes."""
    app.router.add_get("/api/models/embedding/reindex", api_reindex_list)
    app.router.add_post("/api/models/embedding/reindex", api_reindex_start)
    app.router.add_get("/api/models/embedding/reindex/{id}/stream", api_reindex_stream)

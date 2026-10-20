"""HTTP API for embedding re-index jobs — /api/models/embedding/reindex/*.

Switching the active embedding model invalidates every stored vector. A POST
here re-resolves the (already-applied) active embedding model, gates on its
availability, and starts a background re-index of the knowledge store and every
memory store with SSE progress. Mirrors the download-job route shape.

Every change of the model reaches the re-index by one path, :func:`reindex_for_binding`,
whether it is saved here, comes from a provider's removal, is written by another process, or
waits for the model to be ready; :func:`watch_embedding_binding` takes it whenever it is due. So
do another home's vectors, arriving by a sync, a restore's merge or an import
(``embedding_arrivals``).
"""

from __future__ import annotations

import asyncio
import logging
import time
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
    # Off the event loop: resolving the model embeds one probe with it, a round trip that
    # every other request waited for on the loop.
    embedder, embed_fn, model = await asyncio.to_thread(_resolve_embed, request.app)
    if embed_fn is None:
        from personalclaw.embedding_providers.registry import bound_unavailable_reason

        # The bound provider's own reason when it has one (its credentials failed): the
        # download-or-reconnect advice is only a guess at a cause it could not name.
        why = await bound_unavailable_reason()
        return web.json_response(
            {
                "error": why
                or "The selected embedding model is not available (download it "
                "or check the provider connection before re-indexing).",
                "code": "model_not_ready",
            },
            status=409,
        )

    from personalclaw.dashboard.handlers.memory import _global_provider

    job, error = _registry(request).start(
        model=model,
        knowledge_store=getattr(state, "knowledge_store", None),
        memory_store=_global_provider(state),
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


async def reindex_for_binding(app) -> Any:
    """THE path every change of the embedding model takes to the re-index: embed what the model
    bound now has not.

    Whichever way the model changed — Embedding bound in Settings → Models or by any caller of
    ``PUT /api/models/active/embedding``, a provider removed so the next model in the chain is
    bound instead, a binding another process wrote (``--seed-local-model``, an edit by hand), or
    the gateway starting over what a stop or an update left — this counts what the model bound
    now has not embedded, in the knowledge store and every memory store, and starts the one
    re-index for it. Knowledge: an item with no vector, one another model wrote or one written
    before items recorded their model, one at another width than the model writes, and a passage
    vector another model wrote. Memory: a vector that is not the bound model's, and a memory no
    model embedded. Until embedded they are read by keyword.

    A model that cannot embed yet — not downloaded, its provider not up, its app not loaded — is
    looked at again later (``ReindexRegistry.check_again``, taken by
    :func:`watch_embedding_binding`), so its re-index runs once it is ready rather than at the
    next restart. The probe and the counts run off the event loop. Returns the job started, or
    None, and never raises: the change it follows has already been saved.
    """
    from personalclaw.embedding_providers.registry import BoundEmbedding

    registry = app["state"].embedding_reindex()
    try:
        pending = await asyncio.to_thread(_pending_reembed, app)
        job = _start_pending(app, pending, said=registry.waiting(pending.model))
    except Exception:  # noqa: BLE001 — looked at again, and the log says why
        logger.warning("The embedding re-index check failed; it is tried again", exc_info=True)
        registry.check_again(BoundEmbedding.ref() or "", time.monotonic())
        return None
    waiting = pending.knowledge > 0 or pending.passages > 0 or pending.memory > 0
    if not waiting or (job is not None and job.model == pending.model):
        registry.settle(pending.model)
    else:
        # Not ready, refused, or another model's re-index is running: its end is not this one.
        registry.check_again(pending.model, time.monotonic())
    return job


#: The checks a change of the binding made in this process runs (:func:`schedule_reindex_for_
#: binding`), held until they finish — a bare task can be collected — and what a caller that must
#: see one through awaits.
BINDING_CHECKS: set[asyncio.Task] = set()  # type: ignore[type-arg]


def schedule_reindex_for_binding(app) -> None:
    """Take the one path now, in the background: what a change of the embedding binding made in
    this process calls — a binding saved, a provider removed — so its re-index starts at once
    rather than at the watch's next pass. A no-op for an app with no re-index registry."""
    state = app.get("state") if hasattr(app, "get") else None
    if state is None or not callable(getattr(state, "embedding_reindex", None)):
        return
    task = asyncio.ensure_future(reindex_for_binding(app))
    BINDING_CHECKS.add(task)
    task.add_done_callback(BINDING_CHECKS.discard)


#: How often the gateway's watch reads the binding (one small file read; no probe unless due).
WATCH_SECS = 30.0


async def watch_embedding_binding(app, *, every: float = WATCH_SECS) -> None:
    """The gateway's watch on the embedding binding, from its start to its stop.

    Takes :func:`reindex_for_binding` at once — the start's check, for what a stop or an update
    left — and then whenever the model bound now is not the one it last settled, a look again it
    asked for is due (``ReindexRegistry.check_due``), or a merge brought another home's rows in
    (``embedding_arrivals``): a sync, a restore's merge or an import, whose vectors another model
    may have written. Reading the binding is a file read, so the watch sees a change nothing in
    this process made, such as a binding another process wrote, and it probes a model only when
    one of those is so. Rows that arrive while a re-index runs are taken once it ends, since the
    one running counted what was there when it began.
    """
    from personalclaw import embedding_arrivals
    from personalclaw.embedding_providers.registry import BoundEmbedding

    registry = app["state"].embedding_reindex()
    while True:
        try:
            due = registry.check_due(BoundEmbedding.ref() or "", time.monotonic())
            if registry.active() is None and embedding_arrivals.take():
                due = True
            if due:
                await reindex_for_binding(app)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — one failed pass must not end the watch
            logger.warning("The embedding binding watch could not read the binding", exc_info=True)
        await asyncio.sleep(every)


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
    from personalclaw.dashboard.handlers.memory import _global_provider

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
    with every_memory_vector_store(_global_provider(state)) as stores:
        memory = sum(_not_embedded(store) for store in stores)
    return _Pending(
        model=model,
        embedder=embedder,
        ready=embed_fn is not None,
        knowledge=knowledge,
        memory=memory,
        passages=passages,
    )


def _start_pending(app, pending: _Pending, *, said: bool = False) -> Any:
    """Start the re-index *pending* calls for, on the event loop's thread; None when none is.

    *said*: the model is already waiting to be ready and the log has said so, so each look again
    after that is not another warning."""
    from personalclaw.dashboard.handlers.memory import _global_provider

    knowledge = pending.knowledge > 0 or pending.passages > 0
    if not knowledge and pending.memory <= 0:
        return None  # nothing bound, or every store is whole (or empty)
    if not pending.ready:
        (logger.debug if said else logger.warning)(
            "Embedding re-index needed (%d knowledge item(s), %d passage vector(s), %d memory "
            "vector(s)), but the bound embedding model (%s) isn't ready: they stay "
            "keyword-searchable until it is, and the re-index runs once it is.",
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
        memory_store=_global_provider(state),
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

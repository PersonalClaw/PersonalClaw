"""Embedding re-index jobs with live progress.

Switching the active embedding model invalidates every stored vector — they came
from a different model and live in a different space (often a different
dimension). This module re-indexes both embedding stores as a background job
with SSE progress, mirroring :mod:`personalclaw.dashboard.model_downloads`:

  * **Knowledge** — ``KnowledgeStore`` items (clear ``embedding`` → re-embed each
    from preserved title/summary/content).
  * **Episodic memory** — ``VectorMemoryStore`` episodic rows (clear → re-embed
    from preserved text → rebuild FAISS). Semantic memory embeds lazily at query
    time, so clearing is enough there.

A single job at a time (re-indexing twice concurrently would race the stores);
``start`` returns the running job if one is already in flight. Progress frames
publish on the per-job SSE hub keyed ``reindex:<id>``.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from personalclaw.dashboard.sse import SseRegistry

logger = logging.getLogger(__name__)


def registry_key(job_id: str) -> str:
    """The SSE hub key for a re-index job's progress stream."""
    return f"reindex:{job_id}"


@dataclass
class ReindexJob:
    """One embedding re-index — identity, lifecycle, and progress.

    ``status`` is the coarse lifecycle (``running`` → ``done`` / ``error``);
    ``phase`` is the human-facing step. ``done``/``total`` count items processed
    across both stores so the UI can show a determinate bar.
    """

    id: str
    model: str
    status: str = "running"  # running | done | error
    phase: str = "queued"
    done: int = 0
    total: int = 0
    knowledge: int = 0
    memory: int = 0
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "model": self.model,
            "status": self.status,
            "phase": self.phase,
            "done": self.done,
            "total": self.total,
            "knowledge": self.knowledge,
            "memory": self.memory,
            "error": self.error,
        }


@dataclass
class _Running:
    job: ReindexJob
    task: asyncio.Task | None = None  # type: ignore[type-arg]


class ReindexRegistry:
    """Owns embedding re-index jobs + their per-job SSE progress streams.

    At most one job runs at a time. Finished jobs are retained so a re-attaching
    client (page reload) sees the terminal state.
    """

    def __init__(self) -> None:
        self._jobs: dict[str, ReindexJob] = {}
        self._running: dict[str, _Running] = {}
        self._sse = SseRegistry()
        self._counter = 0

    @property
    def sse(self) -> SseRegistry:
        return self._sse

    def _next_id(self) -> str:
        self._counter += 1
        return f"reindex-{self._counter}"

    def get(self, job_id: str) -> ReindexJob | None:
        return self._jobs.get(job_id)

    def list(self) -> list[ReindexJob]:
        return list(self._jobs.values())

    def active(self) -> ReindexJob | None:
        """The currently-running job, if any."""
        for run in self._running.values():
            if run.job.status == "running":
                return run.job
        return None

    def start(
        self, model: str, knowledge_store: Any, vector_store: Any, embedder: Any, embed_fn: Any
    ) -> tuple[ReindexJob | None, str | None]:
        """Begin a re-index (or return the in-flight one).

        ``embedder`` (knowledge, exposes ``embed_for_item``) and ``embed_fn``
        (memory, ``str -> list[float] | None``) must already be resolved from the
        NEW active model — the caller gates on availability before calling here.
        """
        running = self.active()
        if running is not None:
            return running, None

        job = ReindexJob(id=self._next_id(), model=model)
        self._jobs[job.id] = job
        run = _Running(job=job)
        self._running[job.id] = run
        run.task = asyncio.ensure_future(
            self._drive(run, knowledge_store, vector_store, embedder, embed_fn)
        )
        return job, None

    def _publish(self, job: ReindexJob, event: str) -> None:
        self._sse.publish(registry_key(job.id), event, job.to_dict())

    async def _drive(
        self, run: _Running, knowledge_store: Any, vector_store: Any, embedder: Any, embed_fn: Any
    ) -> None:
        job = run.job
        try:
            # Tally total work up front for a determinate bar.
            k_total = knowledge_store.count_items_to_reembed() if knowledge_store else 0
            m_total = vector_store.count_episodic_to_reembed() if vector_store else 0
            job.total = k_total + m_total
            job.phase = "clearing"
            self._publish(job, "progress")

            # Run the blocking SQLite + embedding work off the event loop.
            await asyncio.to_thread(
                self._reindex_sync, run, knowledge_store, vector_store, embedder, embed_fn
            )

            job.status = "done"
            job.phase = "done"
            self._publish(job, "done")
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — surface any failure to the UI
            logger.warning("Embedding re-index failed: %s", exc, exc_info=True)
            job.status = "error"
            job.phase = "error"
            job.error = str(exc)[:300]
            self._publish(job, "error")
        finally:
            self._running.pop(job.id, None)

    def _reindex_sync(
        self, run: _Running, knowledge_store: Any, vector_store: Any, embedder: Any, embed_fn: Any
    ) -> None:
        """Blocking re-index of both stores. Publishes throttled progress frames."""
        job = run.job

        # Throttle SSE frames: publish at most every ~25 items (and on phase change).
        def _progress(done_in_phase: int, base: int) -> None:
            job.done = base + done_in_phase
            if job.done % 25 == 0:
                self._publish(job, "progress")

        # ── Knowledge ──
        k_done = 0
        if knowledge_store and embedder is not None:
            job.phase = "reindexing knowledge"
            self._publish(job, "progress")
            knowledge_store.clear_embeddings()
            res = knowledge_store.reembed_all(embedder, on_progress=lambda d, _t: _progress(d, 0))
            job.knowledge = res.get("reembedded", 0)
            k_done = res.get("total", 0)

        # ── Episodic memory ── (continue the bar after the knowledge items)
        if vector_store is not None:
            job.phase = "reindexing memory"
            self._publish(job, "progress")
            vector_store.embed_fn = embed_fn
            vector_store.clear_embeddings()
            res = vector_store.reembed_all(on_progress=lambda d, _t: _progress(d, k_done))
            job.memory = res.get("reembedded", 0)


#: The graph-maintenance registry name for the chunk backfill. Owned here, beside the pass
#: itself, so a second host registers by calling ``register_chunk_backfill_pass`` rather than
#: re-typing the string — two hosts with two spellings would be two passes, not one.
CHUNK_BACKFILL_PASS = "chunk_backfill"


def chunk_backfill_pass(*, batch_size: int) -> int:
    """One bounded batch of the knowledge CHUNK backfill (KL-12). Returns units processed.

    Sibling of the item-vector re-index above, and deliberately separate from it: the
    re-index rewrites the items' OWN vectors on a model switch, while this only adds the
    chunk layer beneath them for items that predate chunking. Both are needed and neither
    substitutes for the other.

    This is a graph-maintenance pass, not a boot hook (KL-14). The backfill used to run from
    ``app.on_startup``, which fires exactly once: on a gateway that stays up for a week, a
    library that gains pre-chunking items after boot never gained deep-document recall. The
    host (:mod:`personalclaw.knowledge.maintenance`) instead calls this on every due tick and
    keeps claiming sub-batches until it returns 0, so the backlog drains across ticks and the
    store lock is released between batches.

    Cheap when there is nothing to do: the backlog is derived from the rows (see
    ``store.count_items_missing_chunks``), so a fully-chunked library costs one COUNT and
    returns 0 — checked BEFORE resolving an embedder, because resolving one probes the
    provider over the network and a no-op pass must not pay for that every tick.

    Faults propagate: the host isolates a failing pass (``fatal=False``) and records it in
    ``MaintenanceResult.errors``, which is strictly more legible than swallowing here. Only
    the deferred cases (empty backlog, no embedding model bound) return 0.
    """
    from personalclaw.knowledge import get_knowledge_store

    # The process-wide store at the canonical path — the same DB file the dashboard state
    # opens. A maintenance pass runs from a tick, which has no aiohttp app to read.
    store = get_knowledge_store()
    if store.count_items_missing_chunks() <= 0:
        return 0

    from personalclaw.dashboard.handlers.embedding_reindex import _resolve_embed
    from personalclaw.knowledge.chunk_backfill import BATCH_SIZE as _FETCH_BATCH
    from personalclaw.knowledge.chunk_backfill import backfill_item_chunks

    # _resolve_embed reads the process-wide embedding registry + config; its `app` argument
    # is unused, so a tick-time caller passes None rather than inventing an app.
    embedder, _embed_fn, _model = _resolve_embed(None)
    if embedder is None:
        logger.info(
            "Knowledge chunk backfill pending: item(s) need chunking but no embedding "
            "model is ready — deep-document recall resumes once one is bound."
        )
        return 0

    # Two bounds, both real: `max_items` is the host's claim for this call (what makes one
    # call one batch, so the host's loop — not this function — decides when to stop), while
    # `batch_size` keeps the per-fetch peak memory at the backfill module's own documented
    # ceiling, since item content is unbounded and the host's claim may be larger.
    result = backfill_item_chunks(
        store,
        embedder,
        batch_size=min(int(batch_size), _FETCH_BATCH),
        max_items=int(batch_size),
    )
    return int(result.get("done", 0))


def register_chunk_backfill_pass() -> None:
    """Register the chunk backfill with the graph-maintenance host.

    Registration is boot-time and free (it stores a callable); the WORK is what moved to the
    tick. Idempotent — the registry is keyed by name, so registering twice replaces rather
    than appends.
    """
    from personalclaw.knowledge import maintenance

    maintenance.register_pass(CHUNK_BACKFILL_PASS, chunk_backfill_pass)

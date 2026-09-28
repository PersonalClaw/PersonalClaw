"""Embedding re-index jobs with live progress.

Switching the active embedding model invalidates every stored vector — they came
from a different model and live in a different space (often a different
dimension). This module re-indexes both embedding stores as a background job
with SSE progress, mirroring :mod:`personalclaw.dashboard.model_downloads`:

  * **Knowledge items** — the ``KnowledgeStore`` items the new model has not embedded (each
    item records the model that wrote its vector, as a chunk does): their vectors are cleared,
    then re-embedded from the preserved title/summary/content. The items it has embedded are
    left alone; the job used to clear and re-embed every item on every run.
  * **Knowledge passages** — the ``chunks`` layer, re-embedded in place and
    re-stamped with the new model's fingerprint. This half used to be
    missing entirely: the item pass never touched ``chunks``, so after a
    same-dimension model swap every passage vector stayed in the previous model's
    space where nothing could detect it. The job now reports the integer count it
    re-embedded and refuses to report ``done`` while any chunk is still on the old
    model.
  * **Memory** — EVERY ``VectorMemoryStore`` in the home: the main ``memory.db`` and each
    working directory's store, open or not (``context.every_memory_vector_store``). Each
    re-embeds, from its preserved text, the memories the new model has not embedded, and
    records the model on every vector it writes (``VectorMemoryStore.reembed_stale``). The main
    store used to be the only one — cleared and redone — so a directory's memory kept the old
    model's vectors after every rebind. Until a vector is re-embedded it is stale: never compared
    with the new model's, and read by keyword.

A single job at a time (re-indexing twice concurrently would race the stores);
``start`` returns the running job if one is already in flight. Progress frames
publish on the per-job SSE hub keyed ``reindex:<id>``.

Every change of the embedding model reaches the job by ONE path,
``handlers.embedding_reindex.reindex_for_binding``, and the registry remembers what that path last
settled (:meth:`ReindexRegistry.settle`, :meth:`ReindexRegistry.check_again`), so the gateway's
watch on the binding knows when to take it again: a binding that changed, and a model that had
work waiting and could not yet do it.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from personalclaw.dashboard.sse import SseRegistry

logger = logging.getLogger(__name__)


def registry_key(job_id: str) -> str:
    """The SSE hub key for a re-index job's progress stream."""
    return f"reindex:{job_id}"


#: How soon a model with work waiting is looked at again after it could not do it — not ready,
#: or another model's re-index still running — and the longest that wait grows to. A local model
#: that finishes downloading, or a provider that comes back, is found within these, and a model
#: that stays unreachable is probed a few times an hour rather than on every pass.
RECHECK_FIRST_SECS = 30.0
RECHECK_MAX_SECS = 600.0


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
    #: Chunk vectors this job actually RE-EMBEDDED, counted from the rows it wrote.
    #: Not a "chunks were handled" flag: it is the integer a user can compare against
    #: ``chunks_stale``, which is read back from the table after the pass.
    chunks: int = 0
    #: Chunk vectors STILL carrying a previous model's fingerprint when the job finished.
    #: Non-zero forces ``status='error'`` — a re-index that reports success while part of
    #: the passage layer is still on the old model is the write-reported-success-and-did-
    #: not-land failure this exists to prevent.
    chunks_stale: int = 0
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
            "chunks": self.chunks,
            "chunks_stale": self.chunks_stale,
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
        # What the one path last settled: the model it started a re-index for or found nothing
        # waiting for (`""` names none bound), and when to look again at one it could not.
        # `None` until the first check, so the gateway's start is always one.
        self._settled: str | None = None
        self._recheck_at = 0.0
        self._recheck_delay = RECHECK_FIRST_SECS
        #: The gateway's watch on the binding (`handlers.embedding_reindex`), stopped with it.
        self.watch: asyncio.Task | None = None  # type: ignore[type-arg]

    # ── what the one path last settled ──

    def check_due(self, ref: str, now: float) -> bool:
        """Whether the model bound now (*ref*, ``""`` for none) needs the one path taken: it is not
        the model last settled, or a look again that was asked for is due. Never while a re-index
        runs: what it leaves is what the next check counts."""
        if self.active() is not None:
            return False
        if ref != self._settled:
            return True
        return bool(self._recheck_at) and now >= self._recheck_at

    def waiting(self, ref: str) -> bool:
        """Whether *ref* is the model already waiting to be looked at again, whose wait the log
        has said."""
        return self._settled == ref and bool(self._recheck_at)

    def settle(self, ref: str) -> None:
        """*ref*'s re-index is started, or it had nothing waiting: nothing to do until the binding
        changes."""
        self._settled = ref
        self._recheck_at = 0.0
        self._recheck_delay = RECHECK_FIRST_SECS

    def check_again(self, ref: str, now: float) -> None:
        """*ref* has work waiting and could not do it now: look again after a wait that doubles
        each time the same model is still waiting, from :data:`RECHECK_FIRST_SECS` to
        :data:`RECHECK_MAX_SECS`."""
        if not self.waiting(ref):
            self._recheck_delay = RECHECK_FIRST_SECS
        self._settled = ref
        self._recheck_at = now + self._recheck_delay
        self._recheck_delay = min(self._recheck_delay * 2, RECHECK_MAX_SECS)

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
        self, model: str, knowledge_store: Any, memory_store: Any, embedder: Any
    ) -> tuple[ReindexJob | None, str | None]:
        """Begin a re-index (or return the in-flight one).

        ``embedder`` (knowledge, exposes ``embed_for_item``) must already be resolved from the
        NEW active model — the caller gates on availability before calling here. ``memory_store``
        is the main memory store; the job visits every other memory store in the home too, and
        each embeds with the model bound now on its own.
        """
        running = self.active()
        if running is not None:
            return running, None

        job = ReindexJob(id=self._next_id(), model=model)
        self._jobs[job.id] = job
        run = _Running(job=job)
        self._running[job.id] = run
        run.task = asyncio.ensure_future(self._drive(run, knowledge_store, memory_store, embedder))
        return job, None

    def _publish(self, job: ReindexJob, event: str) -> None:
        self._sse.publish(registry_key(job.id), event, job.to_dict())

    async def _drive(
        self, run: _Running, knowledge_store: Any, memory_store: Any, embedder: Any
    ) -> None:
        job = run.job
        try:
            # Run the blocking SQLite + embedding work off the event loop.
            await asyncio.to_thread(
                self._reindex_sync, run, knowledge_store, memory_store, embedder
            )

            if job.chunks_stale:
                # Refuse to report done while any chunk vector still wears the
                # previous model's fingerprint. The re-embed left those rows recoverable
                # (their text and old vector are intact), so the honest terminal state is a
                # named failure the user can retry — not a green job over a half-converted
                # passage layer that semantic search will keep skipping.
                job.status = "error"
                job.phase = "incomplete"
                job.error = (
                    f"{job.chunks_stale} chunk vector(s) are still on the previous "
                    f"embedding model ({job.chunks} re-embedded). Semantic search skips "
                    "them until they are rebuilt — check the embedding provider's health "
                    "in Doctor and run the re-index again."
                )
                self._publish(job, "error")
                return

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
        self, run: _Running, knowledge_store: Any, memory_store: Any, embedder: Any
    ) -> None:
        """Blocking re-index of knowledge and every memory store. Publishes throttled progress."""
        from personalclaw.context import every_memory_vector_store

        with every_memory_vector_store(memory_store) as memory_stores:
            self._reindex_stores(run, knowledge_store, memory_stores, embedder)

    def _reindex_stores(
        self, run: _Running, knowledge_store: Any, memory_stores: Sequence[Any], embedder: Any
    ) -> None:
        job = run.job
        # The knowledge phases need the embedder, so only with one is their work counted.
        if embedder is None:
            knowledge_store = None
        # The model's width, so the job re-embeds exactly the items the gateway's start counts
        # (``count_items_to_reembed(active_dim)``): the model's own vector at a width it no longer
        # writes too. Counted and cleared without it, such an item was counted at every start, and
        # every start's job left it as it was.
        _dim = getattr(embedder, "dim", None) if knowledge_store else None
        active_dim = _dim() if callable(_dim) else None
        # Tally total work up front for a determinate bar. The passage vectors count too: there
        # are several per item, so their phase is the longest, and a bar that counted the items
        # alone stood still through it.
        k_total = knowledge_store.count_items_to_reembed(active_dim) if knowledge_store else 0
        p_total = knowledge_store.count_stale_chunk_vectors() if knowledge_store else 0
        job.total = k_total + p_total + sum(_to_reembed(store) for store in memory_stores)
        job.phase = "counting"
        self._publish(job, "progress")

        # Throttle SSE frames to one per ~5% of the work, and at least one per 25 items (and on
        # every phase change). A flat 25 left the bar of a store under 25 memories at 0/N until
        # the job ended, however slowly the model embedded them.
        step = max(1, min(25, job.total // 20))

        def _progress(done_in_phase: int, base: int) -> None:
            job.done = base + done_in_phase
            if job.done % step == 0:
                self._publish(job, "progress")

        # ── Knowledge ──
        k_done = 0
        if knowledge_store:
            job.phase = "reindexing knowledge"
            self._publish(job, "progress")
            # The items the model has not embedded, and no others: their vectors are cleared
            # first, so nothing compares one with this model's meanwhile.
            knowledge_store.clear_stale_embeddings(active_dim)
            res = knowledge_store.reembed_all(
                embedder, only_missing=True, on_progress=lambda d, _t: _progress(d, 0)
            )
            job.knowledge = res.get("reembedded", 0)
            k_done = res.get("total", 0)

            # ── Chunk vectors ──
            # `clear_stale_embeddings` + `reembed_all` above rewrite only the ITEM vectors. The
            # passage layer is where deep-document recall lives, and before this it survived
            # a model switch untouched: same dimension, same row count, so neither the
            # dimension guard nor the ANN index's row-count reconciliation could tell that
            # every chunk vector now belonged to another model's space.
            job.phase = "reindexing passages"
            self._publish(job, "progress")
            chunk_res = knowledge_store.reembed_stale_chunks(
                embedder, on_progress=lambda d, _t, b=k_done: _progress(d, b)
            )
            job.chunks = int(chunk_res.get("reembedded", 0))
            job.chunks_stale = int(chunk_res.get("stale_remaining", 0))
            k_done += int(chunk_res.get("total", 0))
            self._publish(job, "progress")

        # ── Memory ── every store, continuing the bar after the knowledge items
        if memory_stores:
            job.phase = "reindexing memory"
            self._publish(job, "progress")
        base = k_done
        for store in memory_stores:
            # One store that cannot be read (a directory's database locked or damaged) must not
            # cost every other store its re-embed; it stays stale, which its stats and the
            # Doctor both count.
            try:
                res = store.reembed_stale(on_progress=lambda d, _t, b=base: _progress(d, b))
            except Exception:  # noqa: BLE001 — logged with its store, then the next one runs
                logger.warning(
                    "Embedding re-index: could not re-embed the memory in %s",
                    store.db_path,
                    exc_info=True,
                )
                continue
            job.memory += res["reembedded"]
            base += res["total"]


def _to_reembed(store: Any) -> int:
    """``store.count_to_reembed()``, or 0 for a store that cannot be read (see the loop above)."""
    try:
        return int(store.count_to_reembed())
    except Exception:  # noqa: BLE001 — the re-embed pass names the store
        return 0


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

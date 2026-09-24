"""Knowledge ingestion queue (#30, Q4).

A single in-process async worker that drains items enqueued for node-graph ingestion
and runs each through ``pipeline.runner.ingest_item``. Both the native provider (on
create) and external providers (on sync) enqueue here, so there is ONE ingestion path.

Reuses the existing async-task + per-resource SSE substrate (transport doctrine) — no
new concurrency primitive. Progress for item ``X`` is published to the per-resource
feed ``knowledge:ingest:X`` via the gateway's SseRegistry (no-op when nobody watches).
"""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)


class KnowledgeIngestQueue:
    """Serialized async ingestion worker. ``start`` launches the drain loop;
    ``enqueue(item_id)`` adds work; items run one at a time (the store's sqlite
    connection is single-threaded, and ingestion is I/O-light in Task A)."""

    def __init__(
        self,
        store,
        *,
        embedder_factory=None,
        insights_pool=None,
        sse_registry=None,
        params_for=None,
    ):
        self._store = store
        self._embedder_factory = embedder_factory  # () -> embedder | None (lazy/per-run)
        self._insights_pool = insights_pool
        self._sse = sse_registry
        self._params_for = params_for
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._task: asyncio.Task | None = None
        self._seen: set[str] = set()

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._drain())
            logger.info("Knowledge ingest queue started")
            self.recover_pending()

    def recover_pending(self) -> int:
        """Re-enqueue items left mid-ingest by a previous process. The queue is
        in-memory, so a gateway restart strands any item still in ``queued`` or
        ``processing`` (it never reaches a terminal state). On startup, find those
        rows and re-enqueue them so their ingestion resumes. Returns the count."""
        try:
            rows = self._store.db.execute(
                "SELECT id FROM items WHERE processing_status IN ('queued', 'processing')"
            ).fetchall()
        except Exception:
            logger.debug("ingest queue recovery query failed", exc_info=True)
            return 0
        n = 0
        for r in rows:
            self.enqueue(r["id"])
            n += 1
        if n:
            logger.info("Knowledge ingest queue recovered %d pending item(s)", n)
        return n

    def stop(self) -> None:
        if self._task:
            self._task.cancel()
            self._task = None

    def enqueue(self, item_id: str) -> None:
        """Add an item to the ingestion queue (idempotent while pending)."""
        if item_id in self._seen:
            return
        self._seen.add(item_id)
        self._queue.put_nowait(item_id)

    def qsize(self) -> int:
        return self._queue.qsize()

    async def _drain(self) -> None:
        from personalclaw import shutdown_event

        while not shutdown_event.is_set():
            try:
                item_id = await asyncio.wait_for(self._queue.get(), timeout=2.0)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                return
            self._seen.discard(item_id)
            try:
                await self._process(item_id)
            except Exception:
                logger.exception("knowledge ingest failed for %s", item_id)
            finally:
                self._queue.task_done()

    async def _process(self, item_id: str) -> None:
        from personalclaw.knowledge.pipeline.runner import ingest_item, progress_feed

        feed = progress_feed(item_id)

        def _publish(event: str, data: dict) -> None:
            if self._sse is not None:
                self._sse.publish(feed, event, data)

        embedder = None
        if self._embedder_factory:
            try:
                embedder = self._embedder_factory()
            except Exception:
                embedder = None
        await ingest_item(
            self._store,
            item_id,
            embedder=embedder,
            insights_pool=self._insights_pool,
            params_for=self._params_for,
            publish=_publish,
        )

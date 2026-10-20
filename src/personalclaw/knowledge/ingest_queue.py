"""Knowledge ingestion queue.

A single in-process async worker that drains items enqueued for node-graph ingestion
and runs each through ``pipeline.runner.ingest_item``. Both the native provider (on
create) and external providers (on sync) enqueue here, so there is ONE ingestion path.

**Two lanes, one worker.** What a person added or changed herself — an upload, a note, an
edit, one item's Regenerate — waits in her lane, and is always taken before anything in
the background lane: a watched folder's first scan, a feed's new entries, the artifact
mirror, a whole-library regenerate, a recovery after a restart. With one line for both,
a folder of notes enriched on a local model for minutes each held her own memo behind it
for three hours. The lanes share the one worker rather than running side by side because
the ingest runner's terminal stages share the store's single sqlite connection, and two
items' transactions interleaved on it can drop each other's writes. So her item waits
for at most the one item already being read, and :meth:`KnowledgeIngestQueue.standing`
says exactly that, with how long recent items took.

Reuses the existing async-task + per-resource SSE substrate (transport doctrine) — no
new concurrency primitive. Progress for item ``X`` is published to the per-resource
feed ``knowledge:ingest:X`` via the gateway's SseRegistry (no-op when nobody watches).
"""

from __future__ import annotations

import asyncio
import logging
import statistics
import threading
import time
from collections import deque

logger = logging.getLogger(__name__)

#: The lanes :meth:`KnowledgeIngestQueue.standing` names.
LANE_YOURS = "yours"
LANE_BACKGROUND = "background"

#: How many finished items the typical duration is taken over, and how many it needs
#: before it says anything: two runs are an anecdote, not a typical time.
_DURATION_WINDOW = 20
_DURATION_MIN_SAMPLES = 3


class KnowledgeIngestQueue:
    """Serialized async ingestion worker. ``start`` launches the drain loop;
    :meth:`enqueue` adds a person's own work and :meth:`enqueue_background` everything
    else; items run one at a time, hers first (see the module docstring for why one)."""

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
        self._yours: deque[str] = deque()
        self._background: deque[str] = deque()
        # Lanes are touched from the loop AND from worker threads (the memory vault's sync
        # enqueues from `asyncio.to_thread`), so every read-modify-write holds this.
        self._lock = threading.Lock()
        self._wake: asyncio.Event | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._task: asyncio.Task | None = None
        # (item id, wall-clock start) of the item being read now, and how long the last few
        # took — what `standing` answers "how long will this take?" from.
        self._running: tuple[str, float] | None = None
        self._durations: deque[float] = deque(maxlen=_DURATION_WINDOW)

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._loop = asyncio.get_running_loop()
            self._wake = asyncio.Event()
            self._task = asyncio.create_task(self._drain())
            logger.info("Knowledge ingest queue started")
            self.recover_pending()

    def recover_pending(self) -> int:
        """Re-enqueue items left mid-ingest by a previous process. The queue is
        in-memory, so a gateway restart strands any item still in ``queued`` or
        ``processing`` (it never reaches a terminal state). On startup, find those
        rows and re-enqueue them so their ingestion resumes. Returns the count.

        Which lane each goes back to is read off the item, since the lane itself was only
        ever in memory: one that came from a watched source, or is an artifact's mirror, is
        background work; anything else is hers, so an upload a restart interrupted does not
        come back behind a whole folder."""
        from personalclaw.knowledge.artifact_ingest import ARTIFACT_ITEM_TYPE

        try:
            rows = self._store.db.execute(
                "SELECT id, COALESCE(source_id, '') AS source_id, "
                "COALESCE(item_type, '') AS item_type FROM items "
                "WHERE processing_status IN ('queued', 'processing') ORDER BY created_at, id"
            ).fetchall()
        except Exception:
            logger.debug("ingest queue recovery query failed", exc_info=True)
            return 0
        n = 0
        for r in rows:
            if r["source_id"] or r["item_type"] == ARTIFACT_ITEM_TYPE:
                self.enqueue_background(r["id"])
            else:
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
        """Queue something the person added or changed herself; it goes before every item
        in the background lane. An item already waiting in the background lane moves up to
        hers (she asked for it now). Idempotent while pending."""
        with self._lock:
            if item_id in self._yours:
                return
            if item_id in self._background:
                self._background.remove(item_id)
            self._yours.append(item_id)
        self._signal()

    def enqueue_background(self, item_id: str) -> None:
        """Queue background work: taken only when nothing of hers is waiting. Idempotent
        while pending in either lane."""
        with self._lock:
            if item_id in self._yours or item_id in self._background:
                return
            self._background.append(item_id)
        self._signal()

    def qsize(self) -> int:
        """Items waiting in both lanes (not the one being read)."""
        with self._lock:
            return len(self._yours) + len(self._background)

    def standing(self, item_id: str) -> dict | None:
        """Where *item_id* stands, for a page that says how long it will be; ``None`` when
        this queue does not hold it.

        ``{"state": "running", "since": <epoch>, "typical_secs": …}`` for the item being
        read now, else ``{"state": "waiting", "lane": "yours" | "background", "ahead": n,
        "running_since": <epoch> | None, "typical_secs": …}``. ``ahead`` counts the items
        that will be read before it and not the one in progress, which ``running_since``
        describes. ``typical_secs`` is the median of the last few items' times, or ``None``
        until enough have finished to say — a measured fact about the past, never a
        promise about this item."""
        with self._lock:
            running = self._running
            typical = self._typical_secs()
            if running is not None and running[0] == item_id:
                return {"state": "running", "since": running[1], "typical_secs": typical}
            if item_id in self._yours:
                lane, ahead = LANE_YOURS, self._yours.index(item_id)
            elif item_id in self._background:
                lane = LANE_BACKGROUND
                ahead = len(self._yours) + self._background.index(item_id)
            else:
                return None
        return {
            "state": "waiting",
            "lane": lane,
            "ahead": ahead,
            "running_since": running[1] if running is not None else None,
            "typical_secs": typical,
        }

    def _typical_secs(self) -> float | None:
        if len(self._durations) < _DURATION_MIN_SAMPLES:
            return None
        return round(statistics.median(self._durations), 1)

    def _signal(self) -> None:
        """Wake the drain loop, from the loop's own thread or from another one."""
        loop, wake = self._loop, self._wake
        if loop is None or wake is None or loop.is_closed():
            return  # not started: the drain finds the item when it starts
        try:
            on_loop = asyncio.get_running_loop() is loop
        except RuntimeError:
            on_loop = False
        if on_loop:
            wake.set()
        else:
            loop.call_soon_threadsafe(wake.set)

    def _take(self) -> str | None:
        with self._lock:
            if self._yours:
                return self._yours.popleft()
            if self._background:
                return self._background.popleft()
            return None

    async def _drain(self) -> None:
        from personalclaw import shutdown_event

        wake = self._wake
        assert wake is not None
        while not shutdown_event.is_set():
            # Cleared BEFORE the lanes are read, so an enqueue that lands after the read has
            # already set it again and the wait below returns at once.
            wake.clear()
            item_id = self._take()
            if item_id is None:
                try:
                    # The timeout is a safety net only: every enqueue wakes the loop.
                    await asyncio.wait_for(wake.wait(), timeout=2.0)
                except asyncio.TimeoutError:
                    continue
                except asyncio.CancelledError:
                    return
                continue
            started = time.monotonic()
            with self._lock:
                self._running = (item_id, time.time())
            try:
                await self._process(item_id)
            except asyncio.CancelledError:
                return
            except Exception:
                logger.exception("knowledge ingest failed for %s", item_id)
            finally:
                with self._lock:
                    self._running = None
                    self._durations.append(time.monotonic() - started)

    async def _process(self, item_id: str) -> None:
        from personalclaw.knowledge.pipeline.runner import ingest_item, progress_feed

        feed = progress_feed(item_id)

        def _publish(event: str, data: dict) -> None:
            if self._sse is not None:
                self._sse.publish(feed, event, data)

        embedder = None
        if self._embedder_factory:
            # In a worker thread: building the embedder asks a bound model for a vector to learn
            # its width, and that answer is waited for in the calling thread, which on the event
            # loop stopped every request until it came.
            try:
                embedder = await asyncio.to_thread(self._embedder_factory)
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

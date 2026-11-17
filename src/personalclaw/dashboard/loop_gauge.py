"""How long the gateway keeps work waiting: its event loop's lag and its worker pool's wait.

A reload of Needs you on production v0.3.9 had reads that answer in 0.2 s alone answer in 1.7 to
9.6 s together, while healthz, read every half second, stayed at 3 ms. Two causes fit that, and
nothing measured which: the loop held by work that grows with the owner's data, or the default
worker pool full, so every read that awaits ``asyncio.to_thread`` waits for a worker first. This
gauge names it, cheaply enough to run always:

- **The loop's lag.** A tick every :data:`TICK` seconds sleeps that long and measures how much
  later than asked it woke. A loop busy with someone's synchronous work wakes late by that work.
- **The pool's wait.** A no-op handed to the loop's default executor every :data:`TICK` seconds,
  timed from handing it over to its start. A pool with every worker busy starts it late by as long
  as the work ahead of it holds a worker. The queue's length is read beside it.

Each sample is kept for :data:`WINDOW` seconds, and each minute's worst for :data:`MINUTES`
minutes, so ``GET /api/resilience/loop`` answers what the last minute and the last ten looked
like, never a log. The gauge starts with the gateway and stops with it
(:func:`register_loop_gauge`).

A gauge that stopped sampling must never read as a calm one: its window empties and every figure
falls to 0 ms. So the report says whether it is sampling (``sampling``) and how long ago each
measure's last sample was taken (``last_sample_s_ago``), and Diagnostics says so instead of
showing figures. One failed tick is logged and the next one runs; a start on a new loop (a
restarted server's, a test's) starts again even if an old loop's tasks were never stopped.

The pool's size and queue are read from the default executor's private fields, read-only and
guarded. A loop with no such executor (uvloop's, or one that has made none yet) reports the
standard library's default size and an empty queue.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from aiohttp import web

from personalclaw.cancellation import cancel_and_wait

logger = logging.getLogger(__name__)

#: Seconds between samples, of the loop's lag and of the pool's wait alike.
TICK = 0.5
#: Seconds each sample is kept for the last minute's figures.
WINDOW = 60.0
#: Minutes each minute's worst is kept for.
MINUTES = 10
#: Ticks a measure may miss before the gauge no longer counts as sampling.
MISSED_TICKS = 4


def _ms(seconds: float) -> float:
    return round(seconds * 1000, 1)


def _pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(p * (len(ordered) - 1))))]


@dataclass
class _Series:
    """One measure's samples for the last :data:`WINDOW` seconds and each minute's worst."""

    samples: deque[tuple[float, float]] = field(default_factory=deque)
    minutes: deque[tuple[int, float]] = field(default_factory=deque)

    def add(self, at: float, value: float) -> None:
        self.samples.append((at, value))
        while self.samples and self.samples[0][0] < at - WINDOW:
            self.samples.popleft()
        minute = int(at // 60)
        if self.minutes and self.minutes[-1][0] == minute:
            if value > self.minutes[-1][1]:
                self.minutes[-1] = (minute, value)
        else:
            self.minutes.append((minute, value))
        while self.minutes and self.minutes[0][0] <= minute - MINUTES:
            self.minutes.popleft()

    def last_sample_s_ago(self) -> float | None:
        """Seconds since the last sample, or ``None`` before the first."""
        if not self.samples:
            return None
        return round(max(0.0, time.monotonic() - self.samples[-1][0]), 1)

    def report(self) -> dict[str, float | None]:
        values = [v for _, v in self.samples]
        return {
            "last_sample_s_ago": self.last_sample_s_ago(),
            "now_ms": _ms(values[-1]) if values else 0.0,
            "p50_ms": _ms(_pct(values, 0.5)),
            "p95_ms": _ms(_pct(values, 0.95)),
            "max_1m_ms": _ms(max(values, default=0.0)),
            "max_10m_ms": _ms(max((v for _, v in self.minutes), default=0.0)),
        }

    def counts(self) -> dict[str, int]:
        values = [v for _, v in self.samples]
        return {
            "now": int(values[-1]) if values else 0,
            "max_1m": int(max(values, default=0.0)),
            "max_10m": int(max((v for _, v in self.minutes), default=0.0)),
        }


def _pool_of(loop: asyncio.AbstractEventLoop) -> ThreadPoolExecutor | None:
    """The loop's default executor, once it has one (it is made on the first use)."""
    pool = getattr(loop, "_default_executor", None)
    return pool if isinstance(pool, ThreadPoolExecutor) else None


def _queued(pool: ThreadPoolExecutor | None) -> int:
    """Work handed to *pool* that no worker has started yet."""
    try:
        return pool._work_queue.qsize() if pool is not None else 0
    except Exception:  # noqa: BLE001 - a private queue another Python may not have
        return 0


class LoopGauge:
    """The two measures, their windows, and the tasks that take them."""

    def __init__(self) -> None:
        self.lag = _Series()
        self.pool_wait = _Series()
        self.queued = _Series()
        self.started_at: float | None = None
        self._tasks: list[asyncio.Task[None]] = []
        #: The tick whose last run failed, so a run of failures is logged once as a warning.
        self._failing: set[str] = set()
        #: When the pool probe in flight was handed over, while it waits for a worker.
        self._pool_handed: float | None = None

    def _failed(self, tick: str) -> None:
        """Log a failed tick: a warning when a run of failures starts, then quietly."""
        if tick in self._failing:
            logger.debug("the loop gauge's %s tick failed again", tick, exc_info=True)
        else:
            self._failing.add(tick)
            logger.warning("the loop gauge's %s tick failed; it goes on", tick, exc_info=True)

    async def _tick_lag(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            asked = loop.time()
            await asyncio.sleep(TICK)
            try:
                self.lag.add(time.monotonic(), max(0.0, loop.time() - asked - TICK))
                self._failing.discard("lag")
            except Exception:  # noqa: BLE001 - one failed sample must not end the series
                self._failed("lag")

    async def _tick_pool(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            await asyncio.sleep(TICK)
            try:
                handed = time.monotonic()
                queued = _queued(_pool_of(loop))
                self._pool_handed = handed
                try:
                    started = await loop.run_in_executor(None, time.monotonic)
                finally:
                    self._pool_handed = None
                now = time.monotonic()
                self.pool_wait.add(now, max(0.0, started - handed))
                self.queued.add(now, float(queued))
                self._failing.discard("pool")
            except Exception:  # noqa: BLE001 - a shut-down executor: the next tick tries again
                self._failed("pool")

    def _live_here(self) -> bool:
        """A tick is running on the running loop (not a closed loop's, not one that ended)."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return False
        return any(not t.done() and t.get_loop() is loop for t in self._tasks)

    def sampling(self) -> bool:
        """Both ticks run here, and each measure's last sample is under :data:`MISSED_TICKS`
        ticks old. A pool probe still waiting for a worker is a live reading: a full pool is
        exactly what the gauge is there to show, and it takes no sample until a worker frees."""
        if not self._live_here():
            return False
        fresh = MISSED_TICKS * TICK
        lag = self.lag.last_sample_s_ago()
        pool = self.pool_wait.last_sample_s_ago()
        return (lag is not None and lag < fresh) and (
            self._pool_handed is not None or (pool is not None and pool < fresh)
        )

    def _pool_report(self) -> dict[str, float | None]:
        """The pool's figures, with a probe still waiting counted as it stands: its wait so far
        is the wait now, and the worst of the minute and the ten is at least that."""
        report = self.pool_wait.report()
        handed = self._pool_handed
        if handed is not None:
            waiting = _ms(max(0.0, time.monotonic() - handed))
            for key in ("now_ms", "max_1m_ms", "max_10m_ms"):
                report[key] = max(report[key] or 0.0, waiting)
        return report

    def start(self) -> None:
        if self._live_here():
            return
        self.started_at = time.monotonic()
        self._tasks = [
            asyncio.ensure_future(self._tick_lag()),
            asyncio.ensure_future(self._tick_pool()),
        ]

    async def stop(self) -> None:
        tasks, self._tasks = self._tasks, []
        # Only this loop's ticks can be cancelled and awaited here; a closed loop's ended with it.
        loop = asyncio.get_running_loop()
        here = [t for t in tasks if not t.done() and t.get_loop() is loop]
        if here:
            await cancel_and_wait(here, what="the loop gauge", grace=5)

    def report(self) -> dict[str, object]:
        """What the last minute and the last ten looked like, in milliseconds and counts."""
        pool = None
        try:
            pool = _pool_of(asyncio.get_running_loop())
        except RuntimeError:
            pass
        queued = self.queued.counts()
        return {
            "sampling": self.sampling(),
            "sampled_every_ms": _ms(TICK),
            "running_for_s": (
                round(time.monotonic() - self.started_at) if self.started_at is not None else 0
            ),
            "loop_lag": self.lag.report(),
            "pool_wait": self._pool_report(),
            "pool": {
                "workers": (
                    pool._max_workers if pool is not None else min(32, (os.cpu_count() or 1) + 4)
                ),
                "started": len(getattr(pool, "_threads", ())) if pool is not None else 0,
                "queued_now": queued["now"],
                "queued_max_1m": queued["max_1m"],
                "queued_max_10m": queued["max_10m"],
            },
            "cpu_count": os.cpu_count() or 0,
        }


#: The gateway's gauge: one per process.
GAUGE = LoopGauge()


async def api_loop_gauge(request: web.Request) -> web.Response:
    """How long the gateway kept work waiting, the last minute and the last ten."""
    return web.json_response(GAUGE.report())


async def _start(app: web.Application) -> None:
    GAUGE.start()


async def _stop(app: web.Application) -> None:
    await GAUGE.stop()


def register_loop_gauge(app: web.Application) -> None:
    """``GET /api/resilience/loop``, and the gauge's start and stop with the gateway."""
    app.router.add_get("/api/resilience/loop", api_loop_gauge)
    app.on_startup.append(_start)
    app.on_cleanup.append(_stop)

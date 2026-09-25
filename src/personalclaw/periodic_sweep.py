"""A named daemon thread that runs one sweep every N seconds — and can be STOPPED.

The gateway's three watchdogs (app backends, app workers, model sidecars) were each a bare
``while True: time.sleep(30); sweep()`` daemon thread with no way to end it. A process that boots
one gateway and exits never notices. Anything that stops a gateway and carries on does:
``runner.cleanup()`` terminates every app backend (``dashboard/server.py``'s
``_app_backends_shutdown``) and left the backend watchdog running to revive them 30 s later, and
every boot added three more sweepers that never ended.

The test suite is the process that carries on. It boots a real gateway in 25 tests; measured with
the real-home guard (``tests/real_home_guard.py``), the watchdogs those boots left behind made
18,761 reads of the developer's real ``~/.personalclaw`` — the installed ``apps/`` tree, and
``config.json`` through ``AppConfig.load()``, which migrates and writes back when it needs to —
between and during unrelated tests, long after the test that started them had undone its home
isolation.

So the lifetime is owned here: :meth:`PeriodicSweep.start` is idempotent (a second sweeper would
race the first to revive the same crashed backend), and :meth:`PeriodicSweep.stop` ends the
thread and waits for an in-flight sweep — bounded, so a stuck sweep cannot hang a shutdown.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

logger = logging.getLogger(__name__)


class PeriodicSweep:
    """Run ``sweep`` every ``interval`` seconds on one daemon thread named ``name``.

    The first sweep runs one interval AFTER :meth:`start`, as the watchdogs always have: boot has
    just launched what they supervise. A sweep that raises is logged at debug and the thread
    carries on — one bad pass must not end supervision.
    """

    def __init__(self, name: str, interval: float, sweep: Callable[[], None]) -> None:
        self.name = name
        self.interval = interval
        self._sweep = sweep
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop: threading.Event | None = None

    def start(self) -> threading.Thread:
        """Start the sweeper, or return the one already running."""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return self._thread
            stop = threading.Event()
            thread = threading.Thread(target=self._run, args=(stop,), name=self.name, daemon=True)
            self._thread, self._stop = thread, stop
            thread.start()
        logger.info("%s started (interval=%ss)", self.name, self.interval)
        return thread

    def stop(self, timeout: float = 5.0) -> bool:
        """Stop the sweeper and wait up to ``timeout`` for an in-flight sweep to finish.

        Returns whether no sweeper is left running. Idempotent; a no-op when none was started.
        """
        with self._lock:
            thread, stop = self._thread, self._stop
            self._thread = self._stop = None
        if thread is None or stop is None:
            return True
        stop.set()
        if thread is not threading.current_thread():
            thread.join(timeout)
        return not thread.is_alive()

    def running(self) -> bool:
        with self._lock:
            return self._thread is not None and self._thread.is_alive()

    def _run(self, stop: threading.Event) -> None:
        while not stop.wait(self.interval):
            try:
                self._sweep()
            except Exception:  # noqa: BLE001 — one bad sweep must not end the watchdog
                logger.debug("%s sweep failed", self.name, exc_info=True)

"""Adaptive-debounce committer for time-travel history (§5).

Every successful atomic write announces itself through
:func:`personalclaw.atomic_write.register_post_write_hook`. Committing on each
announcement would put a git process in the write path of every JSON store, so
writes are coalesced instead:

* a write schedules a commit **10 seconds** out;
* sustained writing tightens that toward **0** — a burst of edits commits almost
  immediately once it is clear the user (or the agent) is mid-flow, so the
  history stays useful rather than lagging a fixed ten seconds behind reality;
* work is **serialized per root**: a root already committing does not get a
  second concurrent git process, the pending mark is simply left armed for the
  next pass.

The scheduler owns no time of its own. It takes a clock and is driven by
:meth:`HistoryDebouncer.run_pending`, which a one-line background thread calls on
a short cadence in production and a test calls with a fake clock. That is
deliberate: a debouncer that could only be observed by sleeping could only be
tested by measuring a skeleton.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from personalclaw.durability import state_history as sh

logger = logging.getLogger(__name__)

#: First write after quiet schedules a commit this far out.
BASE_DELAY_SECS = 10.0

#: Each further write inside the burst window halves the remaining wait.
DECAY = 0.5

#: How long a write keeps counting as "sustained activity".
BURST_WINDOW_SECS = 60.0

#: At or above this many writes in the window the delay collapses to zero — the
#: literal "10s→0" end of the ramp, rather than an asymptote that never arrives.
SUSTAINED_WRITES = 8

#: Background driver cadence. Short enough that a zero delay means "now".
TICK_SECS = 0.5


def delay_for_writes(writes: int) -> float:
    """The debounce delay for the *n*-th write of a burst. Pure, so it is testable."""
    if writes >= SUSTAINED_WRITES:
        return 0.0
    return BASE_DELAY_SECS * (DECAY ** max(0, writes - 1))


@dataclass
class _Pending:
    root_id: str
    due_at: float
    first_at: float
    writes: int = 0
    surfaces: set[str] = field(default_factory=set)
    paths: int = 0


class HistoryDebouncer:
    """Coalesces post-write notifications into per-root history commits."""

    def __init__(
        self,
        *,
        home: Path | None = None,
        clock: Callable[[], float] = time.monotonic,
        committer: Callable[..., str | None] | None = None,
    ) -> None:
        self._home = home
        self._clock = clock
        self._committer = committer or sh.commit
        self._state = threading.Lock()
        self._pending: dict[str, _Pending] = {}
        self._root_locks: dict[str, threading.Lock] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        #: Observability counters — the panel's "is history keeping up?" answer and
        #: what a serialization rail asserts against.
        self.commits = 0
        self.skips_busy = 0
        self.notifications = 0

    # ── notification intake ────────────────────────────────────────────────

    def notify(self, path: Path | str) -> bool:
        """Record a write. Returns whether it belongs to a tracked root.

        Cheap and non-blocking: this runs inside the caller's write path, so it
        does no git work and never raises.
        """
        try:
            if sh.is_history_path(path, home=self._home):
                return False
            root = sh.root_for_path(path, home=self._home)
        except Exception:  # noqa: BLE001 — never break a write
            logger.debug("time-travel: could not classify %s", path, exc_info=True)
            return False
        if root is None:
            return False
        surface = sh.current_surface()
        now = self._clock()
        with self._state:
            self.notifications += 1
            entry = self._pending.get(root.id)
            if entry is None or now - entry.first_at > BURST_WINDOW_SECS:
                entry = _Pending(root_id=root.id, due_at=now, first_at=now)
                self._pending[root.id] = entry
            entry.writes += 1
            entry.paths += 1
            entry.surfaces.add(surface)
            entry.due_at = now + delay_for_writes(entry.writes)
        return True

    def pending_delay(self, root_id: str, *, now: float | None = None) -> float | None:
        """Seconds until *root_id* is due, or None when nothing is pending."""
        with self._state:
            entry = self._pending.get(root_id)
            if entry is None:
                return None
            return max(0.0, entry.due_at - (now if now is not None else self._clock()))

    def pending_roots(self) -> tuple[str, ...]:
        with self._state:
            return tuple(self._pending)

    # ── driving ────────────────────────────────────────────────────────────

    def run_pending(self, *, now: float | None = None, force: bool = False) -> list[dict]:
        """Commit every root whose debounce has elapsed. One pass, no sleeping."""
        stamp = now if now is not None else self._clock()
        due: list[_Pending] = []
        with self._state:
            for root_id, entry in list(self._pending.items()):
                if force or entry.due_at <= stamp:
                    due.append(entry)
                    del self._pending[root_id]
        results: list[dict] = []
        for entry in due:
            results.append(self._commit_root(entry))
        return results

    def flush(self) -> list[dict]:
        """Commit everything pending immediately (shutdown, or an explicit save)."""
        return self.run_pending(force=True)

    def _lock_for(self, root_id: str) -> threading.Lock:
        with self._state:
            lock = self._root_locks.get(root_id)
            if lock is None:
                lock = self._root_locks[root_id] = threading.Lock()
            return lock

    def _rearm(self, entry: _Pending) -> None:
        """Put a skipped root back on the queue without losing its burst state."""
        with self._state:
            existing = self._pending.get(entry.root_id)
            if existing is None:
                entry.due_at = self._clock()
                self._pending[entry.root_id] = entry
            else:
                existing.writes += entry.writes
                existing.paths += entry.paths
                existing.surfaces |= entry.surfaces

    def _commit_root(self, entry: _Pending) -> dict:
        root = sh.root_by_id(entry.root_id, home=self._home)
        if root is None:
            return {"root": entry.root_id, "ok": False, "error": "unknown root"}
        lock = self._lock_for(entry.root_id)
        # Non-blocking: a root already committing keeps its pending mark instead of
        # queueing a second git process behind the first. Depth-1 queue, no races.
        if not lock.acquire(blocking=False):
            self.skips_busy += 1
            self._rearm(entry)
            return {"root": entry.root_id, "ok": True, "skipped": "busy"}
        try:
            # The surface a commit is attributed to is the one that DID the writes,
            # captured at notify time — the commit itself runs on the driver thread,
            # which knows nothing about who caused it.
            surface = _dominant_surface(entry.surfaces)
            sha = self._committer(root, surface=surface, home=self._home)
            if sha:
                self.commits += 1
            return {
                "root": entry.root_id,
                "ok": True,
                "sha": sha,
                "changed": bool(sha),
                "writes": entry.writes,
                "surface": surface,
            }
        except Exception as exc:  # noqa: BLE001 — history must never break the app
            logger.warning("time-travel: commit failed for %s: %s", entry.root_id, exc)
            return {"root": entry.root_id, "ok": False, "error": str(exc)}
        finally:
            lock.release()

    # ── background driver ──────────────────────────────────────────────────

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="personalclaw-history-debounce", daemon=True
        )
        self._thread.start()

    def stop(self, *, flush: bool = True) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=5)
        if flush:
            self.flush()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.run_pending()
            except Exception:  # noqa: BLE001
                logger.debug("time-travel: debounce pass failed", exc_info=True)
            self._stop.wait(TICK_SECS)


def _dominant_surface(surfaces: set[str]) -> str:
    """One surface for a coalesced commit.

    A burst that contains any unattended write is attributed to the unattended
    surface: the "what changed while I slept" filter must not lose a background
    edit because an interactive write landed in the same ten seconds.
    """
    if not surfaces:
        return sh.current_surface()
    for candidate in (sh.SURFACE_BACKGROUND, sh.SURFACE_SCHEDULED):
        if candidate in surfaces:
            return candidate
    return sorted(surfaces)[0]


# ── process-wide wiring ────────────────────────────────────────────────────

_installed: HistoryDebouncer | None = None
_install_lock = threading.Lock()


def active() -> HistoryDebouncer | None:
    return _installed


def install(*, home: Path | None = None, start: bool = True) -> HistoryDebouncer | None:
    """Subscribe the debouncer to the atomic-write seam. Idempotent.

    Returns None (and stays uninstalled) when git is unavailable — time-travel is
    a convenience built on a tool that may not be present, and a missing git must
    degrade to "no history", never to a broken write path.
    """
    global _installed
    from personalclaw.atomic_write import register_post_write_hook

    with _install_lock:
        if _installed is not None:
            return _installed
        if not sh.git_available():
            logger.info("time-travel: git not available — history disabled")
            return None
        debouncer = HistoryDebouncer(home=home)
        register_post_write_hook(debouncer.notify)
        if start:
            debouncer.start()
        _installed = debouncer
        return debouncer


def uninstall(*, flush: bool = True) -> None:
    """Unsubscribe and stop. Safe to call when nothing is installed."""
    global _installed
    from personalclaw.atomic_write import unregister_post_write_hook

    with _install_lock:
        debouncer, _installed = _installed, None
    if debouncer is None:
        return
    unregister_post_write_hook(debouncer.notify)
    debouncer.stop(flush=flush)

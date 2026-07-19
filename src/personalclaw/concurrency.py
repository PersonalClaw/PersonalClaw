"""Cross-process concurrency primitives — single-flight locks + orphan reaping.

Two small, vendor-neutral primitives used to make long-running background jobs
(history consolidation, autonomous goal loops, memory promotion) robust against
two failure modes that in-process guards alone can't cover:

- **Double-fire across processes.** PClaw runs several processes against one
  ``config_dir()`` — the gateway, the ``personalclaw consolidate`` CLI, the eval
  runner. An in-memory running-set guards one process; :func:`single_flight`
  adds an OS-level advisory lock so a given job-key runs in at most one process
  at a time.
- **Crash-zombie state.** A process that dies mid-job leaves persisted
  ``running`` rows that nothing will ever finish. :func:`reap_orphans` is the
  startup-sweep seam that lets each subsystem resolve its own stale rows before
  its supervisor's first poll.

Both are built on ``fcntl.flock``, the established PClaw locking primitive
(see ``schedule.py``, ``session_pid.py``, ``mcp_core.py``). flock is **released
automatically when the holding process dies**, which is exactly the property we
want: a crash can never leave the lock itself stuck (a DB lock row would).
"""

from __future__ import annotations

import fcntl
import hashlib
import logging
import re
from collections.abc import Awaitable, Callable, Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from personalclaw.config.loader import config_dir

logger = logging.getLogger(__name__)

_UNSAFE = re.compile(r"[^a-zA-Z0-9._-]")


def _locks_dir() -> Path:
    d = config_dir() / "locks"
    d.mkdir(parents=True, exist_ok=True)
    return d


def lock_path(job_key: str) -> Path:
    """The lock file backing ``job_key``.

    The filename is a sanitized, readable prefix plus a short digest of the full
    key, so two keys that sanitize to the same prefix never collide on one file.
    """
    safe = _UNSAFE.sub("_", job_key)[:48]
    digest = hashlib.sha256(job_key.encode("utf-8")).hexdigest()[:8]
    return _locks_dir() / f"{safe}.{digest}.lock"


@contextmanager
def single_flight(job_key: str) -> Iterator[bool]:
    """Cross-process single-flight guard for ``job_key``.

    Yields ``True`` if this process acquired the lock (the caller should do the
    work), or ``False`` if another live process already holds it (the caller
    should skip — single-flight means *don't double-run*, never *wait in line*).
    Acquisition is non-blocking. The lock is released on context exit and, if the
    process dies inside the block, by the OS — so it cannot zombie.

    Usage::

        with single_flight(f"consolidate:{key}") as acquired:
            if not acquired:
                return
            ...  # the guarded work
    """
    path = lock_path(job_key)
    acquired = False
    fd = path.open("w")
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except OSError:
            acquired = False  # already held by another process
        yield acquired
    finally:
        if acquired:
            fcntl.flock(fd, fcntl.LOCK_UN)
        fd.close()


async def reap_orphans(
    label: str,
    items: Iterable[Any],
    reap: Callable[[Any], Awaitable[None]],
) -> int:
    """Startup-sweep seam: resolve each stale ``running`` item via ``reap``.

    The generic half of the orphan-reaper. A subsystem supplies the items it
    considers orphaned (e.g. goal loops persisted as RUNNING whose in-memory
    worker vanished on restart) and an async ``reap`` that resolves one — by
    resuming, failing, or finalizing it, whatever that subsystem's semantics
    require. Per-item failures are isolated and logged so one bad row can never
    block startup. Returns the number of items reaped.
    """
    items = list(items)
    if not items:
        return 0
    reaped = 0
    for item in items:
        try:
            await reap(item)
            reaped += 1
        except Exception:
            logger.warning(
                "reap_orphans[%s]: failed to reap %r", label, item, exc_info=True
            )
    logger.info("reap_orphans[%s]: reaped %d orphan(s) at startup", label, reaped)
    return reaped

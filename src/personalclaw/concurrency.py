"""Cross-process concurrency primitives — single-flight locks + orphan reaping.

Two small, vendor-neutral primitives used to make long-running background jobs
(history consolidation, autonomous goal loops, memory promotion) robust against
two failure modes that in-process guards alone can't cover:

- **Double-fire across processes.** PClaw runs several processes against one
  ``config_dir()`` — the gateway, the ``personalclaw consolidate`` CLI, the eval
  runner. An in-memory running-set guards one call site; :func:`single_flight`
  adds an OS-level advisory lock so a given job-key runs in at most one place at
  a time — across processes, and (because flock is per open file description)
  across threads within one process too.
- **Crash-zombie state.** A process that dies mid-job leaves persisted
  ``running`` rows that nothing will ever finish. :func:`boot_sweep` is the ONE
  boot-adoption path — both work-unit nouns (loops and workflow runs) resolve
  their crash survivors through it, from inside their own supervisor's first
  poll rather than from a separate startup hook (`PP-16`, "one adoption/reaping
  path"). Its docstring carries why that placement, and not the hook, is what
  makes a failed sweep retryable and a slow revival non-blocking.

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
from typing import Protocol, TypeVar

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
    """Single-flight guard for ``job_key`` — at most one holder does the work.

    Yields ``True`` if this caller acquired the lock (it should do the work), or
    ``False`` if the lock is already held (it should skip — single-flight means
    *don't double-run*, never *wait in line*). Acquisition is non-blocking. The
    lock is released on context exit and, if the process dies inside the block,
    by the OS — so it cannot zombie.

    **The exclusion is not cross-process only.** ``flock`` is scoped to the *open
    file description*, and this function opens a fresh one on every call, so two
    THREADS in one process contend exactly as two processes do — the loser gets
    ``False``. Measured (PP-12): 16 threads on one key, peak simultaneous holders
    inside the critical section = 1. That is what makes ``pool.claim_task``'s
    read-modify-write safe against the engine's in-process ``asyncio.create_task``
    fan-out. Worth stating explicitly because the narrower reading has already cost
    a session: a reader who took the guarantee to be cross-process only diagnosed a
    lease race as an unfixable in-process hole, and this docstring was their source.

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


class BootSweepRow(Protocol):
    """The one thing a boot sweep needs of a persisted work-unit row: an id.

    Deliberately this narrow. Both work-unit nouns satisfy it (``loop.loop.Loop`` and
    ``workflows.models.WorkflowRun``) without either importing the other, which is what
    lets ONE sweep serve both without pulling a store into this module.
    """

    id: str


_RowT = TypeVar("_RowT", bound=BootSweepRow)


async def boot_sweep(
    label: str,
    rows: Iterable[_RowT],
    *,
    survived: Callable[[_RowT], bool],
    decide: Callable[[_RowT], Awaitable[bool]],
) -> set[str]:
    """The ONE boot-adoption path: decide the fate of every crash-survivor row, once.

    A process that dies mid-job leaves rows persisted as in-flight that nothing will ever
    finish. ``survived`` answers the only question this primitive owns — *is this row a
    crash survivor, i.e. persisted in-flight with no live driver in THIS process* — and
    ``decide`` applies whatever fate the subsystem's semantics require (re-arm the worker,
    park it for the user, finish it, honestly abort it). ``decide`` returns ``True`` when it
    wrote a fate, and the ids of those rows come back here so the caller's own poll does not
    immediately re-drive what the sweep just settled.

    Per-row failures are isolated and logged: one unreadable row can never cost a whole
    process its boot adoption.

    **Call it from the owning supervisor's FIRST POLL, never from a separate boot hook**
    (`PP-16`, "one adoption/reaping path"). Both properties that makes true are load-bearing
    and neither is available to a boot hook:

    * **A failed sweep is retried.** The caller flips its ``_swept`` flag only after this
      returns, so a sweep that raises is re-attempted on the next poll. A gateway boot hook
      wrapped in ``except: logger.warning`` loses boot adoption for the life of the process —
      and the rows it should have decided sit in-flight forever, which a user reads as "still
      working" while nothing is.
    * **Startup is not blocked on revival.** Reviving a row can mean spawning a worker or
      re-running a planner pass (a model call). Awaited inline in a gateway's startup
      sequence, N stranded rows delay everything after it, including HTTP readiness.

    Returns the set of ids whose fate this sweep decided (``len()`` is the reaped count).
    """
    rows = list(rows)
    orphans = [row for row in rows if survived(row)]
    if not orphans:
        return set()
    decided: set[str] = set()
    for row in orphans:
        try:
            if await decide(row):
                decided.add(row.id)
        except Exception:
            logger.warning("boot_sweep[%s]: failed to decide %r", label, row.id, exc_info=True)
    logger.info(
        "boot_sweep[%s]: %d crash survivor(s), %d decided", label, len(orphans), len(decided)
    )
    return decided

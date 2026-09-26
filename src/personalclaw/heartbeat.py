"""Heartbeat service — periodic background maintenance, and the HEARTBEAT.md task format.

The service runs on a configurable interval (default 60s): idle-session consolidation,
background compression, session reindex, auto-archive and due-commitment delivery — the
user-facing behaviors §4.4 explicitly KEEPS here.

**The HEARTBEAT.md task queue is not run here.** It ran every 60 s with no UI — no schedule, no
last run, no way to turn it off — an automation nobody could see. It is the
``system:heartbeat-tasks`` trigger now (``action_providers/heartbeat_tasks_provider.py``),
listed on the Triggers page with its cadence, its runs and its switch; this module keeps the
file's format (:func:`run_tasks`, the ``HEARTBEAT_KEEP`` sentinel) because the agent writes it
and the trigger reads it.

**Store maintenance is not here at all.** Memory FTS
reconciliation, the history and SEL prunes and skill-library aging belong to the
health-scored remediation engine, which is driven by ONE adaptive-clock trigger
(``system:self-remediation``) and not by this loop. Both the engine's driver
(``_maybe_remediate``, with its private ``_remediation_next_ts`` scheduler) and the
duplicate per-tick copies of those four passes (``_legacy_maintenance``) were DELETED with
that re-homing: one implementation of each pass, on one cadence, with no config flag
switching between two of them.
"""

import asyncio
import logging
import re
from collections.abc import Awaitable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Coroutine

from personalclaw import shutdown_event
from personalclaw.atomic_write import atomic_write
from personalclaw.memory import workspace_dir

if TYPE_CHECKING:
    from personalclaw.history import HistoryConsolidator

logger = logging.getLogger(__name__)

# Deliver target extracted from <!-- deliver:xxx --> in heartbeat entries
_DELIVER_RE = re.compile(r"<!--\s*deliver:(\S+)\s*-->")

# Agent can include this sentinel in its response to signal the task is not done
_KEEP_SENTINEL = "HEARTBEAT_KEEP"
_KEEP_RE = re.compile(_KEEP_SENTINEL, re.IGNORECASE)

_DEFAULT_INTERVAL = 60
# 🔴 `_FTS_REBUILD_TICKS` and `_PRUNE_TICKS` are GONE. They were the cadences of
# `_legacy_maintenance`, the duplicate copy of four passes the remediation engine already
# registers (`memory.rebuild-fts`, `memory.prune-history`, `sel.prune`, `skills.age`). Those
# passes are deficit-driven now, not tick-modulo — a job runs when its measured backlog moves
# the health score, which is why there is no number here to keep in sync with the engine's.
# Background compression pass (Context Economy §4) — hourly, budgeted. It only ever
# touches sessions idle for days, so an hourly cadence is plenty; the per-pass cap
# bounds LLM spend regardless.
_BG_COMPRESS_TICKS = 60
# Cross-session search reindex — every 5 minutes. The
# per-turn hook keeps dashboard chats current, so this exists to catch what that
# hook can't see: channel appends, transcripts rewritten by compaction, sessions
# deleted outside the app, and a first run over pre-existing history. Incremental
# by mtime, so an up-to-date index costs one listing.
_SESSION_INDEX_TICKS = 5
# Session auto-archive — hourly. The rule's unit is DAYS,
# so a tighter cadence buys nothing; hourly means a session that crosses the threshold
# is out of the list within the hour rather than at the next restart.
_SESSION_ARCHIVE_TICKS = 60
# Ceiling per pass so the very first sweep over a large history can't monopolize a
# tick; the remainder lands on subsequent passes.
_SESSION_INDEX_MAX_PER_PASS = 200
HEARTBEAT_FILE = "HEARTBEAT.md"
_HEADER = (
    "# Heartbeat Tasks\n\n<!-- Add tasks below (one per line). "
    "PersonalClaw picks them up on next heartbeat. -->\n"
)


def heartbeat_path() -> Path:
    return workspace_dir() / HEARTBEAT_FILE


#: One HEARTBEAT.md task as the gateway's unattended background turn: `(task, deliver) -> response`.
#: Registered by the gateway at boot, dashboard or not, so the `heartbeat-tasks` trigger runs the
#: queue through the same turn it always used: the background prompt, the unattended approval
#: policy, and delivery of each finished task's result to its `deliver:` target.
_task_runner: Callable[[str, str], Awaitable[str | None]] | None = None


def set_task_runner(runner: Callable[[str, str], Awaitable[str | None]] | None) -> None:
    global _task_runner
    _task_runner = runner


def task_runner() -> Callable[[str, str], Awaitable[str | None]] | None:
    """The registered runner, or None before the gateway has started."""
    return _task_runner


def ensure_heartbeat_file() -> Path:
    """Create HEARTBEAT.md with its header when it is missing, so the agent finds the queue."""
    path = heartbeat_path()
    if not path.exists():
        atomic_write(path, _HEADER)
    return path


@dataclass
class TaskPass:
    """What one pass over HEARTBEAT.md did: how many tasks it ran, and which it kept."""

    ran: int = 0
    kept: int = 0
    failed: list[str] = field(default_factory=list)

    @property
    def done(self) -> int:
        return self.ran - self.kept


async def run_tasks(run_task: Callable[[str, str], Awaitable[str | None]]) -> TaskPass:
    """Run every task in HEARTBEAT.md once, concurrently, and keep the unfinished ones.

    A task is kept when it raised (it retries on the next pass) or when its response carries
    ``HEARTBEAT_KEEP`` (the agent's "not done yet"); every other task is removed. The file is
    rewritten only when there were tasks, so an empty queue costs one read.
    """
    path = heartbeat_path()
    result = TaskPass()
    if not path.exists():
        return result
    tasks = _extract_tasks(path.read_text(encoding="utf-8").strip())
    if not tasks:
        return result
    logger.info("Heartbeat: %d task(s) found", len(tasks))
    keep: list[tuple[str, str]] = []
    outcomes = await asyncio.gather(*[run_task(t, d) for t, d in tasks], return_exceptions=True)
    for (task_text, deliver), outcome in zip(tasks, outcomes):
        if isinstance(outcome, BaseException):
            logger.warning("Heartbeat task failed: %s", task_text[:80], exc_info=outcome)
            keep.append((task_text, deliver))
            result.failed.append(task_text[:80])
        elif _should_keep(outcome):
            logger.info("Heartbeat task incomplete, keeping: %s", task_text[:80])
            keep.append((task_text, deliver))
    result.ran = len(tasks)
    result.kept = len(keep)
    lines = _HEADER
    for text, deliver in keep:
        suffix = f"  <!-- deliver:{deliver} -->" if deliver else ""
        lines += f"- {text}{suffix}\n"
    atomic_write(path, lines)
    return result


class HeartbeatService:
    """Periodic wake-up that runs background maintenance tasks."""

    # Class-level default so an instance built to drive one beat in isolation
    # (``__new__`` + a couple of attributes, which the tests do deliberately) still
    # has every optional callback defined. An added optional hook must never make a
    # partially-constructed service raise on an unrelated tick.
    _on_auto_archive: Callable[[], Coroutine] | None = None

    # 🔴 `memory` IS NOT A PARAMETER. It existed for `_legacy_maintenance`'s
    # `rebuild_index`/`prune_history` calls, and with that method deleted the store was assigned
    # and never read — a constructor argument every caller had to supply for nothing. The engine's
    # `memory.rebuild-fts` / `memory.prune-history` jobs open their own store.
    def __init__(
        self,
        interval: int = _DEFAULT_INTERVAL,
        consolidator: "HistoryConsolidator | None" = None,
        on_due_commitments: Callable[[], Coroutine] | None = None,
        on_auto_archive: Callable[[], Coroutine] | None = None,
    ) -> None:
        self._interval = interval
        self._consolidator = consolidator
        # M5e — proactive commitment delivery: a coroutine the gateway wires to
        # scan due commitments and deliver/dismiss them. None when the gateway
        # didn't wire it (e.g. no dashboard). Invoked once per tick, guarded.
        self._on_due_commitments = on_due_commitments
        # Session auto-archive. A coroutine the gateway
        # wires because the heartbeat has no DashboardState (and should not grow one);
        # None when there is no dashboard. Guarded per tick.
        self._on_auto_archive = on_auto_archive
        self._tick = 0
        self._task: asyncio.Task | None = None  # type: ignore[type-arg]

    async def start(self) -> None:
        self._task = asyncio.create_task(self._loop())
        logger.info("Heartbeat started (interval=%ds)", self._interval)

    def stop(self) -> None:
        if self._task:
            self._task.cancel()
            self._task = None

    async def _loop(self) -> None:
        while not shutdown_event.is_set():
            try:
                await asyncio.wait_for(shutdown_event.wait(), timeout=self._interval)
                return  # shutdown signaled
            except asyncio.TimeoutError:
                pass  # normal wake-up
            self._tick += 1
            try:
                await self._beat()
            except Exception:
                logger.warning("Heartbeat tick failed", exc_info=True)

    async def _beat(self) -> None:
        # 🔴 NO HEARTBEAT.md TASKS HERE: they are the `system:heartbeat-tasks` trigger, so the
        # Triggers page shows their cadence, their runs and their switch (see the module docstring).

        # 🔴 NO STORE MAINTENANCE HERE. The remediation engine is driven by its own
        # adaptive-clock trigger (`system:self-remediation`), so this loop neither runs it nor
        # keeps a duplicate copy of the four passes it absorbed. The block that used to sit here
        # decided between two maintenance implementations on a config flag; both the decision and
        # the second implementation are gone.

        # Check for idle sessions needing history consolidation (every tick)
        if self._consolidator:
            self._consolidator.check_idle_sessions()

        # Background compression pass (Context Economy §4) — hourly, budgeted, off the
        # request path. Summarizes old idle at-rest chats for the model, beside the
        # transcript; config-gated, and it never writes the chat file itself.
        if self._consolidator and self._tick % _BG_COMPRESS_TICKS == 0:
            try:
                await self._run_bg_compression()
            except Exception:
                logger.debug("background compression pass failed", exc_info=True)

        # Cross-session search reindex. Off the loop — it
        # reads transcripts — and incremental, so a current index costs a directory
        # listing. Never fatal: search degrading is not worth a dead heartbeat.
        # `_tick` starts at 1, so a plain modulo would leave a fresh install with no
        # index for the first five minutes — including the initial sweep over
        # pre-existing history, which is exactly when search feels broken. Run on the
        # first tick too, then settle into the cadence.
        if self._tick == 1 or self._tick % _SESSION_INDEX_TICKS == 0:
            try:
                from personalclaw import session_search

                indexed = await asyncio.get_running_loop().run_in_executor(
                    None,
                    lambda: session_search.reindex_all(limit=_SESSION_INDEX_MAX_PER_PASS),
                )
                if indexed:
                    logger.debug("session search: indexed %d session(s)", indexed)
            except Exception:
                logger.debug("session search reindex failed", exc_info=True)

        # Session auto-archive. Hourly; the rule's unit is
        # days so nothing tighter is useful. Guarded — decluttering the chat list is
        # never worth a dead heartbeat.
        if self._on_auto_archive is not None and self._tick % _SESSION_ARCHIVE_TICKS == 0:
            try:
                await self._on_auto_archive()
            except Exception:
                logger.debug("session auto-archive pass failed", exc_info=True)

        # Proactive commitment delivery (M5e — O-A4): deliver any due check-ins
        # the agent inferred, at most once per window (the callback dismisses on
        # delivery so it never re-fires). Off unless the user opted in + the
        # gateway wired delivery. Guarded so a delivery error can't kill the tick.
        if self._on_due_commitments is not None:
            try:
                await self._on_due_commitments()
            except Exception:
                logger.warning("Commitment delivery failed", exc_info=True)

    async def _run_bg_compression(self) -> None:
        """Run one budgeted background-compression pass over idle at-rest sessions.

        Resolves the active embedder for topic segmentation (deterministic fallback
        when none is bound — the designed no-model tier); config-gating + eligibility
        live in the service. Best-effort: any failure degrades to "did nothing"."""
        if self._consolidator is None:
            return
        log = getattr(self._consolidator, "_log", None)
        if log is None:
            return
        embed_fn = None
        try:
            from personalclaw.embedding_providers.registry import get_active_embed_fn

            embed_fn = get_active_embed_fn()
        except Exception:
            embed_fn = None  # no embedder → deterministic turn-count segmentation

        from personalclaw.bg_compress import run_bg_compression_pass

        stats = await run_bg_compression_pass(log, embed_fn=embed_fn)
        if stats:
            saved = sum(s["chars_in"] - s["chars_out"] for s in stats)
            logger.info(
                "Background compression: summarized %d chat(s); the model reads ~%d fewer "
                "chars when they resume",
                len(stats),
                saved,
            )


def _should_keep(result: str | None) -> bool:
    """Return True if the agent response signals the task is incomplete."""
    if result is None:
        return False
    return bool(_KEEP_RE.search(result))


def strip_keep_sentinel(text: str) -> str:
    """Remove HEARTBEAT_KEEP sentinel from text."""
    return _KEEP_RE.sub("", text).strip()


def is_keep_response(text: str | None) -> bool:
    """Return True if *text* contains the HEARTBEAT_KEEP sentinel (case-insensitive).

    Use this to check whether a heartbeat task signaled "not done, retry next cycle".
    """
    if text is None:
        return False
    return _KEEP_SENTINEL in text.upper()


def _extract_tasks(content: str) -> list[tuple[str, str]]:
    """Extract tasks as ``(text, deliver_target)`` tuples.

    ``deliver_target`` comes from an inline ``<!-- deliver:xxx -->`` comment.
    Empty string when absent.
    """
    tasks: list[tuple[str, str]] = []
    in_comment = False
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        # Track multi-line HTML comments (standalone comment lines)
        if "<!--" in stripped and "-->" not in stripped:
            in_comment = True
            continue
        if in_comment:
            if "-->" in stripped:
                in_comment = False
            continue
        # Standalone comment line (<!-- ... --> on one line, no task text)
        if stripped.startswith("<!--") and stripped.endswith("-->"):
            continue
        if stripped.startswith("#"):
            continue
        # Extract inline deliver target before stripping comments
        deliver = ""
        m = _DELIVER_RE.search(stripped)
        if m:
            deliver = m.group(1)
            stripped = stripped[: m.start()].rstrip()
        # Strip leading list markers
        for prefix in ("- [x] ", "- [ ] ", "- ", "* "):
            if stripped.startswith(prefix):
                stripped = stripped[len(prefix) :]
                break
        stripped = stripped.strip()
        if stripped and stripped != "-":
            tasks.append((stripped, deliver))
    return tasks

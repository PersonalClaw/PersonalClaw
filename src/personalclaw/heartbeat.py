"""Heartbeat service — periodic background tasks.

Runs on a configurable interval (default 60s):
- Reads HEARTBEAT.md for pending tasks → sends to agent
- Rebuilds FTS index every 15 min
"""

import asyncio
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Coroutine

from personalclaw import shutdown_event
from personalclaw.atomic_write import atomic_write
from personalclaw.memory import MemoryStore, workspace_dir

if TYPE_CHECKING:
    from personalclaw.history import HistoryConsolidator

logger = logging.getLogger(__name__)

# Deliver target extracted from <!-- deliver:xxx --> in heartbeat entries
_DELIVER_RE = re.compile(r"<!--\s*deliver:(\S+)\s*-->")

# Agent can include this sentinel in its response to signal the task is not done
_KEEP_SENTINEL = "HEARTBEAT_KEEP"
_KEEP_RE = re.compile(_KEEP_SENTINEL, re.IGNORECASE)

_DEFAULT_INTERVAL = 60
_FTS_REBUILD_TICKS = 15  # rebuild every 15 ticks (15 min at 60s interval)
_PRUNE_TICKS = 1440  # prune old history once per day (1440 min at 60s interval)
HEARTBEAT_FILE = "HEARTBEAT.md"
_HEADER = (
    "# Heartbeat Tasks\n\n<!-- Add tasks below (one per line). "
    "PersonalClaw picks them up on next heartbeat. -->\n"
)


def heartbeat_path() -> Path:
    return workspace_dir() / HEARTBEAT_FILE


class HeartbeatService:
    """Periodic wake-up that runs background maintenance tasks."""

    def __init__(
        self,
        memory: MemoryStore,
        on_task: Callable[[str, str], Coroutine] | None = None,
        interval: int = _DEFAULT_INTERVAL,
        consolidator: "HistoryConsolidator | None" = None,
        on_due_commitments: Callable[[], Coroutine] | None = None,
    ) -> None:
        self._memory = memory
        self._on_task = on_task
        self._interval = interval
        self._consolidator = consolidator
        # M5e — proactive commitment delivery: a coroutine the gateway wires to
        # scan due commitments and deliver/dismiss them. None when the gateway
        # didn't wire it (e.g. no dashboard). Invoked once per tick, guarded.
        self._on_due_commitments = on_due_commitments
        self._tick = 0
        self._processing = False
        self._task: asyncio.Task | None = None  # type: ignore[type-arg]

    async def start(self) -> None:
        path = heartbeat_path()
        if not path.exists():
            atomic_write(path, _HEADER)
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
        if not self._processing:
            await self._process_heartbeat_file()

        if self._tick % _FTS_REBUILD_TICKS == 0:
            count = self._memory.rebuild_index()
            logger.info("FTS index rebuilt: %d files", count)

        if self._tick % _PRUNE_TICKS == 0:
            from personalclaw.config.loader import AppConfig

            max_days = AppConfig.load().memory.history_max_days
            self._memory.prune_history(keep_days=max_days)

            # Prune security event log per retention policy
            try:
                from personalclaw.sel import sel

                sel().prune()
            except Exception:
                logger.debug("SEL prune failed", exc_info=True)

            # Groom the auto/ skill library: age active→stale→archived by last-use
            # (#27, pure/reversible). Off the event loop — it does blocking file I/O.
            try:
                from personalclaw.skills.curator import run_aging

                report = await asyncio.get_running_loop().run_in_executor(None, run_aging)
                if report.changed:
                    logger.info(report.summary())
            except Exception:
                logger.debug("Skill curator aging failed", exc_info=True)

        # Check for idle sessions needing history consolidation (every tick)
        if self._consolidator:
            self._consolidator.check_idle_sessions()

        # Proactive commitment delivery (M5e — O-A4): deliver any due check-ins
        # the agent inferred, at most once per window (the callback dismisses on
        # delivery so it never re-fires). Off unless the user opted in + the
        # gateway wired delivery. Guarded so a delivery error can't kill the tick.
        if self._on_due_commitments is not None:
            try:
                await self._on_due_commitments()
            except Exception:
                logger.warning("Commitment delivery failed", exc_info=True)

    async def _run_one_task(self, task_text: str, deliver: str) -> str | None:
        """Execute a single heartbeat task (used by gather).

        Returns the agent response text, or ``None`` if no callback.
        """
        assert self._on_task is not None
        return await self._on_task(task_text, deliver)

    async def _process_heartbeat_file(self) -> None:
        path = heartbeat_path()
        if not path.exists():
            return
        content = path.read_text(encoding="utf-8").strip()
        tasks = _extract_tasks(content)
        if not tasks or not self._on_task:
            return

        self._processing = True
        try:
            logger.info("Heartbeat: %d task(s) found", len(tasks))
            keep: list[tuple[str, str]] = []
            results = await asyncio.gather(
                *[self._run_one_task(t, d) for t, d in tasks],
                return_exceptions=True,
            )
            for (task_text, deliver), result in zip(tasks, results):
                if isinstance(result, BaseException):
                    logger.warning(
                        "Heartbeat task failed: %s", task_text[:80], exc_info=result,
                    )
                    keep.append((task_text, deliver))
                elif _should_keep(result):
                    logger.info("Heartbeat task incomplete, keeping: %s", task_text[:80])
                    keep.append((task_text, deliver))

            # Rewrite: keep incomplete/failed tasks so they retry next tick
            lines = _HEADER
            for text, deliver in keep:
                suffix = f"  <!-- deliver:{deliver} -->" if deliver else ""
                lines += f"- {text}{suffix}\n"
            atomic_write(path, lines)
        finally:
            self._processing = False


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

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

🔴 **A TASK RUNS ONLY WITH THE OWNER'S YES ON IT, as a trigger the chat makes does.** The file sits
in the agent's workspace, and the agent is the writer the product tells to use it
(``config/prompts/background.md``): its ``write_file`` and its shell write it, and so does an app
that declared ``/api/file-write``. Measured on `main`: a task any of them wrote ran on the next
pass as an unattended turn in which every tool the security hooks did not deny was approved, and
nobody was asked. So a task runs only once the owner allowed it, sealed to its text (:data:`BOOK`,
`owner_grants`): one they wrote in the Files editor (the save is the yes to the lines it added,
:func:`seal_owner_edit`) and one they allowed on the Triggers page (:func:`allow`, which asks
first). Any other task waits in the file, listed on the Heartbeat tasks trigger's panel, and a pass
leaves it where it is. An edit to a task is a new task, and a finished task takes its yes with it.

Why not "read-only until allowed", which would let the agent's checks run on their own: the
read-only posture an unattended run gets is the task-mode classifier (``task_modes``), and it
reads a tool it has no declaration for by its name. 75 of the agent's
115 tools pass it, ``computer_click``, ``workflow_start`` and ``memory_remember`` among them. A
task held to it could still change things, so "read-only" would be a promise nothing enforces.

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
from personalclaw.owner_grants import GrantBook, seal

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
# The cross-session search index is NOT kept here: it indexed 200 chats every 5 minutes from
# this loop, 5 hours for a 12,005-chat history, so it keeps itself caught up on its own thread
# (`session_search.INDEXER`, started by the gateway).
# Session auto-archive — hourly. The rule's unit is DAYS,
# so a tighter cadence buys nothing; hourly means a session that crosses the threshold
# is out of the list within the hour rather than at the next restart.
_SESSION_ARCHIVE_TICKS = 60
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
#: policy, and delivery of each finished task's result to its `deliver:` target. Handed only the
#: tasks the owner allowed (:func:`run_tasks`).
TaskRunner = Callable[[str, str], Awaitable[str | None]]
_task_runner: TaskRunner | None = None


def set_task_runner(runner: TaskRunner | None) -> None:
    global _task_runner
    _task_runner = runner


def task_runner() -> TaskRunner | None:
    """The registered runner, or None before the gateway has started."""
    return _task_runner


#: The owner's yes to a task, keyed and sealed by its text (`owner_grants`).
BOOK = GrantBook("heartbeat")


def allowed(task_text: str) -> bool:
    """Whether the owner allowed *task_text*, exactly as written, to run."""
    return BOOK.holds(seal(task_text), task_text)


def allow(task_text: str) -> None:
    """The owner's yes to *task_text*: the Triggers page's Allow, after its question."""
    BOOK.give(seal(task_text), task_text)


def seal_owner_edit(before: str, after: str) -> list[str]:
    """The tasks the owner's own save of HEARTBEAT.md added or changed, allowed as theirs.

    Called by the Files editor's save (`dashboard/handlers/files.api_file_write`) for the owner,
    never an app: the owner typed them, which is the yes. A task that was already in the file keeps
    whatever it had — a save is not a yes to lines someone else wrote. Returns what it allowed.
    """
    earlier = {text for text, _deliver in _extract_tasks(before)}
    sealed: list[str] = []
    for text, _deliver in _extract_tasks(after):
        if text not in earlier and not allowed(text):
            allow(text)
            sealed.append(text)
    return sealed


def queued() -> list[dict[str, object]]:
    """The tasks in HEARTBEAT.md as the Triggers page lists them: text, delivery target, and
    whether the owner's yes is on it."""
    path = heartbeat_path()
    if not path.exists():
        return []
    return [
        {"text": text, "deliver": deliver, "allowed": allowed(text)}
        for text, deliver in _extract_tasks(path.read_text(encoding="utf-8").strip())
    ]


def consent(task_text: str) -> str:
    """The sentence the owner agrees to on the Triggers page's Allow — product copy."""
    return (
        f"Allowing “{task_text}” lets the heartbeat run it with your agent's tools, with nobody "
        "watching, on every pass until it is done: it can change files, run commands and do "
        "anything else your agent can. Until you allow it, it does not run."
    )


#: The consent dialog's heading for :func:`consent` (`http_errors.consent_required`).
CONSENT_TITLE = "Allow this heartbeat task to run?"


def ensure_heartbeat_file() -> Path:
    """Create HEARTBEAT.md with its header when it is missing, so the agent finds the queue."""
    path = heartbeat_path()
    if not path.exists():
        atomic_write(path, _HEADER)
    return path


@dataclass
class TaskPass:
    """What one pass over HEARTBEAT.md did: how many tasks it ran, which it kept, and how many it
    left waiting for the owner's Allow."""

    ran: int = 0
    kept: int = 0
    waiting: int = 0
    failed: list[str] = field(default_factory=list)

    @property
    def done(self) -> int:
        return self.ran - self.kept


async def run_tasks(run_task: TaskRunner) -> TaskPass:
    """Run every task in HEARTBEAT.md the owner allowed once, concurrently, and keep the rest.

    A task the owner has not allowed (:func:`allowed`) is not run: it stays in the file as it is,
    waiting for their yes. A task that ran is kept when it raised (it retries on the next pass) or
    when its response carries ``HEARTBEAT_KEEP`` (the agent's "not done yet"); every other one is
    removed, and the yes it had with it — the same words written again later are a new task. The
    file is rewritten only when a task ran, so a queue with nothing to run costs one read.
    """
    path = heartbeat_path()
    result = TaskPass()
    if not path.exists():
        return result
    tasks = _extract_tasks(path.read_text(encoding="utf-8").strip())
    ready = [(text, deliver) for text, deliver in tasks if allowed(text)]
    result.waiting = len(tasks) - len(ready)
    if not ready:
        return result
    logger.info("Heartbeat: %d task(s) to run, %d waiting", len(ready), result.waiting)
    finished: set[str] = set()
    outcomes = await asyncio.gather(*[run_task(t, d) for t, d in ready], return_exceptions=True)
    for (task_text, _deliver), outcome in zip(ready, outcomes):
        if isinstance(outcome, BaseException):
            logger.warning("Heartbeat task failed: %s", task_text[:80], exc_info=outcome)
            result.failed.append(task_text[:80])
            result.kept += 1
        elif _should_keep(outcome):
            logger.info("Heartbeat task incomplete, keeping: %s", task_text[:80])
            result.kept += 1
        else:
            finished.add(task_text)
    result.ran = len(ready)
    for text in finished:
        BOOK.revoke(seal(text))
    lines = _HEADER
    for text, deliver in tasks:
        if text in finished:
            continue
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

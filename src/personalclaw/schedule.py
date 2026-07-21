"""Schedule service for running agent tasks on a timer.

Jobs are stored in the config directory (``~/.personalclaw/crons.json`` by default,
overridden by ``PERSONALCLAW_HOME``) and executed by a background
asyncio timer.  Each job fires a callback (typically posting the result to
the owner's channel).

Cross-process safety: the CLI and gateway run as separate processes sharing
the same ``crons.json``.  All read-modify-write cycles use advisory file
locking (fcntl), and mtime-based ``_sync()``
detects external file changes
before every mutation.  Job execution releases the lock so long-running jobs
don't block the CLI.

Jobs are created via MCP tools (``schedule_add``) or the CLI (``personalclaw cron add``).

Supports three schedule types:
- ``every`` — recurring interval (min 60s)
- ``at`` — one-shot at a unix timestamp
- ``cron`` — standard cron expression (min hour dom month dow)
"""

import asyncio
import fcntl
import json
import logging
import os
import signal
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Iterator
from zoneinfo import ZoneInfo

from personalclaw.autonomous_framing import with_autonomous_framing

if TYPE_CHECKING:
    from personalclaw.session import SessionManager

try:
    from cron_descriptor import Options, get_description  # type: ignore[import-untyped]
except ImportError:
    Options = None  # type: ignore[assignment,misc]
    get_description = None  # type: ignore[assignment]
from croniter import croniter  # type: ignore[import-untyped]

from personalclaw import shutdown_event
from personalclaw.atomic_write import atomic_write
from personalclaw.config.loader import AppConfig, config_dir
from personalclaw.schedule_history import ScheduleRun, ScheduleRunStore

logger = logging.getLogger(__name__)

# ── Constants ──

_DEFAULT_DIR = config_dir()
_CRONS_FILE = "crons.json"
_STORE_VERSION = 2
_MIN_INTERVAL_SECS = 60
_JOB_TIMEOUT_SECS = 1800  # 30 min per job
# Transient sentinel for ScheduleJob.last_status during a run: "the callback has
# not reported a verdict yet". _execute seeds it, then defaults to "ok" only if it
# survives — so an action that self-reports "error" is not clobbered (T7).
_STATUS_PENDING = "_pending"
_TIMER_POLL_SECS = 30  # check for due cron-expr jobs
_REAPER_INTERVAL = 60  # seconds between reaper sweeps
_REAPER_RESET_TIMEOUT = 30.0  # max seconds for session reset in reaper

# Jitter bounds (seconds) to spread job execution and avoid traffic spikes
_JITTER_HOURLY_MAX = 20 * 60  # 0–20 minutes for hourly jobs
_JITTER_DAILY_MAX = 2 * 3600  # 0–2 hours for daily jobs


# ── Types ──


@dataclass
class ScheduleDefinition:
    """Schedule definition — ``every``, ``at``, or ``cron``."""

    kind: str  # "every" | "at" | "cron"
    every_secs: int | None = None
    at_ts: float | None = None
    cron_expr: str | None = None  # "min hour dom month dow"


@dataclass
class ScheduleJob:
    """A scheduled job.

    The job's *what-runs* is a single canonical Trigger **action** —
    ``{"provider": <name>, "config": {...}}`` chosen from the action-provider
    catalog. There is no separate exec-mode axis: ``invoke-agent`` runs an LLM
    agent turn, ``bash`` / ``run-script`` run a deterministic (zero-token)
    command/script, and any other provider dispatches through the same registry.
    The agent parameters (prompt, agent name, model, approval mode) and the
    command/script body are exposed as read-only projections of ``action.config``
    (see the properties below): ``job.message`` / ``job.agent_id`` / ``job.script``
    all read through to the canonical action.
    """

    id: str
    name: str
    schedule: ScheduleDefinition = field(default_factory=lambda: ScheduleDefinition(kind="every"))
    channel: str | None = None
    thread_ts: str | None = None
    enabled: bool = True
    last_run_ts: float | None = None
    last_status: str | None = None  # "ok" | "error"
    last_error: str | None = None
    # Runtime-only (not persisted): the ActionResult.outcome of the last run —
    # "launched" for a fire-and-forget spawn (run-prompt/run-workflow/invoke-agent),
    # so _record_run can report honest "started ≠ succeeded" status (T7).
    last_outcome: str = ""
    # Runtime-only (not persisted): set for a dry-run REPLAY (T9) so the action
    # dispatch injects dry_run into the action config — the run previews what WOULD
    # happen with no side effects. Cleared after the run.
    dry_run: bool = False
    created_ts: float = 0.0
    delete_after_run: bool = False
    last_result: str | None = None
    context_enabled: bool = False
    acked_items: list[str] = field(default_factory=list)
    created_by: str = ""  # channel user ID of the creator (for DM fallback)
    silent: bool = False  # suppress auto-delivery; agent sends via send_message
    session_key: str = ""  # session that created this job (for scoped removal)
    last_posted_hash: str = ""  # hash of last result delivered to the channel (dedup)
    consecutive_dupes: int = 0  # count of suppressed duplicate results
    last_posted_at: float = 0.0  # epoch when last channel post was delivered (dedup reminder)
    last_failure_hash: str = ""  # hash of last failure notification (dedup crashes)
    last_failure_at: float = 0.0  # epoch of last failure channel alert (dedup reminder)
    consecutive_failures: int = 0  # count of consecutive identical failures (incl. first alert)
    skip_dates: list[str] = field(default_factory=list)  # ISO dates to skip ["2026-04-06"]
    timezone: str = ""  # IANA timezone for skip evaluation
    persistent_session: bool = True  # False → fresh ephemeral session per run
    # When agent_sequence is set, it takes precedence over the action's single agent.
    # The execution logic runs agents in order.
    agent_sequence: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)  # per-job environment variables
    timeout_secs: int = _JOB_TIMEOUT_SECS
    strict_schedule: bool = False  # when True, skip jitter and fire exactly on schedule
    # The canonical Trigger action — the sole source of *what runs*.
    action: dict = field(default_factory=dict)

    # ── action.config projections (read-only) ──
    # These present the action's config as the historical exec fields so reader
    # sites (the executor's agent turn, session-context builder, serializers,
    # CLI) need no change. Writers set ``action`` directly.

    @property
    def _config(self) -> dict:
        return self.action.get("config") or {} if isinstance(self.action, dict) else {}

    @property
    def provider(self) -> str:
        """The action provider name (``invoke-agent`` / ``bash`` / ``run-script`` / …)."""
        return str(self.action.get("provider") or "") if isinstance(self.action, dict) else ""

    @property
    def exec_mode(self) -> str:
        """The execution strategy: 'script' | 'command' | 'agent'."""
        p = self.provider
        if p == "run-script":
            return "script"
        if p == "bash":
            return "command"
        return "agent"

    @property
    def message(self) -> str:
        """The agent prompt — the ``invoke-agent`` action's ``task_template``."""
        return (
            str(self._config.get("task_template") or "") if self.provider == "invoke-agent" else ""
        )

    @property
    def agent_id(self) -> str:
        return str(self._config.get("agent") or "") if self.provider == "invoke-agent" else ""

    @property
    def model(self) -> str:
        """ "" (use agent's model) | model name override."""
        return str(self._config.get("model") or "") if self.provider == "invoke-agent" else ""

    @property
    def approval_mode(self) -> str:
        """ "" (default/hook-based) | "auto" (auto-approve all tools)."""
        return (
            str(self._config.get("approval_mode") or "") if self.provider == "invoke-agent" else ""
        )

    @property
    def script(self) -> str:
        """ "path/to/file.py:func" under ~/.personalclaw/crons/ — the run-script action."""
        return str(self._config.get("script") or "") if self.provider == "run-script" else ""

    @property
    def command(self) -> str:
        """Shell command string — the bash action."""
        return str(self._config.get("command") or "") if self.provider == "bash" else ""

    @property
    def zt_timeout(self) -> int:
        """Zero-token execution timeout (0 = mode default: 30s script / 300s command)."""
        if self.provider in ("run-script", "bash"):
            try:
                return int(self._config.get("timeout") or 0)
            except (ValueError, TypeError):
                return 0
        return 0


# ── Schedule variable catalog ──
# The ``$variables`` an action templated on a SCHEDULE trigger can interpolate.
# A scheduled action runs unattended, so it gets the job identity, the previous
# run's result, and the firing time/zone (assembled into the ActionContext in the
# gateway's ``_run_action_job``). Served by ``GET /api/triggers/variables`` — the
# UIs do NOT mirror it. ``$EVENT`` is ``schedule:<id>`` and ``$CONTEXT`` is the
# previous result, matching the lifecycle base vars' meaning.
SCHEDULE_VARS: tuple[str, ...] = (
    "$EVENT",
    "$CONTEXT",
    "$last_result",
    "$now",
    "$timezone",
    "$job_id",
    "$job_name",
)


# ── Session-context helper ──


def build_schedule_session_context(job: ScheduleJob) -> tuple[str, str]:
    """Compute (session_key, prompt) for one cron run.

    When ``job.persistent_session`` is True (default):
      - session_key is stable across runs: ``cron:{job.id}``
      - prompt prepends ``job.last_result`` so the agent has recent context

    When ``job.persistent_session`` is False:
      - session_key is unique per call: ``cron:{job.id}:{uuid}``
        → each run opens a fresh agent session; no context accumulation
      - prompt is the bare ``job.message`` — no last_result injection

    The key prefix ``cron:{job.id}`` is preserved in both modes so the
    reaper's existing session-matching logic continues to work.

    This is a pure function — all side effects (session creation, channel
    delivery, acked_items handling) happen in the caller. Keep it that way
    so it stays trivially unit-testable.
    """
    if job.persistent_session:
        msg = job.message
        context_parts: list[str] = []
        if job.acked_items:
            context_parts.append(
                "[Iteration context queue — messages from earlier runs]\n"
                + "\n".join(f"- {item}" for item in job.acked_items)
                + "\n[End iteration context queue]"
            )
        if job.last_result:
            context_parts.append(
                "[Previous run result — do NOT repeat the same content]\n"
                f"{job.last_result}\n"
                "[End of previous run result]"
            )
        if context_parts:
            msg = "\n\n".join(context_parts) + "\n\n" + msg
        return f"cron:{job.id}", with_autonomous_framing(msg)

    # Stateless: fresh key, bare message.
    run_id = uuid.uuid4().hex[:8]
    return f"cron:{job.id}:{run_id}", with_autonomous_framing(job.message)


# ── Action builders ──
# A schedule job's *what-runs* is a single canonical action. These build the
# three provider shapes the schedule surface authors; any other provider is
# stored as-is via the unified Triggers API.


def make_agent_action(
    message: str = "",
    agent: str = "",
    model: str = "",
    approval_mode: str = "",
) -> dict:
    """An ``invoke-agent`` action — run an LLM agent turn with this prompt."""
    return {
        "provider": "invoke-agent",
        "config": {
            "task_template": message or "",
            "agent": agent or "",
            "model": model or "",
            "approval_mode": approval_mode or "",
        },
    }


def make_command_action(command: str, timeout: int = 0) -> dict:
    """A ``bash`` action — run a shell command deterministically (zero-token)."""
    return {"provider": "bash", "config": {"command": command or "", "timeout": int(timeout or 0)}}


def make_script_action(script: str, timeout: int = 0) -> dict:
    """A ``run-script`` action — run a sandboxed ``file.py:func`` (zero-token)."""
    return {
        "provider": "run-script",
        "config": {"script": script or "", "timeout": int(timeout or 0)},
    }


def _action_from_record(j: dict) -> dict:
    """The canonical action for a persisted job record.

    Prefers the stored ``action``; for records written before the exec-modes
    were folded into actions, reconstructs it from the legacy exec keys
    (``script`` / ``command`` else agent ``message``/``agent_id``/``model``/
    ``approval_mode``) so old ``crons.json`` files load transparently.
    """
    action = j.get("action")
    if isinstance(action, dict) and action.get("provider"):
        return action
    if j.get("script"):
        return make_script_action(j["script"], int(j.get("zt_timeout") or 0))
    if j.get("command"):
        return make_command_action(j["command"], int(j.get("zt_timeout") or 0))
    return make_agent_action(
        message=j.get("message") or "",
        agent=j.get("agent_id") or "",
        model=j.get("model") or "",
        approval_mode=j.get("approval_mode") or "",
    )


def normalize_action(action: dict | None) -> dict:
    """Validate + canonicalize an action dict (provider + config). Raises on bad input."""
    if not isinstance(action, dict) or not action.get("provider"):
        raise ValueError("action must be an object with a 'provider'")
    provider = str(action["provider"]).strip()
    config = action.get("config") or {}
    if not isinstance(config, dict):
        raise ValueError("action.config must be an object")
    if provider == "run-script":
        script = str(config.get("script") or "").strip()
        if script:
            from personalclaw.schedule_script import resolve_script_path

            resolve_script_path(script)  # eager validation — reject a bad spec at creation
    if provider == "invoke-agent":
        mode = str(config.get("approval_mode") or "")
        if mode not in ("", "auto"):
            raise ValueError(f"Invalid approval_mode: {mode!r}")
    return {"provider": provider, "config": config}


# ── Cron expression matching (via croniter) ──


def cron_expr_matches(expr: str, dt: datetime) -> bool:
    """Check if ``dt`` matches a 5-field cron expression (min hour dom month dow)."""
    try:
        return croniter.match(expr, dt)
    except (ValueError, KeyError):
        return False


def validate_cron_expr(expr: str) -> bool:
    """Return True if ``expr`` is a syntactically valid 5-field cron expression."""
    return croniter.is_valid(expr)


# ── Service ──


def _humanize_cron(expr: str, tz_name: str = "") -> str:
    """Convert a 5-field cron expression to human-readable string with timezone."""
    if get_description is None:
        return expr
    opts = Options()
    opts.use_24hour_time_format = False
    try:
        desc = get_description(expr, opts)
    except Exception:
        return expr

    # Timezone-aware display: evaluate the cron expression in the job's
    # timezone (matching compute_next_run_ts) and display the local time.
    parts = expr.split()
    if tz_name and len(parts) == 5 and parts[0].isdigit() and parts[1].isdigit():
        try:
            tz = ZoneInfo(tz_name)
            # Evaluate in job timezone, same as the scheduler does
            base = datetime.now(tz)
            next_local = croniter(expr, base).get_next(datetime).astimezone(tz)
            local_time = next_local.strftime("%-I:%M %p %Z")
            # cron_descriptor produces UTC-based text; replace the time portion
            utc_base = datetime.now(timezone.utc)
            next_as_utc = croniter(expr, utc_base).get_next(datetime)
            utc_time = next_as_utc.strftime("%-I:%M %p")
            utc_time_padded = next_as_utc.strftime("%I:%M %p")
            result = desc.replace(f"At {utc_time}", f"At {local_time}")
            if result == desc:
                result = desc.replace(f"At {utc_time_padded}", f"At {local_time}")
            if result == desc:
                # Fallback: prepend local time if replacement failed
                result = f"At {local_time}, {desc.removeprefix('At ')}"
            return result
        except Exception:
            pass

    return desc


def format_schedule(schedule: ScheduleDefinition, tz_name: str = "") -> str:
    """Human-readable schedule description."""
    # Fallback: read timezone from config (callers in loops should pass tz_name)
    if not tz_name:
        try:
            tz_name = AppConfig.load().timezone
        except Exception:
            pass
    if schedule.kind == "cron" and schedule.cron_expr:
        return _humanize_cron(schedule.cron_expr, tz_name)
    if schedule.kind == "every" and schedule.every_secs:
        secs = schedule.every_secs
        if secs >= 3600:
            return f"every {secs // 3600}h"
        return f"every {secs}s"
    if schedule.kind == "at" and schedule.at_ts:
        tz = ZoneInfo(tz_name) if tz_name else None
        if tz:
            now = datetime.now(tz)
            dt = datetime.fromtimestamp(schedule.at_ts, tz)
        else:
            now = datetime.now().astimezone()
            dt = datetime.fromtimestamp(schedule.at_ts).astimezone()
        if dt.date() == now.date():
            return f"at {dt:%I:%M %p %Z}"
        return f"at {dt:%I:%M %p %Z}, {dt:%b %-d}"
    return schedule.kind


def get_local_tz() -> tuple[str, ZoneInfo]:
    """Return (tz_name, ZoneInfo) from config, falling back to UTC."""
    try:
        tz_name = AppConfig.load().timezone or "UTC"
        return tz_name, ZoneInfo(tz_name)
    except Exception:
        logger.warning(
            "Failed to load timezone from config, falling back to UTC",
            exc_info=True,
        )
        return "UTC", ZoneInfo("UTC")


def _job_tz(job: ScheduleJob) -> ZoneInfo:
    """Return the job's timezone, falling back to config then UTC."""
    try:
        tz_name = job.timezone or AppConfig.load().timezone or "UTC"
        return ZoneInfo(tz_name)
    except Exception:
        logger.warning("Failed to resolve timezone for job %s, using UTC", job.id, exc_info=True)
        return ZoneInfo("UTC")


def compute_next_run_ts(job: ScheduleJob, now: float | None = None) -> float | None:
    """Return the next fire time as a UTC epoch, or ``None`` if unknown."""
    try:
        if not job.enabled:
            return None
        sched = job.schedule
        now = now if now is not None else time.time()
        if sched.kind == "every" and sched.every_secs is not None:
            last = job.last_run_ts if job.last_run_ts is not None else job.created_ts
            if last is None:
                return None
            nxt = last + sched.every_secs
            return nxt if nxt > now else now
        if sched.kind == "at" and sched.at_ts is not None:
            return sched.at_ts if sched.at_ts > now else None
        if sched.kind == "cron" and sched.cron_expr is not None:
            # croniter interprets cron_expr in base's timezone; get_next(float) returns UTC epoch
            base = datetime.fromtimestamp(now, tz=_job_tz(job))
            return croniter(sched.cron_expr, base).get_next(float)
    except Exception:
        logger.warning("Failed to compute next run for job %s", job.id, exc_info=True)
        return None
    return None


class ScheduleService:
    """Background service for managing and executing scheduled jobs."""

    def __init__(
        self,
        base_dir: Path | None = None,
        on_job: Callable[[ScheduleJob], Awaitable[str | None]] | None = None,
    ):
        self._dir = base_dir or _DEFAULT_DIR
        self._path = self._dir / _CRONS_FILE
        self._on_job = on_job
        self._jobs: list[ScheduleJob] = []
        self._timer_task: asyncio.Task[None] | None = None
        self._running = False
        self._last_mtime: float = 0.0
        self._executing: set[str] = set()  # job IDs currently running
        self._running_tasks: dict[str, asyncio.Task[None]] = {}  # strong refs to prevent GC
        self._job_start_times: dict[str, float] = {}  # job ID → epoch start
        self._reaped_jobs: set[str] = set()  # job IDs killed by the reaper
        self._job_jitter: dict[str, float] = {}  # job ID → jitter seconds applied
        # job_id → active session_key for the in-flight run.
        # Populated by the dispatcher (gateway callback) so the reaper can
        # target per-run ephemeral keys when persistent_session=False.
        self._active_session_keys: dict[str, str] = {}
        self._sessions: "SessionManager | None" = None
        self._reaper_task: asyncio.Task[None] | None = None
        # Execution-run history (the ScheduleRun sub-entity store, service-owned).
        self._run_store = ScheduleRunStore(base_dir=self._dir)
        # job_id → (started_at, trigger) for the in-flight run, so the run
        # record can be tagged "scheduled" vs "manual".
        self._job_run_meta: dict[str, tuple[float, str]] = {}
        # Optional callback to push a live-refresh hint to dashboard clients.
        self._push_refresh: Callable[[str], None] | None = None

    # ── Lifecycle ──

    async def start(self) -> None:
        """Load jobs and start the timer loop."""
        self._load()
        try:
            await self._run_store.rotate_all()
        except Exception:
            logger.debug("Run-history rotation on start failed", exc_info=True)
        self._running = True
        self._arm_timer()
        logger.info("Cron service started with %d jobs", len(self._jobs))

    async def stop(self) -> None:
        """Stop the timer loop and cancel running jobs."""
        self._running = False
        if self._reaper_task:
            self._reaper_task.cancel()
            try:
                await self._reaper_task
            except (asyncio.CancelledError, Exception):
                pass
            self._reaper_task = None
        if self._timer_task:
            self._timer_task.cancel()
            self._timer_task = None
        for task in self._running_tasks.values():
            task.cancel()
        if self._running_tasks:
            await asyncio.gather(*self._running_tasks.values(), return_exceptions=True)
            self._running_tasks.clear()

    # ── Reaper ──

    def start_reaper(self, sessions: "SessionManager") -> None:
        """Start the periodic reaper loop.  Call once after the event loop is running."""
        self._sessions = sessions
        if self._reaper_task is None:
            self._reaper_task = asyncio.create_task(self._reaper_loop())

    async def _reaper_loop(self) -> None:
        """Periodically force-kill cron jobs that exceed the timeout.

        Defense-in-depth: catches cases where ``asyncio.wait_for`` in
        ``_execute_with_timeout`` fails to fire (event-loop saturation,
        orphaned tasks).
        """
        while True:
            await asyncio.sleep(_REAPER_INTERVAL)
            now = time.time()
            for job_id, started in list(self._job_start_times.items()):
                elapsed = now - started
                jitter_allowance = self._job_jitter.get(job_id, 0.0)
                if elapsed <= _JOB_TIMEOUT_SECS + jitter_allowance:
                    continue
                task = self._running_tasks.get(job_id)
                if task and task.done():
                    # Normal timeout path already completed; just clean up tracking.
                    self._job_start_times.pop(job_id, None)
                    continue
                logger.warning(
                    "Reaper: cron job %s exceeded %ds (ran %.0fs), force-killing",
                    job_id,
                    _JOB_TIMEOUT_SECS,
                    elapsed,
                )
                try:
                    await self._force_reap(job_id, elapsed)
                except Exception:
                    logger.exception("Reaper: failed to reap cron job %s", job_id)

    async def _force_reap(self, job_id: str, elapsed: float) -> None:
        """Kill a cron job's session process and cancel its task."""
        # use the active per-run session key if registered;
        # fall back to the stable key for persistent sessions.
        session_key = self._active_session_keys.get(job_id) or f"cron:{job_id}"
        self._reaped_jobs.add(job_id)
        self._job_start_times.pop(job_id, None)  # prevent repeated reaping
        # Kill the session process first.
        if self._sessions:
            try:
                await asyncio.wait_for(
                    self._sessions.reset(session_key), timeout=_REAPER_RESET_TIMEOUT
                )
            except asyncio.TimeoutError:
                logger.warning("Reaper: reset hung for cron %s, attempting SIGKILL", job_id)
                self._sigkill_session(session_key)
            except Exception:
                logger.exception("Reaper: reset failed for cron %s, attempting SIGKILL", job_id)
                self._sigkill_session(session_key)

        # Cancel the asyncio task and clean up tracking state directly.
        # Don't rely on _run_job_isolated's finally — the reaper exists for
        # cases where the normal path is stuck (idempotent with finally).
        task = self._running_tasks.pop(job_id, None)
        if task and not task.done():
            task.cancel()
        self._executing.discard(job_id)

        # Update job state and persist.
        by_id = {j.id: j for j in self._jobs}
        job = by_id.get(job_id)
        if job:
            job.last_status = "error"
            job.last_error = (
                f"Reaped after {int(elapsed)}s (exceeded {_JOB_TIMEOUT_SECS}s deadline)"
            )
            job.last_run_ts = time.time()
            try:
                self._save()
            except Exception:
                logger.exception("Reaper: failed to persist state for cron %s", job_id)

        # SEL audit.
        try:
            from personalclaw.sel import sel

            sel().log_tool_invocation(
                session_key=session_key,
                source="cron",
                tool_name="reaper_force_kill",
                outcome="reaped",
                metadata={
                    "job_id": job_id,
                    "session_key": session_key,
                    "elapsed": int(elapsed),
                },
            )
        except Exception:
            logger.exception("Reaper: SEL audit failed for cron %s", job_id)

    def _sigkill_session(self, session_key: str) -> None:
        """Best-effort SIGKILL when graceful reset hangs.

        Uses killpg to kill the entire process group, then sweeps
        escaped children in different PGIDs (MCP servers).
        """
        if not self._sessions:
            return
        try:
            from personalclaw.acp.client import (
                _get_child_pids,
                _get_start_time,
                _is_our_child,
                _kill_escaped_children,
            )

            session = self._sessions._sessions.get(session_key)
            if not session:
                logger.warning("Reaper: no session found for %s", session_key)
                return
            client = getattr(session.provider, "_client", None)
            raw_pid = getattr(client, "_pid", None) if client else None
            pid = raw_pid if isinstance(raw_pid, int) else None
            if not pid:
                logger.warning("Reaper: no PID found for %s", session_key)
                return
            # Snapshot child tree before killing — children in different
            # PGIDs survive killpg.
            raw_children = getattr(client, "_child_pids", None)
            child_pids: dict[int, int | None] = (
                dict(raw_children) if isinstance(raw_children, dict) else {}
            )
            for p in _get_child_pids(pid):
                if p not in child_pids:
                    child_pids[p] = _get_start_time(p)
            # Validate PID hasn't been recycled before killing.
            original_start = getattr(client, "_start_time", None)
            if original_start is None:
                logger.debug("Reaper: PID %d already dead for %s", pid, session_key)
                _kill_escaped_children(child_pids)
                return
            if not _is_our_child(pid, expected_start=original_start):
                logger.warning("Reaper: PID %d recycled for %s, skipping killpg", pid, session_key)
                stored = dict(raw_children) if isinstance(raw_children, dict) else {}
                _kill_escaped_children(stored)
                return
            # Kill the entire process group first
            logger.warning(
                "Reaper: killpg for PID %d (%d children) for %s",
                pid,
                len(child_pids),
                session_key,
            )
            try:
                os.killpg(os.getpgid(pid), signal.SIGKILL)
            except (ProcessLookupError, OSError):
                try:
                    os.kill(pid, signal.SIGKILL)
                except (ProcessLookupError, OSError):
                    pass
            _kill_escaped_children(child_pids)
        except Exception:
            logger.exception("Reaper: SIGKILL failed for %s", session_key)

    # ── Public API ──

    def add_job(
        self,
        name: str,
        action: dict | None = None,
        every_secs: int | None = None,
        at_ts: float | None = None,
        cron_expr: str | None = None,
        channel: str | None = None,
        thread_ts: str | None = None,
        delete_after_run: bool = False,
        created_by: str = "",
        silent: bool = False,
    ) -> ScheduleJob:
        """Add a new job. Provide one of ``every_secs``, ``at_ts``, or ``cron_expr``.

        ``action`` is the canonical Trigger action ``{"provider", "config"}``. It
        defaults to an empty ``invoke-agent`` action (configure it on the returned
        job) so callers that only set a schedule still get a valid agent job.

        ``silent`` suppresses cron auto-delivery (dashboard notify + channel post):
        the run still executes, but the result isn't pushed anywhere by the engine.
        Used by headless app crons, which have no owner conversation to deliver to.
        """
        action = normalize_action(action) if action else make_agent_action()
        if cron_expr:
            if not validate_cron_expr(cron_expr):
                raise ValueError(f"Invalid cron expression: {cron_expr}")
            schedule = ScheduleDefinition(kind="cron", cron_expr=cron_expr)
        elif every_secs:
            schedule = ScheduleDefinition(
                kind="every", every_secs=max(every_secs, _MIN_INTERVAL_SECS)
            )
        elif at_ts:
            schedule = ScheduleDefinition(kind="at", at_ts=at_ts)
        else:
            raise ValueError("Must provide every_secs, at_ts, or cron_expr")

        job = ScheduleJob(
            id=uuid.uuid4().hex[:8],
            name=name,
            action=action,
            schedule=schedule,
            channel=channel,
            thread_ts=thread_ts,
            enabled=True,
            created_ts=time.time(),
            delete_after_run=delete_after_run,
            created_by=created_by,
            silent=silent,
        )
        with self._file_lock():
            self._sync()
            self._jobs.append(job)
            self._save()
        self._arm_timer()
        logger.info("Added cron job '%s' (%s)", name, job.id)
        return job

    def update_job(self, job_id: str, **kwargs: Any) -> ScheduleJob | None:
        """Update fields on an existing job. Returns updated job or None if not found.

        Accepted kwargs: name, action, every_secs, cron_expr, channel, silent,
        skip_dates, timezone, thread_ts, strict_schedule. The job's *what-runs*
        is the canonical ``action`` — pass a full ``{"provider", "config"}`` dict
        to change the agent prompt/model/approval-mode or the command/script.
        """
        with self._file_lock():
            self._sync()
            for job in self._jobs:
                if job.id != job_id:
                    continue
                # Validate action if provided (canonicalizes + eager-validates spec)
                new_action = (
                    normalize_action(kwargs["action"])
                    if "action" in kwargs and kwargs["action"]
                    else None
                )
                # Validate before any mutations
                if (
                    "cron_expr" in kwargs
                    and kwargs["cron_expr"]
                    and "every_secs" in kwargs
                    and kwargs["every_secs"]
                ):
                    raise ValueError("Cannot specify both cron_expr and every_secs")
                if "cron_expr" in kwargs and kwargs["cron_expr"]:
                    if not validate_cron_expr(kwargs["cron_expr"]):
                        raise ValueError(f"Invalid cron expression: {kwargs['cron_expr']}")
                if "every_secs" in kwargs and kwargs["every_secs"]:
                    try:
                        val = int(kwargs["every_secs"])
                    except (ValueError, TypeError) as e:
                        raise ValueError(f"Invalid interval: {kwargs['every_secs']}") from e
                    if val < _MIN_INTERVAL_SECS:
                        raise ValueError(f"Interval must be >= {_MIN_INTERVAL_SECS}s, got {val}")
                if "name" in kwargs and kwargs["name"]:
                    job.name = kwargs["name"]
                if new_action is not None:
                    job.action = new_action
                if "channel" in kwargs:
                    job.channel = kwargs["channel"] or None
                if "silent" in kwargs:
                    job.silent = bool(kwargs["silent"])
                if "skip_dates" in kwargs:
                    job.skip_dates = kwargs["skip_dates"] or []
                if "timezone" in kwargs:
                    job.timezone = kwargs["timezone"] or ""
                if "strict_schedule" in kwargs:
                    job.strict_schedule = bool(kwargs["strict_schedule"])
                # Schedule changes (already validated above)
                if "cron_expr" in kwargs and kwargs["cron_expr"]:
                    job.schedule = ScheduleDefinition(kind="cron", cron_expr=kwargs["cron_expr"])
                elif "every_secs" in kwargs and kwargs["every_secs"]:
                    job.schedule = ScheduleDefinition(
                        kind="every", every_secs=int(kwargs["every_secs"])
                    )
                self._save()
                self._arm_timer()
                logger.info("Updated cron job %s", job_id)
                return job
        return None

    def remove_job(self, job_id: str) -> bool:
        """Remove a job by ID."""
        with self._file_lock():
            self._sync()
            before = len(self._jobs)
            self._jobs = [j for j in self._jobs if j.id != job_id]
            if len(self._jobs) < before:
                self._save()
                self._arm_timer()
                logger.info("Removed cron job %s", job_id)
                return True
        return False

    def enable_job(self, job_id: str, enabled: bool = True) -> bool:
        """Enable or disable a job by ID."""
        with self._file_lock():
            self._sync()
            for job in self._jobs:
                if job.id == job_id:
                    job.enabled = enabled
                    self._save()
                    self._arm_timer()
                    logger.info("%s cron job %s", "Enabled" if enabled else "Disabled", job_id)
                    return True
        return False

    def ack_job(self, job_id: str, summary: str) -> bool:
        """Acknowledge a cron notification — stores summary for future context."""
        with self._file_lock():
            self._sync()
            for job in self._jobs:
                if job.id == job_id:
                    job.acked_items.append(summary[:500])
                    # Keep only last 20 acks
                    job.acked_items = job.acked_items[-20:]
                    self._save()
                    return True
        return False

    def unack_job(self, job_id: str) -> bool:
        """Remove the most recent acked item from a cron job."""
        with self._file_lock():
            self._sync()
            for job in self._jobs:
                if job.id == job_id and job.acked_items:
                    job.acked_items.pop()
                    self._save()
                    return True
        return False

    # ── Active session tracking ──

    def register_active_session_key(self, job_id: str, session_key: str) -> None:
        """Record the session key used by the current run of ``job_id``.

        The dispatcher calls this at the start of each run. The reaper reads
        it when force-killing a timed-out job. Overwrites any existing entry
        for the same job_id (prior run already ended or was reaped).
        """
        self._active_session_keys[job_id] = session_key

    def clear_active_session_key(self, job_id: str) -> None:
        """Clear the active session key for ``job_id``.

        Called by the dispatcher in its finally/cleanup path so the reaper
        falls back to the stable key for the next (not yet started) run.
        """
        self._active_session_keys.pop(job_id, None)

    def get_active_session_key(self, job_id: str) -> str | None:
        """Return the active session key for ``job_id``, or None if unregistered."""
        return self._active_session_keys.get(job_id)

    async def run_job(self, job_id: str, *, dry_run: bool = False) -> bool:
        """Manually trigger a job, then lock+merge results back to disk.

        Tags the run ``trigger="manual"`` (or ``"replay"`` for a dry run) for the
        run record, and refuses to double-fire a job already executing (so a rapid
        double-trigger from the UI/CLI doesn't run it twice).

        ``dry_run=True`` runs in observe-mode (T9): write-capable tools don't
        execute, so the run previews what WOULD happen against the job's CURRENT
        action with no side effects — useful to validate an edit before it next
        fires live. A dry run is NOT merged back to disk (it changes no real state).
        """
        self._sync()
        job = None
        for j in self._jobs:
            if j.id == job_id:
                job = j
                break
        if not job:
            return False
        if job.id in self._executing:
            return False  # already running — don't double-fire
        trigger = "replay" if dry_run else "manual"
        self._job_run_meta[job.id] = (time.time(), trigger)
        exec_started_at = time.time()
        job.dry_run = dry_run
        try:
            await self._execute(job)
            if not dry_run:
                self._merge_job_result(job)  # a dry run changes no real state
        finally:
            job.dry_run = False
            self._job_run_meta.pop(job.id, None)
            try:
                await self._record_run(job, exec_started_at, trigger)
            except Exception:
                logger.debug("Failed to record %s run for '%s'", trigger, job.name, exc_info=True)
        return True

    async def replay_run(self, job_id: str) -> bool:
        """Dry-run replay (T9): re-fire a job's CURRENT action in observe-mode.

        Thin alias for ``run_job(dry_run=True)`` — write-capable tools don't
        execute, the run is tagged ``trigger="replay"``, and nothing is merged to
        disk. Lets a user preview what a Prompt/Workflow edit would do before it
        next fires live, without side effects."""
        return await self.run_job(job_id, dry_run=True)

    # ── Execution-run history (ScheduleRun sub-entity) ──

    async def _record_run(self, job: ScheduleJob, started_at: float, trigger: str) -> None:
        """Append a ScheduleRun for a completed execution (any mode)."""
        finished_at = time.time()
        if job.last_status == "error":
            status = "failure"
            if (job.last_error or "").startswith("Timed out"):
                status = "timeout"
        elif job.last_outcome == "launched":
            # The action only kicked off a background turn — honest status: we
            # launched it, we don't yet know if the work succeeded (T7).
            status = "launched"
        else:
            status = "success"
        run = ScheduleRun(
            job_id=job.id,
            trigger=trigger,
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=int(max(0.0, finished_at - started_at) * 1000),
            status=status,
            summary=(job.last_result or job.last_error or "")[:200],
            trace=job.last_result or "",
            error=job.last_error or "",
        )
        await self._run_store.append(run)
        if self._push_refresh:
            try:
                self._push_refresh("cron_history")
            except Exception:
                logger.debug("push_refresh callback failed", exc_info=True)

    async def list_runs(
        self, job_id: str, offset: int = 0, limit: int = 10
    ) -> tuple[list[dict[str, Any]], int]:
        """Return (runs, total) for one job — shaped like TaskProvider.list_tasks."""
        return await self._run_store.list_for_job(job_id, offset, limit)

    async def list_all_runs(
        self, offset: int = 0, limit: int = 20, job_id: str | None = None
    ) -> tuple[list[dict[str, Any]], int]:
        """Return (runs, total) across all jobs (from the index)."""
        return await self._run_store.list_all(offset, limit, job_id)

    async def get_run(self, job_id: str, run_id: str) -> dict[str, Any] | None:
        """Return one full run record (with trace), or None."""
        return await self._run_store.get_run(job_id, run_id)

    async def delete_runs(self, job_id: str) -> None:
        """Drop a job's run history (called when the job is deleted)."""
        await self._run_store.delete_for_job(job_id)

    def last_run_status(self, job_id: str) -> str:
        """The newest run record's status for ``job_id`` ("" if none) — sync.

        The PERSISTENT, honest last-run status (success | failure | timeout |
        launched), surviving restarts. Used by the trigger serializer so the UI
        badge reflects the real run outcome rather than ``job.last_status`` (which
        is only "ok"/"error" — a fire-and-forget run that launched a background
        turn shows "ok" there, overstating it). Reads the per-job JSONL directly
        (newest-first), so it's cheap enough for the list serializer."""
        try:
            rows, _ = self._run_store._list_for_job_sync(job_id, 0, 1)
            return str(rows[0].get("status", "")) if rows else ""
        except Exception:
            return ""

    def is_running(self, job_id: str) -> bool:
        """True if a run is in flight for this job."""
        return job_id in self._executing

    def running_since(self, job_id: str) -> float | None:
        """Epoch start of the in-flight run for this job, or None."""
        return self._job_start_times.get(job_id)

    def set_refresh_callback(self, cb: "Callable[[str], None] | None") -> None:
        """Register a callback to hint dashboard clients to refresh a view."""
        self._push_refresh = cb

    def list_jobs(self, include_disabled: bool = False) -> list[ScheduleJob]:
        """List jobs, optionally including disabled ones."""
        self._sync()
        if include_disabled:
            return list(self._jobs)
        return [j for j in self._jobs if j.enabled]

    def status(self) -> dict[str, Any]:
        """Service status summary."""
        return {
            "running": self._running,
            "jobs": len(self._jobs),
            "enabled": sum(1 for j in self._jobs if j.enabled),
        }

    # ── Timer ──

    def _next_wake_secs(self) -> float | None:
        """Compute seconds until the next job should fire."""
        now = time.time()
        delays: list[float] = []
        for job in self._jobs:
            if not job.enabled or job.id in self._executing:
                continue
            if job.schedule.kind == "every" and job.schedule.every_secs:
                last = job.last_run_ts or job.created_ts
                next_run = last + job.schedule.every_secs
                delays.append(max(0.0, next_run - now))
            elif job.schedule.kind == "at" and job.schedule.at_ts:
                delays.append(max(0.0, job.schedule.at_ts - now))
            elif job.schedule.kind == "cron":
                # Poll every _TIMER_POLL_SECS for cron expressions
                delays.append(_TIMER_POLL_SECS)
        return min(delays) if delays else None

    def _effective_delay(self) -> float:
        """Compute the actual timer delay, capped at poll interval.

        Ensures the timer always wakes within _TIMER_POLL_SECS to _sync()
        externally-added jobs, even when the next job is far in the future.
        """
        delay = self._next_wake_secs()
        if delay is None:
            return _TIMER_POLL_SECS
        return min(delay, _TIMER_POLL_SECS)

    def _arm_timer(self) -> None:
        if self._timer_task and not self._timer_task.done():
            self._timer_task.cancel()
        if not self._running:
            return
        delay = self._effective_delay()

        logger.debug("Cron: next timer in %.1fs", delay)

        async def _tick() -> None:
            try:
                await asyncio.wait_for(shutdown_event.wait(), timeout=delay)
                return  # shutdown signaled
            except asyncio.TimeoutError:
                pass  # normal wake-up
            if self._running:
                try:
                    await self._on_timer()
                except Exception:
                    logger.exception("Cron timer error — will re-arm")
                finally:
                    # Always re-arm, even after errors
                    if self._running:
                        self._arm_timer()

        self._timer_task = asyncio.create_task(_tick())

    async def _on_timer(self) -> None:
        """Fire due jobs as independent tasks (non-blocking)."""
        with self._file_lock():
            self._sync()
            now = time.time()
            due = [
                j
                for j in self._jobs
                if j.enabled and j.id not in self._executing and self._is_due(j, now)
            ]

        if not due:
            return

        # Fire each job independently — one hung job never blocks others.
        for j in due:
            self._executing.add(j.id)
            task = asyncio.create_task(self._run_job_isolated(j))
            self._running_tasks[j.id] = task

    async def _run_job_isolated(self, job: ScheduleJob) -> None:
        """Execute a single job and merge results back to disk."""
        scheduled_ts = time.time()  # Capture due-time before jitter to prevent drift
        self._job_start_times[job.id] = scheduled_ts
        # Trigger tag for the run record ("manual" set by run_job, else scheduled).
        _meta = self._job_run_meta.pop(job.id, None)
        trigger = _meta[1] if _meta else "scheduled"
        # Apply jitter to spread execution unless strict_schedule is set
        jitter = self._compute_jitter(job)
        self._job_jitter[job.id] = jitter
        if jitter > 0:
            logger.debug("Cron: applying %.0fs jitter to job '%s'", jitter, job.name)
            await asyncio.sleep(jitter)
        exec_started_at = time.time()  # after jitter — actual execution start
        try:
            await self._execute_with_timeout(job)
        finally:
            self._job_start_times.pop(job.id, None)
            self._job_jitter.pop(job.id, None)
            reaped = job.id in self._reaped_jobs
            self._reaped_jobs.discard(job.id)
            self._executing.discard(job.id)
            self._running_tasks.pop(job.id, None)
            # For 'every' jobs, use scheduled_ts to prevent cumulative drift
            if not reaped and job.schedule.kind == "every":
                job.last_run_ts = scheduled_ts
            if not reaped:
                try:
                    self._merge_job_result(job)
                except Exception:
                    logger.exception("Failed to merge result for job '%s'", job.name)
            # Record the run as a ScheduleRun sub-entity (all execution modes,
            # since this is above the on_job dispatch). Guarded so a history
            # failure never breaks job execution.
            try:
                await self._record_run(job, exec_started_at, trigger)
            except Exception:
                logger.debug("Failed to record run for job '%s'", job.name, exc_info=True)

    @staticmethod
    def _jitter_offset(job_id: str, window: float) -> float:
        """A stable jitter offset in ``[0, window)``, derived from the job id.

        Deterministic (not ``random``): the same job always lands in the same
        sub-window slot, reproducible across restarts. That is what actually
        de-correlates many jobs — a random offset re-rolls every fire, so two
        jobs can still collide on any given fire and a restart reshuffles
        everything; an id-derived offset spreads jobs into stable, distinct slots
        once and keeps them there. Uses BLAKE2b (stable across processes, unlike
        Python's salted ``hash()``) → a fraction → scaled to the window.
        """
        if window <= 0:
            return 0.0
        import hashlib

        digest = hashlib.blake2b((job_id or "").encode("utf-8"), digest_size=8).digest()
        fraction = int.from_bytes(digest, "big") / float(1 << 64)  # [0, 1)
        return fraction * window

    @classmethod
    def _compute_jitter(cls, job: ScheduleJob) -> float:
        """Return a deterministic jitter offset (seconds) based on schedule frequency.

        The offset is stable per job (id-derived, see :meth:`_jitter_offset`) and
        bounded by the same frequency-band windows as before:

        - strict_schedule=True or one-shot 'at' jobs: no jitter
        - Hourly (every <= 3600s or cron firing hourly): 0–20 min
        - Daily (every >= 86400s or cron firing daily): 0–2 hours
        - Everything else: 0–20 min (conservative default)
        """
        if job.strict_schedule:
            return 0.0
        sched = job.schedule
        if sched.kind == "at":
            return 0.0  # one-shot jobs fire at exact time
        if sched.kind == "every" and sched.every_secs:
            if sched.every_secs >= 86400:
                return cls._jitter_offset(job.id, _JITTER_DAILY_MAX)
            elif sched.every_secs >= 3600:
                return cls._jitter_offset(job.id, _JITTER_HOURLY_MAX)
            else:
                return 0.0  # sub-hourly jobs shouldn't be jittered
        if sched.kind == "cron" and sched.cron_expr:
            parts = sched.cron_expr.split()
            if len(parts) == 5:
                # Sub-hourly cron (minute field has / or , or is wildcard): no jitter
                if "/" in parts[0] or "," in parts[0] or parts[0] == "*":
                    return 0.0
                # Single literal hour (e.g., "0 3 * * *") = truly daily/weekly
                if parts[1].isdigit():
                    return cls._jitter_offset(job.id, _JITTER_DAILY_MAX)
                # Multi-hour patterns (*/2, 1,13) or wildcard = hourly jitter
                if parts[1] != "*":
                    return cls._jitter_offset(job.id, _JITTER_HOURLY_MAX)
            return cls._jitter_offset(job.id, _JITTER_HOURLY_MAX)
        return 0.0

    @staticmethod
    def _is_due(job: ScheduleJob, now: float) -> bool:
        if job.schedule.kind == "every" and job.schedule.every_secs:
            last = job.last_run_ts or job.created_ts
            if now < last + job.schedule.every_secs:
                return False
        elif job.schedule.kind == "at" and job.schedule.at_ts:
            if now < job.schedule.at_ts:
                return False
        elif job.schedule.kind == "cron" and job.schedule.cron_expr:
            tz = _job_tz(job)
            dt = datetime.fromtimestamp(now, tz=tz)
            if not cron_expr_matches(job.schedule.cron_expr, dt):
                return False
            # Don't re-fire within the same UTC minute (immune to DST ambiguity)
            if job.last_run_ts and int(job.last_run_ts) // 60 == int(now) // 60:
                return False
        else:
            return False
        # Skip dates check (evaluated in job's local timezone, applies to all schedule types)
        if job.skip_dates:
            local_date = datetime.fromtimestamp(now, _job_tz(job)).strftime("%Y-%m-%d")
            if local_date in job.skip_dates:
                return False
        return True

    async def _execute_with_timeout(self, job: ScheduleJob) -> None:
        """Execute a job with a timeout guard."""
        timeout = job.timeout_secs if 1 <= job.timeout_secs <= 86400 else _JOB_TIMEOUT_SECS
        try:
            await asyncio.wait_for(self._execute(job), timeout=timeout)
        except asyncio.TimeoutError:
            # NB: Timeout bypasses _cron_callback's except block entirely —
            # which also means it bypasses all channel notification logic. From
            # the user's perspective, timeouts are silent (log + dashboard
            # status update only). Adding a timeout channel alert is a separate
            # feature and is intentionally out of scope for failure dedup.
            # Clear failure dedup state so a subsequent real error isn't
            # suppressed as a dup of the pre-timeout failure.
            job.last_status = "error"
            job.last_error = f"Timed out after {timeout}s"
            job.last_run_ts = time.time()
            job.last_failure_hash = ""
            job.last_failure_at = 0.0
            job.consecutive_failures = 0
            logger.error("Cron job '%s' timed out after %ds", job.name, timeout)

    async def _execute(self, job: ScheduleJob) -> None:
        """Run the job callback and update runtime fields (last_run_ts, last_status)."""
        logger.info("Cron: executing '%s' (%s)", job.name, job.id)
        # Reset the per-run status + outcome so a prior run can't leak in. The
        # status is seeded to a sentinel meaning "the callback has not reported a
        # verdict yet"; the action path (_run_action_job) overwrites it with its
        # own "ok"/"error", while the agent path leaves it untouched. We default to
        # "ok" ONLY when the sentinel survived — so a failed action's "error" is no
        # longer CLOBBERED by an unconditional "ok" (the honest-status bug T7 set
        # out to kill: a failed run recorded as success).
        job.last_outcome = ""
        job.last_status = _STATUS_PENDING
        try:
            if self._on_job:
                await self._on_job(job)
            if job.last_status == _STATUS_PENDING:
                job.last_status = "ok"
                job.last_error = None
        except Exception as exc:
            job.last_status = "error"
            job.last_error = str(exc)
            logger.error("Cron job '%s' failed: %s", job.name, exc)

        job.last_run_ts = time.time()

        # One-shot "at" jobs without delete_after_run: disable instead of delete
        if job.schedule.kind == "at" and not job.delete_after_run:
            job.enabled = False

    def _merge_job_result(self, job: ScheduleJob) -> None:
        """Merge a single job's runtime state back to disk."""
        with self._file_lock():
            self._sync()
            by_id = {j.id: j for j in self._jobs}
            if job.id in by_id:
                by_id[job.id].last_run_ts = job.last_run_ts
                by_id[job.id].last_status = job.last_status
                by_id[job.id].last_error = job.last_error
                by_id[job.id].enabled = job.enabled
                by_id[job.id].last_result = job.last_result
                by_id[job.id].last_posted_hash = job.last_posted_hash
                by_id[job.id].consecutive_dupes = job.consecutive_dupes
                by_id[job.id].last_posted_at = job.last_posted_at
                by_id[job.id].last_failure_hash = job.last_failure_hash
                by_id[job.id].last_failure_at = job.last_failure_at
                by_id[job.id].consecutive_failures = job.consecutive_failures
            if job.delete_after_run:
                self._jobs = [j for j in self._jobs if j.id != job.id]
            self._save()

    # ── Persistence ──

    @contextmanager
    def _file_lock(self) -> Iterator[None]:
        """Cross-process advisory lock on the cron store.

        Uses fcntl.flock for cross-process locking.
        """
        self._dir.mkdir(parents=True, exist_ok=True)
        lock = self._dir / ".crons.lock"
        fd = lock.open("w")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            fd.close()

    def _sync(self) -> None:
        """Reload from disk if the file was modified externally."""
        if not self._path.exists():
            return
        try:
            mtime = self._path.stat().st_mtime
        except OSError:
            return
        if mtime > self._last_mtime:
            logger.info("Cron file changed externally, reloading")
            self._load()

    def _load(self) -> None:
        """Deserialize jobs from crons.json and record mtime for sync tracking."""
        if not self._path.exists():
            self._jobs = []
            self._last_mtime = 0.0
            return
        try:
            self._last_mtime = self._path.stat().st_mtime
            data = json.loads(self._path.read_text())
            self._jobs = [
                ScheduleJob(
                    id=j["id"],
                    name=j["name"],
                    action=_action_from_record(j),
                    schedule=ScheduleDefinition(
                        kind=j["schedule"]["kind"],
                        every_secs=j["schedule"].get("every_secs"),
                        at_ts=j["schedule"].get("at_ts"),
                        cron_expr=j["schedule"].get("cron_expr"),
                    ),
                    channel=j.get("channel"),
                    thread_ts=j.get("thread_ts"),
                    enabled=j.get("enabled", True),
                    last_run_ts=j.get("last_run_ts"),
                    last_status=j.get("last_status"),
                    last_error=j.get("last_error"),
                    created_ts=j.get("created_ts", 0.0),
                    delete_after_run=j.get("delete_after_run", False),
                    last_result=j.get("last_result"),
                    context_enabled=j.get("context_enabled", False),
                    acked_items=j.get("acked_items", []),
                    created_by=j.get("created_by", ""),
                    silent=j.get("silent", False),
                    session_key=j.get("session_key", ""),
                    last_posted_hash=j.get("last_posted_hash", ""),
                    consecutive_dupes=j.get("consecutive_dupes", 0),
                    last_posted_at=j.get("last_posted_at", 0.0),
                    last_failure_hash=j.get("last_failure_hash", ""),
                    last_failure_at=j.get("last_failure_at", 0.0),
                    consecutive_failures=j.get("consecutive_failures", 0),
                    skip_dates=j.get("skip_dates", []),
                    timezone=j.get("timezone", ""),
                    persistent_session=j.get("persistent_session", True),
                    agent_sequence=j.get("agent_sequence", []),
                    env=j.get("env", {}),
                    timeout_secs=j.get("timeout_secs", _JOB_TIMEOUT_SECS),
                    strict_schedule=j.get("strict_schedule", False),
                )
                for j in data.get("jobs", [])
            ]
        except (json.JSONDecodeError, KeyError) as exc:
            logger.warning("Failed to load cron store: %s", exc)
            self._jobs = []
            self._last_mtime = 0.0

        # Restore timers for active jobs loaded from disk
        if self._running:
            restored = sum(1 for j in self._jobs if j.enabled)
            if restored:
                self._arm_timer()
                logger.info("Restored %d cron timer(s) from disk", restored)

    def _save(self) -> None:
        """Atomic write (tmp → rename) and update mtime tracking."""
        self._dir.mkdir(parents=True, exist_ok=True)
        data = {
            "version": _STORE_VERSION,
            "jobs": [
                {
                    "id": j.id,
                    "name": j.name,
                    "action": j.action,
                    "schedule": asdict(j.schedule),
                    "channel": j.channel,
                    "thread_ts": j.thread_ts,
                    "enabled": j.enabled,
                    "last_run_ts": j.last_run_ts,
                    "last_status": j.last_status,
                    "last_error": j.last_error,
                    "created_ts": j.created_ts,
                    "delete_after_run": j.delete_after_run,
                    "last_result": j.last_result,
                    "context_enabled": j.context_enabled,
                    "acked_items": j.acked_items,
                    "created_by": j.created_by,
                    "silent": j.silent,
                    "session_key": j.session_key,
                    "last_posted_hash": j.last_posted_hash,
                    "consecutive_dupes": j.consecutive_dupes,
                    "last_posted_at": j.last_posted_at,
                    "last_failure_hash": j.last_failure_hash,
                    "last_failure_at": j.last_failure_at,
                    "consecutive_failures": j.consecutive_failures,
                    "skip_dates": j.skip_dates,
                    "timezone": j.timezone,
                    "persistent_session": j.persistent_session,
                    "agent_sequence": j.agent_sequence,
                    "env": j.env,
                    "timeout_secs": j.timeout_secs,
                    "strict_schedule": j.strict_schedule,
                }
                for j in self._jobs
            ],
        }
        atomic_write(self._path, json.dumps(data, indent=2))
        self._last_mtime = self._path.stat().st_mtime

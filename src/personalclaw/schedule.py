"""Legacy schedule MODEL + the shared schedule helpers.

**`ScheduleService` is gone (S112).** Every consumer moved to the unified trigger store across
S99–S111 — reads, writes, the timer, the reaper, status, dispatch, run records — and the class was
down to two boot lifecycle calls whose only load-bearing work was `ScheduleRunStore.rotate_all()`,
which that store owns directly.

What survives here, and why:

* `ScheduleJob` / `ScheduleDefinition` — the shape `crons.json` is written in. The boot migration
  (`triggers/migrate.py`) still READS that file, and §6 keeps it on disk read-only so
  `automation verify-migration` can diff both sides, so the dataclasses that describe it stay.
* `format_schedule` / `get_local_tz` / `compute_next_run_ts` / `validate_cron_expr` /
  `cron_expr_matches` — shared formatters and cron helpers. `schedule_view.describe_cadence`
  DELEGATES to `format_schedule` on purpose, so a second formatter can never drift from it.
* `make_*_action` / `normalize_action` / `SCHEDULE_VARS` / `build_schedule_session_context` — the
  canonical action shape and the session-context builder the trigger executor still uses.

Nothing here fires anything. The clock engine is `triggers/loop.py`, the store is
`triggers/store.py`, the reaper is `triggers/reaper.py`, and run history is
`schedule_history.ScheduleRunStore`.
"""

import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from personalclaw.autonomous_framing import with_autonomous_framing

try:
    from cron_descriptor import Options, get_description  # type: ignore[import-untyped]
except ImportError:
    Options = None  # type: ignore[assignment,misc]
    get_description = None  # type: ignore[assignment]
from croniter import croniter  # type: ignore[import-untyped]

from personalclaw.config.loader import AppConfig, config_dir

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

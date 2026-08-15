"""Subagent orchestration — spawn isolated background agents.

Each subagent gets its own LLM session (via SessionManager) with a
focused system prompt.  Results are announced back to the caller via
a callback.  Max concurrent limit prevents resource exhaustion.

No spawn recursion: subagents cannot spawn other subagents.
"""

import asyncio
import logging
import os
import signal
import subprocess
import sys
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from personalclaw.config.loader import AppConfig
from personalclaw.context import ContextBuilder
from personalclaw.hooks import TOOL_AUTO_APPROVE, TOOL_DENY, fire_tool_hooks, safe_read_file
from personalclaw.llm.base import (
    EVENT_COMPLETE,
    EVENT_PERMISSION_REQUEST,
    EVENT_TEXT_CHUNK,
    EVENT_TOOL_CALL,
    LLMEvent,
)
from personalclaw.security import redact_credentials, redact_exfiltration_urls
from personalclaw.sel import sel
from personalclaw.session import SessionManager
from personalclaw.session_workspace import result_path as _ws_result_path
from personalclaw.stats import Stats
from personalclaw.subagent_persistence import (
    _agent_dir,
    _cleanup_session_files_sync,
    create_agent_folder,
    delete_agent_folder,
    list_orphans,
    prune_stale_tombstones,
    update_state,
    write_result_chunk,
    write_tombstone,
)
from personalclaw.textfmt import extract_options
from personalclaw.validation import _AGENT_NAME_RE

logger = logging.getLogger(__name__)


_MAX_CONCURRENT = 3

# Auto-size bounds (used when max_subagents == 0). Floor 2 so "auto" always beats
# a single-agent Pi-class host; ceiling 8 because past the SessionManager's
# 4-concurrent cold-start semaphore the marginal throughput falls off while OOM
# risk climbs. The per-agent memory budget reuses spawn_min_memory_gb (the
# headroom the spawn gate already requires per subagent), so the auto cap stays
# consistent with the existing admission control.
_AUTO_FLOOR = 2
_AUTO_CEILING = 8
_CPU_HEADROOM = 2  # leave cores for the gateway + OS


def _total_memory_gb() -> float:
    """Total host RAM in GB, cross-platform; 0.0 if it can't be determined.

    Mirrors the detection in ``dashboard/handlers_system.py`` (``sysctl
    hw.memsize`` on macOS, ``/proc/meminfo`` ``MemTotal`` on Linux) so the two
    agree on host facts without a third-party dependency.
    """
    try:
        if sys.platform == "darwin":
            out = (
                subprocess.check_output(["sysctl", "-n", "hw.memsize"], timeout=2).decode().strip()
            )
            return int(out) / (1024**3)
        if sys.platform == "linux":
            for line in safe_read_file("/proc/meminfo").splitlines():
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) / (1024**2)
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return 0.0
    return 0.0


def resolve_max_subagents(configured: int, per_agent_gb: float = 4.0) -> int:
    """Resolve the subagent concurrency cap, auto-sizing when ``configured == 0``.

    An explicit non-zero ``configured`` is returned unchanged. ``0`` means
    "auto": size from host CPU and total memory, taking the lower of the two
    budgets and clamping to ``[_AUTO_FLOOR, _AUTO_CEILING]`` — a big host gets
    more parallelism, a small one is protected from OOM. ``per_agent_gb`` is the
    memory budgeted per concurrent subagent (defaults to the spawn gate's
    ``spawn_min_memory_gb``). When host facts are unavailable (unknown platform,
    unreadable meminfo) we fall back to the historical fixed cap.
    """
    if configured > 0:
        return configured
    cpu = os.cpu_count() or 0
    total_gb = _total_memory_gb()
    if cpu <= 0 or total_gb <= 0.0:
        logger.info(
            "subagent auto-size: host facts unavailable (cpu=%s, mem=%.1fGB) — "
            "falling back to fixed cap %d",
            cpu,
            total_gb,
            _MAX_CONCURRENT,
        )
        return _MAX_CONCURRENT
    cpu_based = max(1, cpu - _CPU_HEADROOM)
    mem_based = max(1, int(total_gb // max(per_agent_gb, 0.5)))
    resolved = max(_AUTO_FLOOR, min(_AUTO_CEILING, min(cpu_based, mem_based)))
    logger.info(
        "subagent auto-size: %d (cpu=%d→%d, mem=%.1fGB/%.1f→%d, bounds[%d,%d])",
        resolved,
        cpu,
        cpu_based,
        total_gb,
        per_agent_gb,
        mem_based,
        _AUTO_FLOOR,
        _AUTO_CEILING,
    )
    return resolved


def _validate_agent(requested: str) -> tuple[str, str]:
    """Validate an agent name against the configured agents in AppConfig.

    Returns ``(agent_name, error)``. An empty ``requested`` resolves to the
    default agent with no error (``("", "")``). A KNOWN agent returns its name.
    An UNKNOWN agent returns a TYPED error naming the valid agents — never a
    silent downgrade to the default (C1.3): a fan-out that named the wrong agent
    used to run entirely on ``personalclaw`` with nothing but a log line, so the
    caller must fail loudly and let the user fix the name.
    """
    if not requested:
        return "", ""
    from personalclaw.config.loader import AppConfig

    cfg = AppConfig.load()
    if requested in cfg.agents:
        return requested, ""
    available = sorted(cfg.agents.keys() - {"personalclaw", "personalclaw-orchestrator"})
    listed = ", ".join(available) if available else "(none configured)"
    return "", f"unknown agent {requested!r}; valid agents: {listed}"


def _redact(text: str) -> str:
    """Redact credentials and exfiltration URLs from text."""
    text, _ = redact_exfiltration_urls(text)
    text, _ = redact_credentials(text)
    return text


_MAX_DONE_RESULT_LEN = 50_000  # cap subagent_done payload to avoid bloating WS frames


def _done_result(text: str) -> str:
    """Redact + cap result for inclusion in subagent_done event."""
    if not text:
        return ""
    redacted = _redact(text)
    if len(redacted) <= _MAX_DONE_RESULT_LEN:
        return redacted
    return "…(truncated)\n" + redacted[-_MAX_DONE_RESULT_LEN:]


_TIMEOUT_SECS = 1800  # 30 minutes
_TURN_LIMIT = 100
_REAPER_INTERVAL = 60  # seconds between reaper sweeps
_RESET_TIMEOUT = 30.0  # max seconds for session reset in finally block
_ON_DONE_TIMEOUT = 1200.0  # outer cap: max total seconds for semaphore wait + injection
INJECTION_TIMEOUT = 300.0  # inner cap: max seconds for a single stream_and_collect call

# Consecutive child failures within one fan-out before its breaker trips (C1.4).
# Mirrors ``session._CIRCUIT_BREAKER_THRESHOLD`` — the per-child ``subagent:<id>``
# session is released before it can fail, so the breaker must key on the FAN-OUT.
_CIRCUIT_BREAKER_THRESHOLD = 5


def _fanout_key(info: "SubagentInfo") -> str:
    """The concurrency-lane / breaker / budget key for a spawn.

    A workflow run scopes by its ``parent_run`` (``workflow:<run_id>``); a chat
    fan-out scopes by the parent session key. Empty only for a truly parentless
    lone spawn, which shares the ``""`` lane (bounded by the global cap regardless).
    """
    return info.parent_run or info.parent_session_key or ""


def _timeout_context(info: "SubagentInfo", *, include_elapsed: bool = True) -> str:
    """Build a human-readable context string for timeout errors."""
    parts = [f"turn {info.turns}/{info.max_turns}"]
    if info.last_tool:
        parts.append(f"last tool: {_redact(info.last_tool)}")
    if include_elapsed:
        elapsed = info.elapsed if info.elapsed > 0 else (time.time() - info.started)
        parts.append(f"elapsed: {int(elapsed)}s")
    return " | ".join(parts)


def check_memory_available(min_gb: float = 4.0, path: str = "/proc/meminfo") -> tuple[bool, float]:
    """Check if enough memory is available to spawn a subagent.

    Reads /proc/meminfo MemAvailable via ``safe_read_file`` (hooks.py)
    and compares against *min_gb*.
    Returns (ok, available_gb).  On read failure returns (True, -1.0)
    to avoid blocking spawns on non-Linux systems.
    """
    try:
        text = safe_read_file(path)
    except PermissionError:
        logger.warning("Memory check blocked: sensitive path %s", path)
        return (True, -1.0)
    except OSError:
        return (True, -1.0)
    try:
        for line in text.splitlines():
            if line.startswith("MemAvailable:"):
                kb = int(line.split()[1])
                avail = kb / (1024 * 1024)
                return (avail >= min_gb, round(avail, 2))
    except (ValueError, IndexError):
        return (True, -1.0)
    return (True, -1.0)


def validate_cwd(cwd: str, allowed_roots: list[str]) -> tuple[str, str]:
    """Validate a caller-supplied ``cwd`` for ``subagent_run``.

    Resolves symlinks and verifies the path is an existing directory under at
    least one entry in ``allowed_roots``. Empty ``allowed_roots`` disables the
    feature — any non-empty ``cwd`` is rejected.

    Args:
        cwd: Caller-supplied absolute path (may contain ``~``).
        allowed_roots: Permitted root paths from config (may contain ``~``).

    Returns:
        ``(resolved_cwd, error)``. On success ``error`` is empty and
        ``resolved_cwd`` is the canonical absolute path (realpath-resolved).
        On failure ``error`` is a reason string and ``resolved_cwd`` is empty.
    """
    if not cwd:
        return ("", "")
    if not allowed_roots:
        return ("", "cwd override is disabled (subagent_cwd_allowed_roots is empty)")
    try:
        expanded = os.path.expanduser(cwd)
        if not os.path.isabs(expanded):
            return ("", "cwd must be an absolute path")
        resolved = os.path.realpath(expanded)
    except (OSError, ValueError) as exc:
        return ("", f"cwd resolution failed: {exc}")
    if not os.path.isdir(resolved):
        return ("", "cwd does not exist or is not a directory")
    resolved_roots = [os.path.realpath(os.path.expanduser(r)) for r in allowed_roots]
    for root in resolved_roots:
        if resolved == root or resolved.startswith(root + os.sep):
            return (resolved, "")
    return ("", f"cwd is not under any allowed root: {allowed_roots}")


_SYSTEM_PREFIX = (
    "You are a focused sub-agent. Complete the following task concisely. "
    "Do NOT create other agents. Report your result directly.\n"
    "IMPORTANT: Do NOT narrate your own process, failures, retries, or "
    "orchestration decisions. The user does not care how you got the answer. "
    "Only output meaningful, actionable results. Never output greetings or filler.\n\n"
)


# Capability class for a spawn's tool surface (AUTONOMY-GUARDRAILS §4.1). The values mirror the
# workflow-leaf vocabulary (``workflows.batch_compile.Capability``) so a research SUBAGENT and a
# research batch LEAF mean the same thing — write/execute tools are denied — and there is one
# write-tool policy, not two that drift. The denial is enforced at the tool-approval layer (the
# ``_run_inner`` permission loop below), NOT by handing the worker a filtered ``.tools`` list: the
# native ACP runtime does not enforce such a list (WF2LEA-6 measured this), so a research spawn that
# was only *told* its tools would still be able to call a write tool. The seam that actually answers
# the tool call is the only seam where the class can be made true.
CAPABILITY_RESEARCH = "research"
CAPABILITY_MUTATING = "mutating"


def resolve_capability_class(*, capability_class: str, approval_mode: str) -> str:
    """The effective capability class for a spawn (§4.1).

    An explicit ``research``/``mutating`` always wins — a caller that decided is obeyed. An unset
    class defaults BY CONSTRUCTION: ``research`` (read-only) for an AUTO-FIRED spawn
    (``approval_mode == "auto"`` — a cron/unattended run with no human watching), ``mutating`` for a
    human-watched spawn. This is the plan's "auto-fired runs default read-only" rule: write/execute
    on an unattended run is a creation-time grant a caller passes explicitly (``capability_class=
    "mutating"``), never a capability an auto-fired run acquires by default.
    """
    cc = (capability_class or "").strip().lower()
    if cc in (CAPABILITY_RESEARCH, CAPABILITY_MUTATING):
        return cc
    return CAPABILITY_RESEARCH if approval_mode == "auto" else CAPABILITY_MUTATING


@dataclass
class SubagentInfo:
    """Metadata for a running subagent."""

    id: str
    task: str
    started: float = field(default_factory=time.time)
    done: bool = False
    result: str = ""
    result_path: str = ""
    error: str = ""
    parent_session_key: str = ""
    agent: str = ""
    approval_mode: str = ""  # "auto" to skip tool approvals in the subagent session
    # Tool-capability class (§4.1): "research" (read-only — write/execute tools default-denied at
    # the approval layer) or "mutating" (full grant). Empty resolves by construction via
    # ``resolve_capability_class`` — auto-fired spawns default to research so an unattended run
    # cannot write without an explicit creation-time grant.
    capability_class: str = ""
    dry_run: bool = False  # observe-mode: write-capable tools don't execute (T9 replay)
    silent: bool = False  # suppress completion notification (dashboard + channel)
    turns: int = 0
    last_tool: str = ""
    max_turns: int = 0
    reaped: bool = False
    streaming_text: str = ""
    elapsed: float = 0.0
    _raw_task: str = ""  # unredacted task for ACP agent execution prompt
    model: str = ""
    # Per-child token/cost accounting (COST-AND-TOKEN-OBSERVABILITY C2, subagent
    # write-site): carried onto the completion delivery so a fan-out's cost is
    # visible per child, not discarded at EVENT_COMPLETE.
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    # Optional subprocess cwd override. When set, the subagent ACP agent
    # process launches here instead of the default ``subagent_<id>`` sandbox, so
    # cwd-relative resource globs (``.personalclaw/steering/**/*.md``, ``AGENTS.md``)
    # resolve against this directory. Validated on spawn against
    # ``AgentConfig.subagent_cwd_allowed_roots``.
    cwd: str = ""
    # Sandbox provider name (EI-1): the isolation backend this subagent's ACP worker launches
    # through. ``none`` (the default builtin) composes the host path-sandbox + resource ceilings
    # with no further isolation; an installed ``sandbox`` app supplies a stronger container tier.
    sandbox: str = "none"
    # Per-leaf env for a compiled batch branch (WF2WOR-5 C2): the lineage keys plus the
    # capability flag the tool-handler seam reads, already secret-filtered by `leaf_env`. Carried
    # per-SESSION rather than through `os.environ` because leaves of one batch run concurrently in
    # one gateway process — a process-global flag would leak one leaf's posture onto its siblings.
    extra_env: dict[str, str] = field(default_factory=dict)
    _pid: int | None = None  # PID of ACP agent child process, for tombstone diagnostics
    # Fan-out identity (C1.4): the run a spawn belongs to. Empty for a lone spawn.
    # The run-scoped concurrency lane, the consecutive-failure breaker and the
    # run budget all key on ``parent_run or parent_session_key`` (see
    # ``_fanout_key``) so a workflow fan-out and a chat fan-out are each scoped.
    parent_run: str = ""
    # True while this spawn is waiting for a slot (C1.2). A queued spawn carries a
    # REAL, addressable id (this info) and its FULL parameter set, so it can be
    # polled and cancelled before it ever runs; the drain reuses this same info.
    queued: bool = False
    # Set when a spawn is stopped by the user / kill-fan-out / run-budget stop, so
    # the consecutive-failure breaker does NOT count an intentional stop as a
    # child failure.
    cancelled: bool = False
    _outcome_noted: bool = False  # guards double-counting in the breaker/meter


# Delivery callback (C1.1): a BATCH of completed subagents that all share one
# parent session, delivered in a SINGLE parent turn. Batching is the fix for the
# injection wall — N near-simultaneous completions used to each start a full
# parent turn, serialized behind the per-session ``Semaphore(1)``, so a burst lost
# most results. The manager coalesces per parent; the callback delivers the group
# once.
AnnounceCallback = Callable[[list[SubagentInfo]], Awaitable[None]]

# Event callback: (event_type, info, extra_data) -> None
SubagentEventCallback = Callable[[str, "SubagentInfo", dict], Awaitable[None]]


class ToolApprovalCallback(Protocol):
    async def __call__(self, event: LLMEvent, parent_session_key: str = "") -> bool:
        pass


class SpawnApprovalCallback(Protocol):
    async def __call__(
        self, request_id: str, description: str, parent_session_key: str = ""
    ) -> bool:
        pass


class SubagentManager:
    """Spawn and track isolated background agents."""

    def __init__(
        self,
        sessions: SessionManager,
        ctx_builder: ContextBuilder,
        on_done: AnnounceCallback | None = None,
        max_concurrent: int = _MAX_CONCURRENT,
        default_turn_limit: int = _TURN_LIMIT,
        default_timeout: int = _TIMEOUT_SECS,
        on_tool_approval: ToolApprovalCallback | None = None,
        on_tool_approval_factory: (
            Callable[["SubagentInfo"], Callable[[LLMEvent], Awaitable[bool]]] | None
        ) = None,
        on_spawn_approval: SpawnApprovalCallback | None = None,
        is_yolo: Callable[[], bool] | None = None,
        on_event: SubagentEventCallback | None = None,
        run_lane_cap: int = 0,
        delivery_coalesce_secs: float = 0.05,
    ):
        self._sessions = sessions
        self._ctx_builder = ctx_builder
        self._on_done = on_done
        self._max_concurrent = max_concurrent
        # Per-run concurrency lane cap (C1.4). 0 → the global cap (a lone run may use
        # every slot; the lane only bites once TWO fan-outs contend). A caller that
        # wants strict fairness sets it below ``max_concurrent``.
        self._run_lane_cap = run_lane_cap if run_lane_cap > 0 else max_concurrent
        # Delivery coalescing (C1.1): completions for one parent are buffered for a
        # short window and delivered as ONE batch turn, so a burst of N completions
        # is one parent turn, not N serialized behind the per-session Semaphore(1).
        self._delivery_coalesce_secs = max(0.0, delivery_coalesce_secs)
        self._pending_delivery: dict[str, list[SubagentInfo]] = {}
        self._delivery_tasks: dict[str, asyncio.Task] = {}  # type: ignore[type-arg]
        self._default_turn_limit = default_turn_limit
        self._default_timeout = default_timeout if default_timeout > 0 else _TIMEOUT_SECS
        self._on_tool_approval = on_tool_approval  # fallback for non-auto sessions
        self._on_tool_approval_factory = on_tool_approval_factory
        self._on_spawn_approval = on_spawn_approval
        self._is_yolo = is_yolo
        self._on_event = on_event
        self._running_count = 0
        # Per-fan-out running count (C1.4): keyed by ``_fanout_key(info)``. A run's
        # lane is capped independently so one wide fan-out cannot starve every other
        # run — the global ``_running_count`` still bounds the whole host.
        self._running_by_fanout: dict[str, int] = {}
        # Consecutive child-failure count per fan-out (C1.4). ``record_failure`` on
        # the fan-out KEY (not the ephemeral per-child session) trips the breaker at
        # ``_CIRCUIT_BREAKER_THRESHOLD`` — the per-child ``subagent:<id>`` session is
        # gone by the time it fails, so the session-level breaker could never trip.
        self._fanout_failures: dict[str, int] = {}
        # Fan-outs that must refuse further spawns, keyed to a TYPED reason string
        # (C1.4 breaker trip, C1.5 run-budget exceeded, kill-fan-out). A queued or
        # new spawn for one of these is refused with that reason rather than started.
        self._fanout_stops: dict[str, str] = {}
        self.hook_store: Any = None  # Optional ScriptHookStore, set by server.py
        self._agents: dict[str, SubagentInfo] = {}
        self._tasks: dict[str, asyncio.Task] = {}  # type: ignore[type-arg]
        # Queued spawns carry their addressable ``SubagentInfo`` (C1.2): a real id so
        # they can be polled/cancelled, and the FULL parameter set on the info itself
        # (approval_mode/model/silent/dry_run/parent_run) so a drained spawn keeps
        # them instead of silently dropping them and re-entering the interactive gate.
        self._queue: list[SubagentInfo] = []
        self._reaper_task: asyncio.Task | None = None  # type: ignore[type-arg]
        # Cache global approval_mode at init to avoid disk I/O on every
        # parentless spawn (cron, webhooks).
        try:
            self._global_approval_mode = AppConfig.load().agent.approval_mode
        except Exception:
            logger.warning(
                "Failed to load AppConfig for approval_mode; defaulting to interactive",
                exc_info=True,
            )
            self._global_approval_mode = ""

    @staticmethod
    async def _approve_and_log(
        client,
        request_id: str | int,
        session_key: str,
        event: LLMEvent,
        *,
        metadata: dict | None = None,
    ) -> None:
        await client.approve_tool(request_id)
        sel().log_tool_invocation(
            session_key=session_key,
            source="subagent",
            tool_name=event.title,
            tool_kind=event.tool_kind,
            outcome="auto_approved" if metadata and metadata.get("reason") else "approved",
            request_id=request_id,
            metadata=metadata,
        )

    @staticmethod
    async def _reject_and_log(
        client,
        request_id: str | int,
        session_key: str,
        event: LLMEvent,
        *,
        error: str | None = None,
        metadata: dict | None = None,
    ) -> None:
        await client.reject_tool(request_id)
        sel().log_tool_invocation(
            session_key=session_key,
            source="subagent",
            tool_name=event.title,
            tool_kind=event.tool_kind,
            outcome="denied" if error else "rejected",
            request_id=request_id,
            error=error or "",
            metadata=metadata,
        )

    def start_reaper(self) -> None:
        """Start the periodic reaper loop.  Call once after the event loop is running."""
        if self._reaper_task is None:
            self._reaper_task = asyncio.create_task(self._reaper_loop())
            # One-shot orphan reconciliation on startup
            self._reconcile_task = asyncio.create_task(self._reconcile_orphans())

    async def _reconcile_orphans(self) -> None:
        """Scan for orphaned agent folders from a prior gateway run.

        For each orphan (folder with state.json but no tombstone.json
        and not tracked in ``_agents``):
        - PID alive → SIGKILL, tombstone (gateway_restart)
        - PID dead + result → tombstone (gateway_restart, delivered)
        - PID dead + no result → tombstone (gateway_restart, notification_pending)
        """
        try:

            orphans = list_orphans()
            if not orphans:
                return
            logger.info("Reconciling %d orphaned subagent(s)", len(orphans))
            processed = 0
            for state in orphans:
                agent_id = state.get("id", "")
                if not agent_id or agent_id in self._agents:
                    continue  # tracked in current run, skip
                try:
                    pid = state.get("pid")
                    has_result = False
                    try:

                        rp = _agent_dir(agent_id) / "result.txt"
                        has_result = rp.exists() and rp.stat().st_size > 0
                    except OSError:
                        pass

                    recovery = "undeliverable"
                    if pid and self._is_pid_alive(pid):
                        # Use pid_recorded_at (when PID was actually written) instead of
                        # started (folder creation time) to avoid false negatives under load
                        pid_recorded_at = state.get("pid_recorded_at", state.get("started", 0))
                        if self._is_orphan_process(pid, pid_recorded_at):
                            self._kill_orphan_pid(pid)
                            try:
                                sel().log_tool_invocation(
                                    session_key=f"subagent:{agent_id}",
                                    source="subagent",
                                    tool_name="orphan_reconcile_kill",
                                    outcome="killed",
                                    metadata={"subagent_id": agent_id, "pid": pid},
                                )
                            except Exception:
                                logger.debug("SEL audit failed for orphan %s", agent_id)
                        recovery = "result_available" if has_result else "notification_pending"
                    elif has_result:
                        recovery = "result_available"
                    else:
                        recovery = "notification_pending"

                    try:
                        write_tombstone(
                            agent_id,
                            cause="gateway_restart",
                            recovery_action=recovery,
                            pid=pid,
                            turns=state.get("turns", 0),
                            last_tool=state.get("last_tool", ""),
                        )
                    except Exception:
                        logger.debug("Failed to tombstone orphan %s", agent_id, exc_info=True)

                    # Clean up session files for the orphaned agent
                    session_id = state.get("session_id", "")
                    if session_id:
                        try:
                            _cleanup_session_files_sync(session_id)
                        except Exception:
                            logger.debug(
                                "Session cleanup failed for orphan %s", agent_id, exc_info=True
                            )

                    logger.info(
                        "Reconciled orphan %s: recovery=%s, pid=%s, has_result=%s",
                        agent_id,
                        recovery,
                        pid,
                        has_result,
                    )
                    # Notify user about the orphaned agent
                    try:
                        await self._notify_orphan(agent_id, state, recovery, has_result)
                    except Exception:
                        logger.debug("Notification failed for orphan %s", agent_id, exc_info=True)
                except Exception:
                    logger.warning("Failed to reconcile orphan %s", agent_id, exc_info=True)

                # Rate limit: yield to event loop every 50 entries
                processed += 1
                if processed % 50 == 0:
                    await asyncio.sleep(0)
        except Exception:
            logger.warning("Orphan reconciliation failed", exc_info=True)

    @staticmethod
    def _is_pid_alive(pid: int) -> bool:
        """Check if a PID is still running."""
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True  # process exists, we just can't signal it
        except OSError:
            return False

    @staticmethod
    def _is_orphan_process(pid: int, spawned_at: float) -> bool:
        """Check if PID belongs to the original subagent (not a recycled PID).

        Compares /proc/{pid} creation time against the recorded spawn time.
        Returns False if the process was created after the agent was spawned
        (indicating PID reuse).
        """
        try:
            proc_stat = os.stat(f"/proc/{pid}")
            # Process was created before or around the time we spawned the agent
            return proc_stat.st_ctime <= spawned_at + 2.0
        except (FileNotFoundError, OSError):
            return False

    @staticmethod
    def _kill_orphan_pid(pid: int) -> None:
        """Best-effort SIGKILL of an orphaned process."""
        try:
            os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, OSError):
            pass

    async def _notify_orphan(
        self, agent_id: str, state: dict, recovery: str, has_result: bool
    ) -> None:
        """Notify user about an orphaned subagent.

        1. Try session injection if parent session still exists
        2. Fall back to the channel DM via send_message MCP tool
        """
        task_preview = (state.get("task", "") or "")[:100]
        parent_session = state.get("parent_session", "")

        result_path = str(_agent_dir(agent_id) / "result.txt")

        if has_result:
            msg = (
                f"[Subagent completion event]\n"
                f"Agent `{agent_id}` ⚠️ orphaned by gateway restart\n"
                f"Task: {task_preview}\n"
                f"Result saved at: `{result_path}`\n"
                f"Use the read tool to retrieve it."
            )
        else:
            msg = (
                f"[Subagent completion event]\n"
                f"Agent `{agent_id}` ❌ lost to gateway restart\n"
                f"Task: {task_preview}\n"
                f"No result was captured before the restart."
            )

        # Redact before any delivery path (injection or channel DM)
        msg = _redact(msg)

        # Try session injection first
        if parent_session.startswith("dashboard:"):
            try:
                injected = await self._try_inject_orphan_notification(parent_session, msg)
                if injected:
                    # Update tombstone recovery_action
                    try:
                        write_tombstone(
                            agent_id,
                            cause="gateway_restart",
                            recovery_action="delivered",
                            pid=state.get("pid"),
                            turns=state.get("turns", 0),
                            last_tool=state.get("last_tool", ""),
                        )
                    except Exception:
                        pass
                    return
            except Exception:
                logger.debug("Injection failed for orphan %s", agent_id, exc_info=True)

        # Fallback: channel DM
        try:
            await self._send_orphan_channel_dm(msg)
        except Exception:
            logger.debug("Channel DM fallback failed for orphan %s", agent_id, exc_info=True)

    async def _try_inject_orphan_notification(self, parent_session: str, msg: str) -> bool:
        """Try to inject a message into the parent dashboard session.

        Returns True if injection succeeded.
        """
        # This hooks into the existing dashboard session injection mechanism.
        # For now, return False to always fall through to the channel DM.
        # Full injection requires access to the dashboard session, which is
        # wired up at a higher level (gateway.py). This will be connected
        # when the notification plumbing is integrated.
        return False

    async def _send_orphan_channel_dm(self, msg: str) -> None:
        """Surface an orphan notification (best-effort).

        No channel client is wired at this layer, so the notification is logged
        at WARNING rather than DM'd.
        """
        logger.warning("Orphan notification (channel DM pending): %s", msg[:200])

    async def _reaper_loop(self) -> None:
        """Periodically force-kill subagents that exceed the timeout.

        Defense-in-depth: catches cases where ``asyncio.wait_for`` in
        ``_run()`` fails to fire (event-loop saturation, orphaned tasks,
        or ``reset()`` hanging in the finally block).
        """
        while True:
            await asyncio.sleep(_REAPER_INTERVAL)
            now = time.time()
            for agent_id, info in list(self._agents.items()):
                if info.done:
                    continue
                elapsed = now - info.started
                if elapsed <= self._default_timeout:
                    continue
                logger.warning(
                    "Reaper: subagent %s exceeded %ds (ran %.0fs), force-killing",
                    agent_id,
                    self._default_timeout,
                    elapsed,
                )
                try:
                    await self._force_reap(agent_id, info, elapsed)
                except Exception:
                    logger.exception("Reaper: failed to reap %s", agent_id)

            # Prune stale tombstoned folders (>7 days old)
            try:
                pruned = prune_stale_tombstones(max_age_days=7)
                if pruned:
                    logger.info("Reaper: pruned %d stale tombstone(s)", pruned)
            except Exception:
                logger.debug("Reaper: tombstone pruning failed", exc_info=True)

    async def _force_reap(self, agent_id: str, info: SubagentInfo, elapsed: float) -> None:
        """Kill a subagent's session process and mark it done."""
        session_key = f"subagent:{agent_id}"

        # Kill the process FIRST so the pipe unblocks, then cancel the task.
        try:
            await asyncio.wait_for(self._sessions.reset(session_key), timeout=_RESET_TIMEOUT)
        except asyncio.TimeoutError:
            logger.warning("Reaper: reset hung for %s, attempting SIGKILL", agent_id)
            self._sigkill_session(session_key)
        except Exception:
            logger.exception("Reaper: reset failed for %s", agent_id)

        task = self._tasks.pop(agent_id, None)
        if task and not task.done():
            task.cancel()

        if not info.done:
            info.done = True
            info.error = f"Reaped after {int(elapsed)}s (exceeded {self._default_timeout}s deadline) [{_timeout_context(info, include_elapsed=False)}]"  # noqa: E501
            self._dec_running(info)
            Stats().inc_subagent_failed()
            self._write_tombstone(info, "reaped")
            self._note_child_outcome(info)
        info.reaped = True
        self._maybe_clear_fanout(_fanout_key(info))

        # NOT best-effort (SH6.3). This write is the only record that the reaper
        # SIGKILLed a subagent, and it used to sit under `except Exception:
        # logger.exception(...)` — so a genuine audit-write failure (a read-only or full
        # home, a broken SEL chain) vanished into a log line while the kill itself
        # proceeded, and a test patching `sel` could see zero calls with nothing raised.
        # Every other audit write in this file is unguarded; this one now matches. The
        # caller (`_reaper_loop`) logs and moves to the next agent, so an unauditable
        # kill is reported instead of absorbed.
        sel().log_tool_invocation(
            session_key=session_key,
            source="subagent",
            tool_name="reaper_force_kill",
            outcome="reaped",
            metadata={
                "subagent_id": agent_id,
                "session_key": session_key,
                "elapsed": int(elapsed),
            },
        )

        try:
            self._sessions.release(session_key, cleanup=True)
        except Exception:
            logger.warning("Reaper: release failed for %s", agent_id, exc_info=True)

        # Fire WS event immediately so Activity Viewer updates
        # before the slow _on_done path (stream_and_collect).
        info.elapsed = elapsed
        await self._fire_event(
            "subagent_done",
            info,
            {
                "elapsed": elapsed,
                "error": _redact(info.error) if info.error else None,
                "task": _redact(info.task),
                "agent": _redact(info.agent),
                "result": _done_result(info.result),
            },
        )

        if self._on_done:
            # C1.1: reaped completions go through the SAME coalesced batch delivery,
            # and a delivery failure NEVER resets the parent — the orchestrator's
            # context is preserved and the failure is surfaced instead.
            self._enqueue_delivery(info)

        # Truncate retained text AFTER _on_done to preserve full output for result injection
        if len(info.streaming_text) > 10_000:
            info.streaming_text = info.streaming_text[:10_000] + "\n…(truncated)"

    def _sigkill_session(self, session_key: str) -> None:
        """Best-effort SIGKILL when graceful reset hangs.

        Uses killpg to kill the entire process group, then sweeps
        escaped children in different PGIDs (MCP servers).
        """
        try:
            from personalclaw.acp.client import (
                _get_child_pids,
                _get_start_time,
                _is_our_child,
                _kill_escaped_children,
            )

            session = self._sessions._sessions.get(session_key)
            if not session:
                return
            client = getattr(session.provider, "_client", None)
            raw_pid = getattr(client, "_pid", None) if client else None
            pid = raw_pid if isinstance(raw_pid, int) else None
            if not pid:
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
            # Sweep children that escaped to different PGIDs
            _kill_escaped_children(child_pids)
        except Exception:
            logger.exception("Reaper: SIGKILL failed for %s", session_key)

    def notify_injection_failed(
        self, info: SubagentInfo, reason: str = "delivery timed out"
    ) -> None:
        """Notify UI and queue failure for LLM when injection times out.

        Appends a synthetic error to the dashboard session (UI) and queues a
        failure message into ``session._pending_subagent_failures`` so the LLM
        learns about the failure on the next ``_run_chat`` turn and can read
        the result from disk if needed.
        """
        try:
            parent_key = info.parent_session_key
            if not parent_key.startswith("dashboard:"):
                return
            session_name = parent_key.removeprefix("dashboard:")

            # Build failure message the LLM will see on next turn
            task_preview = _redact((info.task or "")[:100])
            result_hint = ""
            if info.result_path:
                try:
                    size = os.path.getsize(info.result_path)
                    size_str = f"{size:,} bytes"
                except OSError:
                    size_str = ""
                result_hint = (
                    f"\nResult saved at: {info.result_path}"
                    + (f" ({size_str})" if size_str else "")
                    + "\nUse the read tool to retrieve it if needed."
                )
            failure_msg = (
                f"[Subagent completion event]\n"
                f"Agent `{info.id}` ❌ {reason}\n"
                f"Task: {task_preview}\n"
                f"The agent finished but result delivery timed out.{result_hint}"
            )

            # Queue for LLM context drain on next _run_chat
            if self._on_event:
                _task = asyncio.ensure_future(
                    self._fire_event(
                        "subagent_injection_failed",
                        info,
                        {
                            "error": reason,
                            "session": session_name,
                            "failure_msg": failure_msg,
                        },
                    )
                )
                _task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)
        except Exception:
            logger.debug("notify_injection_failed failed for %s", info.id, exc_info=True)

    @property
    def max_concurrent(self) -> int:
        return self._max_concurrent

    @property
    def running_count(self) -> int:
        return self._running_count

    def running_agents_for(self, parent_key: str) -> list[dict]:
        """Return summary dicts for agents belonging to *parent_key*."""
        from personalclaw.security import redact_credentials, redact_exfiltration_urls

        def _r(s: str) -> str:
            s, _ = redact_exfiltration_urls(s)
            s, _ = redact_credentials(s)
            return s

        return [
            {
                "id": a.id,
                "task": _r(a.task[:80]),
                "agent": _r(a.agent),
                "turns": a.turns,
                "last_tool": _r(a.last_tool),
                "startedAt": a.started,
            }
            for a in self._agents.values()
            if not a.done and a.parent_session_key == parent_key
        ]

    def spawn(
        self,
        task: str,
        parent_session_key: str = "",
        agent: str = "",
        max_turns: int = 0,
        model: str | None = None,
        cwd: str = "",
        approval_mode: str | None = None,
        capability_class: str | None = None,
        silent: bool = False,
        dry_run: bool = False,
        parent_run: str = "",
        sandbox: str = "none",
        extra_env: dict[str, str] | None = None,
    ) -> SubagentInfo | None:
        """Spawn a subagent for *task*.

        Approval priority (first match wins):

        1. YOLO mode → immediate execution
        2. ``approval_mode="auto"`` from caller → immediate execution
        3. ``auto_approve_subagent_spawn`` config → auto-approved execution
        4. ``on_spawn_approval`` callback → interactive approval
        5. Otherwise → rejected

        When ``approval_mode="auto"`` is set, it has two effects:
        - Skips the spawn approval gate (this method)
        - Sets the subagent's session-level tool approval policy to
          "auto" in ``_run_inner()``, meaning all tool calls within
          the subagent are auto-approved for its entire lifetime.

        This dual behavior is intentional for headless callers (e.g. a
        background cron/agent) that have no UI to respond to approval prompts.
        The parameter is only accepted via the internal ``POST /api/spawn``
        endpoint (requires X-Internal-Secret), not from LLM tool calls.

        Args:
            task (str): The prompt/task description for the subagent.
            parent_session_key (str): Session key of the caller.
            agent (str): Agent name override (default: "personalclaw").
            model (str): Model override.
            cwd (str): Optional absolute path where the subagent subprocess
                launches instead of the default ``subagent_<id>`` sandbox.
                Validated against ``AgentConfig.subagent_cwd_allowed_roots``;
                rejected spawns return a done ``SubagentInfo`` with ``error``
                set. Enables cwd-relative resource globs (``AGENTS.md``,
                ``.personalclaw/steering``) to resolve correctly.
            approval_mode (str | None): "auto" to skip spawn gate and
                set session-level auto-approve.  Only honored from
                authenticated internal callers (X-Internal-Secret).
            silent (bool): Suppress completion notifications.
            parent_run (str): The run this spawn belongs to (``workflow:<run_id>``
                or any caller-chosen fan-out key). Scopes the run-level concurrency
                lane, the consecutive-failure breaker and the run budget so one
                wide fan-out cannot starve or overspend against every other run.

        Returns:
            SubagentInfo | None: Agent metadata, or None if at capacity.
        """
        # --- Redact task once for all SubagentInfo storage (raw task kept for ACP agent prompt) ---
        _redacted_task = redact_credentials(redact_exfiltration_urls(task)[0])[0]

        # --- Memory guard: refuse to spawn if system memory is critically low ---
        try:
            min_mem = AppConfig.load().agent.spawn_min_memory_gb
        except Exception:
            min_mem = 4.0
        mem_ok, avail_gb = check_memory_available(min_gb=min_mem)
        if not mem_ok:
            logger.warning(
                "Subagent spawn refused: only %.2f GB available (min %.1f GB required)",
                avail_gb,
                min_mem,
            )
            sel().log_tool_invocation(
                session_key=parent_session_key or "",
                source="subagent",
                tool_name="subagent_run",
                outcome="refused_low_memory",
                metadata={
                    "available_gb": avail_gb,
                    "min_gb": min_mem,
                    "task": _redacted_task[:120],
                },
            )
            info = SubagentInfo(
                id=uuid.uuid4().hex[:8],
                task=_redacted_task,
                agent=agent,
                done=True,
                error=f"spawn refused: only {avail_gb:.1f} GB memory available (need {min_mem:.0f} GB)",  # noqa: E501
            )
            return info

        # --- Incident kill switch (§1.3): refuse spawns during an incident ---
        # A subagent is unattended work. Interactive chat is untouched; a spawn is
        # not the chat stream, so it is suspended.
        try:
            from personalclaw.guardrails.incident import incident_active

            if incident_active():
                logger.info("Subagent spawn refused: incident mode active")
                sel().log_tool_invocation(
                    session_key=parent_session_key or "",
                    source="subagent",
                    tool_name="subagent_run",
                    outcome="refused_incident",
                    metadata={"task": _redacted_task[:120]},
                )
                return SubagentInfo(
                    id=uuid.uuid4().hex[:8],
                    task=_redacted_task,
                    agent=agent,
                    done=True,
                    error="spawn refused: incident mode active (resume with "
                    "`personalclaw incident off`)",
                )
        except Exception:
            logger.debug("subagent spawn incident check failed (fail-open)", exc_info=True)

        # --- Budget guard: refuse to spawn if the day-scope spend ceiling is hit ---
        # A subagent is unattended work; if the day's guardrail budget is already
        # exhausted, don't start another one (§1.1 pause-into-refuse). Fail-open on
        # any error — the budget is a guardrail, not a hard gate.
        try:
            from personalclaw.guardrails.budgets import BudgetVerdict, budget_from_config, get_meter

            _day_budget = budget_from_config()
            if not _day_budget.is_unlimited:
                _verdict, _reason = get_meter().check_day(_day_budget)
                if _verdict is BudgetVerdict.EXCEEDED:
                    logger.warning("Subagent spawn refused: %s", _reason)
                    sel().log_tool_invocation(
                        session_key=parent_session_key or "",
                        source="subagent",
                        tool_name="subagent_run",
                        outcome="refused_budget_exceeded",
                        metadata={"reason": _reason, "task": _redacted_task[:120]},
                    )
                    return SubagentInfo(
                        id=uuid.uuid4().hex[:8],
                        task=_redacted_task,
                        agent=agent,
                        done=True,
                        error=f"spawn refused: {_reason} (resets next day, or raise the "
                        f"budget in Settings → Guardrails)",
                    )
        except Exception:
            logger.debug("subagent spawn budget check failed (fail-open)", exc_info=True)

        # --- CWD validation: reject bad paths before consuming a session ---
        resolved_cwd = ""
        if cwd:
            try:
                allowed_roots = AppConfig.load().agent.subagent_cwd_allowed_roots
            except Exception:
                # Fail closed: if config is unavailable, treat cwd override as
                # disabled. Defaulting to the permissive default here would
                # silently re-enable the feature for admins who set
                # subagent_cwd_allowed_roots=[] to disable it.
                allowed_roots = []
            resolved_cwd, cwd_err = validate_cwd(cwd, allowed_roots)
            if cwd_err:
                logger.warning("Subagent spawn refused: invalid cwd %r: %s", cwd, cwd_err)
                sel().log_tool_invocation(
                    session_key=parent_session_key or "",
                    source="subagent",
                    tool_name="subagent_run",
                    outcome="rejected_invalid_cwd",
                    metadata={"cwd": cwd[:200], "reason": cwd_err, "task": _redacted_task[:120]},
                )
                info = SubagentInfo(
                    id=uuid.uuid4().hex[:8],
                    task=_redacted_task,
                    agent=agent,
                    done=True,
                    error=f"spawn refused: {cwd_err}",
                )
                return info

        # --- Agent validation (C1.3): an unknown agent is a TYPED error, never a
        # silent downgrade. Done BEFORE any queue slot so a bad name fails fast and a
        # queued spawn is never drained onto the wrong agent. ---
        if agent:
            agent, err = _validate_agent(agent)
            if err:
                return SubagentInfo(
                    id=uuid.uuid4().hex[:8], task=_redacted_task, agent="", done=True, error=err
                )

        # --- Build the addressable info up front (C1.2): a queued spawn carries a
        # REAL id and its full parameter set (approval_mode/model/silent/dry_run/
        # parent_run), so it is pollable + cancellable and a drain never re-generates
        # the id or drops a parameter. ---
        agent_id: str = uuid.uuid4().hex[:8]
        info = SubagentInfo(
            id=agent_id,
            task=_redacted_task,
            parent_session_key=parent_session_key,
            agent=agent,
            approval_mode=approval_mode or "",
            capability_class=capability_class or "",
            dry_run=dry_run,
            silent=silent,
            max_turns=max_turns,
            model=model or "",
            cwd=resolved_cwd,
            parent_run=parent_run,
            sandbox=sandbox or "none",
            extra_env=dict(extra_env or {}),
        )
        info._raw_task = task  # unredacted prompt for ACP agent execution

        # --- Fan-out stop (C1.4 breaker / C1.5 run budget / kill-fan-out): a stopped
        # fan-out refuses further spawns with the recorded TYPED reason. ---
        fkey = _fanout_key(info)
        stop_reason = self._fanout_stops.get(fkey)
        if stop_reason:
            info.done = True
            info.error = f"spawn refused: {stop_reason}"
            sel().log_tool_invocation(
                session_key=parent_session_key or "",
                source="subagent",
                tool_name="subagent_run",
                outcome="refused_fanout_stopped",
                metadata={"subagent_id": agent_id, "reason": stop_reason},
            )
            return info

        # --- Capacity: queue when the GLOBAL cap OR this run's LANE cap is full. The
        # per-run lane (C1.4) reserves host headroom for other runs, so one wide
        # fan-out cannot starve them even while global slots remain free. ---
        if (
            self._running_count >= self._max_concurrent
            or self._lane_count(fkey) >= self._run_lane_cap
        ):
            info.queued = True
            self._agents[agent_id] = info
            self._queue.append(info)
            logger.info(
                "Subagent %s queued (%d running, lane[%s]=%d, %d queued)",
                agent_id,
                self._running_count,
                fkey or "-",
                self._lane_count(fkey),
                len(self._queue),
            )
            return info

        self._agents[agent_id] = info
        self._dispatch_run(info)
        return info

    def _dispatch_run(self, info: SubagentInfo) -> None:
        """Start (or gate) a spawn that has cleared the queue.

        Increments the global + run-lane counts, then routes through the same
        approval priority ``spawn`` documents. Shared by the initial spawn and the
        queue drain so a drained spawn takes the identical path with its original
        id + parameters (C1.2). A rejection here decrements the counts it took.
        """
        agent = info.agent
        agent_id = info.id
        info.queued = False
        self._inc_running(info)

        # Check parent session trust (approval_policy="auto") set by dashboard trust toggle.
        parent_trusted = (
            info.parent_session_key
            and self._sessions.get_approval_policy(info.parent_session_key) == "auto"
        )

        if self._is_yolo and self._is_yolo():
            self._tasks[agent_id] = asyncio.create_task(self._run(info))
            self._log_spawned(info)
        elif info.approval_mode == "auto":
            self._tasks[agent_id] = asyncio.create_task(self._run(info))
            self._log_spawned(info)
            sel().log_tool_invocation(
                session_key=info.parent_session_key,
                source="subagent",
                tool_name="subagent_run",
                outcome="auto_approved_spawn",
                metadata={"subagent_id": agent_id, "reason": "approval_mode_auto"},
            )
        elif parent_trusted:
            self._tasks[agent_id] = asyncio.create_task(self._run(info))
            self._log_spawned(info)
            sel().log_tool_invocation(
                session_key=info.parent_session_key,
                source="subagent",
                tool_name="subagent_run",
                outcome="auto_approved_spawn",
                metadata={"subagent_id": agent_id, "reason": "parent_trusted"},
            )
        elif self._ctx_builder and self._ctx_builder.hooks:
            if self._ctx_builder.hooks.auto_approve_subagent_spawn is True:
                self._tasks[agent_id] = asyncio.create_task(self._run(info))
                self._log_spawned(info)
                sel().log_tool_invocation(
                    session_key=info.parent_session_key,
                    source="subagent",
                    tool_name="subagent_run",
                    outcome="auto_approved_spawn",
                    metadata={"subagent_id": agent_id, "reason": "tool_calls_gated"},
                )
            elif self._on_spawn_approval:
                self._tasks[agent_id] = asyncio.create_task(self._spawn_with_approval(info))
            else:
                info.done = True
                info.error = "spawn rejected: no approval mechanism configured"
                self._dec_running(info)
                self._drain_queue()
                sel().log_tool_invocation(
                    session_key=info.parent_session_key,
                    source="subagent",
                    tool_name="subagent_run",
                    outcome="rejected_spawn",
                    metadata={"subagent_id": agent_id, "reason": "no_approval_mechanism"},
                )
                return
        elif self._on_spawn_approval:
            self._tasks[agent_id] = asyncio.create_task(self._spawn_with_approval(info))
        else:
            info.done = True
            info.error = "spawn rejected: no approval mechanism configured"
            self._dec_running(info)
            self._drain_queue()
            sel().log_tool_invocation(
                session_key=info.parent_session_key,
                source="subagent",
                tool_name="subagent_run",
                outcome="rejected",
                metadata={"subagent_id": agent_id, "reason": "no approval mechanism"},
            )
            logger.warning("Subagent %s rejected: no approval callback", agent_id)
            if self._on_done:
                self._tasks[agent_id] = asyncio.ensure_future(self._safe_announce(info))

        # `SubagentSpawn` (AUTO crit 5): declared, selectable in the hook UI, and fired by nothing
        # until now. Gated on `not info.done` so a REJECTED spawn does not announce one: every
        # rejection path above sets `done=True` with an `error`, so a hook watching this event sees
        # only subagents that actually started.
        #
        # `depth` is NOT passed: `SubagentInfo` carries no depth field (checked), and the recursion
        # bound lives on the `invoke-agent` action's `__hook_depth`, which `fire_for_ids` injects.
        # Inventing a zero here would report every subagent as top-level.
        if not info.done:
            from personalclaw.triggers.lifecycle_fire import fire_sync, subagent_spawn_payload

            fire_sync(
                subagent_spawn_payload(
                    subagent_id=agent_id,
                    parent_session_key=info.parent_session_key,
                    agent_role=agent or "",
                ),
                subagent_id=agent_id,
                parent_session_key=info.parent_session_key,
                agent_role=agent or "",
            )

    # ── Run-scoped concurrency lane (C1.4) ──────────────────────────────

    def _lane_count(self, fkey: str) -> int:
        """Running spawns in one fan-out's lane."""
        return self._running_by_fanout.get(fkey, 0)

    def _inc_running(self, info: SubagentInfo) -> None:
        """Take a global + run-lane slot for a starting spawn."""
        self._running_count += 1
        fkey = _fanout_key(info)
        self._running_by_fanout[fkey] = self._running_by_fanout.get(fkey, 0) + 1

    def _dec_running(self, info: SubagentInfo) -> None:
        """Release the global + run-lane slot a spawn held. Idempotent-safe: the
        lane entry is dropped at zero so a completed fan-out leaves no residue."""
        self._running_count = max(0, self._running_count - 1)
        fkey = _fanout_key(info)
        remaining = self._running_by_fanout.get(fkey, 0) - 1
        if remaining > 0:
            self._running_by_fanout[fkey] = remaining
        else:
            self._running_by_fanout.pop(fkey, None)

    def _note_child_outcome(self, info: SubagentInfo) -> None:
        """Fold one finished child into its fan-out's consecutive-failure breaker.

        The per-child ``subagent:<id>`` session is already released by the time a
        child fails, so the session-level breaker (``session._CIRCUIT_BREAKER_``) can
        never trip for sub-agents — the real guards were only the turn limit and the
        reaper (C1.4). This keys the breaker on the FAN-OUT instead: a SUCCESS resets
        the count; a genuine FAILURE increments it, and at
        ``_CIRCUIT_BREAKER_THRESHOLD`` consecutive failures the whole fan-out is
        stopped so a broken run cannot keep burning children. A user-CANCELLED child
        is neither (an intentional stop is not a failure).
        """
        if info._outcome_noted:
            return
        info._outcome_noted = True
        fkey = _fanout_key(info)
        if info.cancelled:
            return
        if info.error:
            count = self._fanout_failures.get(fkey, 0) + 1
            self._fanout_failures[fkey] = count
            if count >= _CIRCUIT_BREAKER_THRESHOLD and fkey not in self._fanout_stops:
                reason = f"fan-out breaker tripped after {count} consecutive child failures"
                self._fanout_stops[fkey] = reason
                logger.error("Subagent fan-out breaker tripped for %s: %s", fkey or "-", reason)
                try:
                    sel().log_tool_invocation(
                        session_key=info.parent_session_key or "",
                        source="subagent",
                        tool_name="subagent_run",
                        outcome="fanout_breaker_tripped",
                        metadata={"fanout": fkey, "failures": count},
                    )
                except Exception:
                    logger.debug("SEL audit failed for breaker trip %s", fkey, exc_info=True)
        else:
            # A success clears the consecutive-failure streak for this fan-out.
            self._fanout_failures.pop(fkey, None)

    def _charge_child_and_check_budget(self, info: SubagentInfo) -> None:
        """Fold one child's cost into the fan-out's RUN-scoped budget and, if the
        run ceiling is now exceeded, STOP the fan-out mid-flight (C1.5).

        Consumes the per-child cost/tokens already captured at ``EVENT_COMPLETE``
        (COST-AND-TOKEN-OBSERVABILITY T1.3 — NOT a second ledger) and composes with
        AUTONOMY-GUARDRAILS' :class:`SpendMeter` run scope: it charges the meter
        keyed on the fan-out key and re-reads ``check_run`` after each child. The
        day-scope check at spawn is a point-in-time snapshot; N children each spend
        under it, so the run scope is what actually bounds a fan-out. On EXCEEDED the
        fan-out is stopped with a TYPED reason (refusing queued/new spawns) and its
        in-flight children are cancelled. Fail-open: a budget is a guardrail.
        """
        fkey = _fanout_key(info)
        try:
            from personalclaw.guardrails.budgets import (
                BudgetVerdict,
                get_meter,
                run_budget_from_config,
            )

            budget = run_budget_from_config()
            if budget.is_unlimited:
                return
            meter = get_meter()
            meter.charge(info.input_tokens + info.output_tokens, info.cost_usd, run_key=fkey)
            verdict, reason = meter.check_run(fkey, budget)
            if verdict is BudgetVerdict.EXCEEDED and fkey not in self._fanout_stops:
                typed = f"run budget exceeded ({reason})"
                self._fanout_stops[fkey] = typed
                logger.warning("Subagent fan-out %s stopped mid-flight: %s", fkey or "-", typed)
                try:
                    sel().log_tool_invocation(
                        session_key=info.parent_session_key or "",
                        source="subagent",
                        tool_name="subagent_run",
                        outcome="fanout_budget_exceeded",
                        metadata={"fanout": fkey, "reason": reason},
                    )
                except Exception:
                    logger.debug("SEL audit failed for budget stop %s", fkey, exc_info=True)
                # Kill the in-flight children of this fan-out so "stops mid-flight"
                # is literal, not just "refuses the next one".
                asyncio.ensure_future(self.cancel_fanout(fkey, reason=typed))
        except Exception:
            logger.debug("fan-out budget check failed (fail-open)", exc_info=True)

    def _maybe_clear_fanout(self, fkey: str) -> None:
        """Drop a fan-out's breaker/budget/stop state once it is fully drained.

        A later fan-out reusing the same key (a new workflow run rarely does; a
        chat parent may) must start clean — otherwise a prior run's tripped breaker
        would pre-stop it. Called after each child finishes; clears only when no
        child of this key is running or queued.
        """
        if self._lane_count(fkey) > 0:
            return
        if any(_fanout_key(qi) == fkey for qi in self._queue):
            return
        self._fanout_failures.pop(fkey, None)
        self._fanout_stops.pop(fkey, None)
        try:
            from personalclaw.guardrails.budgets import get_meter

            get_meter().end_run(fkey)
        except Exception:
            logger.debug("fan-out meter end_run failed for %s", fkey, exc_info=True)

    async def _safe_announce(self, info: SubagentInfo) -> None:
        """Notify completion callback (single info, as a one-element batch) with
        error handling. Used by rejection paths that never entered ``_run``.

        Args:
            info (SubagentInfo): The subagent metadata.
        """
        assert self._on_done is not None
        try:
            await self._on_done([info])
        except Exception:
            logger.exception("Subagent announce failed for %s", info.id)

    def _drain_queue(self) -> None:
        """Dispatch the next queued spawn when a slot frees up.

        Respects BOTH the global cap and the queued spawn's own run-lane cap
        (C1.4) — a queued spawn whose lane is still full is skipped and the next
        eligible one is tried, so a wide fan-out cannot monopolise the drain. The
        queued ``SubagentInfo`` (with its full parameter set, C1.2) is dispatched
        as-is via ``_dispatch_run`` — the id and every parameter survive the drain.
        Staggers by 2 seconds to avoid CPU/memory spikes.
        """
        if not self._queue or self._running_count >= self._max_concurrent:
            return
        # Find the first queued spawn whose run-lane also has room.
        idx = next(
            (
                i
                for i, qi in enumerate(self._queue)
                if self._lane_count(_fanout_key(qi)) < self._run_lane_cap
            ),
            None,
        )
        if idx is None:
            return  # every queued spawn's lane is full; wait for a lane to free
        info = self._queue.pop(idx)
        # A spawn cancelled while queued must not be dispatched.
        if info.done or info.cancelled:
            self._drain_queue()
            return
        # A fan-out stopped while this spawn waited is refused, not started.
        stop_reason = self._fanout_stops.get(_fanout_key(info))
        if stop_reason:
            info.done = True
            info.error = f"spawn refused: {stop_reason}"
            self._drain_queue()
            return
        logger.info("Draining queue: dispatching %s (%d left)", info.id, len(self._queue))
        self._dispatch_run(info)
        if self._queue and self._running_count < self._max_concurrent:
            asyncio.get_event_loop().call_later(2.0, self._drain_queue)

    async def _spawn_with_approval(self, info: SubagentInfo) -> None:
        """Request approval before starting the subagent.

        If approval is denied the subagent is marked as done with an
        error and the running count is decremented without executing.

        Args:
            info (SubagentInfo): The subagent metadata.
        """
        assert self._on_spawn_approval is not None
        request_id: str = f"spawn:{info.id}"
        try:
            from personalclaw.security import (
                redact_credentials,
                redact_exfiltration_urls,
            )

            task_safe, _ = redact_exfiltration_urls(info.task)
            task_safe, _ = redact_credentials(task_safe)
            task_preview: str = task_safe[:80]
            approved: bool = await self._on_spawn_approval(
                request_id, f"subagent_run({task_preview})", info.parent_session_key
            )
        except Exception:
            logger.exception("Spawn approval failed for %s", info.id)
            approved = False

        if not approved:
            info.done = True
            info.error = "spawn rejected"
            self._dec_running(info)
            self._drain_queue()
            self._tasks.pop(info.id, None)
            sel().log_tool_invocation(
                session_key=info.parent_session_key,
                source="subagent",
                tool_name="subagent_run",
                outcome="rejected",
                metadata={"subagent_id": info.id},
            )
            logger.info("Subagent %s spawn rejected", info.id)
            if self._on_done:
                await self._safe_announce(info)
            return

        self._log_spawned(info)
        await self._run(info)

    def _log_spawned(self, info: SubagentInfo) -> None:
        """Record spawn metrics and audit log entry.

        Args:
            info (SubagentInfo): The subagent metadata.
        """
        # Persist agent folder to disk for orphan recovery
        try:

            create_agent_folder(
                info.id,
                task=info.task,
                agent=info.agent,
                parent_session=info.parent_session_key,
                max_turns=info.max_turns,
            )
        except Exception:
            logger.warning("Failed to create agent folder for %s", info.id, exc_info=True)

        Stats().inc_subagent_spawned()
        sel().log_tool_invocation(
            session_key=info.parent_session_key,
            source="subagent",
            tool_name="subagent_run",
            outcome="spawned",
            metadata={
                "subagent_id": info.id,
                "agent": info.agent or "personalclaw",
                "cwd": info.cwd,
            },
        )
        logger.info("Subagent %s spawned: %s", info.id, info.task[:80])

    @property
    def running(self) -> list[SubagentInfo]:
        """Return currently running (not done) subagents."""
        return [a for a in self._agents.values() if not a.done]

    @property
    def all_agents(self) -> list[SubagentInfo]:
        """Return all tracked subagents (running and done)."""
        return list(self._agents.values())

    def get(self, agent_id: str) -> SubagentInfo | None:
        """Get agent info by ID."""
        return self._agents.get(agent_id)

    @property
    def count(self) -> int:
        return len(self.running)

    async def _run(self, info: SubagentInfo) -> None:
        """Execute a subagent task in its own session."""
        session_key = f"subagent:{info.id}"
        try:
            await asyncio.wait_for(
                self._run_inner(info, session_key), timeout=self._default_timeout
            )
        except asyncio.TimeoutError:
            if not info.reaped:
                info.error = f"Timed out after {self._default_timeout // 60} minutes [{_timeout_context(info)}]"  # noqa: E501
                info.done = True
                Stats().inc_subagent_failed()
                self._write_tombstone(info, "timeout")
            logger.warning("Subagent %s timed out", info.id)
        except asyncio.CancelledError:
            if not info.reaped:
                info.done = True
                info.error = "cancelled"
                Stats().inc_subagent_failed()
                self._write_tombstone(info, "cancelled")
            logger.info("Subagent %s cancelled", info.id)
        except Exception as exc:
            if not info.reaped:
                info.error = str(exc)
                info.done = True
                Stats().inc_subagent_failed()
                self._write_tombstone(info, "error")
            logger.exception("Subagent %s failed", info.id)
        finally:
            if not info.reaped:
                # Fire WS event immediately so Activity Viewer updates
                # before the slow reset + on_done path.
                info.elapsed = time.time() - info.started
                await self._fire_event(
                    "subagent_done",
                    info,
                    {
                        "elapsed": info.elapsed,
                        "error": _redact(info.error) if info.error else None,
                        "task": _redact(info.task),
                        "agent": _redact(info.agent),
                        "result": _done_result(info.result),
                        # Per-child cost/tokens for the activity panel (C1.5) — the
                        # T1.3 ledger figures already captured at EVENT_COMPLETE.
                        "cost_usd": round(info.cost_usd, 6),
                        "tokens": info.input_tokens + info.output_tokens,
                    },
                )
                try:
                    self._sessions.release(session_key, cleanup=True)
                except Exception:
                    logger.warning("Subagent %s: release failed", info.id, exc_info=True)
                self._dec_running(info)
                # Record the child's outcome against the fan-out breaker (C1.4)
                # BEFORE draining, so a failing fan-out trips before the next child
                # takes its freed slot.
                self._note_child_outcome(info)
                self._drain_queue()
                self._maybe_clear_fanout(_fanout_key(info))
                try:
                    await asyncio.wait_for(
                        self._sessions.reset(session_key), timeout=_RESET_TIMEOUT
                    )
                except asyncio.TimeoutError:
                    logger.warning("Subagent %s: reset timed out, force-killing", info.id)
                    self._sigkill_session(session_key)
                    try:
                        sel().log_tool_invocation(
                            session_key=session_key,
                            source="subagent",
                            tool_name="run_finally_force_kill",
                            outcome="sigkill",
                            metadata={"subagent_id": info.id},
                        )
                    except Exception:
                        logger.exception("Subagent %s: SEL audit failed", info.id)
                except Exception:
                    logger.exception("Subagent %s: reset failed", info.id)
            self._tasks.pop(info.id, None)

        if self._on_done and not info.reaped:
            # C1.1: enqueue for COALESCED batch delivery instead of starting a
            # dedicated parent turn per completion. A burst of completions for one
            # parent is delivered as a single turn.
            self._enqueue_delivery(info)

    def _enqueue_delivery(self, info: SubagentInfo) -> None:
        """Buffer a completed subagent for coalesced batch delivery (C1.1).

        Groups by parent session key and (re)arms a short coalescing timer, so all
        completions that land within the window ship in ONE parent turn. A parent
        with a completion already mid-delivery has the new one appended to the next
        batch rather than racing the semaphore.
        """
        parent_key = info.parent_session_key
        self._pending_delivery.setdefault(parent_key, []).append(info)
        existing = self._delivery_tasks.get(parent_key)
        if existing and not existing.done():
            return  # a timer/flush is already pending for this parent
        self._delivery_tasks[parent_key] = asyncio.create_task(
            self._deliver_after_delay(parent_key)
        )

    async def _deliver_after_delay(self, parent_key: str) -> None:
        """Wait the coalescing window, then flush this parent's pending batch."""
        rearm = False
        try:
            if self._delivery_coalesce_secs > 0:
                await asyncio.sleep(self._delivery_coalesce_secs)
            await self._flush_delivery(parent_key)
        except asyncio.CancelledError:
            self._delivery_tasks.pop(parent_key, None)
            raise
        except Exception:
            logger.exception("Subagent delivery flush failed for %s", parent_key)
        self._delivery_tasks.pop(parent_key, None)
        # A completion that arrived DURING the flush re-arms the timer — done here
        # (not in a finally) so a cancelled task never touches create_task with no
        # running loop during teardown.
        if self._pending_delivery.get(parent_key):
            rearm = True
        if rearm:
            self._delivery_tasks[parent_key] = asyncio.create_task(
                self._deliver_after_delay(parent_key)
            )

    async def _flush_delivery(self, parent_key: str) -> None:
        """Deliver one parent's buffered completions as a single batch (C1.1).

        The whole batch is delivered under ONE ``_ON_DONE_TIMEOUT``. On a delivery
        FAILURE the orchestrator's context is PRESERVED — the parent session is NOT
        reset (the old remedy wiped the very conversation that asked for the work);
        the failure is surfaced per child via ``notify_injection_failed`` instead.
        """
        batch = self._pending_delivery.pop(parent_key, [])
        if not batch or not self._on_done:
            return
        try:
            await asyncio.wait_for(self._on_done(batch), timeout=_ON_DONE_TIMEOUT)
            for info in batch:
                if not info.error:
                    self._cleanup_delivered(info)
        except asyncio.TimeoutError:
            logger.error(
                "Subagent batch delivery for %s timed out after %.0fs (%d result(s)) — "
                "parent context PRESERVED, surfacing failure",
                parent_key,
                _ON_DONE_TIMEOUT,
                len(batch),
            )
            # DO NOT reset the parent session (C1.1): resetting wiped the
            # orchestrator's context. Surface the failure to the parent instead so
            # the LLM can read each result from disk on its next turn.
            for info in batch:
                self.notify_injection_failed(
                    info,
                    reason=f"batch delivery timed out after {int(_ON_DONE_TIMEOUT)}s",
                )
        except Exception:
            logger.exception("Subagent batch delivery failed for %s", parent_key)
            for info in batch:
                self.notify_injection_failed(info, reason="batch delivery failed")

    async def flush_deliveries(self) -> None:
        """Await every pending coalesced delivery (C1.1). Called at graceful
        shutdown so a burst of completions in flight is delivered before the loop
        stops, and by tests to make the deferred delivery deterministic."""
        for _ in range(100):  # bounded: a re-arm may enqueue one more round
            tasks = [t for t in self._delivery_tasks.values() if not t.done()]
            if not tasks:
                return
            await asyncio.gather(*tasks, return_exceptions=True)

    def _cleanup_delivered(self, info: SubagentInfo) -> None:
        """Clean up the agent folder + workspace result file after a delivered
        completion (unchanged from the per-completion path, just factored out)."""
        try:
            delete_agent_folder(info.id)
        except Exception:
            logger.debug("Failed to clean up agent folder for %s", info.id, exc_info=True)
        try:
            parent_key = info.parent_session_key
            if parent_key.startswith("dashboard:"):
                session_name = parent_key.removeprefix("dashboard:")
                _ws_result_path(session_name, info.id).unlink(missing_ok=True)
        except Exception:
            logger.debug("Failed to clean workspace result for %s", info.id, exc_info=True)

    async def _fire_event(self, etype: str, info: SubagentInfo, extra: dict | None = None) -> None:
        if self._on_event:
            try:
                await self._on_event(etype, info, extra or {})
            except Exception:
                logger.warning("on_event failed for %s/%s", etype, info.id, exc_info=True)

    @staticmethod
    def _write_tombstone(info: SubagentInfo, cause: str) -> None:
        """Best-effort tombstone write for abnormal exits."""
        try:

            write_tombstone(
                info.id,
                cause=cause,
                recovery_action="pending",
                pid=info._pid,
                turns=info.turns,
                last_tool=info.last_tool,
            )
        except Exception:
            logger.debug("Failed to write tombstone for %s", info.id, exc_info=True)

    async def _run_inner(self, info: SubagentInfo, session_key: str) -> None:
        """Inner execution — called within timeout wrapper."""
        # Inherit approval policy from parent session; yolo/trust overrides
        parent_policy = self._sessions.get_approval_policy(info.parent_session_key)
        # Explicit approval_mode from spawn caller (e.g. a background cron/agent)
        if not parent_policy and info.approval_mode == "auto":
            parent_policy = "auto"
            sel().log_api_access(
                caller=info.parent_session_key or f"subagent:{info.id}",
                operation="subagent.approval_mode_auto_policy",
                outcome="ok",
                source="subagent",
                resources=f"subagent_id={info.id}",
            )
        if not parent_policy and self._is_yolo and self._is_yolo():
            parent_policy = "auto"
            sel().log_api_access(
                caller=info.parent_session_key,
                operation="subagent.yolo_policy_fallback",
                outcome="ok",
                source="subagent",
                resources=f"subagent_id={info.id}",
            )
        if not parent_policy and info.parent_session_key == "":
            if self._global_approval_mode == "auto":
                parent_policy = "auto"
                sel().log_api_access(
                    caller=f"subagent:{info.id}",
                    operation="subagent.config_policy_fallback",
                    outcome="ok",
                    source="subagent",
                    resources=f"subagent_id={info.id}",
                )
        # auto_approve_subagent_tools auto-approves tool calls inside
        # subagents (separate from the spawn gate, deny-by-default).
        if not parent_policy and self._ctx_builder and self._ctx_builder.hooks:
            if self._ctx_builder.hooks.auto_approve_subagent_tools is True:
                parent_policy = "auto"
                sel().log_api_access(
                    caller=info.parent_session_key or f"subagent:{info.id}",
                    operation="subagent.auto_approve_subagent_tools_policy",
                    outcome="ok",
                    source="subagent",
                    resources=f"subagent_id={info.id}",
                )
        # 🔴 THE CEILING BOUNDS THE SPAWN GRANT (PHF-8). Every branch above can only WIDEN
        # this run to "auto"; none of them consults the operator's governance ceiling. So an
        # operator who declared `{"scopes": {"approval": {"value": "ask"}}}` still got
        # auto-approving subagents the moment a toggle, a yolo flag or a config default said
        # so. `ceiling_permits_approval` resolves the grant through the same tightest-wins
        # composition every other seam uses, and the refusal is SEL-audited — a silently
        # downgraded grant would be indistinguishable from the grant never being asked for.
        if parent_policy == "auto":
            from personalclaw.guardrails.policy import ceiling_permits_approval

            if not ceiling_permits_approval("auto"):
                parent_policy = ""
                logger.warning(
                    "subagent %s: the governance ceiling refused the auto-approval grant; "
                    "tool calls stay gated",
                    info.id,
                )
                sel().log_api_access(
                    caller=info.parent_session_key or f"subagent:{info.id}",
                    operation="subagent.approval_grant_refused",
                    outcome="blocked",
                    source="guardrails",
                    resources=f"subagent_id={info.id},grant=auto,refused_by=governance_ceiling",
                )
        # Inherit agent from parent session when not explicitly specified
        agent = info.agent or self._sessions.get_agent(info.parent_session_key)
        if not info.agent and agent:
            sel().log_api_access(
                caller=f"subagent:{info.id}",
                operation="subagent.agent_inheritance",
                outcome="ok",
                source="subagent",
                resources=f"subagent_id={info.id},inherited_agent={agent}",
            )
        extra_kwargs: dict[str, Any] = {}
        if info.model:
            extra_kwargs["model"] = info.model
        else:
            # A model-less spawn resolves the ``orchestration`` chain for its inner
            # model (MODEL-USE-CASES-V2 T2.2) — an explicit spawn model still wins
            # (the branch above). Unbound axis → chat chain, unchanged.
            extra_kwargs["model_axis"] = "orchestration"
        if info.cwd:
            extra_kwargs["cwd"] = info.cwd
        # Sandbox provider (EI-1): thread the chosen isolation backend to the ACP worker
        # launch. Only forward a non-default so the chat/native paths (which ignore it) are
        # untouched; ``none`` is the transport default.
        if info.sandbox and info.sandbox != "none":
            extra_kwargs["sandbox"] = info.sandbox
        # Unattended = no human can answer an interactive tool/approval prompt, so
        # strip those tools + fail their gate fast (T5). This is true for HEADLESS
        # spawns only — cron / scheduled run-prompt/run-workflow / invoke-agent —
        # which set info.approval_mode="auto" explicitly, OR spawns with no live
        # interactive parent session to escalate to.
        #
        # It must NOT be inferred from parent_policy=="auto" alone: a live dashboard
        # chat in Trust/YOLO also yields parent_policy="auto", but the human IS
        # present and chose to auto-approve — so the subagent must KEEP its tools and
        # let the parent_policy=="auto" branch in _run auto-approve them (mirroring
        # the parent's permission mode), not strip them and auto-decline.
        has_interactive_parent = bool(
            info.parent_session_key and self._sessions.has_session(info.parent_session_key)
        )
        if info.approval_mode == "auto" or (parent_policy == "auto" and not has_interactive_parent):
            extra_kwargs["unattended"] = True
        # Dry-run replay (T9): observe-mode — write-capable tools don't execute, so
        # the run previews what WOULD happen with no side effects.
        if info.dry_run:
            extra_kwargs["dry_run"] = True
        if info.extra_env:
            # The leaf's posture + lineage. Passed through the session's `extra_env` seam, which
            # already forces a cold (non-pooled) session — a warm pooled worker would carry the
            # PREVIOUS leaf's env, and inheriting a sibling's capability flag is precisely the
            # cross-contamination this must not have.
            extra_kwargs["extra_env"] = dict(info.extra_env)
        client, is_new, _resumed = await self._sessions.get_or_create(
            session_key,
            agent=agent or None,
            approval_policy=parent_policy,
            **extra_kwargs,
        )
        # Intentionally check info.agent (not resolved `agent`) so only
        # explicitly requested agents skip _SYSTEM_PREFIX (defense-in-depth).
        named_agent = bool(info.agent and _AGENT_NAME_RE.fullmatch(info.agent))
        raw_task = info._raw_task or info.task
        if named_agent:
            message = raw_task
        else:
            # The sub-agent system prefix lives in the prompt system (bundled
            # ``subagent-system-prefix`` snippet); fall back to the inline constant.
            from personalclaw.prompt_providers.runtime import render_snippet_block

            prefix = render_snippet_block("subagent-system-prefix")
            prefix = (prefix + "\n\n") if prefix else _SYSTEM_PREFIX
            message = prefix + raw_task
        full_message, _ = self._ctx_builder.build_message(message, is_new, session_key)

        result_text = ""
        turns = 0
        turn_limit = info.max_turns or self._default_turn_limit or _TURN_LIMIT
        # Reports inherited agent (not just info.agent) so telemetry shows
        # the actual agent used for this subagent session.
        await self._fire_event(
            "subagent_spawn", info, {"task": _redact(info.task), "agent": agent or ""}
        )
        # Stream results to disk for orchestrated chat.

        # Record PID for orphan recovery
        try:

            pid = self._sessions.get_pid(session_key)
            if pid:
                info._pid = pid  # make available for _write_tombstone
                update_state(info.id, pid=pid, pid_recorded_at=time.time())
        except Exception:
            logger.debug("Failed to record PID for %s", info.id, exc_info=True)

        # Record session_id and provider type for session file cleanup
        try:
            session_id = client.session_id if hasattr(client, "session_id") else ""
            update_state(info.id, session_id=session_id, provider="acp")
        except Exception:
            logger.debug("Failed to record session_id for %s", info.id, exc_info=True)

        _rp = _agent_dir(info.id) / "result.txt"
        info.result_path = str(_rp)
        # §4.1 read-only research class: resolve ONCE per run. An auto-fired spawn defaults to the
        # research (read-only) class, so its write/execute tools are denied at the approval loop
        # below. Resolved here (not per event) because the class is fixed for the run's lifetime.
        _research_readonly = (
            resolve_capability_class(
                capability_class=info.capability_class, approval_mode=info.approval_mode
            )
            == CAPABILITY_RESEARCH
        )
        async for event in client.stream(full_message):
            if event.kind == EVENT_TEXT_CHUNK:
                result_text += event.text
                write_result_chunk(info.id, event.text)
                redacted = _redact(event.text)
                info.streaming_text += redacted
                if len(info.streaming_text) > 50_000:
                    info.streaming_text = "…(truncated)\n" + info.streaming_text[-40_000:]
                await self._fire_event("subagent_chunk", info, {"text": redacted})
            elif event.kind == EVENT_PERMISSION_REQUEST:
                turns += 1
                info.turns = turns
                info.last_tool = event.title or ""
                # Persist turn state for orphan recovery diagnostics
                try:
                    update_state(info.id, turns=turns, last_tool=event.title or "")
                except Exception:
                    pass
                await self._fire_event(
                    "subagent_tool",
                    info,
                    {"tool": _redact(event.title or ""), "tool_kind": event.tool_kind},
                )
                if turns > turn_limit:
                    info.result = result_text or "_Partial output._"
                    info.error = f"turn_limit:{turn_limit}"
                    info.done = True
                    Stats().inc_subagent_failed()
                    logger.warning("Subagent %s hit turn limit (%d)", info.id, turn_limit)
                    self._write_tombstone(info, "turn_limit")
                    return
                # §4.1: a research-class spawn is read-only. Its write/execute tools are DENIED
                # HERE, at the tool-approval layer, BEFORE any auto-approve branch below can admit
                # them. Placement is load-bearing: an auto-fired research run resolves
                # parent_policy="auto" (from approval_mode="auto"), so a denial placed AFTER that
                # branch would be dead code and the class would be a label, not a control. Uses the
                # SAME ``is_write_tool`` policy the workflow research leaf uses
                # (``leaf_tool_denial``) — a research subagent and a research leaf deny alike.
                if _research_readonly:
                    from personalclaw.workflows.batch_compile import is_write_tool

                    if is_write_tool(event.title or ""):
                        await self._reject_and_log(
                            client,
                            event.request_id,
                            session_key,
                            event,
                            error="research_capability_deny",
                            metadata={
                                "subagent_id": info.id,
                                "capability_class": CAPABILITY_RESEARCH,
                                "tool": event.title or "",
                            },
                        )
                        continue
                tool_result = self._ctx_builder.hooks.on_tool_call(event.title)
                if tool_result.action == TOOL_DENY:
                    await self._reject_and_log(
                        client, event.request_id, session_key, event, error="hook_deny"
                    )
                    continue
                if tool_result.action == TOOL_AUTO_APPROVE:
                    await self._approve_and_log(
                        client,
                        event.request_id,
                        session_key,
                        event,
                        metadata={"subagent_id": info.id, "reason": "hook_auto_approve"},
                    )
                    continue
                if parent_policy == "auto":
                    await self._approve_and_log(
                        client,
                        event.request_id,
                        session_key,
                        event,
                        metadata={"subagent_id": info.id, "reason": "parent_policy_auto"},
                    )
                    continue
                if self._on_tool_approval_factory:
                    approve_cb = self._on_tool_approval_factory(info)
                    approved = await approve_cb(event)
                    if not approved:
                        await self._reject_and_log(
                            client,
                            event.request_id,
                            session_key,
                            event,
                            metadata={"subagent_id": info.id, "reason": "factory_rejected"},
                        )
                        continue
                    await self._approve_and_log(
                        client,
                        event.request_id,
                        session_key,
                        event,
                        metadata={"subagent_id": info.id},
                    )
                elif self._on_tool_approval:
                    approved = await self._on_tool_approval(event, info.parent_session_key)
                    if not approved:
                        await self._reject_and_log(client, event.request_id, session_key, event)
                        continue
                    await self._approve_and_log(
                        client,
                        event.request_id,
                        session_key,
                        event,
                        metadata={"subagent_id": info.id},
                    )
                else:
                    # No callback, no auto policy — deny by default
                    await self._reject_and_log(
                        client,
                        event.request_id,
                        session_key,
                        event,
                        metadata={"subagent_id": info.id, "reason": "no_policy_deny_default"},
                    )
                    continue
            elif event.kind == EVENT_TOOL_CALL:
                # Fire PreToolUse hooks for auto-approved tools (informational only)
                sel().log_tool_invocation(
                    session_key=session_key,
                    source="subagent",
                    tool_name=event.title,
                    tool_kind=event.tool_kind,
                    outcome="auto_approved",
                    metadata={"subagent_id": info.id},
                )
                await fire_tool_hooks(
                    self.hook_store,
                    event.title,
                    event.tool_input,
                    subagent_id=info.id,
                    parent_session_key=info.parent_session_key,
                    agent_role=info.agent,
                )
            elif event.kind == EVENT_COMPLETE:
                # Capture the child's token/cost accounting before breaking — S2k
                # discarded it here (COST-AND-TOKEN-OBSERVABILITY C2, subagent site).
                info.input_tokens = int(getattr(event, "input_tokens", 0) or 0)
                info.output_tokens = int(getattr(event, "output_tokens", 0) or 0)
                cost = float(getattr(event, "cost_usd", 0.0) or 0.0)
                if not cost and info.model:
                    from personalclaw.pricing import estimate_cost

                    cost = estimate_cost(
                        info.model,
                        input_tokens=info.input_tokens,
                        output_tokens=info.output_tokens,
                        cache_read_tokens=int(getattr(event, "cache_read_tokens", 0) or 0),
                        cache_creation_tokens=int(getattr(event, "cache_creation_tokens", 0) or 0),
                    )
                info.cost_usd = cost
                # Write the resolved cost back so the ledger records it without a
                # redundant second estimate (see _record_subagent_usage).
                try:
                    event.cost_usd = cost  # type: ignore[attr-defined]
                except (AttributeError, TypeError):
                    pass
                self._record_subagent_usage(info, session_key, event)
                break

        # Strip [OPTIONS: ...] tags and redact sensitive content
        cleaned, _ = extract_options(result_text) if result_text else (result_text, [])
        if cleaned:
            from personalclaw.security import (
                redact_credentials,
                redact_exfiltration_urls,
            )

            cleaned, _ = redact_exfiltration_urls(cleaned)
            cleaned, _ = redact_credentials(cleaned)
        info.result = cleaned or "_No response._"
        # Cap disk file and trim memory — gateway decides how much to show based on mode.
        if info.result_path:
            from pathlib import Path

            from personalclaw.context_management import cap_result_file, evict_completed_agents

            cap_result_file(Path(info.result_path))
            if len(info.result) > 3000:
                info.result = info.result[:3000]
            evict_completed_agents(self._agents)
        info.done = True
        self._sessions.record_success(session_key)
        # Fold this child's cost into the run-scoped budget and stop the fan-out
        # mid-flight if the run ceiling is now exceeded (C1.5). Charged here, at the
        # one place the per-child cost is known, so the check re-runs after EVERY
        # child rather than once at spawn.
        self._charge_child_and_check_budget(info)
        Stats().inc_subagent_completed()
        logger.info("Subagent %s completed", info.id)

    @staticmethod
    def _record_subagent_usage(info: "SubagentInfo", session_key: str, event: object) -> None:
        """Append one usage-ledger row for a completed subagent turn (source='subagent').

        A fan-out of N children yields N rows, each keyed to the PARENT session so a
        fan-out's total cost is attributable per child. Delegates to the shared
        :func:`personalclaw.usage_ledger.record_from_event` seam — the caller already
        set ``info.cost_usd``/``cost`` from the same event, so the derived cost matches;
        the ledger writes provider-cost-wins / honest-unpriced / fail-open."""
        from personalclaw.usage_ledger import record_from_event

        record_from_event(
            event,
            source="subagent",
            session_key=info.parent_session_key or session_key,
            agent=info.agent or "",
            provider="acp",  # subagents run through the ACP runtime
            model=info.model or "",
        )

    async def cancel(self, agent_id: str) -> bool:
        """Cancel a single subagent — running OR still queued. Returns True if found."""
        info = self._agents.get(agent_id)
        if not info or info.done:
            return False
        info.cancelled = True  # an intentional stop is not a child failure (C1.4)
        if info.queued:
            # Not yet started: drop it from the queue and mark done without a reap.
            self._queue = [qi for qi in self._queue if qi.id != agent_id]
            info.queued = False
            info.done = True
            info.error = "Cancelled by user"
            return True
        info.error = "Cancelled by user"
        await self._force_reap(agent_id, info, time.time() - info.started)
        return True

    async def cancel_fanout(self, fanout_key: str, *, reason: str = "") -> int:
        """Kill EVERY child (running + queued) of one parent/run — "stop this
        fan-out" (C1.4). Returns the number cancelled.

        Keyed on ``_fanout_key`` so a chat fan-out (parent session) and a workflow
        fan-out (``workflow:<run_id>``) are each addressable as a unit. Marks the
        fan-out stopped so a spawn already in flight to the queue is refused too,
        then cancels each child concurrently. Idempotent: a second call finds
        nothing to cancel. Unlike ``cancel_all`` (the shutdown switch) this touches
        ONE fan-out and leaves other runs untouched.
        """
        if not fanout_key:
            return 0
        self._fanout_stops.setdefault(fanout_key, reason or "fan-out cancelled by user")
        victims = [
            info
            for info in list(self._agents.values())
            if not info.done and _fanout_key(info) == fanout_key
        ]
        for info in victims:
            info.cancelled = True
        await asyncio.gather(*(self.cancel(info.id) for info in victims), return_exceptions=True)
        logger.info("Cancelled fan-out %s (%d child(ren))", fanout_key, len(victims))
        return len(victims)

    async def cancel_all(self) -> None:
        """Cancel all running subagents and wait for cleanup."""
        if self._reaper_task and not self._reaper_task.done():
            self._reaper_task.cancel()
            self._reaper_task = None
        tasks_to_await: list[asyncio.Task] = []  # type: ignore[type-arg]
        for agent_id, task in list(self._tasks.items()):
            if not task.done():
                task.cancel()
                tasks_to_await.append(task)
        if tasks_to_await:
            await asyncio.gather(*tasks_to_await, return_exceptions=True)
        self._tasks.clear()

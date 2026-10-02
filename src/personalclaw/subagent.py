"""Subagent orchestration — spawn isolated background agents.

Each subagent gets its own LLM session (via SessionManager) with a
focused system prompt.  Results are announced back to the caller via
a callback.  Max concurrent limit prevents resource exhaustion.

No spawn recursion: subagents cannot spawn other subagents.
"""

import asyncio
import contextlib
import functools
import logging
import os
import signal
import subprocess
import sys
import time
import uuid
from collections.abc import Awaitable, Callable, Iterable, Iterator
from dataclasses import dataclass, field, replace
from functools import partial
from typing import TYPE_CHECKING, Any, Protocol, TypeGuard

from personalclaw import approval_grants, memory_writes
from personalclaw.acp.permission_authority import screen_tool_call
from personalclaw.approval_grants import ToolDecision, decision_of
from personalclaw.cancellation import cancel_and_wait
from personalclaw.config.loader import AppConfig
from personalclaw.context import ContextBuilder
from personalclaw.guardrails.failure import budget_refusal
from personalclaw.hooks import TOOL_AUTO_APPROVE, TOOL_DENY, fire_tool_hooks, safe_read_file
from personalclaw.llm.base import (
    EVENT_COMPLETE,
    EVENT_PERMISSION_REQUEST,
    EVENT_TEXT_CHUNK,
    EVENT_TOOL_CALL,
    EVENT_TOOL_RESULT,
    LLMEvent,
)
from personalclaw.llm.events import (
    TOOL_META_APPROVAL_WAIVED,
    TOOL_META_AUTO_DENIED,
    refusal_audit,
    unasked_outcome,
    unasked_reason,
)
from personalclaw.security import redact_credentials, redact_exfiltration_urls, redact_for_model
from personalclaw.sel import sel
from personalclaw.session import SessionManager
from personalclaw.session_workspace import result_path as _ws_result_path
from personalclaw.stats import Stats
from personalclaw.subagent_ask import spawn_ask, spawn_refusal
from personalclaw.subagent_persistence import (
    _agent_dir,
    create_agent_folder,
    delete_agent_folder,
    prune_stale_tombstones,
    update_state,
    write_result_chunk,
    write_tombstone,
)
from personalclaw.subagent_tier import (
    CAPABILITY_TEXT,
    CallBudget,
    ended_without_answering,
    refuse_unheld,
    run_agent,
    tier_for,
)
from personalclaw.task_modes import declared_level
from personalclaw.textfmt import extract_options
from personalclaw.usage_ledger import spent_rows
from personalclaw.validation import _AGENT_NAME_RE

if TYPE_CHECKING:
    from personalclaw.agents.native.runtime import NativeAgentRuntime

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

# Consecutive child failures within one fan-out before its breaker trips.
# Mirrors ``session._CIRCUIT_BREAKER_THRESHOLD`` — the per-child ``subagent:<id>``
# session is released before it can fail, so the breaker must key on the FAN-OUT.
_CIRCUIT_BREAKER_THRESHOLD = 5


def spawn_approval_id(agent_id: str) -> str:
    """The registry id of the approval a spawn waits on before it starts."""
    return f"spawn:{agent_id}"


def tool_approval_id(agent_id: str, request_id: object) -> str:
    """The registry id of one of a running subagent's tool calls.

    Namespaced by the subagent for the reason a chat's is namespaced by its session: the raw id
    is an ACP agent's JSON-RPC message id, which every subagent's connection counts from the same
    small integers. Two subagents both waiting on ``"1"`` shared one registry row, so one being
    cancelled took the OTHER's approval off every surface while that one was still waiting — and
    the id alone could not say which subagent (and so which run) asked.
    """
    return f"subagent:{agent_id}:{request_id}"


def agent_work_id(agent_id: str) -> str:
    """How a trigger's history row names the agent its fire started (`ActionResult.work_id`), so
    the row can say how that agent's run ended when it does."""
    return f"subagent:{agent_id}"


def approval_subagent_id(approval_id: str) -> str:
    """The subagent an approval belongs to, from either id above — "" when it is not one's."""
    if approval_id.startswith("spawn:"):
        return approval_id[len("spawn:") :]
    if approval_id.startswith("subagent:"):
        agent_id, sep, _request = approval_id[len("subagent:") :].partition(":")
        return agent_id if sep else ""
    return ""


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


def _waiting_note(tool: str) -> str:
    """The clause a time-limit stop adds when the agent was waiting for its owner to answer a
    call: the limit ended the wait, and saying only that it "timed out" read as the agent failing
    at its work."""
    return f" while waiting for you to answer {_redact(tool)[:80]}" if tool else ""


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


def validate_cwd(cwd: str, allowed_roots: list[str], *, run_workdir: str = "") -> tuple[str, str]:
    """Validate a caller-supplied ``cwd`` for a spawn.

    Resolves symlinks and verifies the path is an existing directory inside the workspace (the
    folder the parent session itself works in), under a root the operator added in
    ``agent.subagent_cwd_allowed_roots``, or inside *run_workdir*: the folder of the workflow run
    the spawn is a step of (``workflows.provisioning.run_workdir``: its scratch folder, its own
    worktree, the folder its project is bound to when it works in place, or its project's context
    folder), which the engine put the run's steps in. The list is empty by default, so the
    workspace and a run's own folder are where a spawn may work until the owner names another.

    Args:
        cwd: Caller-supplied absolute path (may contain ``~``).
        allowed_roots: Extra permitted root paths from config (may contain ``~``).
        run_workdir: The spawning run's own folder, ``""`` for any other spawn.

    Returns:
        ``(resolved_cwd, error)``. On success ``error`` is empty and
        ``resolved_cwd`` is the canonical absolute path (realpath-resolved).
        On failure ``error`` is a reason string and ``resolved_cwd`` is empty.
    """
    if not cwd:
        return ("", "")
    try:
        expanded = os.path.expanduser(cwd)
        if not os.path.isabs(expanded):
            return ("", "cwd must be an absolute path")
        resolved = os.path.realpath(expanded)
    except (OSError, ValueError) as exc:
        return ("", f"cwd resolution failed: {exc}")
    if not os.path.isdir(resolved):
        return ("", "cwd does not exist or is not a directory")
    # The workspace defaults to the home's own folder, which no configured root names: a subagent
    # may always work where the session that spawns it works.
    from personalclaw.config.loader import workspace_root

    workspace = os.path.realpath(workspace_root())
    resolved_roots = [workspace] + [os.path.realpath(os.path.expanduser(r)) for r in allowed_roots]
    if run_workdir:
        resolved_roots.append(os.path.realpath(run_workdir))
    for root in resolved_roots:
        if resolved == root or resolved.startswith(root + os.sep):
            return (resolved, "")
    return (
        "",
        f"cwd is not in the workspace ({workspace}) or under any allowed root: {allowed_roots}. "
        "Add its folder in Settings → Agent defaults → Allowed working directories.",
    )


def _run_workdir_for(parent_run: str) -> str:
    """The folder of the workflow run a spawn is a step of, or ``""``.

    ``parent_run`` is ``workflow:<run_id>`` only when the workflow engine dispatches the run's own
    step: no caller of ``/api/spawn`` or of an agent's tool can set it. The folder itself comes
    from that run's record (``workflows.provisioning.run_workdir``), never from the request, so a
    spawn is admitted to its own run's folder and to no other."""
    from personalclaw.workflows.ownership import OWNED_PREFIX

    if not parent_run.startswith(OWNED_PREFIX):
        return ""
    from personalclaw.workflows.provisioning import run_workdir

    return run_workdir(parent_run[len(OWNED_PREFIX) :])


_SYSTEM_PREFIX = (
    "You are a focused sub-agent. Complete the following task concisely. "
    "Do NOT create other agents. Report your result directly.\n"
    "IMPORTANT: Do NOT narrate your own process, failures, retries, or "
    "orchestration decisions. The user does not care how you got the answer. "
    "Only output meaningful, actionable results. Never output greetings or filler.\n\n"
)


# Capability class for a spawn's tool surface. The values mirror the
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

    An explicit ``research``/``mutating``/``text`` always wins — a caller that decided is obeyed.
    An unset class defaults BY CONSTRUCTION: ``research`` (read-only) for an AUTO-FIRED spawn
    (``approval_mode == "auto"`` — a cron/unattended run with no human watching), ``mutating`` for a
    human-watched spawn. This is the plan's "auto-fired runs default read-only" rule: write/execute
    on an unattended run is a creation-time grant a caller passes explicitly (``capability_class=
    "mutating"``), never a capability an auto-fired run acquires by default.
    """
    cc = (capability_class or "").strip().lower()
    if cc in (CAPABILITY_RESEARCH, CAPABILITY_MUTATING, CAPABILITY_TEXT):
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
    # Tool-capability class: "research" (read-only — write/execute tools default-denied at
    # the approval layer), "mutating" (full grant) or "text" (no tools, the task alone). Empty
    # resolves by construction via ``resolve_capability_class`` — auto-fired spawns default to
    # research so an unattended run cannot write without an explicit creation-time grant.
    capability_class: str = ""
    dry_run: bool = False  # observe-mode: write-capable tools don't execute (T9 replay)
    silent: bool = False  # suppress completion notification (dashboard + channel)
    turns: int = 0
    last_tool: str = ""
    max_turns: int = 0
    reaped: bool = False
    streaming_text: str = ""
    elapsed: float = 0.0
    _raw_task: str = ""  # the task as given; masked where the agent's prompt is composed
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
    # resolve against this directory. Validated on spawn by ``validate_cwd``: the workspace,
    # ``AgentConfig.subagent_cwd_allowed_roots``, or the folder of the workflow run it is a step of.
    cwd: str = ""
    # Sandbox provider name: the isolation backend this subagent's ACP worker launches
    # through. ``none`` (the default builtin) composes the host path-sandbox + resource ceilings
    # with no further isolation; an installed ``sandbox`` app supplies a stronger container tier.
    sandbox: str = "none"
    # Per-leaf env for a compiled batch branch (WF2WOR-5 C2): the lineage keys plus the
    # capability flag the tool-handler seam reads, already secret-filtered by `leaf_env`. Carried
    # per-SESSION rather than through `os.environ` because leaves of one batch run concurrently in
    # one gateway process — a process-global flag would leak one leaf's posture onto its siblings.
    extra_env: dict[str, str] = field(default_factory=dict)
    _pid: int | None = None  # PID of ACP agent child process, for tombstone diagnostics
    # Fan-out identity: the run a spawn belongs to. Empty for a lone spawn.
    # The run-scoped concurrency lane, the consecutive-failure breaker and the
    # run budget all key on ``parent_run or parent_session_key`` (see
    # ``_fanout_key``) so a workflow fan-out and a chat fan-out are each scoped.
    parent_run: str = ""
    # True while this spawn is waiting for a slot. A queued spawn carries a
    # REAL, addressable id (this info) and its FULL parameter set, so it can be
    # polled and cancelled before it ever runs; the drain reuses this same info.
    queued: bool = False
    # Set when a spawn is stopped by the user / kill-fan-out / run-budget stop, so
    # the consecutive-failure breaker does NOT count an intentional stop as a
    # child failure.
    cancelled: bool = False
    _outcome_noted: bool = False  # guards double-counting in the breaker/meter
    # The store id of the trigger whose fire started this agent (its `invoke-agent` or
    # `run-prompt` action), or "". An approval it asks for is listed under the trigger, and a call
    # it is denied leaves a note that can run the trigger again (`auto_denials.py`).
    # Last, so no field an app passes by position moves (`sdk.channel` exports this class).
    trigger_id: str = ""
    # What the run is called where a person reads it: the name of the trigger that started it, or
    # the instruction it was given (`triggers.store.run_title`). Its completion notice and the
    # background-agents list lead with it. "" for a run nobody named, which reads by its task. After
    # `trigger_id` for the same reason `trigger_id` is last.
    title: str = ""
    # The owner answered its start with Deny: it never ran, by their own decision, so nothing that
    # reads how it ended calls that a failure (its trigger's history, the notes). `error` still says
    # what happened, for the readers that show it. Last, for the reason `trigger_id` is.
    declined: bool = False
    # What its start asks the owner to allow, as its caller keys it (a workflow step's
    # `engine.stage_request_key`), or "" for a caller that does not. Last, for the reason
    # `trigger_id` is.
    request_key: str = ""
    # When the owner allowed that start: set when she answers Allow, or handed in by a caller
    # resuming a start she allowed before (`spawn(approved_at=…)`), which then starts without
    # asking (`SubagentManager._spawn_grant`). 0 while nobody has. Last, for the reason
    # `trigger_id` is.
    approved_at: float = 0.0
    # A trigger run's reach (`ActionContext.fire_files`, read) and the files it may change
    # (`write_scope`, real paths): its file tools reach both, and a read-only run writes those.
    # Last, for the reason `trigger_id` is.
    may_read: tuple[str, ...] = ()
    may_change: tuple[str, ...] = ()
    # The tools of the calls its own limits refused (`SubagentTier.limited`): its trigger's history
    # then records it as refused, not as a success (`triggers.settle`). Last, as `trigger_id` is.
    refused: list[str] = field(default_factory=list)
    # The app whose `agent` permission started this run, or "" (`subagent_tier`). Last, too.
    app: str = ""


# Delivery callback: a BATCH of completed subagents that all share one
# parent session, delivered in a SINGLE parent turn. Batching is the fix for the
# injection wall — N near-simultaneous completions used to each start a full
# parent turn, serialized behind the per-session ``Semaphore(1)``, so a burst lost
# most results. The manager coalesces per parent; the callback delivers the group
# once.
AnnounceCallback = Callable[[list[SubagentInfo]], Awaitable[None]]

# Event callback: (event_type, info, extra_data) -> None
SubagentEventCallback = Callable[[str, "SubagentInfo", dict], Awaitable[None]]


def _is_native(client: object) -> "TypeGuard[NativeAgentRuntime]":
    """Whether *client* is PersonalClaw's own loop, which stamps each result with how it was
    decided (``llm.events``), rather than an ACP CLI, which decides what to ask about itself."""
    from personalclaw.agents.native.runtime import NativeAgentRuntime

    return isinstance(client, NativeAgentRuntime)


class ToolApprovalCallback(Protocol):
    async def __call__(
        self, event: LLMEvent, parent_session_key: str = ""
    ) -> "bool | ToolDecision":
        pass


class SpawnApprovalCallback(Protocol):
    """Asks whether a subagent may start, with a tool call's permission request (`subagent_ask`)."""

    async def __call__(
        self, event: LLMEvent, parent_session_key: str = ""
    ) -> "bool | ToolDecision":
        pass


@dataclass(frozen=True)
class SubagentLimits:
    """The Settings → Agent defaults → Subagents limits, as they read at one decision."""

    max_concurrent: int
    turn_limit: int
    timeout: int


#: The ``reason`` a spawn's ``auto_approved_spawn`` row has always carried, per grant. Kept so an
#: auditor's existing queries still match; ``decided_by`` beside it is the grant's own name.
_SPAWN_GRANT_REASONS = {
    approval_grants.APPROVAL_MODE: "approval_mode_auto",
    approval_grants.TRIGGER: "trigger_allowed",
    approval_grants.PARENT_TRUST: "parent_trusted",
    approval_grants.HOOK_SETTING: "tool_calls_gated",
    approval_grants.YOLO: "yolo",
    approval_grants.APPROVED_BEFORE_RESUME: "allowed_before_resume",
}


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
        on_done_timeout: float = _ON_DONE_TIMEOUT,
        reset_timeout: float = _RESET_TIMEOUT,
        *,
        limits: Callable[[], SubagentLimits] | None = None,
    ):
        self._sessions = sessions
        self._ctx_builder = ctx_builder
        self._on_done = on_done
        # The three limits are either FIXED (a caller that passes them, like a test) or read from
        # ``limits`` at each decision — the gateway's, which reads Settings → Agent defaults →
        # Subagents, so a lowered cap binds the next spawn without a restart (`approval_grants`,
        # rule 1). A lowered limit must never wait for a restart: it is the looser posture.
        self._limits = limits
        self._fixed_max_concurrent = max_concurrent
        self._fixed_turn_limit = default_turn_limit
        self._fixed_timeout = default_timeout if default_timeout > 0 else _TIMEOUT_SECS
        # Per-run concurrency lane cap. 0 → the global cap (a lone run may use
        # every slot; the lane only bites once TWO fan-outs contend). A caller that
        # wants strict fairness sets it below ``max_concurrent``.
        self._fixed_run_lane_cap = run_lane_cap
        # Delivery coalescing: completions for one parent are buffered for a
        # short window and delivered as ONE batch turn, so a burst of N completions
        # is one parent turn, not N serialized behind the per-session Semaphore(1).
        self._delivery_coalesce_secs = max(0.0, delivery_coalesce_secs)
        # Delivery / reset caps live on the INSTANCE, not as module globals read at
        # await time. A cap read off a global can only be lowered by patching that
        # global, which binds for the duration of the patch BLOCK rather than for the
        # life of the manager — so any await reached outside that window silently used
        # the 1200s/30s production default and parked until pytest-timeout killed the
        # shard (#2996, #3143). Bound to the object and the cap can never be out of
        # force for an await this manager owns.
        self._on_done_timeout = on_done_timeout
        self._reset_timeout = reset_timeout
        self._pending_delivery: dict[str, list[SubagentInfo]] = {}
        self._delivery_tasks: dict[str, asyncio.Task] = {}  # type: ignore[type-arg]
        self._on_tool_approval = on_tool_approval  # fallback for non-auto sessions
        self._on_tool_approval_factory = on_tool_approval_factory
        self._on_spawn_approval = on_spawn_approval
        self._is_yolo = is_yolo
        self._on_event = on_event
        self._running_count = 0
        # Per-fan-out running count: keyed by ``_fanout_key(info)``. A run's
        # lane is capped independently so one wide fan-out cannot starve every other
        # run — the global ``_running_count`` still bounds the whole host.
        self._running_by_fanout: dict[str, int] = {}
        # Consecutive child-failure count per fan-out. ``record_failure`` on
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
        # Queued spawns carry their addressable ``SubagentInfo``: a real id so
        # they can be polled/cancelled, and the FULL parameter set on the info itself
        # (approval_mode/model/silent/dry_run/parent_run) so a drained spawn keeps
        # them instead of silently dropping them and re-entering the interactive gate.
        self._queue: list[SubagentInfo] = []
        self._reaper_task: asyncio.Task | None = None  # type: ignore[type-arg]
        # The grant that last waived an ask in each running agent's runtime (`_policy_source`),
        # so the audit row of a call its runtime approved without asking names who decided.
        self._waived_by: dict[str, str] = {}
        # When each RUNNING agent's run began (`time.monotonic`), the moment its time limit counts
        # from. A spawn still queued for a slot, or still waiting for its owner to approve its
        # start, has no entry: it is not running, and what it waits on bounds the wait.
        self._run_started: dict[str, float] = {}
        # The call each running agent is waiting for its owner to answer, while it waits — so a
        # time limit that ends the wait says that is what it ended.
        self._asking_owner: dict[str, str] = {}

    # ── Limits, as they read now ─────────────────────────────────────────

    def _limits_now(self) -> SubagentLimits | None:
        if self._limits is None:
            return None
        try:
            return self._limits()
        except Exception:  # noqa: BLE001 - an unreadable setting keeps the fixed limits
            logger.warning("subagent limits could not be read; using the fixed ones", exc_info=True)
            return None

    @property
    def _max_concurrent(self) -> int:
        live = self._limits_now()
        return live.max_concurrent if live is not None else self._fixed_max_concurrent

    @property
    def _run_lane_cap(self) -> int:
        return self._fixed_run_lane_cap if self._fixed_run_lane_cap > 0 else self._max_concurrent

    @property
    def _default_turn_limit(self) -> int:
        live = self._limits_now()
        return live.turn_limit if live is not None else self._fixed_turn_limit

    @property
    def _default_timeout(self) -> int:
        live = self._limits_now()
        timeout = live.timeout if live is not None else self._fixed_timeout
        return timeout if timeout > 0 else _TIMEOUT_SECS

    # ── Who may approve, decided now (`approval_grants`) ──────────────────

    def _spawn_grant(self, info: SubagentInfo) -> str:
        """The grant that starts *info* without asking, read now, or ``""`` (the spawn asks).

        A trigger's agent starts on the Allow its trigger was given, when that trigger's action is
        the one that starts it (`triggers.grants.allows_its_agent`): the owner said yes to "use
        the “Invoke Agent” action when it runs", and asking again at the start asked it twice. It
        is a grant for the start alone, so :meth:`_standing_grant` does not read it.
        """
        if info.app:  # its `agent` permission, agreed to at install: the start alone, too
            return approval_grants.APP
        if self._is_yolo and self._is_yolo():
            return approval_grants.YOLO
        if info.approval_mode == "auto":
            return approval_grants.APPROVAL_MODE
        if info.trigger_id:
            from personalclaw.triggers.grants import allows_its_agent

            if allows_its_agent(info.trigger_id):
                return approval_grants.TRIGGER
        if info.parent_session_key and self._sessions.get_approval_policy(
            info.parent_session_key
        ) in ("auto", "yolo"):
            return approval_grants.PARENT_TRUST
        hooks = self._ctx_builder.hooks if self._ctx_builder else None
        if hooks is not None and hooks.auto_approve_subagent_spawn is True:
            return approval_grants.HOOK_SETTING
        if self._allowed_before(info):
            return approval_grants.APPROVED_BEFORE_RESUME
        return ""

    def _allowed_before(self, info: SubagentInfo) -> bool:
        """Whether the owner allowed this same start before, recently enough to stand for it.

        The caller hands her answer back (``spawn(request_key=…, approved_at=…)``) when it resumes
        the start it was for: a workflow step whose attempt a restart or a pause cut off. It stands
        within the time limit a subagent is given: the work she allowed could not have run longer,
        so an answer older than that was about an occasion that is over, and the start asks again.
        A time in the future is not an answer anyone gave.
        """
        if not info.request_key or info.approved_at <= 0:
            return False
        age = time.time() - info.approved_at
        return 0 <= age <= self._default_timeout

    def _standing_grant(self, info: SubagentInfo) -> str:
        """The grant that lets *info*'s agent approve its own tool calls NOW, or ``""``.

        Each one is read at this moment, not at the spawn and not at startup: the chat that started
        the agent (its Trust or YOLO pushed into its policy), the spawn's own ``approval_mode``,
        YOLO, the owner's Approval mode "Auto" (for an agent no chat started, and only once the
        owner chose it: the mode ships asking, `approval_grants.setting_grant`), the hook setting.
        Not checked against the ceiling: :meth:`_grant_now` is.
        """
        if self._sessions.get_approval_policy(info.parent_session_key) in ("auto", "yolo"):
            return approval_grants.PARENT_TRUST
        if info.approval_mode == "auto":
            return approval_grants.APPROVAL_MODE
        if self._is_yolo and self._is_yolo():
            return approval_grants.YOLO
        if not info.parent_session_key and (setting := approval_grants.setting_grant()):
            return setting
        hooks = self._ctx_builder.hooks if self._ctx_builder else None
        if hooks is not None and hooks.auto_approve_subagent_tools is True:
            return approval_grants.HOOK_SETTING
        return ""

    def _grant_now(self, info: SubagentInfo, *, audit: bool) -> str:
        """:meth:`_standing_grant`, if the operator ceiling lets it stand; else ``""``."""
        grant = "" if info.app else self._standing_grant(info)  # never an app's (`subagent_tier`)
        if grant and approval_grants.stands(
            grant,
            caller=info.parent_session_key or f"subagent:{info.id}",
            subject=f"subagent_id={info.id}",
            audit=audit,
        ):
            return grant
        return ""

    def _policy_source(self, info: SubagentInfo) -> Callable[[], str]:
        """What the agent's runtime reads at each decision: ``"auto"`` while a grant stands.

        Handed to the session in place of a fixed policy, so a grant revoked while the agent runs
        stops waiving its next call. Unaudited here — the runtime asks more than once per call; the
        refusal was audited when the agent started, and each call's decision is audited where it
        lands (the permission branch, or the waived call's result).
        """

        def _now() -> str:
            grant = self._grant_now(info, audit=False)
            self._waived_by[info.id] = grant
            return "auto" if grant else ""

        return _now

    async def _fire_granted(
        self, info: SubagentInfo, event: LLMEvent, grant: str, call_inputs: dict[str, Any]
    ) -> None:
        """Tell the gateway a grant approved one of *info*'s calls without asking anyone.

        The gateway settles the Inbox note a previous, unanswered ask of the same call left
        (``approval_state.settle_granted``): the call has now been decided, and ran.
        """
        stored = call_inputs.pop(event.tool_call_id or "", None)
        tool_input = event.tool_input if event.kind == EVENT_PERMISSION_REQUEST else stored
        await self._fire_event(
            "subagent_tool_granted",
            info,
            {"tool": event.title or "", "tool_input": tool_input, "decided_by": grant},
        )

    @staticmethod
    async def _approve_and_log(
        client,
        request_id: str | int,
        session_key: str,
        event: LLMEvent,
        *,
        decided_by: str,
        metadata: dict | None = None,
    ) -> None:
        """Approve and audit it: ``approved`` when a person did, ``auto_approved`` for a grant."""
        await client.approve_tool(request_id)
        sel().log_tool_invocation(
            session_key=session_key,
            source="subagent",
            tool_name=event.title,
            tool_kind=event.tool_kind,
            outcome="approved" if decided_by == approval_grants.YOU else "auto_approved",
            request_id=request_id,
            metadata={**(metadata or {}), "decided_by": decided_by},
        )

    @staticmethod
    async def _reject_and_log(
        client,
        request_id: str | int,
        session_key: str,
        event: LLMEvent,
        *,
        decided_by: str,
        unanswered: str = "",
        error: str | None = None,
        metadata: dict | None = None,
        refused: bool = False,
    ) -> None:
        """Refuse and audit what happened: a policy's ``denied``, a control's ``refused`` (one of
        the shell's own, named in *metadata*), a person's ``rejected``, or nobody's answer —
        ``unanswered`` is ``expired`` or ``cancelled`` — never one as another."""
        await client.reject_tool(request_id)
        sel().log_tool_invocation(
            session_key=session_key,
            source="subagent",
            tool_name=event.title,
            tool_kind=event.tool_kind,
            # Each word spelled out, for the outcome census
            # (`tests/test_audit_outcome_families.py`).
            outcome=(
                "expired"
                if unanswered == "expired"
                else (
                    "cancelled"
                    if unanswered == "cancelled"
                    else ("refused" if refused else ("denied" if error else "rejected"))
                )
            ),
            request_id=request_id,
            error=error or "",
            metadata={**(metadata or {}), "decided_by": decided_by},
        )

    def start_reaper(self) -> None:
        """Start the periodic reaper loop.  Call once after the event loop is running.

        What a PREVIOUS run left behind is settled by the gateway once its dashboard is up
        (`subagent_orphans.reconcile_orphans`), because the owner is told through it."""
        if self._reaper_task is None:
            self._reaper_task = asyncio.create_task(self._reaper_loop())

    async def _reaper_loop(self) -> None:
        """Periodically force-kill subagents that exceed the timeout.

        Defense-in-depth: catches cases where ``asyncio.wait_for`` in
        ``_run()`` fails to fire (event-loop saturation, orphaned tasks,
        or ``reset()`` hanging in the finally block).

        So it measures what that deadline measures: the time since the agent's RUN began
        (``_run_started``). It used to measure from the spawn REQUEST, so it also stopped a spawn
        still waiting for its owner to approve its start, or for a slot, and a loop's step asking
        at the start of a two-hour approval wait was stopped at thirty minutes as a failure.
        """
        while True:
            await asyncio.sleep(_REAPER_INTERVAL)
            now = time.monotonic()
            for agent_id, info in list(self._agents.items()):
                started = self._run_started.get(agent_id)
                if info.done or started is None:
                    continue
                elapsed = now - started
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

    async def _force_reap(
        self, agent_id: str, info: SubagentInfo, elapsed: float, *, reason: str = ""
    ) -> None:
        """Kill a subagent's session process and mark it done.

        *reason* is a cancel's own error; empty for the reaper's deadline kill, which states its
        own.
        """
        session_key = f"subagent:{agent_id}"

        # Kill the process FIRST so the pipe unblocks, then cancel the task.
        try:
            await asyncio.wait_for(self._sessions.reset(session_key), timeout=self._reset_timeout)
        except asyncio.TimeoutError:
            logger.warning("Reaper: reset hung for %s, attempting SIGKILL", agent_id)
            self._sigkill_session(session_key)
        except Exception:
            logger.exception("Reaper: reset failed for %s", agent_id)

        task = self._tasks.pop(agent_id, None)
        if task and not task.done():
            task.cancel()
        self._run_started.pop(agent_id, None)
        asking = self._asking_owner.pop(agent_id, "")

        if not info.done:
            info.done = True
            # A cancel names its own reason, and it is the true one: the deadline sentence is for
            # the reaper's own kill, and stamping it over a cancel told the activity view that a
            # subagent stopped seconds in had "exceeded" its 30-minute cap.
            context = _timeout_context(info, include_elapsed=False)
            info.error = reason or (
                f"Reaped after {int(elapsed)}s "
                f"(exceeded {self._default_timeout}s deadline){_waiting_note(asking)} [{context}]"
            )
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
            # Reaped completions go through the SAME coalesced batch delivery,
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
        learns about the failure on the next ``run_chat`` turn and can read
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

            # Queue for LLM context drain on next run_chat
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
                "title": _r(a.title),
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
        *,
        trigger_id: str = "",
        title: str = "",
        request_key: str = "",
        approved_at: float = 0.0,
        may_read: tuple[str, ...] = (),
        may_change: tuple[str, ...] = (),
        app: str = "",
    ) -> SubagentInfo | None:
        """Spawn a subagent for *task*.

        Approval priority (first match wins), read when the spawn is admitted:

        1. A standing grant (:meth:`_spawn_grant`: YOLO, ``approval_mode="auto"`` from the
           caller, the Allow of the trigger whose action starts it, the parent chat's Trust,
           ``auto_approve_subagent_spawn``, the owner's Allow of this same start before a
           restart) → immediate
           execution, but only if the operator ceiling lets that grant stand
           (``approval_grants.stands``). Under ``approval: ask`` none does, and the spawn is
           asked like any other.
        2. ``on_spawn_approval`` callback → interactive approval
        3. Otherwise → rejected

        When ``approval_mode="auto"`` is set, it has two effects, both bounded by the ceiling:
        - Skips the spawn approval gate (this method)
        - Lets the subagent's runtime approve its own tool calls while the grant stands
          (:meth:`_policy_source`, read at each call, not once for the agent's lifetime).

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
            trigger_id (str): The trigger whose fire this is (``ActionContext.trigger_id``),
                kept on the info so its approvals and denials can name and re-run it.
            title (str): What the run is called where a person reads it — for a trigger's run,
                its name or its instruction (``triggers.store.run_title``). Its completion notice
                is titled with it and leads with what the agent said.
            request_key (str): What this start asks the owner to allow, as the caller keys it,
                kept on the info with the time she allows it (``approved_at``), so a caller that
                resumes the same start after a restart can hand her answer back.
            approved_at (float): When the owner allowed this same start before — the caller's
                own record of her answer, handed back to resume it. The start then does not
                ask again, within the time limit a subagent is given (:meth:`_spawn_grant`).
            app (str): The app whose ``agent`` permission starts this run (``subagent_tier``).

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

        # --- Incident kill switch: refuse spawns during an incident ----------
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

        # --- Budget guard: refuse to spawn if the day-scope token ceiling is hit ---
        # A subagent is unattended work; if the day's token budget is already
        # exhausted, don't start another one (§1.1 pause-into-refuse). An UNREADABLE
        # ceiling refuses too (#3458) — it follows `proactive/autoexec.py`'s "an unverified
        # ceiling authorises nothing" rather than this seam's old blanket fail-open,
        # because a spawn is exactly the unattended spend the ceiling exists to bound.
        # Any other error still fails open: the budget is a guardrail, not a hard gate.
        try:
            from personalclaw.guardrails.budgets import (
                BudgetConfigUnreadable,
                BudgetVerdict,
                budget_from_config,
                get_meter,
            )

            try:
                _day_budget = budget_from_config()
            except BudgetConfigUnreadable as _exc:
                logger.warning("Subagent spawn refused: %s", _exc)
                sel().log_tool_invocation(
                    session_key=parent_session_key or "",
                    source="subagent",
                    tool_name="subagent_run",
                    outcome="refused_budget_unverified",
                    metadata={"reason": str(_exc), "task": _redacted_task[:120]},
                )
                return SubagentInfo(
                    id=uuid.uuid4().hex[:8],
                    task=_redacted_task,
                    agent=agent,
                    done=True,
                    error=f"spawn refused: {_exc}, so nothing ran (fix "
                    f"`guardrails.budgets` in config.json)",
                )
            if not _day_budget.is_unlimited:
                # The token ceiling only: a spent dollar one refuses the spawn's calls that cost
                # money where they are made, and one on a model that costs nothing still runs.
                _verdict, _reason = get_meter().check_day_before_work(_day_budget)
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
                # Fail closed: with the config unavailable, no folder the owner might have added
                # counts, so a subagent may work in the workspace (and a run's step in its own
                # run's folder) and nowhere else. `[]` is also the field's default, so a
                # config.json the loader had to discard (`AppConfig.load` returns defaults and
                # never raises for that) lands on the same narrow list without this arm. What
                # remains here is defence in depth for an unexpected raise.
                allowed_roots = []
            resolved_cwd, cwd_err = validate_cwd(
                cwd, allowed_roots, run_workdir=_run_workdir_for(parent_run)
            )
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

        # --- Agent validation: an unknown agent is a TYPED error, never a
        # silent downgrade. Done BEFORE any queue slot so a bad name fails fast and a
        # queued spawn is never drained onto the wrong agent. ---
        if agent:
            agent, err = _validate_agent(agent)
            if err:
                return SubagentInfo(
                    id=uuid.uuid4().hex[:8], task=_redacted_task, agent="", done=True, error=err
                )
        agent = run_agent(capability_class, agent)

        # --- Build the addressable info up front: a queued spawn carries a
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
            capability_class=(capability_class or "").strip().lower(),
            dry_run=dry_run,
            silent=silent,
            max_turns=max_turns,
            model=model or "",
            cwd=resolved_cwd,
            parent_run=parent_run,
            sandbox=sandbox or "none",
            extra_env=dict(extra_env or {}),
            trigger_id=trigger_id or "",
            title=redact_credentials(redact_exfiltration_urls(title or "")[0])[0],
            request_key=request_key or "",
            approved_at=float(approved_at or 0.0) if request_key else 0.0,
            may_read=tuple(may_read),
            may_change=tuple(may_change),
            app=app,
        )
        info._raw_task = task  # masked by `redact_for_model` when the prompt is composed
        memory_writes.hand_on(agent_work_id(agent_id), parent_session_key)  # keeps what it keeps

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
        # per-run lane reserves host headroom for other runs, so one wide
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

        # A standing grant approves the spawn without asking: read NOW (rule 1 of
        # `approval_grants`), and only if the operator ceiling lets it stand (rule 2) — an
        # `approval: ask` ceiling has every spawn asked, whatever the spawn argument, a toggle or
        # the hook setting says. The audit row names the grant (rule 3); YOLO's had none.
        grant = self._spawn_grant(info)
        if grant and approval_grants.stands(
            grant,
            caller=info.parent_session_key or f"subagent:{agent_id}",
            subject=f"subagent_run,subagent_id={agent_id}",
        ):
            self._tasks[agent_id] = asyncio.create_task(self._run(info))
            self._log_spawned(info)
            sel().log_tool_invocation(
                session_key=info.parent_session_key,
                source="subagent",
                tool_name="subagent_run",
                outcome="auto_approved_spawn",
                metadata={
                    "subagent_id": agent_id,
                    "reason": _SPAWN_GRANT_REASONS.get(grant, grant),
                    "decided_by": grant,
                },
            )
        elif self._on_spawn_approval:
            self._tasks[agent_id] = asyncio.create_task(self._spawn_with_approval(info))
        elif self._ctx_builder and self._ctx_builder.hooks:
            info.done = True
            info.error = "spawn rejected: no approval mechanism configured"
            self._dec_running(info)
            self._drain_queue()
            sel().log_tool_invocation(
                session_key=info.parent_session_key,
                source="subagent",
                tool_name="subagent_run",
                outcome="rejected_spawn",
                metadata={
                    "subagent_id": agent_id,
                    "reason": "no_approval_mechanism",
                    "decided_by": "no_approval_mechanism",
                },
            )
            return
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
                metadata={
                    "subagent_id": agent_id,
                    "reason": "no approval mechanism",
                    "decided_by": "no_approval_mechanism",
                },
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

    # ── Run-scoped concurrency lane ─────────────────────────────────────

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
        in-flight children are cancelled. Fail-open: a budget is a guardrail — except for
        an UNREADABLE ceiling, which stops the fan-out (#3458), because the run scope is
        the only thing that bounds N children each spending under one day snapshot.

        The DAY scope is charged where each child's calls are made, once: by the spend guard on
        a native child's model, and on an agent CLI's own turns (``acp.spend``), since every
        spawn rides the metered orchestration axis. So only the fan-out's run scope is charged
        here. Charging the day here as well counted a child twice, so the cap bit at half the
        real spend and its refusal named a total the day had not spent.
        """
        fkey = _fanout_key(info)
        try:
            from personalclaw.guardrails.budgets import (
                BudgetConfigUnreadable,
                BudgetVerdict,
                get_meter,
                run_budget_from_config,
            )

            meter = get_meter()
            tokens = info.input_tokens + info.output_tokens
            try:
                budget = run_budget_from_config()
            except BudgetConfigUnreadable as exc:
                if fkey not in self._fanout_stops:
                    self._fanout_stops[fkey] = f"{exc}, so the fan-out stopped"
                    logger.warning("Subagent fan-out %s stopped: %s", fkey or "-", exc)
                return
            if budget.is_unlimited:
                return
            meter.charge_run(fkey, tokens, info.cost_usd)
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
        request_id: str = spawn_approval_id(info.id)
        try:
            decision = decision_of(
                await self._on_spawn_approval(
                    spawn_ask(request_id, info.task, info.agent), info.parent_session_key
                )
            )
        except Exception:
            logger.exception("Spawn approval failed for %s", info.id)
            decision = ToolDecision(False, "rejected", "approval_failed")

        if not decision:
            info.done = True
            info.error = spawn_refusal(decision)
            # A person's Deny, and only that: a window nobody answered, or an approval that could
            # not be asked, is not their decision.
            info.declined = (
                decision.outcome == "rejected" and decision.decided_by == approval_grants.YOU
            )
            self._dec_running(info)
            self._drain_queue()
            self._tasks.pop(info.id, None)
            sel().log_tool_invocation(
                session_key=info.parent_session_key,
                source="subagent",
                tool_name="subagent_run",
                # What happened, not a Deny for each: nobody answering in time is `expired`.
                outcome=(
                    "expired"
                    if decision.outcome == "expired"
                    else ("cancelled" if decision.outcome == "cancelled" else "rejected")
                ),
                metadata={"subagent_id": info.id, "decided_by": decision.decided_by},
            )
            logger.info("Subagent %s spawn rejected (%s)", info.id, decision.outcome)
            if self._on_done:
                await self._safe_announce(info)
            return

        sel().log_tool_invocation(
            session_key=info.parent_session_key,
            source="subagent",
            tool_name="subagent_run",
            outcome="approved" if decision.decided_by == approval_grants.YOU else "auto_approved",
            metadata={"subagent_id": info.id, "decided_by": decision.decided_by},
        )
        if decision.decided_by == approval_grants.YOU and info.request_key:
            # Her answer, for the caller to keep with the work it started: resuming this start
            # after a restart hands it back instead of asking her again.
            info.approved_at = time.time()
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
                title=info.title,
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

    @contextlib.contextmanager
    def _waiting_for_owner(self, info: SubagentInfo, tool: str) -> Iterator[None]:
        """Note, while it lasts, that the agent is waiting for its owner to answer *tool*.

        The wait still counts against the agent's time limit — Settings says an approval a
        running agent asks for ends with that work's time limit — so what this changes is what a
        stop in the middle of it says: that it was waiting for you, not that it failed.

        Cleared only when the answer comes, deliberately NOT in a ``finally``: a stop cancels the
        wait before ``_run`` words the stop, and ``_run`` (or the reaper) clears it after.
        """
        self._asking_owner[info.id] = tool or "a call"
        yield
        self._asking_owner.pop(info.id, None)

    async def _run(self, info: SubagentInfo) -> None:
        """Execute a subagent task in its own session."""
        session_key = f"subagent:{info.id}"
        # The time limit as Settings reads it when this agent starts (the reaper re-reads it). It
        # counts from HERE, the start of the run, which the reaper reads too (`_run_started`): a
        # wait for a slot or for its owner to approve the start is not the run.
        timeout = self._default_timeout
        self._run_started[info.id] = time.monotonic()
        try:
            await asyncio.wait_for(self._run_inner(info, session_key), timeout=timeout)
        except asyncio.TimeoutError:
            if not info.reaped:
                waiting = _waiting_note(self._asking_owner.get(info.id, ""))
                info.error = (
                    f"Timed out after {timeout // 60} minutes{waiting} [{_timeout_context(info)}]"
                )
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
            cap = budget_refusal(exc)  # a spend cap's refusal is said as itself, and not traced
            if not info.reaped:
                info.error = cap.sentence() if cap is not None else str(exc)
                info.done = True
                Stats().inc_subagent_failed()
                self._write_tombstone(info, "error")
            (logger.warning if cap else logger.exception)("Subagent %s failed: %s", info.id, exc)
        finally:
            self._waived_by.pop(info.id, None)
            self._run_started.pop(info.id, None)
            self._asking_owner.pop(info.id, None)
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
                        # Per-child cost/tokens for the activity panel — the
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
                # Record the child's outcome against the fan-out breaker
                # BEFORE draining, so a failing fan-out trips before the next child
                # takes its freed slot.
                self._note_child_outcome(info)
                self._drain_queue()
                self._maybe_clear_fanout(_fanout_key(info))
                try:
                    await asyncio.wait_for(
                        self._sessions.reset(session_key), timeout=self._reset_timeout
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
            # Enqueue for COALESCED batch delivery instead of starting a
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

        The whole batch is delivered under ONE ``self._on_done_timeout``. On a delivery
        FAILURE the orchestrator's context is PRESERVED — the parent session is NOT
        reset (the old remedy wiped the very conversation that asked for the work);
        the failure is surfaced per child via ``notify_injection_failed`` instead.
        """
        batch = self._pending_delivery.pop(parent_key, [])
        if not batch or not self._on_done:
            return
        try:
            await asyncio.wait_for(self._on_done(batch), timeout=self._on_done_timeout)
            for info in batch:
                if not info.error:
                    self._cleanup_delivered(info)
        except asyncio.TimeoutError:
            logger.error(
                "Subagent batch delivery for %s timed out after %.0fs (%d result(s)) — "
                "parent context PRESERVED, surfacing failure",
                parent_key,
                self._on_done_timeout,
                len(batch),
            )
            # DO NOT reset the parent session: resetting wiped the
            # orchestrator's context. Surface the failure to the parent instead so
            # the LLM can read each result from disk on its next turn.
            for info in batch:
                self.notify_injection_failed(
                    info,
                    reason=f"batch delivery timed out after {int(self._on_done_timeout)}s",
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
        # Who may approve this agent's tool calls without asking. Read NOW, and again at every
        # call it makes (`_policy_source`, handed to its session below): the chat that started it,
        # the spawn's own `approval_mode`, YOLO, the owner's Approval mode "Auto" for an agent no
        # chat started, the hook setting (`approval_grants`, rule 1). Bounded by the operator
        # ceiling (rule 2): a refusal is audited here, once, and the calls then ask.
        grant = self._grant_now(info, audit=True)
        if grant:
            sel().log_api_access(
                caller=info.parent_session_key or f"subagent:{info.id}",
                operation=f"subagent.approval_grant:{grant}",
                outcome="ok",
                source="subagent",
                resources=f"subagent_id={info.id}",
            )
        parent_policy = "auto" if grant else ""
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
        if info.cwd:
            extra_kwargs["cwd"] = info.cwd
        # Sandbox provider: thread the chosen isolation backend to the ACP worker
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
        #
        # And it follows only a grant that STANDS. Under an `ask` ceiling a spawn's own
        # `approval_mode: "auto"` grants nothing (the spawn itself asked you), so its agent is not
        # headless either: each call it makes reaches the relay and asks you, as an agent no grant
        # covers does, instead of being declined with nobody asked.
        has_interactive_parent = bool(
            info.parent_session_key and self._sessions.has_session(info.parent_session_key)
        )
        if grant and (info.approval_mode == "auto" or not has_interactive_parent):
            extra_kwargs["unattended"] = True
        # Dry-run replay (T9): observe-mode — write-capable tools don't execute, so
        # the run previews what WOULD happen with no side effects.
        if info.dry_run:
            extra_kwargs["dry_run"] = True
        if info.may_read or info.may_change:
            extra_kwargs["extra_tool_roots"] = [*info.may_read, *info.may_change]
        from personalclaw.workflows.provisioning import step_reads

        if reads := step_reads(info.parent_run):  # its run's project tree, its batch's folder
            extra_kwargs["read_tool_roots"] = reads
        if info.extra_env:
            # The leaf's posture + lineage. Passed through the session's `extra_env` seam, which
            # already forces a cold (non-pooled) session — a warm pooled worker would carry the
            # PREVIOUS leaf's env, and inheriting a sibling's capability flag is precisely the
            # cross-contamination this must not have.
            extra_kwargs["extra_env"] = dict(info.extra_env)
        client, is_new, _resumed = await self._sessions.get_or_create(
            session_key,
            agent=agent or None,
            # The spawn's own model when it names one; with none, the orchestration chain
            # serves it, and an unbound axis falls back to chat.
            model=info.model or None,
            approval_policy=parent_policy,
            approval_source=self._policy_source(info),
            # EVERY spawn rides the orchestration axis, one that names a model too: the model
            # rides beside the axis, as a loop's own model rides the loops axis. The axis is not
            # only which chain serves a model-less spawn — it is what puts the spend guard on the
            # child's calls. A spawn given a model used to take the chat binding instead, which
            # has no guard, so the daily dollar cap never counted what it spent.
            model_axis="orchestration",
            **extra_kwargs,
        )
        # Intentionally check info.agent (not resolved `agent`) so only
        # explicitly requested agents skip _SYSTEM_PREFIX (defense-in-depth).
        named_agent = bool(info.agent and _AGENT_NAME_RE.fullmatch(info.agent))
        # Composed from what a parent model, a trigger or a workflow step read, so it is masked.
        raw_task = redact_for_model(info._raw_task or info.task)
        if named_agent:
            message = raw_task
        else:
            # The sub-agent system prefix lives in the prompt system (bundled
            # ``subagent-system-prefix`` snippet); fall back to the inline constant.
            from personalclaw.prompt_providers.runtime import render_snippet_block

            prefix = render_snippet_block("subagent-system-prefix")
            prefix = (prefix + "\n\n") if prefix else _SYSTEM_PREFIX
            message = prefix + raw_task
        from personalclaw.context_headroom import resolve_window

        full_message = message  # a text run is handed the task alone (`subagent_tier`)
        if info.capability_class != CAPABILITY_TEXT:
            full_message, _ = self._ctx_builder.build_message(
                message, is_new, session_key, window=await resolve_window(serving=client)
            )

        result_text = ""
        info.turns = 0
        budget = CallBudget(info.max_turns or self._default_turn_limit or _TURN_LIMIT)
        counted = partial(budget.past_it, fire=self._fire_event, tombstone=self._write_tombstone)
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

        # Record session_id for session file cleanup, and the runtime that serves it (`native`, or
        # the agent CLI's `acp:<cli>`) with the model it sends to, for anyone reading it live.
        try:
            session_id = client.session_id if hasattr(client, "session_id") else ""
            update_state(
                info.id,
                session_id=session_id,
                provider=str(getattr(client, "provider_id", "") or "acp"),
                model=str(getattr(client, "served_model_ref", "") or info.model or ""),
            )
        except Exception:
            logger.debug("Failed to record session_id for %s", info.id, exc_info=True)

        _rp = _agent_dir(info.id) / "result.txt"
        info.result_path = str(_rp)
        # §4.1 read-only research class: resolve ONCE per run, before the stream opens (`tier_for`).
        # An auto-fired spawn defaults to the research (read-only) class, so its write/execute
        # tools are denied at the approval loop below. A call is within the grant by what its tool
        # DECLARES, asked as a research leaf's and a room critic's are, and an automation's own
        # agent may also tell the owner what it found (`SubagentTier`, which also tallies how the
        # agent's calls came out).
        tier = tier_for(info)
        _capability_class, _tool_profile = tier.capability_class, tier.profile

        # 🔴 The grants are enforced in the approval loop below, which sees only the calls that
        # ASK. A native runtime answers an ask itself while a standing grant stands (its policy
        # source says `auto`), so none of its calls reached that loop and a research run's write
        # tools ran. It is held to the same tier, asked before its own approval (`tier.hold`).
        native = _is_native(client)
        if _is_native(client):
            tier.hold(client)
        elif refuse_unheld(info, agent):  # a run its runtime cannot hold to its tier
            return
        # Each call's input by its id, from the moment it is made to its result: a call its runtime
        # approved from the policy is reported with the input it ran with (`_fire_granted`).
        call_inputs: dict[str, Any] = {}
        # The calls that were ASKED about. Each is audited where it is answered, below; every
        # other call is audited once, at its result (`llm.events.unasked_outcome`).
        asked: set[str] = set()
        spent = functools.partial(self._record_subagent_usage, info, session_key)
        # The turn's terminal event, which says how it stopped (`ended_without_answering`).
        ending: LLMEvent | None = None
        async for event in spent_rows(client.stream(full_message), spent):
            if event.kind == EVENT_TEXT_CHUNK:
                result_text += event.text
                write_result_chunk(info.id, event.text)
                redacted = _redact(event.text)
                info.streaming_text += redacted
                if len(info.streaming_text) > 50_000:
                    info.streaming_text = "…(truncated)\n" + info.streaming_text[-40_000:]
                await self._fire_event("subagent_chunk", info, {"text": redacted})
            elif event.kind == EVENT_PERMISSION_REQUEST:
                asked.add(event.tool_call_id or "")
                call_id = event.tool_call_id or f"ask:{event.request_id}"
                if await counted(info, event, call_id):
                    info.result = result_text or "_Partial output._"
                    return
                # The spawn's TOOL GRANTS decide, and they are enforced HERE, at the
                # tool-approval layer, BEFORE any auto-approve branch below can admit the call.
                # Placement is load-bearing: an auto-fired research run resolves
                # parent_policy="auto" (from approval_mode="auto"), so a denial placed AFTER that
                # branch would be dead code and the grant would be a label, not a control. It is
                # the check the native runtime is handed, on what the request says its tool
                # declares; so a ceiling that narrowed this spawn's tools refuses the rest even for
                # a MUTATING class. An ACP child's own tool declares nothing, so only its read-only
                # shell commands pass a `read` grant.
                _grant_deny = tier.refusal(
                    event.title or "",
                    event.risk_level,
                    event.tool_kind,
                    event.tool_input,
                    proposes=event.proposes,
                    tells_owner=event.tells_owner,
                )
                if _grant_deny:
                    tier.refused(call_id, event.title or "", _grant_deny, limit=True)
                    await self._reject_and_log(
                        client,
                        event.request_id,
                        session_key,
                        event,
                        decided_by="tool_grants",
                        error="tool_grants_deny",
                        metadata={
                            "subagent_id": info.id,
                            "capability_class": _capability_class,
                            "tool_grants": _tool_profile.tool_grants,
                            "tool": event.title or "",
                            "reason": _grant_deny,
                        },
                    )
                    continue
                # Shell checks see the command that would RUN, not only the CLI's title.
                tool_result = screen_tool_call(
                    self._ctx_builder.hooks, event.title or "", event.tool_input, info.cwd or None
                )
                if tool_result.action == TOOL_DENY:
                    tier.refused(
                        call_id, event.title or "", tool_result.reason or "a hook blocked it"
                    )
                    control = tool_result.audit()
                    await self._reject_and_log(
                        client,
                        event.request_id,
                        session_key,
                        event,
                        decided_by=control.get("control", "hook_deny"),
                        error="hook_deny",
                        metadata={"subagent_id": info.id, **control},
                        refused=bool(control),
                    )
                    continue
                # The operator's own hook pattern is a grant too, and "a hook decides" is a level
                # an `ask` ceiling refuses (`approval_grants.LEVEL_HOOK`). It used to be checked
                # before, and instead of, the one ceiling check a subagent's calls had.
                if tool_result.action == TOOL_AUTO_APPROVE and approval_grants.stands(
                    approval_grants.HOOK_PATTERN,
                    caller=f"subagent:{info.id}",
                    subject=_redact(event.title or "")[:80],
                    level=approval_grants.LEVEL_HOOK,
                ):
                    await self._approve_and_log(
                        client,
                        event.request_id,
                        session_key,
                        event,
                        decided_by=approval_grants.HOOK_PATTERN,
                        metadata={"subagent_id": info.id, "reason": "hook_auto_approve"},
                    )
                    await self._fire_granted(info, event, approval_grants.HOOK_PATTERN, call_inputs)
                    continue
                # A call whose tool declares it only reads asks nobody, as a native agent's never
                # does: an ACP child asks about every call, so it is answered here, past the
                # grants and hooks above, and never relayed to a person.
                if declared_level(event.risk_level) == "safe":
                    await self._approve_and_log(
                        client,
                        event.request_id,
                        session_key,
                        event,
                        decided_by=approval_grants.DECLARED_READ,
                        metadata={"subagent_id": info.id, "reason": approval_grants.DECLARED_READ},
                    )
                    await self._fire_granted(
                        info, event, approval_grants.DECLARED_READ, call_inputs
                    )
                    continue
                # A standing grant, read at THIS call (it may have been revoked since the agent
                # started) and bounded by the ceiling.
                grant = self._grant_now(info, audit=True)
                if grant:
                    await self._approve_and_log(
                        client,
                        event.request_id,
                        session_key,
                        event,
                        decided_by=grant,
                        metadata={"subagent_id": info.id, "reason": "parent_policy_auto"},
                    )
                    await self._fire_granted(info, event, grant, call_inputs)
                    continue
                if self._on_tool_approval_factory:
                    approve_cb = self._on_tool_approval_factory(info)
                    with self._waiting_for_owner(info, event.title or ""):
                        decision = decision_of(await approve_cb(event))
                elif self._on_tool_approval:
                    # The callback lists the call under an id that names THIS subagent; the
                    # client is still answered on the agent's own raw id (`event` below).
                    with self._waiting_for_owner(info, event.title or ""):
                        decision = decision_of(
                            await self._on_tool_approval(
                                replace(
                                    event, request_id=tool_approval_id(info.id, event.request_id)
                                ),
                                info.parent_session_key,
                            )
                        )
                else:
                    # No callback, no auto policy — deny by default
                    tier.refused(call_id, event.title or "", "nobody could approve it", limit=True)
                    await self._reject_and_log(
                        client,
                        event.request_id,
                        session_key,
                        event,
                        decided_by="no_approval_mechanism",
                        metadata={"subagent_id": info.id, "reason": "no_policy_deny_default"},
                    )
                    continue
                if not decision:
                    tier.declined(call_id, event.title or "", decision.outcome)
                    await self._reject_and_log(
                        client,
                        event.request_id,
                        session_key,
                        event,
                        decided_by=decision.decided_by,
                        unanswered=(
                            decision.outcome if decision.outcome in ("expired", "cancelled") else ""
                        ),
                        metadata={"subagent_id": info.id},
                    )
                    continue
                await self._approve_and_log(
                    client,
                    event.request_id,
                    session_key,
                    event,
                    decided_by=decision.decided_by,
                    metadata={"subagent_id": info.id},
                )
            elif event.kind == EVENT_TOOL_CALL:
                # The call is being MADE, and nothing is decided yet: the native loop yields this
                # card before its own gates run. It is audited where it is decided — the branch
                # above for an asked call, its result for any other. A row here said
                # `auto_approved` for every call, a refused one and a person's Allow included.
                if event.tool_call_id:
                    call_inputs[event.tool_call_id] = event.tool_input
                    if await counted(info, event, event.tool_call_id):
                        info.result = result_text or "_Partial output._"
                        return
                await fire_tool_hooks(
                    self.hook_store,
                    event.title,
                    event.tool_input,
                    subagent_id=info.id,
                    parent_session_key=info.parent_session_key,
                    agent_role=info.agent,
                )
            elif event.kind == EVENT_TOOL_RESULT:
                meta = event.tool_meta or {}
                tier.result(event.tool_call_id or "", event.title or "", meta if native else {})
                if (event.tool_call_id or "") not in asked:
                    # Nobody was asked, so this is the call's one audit row, from what its runtime
                    # stamped: refused by one of its gates, declined with nobody to ask, answered
                    # from the live policy source (the grant standing at that moment, named), or
                    # a tool that asks nobody. An ACP CLI stamps nothing: a call it never asked
                    # the host about ran on the CLI's own say.
                    refusal = refusal_audit(meta)
                    waived = bool(meta.get(TOOL_META_APPROVAL_WAIVED)) and not refusal
                    decided_by = (
                        self._waived_by.get(info.id) or approval_grants.SESSION_POLICY
                        if waived
                        else (unasked_reason(meta) if native else "not_asked_by_cli")
                    )
                    sel().log_tool_invocation(
                        session_key=session_key,
                        source="subagent",
                        tool_name=event.title,
                        tool_kind=event.tool_kind,
                        outcome=unasked_outcome(meta),
                        request_id=event.tool_call_id or "",
                        metadata={
                            "subagent_id": info.id,
                            "reason": decided_by,
                            "decided_by": decided_by,
                            **refusal,
                        },
                    )
                    if waived:
                        await self._fire_granted(info, event, decided_by, call_inputs)
                if meta.get(TOOL_META_AUTO_DENIED):
                    # The native runtime declined a call that needed an approval: a subagent is
                    # unattended, so nobody could be asked. It told the model; this tells the
                    # owner, through the gateway, which can reach the Inbox.
                    await self._fire_event(
                        "subagent_auto_denied", info, {"tool": _redact(event.title or "")}
                    )
                call_inputs.pop(event.tool_call_id or "", None)
            elif event.kind == EVENT_COMPLETE:
                # Capture the child's token/cost accounting before breaking — S2k
                # discarded it here (subagent site).
                from personalclaw.routing.rates import price_event
                from personalclaw.usage_ledger import answered_model, answered_provider

                info.input_tokens = int(getattr(event, "input_tokens", 0) or 0)
                info.output_tokens = int(getattr(event, "output_tokens", 0) or 0)
                ending = event
                # Priced by the entry and model that answered, which a spawn with no model of its
                # own never named: its child ran on the chain's head and was charged nothing. An
                # ACP child names neither, and is priced by its runtime and the model it chose.
                cost = price_event(
                    event,
                    provider=answered_provider(event, "acp"),
                    model=answered_model(event, info.model),
                ).dollars
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
        # Every call refused, or its model out of output room before it answered: it did nothing it
        # was asked, so it ends not done, with why, and its reply stays its result. Set with
        # `done`, so nothing reads it finished without the reason.
        couldnt, cause = ended_without_answering(tier, ending, result_text)
        info.error, info.refused = info.error or couldnt, tier.limited()
        info.done = True
        self._sessions.record_success(session_key)
        # Fold this child's cost into the run-scoped budget and stop the fan-out
        # mid-flight if the run ceiling is now exceeded. Charged here, at the
        # one place the per-child cost is known, so the check re-runs after EVERY
        # child rather than once at spawn.
        self._charge_child_and_check_budget(info)
        if couldnt:
            Stats().inc_subagent_failed()
            self._write_tombstone(info, cause)
            logger.info("Subagent %s could not do its task (%s)", info.id, cause)
            return
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
            # An ACP subagent's runtime. A native one's event names the entry that answered, and
            # the seam records that instead (`usage_ledger.answered_provider`).
            provider="acp",
            model=info.model or "",
        )

    async def cancel(self, agent_id: str, *, reason: str = "Cancelled by user") -> bool:
        """Cancel a single subagent — running, still queued, or waiting to be approved.

        Returns True if found. *reason* is the error it ends with, so a subagent stopped because
        the run that spawned it ended says that rather than claiming a user cancelled it.
        """
        info = self._agents.get(agent_id)
        if not info or info.done:
            return False
        info.cancelled = True  # an intentional stop is not a child failure
        if info.queued:
            # Not yet started: drop it from the queue and mark done without a reap.
            self._queue = [qi for qi in self._queue if qi.id != agent_id]
            info.queued = False
            info.done = True
            info.error = reason
            return True
        await self._force_reap(agent_id, info, time.time() - info.started, reason=reason)
        return True

    async def stop_agents(self, agent_ids: Iterable[str], *, because: str) -> int:
        """Stop each of *agent_ids* still going — running, queued, or waiting to be approved —
        ending it "Cancelled because <because>". Returns how many it stopped.

        How work its owner no longer wants ends (``started_work``: a stopped turn, an ended loop,
        an ended run). Before a stop reached them, a stopped fan-out kept running: it burned
        tokens, held its worktrees, and later delivered results into a session the user had
        already stopped.

        A fan-out left holding nothing but these is marked stopped too, so a spawn already on its
        way into it is refused rather than started after the stop; the mark clears once the
        fan-out drains. One still holding other work (an earlier turn's subagent in the same chat)
        is left open, or that chat could start no agent until the other one finished.

        Reuses :meth:`cancel` for the kill so there is exactly ONE way a subagent dies (session
        reset → SIGKILL fallback → reap → tombstone → audit). Idempotent: a second call finds each
        one already done and returns 0.
        """
        wanted = set(agent_ids)
        victims = [info for info in list(self._agents.values()) if info.id in wanted]
        victims = [info for info in victims if not info.done]
        if not victims:
            return 0
        ids = {info.id for info in victims}
        for info in victims:
            info.cancelled = True  # an intentional stop is not a child failure
            fkey = _fanout_key(info)
            if fkey and not any(
                not other.done and other.id not in ids and _fanout_key(other) == fkey
                for other in self._agents.values()
            ):
                self._fanout_stops.setdefault(fkey, because)
        results = await asyncio.gather(
            *(self.cancel(info.id, reason=f"Cancelled because {because}") for info in victims),
            return_exceptions=True,
        )
        stopped = sum(1 for r in results if r is True)
        logger.info("Stopped %d/%d subagent(s): %s", stopped, len(victims), because)
        return stopped

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
        """Cancel all running subagents and wait a bounded time for their cleanup: one cancelled
        while it starts its agent's process may never leave (``cancel_and_wait``)."""
        if self._reaper_task and not self._reaper_task.done():
            self._reaper_task.cancel()
            self._reaper_task = None
        await cancel_and_wait(list(self._tasks.values()), what="subagents")
        self._tasks.clear()

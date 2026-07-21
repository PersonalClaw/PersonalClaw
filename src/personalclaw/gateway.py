"""Gateway process orchestrator for PersonalClaw.

Manages the lifecycle of all runtime services: session manager, cron
scheduler, context builder, heartbeat, autonudge, inbox, MCP discovery,
subagents, task runner, dashboard / API server, update checks, and signal
handling. This is the core process boot — it runs with or without any external
channel configured.

Channel connectivity is optional and pluggable via the channel-transport seam:
each registered transport's ``start_inbound`` runs at boot (Slack Socket-Mode
lives entirely in the ``slack-channel`` app bundle), and the transport registers
its outbound :class:`~personalclaw.channel_delivery.ChannelDelivery` on the
orchestrator. Core imports NO vendor channel code. With no channel configured the
gateway runs dashboard-only.
"""

import asyncio
import hashlib
import json
import logging
import os
import re
import signal
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from aiohttp import web

from personalclaw import shutdown_event
from personalclaw.acp.errors import AcpError, AcpProcessDied
from personalclaw.autonudge import (
    AutoNudgeService,
    NudgeLoop,
)
from personalclaw.autonudge import enabled as autonudge_enabled
from personalclaw.channel_history import ChannelHistory
from personalclaw.config import AppConfig
from personalclaw.config.loader import (
    CRED_OWNER_ID,
    CRED_SLACK_APP_TOKEN,
    CRED_SLACK_BOT_TOKEN,
    config_dir,
)
from personalclaw.constants import CHAT_TURN_TIMEOUT, DATA_WARNING
from personalclaw.context import ContextBuilder
from personalclaw.dashboard import start_dashboard
from personalclaw.dashboard.chat_runner import _run_chat
from personalclaw.dashboard.handlers import MAX_PROMPT_BYTES
from personalclaw.dashboard.handlers.autonudge import render_nudge_message
from personalclaw.dashboard.origin import (
    build_dashboard_url,
    format_dashboard_urls,
    is_local_bind,
    parse_dashboard_url,
    resolve_bind_host,
    resolve_dashboard_host,
)
from personalclaw.dashboard.state import DashboardState
from personalclaw.dashboard.token_auth import MAX_SESSION_TTL_SECS, generate_token
from personalclaw.frontend import build_frontend_async
from personalclaw.heartbeat import HeartbeatService, is_keep_response, strip_keep_sentinel
from personalclaw.history import ConversationLog, HistoryConsolidator
from personalclaw.hooks import HookManager, HooksConfig
from personalclaw.learn import LessonStore
from personalclaw.llm.base import LLMEvent
from personalclaw.llm_helpers import (
    PromptBusyExhaustedError,
    ToolApprovalPolicy,
    stream_and_collect,
)
from personalclaw.memory import MemoryStore
from personalclaw.schedule import ScheduleJob, ScheduleService, build_schedule_session_context
from personalclaw.security import redact_credentials, redact_exfiltration_urls
from personalclaw.sel import sel
from personalclaw.session import BACKGROUND_KEY, SessionManager
from personalclaw.skills import SkillsLoader
from personalclaw.subagent import (
    INJECTION_TIMEOUT,
    SubagentInfo,
    SubagentManager,
    ToolApprovalCallback,
    resolve_max_subagents,
)

if TYPE_CHECKING:
    from personalclaw.channel_delivery import ChannelDelivery
    from personalclaw.dashboard.state import _ChatSession
    from personalclaw.inbox_service import InboxService
    from personalclaw.loop.watchdog import LoopWatchdog

logger = logging.getLogger(__name__)

# Full chat turn timeout — tool calls, multi-step reasoning, spawning.
# More generous than INJECTION_TIMEOUT (120s) which only covers stream_and_collect.

# Max retries for injecting subagent results into parent sessions.
_MAX_INJECT_ATTEMPTS = 2

# Upper bound for a single autonudge-driven goal loop turn. Loop cycles run long
# (subagent fan-out, 15-20 min), so this is generous — it only fires to free a
# genuinely-wedged turn (e.g. an ACP turn that hung and never emitted turn-end).
# Mirrors the watchdog's _MAX_TURN_SECS so the two agree.
_NUDGE_TURN_TIMEOUT = 1800.0

# A loop cycle's deliverable is its finding file (findings/cycle_NNN.json).
# Some ACP worker agents (notably claude-code) end their turn after the "orient"
# phase — reading status/brief/findings and DESCRIBING a plan — without invoking
# any write tool, because the agent self-paces a single prompt to end_turn once
# it stops emitting. When a loop worker turn ends but the finding count did NOT
# advance, re-prompt the SAME logical cycle with a forceful continuation (up to
# _MAX_CYCLE_REPROMPTS) so the agent actually executes the work + writes. The
# re-prompt loop runs inside the turn task and suppresses autonudge re-arm so the
# idle timer can't fire a competing next-cycle nudge mid-loop. Native workers
# write in one turn so the finding count advances immediately and this never fires.
_MAX_CYCLE_REPROMPTS = 3
_CYCLE_REPROMPT_MSG = (
    "You ended the turn without writing this cycle's deliverable. Do it NOW, in "
    "THIS turn, before you stop: use your file-write/editor tools to actually "
    "write findings/cycle_NNN.json (next sequential N) with the structured "
    "finding, and (if the goal has a document deliverable) create or update it in "
    "the loop dir. Do not just describe them — write the files, then end the turn."
)

# Conservative per-message chunk limit for channel delivery (fits Slack's
# 3000-char Block Kit section.text bound, the tightest known transport).
_CRON_MSG_LIMIT = 3000

# Volatile patterns stripped before hashing cron results for dedup.
_VOLATILE_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?"  # ISO timestamps
    r"|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",  # UUIDs
    re.IGNORECASE,
)
_EPOCH_RE = re.compile(r"\b\d{10,13}\b")
_EPOCH_WINDOW_SECS = 300  # strip epoch values within ±5 min of now
_SUCCESS_REMINDER_SECS = 86400  # post "still succeeding w/ same result" reminder every 24h
_FAILURE_REMINDER_SECS = 3600  # re-alert still-failing cron every 1h (louder than success dedup)


# Tool-name prefixes treated as read-only by the --approval reads flag.
# Matched against the leading verb token of an event.title (e.g. "Read foo.txt"
# -> "read"). Conservative list — anything not on it falls through to the
# standard approval flow.
_READ_ONLY_TOOL_PREFIXES = (
    "read",
    "list",
    "get",
    "search",
    "find",
    "describe",
    "show",
    "view",
    "fetch",
    "query",
    "grep",
    "ls",
    "cat",
    "head",
    "tail",
)

# Tokens that disqualify a tool from auto-approval even if its leading
# verb is in _READ_ONLY_TOOL_PREFIXES. After splitting the title on
# whitespace/punctuation/underscore/dash, any resulting token that exactly
# matches one of these entries causes rejection. Catches compound names
# a third-party MCP author might pick (e.g. read_or_write, find_and_replace,
# get_or_create) where the read prefix masks a write capability. Fail
# closed on ambiguity.
_WRITE_INDICATORS = (
    "write",
    "delete",
    "create",
    "destroy",
    "remove",
    "update",
    "modify",
    "replace",
    "set",
    "put",
    "post",
    "exec",
    "execute",
    "run",
    "rm",
    "rmdir",
    "drop",
    "patch",
    "send",
    "publish",
    "save",
    "edit",
    "kill",
    "terminate",
)


def _is_read_only_tool(event_title: str) -> bool:
    """Return True if event_title looks like a read-only tool invocation.

    Used by --approval reads to auto-approve a conservative set of read
    verbs while still gating writes. Two-stage check:

    1. Leading token (before any whitespace/punctuation) must be in
       _READ_ONLY_TOOL_PREFIXES.
    2. After splitting the title on whitespace/punctuation/underscore/dash,
       no resulting token may exactly match one in _WRITE_INDICATORS — catches
       compound names like read_or_write, find_and_replace, get_or_create.
       Exact token equality, not substring containment: ``setter`` does not
       match ``set``.

    Fails closed on ambiguity.
    """
    if not event_title:
        return False
    lowered = event_title.strip().lower()
    if not lowered:
        return False
    # Tokenize on whitespace, underscores, dashes, and common punctuation
    # so compound names like read_or_write break into ["read", "or", "write"].
    tokens = [t for t in re.split(r"[\s_\-:()/.,]+", lowered) if t]
    if not tokens:
        return False
    leading = tokens[0]
    if leading not in _READ_ONLY_TOOL_PREFIXES:
        return False
    # Reject if any token (other than the leading verb itself) is a known
    # write indicator. Catches read_or_write, find_and_replace, etc.
    if any(token in _WRITE_INDICATORS for token in tokens):
        return False
    return True


def _result_hash(text: str) -> str:
    """Normalize volatile data and return a 16-hex-char SHA-256 prefix.

    Strips ISO timestamps, UUIDs, and any 10–13 digit number that looks
    like an epoch timestamp (within ±5 minutes of now).  Non-epoch numeric
    IDs (account IDs, build IDs) are likely preserved because they would
    likely fall outside the time window.

    Truncated to 64 bits — sufficient for 1:1 comparison against a single
    previous hash (collision probability ~1/2^64 per comparison).
    """
    now = time.time()
    lo = now - _EPOCH_WINDOW_SECS
    hi = now + _EPOCH_WINDOW_SECS

    def _strip_epoch(m: re.Match) -> str:
        v = int(m.group())
        # 13 digits → millis, convert to seconds for comparison
        ts = v / 1000 if v > 9_999_999_999 else v
        return "" if lo <= ts <= hi else m.group()

    text = _VOLATILE_RE.sub("", text)
    text = _EPOCH_RE.sub(_strip_epoch, text)
    return hashlib.sha256(text.encode()).hexdigest()[:16]


class GatewayOrchestrator:
    """Manages the lifecycle of all gateway services.

    Responsibilities are intentionally narrow — event routing and
    interactive handling are delegated to :mod:`events` and
    :mod:`interactions` respectively.
    """

    def __init__(
        self,
        cfg: AppConfig,
        *,
        no_dashboard: bool = False,
        no_crons: bool = False,
        no_open: bool = False,
        port_override: str | None = None,
        json_ready: bool = False,
        approval_mode: str | None = None,
    ) -> None:
        # NOTE: test_heartbeat_prompt_deliver.py creates instances via __new__
        # (bypassing __init__). Update that fixture if new attributes are added.
        self._cfg = cfg
        self._no_dashboard = no_dashboard
        self._no_crons = no_crons
        self._no_open = no_open
        self._port_override = port_override
        self._json_ready = json_ready
        self._approval_mode = approval_mode
        creds = cfg.load_credentials()
        self._app_token = creds.get(CRED_SLACK_APP_TOKEN, "")
        self._bot_token = creds.get(CRED_SLACK_BOT_TOKEN, "")
        self._owner_id = creds.get(CRED_OWNER_ID, "")
        # Multi-user access is disabled — only owner is authorized. The channel
        # app owns its allowlist config (SlackSettings) and enforces owner-only
        # in its own runtime; core holds no channel allowlist.
        self._slack_enabled = bool(self._app_token and self._bot_token)

        # Outbound delivery to the active channel (Slack, …) — the channel
        # transport registers a ChannelDelivery here at start_inbound. None →
        # deliver to the dashboard only. Core never imports channel code.
        self._channel_delivery: "ChannelDelivery | None" = None

        # Services (initialized in start())
        self.sessions: SessionManager | None = None
        self.ctx_builder: ContextBuilder | None = None
        self.conv_log: ConversationLog | None = None
        self.consolidator: HistoryConsolidator | None = None
        self.cron_svc: ScheduleService | None = None
        self._running_script_ids: set[str] = set()  # zero-token jobs in flight
        self.heartbeat_svc: HeartbeatService | None = None
        self.loop_watchdog: "LoopWatchdog | None" = None
        self.inbox_svc: "InboxService | None" = None
        self.subagent_mgr: SubagentManager | None = None
        self._cron_injecting: dict[str, int] = {}  # parent_key → pending injection count
        self.channel_history: ChannelHistory | None = None
        self.dashboard_state: DashboardState | None = None
        self._background_tasks: "set[asyncio.Task]" = set()  # prevent GC of fire-and-forget tasks
        self._dashboard_runner: web.AppRunner | None = None
        self._handler_tasks: "set[asyncio.Task]" = set()  # type: ignore[type-arg]
        self._session_tasks: "dict[str, asyncio.Task]" = {}  # type: ignore[type-arg]
        self._pending_queue: dict[str, list] = {}

    # ------------------------------------------------------------------
    # GatewayServices contract (see personalclaw.gateway_services) — the
    # read-only surface a channel transport drives for inbound handling.
    # ------------------------------------------------------------------
    @property
    def config(self) -> AppConfig:
        """Live gateway config, exposed to channel transports read-only."""
        return self._cfg

    @property
    def owner_id(self) -> str:
        """Primary owner's channel-user id (``""`` if unset)."""
        return self._owner_id

    def register_channel_delivery(self, delivery: "ChannelDelivery | None") -> None:
        """Register the active channel's outbound delivery handle (called by the
        channel transport at ``start_inbound``). ``None`` clears it."""
        self._channel_delivery = delivery

    # ------------------------------------------------------------------
    # Tool approval callback (shared by cron, heartbeat, subagent, task)
    # ------------------------------------------------------------------

    def _interactive_approval(
        self, source: str, session_resolver: Callable[[str], str] | None = None
    ) -> ToolApprovalCallback:
        """Return an approval callback that races dashboard vs channel DM.

        Uses the same rich Block Kit message as the main-agent approval flow
        so users see full command text, security redactions, and Trust-session
        controls for background agents too.
        """

        async def _approve(event: LLMEvent, parent_session_key: str = "") -> bool:
            from personalclaw.trust_mode import is_yolo_active as is_yolo_mode

            # Resolve session: use explicit session, or try to find from active dashboard session
            # Heuristic fallback: picks first running session (dict insertion order). Not guaranteed
            # to be the correct session for subagents, but explicit session param is the primary path.  # noqa: E501
            resolved_session = ""
            if not resolved_session and self.dashboard_state and self.dashboard_state._sessions:
                # Heuristic: pick first running session (insertion order)
                for k in self.dashboard_state._sessions:
                    if self.dashboard_state._sessions[k].running:
                        resolved_session = k.removeprefix("dashboard:")
                        break

            # Per-source auto-approve (e.g. cron, subagent)
            if source in self._cfg.hooks.get("auto_approve_sources", []):
                logger.info("Auto-approving tool %s from source %s", event.title, source)
                return True

            # CLI --approval flag override (composable test mode).
            # 'yolo' auto-approves all; 'reads' auto-approves read-only tools;
            # 'interactive' falls through to the standard flow.
            if self._approval_mode in ("yolo", "reads"):
                approve = self._approval_mode == "yolo" or (
                    self._approval_mode == "reads" and _is_read_only_tool(event.title or "")
                )
                if approve:
                    # Emit a SEL audit event so the audit trail records WHICH
                    # mode auto-approved the tool. Downstream sites already
                    # log the invocation itself; this captures the decision.
                    try:
                        _safe = redact_exfiltration_urls(redact_credentials(event.title or "")[0])[
                            0
                        ]
                        sel().log_api_access(
                            caller=f"cli:approval={self._approval_mode}",
                            operation=f"{source}.cli_approval_auto_approve",
                            outcome="ok",
                            resources=_safe,
                        )
                    except Exception:
                        logger.warning(
                            "SEL audit failed for cli --approval auto-approve", exc_info=True
                        )
                    return True

            # Check both YOLO sources: channel handler (!yolo on) and dashboard UI.
            # Both must honor their TTL — use is_yolo_active() (which expires on
            # read), NOT the raw _yolo field, or an expired dashboard YOLO would
            # keep auto-approving channel tool calls past its 6h ceiling.
            if is_yolo_mode():
                return True

            if self.dashboard_state:
                if self.dashboard_state.is_yolo_active():
                    return True
                # Check if the parent session is trusted (not all sessions).
                # Use session_resolver or resolved_session to find the parent;
                # only fall back to all-sessions check when neither exists.
                # When session_resolver exists but returns falsy, we do NOT
                # fall back to the heuristic -- if the explicit resolver
                # can't find the parent, guessing would widen trust scope.

                def _sel_log(**kw: str) -> None:
                    try:
                        from personalclaw.sel import sel

                        sel().log_api_access(**kw)
                    except Exception:
                        logger.warning("SEL audit failed for trust check", exc_info=True)

                _safe_title = redact_exfiltration_urls(redact_credentials(event.title)[0])[0]

                if session_resolver:
                    try:
                        _parent_session_name = session_resolver(str(event.request_id))
                    except Exception:
                        logger.warning(
                            "session_resolver failed for %s", event.request_id, exc_info=True
                        )
                        _parent_session_name = None
                elif resolved_session:
                    _parent_session_name = resolved_session
                else:
                    _parent_session_name = None

                if _parent_session_name:
                    _ps = (self.dashboard_state._sessions or {}).get(_parent_session_name)
                    if _ps and _ps._trust:
                        _sel_log(
                            caller=f"session:{_parent_session_name}",
                            operation=f"{source}.scoped_trust_auto_approve",
                            outcome="ok",
                            resources=_safe_title,
                        )
                        return True
                    elif _ps:
                        _sel_log(
                            caller=f"session:{_parent_session_name}",
                            operation=f"{source}.scoped_trust_not_trusted",
                            outcome="not_auto_approved",
                            resources=_safe_title,
                        )
                    else:
                        _sel_log(
                            caller=f"session:{_parent_session_name}",
                            operation=f"{source}.scoped_trust_session_not_found",
                            outcome="not_auto_approved",
                            resources=_safe_title,
                        )
                elif not session_resolver and not resolved_session:
                    # No resolver available at all -- fall back to all-sessions
                    sessions = self.dashboard_state._sessions
                    if sessions and all(s._trust for s in sessions.values()):
                        _sel_log(
                            caller=f"source:{source}",
                            operation=f"{source}.all_sessions_trust_auto_approve",
                            outcome="ok",
                            resources=_safe_title,
                        )
                        return True
                    else:
                        _sel_log(
                            caller=f"source:{source}",
                            operation=f"{source}.all_sessions_trust_not_trusted",
                            outcome="not_auto_approved",
                            resources=_safe_title,
                        )
                else:
                    # Resolver existed but failed -- fall through to interactive approval
                    _sel_log(
                        caller=f"source:{source}",
                        operation=f"{source}.scoped_trust_fallthrough",
                        outcome="not_auto_approved",
                        resources=_safe_title,
                    )

            request_id = str(event.request_id)

            # Prompt via the active channel (Slack, …) if one is registered. The
            # channel owns its approval UI + owner-response wait; core races it
            # against the dashboard prompt via the on_prompted hook (which hands us
            # the channel's pending future so a dashboard click resolves both).
            if self._channel_delivery is not None:
                try:
                    dashboard_future = None
                    approved: "bool | None" = None

                    def _on_prompted(pending: Any) -> None:
                        nonlocal dashboard_future
                        if not self.dashboard_state:
                            return
                        dashboard_future = asyncio.ensure_future(
                            self.dashboard_state.request_approval(
                                request_id,
                                source,
                                event.title,
                                tool_input=event.tool_input,
                                tool_purpose=event.tool_purpose,
                                session=(
                                    session_resolver(request_id)
                                    if session_resolver
                                    else resolved_session
                                ),
                            )
                        )

                        def _on_dashboard_done(fut: "asyncio.Future") -> None:  # type: ignore[type-arg]  # noqa: E501
                            if fut.cancelled() or fut.exception():
                                return
                            result = "approved" if fut.result() else "rejected"
                            if not pending.future.done():
                                pending.future.set_result(result)

                        dashboard_future.add_done_callback(_on_dashboard_done)

                    try:
                        approved = await self._channel_delivery.request_approval(
                            event,
                            source=source,
                            parent_session_key=parent_session_key,
                            sessions=self.sessions,
                            on_prompted=_on_prompted,
                        )
                    finally:
                        if self.dashboard_state:
                            self.dashboard_state.resolve_approval(request_id, bool(approved))
                        if dashboard_future and not dashboard_future.done():
                            dashboard_future.cancel()

                    if approved is not None:
                        return approved
                except Exception:
                    logger.debug(
                        "Channel approval failed, falling back to dashboard", exc_info=True
                    )

            # Fallback: dashboard only
            if self.dashboard_state:
                return await self.dashboard_state.request_approval(
                    request_id,
                    source,
                    event.title,
                    tool_input=event.tool_input,
                    tool_purpose=event.tool_purpose,
                    session=session_resolver(request_id) if session_resolver else resolved_session,
                )
            return True  # no UI → auto-approve

        return _approve

    # Required packages that must be importable (import_name, pip_spec).
    # pip_spec may include version constraints matching setup.cfg.
    _REQUIRED_DEPS = [
        ("snowballstemmer", "snowballstemmer>=1.0"),
    ]

    def _check_missing_deps(self) -> None:
        """Auto-repair missing pip deps for venv installs.

        After auto-update, old code may have pulled new source via git reset
        but skipped ``pip install``. This catches the gap on next startup.
        """
        import importlib
        import importlib.util

        missing = [pip for mod, pip in self._REQUIRED_DEPS if importlib.util.find_spec(mod) is None]
        if not missing:
            return

        proj = os.environ.get("PERSONALCLAW_PROJECT_DIR", "")
        if not proj:
            return

        logger.warning("Missing deps %s — installing directly", missing)
        print(f"Installing missing dependencies: {', '.join(missing)}")
        import subprocess as _sp

        result = _sp.run(
            [sys.executable, "-m", "pip", "install", "--quiet", *missing],
            cwd=proj,
            capture_output=True,
            timeout=300,
        )
        if result.returncode == 0:
            # Invalidate import caches so the new packages are found
            importlib.invalidate_caches()
            print("✅ Dependencies installed")
        else:
            print("❌ pip install failed — run manually: personalclaw update")
            logger.error("Dep repair failed: %s", result.stderr.decode(errors="replace")[:500])

    # ------------------------------------------------------------------
    # Service initialisation
    # ------------------------------------------------------------------

    def _init_services(self) -> None:
        """Initialize memory, skills, hooks, context, history, sessions."""
        if not self._slack_enabled:
            logger.info("Starting in dashboard-only mode (no channel credentials)")

        # Auto-repair missing pip deps (handles chicken-and-egg after auto-update)
        try:
            self._check_missing_deps()
        except Exception:
            logger.warning("Dep check failed", exc_info=True)

        # Auto-install agent config so MCP servers are always up to date
        try:
            from personalclaw.agent import rebuild_agent_config  # circular import

            path = rebuild_agent_config()
            logger.info("Agent config installed: %s", path)
        except Exception:
            logger.warning("Agent config install failed", exc_info=True)

        factory = self._cfg.create_provider_factory()

        # Memory, skills, hooks, lessons
        memory = MemoryStore()
        memory.init()

        # Vector memory (structured semantic store)
        from personalclaw.vector_memory import VectorMemoryStore

        self.vector_memory = VectorMemoryStore(
            confidence_threshold=self._cfg.memory.semantic_confidence_threshold,
            extra_prefixes=self._cfg.memory.semantic_keys or None,
            dedup_threshold=self._cfg.memory.episodic_dedup_threshold,
            episodic_max=self._cfg.memory.episodic_max_count,
            episodic_limit=self._cfg.memory.episodic_max_results,
        )
        self.vector_memory.init()
        memory.vector_store = self.vector_memory

        skills = SkillsLoader()
        hooks = HookManager(HooksConfig.from_dict(self._cfg.hooks))
        lessons = LessonStore()
        # bot_name deliberately NOT pinned here — ContextBuilder resolves it
        # live from config per turn, so a Settings → Account rename takes
        # effect on the next message without a gateway restart.
        self.ctx_builder = ContextBuilder(
            memory=memory,
            skills=skills,
            hooks=hooks,
            lessons=lessons,
        )

        # Conversation history
        self.conv_log = ConversationLog()
        self.conv_log.init()
        self.ctx_builder.conversation_log = self.conv_log

        # Session manager
        self.sessions = SessionManager(
            self._cfg, provider_factory=factory
        )  # type: ignore[arg-type]

        # History consolidator
        self.consolidator = HistoryConsolidator(
            log=self.conv_log,
            memory=memory,
            sessions=self.sessions,
            lesson_store=lessons,
            history_idle_secs=self._cfg.memory.history_idle_hours * 3600,
            vector_store=self.vector_memory,
            migrated=self._cfg.memory.migrated,
            skills_loader=skills,
            auto_skills_enabled=self._cfg.skills.auto_create_from_sessions,
            auto_refine_enabled=self._cfg.skills.auto_refine_on_deviation,
            auto_min_tool_calls=self._cfg.skills.auto_min_tool_calls,
            auto_similarity_threshold=self._cfg.skills.auto_similarity_threshold,
        )
        # E11: extract skills from a session one last time when it idles out.
        self.sessions.set_session_expire_callback(self.consolidator.consolidate_session)

        # Channel history buffer
        self.channel_history = ChannelHistory(
            observe_max_entries=self._cfg.observe_max_messages,
            observe_ttl_secs=int(self._cfg.observe_ttl_hours * 3600),
            history_dir=config_dir() / "history",
        )
        self.ctx_builder.channel_history = self.channel_history
        # Observe-mode channel registration is channel-specific config — the channel
        # app registers its observe channels via services.channel_history.set_observe
        # at start_inbound (core holds no per-channel activation config).

        # FTS index
        indexed = memory.rebuild_index()
        logger.info("FTS index built: %d files", indexed)

    async def _run_action_job(self, job: "ScheduleJob") -> str | None:
        """Run a non-agent Schedule action via the action-provider registry.

        Covers every provider except ``invoke-agent`` (which runs a full LLM turn
        on the agent path). The deterministic ``bash`` / ``run-script`` providers
        and any other registered action all dispatch here, so a scheduled trigger
        and a lifecycle trigger execute the same action the same way.

        Returns the result string (delivered by the caller) or None (silent).
        Sets job.last_status/last_error/last_result so the run record + dedup
        work, and auto-pauses the job after 5 consecutive failures.
        """
        from personalclaw.action_providers import ActionContext, get_action_provider
        from personalclaw.action_providers.registry import _ensure_default_providers_registered

        _ensure_default_providers_registered()
        provider = get_action_provider(job.provider)
        if provider is None:
            job.last_status = "error"
            job.last_error = f"unknown action provider {job.provider!r}"
            self._maybe_autopause(job)
            return None

        config = job.action.get("config") or {}
        # Dry-run replay (T9): inject observe-mode into the action config so the
        # spawn-based providers run write-capable tools in preview-only mode. A
        # shallow copy so the persisted action config is never mutated.
        # A deterministic provider (bash/run-script/webhook/…) has NO observe
        # mode — dispatching to it would execute the REAL side effects while the
        # UI promises none. Refuse to execute and record a preview instead.
        if getattr(job, "dry_run", False):
            if not getattr(provider, "supports_dry_run", False):
                job.last_status = "ok"
                job.last_error = None
                job.last_result = (
                    f"[dry run] {provider.display_name} actions execute directly "
                    f"(no observe mode) — not run. Would run with config: "
                    f"{json.dumps({k: v for k, v in config.items() if k != 'dry_run'}, default=str)[:500]}"  # noqa: E501
                )
                job.last_outcome = "skip"
                logger.info(
                    "Action cron '%s': dry run refused for direct-execution provider %s (previewed only)",  # noqa: E501
                    job.name,
                    job.provider,
                )
                return None
            config = {**config, "dry_run": True}
        # The schedule's "what fires" maps to the action event; the prompt/last
        # result is the free-form context a templated action can interpolate. The
        # payload keys mirror schedule.SCHEDULE_VARS so a templated action resolves
        # $last_result / $now / $timezone / $job_id / $job_name.
        from datetime import datetime
        from zoneinfo import ZoneInfo

        from personalclaw.schedule import get_local_tz

        now = time.time()
        tz_name = job.timezone or get_local_tz()[0]
        last_result = job.last_result or ""
        try:
            now_str = datetime.fromtimestamp(now, tz=ZoneInfo(tz_name)).isoformat()
        except Exception:
            now_str = datetime.fromtimestamp(now).isoformat()
        ctx = ActionContext(
            event=f"schedule:{job.id}",
            context=last_result,
            payload={
                "job_id": job.id,
                "job_name": job.name,
                "session_key": f"cron:{job.id}",
                "last_result": last_result,
                "now": now_str,
                "timezone": tz_name,
            },
        )
        # zt_timeout overrides; else the mode default (300s command / 30s script /
        # 30s for any other action). The deterministic providers also clamp via
        # their sandbox helper, so this only matters when zt_timeout is unset.
        if job.zt_timeout:
            timeout = job.zt_timeout
        elif job.provider == "bash":
            timeout = 300
        else:
            timeout = 30

        self._running_script_ids.add(job.id)
        logger.info(
            "Action cron '%s' dispatch via %s (dry_run=%s)",
            job.name,
            job.provider,
            getattr(job, "dry_run", False),
        )
        try:
            result = await provider.execute(config, ctx, timeout=timeout)
            logger.info(
                "Action cron '%s' result: success=%s outcome=%r err=%r",
                job.name,
                result.success,
                result.outcome,
                result.error,
            )
        except Exception as exc:
            job.last_status = "error"
            job.last_error = str(exc)
            self._maybe_autopause(job)
            logger.exception("Action cron job '%s' (%s) failed", job.name, job.provider)
            return None
        finally:
            self._running_script_ids.discard(job.id)

        if result.success:
            job.last_status = "ok"
            job.last_error = None
            job.last_result = (result.stdout or "").strip()
            job.last_outcome = result.outcome or ""
            job.consecutive_failures = 0
            # A run-script action that signalled "done" sets delete_after_run; the
            # provider reports it via outcome so one-shot scripts still self-remove.
            if result.outcome == "done":
                job.delete_after_run = True
            if result.outcome == "skip":
                return None  # silent success
            return job.last_result or None
        job.last_status = "error"
        job.last_error = result.error or result.stderr or f"exit {result.exit_code}"
        job.last_result = (result.stdout or "").strip()
        self._maybe_autopause(job)
        return None

    def _maybe_autopause(self, job: "ScheduleJob") -> None:
        """Disable a zero-token job after 5 consecutive failures."""
        job.consecutive_failures = (job.consecutive_failures or 0) + 1
        if job.consecutive_failures >= 5:
            job.enabled = False
            logger.warning(
                "Cron job '%s' auto-paused after %d consecutive failures",
                job.name,
                job.consecutive_failures,
            )

    async def _init_cron(self) -> None:
        """Initialize and start the cron service."""

        async def _cron_callback(job: ScheduleJob) -> str | None:
            # ── Non-agent actions (no LLM, no ACP turn) ──
            # Every provider except invoke-agent dispatches through the action
            # registry and returns. This branch comes first so deterministic
            # bash/run-script actions never build a session.
            if job.provider and job.provider != "invoke-agent":
                return await self._run_action_job(job)

            # helper picks stable vs ephemeral session key and
            # decides whether to prepend last_result, based on job.persistent_session.
            session_key, msg = build_schedule_session_context(job)

            # Dry-run replay of an agent job (invoke-agent or legacy message cron):
            # this path runs a FULL LLM turn on a possibly REUSED stable session —
            # observe-mode can't be guaranteed (an already-live session keeps its
            # real tools; ACP-dialect agents ignore the flag entirely). Refuse to
            # execute and record a preview instead (T9 honesty — same contract as
            # the deterministic action providers in _run_action_job).
            if getattr(job, "dry_run", False):
                job.last_result = (
                    "[dry run] Agent triggers run a full LLM turn — not run. "
                    f"Would send to session '{session_key}'"
                    + (f" (agent {job.agent_id})" if job.agent_id else "")
                    + f":\n{msg[:400]}"
                )
                job.last_outcome = "skip"
                logger.info(
                    "Cron '%s': dry run refused for agent path (previewed only)",
                    job.name,
                )
                return None

            # ── Sequential agent execution (Hub integration) ──
            # When agent_sequence has multiple agents, run them sequentially
            # with per-agent session keys and per-job env vars.
            agents = job.agent_sequence if job.agent_sequence else []
            if len(agents) > 1:
                assert self.sessions is not None
                assert self.ctx_builder is not None
                result_text = "_No response._"
                for agent in agents:
                    agent_session_key = f"cron:{job.id}:{agent}"
                    if self.cron_svc is not None:
                        self.cron_svc.register_active_session_key(job.id, agent_session_key)
                    _acq = False
                    try:
                        client, is_new, _resumed = await self.sessions.get_or_create(
                            agent_session_key,
                            agent=agent,
                            channel_id=job.channel,
                            approval_policy=job.approval_mode,
                            extra_env=job.env or None,
                        )
                        _acq = True
                        full_message, _ = self.ctx_builder.build_message(
                            msg, True, interactive=False, agent=agent
                        )
                        # Cron is UNATTENDED — assume no user is present, even if a
                        # HITL prompt COULD be surfaced. So never wait on interactive
                        # approval (it would hang until timeout-deny and fail tools);
                        # run HOOK_BASED (security denylist/credential hooks still fire)
                        # with no interactive callback → hook-neutral tools auto-approve.
                        result_text = await stream_and_collect(
                            client,
                            full_message,
                            approval_policy=(
                                ToolApprovalPolicy.AUTO_APPROVE
                                if job.approval_mode == "auto"
                                else ToolApprovalPolicy.HOOK_BASED
                            ),
                            hooks=self.ctx_builder.hooks,
                            on_tool_approval=None,
                        )
                        if not result_text:
                            result_text = "_No response._"
                        logger.info("Cron '%s': agent '%s' completed", job.name, agent)
                    finally:
                        if _acq:
                            self.sessions.release(agent_session_key)
                            await self.sessions.reset(agent_session_key)
                            if self.cron_svc is not None:
                                self.cron_svc.clear_active_session_key(job.id)
                job.last_result = result_text
                return result_text

            # ── Single-agent path ──
            # Tell the reaper which key to target if this run hangs.
            if self.cron_svc is not None:
                self.cron_svc.register_active_session_key(job.id, session_key)

            _acquired = False
            try:
                assert self.sessions is not None
                assert self.ctx_builder is not None
                client, is_new, _resumed = await self.sessions.get_or_create(
                    session_key,
                    agent=job.agent_id or None,
                    channel_id=job.channel,
                    approval_policy=job.approval_mode,
                    extra_env=job.env or None,
                )
                _acquired = True
                if job.acked_items:
                    msg += (
                        "\n\n[User has seen and acknowledged ALL of the following — "
                        "do NOT repeat the same content]\n"
                        + "\n".join(f"- {a}" for a in job.acked_items)
                    )
                full_message, _ = self.ctx_builder.build_message(
                    msg,
                    True,
                    interactive=False,
                    agent=job.agent_id or None,
                )

                # Cron is UNATTENDED — never wait on interactive approval (would hang
                # to timeout-deny). HOOK_BASED keeps the security hooks; hook-neutral
                # tools auto-approve since no user is present to answer.
                result_text = await stream_and_collect(
                    client,
                    full_message,
                    approval_policy=(
                        ToolApprovalPolicy.AUTO_APPROVE
                        if job.approval_mode == "auto"
                        else ToolApprovalPolicy.HOOK_BASED
                    ),
                    hooks=self.ctx_builder.hooks,
                    on_tool_approval=None,
                )

                if not result_text:
                    result_text = "_No response._"

                job.last_result = result_text

                # ── Error deduplication ──
                # Suppress channel delivery for repeated identical results to avoid spam.
                rh = _result_hash(result_text)

                # Clear failure dedup on any success, regardless of whether
                # the success result itself is a dup. A successful run means
                # the job recovered — next failure should always alert fresh.
                job.last_failure_hash = ""
                job.last_failure_at = 0.0
                job.consecutive_failures = 0

                if rh == job.last_posted_hash:
                    job.consecutive_dupes += 1
                    # Time-based reminder: re-post after 24h so persistent identical
                    # results don't go unnoticed indefinitely.
                    if time.time() - job.last_posted_at >= _SUCCESS_REMINDER_SECS:
                        # NB: consecutive_dupes is captured here before the reset
                        # at the post-delivery state update further below.
                        result_text = (
                            f"⚠️ Cron '{job.name}' has produced the same result"
                            f" {job.consecutive_dupes} times in a row:\n\n{result_text}"
                        )
                    else:
                        logger.info(
                            "Cron '%s': duplicate result #%d — suppressing channel delivery",
                            job.name,
                            job.consecutive_dupes,
                        )
                        if self.dashboard_state:
                            redacted_for_dash, _ = redact_exfiltration_urls(result_text)
                            redacted_for_dash, _ = redact_credentials(redacted_for_dash)
                            title = f"Cron: {job.name} (dup #{job.consecutive_dupes}, muted)"
                            title, _ = redact_exfiltration_urls(title)
                            title, _ = redact_credentials(title)
                            self.dashboard_state.notify(
                                "cron",
                                title,
                                redacted_for_dash,
                                meta={"job_id": job.id},
                            )
                        from personalclaw.sel import sel

                        sel().log_tool_invocation(
                            session_key=f"cron:{job.id}",
                            tool_name="cron_dedup_suppress",
                            outcome="suppressed",
                            downstream_service="none",
                        )
                        return result_text

                if job.silent:
                    logger.info("Cron job '%s' silent — suppressing auto-delivery", job.name)
                    from personalclaw.sel import sel

                    sel().log_tool_invocation(
                        session_key=f"cron:{job.id}",
                        tool_name="cron_silent_suppress",
                        outcome="suppressed",
                        downstream_service="none",
                    )
                    return result_text

                if self.dashboard_state:
                    redacted_for_dash, _ = redact_exfiltration_urls(result_text)
                    redacted_for_dash, _ = redact_credentials(redacted_for_dash)
                    self.dashboard_state.notify(
                        "cron",
                        f"Cron: {job.name}",
                        redacted_for_dash,
                        meta={"job_id": job.id},
                    )
                if self._channel_delivery is not None:
                    try:
                        # Resolve the target channel (the delivery impl retries a
                        # transient DM-open internally); then deliver with the
                        # channel's cron-ack affordance. Delivery is NOT retried to
                        # avoid duplicates.
                        channel = job.channel
                        if not channel and (job.created_by or self._owner_id):
                            channel = await self._channel_delivery.open_dm(
                                job.created_by or self._owner_id
                            )
                        if channel:
                            parent_ts = await self._channel_delivery.deliver_cron_result(
                                channel,
                                job.name,
                                job.id,
                                result_text,
                                job.thread_ts or "",
                            )
                            thread_root = job.thread_ts or parent_ts
                            # Store thread_ts so subagents can route replies here
                            if thread_root and self.sessions:
                                await self.sessions.set_thread(session_key, thread_root)
                                await self.sessions.set_channel(session_key, channel)
                            # Dedup state: only advance after confirmed delivery.
                            job.last_posted_hash = rh
                            job.consecutive_dupes = 0
                            job.last_posted_at = time.time()
                        else:
                            logger.warning(
                                "Cron '%s': no channel resolved, skipping notification", job.name
                            )
                    except Exception as channel_exc:
                        logger.error(
                            "Cron job '%s': channel delivery failed (job succeeded)",
                            job.name,
                            exc_info=True,
                        )
                        if self.dashboard_state:
                            exc_msg, _ = redact_exfiltration_urls(str(channel_exc))
                            exc_msg, _ = redact_credentials(exc_msg)
                            self.dashboard_state.notify(
                                "cron",
                                f"Cron: {job.name}",
                                f"Job completed but channel delivery failed: {exc_msg}",
                                meta={"job_id": job.id},
                            )
                # Session cleanup happens in finally block
                return result_text
            except Exception as exc:
                # Attempt one retry for ACP process death before any dedup / alert.
                exc_msg = str(exc).lower()
                if (
                    isinstance(exc, AcpError)
                    and ("not running" in exc_msg or "process exited" in exc_msg)
                    and not getattr(job, "_acp_retried", False)
                    and self.sessions is not None
                ):
                    logger.warning(
                        "Cron '%s': ACP process died, resetting session and retrying",
                        job.name,
                    )
                    job._acp_retried = True  # type: ignore[attr-defined]
                    try:
                        if _acquired:
                            self.sessions.release(session_key)
                            _acquired = False
                        await self.sessions.reset(session_key)
                        return await _cron_callback(job)
                    except Exception:
                        pass  # retry failed — fall through to dedup + alert
                    finally:
                        job._acp_retried = False  # type: ignore[attr-defined]
                logger.exception("Cron job '%s' failed", job.name)
                # During an in-flight ACP retry (inner recursive _cron_callback
                # call), suppress all notify/channel/dedup work — the outer
                # invocation is authoritative and will handle notification
                # for the retry's final failure. Without this guard, the
                # inner call emits its own dashboard notify + channel alert
                # and advances dedup state, duplicating the outer handler.
                if getattr(job, "_acp_retried", False):
                    raise
                # ── Failure dedup: suppress repeated identical crash notifications ──
                exc_summary = f"{type(exc).__name__}: {exc}"
                exc_summary, _ = redact_exfiltration_urls(exc_summary)
                exc_summary, _ = redact_credentials(exc_summary)
                fh = _result_hash(exc_summary)
                is_dup = fh == job.last_failure_hash
                if is_dup and time.time() - job.last_failure_at < _FAILURE_REMINDER_SECS:
                    job.consecutive_failures += 1
                    logger.info(
                        "Cron '%s': duplicate failure #%d — suppressing channel delivery",
                        job.name,
                        job.consecutive_failures,
                    )
                    # Dashboard notify is best-effort — never mask the original
                    # exception if notification itself fails.
                    try:
                        if self.dashboard_state:
                            title = (
                                f"Cron: {job.name} (dup failure #{job.consecutive_failures}, muted)"
                            )
                            title, _ = redact_exfiltration_urls(title)
                            title, _ = redact_credentials(title)
                            self.dashboard_state.notify(
                                "cron",
                                title,
                                f"Job failed (suppressed — same error):\n{exc_summary}",
                                meta={"job_id": job.id, "failure_hash": fh},
                            )
                    except Exception:
                        logger.debug(
                            "Dashboard notify failed in cron failure suppress path", exc_info=True
                        )
                    # SEL logging is best-effort — never mask the original
                    # exception if audit logging itself fails.
                    try:
                        from personalclaw.sel import sel

                        sel().log_tool_invocation(
                            session_key=f"cron:{job.id}",
                            tool_name="cron_failure_dedup_suppress",
                            outcome="suppressed",
                            downstream_service="none",
                        )
                    except Exception:
                        logger.debug(
                            "SEL logging failed in cron failure suppress path",
                            exc_info=True,
                        )
                    raise
                # First failure (or fresh failure after reminder window) — alert.
                # Dashboard notify is best-effort — never mask the original
                # exception if notification itself fails.
                try:
                    if self.dashboard_state:
                        alert_title = f"Cron: {job.name}"
                        alert_title, _ = redact_exfiltration_urls(alert_title)
                        alert_title, _ = redact_credentials(alert_title)
                        self.dashboard_state.notify("cron", alert_title, "Job failed")
                except Exception:
                    logger.debug(
                        "Dashboard notify failed in cron failure alert path", exc_info=True
                    )
                # Compute the count this alert represents (including itself) so
                # the re-alert message can call out persistence.
                new_count = job.consecutive_failures + 1 if is_dup else 1
                if is_dup:
                    fail_msg = (
                        f"⏰ *Cron: {job.name}* ❌ _Job still failing"
                        f" ({new_count} consecutive identical failures)"
                        f" — check logs._"
                    )
                else:
                    fail_msg = f"⏰ *Cron: {job.name}* ❌ _Job failed — check logs._"
                channel_delivery_failed = False  # track real delivery exceptions only
                # Silent jobs (all app-manifest crons) never deliver to a channel —
                # their created_by is an "app:<name>" pseudo-user open_dm can't resolve.
                if self._channel_delivery is not None and not job.silent:
                    try:
                        channel = job.channel
                        if not channel and (job.created_by or self._owner_id):
                            channel = await self._channel_delivery.open_dm(
                                job.created_by or self._owner_id
                            )
                        if channel:
                            fail_msg, _ = redact_exfiltration_urls(fail_msg)
                            fail_msg, _ = redact_credentials(fail_msg)
                            await self._channel_delivery.deliver_text(channel, fail_msg)
                        else:
                            logger.warning(
                                "Cron '%s': no channel resolved for error notification", job.name
                            )

                    except Exception:
                        channel_delivery_failed = True
                        logger.error(
                            "Cron job '%s': channel failure-notification delivery failed",
                            job.name,
                            exc_info=True,
                        )
                # Advance dedup state unless channel delivery raised. "No channel
                # available" is treated as a skip (not a failure), so dedup still
                # advances — otherwise every identical failure re-notifies the
                # dashboard, which is what dedup is supposed to prevent.
                if not channel_delivery_failed:
                    job.last_failure_hash = fh
                    job.last_failure_at = time.time()
                    job.consecutive_failures = new_count
                    # SEL logging is best-effort — never mask the original
                    # exception if audit logging itself fails.
                    try:
                        from personalclaw.sel import sel

                        sel().log_tool_invocation(
                            session_key=f"cron:{job.id}",
                            tool_name="cron_failure_alert",
                            outcome="alerted",
                            downstream_service=(
                                "channel" if self._channel_delivery is not None else "none"
                            ),
                        )
                    except Exception:
                        logger.debug(
                            "SEL logging failed in cron failure alert path",
                            exc_info=True,
                        )
                raise
            finally:
                assert self.sessions is not None
                if _acquired:
                    self.sessions.release(session_key)
                    # Defer session reset if subagents are still running or
                    # mid-injection — _subagent_done will reset after the last one.
                    has_pending = self.subagent_mgr and any(
                        a.parent_session_key == session_key for a in self.subagent_mgr.running
                    )
                    has_injecting = self._cron_injecting.get(session_key, 0) > 0
                    if has_pending or has_injecting:
                        logger.info("Cron '%s': deferring reset, subagents pending", job.name)
                        # leave the active-session registration in place so
                        # the reaper can still target the ephemeral key if the deferred
                        # reset hangs. _subagent_done will clear it after the real reset.
                    else:
                        await self.sessions.reset(session_key)
                        # reset done → reaper no longer needs this key.
                        if self.cron_svc is not None:
                            self.cron_svc.clear_active_session_key(job.id)
                # Restore per-job env vars (single-agent path) — now handled via extra_env passthrough  # noqa: E501

        self.cron_svc = ScheduleService(base_dir=config_dir(), on_job=_cron_callback)
        if self._no_crons:
            logger.info("Cron scheduler disabled (--no-crons)")
        else:
            await self.cron_svc.start()
            if self.sessions:
                self.cron_svc.start_reaper(self.sessions)
            else:
                logger.warning("Cron reaper not started: sessions not available")
            # Reconcile app-declared crons (untrusted-app sandbox P3): register the
            # scheduled jobs enabled apps declare + are permitted (can_use_cron), and
            # prune stale app:* jobs. Idempotent; apps loaded before this via the
            # extension loader. Best-effort — never block the scheduler on it.
            try:
                from personalclaw.apps.app_crons import reconcile_app_crons

                reconcile_app_crons(self.cron_svc)
            except Exception:
                logger.warning("app-cron reconcile failed", exc_info=True)

    async def _init_heartbeat(self) -> None:
        """Initialize and start the heartbeat service."""
        memory = self.ctx_builder.memory if self.ctx_builder else MemoryStore()

        async def _heartbeat_task(task_text: str, deliver: str) -> str | None:
            assert self.sessions is not None
            assert self.ctx_builder is not None
            session_key = BACKGROUND_KEY
            _acquired = False
            try:
                client, is_new, _resumed = await self.sessions.get_or_create(session_key)
                _acquired = True
                full_message, _ = self.ctx_builder.build_message(task_text, is_new)

                # Heartbeat is a pure UNATTENDED background loop — no user present.
                # HOOK_BASED keeps the security hooks; hook-neutral tools auto-approve
                # (no interactive callback), never hanging on an unanswerable prompt.
                result_text = await stream_and_collect(
                    client,
                    full_message,
                    approval_policy=ToolApprovalPolicy.HOOK_BASED,
                    hooks=self.ctx_builder.hooks,
                    on_tool_approval=None,
                )

                if not result_text:
                    result_text = "_No response._"
            except Exception:
                logger.exception("Heartbeat task failed: %s", task_text[:80])
                raise
            finally:
                if _acquired:
                    self.sessions.release(session_key)
                    await self.sessions.recycle_background()

            result_safe, _ = redact_exfiltration_urls(result_text)
            result_safe, _ = redact_credentials(result_safe)
            display_text = strip_keep_sentinel(result_safe)
            # Only notify when task is complete — suppress delivery for
            # incomplete tasks (HEARTBEAT_KEEP) to avoid spamming every cycle.
            if is_keep_response(result_safe):
                logger.info("Heartbeat task incomplete, suppressing delivery: %s", task_text[:80])
            else:
                task_safe, _ = redact_exfiltration_urls(task_text[:100])
                task_safe, _ = redact_credentials(task_safe)
                await self._deliver_result(
                    "Heartbeat",
                    task_safe,
                    display_text,
                    deliver,
                )
            return result_safe

        async def _deliver_due_commitments() -> None:
            """Deliver any due proactive check-ins (M5e — O-A4), then dismiss them.

            Off unless the user opted in. The commitment ``text`` is the LLM-
            authored natural check-in captured at consolidation (guardrails
            already gated capture), so delivery is a plain send — no second LLM
            call. Each delivered commitment is dismissed so the heartbeat never
            re-fires the same window. Scoped + audited."""
            from datetime import datetime, timezone

            from personalclaw.config.loader import AppConfig

            if not AppConfig.load().memory.proactive_commitments:
                return
            if self.consolidator is None:
                return
            svc = self.consolidator._svc
            if not svc.has_vector:
                return
            now_iso = datetime.now(timezone.utc).isoformat()
            try:
                due = svc.due_commitments_all(now_iso=now_iso)
            except Exception:
                logger.debug("due-commitment scan failed", exc_info=True)
                return
            for c in due:
                channel = c.get("channel") or "dashboard"
                text, _ = redact_exfiltration_urls(c.get("text", ""))
                text, _ = redact_credentials(text)
                if not text:
                    svc.dismiss_commitment(c["key"])
                    continue
                try:
                    await self._deliver_result(
                        "Proactive check-in",
                        "",
                        text,
                        channel,
                    )
                    sel().log_api_access(
                        caller="heartbeat",
                        operation="commitment_deliver",
                        outcome="approved",
                        source="gateway",
                        resources=f"agent={c.get('agent', '')},channel={channel}",
                    )
                except Exception:
                    logger.warning("Commitment delivery failed for %s", c["key"], exc_info=True)
                finally:
                    # Dismiss either way — a delivered-or-failed commitment is done
                    # for this window (it never re-fires; the next window re-infers).
                    svc.dismiss_commitment(c["key"])

        self.heartbeat_svc = HeartbeatService(
            memory=memory,
            on_task=_heartbeat_task,
            consolidator=self.consolidator,
            on_due_commitments=_deliver_due_commitments,
        )
        await self.heartbeat_svc.start()

    async def _init_autonudge(self) -> None:
        """Initialize and start the auto-nudge service (feature-flagged)."""
        if not autonudge_enabled():
            logger.info("AutoNudge disabled via feature flag")
            return

        async def _fire(loop: NudgeLoop) -> bool:
            """Inject nudge message into the bound chat session.

            Returns True if the nudge was actually dispatched, False if skipped
            (session missing, dashboard not ready, or turn still active). The
            service uses this to avoid counting skipped cycles toward
            max_cycles.
            """
            # Guard (not assert): stripped under -O; also _init_autonudge() can
            # run before _init_dashboard(), and _init_dashboard is skipped
            # entirely in --no-dashboard mode. Mirrors _observer's guard below.
            if self.dashboard_state is None:
                logger.warning(
                    "AutoNudge: dashboard not ready — skipping fire for loop %s", loop.id
                )
                return False
            dstate = self.dashboard_state
            session = dstate._sessions.get(loop.session_name)
            if session is None:
                logger.warning(
                    "AutoNudge: session %s missing — removing loop %s", loop.session_name, loop.id
                )
                await self.autonudge_svc.remove(loop.id)  # type: ignore[union-attr]
                return False
            msg = render_nudge_message(loop.message, loop.stop_sentinel_path)
            tagged = f"[auto-nudge cycle {loop.cycle_count + 1}]\n{msg}"
            from personalclaw.dashboard.chat import (  # circular import: gateway -> dashboard.chat -> gateway (chat dispatch references GatewayOrchestrator)  # noqa: E501
                _run_chat,
            )

            if session.running:
                # Turn still active — drop this nudge. Next idle-timer tick will
                # schedule again once the turn ends. Queueing would stack
                # identical 3KB+ nudges and blow up the context window.
                # Returning False keeps cycle_count accurate (only delivered
                # nudges count toward max_cycles).
                logger.info(
                    "AutoNudge skip: session %s is running (loop %s cycle %d)",
                    session.key,
                    loop.id,
                    loop.cycle_count,
                )
                return False
            # Show nudge as a distinct "nudge" role message in the session history.
            session.append("nudge", tagged, "msg msg-nudge")

            # Every unified Loop kind (goal/code/general/design) is a cycle-driven
            # worker (app="loop", keyed loop-<id>) whose deliverable is a per-cycle
            # finding file — they share the deliverable-forcing re-prompt + turn path.
            _app = getattr(session, "_app", "")
            _is_loop = _app == "loop"

            def _finding_count(_key: str) -> int:
                try:
                    from personalclaw.loop import store as _lstore

                    # loop-<id> (main) or loop-<id>-<taskid> (parallel task-worker);
                    # findings live on the parent loop in both cases.
                    _lid = _key.split("loop-", 1)[-1]
                    if _lstore.loop_dir(_lid) is None and "-" in _lid:
                        _lid = _lid.rsplit("-", 1)[0]
                    return len(_lstore.get_findings(_lid))
                except Exception:
                    return 0

            async def _run_one(_sess, _msg, turn_timeout: float) -> None:
                try:
                    await asyncio.wait_for(_run_chat(dstate, _sess, _msg), timeout=turn_timeout)
                except asyncio.TimeoutError:
                    logger.warning(
                        "AutoNudge: turn for %s exceeded %ss — cancelling wedged turn",
                        _sess.key,
                        turn_timeout,
                    )
                    _sess._last_turn_errored = True
                    try:
                        from personalclaw.dashboard.chat_utils import _history_key_for

                        prov = dstate.sessions.get_provider(_history_key_for(_sess.key))
                        if prov is not None and hasattr(prov, "cancel"):
                            await prov.cancel()
                    except Exception:
                        logger.debug(
                            "cancel after turn timeout failed for %s", _sess.key, exc_info=True
                        )
                    finally:
                        _sess._running = False

            async def _run_turn_bounded(_sess=session, _msg=tagged) -> None:
                # Bound each turn so a wedged worker turn can't hold the session
                # `running` forever. Loop cycles run long (subagent fan-out,
                # 15-20 min), so a generous bound (matches the watchdog cap).
                turn_timeout = _NUDGE_TURN_TIMEOUT if _is_loop else CHAT_TURN_TIMEOUT
                if not _is_loop:
                    await _run_one(_sess, _msg, turn_timeout)
                    return
                # Goal loop: drive the cycle to an actual deliverable. Some ACP
                # workers (claude-code) end their turn after only reading, and
                # then NO-OP further prompts on the same session — so a plain
                # re-prompt yields an empty turn. Before each retry we start a
                # FRESH ACP session (start_fresh_turn_session) so the agent
                # re-engages, then re-prompt. Bounded by _MAX_CYCLE_REPROMPTS.
                # Suppress autonudge re-arm for the whole loop so the idle timer
                # doesn't fire a competing next-cycle nudge mid-loop; re-arm once
                # at the end. Native workers write in one turn → loop exits
                # immediately, fresh-session never invoked.
                before = _finding_count(_sess.key)
                _sess._suppress_autonudge_rearm = True
                try:
                    await _run_one(_sess, _msg, turn_timeout)
                    for attempt in range(_MAX_CYCLE_REPROMPTS):
                        if _finding_count(_sess.key) > before or getattr(
                            _sess, "_last_turn_errored", False
                        ):
                            break
                        logger.info(
                            "AutoNudge: %s produced no finding (re-prompt %d/%d) — fresh ACP session + re-prompt",  # noqa: E501
                            _sess.key,
                            attempt + 1,
                            _MAX_CYCLE_REPROMPTS,
                        )
                        # Re-engage: a no-op'd ACP session won't service a repeat
                        # prompt, so begin a fresh agent session on the live
                        # process. The live provider lives in the SessionManager
                        # (NOT on the dashboard _ChatSession), keyed by the
                        # history key (dashboard:<session.key>).
                        fresh_started = False
                        try:
                            from personalclaw.dashboard.chat_utils import _history_key_for

                            hkey = _history_key_for(_sess.key)
                            prov = dstate.sessions.get_provider(hkey)
                            fresh = getattr(prov, "start_fresh_turn_session", None)
                            if fresh is not None:
                                await fresh()
                                fresh_started = True
                                # The fresh ACP session has NO context; make the
                                # next turn re-inject the worker system prompt.
                                dstate.sessions.mark_new(hkey)
                            else:
                                logger.debug(
                                    "no start_fresh_turn_session on provider for %s", _sess.key
                                )
                        except Exception:
                            logger.debug(
                                "fresh turn session failed for %s", _sess.key, exc_info=True
                            )
                        # A fresh ACP session has NO conversation context (the
                        # agent greets "ready when you are"), so a bare "you forgot
                        # to write" continuation is meaningless — re-send the FULL
                        # self-contained cycle prompt (loop id, dir, protocol)
                        # plus an explicit write reminder.
                        retry_msg = (
                            (_msg + "\n\n" + _CYCLE_REPROMPT_MSG)
                            if fresh_started
                            else _CYCLE_REPROMPT_MSG
                        )
                        _sess.append("nudge", retry_msg, "msg msg-nudge")
                        await _run_one(_sess, retry_msg, turn_timeout)
                finally:
                    _sess._suppress_autonudge_rearm = False
                    # Re-arm the idle timer ONCE now the logical cycle is done.
                    try:
                        from personalclaw.autonudge import get_instance as _an_get

                        _an = _an_get()
                        if _an is not None:
                            _an.notify_turn_complete(
                                _sess.key, errored=getattr(_sess, "_last_turn_errored", False)
                            )
                    except Exception:
                        logger.debug("re-arm after cycle failed for %s", _sess.key, exc_info=True)

            task = asyncio.create_task(_run_turn_bounded())
            # Mirror dashboard /api/chat/send path so session.running == True and sidebar
            # shows the "turn active" three-dots indicator immediately.
            session.task = task
            self.dashboard_state._background_tasks.add(task)
            task.add_done_callback(self.dashboard_state._background_tasks.discard)
            # For loop worker sessions, report the turn outcome to the supervisor
            # so a broken worker fails the loop fast instead of burning cycles
            # silently. A turn that ends with an `error` message (how _run_chat
            # records a crash) counts as a failed cycle.
            # The unified watchdog supervises every kind (sessions are app="loop",
            # keyed loop-<id>); report each worker turn's outcome so a broken worker
            # fails fast. A parallel code task-worker is keyed loop-<id>-<taskid>, so
            # its id-split yields "<id>-<taskid>" which is not a real loop id — the
            # watchdog's record_turn_outcome no-ops on it (only the main worker's id
            # matches a loop), exactly the per-worker isolation the legacy split gave.
            if getattr(session, "_app", "") == "loop" and self.loop_watchdog is not None:

                def _report_turn(_t: "asyncio.Task", _key: str = session.key) -> None:
                    sess = (
                        self.dashboard_state._sessions.get(_key) if self.dashboard_state else None
                    )
                    errored = bool(sess and getattr(sess, "_last_turn_errored", False))
                    cid = _key.split("loop-", 1)[-1]
                    if self.loop_watchdog is not None:
                        self.loop_watchdog.record_turn_outcome(cid, ok=not errored)

                task.add_done_callback(_report_turn)
            self._session_tasks[session.key] = task
            self.dashboard_state.push_sessions_update()
            return True

        def _observer(event: str, loop: NudgeLoop | None) -> None:
            if self.dashboard_state and loop is not None:
                self.dashboard_state.broadcast_ws(
                    "autonudge_state",
                    {
                        "event": event,
                        "session": loop.session_name,
                        "loop": {
                            "id": loop.id,
                            "session_name": loop.session_name,
                            "message": loop.message,
                            "idle_secs": loop.idle_secs,
                            "max_cycles": loop.max_cycles,
                            "cycle_count": loop.cycle_count,
                            "active": loop.active,
                            "last_fire_ts": loop.last_fire_ts,
                        },
                    },
                )

        self.autonudge_svc = AutoNudgeService(base_dir=config_dir(), on_fire=_fire)
        self.autonudge_svc.subscribe(_observer)
        await self.autonudge_svc.start()

        # Goal-loop supervisor — drives loop lifecycle on top of autonudge. Needs
        # both the dashboard state (worker sessions) and the autonudge service, so
        # it's started here once both exist. In --no-dashboard mode there is no
        # state, so the watchdog is skipped.
        if self.dashboard_state is not None:
            # The unified Loop supervisor — ONE watchdog for every kind
            # (general/goal/code/design) on top of autonudge. Replaces the legacy
            # goal-loop + code watchdogs at the cutover (Slice 2e). Re-arm loops left
            # RUNNING/PLANNING by a crash/restart BEFORE it polls — its first poll
            # would otherwise misread a crash-interrupted loop (worker session gone).
            from personalclaw.loop import manager as _loop_manager
            from personalclaw.loop.watchdog import LoopWatchdog

            try:
                await _loop_manager.reap_orphaned_loops(self.dashboard_state, self.autonudge_svc)
            except Exception:
                logger.warning("loop orphan reap at startup failed", exc_info=True)

            self.loop_watchdog = LoopWatchdog(self.dashboard_state, self.autonudge_svc)
            self.loop_watchdog.start()

    async def _init_inbox(self) -> None:
        """Construct the Inbox service (state + store + on-demand AI triage).

        Slack-independent: draft/classify/digest run over STORED items (populated by
        the native push source + any configured poll provider) through the bound chat
        model, so they work with no external provider connected. A message-source
        provider is attached when one is configured, enabling poll/history; otherwise
        polling no-ops. Attached to the dashboard state in ``_init_dashboard`` (which
        runs after this)."""
        from personalclaw.inbox import InboxState, InboxStore
        from personalclaw.inbox_service import InboxService

        sec = self._cfg.inbox
        state = InboxState()
        state.load()
        store = InboxStore()
        store.load()

        provider = None
        if sec.enabled:
            try:
                # The inbox's poll source is the in-process filesystem source. (The
                # inbox is also fed by the always-on native push source regardless.)
                # Channel providers like Slack are NOT inbox sources today — a channel
                # is for interactive chat, not inbox polling; if a Slack-as-inbox-source
                # is ever wanted it'd be a dedicated inbox-provider app, not assumed here.
                from personalclaw.inbox_providers import get_default_provider

                provider = get_default_provider("filesystem")
            except Exception:
                logger.debug("inbox: message-source provider unavailable", exc_info=True)

        if self.inbox_svc is not None:
            self.inbox_svc.stop()
        self.inbox_svc = InboxService(
            state=state,
            store=store,
            provider=provider,
            # The OPERATOR's name (drafts are written on behalf of the human —
            # "reply as {{user_name}}"), NOT agent.bot_name (the assistant's name).
            user_name=(self._cfg.dashboard.user_name or "").strip() or "the user",
            style_rules="\n".join(sec.style_rules or []),
        )
        # Background loop: polls the wired provider (when any) + runs retention
        # maintenance honoring the inbox entity settings. Cheap when idle.
        self.inbox_svc.start()
        logger.info(
            "Inbox service initialized (provider=%s)", provider.source_name if provider else "none"
        )

    async def _restart_inbox(self) -> str:
        """Rebuild the inbox service from current config (e.g. after a settings
        change) and re-attach it to the dashboard state. Returns "ok" or an error
        string, matching the /api/inbox/restart handler contract."""
        try:
            self._cfg = AppConfig.load()
            await self._init_inbox()
            if self.dashboard_state is not None:
                self.dashboard_state._inbox_svc = self.inbox_svc
            return "ok"
        except Exception as exc:
            logger.exception("Inbox restart failed")
            return str(exc) or "restart failed"

    def _notif_meta(self, parent_key: str | None) -> dict[str, str] | None:
        """Build notification meta with session or channel_link for jump-to-source.

        The deep-link format is a provider concern: core asks the registered
        :class:`ChannelDelivery` for ``build_thread_link(channel, ts)`` and never
        constructs vendor URLs itself. No delivery handle (or no link) → no meta.
        """
        if not parent_key:
            return None
        if parent_key.startswith("dashboard:"):
            return {"session": parent_key.removeprefix("dashboard:")}
        if ":" in parent_key and not parent_key.startswith(("cron:", "subagent:", "hook:")):
            chan, ts = parent_key.split(":", 1)
            if self._channel_delivery is not None:
                try:
                    link = self._channel_delivery.build_thread_link(chan, ts)
                except Exception:
                    logger.debug("build_thread_link failed for %s", parent_key, exc_info=True)
                    link = ""
                if link:
                    return {"channel_link": link}
        return None

    async def _deliver_result(
        self,
        title: str,
        task_summary: str,
        result_text: str,
        deliver: str,
    ) -> None:
        """Route a background result to the right surface.

        ``deliver`` values:
        - ``prompt:dashboard:<session>`` → send as user prompt to dashboard session (agent turn)
        - ``dashboard:<session>`` → inject into existing dashboard chat session
        - ``dashboard``        → create new dashboard chat session
        - ``channel:<chan>:<ts>`` → reply to a channel thread (via ChannelDelivery)
        - ``channel``          → new channel DM only (no dashboard notification)
        - ``silent``           → log only
        - ``""`` (empty)       → channel DM (if available) + dashboard notification
        """
        result_text, _ = redact_exfiltration_urls(result_text)
        result_text, _ = redact_credentials(result_text)
        task_summary, _ = redact_exfiltration_urls(task_summary)
        task_summary, _ = redact_credentials(task_summary)
        title, _ = redact_exfiltration_urls(title)
        title, _ = redact_credentials(title)
        body = f"{task_summary}\n\n{result_text}"

        # ── silent: log only ──
        if deliver == "silent":
            logger.info("%s (silent): %s", title, task_summary)
            return

        # ── prompt:dashboard:<session> → send as user prompt to session (triggers agent turn) ──
        if deliver.startswith("prompt:dashboard:"):
            session_name = deliver.removeprefix("prompt:dashboard:")
            if not session_name:
                logger.debug("Heartbeat prompt:dashboard: missing session name, skipping")
                return
            if self.dashboard_state:
                session = self.dashboard_state.resolve_session(session_name)
                if session:
                    # Truncate the variable-size *content* separately so the title/prefix
                    # can never be sliced at a multi-byte boundary. errors='ignore'
                    # (not 'replace') keeps the final byte size <= limit — U+FFFD
                    # would be 3 bytes and push past the cap.
                    prefix = f"{title}\n\n"
                    prefix_bytes = len(prefix.encode("utf-8"))
                    content_budget = max(0, MAX_PROMPT_BYTES - prefix_bytes)
                    content_bytes = result_text.encode("utf-8")
                    if len(content_bytes) > content_budget:
                        truncated = content_bytes[:content_budget].decode("utf-8", errors="ignore")
                        logger.warning(
                            "Heartbeat prompt truncated to %d bytes for session %s",
                            MAX_PROMPT_BYTES,
                            session_name,
                        )
                        prompt = prefix + truncated
                    else:
                        prompt = prefix + result_text
                    # Lazy import avoids circular dependency (chat → gateway)
                    from personalclaw.dashboard.chat import _run_chat

                    sel().log_api_access(
                        caller="heartbeat",
                        operation="heartbeat_prompt_deliver",
                        outcome="approved",
                        source="gateway",
                        resources=f"requested={session_name},resolved={session.key}",
                    )
                    ran = session.enqueue_or_run_prompt(prompt, _run_chat, self.dashboard_state)
                    if ran:
                        # Only push UI updates when the prompt actually started —
                        # queued prompts produce no visible change until dequeued.
                        self.dashboard_state.push_sessions_update()
                        self.dashboard_state.notify(
                            "heartbeat", title, body, meta={"session": session.key}
                        )
                    else:
                        logger.info(
                            "Heartbeat prompt queued for busy session %s (queue depth=%d)",
                            session.key,
                            session.queue_depth,
                        )
                else:
                    sel().log_api_access(
                        caller="heartbeat",
                        operation="heartbeat_prompt_deliver",
                        outcome="not_found",
                        source="gateway",
                        resources=f"requested={session_name}",
                    )
                    logger.warning("Heartbeat prompt target session %s not found", session_name)
            else:
                logger.debug("prompt:dashboard:%s ignored — no dashboard_state", session_name)
            return

        # ── dashboard:<session> → inject into specific session ──
        if deliver.startswith("dashboard:"):
            session_name = deliver.removeprefix("dashboard:")
            if self.dashboard_state:
                session = self.dashboard_state.resolve_session(session_name)
                if session:
                    sel().log_api_access(
                        caller="heartbeat",
                        operation="heartbeat_inject_deliver",
                        outcome="approved",
                        source="gateway",
                        resources=f"requested={session_name},resolved={session.key}",
                    )
                    session.append("assistant", f"{title}\n\n{result_text}", "msg msg-a")
                    self.dashboard_state.push_sessions_update()
                    self.dashboard_state.notify(
                        "heartbeat", title, body, meta={"session": session.key}
                    )
                else:
                    sel().log_api_access(
                        caller="heartbeat",
                        operation="heartbeat_inject_deliver",
                        outcome="not_found",
                        source="gateway",
                        resources=f"requested={session_name}",
                    )
                    logger.warning("Heartbeat deliver target session %s not found", session_name)
            else:
                logger.debug("dashboard:%s ignored — no dashboard_state", session_name)
            return

        # ── dashboard (no session) → new session ──
        if deliver == "dashboard":
            if self.dashboard_state:
                session = self.dashboard_state.get_or_create_session()
                session.append("assistant", f"{title}\n\n{result_text}", "msg msg-a")
                self.dashboard_state.push_sessions_update()
                self.dashboard_state.notify("heartbeat", title, body, meta={"session": session.key})
            return

        # ── channel (no thread) → new channel DM only ──
        if deliver == "channel":
            if self._channel_delivery is not None and self._owner_id:
                try:
                    channel = await self._channel_delivery.open_dm(self._owner_id)
                    if channel:
                        await self._channel_delivery.deliver_notification(
                            channel, title, result_text
                        )
                except Exception:
                    logger.exception("Heartbeat channel delivery failed")
            return

        # ── channel:<channel>:<thread_ts> → reply to thread ──
        if deliver.startswith("channel:"):
            parts = deliver.split(":", 2)
            try:
                if self._channel_delivery is not None and len(parts) == 3:
                    chan, ts = parts[1], parts[2]
                    await self._channel_delivery.deliver_notification(chan, title, result_text, ts)
                elif self._channel_delivery is not None and self._owner_id:
                    chan = await self._channel_delivery.open_dm(self._owner_id)
                    if chan:
                        await self._channel_delivery.deliver_notification(chan, title, result_text)
            except Exception:
                logger.exception("Heartbeat channel delivery failed")
            if self.dashboard_state:
                self.dashboard_state.notify("heartbeat", title, body)
            return

        # ── default: channel DM + dashboard notification ──
        if self._channel_delivery is not None and self._owner_id:
            try:
                channel = await self._channel_delivery.open_dm(self._owner_id)
                if channel:
                    await self._channel_delivery.deliver_notification(channel, title, result_text)
            except Exception:
                logger.exception("Heartbeat channel delivery failed")
        if self.dashboard_state:
            self.dashboard_state.notify("heartbeat", title, body)

    def _init_mcp_discovery(self) -> None:
        """Log configured MCP servers at startup.

        The actual config merge is handled by rebuild_agent_config() which
        runs earlier in __init__. This just logs what's configured for
        debugging visibility.
        """
        try:
            from personalclaw.mcp_discovery import list_servers  # circular import

            servers = list_servers()
            if servers:
                srv_names = [s.name for s in servers]
                logger.info("Configured MCP servers: %s", ", ".join(srv_names))
            else:
                logger.info("No MCP servers configured")
        except Exception:
            logger.debug("MCP server listing failed", exc_info=True)

    def _init_subagents(self) -> None:
        """Initialize the subagent manager."""

        async def _broadcast_subagent_status(info: SubagentInfo, event: str) -> None:
            """Broadcast subagent status change via WS for per-session tracking."""
            if not self.dashboard_state:
                return
            try:
                session = info.parent_session_key.removeprefix("dashboard:")
                agents = (
                    self.subagent_mgr.running_agents_for(info.parent_session_key)
                    if self.subagent_mgr
                    else []
                )
                running = len(agents)
                payload = {
                    "running": running,
                    "id": info.id,
                    "event": event,
                    "session": session,
                    "agents": agents,
                }
                logger.info(
                    "📡 subagent_status WS: event=%s session=%s running=%d agents=%d",
                    event,
                    session,
                    running,
                    len(agents),
                )
                self.dashboard_state.broadcast_ws("subagent_status", payload)
            except Exception:
                logger.info("Failed to broadcast subagent %s status", info.id, exc_info=True)

        def _retrigger_recovery(session: "_ChatSession", parent_key: str) -> None:
            """Drain queued failures into a new recovery _run_chat turn.

            Called from _on_done callbacks after resetting the guard, so
            failures that arrived while the previous recovery was running
            get processed without waiting for user input.
            """
            if session._recovery_chat_triggered or not session._pending_subagent_failures:
                return
            if not self.dashboard_state:
                return
            _max_retrigger = 3
            if session._recovery_retrigger_count >= _max_retrigger:
                logger.warning(
                    "Recovery retrigger cap (%d) reached for %s, dropping %d queued failures",
                    _max_retrigger,
                    parent_key,
                    len(session._pending_subagent_failures),
                )
                session._pending_subagent_failures.clear()
                return
            session._recovery_retrigger_count += 1
            session._recovery_chat_triggered = True
            from personalclaw.dashboard.chat import _run_chat

            failures = session._pending_subagent_failures[:]
            session._pending_subagent_failures.clear()
            msg = "\n\n".join(failures)
            msg, _ = redact_exfiltration_urls(msg)
            msg, _ = redact_credentials(msg)
            session.append("user", msg, "msg msg-u auto-go")
            logger.info(
                "Re-triggering recovery _run_chat for %s (%d queued failures)",
                parent_key,
                len(failures),
            )

            def _done(t: "asyncio.Task") -> None:  # type: ignore[type-arg]
                if t.cancelled():
                    logger.warning("Re-triggered recovery cancelled for %s", parent_key)
                    session._recovery_chat_triggered = False
                    return
                elif t.exception():
                    logger.error(
                        "Re-triggered recovery failed for %s",
                        parent_key,
                        exc_info=t.exception(),
                    )
                session._recovery_chat_triggered = False
                if session._pending_subagent_failures:
                    _retrigger_recovery(session, parent_key)

            _task = asyncio.create_task(
                asyncio.wait_for(
                    _run_chat(self.dashboard_state, session, msg),
                    timeout=CHAT_TURN_TIMEOUT,
                ),
            )
            session.task = _task
            self._background_tasks.add(_task)
            _task.add_done_callback(self._background_tasks.discard)
            _task.add_done_callback(_done)

        async def _subagent_done(info: SubagentInfo) -> None:
            async def _inject_with_retry(
                client,
                msg: str,
                parent_key: str,
                label: str,
            ) -> str | None:
                """Retry stream_and_collect up to 3 times on AcpError.

                Cancels any orphaned prompt between attempts so the next
                retry doesn't hit 'Prompt already in progress'.
                """
                for attempt in range(3):
                    try:
                        return await stream_and_collect(client, msg)
                    except PromptBusyExhaustedError:
                        # Provider is dead after exhausting prompt-busy retries.
                        # Reset session + notify, same as TimeoutError path.
                        logger.error(
                            "Subagent %s: provider dead after prompt-busy retries (%s)",
                            info.id,
                            label,
                        )
                        try:
                            assert self.sessions is not None
                            await self.sessions.reset(parent_key)
                        except Exception:
                            logger.debug(
                                "Failed to reset %s after busy exhaustion",
                                parent_key,
                                exc_info=True,
                            )
                        if self.subagent_mgr:
                            self.subagent_mgr.notify_injection_failed(
                                info,
                                reason="provider dead after prompt-busy retries",
                            )
                        return None
                    except AcpProcessDied:
                        logger.warning(
                            "Subagent %s: ACP process died during %s injection",
                            info.id,
                            label,
                        )
                        try:
                            assert self.sessions is not None
                            await self.sessions.reset(parent_key)
                        except Exception:
                            logger.debug(
                                "Failed to reset %s after process death",
                                parent_key,
                                exc_info=True,
                            )
                        if self.subagent_mgr:
                            self.subagent_mgr.notify_injection_failed(
                                info,
                                reason="ACP process died",
                            )
                        return None
                    except AcpError:
                        if attempt == 2:
                            raise
                        logger.warning(
                            "Subagent %s %s injection attempt %d failed, retrying",
                            info.id,
                            label,
                            attempt + 1,
                        )
                        try:
                            assert self.sessions is not None
                            await self.sessions.cancel_current(parent_key)
                        except Exception:
                            logger.debug(
                                "Failed to cancel parent prompt for %s",
                                info.id,
                                exc_info=True,
                            )
                        await asyncio.sleep(2**attempt)
                return None  # unreachable, but satisfies type checker

            await _broadcast_subagent_status(info, "done")
            status = "failed" if info.error else "completed"
            title = f"Subagent `{info.id}` {status}"

            parent_key = info.parent_session_key
            guard_msg = ""
            # Full result (up to 3000 chars) for immediate use.
            if info.error:
                detail = f"Error: {info.error}"
            else:
                detail = info.result or "_No response._"
                if len(detail) > 3000:
                    detail = detail[:3000] + "\n…[truncated]"
            detail, _ = redact_exfiltration_urls(detail)
            detail, _ = redact_credentials(detail)
            task_text, _ = redact_exfiltration_urls(info.task)
            task_text, _ = redact_credentials(task_text)
            task_text = task_text[:100]
            body = f"{task_text}\n\n{detail}"
            title, _ = redact_exfiltration_urls(title)
            title, _ = redact_credentials(title)

            announce = (
                f"[Subagent completion event]\n"
                f"Agent `{info.id}`"
                f"{f' ({info.agent})' if info.agent else ''}"
                f" {status}\n"
                f"Task: {task_text}\n\n"
                f"{detail}"
                f"{guard_msg}"
            )

            parent_key = info.parent_session_key

            # ── Route completion back to the originating session ──
            # Dashboard → dashboard only (no channel delivery)
            # Channel → channel thread + dashboard notification
            # Cron/no parent → dashboard notification only

            if parent_key.startswith("dashboard:") and self.dashboard_state:
                # Dashboard session — route subagent result through _run_chat
                # for full streaming, tool call visibility, and proper lifecycle.
                _session_name = parent_key.removeprefix("dashboard:")
                _injection_session = self.dashboard_state.get_session(_session_name)

                # Redact LLM-generated output before any external surface
                announce, _ = redact_exfiltration_urls(announce)
                announce, _ = redact_credentials(announce)
                body, _ = redact_exfiltration_urls(body)
                body, _ = redact_credentials(body)

                if _injection_session:

                    if _injection_session.running:
                        # Session is busy — wait for current turn to finish,
                        # then inject. No visible queue card.
                        _current = _injection_session.task
                        if _current is not None:
                            try:
                                await asyncio.wait_for(
                                    asyncio.shield(_current),
                                    timeout=INJECTION_TIMEOUT,
                                )
                            except asyncio.TimeoutError:
                                pass  # Timed out waiting — session still busy, will be queued below
                            except asyncio.CancelledError:
                                raise  # Don't swallow cancellation of this coroutine
                            except Exception:
                                pass  # Task failed — session is now idle

                        # Re-check: another injection may have claimed the session
                        # during the await above.
                        if _injection_session.running:
                            logger.info(
                                "Subagent %s: session %s claimed by another injection, queuing",
                                info.id,
                                _session_name,
                            )
                            # Bounded by CHAT_TURN_TIMEOUT (~600s): _run_chat's
                            # finally block drains session._queue on any exit path.
                            _injection_session.queue_append(announce)
                            self.dashboard_state.push_sessions_update()
                            logger.info("Subagent %s → queued in %s", info.id, _session_name)
                            self.dashboard_state.notify(
                                "subagent",
                                title,
                                body,
                                meta=self._notif_meta(parent_key),
                            )
                            return

                    # Session is idle — start _run_chat.
                    _task = asyncio.create_task(
                        asyncio.wait_for(
                            _run_chat(self.dashboard_state, _injection_session, announce),
                            timeout=CHAT_TURN_TIMEOUT,
                        )
                    )
                    _injection_session.task = _task
                    self.dashboard_state._background_tasks.add(_task)
                    _task.add_done_callback(self.dashboard_state._background_tasks.discard)

                    def _on_inject_done(t: "asyncio.Task") -> None:  # type: ignore[type-arg]
                        if _injection_session.task is t:
                            _injection_session.task = None
                        if not t.cancelled() and t.exception():
                            logger.error("Subagent injection _run_chat failed: %s", t.exception())
                            if self.subagent_mgr:
                                _reason = str(t.exception())
                                _reason, _ = redact_exfiltration_urls(_reason)
                                _reason, _ = redact_credentials(_reason)
                                self.subagent_mgr.notify_injection_failed(
                                    info,
                                    reason=_reason,
                                )

                    _task.add_done_callback(_on_inject_done)
                    self.dashboard_state.push_sessions_update()
                    logger.info("Subagent %s → _run_chat in %s", info.id, _session_name)
                else:
                    logger.info(
                        "Subagent %s: parent session %s gone, notification only",
                        info.id,
                        _session_name,
                    )

                # Dashboard notification for the notification panel
                self.dashboard_state.notify(
                    "subagent",
                    title,
                    body,
                    meta=self._notif_meta(parent_key),
                )
                return

            if parent_key and not parent_key.startswith(("cron:", "subagent:")):
                # Channel session — inject silently into ACP session (no visible channel message).
                # Retry up to _MAX_INJECT_ATTEMPTS times on timeout.
                assert self.sessions is not None
                _injected = False
                _channel_failure_reasons: list[str] = []
                _sleep_before_retry = False
                for _attempt in range(1, _MAX_INJECT_ATTEMPTS + 1):
                    if _sleep_before_retry:
                        await asyncio.sleep(2)
                        _sleep_before_retry = False
                    _acquired = False
                    try:
                        logger.debug(
                            "Subagent %s: channel injection attempt %d/%d into %s",
                            info.id,
                            _attempt,
                            _MAX_INJECT_ATTEMPTS,
                            parent_key,
                        )
                        client, is_new, _resumed = await self.sessions.get_or_create(parent_key)
                        _acquired = True
                        if self.ctx_builder:
                            msg, _ = self.ctx_builder.build_message(announce, is_new, parent_key)
                        else:
                            msg = announce
                        response = await asyncio.wait_for(
                            _inject_with_retry(client, msg, parent_key, "channel"),
                            timeout=INJECTION_TIMEOUT,
                        )
                        _injected = True  # LLM processed result; channel posting is best-effort

                        # Post only the LLM's synthesized response to the channel
                        try:
                            if response and self._channel_delivery is not None and self._owner_id:
                                channel = (
                                    self.sessions.get_channel(parent_key) if self.sessions else None
                                ) or await self._channel_delivery.open_dm(self._owner_id)
                                if channel:
                                    elapsed = (
                                        info.elapsed
                                        if info.elapsed > 0
                                        else (time.monotonic() - info.started)
                                    )
                                    await self._channel_delivery.deliver_subagent_reply(
                                        channel,
                                        response,
                                        parent_key,
                                        elapsed,
                                    )
                        except Exception:
                            logger.exception(
                                "Subagent %s: channel posting failed (injection succeeded)",
                                info.id,
                            )
                        logger.info("Subagent %s → channel session %s", info.id, parent_key)
                        break
                    except asyncio.TimeoutError:
                        _channel_failure_reasons.append(
                            f"attempt {_attempt} timed out after {int(INJECTION_TIMEOUT)}s"
                        )
                        logger.warning(
                            "Subagent %s: channel injection attempt %d/%d timed out after %.0fs",
                            info.id,
                            _attempt,
                            _MAX_INJECT_ATTEMPTS,
                            INJECTION_TIMEOUT,
                        )
                        if _acquired:
                            try:
                                await self.sessions.reset(parent_key)
                            except Exception:
                                logger.debug(
                                    "Failed to reset %s after channel injection timeout",
                                    parent_key,
                                    exc_info=True,
                                )
                        if _attempt < _MAX_INJECT_ATTEMPTS:
                            _sleep_before_retry = True
                    except Exception as exc:
                        _channel_failure_reasons.append(f"attempt {_attempt} failed: {exc}")
                        logger.exception("Subagent %s channel injection failed", info.id)
                        break
                    finally:
                        if _acquired:
                            try:
                                await self.sessions.cancel_current(parent_key)
                            except Exception:
                                logger.debug(
                                    "Failed to cancel parent prompt for %s",
                                    info.id,
                                    exc_info=True,
                                )
                            try:
                                self.sessions.release(parent_key)
                            except Exception:
                                logger.exception("Failed to release session %s", parent_key)

                if not _injected:
                    _last_failure_reason = "; ".join(_channel_failure_reasons)
                    _last_failure_reason, _ = redact_exfiltration_urls(_last_failure_reason)
                    _last_failure_reason, _ = redact_credentials(_last_failure_reason)
                    logger.error(
                        "Subagent %s: all %d channel injection attempts failed: %s",
                        info.id,
                        _MAX_INJECT_ATTEMPTS,
                        _last_failure_reason,
                    )
                    if self.subagent_mgr:
                        self.subagent_mgr.notify_injection_failed(
                            info,
                            reason=_last_failure_reason,
                        )
                # Dashboard notification
                if self.dashboard_state:
                    self.dashboard_state.notify(
                        "subagent",
                        title,
                        body,
                        meta=self._notif_meta(parent_key),
                    )
                return

            # Cron parent — inject result back into the cron session.
            # Track pending injections to avoid resetting the session while
            # other subagents are queued behind the per-session semaphore.
            if parent_key.startswith("cron:"):
                self._cron_injecting[parent_key] = self._cron_injecting.get(parent_key, 0) + 1
                assert self.sessions is not None
                acquired = False
                cron_response: str | None = None
                try:
                    client, is_new, _resumed = await self.sessions.get_or_create(parent_key)
                    acquired = True
                    if self.ctx_builder:
                        msg, _ = self.ctx_builder.build_message(announce, is_new, parent_key)
                    else:
                        msg = announce
                    cron_response = await asyncio.wait_for(
                        _inject_with_retry(client, msg, parent_key, "cron"),
                        timeout=INJECTION_TIMEOUT,
                    )
                except asyncio.TimeoutError:
                    logger.error(
                        "Subagent %s: cron injection timed out after %.0fs",
                        info.id,
                        INJECTION_TIMEOUT,
                    )
                    try:
                        await self.sessions.reset(parent_key)
                    except Exception:
                        logger.debug(
                            "Failed to reset %s after cron injection timeout",
                            parent_key,
                            exc_info=True,
                        )
                    if self.subagent_mgr:
                        self.subagent_mgr.notify_injection_failed(
                            info,
                            reason=f"injection timed out after {int(INJECTION_TIMEOUT)}s",
                        )
                except Exception:
                    logger.exception("Subagent %s cron injection failed", info.id)
                finally:
                    if acquired:
                        try:
                            await self.sessions.cancel_current(parent_key)
                        except Exception:
                            logger.debug(
                                "Failed to cancel parent prompt for cron %s", info.id, exc_info=True
                            )
                        try:
                            self.sessions.release(parent_key)
                        except Exception:
                            logger.exception("Failed to release session %s", parent_key)
                    self._cron_injecting[parent_key] = self._cron_injecting.get(parent_key, 1) - 1
                    if self._cron_injecting[parent_key] <= 0:
                        self._cron_injecting.pop(parent_key, None)
                if cron_response:
                    cron_response, _ = redact_exfiltration_urls(cron_response)
                    cron_response, _ = redact_credentials(cron_response)
                    body = f"{body}\n\n{cron_response}"
                    logger.info("Subagent %s → cron session %s", info.id, parent_key)
                # Reset only when no subagents running AND no injections pending
                still_running = self.subagent_mgr and any(
                    a.parent_session_key == parent_key and a.id != info.id
                    for a in self.subagent_mgr.running
                )
                still_injecting = self._cron_injecting.get(parent_key, 0) > 0
                if not still_running and not still_injecting:
                    try:
                        await self.sessions.reset(parent_key)
                        logger.info(
                            "Cron session %s: last subagent done, session reset", parent_key
                        )
                        # reset succeeded → reaper no longer needs the
                        # registered ephemeral key. Clear inside try so a failed
                        # reset leaves the key registered (ephemeral session may
                        # still be alive — reaper must be able to target it).
                        # parent_key is "cron:{job_id}" (persistent) or
                        # "cron:{job_id}:{run_id}" (ephemeral); job_id is the
                        # second colon-separated segment in both cases.
                        cron_svc = getattr(self, "cron_svc", None)
                        if cron_svc is not None:
                            parts = parent_key.split(":", 2)
                            if len(parts) >= 2:
                                cron_svc.clear_active_session_key(parts[1])
                    except Exception:
                        logger.exception(
                            "Cron session %s: reset failed after last subagent", parent_key
                        )

            # Dashboard notification
            if self.dashboard_state and not info.silent:
                self.dashboard_state.notify(
                    "subagent",
                    title,
                    body,
                    meta=self._notif_meta(parent_key),
                )
            if not parent_key.startswith("cron:"):
                logger.info("Subagent %s → notification only (parent=%s)", info.id, parent_key)

        assert self.sessions is not None
        assert self.ctx_builder is not None

        def _is_yolo() -> bool:
            # Subagents inherit the EXPIRING override, not a stale flag: route
            # through is_yolo_active() so a TTL-expired dashboard YOLO no longer
            # auto-approves spawned subagents' tool calls.
            from personalclaw.trust_mode import is_yolo_active as is_yolo_mode

            state = self.dashboard_state
            if state is not None and state.is_yolo_active():
                return True
            return is_yolo_mode()

        def _spawn_session_resolver(request_id: str) -> str:
            """Resolve session from spawn request_id (spawn:{agent_id})."""
            agent_id = request_id.removeprefix("spawn:")
            info = self.subagent_mgr.get(agent_id) if self.subagent_mgr is not None else None
            session = (
                info.parent_session_key.removeprefix("dashboard:")
                if info and info.parent_session_key
                else ""
            )
            logger.info(
                "_spawn_session_resolver: rid=%s agent_id=%s info=%s session=%s",
                request_id,
                agent_id,
                info is not None,
                session,
            )
            return session

        _approve_subagent = self._interactive_approval(
            "subagent", session_resolver=_spawn_session_resolver
        )

        async def _spawn_approve(
            request_id: str, description: str, parent_session_key: str = ""
        ) -> bool:
            event = LLMEvent(kind="permission_request", request_id=request_id, title=description)
            return await _approve_subagent(event, parent_session_key)

        async def _subagent_event(etype: str, info: SubagentInfo, extra: dict) -> None:
            if not self.dashboard_state:
                return
            session_name = info.parent_session_key.removeprefix("dashboard:")
            base = {"id": info.id, "session": session_name}
            if etype == "subagent_injection_failed":
                # Show error in UI + queue for LLM context on next turn.
                session = self.dashboard_state.get_session(session_name)
                if session:
                    task_preview, _ = redact_exfiltration_urls((info.task or "")[:100])
                    task_preview, _ = redact_credentials(task_preview)
                    error_text, _ = redact_exfiltration_urls(extra.get("error", "timed out"))
                    error_text, _ = redact_credentials(error_text)
                    session.append(
                        "assistant",
                        f"[Subagent completion event]\n"
                        f"Agent `{info.id}` failed\n"
                        f"Task: {task_preview}\n\n"
                        f"Error: {error_text}\n"
                        f"Result delivery timed out — the subagent finished but "
                        f"its result could not be injected into this session.",
                        "msg msg-a",
                    )
                    # Queue failure for LLM context drain
                    failure_msg = extra.get("failure_msg", "")
                    if failure_msg:
                        failure_msg, _ = redact_exfiltration_urls(failure_msg)
                        failure_msg, _ = redact_credentials(failure_msg)
                        session._pending_subagent_failures.append(failure_msg)
                    self.dashboard_state.push_sessions_update()
                    logger.warning(
                        "Injected timeout error for subagent %s into session %s",
                        info.id,
                        session_name,
                    )
                self.dashboard_state.broadcast_ws(etype, {**base, **extra})
            elif etype == "subagent_chunk":
                # Heavy data — only to subscribed clients
                self.dashboard_state.broadcast_ws_subagent_subscribers(etype, {**base, **extra})
            else:
                # Lightweight status events — broadcast to all
                self.dashboard_state.broadcast_ws(etype, {**base, **extra})

        self.subagent_mgr = SubagentManager(
            sessions=self.sessions,
            ctx_builder=self.ctx_builder,
            on_done=_subagent_done,
            max_concurrent=resolve_max_subagents(
                self._cfg.agent.max_subagents,
                per_agent_gb=self._cfg.agent.spawn_min_memory_gb,
            ),
            default_turn_limit=self._cfg.agent.subagent_max_turns,
            default_timeout=self._cfg.agent.subagent_timeout_secs,
            on_tool_approval=_approve_subagent,
            on_spawn_approval=_spawn_approve,
            is_yolo=_is_yolo,
            on_event=_subagent_event,
        )
        self.subagent_mgr.start_reaper()

    async def _init_dashboard(self) -> None:
        """Start the dashboard web server."""
        assert self.sessions is not None
        assert self.cron_svc is not None

        configured_host, dashboard_port = parse_dashboard_url(self._cfg.dashboard.url)
        # --port override (literal int or "auto" for ephemeral)
        if self._port_override == "auto":
            dashboard_port = 0
        elif self._port_override is not None:
            dashboard_port = int(self._port_override)
        self._dashboard_port = dashboard_port
        self._configured_host = configured_host
        # resolve_bind_host() honors the PERSONALCLAW_BIND_HOST escape hatch
        # and otherwise sticks to loopback. ``local_only`` is derived from the
        # resolved bind.
        self._local_only = is_local_bind(resolve_bind_host())
        self._dashboard_runner, self.dashboard_state = await start_dashboard(
            sessions=self.sessions,
            crons=self.cron_svc,
            lessons=LessonStore(),
            port=dashboard_port,
            subagents=self.subagent_mgr,
            context_builder=self.ctx_builder,
            conversation_log=self.conv_log,
            consolidator=self.consolidator,
            local_only=self._local_only,
            configured_host=configured_host,
            dashboard_url=self._cfg.dashboard.url,
            owner_id=self._owner_id,
        )
        # When --port auto was requested, read the OS-assigned ephemeral port
        # back from the runner so subsequent URL building and the READY line
        # use the real bound port.
        if dashboard_port == 0 and self._dashboard_runner is not None:
            addresses = self._dashboard_runner.addresses
            if addresses:
                self._dashboard_port = addresses[0][1]
        if self.dashboard_state:
            self.dashboard_state.no_crons = self._no_crons  # dashboard mode
            # Let the scheduler hint dashboard clients to refresh views when a
            # run records (Executions/Logs live-update without polling).
            _state = self.dashboard_state
            self.cron_svc.set_refresh_callback(lambda *kinds: _state.push_refresh(*kinds))
            # Attach the inbox service (built in _init_inbox, which runs before the
            # dashboard state exists) so the Inbox handlers reach draft/classify/digest.
            self.dashboard_state._inbox_svc = self.inbox_svc
            self.dashboard_state._inbox_restart = self._restart_inbox

    async def _init_api_server(self) -> None:
        """Start a minimal API-only HTTP server for MCP tool transport."""
        from personalclaw.dashboard import start_api_server

        assert self.sessions is not None
        assert self.cron_svc is not None
        configured_host, dashboard_port = parse_dashboard_url(self._cfg.dashboard.url)
        # --port override (literal int or "auto" for ephemeral)
        if self._port_override == "auto":
            dashboard_port = 0
        elif self._port_override is not None:
            dashboard_port = int(self._port_override)
        self._dashboard_port = dashboard_port
        self._configured_host = configured_host
        # resolve_bind_host() honors the PERSONALCLAW_BIND_HOST escape hatch
        # and otherwise sticks to loopback. ``local_only`` is derived from the
        # resolved bind.
        self._local_only = is_local_bind(resolve_bind_host())
        self._dashboard_runner, self.dashboard_state = await start_api_server(
            sessions=self.sessions,
            crons=self.cron_svc,
            lessons=LessonStore(),
            port=dashboard_port,
            subagents=self.subagent_mgr,
            owner_id=self._owner_id,
        )
        if dashboard_port == 0 and self._dashboard_runner is not None:
            addresses = self._dashboard_runner.addresses
            if addresses:
                self._dashboard_port = addresses[0][1]
        if self.dashboard_state:
            self.dashboard_state.no_crons = self._no_crons  # API-only mode

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------

    async def _shutdown(self) -> None:
        """Graceful cleanup of all services."""
        # Reap app-backend subprocesses FIRST, synchronously — before the
        # ACP/session teardown below. ACP cleanup can take many seconds when a
        # delegate CLI is wedged (force-kill retries), and it used to run in the
        # same gather as the dashboard runner's on_cleanup hooks; an impatient
        # operator SIGKILLing the gateway during that window would orphan the
        # app backends. Stopping them up front makes the common path leak-free
        # regardless of how slow (or interrupted) the rest of shutdown is. The
        # on_cleanup hook remains as a backstop (idempotent — _procs is emptied
        # by stop_all, so the second pass is a no-op).
        try:
            from personalclaw.apps.backend_runtime import get_backend_supervisor

            get_backend_supervisor().stop_all()
        except Exception:
            logger.debug("early app-backend reap failed", exc_info=True)

        # Save all active chat sessions to history before shutdown
        if self.dashboard_state:
            from personalclaw.dashboard.chat import save_all_sessions_to_history

            save_all_sessions_to_history(self.dashboard_state)
            self.dashboard_state.file_indexes.stop_all()

        # Cancel in-flight handler tasks
        for t in list(self._handler_tasks):
            t.cancel()
        if self._handler_tasks:
            await asyncio.gather(*self._handler_tasks, return_exceptions=True)

        # Stop services
        if self.loop_watchdog:
            await self.loop_watchdog.stop()
        if self.cron_svc:
            await self.cron_svc.stop()
        if self.heartbeat_svc:
            self.heartbeat_svc.stop()
        if self.inbox_svc:
            self.inbox_svc.stop()
        # Kill all ACP processes and close connections
        cleanup_tasks: list = []
        if self.subagent_mgr:
            cleanup_tasks.append(self.subagent_mgr.cancel_all())
        if self.sessions:
            cleanup_tasks.append(self.sessions.close_all())
        if self._dashboard_runner:
            # Close WS connections first so handlers exit promptly
            if self.dashboard_state:
                await self.dashboard_state.close_all_ws()
            cleanup_tasks.append(self._dashboard_runner.cleanup())
        # Stop channel inbound receivers (Slack Socket-Mode lives in the app now).
        from personalclaw.channel_transports import get_transport, list_transports

        for _tn in list_transports():
            _tp = get_transport(_tn)
            if _tp is not None:
                cleanup_tasks.append(_tp.stop_inbound())

        if cleanup_tasks:
            await asyncio.gather(*cleanup_tasks, return_exceptions=True)

    # ------------------------------------------------------------------
    # Auto-update
    # ------------------------------------------------------------------

    async def _check_for_updates(self) -> None:
        """Blocking update check — auto-applies if enabled, otherwise notifies."""
        try:
            from personalclaw.dashboard.handlers import _do_update_check, _update_info

            await _do_update_check()
            if _update_info.get("available"):
                logger.info("Updates available from remote")
                from personalclaw.config import AppConfig

                cfg = AppConfig.load()
                if cfg.auto_update:
                    logger.info("Auto-update enabled — applying update")
                    await self._auto_apply_update()
                elif self.dashboard_state:
                    self.dashboard_state.push_refresh("update_available")
            else:
                print("Already on latest version")
        except Exception:
            logger.debug("Update check failed", exc_info=True)

    async def _auto_apply_update(self) -> None:
        """Auto-apply: fetch, reset to remote, rebuild, restart.

        Uses ``git fetch`` + ``git reset --hard`` instead of ``git pull``
        so local tracked-file edits never cause merge conflicts.
        Untracked files (task specs, notes) are untouched by reset.
        """
        proj = os.environ.get("PERSONALCLAW_PROJECT_DIR", "")
        if not proj:
            return
        try:
            # Detect current branch
            branch_proc = await asyncio.create_subprocess_exec(
                "git",
                "rev-parse",
                "--abbrev-ref",
                "HEAD",
                cwd=proj,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            branch_out, _ = await asyncio.wait_for(branch_proc.communicate(), timeout=10)
            if branch_proc.returncode != 0:
                logger.error("Auto-update: could not determine current branch")
                return
            branch = branch_out.strip().decode() if branch_out else ""
            # Detached HEAD is coerced to the release branch — a checkout that
            # detached at a release tag still auto-updates back onto main.
            if not branch or branch == "HEAD":
                branch = "main"

            # Only auto-update on main — feature branches need manual update
            if branch != "main":
                logger.debug("Auto-update: skipping — on branch %s, not main", branch)
                return

            if self.dashboard_state:
                self.dashboard_state.push_update_progress("pulling", "Fetching latest changes…")

            fetch = await asyncio.create_subprocess_exec(
                "git",
                "fetch",
                "origin",
                branch,
                cwd=proj,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(fetch.communicate(), timeout=60)

            if fetch.returncode != 0:
                if self.dashboard_state:
                    self.dashboard_state.clear_update_progress()
                return

            # Check if there are actually new commits
            diff_proc = await asyncio.create_subprocess_exec(
                "git",
                "diff",
                "HEAD",
                f"origin/{branch}",
                "--quiet",
                cwd=proj,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(diff_proc.wait(), timeout=10)
            if diff_proc.returncode == 0:
                # No diff — already up to date
                if self.dashboard_state:
                    self.dashboard_state.clear_update_progress()
                return

            # Warn if local tracked-file edits will be discarded
            status_proc = await asyncio.create_subprocess_exec(
                "git",
                "status",
                "--porcelain",
                cwd=proj,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            status_out, _ = await asyncio.wait_for(status_proc.communicate(), timeout=10)
            if status_out and status_out.strip():
                tracked = [
                    ln
                    for ln in status_out.decode(errors="replace").splitlines()
                    if not ln.startswith("??")
                ]
                if tracked:
                    logger.warning("Auto-update: discarding local tracked-file changes in %s", proj)

            # Hard reset to remote — discards local tracked-file edits,
            # untracked files (task specs, notes) are preserved.
            reset = await asyncio.create_subprocess_exec(
                "git",
                "reset",
                "--hard",
                f"origin/{branch}",
                cwd=proj,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(reset.wait(), timeout=10)
            if reset.returncode != 0:
                logger.error("Auto-update: git reset --hard failed (rc=%d)", reset.returncode)
                if self.dashboard_state:
                    self.dashboard_state.clear_update_progress()
                return
            logger.info("Auto-update: reset to origin/%s, rebuilding", branch)

            # pip install -e . picks up new dependencies into the RUNNING
            # interpreter's env (sys.executable) before the re-exec. Git ran
            # at the repo root; pip + the frontend build run at the package
            # root (nested in the monorepo layout).
            from personalclaw.dashboard.handlers.updates import _package_root

            pkg_root = _package_root(proj)
            if self.dashboard_state:
                self.dashboard_state.push_update_progress("installing", "Installing package…")
            pip_install = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "pip",
                "install",
                "-e",
                ".",
                "--quiet",
                cwd=pkg_root,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, pip_err = await asyncio.wait_for(pip_install.communicate(), timeout=400)
            if pip_install.returncode != 0:
                logger.error(
                    "Auto-update: pip install failed (rc=%d): %s",
                    pip_install.returncode,
                    pip_err.decode(errors="replace")[:500],
                )
                # Restarting into an env with missing/stale deps could brick
                # the gateway — keep running the current image instead.
                if self.dashboard_state:
                    self.dashboard_state.push_update_progress("error", "pip install failed")
                return

            if self.dashboard_state:
                self.dashboard_state.push_update_progress("building", "Building frontend…")
            # Build frontend assets (npm ci && npm run build in <pkg>/web/)
            await build_frontend_async(
                pkg_root,
                push_progress=(
                    self.dashboard_state.push_update_progress if self.dashboard_state else None
                ),
            )

            logger.info("Auto-update: rebuild complete, restarting")
            print("Update applied — restarting gateway…")
            if self.dashboard_state:
                # Same proven restart path as the manual /api/update pipeline:
                # pushes the 'restarting' step, saves history, closes sessions,
                # drains frames, then os.execve's a fresh gateway in-place.
                # (Replaces a dead importlib.reload tail whose NameError was
                # swallowed — the new code was built but NEVER exec'd.)
                self.dashboard_state.push_update_progress("restarting", "Restarting server…")
                from personalclaw.dashboard.handlers.updates import _graceful_reexec

                await _graceful_reexec(self.dashboard_state)
                return
            # Headless (no dashboard state): close sessions and re-exec directly.
            if self.sessions:
                await self.sessions.close_all()
            # Use -m personalclaw instead of sys.argv[0] because build artifacts
            # clean may have deleted the original __main__.py path.
            os.execv(sys.executable, [sys.executable, "-m", "personalclaw"] + sys.argv[1:])
        except Exception:
            logger.warning("Auto-update failed", exc_info=True)

    async def _start_channel_inbound(self) -> None:
        """Drive every registered channel transport's inbound receiver.

        The gateway satisfies :class:`~personalclaw.gateway_services.GatewayServices`,
        so it passes itself as the services handle. A transport that owns a push
        receiver (Slack Socket-Mode, in the slack-channel app) connects here; the
        Web UI transport is a no-op. Failures are isolated per-transport — a
        channel that can't start never takes down the gateway."""
        from personalclaw.channel_transports import get_transport, list_transports

        for tname in list_transports():
            transport = get_transport(tname)
            if transport is None:
                continue
            try:
                await transport.start_inbound(self)
            except Exception:
                logger.warning("Channel transport %r start_inbound failed", tname, exc_info=True)

    # ------------------------------------------------------------------
    # Main run loop
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """Start all services and block until shutdown signal."""
        # Raise FD limit — each ACP agent session uses ~6 FDs (3 pipes)
        # plus MCP server subprocesses. Default macOS limit (256) is too low.
        import resource

        try:
            soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
            target = min(hard, 10240)
            if soft < target:
                resource.setrlimit(resource.RLIMIT_NOFILE, (target, hard))
                logger.info("Raised FD limit: %d → %d", soft, target)
        except Exception:
            pass

        # Clean up orphaned ACP agent processes from previous runs
        from personalclaw.session import cleanup_orphaned_sessions

        cleanup_orphaned_sessions()

        # ── Initialise all services ──
        self._init_services()

        # Wire embedding function from the Settings > Models active embedding
        # selection. When no embedding model is bound, semantic embeddings
        # stay off until the user picks one.
        from personalclaw.embedding_providers.registry import get_active_embed_fn

        embed_fn = get_active_embed_fn()
        if embed_fn and getattr(self, "vector_memory", None) is not None:
            self.vector_memory.embed_fn = embed_fn

        await self._init_cron()
        await self._init_heartbeat()
        try:
            await self._init_inbox()
            logger.info("Inbox service initialized successfully")
        except Exception:
            logger.exception("Inbox init failed")
        self._init_mcp_discovery()
        self._init_subagents()
        if not self._no_dashboard:
            await self._init_dashboard()
        else:
            await self._init_api_server()

        # Emit machine-readable READY line for test harnesses (--json-ready).
        # Printed BEFORE bg_session and other startup chatter so the harness
        # can read it deterministically with a single readline() in the
        # PERSONALCLAW_READY: prefix matcher.
        if self._json_ready:
            ready_token = generate_token("local-startup", ttl_seconds=MAX_SESSION_TTL_SECS)
            ready_payload = {
                "port": self._dashboard_port,
                "token": ready_token,
                "pid": os.getpid(),
                "home": os.environ.get("PERSONALCLAW_HOME", str(Path.home() / ".personalclaw")),
            }
            print(f"PERSONALCLAW_READY:{json.dumps(ready_payload)}", flush=True)

        # AutoNudge must run after dashboard init — _fire callback dereferences
        # self.dashboard_state. In --no-dashboard mode the guard inside _fire
        # early-returns so persisted loops are harmless until a dashboard
        # process takes over.
        await self._init_autonudge()

        # Start inbound receivers for every registered channel transport (Slack
        # Socket-Mode lives in the slack-channel app now). Each transport connects
        # + degrades gracefully internally; a channel failure never crashes the
        # gateway. The Web UI transport is a no-op here (dashboard drives its own
        # inbound). This is the core→channel seam — core imports no vendor code.
        await self._start_channel_inbound()

        # Check for updates before printing URLs
        print("Checking for updates…")
        await self._check_for_updates()

        # ── Signal handlers ──
        loop = asyncio.get_running_loop()
        _shutting_down = False

        def _on_signal(*_args: object) -> None:
            nonlocal _shutting_down
            if _shutting_down:
                print("\nForce exit!")
                cleanup_orphaned_sessions()
                # Reap app-backend subprocesses even on the force-exit path —
                # os._exit() skips the graceful _shutdown()/on_cleanup hooks, so
                # without this a double-signal would orphan every app backend
                # (reparented to init), the exact leak that piled up dozens.
                try:
                    from personalclaw.apps.backend_runtime import get_backend_supervisor

                    get_backend_supervisor().stop_all()
                except Exception:
                    pass
                os._exit(0)
            _shutting_down = True
            shutdown_event.set()

        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, _on_signal)

        # Wait for MCP probe to finish before warming sessions —
        # ACP agent reads MCP config at spawn time, so sessions must
        # start AFTER the probe has synced all servers to mcp.json.
        from personalclaw.dashboard.handlers import _bg_mcp_probe

        print("Probing MCP servers…")
        try:
            from personalclaw.config.loader import AppConfig as _Cfg

            _probe_t = _Cfg.load().dashboard.mcp_probe_timeout_secs + 15
        except Exception:
            _probe_t = 30  # fallback: original default (15 + 15)
        try:
            await asyncio.wait_for(_bg_mcp_probe(), timeout=_probe_t)
        except asyncio.TimeoutError:
            print("MCP probe timed out — continuing without full probe")

        # ── Start background session and print URLs ──
        # Report every connected external channel transport (the in-app webui
        # one is always present and not news) — no hardcoded transport name.
        from personalclaw.channel_transports import get_transport as _get_transport
        from personalclaw.channel_transports import list_transports as _list_transports

        _connected_channels = [
            _tp.display_name
            for _tp in (_get_transport(_n) for _n in _list_transports())
            if _tp and _tp.name != "webui" and _tp.connected
        ]

        async def _start_bg_session() -> None:
            try:
                assert self.sessions is not None
                await self.sessions.start_pool(blocking=False)
                logger.info("Background session starting")
            except Exception:
                logger.warning("Background session start failed", exc_info=True)

            if not self._no_dashboard:
                host = resolve_dashboard_host(self._local_only, self._configured_host)
                base_url = f"http://{host}:{self._dashboard_port}"
                startup_token = generate_token("local-startup", ttl_seconds=MAX_SESSION_TTL_SECS)
                dashboard_url = build_dashboard_url(
                    base_url, startup_token, local_only=self._local_only
                )
                for line in format_dashboard_urls(
                    dashboard_url,
                    port=self._dashboard_port,
                    local_only=self._local_only,
                    has_custom_host=bool(self._configured_host),
                ):
                    print(line)

                # Auto-open dashboard — skip on headless remote sessions
                _is_ssh = bool(os.environ.get("SSH_CONNECTION") or os.environ.get("SSH_CLIENT"))
                _has_display = bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
                _skip_open = _is_ssh and not _has_display and sys.platform != "darwin"
                if self._no_open or not self._cfg.dashboard.auto_open_browser:
                    pass  # suppressed via --no-open flag or config
                elif _skip_open:
                    print("Headless remote session — skipping browser auto-open")
                else:
                    import webbrowser

                    webbrowser.open(dashboard_url)
            for _ch_name in _connected_channels:
                print(f"PersonalClaw gateway connected to {_ch_name}")

        asyncio.create_task(_start_bg_session())
        print("PersonalClaw gateway starting…")
        print(f"\n{DATA_WARNING}\n")

        # Channel inbound (Slack Socket-Mode) already connected inside
        # _start_channel_inbound() above — the transport owns its own
        # retry/degrade-gracefully loop.

        # Block until shutdown
        await shutdown_event.wait()
        print("Shutting down…")

        try:
            await asyncio.wait_for(self._shutdown(), timeout=10.0)
        except (asyncio.TimeoutError, Exception):
            logger.warning("Graceful shutdown timed out — force exiting")

        print("Goodbye!")
        # Kill any ACP agent processes that survived graceful shutdown
        cleanup_orphaned_sessions()
        os._exit(0)


async def run_gateway(
    cfg: AppConfig,
    *,
    no_dashboard: bool = False,
    no_crons: bool = False,
    no_open: bool = False,
    port_override: str | None = None,
    json_ready: bool = False,
    approval_mode: str | None = None,
) -> None:
    """Start the gateway process (blocks until shutdown).

    Boots all core services (chat, cron, subagents, task runner, dashboard).
    If channel credentials are present the enabled channel app also connects its
    channel; otherwise it runs in **dashboard-only** mode.
    """
    orchestrator = GatewayOrchestrator(
        cfg,
        no_dashboard=no_dashboard,
        no_crons=no_crons,
        no_open=no_open,
        port_override=port_override,
        json_ready=json_ready,
        approval_mode=approval_mode,
    )
    await orchestrator.run()

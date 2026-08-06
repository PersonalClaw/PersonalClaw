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

from personalclaw import notification_kinds, shutdown_event
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
from personalclaw.dashboard.token_auth import (
    DEFAULT_BROWSER_SESSION_TTL_SECS,
    generate_token,
)
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
from personalclaw.schedule_history import ScheduleRunStore
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
    from personalclaw.workflows.watchdog import WorkflowWatchdog

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
        self._file_watch_task: "asyncio.Task[None] | None" = None  # S93 file-watch poll loop
        self._web_watch_task: "asyncio.Task[None] | None" = None  # S121 web_watch poll loop
        self._clock_task: "asyncio.Task[None] | None" = None  # S100 unified clock loop
        self._reaper_task: "asyncio.Task[None] | None" = None  # S106 trigger reaper
        self._running_script_ids: set[str] = set()  # zero-token jobs in flight
        self.heartbeat_svc: HeartbeatService | None = None
        self.loop_watchdog: "LoopWatchdog | None" = None
        self.workflow_watchdog: "WorkflowWatchdog | None" = None
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
            # to be the correct session for subagents, but explicit session param
            # is the primary path.  # noqa: E501
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

        # Same installer resolution as the app installer and self-updater: a uv
        # venv has no pip module, and startup dep-repair silently failing there
        # left the gateway running without deps it had just decided it needed.
        from personalclaw._installer import NoInstallerError, install_argv

        try:
            argv = install_argv(["--quiet", *missing])
        except NoInstallerError as exc:
            print(f"❌ {exc}")
            logger.error("Dep repair impossible: %s", exc)
            return

        result = _sp.run(
            argv,
            cwd=proj,
            capture_output=True,
            timeout=300,
        )
        if result.returncode == 0:
            # Invalidate import caches so the new packages are found
            importlib.invalidate_caches()
            print("✅ Dependencies installed")
        else:
            print("❌ Dependency install failed — run manually: personalclaw update")
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

        # Move any pre-v2 workflow SOPs aside (WORKFLOWS-V2 Phase 1). Idempotent and
        # non-destructive: the user's own writing is preserved under
        # `workflows/_legacy_sops/`, out of the way of the v2 def store that lands in
        # the same parent. A no-op on every home that has none.
        try:
            from personalclaw.workflows.legacy import archive_legacy_sops

            archive_legacy_sops(config_dir() / "workflows")
        except Exception:
            logger.debug("Legacy SOP archival skipped", exc_info=True)

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
        # graph_enabled is deliberately NOT pinned here — the store reads
        # `memory.graph_enabled` live so the Settings toggle works without a restart.
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

    # 🔴 `_run_action_job` + `_maybe_autopause` retired with `ScheduleService` (S112). Both took
    # a `ScheduleJob` and were reachable only from the deleted `_cron_callback` dispatcher. The
    # substrate GENERALIZED both: action dispatch is `_fire_store_trigger`, and the autopause
    # counter is `triggers/autopause.py`, which fixed the defect this pair carried (one counter
    # incremented at four call sites with no way to tell a policy block from a real failure).

    def _day_budget_exceeded(self, *, context: str) -> bool:
        """True when the day-scope guardrail spend ceiling is already hit.

        Used as a pre-dispatch gate for unattended LLM work (cron agent fires).
        On the transition into exceeded, emits ONE needs-input notification so the
        user learns their automation is paused for the day without a per-fire spam.
        Fail-open (returns False) on any error — a broken budget read must never
        wedge unattended work; the meter + breaker remain the hard controls.
        """
        try:
            from personalclaw.guardrails.budgets import (
                BudgetVerdict,
                budget_from_config,
                get_meter,
            )

            budget = budget_from_config()
            if budget.is_unlimited:
                return False
            verdict, reason = get_meter().check_day(budget)
            if verdict is not BudgetVerdict.EXCEEDED:
                # Re-arm the one-shot notification: once the day rolls over (or the
                # user raises the budget) and we're back under the ceiling, the next
                # exceeded window notifies again.
                self._budget_notified = False
                return False
            # One-shot notification per exceeded window (de-duped by the flag).
            if not getattr(self, "_budget_notified", False):
                self._budget_notified = True
                if self.dashboard_state is not None:
                    try:
                        self.dashboard_state.notify(
                            notification_kinds.WARNING,
                            "Daily automation budget reached",
                            f"{context} was skipped — {reason}. Unattended runs resume "
                            f"tomorrow, or raise the budget in Settings → Guardrails.",
                        )
                    except Exception:
                        logger.debug("budget notify failed", exc_info=True)
            logger.info("%s skipped: %s", context, reason)
            return True
        except Exception:
            logger.debug("day-budget check failed (fail-open)", exc_info=True)
            return False

    async def _clock_loop(self) -> None:
        """Drive the unified clock: tick → dispatch → execute (§3 — S100).

        The sole engine that fires clock triggers now. `triggers/loop.run_forever` owns the cadence
        and the resilience (one bad tick never kills the loop); this method only supplies the two
        things the gateway knows: the store's home and the runner.

        The runner is the SAME action-provider dispatch a file-watch fire uses, so a clock
        fire and a
        file fire execute the same action the same way — one dispatch path rather than two
        that drift.
        """
        from personalclaw.config.loader import config_dir
        from personalclaw.triggers import loop as clock_loop
        from personalclaw.triggers.store import TriggerStore

        store = TriggerStore(base_dir=config_dir())

        async def _runner(payload: dict[str, Any]) -> Any:
            trigger_id = str(payload.get("trigger_id") or "")
            row = store.get(trigger_id)
            if row is None:
                return {"status": "error"}
            await self._fire_store_trigger(row.trigger, payload)
            return {"status": "launched"}

        await clock_loop.run_forever(
            store,
            runner=_runner,
            sessions=self.sessions,
            base_dir=store.base_dir,
        )

    def _push_trigger_refresh(self) -> None:
        """Hint open dashboard views to refresh after a store-backed fire (S107).

        Both kinds, matching what the legacy `_record_run` pushed plus the list the fire may have
        changed: `cron_history` for the run feed, `crons` for the trigger list's status dots and
        next-fire times. Best-effort — a broadcast failure must never affect the fire's outcome, and
        a dashboard-less gateway (`--no-dashboard`) simply has nothing to notify.
        """
        # `getattr`, not attribute access: this runs in the fire path's `finally`, and an
        # orchestrator that has not reached `_init_dashboard` yet (or a partially-built one) has
        # no `dashboard_state` attribute at all. An AttributeError from a `finally` would REPLACE
        # the fire's own outcome — a refresh hint must never be able to do that.
        state = getattr(self, "dashboard_state", None)
        if state is None:
            return
        try:
            state.push_refresh("crons", "cron_history")
        except Exception:  # noqa: BLE001 - a refresh hint is never worth failing a fire over
            logger.debug("could not push a trigger refresh", exc_info=True)

    async def _trigger_reaper_loop(self) -> None:
        """Bound every store-backed run: sweep for blown deadlines and free the claim (§3.1 — S106).

        Replaces `ScheduleService.start_reaper`, whose sweep read a dict that only the retired
        legacy timer ever wrote — inert since the S100 cutover, and silently so. Like `_clock_loop`,
        this method supplies only the two things the gateway knows (the store and its home) and
        leaves the cadence and resilience to the module.
        """
        from personalclaw.config.loader import config_dir
        from personalclaw.triggers import reaper
        from personalclaw.triggers.store import TriggerStore

        store = TriggerStore(base_dir=config_dir())
        await reaper.run_forever(store=store, base_dir=store.base_dir)

    async def _fire_store_trigger(
        self, trigger: Any, payload: dict[str, Any], *, event: str = "trigger.fired"
    ) -> None:
        """Run one store-backed trigger's declared action through the action-provider registry.

        Shared by the clock loop and the file-watch loop, so every store-backed fire goes
        through one
        dispatch. A failed action is logged rather than raised: the outcome belongs to the
        executor's
        typed classification, and a raise here would strand the rest of the drain.
        """
        from personalclaw.action_providers import ActionContext, get_action_provider
        from personalclaw.action_providers.registry import _ensure_default_providers_registered
        from personalclaw.triggers import secrets as _trigger_secrets

        workflow = trigger.workflow or {}
        inline = workflow.get("inline") if isinstance(workflow.get("inline"), dict) else None
        provider_name = str((inline or workflow).get("provider") or "")
        config = (inline or workflow).get("config") or {}
        if not provider_name:
            logger.debug("trigger %s has no action provider", trigger.id)
            return
        _ensure_default_providers_registered()
        provider = get_action_provider(provider_name)
        if provider is None:
            logger.warning("trigger %s: unknown action provider %r", trigger.id, provider_name)
            return

        # 🔴 THE INJECTION SCREEN, on the payload that actually carries untrusted text (§7/R4 rule a
        # — S134). Measured: `FireContext.payload_text` defaulted to "" and `service.tick` never set
        # it, so `evaluate`'s `if ctx.payload_text:` was permanently false — the
        # screen had NEVER run
        # on a real fire, while every ledger row listed `screen` among the gates
        # PASSED. And the kinds
        # that DO carry third-party prose (web_watch items, file changes) never reach that walk at
        # all: they are dispatched straight here.
        #
        # Screened HERE rather than by threading a payload back into `tick`, because this is the one
        # place every polled payload passes through on its way to a provider — the same reasoning
        # S122 used for chaining. A blocked payload is NEVER auto-retried (`blocked_injection` is
        # terminal by design), which is also why `payload_text_for` reads an allowlist of prose-
        # carrying keys instead of screening ids and URLs that would produce false blocks.
        from personalclaw.triggers import screen as screen_mod
        from personalclaw.triggers.screen import payload_text_for
        from personalclaw.triggers.screen import screen as screen_text

        untrusted = payload_text_for(payload, kind=str(getattr(trigger, "kind", "") or ""))
        if untrusted:
            verdict = screen_text(untrusted)
            if getattr(verdict, "verdict", "") == "blocked":
                groups = ", ".join(getattr(verdict, "groups", ()) or ()) or "injection"
                logger.warning(
                    "trigger %s: payload blocked by the injection screen (%s); not retried",
                    trigger.id,
                    groups,
                )
                # 🔴 A TYPED LEDGER ROW, not just a log line (§7 crit 8 — S136). S134 wired the
                # screen here and recorded the row as still owed: this path is not a `tick` fire,
                # so nothing wrote one. A refusal only a log knows about is a silent drop by
                # criterion 8's own definition — the user sees an automation that stopped, with the
                # reason in a file they will not read. And `blocked_injection` NEVER auto-retries,
                # so this row is the only record that will ever exist for this fire.
                await self._record_blocked_fire(trigger, groups)
                self._push_trigger_refresh()
                return
            # 🔴 FENCE-AND-PROCEED, which nothing actually did (§7/R4 rule c — S157).
            # `Verdict.SUSPICIOUS` exists precisely so a payload can be fenced and still run —
            # `screen_to_outcome` maps it to `ran` on the stated grounds that "the payload is FENCED
            # and the run proceeds". Measured: only `web_watch` fenced (at origin, S127), so a
            # `persona_hijack` payload from webhook/event/file reached the provider VERBATIM.
            #
            # Fenced for CLEAN too, not only suspicious: the screen is a pattern matcher and its
            # clean verdict means "no known pattern", not "trustworthy". This text still crossed the
            # trust boundary, and every other ingestion seam in the codebase fences it
            # unconditionally (`web/fetch`, `inbox_service`, `event_triggers`, `bindings`). Fencing
            # only what a matcher flagged would make the guarantee depend on the corpus being
            # complete, which is the one thing a pattern corpus never is.
            payload = screen_mod.fence_payload(
                payload, kind=str(getattr(trigger, "kind", "") or ""), trigger_id=trigger.id
            )
        # 🔴 RESOLVE `{{secret:KEY}}` HERE, at dispatch (§7 item 6 / decision 11 — S115). Workflows
        # have carried this form since WF2-R14 and three surfaces tell the author to use it, but a
        # TRIGGER action passed the literal placeholder to the provider — measured: a bash command
        # `echo tok={{secret:MY_KEY}}` printed `tok={{secret:MY_KEY}}`. So the only way to make a
        # trigger authenticate was to paste the credential into `triggers.json`, a file that is
        # snapshotted (S113), echoed into run records, and rendered in the UI.
        #
        # At DISPATCH, never at save: the stored config keeps the placeholder, so the secret is not
        # on disk. An unresolved key REFUSES rather than substituting "" — an empty Authorization
        # header produces a remote 401 nobody can trace back to a missing credential.
        try:
            config = _trigger_secrets.resolve(config)
        except _trigger_secrets.UnresolvedSecret as exc:
            logger.warning("trigger %s: %s", trigger.id, exc)
            self._push_trigger_refresh()
            return

        try:
            ctx = ActionContext(event=event, context="", payload=payload)
            # 🔴 The MODE DEFAULT the legacy dispatcher applied (gateway.py:820 — 300s for a command,
            # 30s otherwise), because a `bash` fire is a real subprocess and 30s is not a command's
            # budget. Measured: this call passed nothing, so every store-backed bash fire took the
            # 30s SIGNATURE default and a migrated `zt_timeout: 600` cron was cut to 30. The
            # per-action override lives in the config and is honoured by the provider itself (both
            # `bash` and `run-script` prefer `action_config["timeout"]`), so this is only the floor.
            timeout = 300 if provider_name == "bash" else 30
            # 🔴 THE RESULT WAS DISCARDED (§3.7 / crit 3 — S139). `await provider.execute(...)` threw
            # its return value away, so nothing on this path knew if a fire SUCCEEDED. Measured:
            # six consecutive failing provider runs left `health_status: 'ok'` with an empty
            # `last_failure_at` and `enabled: True` — criterion 3's "autopause after 5" could not
            # possibly hold, because the whole `autopause` module (13 functions) was imported by NO
            # production code and the counter it spends had no writer.
            # 🔴 BIND the per-fire run scope so model spend is ATTRIBUTABLE (S153).
            # `SpendMeter.charge` has accepted `run_key=` since guardrails landed and its only
            # production caller never passed one, so `run_totals` was permanently empty — which is
            # why `cost_cap`/`max_cost_usd_per_run` sat in `UNMETERED_CAPS` for twenty sessions. A
            # ContextVar rather than a parameter: the guard is built by `provider_bridge` from
            # provider config and has no run identity, and threading one in would touch all 33 call
            # sites that reach the bridge.
            #
            # Keyed per FIRE, not per trigger: `max_cost_usd_per_run` is a per-run cap, and a
            # trigger-scoped key would accumulate across fires and make the second fire of a
            # healthy automation look over budget. Reset in a `finally` so a raising
            # provider cannot leak the scope into the next fire on this task.
            #
            # 🔴 S154 completes it: binding the KEY made spend attributable, and binding the
            # CEILING beside it makes `max_cost_usd_per_run` enforceable. Both are ambient for
            # the same reason — the guard is built from provider config and never sees the
            # trigger. `run_budget_for` reads only `max_cost_usd_per_run`; `cost_cap` is a
            # per-window promise with no durable per-window store, so it stays unmetered
            # rather than being silently re-defined as per-run.
            from personalclaw.guardrails.budgets import (
                get_meter,
                reset_current_run_budget,
                reset_current_run_key,
                set_current_run_budget,
                set_current_run_key,
            )
            from personalclaw.triggers.calendar import run_budget_for

            run_key = f"trigger:{trigger.id}:{int(time.time() * 1000)}"
            run_token = set_current_run_key(run_key)
            # `getattr` rather than `trigger.gates`, matching this path's house style (`kind`,
            # `id`, `delivery` are all read the same way): the fire path is driven with partial
            # trigger shapes, and a ceiling lookup must never be what turns a fire into an error.
            budget_token = set_current_run_budget(run_budget_for(getattr(trigger, "gates", None)))
            try:
                result = await provider.execute(config, ctx, timeout=timeout)
            finally:
                reset_current_run_budget(budget_token)
                reset_current_run_key(run_token)
                # 🔴 DROP the per-fire counter. `SpendMeter.end_run` shipped with the module and
                # had NO caller, and S153's per-FIRE keying turned that into a real leak:
                # measured 5000 distinct keys retained after 5000 fires, held for the life of a
                # gateway process that is meant to run for months. The cap is enforced DURING
                # the run, so the total has no reader once the fire is over.
                try:
                    get_meter().end_run(run_key)
                except Exception:  # noqa: BLE001 - bookkeeping must not mask a fire's outcome
                    logger.debug("end_run failed for %s", run_key, exc_info=True)
            await self._record_fire_outcome(trigger, result=result)
            self._deliver_fire_outcome(trigger, ok=bool(getattr(result, "success", True)))
        except Exception as exc:  # noqa: BLE001 - a failed fire is logged, never crashes the loop
            logger.warning("trigger %s: action failed", trigger.id, exc_info=True)
            await self._record_fire_outcome(trigger, exc=exc)
            self._deliver_fire_outcome(trigger, ok=False, error=f"{type(exc).__name__}: {exc}")
        finally:
            # 🔴 THE LIVE REFRESH (S107). `ScheduleService._record_run` pushed `cron_history` so
            # the Executions/Logs views update without polling — and `_record_run` is reachable only
            # from `run_job` (manual) and `_run_job_isolated` (the retired timer). So since the
            # cutover a SCHEDULED fire updated no open view: the user watched a stale page until
            # navigating. In a `finally` because a FAILED fire is the one someone is watching for.
            self._push_trigger_refresh()
            # 🔴 THE CHAIN (S122). `run_completed` was a declared kind with NO firing path: measured,
            # a `run_completed` trigger pointed at a real clock trigger was reached by nothing — not
            # the tick, not either poller. So "when my nightly backup finishes, notify me" was
            # creatable, listed in the UI, and permanently silent.
            #
            # Chained HERE because this is the single point every store-backed run completes, so a
            # chain inherits the same dispatch — and therefore the same gates, including the kill
            # switch and the capability fence. A chain with its own dispatch path would be a second
            # place for those controls to be forgotten, which is exactly how the `web_watch` gap
            # happened. After the refresh, so a slow chain never delays the view update.
            await self._fire_chained_triggers(trigger, payload)

    def _surface_missed_review(self, report: dict[str, Any]) -> None:
        """Put the boot's missed-fire review in front of the user (§3.4 / crit 7 — S142).

        Criterion 7 says "missed slots appear in the review card". §3.4's rule is REVIEW, don't lie
        and don't storm: a boot that silently caught everything up is the storm, and one that says
        nothing is the lie. So the review becomes ONE notification naming the count, not one per
        missed slot — a laptop opened after a weekend would otherwise deliver hundreds.

        Silent when nothing was missed, deliberately: "0 automations missed a run" on every restart
        trains the user to dismiss the notification that matters. Goes through `state.notify` like
        every other substrate notification (R18 — no second path), so a muted channel stays muted.
        Never raises: the sweep already re-armed the schedule, and failing to announce it must not
        undo that.
        """
        try:
            state = getattr(self, "dashboard_state", None)
            if state is None:
                return
            review = report.get("review") or {}
            rows = review.get("rows") or []
            summaries = review.get("summaries") or []
            total = len(rows) + sum(int(s.get("count", 0) or 0) for s in summaries)
            if total <= 0:
                return
            affected = len(
                {str(r.get("trigger_id", "")) for r in rows}
                | {str(s.get("trigger_id", "")) for s in summaries}
            )
            caught_up = [c for c in (report.get("catch_up") or []) if c.get("catching_up")]
            body = (
                f"{total} scheduled run{'s' if total != 1 else ''} were missed across "
                f"{affected} automation{'s' if affected != 1 else ''} while PersonalClaw was not "
                "running. Review them and choose what to run now."
            )
            if caught_up:
                body += (
                    f" {len(caught_up)} with catch-up enabled will fire once, staggered, "
                    "on their own."
                )
            state.notify(
                kind="info",
                title="Missed scheduled runs",
                body=body,
                meta={
                    "event": "automation.missed_review",
                    "statusUrl": "#/triggers",
                    "missed": total,
                    "triggers": affected,
                    "caught_up": len(caught_up),
                    "truncated": bool(review.get("truncated")),
                },
            )
        except Exception:  # noqa: BLE001 - see the docstring
            logger.debug("could not surface the missed-fire review", exc_info=True)

    def _surface_attention_card(self, trigger: Any, decision: Any) -> None:
        """Put an autopaused/quarantined trigger in front of the user (crit 3 — S141).

        🔴 `attention_card` returns None for a still-firing or parked trigger, which is why the
        control flow here is "if card: send it" — the module deliberately makes it impossible to
        write a card that says nothing.

        Deduped on the card's own FINGERPRINT, not the delivery event id: a fingerprint is
        `(trigger_id, state)`, so re-entering the same paused state does not re-alert, while a
        trigger that goes autopaused → resumed → autopaused legitimately alerts twice.
        `is_duplicate_card` owns that comparison; the seen-set lives here as the delivery one does.

        Goes through `state.notify` like every other substrate notification (R18: no second path),
        so a muted channel stays muted. Never raises — the pause already happened, and failing to
        announce it must not undo it.
        """
        try:
            from personalclaw.triggers import autopause

            state = getattr(self, "dashboard_state", None)
            if state is None:
                return
            card = autopause.attention_card(
                trigger_id=str(getattr(trigger, "id", "") or ""),
                trigger_name=str(getattr(trigger, "name", "") or ""),
                decision=decision,
                last_error=str(getattr(trigger, "last_error_summary", "") or ""),
            )
            if card is None:
                return
            if not hasattr(self, "_attention_fingerprints"):
                self._attention_fingerprints: set[str] = set()
            if autopause.is_duplicate_card(card.fingerprint, self._attention_fingerprints):
                return
            state.notify(
                kind="warning",
                title=card.title,
                body=card.body,
                meta={
                    "event": "automation.needs_attention",
                    "statusUrl": f"#/triggers?open={card.trigger_id}",
                    "trigger_id": card.trigger_id,
                    "state": card.state,
                    "actions": list(card.actions),
                },
            )
            self._attention_fingerprints.add(card.fingerprint)
        except Exception:  # noqa: BLE001 - see the docstring
            logger.debug("could not surface the attention card for %s", trigger, exc_info=True)

    def _next_delivery_attempt(self) -> str:
        """A monotonically increasing per-fire key for `event_id` (S161).

        Each FIRE is a distinct event, so its delivery needs a distinct id — otherwise
        `is_duplicate` reads the second fire of a healthy automation as a redelivery of the first
        and drops it. A counter rather than a timestamp because a millisecond stamp collides for
        fires in the same tick: measured, 5 rapid `int(time.time() * 1000)` reads returned ONE
        distinct value, so 5 fires still produced only 2 notifications.

        Process-local, and that is sufficient: `is_duplicate`'s seen-set is process-local too
        (`_delivered_event_ids`), so the id only has to be unique against ids this process has
        already delivered. A restart clears both together.
        """
        n = int(getattr(self, "_delivery_attempt_seq", 0)) + 1
        self._delivery_attempt_seq = n
        return f"a{n}"

    def _dedupe_repeat_failure(self, trigger: Any, *, error: str) -> bool:
        """True when this failure repeats the last alerted one inside the reminder window (S161).

        Persists the hash + timestamp on the trigger either way, so a NEW error resets the window
        rather than inheriting the previous one's remaining time.

        Gated on `failure_policy.dedupe_hash` because that is what §1.1 declares. Coalescing alerts
        for a user who did not ask for it would be the opposite failure — a broken automation going
        quieter than they expect.

        **The autopause counter is untouched.** The legacy control advanced `consecutive_failures`
        while suppressing the notification, and that separation is the point: dedup is about how
        loudly the user is told, never about whether the failure counted. Coupling them would let a
        repeating error escape autopause entirely — the worst possible reading.

        Never raises: a bookkeeping failure must not swallow a real alert, so any error falls
        through to delivering (fail-LOUD, the safe direction for a notification).
        """
        try:
            from personalclaw.config.loader import config_dir
            from personalclaw.triggers import delivery as _delivery
            from personalclaw.triggers.store import TriggerStore

            policy = getattr(trigger, "failure_policy", None)
            if not isinstance(policy, dict) or not policy.get("dedupe_hash"):
                return False
            trigger_id = str(getattr(trigger, "id", "") or "")
            if not trigger_id:
                return False
            # 🔴 READ THE DEDUP STATE FROM THE STORE, not from the passed-in trigger. Caught by
            # driving it: the fire path hands `_deliver_fire_outcome` the in-memory row the TICK
            # built, and this method writes the hash back to disk — so the object the next fire
            # arrives with is stale, its `last_alert_hash` still empty, and nothing ever matched.
            # A dedup control whose state the reader cannot see is the inert shape again, one layer
            # in. `_record_fire_outcome` re-reads the store for exactly this reason.
            store = TriggerStore(base_dir=config_dir())
            row = store.get(trigger_id)
            live = row.trigger if row is not None else trigger
            suppress, digest = _delivery.suppress_repeat_failure(
                error=error,
                last_hash=str(getattr(live, "last_alert_hash", "") or ""),
                last_at=float(getattr(live, "last_alert_at", 0.0) or 0.0),
                now=time.time(),
            )
            if not digest:
                return False
            if not suppress:
                if row is not None:
                    live.last_alert_hash = digest
                    live.last_alert_at = time.time()
                    store.upsert(live)
                return False
            logger.info(
                "trigger %s: duplicate failure suppressed (same error within the reminder window)",
                trigger_id,
            )
            return True
        except Exception:  # noqa: BLE001 - see the docstring: fall through to delivering
            logger.debug("failure dedup check failed for %s", trigger, exc_info=True)
            return False

    def _deliver_fire_outcome(self, trigger: Any, *, ok: bool, error: str = "") -> None:
        """Notify the user about a completed fire, with a deep link (§R18 / crit 10 — S140).

        🔴 WHY THIS EXISTS. `triggers/delivery.py` implements criterion 10 in full — `statusUrl`
        deep links, stable event ids for retry dedup, `is_duplicate`, destination formatting — but
        `build_delivery` had no caller outside `executor.delivery_for`, which itself had none.
        Driven first: a completed fire produced no notification and no `statusUrl` anywhere under
        the home. Two dead layers, the same shape as S139's autopause chain.

        Routes through `state.notify`, which is `deliver`'s own contract: R18 says "the substrate
        does not build a second notification path", so the existing `notification_allowed` gate and
        the per-(source, kind) rule both still apply. A muted channel stays muted.

        The dedup set lives on the orchestrator, which is the honest scope: the retry window is a
        transport concern, and an in-memory set is right for one gateway process — a persisted one
        would claim a durability this path does not have. `event_id` is stable across
        retries by construction, so a redelivery inside the process is suppressed.

        Never raises. A notification failure must not fail the run that already completed.
        """
        try:
            from personalclaw.triggers import delivery as _delivery

            state = getattr(self, "dashboard_state", None)
            if state is None:
                return
            if not hasattr(self, "_delivered_event_ids"):
                self._delivered_event_ids: set[str] = set()
            # 🔴 SUPPRESS A REPEATED IDENTICAL FAILURE (R7's `dedupe_hash` — S161). The legacy
            # scheduler had this control; the unified path kept its constant and helper and dropped
            # the check. Measured: the same error on 6 consecutive fires produced 6 notifications,
            # because `event_id` dedupes the same event REDELIVERED (same run_id), not different
            # fires carrying an identical error.
            #
            # Opt-in via `failure_policy.dedupe_hash`, matching the declared schema — a user who did
            # not ask for coalescing keeps every alert. Capped by a 1h window, so a still-broken
            # automation re-alerts: "it stopped telling me" and "it got fixed" must not look alike.
            if not ok and self._dedupe_repeat_failure(trigger, error=error):
                return
            note = _delivery.build_delivery(
                trigger_id=str(getattr(trigger, "id", "") or ""),
                trigger_name=str(getattr(trigger, "name", "") or ""),
                ok=ok,
                summary=error[:200],
                # 🔴 EACH FIRE IS A NEW EVENT (R18 / crit 10 — S161). This passed neither `run_id`
                # nor `attempt_key`, so `event_id` — derived from exactly those three parts —
                # produced the SAME id for every fire of a trigger, and `is_duplicate` then dropped
                # every notification after the first. Measured: a healthy daily digest with
                # `delivery: "inbox"` notified the user ONCE, EVER; fires 2-5 were silently
                # discarded as "already sent".
                #
                # `event_id`'s own docstring names the fix: "`attempt_key` is for the case where a
                # re-run genuinely IS a new event … Callers pass the run's epoch". Criterion 10's
                # dedup is for the SAME event REDELIVERED (a transport retry), and applying it to
                # distinct fires inverted it into a mute.
                #
                # A COUNTER, not the clock: my first fix used `int(time.time() * 1000)` and
                # measured 5 fires producing only 2 notifications, because a millisecond stamp
                # collides for anything firing in the same tick (5 rapid reads returned one
                # distinct value). The counter is monotonic whatever the clock's resolution.
                attempt_key=self._next_delivery_attempt(),
                # 🔴 The OUTCOME picks the route (R12 / decision 13 — S158). This read
                # `trigger.delivery` unconditionally, so `failure_delivery` — declared, persisted,
                # round-tripped and editable — was never consulted, and a `delivery: "none"`
                # automation that BROKE reported through the silent channel. Its own comment names
                # the contract: "failures reach the inbox even when `delivery` is none".
                destination=_delivery.route_for(trigger, ok=ok),
            )
            _delivery.deliver(state, note, delivered_ids=self._delivered_event_ids)
        except Exception:  # noqa: BLE001 - see the docstring
            logger.debug("could not deliver the fire outcome for %s", trigger, exc_info=True)

    async def _record_fire_outcome(
        self, trigger: Any, *, result: Any = None, exc: BaseException | None = None
    ) -> None:
        """Record a fire's outcome and autopause a failing trigger (§3.7 / crit 3 — S139).

        🔴 WHY THIS EXISTS. `triggers/autopause.py` ships 13 functions implementing criterion 3 —
        typed exits, a 5-failure budget, parking for transport outages, immediate pause for config
        errors, the attention card — and **not one production module imported it**. Driven before
        writing: six failing provider runs left the trigger `enabled`, `health_status: 'ok'`, and
        an empty `last_failure_at`. The decision engine was complete and unreachable.

        The counter is DERIVED from the run ledger, not stored on the row, because
        `LEGACY_FIELD_MAP` says exactly that: *"autopause counter is derived from fire records"*. A
        copy on the trigger would be a second truth that can disagree with the ledger it summarises.

        Never raises. A bookkeeping failure must not turn a completed fire into a crashed one — the
        outcome already happened, and losing the record is strictly better than losing the loop.
        """
        try:
            from personalclaw.config.loader import config_dir
            from personalclaw.schedule_history import ScheduleRun
            from personalclaw.triggers import autopause
            from personalclaw.triggers.models import TriggerState
            from personalclaw.triggers.store import TriggerStore

            trigger_id = str(getattr(trigger, "id", "") or "")
            if not trigger_id:
                return

            if exc is not None:
                # A RAISING provider is classified by exception type: auth → transport → config →
                # failed, so a credential outage PARKS rather than spending the failure budget.
                exit_type = autopause.classify_exception(exc)
            elif result is not None and not bool(getattr(result, "success", True)):
                # A provider that returned `success=False` without raising carries no exception to
                # classify, so it reads as a plain FAILED — the fail-safe direction the module's own
                # `classify_exception(None)` takes for an unrecognised error.
                exit_type = autopause.ExitType.FAILED.value
            else:
                exit_type = autopause.ExitType.OK.value

            # 🔴 WRITE THE ROW FIRST, then count. Found by driving: the counter reads the run
            # ledger, and the store-backed fire path wrote NO row per fire — so the count was
            # permanently 0 and a trigger could fail forever. `_record_run` died with
            # `ScheduleService` (S112) and nothing replaced it on this path, which is why parking
            # (stateless, from the exception type) worked while the BUDGET (stateful) did not.
            store_runs = ScheduleRunStore(config_dir())
            now = time.time()
            await store_runs.append(
                ScheduleRun(
                    run_id=f"fire-{int(now * 1000)}",
                    job_id=trigger_id,
                    trigger=exit_type,
                    started_at=now,
                    finished_at=now,
                    status="success" if exit_type == autopause.ExitType.OK.value else "failure",
                    error="" if exc is None else f"{type(exc).__name__}: {exc}"[:200],
                )
            )
            # 🔴 The count must be the streak BEFORE this fire: `evaluate` adds its own unit
            # (`count = consecutive_failures + 1`, then pauses at the threshold). Counting the row
            # just written would double-count and pause after FOUR failures — caught by driving the
            # 4-then-success-then-1 sequence, which paused on the fourth.
            runs, _total = await store_runs.list_for_job(trigger_id, 0, 20)
            prior = max(0, autopause.consecutive_failures_from(runs) - 1)

            decision = autopause.evaluate(
                exit_type=exit_type,
                consecutive_failures=prior,
                now=time.time(),
                # 🔴 The PER-TRIGGER budget (R7 — S160). `evaluate` has always accepted `budget=` and
                # this call never passed one, so `failure_policy.autopause_after` had zero readers:
                # a trigger declaring `{"autopause_after": 2}` ran to the hardcoded 5. A
                # control that silently WIDENS a tolerance its author narrowed, and so is
                # invisible — the trigger
                # keeps running, exactly as a healthy one does.
                budget=autopause.budget_for(trigger),
                quarantined=str(getattr(trigger, "state", "")) == TriggerState.QUARANTINED.value,
            )

            store = TriggerStore(base_dir=config_dir())
            row = store.get(trigger_id)
            if row is None:
                return
            live = row.trigger
            live.health_status = decision.health
            live.state = decision.state
            from datetime import datetime, timezone

            stamp = datetime.now(timezone.utc).isoformat()
            if exit_type == autopause.ExitType.OK.value:
                live.last_success_at = stamp
            else:
                live.last_failure_at = stamp
                # 🔴 THE ERROR, not the lifecycle reason (§3.7 / decision 9 — S162). This stored
                # `decision.reason`, so `last_error_summary` held "failure 3 of 5" — and the
                # attention card, which passes that field into its `last_error` slot, rendered
                # **"paused after 5 consecutive failures. Last error: paused after 5 consecutive
                # failures."** The one field carrying evidence repeated the sentence beside it, so
                # the actual exception never reached the user. `attention_card`'s own docstring
                # says why the slot exists: "'paused after 5 consecutive failures' without the
                # error is an alert the user has to go digging to act on."
                #
                # Falls back to the lifecycle reason only when there is no error text at all (a
                # provider returning `success=False` without raising) — an empty evidence line
                # would be worse than a redundant one.
                detail = f"{type(exc).__name__}: {exc}" if exc is not None else ""
                if not detail and result is not None:
                    detail = str(getattr(result, "error", "") or "")
                live.last_error_summary = (detail or decision.reason)[:200]
            # 🔴 The PAUSE itself, which is the whole point: a state the module classifies as
            # needing attention must stop firing. Leaving `enabled` True while labelling the row
            # "autopaused" would be the inert control this program keeps finding.
            if autopause.needs_attention(decision.state):
                live.enabled = False
                logger.warning(
                    "trigger %s autopaused: %s", trigger_id, decision.reason or decision.state
                )
            # 🔴 PERSIST THE PARK COOLDOWN (§3.7 / decision 9 — S159). `evaluate` has always returned
            # `retry_after=now + PARK_COOLDOWN_SECS` on a parking exit and this path DROPPED it, so
            # `unpark_due` — the clock decision that brings a parked trigger back — had nothing to
            # read and no caller. Measured: one transport outage parked a working trigger,
            # which then fired 0 times over the next 5 slots and stayed `parked`. A 30-second
            # network blip permanently disabled the automation.
            #
            # Cleared on any NON-parking outcome so a recovered trigger does not carry a stale
            # cooldown into its next outage.
            live.park_retry_after = (
                float(decision.retry_after) if decision.state == TriggerState.PARKED.value else 0.0
            )
            store.upsert(live)
            # 🔴 Criterion 3's SECOND clause — "and surfaces in the Runs inbox" (S141).
            # `attention_card`, `inbox_fingerprint` and `is_duplicate_card` were all dead: an
            # autopaused automation stopped silently, and a trigger that stops without saying so is
            # indistinguishable from one that finished. The card is what turns the state change into
            # something the user can act on.
            self._surface_attention_card(live, decision)
        except Exception:  # noqa: BLE001 - see the docstring
            logger.debug("could not record the fire outcome for %s", trigger, exc_info=True)

    async def _record_blocked_fire(self, trigger: Any, groups: str) -> None:
        """Write the `blocked_injection` ledger row for a screened payload (§7 crit 8 — S136).

        ASYNC because `ScheduleRunStore.append` is. mypy caught the sync version as an
        unused coroutine — i.e. the row would never have been written at all, which is a
        neater demonstration of this session's own theme than anything I could contrive.

        Best-effort by construction: a bookkeeping failure must not change the SECURITY decision.
        The payload is refused before this runs, so the worst case is a refusal with no row —
        exactly what S134 shipped and this closes, never a re-opened hole.

        The screened TEXT is deliberately not stored. Criterion 11's discipline generalises: a
        blocked payload is hostile third-party content, and copying it into a store the UI renders
        would move an injection attempt out of a refused fire and into a surface a human reads. The
        matched GROUPS name the pattern class, which is what tells a real attack from a false
        positive.
        """
        try:
            import time as _time

            from personalclaw.config.loader import config_dir
            from personalclaw.schedule_history import ScheduleRun

            now = _time.time()
            await ScheduleRunStore(config_dir()).append(
                ScheduleRun(
                    run_id=f"blocked-{int(now * 1000)}",
                    job_id=str(getattr(trigger, "id", "") or ""),
                    trigger="blocked_injection",
                    started_at=now,
                    finished_at=now,
                    status="blocked_injection",
                    error=f"payload blocked by the injection screen ({groups}); never retried",
                )
            )
        except Exception:  # noqa: BLE001 - bookkeeping must never alter a security decision
            logger.debug("could not record the blocked-fire row for %s", trigger, exc_info=True)

    async def _fire_chained_triggers(self, trigger: Any, payload: dict[str, Any]) -> None:
        """Fire every `run_completed` trigger waiting on the run that just finished (S122).

        Never raises: a chain is a convenience layered on a completed run, and letting it fail the
        run it followed would make chaining strictly worse than not chaining.

        The depth cap and cycle detection live in `chain.next_fires`, which returns refusals as data
        so they are logged rather than dropped — a chain that stopped silently is indistinguishable
        from one that was never configured.
        """
        try:
            from personalclaw.config.loader import config_dir
            from personalclaw.triggers import chain
            from personalclaw.triggers.store import TriggerStore

            workflow = trigger.workflow if isinstance(trigger.workflow, dict) else {}
            fires, refused = chain.next_fires(
                TriggerStore(base_dir=config_dir()),
                source_id=trigger.id,
                source_payload=payload,
                source_def=str(workflow.get("ref", "") or ""),
            )
            for row in refused:
                logger.info("chain %s did not fire: %s", row["trigger_id"], row["reason"])
            for chained, chained_payload in fires:
                await self._fire_store_trigger(chained, chained_payload, event="trigger.chained")
        except Exception:  # noqa: BLE001 - a chain must never fail the run it followed
            logger.warning("chain dispatch failed after %s", trigger.id, exc_info=True)

    async def _file_watch_poll_loop(self) -> None:
        """Poll `file` triggers and fire the ones whose watched paths changed (§3 / crit 2 — S93).

        This is the runtime that makes a chat-created "when a file in ~/notes changes…" automation
        (S92) actually fire. It is DISJOINT from `ScheduleService`: that fires clock crons and reads
        no `file` trigger, and the tick clock (`service.due_ids`) never surfaces a `file` trigger
        (it has no `next_fire_at`). So running this beside the cron loop cannot double-fire anything
        — which is what lets it land as an additive cutover rather than the clock switch-over the
        roadmap still defers.

        Incident mode suspends it, matching `_cron_callback`: an unattended fire is an unattended
        fire regardless of what triggered it. One bad watch never stops the loop for the others
        (`poll_all` isolates each), and the loop never dies on an exception — a poll loop that threw
        once and stopped would silently retire every file automation the user has.
        """
        from personalclaw.config.loader import config_dir
        from personalclaw.triggers import file_poll
        from personalclaw.triggers.store import TriggerStore

        store = TriggerStore(base_dir=config_dir())
        while True:
            try:
                await asyncio.sleep(file_poll.POLL_INTERVAL_SECS)
                from personalclaw.guardrails.incident import incident_active

                if incident_active():
                    continue
                for payload in file_poll.poll_all(store):
                    await self._fire_file_trigger(payload)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - the loop must outlive any single poll's failure
                logger.warning("file-watch poll loop iteration failed", exc_info=True)

    async def _web_watch_poll_loop(self) -> None:
        """Poll every `web_watch` trigger and fire the ones with NEW items (§7 item 8 — S121).

        🔴 Measured before this existed: `web_watch` was a fully declared kind — creatable in chat
        (`nl_kind` routes any URL to it), persisted, listed by `/api/triggers` and rendered on the
        Automations page — and **nothing polled it**. The clock tick skips it (it has no
        `next_fire_at`) and the file poller only reads `file`. So a user could ask for exactly what
        the plan advertises, be told it worked, and never get a fire.

        Deliberately mirrors `_file_watch_poll_loop` rather than inventing a second shape: same
        incident-mode suspension (an unattended fire is an unattended fire), same per-trigger
        isolation inside `poll_all`, and the same never-die contract — a loop that threw once and
        stopped would silently retire every web watch the user has.

        The skipped rows are LOGGED rather than dropped. §7 criterion 8 bans silent drops, and
        "the daily request budget is spent" is exactly the kind of decision a user needs to find
        when they ask why a watch went quiet.
        """
        from personalclaw.config.loader import config_dir
        from personalclaw.triggers import web_poll
        from personalclaw.triggers.store import TriggerStore

        store = TriggerStore(base_dir=config_dir())
        while True:
            try:
                await asyncio.sleep(web_poll.POLL_INTERVAL_SECS)
                from personalclaw.guardrails.incident import incident_active

                if incident_active():
                    continue
                payloads, skipped = await asyncio.to_thread(
                    web_poll.poll_all, store, now=time.time()
                )
                for row in skipped:
                    logger.info("web_watch %s did not fire: %s", row["trigger_id"], row["reason"])
                for payload in payloads:
                    await self._fire_file_trigger(payload)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - the loop must outlive any single poll's failure
                logger.warning("web_watch poll loop iteration failed", exc_info=True)

    async def _fire_file_trigger(self, payload: dict[str, Any]) -> None:
        """Run one file trigger's declared action (S93), through the shared store dispatch.

        Delegates to `_fire_store_trigger` (S100) rather than repeating the provider lookup: a clock
        fire and a file fire must execute the same action the same way, and two near-identical
        dispatches were exactly the dual path the clean break forbids.
        """
        from personalclaw.config.loader import config_dir
        from personalclaw.triggers.store import TriggerStore

        trigger_id = str(payload.get("trigger_id") or "")
        row = TriggerStore(base_dir=config_dir()).get(trigger_id)
        if row is None:
            return
        # The event NAME identifies the source to the action; a clock fire and a file fire share the
        # dispatch but not the label, so a provider can still tell what woke it.
        await self._fire_store_trigger(row.trigger, payload, event="file.changed")

    async def _init_cron(self) -> None:
        """Initialize and start the cron service."""
        # 🔴 The legacy cron DISPATCHER retired with `ScheduleService` (S112). It was the
        # `on_job` callback: ~450 lines that resolved a channel, built a session, ran the turn
        # and posted the result — reachable ONLY from the timer the S100 cutover stopped arming.
        # Store-backed fires go through `_fire_store_trigger` (one dispatch for clock, file and
        # event kinds), and the clock loop, reaper and run records are their own modules now.
        if self._no_crons:
            logger.info("Automations disabled (--no-crons)")
        else:
            # Rotate run history at boot — the ONE load-bearing thing the retired legacy service's
            # boot call still did. `ScheduleRunStore` owns rotation, so it is called directly
            # (S112).
            try:
                await ScheduleRunStore(config_dir()).rotate_all()
            except Exception:
                logger.debug("Run-history rotation at boot failed", exc_info=True)
            # The file-watch poll loop (S93): fires `file` triggers whose watched paths changed —
            # the runtime that makes S92's chat-created file automations actually run. Lives in the
            # else-branch so --no-crons disables it too (a file watch is unattended background work
            # like a cron). Disjoint from ScheduleService, so no double-fire.
            self._file_watch_task = asyncio.create_task(self._file_watch_poll_loop())
            # The web_watch poll loop (S121). Same placement and the same reasoning as the file
            # watch above: unattended background work, so `--no-crons` disables it too, and it is
            # disjoint from every other firing path so it cannot double-fire. Measured before
            # wiring: `web_watch` was creatable in chat, listed by the API and rendered in the UI,
            # and NOTHING polled it — the clock tick skips it (no `next_fire_at`) and the file
            # poller only reads `file`.
            self._web_watch_task = asyncio.create_task(self._web_watch_poll_loop())
            # Import `crons.json` into the unified trigger store and arm the imported clocks (S98).
            # Measured: `migrate_from_crons` was called by NOTHING outside tests, so `triggers.json`
            # was empty on a real machine — every cron lived only in the legacy file, which blocks
            # re-pointing `/api/triggers` at the store (§6) and leaves the tick nothing to fire.
            # Idempotent and additive: `crons.json` stays on disk (§6's "read-only one release",
            # which `verify-migration` needs to diff) and the legacy scheduler still runs from
            # it, so a bad import is fixed by editing the legacy file and restarting rather than
            # by restoring a deletion.
            try:
                from personalclaw.triggers.boot_migrate import migrate_and_arm

                # No explicit home: `migrate_and_arm` resolves it through its OWN `config_dir`, so
                # there is exactly one place to redirect the boot migration (which is what
                # `tests/conftest.py::_isolate_trigger_store` patches). Passing `config_dir()` from
                # here instead bypassed that single point and made three pre-existing gateway tests
                # migrate the USER's real crons into `~/.personalclaw/triggers.json`.
                migrate_and_arm()
            except Exception:
                logger.warning("trigger-store migration failed at boot", exc_info=True)
            # 🔴 THE TWO SYSTEM RECONCILERS, now AFTER the migration and against the STORE (S108).
            # Both used to write `crons.json` from BEFORE this point, which was doubly wrong: the
            # clock engine reads the store only, and the migration that would have imported their
            # writes had already run. Measured: an app's declared cron and the notification digest
            # were both inert until the NEXT boot — so a freshly installed app's cron never ran on
            # the session that installed it, and a digest schedule edited in Settings took two
            # restarts. Ordered after the migration so a reconciler never fights an import over the
            # same id.
            from personalclaw.triggers.store import TriggerStore

            _trigger_store = TriggerStore(base_dir=config_dir())
            # App-declared crons (untrusted-app sandbox P3): register what enabled+permitted apps
            # declare (can_use_cron) and prune stale `app:*` rows. Idempotent; apps are loaded
            # before this by the extension loader. Best-effort — never block the scheduler on it.
            try:
                from personalclaw.apps.app_crons import reconcile_app_crons

                reconcile_app_crons(_trigger_store)
            except Exception:
                logger.warning("app-cron reconcile failed", exc_info=True)
            # The notification digest (plan 42 T5.1). Reconciled, not just created, so a schedule
            # edited in Settings converges without the user knowing a cron exists.
            try:
                from personalclaw.action_providers.digest_provider import reconcile_digest_cron

                reconcile_digest_cron(_trigger_store)
            except Exception:
                logger.warning("digest-cron reconcile failed", exc_info=True)
            # 🔴 THE BOOT SWEEP (§3.1/§3.4, criterion 7 — S142). `service.boot` is what recovers
            # the exactly-one-upcoming invariant, STAGGERS an overdue population, and produces the
            # missed-fire review. It had **zero callers**: boot ran `migrate_and_arm`, which only
            # arms rows with NO `next_fire_at` (`needs_arming`), so a trigger that WAS armed and
            # went overdue while the lid was shut was left with its stale past fire — and the first
            # tick found it due. Measured on ten minutely triggers overdue by an hour: **10 of 10
            # due in the same instant at boot**, the restart stampede `boot_recovery`'s
            # deterministic per-id stagger exists to prevent (108-179s apart, when called).
            #
            # AFTER the reconcilers so an app-declared or digest cron written moments ago is swept
            # too, and BEFORE the clock loop starts so no tick sees an unrecovered row.
            try:
                from personalclaw.triggers import service as _svc

                boot_report = _svc.boot(_trigger_store)
                logger.info(
                    "trigger boot sweep: re-armed %d of %d, %d missed slots to review",
                    len(boot_report.get("rearmed") or []),
                    int(boot_report.get("total", 0) or 0),
                    len((boot_report.get("review") or {}).get("rows") or []),
                )
                self._surface_missed_review(boot_report)
            except Exception:
                logger.warning("trigger boot sweep failed", exc_info=True)
            # The unified CLOCK LOOP (S100) — now the only thing that fires a clock trigger. The
            # legacy timer is gone entirely as of S112, along with the class that owned it.
            self._clock_task = asyncio.create_task(self._clock_loop())
            # The trigger REAPER (S106), replacing `ScheduleService.start_reaper`. That one swept a
            # dict written only by the retired timer's `_run_job_isolated`, so it has been provably
            # inert since S100 — driven with a genuinely hung task, eight sweeps reaped nothing.
            # This one reads S97's cross-process claims, so it bounds every store-backed run and
            # survives a restart. It needs no `sessions`: the subagent manager's own live reaper
            # owns the spawned PROCESS, and this owns the CLAIM (see `triggers/reaper.py`).
            self._reaper_task = asyncio.create_task(self._trigger_reaper_loop())

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
                _hb_model = getattr(getattr(client, "client", None), "_model", "") or ""

                def _hb_usage(event: object, _m: str = _hb_model) -> None:
                    from personalclaw.usage_ledger import record_from_event

                    record_from_event(
                        event,
                        source="background",
                        session_key=session_key,
                        provider="acp",
                        model=_m if isinstance(_m, str) and _m != "auto" else "",
                    )

                result_text = await stream_and_collect(
                    client,
                    full_message,
                    approval_policy=ToolApprovalPolicy.HOOK_BASED,
                    hooks=self.ctx_builder.hooks,
                    on_tool_approval=None,
                    on_complete=_hb_usage,
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

        async def _auto_archive_sessions() -> None:
            """Move conversations idle past the configured threshold to Archived.

            Reversible by construction: an archived session keeps its transcript and
            its search index entry, so a wrong archive costs one click to restore.
            Off entirely when ``session.auto_archive_days`` is 0.
            """
            state = getattr(self, "dashboard_state", None)
            if state is None:
                return
            from personalclaw.config.loader import AppConfig
            from personalclaw.dashboard.chat_persistence import _save_session_to_history
            from personalclaw.dashboard.session_lifecycle import run_auto_archive

            days = int(AppConfig.load().session.auto_archive_days)
            if days <= 0:
                return
            keys = run_auto_archive(state, days=days)
            for key in keys:
                session = state._sessions.get(key)
                if session is not None:
                    _save_session_to_history(state, session, force=True)
            if keys:
                state.push_sessions_update()

        self.heartbeat_svc = HeartbeatService(
            memory=memory,
            on_task=_heartbeat_task,
            consolidator=self.consolidator,
            on_due_commitments=_deliver_due_commitments,
            on_auto_archive=_auto_archive_sessions,
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

        # The workflow engine's supervisor (WORKFLOWS-V2 Slice 1). It adopts runs the
        # store still thinks are live — after a restart NO run has a controller, so
        # without this they sit in RUNNING forever, which a user reads as "still
        # working" while nothing is.
        if self._cfg.workflows.enabled:
            from personalclaw.workflows.bundled_defs import register_bundled_provider
            from personalclaw.workflows.controller import EngineServices
            from personalclaw.workflows.native_defs import register_native_provider
            from personalclaw.workflows.tick import Limits
            from personalclaw.workflows.watchdog import WorkflowWatchdog

            # The native filesystem def provider — where a user's OWN workflows live.
            # `defs.py` is only a registry seam, so without this nothing writable is
            # registered and saving a definition fails with "no writable provider" unless
            # an app happens to contribute one.
            register_native_provider()
            # The shipped template library (Slice 9a). Read-only, served straight from the
            # package — no boot-time copy into the user's home, so an upgrade ships new
            # templates with no "did the user edit it?" reconciliation.
            register_bundled_provider()

            wf_cfg = self._cfg.workflows
            self.workflow_watchdog = WorkflowWatchdog(
                self.dashboard_state,
                EngineServices(
                    subagents=self.subagent_mgr,
                    model_tiers=wf_cfg.model_tiers(),
                    lane_limits=Limits(lanes=wf_cfg.lane_caps()),
                    node_timeout_total=wf_cfg.default_node_timeout_total_secs,
                    node_timeout_stall=wf_cfg.default_node_timeout_stall_secs,
                ),
            )
            self.workflow_watchdog.start()
            # Publish the supervisor so BOTH consumers can reach it: the REST handlers
            # (Slice 7a) read `state.workflows`, and the `run-workflow` action provider
            # reads `ActionServices.workflows`. Without this the routes create runs nobody
            # drives, and the trigger provider returns "no supervisor available" — both
            # already handle a None, but both are inert until this line runs.
            if self.dashboard_state is not None:
                self.dashboard_state.workflows = self.workflow_watchdog
            try:
                from personalclaw.action_providers.services import get_action_services

                svc = get_action_services()
                if svc is not None:
                    svc.workflows = self.workflow_watchdog
            except Exception:
                logger.debug("could not attach the workflow supervisor to action services")

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
                            notification_kinds.HEARTBEAT, title, body, meta={"session": session.key}
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
                        notification_kinds.HEARTBEAT, title, body, meta={"session": session.key}
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
                self.dashboard_state.notify(
                    notification_kinds.HEARTBEAT, title, body, meta={"session": session.key}
                )
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
                self.dashboard_state.notify(notification_kinds.HEARTBEAT, title, body)
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
            self.dashboard_state.notify(notification_kinds.HEARTBEAT, title, body)

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

                def _inject_usage(event: object, _src: str = label, _key: str = parent_key) -> None:
                    from personalclaw.usage_ledger import record_from_event

                    _m = getattr(getattr(client, "client", None), "_model", "") or ""
                    record_from_event(
                        event,
                        source=_src,  # "channel" | "cron" — the announce path's label
                        session_key=_key,
                        provider="acp",
                        model=_m if isinstance(_m, str) and _m != "auto" else "",
                    )

                for attempt in range(3):
                    try:
                        return await stream_and_collect(client, msg, on_complete=_inject_usage)
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
            # Subagent result → the parent transcript. A blind head-cut here was a real
            # failure class ("the subagent found it but the 3000-char cap ate it" — a
            # finding buried past char 3000 vanished). Route through project_and_retain
            # (Context Economy §2.5a) so the parent gets a TYPE-PROJECTED digest plus a
            # raw_ref recovery handle (tool_result_get) instead of a truncated prefix. The
            # raw is retained under the PARENT's session key (the injected message lives in
            # the parent transcript, so its raw must share that lifecycle). Fail-soft: no
            # session key or a small result passes through untouched.
            if info.error:
                detail = f"Error: {info.error}"
            else:
                detail = info.result or "_No response._"
                if len(detail) > 3000:
                    from personalclaw.tool_providers.projection import project_and_retain

                    detail, _meta = project_and_retain(detail, session_key=parent_key, cap=3000)
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
                                notification_kinds.SUBAGENT,
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
                    notification_kinds.SUBAGENT,
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
                        notification_kinds.SUBAGENT,
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
                    except Exception:
                        logger.exception(
                            "Cron session %s: reset failed after last subagent", parent_key
                        )

            # Dashboard notification
            if self.dashboard_state and not info.silent:
                self.dashboard_state.notify(
                    notification_kinds.SUBAGENT,
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
            # (S107) The scheduler's refresh callback is gone. It fired only from
            # `_record_run`, reachable only from the retired timer and the manual-run path —
            # and that path's HANDLER already pushes both kinds in its own `finally`. Scheduled
            # fires now push through `_push_trigger_refresh` on the store-backed fire path,
            # which is the one that actually runs.
            # Attach the inbox service (built in _init_inbox, which runs before the
            # dashboard state exists) so the Inbox handlers reach draft/classify/digest.
            self.dashboard_state._inbox_svc = self.inbox_svc
            self.dashboard_state._inbox_restart = self._restart_inbox

    async def _init_api_server(self) -> None:
        """Start a minimal API-only HTTP server for MCP tool transport."""
        from personalclaw.dashboard import start_api_server

        assert self.sessions is not None
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
        if self.workflow_watchdog:
            await self.workflow_watchdog.stop()
        for _task in (
            self._file_watch_task,
            self._web_watch_task,
            self._clock_task,
            self._reaper_task,
        ):
            if _task is None:
                continue
            _task.cancel()
            try:
                await _task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001 - shutdown is best-effort
                pass
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
            ready_token = generate_token(
                "local-startup", ttl_seconds=DEFAULT_BROWSER_SESSION_TTL_SECS
            )
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

        # ── Structured crash capture (PLATFORM-RESILIENCE §6.5) ──
        # An unhandled exception escaping a background task (a chat turn, a loop
        # worker) reaches the loop's exception handler. Capture it as ONE structured,
        # redacted artifact under ~/.personalclaw/crashes/ (best-effort, never masks
        # the original) so a mid-stream death leaves a recoverable record, then chain
        # to the default handler so logging is unchanged.
        _default_exc_handler = loop.get_exception_handler()

        def _crash_exc_handler(lp: "asyncio.AbstractEventLoop", context: dict) -> None:
            try:
                exc = context.get("exception")
                if isinstance(exc, BaseException) and not isinstance(
                    exc, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)
                ):
                    from personalclaw.resilience.crashes import record_crash

                    key = ""
                    task = context.get("task")
                    if task is not None:
                        key = str(getattr(task, "get_name", lambda: "")() or "")
                    kind = "loop_worker" if "loop" in key.lower() else "turn"
                    _ds = self.dashboard_state
                    _start = float(getattr(_ds, "start_time", 0.0)) if _ds is not None else 0.0
                    record_crash(
                        kind,  # type: ignore[arg-type]
                        exc,
                        session_key=key,
                        uptime_secs=time.time() - _start,
                        now=time.time(),
                    )
            except Exception:
                logger.debug("crash exception-handler hook failed", exc_info=True)
            # Chain to the previously-installed handler (or the loop default).
            if _default_exc_handler is not None:
                _default_exc_handler(lp, context)
            else:
                loop.default_exception_handler(context)

        loop.set_exception_handler(_crash_exc_handler)

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
                startup_token = generate_token(
                    "local-startup", ttl_seconds=DEFAULT_BROWSER_SESSION_TTL_SECS
                )
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

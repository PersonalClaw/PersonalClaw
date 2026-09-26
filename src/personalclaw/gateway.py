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
import functools
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

from personalclaw import gateway_base, notification_kinds, shutdown_event
from personalclaw.acp.errors import AcpError, AcpProcessDied
from personalclaw.approval_brief import attach_approval_brief
from personalclaw.cancellation import kill_timed_out
from personalclaw.channel_history import ChannelHistory
from personalclaw.config import AppConfig
from personalclaw.config import loader as config_loader
from personalclaw.config.loader import CRED_OWNER_ID
from personalclaw.constants import CHAT_TURN_TIMEOUT, DATA_WARNING
from personalclaw.context import ContextBuilder
from personalclaw.dashboard import start_dashboard
from personalclaw.dashboard.chat_runner import run_chat
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
from personalclaw.env import _is_wsl, browser_available
from personalclaw.frontend import build_frontend_async
from personalclaw.heartbeat import HeartbeatService, is_keep_response, strip_keep_sentinel
from personalclaw.history import ConversationLog, HistoryConsolidator
from personalclaw.hooks import HookManager, HooksConfig
from personalclaw.llm.base import LLMEvent
from personalclaw.llm_helpers import (
    PromptBusyExhaustedError,
    stream_and_collect,
)
from personalclaw.loop import files as loop_files
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
    approval_subagent_id,
    resolve_max_subagents,
)
from personalclaw.triggers.models import Outcome
from personalclaw.triggers.nudge import (
    AutoNudgeService,
    NudgeLoop,
)
from personalclaw.triggers.nudge import enabled as autonudge_enabled


def config_dir() -> Path:
    """The active home, re-resolved per call — see :func:`personalclaw.config.loader.config_dir`.

    DEFINED here rather than imported: this module can be imported lazily, and an
    import-time binding captures whatever the name pointed at on first use (#2443).
    """
    return config_loader.config_dir()


if TYPE_CHECKING:
    from personalclaw.channel_delivery import ChannelDelivery
    from personalclaw.channel_transports.base import ChannelMessage
    from personalclaw.channel_trust import TrustVerdict
    from personalclaw.dashboard.state import _ChatSession
    from personalclaw.inbox_service import InboxService
    from personalclaw.llm_helpers import ToolApprovalPolicy
    from personalclaw.loop.watchdog import LoopWatchdog
    from personalclaw.workflows.watchdog import WorkflowWatchdog

logger = logging.getLogger(__name__)

# Full chat turn timeout — tool calls, multi-step reasoning, spawning.
# More generous than INJECTION_TIMEOUT (120s) which only covers stream_and_collect.

# Max retries for injecting subagent results into parent sessions.
_MAX_INJECT_ATTEMPTS = 2

# The auto-update install deadline, named so a test can inject one instead of sleeping on
# it. It was an inline literal, which made the only timeout path left in this function
# untestable except by waiting it out — and an untested timeout path is how it ended up
# with no teardown at all (2 orphans per timed-out install, measured: the `pip` child and
# its build-backend grandchild).
_AUTOUPDATE_PIP_TIMEOUT = 400.0  # `pip install -e .` — forks build backends

# Max chars persisted/delivered for a fire's error summary. Sized to fit a rendered
# AgentError envelope (WHAT/WHY/FIX, ~250 chars) so the FIX line — the actionable
# remediation PLATFORM-LEGIBILITY §2 adds over a bare ``TypeName: msg`` — survives into
# the run-ledger row, ``last_error_summary``, and the delivered notification, instead of
# being cut mid-word (as the old 200-char slice did, dropping FIX from every sink).
_ERROR_SUMMARY_MAX = 512

# How often the earned-autonomy promotion scan runs (§6.1). Six hours, not the poll
# interval it rides: one pass reads the SEL tail once per declared action type, and a rung
# is earned over DAYS, so a faster clock would buy nothing and cost a file scan a minute.
_AUTONOMY_PROPOSAL_INTERVAL_SECS = 6 * 60 * 60

# How often a staged auto-update waiter re-checks whether in-flight work has drained
# (RUM-5). A staged apply HOLDS while a session/subagent is running and fires only once
# the tree is idle; 30s is responsive without spinning — the wait is measured in the
# lifetime of the work it defers to, not this cadence.
_STAGED_APPLY_POLL_SECS = 30.0

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


#: The only statuses a pre-dispatch REFUSAL may record (``_record_refused_fire``). Both are
#: keys `triggers.history.SCHEDULE_STATUS_TO_OUTCOME` resolves, and the guard that reads this
#: tuple is what stops a caller inventing a third: an unmapped status falls to that table's
#: silent FAILED fallback, so a defended fire would appear in the user's history as a broken
#: automation. `test_triggers_status_vocabulary` reads this same tuple to enumerate what this
#: writer can produce — keeping it a module constant is what keeps that rail able to see it.
_REFUSAL_STATUSES: tuple[str, ...] = (
    "blocked_injection",
    Outcome.SKIPPED_GATE.value,
    # AG-2's day-budget pause. A bare string because there is deliberately no `Outcome.NEEDS_INPUT`:
    # this status belongs to the EXECUTOR's vocabulary (`executor.STATUS_TO_OUTCOME`), which
    # projects it to `Outcome.DEFERRED` — "parked awaiting a human".
    # `SCHEDULE_STATUS_TO_OUTCOME` carries the same key with the same target so one word cannot
    # mean two things across the merged runs feed, and
    # `test_the_two_status_families_do_not_disagree` is what keeps the two tables honest.
    "needs_input",
)


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


def injection_approval_policy(parent_key: str) -> "ToolApprovalPolicy":
    """Tool-approval policy for a subagent RESULT-INJECTION turn (AUTONOMY-GUARDRAILS §3, AG-11).

    The injection turn runs IN the parent session (announcing a child's result). For an UNATTENDED
    parent — a cron/channel/inbox/side/loop announce, or the ``_bg`` background key: no human is
    watching — the approval resolves through the session's SafetyProfile via
    ``approval_policy_for_session`` (which reads ``profile_for_session``): the one profile path,
    replacing the blanket AUTO_APPROVE default a cron parent used to get. An INTERACTIVE parent (a
    dashboard chat) keeps AUTO_APPROVE — a human is present and chose to auto-approve.

    Behaviour-preserving where it matters: an announce that calls no tool is unaffected, and under
    HOOK_BASED the security hooks still auto-approve hook-neutral tools; only the dangerous tools
    those hooks already deny elsewhere are now gated on an unattended announce turn.
    """
    from personalclaw.guardrails.policy import approval_policy_for_session, is_unattended_session
    from personalclaw.llm_helpers import ToolApprovalPolicy

    if is_unattended_session(parent_key):
        return approval_policy_for_session(parent_key)
    return ToolApprovalPolicy.AUTO_APPROVE


def _background_write_surface(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Label every state write an unattended trigger dispatch causes ``background`` (DAS-9).

    `state_history` declares three writing surfaces and the time-travel panel's "what changed
    while I slept" filter reads them — but `SURFACE_BACKGROUND` had ZERO producers. Measured: the
    only `writing_surface` call in the whole tree was `state_history.py`'s own hourly maintenance
    job, which sets `SURFACE_SCHEDULED`. So a commit caused by a clock / file / webhook / chained
    fire was recorded on the DEFAULT `interactive` surface, and the filter could not tell an
    automation's edit from something the user did at the keyboard — the one distinction the whole
    surface field exists to make.

    A decorator rather than a `with` inside the method, for SCOPE. The surface is read by the
    post-write hook in the WRITER's context (`history_debounce.notify` calls
    `sh.current_surface()`), so it has to be live for every write the dispatch causes: the
    injection-screen refusal row, the denylist and rung refusal rows, the provider's own writes,
    the outcome record, and the chained fires in the `finally`. A context wrapped around only
    `provider.execute` would leave the rest unlabelled — the same bug in a smaller box.

    Unconditionally `background` because unattendedness is NOT a second decision taken here: this
    seam resolves its guardrail identity as `unattended_dispatch_key("trigger:<id>")`, which
    `is_unattended_session` classifies as unattended BY CONSTRUCTION — a store-trigger fire has no
    chat session by definition, which is exactly why the denylist and the rung ladder below already
    judge it under the HEADLESS posture. Branching on that key here would add a second, parallel
    notion of "unattended" whose else-arm is unreachable. The ATTENDED counterpart is a different
    function — `dashboard.handlers.triggers._dispatch_store_action`, the hand-driven "run now" —
    which keeps the default `interactive` surface and is untouched by this.

    The surface travels on a ContextVar, so it survives the `await`s inside the dispatch. It does
    not cross a `run_in_executor` boundary; that is a property of the seam `state_history` chose,
    shared with the `SURFACE_SCHEDULED` producer, and not something this wrap changes.
    """

    @functools.wraps(fn)
    async def _wrapped(*args: Any, **kwargs: Any) -> Any:
        from personalclaw.durability.state_history import SURFACE_BACKGROUND, writing_surface

        with writing_surface(SURFACE_BACKGROUND):
            return await fn(*args, **kwargs)

    return _wrapped


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
        self._owner_id = creds.get(CRED_OWNER_ID, "")
        # Multi-user access is disabled — only owner is authorized. The channel
        # app owns its allowlist config and enforces owner-only in its own runtime;
        # core holds no channel allowlist, and no channel's credentials either: whether a
        # channel is configured is the channel's own answer (`configured_channels`).

        # Outbound delivery lives in `channel_delivery`'s per-provider registry — not on this
        # object and not on DashboardState, which each held their own slot for the same fact
        # until #959, both overwritten by whichever transport started last. `_channel_delivery`
        # below is now a read-only view answering the OWNER-REACHABLE question. Core never
        # imports channel code; the registry holds handles the apps hand it.

        # Services (initialized in start())
        self.sessions: SessionManager | None = None
        self.ctx_builder: ContextBuilder | None = None
        self.conv_log: ConversationLog | None = None
        self.consolidator: HistoryConsolidator | None = None
        self._file_watch_task: "asyncio.Task[None] | None" = None  # S93 file-watch poll loop
        self._web_watch_task: "asyncio.Task[None] | None" = None  # S121 web_watch poll loop
        self._clock_task: "asyncio.Task[None] | None" = None  # S100 unified clock loop
        self._reaper_task: "asyncio.Task[None] | None" = None  # S106 trigger reaper
        # RUM-5: a staged auto-update waiter that HOLDS until in-flight work drains,
        # then applies. One at a time — a second available-update check reuses the
        # live waiter rather than spawning a rival apply against the same tree.
        self._staged_apply_task: "asyncio.Task[None] | None" = None
        self._last_autonomy_scan: float = 0.0  # §6.1 promotion-proposal scan throttle
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
        """The owner id under the one shared key (see ``GatewayServices.owner_id``)."""
        return self._owner_id

    @property
    def _channel_delivery(self) -> "ChannelDelivery | None":
        """A connected channel that can reach the owner, or None.

        Every reader of this in the gateway addresses the OWNER — a cron result, a heartbeat
        summary, an approval prompt, a subagent reply — through `open_dm(owner_id)`, with no
        origin channel to honour. A reply to an incoming message is a different question and
        resolves through :func:`channel_delivery.delivery_for`; sending one through here is
        exactly the misroute #959 reported.
        """
        from personalclaw.channel_delivery import owner_reachable

        return owner_reachable()

    @_channel_delivery.setter
    def _channel_delivery(self, delivery: "ChannelDelivery | None") -> None:
        """Assigning is registering, exactly as on :class:`DashboardState`.

        Kept as a setter rather than removed because assignment is how this handle has always
        been installed on both objects — including by 23 gateway tests that hand the orchestrator
        a fake before driving a delivery path. Those tests are asserting gateway behaviour, not
        the registry's shape, so the seam absorbs the assignment instead of the assertion moving.
        """
        from personalclaw.channel_delivery import register

        register(delivery)

    def register_channel_delivery(
        self, delivery: "ChannelDelivery | None", provider: str = ""
    ) -> None:
        """Register a channel's outbound delivery handle (called by the channel transport at
        ``start_inbound``). ``None`` clears — every handle, or one provider's when named.

        ``provider`` is the same string the transport already passes to
        :meth:`deliver_channel_inbound` on the way in, at the same point in its lifecycle. It is
        optional only so that a core upgrade cannot break an app that has not been updated yet:
        omitted, the provider is derived from the handle's own module, which still gives three
        connected apps three distinct keys instead of one shared slot.
        """
        from personalclaw.channel_delivery import register

        register(delivery, provider)

    async def deliver_channel_inbound(
        self, provider: str, msg: "ChannelMessage", *, is_dm: bool = True
    ) -> "TrustVerdict":
        """The guarded inbound door (EA-7) — trust is applied before a session is reached.

        Delegates to :func:`personalclaw.channel_inbound.deliver_inbound`, which owns the
        chokepoint (and its per-message idempotency, so an un-migrated transport that still
        calls ``guard_inbound`` itself cannot cause a double owner notification). The
        orchestrator adds nothing to the decision — it only supplies itself as the services
        handle, which is what makes the door reachable from a transport's ``start_inbound``
        argument without changing :class:`ChannelTransportProvider`.
        """
        from personalclaw.channel_inbound import deliver_inbound

        return await deliver_inbound(self, provider, msg, is_dm=is_dm, turn_runner=run_chat)

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

                def _sel_log(**kw: Any) -> None:
                    # `Any`, not `str`: every call site here only ever passes the string
                    # fields, but `log_api_access` also accepts a `metadata: dict | None`
                    # keyword (#2948) and mypy checks a `**kwargs` forward against every
                    # parameter of the callee, not just the ones actually supplied.
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

                from personalclaw.workflows import ownership as _ownership

                if _parent_session_name and _parent_session_name.startswith(
                    _ownership.OWNED_PREFIX
                ):
                    # A workflow STAGE: its parent is the run-owned key `workflow:<run>:<node>`,
                    # which is never a dashboard session, so there is no chat Trust toggle to
                    # consult and a session lookup can only ever miss. An UNATTENDED run never
                    # reaches here — its stages spawn `approval_mode="auto"`
                    # (`engine.dispatch_stage`) — so this is an attended run asking, and the audit
                    # says that instead of `scoped_trust_session_not_found`, which read as a
                    # broken lookup on every stage of every run.
                    _sel_log(
                        caller=f"run:{_parent_session_name}",
                        operation=f"{source}.run_stage_attended",
                        outcome="not_auto_approved",
                        resources=_safe_title,
                    )
                elif _parent_session_name:
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

                    # OU-9: stamp the structured brief (tool + blast-radius line) onto
                    # the event as ADDITIVE meta before handing it to the channel, so a
                    # phone prompt can say what the call can touch. Adds one
                    # `tool_meta` key and changes no argument — a channel that ignores
                    # it prompts exactly as before. The dashboard stays the rich
                    # surface; this is data, not rendering.
                    attach_approval_brief(event)
                    try:
                        approved = await self._channel_delivery.request_approval(
                            event,
                            source=source,
                            parent_session_key=parent_session_key,
                            sessions=self.sessions,
                            on_prompted=_on_prompted,
                        )
                    finally:
                        # Only a real answer is delivered to the dashboard's copy. `None` is the
                        # channel producing NO answer (it could not deliver, or this wait was
                        # cancelled); recording that as a rejection wrote an `approval_decision`
                        # row and a "denied" card for a decision nobody made. Cancelling the
                        # dashboard waiter instead ends its approval as `cancelled`.
                        if self.dashboard_state and approved is not None:
                            self.dashboard_state.resolve_approval(request_id, approved)
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
        # bot_name deliberately NOT pinned here — ContextBuilder resolves it
        # live from config per turn, so a Settings → Account rename takes
        # effect on the next message without a gateway restart.
        self.ctx_builder = ContextBuilder(
            memory=memory,
            skills=skills,
            hooks=hooks,
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

        The pre-dispatch gate for unattended work, called by `_fire_store_trigger` — every
        clock, file, webhook and chained fire. On the transition into exceeded, emits ONE
        notification so the user learns their automation is paused for the day without a
        per-fire spam; the caller records the per-fire `needs_input` outcome that projects
        to `Outcome.DEFERRED`, so the PAUSE is legible in the runs feed even though the
        TOAST is de-duped. (Until AG-2 this said it emitted a "needs-input notification"
        while emitting a WARNING and having no caller at all — the docstring asserted the
        clause the code did not satisfy.)

        Two different unknowns, two answers (#3458).

        An unreadable CEILING pauses (returns True) and notifies: the ceiling is the thing
        that makes leaving automation running safe, so discarding the operator's own
        decision and dispatching anyway is the dangerous direction, and a pause that says
        why is not the wedge the old fail-open was written to avoid.

        Any other error still fails OPEN (returns False) — a spend-counter hiccup is a
        bookkeeping fault, not a lost decision, and the meter + breaker remain the hard
        controls.
        """
        try:
            from personalclaw.guardrails.budgets import (
                BudgetConfigUnreadable,
                BudgetVerdict,
                budget_from_config,
                get_meter,
            )
        except Exception:
            logger.debug("day-budget check import failed (fail-open)", exc_info=True)
            return False
        try:
            budget = budget_from_config()
        except BudgetConfigUnreadable as exc:
            self._notify_budget_once(
                "Automation paused — the spend ceiling is unreadable",
                f"{context} was skipped — {exc}. Unattended runs resume once "
                f"`guardrails.budgets` in config.json parses again.",
            )
            logger.warning("%s skipped: %s", context, exc)
            return True
        try:
            if budget.is_unlimited:
                return False
            verdict, reason = get_meter().check_day(budget)
            if verdict is not BudgetVerdict.EXCEEDED:
                # Re-arm the one-shot notification: once the day rolls over (or the
                # user raises the budget) and we're back under the ceiling, the next
                # exceeded window notifies again.
                self._budget_notified = False
                return False
            self._notify_budget_once(
                "Daily automation budget reached",
                f"{context} was skipped — {reason}. Unattended runs resume "
                f"tomorrow, or raise the budget in Settings → Guardrails.",
            )
            logger.info("%s skipped: %s", context, reason)
            return True
        except Exception:
            logger.debug("day-budget check failed (fail-open)", exc_info=True)
            return False

    def _notify_budget_once(self, title: str, body: str) -> None:
        """One toast per pause window, never per fire.

        Both pause reasons — a ceiling that is SPENT and a ceiling that could not be READ
        (#3458) — go through here, so a user who has both cannot be told twice and the two
        cannot drift into two de-dup schemes. Re-armed by the under-ceiling branch above,
        which is what makes the next window notify again.
        """
        if getattr(self, "_budget_notified", False):
            return
        self._budget_notified = True
        if self.dashboard_state is None:
            return
        try:
            self.dashboard_state.notify(notification_kinds.WARNING, title, body)
        except Exception:
            logger.debug("budget notify failed", exc_info=True)

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
            # A row written by anyone else (a loop's auto-nudge, the chat's automation tools in
            # their own process) reaches an open Triggers page within one tick, rather than never.
            on_store_changed=lambda: self._push_trigger_refresh("crons"),
        )

    def _push_trigger_refresh(self, *kinds: str) -> None:
        """Hint open dashboard views to refresh after a store-backed fire (S107).

        Both kinds by default, matching what the legacy `_record_run` pushed plus the list the fire
        may have changed: `cron_history` for the run feed, `crons` for the trigger list's status
        dots and next-fire times. A store change that is not a fire names `crons` alone.
        Best-effort — a broadcast failure must never affect the fire's outcome, and a
        dashboard-less gateway (`--no-dashboard`) simply has nothing to notify.
        """
        # `getattr`, not attribute access: this runs in the fire path's `finally`, and an
        # orchestrator that has not reached `_init_dashboard` yet (or a partially-built one) has
        # no `dashboard_state` attribute at all. An AttributeError from a `finally` would REPLACE
        # the fire's own outcome — a refresh hint must never be able to do that.
        state = getattr(self, "dashboard_state", None)
        if state is None:
            return
        try:
            state.push_refresh(*(kinds or ("crons", "cron_history")))
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

    @_background_write_surface
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

        # The context the provider will receive, built HERE rather than at the `execute` call so
        # the denylist gate below judges the same `(config, ctx)` pair the provider is handed —
        # the call shape the other two seams already use. `status_url` is this trigger's own row,
        # so an action whose effect is a notification can link back to what fired it.
        from personalclaw.triggers.delivery import status_url as _trigger_status_url

        ctx = ActionContext(
            event=event,
            context="",
            payload=payload,
            status_url=_trigger_status_url(trigger_id=str(getattr(trigger, "id", "") or "")),
        )

        # 🔴 THE DENYLIST, at the seam that lost it (AUTONOMY-GUARDRAILS §1.2 — AG-12). §1.2 says
        # the denylist is enforced at the THREE dispatch seams every action-provider execution
        # passes through, "so an app-contributed provider inherits the denylist without knowing it
        # exists", and names the third as `gateway.py:701` — `_run_action_job`, which retired with
        # `ScheduleService` (S112). The successor that method became inherited the kill switch and
        # the rung ladder but NOT the denylist: measured, `enforce_action` appeared once in
        # `hooks.py`, once in `event_triggers.py` and ZERO times here — while this is the dispatch
        # path for every clock, file, webhook and chained trigger, the busiest unattended path in
        # the product. Retiring a legacy path is never a pure deletion.
        #
        # Placed AFTER `_trigger_secrets.resolve` deliberately: the check must see the config the
        # provider will actually receive, so a `{{secret:...}}` that expands into a denied command
        # or a sensitive path is judged on its resolved value and not on a placeholder that dodges
        # every pattern. And BEFORE the rung ladder, matching both other seams — a rung never
        # relaxes a block, an incident, or a budget pause.
        from personalclaw.guardrails.denylist import enforce_action
        from personalclaw.guardrails.policy import unattended_dispatch_key
        from personalclaw.guardrails.rungs import announce_withheld, record_reversal
        from personalclaw.guardrails.rungs import route_provider_action as _route_action

        # 🔴 THE SESSION IDENTITY (PHF-8), now shared by BOTH gates on this seam (the shape
        # `event_triggers` uses), so the denylist and the ladder judge one fire under one resolved
        # posture rather than two. The rung call passed `session_key=""` — which
        # `is_unattended_session` classifies as ATTENDED, so a clock/file/webhook trigger
        # fire resolved INTERACTIVE and "headless by construction" held only in tests. A
        # store-trigger fire has no chat session by definition, so it gets the sessionless
        # unattended identity: it resolves HEADLESS and is bounded by the operator ceiling,
        # and the trigger id rides along so a clamp in the SEL names the automation. Threading it
        # into `enforce_action` is also what lets the run's `SafetyProfile.denylist_extra` and its
        # `path_allowlist` confinement layer here exactly as they do at the other two seams.
        dispatch_key = unattended_dispatch_key(f"trigger:{getattr(trigger, 'id', '') or ''}")
        decision = enforce_action(provider_name, config, ctx, session_key=dispatch_key)
        if decision.blocked:
            matched = decision.matched or ""
            reason = decision.reason or "blocked by a guardrail rule"
            logger.warning(
                "trigger %s: action blocked by the guardrails denylist (%s)", trigger.id, matched
            )
            # `skipped_gate`, the same status the rung hold below records: a denylist block is a
            # pre-dispatch POLICY refusal, which is the class `_REFUSAL_STATUSES` admits.
            # `Outcome.REFUSED` reads closer in prose but is not in that tuple, and an unmapped
            # status falls to `SCHEDULE_STATUS_TO_OUTCOME`'s silent FAILED default — a defended
            # fire would then appear in the user's history as a broken automation.
            # `enforce_action` has already written the SEL row and, for `needs_human`, fired the
            # notification; this row is what puts the refusal in the Runs history too.
            from personalclaw.triggers.models import Outcome as _Outcome

            await self._record_refused_fire(
                trigger,
                status=_Outcome.SKIPPED_GATE.value,
                error=(
                    f"blocked by the guardrails denylist: {matched} — {reason}"
                    if matched
                    else f"blocked by the guardrails denylist: {reason}"
                ),
            )
            self._push_trigger_refresh()
            return

        # 🔴 RUNG ROUTING, at the seam the retired one became (AUTONOMY-GUARDRAILS §5.2). The plan
        # names `_run_action_job` as the third dispatch seam; that method retired with
        # `ScheduleService` (S112) and, per the note at its old site, "the substrate GENERALIZED
        # both: action dispatch is `_fire_store_trigger`". So this IS the third seam, under a new
        # name — and it is the one every clock / file / webhook / chained trigger passes through.
        # Wiring only the hook and event-trigger seams would honour a declared floor at two of
        # three dispatch points, which is the same shape as not honouring it at all.
        #
        # The route comes from the provider NAME; the name→type mapping lives on the declaration
        # (`ActionTypeSpec.providers`), so an app-contributed action inherits its declared bounds
        # here with no branch of its own.
        route = _route_action(provider_name, session_key=dispatch_key)
        if not route.executes:
            announce_withheld(
                route,
                title=f"{provider_name} is waiting for you",
                body=(
                    f"The {provider_name!r} action on trigger {trigger.id} did not run: "
                    f"{route.reason}."
                ),
                refs={"trigger": str(getattr(trigger, "id", "") or ""), "provider": provider_name},
                dedup_key=f"autonomy_hold:{route.key}:trigger:{getattr(trigger, 'id', '')}",
            )
            from personalclaw.triggers.models import Outcome as _Outcome

            await self._record_refused_fire(
                trigger,
                status=_Outcome.SKIPPED_GATE.value,
                error=f"held for your approval: {route.reason}",
            )
            self._push_trigger_refresh()
            return

        # 🔴 THE DAY-BUDGET PAUSE, at the seam that never had one (AUTONOMY-GUARDRAILS §1.1 — AG-2).
        # `_day_budget_exceeded` was WRITTEN to be this gate — its own docstring says it is "used as
        # a pre-dispatch gate for unattended LLM work (cron agent fires)" — and it had ZERO
        # production callers. Measured at `171a613ae`: `day_budget_exceeded` appears once in `src/`,
        # on its own `def` line, and only tests ever called it. Its caller was `_run_action_job`,
        # which retired with `ScheduleService` (S112); the successor seam inherited neither this
        # gate nor the denylist (AG-12) nor the failure wrap — the third time that retirement was
        # found to have dropped a control it never re-attached.
        #
        # What that cost: the ceiling `settings/GuardrailsPanel.tsx:55` promises will pause "a cron
        # fire" was enforced only at the model-call layer (`model_call.py:302`), which a `bash` or
        # `http` action never reaches at all — so a per-minute trigger over its day ceiling kept
        # firing — and which, when it IS reached, raises into the fire's error path and reports a
        # BROKEN automation rather than a deliberate pause.
        #
        # `needs_input`, not `skipped_gate`: AG-2's clause says the fire "pauses into needs-input",
        # and `executor.STATUS_TO_OUTCOME` already maps that status to `Outcome.DEFERRED` — "parked
        # awaiting a human", the one reading of DEFERRED that means a ceiling only a person can
        # lift. `skipped_gate` is in `INERT_OUTCOMES` and folds OUT of the default runs inbox, which
        # would hide the very pause the user is meant to act on.
        #
        # LAST of the pre-dispatch gates deliberately: when a denylist block or a rung hold also
        # applies, the row must record the SECURITY verdict, not a budget pause. A rung cannot
        # relax this — a withheld action returned above and spent nothing.
        #
        # Recording cannot wedge the fire: `_day_budget_exceeded` fail-opens on any error, and both
        # calls below swallow their own failures, so a bookkeeping fault can never turn a pause into
        # a stuck automation. The one-shot `_budget_notified` de-dupe stays INSIDE the gate where it
        # belongs — it exists so a per-minute trigger does not raise one toast per fire — while the
        # outcome row is written per fire, because a fire that was skipped produced an outcome and
        # suppressing it would drop the row that makes the pause legible in the runs feed.
        if self._day_budget_exceeded(context=f"trigger {getattr(trigger, 'id', '') or ''}"):
            await self._record_refused_fire(
                trigger,
                status="needs_input",
                error=(
                    "paused — the daily automation budget is spent. Unattended runs resume "
                    "tomorrow, or raise the budget in Settings → Guardrails."
                ),
            )
            self._push_trigger_refresh()
            return

        try:
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
            # `auto_with_undo`: persist the provider's reversal handle + passively notify. Only
            # for an action that succeeded — a failed action has nothing to take back.
            if route.records_reversal and bool(getattr(result, "success", False)):
                record_reversal(
                    route,
                    result,
                    label=provider_name,
                    refs={
                        "trigger": str(getattr(trigger, "id", "") or ""),
                        "provider": provider_name,
                    },
                )
            await self._record_fire_outcome(trigger, result=result)
            self._deliver_fire_outcome(trigger, ok=bool(getattr(result, "success", True)))
        except Exception as exc:  # noqa: BLE001 - a failed fire is logged, never crashes the loop
            # PLATFORM-LEGIBILITY §2: a provider that RAISES (rather than returning a failed
            # result) is wrapped in the shared WHAT/WHY/FIX envelope here — the same wrap the
            # hook (`hooks.py`) and event-trigger (`event_triggers.py`) seams already apply — so
            # an app-contributed provider surfaces a coded, actionable failure on this (busiest,
            # unattended: clock/file/webhook/chained) dispatch path instead of a bare
            # ``TypeName: msg``. Retiring `_run_action_job` (S112) dropped this wrap the way it
            # dropped the denylist (AG-12): the successor seam inherited neither. Built ONCE and
            # threaded into BOTH sinks — the persisted run record / `last_error_summary`, and the
            # delivered notification — so the one envelope is the source of both, not two copies.
            from personalclaw.action_providers import provider_failure

            logger.warning("trigger %s: action failed", trigger.id, exc_info=True)
            rendered = provider_failure(provider_name, exc).render()
            await self._record_fire_outcome(trigger, exc=exc, error=rendered)
            self._deliver_fire_outcome(trigger, ok=False, error=rendered)
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
            # 🔴 ONE NOTIFICATION PER FIRE. A `notify` action's success already put the user's own
            # note in front of them — measured: 5 fires of a per-minute notify trigger made 10
            # notifications, each fire's "Standup nudge: review Q4 tasks" followed by an empty
            # "Standup nudge finished". The report would be a note about the note, so it is not
            # sent; the action's note carries the trigger link instead (`ActionContext.status_url`).
            # A failure still reports: in that case the action's own note never went out.
            if ok and _delivery.notifies_on_its_own(trigger):
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
                summary=error[:_ERROR_SUMMARY_MAX],
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
                # 🔴 A CLOCK TRIGGER IS A SCHEDULED JOB (issue #415), and its outcome belongs on the
                # `cron/*` rows the matrix has always offered — which nothing had emitted since the
                # ScheduleService removal, leaving two configurable controls that could not fire.
                # Read off the trigger because this substrate also carries webhook, event, file and
                # web_watch outcomes, and those are not scheduled jobs. Through `is_scheduled` so a
                # test can derive the kind an outcome WILL carry instead of assuming one.
                scheduled=_delivery.is_scheduled(trigger),
            )
            _delivery.deliver(state, note, delivered_ids=self._delivered_event_ids)
        except Exception:  # noqa: BLE001 - see the docstring
            logger.debug("could not deliver the fire outcome for %s", trigger, exc_info=True)

    async def _record_fire_outcome(
        self,
        trigger: Any,
        *,
        result: Any = None,
        exc: BaseException | None = None,
        error: str = "",
    ) -> None:
        """Record a fire's outcome and autopause a failing trigger (§3.7 / crit 3 — S139).

        On the raise path the caller passes the pre-rendered WHAT/WHY/FIX envelope as
        ``error`` (PLATFORM-LEGIBILITY §2); ``exc`` is still passed because the autopause
        exit is classified by exception TYPE, independent of the human-facing text. So
        ``error`` is the persisted evidence and ``exc`` is the classification signal — one
        envelope, built once at the seam, rather than this method re-deriving a bare
        ``TypeName: msg`` of its own.

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
                    error=error[:_ERROR_SUMMARY_MAX],
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
                # On the raise path `error` is the seam's pre-rendered WHAT/WHY/FIX envelope
                # (PLATFORM-LEGIBILITY §2), whose WHAT line still carries the concrete
                # ``TypeName: msg`` — so the evidence is richer, not lost. It falls back to the
                # result's own error string (a provider returning `success=False` without raising),
                # then to the lifecycle reason — an empty evidence line would be worse than a
                # redundant one.
                detail = error
                if not detail and result is not None:
                    detail = str(getattr(result, "error", "") or "")
                live.last_error_summary = (detail or decision.reason)[:_ERROR_SUMMARY_MAX]
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
        await self._record_refused_fire(
            trigger,
            status="blocked_injection",
            error=f"payload blocked by the injection screen ({groups}); never retried",
        )

    async def _record_refused_fire(self, trigger: Any, *, status: str, error: str) -> None:
        """Write ONE ledger row for a fire a gate refused before the provider was called.

        Shared by every pre-dispatch refusal on this path (the injection screen, the rung
        ladder), because a refusal only a log knows about is a silent drop — and two copies
        of this row would be two chances to write a `status` no projection maps, which reads
        in the user's history as a genuine failure.

        ``status`` must be one of :data:`_REFUSAL_STATUSES`. Refused rather than written,
        because the projection table reads statuses with a `.get(status, FAILED)` — a status
        nobody mapped is indistinguishable from one somebody decided about, and it lands in
        the user's history as a genuine failure.
        """
        if status not in _REFUSAL_STATUSES:
            logger.error(
                "refusing to record fire status %r: not one of %s", status, _REFUSAL_STATUSES
            )
            return
        try:
            import time as _time

            from personalclaw.config.loader import config_dir
            from personalclaw.schedule_history import ScheduleRun

            now = _time.time()
            await ScheduleRunStore(config_dir()).append(
                ScheduleRun(
                    run_id=f"{status}-{int(now * 1000)}",
                    job_id=str(getattr(trigger, "id", "") or ""),
                    trigger=status,
                    started_at=now,
                    finished_at=now,
                    status=status,
                    error=error,
                )
            )
        except Exception:  # noqa: BLE001 - bookkeeping must never alter a security decision
            logger.debug("could not record the refused-fire row for %s", trigger, exc_info=True)

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
                # The watched scratchpad (UP-R18 / universal-planning crit 9). It rides THIS loop
                # rather than adding a third poll task: it is the same "a local file changed" clock
                # at the same cadence, and its own fingerprint check makes an unchanged file one
                # `stat`. Deliberately NOT a store trigger — a scratchpad line never starts a run,
                # so there is no action to dispatch and nothing for the capability fence to guard;
                # it raises an inbox PROPOSAL and stops. Incident mode already suspended above,
                # which is right: proposing work is still unattended background activity.
                await asyncio.to_thread(self._scan_scratchpad)
                # The earned-autonomy promotion scan (§6.1). Rides this loop for the same
                # reason the scratchpad does — it is a periodic "look at local state and
                # raise a proposal" pass with nothing to dispatch — but on its OWN much
                # slower clock, because each pass reads the SEL tail once per declared
                # action type. Self-throttled rather than given a task of its own.
                await asyncio.to_thread(self._scan_autonomy_promotions)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - the loop must outlive any single poll's failure
                logger.warning("file-watch poll loop iteration failed", exc_info=True)

    def _scan_autonomy_promotions(self) -> None:
        """Raise a proposal for every action type that has EARNED its next rung (§6.1).

        The ladder only ever climbs on a click, so an earned rung has to travel to the user
        instead of waiting to be found in a Settings panel — that is the difference between
        a promotion the user chose and a promotion nobody ever hears about. The proposals
        are deduped per (type, rung), so re-running this costs nothing and re-raises nothing.

        Never promotes and never raises: this scan cannot change a rung, and a failed scan
        must not stop the file fires that share the loop.
        """
        import time as _time

        now = _time.monotonic()
        if now - self._last_autonomy_scan < _AUTONOMY_PROPOSAL_INTERVAL_SECS:
            return
        self._last_autonomy_scan = now
        try:
            from personalclaw.guardrails.ladder import propose_promotions
            from personalclaw.workflows.handlers import promotion_attention_note

            proposed = propose_promotions(note_for=promotion_attention_note)
            if proposed:
                logger.info(
                    "autonomy: proposed a promotion for %d action type(s): %s",
                    len(proposed),
                    ", ".join(proposed),
                )
        except Exception:  # noqa: BLE001 - additive; never breaks the poll loop
            logger.warning("autonomy promotion scan failed", exc_info=True)
        # §4.4 mechanical revocation, nodding leg. The other three triggers fire at
        # their own conclusion events; a nodding gate is a STANDING condition with no
        # event, so this sweep carries it — gateway-side, because guardrails must not
        # import the workflows layer (the same inversion as `note_for` above). The
        # journal walk is priced only when something is actually at stake: with no
        # standing grant there is nothing to revoke, and revocation's natural
        # idempotence (a revoked scope holds no grant) keeps a persistent nodding gate
        # from re-firing every sweep.
        try:
            from personalclaw.guardrails.autonomy import registered_action_types, rung_state
            from personalclaw.guardrails.ladder import revoke_granted_scopes
            from personalclaw.workflows.handlers import nodding_revocation_cause

            any_granted = any(
                (state := rung_state(spec.key)) is not None and state.granted_at
                for spec in registered_action_types()
            )
            if any_granted:
                cause = nodding_revocation_cause()
                if cause:
                    revoked = revoke_granted_scopes(
                        cause=cause,
                        evidence_id="nodding_gate",
                        source="nodding_loop",
                    )
                    if revoked:
                        logger.warning(
                            "autonomy: nodding gate revoked %d grant(s): %s",
                            len(revoked),
                            ", ".join(revoked),
                        )
        except Exception:  # noqa: BLE001 - additive; never breaks the poll loop
            logger.warning("autonomy nodding revocation sweep failed", exc_info=True)
        # E3 lab_field_divergence (ES-9): a subject whose lab score rose while its live
        # field trend fell files the §4.2 demotion signal mechanically. A divergence is
        # a STANDING condition like the nodding gate above, so it rides the same sweep;
        # it lives in the evals layer (which may import both guardrails and workflows)
        # and prices its own reads — no standing grant anywhere means it returns
        # immediately, and both demotion paths gate on a standing grant, so a
        # persistent divergence files once and then nothing.
        try:
            from personalclaw.evals.field_metrics import sweep_lab_field_divergence

            demoted = sweep_lab_field_divergence()
            if demoted:
                logger.warning(
                    "autonomy: lab_field_divergence filed demotions for %d subject(s): %s",
                    len(demoted),
                    ", ".join(demoted),
                )
        except Exception:  # noqa: BLE001 - additive; never breaks the poll loop
            logger.warning("lab_field_divergence sweep failed", exc_info=True)

    def _scan_scratchpad(self) -> None:
        """Scan the configured scratchpad and raise proposals for its new actionable lines.

        Off unless `planning.scratchpad_path` is set — `scan_and_propose` returns immediately on an
        empty path, so an unconfigured install reads no files at all. Runs in a worker thread
        because a parse plus one injection screen per line is blocking work, and never raises: a
        failed scan must not stop the file-watch fires that share this loop.
        """
        try:
            from personalclaw.planning.scratchpad import scan_and_propose

            raised = scan_and_propose(self.dashboard_state)
            if raised:
                logger.info("scratchpad intake raised %d proposal(s)", len(raised))
        except Exception:  # noqa: BLE001 - intake is additive; it must never break the poll loop
            logger.warning("scratchpad intake failed", exc_info=True)

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
            # The Self-QA commit watcher (SELF-VERIFICATION §3.1). Reconciled, not just created,
            # so toggling the companion or re-pointing `watched_repo` in Settings converges without
            # a restart being part of the instructions. No-ops entirely when it is off.
            try:
                from personalclaw.selfqa.install import reconcile as reconcile_selfqa_watch

                reconcile_selfqa_watch(_trigger_store)
            except Exception:
                logger.warning("selfqa-watch reconcile failed", exc_info=True)
            # The monthly usage recap (MRT-3). Sits in the `--no-crons` else-branch with every
            # other unattended writer: a recap is background work, and a harness run must not
            # emit one. Creation only, not convergence — "monthly" is the feature, not a setting.
            try:
                from personalclaw.action_providers.usage_recap_provider import (
                    reconcile_usage_recap_cron,
                )

                reconcile_usage_recap_cron(_trigger_store)
            except Exception:
                logger.warning("usage-recap-cron reconcile failed", exc_info=True)
            # 🔴 The morning source digest (WATCHED-SOURCES §6.2). THIS LINE IS WS-7's MISSING
            # HALF: `run_morning_digest` shipped fully tested with zero callers, which its own
            # execution log records as the atom's one PARTIAL clause. Same else-branch as the
            # recap above for the same reason — an unattended writer must not fire in a harness
            # run. Creation only: "morning" is the feature, not a setting, so there is no
            # schedule to converge and no config field behind it.
            try:
                from personalclaw.action_providers.source_digest_provider import (
                    reconcile_source_digest_cron,
                )

                reconcile_source_digest_cron(_trigger_store)
            except Exception:
                logger.warning("source-digest-cron reconcile failed", exc_info=True)
            # 🔴 The periodic identity report (LEARNING-VISIBILITY T2.5 — LV-4). THIS LINE IS THE
            # SCHEDULE HALF: `deliver_identity_report` shipped fully tested with a POST route as
            # its ONLY caller, so the plan's "scheduled (default monthly, configurable) background
            # job" was a function nothing drove. Same else-branch as the recap and the source
            # digest for the same reason — it writes an artifact, raises an inbox row and spends a
            # background model call unattended, and a harness run must do none of that. CONVERGED
            # rather than created, like the digest and the remediation engine: the cadence is
            # config (`learning.identity_report_cadence`), so changing it on the Learning page
            # takes effect without the user knowing a trigger exists, and `off` disables the row.
            try:
                from personalclaw.action_providers.identity_report_provider import (
                    reconcile_identity_report_trigger,
                )

                reconcile_identity_report_trigger(_trigger_store)
            except Exception:
                logger.warning("identity-report trigger reconcile failed", exc_info=True)
            # 🔴 The health-scored remediation engine (PLATFORM-RESILIENCE §4.3 — PR2-8). THIS LINE
            # IS THE RE-HOMING: the engine used to be driven by `HeartbeatService._maybe_remediate`,
            # which carried its own private `_remediation_next_ts` scheduler; that job is deleted
            # and this trigger is its only driver. Same else-branch as the recap and the source
            # digest for the same reason — the engine prunes and re-indexes unattended, and a
            # harness run must not do that. CONVERGED rather than created, like the digest: both
            # cadences and the on/off switch are config, so an edit in Settings takes effect without
            # the user knowing a trigger exists.
            try:
                from personalclaw.action_providers.remediation_provider import (
                    reconcile_remediation_trigger,
                )

                reconcile_remediation_trigger(_trigger_store)
            except Exception:
                logger.warning("self-remediation trigger reconcile failed", exc_info=True)
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
            # 🔴 THE BOOT ORPHAN PASS (WF2AUT-16). A run this gateway's PREDECESSOR was executing
            # left a live claim behind, and until now the only thing that could end it was
            # `reaper.run_forever`'s 1800s deadline — so for half an hour after every crash or
            # restart the run read as still in flight. `guardrails/self_destruct.py` names the cost:
            # "the ScheduleRunStore row never reaches a terminal state and the fire reads afterwards
            # as a HUNG run rather than as a self-inflicted stop. The user is left debugging a
            # phantom." The claim now carries its `owner_pid`, so at boot the answer is an
            # OBSERVATION rather than a wait: a claim whose owner is provably gone is released, its
            # run row is closed, and its trigger reads DEGRADED with the reason.
            #
            # HERE, and the position is load-bearing twice: BEFORE the clock loop, so the first tick
            # does not evaluate `existing_claim` against a dead owner's claim and suppress the fire
            # it should grant (`overlap: skip`); and BEFORE the reaper, so the answer never depends
            # on which background task happens to sweep first.
            try:
                from personalclaw.triggers import reaper as _reaper

                interrupted = await _reaper.terminalize_orphans(
                    store=_trigger_store, base_dir=_trigger_store.base_dir
                )
                if interrupted:
                    logger.info(
                        "boot orphan pass: %d run(s) interrupted by a restart terminalized (%s)",
                        len(interrupted),
                        ", ".join(str(r.get("trigger_id") or "?") for r in interrupted),
                    )
            except Exception:
                logger.warning("boot orphan pass failed", exc_info=True)
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
        """Initialize and start the heartbeat service.

        No `MemoryStore` is resolved here any more (PR2-8): the only thing that ever wanted one was
        `HeartbeatService._legacy_maintenance`, and with the remediation engine re-homed onto
        its own trigger the engine's memory jobs open their own store.
        """

        async def _heartbeat_task(task_text: str, deliver: str) -> str | None:
            assert self.sessions is not None
            assert self.ctx_builder is not None
            session_key = BACKGROUND_KEY
            _acquired = False
            try:
                client, is_new, _resumed = await self.sessions.get_or_create(session_key)
                _acquired = True
                from personalclaw.context_headroom import resolve_window

                # Named, not derived: this call passes no session key, and a keyless build
                # derives the CHAT use case — so a heartbeat ran on the interactive-chat
                # prompt while Settings → Prompts promised it the Background one.
                full_message, _ = self.ctx_builder.build_message(
                    task_text,
                    is_new,
                    prompt_use_case="background",
                    window=await resolve_window(serving=client),
                )

                # Heartbeat is a pure UNATTENDED background loop — no user present.
                # The approval policy is DERIVED from the session's SafetyProfile, not
                # hardcoded: `_bg` classifies as unattended, so `profile_for_session`
                # resolves to HEADLESS and its approval ("hook_based") maps to
                # HOOK_BASED — the unattended heartbeat resolves through HEADLESS by
                # construction (AUTONOMY-GUARDRAILS Success Criterion #7). This is
                # behavior-preserving: HEADLESS.approval == the prior HOOK_BASED literal.
                # HOOK_BASED keeps the security hooks; hook-neutral tools auto-approve
                # (no interactive callback), never hanging on an unanswerable prompt.
                from personalclaw.guardrails.policy import approval_policy_for_session

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
                    approval_policy=approval_policy_for_session(session_key),
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
            from personalclaw.dashboard.chat_persistence import save_session_to_history
            from personalclaw.dashboard.session_lifecycle import run_auto_archive

            days = int(AppConfig.load().session.auto_archive_days)
            if days <= 0:
                return
            keys = run_auto_archive(state, days=days)
            for key in keys:
                session = state._sessions.get(key)
                if session is not None:
                    save_session_to_history(state, session, force=True)
            if keys:
                state.push_sessions_update()

        self.heartbeat_svc = HeartbeatService(
            on_task=_heartbeat_task,
            consolidator=self.consolidator,
            on_due_commitments=_deliver_due_commitments,
            on_auto_archive=_auto_archive_sessions,
        )
        await self.heartbeat_svc.start()

    def _register_graph_maintenance_passes(self) -> None:
        """Give the standing maintenance jobs their cadence (KL-14).

        Registered HERE rather than at import of `maintenance.py`, because that module is
        imported by the knowledge write path — `store.py` marks the watermark on every
        index-affecting write — and pulling the memory service, the consolidation planner and
        an action-provider module into every write would be a real cost for no benefit.
        """
        try:
            from personalclaw.knowledge import maintenance_passes

            names = maintenance_passes.register_all()
            logger.info("graph maintenance passes registered: %s", ", ".join(names) or "none")
        except Exception:  # noqa: BLE001 — no cadence is a degradation, not a startup failure
            logger.warning("graph maintenance passes not registered", exc_info=True)

    def _install_graph_maintenance_probe(self) -> None:
        """Tell the graph-maintenance host how to measure in-flight ingest work (KL-14).

        The host defers its pass while an import is still running, so a bulk import costs ONE
        edge pass instead of one per item. It cannot ask for that depth itself: the queue is a
        lazy accessor on `DashboardState` that STARTS a worker when none exists, and
        `knowledge/` importing the dashboard would invert the direction the app-platform
        boundary sets. So the depth is INJECTED here.

        🔴 Reads the private `_knowledge_ingest_queue` rather than calling the public
        `knowledge_ingest_queue()` accessor ON PURPOSE: the public one constructs and STARTS a
        queue as a side effect, so asking "how busy is the queue?" would spin one up on a
        gateway that never ingested anything. Probing must not create the thing it probes.

        Without this the host still runs — it treats an unknown depth as drained and logs one
        warning saying so — but the coalescing clause would be inert, which is why the wiring
        has its own test rather than being assumed from the fact that this line exists.
        """
        try:
            from personalclaw.knowledge import maintenance

            def _depth() -> int:
                state = self.dashboard_state
                queue = getattr(state, "_knowledge_ingest_queue", None) if state else None
                try:
                    return int(queue.qsize()) if queue is not None else 0
                except Exception:  # noqa: BLE001 — a depth read must never break a tick
                    return 0

            maintenance.set_in_flight_probe(_depth)
        except Exception:  # noqa: BLE001 — a missing probe degrades, it does not fail startup
            logger.warning("graph-maintenance in-flight probe not installed", exc_info=True)

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
                run_chat,
            )

            if session.running or getattr(session, "_suppress_autonudge_rearm", False):
                # Turn still active — drop this nudge. The next idle poll will
                # try again once the turn ends. Queueing would stack
                # identical 3KB+ nudges and blow up the context window.
                # Returning False keeps cycle_count accurate (only delivered
                # nudges count toward max_cycles).
                #
                # The suppression flag matters HERE now (WF2AUT-11): the timer world armed no
                # timer mid-cycle, so nothing could fire in a re-prompt GAP (running briefly
                # False between re-prompts). The poll world stays due the whole cycle, so the
                # flag the re-prompt loop already sets is the fence that keeps a competing
                # next-cycle nudge out of the gap.
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

                    # loop-<id> (main) or loop-<id>-<taskid> (parallel task-worker);
                    # findings live on the parent loop in both cases.
                    _lid = _key.split("loop-", 1)[-1]
                    if loop_files.loop_dir(_lid) is None and "-" in _lid:
                        _lid = _lid.rsplit("-", 1)[0]
                    return len(loop_files.get_findings(_lid))
                except Exception:
                    return 0

            async def _run_one(_sess, _msg, turn_timeout: float) -> None:
                try:
                    await asyncio.wait_for(run_chat(dstate, _sess, _msg), timeout=turn_timeout)
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

            def _cycle_still_armed(_sess: Any) -> bool:
                """Is the loop that fired this cycle still armed to run it?

                Two facts, both required: the session is still the one the dashboard has
                registered under its key (a delete pops it), and the nudge loop that fired the
                cycle still exists and is active (a pause deactivates it, a stop removes it).
                """
                key = str(getattr(_sess, "key", "") or "")
                if not key or dstate._sessions.get(key) is not _sess:
                    return False
                nudge_svc = self.autonudge_svc
                armed = nudge_svc.get_by_session(key) if nudge_svc is not None else None
                return armed is not None and bool(getattr(armed, "active", False))

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
                        if not _cycle_still_armed(_sess):
                            # The loop was paused, stopped or deleted while this cycle ran. The
                            # re-prompts are the SAME cycle, so they end with it — this is what
                            # used to write a finding minutes after "Paused", and re-save a
                            # deleted loop's transcript as an orphan chat.
                            logger.info(
                                "AutoNudge: %s is no longer armed — abandoning the cycle's "
                                "re-prompts",
                                _sess.key,
                            )
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
                        from personalclaw.triggers.nudge import get_instance as _an_get

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
            # silently. A turn that ends with an `error` message (how run_chat
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
            # goal-loop + code watchdogs at the cutover (Slice 2e). Loops left
            # RUNNING/PLANNING by a crash/restart are re-armed by the watchdog's OWN first
            # poll, before it reads a single loop — there is deliberately no boot hook here
            # (`PP-16`, "one adoption/reaping path": both work-unit nouns sweep from the
            # supervisor that owns them, through `concurrency.boot_sweep`). A hook here could
            # not be retried when it raised, and awaiting it delayed everything below,
            # including HTTP readiness, by however long N stranded planner passes took.
            from personalclaw.loop.watchdog import LoopWatchdog

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
            # The run-end learner (LEARNING-FLYWHEEL §3.3) writes through a MemoryService over
            # the same vector store the rest of the process uses. Handed in here so a terminal
            # run mines its own ledger for failed steps and files lesson proposals — inert until
            # this service reports `has_vector`, so an embedder-less box (or a partially-inited
            # orchestrator, e.g. a test that drives only this path) learns nothing rather than
            # crashing. `over_vector_store(None)` is itself an inert service, so the `getattr`
            # default is a real no-op, not a workaround.
            from personalclaw.memory_service import MemoryService
            from personalclaw.workflows.verify import run_verify_block

            self.workflow_watchdog = WorkflowWatchdog(
                self.dashboard_state,
                EngineServices(
                    subagents=self.subagent_mgr,
                    # The deterministic verifier every `verify_command` gate runs through
                    # (WF2LOO-10). Previously UNSET here, which made every verification gate
                    # in production fail INTERNAL with "no verifier wired" — the engine's
                    # gate contract was complete and its last mile was missing, so two
                    # shipped templates ended on a gate that could not run. Screened +
                    # rlimited + tristate by `loop.gates.run_verify_command`.
                    verify=run_verify_block,
                    model_tiers=wf_cfg.model_tiers(),
                    lane_limits=Limits(lanes=wf_cfg.lane_caps()),
                    node_timeout_total=wf_cfg.default_node_timeout_total_secs,
                    node_timeout_stall=wf_cfg.default_node_timeout_stall_secs,
                    memory=MemoryService.over_vector_store(getattr(self, "vector_memory", None)),
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

        Source-independent: draft/classify/digest run over STORED items (populated by
        the native push source + any configured poll provider) through the bound chat
        model, so they work with no external provider connected. A message-source
        provider is attached when one is configured, enabling poll/history; otherwise
        polling no-ops. Attached to the dashboard state in ``_init_dashboard`` (which
        runs after this)."""
        from personalclaw.identity import operator_name
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
                # Sources are selected BY NAME through the vendor-neutral seam below,
                # so this names no vendor: any other source — including one an app
                # contributes — is resolved by its own ``source_name``, not assumed
                # here. Since INU-8 the seam resolves an app-declared source too
                # (app-contributed instance → entry-point class → native →
                # filesystem); which NAME the inbox polls is the caller's choice, and
                # this default call site asks for the in-process filesystem source.
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
            user_name=operator_name() or "the user",
            style_rules="\n".join(sec.style_rules or []),
        )
        # Background loop: polls the wired provider (when any). Cheap when idle.
        # Retention/dismissed/feedback maintenance is the remediation engine's
        # `inbox.maintenance` job now, not a second cadence in this loop (PR2-11).
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
        from personalclaw.workflows import ownership

        if parent_key.startswith("dashboard:"):
            return {"session": parent_key.removeprefix("dashboard:")}
        # `workflow:<run>:<node>` is excluded for the same reason as `cron:`/`subagent:`/`hook:`:
        # the `chan, ts = key.split(":", 1)` below reads a namespace prefix as a CHANNEL id, so a
        # run-owned key would ask the delivery provider to build a thread link for a channel named
        # "workflow" with ts "<run>:<node>". That is a vendor call on parsed garbage. Latent rather
        # than live today — every run-owned spawn is `silent=True`, and the one caller reachable
        # with such a key suppresses the notification for a silent batch — but it is the same
        # omission as the routing branch in `_subagent_done`, one branch away from the tail that
        # run-owned completions now land in.
        if ":" in parent_key and not parent_key.startswith(
            ("cron:", "subagent:", "hook:", ownership.OWNED_PREFIX)
        ):
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
                    from personalclaw.dashboard.chat import run_chat

                    sel().log_api_access(
                        caller="heartbeat",
                        operation="heartbeat_prompt_deliver",
                        outcome="approved",
                        source="gateway",
                        resources=f"requested={session_name},resolved={session.key}",
                    )
                    ran = session.enqueue_or_run_prompt(prompt, run_chat, self.dashboard_state)
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
            await self._notify_owner_dm(title, result_text)
            return

        # ── channel:<channel>:<thread_ts> → reply to thread ──
        if deliver.startswith("channel:"):
            parts = deliver.split(":", 2)
            try:
                if self._channel_delivery is not None and len(parts) == 3:
                    chan, ts = parts[1], parts[2]
                    await self._channel_delivery.deliver_notification(chan, title, result_text, ts)
                else:
                    await self._notify_owner_dm(title, result_text)
            except Exception:
                logger.exception("Heartbeat channel delivery failed")
            if self.dashboard_state:
                self.dashboard_state.notify(notification_kinds.HEARTBEAT, title, body)
            return

        # ── default: channel DM + dashboard notification ──
        await self._notify_owner_dm(title, result_text)
        if self.dashboard_state:
            self.dashboard_state.notify(notification_kinds.HEARTBEAT, title, body)

    async def _notify_owner_dm(self, title: str, text: str) -> None:
        """Deliver a notification to the owner's DM on a channel that knows who the owner is.

        Through :func:`channel_delivery.open_owner_dm`, which picks the channel and the owner's
        id on it together; the DM and the message go through the same handle.
        """
        from personalclaw.channel_delivery import open_owner_dm

        try:
            opened = await open_owner_dm()
            if opened is not None:
                delivery, channel = opened
                await delivery.deliver_notification(channel, title, text)
        except Exception:
            logger.exception("Heartbeat channel delivery failed")

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
        # Imported for `_subagent_done`'s routing: the run-owned session namespace is defined once,
        # in the module that owns it, so this branch cannot drift from `dispatch_stage`'s key.
        from personalclaw.workflows import ownership

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
            """Drain queued failures into a new recovery run_chat turn.

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
            from personalclaw.dashboard.chat import run_chat

            failures = session._pending_subagent_failures[:]
            session._pending_subagent_failures.clear()
            msg = "\n\n".join(failures)
            msg, _ = redact_exfiltration_urls(msg)
            msg, _ = redact_credentials(msg)
            session.append("user", msg, "msg msg-u auto-go")
            logger.info(
                "Re-triggering recovery run_chat for %s (%d queued failures)",
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
                    run_chat(self.dashboard_state, session, msg),
                    timeout=CHAT_TURN_TIMEOUT,
                ),
            )
            session.task = _task
            self._background_tasks.add(_task)
            _task.add_done_callback(self._background_tasks.discard)
            _task.add_done_callback(_done)

        async def _subagent_done(batch: "list[SubagentInfo]") -> None:
            # C1.1: a batch of completions that all share ONE parent session,
            # delivered in a SINGLE parent turn. The manager coalesces per parent, so
            # every member here has the same parent_key. ``info`` is the
            # representative used for routing/logging; per-child failures notify each
            # member. This replaces the old one-turn-per-completion path that
            # serialized behind the parent's Semaphore(1) and lost bursts.
            if not batch:
                return
            info = batch[0]

            def _notify_all_failed(reason: str) -> None:
                if not self.subagent_mgr:
                    return
                for _member in batch:
                    self.subagent_mgr.notify_injection_failed(_member, reason=reason)

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

                # Cron-approval rewire (AUTONOMY-GUARDRAILS §3, AG-11): the result-injection turn's
                # tool approval resolves through the SafetyProfile for an unattended parent, and
                # stays AUTO_APPROVE for an interactive one. See ``injection_approval_policy``.
                _inject_policy = injection_approval_policy(parent_key)
                _inject_hooks = self.ctx_builder.hooks if self.ctx_builder else None
                for attempt in range(3):
                    try:
                        return await stream_and_collect(
                            client,
                            msg,
                            on_complete=_inject_usage,
                            approval_policy=_inject_policy,
                            hooks=_inject_hooks,
                        )
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
                        _notify_all_failed("provider dead after prompt-busy retries")
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
                        _notify_all_failed("ACP process died")
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

            parent_key = info.parent_session_key
            for _member in batch:
                await _broadcast_subagent_status(_member, "done")

            # Build ONE announce covering every completion in the batch (C1.1). A
            # burst of 8 completions becomes a single parent turn listing all 8,
            # rather than 8 turns serialized behind the parent's Semaphore(1).
            def _one_block(member: "SubagentInfo") -> str:
                m_status = "failed" if member.error else "completed"
                # Subagent result → the parent transcript. A blind head-cut here was a
                # real failure class; route long output through project_and_retain
                # (Context Economy §2.5a) for a type-projected digest + raw_ref handle.
                if member.error:
                    m_detail = f"Error: {member.error}"
                else:
                    m_detail = member.result or "_No response._"
                    if len(m_detail) > 3000:
                        from personalclaw.tool_providers.projection import project_and_retain

                        m_detail, _m = project_and_retain(
                            m_detail, session_key=parent_key, cap=3000
                        )
                m_detail, _ = redact_exfiltration_urls(m_detail)
                m_detail, _ = redact_credentials(m_detail)
                m_task, _ = redact_exfiltration_urls(member.task)
                m_task, _ = redact_credentials(m_task)
                m_task = m_task[:100]
                return (
                    f"Agent `{member.id}`"
                    f"{f' ({member.agent})' if member.agent else ''}"
                    f" {m_status}\n"
                    f"Task: {m_task}\n\n"
                    f"{m_detail}"
                )

            blocks = [_one_block(m) for m in batch]
            n_failed = sum(1 for m in batch if m.error)
            if len(batch) == 1:
                status = "failed" if info.error else "completed"
                title = f"Subagent `{info.id}` {status}"
                announce = "[Subagent completion event]\n" + blocks[0]
            else:
                status = "completed" if n_failed == 0 else "with failures"
                title = f"{len(batch)} subagents {status}"
                announce = (
                    f"[Subagent completion batch — {len(batch)} agents, "
                    f"{n_failed} failed]\n\n" + "\n\n---\n\n".join(blocks)
                )
            title, _ = redact_exfiltration_urls(title)
            title, _ = redact_credentials(title)
            body = announce

            # ── Route completion back to the originating session ──
            # Dashboard → dashboard only (no channel delivery)
            # Channel → channel thread + dashboard notification
            # Cron/no parent → dashboard notification only

            if parent_key.startswith("dashboard:") and self.dashboard_state:
                # Dashboard session — route subagent result through run_chat
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
                            # Bounded by CHAT_TURN_TIMEOUT (~600s): run_chat's
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

                    # Session is idle — start run_chat.
                    _task = asyncio.create_task(
                        asyncio.wait_for(
                            run_chat(self.dashboard_state, _injection_session, announce),
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
                            logger.error("Subagent injection run_chat failed: %s", t.exception())
                            _reason = str(t.exception())
                            _reason, _ = redact_exfiltration_urls(_reason)
                            _reason, _ = redact_credentials(_reason)
                            _notify_all_failed(_reason)

                    _task.add_done_callback(_on_inject_done)
                    self.dashboard_state.push_sessions_update()
                    logger.info("Subagent %s → run_chat in %s", info.id, _session_name)
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

            if parent_key and not parent_key.startswith(
                # `workflow:<run>:<node>` is a RUN-OWNED session (`ownership.OWNED_PREFIX`), not a
                # channel. Its completion is consumed by the run's own controller, which polls
                # `SubagentManager.get` (`controller._reconcile_dispatched_stages`) — so the work
                # here is not "deliver it somewhere else", it is "do not deliver it twice". Without
                # this the key fell through to the branch below and a finished stage was treated as
                # a chat: `sessions.get_or_create("workflow:...")` spun up an ACP session for a
                # session that never existed and burned a full model turn injecting the result into
                # it, retried `_MAX_INJECT_ATTEMPTS` times. `dispatch_stage` already declares the
                # intended policy in its docstring — "completions belong in the run journal, not
                # injected into whatever chat session happened to start the run" — and passes
                # `silent=True` to say so; the only reader of `silent` is the notification tail
                # below, which is where this now lands. The PREFIX (not `is_owned`) is the right
                # test for routing: a malformed owned key is still not a channel.
                ("cron:", "subagent:", ownership.OWNED_PREFIX)
            ):
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
                            from personalclaw.context_headroom import resolve_window

                            msg, _ = self.ctx_builder.build_message(
                                announce,
                                is_new,
                                parent_key,
                                window=await resolve_window(serving=client),
                            )
                        else:
                            msg = announce
                        response = await asyncio.wait_for(
                            _inject_with_retry(client, msg, parent_key, "channel"),
                            timeout=INJECTION_TIMEOUT,
                        )
                        _injected = True  # LLM processed result; channel posting is best-effort

                        # Post only the LLM's synthesized response to the channel
                        try:
                            # The session's own thread when it has one; otherwise the owner's
                            # DM, on a channel that knows the owner's id there.
                            target: "tuple[ChannelDelivery, str] | None" = None
                            thread_channel = (
                                self.sessions.get_channel(parent_key) if self.sessions else None
                            )
                            if response and thread_channel and self._channel_delivery is not None:
                                target = (self._channel_delivery, thread_channel)
                            elif response:
                                from personalclaw.channel_delivery import open_owner_dm

                                target = await open_owner_dm()
                            if target is not None and response:
                                delivery, channel = target
                                elapsed = (
                                    info.elapsed
                                    if info.elapsed > 0
                                    else (time.monotonic() - info.started)
                                )
                                await delivery.deliver_subagent_reply(
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
                        # A provider transient is at least as retriable as the timeout above, and
                        # abandoning here loses a subagent's COMPLETED work: the agent ran, produced
                        # its result, and nothing delivers it. Retriability comes from the engine's
                        # taxonomy (`RETRYABLE_CLASSES` = TRANSIENT | NETWORK) rather than a second
                        # classifier local to this loop, so there is one vocabulary for "may retry".
                        from personalclaw.workflows.failure_taxonomy import classify_exception

                        if _attempt < _MAX_INJECT_ATTEMPTS and classify_exception(exc).retryable:
                            _sleep_before_retry = True
                            continue
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
                        # The count is the attempts MADE, not `_MAX_INJECT_ATTEMPTS`: an early
                        # abandon on a non-retriable error is a legitimate outcome, and printing
                        # the ceiling claimed "all 2 attempts failed" while listing only attempt 1.
                        "Subagent %s: all %d channel injection attempts failed: %s",
                        info.id,
                        len(_channel_failure_reasons),
                        _last_failure_reason,
                    )
                    _notify_all_failed(_last_failure_reason)
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
                        from personalclaw.context_headroom import resolve_window

                        msg, _ = self.ctx_builder.build_message(
                            announce,
                            is_new,
                            parent_key,
                            window=await resolve_window(serving=client),
                        )
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
                    _notify_all_failed(f"injection timed out after {int(INJECTION_TIMEOUT)}s")
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
                _batch_ids = {m.id for m in batch}
                still_running = self.subagent_mgr and any(
                    a.parent_session_key == parent_key and a.id not in _batch_ids
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

            # Dashboard notification — suppressed only when EVERY member is silent.
            if self.dashboard_state and not all(m.silent for m in batch):
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
            """The parent session of the subagent a spawn or tool-call approval id names.

            Both shapes carry the subagent (``subagent.approval_subagent_id``), so a stage's tool
            call is listed under its run like the stage's spawn is — which is what lets the run's
            page show it and the decision path tell when that run has ended.
            """
            agent_id = approval_subagent_id(request_id)
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

    def _publish_runtime_base(self) -> None:
        """Publish the socket we ACTUALLY bound, for every child to resolve from.

        This process is the only one that knows the answer, so it is the only one allowed
        to state it: ``gateway_base.publish()`` records the bound port in this process's
        environment (inherited by every child we spawn) and in a per-home runtime record
        (readable by a child whose environment was rebuilt from an allowlist).

        Before the owner existed, each child worked the base out for itself from
        ``dashboard.url`` or from the import-time ``DASHBOARD_PORT``, and BOTH fall back to
        the fixed 10000. Neither ``--port`` nor ``--port auto`` writes that config, so a
        gateway on another port addressed its children at 10000 — which on a multi-instance
        host is a DIFFERENT instance, not a dead socket (#2539). Two earlier symptoms of the
        same root: ``subagent_run`` answering ``<urlopen error [Errno 61] Connection
        refused>`` on a kiro ACP session while the in-process tools beside it worked
        (`AAP-3`, `K58`), and a ``run-script`` action's ``ctx.notify()`` persisting into a
        second instance's notification store.

        No ``if self._dashboard_port:`` guard: an unset/zero port here means we bound but
        cannot say to what, and ``publish()`` raises. Failing at startup is the honest
        answer — the guard's silence just deferred the same failure to the first tool call,
        by which time the request had already gone somewhere.
        """
        gateway_base.publish(self._dashboard_port)

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
        self._publish_runtime_base()
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
            # No approval survives a restart, so an Inbox row still asking for one from the
            # previous run is asking for nothing — close those before anyone opens them.
            self.dashboard_state.close_orphaned_approval_rows()

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
            port=dashboard_port,
            subagents=self.subagent_mgr,
            owner_id=self._owner_id,
        )
        if dashboard_port == 0 and self._dashboard_runner is not None:
            addresses = self._dashboard_runner.addresses
            if addresses:
                self._dashboard_port = addresses[0][1]
        self._publish_runtime_base()
        if self.dashboard_state:
            self.dashboard_state.no_crons = self._no_crons  # API-only mode

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------

    async def _shutdown(self) -> None:
        """Graceful cleanup of all services."""
        # Withdraw the runtime base record FIRST: from here on this instance is not serving,
        # and a record left behind is a record a later child could resolve. (The pid check in
        # ``live_port()`` already covers a crash; this covers the graceful case exactly.)
        gateway_base.unpublish()
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
            self._staged_apply_task,
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
        """Blocking update check — then acts on the opt-in ``updates.auto`` mode.

        ``off`` (the default) is NOTIFY-ONLY: an available update raises the
        ``update_available`` refresh and is never applied unattended. ``staged``
        applies at the next safe point — it HOLDS while any session/subagent is in
        flight (:meth:`DashboardState.active_work_snapshot`) and fires only once that drains,
        landing solely on the resolved channel/pin release tag, never on ``main``
        (the apply is :meth:`_auto_apply_update`, RUM-4). The check itself always
        runs here; its own egress kill switch is ``updates.check_enabled`` (RUM-3).
        """
        try:
            from personalclaw.dashboard.handlers import _do_update_check, _update_info

            await _do_update_check()
            if _update_info.get("available"):
                logger.info("Updates available from remote")
                from personalclaw.config import AppConfig

                cfg = AppConfig.load()
                if cfg.updates.auto == "staged":
                    logger.info("Auto-update mode 'staged' — applying at the next safe point")
                    await self._staged_auto_apply()
                elif self.dashboard_state:
                    self.dashboard_state.push_refresh("update_available")
            else:
                print("Already on latest version")
        except Exception:
            logger.debug("Update check failed", exc_info=True)

    def _work_in_flight(self) -> bool:
        """Whether a restart-interrupting unit is running: any not-done background
        subagent or any live chat session. Answered in ONE place by reusing
        :meth:`DashboardState.active_work_snapshot`, so the staged-apply gate and the
        manual-restart confirm gate agree. Headless (no dashboard state) has no such
        work to interrupt, so it reads idle."""
        if self.dashboard_state is None:
            return False
        snap = self.dashboard_state.active_work_snapshot()
        return snap["running_agents"] > 0 or snap["sessions"] > 0

    async def _staged_auto_apply(self) -> None:
        """Apply a staged update at the next safe point.

        If in-flight work is present, DEFER: a single background waiter re-checks
        and applies once it drains — this never blocks the caller (startup, the
        boot-path check) while work is running. Otherwise apply immediately. Either
        way the apply is :meth:`_auto_apply_update`, which resolves the channel/pin
        release tag and never touches ``main`` (RUM-4).
        """
        if self._work_in_flight():
            if self._staged_apply_task is not None and not self._staged_apply_task.done():
                # A waiter is already holding — don't spawn a rival apply.
                return
            self._staged_apply_task = asyncio.create_task(self._await_idle_then_apply())
            return
        await self._auto_apply_update()

    async def _await_idle_then_apply(self) -> None:
        """Background waiter: hold while work is in flight, then apply once idle.

        Polls :meth:`_work_in_flight` on ``_STAGED_APPLY_POLL_SECS``. Cancels
        cleanly on shutdown and never applies after a shutdown request.
        """
        try:
            while not shutdown_event.is_set() and self._work_in_flight():
                await asyncio.sleep(_STAGED_APPLY_POLL_SECS)
            if not shutdown_event.is_set():
                await self._auto_apply_update()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("Staged auto-update failed", exc_info=True)

    async def _auto_apply_update(self) -> None:
        """Auto-apply the resolved release: fetch, advance to the target, restart.

        Release-based, never pull-from-main (RUM-4). The ``updates`` channel/pin
        decides the target: every channel but ``nightly`` resolves a release TAG
        and moves the checkout onto it (``git fetch --tags`` + ``git checkout
        <tag>``); the git-only ``nightly`` channel is the ONE path that tracks the
        current branch, and it advances by FAST-FORWARD only — never a silent
        ``reset``. A stable/beta checkout that is already on (or past) the resolved
        tag does nothing, so an unreleased ``main`` commit can no longer trigger an
        unattended move.

        SAFE-BY-DEFAULT: this UNATTENDED path never silently discards a user's
        uncommitted tracked-file edits. If the working tree carries tracked
        changes it REFUSES to advance and leaves the tree untouched — the
        interactive surfaces do the same, and all share the
        ``self_update.git_tracked_changes`` predicate so "is it safe to advance?"
        is answered in exactly one place. Untracked files (task specs, notes) are
        never at risk and never block an update.
        """
        proj = os.environ.get("PERSONALCLAW_PROJECT_DIR", "")
        if not proj:
            return
        from personalclaw import __version__ as _cur_version
        from personalclaw import self_update
        from personalclaw.config import AppConfig

        try:
            cfg = AppConfig.load()
            channel = cfg.updates.channel
            pin = cfg.updates.pin

            # SAFE-BY-DEFAULT: refuse to advance over uncommitted tracked edits.
            # ONE predicate, shared with the dashboard + CLI paths.
            tracked = await asyncio.to_thread(self_update.git_tracked_changes, proj)
            if tracked:
                logger.warning(
                    "Auto-update: refusing to apply — %d uncommitted tracked-file "
                    "change(s) in %s. Commit or `git stash` them; the update applies "
                    "on the next check.",
                    len(tracked),
                    proj,
                )
                if self.dashboard_state:
                    self.dashboard_state.push_update_progress(
                        "error",
                        "Update paused — commit or stash your local changes first.",
                    )
                return

            if channel == "nightly":
                # Nightly: track the current branch, advancing by fast-forward only.
                branch = await asyncio.to_thread(self_update.resolve_default_branch, proj)
                if self.dashboard_state:
                    self.dashboard_state.push_update_progress("pulling", "Fetching latest changes…")
                fetch = await asyncio.to_thread(self_update.git_fetch, proj, branch)
                if fetch.returncode != 0:
                    if self.dashboard_state:
                        self.dashboard_state.clear_update_progress()
                    return
                if await asyncio.to_thread(self_update.git_is_up_to_date, proj, branch):
                    if self.dashboard_state:
                        self.dashboard_state.clear_update_progress()
                    return
                ff = await asyncio.to_thread(self_update.git_fast_forward, proj, branch)
                if ff.returncode != 0:
                    logger.error(
                        "Auto-update: fast-forward to origin/%s failed (rc=%d) — "
                        "branch diverged; leaving tree untouched",
                        branch,
                        ff.returncode,
                    )
                    if self.dashboard_state:
                        self.dashboard_state.clear_update_progress()
                    return
                logger.info("Auto-update: fast-forwarded %s, rebuilding", branch)
            else:
                # Ride the release tag resolved from the channel/pin. Skip when we
                # are already on (or past) it — the whole point of retiring
                # pull-from-main: an unreleased `main` commit never moves the tree.
                target = await self_update.resolve_target(channel, pin)
                if not target:
                    logger.debug("Auto-update: no release resolved for channel=%s", channel)
                    if self.dashboard_state:
                        self.dashboard_state.clear_update_progress()
                    return
                tgt_norm = self_update.normalize_version(target)
                already = self_update.version_tuple(tgt_norm) <= self_update.version_tuple(
                    self_update.normalize_version(_cur_version)
                )
                if (not pin and already) or (
                    pin and tgt_norm == self_update.normalize_version(_cur_version)
                ):
                    if self.dashboard_state:
                        self.dashboard_state.clear_update_progress()
                    return
                if self.dashboard_state:
                    self.dashboard_state.push_update_progress("pulling", f"Checking out {target}…")
                fetch = await asyncio.to_thread(self_update.git_fetch_tags, proj)
                if fetch.returncode != 0:
                    if self.dashboard_state:
                        self.dashboard_state.clear_update_progress()
                    return
                checked = await asyncio.to_thread(self_update.git_checkout, proj, target)
                if checked.returncode != 0:
                    logger.error(
                        "Auto-update: git checkout %s failed (rc=%d)",
                        target,
                        checked.returncode,
                    )
                    if self.dashboard_state:
                        self.dashboard_state.clear_update_progress()
                    return
                logger.info("Auto-update: checked out %s, rebuilding", target)

            # pip install -e . picks up new dependencies into the RUNNING
            # interpreter's env (sys.executable) before the re-exec. Git ran
            # at the repo root; pip + the frontend build run at the package
            # root (nested in the monorepo layout).
            pkg_root = self_update.package_root(proj)
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
                # Own group: pip forks build backends / compilers, all inheriting these
                # pipes. Without it kill_timed_out CORRECTLY refuses to signal a group —
                # this child would share the gateway's — and falls back to a single-pid
                # kill, which leaves the build backend holding the pipe. Measured: the
                # grandchild survived the teardown. Same reason as the twin in
                # dashboard/handlers/updates.py.
                start_new_session=True,
            )
            try:
                _, pip_err = await asyncio.wait_for(
                    pip_install.communicate(), timeout=_AUTOUPDATE_PIP_TIMEOUT
                )
            except asyncio.TimeoutError:
                # The deadline is the WHOLE teardown: `wait_for` cancels the read but
                # leaves the child (and pip's forked build backends) running, so without
                # this the timeout left two live processes behind per fire — on the
                # auto-update poll, which means they accumulate for the gateway's life.
                # kill_timed_out is the ONE owner of that path: it checks group
                # leadership before signalling a group and its reap is bounded.
                await kill_timed_out(pip_install)
                logger.error(
                    "Auto-update: pip install timed out after %.0fs; child killed and reaped",
                    _AUTOUPDATE_PIP_TIMEOUT,
                )
                if self.dashboard_state:
                    self.dashboard_state.push_update_progress("error", "pip install timed out")
                return
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
        from personalclaw.channel_transports import (
            configured_channels,
            get_transport,
            list_transports,
            start_inbound,
        )

        for tname in list_transports():
            transport = get_transport(tname)
            if transport is None:
                continue
            try:
                await start_inbound(transport, self)
            except Exception:
                logger.warning("Channel transport %r start_inbound failed", tname, exc_info=True)
        # Each channel app says whether it has what it needs (its own health), so this line is
        # true for a channel configured on the Apps page too — core used to infer it from two
        # Slack credential names, and printed "no channel credentials" beside a working Slack.
        if not await configured_channels():
            logger.info("Starting in dashboard-only mode (no channel app is configured)")

    # ------------------------------------------------------------------
    # Main run loop
    # ------------------------------------------------------------------

    def _wire_embeddings(self) -> None:
        """Bind the Settings > Models embedding selection to the gateway's vector memory.

        Called AFTER the dashboard / API-server init, never before it: that init is where the
        installed apps register their provider types (``load_all_extensions``) and where
        ``config.json``'s ``providers[]`` are replayed into the LLM registry. Resolved any
        earlier — as it was, right after ``_init_services()`` — an app-provided embedding model
        (Ollama, the sentence-transformers app) could not be built because its app had not
        registered it yet: the vector memory booted with no embed fn, and an Ollama binding logged
        a chained traceback on every boot for a provider that was configured correctly. When no
        embedding model is bound, semantic embeddings stay off until the user picks one.
        """
        from personalclaw.embedding_providers.registry import get_active_embed_fn

        embed_fn = get_active_embed_fn()
        if embed_fn and getattr(self, "vector_memory", None) is not None:
            self.vector_memory.embed_fn = embed_fn

    async def run(self) -> None:
        """Start all services and block until shutdown signal."""
        # ── GOVERNANCE BOOT, first and fail-closed (PLATFORM-HARDENING-FLOORS §5) ──
        # The operator's ceiling is established BEFORE any service exists, because every
        # service below can dispatch an unattended action and each one resolves its posture
        # through `profile_for_session`. A corrupt/unknown ceiling therefore aborts the
        # process with WHAT/WHY/FIX rather than starting wide open with a logged warning:
        # "governance could not be established" is not a degraded mode, it is a stop.
        from personalclaw.guardrails.ceiling import ensure_governance_boot

        ensure_governance_boot()

        # ── KEYSTONE AUDIT for desktop computer use (DESKTOP-COMPUTER-USE §3 floor 1) ──
        # Resolves the out-of-band enable file ONCE, here, so the posture the whole process
        # runs under is fixed before anything can dispatch and is recorded to the SEL. It
        # does NOT abort: unlike governance, "off" is a normal (and the default) state, so a
        # missing or malformed keystone is a refusal at the tool, not a dead gateway.
        from personalclaw.computer_use.enable_state import ensure_computer_use_boot

        ensure_computer_use_boot()

        # Raise the gateway's own FD limit — each ACP agent session uses ~6 FDs
        # (3 pipes) plus MCP server subprocesses, and the default macOS soft limit
        # (256) is too low. The ``resource`` module is POSIX-only; on a platform
        # without it (native Windows) this degrades to a no-op through the guarded
        # helper instead of ``ImportError``-ing gateway boot here. (WIN-1)
        from personalclaw.resource_limits import raise_fd_limit

        raise_fd_limit()

        # Clean up orphaned ACP agent processes from previous runs
        from personalclaw.session import cleanup_orphaned_sessions

        cleanup_orphaned_sessions()

        # ── Initialise all services ──
        self._init_services()

        await self._init_cron()
        await self._init_heartbeat()
        self._install_graph_maintenance_probe()
        self._register_graph_maintenance_passes()
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
        self._wire_embeddings()

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
        from personalclaw.channel_transports import WEBUI_TRANSPORT
        from personalclaw.channel_transports import get_transport as _get_transport
        from personalclaw.channel_transports import list_transports as _list_transports

        _connected_channels = [
            _tp.display_name
            for _tp in (_get_transport(_n) for _n in _list_transports())
            if _tp and _tp.name != WEBUI_TRANSPORT and _tp.connected
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

                # Auto-open dashboard — skip on headless remote sessions. The predicate
                # lives in `env.browser_available()` because `personalclaw setup` asks the
                # same question to decide whether to point at this dashboard flow.
                if self._no_open or not self._cfg.dashboard.auto_open_browser:
                    pass  # suppressed via --no-open flag or config
                elif not browser_available():
                    print("Headless remote session — skipping browser auto-open")
                else:
                    _open_dashboard(dashboard_url)
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


def _open_dashboard(url: str) -> None:
    """Open the dashboard in a browser, best-effort, and always print the URL.

    The URL is printed prominently first so a user whose browser does not
    auto-launch (headless-ish, WSL, a misconfigured ``$BROWSER``) can still
    click or copy it — a no-op improvement on every platform.

    Under WSL, ``webbrowser.open`` has no Linux browser to launch, so we go
    straight to ``wslview`` (from wslu), which hands the URL to the Windows
    default browser — WSL2 forwards localhost, so the dashboard resolves. On
    normal Linux/macOS the standard ``webbrowser.open`` path is used unchanged;
    ``wslview`` is only attempted as a fallback when that open reports failure
    (returns False) or raises. A missing ``wslview`` is swallowed — it must
    never crash the gateway boot.
    """
    import webbrowser

    print(f"Open PersonalClaw: {url}", flush=True)

    if _is_wsl():
        _wslview_open(url)
        return

    try:
        opened = webbrowser.open(url)
    except Exception:
        opened = False
    if not opened:
        _wslview_open(url)


def _wslview_open(url: str) -> bool:
    """Try to open *url* via ``wslview`` (wslu). Best-effort; never raises.

    Returns True if ``wslview`` was launched, False if it is absent or failed.
    """
    import subprocess

    try:
        subprocess.run(["wslview", url], check=False)
        return True
    except (FileNotFoundError, OSError, subprocess.SubprocessError):
        return False


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

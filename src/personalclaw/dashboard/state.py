"""Dashboard shared state — ChatSession and DashboardState."""

import asyncio
import json
import logging
import os
import re
import time
import traceback
import uuid
from collections.abc import Coroutine
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from aiohttp import web

from personalclaw import trust_mode
from personalclaw.atomic_write import atomic_write
from personalclaw.config.loader import DASHBOARD_PORT, config_dir
from personalclaw.dashboard.sse import SseRegistry
from personalclaw.knowledge.store import KnowledgeStore
from personalclaw.security import redact_credentials, redact_exfiltration_urls
from personalclaw.sel import sel
from personalclaw.task_modes import (  # noqa: F401,E501 — re-exported for dashboard callers (chat_runner, tests)
    is_read_only_bash,
    resolve_effective_risk,
)

if TYPE_CHECKING:
    from personalclaw.dashboard._types import (  # noqa: F401
        ContextBuilder,
        ConversationLog,
        HistoryConsolidator,
        LessonStore,
        ScheduleService,
        SessionManager,
        SubagentManager,
    )
    from personalclaw.dashboard.side_state import SideState
    from personalclaw.engagement_signals import EngagementStore

logger = logging.getLogger(__name__)


def _knowledge_embedder_factory():
    """Build a knowledge embedder from PClaw config (or None if disabled).

    Used by the ingestion queue's terminal embed stage — same construction as the
    knowledge handlers' ``_create_embedder``."""
    try:
        from personalclaw.config.loader import config_path
        from personalclaw.knowledge.embedder import create_embedder_from_config

        cfg_path = config_path()
        cfg = json.loads(cfg_path.read_text()) if cfg_path.exists() else {}
        return create_embedder_from_config(cfg)
    except Exception:
        return None


def _log_task_exception(task: asyncio.Task[Any]) -> None:
    """Log unhandled exceptions from fire-and-forget tasks.

    Shared by gateway._deliver_result and chat.py queue-drain paths.
    Short-circuits on cancelled tasks (task.exception() would raise CancelledError).
    Exception message is redacted to avoid leaking credentials/URLs to log sinks.
    """
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        try:
            tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
            redacted_tb, _ = redact_credentials(tb)
            redacted_tb, _ = redact_exfiltration_urls(redacted_tb)
            logger.error("Background task failed:\n%s", redacted_tb)
        except Exception as redaction_err:
            # Include the redaction failure class so bugs in the redactor are visible,
            # without logging the raw traceback (which defeats the redaction contract).
            logger.error(
                "Background task failed (redaction error %s): %s",
                type(redaction_err).__name__,
                type(exc).__name__,
            )


# Read-only bash command classification lives in the neutral ``task_modes``
# module (shared by the dashboard + the native runtime without a dependency
# cycle). ``is_read_only_bash`` is imported at the top of this module and
# re-exported for the dashboard call sites that import it from state.


# ── Shared helpers ──


def parse_cls_meta(cls_val: str) -> dict | None:
    """Parse a JSON-encoded ``cls`` string into a meta dict.

    Returns the parsed dict (with ``tool_input`` sanitized) or ``None``
    if ``cls_val`` is not valid JSON or not a dict.  Used by both
    ``_prepare_messages`` (HTTP history) and ``_broadcast_chat_message``
    (live WS push) so the frontend sees an identical ``meta`` structure.
    """
    if not cls_val:
        return None
    try:
        meta = json.loads(cls_val)
        if not isinstance(meta, dict):
            return None
    except (json.JSONDecodeError, TypeError):
        return None

    # Defence-in-depth: sanitize LLM-controlled content at every read boundary
    if isinstance(meta.get("tool_input"), str):
        sanitized, _ = redact_exfiltration_urls(meta["tool_input"])
        sanitized, _ = redact_credentials(sanitized)
        meta["tool_input"] = sanitized

    # Normalize: backend stores as request_id, frontend expects approval_id
    if "request_id" in meta and "approval_id" not in meta:
        meta["approval_id"] = meta.pop("request_id")

    return meta


def _mark_permission_resolved(messages: list[dict], request_id: str, decision: str) -> None:
    """Persist a resolved decision into a permission message's cls JSON."""
    for msg in reversed(messages):
        if msg.get("role") == "permission":
            try:
                cls = json.loads(msg.get("cls", "{}"))
                if cls.get("request_id") == request_id:
                    cls["resolved"] = decision
                    msg["cls"] = json.dumps(cls)
                    return
            except (json.JSONDecodeError, TypeError):
                pass


# ── Constants ──


_DEFAULT_PORT = DASHBOARD_PORT
_SSE_INTERVAL_SECS = 5
_NOTIFICATIONS_FILE = "notifications.jsonl"
_MAX_PERSISTED_NOTIFICATIONS = 200
_AUTO_COMPACT_NOTICE = "🔄 Auto-compacted at {pct:.0f}%."
_MAX_SESSION_MESSAGES = 10000  # Keep all messages — virtual scrolling handles performance

# Bare chat-N label matcher used by DashboardState.resolve_session() for prefix fallback.
# Gates the prefix lookup to prevent broad matches (e.g. bare "chat" binding to any session).
_CHAT_N_RE = re.compile(r"chat-\d+")

# Cron notification wrapper format — used by handlers.py (create), chat.py (detect), ChatPage.tsx (render)  # noqa: E501
CRON_NOTIFY_PREFIX = "[Cron notification from "
CRON_NOTIFY_END = "[End of cron notification]"
CRON_NOTIFY_RE = re.compile(rf'^{re.escape(CRON_NOTIFY_PREFIX)}"(.*)"\]')
SUBAGENT_COMPLETION_PREFIX = "[Subagent completion event]"

_OPTIONS_RE = re.compile(r"\[OPTIONS:\s*([^\]]+)\]")


def _redact(text: str) -> str:
    """Sanitise LLM output before surfacing to dashboard."""
    text, _ = redact_exfiltration_urls(text)
    text, _ = redact_credentials(text)
    return text


def _parse_options(text: str) -> list[str]:
    """Extract pipe-separated choices from the LAST [OPTIONS: A | B | C] in text."""
    matches = _OPTIONS_RE.findall(text)
    if not matches:
        return []
    parts = [p.strip() for p in matches[-1].split("|")]
    return [p for p in parts if p]


VALID_MEMORY_MODES = ("persistent", "incognito", "temporary")


class _ChatSession:
    """Independent chat session that runs server-side."""

    __slots__ = (
        "key",
        "title",
        "agent",
        "model",
        "linked_session_key",
        "reasoning_effort",
        "acp_provider",
        "acp_provider_agent",
        "acp_mode",
        "mode",
        "workspace_dir",
        "project_id",
        "created_at",
        "messages",
        "total_messages",
        "task",
        "event",
        "_pending",
        "_queue",
        "_approval_futures",
        "_trust",
        "_trust_reads",
        "_agent_floor_seeded",
        "_task_mode",
        "_suppress_autonudge_rearm",
        "_titled",
        "_resumed_count",
        "_on_message",
        "_has_reader",
        "_stop_state",
        "_stop_event_id",
        "_dirty",
        "_orch_tracker",
        "_auto_run",
        "_recovery_chat_triggered",
        "_stage_titles",
        "_stage_descriptions",
        "_plan_goal",
        "_channel_linked",
        "_channel_id",
        "_channel_thread_ts",
        "folder_id",
        "pinned",
        "tags",
        "_pending_subagent_failures",
        "_recovery_retrigger_count",
        "_prompt_busy_retries",
        "_acp_pipe_death_retries",
        "_empty_response_retries",
        "_batch_rejected",
        "color_index",
        "color_theme",
        "memory_mode",
        "_ephemeral",
        "_pending_context",
        "_app",
        "_last_turn_errored",
        "_pending_variants",
        "_lock",
        "forked_from",
        "_fork_lock",
        "_tab_id",
        "_disk_older_count",
        "_file_changes",
        "_side",
        "_extra_tool_roots",
        "_unattended",
    )

    def __init__(
        self,
        key: str,
        title: str = "",
        agent: str = "",
        workspace_dir: str = "",
        model: str = "",
        mode: str = "",
        memory_mode: str = "persistent",
        ephemeral: bool = False,
        project_id: str = "",
    ) -> None:
        self.key = key
        self.title = title or key
        self.agent = agent
        self.model = model
        # The Project this chat scopes under (optional). A project-bound chat is fed
        # its project's workspace + loop history + context-dir locations (Slice 6), so
        # every loop + chat under a project shares one cohesive context. "" = unscoped.
        self.project_id: str = project_id
        # When set, this dashboard session is linked to another session's
        # conversation (e.g. a cron-{id} chat threaded to its cron:{id} agent
        # session). Drives the "Continue" affordance + bidirectional threading.
        self.linked_session_key: str = ""
        # Reasoning effort: "" = provider default, else one of low/medium/high/max.
        # Consumed by Claude Code (--effort flag).
        self.reasoning_effort: str = ""
        # Ephemeral ACP-agent override (a discovered runtime agent picked live in
        # the chat picker — NOT a saved AgentProfile). When set these win over the
        # named-definition resolution in chat_runner: ``acp_provider`` is the
        # runtime id ("acp:<cli>"), ``acp_provider_agent`` the ACP modeId (persona-style
        # agent; "" for claude). Discovery is live + account-dynamic, so we
        # never persist these to config — they live only on the session.
        self.acp_provider: str = ""
        self.acp_provider_agent: str = ""
        # ACP permission-mode override for this session (Zed dialect: acceptEdits
        # / bypassPermissions / plan …). Empty = adapter default ("default",
        # which PROMPTS for writes). Set for unattended goal-loop workers so an
        # ACP agent (claude-code) actually executes its file writes instead of
        # avoiding them; the host gate + SEL audit still govern via auto-approve.
        self.acp_mode: str = ""
        self.mode = mode
        # The session's working directory. Memory is scoped to it. Empty = root.
        self.workspace_dir: str = workspace_dir
        # Extra dirs the native file tools may read/write OUTSIDE workspace_dir. Set
        # for brownfield Code/Goal-Loop workers to the project files dir (under
        # ~/.personalclaw), where the engine writes status.json/brief.md/findings —
        # the worker's cwd is the user workspace, so without this the workspace-
        # confined file tools would reject those engine-file paths. Empty for chat.
        self._extra_tool_roots: list[str] = []
        # Unattended run: no human is present to answer a tool-approval prompt or
        # an option-prompt-shaped tool. Set for unattended loop workers and
        # scheduled run-prompt/run-workflow turns (T5). The native runtime strips
        # interactive tools + fails the approval gate fast so the turn can't wedge.
        # Complements the loop watchdog's "unattended NEVER pauses" enforcement —
        # this closes the tool-availability layer the watchdog can't reach.
        self._unattended: bool = False
        self.created_at: str = datetime.now(timezone.utc).isoformat()
        self.messages: list[dict[str, Any]] = []
        self.total_messages: int = 0  # lifetime count (survives trimming)
        self.task: asyncio.Task | None = None  # type: ignore[type-arg]
        self.event = asyncio.Event()
        self._pending: list[dict[str, str]] = []
        self._queue: list[dict[str, str]] = []  # [{"id": uuid, "content": str}, ...]
        self._approval_futures: dict[str, asyncio.Future[str]] = {}  # type: ignore[type-arg]
        self._trust: bool = False  # auto-approve tools for this session
        self._trust_reads: bool = False  # auto-approve read-only bash commands
        # One-shot latch: has this live session already been seeded from the bound
        # agent's persistent approval floor ("Always allow for this agent")? The seed
        # RAISES trust exactly once per session so a later explicit "Normal" wins and
        # is not clobbered on the next turn. In-memory like _trust itself: a gateway
        # restart re-seeds from the durable AgentProfile floor (that's what a durable
        # grant means), while the ephemeral per-session override does not survive.
        self._agent_floor_seeded: bool = False
        # Task mode — an ORTHOGONAL axis to the approval rungs above (which gate
        # *whether* a tool auto-approves). Task mode gates *which* tools are even
        # available + *how* the agent frames the work, layered on the active agent:
        #   'agent' — full execution (default).
        #   'ask'   — read-only: only SAFE/read tools run; mutating tools are denied.
        #   'plan'  — the agent reasons/plans but NO tool executes (ACP claude also
        #             enforces natively via acp_mode=plan; the host gate enforces
        #             universally for native / the default dialect).
        #   'build' — scoped to producing an artifact/widget/skill.
        # Complements approval mode: e.g. Plan + Trust is a valid combination.
        self._task_mode: str = "agent"
        # When set, chat_runner skips re-arming the autonudge idle timer on turn
        # exit. The goal-loop re-prompt loop sets this while it drives several
        # back-to-back turns within ONE logical cycle, so the idle timer doesn't
        # fire a competing next-cycle nudge mid-loop; the loop re-arms once at the
        # end. (Without it, each inner turn's completion re-armed the timer and
        # raced the loop — see ACP goal-loop worker re-prompt.)
        self._suppress_autonudge_rearm: bool = False
        self._titled: bool = False  # True once a title has been assigned
        self._resumed_count: int = 0  # messages loaded from history on resume
        # Callback for broadcasting messages via global SSE
        self._on_message: object | None = None  # Callable[[str, dict], None] | None
        self._has_reader: bool = False  # True when HTTP SSE stream is draining
        self._stop_state: str = "idle"  # 'idle' | 'soft_pending' | 'killing'
        self._stop_event_id: str | None = None  # transcript message id for in-flight stop
        self._dirty: bool = False  # True when messages changed since last flush
        self._orch_tracker: Any = None  # OrchestrationTracker, set by gateway
        self._auto_run: bool = False  # "Go All" — skip stage gates
        self._recovery_chat_triggered: bool = False  # guard against concurrent failure recovery
        self._stage_titles: list[str] = []  # stage titles extracted from plan
        self._stage_descriptions: list[list[str]] = []  # bullet points per stage
        self._plan_goal: str = ""  # goal from 📋 Plan for: header
        self._channel_linked: bool = False  # True when linked to a channel thread
        self._channel_id: str = ""
        self._channel_thread_ts: str = ""
        self.folder_id: str = ""  # project folder assignment
        self.pinned: bool = False  # pinned to top of sidebar
        self.tags: list[str] = []  # assigned tag ids (see DashboardState._tags)
        self._pending_subagent_failures: list[str] = []
        self._recovery_retrigger_count: int = 0
        self._prompt_busy_retries: int = 0
        self._acp_pipe_death_retries: int = 0
        self._empty_response_retries: int = 0  # consecutive empty turns (silent-retry guard)
        self._batch_rejected: bool = False
        self.color_index: int | None = None
        self.color_theme: str = ""
        if memory_mode not in VALID_MEMORY_MODES:
            raise ValueError(
                f"invalid memory_mode {memory_mode!r}, must be one of {VALID_MEMORY_MODES}"
            )
        self.memory_mode: str = memory_mode
        self._ephemeral: bool = ephemeral  # Incognito mode: no memory writes
        self._pending_context: list[dict[str, Any]] = []
        self._app: str = ""  # owning app identity (empty = dashboard user)
        self._last_turn_errored: bool = False  # set by _run_chat on a crashed turn
        # Regenerate feature: variants pending attachment to next finalized assistant message
        self._pending_variants: list[dict] = []
        self._lock = asyncio.Lock()
        self.forked_from: str | None = None  # parent session key if this is a fork
        self._fork_lock: asyncio.Lock = (
            asyncio.Lock()
        )  # serialises concurrent forks on this session
        self._tab_id: str = ""  # permanent tab identity for cross-restart session chaining
        self._disk_older_count: int = (
            0  # count of disk messages OLDER than in-memory window (stable, set at restore/resume)
        )
        # Per-turn file-change accumulator [{path, before, after}], reset at the
        # top of each _run_chat and flushed onto the assistant message's meta at turn end.
        self._file_changes: list[dict[str, str]] = []
        # Ephemeral side-chat buffer (None = closed). Side Q&A lives ONLY here,
        # never in self.messages — see dashboard/side_state.py.
        self._side: "SideState | None" = None

    @property
    def _plan_stage_count(self) -> int:
        return len(self._stage_titles)

    @property
    def _stopping(self) -> bool:
        return self._stop_state != "idle"

    @_stopping.setter
    def _stopping(self, value: bool) -> None:
        self._stop_state = "soft_pending" if value else "idle"

    def append(
        self,
        role: str,
        content: str,
        cls: str = "",
        ts: str = "",
        *,
        broadcast: bool = True,
        meta: dict | None = None,
    ) -> None:
        msg: dict[str, Any] = {
            "role": role,
            "content": content,
            "cls": cls,
            "ts": ts or datetime.now(timezone.utc).isoformat(),
        }
        if meta:
            msg["meta"] = meta
        self.messages.append(msg)
        self.total_messages += 1
        self._dirty = True
        self._pending.append(msg)
        self.event.set()
        # Broadcast via global SSE when no HTTP stream reader is active
        # Skip: chunk (too noisy), done (internal), user (frontend adds optimistically)
        if (
            broadcast
            and self._on_message
            and role not in ("chunk", "done", "user")
            and not self._has_reader
        ):
            self._on_message(self.key, msg)  # type: ignore[operator]
        # Trim old messages to bound memory usage
        if len(self.messages) > _MAX_SESSION_MESSAGES:
            excess = len(self.messages) - _MAX_SESSION_MESSAGES
            del self.messages[:excess]

    def drain(self) -> list[dict[str, str]]:
        """Return and clear pending messages."""
        out = self._pending[:]
        self._pending.clear()
        self.event.clear()
        return out

    def mark_permission_resolved(self, approval_id: str, decision: str = "approved") -> None:
        """Update stored permission message cls JSON with resolved flag."""
        for m in self.messages:
            if m.get("role") == "permission":
                try:
                    cls_data = json.loads(m.get("cls", ""))
                    if isinstance(cls_data, dict) and cls_data.get("request_id") == approval_id:
                        cls_data["resolved"] = decision
                        m["cls"] = json.dumps(cls_data)
                        return
                except (json.JSONDecodeError, TypeError):
                    pass

    # ── Queue helpers (dict-based queue items) ──

    def queue_append(self, content: str) -> str:
        """Append a message to the queue. Returns the generated queue ID."""
        qid = uuid.uuid4().hex[:12]
        self._queue.append({"id": qid, "content": content})
        return qid

    def queue_insert(self, index: int, content: str) -> str:
        """Insert a message at a specific queue position. Returns the queue ID."""
        qid = uuid.uuid4().hex[:12]
        self._queue.insert(index, {"id": qid, "content": content})
        return qid

    def queue_pop(self, index: int = 0) -> dict[str, str]:
        """Pop a queue item by index. Returns {"id": ..., "content": ...}."""
        return self._queue.pop(index)

    def queue_remove_by_id(self, queue_id: str) -> str | None:
        """Remove a queue item by ID. Returns the content or None if not found."""
        for i, item in enumerate(self._queue):
            if item["id"] == queue_id:
                del self._queue[i]
                return item["content"]
        return None

    def queue_promote(self, queue_id: str) -> bool:
        """Move a queued item to the front, preserving its id. Returns True if found.

        Used by /interrupt's optional ``queue_id`` so the promoted message runs
        next without re-minting its id (the frontend queue card keys off id).
        """
        for i, item in enumerate(self._queue):
            if item["id"] == queue_id:
                if i > 0:
                    self._queue.insert(0, self._queue.pop(i))
                return True
        return False

    @property
    def running(self) -> bool:
        return self.task is not None and not self.task.done()

    @property
    def queue_depth(self) -> int:
        """Number of prompts currently queued behind the active turn."""
        return len(self._queue)

    @property
    def is_restricted(self) -> bool:
        """True when memory writes (consolidation, lessons) are blocked."""
        return self.memory_mode != "persistent"

    @property
    def blocks_reads(self) -> bool:
        """True when memory-context injection into this session is blocked."""
        return self.memory_mode == "temporary"

    def enqueue_or_run_prompt(
        self,
        prompt: str,
        run_chat_coro: "Callable[[DashboardState, _ChatSession, str], Coroutine[Any, Any, None]]",
        state: "DashboardState",
    ) -> bool:
        """Queue *prompt* if busy, otherwise start an agent turn.

        Encapsulates the queue-vs-run decision so callers don't need to
        touch ``_queue``, ``task``, or ``_background_tasks`` directly.
        Always registers :func:`_log_task_exception` to prevent silent failures.

        Returns ``True`` if the prompt started an agent turn, ``False`` if
        it was queued. Lets callers gate UI-visible side-effects (notifications,
        SSE pushes) on whether the prompt actually ran.

        Concurrency: the check (``self.running``) and mutation (``self.task = ...``)
        run synchronously on the asyncio event loop with no ``await`` between them,
        so two concurrent callers targeting the same session cannot both observe
        ``running == False`` within a single loop iteration.
        """
        if self.running:
            self.queue_append(prompt)
            return False
        self.append("user", prompt, "msg msg-u")
        task = asyncio.create_task(run_chat_coro(state, self, prompt))
        self.task = task
        state._background_tasks.add(task)
        task.add_done_callback(state._background_tasks.discard)
        task.add_done_callback(_log_task_exception)
        return True

    def to_dict(self) -> dict:
        last_ts = self.messages[-1].get("ts", "") if self.messages else ""
        # Single reverse scan for last_msg, options, and last_activity_ts.
        last_msg = ""
        has_options = False
        options: list[str] = []
        prompt_preview = ""
        last_conv_role = ""
        last_activity_ts = ""
        found_conv = False
        for m in reversed(self.messages):
            role = m.get("role")
            # Capture last_activity_ts from the most recent actionable message
            if not last_activity_ts and role in ("tool_call", "tool_result", "assistant"):
                last_activity_ts = m.get("ts") or ""
            # Capture last conversational message (once)
            if not found_conv and role in ("user", "assistant"):
                txt = m.get("content") or ""
                if txt:
                    found_conv = True
                    last_conv_role = role
                    redacted = _redact(txt)
                    last_msg = (redacted[:80] + "…") if len(redacted) > 80 else redacted
                    if role == "assistant":
                        options = _parse_options(txt)
                        has_options = bool(options)
                        if has_options:
                            stripped = _redact(_OPTIONS_RE.sub("", txt).strip())
                            prompt_preview = (
                                stripped[:240] + "…" if len(stripped) > 240 else stripped
                            )
            if found_conv and last_activity_ts:
                break
        pending_approval = any(not f.done() for f in self._approval_futures.values())
        # waiting_for_input: turn ended (not running), no options, no approval,
        # and the last conversational message is from the assistant (not user).
        waiting_for_input = (
            not self.running
            and not has_options
            and not pending_approval
            and bool(self.messages)
            and last_conv_role == "assistant"
        )
        # If an approval is pending, surface the tool metadata from the most
        # recent unresolved permission message so the Board can show inline
        # Approve/Trust/Reject buttons without a second API call.
        #
        # LANE ASSIGNMENT NOTE: The frontend's inferLane() uses the boolean
        # `pending_approval` field (not `pending_approval_info`) to assign
        # sessions to the "Needs Approval" lane. `pending_approval_info` is
        # supplementary UI metadata (tool name, input, kind) for rendering
        # inline action buttons — it does NOT drive lane placement.
        pending_approval_info: dict[str, str] | None = None
        if pending_approval:
            for m in reversed(self.messages):
                if m.get("role") != "permission":
                    continue
                meta = parse_cls_meta(m.get("cls") or "") or {}
                if meta.get("resolved"):
                    continue
                pending_approval_info = {
                    "tool": _redact(m.get("content") or ""),
                    "tool_input": _redact(meta.get("tool_input", "")),
                    "tool_kind": _redact(meta.get("tool_kind", "")),
                    "request_id": _redact(meta.get("approval_id", meta.get("request_id", ""))),
                }
                break
        return {
            "key": self.key,
            "title": _redact(self.title) if self.title else self.title,
            "agent": self.agent,
            "model": self.model,
            "linked_session_key": self.linked_session_key,
            "reasoning_effort": self.reasoning_effort,
            "acp_provider": self.acp_provider,
            "acp_provider_agent": self.acp_provider_agent,
            "mode": self.mode,
            "workspace_dir": self.workspace_dir,
            "project_id": self.project_id,
            "messages": len(self.messages),
            "running": self.running,
            "stopping": self._stopping,
            "pending_approval": pending_approval,
            "pending_approval_info": pending_approval_info,
            "last_activity_ts": last_activity_ts,
            "waiting_for_input": waiting_for_input,
            "stop_state": self._stop_state,
            "created": self.created_at,
            "last_ts": last_ts,
            "last_message": last_msg,
            "has_options": has_options,
            "options": [_redact(o) for o in options],
            "prompt_preview": prompt_preview,
            "trust": self._trust,
            "trust_reads": self._trust_reads,
            "task_mode": self._task_mode,
            "channel_linked": self._channel_linked,
            "channel_id": self._channel_id,
            "channel_thread_ts": self._channel_thread_ts,
            "folder_id": self.folder_id,
            "pinned": self.pinned,
            "tags": list(self.tags),
            "color_index": self.color_index,
            "color_theme": self.color_theme,
            "memory_mode": self.memory_mode,
            "forked_from": self.forked_from,
            # Owning-app tag. Non-empty for hidden worker sessions (e.g.
            # autonomous goal loops); the chat sidebar filters these out so they
            # never appear as user conversations.
            "app": self._app,
        }


class DashboardState:
    """Shared state injected into all handlers via ``app["state"]``."""

    def __init__(
        self,
        sessions: "SessionManager",
        crons: "ScheduleService",
        lessons: "LessonStore",
        start_time: float,
        subagents: "SubagentManager | None" = None,
        context_builder: "ContextBuilder | None" = None,
        conversation_log: "ConversationLog | None" = None,
        consolidator: "HistoryConsolidator | None" = None,
        owner_id: str = "",
    ):
        self.sessions = sessions
        self.crons = crons
        self.lessons = lessons
        self.start_time = start_time
        self.subagents = subagents
        self._inbox_state: Any = None
        self._inbox_store: Any = None
        self._inbox_svc: Any = None
        self._inbox_restart: Any = None
        self.context_builder = context_builder
        self.conversation_log = conversation_log
        self.consolidator = consolidator
        # The active channel's ChannelDelivery (set by the channel transport at
        # start_inbound), or None when no messaging channel is configured. This is
        # the ONLY outbound-channel handle core holds — all channel delivery
        # (text/attachments/streaming/identity lookups) goes through this
        # provider-agnostic seam; core never touches a vendor client (no vendor import).
        self.channel_delivery: Any = None
        self.owner_id = owner_id
        self._owner_hash: str | None = None
        self.messages_received = 0
        # Per-resource SSE: one hub per goal loop (key ``loop:<id>``). The loop
        # watchdog publishes lifecycle events here; the per-loop /stream endpoint
        # serves them. Single source of truth on the state so producer (watchdog)
        # and consumer (handler) share the same hubs.
        # (Always-on dashboard state rides the WebSocket — see _broadcast.)
        # Per-loop lifecycle SSE (key ``loop:<id>``) — the unified watchdog publishes
        # every kind's stage/finding/lifecycle events here; /api/loops/{id}/stream
        # serves it. (The Code feature is the `code` kind on this ONE registry — the
        # old per-project _code_sse was orphaned at the unification cutover.)
        self._loop_sse = SseRegistry()
        # Per-item knowledge ingestion progress (key ``knowledge:ingest:<item_id>``).
        # The ingest queue publishes node-graph progress here; the per-item /stream
        # endpoint serves it. (#30)
        self._knowledge_ingest_sse = SseRegistry()
        self._knowledge_ingest_queue: Any = None  # lazy KnowledgeIngestQueue
        self._knowledge_provider: Any = None  # lazy NativeKnowledgeProvider
        # Config-tree FS watcher → live UI refresh (key ``fs:config``, #44).
        self._config_fs_sse = SseRegistry()
        self._config_fs_watcher: Any = None  # lazy ConfigFsWatcher
        # Bundled-model downloads run as background jobs and stream progress over
        # their own per-job SSE hubs (key ``download:<id>``). Held on the state so
        # the start/stream/cancel handlers share one registry across requests.
        self._model_downloads: Any = None  # lazy ModelDownloadRegistry
        self._embedding_reindex: Any = None  # lazy ReindexRegistry
        # Magic re-tag batch job (chat_retag) — one at a time; job retained
        # after completion so a re-attaching client sees the terminal state.
        self._retag_job: Any = None  # RetagJob | None
        self._retag_task: asyncio.Task | None = None  # type: ignore[type-arg]
        self._notification_log: list[dict[str, Any]] = _load_notifications()
        self._sessions: dict[str, _ChatSession] = {}
        self._channel_to_session: dict[str, str] = {}  # channel session_key → session name
        self._session_counter = 0
        self._folders: list[dict[str, Any]] = []  # project folder definitions
        # Tag vocabulary: list of {id, name, color, order}. User-managed.
        self._tags: list[dict[str, Any]] = []
        # Sidebar columns — flat list of {id, name, tag_ids, mode, order, include_untagged}
        self._tag_boards: list[dict[str, Any]] = []
        self._background_tasks: set[asyncio.Task] = set()  # type: ignore[type-arg]
        # YOLO / auto-approve is process-global trust state owned by
        # personalclaw.trust_mode (single source of truth). The state object
        # delegates to it and registers a callback to clear per-session approval
        # policies when YOLO turns off. See enable_yolo / is_yolo_active below.
        from personalclaw import trust_mode as _trust

        _trust.register_on_disable(self._on_yolo_disabled)
        self.no_crons: bool = False  # --no-crons flag: cron execution disabled
        self._hook_store: Any = None  # Lazy-init ScriptHookStore
        # Task refine state (background LLM spec generation)
        self._refine_status: str = "idle"  # idle, running, done, error, cancelled
        self._refine_text: str = ""
        self._refine_error: str = ""
        self._terminal_sessions: dict[str, Any] = {}  # PTY sessions for CLI panel
        self._terminal_reaper: asyncio.Task | None = None  # type: ignore[type-arg]
        self._sel_prune_task: asyncio.Task | None = None  # type: ignore[type-arg]

        # Knowledge Library
        self._knowledge_store: "KnowledgeStore | None" = None  # Lazy-initialized on first access
        self._refine_input: str = ""
        self._refine_task: asyncio.Task | None = None  # type: ignore[type-arg]
        self._refine_session_key: str = ""
        self._refine_answer_future: asyncio.Future | None = None  # type: ignore[type-arg]
        # WebSocket clients (multiplexed real-time connection)
        self._ws_clients: list[web.WebSocketResponse] = []
        # Per-connection app identity for untrusted-app scoping (sandbox P1): a WS
        # opened by an app's SDK (owner cookie + ?app_token=) records the app name
        # here; broadcast_ws then delivers only the events the app's manifest
        # declares (permissions.events). An owner/dashboard connection is absent from
        # this map and receives the full event stream.
        self._ws_app: dict[web.WebSocketResponse, str] = {}
        self._ws_log_subscribers: set[web.WebSocketResponse] = set()
        self._ws_subagent_subscribers: set[web.WebSocketResponse] = set()
        # The gateway's event loop, captured when the first WS client registers.
        # broadcast_ws is invoked from BOTH the loop (chat runner) and off-loop
        # threads (MCP tool subprocess callbacks, subagent/cron announce paths);
        # off-loop callers can't asyncio.ensure_future, which silently dropped the
        # frame. We schedule sends onto this captured loop instead (see _send_ws_all).
        self._ws_loop: asyncio.AbstractEventLoop | None = None
        # Pending tool approvals: id → asyncio.Future[bool]
        self._pending_approvals: dict[str, dict] = {}
        self._approval_futures: dict[str, asyncio.Future] = {}  # type: ignore[type-arg]
        self._flush_task: asyncio.Task | None = None  # type: ignore[type-arg]
        self._upload_sweep_task: asyncio.Task | None = None  # type: ignore[type-arg]
        self._engagement_store: "EngagementStore | None" = None  # lazily built (inbox ranking)
        # Update progress tracking (shared across all connected clients)
        self._update_progress: dict[str, str] | None = None  # {step, detail}
        # Restricted (incognito/temporary): session keys with memory writes disabled
        self._restricted_keys: set[str] = set()
        # Ephemeral: session keys with no memory writes at all
        self._ephemeral_keys: set[str] = set()
        # Per-project file index registry (shared across sessions)
        from personalclaw.dashboard.file_index import FileIndexRegistry

        self.file_indexes = FileIndexRegistry()

    def wire_session_compact_callback(self) -> None:
        """Register the dashboard's compaction callback on the session manager."""

        async def _on_compacted(session_key: str, pct: float) -> None:
            if not session_key.startswith("dashboard:"):
                return
            session_name = session_key[len("dashboard:") :]
            session = self.get_session(session_name)
            if session is None:
                return
            message = _AUTO_COMPACT_NOTICE.format(pct=pct)
            try:
                session.append("assistant", message, "msg msg-a")
            except Exception:
                logging.getLogger(__name__).exception(
                    "Failed to append compact notice to session %s", session_name
                )
            try:
                self.broadcast_ws("context_usage", {"session": session_name, "pct": 0.0})
            except Exception:
                logging.getLogger(__name__).exception(
                    "Failed to broadcast context_usage for session %s", session_name
                )

        self.sessions.set_compact_callback(_on_compacted)

    def status_snapshot(self, *, update_available: bool = False) -> dict[str, Any]:
        """Core status fields served by GET /api/status."""
        uptime = int(time.time() - self.start_time)
        return {
            "uptime": _fmt_duration(uptime),
            "start_time": self.start_time,
            "sessions": self.sessions.count,
            "messages": self.messages_received,
            "cron_jobs": len(self.crons.list_jobs()),
            "lessons": len(self.lessons.load_all()),
            "subagents": self.subagents.count if self.subagents else 0,
            "update_available": update_available,
            "no_crons": self.no_crons,
        }

    _APPROVAL_TIMEOUT = 7200  # 2 hours — interactive default (a human is present)
    # Unattended origins (cron / loop / heartbeat / scheduled) have no human to
    # answer a prompt, so a long wait just hangs the run. They fail CLOSED to
    # deny after a short window. Keyed by a substring of the approval `source`.
    _UNATTENDED_APPROVAL_TIMEOUT = 300  # 5 minutes
    _UNATTENDED_SOURCE_MARKERS = ("cron", "loop", "heartbeat", "schedule", "autonudge")
    _FLUSH_INTERVAL = 5  # seconds between dirty-session flushes

    _log = logging.getLogger(__name__)

    @property
    def knowledge_store(self):  # type: ignore[override]
        """Lazy-init KnowledgeStore on first access."""
        if self._knowledge_store is None:
            db_dir = os.path.join(str(config_dir()), "workspace", "knowledge")
            os.makedirs(db_dir, exist_ok=True)
            self._knowledge_store = KnowledgeStore(os.path.join(db_dir, "knowledge.db"))
        return self._knowledge_store

    _YOLO_TTL = trust_mode.YOLO_DASHBOARD_TTL_SECS  # 6h dashboard ceiling

    def enable_yolo(self, *, from_config: bool = False) -> None:
        """Enable YOLO mode — auto-approve all tools globally.

        Delegates to the canonical :mod:`personalclaw.trust_mode`. When
        *from_config* is True the mode is permanent (no TTL) and cannot be
        downgraded by the dashboard toggle.
        """
        trust_mode.enable_yolo(ttl_secs=self._YOLO_TTL, from_config=from_config)

    def disable_yolo(self) -> None:
        """Turn off YOLO mode. Per-session trust is untouched."""
        trust_mode.disable_yolo()

    def _on_yolo_disabled(self, reason: str) -> None:
        """trust_mode callback: audit + clear untrusted per-session policies.

        Fires whenever YOLO turns off (manual toggle or TTL expiry) so a lapsed
        override no longer leaves auto-approve policies on untrusted sessions.
        """
        if reason == "expired":
            try:
                from personalclaw.sel import sel

                sel().log_api_access(
                    caller="dashboard:yolo_ttl",
                    operation="mode_change:yolo_expired",
                    outcome="disabled",
                    resources=",".join(s.key for s in self._sessions.values()),
                )
            except Exception:
                self._log.warning("SEL audit failed for YOLO expiry", exc_info=True)
        for session in self._sessions.values():
            if not session._trust and not session._trust_reads:
                self.sessions.set_approval_policy(f"dashboard:{session.key}", "")

    def is_yolo_active(self) -> bool:
        """Return whether YOLO mode is currently active, auto-expiring after TTL."""
        return trust_mode.is_yolo_active()

    def yolo_remaining_secs(self) -> float | None:
        """Seconds until YOLO auto-expires, or None if inactive/permanent.

        Surfaced in status so the UI can warn the user the override is about to
        lapse (and that it will require re-authorization). Config-driven YOLO is
        permanent → None.
        """
        return trust_mode.yolo_remaining_secs()

    def _approval_timeout_for(self, source: str) -> float:
        """Resolve the response window for an approval by its origin.

        Unattended origins (no human at the keyboard) get a short window and fail
        closed to deny on expiry, so an autonomous run can't hang for hours on a
        prompt nobody will answer; interactive origins keep the long window.
        """
        low = (source or "").lower()
        if any(marker in low for marker in self._UNATTENDED_SOURCE_MARKERS):
            return self._UNATTENDED_APPROVAL_TIMEOUT
        return self._APPROVAL_TIMEOUT

    async def request_approval(
        self,
        approval_id: str,
        source: str,
        tool: str,
        *,
        tool_input: str = "",
        tool_purpose: str = "",
        session: str = "",
    ) -> bool:
        """Request interactive approval. Returns True if approved, False if rejected/timeout.

        The timeout is origin-aware (see :meth:`_approval_timeout_for`): unattended
        sources deny fast, interactive sources wait longer. Timeout always fails
        closed to deny.
        """
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[bool] = loop.create_future()
        self._approval_futures[approval_id] = fut

        # Sanitize LLM-sourced fields before broadcasting to dashboard clients
        safe_tool, _ = redact_exfiltration_urls(tool)
        safe_tool, _ = redact_credentials(safe_tool)
        safe_input, _ = redact_exfiltration_urls(tool_input)
        safe_input, _ = redact_credentials(safe_input)
        safe_purpose, _ = redact_exfiltration_urls(tool_purpose)
        safe_purpose, _ = redact_credentials(safe_purpose)

        self._pending_approvals[approval_id] = {
            "id": approval_id,
            "source": source,
            "tool": safe_tool,
            "tool_input": safe_input,
            "tool_purpose": safe_purpose,
            "session": session,
            "ts": time.time(),
        }
        self.broadcast_ws("approval", self._pending_approvals[approval_id])
        timeout = self._approval_timeout_for(source)
        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            # Fail closed: an unanswered prompt denies. Audit unattended timeouts
            # so a silently-denied autonomous action is traceable.
            try:
                from personalclaw.sel import sel

                sel().log_api_access(
                    caller=f"approval_timeout:{source}",
                    operation="approval_timeout:denied",
                    outcome="denied",
                    resources=f"tool={safe_tool[:80]} after={int(timeout)}s",
                )
            except Exception:
                self._log.debug("SEL audit failed for approval timeout", exc_info=True)
            return False
        finally:
            self._pending_approvals.pop(approval_id, None)
            self._approval_futures.pop(approval_id, None)

    def _audit_and_broadcast_approval(
        self, session_key: str, approval_id: str, approved: bool
    ) -> None:
        """Emit SEL audit event and broadcast WS notification for an approval decision."""
        try:
            sel().log_tool_invocation(
                session_key=session_key,
                tool_name="approval_decision",
                outcome="approved" if approved else "rejected",
                request_id=approval_id,
                source="dashboard",
            )
        except Exception:
            self._log.warning("SEL audit failed for approval resolution", exc_info=True)
        try:
            self.broadcast_ws("approval_resolved", {"id": approval_id, "approved": approved})
        except Exception:
            self._log.warning("WS broadcast failed for approval resolution", exc_info=True)

    def resolve_approval(self, approval_id: str, approved: bool) -> bool:
        """Resolve a pending approval. Returns False if not found.

        State-level futures receive ``bool`` (consumed by gateway, which converts to str).
        Session-level futures receive ``str`` ("approved"/"rejected", consumed by channel.py).
        """
        decision = "approved" if approved else "rejected"
        fut = self._approval_futures.get(approval_id)
        if fut and not fut.done():
            fut.set_result(approved)
            self._audit_and_broadcast_approval("state", approval_id, approved)
            return True
        # Also check session-level approval futures (chat tool approvals)
        for session in self._sessions.values():
            fut = session._approval_futures.get(approval_id)
            if fut and not fut.done():
                fut.set_result(decision)
                _mark_permission_resolved(session.messages, approval_id, decision)
                self._audit_and_broadcast_approval(session.key, approval_id, approved)
                self.push_sessions_update()
                return True
        return False

    def start_flush_loop(self) -> None:
        """Start background loop that flushes dirty sessions to disk every 5s."""
        if self._flush_task is None:
            self._flush_task = asyncio.ensure_future(self._flush_loop())

    async def _flush_loop(self) -> None:
        """Periodically save dirty sessions so a crash loses at most 5s of chat."""
        from personalclaw import shutdown_event

        while not shutdown_event.is_set():
            try:
                await asyncio.wait_for(shutdown_event.wait(), timeout=self._FLUSH_INTERVAL)
                return
            except asyncio.TimeoutError:
                pass
            await asyncio.get_running_loop().run_in_executor(None, self._flush_dirty_sessions)

    def _flush_dirty_sessions(self) -> None:
        """Write any session with new messages to its JSONL file."""
        if not self.conversation_log:
            return
        from personalclaw.dashboard.chat import _save_session_to_history

        for session in list(self._sessions.values()):
            if not session._dirty or not session.messages:
                continue
            try:
                _save_session_to_history(self, session)
                session._dirty = False
            except Exception:
                logger.warning("Flush failed for session %s", session.key, exc_info=True)

    def notify(self, kind: str, title: str, body: str, *, meta: dict | None = None) -> None:
        """Push a notification to ALL connected SSE clients and persist to disk.

        Delivery honors the notification entity settings (Settings →
        Notifications): mute-all, minimum severity, and quiet hours — the
        single gate for every emitter (crons, loops, hooks, inbox alerts,
        heartbeats, app actions). A suppressed notification is dropped
        entirely (not logged unread) — mute means mute.
        """
        from personalclaw.providers.entity_routes import notification_allowed

        try:
            if not notification_allowed(kind):
                logger.debug("Notification suppressed by settings: %s %r", kind, title)
                return
        except Exception:  # never let the prefs gate break delivery
            logger.debug("notification_allowed failed; delivering", exc_info=True)
        note: dict[str, Any] = {
            "kind": kind,
            "title": title,
            "body": body,
            "ts": datetime.now(tz=timezone.utc).isoformat(),
        }
        if meta:
            note.update(meta)
        self._notification_log.append(note)
        self._broadcast(note)
        _persist_notification(note)

    def unread_count(self) -> int:
        """Number of notifications not yet acknowledged (derived, never cached)."""
        return sum(1 for n in self._notification_log if not n.get("acked"))

    def loop_sse(self) -> SseRegistry:
        """The per-loop SSE registry (key ``loop:<id>``) — serves every kind, incl. code."""
        return self._loop_sse

    def knowledge_ingest_sse(self) -> SseRegistry:
        """Per-item knowledge ingestion SSE registry (key ``knowledge:ingest:<id>``)."""
        return self._knowledge_ingest_sse

    def config_fs_sse(self) -> SseRegistry:
        """Config-tree FS-watch SSE registry (key ``fs:config``, #44)."""
        return self._config_fs_sse

    def config_fs_watcher(self):
        """Lazy-init the config-tree FS watcher (#44). Publishes file ``changed``
        events to ``config_fs_sse`` so the UI live-refreshes on out-of-band edits."""
        if self._config_fs_watcher is None:
            from personalclaw.fs_watch import ConfigFsWatcher, default_config_roots

            self._config_fs_watcher = ConfigFsWatcher(
                default_config_roots(),
                publish=self._config_fs_sse.publish,
            )
            try:
                self._config_fs_watcher.start()
            except RuntimeError:
                pass  # no loop yet; the gateway starts it on serve
        return self._config_fs_watcher

    def knowledge_ingest_queue(self):
        """Lazy-init the knowledge ingestion queue (node-graph engine, #30).

        Reuses the per-resource SSE substrate for progress. Started on first access;
        both the native provider (on create) and external sync enqueue into it."""
        if self._knowledge_ingest_queue is None:
            from personalclaw.knowledge.ingest_queue import KnowledgeIngestQueue

            self._knowledge_ingest_queue = KnowledgeIngestQueue(
                self.knowledge_store,
                embedder_factory=_knowledge_embedder_factory,
                insights_pool=None,  # set by the gateway when an LLM pool exists
                sse_registry=self._knowledge_ingest_sse,
            )
            try:
                self._knowledge_ingest_queue.start()
            except RuntimeError:
                # No running loop yet (e.g. accessed at import/test time) — the
                # gateway re-starts it on serve; enqueue still buffers.
                pass
        return self._knowledge_ingest_queue

    def knowledge_provider(self):
        """Lazy-init the native knowledge provider (12 typed-create + enqueue, #30)."""
        if self._knowledge_provider is None:
            from personalclaw.knowledge_providers.native import create_native_provider
            from personalclaw.knowledge_providers.registry import register_provider

            queue = self.knowledge_ingest_queue()
            self._knowledge_provider = create_native_provider(
                self.knowledge_store,
                enqueue=queue.enqueue,
            )
            register_provider(self._knowledge_provider)
        return self._knowledge_provider

    def model_downloads(self) -> Any:
        """The bundled-model download job registry (lazy, per-process singleton)."""
        if self._model_downloads is None:
            from personalclaw.dashboard.model_downloads import ModelDownloadRegistry

            self._model_downloads = ModelDownloadRegistry()
        return self._model_downloads

    def embedding_reindex(self) -> Any:
        """The embedding re-index job registry (lazy, per-process singleton)."""
        if self._embedding_reindex is None:
            from personalclaw.dashboard.embedding_reindex import ReindexRegistry

            self._embedding_reindex = ReindexRegistry()
        return self._embedding_reindex

    def delete_notification(self, ts: str) -> bool:
        """Remove a single notification by timestamp and persist to disk."""
        before = len(self._notification_log)
        self._notification_log = [n for n in self._notification_log if n.get("ts") != ts]
        removed = len(self._notification_log) < before
        if removed:
            _rewrite_notifications(self._notification_log)
            self.broadcast_ws("notification_removed", {"ts": ts})
        return removed

    def delete_notifications_for_loop(self, loop_id: str) -> int:
        """Remove all notifications tagged with ``loop_id`` and persist. Called
        when a goal loop is deleted so its notices don't linger as dead links
        (their 'Open goal' would dead-end on the not-found cockpit). Returns the
        count removed."""
        if not loop_id:
            return 0
        before = len(self._notification_log)
        removed_ts = [
            n.get("ts", "") for n in self._notification_log if n.get("loop_id") == loop_id
        ]
        self._notification_log = [n for n in self._notification_log if n.get("loop_id") != loop_id]
        removed = before - len(self._notification_log)
        if removed:
            _rewrite_notifications(self._notification_log)
            self.broadcast_ws("notification_removed", {"ts": removed_ts})
        return removed

    def ack_notification(self, ts: str) -> bool:
        """Mark a notification as acknowledged and persist."""
        for n in self._notification_log:
            if n.get("ts") == ts:
                n["acked"] = True
                _rewrite_notifications(self._notification_log)
                self.broadcast_ws("notification_ack", {"ts": ts})
                return True
        return False

    def unack_notification(self, ts: str) -> bool:
        """Mark a notification as unread and persist."""
        for n in self._notification_log:
            if n.get("ts") == ts:
                n["acked"] = False
                _rewrite_notifications(self._notification_log)
                self.broadcast_ws("notification_unack", {"ts": ts})
                return True
        return False

    def clear_notifications(self) -> None:
        """Remove all notifications from memory and disk."""
        self._notification_log.clear()
        path = _notifications_path()
        try:
            if path.exists():
                path.write_text("", encoding="utf-8")
        except Exception:
            logger.debug("Failed to clear notifications file", exc_info=True)
        self.broadcast_ws("notification_removed", {"ts": "*"})

    def get_session(self, name: str) -> _ChatSession | None:
        """Look up a session by name without creating it. Returns None if absent."""
        return self._sessions.get(name)

    def get_linked_session(self, session_key: str) -> "_ChatSession | None":
        """Look up a dashboard session linked to a channel thread. Cleans up stale mappings."""
        session_name = self._channel_to_session.get(session_key)
        if not session_name:
            return None
        session = self._sessions.get(session_name)
        if not session or not session._channel_linked or session._channel_thread_ts != session_key:
            self._channel_to_session.pop(session_key, None)
            return None
        return session

    def resolve_session(self, name: str) -> _ChatSession | None:
        """Like :meth:`get_session`, but also resolves bare ``chat-N`` labels.

        Falls back to a prefix match so ``chat-2`` resolves to
        ``chat-2-<timestamp>`` when no exact match exists. The fallback is
        gated to names matching ``chat-\\d+`` to prevent broad-prefix
        collisions (e.g. a bare ``chat`` binding to any ``chat-*`` session).

        Tie-break: when multiple sessions share the same ``chat-N-`` prefix
        (e.g. after a resume creates a second timestamped session), returns
        the first session in dict iteration order. Under normal operation
        that's also the oldest session, but callers should not rely on it
        after ad-hoc removals and re-adds. In practice only one active
        session per chat-N label exists at a time.

        Use this from trusted delivery paths (heartbeat, cron) where the
        caller wants short-label addressing. Do NOT use from HTTP handlers
        that pass the resolved name to key-derivation functions
        (e.g. ``_history_key_for``) — those require the full session key.
        """
        session = self._sessions.get(name)
        if session is not None:
            return session
        if not _CHAT_N_RE.fullmatch(name):
            return None
        prefix = name + "-"
        for key, s in self._sessions.items():
            if key.startswith(prefix):
                return s
        return None

    def link_channel(self, session_name: str, thread_ts: str, channel_id: str) -> None:
        """Update a session's channel link state and persist to SessionStore."""
        session = self._sessions.get(session_name)
        if not session:
            return
        # Remove stale mapping if session was previously linked to a different thread
        old_ts = session._channel_thread_ts
        if old_ts and old_ts != thread_ts:
            self._channel_to_session.pop(old_ts, None)
        # Clear persisted link of old session if this thread was previously owned by another session
        old_owner = self._channel_to_session.get(thread_ts)
        if old_owner and old_owner != session_name:
            old_session = self._sessions.get(old_owner)
            if old_session:
                old_session._channel_linked = False
                old_session._channel_thread_ts = ""
                old_session._channel_id = ""
            if self.sessions:
                from personalclaw.dashboard.chat import _history_key_for

                self.sessions.set_channel_link(_history_key_for(old_owner), "", "")
        session._channel_linked = True
        session._channel_id = channel_id
        session._channel_thread_ts = thread_ts
        self._channel_to_session[thread_ts] = session_name
        # Persist so link survives gateway restarts
        if self.sessions:
            from personalclaw.dashboard.chat import _history_key_for

            self.sessions.set_channel_link(_history_key_for(session_name), thread_ts, channel_id)
        self.push_sessions_update()

    def get_or_create_session(
        self,
        name: str | None = None,
        agent: str = "",
        workspace_dir: str = "",
        model: str = "",
        mode: str = "",
        memory_mode: str | None = None,
        ephemeral: bool | None = None,
        app: str = "",
        project_id: str = "",
    ) -> _ChatSession:
        """Return existing session or create a new one."""
        if name and name in self._sessions:
            existing = self._sessions[name]
            if memory_mode is not None and memory_mode != existing.memory_mode:
                raise ValueError(
                    f"Session {name!r} already exists with memory_mode={existing.memory_mode!r}"
                )
            return existing
        if not name:
            import time

            self._session_counter += 1
            ts = int(time.time())
            name = f"chat-{self._session_counter}-{ts}"
        session = _ChatSession(
            name,
            agent=agent,
            workspace_dir=workspace_dir,
            model=model,
            mode=mode,
            memory_mode=memory_mode or "persistent",
            project_id=project_id,
        )
        session._tab_id = uuid.uuid4().hex[:12]
        session._on_message = self._broadcast_chat_message
        session._app = app
        if memory_mode and memory_mode != "persistent":
            self._restricted_keys.add(f"dashboard:{name}")
        if ephemeral:
            self._ephemeral_keys.add(f"dashboard:{name}")
        # Check if this session is already linked to a channel thread
        try:
            if self.sessions:
                from personalclaw.dashboard.chat import _history_key_for

                _ts, _ch = self.sessions.get_channel_link(_history_key_for(name))
                session._channel_linked = _ts is not None
                if _ts and _ch:
                    session._channel_id = _ch
                    session._channel_thread_ts = _ts
        except Exception:
            pass
        self._sessions[name] = session
        self.push_sessions_update()
        return session

    def _broadcast_chat_message(self, session_name: str, msg: dict) -> None:
        """Push a chat message to all SSE clients via the global stream."""
        payload: dict[str, Any] = {
            "_type": "chat_message",
            "session": session_name,
            "role": msg.get("role", ""),
            "content": msg.get("content", ""),
            "ts": msg.get("ts", ""),
        }
        # Include cls so clients receive the raw class string
        cls_val = msg.get("cls", "")
        if cls_val:
            payload["cls"] = cls_val
            # Parse cls as JSON to send structured meta field for new frontend
            meta = parse_cls_meta(cls_val)
            if meta is not None:
                payload["meta"] = meta
        # Also include direct meta (e.g. tool_call_id on tool messages)
        direct_meta = msg.get("meta")
        if direct_meta and isinstance(direct_meta, dict):
            payload["meta"] = {**(payload.get("meta") or {}), **direct_meta}
        self._broadcast(payload)

    # ── Folder persistence ──

    _FOLDERS_FILE = "folders.json"
    _TAGS_FILE = "tags.json"
    _TAG_BOARDS_FILE = "tag_boards.json"

    # Seed vocabulary created on first run when tags.json is missing or empty.
    # status=True tags are mutually-exclusive workflow states. Drag-between-columns
    # strips all status tags from a card and applies the destination column's
    # status tag. Non-status tags survive the drag.
    _DEFAULT_TAGS: list[dict[str, Any]] = [
        {"id": "planned", "name": "Planned", "color": "#6b7280", "order": 0, "status": True},
        {"id": "todo", "name": "ToDo", "color": "#3b82f6", "order": 1, "status": True},
        {
            "id": "implementation",
            "name": "Implementation",
            "color": "#8b5cf6",
            "order": 2,
            "status": True,
        },
        {"id": "review", "name": "Review", "color": "#f59e0b", "order": 3, "status": True},
        {"id": "done", "name": "Done", "color": "#10b981", "order": 4, "status": True},
    ]

    def load_folders(self) -> None:
        """Load folder definitions from disk."""
        path = config_dir() / self._FOLDERS_FILE
        try:
            if path.exists():
                self._folders = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            logger.warning("Failed to load folders", exc_info=True)

    def save_folders(self) -> None:
        """Persist folder definitions to disk (atomic write)."""
        path = config_dir() / self._FOLDERS_FILE
        self._atomic_write_json(path, self._folders)

    def load_tags(self) -> None:
        """Load tag vocabulary and sidebar columns from disk; seed defaults if missing.

        Only seed when ``tags.json`` does not exist. An explicitly-empty file
        is left as-is (so a user who deletes every tag stays at zero tags
        across restarts), and a parse failure is left untouched (so a
        transient I/O error never silently overwrites saved data).
        """
        tags_path = config_dir() / self._TAGS_FILE
        file_existed = tags_path.exists()
        try:
            if file_existed:
                raw = json.loads(tags_path.read_text(encoding="utf-8"))
                if isinstance(raw, list):
                    self._tags = [t for t in raw if isinstance(t, dict) and t.get("id")]
        except Exception:
            logger.warning("Failed to load tags", exc_info=True)
            # Treat a parse error like a present file: do not re-seed.
            file_existed = True
        if not file_existed and not self._tags:
            # Fresh install (no tags.json on disk) — seed the default vocabulary.
            self._tags = [dict(t) for t in self._DEFAULT_TAGS]
            self.save_tags()

        # Column layout: flat list of {id, name, tag_ids, mode, order}.
        # Empty list = single implicit "all sessions" column.
        columns_path = config_dir() / self._TAG_BOARDS_FILE
        try:
            if columns_path.exists():
                raw = json.loads(columns_path.read_text(encoding="utf-8"))
                if isinstance(raw, list):
                    self._tag_boards = [c for c in raw if isinstance(c, dict) and c.get("id")]
        except Exception:
            logger.warning("Failed to load sidebar columns", exc_info=True)

    def save_tags(self) -> None:
        """Persist tag vocabulary to disk (atomic write)."""
        self._atomic_write_json(config_dir() / self._TAGS_FILE, self._tags)

    def save_tag_boards(self) -> None:
        """Persist sidebar column layout to disk (atomic write)."""
        self._atomic_write_json(config_dir() / self._TAG_BOARDS_FILE, self._tag_boards)

    @staticmethod
    def _atomic_write_json(path: Path, data: Any) -> None:
        """Atomic JSON write used by folder/tag persistence helpers."""
        try:
            atomic_write(path, json.dumps(data), fsync=True)
        except Exception:
            logger.warning("Failed to write %s", path.name, exc_info=True)

    def push_sessions_update(self) -> None:
        """Push current session list to all SSE clients (instant UI update)."""
        yolo_active = self.is_yolo_active()  # expire first if needed
        sessions_data = [s.to_dict() for s in self._sessions.values()]
        self._broadcast(
            {
                "_type": "sessions",
                "_sessions_list": sessions_data,
                "_yolo": yolo_active,
                "sessions": json.dumps(sessions_data),
            }
        )

    def push_session_title(self, key: str, title: str) -> None:
        """Push a targeted title update for a single session.

        Also pushes a full sessions update so the sidebar reflects the new
        title without callers needing to do both.
        """
        self._broadcast({"_type": "session_title", "key": key, "title": title})
        self.push_sessions_update()

    def push_refresh(self, *kinds: str) -> None:
        """Push a lightweight refresh hint for specific data types.

        The frontend receives ``event: refresh`` with ``data: kind1,kind2``
        and fetches fresh data only for those types.  This replaces blind
        polling — the server tells the client *when* to refresh, not the
        client guessing on a timer.

        Supported kinds: ``crons``, ``lessons``, ``agents``, ``history``.
        """
        self._broadcast({"_type": "refresh", "kinds": ",".join(kinds)})

    def push_update_progress(self, step: str, detail: str = "") -> None:
        """Broadcast an update progress event to all connected clients.

        ``step`` is a short machine-readable phase name (e.g. ``pulling``,
        ``installing``, ``building``, ``restarting``, ``error``, ``failed``).
        ``detail`` is an optional human-readable message.
        """
        self._update_progress = {"step": step, "detail": detail}
        self._broadcast(
            {
                "_type": "update_progress",
                "step": step,
                "detail": detail,
            }
        )

    def clear_update_progress(self) -> None:
        """Reset update progress (e.g. after cancel or completion)."""
        self._update_progress = None

    def _broadcast(self, note: dict[str, Any]) -> None:
        """Fan a dashboard state note out to the WebSocket clients.

        The interactive dashboard surface runs on a single multiplexed
        WebSocket (see ``web/src/hooks/useWebSocket.ts``), so the dashboard's
        always-on concerns — status, session list/titles, notifications, and
        refresh hints — ride that one connection. This is the single-transport-
        per-concern doctrine: always-on state on the WS, page-scoped
        feeds (loops/logs/file-watch) on their own per-resource SSE.
        """
        # WS broadcast — translate internal _type to WS message format
        if self._ws_clients:
            msg_type = note.get("_type", "notification")
            if msg_type == "sessions":
                sessions_list = note.get("_sessions_list") or json.loads(note["sessions"])
                ws_msg = json.dumps(
                    {
                        "type": "sessions",
                        "data": sessions_list,
                        "yolo": note.get("_yolo", False),
                        "channelTrusted": note.get("channelTrusted", False),
                    }
                )
            elif msg_type == "session_title":
                ws_msg = json.dumps(
                    {"type": "session_title", "data": {"key": note["key"], "title": note["title"]}}
                )
            elif msg_type == "refresh":
                ws_msg = json.dumps(
                    {"type": "refresh", "data": {"kinds": note["kinds"].split(",")}}
                )
            elif msg_type == "update_progress":
                ws_msg = json.dumps(
                    {
                        "type": "update_progress",
                        "data": {"step": note["step"], "detail": note.get("detail", "")},
                    }
                )
            elif msg_type == "chat_message":
                chat_data: dict[str, Any] = {
                    "session": note["session"],
                    "role": note["role"],
                    "content": note["content"],
                    "ts": note.get("ts", ""),
                }
                # Include cls for messages with metadata (e.g. permission with tool_input)
                if note.get("cls"):
                    chat_data["cls"] = note["cls"]
                if note.get("meta"):
                    chat_data["meta"] = note["meta"]
                ws_msg = json.dumps({"type": "chat_message", "data": chat_data})
            else:
                ws_msg = json.dumps({"type": "notification", "data": note})
            self._send_ws_all(ws_msg)

    def _send_ws_all(self, msg: str) -> None:
        """Send a pre-serialized JSON string to all WS clients.

        Safe to call from ANY thread. On the gateway loop each send is scheduled
        with ensure_future; off-loop (MCP tool subprocess callbacks, subagent/cron
        announce paths) it's submitted to the captured gateway loop via
        run_coroutine_threadsafe. The old code called ensure_future directly, which
        raised off-loop → the send coroutine was dropped unawaited (a RuntimeWarning
        + a silently-lost frame, e.g. subagent lifecycle cards not updating live)."""
        dead: list[web.WebSocketResponse] = []
        for ws in list(self._ws_clients):
            if ws.closed:
                dead.append(ws)
                continue
            try:
                if not self._schedule_ws_send(ws.send_str(msg)):
                    dead.append(ws)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self._remove_ws(ws)

    def _schedule_ws_send(self, coro) -> bool:  # type: ignore[no-untyped-def]
        """Schedule a WS send coroutine on the right loop from any thread.

        Returns False (so the caller can drop the client) only on an actual
        scheduling error. On the gateway loop → ensure_future; off-loop → submit to
        the captured loop with run_coroutine_threadsafe; no loop anywhere (sync
        startup/tests) → close the coroutine cleanly so it isn't reported unawaited."""
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        try:
            if running is not None:
                asyncio.ensure_future(coro)
            elif self._ws_loop is not None and not self._ws_loop.is_closed():
                asyncio.run_coroutine_threadsafe(coro, self._ws_loop)
            else:
                coro.close()  # no loop available — can't deliver; avoid unawaited warning
            return True
        except Exception:
            try:
                coro.close()
            except Exception:
                pass
            return False

    def broadcast_ws(self, msg_type: str, data: object) -> None:
        """Send a typed message to all WS clients (not SSE).

        Owner/dashboard connections get every event. An app-scoped connection
        (sandbox P1) gets an event ONLY if the app's manifest declares it in
        ``permissions.events`` — server-side enforcement so an untrusted app can't
        observe events it didn't ask for (the SDK's client-side filter is advisory)."""
        if not self._ws_clients:
            return
        msg = json.dumps({"type": msg_type, "data": data})
        # Fast path: no app-scoped connections → everyone gets everything.
        if not self._ws_app:
            self._send_ws_all(msg)
            return
        dead: list[web.WebSocketResponse] = []
        for ws in list(self._ws_clients):
            if ws.closed:
                dead.append(ws)
                continue
            app = self._ws_app.get(ws, "")
            if app and not self._app_may_see_event(app, msg_type):
                continue
            try:
                if not self._schedule_ws_send(ws.send_str(msg)):
                    dead.append(ws)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self._remove_ws(ws)

    def _app_may_see_event(self, app: str, event_type: str) -> bool:
        """Whether an app-scoped WS may receive ``event_type`` per its manifest."""
        try:
            from personalclaw.apps.permissions import checker_for

            checker = checker_for(app)
            return checker is not None and checker.can_use_event(event_type)
        except Exception:
            return False

    def register_ws(self, ws: web.WebSocketResponse, *, app: str = "") -> None:
        """Register a new WebSocket client.

        ``app`` scopes the connection to an installed app (sandbox P1): its events
        are filtered to the app's declared ``permissions.events`` in broadcast_ws.
        Empty (the owner/dashboard) receives the full stream."""
        # Capture the gateway loop so off-loop broadcast_ws callers (MCP tool
        # subprocess callbacks, subagent/cron threads) can schedule sends onto it.
        if self._ws_loop is None:
            try:
                self._ws_loop = asyncio.get_running_loop()
            except RuntimeError:
                pass
        self._ws_clients.append(ws)
        if app:
            self._ws_app[ws] = app

    def unregister_ws(self, ws: web.WebSocketResponse) -> None:
        """Remove a WebSocket client on disconnect."""
        self._remove_ws(ws)

    def _remove_ws(self, ws: web.WebSocketResponse) -> None:
        """Remove a WS client from all subscriber lists."""
        try:
            self._ws_clients.remove(ws)
        except ValueError:
            pass
        self._ws_app.pop(ws, None)
        self._ws_log_subscribers.discard(ws)
        self._ws_subagent_subscribers.discard(ws)

    def subscribe_logs(self, ws: web.WebSocketResponse) -> None:
        """Subscribe a WS client to log events."""
        self._ws_log_subscribers.add(ws)

    def unsubscribe_logs(self, ws: web.WebSocketResponse) -> None:
        """Unsubscribe a WS client from log events."""
        self._ws_log_subscribers.discard(ws)

    def subscribe_subagents(self, ws: web.WebSocketResponse) -> None:
        self._ws_subagent_subscribers.add(ws)

    def unsubscribe_subagents(self, ws: web.WebSocketResponse) -> None:
        self._ws_subagent_subscribers.discard(ws)

    def broadcast_ws_subagent_subscribers(self, msg_type: str, data: object) -> None:
        """Send to subagent-subscribed clients only (for heavy chunk data).

        Thread-safe like _send_ws_all: subagent chunk events originate off the
        gateway loop, so each send is scheduled onto the captured loop when needed."""
        if not self._ws_subagent_subscribers:
            return
        msg = json.dumps({"type": msg_type, "data": data})
        dead: list[web.WebSocketResponse] = []
        for ws in list(self._ws_subagent_subscribers):
            if ws.closed:
                dead.append(ws)
                continue
            try:
                if not self._schedule_ws_send(ws.send_str(msg)):
                    dead.append(ws)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self._remove_ws(ws)

    async def close_all_ws(self) -> None:
        """Close all WebSocket connections (called on shutdown)."""
        if self._flush_task:
            self._flush_task.cancel()
            self._flush_task = None
        for ws in list(self._ws_clients):
            try:
                await ws.close()
            except Exception:
                pass
        self._ws_clients.clear()
        self._ws_log_subscribers.clear()
        self._ws_subagent_subscribers.clear()


# ── Notification persistence ──


def _notifications_path() -> Path:
    """Path to the notifications JSONL file."""
    return config_dir() / _NOTIFICATIONS_FILE


def _load_notifications() -> list[dict[str, Any]]:
    """Load persisted notifications from disk (newest last)."""
    path = _notifications_path()
    if not path.exists():
        return []
    try:
        entries: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        # Keep only the most recent N
        return entries[-_MAX_PERSISTED_NOTIFICATIONS:]
    except Exception:
        logger.debug("Failed to load notifications", exc_info=True)
        return []


def _persist_notification(note: dict[str, str]) -> None:
    """Append a single notification to the JSONL file on disk."""
    path = _notifications_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(note) + "\n")
        # Trim if file grows too large (keep last N lines)
        _maybe_trim_notifications(path)
    except Exception:
        logger.debug("Failed to persist notification", exc_info=True)


def _rewrite_notifications(notifications: list[dict[str, str]]) -> None:
    """Rewrite the entire notifications file from the in-memory list."""
    path = _notifications_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = [json.dumps(n) + "\n" for n in notifications[-_MAX_PERSISTED_NOTIFICATIONS:]]
        path.write_text("".join(lines), encoding="utf-8")
    except Exception:
        logger.debug("Failed to rewrite notifications file", exc_info=True)


def _maybe_trim_notifications(path: Path) -> None:
    """Trim the notifications file if it exceeds 2x the max."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
        if len(lines) <= _MAX_PERSISTED_NOTIFICATIONS * 2:
            return
        kept = lines[-_MAX_PERSISTED_NOTIFICATIONS:]
        path.write_text("".join(kept), encoding="utf-8")
    except Exception:
        pass


def _fmt_duration(secs: int) -> str:
    """Format seconds as human-readable duration."""
    h, rem = divmod(secs, 3600)
    m, s = divmod(rem, 60)
    return f"{h}h {m}m" if h > 0 else f"{m}m {s}s"

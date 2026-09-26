"""Dashboard shared state — ChatSession and DashboardState."""

import asyncio
import json
import logging
import os
import re
import time
import traceback
import uuid
from collections.abc import Coroutine, Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from aiohttp import web

from personalclaw import trust_mode
from personalclaw.atomic_write import atomic_write
from personalclaw.config import loader as config_loader
from personalclaw.config.loader import DASHBOARD_PORT
from personalclaw.dashboard.approval_state import DashboardApprovalState
from personalclaw.dashboard.desktop_registry import DesktopRegistry
from personalclaw.dashboard.sse import SseRegistry
from personalclaw.dashboard.ws_state import DashboardWebSocketState
from personalclaw.guardrails.loop_breaker import LoopBreaker
from personalclaw.knowledge.store import KnowledgeStore
from personalclaw.security import redact_credentials, redact_exfiltration_urls
from personalclaw.task_modes import (  # noqa: F401,E501 — re-exported for dashboard callers (chat_runner, tests)
    is_read_only_bash,
    read_only_command,
    resolve_effective_risk,
    shell_command,
    tool_input_to_str,
)


def config_dir() -> Path:
    """The active home, re-resolved per call — see :func:`personalclaw.config.loader.config_dir`.

    DEFINED here rather than imported: this module can be imported lazily, and an
    import-time binding captures whatever the name pointed at on first use (#2443).
    """
    return config_loader.config_dir()


if TYPE_CHECKING:
    from personalclaw.dashboard._types import (  # noqa: F401
        ContextBuilder,
        ConversationLog,
        HistoryConsolidator,
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


# ── Constants ──


_DEFAULT_PORT = DASHBOARD_PORT
_SSE_INTERVAL_SECS = 5
_NOTIFICATIONS_FILE = "notifications.jsonl"
_MAX_PERSISTED_NOTIFICATIONS = 200
_AUTO_COMPACT_NOTICE = "🔄 Auto-compacted at {pct:.0f}%."

# Bare chat-N label matcher used by DashboardState.resolve_session() for prefix fallback.
# Gates the prefix lookup to prevent broad matches (e.g. bare "chat" binding to any session).
_CHAT_N_RE = re.compile(r"chat-\d+")

# Cron notification wrapper format — used by handlers.py (create), chat.py (detect), ChatPage.tsx (render)  # noqa: E501
CRON_NOTIFY_PREFIX = "[Cron notification from "
CRON_NOTIFY_END = "[End of cron notification]"
CRON_NOTIFY_RE = re.compile(rf'^{re.escape(CRON_NOTIFY_PREFIX)}"(.*)"\]')
SUBAGENT_COMPLETION_PREFIX = "[Subagent completion event]"

# The ``[OPTIONS: …]`` mechanism is retired as a SUGGESTION surface (chat renders
# `chat_followups` chips instead), but sessions persisted before the retirement still
# carry the marker. This pattern stays so the Board keeps stripping it out of
# `prompt_preview` rather than showing a raw tag.
_OPTIONS_RE = re.compile(r"\[OPTIONS:\s*([^\]]+)\]")

#: The meta-line key a conversation's creating app is persisted under
#: (``_ChatSession.created_by_app``). Written by ``chat_persistence.save_session_to_history`` and
#: read back by :meth:`DashboardState.session_creating_app` and the session-creation chokepoint.
CREATED_BY_APP_META_KEY = "created_by_app"


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
        "_acp_meta_binding",
        "acp_mode",
        "mode",
        "workspace_dir",
        "project_id",
        "created_at",
        "messages",
        "_stream",
        "task",
        "event",
        "_pending",
        "_queue",
        "_approval_futures",
        "_trust",
        "_trust_reads",
        "_agent_floor_seeded",
        "_task_mode",
        "_investigate_ctx",
        "_suppress_autonudge_rearm",
        "_titled",
        "_resumed_count",
        "_on_message",
        "_has_reader",
        "_stop_state",
        "_stop_event_id",
        "_dirty",
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
        "lifecycle",
        "last_activity_at",
        "never_archive",
        "_pending_subagent_failures",
        "_recovery_retrigger_count",
        "_prompt_busy_retries",
        "_acp_pipe_death_retries",
        "_empty_response_retries",
        "_batch_rejected",
        "color_index",
        "color_theme",
        "natural_voice",
        "memory_mode",
        "_ephemeral",
        "_pending_context",
        "_app",
        "created_by_app",
        "_last_turn_errored",
        "_followups_task",
        "_pending_variants",
        "_lock",
        "forked_from",
        "_fork_lock",
        "_tab_id",
        "_disk_older_count",
        "_file_changes",
        "_declared_file_change_idx",
        "_acp_breaker",
        "_memory_citations",
        "_skills_used",
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
        # What the session's PERSISTED meta line asked its runtime to be, recorded on
        # restore whether or not the binding was honoured, and consumed by the first
        # turn after a restore. A lost ACP binding is not a cosmetic loss: the turn
        # then runs on the native axis with different tools and different confinement
        # while looking completely normal, so the turn says so instead (G5). Empty
        # once consumed, and cleared outright when the user picks a runtime by hand —
        # an explicit choice is not a silent fallback.
        self._acp_meta_binding: str = ""
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
        # The transcript: one entry per thing the user wrote or saw, and nothing else.
        # It is never trimmed — the whole-file save rewrites the transcript FROM this
        # list, so an entry missing here is an entry deleted from disk (see
        # `chat_persistence._seed_transcript`, which loads the whole file, never a window).
        self.messages: list[dict[str, Any]] = []
        # The answer being streamed right now, while it is still being written: ONE
        # `streaming` entry in `messages`, grown in place by `stream_chunk` and settled
        # into an `assistant` entry by `finish_stream`. None between answers.
        self._stream: dict[str, Any] | None = None
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
        # Investigate Anywhere (plan 60): the staged context envelope (a dict from
        # InvestigateContext.to_dict) consumed + cleared by the FIRST turn's
        # _inject_investigate_context. Transient — never persisted with history.
        self._investigate_ctx: dict | None = None
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
        # Session LIFECYCLE (distinct from history JSONL rotation, which is storage):
        # "active" | "archived". Archiving declutters the list without deleting or
        # de-indexing anything — an archived session stays fully searchable, which is
        # what makes it safe to auto-archive on a timer.
        self.lifecycle: str = "active"
        # Wall-clock of the last real turn, driving the auto-archive rule. 0.0 means
        # "never recorded" (an old session, or one that has not been used since the
        # field landed) and is treated as not-yet-stale rather than instantly archivable.
        self.last_activity_at: float = 0.0
        # User pin against the lifecycle: never auto-archive this one. Independent of
        # `pinned` (sidebar ordering) on purpose — wanting a session at the top and
        # wanting it exempt from cleanup are different intents.
        self.never_archive: bool = False
        self._pending_subagent_failures: list[str] = []
        self._recovery_retrigger_count: int = 0
        self._prompt_busy_retries: int = 0
        self._acp_pipe_death_retries: int = 0
        self._empty_response_retries: int = 0  # consecutive empty turns (silent-retry guard)
        self._batch_rejected: bool = False
        self.color_index: int | None = None
        self.color_theme: str = ""
        # Natural voice (PT-7), per-conversation scope: a TRI-state
        # ("" | "on" | "off"), not a bool. "" means this conversation states
        # nothing and inherits the bound agent's preference; "off" is a deliberate
        # override of an agent that asks for it. Overriding here never edits the
        # agent — see personalclaw/natural_voice.py for the resolution order.
        self.natural_voice: str = ""
        if memory_mode not in VALID_MEMORY_MODES:
            raise ValueError(
                f"invalid memory_mode {memory_mode!r}, must be one of {VALID_MEMORY_MODES}"
            )
        self.memory_mode: str = memory_mode
        self._ephemeral: bool = ephemeral  # Incognito mode: no memory writes
        self._pending_context: list[dict[str, Any]] = []
        # Where the conversation came from, for display and routing: a hidden worker's tag
        # ("loop", "loops"), the channel it arrived on ("slack"), or an app's name. It is NOT who
        # owns it — those tags are ordinary strings an installed app can also be named.
        self._app: str = ""
        # The app whose token started this conversation, or "" for yours. Set only from a
        # VERIFIED app identity (`get_or_create_session(created_by_app=…)`), persisted on the meta
        # line and restored with the session, and the one thing an app's reach into a conversation
        # is decided on (`DashboardState.session_creating_app`).
        self.created_by_app: str = ""
        self._last_turn_errored: bool = False  # set by run_chat on a crashed turn
        # Follow-up chips (CHAT-CRAFT S3): the fire-and-forget background task that
        # suggests next messages after a completed turn; cancelled by the next dispatch.
        self._followups_task: asyncio.Task | None = None  # type: ignore[type-arg]
        # Regenerate feature: variants pending attachment to next finalized assistant message
        self._pending_variants: list[dict] = []
        self._lock = asyncio.Lock()
        self.forked_from: str | None = None  # parent session key if this is a fork
        self._fork_lock: asyncio.Lock = (
            asyncio.Lock()
        )  # serialises concurrent forks on this session
        self._tab_id: str = ""  # permanent tab identity for cross-restart session chaining
        # Messages in OLDER sibling files of this tab (legacy cross-restart chaining), which
        # the conversation shows before this buffer but which this session never writes.
        # Set when the transcript is seeded; the session's own file is always loaded whole.
        self._disk_older_count: int = 0
        # Per-turn file-change accumulator [{path, before, after}], reset at the
        # top of each run_chat and flushed onto the assistant message's meta at turn end.
        self._file_changes: list[dict[str, str]] = []
        # path -> its index in `_file_changes`, for chips a BACKEND declared rather than
        # ones the host inferred (ACP-AGENT-PARITY §2.5). A streaming adapter re-declares
        # the same edit as its arguments fill in, and the flush's earliest-before rule
        # would then pin a partial first declaration; this lets the newest one replace it
        # in place. Reset alongside `_file_changes` — an index into an emptied list is
        # what would overwrite slot 0 of the next turn.
        self._declared_file_change_idx: dict[str, int] = {}
        # The ACP loop breaker, kept for the SESSION rather than the turn
        # (ACP-AGENT-PARITY §2.3, `G155`). `LoopBreaker` defines its own ceiling as
        # "this RUN's total failures" (default 30, `guardrails.loop_breaker`), and for an
        # ACP session the host-side analogue of a native run is its sequence of turns —
        # a fresh breaker per turn reset the counter every turn, so an unattended loop
        # repeating a failing tool for twenty turns could never reach thirty and the
        # circuit rung was unreachable by construction. Per-key streaks persist for the
        # same reason, and `record()` still clears a key on success, so a tool that
        # recovers is not held against the model.
        self._acp_breaker = LoopBreaker()
        # Episodic memory citations [{n, id, preview}] surfaced into THIS turn's prompt
        # (MEMORY-GRAPH-AND-VAULT §5.4). Reset per turn, populated from the assembled
        # context's metadata, and attached to each finalized assistant message's meta so
        # the frontend can turn a `[Memory N]` token into a deep-link to the episode.
        self._memory_citations: list[dict] = []
        # Skills whose content actually reached THIS turn's prompt, as
        # [{name, state, loaded_tokens}] (LEARNING-VISIBILITY T2.1). Reset per turn,
        # populated from the assembled context's `skill_decisions` metadata, and attached
        # to each finalized assistant message's meta so the frontend can show "used N
        # skills" without a second channel. A REFUSED skill is deliberately absent: it was
        # named to the agent but never loaded, so counting it would overstate the turn.
        self._skills_used: list[dict] = []
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
        """Add one transcript entry — something the user wrote or saw.

        Stream bookkeeping never comes through here. A streamed chunk grows the one open
        answer (:meth:`stream_chunk`) and the end-of-turn marker goes to live readers only
        (:meth:`signal_done`), because every entry in ``messages`` is served, counted and
        persisted as a message.
        """
        msg: dict[str, Any] = {
            "role": role,
            "content": content,
            "cls": cls,
            "ts": ts or datetime.now(timezone.utc).isoformat(),
        }
        if meta:
            msg["meta"] = meta
        self.messages.append(msg)
        # `ts` is the live-vs-replay discriminator. A live turn passes no timestamp (it is
        # "now"); history REPLAY passes each message's stored ts.
        self._announce(msg, replay=bool(ts), broadcast=broadcast)

    def _announce(self, msg: dict[str, Any], *, replay: bool, broadcast: bool) -> None:
        """What a new — or newly settled — transcript entry owes the rest of the system."""
        # Stamp real activity for the auto-archive rule. Only user/assistant turns count:
        # system notices are not the user using the chat. Recording here — the one place
        # every entry is announced — covers every producer (web, channel, resume) without
        # touching any of them.
        #
        # A replay must not stamp: rehydrating an archived session replays its transcript
        # through `append`, and stamping would un-archive a chat just by opening it, or by
        # a restart restoring it.
        if msg["role"] in ("user", "assistant") and not replay:
            self.last_activity_at = time.time()
            # A real turn un-archives: using an archived chat is the clearest possible
            # signal that it is active again.
            if self.lifecycle == "archived":
                self.lifecycle = "active"
        self._dirty = True
        self._pending.append(msg)
        self.event.set()
        # Broadcast via global SSE when no HTTP stream reader is active. A user entry is
        # not echoed: the frontend adds it optimistically.
        if broadcast and self._on_message and msg["role"] != "user" and not self._has_reader:
            self._on_message(self.key, msg)  # type: ignore[operator]

    def _stream_index(self) -> int | None:
        """Where the open streaming entry sits in ``messages`` — found by IDENTITY.

        ``None`` when nothing is streaming, and also when the buffer was rebuilt without
        the entry (a ``/clear``, a purge): the stream is then forgotten rather than grown
        in a dict nothing will ever persist. The entry is the last or next-to-last row
        while it streams, so the scan from the end stops at once.
        """
        entry = self._stream
        if entry is not None:
            for i in range(len(self.messages) - 1, -1, -1):
                if self.messages[i] is entry:
                    return i
            self._stream = None
        return None

    @property
    def streaming_text(self) -> str | None:
        """The answer being streamed, as far as it has arrived — ``None`` when none is."""
        idx = self._stream_index()
        return None if idx is None else self.messages[idx]["content"]

    def stream_chunk(self, text: str) -> None:
        """Grow the answer being streamed by *text*.

        A chunk is not a transcript entry. However many chunks an answer arrives in, it
        is ONE ``streaming`` entry until it settles — ten thousand chunks used to be ten
        thousand entries, and the buffer then pushed the user's own prompt out to make
        room for them. A live reader still gets every delta as a ``chunk`` frame on
        ``_pending`` (the HTTP SSE stream and the OpenAI dialect each claim
        ``_has_reader`` before the turn starts); the WS path gets ``chat_chunk`` from the
        runner. Without a reader nothing drains ``_pending``, so no frame is queued.
        """
        now = datetime.now(timezone.utc).isoformat()
        idx = self._stream_index()
        if idx is None:
            self._stream = {"role": "streaming", "content": text, "cls": "msg msg-a", "ts": now}
            self.messages.append(self._stream)
        else:
            self.messages[idx]["content"] += text
        self._dirty = True
        if self._has_reader:
            self._pending.append({"role": "chunk", "content": text, "cls": "chunk", "ts": now})
            self.event.set()

    def finish_stream(self, content: str) -> dict[str, Any]:
        """Settle the answer being streamed as an ``assistant`` entry, where it streamed.

        Settled IN PLACE, so a stop card appended while the answer was still arriving
        stays after the prose. With no open stream (none started, or the buffer was
        rebuilt under it) the answer is appended instead. Returns the settled entry.
        """
        idx = self._stream_index()
        self._stream = None
        if idx is None:
            self.append("assistant", content, "msg msg-a")
            return self.messages[-1]
        entry = self.messages[idx]
        entry["role"] = "assistant"
        entry["content"] = content
        entry["ts"] = datetime.now(timezone.utc).isoformat()
        self._announce(entry, replay=False, broadcast=True)
        return entry

    def discard_stream(self) -> None:
        """Drop the answer being streamed without settling it."""
        idx = self._stream_index()
        self._stream = None
        if idx is not None:
            del self.messages[idx]
            self._dirty = True

    def signal_done(self) -> None:
        """Tell a live reader the turn is over.

        Not a transcript entry: it goes to ``_pending`` only, so nothing serves, counts
        or persists it. (As a ``done`` row in ``messages`` it made the detail ``total``
        and the paginated tail count one phantom message per turn.) Queued only for a
        reader that is attached — a marker left in an undrained queue would end the NEXT
        reader's stream before its turn began.
        """
        if self._has_reader:
            now = datetime.now(timezone.utc).isoformat()
            self._pending.append({"role": "done", "content": "", "cls": "done", "ts": now})
            self.event.set()

    def drain(self) -> list[dict[str, str]]:
        """Return and clear pending messages."""
        out = self._pending[:]
        self._pending.clear()
        self.event.clear()
        return out

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

    @property
    def message_count(self) -> int:
        """How many messages the open conversation serves.

        The persisted head older than this buffer (``_disk_older_count``) plus every entry
        in it — the buffer holds transcript entries only, and the detail view serves each
        one once. The chat list reads this, so the sidebar and the open conversation cannot
        disagree about one session (#2862).
        """
        return self._disk_older_count + len(self.messages)

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
            "messages": self.message_count,
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
            "lifecycle": self.lifecycle,
            "last_activity_at": self.last_activity_at,
            "never_archive": self.never_archive,
            "color_index": self.color_index,
            "color_theme": self.color_theme,
            # The per-conversation tri-state only. The RESOLVED pair
            # (natural_voice_effective / natural_voice_source) is added by the
            # session-detail and natural-voice handlers, not here: resolving needs
            # the bound agent's definition, and this method runs once per row of
            # the session list — one uncached AppConfig.load() per row.
            "natural_voice": self.natural_voice,
            "memory_mode": self.memory_mode,
            "forked_from": self.forked_from,
            # Owning-app tag. Non-empty for hidden worker sessions (e.g.
            # autonomous goal loops); the chat sidebar filters these out so they
            # never appear as user conversations.
            "app": self._app,
        }


class DashboardState(DashboardWebSocketState, DashboardApprovalState):
    """Shared state injected into all handlers via ``app["state"]``."""

    def __init__(
        self,
        sessions: "SessionManager",
        start_time: float,
        subagents: "SubagentManager | None" = None,
        context_builder: "ContextBuilder | None" = None,
        conversation_log: "ConversationLog | None" = None,
        consolidator: "HistoryConsolidator | None" = None,
        owner_id: str = "",
    ):
        self.sessions = sessions
        self.start_time = start_time
        self.subagents = subagents
        self._inbox_state: Any = None
        self._inbox_store: Any = None
        self._inbox_svc: Any = None
        self._inbox_restart: Any = None
        self.context_builder = context_builder
        self.conversation_log = conversation_log
        self.consolidator = consolidator
        # `channel_delivery` is a PROPERTY over `channel_delivery`'s per-provider registry (see
        # below), not a slot. It was a slot here AND a second one on the gateway orchestrator —
        # two holders for one fact, and every shipped transport wrote both, so one overwrite took
        # out delivery on two unrelated paths at once (#959).
        self.owner_id = owner_id
        self._owner_hash: str | None = None
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
        # Per-run workflow engine SSE (key ``workflow:<run_id>``) — the run controller
        # publishes node/run lifecycle events here; the per-run /stream endpoint serves
        # it. Deliberately NOT routed through ``notify``: that is the user-notification
        # gate (mute / severity / quiet-hours) and would silently eat engine events.
        self._workflow_sse = SseRegistry()
        # The workflow supervisor (`WorkflowWatchdog`), attached at gateway boot. Declared
        # here rather than set as a bare dynamic attribute so a reader can see it exists and
        # that None is a real state — the REST handlers must tolerate a gateway whose
        # workflows feature is disabled.
        self.workflows: Any = None
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
        # DESKTOP-CAPABILITIES DC-2: the Electron shell's pushed capability
        # manifest + its per-session token. Empty (and honestly "not connected")
        # whenever the gateway is serving a plain browser tab.
        self.desktop = DesktopRegistry()
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
        # here; every send path then delivers only the events the app's manifest
        # declares (permissions.events), via `_ws_may_receive`. An owner/dashboard
        # connection is absent from this map and receives the full event stream.
        self._ws_app: dict[web.WebSocketResponse, str] = {}
        self._ws_log_subscribers: set[web.WebSocketResponse] = set()
        self._ws_subagent_subscribers: set[web.WebSocketResponse] = set()
        # The streamed-chunk stamp (`next_stream_seq`): the resume watermark a session
        # detail reports and every chat_chunk carries. Based at the boot time in
        # microseconds so it never restarts across a gateway restart (see there).
        self._stream_seq = time.time_ns() // 1_000
        # The gateway's event loop, captured when the first WS client registers.
        # broadcast_ws is invoked from BOTH the loop (chat runner) and off-loop
        # threads (MCP tool subprocess callbacks, subagent/cron announce paths);
        # off-loop callers can't asyncio.ensure_future, which silently dropped the
        # frame. We schedule sends onto this captured loop instead (see _dispatch_ws).
        self._ws_loop: asyncio.AbstractEventLoop | None = None
        # Pending tool approvals: id → asyncio.Future[bool]
        self._pending_approvals: dict[str, dict] = {}
        self._approval_futures: dict[str, asyncio.Future] = {}  # type: ignore[type-arg]
        # A stopped turn's pending approvals are over (see `cancel_turn_approvals`). The
        # SessionManager owns the stop verb and cannot import upward to this object, so it is
        # handed the one callback it needs — the same capability-shaped registration the
        # subagent manager makes for its children. Guarded for a stub SessionManager in a test.
        if hasattr(sessions, "register_turn_stop_hook"):
            sessions.register_turn_stop_hook(self.cancel_turn_approvals)
        self._flush_task: asyncio.Task | None = None  # type: ignore[type-arg]
        self._upload_sweep_task: asyncio.Task | None = None  # type: ignore[type-arg]
        # Scheduled-backup service (DURABILITY-AND-SYNC §3); held to prevent GC.
        self._durability_svc: object | None = None
        # Watched-source poll engine (WATCHED-SOURCES §1.2); held to prevent GC.
        self._source_engine: object | None = None
        # Artifact→knowledge mirror (PRODUCT-EXPERIENCE-PARITY §6). Held for the same reason
        # AND one more: it owns the subscription in `artifacts.changes`, so dropping the
        # reference would leave the listener registered against a collected indexer.
        self._artifact_indexer: object | None = None
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
        # MULTIMODAL-IO §4.2 — the last text spoken per session, so a hands-free
        # transcription can be recognized as the speaker bleeding back into the
        # microphone. Bounded and in-memory only: this is echo-suppression
        # scratch, never history, and must not survive a restart.
        self._last_spoken: dict[str, str] = {}

    # ── the outbound channel seam ────────────────────────────────────────────────────────
    #
    # A property pair over `channel_delivery`'s per-provider registry, so this state and the
    # gateway orchestrator resolve ONE set of handles instead of holding two slots that drifted.
    #
    # The SETTER is what keeps the shipped channel apps working unchanged: every transport does
    # `services.dashboard_state.channel_delivery = delivery` (alongside its
    # `register_channel_delivery` call), so that assignment is now a registration keyed by the
    # handle's own provider rather than an overwrite of a shared slot. Assigning None clears
    # every handle, which is what it always meant here.

    @property
    def channel_delivery(self) -> Any:
        """A connected channel that can reach the owner, or None.

        Deliberately the OWNER-REACHABLE pick, matching what the readers of this attribute ask:
        "is any channel connected, and can it take a DM / list its reply targets". A REPLY to an
        incoming message must not resolve here — it carries the origin channel's id and is
        answerable by exactly one provider, which is :meth:`delivery_for`. That distinction is
        the whole of #959: this attribute returning "whatever registered last" is how a Discord
        answer was handed to Telegram with a Discord channel id.
        """
        from personalclaw.channel_delivery import owner_reachable

        return owner_reachable()

    @channel_delivery.setter
    def channel_delivery(self, delivery: Any) -> None:
        from personalclaw.channel_delivery import register

        register(delivery)

    def delivery_for(self, provider: str) -> Any:
        """The handle for one provider, or None when that channel is not connected.

        The resolver for a reply: None means DO NOT SEND. Falling back to another provider is
        not a degraded delivery, it is a message posted to the wrong place.
        """
        from personalclaw.channel_delivery import delivery_for

        return delivery_for(provider)

    def channel_provider_for(self, session_key: str) -> str:
        """Which channel a session's messages came FROM, or "" for a dashboard session.

        Stamped at session creation by the one inbound door
        (`channel_inbound._route_to_session` → `get_or_create_session(app=provider)`), which is
        also the code that already knows the provider — it just had nowhere to put it that the
        outbound side could read.
        """
        # Local import, as every other `_history_key_for` use in this module does (it lives in
        # `dashboard.chat`, which imports this module).
        from personalclaw.dashboard.chat import _history_key_for

        session = self._sessions.get(session_key) or self._sessions.get(
            _history_key_for(session_key)
        )
        return str(getattr(session, "_app", "") or "") if session is not None else ""

    _LAST_SPOKEN_MAX_SESSIONS = 32
    _LAST_SPOKEN_MAX_CHARS = 4000

    def record_spoken(self, session_key: str, text: str) -> None:
        """Remember what was just synthesized for ``session_key`` (latest wins)."""

        key = str(session_key or "")
        if not isinstance(text, str) or not text.strip():
            return
        self._last_spoken.pop(key, None)
        self._last_spoken[key] = text[-self._LAST_SPOKEN_MAX_CHARS :]
        while len(self._last_spoken) > self._LAST_SPOKEN_MAX_SESSIONS:
            # dicts preserve insertion order — drop the least recently spoken.
            self._last_spoken.pop(next(iter(self._last_spoken)))

    def last_spoken(self, session_key: str) -> str:
        """The last text synthesized for ``session_key``, or ``""``."""

        return self._last_spoken.get(str(session_key or ""), "")

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
                # ``None``, not 0.0: a compaction shrank the window but nothing has
                # re-measured it yet, so the honest chip is absent rather than "0%".
                self.broadcast_ws("context_usage", {"session": session_name, "pct": None})
            except Exception:
                logging.getLogger(__name__).exception(
                    "Failed to broadcast context_usage for session %s", session_name
                )

        self.sessions.set_compact_callback(_on_compacted)

    def trigger_counts(self) -> dict[str, int]:
        """`{total, enabled, broken}` across the unified store (S107).

        One helper, because TWO status surfaces were counting automations off the legacy service and
        both went blind at the S100/S101 cutover — `GET /api/status`'s `cron` block and the
        `cron_jobs` metric the dashboard's SystemHealth widget renders as "triggers". Measured
        against a home with three valid store triggers (two enabled): both reported 0.

        The legacy service is folded in by id so a home mid-migration counts each automation once.
        Never raises: a status surface that 500s because a store row is malformed is worse than one
        reporting zeros, and `broken` is what makes a malformed row visible anyway.
        """
        from personalclaw.triggers import schedule_view as SV
        from personalclaw.triggers.store import TriggerStore

        try:
            # No `legacy=` fold-in any more (S112). Proven redundant: since S110 the boot migration
            # imports EVERY legacy row — including the ones the conversion refuses, written disabled
            # — so `counts(store)` and `counts(store, legacy=svc)` returned identical results.
            return SV.counts(TriggerStore(base_dir=config_dir()))
        except Exception:  # noqa: BLE001 - status must render even with an unusable store
            logger.debug("trigger counts unavailable", exc_info=True)
            return {"total": 0, "enabled": 0, "broken": 0}

    def _lessons_count(self) -> int:
        """Count of lessons in memory.db ``lesson.*`` (the sole lesson store).

        Reads through the memory service over the context builder's store; returns
        0 when no store is wired (e.g. a bare test state)."""
        cb = self.context_builder
        if cb is None:
            return 0
        try:
            from typing import cast

            from personalclaw.memory_providers.base import MemoryProvider
            from personalclaw.memory_service import service_for

            # A markdown MemoryStore is accepted at runtime (service_for duck-types
            # its markdown/FTS surface); the cast satisfies the strict annotation.
            return len(service_for(cast("MemoryProvider", cb.memory)).get_lessons())
        except Exception:
            return 0

    def status_snapshot(self, *, update_available: bool = False) -> dict[str, Any]:
        """Core status fields served by GET /api/status."""
        uptime = int(time.time() - self.start_time)
        return {
            "uptime": _fmt_duration(uptime),
            "start_time": self.start_time,
            "sessions": self.sessions.count,
            # NB: the dashboard's "triggers" rail is NOT sourced here. `cron_jobs` used to be —
            # `trigger_counts()["total"]`, the schedule STORE's count — but that under-counted the
            # rail's own label: the Triggers page counts lifecycle hooks too (issue 773). The rail
            # now reads `triggers` (the unified count), assembled by `api_status` via
            # `handlers.triggers.unified_trigger_count`. The schedule-store count still ships as the
            # richer `cron` block (`trigger_counts()`); a flat `cron_jobs` mirror of `cron["total"]`
            # with no remaining reader is dropped.
            "lessons": self._lessons_count(),
            "subagents": self.subagents.count if self.subagents else 0,
            "update_available": update_available,
            "no_crons": self.no_crons,
        }

    def active_work_snapshot(self) -> dict[str, int]:
        """Count in-flight work a restart/apply would interrupt: running (not-done)
        background subagents + live chat sessions.

        The ONE place "is it safe to restart/apply now?" is answered — reused by the
        manual-restart confirm gate (``/api/system/restart?probe=1``) and the staged
        auto-update gate (``gateway._work_in_flight``, RUM-5) so the two never diverge.
        Lives on ``DashboardState`` (not the HTTP handler) because it reads only this
        object's own ``subagents``/``sessions``, and the gateway must consult it without
        importing the dashboard's HTTP surface.
        """
        running_agents = 0
        subs = getattr(self, "subagents", None)
        if subs is not None:
            try:
                running_agents = sum(1 for a in subs.all_agents if not a.done)
            except Exception:
                running_agents = 0
        try:
            sessions = len(self.sessions._sessions)
        except Exception:
            sessions = 0
        return {"running_agents": running_agents, "sessions": sessions}

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
        """Write any DIRTY session to its JSONL file — messages or metadata.

        The message-count half of this guard (``or not session.messages``) is gone (#2969).
        ``_dirty`` already means "something changed"; the colour and natural-voice handlers
        set only that and nothing else, so on a conversation with no turns yet their writes
        were accepted `200 {"ok": true}` and then dropped. Whether the change was a turn or
        a piece of metadata is ``save_session_to_history``'s question, not this loop's — and
        it answers it, refusing to write an empty buffer over a persisted transcript and
        refusing to mint a file for a pristine tab.
        """
        if not self.conversation_log:
            return
        from personalclaw.dashboard.chat import save_session_to_history

        for session in list(self._sessions.values()):
            if not session._dirty:
                continue
            try:
                save_session_to_history(self, session)
                session._dirty = False
            except Exception:
                logger.warning("Flush failed for session %s", session.key, exc_info=True)

    #: Note fields the PLATFORM decides, which caller-supplied `meta` may not set (issue 423).
    #:
    #: Only the ones the authority-last assignment in `notify` cannot cover on its own:
    #:
    #: * `mode`, `targets`, `source`, `escalated_by`, `badge_only`, `native` — written further down
    #:   under a condition, so a note that took a different branch would leave a caller's value in
    #:   place. `source` in particular names the surface a consumer deep-links to, and the comment
    #:   at its assignment explains why the RULE owns it rather than the emitter.
    #: * `acked` — `notify` never writes it; the ack path (`handlers/messaging.py`) does. A note
    #:   that arrives already acknowledged is one the user never sees.
    #:
    #: `kind`/`title`/`body`/`ts` are deliberately NOT listed: they are assigned unconditionally
    #: after the merge, which protects them structurally and keeps this set from having to grow in
    #: step with the happy path.
    #:
    #: `withheld_reason`/`routed_to` are the addressing VERDICT (`TSE2-5`) — this method's
    #: conclusion about a note, computed from the addressee below, not an input. They ride out
    #: on the note through `GET /api/notifications`, so an emitter that could supply them could
    #: label its own note "already routed to Dana" while it was in fact fired at the local
    #: owner. The `addressee` ITSELF is deliberately NOT reserved: naming who a notification is
    #: for is the emitter's job (see `inbox.emit_attention_item`).
    _RESERVED_NOTE_KEYS: frozenset[str] = frozenset(
        {
            "mode",
            "targets",
            "source",
            "escalated_by",
            "badge_only",
            "native",
            "acked",
            "withheld_reason",
            "routed_to",
        }
    )

    def notify(self, kind: str, title: str, body: str, *, meta: dict | None = None) -> None:
        """Push a notification to ALL connected SSE clients and persist to disk.

        THE single delivery choke point for every emitter (crons, loops, hooks, inbox
        alerts, heartbeats, app actions). Three layers of policy, in this order:

        1. **The global gate** (`notification_posture`) — mute-all, minimum severity,
           quiet hours. Still outermost: mute means mute, whatever a rule says, and a dropped
           notification is dropped entirely, not logged unread. The one gradation is quiet hours
           over an ATTENTION kind, which returns `quiet` and is recorded as a `badge` below
           instead of vanishing — a loop that needed an answer overnight has to leave a trace
           (#341).
        2. **The per-(source, kind) rule** (`notification_rules`) — never / badge /
           immediate / digest, plus conditions that escalate a quieter mode when the text
           matches a keyword or names the operator.
        3. **The addressee** (`notification_addressing`, `TSE2-5`) — WHO the note is for.
           The first two layers answer "should this be delivered" and "how loudly"; neither
           could answer "to whom", so every note went to *this* dashboard by construction.
           A note addressed to somebody else is recorded here and fired nowhere here, and is
           offered to a `type=notification` delivery provider that says it can reach them.

        The three are ordered by whose instruction they carry: the user's own settings, then
        the user's own rules, then the note's own attribution. The addressee sits *inside*
        the rules layer (after `never`, before the delivery modes) because `digest` and
        `badge` are local deliveries too — a foreign note in the morning digest is a foreign
        note fired, one day late.

        With no rules file and no addressee, every registered kind resolves to ``immediate``
        and this behaves exactly as it did before either layer existed — that equivalence is
        the safety property of shipping without a gate, and `test_notification_rules.py` /
        `test_notification_addressing.py` pin the two halves of it.
        """
        from personalclaw import identity
        from personalclaw import notification_addressing as addressing
        from personalclaw import notification_rules as rules
        from personalclaw.providers import entity_routes

        posture = entity_routes.POSTURE_DELIVER
        try:
            posture = entity_routes.notification_posture(kind)
        except Exception:  # never let the prefs gate break delivery
            logger.debug("notification_posture failed; delivering", exc_info=True)
            posture = entity_routes.POSTURE_DELIVER
        if posture == entity_routes.POSTURE_DROP:
            logger.debug("Notification suppressed by settings: %s %r", kind, title)
            return

        # 🔴 THE NOTE OWNS ITS OWN FIELDS (issue 423). This was `note = {kind, title, body, ts}`
        # followed by `note.update(meta)`, so caller-supplied meta merged OVER the four fields the
        # platform had just decided. `kind` is the worst of them: `notification_allowed(kind)` above
        # has already been evaluated on the PARAMETER, so an emitter could pass a gate as one kind
        # and be persisted, broadcast and rule-matched as another. `acked` is next: the ack path
        # (`handlers/messaging.py`) owns it, and a note carrying `acked: True` on arrival is one the
        # user never sees.
        #
        # This matters because `notify` is the choke point for EVERY emitter — its own docstring
        # lists "crons, loops, hooks, inbox alerts, heartbeats, app actions" — and an app bundle is
        # third-party code. Meta is the one part of a note a caller controls.
        #
        # Meta goes in FIRST and the platform's fields are assigned after, so authority is
        # structural rather than a list someone has to remember to extend: a field this method
        # starts setting later is protected by the assignment itself. `_RESERVED_NOTE_KEYS` covers
        # only what that cannot reach — the fields set further down under a condition, and `acked`,
        # which `notify` never writes at all. ARCC's input-validation guidance is explicit that this
        # is the allowlist direction ("only data fitting specific, approved criteria is processed").
        supplied = dict(meta or {})
        smuggled = sorted(set(supplied) & self._RESERVED_NOTE_KEYS)
        if smuggled:
            # Logged, not silent: a reserved key in meta is an emitter bug, and dropping it without
            # a word is how the emitter's author never learns their field vanished.
            logger.debug(
                "notify(%s): ignoring platform-owned key(s) %s supplied in meta", kind, smuggled
            )
        note: dict[str, Any] = {
            k: v for k, v in supplied.items() if k not in self._RESERVED_NOTE_KEYS
        }
        note.update(
            {
                "kind": kind,
                "title": title,
                "body": body,
                "ts": datetime.now(tz=timezone.utc).isoformat(),
            }
        )

        # Resolve the rule. Every failure path here falls through to immediate delivery:
        # a policy layer that can't read its own config must not be able to silence the
        # system (the same reason the gate above fails open).
        try:
            rule = rules.resolve_rule_for_legacy(kind)
            # The USER's name (`identity.operator_name`), which is what "mentions you by name"
            # means — this read `agent.bot_name` once, and escalated on the assistant's name.
            reason = rule.conditions.matches(f"{title}\n{body}", identity.operator_name())
            if reason:
                rule = rule.escalated()
                note["escalated_by"] = reason
        except Exception:
            logger.debug("notification rule resolution failed; delivering", exc_info=True)
            rule = None

        mode = rule.mode if rule is not None else "immediate"
        # QUIET HOURS ON AN ATTENTION KIND (#341, bug B). The gate no longer drops it — it says
        # "record it, don't interrupt", which is what `badge` already means. Written here rather
        # than inside the gate because the gate decides WHETHER, and the mode is HOW: this is the
        # one place that vocabulary lives. Only `immediate` is downgraded; `digest` and `badge` are
        # already quieter and `never` is the user's own instruction, honoured below.
        if posture == entity_routes.POSTURE_QUIET and mode == "immediate":
            mode = "badge"
        note["mode"] = mode
        if rule is not None:
            note["targets"] = list(rule.targets)
            # The rule key's source, not a prefix of `kind`. Legacy flat wire strings do
            # not carry it (`app.route.drift` resolves to source `system`), so a consumer
            # that split the kind would deep-link the wrong surface.
            note["source"] = rule.source

        if mode == "never":
            logger.debug("Notification dropped by rule %s: %r", rule.key if rule else kind, title)
            return

        # 🔴 THE ADDRESSEE (`TSE2-5`). Everything below this point is a LOCAL fire — a toast on
        # this dashboard, a row in this digest, a ping to this owner's phone, a banner on this
        # desktop. A note addressed to somebody else has no business in any of them, and before
        # this it reached all four: `TSE2-3`'s shared inbox renders a teammate's item, and when
        # that item wanted attention `emit_attention_item` fired at whoever happened to be
        # sitting here.
        #
        # Placed after `never` and before the three delivery modes deliberately. `never` is the
        # user's own instruction and outranks everything, as it already did. The modes below are
        # all *local* deliveries — including `digest`, which is why the addressing decision
        # cannot live inside the `immediate` branch: a foreign note queued for the morning
        # digest is a foreign note fired, one day late.
        #
        # This is the trigger posture, not a second mechanism: `triggers/ownership.py` withholds
        # a foreign row from the ARM read (`triggers/provider.py::armable`) while the LISTING
        # read keeps it visible. Here the notification log is the listing — `_append_notification`
        # still runs, so the note appears in the bell and `GET /api/notifications` — and the fire
        # half (`_broadcast`, `native`, `push`, the digest) is what the addressee gates.
        if not addressing.is_locally_addressed(note):
            # Route it where it actually belongs FIRST, so `routed_to` is recorded on the row
            # the user sees. With no `type=notification` provider installed this returns "" and
            # the note is simply visible-but-inert — "nobody could reach them" must never read
            # as "delivered".
            from personalclaw.notification_providers.registry import deliver_to_addressee

            addressee = addressing.addressee_of(note)
            note[addressing.WITHHELD_REASON_KEY] = addressing.FOREIGN_ADDRESSEE
            note[addressing.ROUTED_TO_KEY] = deliver_to_addressee(note, addressee)
            logger.debug(
                "Notification addressed to %r, not this owner — visible, not fired (routed_to=%r)",
                addressee,
                note[addressing.ROUTED_TO_KEY],
            )
            self._append_notification(note)
            self._announce_logged(note)
            return

        if mode == "digest":
            rules.queue_for_digest(note)
            return
        if mode == "badge":
            # Persist and count, but do not push a toast: the badge is the delivery.
            note["badge_only"] = True
            self._append_notification(note)
            self._announce_logged(note)
            return

        # The `native` target (DC-5). Decided HERE, on the one delivery choke point, and
        # only for `immediate` — the three quieter modes returned above, which is what makes
        # "badge means no interruption" survive a rule that also names `native`.
        #
        # The gateway cannot raise an OS notification itself: it is an HTTP server that may
        # equally be serving a browser tab. So the decision rides the note out over the WS
        # and the Electron renderer hands it to the main process (`desktop/
        # nativeNotifications.js`). A note with no `native` key reaches a shell that will not
        # raise anything, so an unrelated rule cannot leak an OS notification.
        #
        # Fail OPEN, like every other layer in this method: a registry read that raises
        # degrades to the dashboard delivery below (which is the documented fallback anyway)
        # rather than dropping the note. A native banner is the nice-to-have here; the note
        # reaching the bell is not.
        try:
            native = rules.native_delivery(rule, self.desktop.capability(rules.NATIVE_CAPABILITY))
        except Exception:
            logger.debug("native delivery decision failed; dashboard only", exc_info=True)
            native = None
        if native is not None:
            note["native"] = native

        self._append_notification(note)
        self._broadcast(note)
        # Plan 42's `push` TARGET, live since MOBILE-COMPANION MC-5. Deliberately after the
        # dashboard broadcast and outside its try: the desktop delivery is the one that must
        # never wait on (or be broken by) a third-party push service. `deliver_async` hands
        # the blocking POST to a daemon thread and swallows its own failures.
        if rule is not None and "push" in rule.targets:
            self._push_target(kind, note)

    def _announce_logged(self, note: dict[str, Any]) -> None:
        """Tell the bell a note was recorded WITHOUT being fired (a badge, a foreign addressee).

        Its own frame, not ``notification``: the toasts and the desktop banner act on that one,
        and a badge is the promise that nothing interrupts. The bell, Home and the feed re-read
        on any ``notification*`` frame and poll only as a safety net, so a note that sent
        nothing reached the bell a poll later, and a captured note sat uncounted beside the
        Inbox row it was about.
        """
        try:
            self.broadcast_ws("notification_logged", {"ts": note.get("ts", "")})
        except Exception:
            logger.debug("notification_logged broadcast failed", exc_info=True)

    #: Meta keys that can name the item a notification is about, most specific first. The
    #: push payload carries the id and NOTHING else, so the phone can open the right thing
    #: without the ping having described it.
    _PUSH_ITEM_KEYS: tuple[str, ...] = ("item_id", "inbox_item", "session")

    def _push_target(self, kind: str, note: dict[str, Any]) -> None:
        """Fire one content-free ping for *note*. **Only ids cross this boundary.**

        The payload is built from ``kind`` plus a single id looked up in the note's meta —
        never from ``title`` or ``body``, which are the fields carrying the user's own text.
        That is why this reads specific keys instead of forwarding ``note``: a
        forward-the-dict shape is one careless edit away from shipping the message body to a
        push service, and :func:`personalclaw.push.content_free_payload` is the only
        constructor that can build what the sender accepts.
        """
        try:
            from personalclaw import push

            item_id = ""
            for key in self._PUSH_ITEM_KEYS:
                candidate = str(note.get(key) or "").strip()
                if candidate:
                    item_id = candidate
                    break
            push.deliver_async(kind, item_id)
        except Exception:
            self._log.debug("push target dispatch failed", exc_info=True)

    def unread_count(self) -> int:
        """How many things are actually waiting on you — derived, never cached.

        **This now counts unresolved INBOX items, not unacked notification-log entries**
        (plan 42 T5.2). The two stores had diverged into two answers to one question: the
        log tracked "did a toast get acknowledged", the inbox tracks "is this dealt with".
        A user who handled a request in the inbox still saw a badge, and dismissing a toast
        cleared the badge for work that was still outstanding. Inbox status is the honest
        answer, so the log becomes a pure delivery audit.

        Counts PENDING only, not SEEN: the badge means "new since you last looked", and
        opening an item marks it SEEN. Counting SEEN would leave the badge lit until every
        item was resolved, which is what the *list* is for.

        Fails to 0 rather than raising — a badge is chrome, and a broken read must not take
        down every consumer of the sessions payload.
        """
        try:
            from personalclaw.inbox import InboxStore, ItemStatus

            store = getattr(self, "_inbox_store", None)
            svc = getattr(self, "_inbox_svc", None)
            if svc is not None:
                store = svc.inbox
            elif store is None:
                store = InboxStore()
                store.load()
                self._inbox_store = store
            return sum(1 for i in store.items.values() if i.status == ItemStatus.PENDING)
        except Exception:
            logger.debug("unread_count from inbox failed", exc_info=True)
            return 0

    def loop_sse(self) -> SseRegistry:
        """The per-loop SSE registry (key ``loop:<id>``) — serves every kind, incl. code."""
        return self._loop_sse

    def workflow_sse(self) -> SseRegistry:
        """The per-run workflow SSE registry (key ``workflow:<run_id>``)."""
        return self._workflow_sse

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

    def _append_notification(self, note: dict[str, Any]) -> None:
        """Append to the log — the ONE seam that enforces the size cap.

        Memory and file are trimmed together: the log floats up to 2× the cap
        between trims, then both drop to the newest cap-many rows in the same
        step. Keeping memory the exact mirror of the file is what makes every
        `_rewrite_notifications` call lossless (Issue 420 — a load that capped
        below the file's row count let one ack silently destroy the rest).
        """
        self._notification_log.append(note)
        if len(self._notification_log) > _MAX_PERSISTED_NOTIFICATIONS * 2:
            self._notification_log = self._notification_log[-_MAX_PERSISTED_NOTIFICATIONS:]
            _rewrite_notifications(self._notification_log)
        else:
            _persist_notification(note)

    def ack_notification(self, ts: str) -> bool:
        """Mark a notification as acknowledged and persist."""
        for n in self._notification_log:
            if n.get("ts") == ts:
                n["acked"] = True
                _rewrite_notifications(self._notification_log)
                self.broadcast_ws("notification_ack", {"ts": ts})
                return True
        return False

    def ack_item_notifications(self, item_ids: Iterable[str]) -> list[str]:
        """Mark read every notification that is a view of one of *item_ids*. Returns their ts.

        Called by the Inbox's one status transition (`inbox.set_item_status`) when rows CLOSE:
        a notification about a row that is done is not news, and the bell kept counting them.
        Matched on ``inbox_item``, the link `inbox.emit_attention_item` stamps. One rewrite and
        one frame for the whole batch, so "Dismiss all" is not one of each per row.
        """
        wanted = {str(i) for i in item_ids if i}
        if not wanted:
            return []
        read: list[str] = []
        for n in self._notification_log:
            if not n.get("acked") and str(n.get("inbox_item") or "") in wanted:
                n["acked"] = True
                read.append(str(n.get("ts") or ""))
        if read:
            _rewrite_notifications(self._notification_log)
            self.broadcast_ws("notification_ack", {"ts": read})
        return read

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
        created_by_app: str = "",
    ) -> _ChatSession:
        """Return existing session or create a new one.

        ``app`` is the origin tag (a hidden worker's, a channel's). ``created_by_app`` is the
        VERIFIED identity of the app whose request starts the conversation, which only a route
        handler holding an app token passes. A name already persisted keeps the creator its meta
        line records, whatever the caller passed: every restore path mints its session here, so
        this is the one place a restore could hand an app's conversation back as yours, or one of
        yours to an app, and it does neither.
        """
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
        persisted_creator = self._persisted_creating_app(name)
        session.created_by_app = (
            persisted_creator if persisted_creator is not None else created_by_app
        )
        # An app's conversation is tagged with the app too, which is what keeps it out of your chat
        # list — after a restart as well as before.
        session._app = app or session.created_by_app
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
        # APE-2: the `session.created` platform-event emit site — a new session row just
        # became true here. Fanned out ONLY to apps that declared the subscription (deny by
        # default); `emit` is total, so an app-side failure can never fail this creation.
        #
        # Guarded on "no persisted history", because this method is ALSO how a session is
        # REHYDRATED: `chat_persistence.restore_recent_sessions` (bulk, at startup) and
        # `_rehydrate_session_from_history` / the resume + post-to-an-old-session paths all
        # reach the create branch for a session that already exists on disk. Announcing
        # those would re-fire `session.created` for every restored session on every gateway
        # restart, and an app would double-count sessions it already saw. Asked
        # provider-agnostically (`resolve_history_key`) rather than by key shape, so a
        # channel thread is recognised as persisted too. Fails OPEN: an unreadable log reads
        # as "no history", which at worst re-announces, never swallows a real creation.
        if not self._has_persisted_history(name):
            from personalclaw.apps.app_events import SESSION_CREATED
            from personalclaw.apps.app_events import emit as emit_platform_event

            emit_platform_event(SESSION_CREATED, {"session": name})
        self.push_sessions_update()
        return session

    def _has_persisted_history(self, name: str) -> bool:
        """Whether ``name`` already has persisted conversation metadata — i.e. a session
        materialized under this name is being REHYDRATED, not created.

        Used by the ``session.created`` platform-event emit site (APE-2) to tell a real
        creation from a restore, since both go through ``get_or_create_session``.
        Best-effort by design: any failure answers "no history", so the worst outcome is a
        re-announced session rather than a swallowed creation."""
        try:
            from personalclaw.dashboard.chat_utils import resolve_history_key

            return bool(resolve_history_key(self.conversation_log, name))
        except Exception:
            return False

    def session_creating_app(self, name: str) -> str:
        """The app whose token started the conversation *name*, or ``""``.

        ``""`` answers for a conversation of yours, for one that does not exist, and for one whose
        record cannot be read — every case in which an app must be refused, which is the point:
        the permission middleware lets an app reach a conversation only when this names that app,
        so there is no answer in which an unknown becomes the app's. A resident session answers
        from memory, and one only on disk from its meta line, read without rehydrating it, so a
        refused request loads nothing.
        """
        session = self._sessions.get(name)
        if session is not None:
            return session.created_by_app
        return self._persisted_creating_app(name) or ""

    def _persisted_creating_app(self, name: str) -> str | None:
        """The creating app the meta line persisted under *name* records (``""`` for yours), or
        ``None`` when nothing is persisted under it or its record cannot be read."""
        log = self.conversation_log
        if log is None:
            return None
        try:
            from personalclaw.dashboard.chat_utils import resolve_history_key

            key = resolve_history_key(log, name)
            if not key:
                return None
            creator = (log.get_metadata(key) or {}).get(CREATED_BY_APP_META_KEY, "")
        except Exception:  # noqa: BLE001 — an unreadable record answers "unknown", never an app
            self._log.warning("creating-app lookup failed for %s", name, exc_info=True)
            return None
        return creator if isinstance(creator, str) else ""

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


# ── Notification persistence ──


def _notifications_path() -> Path:
    """Path to the notifications JSONL file."""
    return config_dir() / _NOTIFICATIONS_FILE


def _load_notifications() -> list[dict[str, Any]]:
    """Load persisted notifications from disk (newest last).

    EVERY valid row loads — no cap slice. The in-memory log is the write
    authority for `_rewrite_notifications`, so a load that silently truncated
    below what the file holds turned the next read-state rewrite (one ack)
    into permanent deletion of every unloaded row (Issue 420: the steady-state
    file legitimately holds up to 2× the cap between trims). The size cap is
    enforced at the append seam, on memory and file together.
    """
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
        return entries
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
    except Exception:
        logger.debug("Failed to persist notification", exc_info=True)


def _rewrite_notifications(notifications: list[dict[str, str]]) -> None:
    """Rewrite the notifications file to exactly the given rows.

    No cap slice here: this writes what the caller holds, so a read-state
    change (ack/unack/delete) can never shorten the log below what memory —
    which now mirrors the file in full — carries. The cap lives at the
    append seam, where memory and file are trimmed together.
    """
    path = _notifications_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = [json.dumps(n) + "\n" for n in notifications]
        path.write_text("".join(lines), encoding="utf-8")
    except Exception:
        logger.debug("Failed to rewrite notifications file", exc_info=True)


def _fmt_duration(secs: int) -> str:
    """Format seconds as human-readable duration."""
    h, rem = divmod(secs, 3600)
    m, s = divmod(rem, 60)
    return f"{h}h {m}m" if h > 0 else f"{m}m {s}s"

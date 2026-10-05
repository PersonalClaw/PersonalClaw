"""Shared utility functions for dashboard chat modules.

Redaction, model normalization, queue operations, stream chunk building,
persona injection, and other helpers used across chat_*.py modules.
"""

import functools
import json
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from personalclaw.llm.base import LLMEvent

from personalclaw import task_modes
from personalclaw.dashboard.chat_queue import ON_RECORD
from personalclaw.dashboard.state import (
    CRON_NOTIFY_PREFIX,
    SUBAGENT_COMPLETION_PREFIX,
    DashboardState,
    _ChatSession,
    parse_cls_meta,
    tool_input_to_str,
)
from personalclaw.llm.events import COMPACTION_AUTOMATIC
from personalclaw.security import redact_credentials, redact_exfiltration_urls
from personalclaw.sel import SecurityEvent, sel
from personalclaw.turn_streams import closing_stream
from personalclaw.usage_ledger import Attribution
from personalclaw.validation import MAX_TOOL_NAME_LEN, sanitize_string

logger = logging.getLogger(__name__)


def _redact_deep(obj):
    """Recursively redact all string values in a nested structure."""
    if isinstance(obj, str):
        obj, _ = redact_exfiltration_urls(obj)
        obj, _ = redact_credentials(obj)
        return obj
    if isinstance(obj, dict):
        return {k: _redact_deep(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_redact_deep(v) for v in obj]
    return obj


def _build_stream_chunk(msg: dict) -> str:
    """Build a JSON SSE chunk from a session message, with meta redaction for permissions."""
    try:
        meta = parse_cls_meta(msg.get("cls", "")) if msg.get("role") == "permission" else None
    except Exception:
        logger.warning("Failed to parse cls meta for permission message", exc_info=True)
        meta = None
    if meta:
        meta = _redact_deep(meta)
    content = msg.get("content", "")
    if isinstance(content, str):
        content, _ = redact_exfiltration_urls(content)
        content, _ = redact_credentials(content)
    else:
        content = _redact_deep(content)
    cls_val = msg.get("cls", "")
    if isinstance(cls_val, str):
        cls_val, _ = redact_exfiltration_urls(cls_val)
        cls_val, _ = redact_credentials(cls_val)
    else:
        cls_val = _redact_deep(cls_val)
    return json.dumps(
        {
            "type": msg["role"],
            "content": content,
            "ts": msg.get("ts", ""),
            "cls": cls_val,
            **({"meta": meta} if meta else {}),
        }
    )


# The bash-command extractor + the task-mode gate logic live in the neutral
# ``task_modes`` module (the single source of truth, also enforced in the native
# runtime so a Trust/YOLO auto-approve can't bypass a task-mode restriction).
# Re-exported here under the dashboard's private name for existing call sites.
_extract_bash_command = task_modes.extract_bash_command


def task_mode_denies(
    session: "_ChatSession",
    declared: object,
    title: str,
    tool_kind: str,
    tool_input: object,
    *,
    builds: bool = False,
) -> str:
    """Return a deny-reason for the session's TASK mode, or '' to allow the tool.

    Thin session-aware wrapper over the canonical gate in ``task_modes`` (the same
    logic the native runtime enforces before approval). ``declared`` and ``builds`` are what
    the tool behind the call declares — a permission request's ``risk_level``/``builds`` —
    and an ACP CLI's own tool declares nothing, so only its read-only shell commands pass.
    """
    mode = getattr(session, "_task_mode", "agent")
    return task_modes.task_mode_denies(mode, declared, title, tool_kind, tool_input, builds=builds)


def apply_task_mode(state: DashboardState, session: "_ChatSession", mode: str) -> None:
    """The ONE write path for a session's task mode.

    Two writes that must never drift apart: the session's own posture (read by the
    dashboard-side gate + the prompt framing) and the runtime's posture (read by the
    native runtime's ``_guard_and_invoke`` gate, before approval). Setting only the
    first leaves a "plan" session whose tools still run.
    """
    session._task_mode = mode
    state.sessions.set_task_mode(f"dashboard:{session.key}", mode)


# Per-task-mode system-prompt framing — appended to the agent's system prompt so
# the model FRAMES the work to match the mode (the tool gate enforces it; this
# shapes intent + output so the agent doesn't fight the gate). 'agent' adds nothing.
# Shared tail for the restricted modes (ask/plan/build). The model can't flip the
# mode itself (a silent self-escalation out of a read-only posture would defeat the
# point of Ask/Plan and is a prompt-injection hole), but it CAN propose a switch the
# user approves with one click — the same propose->approve handshake as a tool
# approval. End the reply with a [SWITCH_TO_AGENT: <continuation>] marker; the UI
# renders it as a primary button that flips the session to Agent AND runs the
# continuation. This is the affordance to use for an escalation — a plain textual
# suggestion only re-sends literal text and leaves the session restricted.
_SWITCH_HINT = (
    " You cannot change the mode yourself, but you can offer a one-click switch: when "
    "the user wants you to actually do the work, end your reply with a marker "
    "[SWITCH_TO_AGENT: <short imperative continuation>] — e.g. "
    "[SWITCH_TO_AGENT: create the file] or [SWITCH_TO_AGENT: execute the plan above]. "
    "The UI turns it into a 'Switch to Agent & run it' button; clicking it flips this "
    "session to Agent mode and runs your continuation. Use this marker for the switch."
)

# Prepended to EVERY restricted-mode framing. The session's mode can change mid-
# conversation (the user picks a different tab), so an earlier turn may have refused
# under a DIFFERENT mode (e.g. "I can't, I'm in Ask mode"). This block is authoritative
# for the current turn: the only mode that applies now is the one named below — judge
# each tool against THIS posture, not whatever a prior turn said. Without it the model
# anchors on its earlier refusal and keeps declining work the new mode actually permits
# (e.g. switching Ask→Build still refusing to produce an artifact). Agent's framing has
# the same lift; this gives the restricted modes parity.
_MODE_LIFT = (
    " This posture is current as of THIS turn and supersedes any mode an earlier turn "
    "in this conversation mentioned — if a previous reply refused because it was in a "
    "different mode, re-evaluate against the mode stated here and don't carry that "
    "refusal forward."
)

_TASK_MODE_FRAMING = {
    "agent": (
        "## Task mode: Agent\n"
        "You are in AGENT mode — full execution. Use whatever tools the task needs to "
        "actually carry out the user's request: read, write, run commands, create "
        "artifacts, spawn work. If an earlier turn in THIS conversation declined to act "
        "because it was in Ask, Plan, or Build mode, that restriction has been lifted — "
        "do not refuse on those grounds again; proceed and do the work now."
    ),
    "ask": (
        "## Task mode: Ask\n"
        "You are in ASK mode — a read-only Q&A posture. Answer the user's question "
        "directly and concisely from your knowledge, memory, and read-only inspection "
        "of the workspace. You MAY read files, search, and recall memory, but you MUST "
        "NOT modify anything — no file writes/edits, no shell commands with side "
        "effects, no creating artifacts, no spawning work. Mutating tools are blocked "
        "in this mode; don't attempt them. If the user clearly wants you to *do* "
        "something, answer their question, then tell them to do the work."
        + _MODE_LIFT
        + _SWITCH_HINT
    ),
    "plan": (
        "## Task mode: Plan\n"
        "You are in PLAN mode. Produce a clear, actionable plan for the work — steps, "
        "files/areas involved, risks, and the order of operations. You MAY use "
        "read-only tools (read files, search, inspect) to GROUND the plan in the "
        "actual state of things — but you MUST NOT execute or mutate anything "
        "(no writes/edits, no commands with side effects). Inspect as needed, then "
        "present the plan for the user to review, then tell them to run it."
        + _MODE_LIFT
        + _SWITCH_HINT
    ),
    "build": (
        "## Task mode: Build\n"
        "You are in BUILD mode — focused on producing a concrete deliverable (an "
        "artifact, widget, document, infographic, or skill). Read what you need, then "
        "create/iterate the artifact. Tools are scoped to read-only inspection plus "
        "artifact/widget/skill production; unrelated mutating tools are blocked. Lead "
        "with the produced artifact rather than a long explanation. If the user asks for "
        "non-build work (e.g. editing project files, running commands), explain it's out "
        "of scope for Build, then tell them how to do it." + _MODE_LIFT + _SWITCH_HINT
    ),
}


def task_mode_framing(session: "_ChatSession") -> str:
    """The system-prompt framing block for the session's task mode.

    Every mode (including Agent) states its posture explicitly so a mid-chat
    mode switch is communicated to the model — Agent's block actively lifts any
    Ask/Plan/Build restriction the model declared in an earlier turn, otherwise
    it anchors on that stale history and keeps refusing after the user switches.
    """
    return _TASK_MODE_FRAMING.get(getattr(session, "_task_mode", "agent"), "")


# Deprecated -1m model aliases → base model
_DEPRECATED_MODEL_MAP = {
    "claude-opus-4.6-1m": "claude-opus-4.6",
    "claude-sonnet-4.6-1m": "claude-sonnet-4.6",
}


def _normalize_model(name: str) -> str:
    """Map deprecated model names to their replacements."""
    return _DEPRECATED_MODEL_MAP.get(name, name)


def is_deprecated_model(name: str) -> bool:
    """Check if a model name is deprecated (public API for cross-module use)."""
    return name in _DEPRECATED_MODEL_MAP


# ACP agent slash command root words
_SLASH_COMMANDS = frozenset(
    {
        "/agent",
        "/changelog",
        "/chat",
        "/clear",
        "/code",
        "/compact",
        "/context",
        "/editor",
        "/exit",
        "/experiment",
        "/help",
        "/hooks",
        "/issue",
        "/logdump",
        "/mcp",
        "/model",
        "/paste",
        "/prompts",
        "/q",
        "/quit",
        "/reply",
        "/tangent",
        "/todos",
        "/tools",
        "/undo",
        "/usage",
    }
)

_BLOCKED_SLASH_COMMANDS = frozenset(
    {"/quit", "/exit", "/q", "/chat", "/paste", "/reply", "/editor"}
)

#: The kind stamped on the substitution notice's ``activity_event``. Reuses the attention
#: channel CE2-8 established for headroom notices — ``ActivityLine`` renders any kind's
#: text inline, so this needs no frontend counterpart, only a truthful sentence.
SLASH_FALLBACK_ACTIVITY_KIND = "slash_fallback"

#: The kind stamped on the notice that a turn runs on another model than the one chosen for it
#: (the agent's pin or the chat's own pick cannot run). Same channel, same inline rendering.
MODEL_SUBSTITUTION_ACTIVITY_KIND = "model_substitution"


def model_substitution_notice(client: object) -> str:
    """The chat's sentence for a runtime serving in place of the chosen model, else "".

    Read off the runtime (``NativeAgentRuntime.model_substitution``), which the builder stamped
    with the model that actually answers — so the sentence cannot name a model the turn did not
    run on.
    """
    from personalclaw.llm.base import ModelSubstitution

    substitution = getattr(client, "model_substitution", None)
    return substitution.notice() if isinstance(substitution, ModelSubstitution) else ""


#: The kind on the live line a loop's turn says when its model can't use tools. Same channel and
#: the same inline rendering as the substitution notice, and the loop's page shows it in its live
#: activity.
NO_TOOLS_ACTIVITY_KIND = "no_tools"

#: The ``_app`` tags a loop's hidden sessions carry: ``"loop"`` on its workers (``loop/manager``)
#: and ``"loops"`` on its planner (``loop/plan_walkthrough``). Some checks key WORKER behaviour off
#: ``"loop"`` alone, on purpose — the planner is not a cycle worker — but both are loop work, so
#: both take the loops axis (``chat_runner.model_axis_for``).
LOOP_WORK_APPS = frozenset({"loop", "loops"})


def tools_said(state: object, session: object, client: object, *, said: str = "") -> str:
    """The model *client* runs on without tools (``NativeAgentRuntime.tool_less_model``), or "".

    For a loop's session it is said on the loop's live activity, unless it is the *said* model
    the turn already named: a loop's work is done with tools, so a turn without them is never
    left to the gateway log alone. What the loop then does about it is the cycle driver's: it
    reads the same runtime (``loop.manager.ran_without_tools``) and holds the loop
    (``LoopWatchdog.hold_without_tools``).
    """
    from personalclaw.llm.tool_use import runs_without_tools, tool_less_model

    model = tool_less_model(client)
    if model and model != said and getattr(session, "_app", "") in LOOP_WORK_APPS:
        state.broadcast_ws(  # type: ignore[attr-defined]
            "activity_event",
            {
                "session": getattr(session, "key", ""),
                "kind": NO_TOOLS_ACTIVITY_KIND,
                "text": f"{runs_without_tools(model)}.",
            },
        )
    return model


async def stream_slash_command(
    client, command: str, *, prompt: str, notify
) -> "AsyncIterator[LLMEvent]":
    """Run *command* natively if the provider can, else answer *prompt* as plain text.

    THE slash-command dispatch decision (`G4`). Four outcomes, all deliberate:

    0. **The provider runs THIS command itself, in-process** (``compacts_in_process``) →
       ``stream_command`` is dispatched even though the provider has no wire-level command
       axis, because there is nothing to send: the native loop owns its own message list, so
       ``/compact`` is a local operation on local state. Checked FIRST, ahead of the axis
       gate — otherwise the one command the runtime can genuinely execute falls into the
       substitution below and the model is asked about the TEXT "/compact", which is what
       made the composer's advertisement a lie on the default provider (#470).
    1. **Provider declares no command axis** → nothing is sent as a command; *prompt* is
       streamed as an ordinary turn and ``notify`` says so. Covers the measured
       claude-code case (adapter 0.60.0 advertises no command capability) and every
       native/HTTP provider, whose ``stream_command`` was always a plain prompt wearing a
       command's name.
    2. **Declared, but the agent answers ``-32601`` before yielding anything** → the turn
       is still untouched, so the same substitution runs. This is the version-drift case:
       the capability said yes and the method wasn't there.
    3. **Declared, and ``-32601`` arrives AFTER events have been yielded** → NO
       substitution. Re-issuing would append a second answer to the same assistant
       message, re-run any tool call that already ran, and bill the turn twice. The turn
       stops with :class:`AcpCommandFailedAfterOutput`, whose message explains that to the
       user, and the partial output is preserved by the caller's error handling.

    Any OTHER JSON-RPC error is not caught here at all: only "method not found" means the
    agent *cannot*, and a substitution triggered by anything else would silently swallow a
    real failure.

    ``notify`` takes the user-visible sentence; the caller owns the surface it lands on.
    """
    from personalclaw.acp.errors import (
        AcpCommandFailedAfterOutput,
        AcpCommandsUnsupported,
        AcpMethodNotFound,
    )

    # Outcome 0 — a command the provider performs on its OWN state. No wire, no
    # substitution, no notice: the command really ran, so there is nothing to disclose.
    if command == "/compact" and bool(getattr(client, "compacts_in_process", False)):
        async with closing_stream(client.stream_command(command)) as events:
            async for event in events:
                yield event
        return

    if not bool(getattr(client, "supports_native_commands", False)):
        notify(f"`{command}` isn't a command this agent can run — sent as a plain message.")
        async with closing_stream(client.stream(prompt)) as events:
            async for event in events:
                yield event
        return

    produced = 0
    try:
        async with closing_stream(client.stream_command(command)) as events:
            async for event in events:
                produced += 1
                yield event
        return
    except (AcpCommandsUnsupported, AcpMethodNotFound) as exc:
        if produced:
            raise AcpCommandFailedAfterOutput(command) from exc
        logger.info("slash command %s unsupported (%s) — substituting a plain prompt", command, exc)
        notify(f"`{command}` was rejected as an unknown command — re-sent as a plain message.")
    async with closing_stream(client.stream(prompt)) as events:
        async for event in events:
            yield event


# The slash commands the DASHBOARD handles directly — the only ones the composer
# "/" menu advertises. Each maps to a deterministic action (an instant GUI action
# in the web client, or server-side handling here), so it works regardless of the
# bound model. Commands NOT in this map are still typeable and dispatch to the
# native harness via `is_slash` → stream_command, but they aren't surfaced,
# because a model that doesn't recognise them would only improvise a response.
# Order here is the menu order. This is the single source of truth for the menu.
_SLASH_COMMAND_HINTS: dict[str, str] = {
    "/help": "List available slash commands",
    "/optimize": "Optimize a prompt, then send it",
    "/clear": "Start a fresh chat",
    "/prompts": "Open the saved-prompt palette",
    "/model": "Switch the model for this chat",
    "/agent": "Switch the agent for this chat",
    "/effort": "Set reasoning effort for this chat",
    "/project": "Scope this new chat to a project",
    "/tools": "Open the Tools page",
    "/undo": "Roll back the last N conversation turns",
    "/rewind-to-turn": "Put back the agent's file edits after turn N (not a command's)",
    "/compact": "Compact the conversation to free context",
}


# Tool/status turns once persisted their content with a leading status emoji
# (a wrench for a call, a prohibition sign for blocked/rejected, a check for
# approved). That violated the no-emoji rule and made the emoji a load-bearing
# sentinel, so new writes carry the bare title/text. This strips a leading
# pictographic sentinel + following space from ALREADY-PERSISTED sessions on read,
# so historical turns still render a clean tool name. No-op for new content.
_LEADING_STATUS_EMOJI_RE = re.compile(r"^[\U0001F000-\U0001FAFF☀-➿️⬀-⯿]+\s*")


def strip_status_sentinel(content: str) -> str:
    """Remove a legacy leading status-emoji sentinel from persisted turn content."""
    return _LEADING_STATUS_EMOJI_RE.sub("", content).strip() if content else content


def _broadcast_auto_tool(state: DashboardState, session: _ChatSession, event: "LLMEvent") -> str:
    """Broadcast an auto-approved tool call via WS with redacted title. Returns redacted title."""
    title, _ = redact_exfiltration_urls(event.title)
    title, _ = redact_credentials(title)
    kind, _ = redact_exfiltration_urls(event.tool_kind)
    kind, _ = redact_credentials(kind)
    tcid, _ = redact_exfiltration_urls(event.tool_call_id or "")
    tcid, _ = redact_credentials(tcid)
    state.broadcast_ws(
        "tool_call",
        {
            "session": session.key,
            "tool": title,
            "kind": kind,
            "auto": True,
            "tool_call_id": tcid,
            "purpose": redact_credentials(
                redact_exfiltration_urls((event.tool_purpose or "")[:200])[0]
            )[0],
            "input_preview": redact_credentials(
                redact_exfiltration_urls(tool_input_to_str(event.tool_input)[:4000])[0]
            )[0],
        },
    )
    return title


async def _say_compaction_notice(state: DashboardState, session: _ChatSession, text: str) -> None:
    """Say a compaction's outcome where the conversation is: its dashboard chat, and the channel
    thread it is linked to (``DashboardState.tell_linked_channel``), where its replies go."""
    session.append("assistant", text, "msg msg-a")
    state.broadcast_ws(
        "chat_message",
        {"session": session.key, "role": "assistant", "content": text},
    )
    await state.tell_linked_channel(_history_key_for(session.key), text)


async def _broadcast_compaction_result(
    state: DashboardState, session: _ChatSession, event: "LLMEvent"
) -> str | None:
    """Say a compaction outcome in the conversation (:func:`_say_compaction_notice`). Returns the
    message text, or None for a status that is no outcome.

    Three terminal statuses, and the third is the one worth reading. ``noop`` says the pass
    ran and found nothing to reclaim — a short conversation is already compact. It is NOT a
    failure and NOT a "Conversation compacted." either: the in-process compaction the native
    runtime performs returns a real before/after, so on a fresh chat the honest answer is
    that nothing moved. Saying "compacted" there would be the same shape of lie as the
    substituted plain-prompt answer this replaced (#470).

    A loop that compacted its own history mid-turn (``COMPACTION_AUTOMATIC``) is announced in
    the same words as a ``/compact`` that did: it is the same pass, and the conversation changed
    the same way.
    """
    status_type = event.text
    if status_type in ("completed", COMPACTION_AUTOMATIC):
        summary, _ = redact_credentials(event.title)
        summary, _ = redact_exfiltration_urls(summary)
        msg_text = f"Conversation compacted: {summary}" if summary else "Conversation compacted."
    elif status_type == "noop":
        msg_text = "Nothing to compact — this conversation is already short enough."
    elif status_type == "failed":
        error, _ = redact_credentials(event.title or "unknown error")
        error, _ = redact_exfiltration_urls(error)
        msg_text = f"Compaction failed: {error}"
    else:
        return None
    await _say_compaction_notice(state, session, msg_text)
    return msg_text


def _emit_agent_assignment(session_name: str, agent: str, outcome: str = "applied") -> None:
    """Emit a SEL audit event when an agent is set, changed, or rejected on a session."""
    sel().log(
        SecurityEvent(
            event_id=uuid.uuid4().hex,
            timestamp=datetime.now(tz=timezone.utc).isoformat(),
            event_type="agent_assignment",
            caller_identity=f"dashboard:{session_name}",
            agent=agent,
            source="dashboard",
            operation="session_agent_set",
            outcome=outcome,
            resources=f"session={session_name}",
        )
    )


def _validate_tool_name(tool_name: str, tool_kind: str = "") -> str:
    """Validate and sanitize tool display names for hook matching."""
    sanitized = sanitize_string(tool_name)
    if not sanitized:
        raise ValueError("Tool name cannot be empty")
    if tool_kind != "execute" and len(sanitized) > MAX_TOOL_NAME_LEN:
        raise ValueError(f"Tool name exceeds max length {MAX_TOOL_NAME_LEN}")
    return sanitized


def _history_key_for(session_name: str) -> str:
    """Canonical history key for a DASHBOARD chat session.

    Dashboard sessions live under the ``dashboard:`` namespace; a ``dashboard_``
    filename form normalizes to it. This helper is for dashboard-native session
    ids only — it does NOT know about channel-provider threads (those persist +
    resolve under their own bare provider key; see ``resolve_history_key``).

    The rule itself is :func:`personalclaw.constants.dashboard_history_key`: a chat's turns
    are written under this key, and code below this layer that reads them back (a loop's
    spend total) keys through the same function, so the two cannot disagree."""
    from personalclaw.constants import dashboard_history_key

    return dashboard_history_key(session_name)


def take_in_the_users_links(caller_app: str, session_name: str, text: str) -> None:
    """The links in *text*, a message sent into chat *session_name*, become the user's for the
    chat's web_fetch (``web.fetch``'s provenance rule), under the key the chat's turns hand
    their runtime. *caller_app* is the app whose token made the request (``request["app"]``):
    an app's message is the app's words, not the user's, and grants nothing."""
    if caller_app:
        return
    from personalclaw.web.fetch import record_user_message_urls

    record_user_message_urls(_history_key_for(session_name), text)


def attached_item_source(session_name: str, item: dict) -> str:
    """The ``Source:`` line for a library item attached to a message in chat *session_name*:
    the link the item was saved from, masked as its content is. The link is part of what the
    user attached, so it is the user's for the chat's web_fetch, as a link they pasted would be.
    ``""`` when the item was saved from no link."""
    source, _ = redact_credentials(str(item.get("url") or "").strip())
    source, _ = redact_exfiltration_urls(source)
    if not source:
        return ""
    from personalclaw.web.fetch import record_user_message_urls

    record_user_message_urls(_history_key_for(session_name), source)
    return f"\nSource: {source}"


def chat_usage(session: _ChatSession) -> Attribution:
    """Whose spend a model call made for one of *session*'s turns is: the chat's, recorded as the
    chat's own turns are (``chat_runner``'s turn row), so the chat's total holds it."""
    return Attribution(
        source=getattr(session, "_app", "") or "chat",
        session_key=_history_key_for(session.key),
        agent=session.agent or "",
    )


def candidate_history_keys(session_name: str) -> tuple[str, ...]:
    """Every key *session_name*'s conversation log could live under, in resolution order.

    The one place that knows a session's possible key SHAPES: the bare key (a
    channel-provider thread persists under its own key, exactly as the channel app wrote
    it) and the ``dashboard:`` form. Both :func:`resolve_history_key` and
    ``chat_persistence.session_key_exists`` used to build this pair inline, which is how
    "which files belong to this key" came to have two implementations that had to be kept
    in agreement by hand. Callers that need ONE key want
    :func:`persisted_history_key`; this is for the probes that must try all of them.
    """
    dash = _history_key_for(session_name)
    return (session_name,) if dash == session_name else (session_name, dash)


def resolve_history_key(conversation_log, session_name: str) -> str | None:
    """Provider-agnostically resolve the canonical persisted key for *session_name*.

    A chat session is either a dashboard-native session (persisted under the
    ``dashboard:`` namespace) or a CHANNEL-PROVIDER thread (Slack/Discord/…),
    which persists under its OWN bare key exactly as the channel app wrote it.
    Core must not assume a key SHAPE (no provider-specific pattern) — it just asks
    the conversation log which key actually has metadata:

      1. the key as given (a channel thread key is canonical as-is), then
      2. the dashboard-namespaced form (a dashboard session).

    Returns the key that has persisted metadata, or ``None`` if neither does."""
    if conversation_log is None:
        return None
    for candidate in candidate_history_keys(session_name):
        try:
            if conversation_log.get_metadata(candidate):
                return candidate
        except Exception:
            continue
    return None


def persisted_history_key(conversation_log, session_name: str) -> str:
    """THE owner of a chat session's ON-DISK identity: the key its file lives under.

    :func:`_history_key_for` answers a different, weaker question — "what does the
    dashboard namespace look like for this name?" It PREFIXES; it never looks at the
    disk. Passing its answer to a ``conversation_log`` read or write asserts a key
    SHAPE, and a channel-provider thread (which persists under its own bare key,
    exactly as the channel app wrote it) does not have that shape. The read then finds
    nothing and the write lands in a second, empty file beside the real transcript.

    :func:`resolve_history_key` is the part that actually knows: it asks the log which
    candidate key HAS metadata. But it answers ``None`` for a session with nothing
    persisted yet, which a reader wants (``None`` = "never persisted, do not
    materialise a phantom") and a writer does not (a brand-new session must still get
    a file). So the write side needs the resolution AND a fallback, and it had grown a
    copy-pasted three-line idiom in two places —
    ``resolve_history_key(log, k) or _history_key_for(k)`` in
    ``save_session_to_history`` and again in ``api_chat_session_detail`` — while eight
    other call sites simply skipped it and hand-formed the prefix. A duplicated idiom
    is not an owner; this function is, and every keyed ``conversation_log`` access in
    ``dashboard/`` routes through it or through ``resolve_history_key`` directly
    (``tests/test_session_key_one_owner_audit.py`` reds on a new bypass).

    Returns the resolved persisted key, falling back to the dashboard-namespaced form
    when nothing is persisted under either candidate — so for a never-yet-saved
    session the answer is byte-identical to the old hand-formed one, and the behaviour
    changes ONLY where a file already exists under a key the prefix would have missed.
    """
    return resolve_history_key(conversation_log, session_name) or _history_key_for(session_name)


def full_session_messages(state: DashboardState, session: _ChatSession) -> list[dict]:
    """THE owner of "the whole transcript for this session, oldest first".

    The in-memory buffer holds the session's whole file (``_seed_transcript`` never loads
    a window); a legacy tab chained across restarts also has OLDER sibling files, counted
    by ``_disk_older_count``, so the complete transcript is ``the older sibling head + the
    buffer``. That splice is what makes an INDEX into this list meaningful: the visible
    user/assistant position inside it is ``at_message_index``, the coordinate
    ``POST .../fork`` and edit-resend speak.

    Extracted because a second reader arrived. ``GET /api/chat/sessions/{session}``
    built the splice inline, and ``GET /api/chat/sessions/{session}/map`` has to index
    the IDENTICAL list — derive the map from the bare buffer instead and every
    coordinate it publishes is off by ``_disk_older_count`` visible messages, silently
    addressing the wrong turn. One splice, one meaning.

    ``_disk_older_count`` is the stable slice boundary (set at restore/resume, never
    drifting as new messages arrive), and an unreadable log degrades to the buffer
    alone rather than failing the read — a partial transcript is still navigable.
    """
    mem_msgs = list(session.messages)
    if session._disk_older_count <= 0 or not state.conversation_log:
        return mem_msgs
    history_key = persisted_history_key(state.conversation_log, session.key)
    try:
        disk_msgs = state.conversation_log.read_messages_chained(history_key)
    except Exception:  # noqa: BLE001 — an unreadable log must not fail a transcript read
        logger.warning("read_messages_chained failed for %s", history_key, exc_info=True)
        disk_msgs = []
    older = disk_msgs[: session._disk_older_count] if disk_msgs else []
    return list(older) + mem_msgs


def _apply_incognito_prefix(session, message: str) -> str:
    """Prepend the incognito/temporary instruction for non-persistent sessions.

    The instruction text lives in the prompt system as a bundled snippet
    (``session-incognito`` / ``session-temporary``), rendered here and separated
    from the message by the blank line the snippet omits."""
    from personalclaw.prompt_providers.runtime import render_snippet_block

    if session.memory_mode == "temporary":
        return render_snippet_block("session-temporary") + "\n\n" + message
    if session.memory_mode == "incognito":
        return render_snippet_block("session-incognito") + "\n\n" + message
    return message


# Themes that carry a PERSONA — a bundled ``persona-<id>`` prompt snippet appended
# on a new session's first turn. A CLOSED set: the value
# arrives from a client, and it selects a snippet name, so an open set would let a
# caller name any snippet in the prompt store. Lumon is entry #1 rather than a
# special case — the hardcoded branch it replaced is gone (clean break).
_PERSONA_THEMES: frozenset[str] = frozenset({"lumon", "retro-terminal", "claw-arcade"})


def persona_themes() -> frozenset[str]:
    """The themes that carry a persona. Shared with the request-validation site so
    the accepted values and the injectable set can never drift apart."""
    return _PERSONA_THEMES


def _maybe_inject_persona(message: str, color_theme: str, is_new: bool) -> str:
    """Append the theme's persona to *message* on a new session's first turn.

    Session-scoped by design: the snippet itself tells the model to drop the voice
    if the user switches themes mid-session, so injecting once is correct rather
    than a limitation.
    """
    if not is_new or color_theme not in _PERSONA_THEMES:
        return message
    try:
        text = _cached_persona(color_theme)
        if text:
            tag = f"{color_theme.upper().replace('-', ' ')} PERSONA"
            return message + f"\n[{tag}]\n{text}\n[END {tag}]\n\n"
        return message
    except Exception:
        logger.warning("Persona injection failed for theme %r", color_theme, exc_info=True)
        return message


@functools.lru_cache(maxsize=len(_PERSONA_THEMES) or 1)
def _cached_persona(theme: str) -> str:
    """Load and cache one theme's persona snippet.

    The snippet is the bundled ``persona-<theme>`` (editable in Settings →
    Prompts), rendered raw (no variables). Only callers that already checked
    ``_PERSONA_THEMES`` reach here, so the name can't be attacker-chosen."""
    from personalclaw.prompt_providers.runtime import render_snippet_block

    return render_snippet_block(f"persona-{theme}")


def _project_context_preamble(project_id: str) -> str:
    """First-turn context block for a project-bound chat (Slice 6 D2): tells the
    agent which Project it's scoped to, its workspace, the loop history run on it, and
    the additional-context dir — so a project chat shares the project's cohesive
    context (every loop + chat under a project can read the others' outcomes). Empty on
    any failure or unknown project (best-effort, never blocks the turn)."""
    try:
        from personalclaw.tasks.hierarchy import HierarchyStore

        store = HierarchyStore()
        proj = store.get_project(project_id)
        if proj is None:
            return ""
        # The framing ([PROJECT CONTEXT] … [END PROJECT CONTEXT]) lives in the
        # prompt system (bundled ``project-context`` snippet); we assemble only the
        # dynamic detail lines here and render them into it below.
        lines: list[str] = []
        # The user-authored project brief — the goal/scope/background, shared with every
        # agent working on any session OR loop in this project (parity with the loop
        # brief's _project_brief_block). Foundational context, so it leads the preamble.
        brief = str(getattr(proj, "brief", "") or "").strip()
        if brief:
            lines.append(
                f"- Project brief (the goal/scope/background of this project — treat as foundational context): {brief}"  # noqa: E501
            )
        # The LIVING overview and the wayfinder ledgers, appended to
        # THIS composer rather than a second one. A parallel project-context builder would drift,
        # and an agent that sees a different project description in chat than in a run gives
        # answers nobody can reconcile. Overview is current state; the decisions ledger is history —
        # kept as separate lines because collapsing them loses one or the other.
        try:
            from personalclaw import project_context as _pctx

            overview = _pctx.read_overview(project_id)
            if overview:
                lines.append(
                    "- Project overview (CURRENT state — what this project now knows; revised as "
                    f"runs complete, distinct from the append-only history below): {overview}"
                )
            decisions = _pctx.read_ledger(project_id, "decisions")
            if decisions:
                lines.append(f"- Decisions so far ({len(decisions)}, newest last):")
                for entry in decisions[-8:]:
                    lines.append(f"    • {entry}")
            fog = _pctx.read_ledger(project_id, "fog")
            if fog:
                lines.append(
                    "- Not yet specified (open questions not precise enough to be tasks — "
                    "ask rather than assume):"
                )
                for entry in fog[:8]:
                    lines.append(f"    • {entry}")
            out_of_scope = _pctx.read_ledger(project_id, "out_of_scope")
            if out_of_scope:
                lines.append(
                    "- Out of scope (deliberately excluded — do NOT re-propose these without "
                    "the user redrawing the brief):"
                )
                for entry in out_of_scope[:8]:
                    lines.append(f"    • {entry}")
        except Exception:
            logger.debug("project living context for preamble failed", exc_info=True)
        ws = str(getattr(proj, "workspace_dir", "") or "").strip()
        if ws:
            lines.append(f"- Workspace: {ws}")
        try:
            cdir = str(store.context_dir(project_id))
        except Exception:
            cdir = ""
        if cdir:
            lines.append(
                f"- Project context directory (shared outcomes + intermediate files from this project's loops + chats — read it for continuity): {cdir}"  # noqa: E501
            )
            # List what's actually IN the context dir so the chat knows the shared
            # context that exists (e.g. decisions.md a loop wrote) without guessing —
            # the path alone left the agent unable to enumerate it (Slice 6 gap).
            try:
                from pathlib import Path

                from personalclaw.project_context import inlined_context_files

                # Files whose CONTENT is already inlined above are excluded from the listing.
                # Measured on a live project: the overview and all three ledgers appeared both as
                # inlined text and as "read any for continuity", inviting the agent to spend four
                # tool calls re-reading what it had already been given — and a listing that
                # recommends redundant work is one an agent learns to ignore wholesale.
                already = inlined_context_files(project_id)
                entries = sorted(
                    (
                        p
                        for p in Path(cdir).iterdir()
                        if p.is_file() and not p.name.startswith(".") and p.name not in already
                    ),
                    key=lambda p: p.name,
                )
                if entries:
                    # Wording tracks whether anything WAS excluded: "Files in it" over-claims
                    # completeness when four were held back, and "Other files" is confusing when
                    # nothing was.
                    header = "Other files in it" if already else "Files in it"
                    lines.append(f"    {header} (read any for continuity):")
                    for p in entries[:30]:
                        try:
                            kb = max(1, round(p.stat().st_size / 1024))
                        except OSError:
                            kb = 0
                        lines.append(f"    • {p.name}" + (f" (~{kb}KB)" if kb else ""))
            except Exception:
                logger.debug("project context-dir listing for preamble failed", exc_info=True)
        try:
            from personalclaw.loop import store as _loop_store

            loops = _loop_store.list_for_project(project_id)
            if loops:
                lines.append(f"- Loops run on this project ({len(loops)}):")
                for lp in loops[:12]:
                    lines.append(f"    • [{lp.kind}] {lp.name} — {lp.status}")
        except Exception:
            logger.debug("project loop history for preamble failed", exc_info=True)
        from personalclaw.prompt_providers.runtime import render_snippet_block

        return render_snippet_block(
            "project-context",
            {
                "project_name": str(getattr(proj, "name", project_id)),
                "project_details": "\n".join(lines),
            },
        )
    except Exception:
        logger.debug("project context preamble failed for %s", project_id, exc_info=True)
        return ""


def _maybe_consolidate(state, session) -> None:
    """Run the SESSION_END consolidation envelope, gated by the LearningGate.

    Consolidation is the SESSION_END cadence (LEARNING-FLYWHEEL §3.3). Its permission question —
    "may this session teach us anything?" — is the gate's job, not this site's: routing it through
    `LearningGate.decide(Cadence.SESSION_END, ...)` is what makes the restriction check identical
    to every other cadence's, and it is the live `Cadence.SESSION_END` reference that lets
    `assert_gate_covers_cadences()` see this cadence as wired. A denial (ephemeral, incognito,
    temporary, or learning disabled) is audited with its reason rather than silently skipped.

    Asked as the session's own pass, never as the work of the turn that just ended
    (``memory_writes.as_its_session``): consolidation reads the whole conversation and takes only
    the owner's words from it, so who asked for that turn (``memory_writes.asker``) decides
    nothing of it. The session's mode and the app that started it still do.
    """
    if not state.consolidator:
        return
    from personalclaw import memory_writes
    from personalclaw.learning.gate import Cadence, LearningGate

    with memory_writes.as_its_session(session):
        decision = LearningGate.for_session(session).decide(Cadence.SESSION_END)
    if decision.permitted:
        state.consolidator.maybe_consolidate(_history_key_for(session.key))
    else:
        sel().log_api_access(
            caller=f"dashboard:{session.key}",
            operation="consolidate",
            outcome="denied",
            source="dashboard",
            resources=f"gate:{decision.reason.value}",
        )


def _redact_for_display(text: str) -> str:
    """Apply all redaction passes for dashboard/WS display."""
    text, _ = redact_exfiltration_urls(text)
    text, _ = redact_credentials(text)
    return text


def _remove_queued_by_id(messages: list[dict], queue_id: str) -> bool:
    """Remove a 'queued' placeholder by queue_id stored in cls JSON."""
    for i, m in enumerate(messages):
        if m.get("role") != "queued":
            continue
        try:
            cls = json.loads(m.get("cls", "{}"))
            if cls.get("queue_id") == queue_id:
                del messages[i]
                return True
        except (json.JSONDecodeError, TypeError):
            pass
    return False


def _dequeue_next_message(session, merge_enabled: bool) -> tuple:
    """Drain the queue: merge non-cron messages or pop the first one.

    A retry (`_ChatSession.queue_retry`) always runs alone: it is the turn that just ended, sent
    again, and merged it would become a new message carrying hers a second time. So does a message
    whose row is in the chat already (`chat_queue.ON_RECORD`), for the same reason.
    """
    if session._queue and (session._queue[0].get("retry") or session._queue[0].get(ON_RECORD)):
        item = session.queue_pop(0)
        return item["content"], [item]
    if merge_enabled and len(session._queue) > 1:
        to_merge: list[dict] = []
        for item in list(session._queue):
            if (
                item.get(ON_RECORD)
                or item["content"].startswith(CRON_NOTIFY_PREFIX)
                or item["content"].startswith(SUBAGENT_COMPLETION_PREFIX)
            ):
                break
            to_merge.append(item)
        if len(to_merge) > 1:
            del session._queue[: len(to_merge)]
            merged = "\n\n".join(item["content"] for item in to_merge)
            return f"[{len(to_merge)} queued messages merged]\n\n{merged}", to_merge
    item = session.queue_pop(0)
    return item["content"], [item]


def _prepare_messages(messages: list[dict], running: bool) -> list[dict]:
    """Prepare messages for API response — one served message per transcript entry.

    The buffer holds transcript entries only (``_ChatSession.append``), so this maps each
    one to its wire form and drops none; the chat list's count
    (``_ChatSession.message_count``) relies on that. The answer still streaming is ONE
    ``streaming`` entry, served in the exact shape the client has always hydrated.
    """
    out: list[dict] = []
    for m in messages:
        role = m.get("role", "")
        text = m.get("content", "")
        if role == "streaming":
            out.append(
                {"role": "streaming", "content": _redact_for_display(text), "cls": "msg msg-a"}
            )
            continue
        if role not in ("user", "system") and text:
            text, _ = redact_exfiltration_urls(text)
            text, _ = redact_credentials(text)
            m = {**m, "content": text}
        msg_out = dict(m)
        if msg_out.get("variants"):
            msg_out["variants"] = [
                {
                    **v,
                    "content": redact_credentials(
                        redact_exfiltration_urls(v.get("content", ""))[0]
                    )[0],
                }
                for v in msg_out["variants"]
                if isinstance(v, dict)
            ]
        # Rewind tails: redact non-user snapshot content on the
        # wire, matching the main transcript's redaction (user content is left
        # as-is, exactly like the primary user messages above).
        if isinstance(msg_out.get("rewound"), list):
            redacted_chain: list[dict] = []
            for snap in msg_out["rewound"]:
                if not isinstance(snap, dict) or not isinstance(snap.get("messages"), list):
                    continue
                snap_msgs = []
                for sm in snap["messages"]:
                    if not isinstance(sm, dict):
                        continue
                    sc = sm.get("content", "")
                    if sm.get("role") not in ("user", "system") and sc:
                        sc, _ = redact_exfiltration_urls(sc)
                        sc, _ = redact_credentials(sc)
                    snap_msgs.append({**sm, "content": sc})
                redacted_chain.append({**snap, "messages": snap_msgs})
            msg_out["rewound"] = redacted_chain
        meta = parse_cls_meta(m.get("cls", ""))
        if meta is not None:
            msg_out["meta"] = meta
        out.append(msg_out)
    return out

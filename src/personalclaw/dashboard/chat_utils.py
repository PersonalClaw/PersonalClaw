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
    from personalclaw.llm.base import LLMEvent

from personalclaw import task_modes
from personalclaw.dashboard.state import (
    CRON_NOTIFY_PREFIX,
    SUBAGENT_COMPLETION_PREFIX,
    DashboardState,
    _ChatSession,
    parse_cls_meta,
)
from personalclaw.security import redact_credentials, redact_exfiltration_urls
from personalclaw.sel import SecurityEvent, sel
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
    session: "_ChatSession", title: str, tool_kind: str, tool_input: object
) -> str:
    """Return a deny-reason for the session's TASK mode, or '' to allow the tool.

    Thin session-aware wrapper over the canonical gate in ``task_modes`` (the same
    logic the native runtime enforces before approval). Note: unlike the runtime,
    which gates EVERY mode here, this dashboard-side path still treats ``plan`` via
    the dedicated plan branch in ``chat_runner`` — so it forwards plan to the shared
    gate too (which now allows read-only inspection in plan, blocking only writes).
    """
    mode = getattr(session, "_task_mode", "agent")
    return task_modes.task_mode_denies(mode, title, tool_kind, tool_input)


# Per-task-mode system-prompt framing — appended to the agent's system prompt so
# the model FRAMES the work to match the mode (the tool gate enforces it; this
# shapes intent + output so the agent doesn't fight the gate). 'agent' adds nothing.
# Shared tail for the restricted modes (ask/plan/build). The model can't flip the
# mode itself (a silent self-escalation out of a read-only posture would defeat the
# point of Ask/Plan and is a prompt-injection hole), but it CAN propose a switch the
# user approves with one click — the same propose->approve handshake as a tool
# approval. End the reply with a [SWITCH_TO_AGENT: <continuation>] marker; the UI
# renders it as a primary button that flips the session to Agent AND runs the
# continuation. This is the affordance to use — NOT an [OPTIONS: …] chip (which only
# re-sends literal text and leaves the session in the restricted mode).
_SWITCH_HINT = (
    " You cannot change the mode yourself, but you can offer a one-click switch: when "
    "the user wants you to actually do the work, end your reply with a marker "
    "[SWITCH_TO_AGENT: <short imperative continuation>] — e.g. "
    "[SWITCH_TO_AGENT: create the file] or [SWITCH_TO_AGENT: execute the plan above]. "
    "The UI turns it into a 'Switch to Agent & run it' button; clicking it flips this "
    "session to Agent mode and runs your continuation. Use this marker for the switch, "
    "never an [OPTIONS: …] chip."
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


def tool_input_to_str(value: object) -> str:
    """Coerce an event's ``tool_input`` to a display string.

    ``AgentEvent.tool_input`` is typed ``Any``: ACP agents pass the raw JSON
    argument *string*, the native loop passes the parsed *dict*, and other
    providers may pass anything. Display/redaction code slices the result
    (``[:4000]``), so it must be a string: dicts/lists are JSON-encoded,
    ``None`` becomes ``""``, everything else is ``str()``.
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (dict, list)):
        try:
            return json.dumps(value, default=str)
        except (TypeError, ValueError):
            return str(value)
    return str(value)


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


def _broadcast_compaction_result(
    state: DashboardState, session: _ChatSession, event: "LLMEvent"
) -> str | None:
    """Broadcast compaction completed/failed to the session. Returns message text or None."""
    status_type = event.text
    if status_type == "completed":
        summary, _ = redact_credentials(event.title)
        summary, _ = redact_exfiltration_urls(summary)
        msg_text = f"Conversation compacted: {summary}" if summary else "Conversation compacted."
    elif status_type == "failed":
        error, _ = redact_credentials(event.title or "unknown error")
        error, _ = redact_exfiltration_urls(error)
        msg_text = f"Compaction failed: {error}"
    else:
        return None
    session.append("assistant", msg_text, "msg msg-a")
    state.broadcast_ws(
        "chat_message",
        {"session": session.key, "role": "assistant", "content": msg_text},
    )
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
    resolve under their own bare provider key; see ``resolve_history_key``)."""
    if session_name.startswith("dashboard:"):
        return session_name
    while session_name.startswith("dashboard_"):
        session_name = session_name[len("dashboard_") :]
    return f"dashboard:{session_name}"


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
    try:
        if conversation_log.get_metadata(session_name):
            return session_name
    except Exception:
        pass
    dash = _history_key_for(session_name)
    if dash != session_name:
        try:
            if conversation_log.get_metadata(dash):
                return dash
        except Exception:
            pass
    return None


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


def _maybe_inject_persona(message: str, color_theme: str, is_new: bool) -> str:
    """Append Lumon persona to *message* on first turn when theme is 'lumon'."""
    if color_theme != "lumon" or not is_new:
        return message
    try:
        text = _cached_lumon_persona()
        if text:
            return message + f"\n[LUMON PERSONA]\n{text}\n[END LUMON PERSONA]\n\n"
        return message
    except Exception:
        logger.warning("Lumon persona injection failed", exc_info=True)
        return message


@functools.lru_cache(maxsize=1)
def _cached_lumon_persona() -> str:
    """Load and cache the Lumon persona from the prompt system.

    The persona is the bundled ``persona-lumon`` snippet (editable in
    Settings → Prompts), rendered raw (no variables)."""
    from personalclaw.prompt_providers.runtime import render_snippet_block

    return render_snippet_block("persona-lumon")


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

                entries = sorted(
                    (p for p in Path(cdir).iterdir() if p.is_file() and not p.name.startswith(".")),
                    key=lambda p: p.name,
                )
                if entries:
                    lines.append("    Files in it (read any for continuity):")
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
    """Run memory consolidation unless session is restricted."""
    if state.consolidator and not session.is_restricted:
        state.consolidator.maybe_consolidate(_history_key_for(session.key))
    elif state.consolidator and session.is_restricted:
        sel().log_api_access(
            caller=f"dashboard:{session.key}",
            operation="consolidate",
            outcome="denied",
            source="dashboard",
            resources="restricted_session_block",
        )


def _sync_dashboard_sessions(state: "DashboardState") -> None:
    """Push current session keys to SessionManager so orphaned sessions get reaped."""
    state.sessions.set_active_dashboard_sessions({_history_key_for(k) for k in state._sessions})


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
    """Drain the queue: merge non-cron messages or pop the first one."""
    if merge_enabled and len(session._queue) > 1:
        to_merge: list[dict] = []
        for item in list(session._queue):
            if item["content"].startswith(CRON_NOTIFY_PREFIX) or item["content"].startswith(
                SUBAGENT_COMPLETION_PREFIX
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
    """Prepare messages for API response."""
    out: list[dict] = []
    chunk_text = ""
    for m in messages:
        role = m.get("role", "")
        if role == "chunk":
            chunk_text += m.get("content", "")
        elif role == "done":
            continue
        else:
            if chunk_text:
                redacted_chunk, _ = redact_exfiltration_urls(chunk_text)
                redacted_chunk, _ = redact_credentials(redacted_chunk)
                out.append({"role": "streaming", "content": redacted_chunk, "cls": "msg msg-a"})
                chunk_text = ""
            text = m.get("content", "")
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
            meta = parse_cls_meta(m.get("cls", ""))
            if meta is not None:
                msg_out["meta"] = meta
            out.append(msg_out)
    if chunk_text:
        redacted_chunk, _ = redact_exfiltration_urls(chunk_text)
        redacted_chunk, _ = redact_credentials(redacted_chunk)
        out.append({"role": "streaming", "content": redacted_chunk, "cls": "msg msg-a"})
    return out

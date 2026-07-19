"""Core LLM runner — _run_chat, segment flushing, prompt expansion."""

import asyncio
import json
import logging
import time
from pathlib import Path

from personalclaw.acp.errors import AcpError, AcpProcessDied
from personalclaw.acp.types import (
    EVENT_AGENT_SWITCHED,
    EVENT_CLEAR_STATUS,
    EVENT_COMPACTION_STATUS,
    STOP_REASON_CANCELLED,
    STOP_REASON_END_TURN,
)
from personalclaw.config.loader import (
    AppConfig,
    config_dir,
    resolve_agent_bindings,
)
from personalclaw.constants import CHAT_TURN_TIMEOUT
from personalclaw.context_engine import assemble_context
from personalclaw.dashboard.chat_persistence import _build_history_prefix, _save_session_to_history
from personalclaw.dashboard.chat_title import _maybe_auto_title
from personalclaw.dashboard.chat_utils import (
    _BLOCKED_SLASH_COMMANDS,
    _SLASH_COMMANDS,
    _apply_incognito_prefix,
    _broadcast_auto_tool,
    _broadcast_compaction_result,
    _dequeue_next_message,
    _extract_bash_command,
    _history_key_for,
    _maybe_consolidate,
    _maybe_inject_persona,
    _normalize_model,
    _project_context_preamble,
    _redact_for_display,
    _validate_tool_name,
    strip_status_sentinel,
    task_mode_denies,
    task_mode_framing,
    tool_input_to_str,
)
from personalclaw.dashboard.handlers import MAX_PROMPT_BYTES, _list_provider_prompts
from personalclaw.llm_helpers import humanize_provider_error
from personalclaw.dashboard.state import (
    CRON_NOTIFY_PREFIX,
    CRON_NOTIFY_RE,
    SUBAGENT_COMPLETION_PREFIX,
    DashboardState,
    _ChatSession,
    is_read_only_bash,
    resolve_effective_risk,
)
from personalclaw.hooks import (
    HOOK_EVENT_AGENT_SPAWN,
    HOOK_EVENT_ERROR,
    HOOK_EVENT_POST_TOOL_USE,
    HOOK_EVENT_PRE_TOOL_USE,
    HOOK_EVENT_SESSION_START,
    HOOK_EVENT_STOP,
    HOOK_EVENT_USER_PROMPT_SUBMIT,
    TOOL_AUTO_APPROVE,
    TOOL_DENY,
    fire_tool_hooks,
)
from personalclaw.llm_helpers import PromptBusyExhaustedError
from personalclaw.llm.base import (
    EVENT_COMPLETE,
    EVENT_PERMISSION_REQUEST,
    EVENT_TEXT_CHUNK,
    EVENT_THINKING_CHUNK,
    EVENT_TOOL_CALL,
    EVENT_TOOL_CALL_UPDATE,
    EVENT_TOOL_RESULT,
)
from personalclaw.security import is_sensitive_path, redact_credentials, redact_exfiltration_urls
from personalclaw.sel import sel
from personalclaw.stats import Stats
from personalclaw.validation import ValidationError, validate_ask_user_question

logger = logging.getLogger(__name__)


def is_empty_turn(
    *,
    assistant_text: str,
    stop_reason: str,
    saw_compaction: bool,
    needs_session_reset: bool,
    is_slash: bool,
    tool_call_count: int,
    is_loop: bool,
) -> bool:
    """True iff a completed turn produced nothing and is worth auto-retrying.

    An empty turn has no final assistant prose AND made no tool calls. It is NOT
    counted empty (a benign no-op) when it was a user cancel, a compaction /
    clear / agent-switch turn (each emits its own status line), a slash command,
    a tool-only turn (the agent did real work, just no closing prose), or a goal
    loop worker turn (loops own a dedicated deliverable-forcing re-prompt loop,
    so the generic retry must stand aside).
    """
    if assistant_text.strip():
        return False
    benign = (
        stop_reason == STOP_REASON_CANCELLED
        or saw_compaction
        or needs_session_reset
        or is_slash
        or tool_call_count > 0
        or is_loop
    )
    return not benign


def _maybe_after_turn_review(state, session, user_message: str, assistant_text: str, tool_calls: int, provider=None) -> None:
    """Run the after-turn self-improvement review when the turn warrants it.

    Gated (config + non-ephemeral + correction-or-≥N-tools); the actual capture
    is best-effort and synchronous-but-cheap (a heuristic + a guarded
    write_lesson — no LLM call in this path). Surfaces a quiet 'Learned: …' chip.
    """
    from personalclaw import after_turn_review as atr
    from personalclaw.config.loader import AppConfig

    cfg = AppConfig.load().learning
    # Restricted (incognito/temporary) sessions promise "no memory writes" —
    # they must never feed durable learning. `_ephemeral` is the separate
    # legacy flag; `is_restricted` is the memory_mode contract.
    if (
        not getattr(cfg, "enabled", True)
        or bool(getattr(session, "_ephemeral", False))
        or bool(getattr(session, "is_restricted", False))
    ):
        return
    from personalclaw.memory_service import service_for

    memory = state.context_builder.get_memory_for(
        session.workspace_dir or None, getattr(session, "memory_store", None)
    )
    svc = service_for(memory)
    # Preference-facet capture is a cheap no-LLM heuristic and the passive-learning
    # core — it must run on EVERY (non-ephemeral) turn, NOT be gated behind the
    # expensive-review threshold (≥N tools / correction). A bare conversational
    # style nudge ("keep it concise") does no tool work and isn't a correction, so
    # gating it there silently dropped the common case. Run it first, unconditionally.
    facet_learned = atr.capture_preference_facet(svc, user_message)
    if facet_learned and getattr(cfg, "surface_chip", True):
        _flabel, _ = redact_credentials(redact_exfiltration_urls(facet_learned[:200])[0])
        state.broadcast_ws(
            "activity_event",
            {"session": session.key, "kind": "learned", "text": f"Learned: {_flabel}"},
        )
    # The expensive review (procedural drain + correction→lesson) stays gated.
    correction = atr.is_correction_signal(user_message)
    if not atr.should_review(
        enabled=getattr(cfg, "enabled", True),
        is_ephemeral=bool(getattr(session, "_ephemeral", False)),
        correction=correction,
        tool_calls=tool_calls or 0,
        min_tool_calls=getattr(cfg, "min_tool_calls", 4),
        correction_heuristic=getattr(cfg, "correction_heuristic", True),
    ):
        return
    # Procedural memory (M5d): drain this turn's tool outcomes (native runtime
    # only — ACP providers don't accumulate them) into how-to-work priors. The
    # provider is the ModelProvider returned by get_or_create (threaded in by the
    # caller) — the dashboard session has no `.provider` attribute, so reading it
    # off the session silently no-oped this whole class.
    drain = getattr(provider, "drain_tool_outcomes", None)
    if callable(drain):
        try:
            atr.record_procedural_outcomes(svc, drain(),
                                           scope_ref=session.workspace_dir or None)
        except Exception:
            logger.debug("procedural outcome capture failed", exc_info=True)
    learned = atr.run_after_turn_review(
        service=svc,
        user_message=user_message,
        assistant_text=assistant_text,
        correction=correction,
        capture_facets=False,  # already captured before the gate, above
    )
    if learned and getattr(cfg, "surface_chip", True):
        _label, _ = redact_credentials(redact_exfiltration_urls(learned[:200])[0])
        state.broadcast_ws(
            "activity_event",
            {"session": session.key, "kind": "learned", "text": f"Learned: {_label}"},
        )


def _maybe_skill_ladder_review(state, session, user_message: str, assistant_text: str, tool_calls: int) -> None:
    """Schedule the forked-LLM 4-tier skill-ladder review (learn-after-turn-review
    skill axis) as a background task — non-blocking, never delays the next turn.

    Same gate as the memory review (enabled + non-ephemeral + correction-or-≥N-tools),
    plus its own ``skill_ladder`` flag. Every skill it decides on is ENQUEUED as a
    propose-only proposal (never a live write). Best-effort; a 'Proposed skill: …'
    chip surfaces if something lands."""
    import asyncio

    from personalclaw import after_turn_review as atr
    from personalclaw.config.loader import AppConfig

    cfg = AppConfig.load().learning
    if not getattr(cfg, "enabled", True) or not getattr(cfg, "skill_ladder", True):
        return
    # Same restricted-session guard as the memory review: incognito/temporary
    # sessions must not seed skill proposals from their content.
    if bool(getattr(session, "_ephemeral", False)) or bool(
        getattr(session, "is_restricted", False)
    ):
        return
    correction = atr.is_correction_signal(user_message)
    if not atr.should_review(
        enabled=getattr(cfg, "enabled", True),
        is_ephemeral=bool(getattr(session, "_ephemeral", False)),
        correction=correction,
        tool_calls=tool_calls or 0,
        min_tool_calls=getattr(cfg, "min_tool_calls", 4),
        correction_heuristic=getattr(cfg, "correction_heuristic", True),
    ):
        return
    # Candidate skills to bias refinement toward (the always-on + indexed set).
    try:
        loaded = [s["key"] for s in state.context_builder.skills.list_skills()][:40]
    except Exception:
        loaded = []

    async def _run() -> None:
        try:
            summary = await atr.run_skill_ladder_review(
                session_key=session.key,
                user_message=user_message,
                assistant_text=assistant_text,
                loaded_skills=loaded,
            )
        except Exception:
            logger.debug("skill-ladder review failed", exc_info=True)
            return
        if summary and getattr(cfg, "surface_chip", True):
            label, _ = redact_credentials(redact_exfiltration_urls(summary[:200])[0])
            state.broadcast_ws(
                "activity_event",
                {"session": session.key, "kind": "learned", "text": label},
            )

    try:
        t = asyncio.create_task(_run())
        state._background_tasks.add(t)
        t.add_done_callback(state._background_tasks.discard)
    except RuntimeError:
        logger.debug("skill-ladder review: no running loop to schedule on", exc_info=True)


def _agent_label(session: object) -> str:
    """Telemetry/display agent name for a session.

    Falls back to the seeded default agent (native ``PersonalClaw``) rather than
    a hardcoded ``"personalclaw"`` literal, so logs/labels track the configured
    default. Resolution is name-only (no provider build), safe on any hot path.
    """
    from personalclaw.agents.defaults import DEFAULT_NATIVE_AGENT_NAME

    return getattr(session, "agent", "") or DEFAULT_NATIVE_AGENT_NAME


def _resolve_agent_id(agent: str | None, provider_kind: str, provider_agent: str | None) -> str:
    """Normalize the turn's agent to the workflow scope_ref binding-id form."""
    from personalclaw.workflows.composition import resolve_agent_id

    return resolve_agent_id(agent, provider_kind, provider_agent)


def _redact_text(text: str) -> str:
    """Strip credentials + exfiltration URLs from a user-facing string."""
    return redact_credentials(redact_exfiltration_urls(text)[0])[0]


_TOOL_INPUT_OBJ_MAX = 8000  # cap the serialized structured input shipped to the UI


def _redact_tool_input_obj(tool_input: object) -> dict | None:
    """Structured tool input for schema-driven rendering — a dict with each string
    value redacted (credentials + exfil URLs), bounded in total size.

    Returns ``None`` for non-dict input (ACP passes a string) so the UI falls back
    to the string ``input_preview`` exactly as before. The same redaction the
    string preview gets is applied per value, so shipping the object never leaks
    what the string path would have stripped."""
    if not isinstance(tool_input, dict):
        return None

    def _red(v: object) -> object:
        if isinstance(v, str):
            return _redact_text(v)
        if isinstance(v, dict):
            return {k: _red(x) for k, x in v.items()}
        if isinstance(v, list):
            return [_red(x) for x in v]
        return v

    try:
        obj = {str(k): _red(v) for k, v in tool_input.items()}
    except Exception:
        return None
    # Bound the shipped size: if the serialized object is huge, drop it (the
    # string preview is already capped + shipped alongside).
    import json as _json
    try:
        if len(_json.dumps(obj, default=str)) > _TOOL_INPUT_OBJ_MAX:
            return None
    except (TypeError, ValueError):
        return None
    return obj


def _emit_question_card(
    state: DashboardState, session_key: str, tool_input: object, tool_call_id: str | None
) -> None:
    """Broadcast a ``question_card`` frame for an ``AskUserQuestion`` tool call.

    Validates + normalizes the raw tool input, redacts every user-facing string,
    then broadcasts. A malformed payload is logged and skipped (no frame) so a
    garbled tool call can never break the turn or the card UI. The card is
    additive to the tool-call pill — it does not gate the tool's own result.
    """
    if not tool_input:
        return
    try:
        # tool_input is Any (ACP → JSON str; native loop → dict). Accept either.
        raw = tool_input if isinstance(tool_input, dict) else json.loads(tool_input)
        questions = validate_ask_user_question(raw)
    except (json.JSONDecodeError, TypeError, ValidationError) as exc:
        logger.warning("AskUserQuestion card skipped: %s", exc)
        return
    for q in questions:
        q["question"] = _redact_text(q["question"])
        q["header"] = _redact_text(q["header"])
        for opt in q["options"]:
            opt["label"] = _redact_text(opt["label"])
            opt["description"] = _redact_text(opt["description"])
    state.broadcast_ws(
        "question_card",
        {"session": session_key, "tool_call_id": tool_call_id, "questions": questions},
    )


# File-change chips: the native default agent's write tools.
_WRITE_FILE_TOOLS = {"write_file", "edit_file"}
# Per-file snapshot cap (chars). Diffs of huge files are not useful inline and
# would bloat persisted meta; truncate with a marker.
_MAX_FILE_SNAPSHOT = 200_000


def _file_change_base(session: _ChatSession) -> Path:
    """The workspace base a write tool resolves paths against — mirror
    NativeBuiltinToolProvider._resolve (cwd = session.workspace_dir or root)."""
    from personalclaw.config.loader import workspace_root

    return Path(session.workspace_dir).resolve() if session.workspace_dir else workspace_root()


def _truncate_snapshot(text: str) -> str:
    if len(text) > _MAX_FILE_SNAPSHOT:
        return text[:_MAX_FILE_SNAPSHOT] + "\n… [truncated]"
    return text


def _capture_file_change(session: _ChatSession, tool_name: str, tool_input: object) -> None:
    """On a native write_file/edit_file CALL, snapshot before+after for the chip.

    Robust by construction: ``before`` is read off disk (empty for a new file);
    ``after`` is computed in-memory from the call args (write_file → ``content``;
    edit_file → before with the first ``old_str`` replaced), so no racy turn-end
    re-read is needed. Never raises — a snapshot failure must not break the turn.
    Sensitive paths are skipped (don't surface secrets in a diff chip).
    """
    if tool_name not in _WRITE_FILE_TOOLS:
        return
    try:
        args = tool_input if isinstance(tool_input, dict) else json.loads(tool_input_to_str(tool_input))
        if not isinstance(args, dict):
            return
        rel = str(args.get("path") or "")
        if not rel:
            return
        base = _file_change_base(session)
        target = (base / rel).resolve() if not Path(rel).is_absolute() else Path(rel).resolve()
        # Confinement: only snapshot inside the workspace (matches the tool's gate).
        if base != target and base not in target.parents:
            return
        if is_sensitive_path(str(target)):
            return
        before = ""
        if target.is_file():
            try:
                before = target.read_text(encoding="utf-8", errors="replace")
            except Exception:
                return  # binary/unreadable → skip silently
        if tool_name == "write_file":
            after = str(args.get("content", ""))
        else:  # edit_file: mirror the tool impl (1 replacement, or all when replace_all)
            old, new = str(args.get("old_str", "")), str(args.get("new_str", ""))
            n = -1 if args.get("replace_all") else 1
            after = before.replace(old, new, n) if old and old in before else before
        if before == after:
            return  # no-op write → no chip
        session._file_changes.append({
            "path": rel,
            "before": _truncate_snapshot(before),
            "after": _truncate_snapshot(after),
        })
    except Exception:
        logger.debug("file-change snapshot skipped for %s", tool_name, exc_info=True)


def _flush_file_changes(session: _ChatSession) -> None:
    """Attach accumulated file changes to the last assistant message's meta so the
    chips render + survive reload. Dedups per path (first ``before`` / last
    ``after``), redacts every field, then clears the accumulator."""
    changes = session._file_changes
    session._file_changes = []
    if not changes:
        return
    # Dedup by path: keep the earliest before and the latest after.
    merged: dict[str, dict[str, str]] = {}
    for c in changes:
        p = c["path"]
        if p in merged:
            merged[p]["after"] = c["after"]
        else:
            merged[p] = dict(c)
    out: list[dict[str, str]] = []
    for c in merged.values():
        path, _ = redact_credentials(redact_exfiltration_urls(c["path"])[0])
        before, _ = redact_credentials(redact_exfiltration_urls(c["before"])[0])
        after, _ = redact_credentials(redact_exfiltration_urls(c["after"])[0])
        out.append({"path": path, "before": before, "after": after})
    # Attach to the most recent assistant message (the turn's final answer).
    for m in reversed(session.messages):
        if m.get("role") == "assistant":
            m.setdefault("meta", {})["file_changes"] = out
            break


def _flush_segment(
    state: DashboardState,
    session: _ChatSession,
    assistant_text: str,
    *,
    broadcast: bool = True,
) -> None:
    """Finalize current text block as a segment and persist it."""
    # Remove trailing chunk messages (they belong to this segment).
    # Also pull aside any stop_event interleaved with this segment's chunks
    # so it lands AFTER the finalized assistant message. Historical
    # stop_events from prior turns stay in place.
    def _is_stop_event(m: dict) -> bool:
        cls_val = m.get("cls", "")
        if not cls_val or not isinstance(cls_val, str):
            return False
        try:
            parsed = json.loads(cls_val)
            return isinstance(parsed, dict) and parsed.get("kind") == "stop_event"
        except (json.JSONDecodeError, ValueError):
            return False
    # Walk backwards to find the start of the trailing chunk/stop_event run.
    boundary = len(session.messages)
    for i in range(len(session.messages) - 1, -1, -1):
        role = session.messages[i].get("role", "")
        if role == "chunk" or _is_stop_event(session.messages[i]):
            boundary = i
        else:
            break
    head = session.messages[:boundary]
    tail = session.messages[boundary:]
    trailing_stop_events = [m for m in tail if _is_stop_event(m)]
    session.messages = head  # drops chunks AND trailing stop_events; tail.non-chunk-non-stop stays in head
    # Redact the accumulated text
    redacted, exfil_warnings = redact_exfiltration_urls(assistant_text)
    for w in exfil_warnings:
        logger.warning("Exfiltration URL redacted in chat segment: %s", w)
    redacted, cred_warnings = redact_credentials(redacted)
    for w in cred_warnings:
        logger.warning("Credential redacted in chat segment: %s", w)
    # Persist as assistant message. Broadcast is kept enabled so that
    # other tabs viewing the same session receive the finalized text.
    # The active tab already has this content from streaming chunks;
    # the chat_segment event tells it to finalize streaming → assistant.
    session.append("assistant", redacted, "msg msg-a")
    last_msg: dict = session.messages[-1]
    # If a regenerate is pending, attach the stashed variants to this fresh assistant message.
    attached_variants = False
    if session._pending_variants:
        pending_list = [
            {**v, "content": redact_credentials(redact_exfiltration_urls(v.get("content", ""))[0])[0]}
            for v in session._pending_variants if isinstance(v, dict)
        ]
        pending_list.append({"content": redacted, "ts": last_msg.get("ts", "")})
        last_msg["variants"] = pending_list
        last_msg["variant_idx"] = len(pending_list) - 1
        session._pending_variants = []
        attached_variants = True
    # Re-append any stop_event that belongs to this segment's trailing run,
    # placed AFTER the finalized assistant message so the UI shows
    # prose → stop card.
    for ev in trailing_stop_events:
        session.messages.append(ev)
    # Tell the frontend to finalize streaming → assistant.
    if broadcast:
        state.broadcast_ws("chat_segment", {"session": session.key})
    # Notify the frontend about newly-attached regenerate variants so the ‹n/N›
    # switcher appears live. This is a metadata signal INDEPENDENT of the
    # streaming-finalize `chat_segment` above: the end-of-turn flush uses
    # broadcast=False (the active tab already streamed the text), but the switcher
    # still has to light up — so this fires whenever variants were just attached,
    # regardless of `broadcast`. Use last_msg (the assistant message), not
    # session.messages[-1] which may be a trailing stop_event.
    if attached_variants:
        state.broadcast_ws(
            "chat_variant_switch",
            {
                "session": session.key,
                "index": last_msg.get("variant_idx", 0),
                "count": len(last_msg.get("variants") or []),
                "content": redacted,
            },
        )


def _parse_inline_kwargs(text: str) -> tuple[str, dict[str, str]]:
    """Pull ``key=value`` pairs from the trailing user_text of an @prompt mention.

    Returns ``(remaining_text, kwargs)``. Quoting is supported via simple
    paired double-quotes so values may contain spaces. Anything that
    doesn't match ``key=...`` is left in remaining_text verbatim so the
    user can still pass freeform context after the variable bindings.
    """
    import shlex

    if not text:
        return "", {}
    try:
        tokens = shlex.split(text, posix=True)
    except ValueError:
        return text, {}
    kwargs: dict[str, str] = {}
    leftovers: list[str] = []
    for tok in tokens:
        if "=" in tok and tok.split("=", 1)[0].isidentifier():
            k, v = tok.split("=", 1)
            kwargs[k] = v
        else:
            leftovers.append(tok)
    return " ".join(leftovers).strip(), kwargs


def _expand_prompt_mention(
    message: str,
    state: DashboardState,
    session: _ChatSession,
) -> tuple[str, str]:
    """Expand ``@prompt-name [key=value ...] rest`` into rendered template + user text.

    Prompts are resolved through the registered PromptProvider: it renders
    ``{{var}}`` placeholders against declared typed variables, with values
    supplied inline as ``key=value`` tokens (or shell-quoted
    ``key="value with spaces"``). Required variables that are missing produce
    a block with a helpful system message — the user can re-issue with the
    missing bindings.

    Returns ``(expanded_message, "ok")`` on success,
    ``(original_message, "blocked")`` on render failure,
    ``(original_message, "too_large")`` when the rendered prompt exceeds the
    size limit, or ``(original_message, "not_found")`` when nothing matches.
    """
    if not message.startswith("@"):
        return message, "not_found"

    body = message[1:]
    parts = body.split(None, 1)
    mention = parts[0] if parts else body
    raw_tail = parts[1].strip() if len(parts) > 1 else ""
    user_text, inline_vars = _parse_inline_kwargs(raw_tail)

    bare = mention.split("/", 1)[-1] if "/" in mention else mention

    # Resolve through the registered PromptProvider (supports typed variables).
    try:
        from personalclaw.prompt_providers import get_default_provider, render_template
        from personalclaw.prompt_providers.base import PromptRenderError
        from personalclaw.prompt_providers.registry import _ensure_default_providers_registered

        _ensure_default_providers_registered()
        provider = get_default_provider()
    except Exception:
        provider = None

    tpl = provider.get_prompt(bare) if provider is not None else None
    if tpl is None:
        return message, "not_found"
    # Compose-aware: resolve {{> snippet}} includes through the same provider so a
    # @-mentioned prompt can pull in shared snippets just like the authoring/render UI.
    _resolver = (lambda n: provider.get_snippet(n)) if provider is not None else None
    try:
        content = render_template(tpl, inline_vars, resolver=_resolver)
    except PromptRenderError as exc:
        session.append(
            "system",
            f"Prompt **@{tpl.name}** could not be rendered: {exc}. "
            f"Provide values inline as `@{tpl.name} key=value`.",
            "msg msg-warn",
        )
        state.push_sessions_update()
        return message, "blocked"
    if len(content.encode("utf-8")) > MAX_PROMPT_BYTES:
        logger.warning(
            "Prompt %s exceeds max size (%d > %d bytes)",
            mention, len(content.encode("utf-8")), MAX_PROMPT_BYTES,
        )
        return message, "too_large"
    content, _ = redact_credentials(content)
    content, _ = redact_exfiltration_urls(content)
    # The expansion wrapper lives in the prompt system (bundled ``prompt-expansion``
    # snippet); fall back to the inline form if it can't resolve.
    from personalclaw.prompt_providers.runtime import render_snippet_block

    expanded = render_snippet_block("prompt-expansion", {"content": content, "user_text": user_text})
    if not expanded:
        expanded = f"Execute the following instructions:\n\n{content}"
        if user_text:
            expanded += f"\n\n---\nAdditional context from user: {user_text}"
    session.append(
        "system",
        f"Loaded prompt **@{tpl.name}** ({len(content):,} chars rendered)",
        "msg msg-info",
    )
    state.push_sessions_update()
    return expanded, "ok"


async def _inject_attachment_content(session: _ChatSession, message: str) -> str:
    """Prepend extracted attachment content to *message* for this turn.

    Reads the turn's attached file paths from the most-recent user message's
    ``meta.files`` (set by api_chat from the composer), AWAITS each file's
    content extraction (started at upload), and prepends a labelled block so the
    model answers against the file. Files that yield no text (extraction failed /
    empty) are noted so the model doesn't silently pretend they had content.
    """
    # Find the last user message's attached files.
    files: list[str] = []
    for m in reversed(session.messages):
        if m.get("role") == "user":
            meta = m.get("meta") or {}
            raw = meta.get("files")
            if isinstance(raw, list):
                files = [str(p) for p in raw if isinstance(p, str) and p]
            break
    # Only attachments (uploads dir) get extracted+inlined here; @-mentioned
    # workspace files are left for the agent's own file tools to read on demand.
    import os as _os

    from personalclaw.config.loader import config_dir

    uploads = str((config_dir() / "uploads").resolve())
    attached = [p for p in files if _os.path.realpath(p).startswith(uploads)]
    if not attached:
        return message

    from personalclaw.dashboard.attachment_extract import display_name, get_extractor

    extractor = get_extractor()
    blocks: list[str] = []
    for p in attached:
        import mimetypes as _mt

        text = await extractor.get(p, _mt.guess_type(p)[0])
        name = display_name(p)
        if text:
            blocks.append(f"### Attached file: {name}\n\n{text}")
        else:
            blocks.append(f"### Attached file: {name}\n\n(No extractable text content.)")
    if not blocks:
        return message
    header = (
        "The user attached the following file(s). Their extracted content is "
        "included below — use it to answer.\n\n"
    )
    return f"{header}{chr(10).join(blocks)}\n\n---\n\n{message}"


def _inject_knowledge_content(state: "DashboardState", session: _ChatSession, message: str) -> str:
    """Prepend @-mentioned knowledge-item content to *message* for this turn.

    The composer's ``@`` menu can reference knowledge library items; their ids
    arrive on the most-recent user message's ``meta.knowledge``. Each item's
    stored content is redacted (credentials + exfiltration URLs) and prepended in
    a labelled block, so the model answers grounded in the referenced knowledge —
    mirroring :func:`_inject_attachment_content` for uploaded files.
    """
    ids: list[str] = []
    for m in reversed(session.messages):
        if m.get("role") == "user":
            meta = m.get("meta") or {}
            raw = meta.get("knowledge")
            if isinstance(raw, list):
                ids = [str(i) for i in raw if isinstance(i, str) and i]
            break
    if not ids:
        return message

    from personalclaw.security import redact_credentials, redact_exfiltration_urls

    store = state.knowledge_store
    blocks: list[str] = []
    for kid in ids:
        try:
            item = store.get_item(kid)
        except Exception:
            item = None
        if not item:
            continue
        title = str(item.get("title") or "Untitled")
        content = str(item.get("content") or "")
        content, _ = redact_credentials(content)
        content, _ = redact_exfiltration_urls(content)
        if content.strip():
            blocks.append(f"### Knowledge: {title}\n\n{content}")
        else:
            blocks.append(f"### Knowledge: {title}\n\n(No text content.)")
    if not blocks:
        return message
    header = (
        "The user referenced the following item(s) from their knowledge library. "
        "Their content is included below — use it to answer.\n\n"
    )
    return f"{header}{chr(10).join(blocks)}\n\n---\n\n{message}"


async def _run_chat(
    state: DashboardState,
    session: _ChatSession,
    message: str,
    *,
    _prompt_depth: int = 0,
    regenerate_hint: str = "",
) -> None:
    """Stream LLM response into *session*.  Survives browser disconnect."""
    # Reset the per-turn error flag; the except block sets it True on a crash.
    session._last_turn_errored = False

    def _agent_hook_ids() -> list[str]:
        """Resolve THIS session's agent's referenced lifecycle-trigger IDs
        (agent-scoped firing). Resolved per-fire so an in-session agent switch is
        honored. Returns [] (→ fire_for_ids fires nothing) on any resolution
        failure, so a broken lookup can never silently fall back to global firing."""
        try:
            _cfg = AppConfig.load()
            return list(resolve_agent_bindings(_cfg, session.agent or None).triggers or [])
        except Exception:
            logger.debug("trigger-id resolution failed for session %s", session.key, exc_info=True)
            return []

    async def _fire(
        event: str,
        context: str = "",
        tool_name: str = "",
        tool_input: dict | None = None,
        tool_response: dict | None = None,
    ) -> list[str]:
        """Fire script hooks. Returns stdout texts from exit-0 hooks (for context injection).

        Agent-scoped — only the hooks the session's agent references fire,
        via ``fire_for_ids``. There is no global firing path.
        """
        injected: list[str] = []
        if state._hook_store is None:
            if event == HOOK_EVENT_PRE_TOOL_USE:
                injected.append("BLOCKED:system:hook store not initialized")
                logger.error("Hook store not initialized for PRE_TOOL_USE - blocking tool")
            return injected
        try:
            results = await state._hook_store.fire_for_ids(
                event,
                _agent_hook_ids(),
                context,
                tool_name=tool_name,
                tool_input=tool_input,
                tool_response=tool_response,
            )
            for r in results:
                if r.exit_code == 0 and r.stdout:
                    injected.append(r.stdout)
                    logger.info("Hook %s stdout: %s", r.hook_name, r.stdout[:200])
                    state.broadcast_ws(
                        "activity_event",
                        {
                            "session": session.key,
                            "kind": "hook",
                            "text": f"Hook {r.hook_name}: injected {len(r.stdout)} chars",
                        },
                    )
                elif r.exit_code == 2:
                    injected.append(
                        f"BLOCKED:{r.hook_name}:{r.stderr[:200] if r.stderr else 'hook denied'}"
                    )
                    logger.warning(
                        "Hook %s blocked tool: %s",
                        r.hook_name,
                        r.stderr[:200] if r.stderr else "exit 2",
                    )
                    state.broadcast_ws(
                        "activity_event",
                        {
                            "session": session.key,
                            "kind": "hook",
                            "text": f"Hook {r.hook_name} BLOCKED: {r.stderr[:100] if r.stderr else 'denied'}",
                        },
                    )
                elif r.exit_code not in (0, 2) and r.stderr:
                    # Non-zero, non-block: show warning
                    logger.warning("Hook %s warning: %s", r.hook_name, r.stderr[:200])
        except Exception as exc:
            if event == HOOK_EVENT_PRE_TOOL_USE:
                logger.warning("Hook fire error during blocking event %s: %s", event, exc)
                raise
            logger.warning("Hook fire error: %s", exc)
        return injected

    session_key = _history_key_for(session.key)

    # Inherit channel link: if this dashboard session mirrors a channel thread,
    # copy the link so bidirectional sync works.
    if session_key.startswith("dashboard:"):
        _link = state.sessions.get_channel_link(session_key)
        if not (_link and _link[0]):
            _raw = session_key[len("dashboard:") :]
            _link = state.sessions.get_channel_link(_raw)
            if _link and _link[0] and _link[1]:
                state.sessions.set_channel_link(session_key, _link[0], _link[1])

    assistant_text = ""
    last_heartbeat = time.time()
    chunk_seq = 0
    in_tool_group = False
    _pending_tools: dict[str, str] = {}  # tool_call_id -> tool_name
    needs_session_reset = False
    saw_compaction = False
    # Reset the per-turn file-change accumulator here — all dispatch paths
    # (handler, orchestrator, queued re-dispatch) funnel through _run_chat.
    session._file_changes = []

    # ── Attachments: inject extracted file content into the prompt ──
    # Uploaded attachments begin content-extraction at upload time (knowledge
    # EXTRACTION graph only — text read / ASR / OCR / ffmpeg). Here we AWAIT any
    # pending extraction for THIS turn's attached files and prepend the text, so
    # "summarize this file" sees the content. The user's turn was already accepted;
    # blocking here blocks only the RESPONSE until extraction finishes (depth 0 only,
    # so re-entrant prompt-expansion / queue dispatch don't re-inject).
    if _prompt_depth == 0:
        try:
            message = await _inject_attachment_content(session, message)
        except Exception:
            logger.warning("attachment content injection failed", exc_info=True)
        try:
            message = _inject_knowledge_content(state, session, message)
        except Exception:
            logger.warning("knowledge content injection failed", exc_info=True)

    # ── Slash commands: detect early, before session acquisition ──
    first_word = message.split()[0] if message.strip() else ""
    is_slash = first_word in _SLASH_COMMANDS

    # Block dangerous/local-only commands before acquiring a session
    if first_word in _BLOCKED_SLASH_COMMANDS:
        session.append(
            "assistant",
            f"`{first_word}` is not available in the dashboard.",
            "msg msg-a",
        )
        state.push_sessions_update()
        return

    # ── /prompts: handle locally instead of forwarding to ACP agent ──
    if first_word == "/prompts":

        args = message.split(None, 2)  # /prompts [get] [name]
        sub = args[1] if len(args) > 1 else ""

        if sub == "get" and len(args) > 2:
            # /prompts get <name> — invoke the prompt in this chat
            name = args[2]
            expanded, status = _expand_prompt_mention(f"@{name}", state, session)
            if status == "ok":
                sel().log_tool_invocation(
                    session_key="",
                    agent=_agent_label(session),
                    source="dashboard",
                    tool_name="prompt_expansion",
                    tool_kind="prompt",
                    outcome="ok",
                    metadata={"mention": f"@{name}", "session": session.key, "via": "/prompts get"},
                )
                # Re-enter _run_chat with the expanded message (depth=1, no further expansion)
                await _run_chat(state, session, expanded, _prompt_depth=1)
            elif status == "blocked":
                sel().log_tool_invocation(
                    session_key="",
                    agent=_agent_label(session),
                    source="dashboard",
                    tool_name="prompt_expansion",
                    tool_kind="prompt",
                    outcome="blocked",
                    metadata={"mention": f"@{name}", "session": session.key, "via": "/prompts get"},
                )
                session.append(
                    "assistant", f"Prompt `{name}` blocked — sensitive path.", "msg msg-a"
                )
                state.push_sessions_update()
            elif status == "too_large":
                sel().log_tool_invocation(
                    session_key="",
                    agent=_agent_label(session),
                    source="dashboard",
                    tool_name="prompt_expansion",
                    tool_kind="prompt",
                    outcome="too_large",
                    metadata={"mention": f"@{name}", "session": session.key, "via": "/prompts get"},
                )
                session.append(
                    "assistant",
                    f"Prompt `{name}` exceeds size limit ({MAX_PROMPT_BYTES // 1000}KB).",
                    "msg msg-a",
                )
                state.push_sessions_update()
            else:
                sel().log_tool_invocation(
                    session_key="",
                    agent=_agent_label(session),
                    source="dashboard",
                    tool_name="prompt_expansion",
                    tool_kind="prompt",
                    outcome="not_found",
                    metadata={"mention": f"@{name}", "session": session.key, "via": "/prompts get"},
                )
                session.append("assistant", f"Prompt `{name}` not found.", "msg msg-a")
                state.push_sessions_update()
            return

        # /prompts or /prompts list — show available prompts
        try:
            prompts = _list_provider_prompts()
        except Exception:
            prompts = []
        if not prompts:
            session.append(
                "assistant",
                "No prompts found. Create prompts in `~/.personalclaw/prompts/`.",
                "msg msg-a",
            )
            state.push_sessions_update()
            return
        lines = ["**Available Prompts** — type `@name` to invoke\n"]
        for p in prompts:
            desc = f" — {p['description']}" if p["description"] else ""
            lines.append(f"- `@{p['fullName']}`{desc}")
        text = "\n".join(lines)
        text, _ = redact_credentials(text)
        text, _ = redact_exfiltration_urls(text)
        session.append("assistant", text, "msg msg-a")
        sel().log_tool_invocation(
            session_key="",
            agent=_agent_label(session),
            source="dashboard",
            tool_name="prompt_list",
            tool_kind="prompt",
            outcome="ok",
            metadata={"count": len(prompts), "session": session.key, "via": "/prompts"},
        )
        state.push_sessions_update()
        return

    _acquired = False
    _mirror_stream_ts: str = ""
    _mirror_chan: str | None = ""
    _mirror_active_task = ""
    _mirror_active_task_title = ""
    _mirror_thread: str | None = ""
    _mirror_task_counter = 0
    try:
        # Resolve agent bindings early so we pass the correct ACP agent
        # name (e.g. "personalclaw") instead of the PersonalClaw session name
        # (e.g. "default") which has no matching ~/.personalclaw/agents/ config.
        provider_agent: str | None = None
        memory_store: str | None = None
        agent_system_prompt: str = ""
        provider_kind: str = ""
        acp_mode: str = ""
        agent_approval_mode: str = ""
        global_approval_mode: str = ""
        try:
            cfg = AppConfig.load()
            global_approval_mode = cfg.agent.approval_mode or ""
            bindings = resolve_agent_bindings(cfg, session.agent or None)
            provider_agent = bindings.provider_agent
            acp_mode = getattr(bindings, "acp_mode", "") or ""
            memory_store = bindings.memory_store_name
            agent_system_prompt = bindings.system_prompt
            # The bound agent's EXPLICIT persistent approval grant (the "Always allow for
            # this agent" the card's scope picker writes → AgentProfile.approval_mode).
            # Consumed below to seed a NEW session's trust — the single seam that makes the
            # grant auto-approve in chat. Only an explicit per-agent value counts: a "" that
            # would inherit the schema-default global "auto" must NOT silently auto-approve
            # every chat (that would make the Normal permission mode meaningless).
            agent_approval_mode = getattr(bindings, "approval_mode", "") or ""
            # The runtime kind resolved from the agent's actual PROFILE. Thread it
            # to the factory so routing honors the user's selection — we pass
            # provider_agent (the ACP-internal name) as ``agent`` below, which the
            # bridge cannot map back to the profile to re-derive the kind.
            provider_kind = getattr(bindings, "provider", "") or ""
        except Exception:
            logger.warning("Failed to resolve agent bindings in _run_chat", exc_info=True)

        # Task-mode framing — a LAYER on the resolved system prompt, threaded as
        # system_prompt_suffix (NOT folded into the override): for the default
        # agent bindings.system_prompt is empty, and folding the framing into it
        # made build_message treat the 4-line posture block as the ENTIRE system
        # prompt — silently dropping identity/{{bot_name}}, widget instructions,
        # output format, and safety rules on every default-agent chat.
        _tm_framing = task_mode_framing(session)

        # Ephemeral discovered-ACP-agent override (picked live in the chat picker,
        # NOT a saved definition). When the session carries one it WINS over the
        # named-definition resolution above: bind the chosen runtime + modeId
        # directly. reasoning_effort is already a session field (forwarded below).
        _acp_provider = getattr(session, "acp_provider", "") or ""
        if _acp_provider:
            provider_kind = _acp_provider
            provider_agent = getattr(session, "acp_provider_agent", "") or ""

        # Per-session ACP permission-mode override (e.g. an unattended goal loop
        # worker sets bypassPermissions so an ACP agent freely executes file
        # writes instead of avoiding them in the default "prompts for writes"
        # mode). The host approval gate + SEL audit still govern via auto-approve.
        # The _plan rung below still wins when active (it's behavioral, not
        # auto-approve).
        _sess_acp_mode = getattr(session, "acp_mode", "") or ""
        if _sess_acp_mode:
            acp_mode = _sess_acp_mode

        # Plan task-mode → forward acp_mode=plan so an ACP backend that supports it
        # (claude) plans NATIVELY (cleaner output). Permission AUTHORITY stays
        # with the host gate for every rung — we never forward an auto-approve
        # mode (acceptEdits/dontAsk/bypassPermissions); claude always escalates
        # via session/request_permission and the host trust ladder decides. Plan
        # is the sole forwarded mode (it's behavioral, not an approval bypass —
        # the adapter denies execution in plan, it does not auto-allow). Runtimes
        # without a plan mode (the default dialect) ignore it; the host task-mode
        # gate suppresses execution universally regardless.
        if getattr(session, "_task_mode", "agent") == "plan":
            acp_mode = "plan"

        state.broadcast_ws(
            "activity_event", {"session": session.key, "kind": "status", "text": "Creating session…"}
        )
        session.model = _normalize_model(session.model or "") or ""
        client, is_new, resumed = await state.sessions.get_or_create(
            session_key, agent=provider_agent or session.agent or None, model=session.model or None,
            cwd=session.workspace_dir or None,
            reasoning_effort_override=session.reasoning_effort or None,
            provider_kind=provider_kind or None,
            acp_mode=acp_mode or None,
            # Brownfield Code/Goal-Loop workers: let the native file tools also reach
            # the project files dir (engine files live outside the workspace cwd).
            extra_tool_roots=list(getattr(session, "_extra_tool_roots", []) or []) or None,
            # Unattended worker/scheduled turn: strip interactive tools + fail the
            # approval gate fast so a background run can't wedge waiting for a human
            # (T5). Native-runtime-only; the bridge pops it for other runtimes.
            unattended=bool(getattr(session, "_unattended", False)),
            # The Project this session scopes under — the native runtime binds it per
            # turn so artifact_save stamps the artifact's project_id, tying artifacts
            # created here back to the Project (S5). "" for an unscoped session.
            project_id=getattr(session, "project_id", "") or "",
        )
        _acquired = True
        # Display-only: when the user left the model on "auto", show the model the
        # provider actually resolved (AcpAgentProvider stores it on client._model)
        # in the status line — but do NOT write it back onto session.model. That
        # field is the USER'S selection; persisting an ACP CLI's internal default
        # (e.g. claude-code's bundle DEFAULT_MODEL "claude-opus-4-8") would clobber
        # the user's "auto"/chosen model with a model no model-provider offers,
        # which surfaces as the dropdown silently switching mid-session.
        agent_label = provider_agent or session.agent or "default"
        if session.model:
            model_label = session.model
        else:
            _prov_model = getattr(getattr(client, "client", None), "_model", "") or ""
            model_label = _prov_model if (isinstance(_prov_model, str) and _prov_model and _prov_model != "auto") else "auto"
        # Surface WHICH backend is actually serving the turn (transparency): the
        # in-process native loop vs an external ACP CLI (claude-code /
        # codex). The user otherwise had no way to know an external backend was
        # running its own tools. Derive from the live provider: NativeAgentRuntime
        # reports provider_id "native"; ACP reports "acp:<cli>".
        _runtime_label = getattr(client, "provider_id", "") or provider_kind or "native"
        if resumed:
            state.broadcast_ws(
                "activity_event",
                {
                    "session": session.key,
                    "kind": "session",
                    "text": f"Session resumed · {agent_label} · {model_label} · via {_runtime_label}",
                },
            )
        else:
            state.broadcast_ws(
                "activity_event",
                {
                    "session": session.key,
                    "kind": "session",
                    "text": f"Session created · {agent_label} · {model_label} · via {_runtime_label}",
                },
            )

        # Seed this session's trust from the bound agent's persistent approval floor
        # ("Always allow for this agent" = AgentProfile.approval_mode "auto"). This is
        # the per-agent grant made real: the gate reads session._trust, so without this
        # the grant never took effect in chat. Gated on a per-session ONE-SHOT latch —
        # NOT `is_new`, which tracks the runtime client (recreated between turns / on
        # idle eviction) and would re-fire every turn, clobbering an explicit "Normal"
        # the user set mid-session. Seeding once lets session scope OVERRIDE the floor
        # (most-permissive on entry, but the user's later downgrade sticks). Audited so
        # the floor's activation is traceable, not silent.
        if not session._agent_floor_seeded:
            session._agent_floor_seeded = True
            if agent_approval_mode == "auto" and not session._trust:
                session._trust = True
                try:
                    sel().log_api_access(
                        caller="dashboard:approval",
                        operation="mode_change:agent_floor_auto",
                        outcome="enabled",
                        resources=f"{session_key} agent={session.agent or 'default'}",
                    )
                except Exception:
                    logger.warning("SEL audit failed for agent approval-floor seeding", exc_info=True)
            # trust_reads floor: per-agent explicit value wins, else the global
            # agent.approval_mode default. Seeds only the READ-ONLY auto-approve
            # latch — unlike the full-trust "auto" floor above, the global value
            # participates here because trust_reads is a strictly weaker grant
            # (safe-risk tools only; everything else still asks).
            elif (
                (agent_approval_mode or global_approval_mode) == "trust_reads"
                and not session._trust
                and not session._trust_reads
            ):
                session._trust_reads = True
                try:
                    sel().log_api_access(
                        caller="dashboard:approval",
                        operation="mode_change:approval_floor_trust_reads",
                        outcome="enabled",
                        resources=f"{session_key} agent={session.agent or 'default'}",
                    )
                except Exception:
                    logger.warning("SEL audit failed for trust_reads floor seeding", exc_info=True)

        # Propagate trust/YOLO to session so subagents inherit auto-approve.
        if session._trust or state.is_yolo_active():
            state.sessions.set_approval_policy(session_key, "auto")
        else:
            state.sessions.set_approval_policy(session_key, "")

        # Propagate the task mode to the runtime so its tool gate (ask/plan/build)
        # holds regardless of approval — the runtime enforces it before approval,
        # so a Trust/YOLO auto-approve can't bypass a read-only posture.
        state.sessions.set_task_mode(session_key, getattr(session, "_task_mode", "agent"))

        # Write current session key so MCP tools can pass it to spawn API.
        # Keyed by ACP agent PID to avoid races between concurrent sessions.
        try:
            pid = state.sessions.get_pid(session_key)
            if isinstance(pid, int):
                (config_dir() / f"session_pid_{pid}.txt").write_text(session_key, encoding="utf-8")
        except Exception:
            pass

        # ── @prompt expansion: resolve @name to SOP/prompt content ──
        if message.startswith("@") and not is_slash and _prompt_depth < 1:
            original = message
            message, _status = _expand_prompt_mention(message, state, session)
            if _status == "ok":
                sel().log_tool_invocation(
                    session_key=session_key,
                    agent=_agent_label(session),
                    source="dashboard",
                    tool_name="prompt_expansion",
                    tool_kind="prompt",
                    outcome="ok",
                    metadata={"mention": original.split()[0], "session": session.key},
                )
            elif _status in ("blocked", "too_large"):
                sel().log_tool_invocation(
                    session_key=session_key,
                    agent=_agent_label(session),
                    source="dashboard",
                    tool_name="prompt_expansion",
                    tool_kind="prompt",
                    outcome=_status,
                    metadata={"mention": original.split()[0], "session": session.key},
                )
                label = (
                    "sensitive path"
                    if _status == "blocked"
                    else f"size limit ({MAX_PROMPT_BYTES // 1000}KB)"
                )
                session.append("system", f"Prompt blocked — {label}.", "msg msg-info")
                state.push_sessions_update()
                return
            elif _status == "not_found":
                sel().log_tool_invocation(
                    session_key=session_key,
                    agent=_agent_label(session),
                    source="dashboard",
                    tool_name="prompt_expansion",
                    tool_kind="prompt",
                    outcome="not_found",
                    metadata={"mention": original.split()[0], "session": session.key},
                )

        if is_slash:
            full_message = message
            sel().log_tool_invocation(
                session_key=session_key,
                agent=_agent_label(session),
                source="dashboard",
                tool_name="slash_command",
                tool_kind="slash",
                outcome="bypass",
                metadata={"command": first_word, "session": session.key},
            )
        elif state.context_builder:

            compressed: str | None = None
            # is_new = new ACP agent/dashboard process, NOT new conversation.
            # The channel thread persists across processes, so we compress its
            # history to bootstrap the fresh session's context window.
            if is_new and not resumed and state.context_builder.conversation_log:
                from personalclaw.context import compress_thread_history  # circular: context -> chat

                compressed = await compress_thread_history(
                    state.context_builder.conversation_log,
                    session_key,
                    message,
                    state.sessions,
                )
            # After a soft-cancel, ACP agent drops the cancelled turn from its
            # conversation log — but everything BEFORE the cancel is preserved.
            # Re-inject just the cancelled turn (user prompt + partial assistant)
            # as a preamble so the LLM remembers what was interrupted, without
            # duplicating older history. Flag lives on the session (set by
            # SessionManager.stop_turn), consumed one-shot here. Use getattr
            # for prev_turn_cancelled so test doubles don't raise on access.
            _session = getattr(state.sessions, "_sessions", {}).get(session_key)
            if _session is not None and getattr(
                _session, "prev_turn_cancelled", False
            ):
                _session.prev_turn_cancelled = False
                if state.context_builder and state.context_builder.conversation_log:
                    from personalclaw.context import (
                        build_cancelled_turn_preamble,  # circular: context -> dashboard.chat -> chat_runner (can't top-level: context imports chat at module load); circular: context -> chat -> chat_runner; circular: context -> chat
                    )

                    preamble = build_cancelled_turn_preamble(
                        state.context_builder.conversation_log, session_key
                    )
                    if preamble:
                        message = preamble + "\n\n" + message
            logger.info("Chat session=%s is_new=%s mode=%r", session.key, is_new, session.mode)
            # Drain any pending subagent delivery failures so the LLM knows
            # about timed-out results and can read them from disk.
            if session._pending_subagent_failures:
                failures = session._pending_subagent_failures[:]
                session._pending_subagent_failures.clear()
                message = "\n\n".join(failures) + "\n\n" + message
            # Drain pending context injections (silent background context
            # from apps/subagents).  Expired entries are discarded.
            if session._pending_context:
                now = time.time()
                ctx_parts: list[str] = []
                for entry in session._pending_context:
                    max_age = entry.get("maxAge")
                    if max_age is not None:
                        injected_at = entry.get("injectedAt", 0)
                        if injected_at + max_age < now:
                            continue  # expired — silently discard
                    source = entry.get("source", "app")
                    ctx_parts.append(
                        f'[Background context from "{source}"]\n'
                        f'{entry["content"]}\n'
                        f"[End of background context]\n"
                    )
                session._pending_context.clear()
                if ctx_parts:
                    message = "\n".join(ctx_parts) + "\n" + message
            # Use resolved provider agent name (e.g. "personalclaw"), not the session
            # name (e.g. "default"), so build_message's is_custom check
            # correctly identifies personalclaw sessions and enables skills.
            # Lumon persona injection — prepend to message so build_message
            # accounts for it in context budget calculations.
            message = _maybe_inject_persona(message, getattr(session, "color_theme", ""), is_new)
            # Project-bound chat (Slice 6 D2): on the first turn, prepend the project's
            # context — workspace, loop history, context-dir — so the session operates
            # with the project's cohesive shared context. First turn only (is_new); the
            # workspace is already the session cwd (bound at create).
            if is_new and getattr(session, "project_id", ""):
                _proj_pre = _project_context_preamble(session.project_id)
                if _proj_pre:
                    message = f"{_proj_pre}\n\n{message}"
            # Goal-loop capabilities (planner/quorum IT-5): a loop's confirmed
            # skill_ids/workflow_ids load ACTIVELY into every cycle's turn, on top
            # of passive surfacing. Looked up from the GoalLoop row keyed off the
            # ``loop-<id>`` session. Best-effort: any failure leaves them empty.
            _force_skill_ids: list[str] = []
            _force_workflow_ids: list[str] = []
            if getattr(session, "_app", "") == "loop":
                # The unified Loop engine: ALL kinds are app="loop", keyed loop-<id>
                # (or loop-<id>-<taskid> for a parallel code task-worker). The active
                # phase/stage's per-cycle capabilities (∪ the always-on baseline) +
                # directive come from the kind strategy — no per-engine branch.
                try:
                    from personalclaw.loop import kinds as _kinds
                    from personalclaw.loop import store as _loop_store

                    _lid = session.key.split("loop-", 1)[-1]
                    _loop = _loop_store.get(_lid)
                    # A parallel task-worker (loop-<id>-<taskid>) resolves its parent
                    # loop — its caps = the active stage's, same as the main worker.
                    if _loop is None and "-" in _lid:
                        _loop = _loop_store.get(_lid.rsplit("-", 1)[0])
                    if _loop is not None:
                        _kinds.ensure_loaded()
                        _strat = _kinds.get_or_none(_loop.kind)
                        _caps = getattr(_strat, "turn_capabilities", None) if _strat else None
                        if _caps is not None:
                            _force_skill_ids, _force_workflow_ids = _caps(_loop)
                        _dir = getattr(_strat, "turn_directive", None) if _strat else None
                        _pd = _dir(_loop) if _dir else ""
                        if _pd:
                            message = f"{_pd}\n\n{message}"
                except Exception:
                    logger.debug("loop capability lookup skipped", exc_info=True)
            # Assemble via the pluggable context engine (default = the monolithic
            # build_message; a custom engine that raises is quarantined to default
            # so the turn still gets context). Active-recall + structured-
            # compaction land as engine hooks on this seam.
            _assembled = assemble_context(
                state.context_builder,
                message,
                is_new_session=is_new,
                session_key=session_key,
                agent=provider_agent or session.agent or None,
                resumed=resumed,
                cwd=session.workspace_dir or None,
                memory_store=memory_store,
                compressed_history=compressed,
                mode=session.mode,
                blocks_reads=session.blocks_reads,
                # Active recall is an interactive-chat affordance — headless
                # worker apps (goal loops, etc.) opt out so they don't pay the
                # recall budget on every autonomous cycle.
                active_recall=getattr(session, "_app", "") not in ("loop", "code"),
                system_prompt_override=agent_system_prompt,
                system_prompt_suffix=_tm_framing,
                # Resolve the turn's agent to the binding-id form workflow
                # scope_ref uses (native profile name | acp:<cli>/<modeId>), so
                # agent-scoped SOPs surface only on that agent's turns.
                resolved_agent_id=_resolve_agent_id(
                    session.agent or None, provider_kind, provider_agent
                ),
                force_skill_ids=_force_skill_ids,
                force_workflow_ids=_force_workflow_ids,
            )
            full_message = _apply_incognito_prefix(session, _assembled.message)
            if is_new:
                ctx_len = _assembled.injected_chars
                state.broadcast_ws(
                    "activity_event",
                    {
                        "session": session.key,
                        "kind": "context",
                        "text": f"Injected {ctx_len:,} chars of context (memory, lessons, history, episodic)",
                    },
                )
        else:
            full_message = message

        # Re-inject history if session was reset but messages haven't been
        # saved to JSONL yet (e.g. stop button killed the process mid-chat).
        # build_session_context already injects recent() from JSONL, so this
        # only adds value when in-memory messages are newer than disk.
        # Skip for soft stops — session is preserved, no re-injection needed.
        if is_new and session.messages:
            # Check if last stop was soft (session preserved, no re-injection).
            # cls is a JSON-encoded dict (see api_chat_session_stop); parse it.
            _last_stop_soft = False
            for m in reversed(session.messages):
                cls_val = m.get("cls", "")
                if not isinstance(cls_val, str) or not cls_val.startswith("{"):
                    continue
                try:
                    _cls = json.loads(cls_val)
                except (json.JSONDecodeError, TypeError):
                    continue
                if not isinstance(_cls, dict) or _cls.get("kind") != "stop_event":
                    continue
                if _cls.get("outcome") == "soft":
                    _last_stop_soft = True
                break
            if not _last_stop_soft:
                history_key = _history_key_for(session.key)
                disk_count = 0
                if state.conversation_log:
                    disk_count = len(
                        state.conversation_log.read_messages(history_key)
                    )
                mem_count = sum(
                    1 for m in session.messages
                    if m.get("role") in ("user", "assistant")
                )
                if mem_count > disk_count:
                    history = _build_history_prefix(session)
                    if history:
                        full_message = history + full_message

        if is_new:
            await _fire(HOOK_EVENT_SESSION_START, session_key)
            spawn_injected = await _fire(HOOK_EVENT_AGENT_SPAWN, session_key)
        else:
            spawn_injected = []

        injected = await _fire(HOOK_EVENT_USER_PROMPT_SUBMIT, message)
        all_injected = spawn_injected + injected
        if all_injected:
            hook_ctx = "\n\n".join(all_injected)
            full_message = f"[Hook context]\n{hook_ctx}\n[End hook context]\n\n{full_message}"

        if regenerate_hint:
            full_message = f"[System: {regenerate_hint}]\n\n{full_message}"

        # Queue-steering (#37): wire the native loop's steer source so mid-turn
        # messages buffered on the session (steer mode) drain at the next model
        # boundary. Native runtime only (the ACP CLIs don't expose the seam).
        if hasattr(client, "set_steer_source"):
            try:
                client.set_steer_source(lambda: state.sessions.drain_steers(session.key))
            except Exception:
                logger.debug("steer source wiring skipped", exc_info=True)

        # Slash commands use _vendor.dev/commands/execute for full native output;
        # regular messages use session/prompt.
        event_stream = client.stream_command(message) if is_slash else client.stream(full_message)
        state.broadcast_ws("chat_status", {"session": session.key, "status": "Thinking…"})
        state.broadcast_ws(
            "activity_event", {"session": session.key, "kind": "status", "text": "Thinking…"}
        )

        # ── Bidirectional sync: mirror user message to the linked channel thread ──
        if state.channel_delivery and not is_slash:
            _mirror_thread, _mirror_chan = state.sessions.get_channel_link(session_key)
            if _mirror_thread and _mirror_chan:
                try:
                    _mirror_msg = message[:500]
                    _mirror_msg, _ = redact_exfiltration_urls(_mirror_msg)
                    _mirror_msg, _ = redact_credentials(_mirror_msg)
                    await state.channel_delivery.deliver_text(
                        _mirror_chan, f"💬 _{_mirror_msg}_", _mirror_thread
                    )
                    # Start a stream for real-time tool animations
                    _mirror_stream_ts = await state.channel_delivery.start_stream(
                        _mirror_chan, _mirror_thread, initial_text="Thinking…"
                    ) or ""
                except Exception:
                    logger.debug("Failed to mirror user message to the channel", exc_info=True)

        _stop_reason = ""
        # Turn telemetry from the terminal complete event (provider-neutral —
        # both native and ACP populate event_count/tool_call_count). Rendered as
        # the live-only "Turn complete" stats line after the loop.
        _turn_event_count = 0
        _turn_tool_call_count = 0
        async for event in event_stream:
            # Heartbeat every 5s during long operations
            if time.time() - last_heartbeat > 5:
                state.broadcast_ws("heartbeat", {"session": session.key, "ts": time.time()})
                last_heartbeat = time.time()

            # Security: tool_call_id originates from LLM — redact before any use
            if hasattr(event, 'tool_call_id') and event.tool_call_id:
                _tcid, _ = redact_exfiltration_urls(event.tool_call_id)
                _tcid, _ = redact_credentials(_tcid)
                event.tool_call_id = _tcid

            if event.kind == EVENT_TEXT_CHUNK:
                # If we just exited a tool group, finalize the streaming
                # message so post-tool text starts a fresh message.
                if in_tool_group:
                    if assistant_text:
                        _flush_segment(state, session, assistant_text)
                        assistant_text = ""
                    else:
                        # No accumulated text, but still tell frontend to
                        # finalize any streaming message before tools.
                        state.broadcast_ws("chat_segment", {"session": session.key})
                    # Fallback: text after tools means all preceding tools
                    # are complete — mark any that weren't already marked
                    # (e.g. tools with no output).
                    for m in reversed(session.messages):
                        if m.get("role") == "tool" and not m.get("meta", {}).get("done"):
                            m.setdefault("meta", {})["done"] = True
                            tcid = m.get("meta", {}).get("tool_call_id", "")
                            if tcid:
                                state.broadcast_ws("tool_result", {"session": session.key, "tool_call_id": tcid, "output": ""})
                        elif m.get("role") not in ("tool", "permission", "chunk"):
                            break
                in_tool_group = False
                chunk_seq += 1
                safe_chunk, _ = redact_exfiltration_urls(event.text)
                safe_chunk, _ = redact_credentials(safe_chunk)
                assistant_text += safe_chunk
                session.append("chunk", safe_chunk, "chunk")
                # Push chunk to WS clients (HTTP SSE reader drains from session._pending)
                state.broadcast_ws(
                    "chat_chunk",
                    {"session": session.key, "content": safe_chunk, "seq": chunk_seq},
                )
            elif event.kind == EVENT_THINKING_CHUNK:
                # Thinking content is not included in the main response text.
                # Broadcast as a separate WS event for frontend rendering.
                # Per-chunk redaction is best-effort (patterns spanning chunks
                # could be missed); the channel handler applies full-text
                # redaction on the accumulated result before posting.
                # This matches chat_chunk which also broadcasts raw text.
                safe_text, exfil_warnings = redact_exfiltration_urls(event.text)
                for w in exfil_warnings:
                    logger.warning("Exfiltration URL redacted in thinking: %s", w)
                safe_text, cred_warnings = redact_credentials(safe_text)
                for w in cred_warnings:
                    logger.warning("Credential redacted in thinking: %s", w)
                state.broadcast_ws(
                    "chat_thinking",
                    {"session": session.key, "content": safe_text},
                )
            elif event.kind == EVENT_TOOL_CALL:
                # Flush pre-tool text silently (no broadcast) so it persists,
                # but keep the streaming message in place for correct tool ordering.
                if not in_tool_group and assistant_text:
                    _flush_segment(state, session, assistant_text, broadcast=False)
                    assistant_text = ""
                in_tool_group = True
                # Broadcast for real-time visibility and persist
                _title, _ = redact_exfiltration_urls(event.title)
                _title, _ = redact_credentials(_title)
                _kind, _ = redact_exfiltration_urls(event.tool_kind)
                _kind, _ = redact_credentials(_kind)
                # Compute the redacted+capped purpose and input ONCE, reuse for both
                # the live broadcast and the persisted meta — inline tool details
                # must survive reload, so input lands on the tool message's meta, not
                # just the volatile toolLog. tool_input is Any (dict for native, str
                # for ACP) → tool_input_to_str coerces before slicing.
                _purpose = redact_credentials(
                    redact_exfiltration_urls((event.tool_purpose or "")[:200])[0]
                )[0]
                _input_preview = redact_credentials(
                    redact_exfiltration_urls(tool_input_to_str(event.tool_input)[:4000])[0]
                )[0]
                # Structured input object (native passes a dict) for schema-driven
                # field rendering (tool-io-rendering). Redacted per-value, bounded.
                # Falls back to None for non-dict (ACP str) input → UI uses the
                # string preview, exactly as before.
                _input_obj = _redact_tool_input_obj(event.tool_input)
                state.broadcast_ws(
                    "tool_call",
                    {
                        "session": session.key,
                        "tool": _title,
                        "kind": _kind,
                        "tool_call_id": event.tool_call_id,
                        "purpose": _purpose,
                        "input_preview": _input_preview,
                        "input": _input_obj,
                    },
                )
                session.append("tool", _title, "msg msg-tool", meta={"tool_call_id": event.tool_call_id, "purpose": _purpose, "input": _input_preview} if event.tool_call_id else None)
                # Snapshot before/after for a write tool so file-change chips
                # can render below the assistant message at turn end.
                _capture_file_change(session, event.title, event.tool_input)
                # AskUserQuestion → render an interactive question card alongside
                # the pill. The card lets the user answer inline; the agent is
                # already paused on the tool call awaiting the reply.
                if event.title == "AskUserQuestion":
                    _emit_question_card(state, session.key, event.tool_input, event.tool_call_id)
                sel().log_tool_invocation(
                    session_key=session_key,
                    agent=_agent_label(session),
                    source="dashboard",
                    tool_name=event.title,
                    tool_kind=event.tool_kind,
                    outcome="invoked",
                    # Effective risk on EVERY executed tool — this TOOL_CALL event
                    # fires for all tools that actually run, including ones the native
                    # runtime auto-approved under YOLO/policy=auto (which never reach
                    # the chat_runner approval gate). The one place risk is guaranteed
                    # logged for a forensic "what destructive tool ran" query.
                    metadata={"risk": resolve_effective_risk(
                        getattr(event, "risk_level", "") or "", event.title, event.tool_kind, event.tool_input
                    )},
                )
                # Fire PreToolUse hooks for auto-approved tools.
                # NOTE: For EVENT_TOOL_CALL, hooks are informational only - the tool
                # is already running (auto-approved by ACP agent). Hook results cannot
                # block execution. Hook scripts can log, audit, or trigger side effects.
                _raw = event.title or ""
                if _raw.startswith("Running: "):
                    _raw = _raw[9:]
                if event.tool_call_id:
                    _pending_tools[event.tool_call_id] = _raw
                await fire_tool_hooks(state._hook_store, event.title, tool_input_to_str(event.tool_input))
                # Mirror tool call to linked channel stream
                if _mirror_stream_ts and state.channel_delivery:
                    try:
                        if _mirror_active_task:
                            await state.channel_delivery.append_stream_task(
                                _mirror_chan, _mirror_stream_ts,
                                _mirror_active_task, _mirror_active_task_title, "complete",
                            )
                        _mirror_task_counter += 1
                        _mirror_active_task = f"tool_{_mirror_task_counter}"
                        _task_title = event.tool_purpose or _title
                        _task_title, _ = redact_exfiltration_urls(_task_title)
                        _task_title, _ = redact_credentials(_task_title)
                        _task_title = _task_title[:75]
                        _mirror_active_task_title = _task_title
                        await state.channel_delivery.append_stream_task(
                            _mirror_chan, _mirror_stream_ts,
                            _mirror_active_task, _task_title, "in_progress",
                        )
                    except Exception:
                        logger.debug("Mirror tool task failed", exc_info=True)
            elif event.kind == EVENT_TOOL_CALL_UPDATE:
                # Resolved input / refined summary for a tool whose initial
                # tool_call frame was empty (agents stream args in a later
                # frame). Refine the EXISTING card in place — no new message,
                # no re-fire of hooks/SEL/mirror — and re-broadcast so live and
                # reloaded clients both show the args. tool_input is already a
                # str on the ACP path; coerce defensively.
                #
                # The update's title is a refined SUMMARY (the command, the file
                # +range, …), NOT the tool name — so it lands on a separate
                # `detail` field and the stable tool NAME in `content` is kept,
                # so cards stay scannable when many tools are in play.
                if event.tool_call_id:
                    _u_input = redact_credentials(
                        redact_exfiltration_urls(tool_input_to_str(event.tool_input)[:4000])[0]
                    )[0]
                    _u_detail = ""
                    if event.title:
                        _u_detail, _ = redact_exfiltration_urls(event.title)
                        _u_detail, _ = redact_credentials(_u_detail)
                    for m in reversed(session.messages):
                        if m.get("role") == "tool" and m.get("meta", {}).get("tool_call_id") == event.tool_call_id:
                            _meta = m.setdefault("meta", {})
                            if _u_input:
                                _meta["input"] = _u_input
                            # only treat the update title as a detail when it
                            # actually differs from the tool name (some agents
                            # echo the name back), so we don't render "Terminal · Terminal".
                            _name = strip_status_sentinel(m.get("content", ""))
                            if _u_detail and _u_detail != _name:
                                _meta["detail"] = _u_detail
                            state.broadcast_ws(
                                "tool_call",
                                {
                                    "session": session.key,
                                    "tool": _name,
                                    "tool_call_id": event.tool_call_id,
                                    "input_preview": _meta.get("input", ""),
                                    "detail": _meta.get("detail", ""),
                                    "update": True,
                                },
                            )
                            break
            elif event.kind == EVENT_TOOL_RESULT:
                _out = (event.tool_output or "")[:8000]
                _out, _ = redact_exfiltration_urls(_out)
                _out, _ = redact_credentials(_out)
                # Typed tool-result metadata (tool-io-rendering + projection):
                # content_type drives the rich output renderer; raw_ref/truncated/
                # original_length drive the "show full result" affordance. Empty
                # for backends (ACP) that don't supply it → UI renders as before.
                _tmeta = event.tool_meta or {}
                _content_type = str(_tmeta.get("content_type", "") or "")
                _raw_ref = str(_tmeta.get("raw_ref", "") or "")
                _truncated = bool(_tmeta.get("truncated", False))
                _orig_len = _tmeta.get("original_length")
                # TC5: concrete next-steps on a failed tool — surfaced as a card note.
                _recovery = [str(h) for h in (_tmeta.get("recovery_hints") or [])][:6]
                # Tool-call outcome: only present (and False) when the tool FAILED, so
                # the card can color-code it; absent → success (renders as before).
                _tool_ok = _tmeta.get("ok")
                state.broadcast_ws(
                    "tool_result",
                    {
                        "session": session.key,
                        "tool_call_id": event.tool_call_id,
                        "output": _out,
                        "content_type": _content_type,
                        "raw_ref": _raw_ref,
                        "truncated": _truncated,
                        "original_length": _orig_len,
                        "recovery_hints": _recovery,
                        **({"ok": bool(_tool_ok)} if _tool_ok is not None else {}),
                    },
                )
                # Mark the matching tool message as done AND persist the output so
                # both completion state and the inline tool-detail output
                # survive page reload (persisted in message meta, replayed via SSE).
                if event.tool_call_id:
                    for m in reversed(session.messages):
                        if m.get("role") == "tool" and m.get("meta", {}).get("tool_call_id") == event.tool_call_id:
                            _meta = m.setdefault("meta", {})
                            _meta["done"] = True
                            _meta["output"] = _out
                            if _content_type:
                                _meta["content_type"] = _content_type
                            if _raw_ref:
                                _meta["raw_ref"] = _raw_ref
                            if _truncated:
                                _meta["truncated"] = True
                                if _orig_len is not None:
                                    _meta["original_length"] = _orig_len
                            if _recovery:
                                _meta["recovery_hints"] = _recovery
                            if _tool_ok is not None and not _tool_ok:
                                _meta["ok"] = False
                            break
                # Fire PostToolUse hooks
                _tool_name = _pending_tools.pop(event.tool_call_id, "")
                try:
                    _redacted_out, _ = redact_credentials(_out[:2000])
                    _redacted_out, _ = redact_exfiltration_urls(_redacted_out)
                    await _fire(
                        HOOK_EVENT_POST_TOOL_USE,
                        tool_name=_tool_name,
                        tool_response={"output": _redacted_out},
                    )
                except Exception:
                    logger.debug("PostToolUse hook error", exc_info=True)
            elif event.kind == EVENT_PERMISSION_REQUEST:
                # Permission breaks tool grouping
                in_tool_group = False
                # Flush accumulated text as a finalized segment before the
                # permission flow so the frontend renders them in order.
                if assistant_text:
                    _flush_segment(state, session, assistant_text)
                    assistant_text = ""
                # Task-mode gate (orthogonal to the approval gate below). Runs FIRST:
                # task mode decides WHICH tools may run; approval decides whether they
                # auto-approve. The native runtime enforces the SAME gate before
                # approval (so Trust/YOLO can't bypass it); this path is the universal
                # enforcement for ACP runtimes, which gate via their own protocol +
                # only reach here when they request approval. plan/ask/build all flow
                # through the shared gate (plan now allows read-only inspection).
                _task_mode = getattr(session, "_task_mode", "agent")
                _tm_deny = task_mode_denies(session, event.title, event.tool_kind, event.tool_input)
                if _tm_deny:
                    await client.reject_tool(event.request_id)
                    _title, _ = redact_exfiltration_urls(event.title)
                    _title, _ = redact_credentials(_title)
                    session.append("tool", f"{_title} ({_tm_deny})", "msg msg-tool")
                    sel().log_tool_invocation(
                        session_key=session_key,
                        agent=_agent_label(session),
                        source="dashboard",
                        tool_name=event.title,
                        tool_kind=event.tool_kind,
                        outcome="denied",
                        request_id=event.request_id,
                        metadata={"reason": f"task_mode:{_task_mode}"},
                    )
                    continue
                _pre_tool_hooks_fired = False
                if state.context_builder:
                    tool_result = state.context_builder.hooks.on_tool_call(event.title)
                    if tool_result.action == TOOL_DENY:
                        await client.reject_tool(event.request_id)
                        # Carry the deny reason into the transcript so it's visible
                        # why the call was blocked (recoverable hook policy). The
                        # backend's own loop feeds the model its tool_result; this
                        # is the user-facing record.
                        _deny_reason = getattr(tool_result, "reason", "") or "policy hook"
                        session.append(
                            "tool", f"{event.title} (blocked: {_deny_reason})", "msg msg-tool"
                        )
                        sel().log_tool_invocation(
                            session_key=session_key,
                            agent=_agent_label(session),
                            source="dashboard",
                            tool_name=event.title,
                            tool_kind=event.tool_kind,
                            outcome="denied",
                            request_id=event.request_id,
                            error="hook_deny",
                        )
                        continue
                    if tool_result.action == TOOL_AUTO_APPROVE:
                        try:
                            validated_tool = _validate_tool_name(event.title, event.tool_kind)
                        except ValueError as e:
                            await client.reject_tool(event.request_id)
                            session.append("tool", f"{event.title} (invalid: {e})", "msg msg-tool")
                            sel().log_tool_invocation(
                                session_key=session_key,
                                agent=_agent_label(session),
                                source="dashboard",
                                tool_name=event.title,
                                tool_kind=event.tool_kind,
                                outcome="denied",
                                request_id=event.request_id,
                                error=f"validation_failed: {e}",
                            )
                        else:
                            await client.approve_tool(event.request_id)
                            _tool_title = _broadcast_auto_tool(state, session, event)
                            state.broadcast_ws(
                                "activity_event",
                                {
                                    "session": session.key,
                                    "kind": "permission",
                                    "text": f"Auto-approved: {_tool_title}",
                                },
                            )
                            sel().log_tool_invocation(
                                session_key=session_key,
                                agent=_agent_label(session),
                                source="dashboard",
                                tool_name=_tool_title,
                                tool_kind=event.tool_kind,
                                outcome="auto_approved",
                                request_id=event.request_id,
                            )
                        continue
                    try:
                        validated_tool = _validate_tool_name(event.title, event.tool_kind)
                    except ValueError as e:
                        await client.reject_tool(event.request_id)
                        session.append("tool", f"{event.title} (invalid: {e})", "msg msg-tool")
                        sel().log_tool_invocation(
                            session_key=session_key,
                            agent=_agent_label(session),
                            source="dashboard",
                            tool_name=event.title,
                            tool_kind=event.tool_kind,
                            outcome="denied",
                            request_id=event.request_id,
                            error=f"validation_failed: {e}",
                        )
                        continue
                    try:
                        _parsed_input = json.loads(event.tool_input) if event.tool_input else None
                    except Exception:
                        _parsed_input = None
                    try:
                        pre_hook_results = await _fire(
                            HOOK_EVENT_PRE_TOOL_USE, tool_name=validated_tool,
                            tool_input=_parsed_input,
                        )
                    except Exception as hook_exc:
                        await client.reject_tool(event.request_id)
                        session.append("tool", f"{event.title} (hook error)", "msg msg-tool")
                        sel().log_tool_invocation(
                            session_key=session_key,
                            agent=_agent_label(session),
                            source="dashboard",
                            tool_name=event.title,
                            tool_kind=event.tool_kind,
                            outcome="hook_error",
                            request_id=event.request_id,
                            error=str(hook_exc),
                        )
                        continue
                    if any(r.startswith("BLOCKED:") for r in pre_hook_results):
                        await client.reject_tool(event.request_id)
                        _blk = next((r for r in pre_hook_results if r.startswith("BLOCKED:")), "")
                        _blk_reason = _blk.removeprefix("BLOCKED:").strip() or "policy hook"
                        session.append(
                            "tool", f"{event.title} (hook blocked: {_blk_reason})", "msg msg-tool"
                        )
                        sel().log_tool_invocation(
                            session_key=session_key,
                            agent=_agent_label(session),
                            source="dashboard",
                            tool_name=event.title,
                            tool_kind=event.tool_kind,
                            outcome="hook_blocked",
                            request_id=event.request_id,
                        )
                        continue
                    _pre_tool_hooks_fired = True
                    # Hooks passed — fall through to trust-reads/trust/yolo/interactive
                cmd = _extract_bash_command(event.tool_input) if event.tool_input else ""
                yolo_active = state.is_yolo_active()
                # Effective risk of THIS call (per-invocation): the tool's declared
                # risk downgraded to safe when it's a read-only invocation. The
                # single source of truth (task_modes.resolve_effective_risk) — also
                # surfaced to the user on the approval card below.
                effective_risk = resolve_effective_risk(
                    getattr(event, "risk_level", "") or "", event.title, event.tool_kind, event.tool_input
                )
                # Trust-reads: auto-approve any EFFECTIVE-SAFE tool (read_file, grep,
                # knowledge_search, web_search, AND read-only bash — subsumed as safe
                # by invocation). CAUTION/DESTRUCTIVE still prompt.
                if session._trust_reads and not session._trust and not yolo_active and effective_risk == "safe":
                    try:
                        validated_tool = _validate_tool_name(event.title, event.tool_kind)
                    except ValueError as e:
                        await client.reject_tool(event.request_id)
                        session.append(
                            "tool",
                            f"{event.title} (invalid: {e})",
                            "msg msg-tool",
                        )
                        continue
                    await client.approve_tool(event.request_id)
                    _tool_title = _broadcast_auto_tool(state, session, event)
                    session.append(
                        "tool",
                        f"{_tool_title}",
                        "msg msg-tool",
                        meta={"tool_call_id": event.tool_call_id, "purpose": redact_credentials(redact_exfiltration_urls((event.tool_purpose or "")[:200])[0])[0]} if event.tool_call_id else None,
                    )
                    sel().log_tool_invocation(
                        session_key=session_key,
                        agent=_agent_label(session),
                        source="dashboard",
                        tool_name=event.title,
                        tool_kind=event.tool_kind,
                        outcome="auto_approved",
                        request_id=event.request_id,
                        metadata={"reason": "trust_reads", "risk": effective_risk},
                    )
                    continue
                # Trust mode (per-session) or YOLO mode (global) — auto-approve
                if session._trust or yolo_active:
                    try:
                        validated_tool = _validate_tool_name(event.title, event.tool_kind)
                    except ValueError as e:
                        await client.reject_tool(event.request_id)
                        session.append("tool", f"{event.title} (invalid: {e})", "msg msg-tool")
                        sel().log_tool_invocation(
                            session_key=session_key,
                            agent=_agent_label(session),
                            source="dashboard",
                            tool_name=event.title,
                            tool_kind=event.tool_kind,
                            outcome="denied",
                            request_id=event.request_id,
                            error=f"validation_failed: {e}",
                        )
                        continue
                    if not _pre_tool_hooks_fired:
                        try:
                            _parsed_input = json.loads(event.tool_input) if event.tool_input else None
                        except Exception:
                            _parsed_input = None
                        try:
                            pre_hook_results = await _fire(
                                HOOK_EVENT_PRE_TOOL_USE, tool_name=validated_tool,
                                tool_input=_parsed_input,
                            )
                        except Exception as hook_exc:
                            await client.reject_tool(event.request_id)
                            session.append("tool", f"{event.title} (hook error)", "msg msg-tool")
                            sel().log_tool_invocation(
                                session_key=session_key,
                                agent=_agent_label(session),
                                source="dashboard",
                                tool_name=event.title,
                                tool_kind=event.tool_kind,
                                outcome="hook_error",
                                request_id=event.request_id,
                                error=str(hook_exc),
                            )
                            continue
                        if any(r.startswith("BLOCKED:") for r in pre_hook_results):
                            await client.reject_tool(event.request_id)
                            session.append("tool", f"{event.title} (hook blocked)", "msg msg-tool")
                            sel().log_tool_invocation(
                                session_key=session_key,
                                agent=_agent_label(session),
                                source="dashboard",
                                tool_name=event.title,
                                tool_kind=event.tool_kind,
                                outcome="hook_blocked",
                                request_id=event.request_id,
                            )
                            continue
                    await client.approve_tool(event.request_id)
                    _tool_title = _broadcast_auto_tool(state, session, event)
                    sel().log_tool_invocation(
                        session_key=session_key,
                        agent=_agent_label(session),
                        source="dashboard",
                        tool_name=_tool_title,
                        tool_kind=event.tool_kind,
                        outcome="auto_approved",
                        request_id=event.request_id,
                        # Record the effective risk of a blanket auto-approval so a
                        # security auditor can see a DESTRUCTIVE tool ran under trust/
                        # YOLO without a human prompt — the highest-value audit signal
                        # under the "risk is an indicator, floor covers everything" model.
                        metadata={"reason": "yolo" if yolo_active else "trust", "risk": effective_risk},
                    )
                    continue
                # Auto-reject remaining tools after one rejection in a batch
                if getattr(session, '_batch_rejected', False):
                    await client.reject_tool(event.request_id)
                    _title, _ = redact_exfiltration_urls(event.title)
                    _title, _ = redact_credentials(_title)
                    _purpose = redact_credentials(redact_exfiltration_urls((event.tool_purpose or "")[:200])[0])[0]
                    session.append("tool", f"{_title} (rejected)", "msg msg-tool",
                                meta={"tool_call_id": event.tool_call_id, "purpose": _purpose} if event.tool_call_id else None)
                    # Mark the permission as resolved so UI shows rejection
                    perm_meta: dict[str, str] = {"request_id": str(event.request_id), "tool_call_id": event.tool_call_id or "", "resolved": "rejected"}
                    session.append("permission", _title, json.dumps(perm_meta))
                    sel().log_tool_invocation(
                        session_key=session_key,
                        agent=_agent_label(session),
                        source="dashboard",
                        tool_name=event.title,
                        tool_kind=event.tool_kind,
                        outcome="rejected",
                        request_id=event.request_id,
                        metadata={"reason": "batch_rejection"},
                    )
                    logger.warning("AUTO-REJECTED tool=%r (batch rejection)", event.title)
                    continue
                # Interactive approval — send to frontend, wait for decision
                perm_meta = {"request_id": str(event.request_id), "tool_call_id": event.tool_call_id or ""}
                if event.tool_input:
                    # The native loop hands us a parsed dict; ACP agents hand us a
                    # JSON string. The redact guards and the approval-card contract
                    # (state rehydration + frontend) both require a string, so
                    # coerce before scanning.
                    input_text = tool_input_to_str(event.tool_input)
                    # Security: scan for exfiltration URLs and credentials
                    sanitized, _ = redact_exfiltration_urls(input_text)
                    sanitized, _ = redact_credentials(sanitized)
                    perm_meta["tool_input"] = sanitized
                # Flag read-only bash commands for context-aware buttons
                if cmd:
                    perm_meta["is_read_only"] = "1" if is_read_only_bash(cmd) else ""
                # Effective risk of this call (computed above) — a user-facing
                # INDICATOR on the card so the human can weigh the decision. It does
                # not gate: an explicit trust/YOLO still auto-approves everything.
                perm_meta["risk"] = effective_risk
                session.append(
                    "permission",
                    event.title,
                    json.dumps(perm_meta),
                )
                # The live chat page consumes this turn via the HTTP stream, so
                # session.append's SSE broadcast is suppressed (_has_reader). Emit
                # a typed `approval` WS event so the card renders LIVE — without it
                # the prompt only appeared after a manual reload (which rehydrated
                # the persisted permission message).
                state.broadcast_ws(
                    "approval",
                    {
                        "session": session.key,
                        "id": str(event.request_id),
                        "tool": event.title,
                        "tool_input": perm_meta.get("tool_input", ""),
                        "tool_purpose": event.tool_purpose or "",
                        "risk": effective_risk,
                    },
                )
                loop = asyncio.get_running_loop()
                fut: asyncio.Future[str] = loop.create_future()
                session._approval_futures[str(event.request_id)] = fut
                # Push via global SSE AFTER registering the future, so the
                # session dict reflects pending_approval=true and Board cards
                # move into the Blocked lane without a browser refresh.
                state.push_sessions_update()
                try:
                    outcome = await asyncio.wait_for(fut, timeout=7200.0)
                except asyncio.TimeoutError:
                    outcome = "rejected"
                finally:
                    session._approval_futures.pop(str(event.request_id), None)
                if outcome == "approved_trust_reads":
                    session._trust_reads = True
                    outcome = "approved"
                if outcome == "approved":
                    try:
                        validated_tool = _validate_tool_name(event.title, event.tool_kind)
                    except ValueError as e:
                        await client.reject_tool(event.request_id)
                        session.append("tool", f"{event.title} (invalid: {e})", "msg msg-tool")
                        sel().log_tool_invocation(
                            session_key=session_key,
                            agent=_agent_label(session),
                            source="dashboard",
                            tool_name=event.title,
                            tool_kind=event.tool_kind,
                            outcome="denied",
                            request_id=event.request_id,
                            error=f"validation_failed: {e}",
                            metadata={"reason": "interactive"},
                        )
                        break
                    try:
                        _parsed_input = json.loads(event.tool_input) if event.tool_input else None
                    except Exception:
                        _parsed_input = None
                    try:
                        pre_hook_results = await _fire(
                            HOOK_EVENT_PRE_TOOL_USE, tool_name=validated_tool,
                            tool_input=_parsed_input,
                        )
                    except Exception as hook_exc:
                        await client.reject_tool(event.request_id)
                        session.append("tool", f"{event.title} (hook error)", "msg msg-tool")
                        sel().log_tool_invocation(
                            session_key=session_key,
                            agent=_agent_label(session),
                            source="dashboard",
                            tool_name=event.title,
                            tool_kind=event.tool_kind,
                            outcome="hook_error",
                            request_id=event.request_id,
                            error=str(hook_exc),
                            metadata={"reason": "interactive"},
                        )
                        break
                    if any(r.startswith("BLOCKED:") for r in pre_hook_results):
                        await client.reject_tool(event.request_id)
                        _blk = next((r for r in pre_hook_results if r.startswith("BLOCKED:")), "")
                        _blk_reason = _blk.removeprefix("BLOCKED:").strip() or "policy hook"
                        session.append(
                            "tool", f"{event.title} (hook blocked: {_blk_reason})", "msg msg-tool"
                        )
                        sel().log_tool_invocation(
                            session_key=session_key,
                            agent=_agent_label(session),
                            source="dashboard",
                            tool_name=event.title,
                            tool_kind=event.tool_kind,
                            outcome="hook_blocked",
                            request_id=event.request_id,
                            metadata={"reason": "interactive"},
                        )
                    else:
                        await client.approve_tool(event.request_id)
                        _approved_title, _ = redact_exfiltration_urls(event.title)
                        _approved_title, _ = redact_credentials(_approved_title)
                        session.append("tool", f"{_approved_title}", "msg msg-tool",
                                    meta={"tool_call_id": event.tool_call_id, "purpose": redact_credentials(redact_exfiltration_urls((event.tool_purpose or "")[:200])[0])[0]} if event.tool_call_id else None)
                        sel().log_tool_invocation(
                            session_key=session_key,
                            agent=_agent_label(session),
                            source="dashboard",
                            tool_name=event.title,
                            tool_kind=event.tool_kind,
                            outcome="approved",
                            request_id=event.request_id,
                            metadata={"reason": "interactive", "risk": effective_risk},
                        )
                else:
                    await client.reject_tool(event.request_id)
                    session.append("tool", f"{event.title} (rejected)", "msg msg-tool")
                    sel().log_tool_invocation(
                        session_key=session_key,
                        agent=_agent_label(session),
                        source="dashboard",
                        tool_name=event.title,
                        tool_kind=event.tool_kind,
                        outcome="rejected",
                        request_id=event.request_id,
                        metadata={"reason": "interactive", "risk": effective_risk},
                    )

                if outcome != "approved":
                    # mark batch_rejected as true and continue loop instead of breaking
                    # This will allow for marking other batched approval requests as rejected too
                    session._batch_rejected = True
                    logger.warning("PERM REJECTED tool=%r outcome=%r — auto-rejecting remaining batch", event.title, outcome)
                    continue
            elif event.kind == EVENT_COMPACTION_STATUS:
                logger.debug("Main loop: compaction event text=%r", event.text)
                if _broadcast_compaction_result(state, session, event):
                    saw_compaction = True
                    assistant_text = ""
            elif event.kind == EVENT_CLEAR_STATUS:
                session.messages.clear()
                assistant_text = ""
                session.append("assistant", "Conversation cleared.", "msg msg-a")
                state.broadcast_ws("session_clear", {"session": session.key})
                state.broadcast_ws(
                    "chat_message",
                    {"session": session.key, "role": "assistant", "content": "Conversation cleared."},
                )
            elif event.kind == EVENT_AGENT_SWITCHED:
                new_agent, _ = redact_credentials(event.text)
                new_agent, _ = redact_exfiltration_urls(new_agent)
                if new_agent:
                    session.agent = new_agent
                    assistant_text = ""
                    session.append(
                        "assistant",
                        f"Switched to agent: {new_agent}",
                        "msg msg-a",
                    )
                    state.broadcast_ws(
                        "session_agent_switch",
                        {"session": session.key, "agent": new_agent},
                    )
                    needs_session_reset = True
            elif event.kind == EVENT_COMPLETE:
                if event.input_tokens or event.output_tokens:
                    stats = Stats()
                    stats.inc_input_tokens(event.input_tokens)
                    stats.inc_output_tokens(event.output_tokens)
                    if event.cache_creation_tokens:
                        stats.inc_cache_creation_tokens(event.cache_creation_tokens)
                    if event.cache_read_tokens:
                        stats.inc_cache_read_tokens(event.cache_read_tokens)
                    # Realtime burst signal for the topbar token tickers — the
                    # 3s /api/system poll is too coarse to catch a turn's usage.
                    state.broadcast_ws(
                        "token_usage",
                        {
                            "session": session.key,
                            "input": int(event.input_tokens or 0),
                            "output": int(event.output_tokens or 0),
                        },
                    )
                    if event.num_turns:
                        stats.inc_turns(event.num_turns)
                    if event.duration_ms:
                        stats.inc_duration_ms(event.duration_ms)
                    # Resolve the model that actually ran for the cost estimate. When
                    # the user left model on "auto", some ACP backends report the
                    # resolved model only via an `init` event that arrives mid-turn, so
                    # session.model may still be empty here — read it back from the
                    # provider for the estimate. Use it ONLY for the estimate, never
                    # write it onto session.model (the user's selection); the ACP CLI's
                    # internal model would clobber the user's choice with a model no
                    # model-provider offers.
                    _record_model = session.model
                    if not _record_model:
                        _prov_model = getattr(
                            getattr(client, "client", None), "_model", ""
                        ) or ""
                        if (
                            isinstance(_prov_model, str)
                            and _prov_model
                            and _prov_model != "auto"
                        ):
                            _record_model = _prov_model
                    # Derive cost from the pricing table when the provider didn't
                    # report one (most set cost_usd=0.0). Now that the model is
                    # resolved, estimate from token counts so the cost ticker +
                    # usage ledger show a real number. Unknown model → 0.0 (honest
                    # unpriced), so the provider-reported value (if any) always wins.
                    if not event.cost_usd and _record_model:
                        from personalclaw.pricing import estimate_cost
                        event.cost_usd = estimate_cost(
                            _record_model,
                            input_tokens=event.input_tokens,
                            output_tokens=event.output_tokens,
                            cache_read_tokens=event.cache_read_tokens,
                            cache_creation_tokens=event.cache_creation_tokens,
                        )
                    if event.cost_usd:
                        stats.inc_cost_usd(event.cost_usd)
                _stop_reason = event.stop_reason
                _turn_event_count = event.event_count
                _turn_tool_call_count = event.tool_call_count
                if (
                    _stop_reason
                    and _stop_reason != STOP_REASON_END_TURN
                    and _stop_reason != STOP_REASON_CANCELLED
                ):
                    logger.warning(
                        "Unexpected stop_reason %r for session %s",
                        _stop_reason,
                        session.key,
                    )
                break

        # Agent process died mid-turn: re-queue message for automatic retry
        # (mirrors AcpProcessDied handling). Eager reconnect in the provider
        # restores MCPs in background; re-queue ensures the user's message
        # is not silently dropped.
        if _stop_reason and _stop_reason.startswith("error:"):
            _rc = getattr(client, "exit_code", None)
            _rc_suffix = f" (exit {_rc})" if _rc is not None else ""

            def _emit_error(msg: str) -> None:
                session.append("error", msg, "msg msg-err")
                state.broadcast_ws(
                    "chat_message",
                    {"session": session.key, "role": "error", "content": msg},
                )

            if _prompt_depth == 0 and session._acp_pipe_death_retries < 3:
                session._acp_pipe_death_retries += 1
                session.queue_insert(0, message)
                _emit_error(f"⟳ Connection lost{_rc_suffix} — retrying...")
            elif session._acp_pipe_death_retries >= 3:
                _emit_error(f"Session stuck{_rc_suffix} — please start a new chat.")
            else:
                _emit_error(f"⟳ Connection lost{_rc_suffix} — please retry.")
            return

        # /compact acknowledged but compaction deferred — send a lightweight
        # follow-up to trigger the actual compaction so the user doesn't have to.
        logger.debug(
            "Compaction check: first_word=%r saw_compaction=%s", first_word, saw_compaction
        )
        if first_word == "/compact" and not saw_compaction:
            # Clear ACP agent's streamed "Compacting conversation..." text
            session.messages = [m for m in session.messages if m.get("role") != "chunk"]
            assistant_text = ""
            state.broadcast_ws("chat_done", {"session": session.key})
            # Tell frontend to show compacting state and disable input
            logger.info("Deferred compaction: waiting for compaction result")
            state.broadcast_ws(
                "chat_message",
                {"session": session.key, "role": "compacting", "content": ""},
            )
            # ACP agent fires compaction asynchronously after EVENT_COMPLETE —
            # just wait for the result without sending another prompt.
            compaction_result = await client.wait_for_compaction(timeout=120.0)
            logger.info("Deferred compaction result: %s", compaction_result)
            if compaction_result["type"] == "completed":
                summary, _ = redact_credentials(compaction_result.get("summary", ""))
                summary, _ = redact_exfiltration_urls(summary)
                msg = (
                    f"Conversation compacted: {summary}"
                    if summary
                    else "Conversation compacted."
                )
            elif compaction_result["type"] == "failed":
                msg = "Compaction failed."
            else:
                msg = "Compaction timed out."
            session.append("assistant", msg, "msg msg-a")
            state.broadcast_ws(
                "chat_message",
                {"session": session.key, "role": "assistant", "content": msg},
            )
            # Update context usage after compaction
            pct = client.context_usage_pct()
            state.broadcast_ws("context_usage", {"session": session.key, "pct": round(pct, 1)})

        # ── Empty-response auto-retry ───────────────────────────────────────
        # A genuinely empty assistant turn (no text AND no tool calls) that is
        # not a benign no-op self-corrects: silently re-queue once; only a SECOND
        # consecutive empty surfaces a card. Benign no-ops that legitimately
        # produce no final text are excluded — user cancel, a slash command, a
        # compaction/clear/agent-switch turn (each appends its own status line),
        # and tool-only turns (the agent did work, just no closing prose). The
        # silent retry re-queues the same prompt at the head of the queue; usage
        # for the empty turn was already recorded at EVENT_COMPLETE, and the retry
        # is a fresh turn, so nothing is double-counted.
        # Goal loop workers own a dedicated deliverable-forcing re-prompt loop
        # (gateway _fire), so the generic empty-retry must stand aside for them —
        # two retry mechanisms on the same turn would compete.
        _is_empty = is_empty_turn(
            assistant_text=assistant_text,
            stop_reason=_stop_reason,
            saw_compaction=saw_compaction,
            needs_session_reset=needs_session_reset,
            is_slash=is_slash,
            tool_call_count=_turn_tool_call_count,
            is_loop=getattr(session, "_app", "") == "loop",
        )
        if _is_empty:
            if _prompt_depth == 0 and session._empty_response_retries == 0:
                # First empty → silently re-queue the same prompt. The finally
                # block drains the queue (FIFO re-dispatch), same as the error
                # paths; no card, no history write (nothing was appended).
                session._empty_response_retries += 1
                logger.info(
                    "Empty assistant turn for session %s — silently re-queuing once",
                    session.key,
                )
                session.queue_insert(0, message)
                return
            elif session._empty_response_retries >= 1:
                # Second consecutive empty → surface the card and reset the streak.
                session._empty_response_retries = 0
                _empty_msg = "Empty response — please retry."
                session.append("error", _empty_msg, "msg msg-err")
                state.broadcast_ws(
                    "chat_message",
                    {"session": session.key, "role": "error", "content": _empty_msg},
                )
                return
        else:
            # Any non-empty (or benign) turn clears the consecutive-empty streak.
            session._empty_response_retries = 0

        if assistant_text:
            _flush_segment(state, session, assistant_text, broadcast=False)
        # Save to history and trigger memory consolidation
        _save_session_to_history(state, session)
        session._prompt_busy_retries = 0
        session._acp_pipe_death_retries = 0

        if _stop_reason == STOP_REASON_CANCELLED:
            logger.info("Turn cancelled by user for session %s", session.key)
        else:
            _maybe_consolidate(state, session)
            # Continuous learning: after a learning-worthy turn, capture a durable
            # correction before the next turn (vs waiting for session-end
            # consolidation). Best-effort + gated; never blocks. Skips incognito.
            try:
                _maybe_after_turn_review(state, session, message, assistant_text, _turn_tool_call_count, provider=client)
            except Exception:
                logger.debug("after-turn review failed", exc_info=True)
            # Skill axis (4-tier ladder): a background LLM review that may PROPOSE a
            # skill (propose-only queue). Non-blocking; own config flag.
            try:
                _maybe_skill_ladder_review(state, session, message, assistant_text, _turn_tool_call_count)
            except Exception:
                logger.debug("skill-ladder review scheduling failed", exc_info=True)
        state.sessions.check_context_usage(session_key, client)
        pct = client.context_usage_pct()
        state.broadcast_ws("context_usage", {"session": session.key, "pct": round(pct, 1)})
        if _stop_reason != STOP_REASON_CANCELLED:
            state.sessions.record_success(session_key)
        # Broadcast prompt stats for the activity viewer (the live-only "Turn
        # complete" line). Reads the provider-neutral counts carried on the
        # terminal complete event — populated identically by the native loop and
        # the ACP client — so both agent paths render the same chip.
        if _turn_event_count or _turn_tool_call_count:
            state.broadcast_ws(
                "activity_event",
                {
                    "session": session.key,
                    "kind": "stats",
                    "text": f"Turn complete: {_turn_event_count} events, {_turn_tool_call_count} tool calls, context {round(pct)}%",
                },
            )
        _stop_text = redact_exfiltration_urls(assistant_text[:500])[0]
        _stop_text = redact_credentials(_stop_text)[0]
        await _fire(HOOK_EVENT_STOP, _stop_text)

        # ── Bidirectional sync: mirror response to the linked channel thread ──
        # Rendering (mrkdwn, OPTIONS blocks) is the channel's concern — delegate to
        # the active ChannelDelivery so the dashboard imports no channel code.
        if assistant_text and state.channel_delivery and _mirror_thread and _mirror_chan:
            try:
                await state.channel_delivery.deliver_chat_mirror(
                    _mirror_chan, assistant_text, _mirror_thread
                )
            except Exception:
                logger.debug("Failed to mirror response to channel", exc_info=True)
    except asyncio.CancelledError:
        if assistant_text:
            session.messages = [m for m in session.messages if m.get("role") != "chunk"]
            session.append("assistant", redact_credentials(redact_exfiltration_urls(assistant_text)[0])[0], "msg msg-a")
    except AcpProcessDied as exc:
        logger.warning("ACP process died in session %s: %s — resetting session", session.key, exc)
        needs_session_reset = True
        if assistant_text:
            session.messages = [m for m in session.messages if m.get("role") != "chunk"]
            session.append("assistant", redact_credentials(redact_exfiltration_urls(assistant_text)[0])[0], "msg msg-a")
        session._acp_pipe_death_retries += 1
        if _prompt_depth == 0 and session._acp_pipe_death_retries <= 3:
            session.queue_insert(0, message)
            session.append("error", "⟳ Connection lost — retrying...", "msg msg-err")
        elif session._acp_pipe_death_retries > 3:
            session.append("error", "Session stuck — please start a new chat.", "msg msg-err")
        else:
            session.append("error", "⟳ Connection lost — please retry.", "msg msg-err")
    except PromptBusyExhaustedError:
        # Provider was killed after prompt-busy retries exhausted — reset + re-queue.
        logger.info("Prompt busy exhausted in session %s — resetting session and re-queuing", session.key)
        needs_session_reset = True  # checked in finally block
        if assistant_text:
            session.messages = [m for m in session.messages if m.get("role") != "chunk"]
            session.append("assistant", redact_credentials(redact_exfiltration_urls(assistant_text)[0])[0], "msg msg-a")
        session._prompt_busy_retries += 1
        if _prompt_depth == 0 and session._prompt_busy_retries <= 3:
            session.queue_insert(0, message)
        elif session._prompt_busy_retries > 3:
            session.append("error", "Session stuck — please start a new chat.", "msg msg-err")
    except AcpError as exc:
        logger.warning("ACP error in session %s: %s", session.key, exc)
        _msg = str(exc)
        # Retry-eligible transients:
        #   - "already in progress": prompt busy (ACP agent side)
        #   - "process exited" / "not running": ACP subprocess died, need cold-start
        # For both: reset the session and re-queue the message so auto-nudges
        # (and dashboard messages) get executed on a fresh provider instead of
        # surfacing a bare error card with no work done.
        _retry_eligible = (
            "already in progress" in _msg
            or "process exited" in _msg
            or "not running" in _msg
        )
        if _retry_eligible and _prompt_depth == 0:
            logger.info(
                "ACP transient (%s) in session %s — resetting session and re-queuing",
                _msg[:80], session.key,
            )
            needs_session_reset = True  # checked in finally block
            if assistant_text:
                _safe, _ = redact_exfiltration_urls(assistant_text)
                _safe, _ = redact_credentials(_safe)
                session.messages = [m for m in session.messages if m.get("role") != "chunk"]
                session.append("assistant", _safe, "msg msg-a")
            session._prompt_busy_retries += 1
            if session._prompt_busy_retries <= 3:
                session.queue_insert(0, message)
            else:
                session.append("error", "Session stuck — please start a new chat.", "msg msg-err")
        else:
            if assistant_text:
                _safe, _ = redact_exfiltration_urls(assistant_text)
                _safe, _ = redact_credentials(_safe)
                session.messages = [m for m in session.messages if m.get("role") != "chunk"]
                session.append("assistant", _safe, "msg msg-a")
            _err_text, _ = redact_exfiltration_urls(humanize_provider_error(exc))
            _err_text, _ = redact_credentials(_err_text)
            session.append(
                "error",
                _err_text,
                "msg msg-err",
            )
    except Exception as exc:
        logger.exception("Dashboard chat error in session %s", session.key)
        _err_text, _ = redact_exfiltration_urls(humanize_provider_error(exc))
        _err_text, _ = redact_credentials(_err_text)
        session.append("error", _err_text, "msg msg-err")
        # Definitive turn-outcome flag (the last message isn't a reliable signal:
        # the finally block below appends more — queued re-dispatch etc.). Read
        # by the autonudge re-arm and the gateway goal-loop done-callback.
        session._last_turn_errored = True
        await _fire(HOOK_EVENT_ERROR, _err_text)
        await state.sessions.record_failure(session_key)
    finally:
        session._batch_rejected = False
        # ── AutoNudge: re-arm the idle timer on EVERY turn exit (success OR
        # error), so a loop survives a failed turn instead of silently dying.
        # A persistently-broken worker is bounded by the service's consecutive-
        # error cap and (for goal loops) the supervisor's fail-fast.
        try:
            from personalclaw.autonudge import (
                get_instance as _autonudge_get,  # circular: autonudge -> dashboard.chat -> chat_runner
            )

            _autonudge = _autonudge_get()
            if _autonudge is not None and not getattr(session, "_suppress_autonudge_rearm", False):
                _autonudge.notify_turn_complete(
                    session.key, errored=getattr(session, "_last_turn_errored", False)
                )
        except Exception:
            logger.debug("autonudge.notify_turn_complete failed", exc_info=True)
        # Attach this turn's file changes onto the assistant message's meta
        # (before any queued re-dispatch resets the accumulator). Best-effort.
        try:
            _flush_file_changes(session)
        except Exception:
            logger.debug("file-change flush failed", exc_info=True)
        # Clean up mirror stream on any exit path
        if _mirror_stream_ts and state.channel_delivery and _mirror_chan:
            try:
                if _mirror_active_task:
                    await state.channel_delivery.append_stream_task(
                        _mirror_chan, _mirror_stream_ts,
                        _mirror_active_task, _mirror_active_task_title, "complete",
                    )
            except Exception:
                logger.debug("Task append cleanup failed", exc_info=True)
            try:
                await state.channel_delivery.stop_stream(_mirror_chan, _mirror_stream_ts)
            except Exception:
                logger.debug("Stream cleanup failed", exc_info=True)
        if _acquired:
            if needs_session_reset:
                try:
                    await state.sessions.reset(session_key)
                except Exception:
                    logger.warning("Failed to reset session %s after agent switch", session_key)
            state.sessions.release(session_key)
        # Process queued messages (FIFO) — keep SSE stream alive
        if session._queue:
            if session._stopping:
                session.append(
                    "error",
                    "⟳ Session reset — processing next message with conversation history",
                    "msg msg-err",
                )
            session._stopping = False
            state.push_sessions_update()
            # ── Merge or pop: combine queued messages if configured ──
            try:
                _cfg = AppConfig.load()
                merge = _cfg.dashboard.merge_queued_messages
            except Exception:
                logger.warning("Failed to load config; falling back to sequential dequeue", exc_info=True)
                merge = False
            next_msg, consumed = _dequeue_next_message(session, merge_enabled=merge)
            # Notify frontend to remove each consumed queued card
            for item in consumed:
                _c, _ = redact_exfiltration_urls(item["content"])
                _c, _ = redact_credentials(_c)
                _redacted = _redact_for_display(_c)
                state.broadcast_ws("queue_pop", {"session": session.key, "content": _redacted, "queue_id": item["id"]})
            # Redact merged message before storing in session
            next_msg, _ = redact_exfiltration_urls(next_msg)
            next_msg, _ = redact_credentials(next_msg)
            is_cron = next_msg.startswith(CRON_NOTIFY_PREFIX)
            is_subagent = next_msg.startswith(SUBAGENT_COMPLETION_PREFIX)
            _m = CRON_NOTIFY_RE.match(next_msg) if is_cron else None
            cron_label = _m.group(1) if _m else "cron"
            cron_label, _ = redact_exfiltration_urls(cron_label)
            cron_label, _ = redact_credentials(cron_label)
            session.append(
                "subagent" if is_subagent else "inject" if is_cron else "user",
                next_msg,
                json.dumps({"cronLabel": cron_label}) if is_cron else "msg msg-u",
            )
            # A queued user message is persisted here but session.append suppresses
            # the SSE echo for role="user" (the live page normally adds the user
            # bubble optimistically on send — which never happened for a queued
            # message, only its strip card did). Emit a typed event so the bubble
            # renders LIVE as the queue drains; without it the message only appeared
            # after a manual reload (which rehydrated the persisted user turn).
            # Mirrors the approval-card live-render fix above. cron/subagent rows
            # have their own live rendering and must not show as user bubbles.
            if not is_cron and not is_subagent:
                _disp, _ = redact_exfiltration_urls(next_msg)
                _disp, _ = redact_credentials(_disp)
                _disp = _redact_for_display(_disp)
                state.broadcast_ws(
                    "chat_user_message",
                    {"session": session.key, "content": _disp, "ts": session.messages[-1].get("ts", "")},
                )

            task = asyncio.create_task(
                asyncio.wait_for(_run_chat(state, session, next_msg), timeout=CHAT_TURN_TIMEOUT)
            )
            session.task = task
            state._background_tasks.add(task)
            task.add_done_callback(state._background_tasks.discard)
        else:
            session._stopping = False
            # Only send "done" when queue is empty — keeps SSE reader alive
            session.append("done", "", "done")
            # Clear task reference BEFORE pushing session update so that
            # session.running returns False immediately.  Without this,
            # push_sessions_update() reports running=True because the task
            # (this coroutine) hasn't finished its finally block yet.
            session.task = None
            # Push updated running state (now idle) + history refresh to SSE clients
            state.push_sessions_update()
            state.broadcast_ws("chat_done", {"session": session.key})
            state.push_refresh("history")
            # Auto-title: fire in background so it doesn't block the response
            if not session._titled:
                t = asyncio.create_task(_maybe_auto_title(state, session))
                state._background_tasks.add(t)
                t.add_done_callback(state._background_tasks.discard)

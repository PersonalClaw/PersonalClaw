"""What a denied-without-an-answer tool call leaves behind for the morning (F-33).

A tool call that needs a person's approval ends one of four ways
(:data:`~personalclaw.dashboard.approval_state.APPROVAL_OUTCOMES`). Two are answers, and
``cancelled`` is the work that asked stopping first. The other two are this module's:

* **expired** — nobody answered inside the approval window (``agent.approval_timeout_minutes``),
  so the call was denied. Its Inbox row closed with it and nothing else said so: an approval
  asked at night was gone from every surface by morning, and the call had never run.
* **unattended** — the run had no one to ask (a trigger's session, a loop's worker, a channel
  delivery, a subagent), so a call that needs approval is declined AT ONCE rather than parked:
  the chat runner's fail-fast for a runtime that asks, and the native runtime's own decline for
  one that does not (``TOOL_META_AUTO_DENIED``). Declining is the design — an unattended run must
  not wedge waiting for an answer it will never get — but it was written only to the run's
  transcript and the security log.

Each now leaves ONE Inbox item, ``system/auto_denied`` with item kind ``system``: what was denied,
who asked, when, why, and that it did not run. ``refs.session`` is where it happened, so the Inbox
opens the chat or the workflow step; ``refs.chat`` is set only when that is a chat a person can
answer in, which is what lets the Inbox offer to ask it to try again. Deduplicated per approval,
and per ``(session, tool)`` for the unattended case, so a run that retries a declined call is one
item and not twenty.

Best-effort like the approval row it follows: the denial has already happened, and failing to
record it must never turn into a failure of the run that was denied.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from personalclaw import notification_kinds
from personalclaw.workflows import ownership

logger = logging.getLogger(__name__)

#: ``refs.auto_denied`` values — the two ways a call is denied with no answer.
EXPIRED = "expired"
UNATTENDED = "unattended"


def window_words(secs: float) -> str:
    """A wait as a person says it: ``2 hours``, ``90 minutes``, ``1 day``, ``45 seconds``."""
    whole = max(0, int(round(secs)))
    for unit, size in (("day", 86400), ("hour", 3600), ("minute", 60)):
        if whole >= size and whole % size == 0:
            n = whole // size
            return f"{n} {unit}{'' if n == 1 else 's'}"
    if whole >= 60:
        n = whole // 60
        return f"{n} minute{'' if n == 1 else 's'}"
    return f"{whole} second{'' if whole == 1 else 's'}"


def _clock(ts: float) -> str:
    return time.strftime("%H:%M", time.localtime(ts)) if ts > 0 else ""


def _input_line(tool_input: str) -> str:
    text = " ".join(str(tool_input or "").split())
    return text if len(text) <= 200 else text[:199] + "…"


def answerable_chat(session_key: str) -> str:
    """``session_key`` when it names a chat a person can answer in, else ``""``.

    A workflow stage is not a chat, and an unattended session (a trigger's, a loop worker's, a
    channel's, the background session) is by definition one nobody is watching — asking either
    to "try again" would be asking nobody.
    """
    from personalclaw.guardrails.policy import is_unattended_session

    key = (session_key or "").strip()
    # A workflow stage (`workflow:<run>:<node>`, `ownership.is_owned`) is not a chat.
    if not key or ownership.is_owned(key) or is_unattended_session(key):
        return ""
    return key


def unattended_origin(session_key: str) -> str:
    """Who was running, for an unattended session key — prose, never the key itself."""
    from personalclaw.guardrails.policy import is_unattended_session

    key = session_key or ""
    if ownership.is_owned(key):
        return "A workflow step"
    if key.startswith("cron:"):
        return "A scheduled automation"
    if key.startswith("subagent:"):
        return "A subagent"
    if key == "_bg":
        return "The background session"
    if key.startswith("loop"):
        return "A loop"
    if is_unattended_session(key):
        return "An unattended run"
    return "A run"


def note_expired(state: Any, entry: dict[str, Any], *, who: str, window_secs: float) -> str:
    """Record an approval nobody answered in time. Returns the Inbox item id, or ``""``."""
    tool = str(entry.get("tool") or "a tool")
    asked = _clock(float(entry.get("ts") or 0.0))
    lines = [
        f"{who} asked to run {tool}" + (f" at {asked}" if asked else "") + ". Nobody answered "
        f"within {window_words(window_secs)}, so it was denied and did not run."
    ]
    detail = _input_line(str(entry.get("tool_input") or ""))
    if detail:
        lines.append(detail)
    session = str(entry.get("session") or "")
    return _emit(
        state,
        title=f"Denied, no answer: {tool}",
        body="\n".join(lines),
        refs={
            "auto_denied": EXPIRED,
            "tool": tool,
            "denied_approval": str(entry.get("id") or ""),
            **({"session": session} if session else {}),
            **({"chat": answerable_chat(session)} if answerable_chat(session) else {}),
        },
        dedup_key=f"auto_denied:{entry.get('id') or ''}",
    )


def note_unattended(
    state: Any, *, session_key: str, tool: str, tool_input: str = "", who: str = ""
) -> str:
    """Record a call an unattended run declined without asking. Returns the item id, or ``""``."""
    tool = tool or "a tool"
    lines = [
        f"{who or unattended_origin(session_key)} asked to run {tool} while running unattended. "
        "An unattended run cannot wait for an approval, so it was denied and did not run."
    ]
    detail = _input_line(tool_input)
    if detail:
        lines.append(detail)
    return _emit(
        state,
        title=f"Denied, no one to ask: {tool}",
        body="\n".join(lines),
        refs={
            "auto_denied": UNATTENDED,
            "tool": tool,
            **({"session": session_key} if session_key else {}),
        },
        dedup_key=f"auto_denied:{session_key}:{tool}",
    )


def _emit(state: Any, *, title: str, body: str, refs: dict[str, Any], dedup_key: str) -> str:
    try:
        from personalclaw.inbox import ItemKind, emit_attention_item

        return emit_attention_item(
            state,
            source="system",
            kind=notification_kinds.AUTO_DENIED,
            item_kind=ItemKind.SYSTEM.value,
            title=title,
            body=body,
            refs=refs,
            dedup_key=dedup_key,
        )
    except Exception:  # noqa: BLE001 - see the module docstring: best-effort
        logger.debug("could not record a denied call: %s", title, exc_info=True)
        return ""

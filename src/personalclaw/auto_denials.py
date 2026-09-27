"""What a denied-without-an-answer tool call leaves behind for the morning.

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
answer in, which is what lets the Inbox offer to ask it to try again. ``refs.trigger`` is the
trigger whose run it was, when a trigger started the work, which is what lets the Inbox offer to
run that trigger again. Deduplicated per approval, and per ``(session, tool)`` for the unattended
case, so a run that retries a declined call is one item and not twenty.

**When a note is done.** An expired note stands for one call, ``refs.call``, asked in one place
(its session, or its trigger's run). :func:`settle_retried` moves it to HANDLED, with the answer on
``refs.retry`` and who gave it on ``refs.retry_by``, when that call is asked there again and
answered, allowed or denied — or runs there again because a standing grant approved it without
asking (a chat's Trust, YOLO, an automation's own approval mode). That is the
Inbox's own rule (``inbox.resolve_attention_items``): a row raised for a standing request closes
when the request stops standing. Sending the retry does not close it, because nothing has been
decided yet. An unattended note has no answer to wait for, and stays until the owner handles it.

Best-effort like the approval row it follows: the denial has already happened, and failing to
record it must never turn into a failure of the run that was denied.
"""

from __future__ import annotations

import hashlib
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
    from personalclaw.action_providers.heartbeat_tasks_provider import TASK_SESSION_PREFIX
    from personalclaw.guardrails.policy import is_unattended_session

    key = session_key or ""
    if ownership.is_owned(key):
        return "A workflow step"
    if key.startswith(TASK_SESSION_PREFIX):
        return "A heartbeat task"
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


def trigger_asker(name: str) -> str:
    """A trigger as the one who asked, in a sentence: by its name, else "A trigger"."""
    return f"The trigger “{name}”" if name else "A trigger"


def call_key(tool: str, tool_input: str) -> str:
    """One call, as its approval described it: a digest of its tool and its input.

    What a note and a later approval are compared by, so asking THIS call again settles the note
    and a different call of the same tool does not. It digests the registry's own redacted strings
    (``approval_state._approval_entry``), so it carries nothing the note's body does not show.
    """
    return hashlib.sha256(f"{tool}\n{tool_input}".encode("utf-8")).hexdigest()[:16]


def _call_of(entry: dict[str, Any]) -> str:
    return call_key(str(entry.get("tool") or ""), str(entry.get("tool_input") or ""))


def _asked_in(entry: dict[str, Any]) -> dict[str, str]:
    """Where a call was asked: its session, its trigger's run, or both. Empty for nowhere known."""
    return {key: str(entry[key]) for key in ("session", "trigger") if entry.get(key)}


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
            "call": _call_of(entry),
            **_asked_in(entry),
            **({"chat": answerable_chat(session)} if answerable_chat(session) else {}),
        },
        dedup_key=f"auto_denied:{entry.get('id') or ''}",
    )


def note_unattended(
    state: Any,
    *,
    session_key: str,
    tool: str,
    tool_input: str = "",
    who: str = "",
    trigger: str = "",
) -> str:
    """Record a call an unattended run declined without asking. Returns the item id, or ``""``.

    ``trigger`` is the trigger whose run it was, when one started the work. It names the asker,
    and it is the note's identity: every fire of a nightly trigger runs in a new session, so a
    trigger declined every night is one note until the owner handles it, not one per night.
    """
    from personalclaw.security import redact_field
    from personalclaw.triggers.store import trigger_name

    tool = tool or "a tool"
    asker = who or (
        trigger_asker(redact_field(trigger_name(trigger)))
        if trigger
        else unattended_origin(session_key)
    )
    lines = [
        f"{asker} asked to run {tool} while running unattended. "
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
            **({"trigger": trigger} if trigger else {}),
        },
        dedup_key=f"auto_denied:{f'trigger:{trigger}' if trigger else session_key}:{tool}",
    )


#: ``refs.retry`` values: how the call a note stands for was answered once it was asked again —
#: the two answers an approval has (``approval_state.APPROVAL_OUTCOMES``).
RETRY_ANSWERS = frozenset({"approved", "rejected"})


def settle_retried(state: Any, entry: dict[str, Any], *, answer: str, by: str) -> int:
    """Mark HANDLED each open expired note for the call *entry* was, recording *answer*.

    Called with every answered approval (``approval_state.withdraw_approval``, where every door's
    answer arrives), and with every call a standing grant approved without asking
    (``approval_state.settle_granted``). A note settles only for the same call (``refs.call``) asked
    in the same place, every pair of :func:`_asked_in` matching — the Inbox's ref-subset rule — so a
    different call of the same tool, or the same call in another chat, leaves it open. The answer
    and who gave it (``by``: ``approval_grants.YOU``, or the grant's name) go on the row first, and
    the row then moves through the Inbox's one status transition, which tells every open surface
    and reads the note's notification in the bell. Returns how many notes moved.
    """
    if answer not in RETRY_ANSWERS:
        return 0
    place = _asked_in(entry)
    if not place:
        # Asked nowhere in particular, so it cannot be told from the same call asked elsewhere.
        # `resolve_attention_items` refuses an unscoped resolve for the same reason.
        return 0
    wanted = {"auto_denied": EXPIRED, "call": _call_of(entry), **place}
    try:
        from personalclaw.inbox import (
            OPEN_STATUSES,
            InboxStore,
            ItemStatus,
            live_store,
            set_item_status,
        )

        store = live_store(state)
        if store is None:
            store = InboxStore()
            store.load()
        notes = [
            item
            for item in list(store.items.values())
            if item.status in OPEN_STATUSES
            and all(item.refs.get(key) == value for key, value in wanted.items())
        ]
        for item in notes:
            item.refs["retry"] = answer
            item.refs["retry_by"] = by
        return len(set_item_status(state, store, notes, ItemStatus.HANDLED))
    except Exception:  # noqa: BLE001 - see the module docstring: best-effort
        logger.debug("could not settle the notes for %s", entry.get("id"), exc_info=True)
        return 0


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

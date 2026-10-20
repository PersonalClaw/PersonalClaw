"""Conversation export — one chat to Markdown or JSON, redacted.

A conversation is the most portable thing in the product and until now the only way to
get one out was to select the page. This renders a transcript as Markdown (for reading,
pasting, and archiving) or JSON (for re-import and tooling).

**Every export is redacted, including the user's own words.** The plan says export
"reuses history.py's existing redaction", which was not quite the case worth relying on:
the dashboard write path redacts assistant/tool content but deliberately SKIPS ``user``
and ``system`` roles (``chat_persistence.py:606-608``), so a credential the user typed —
or one pasted into a system-context block — is stored raw and would leave the machine in
a file the user is about to attach to an email. Export re-runs both passes over EVERY
role. That is defense in depth for the already-redacted roles and the only redaction the
user/system roles ever get.

Redaction is applied to the rendered value, never written back: the transcript on disk is
the record of what happened and is not rewritten by reading it.

**An export says who spoke and when, in terms that survive leaving the machine.** A room's
transcript is several members talking, so each line is attributed to its member (and its
role, from the roster the caller passes) rather than to a generic "Assistant". And a stored
time is the machine's local wall-clock time with no offset, which reads as a different
instant anywhere else, so every exported time carries its UTC offset.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from personalclaw.history import speaker_of
from personalclaw.http_download import safe_download_stem
from personalclaw.security import redact_field

logger = logging.getLogger(__name__)

VALID_FORMATS = frozenset({"md", "json"})

#: Roles that carry conversation content. Everything else in a transcript is UI
#: bookkeeping (stop-event cards and friends) and would render as noise in an export.
_CONTENT_ROLES = frozenset({"user", "assistant", "system", "tool"})

_ROLE_LABELS = {
    "user": "You",
    "assistant": "Assistant",
    "system": "System",
    "tool": "Tool",
}


def _zoned(ts: object, *, timespec: str = "auto") -> str:
    """*ts* as ISO 8601 WITH its UTC offset, in the user's zone.

    A stored time is written by ``datetime.now()`` — the machine's local wall clock, no
    offset — so a naive value is read back as exactly that before it is shown in the zone the
    user's schedules use (``timezones.resolve_zone``). A value that already carries an offset
    keeps its instant. One that does not parse as a time is shown as it was stored: an export
    reports the record, it does not guess at it.
    """
    raw = str(ts or "")
    try:
        when = datetime.fromisoformat(raw)
    except ValueError:
        return raw
    if when.tzinfo is None:
        when = when.astimezone()
    try:
        from personalclaw.timezones import resolve_zone

        when = when.astimezone(resolve_zone())
    except Exception:  # noqa: BLE001 - an unreadable zone setting keeps the machine's own offset
        logger.debug("export: the user's zone could not be resolved", exc_info=True)
    return when.isoformat(timespec=timespec)


def _content_messages(messages: list[dict]) -> list[dict]:
    """Conversation-bearing messages only, each with its content redacted.

    A message's ``speaker`` (a room member's name) is kept when it has one; the human's
    lines, and every line of a one-assistant chat, have none.
    """
    out: list[dict] = []
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        role = str(msg.get("role", "") or "")
        if role not in _CONTENT_ROLES:
            continue
        content = redact_field(msg.get("content", ""))
        if not content.strip():
            continue
        entry: dict[str, Any] = {"role": role, "content": content}
        # A call its agent CLI ran without asking her says so in her record of it too, in the
        # words its card uses (`dashboard.ungated_calls.report_ungated_call`).
        unasked = (msg.get("meta") or {}).get("ungated") if role == "tool" else None
        if isinstance(unasked, str) and unasked.strip():
            entry["ungated"] = redact_field(unasked)
        speaker = speaker_of(msg)
        if speaker:
            entry["speaker"] = redact_field(speaker)
        ts = msg.get("ts")
        if ts:
            entry["ts"] = _zoned(ts)
        out.append(entry)
    return out


def _one_line(text: str) -> str:
    """A roster entry as one line of a heading: redacted, whitespace folded."""
    return " ".join(redact_field(text or "").split())


def _who(msg: dict, members: Mapping[str, str]) -> str:
    """Who said *msg*, in the words the room view uses.

    A member's line names the member and the role it argues from. A ``system`` line with a
    speaker is the ROOM's note about that member (a refused tool, today) and says so, so the
    room's words are never put in the member's mouth. A speaker no longer on the roster still
    said what they said, so the name stands alone.
    """
    speaker = msg.get("speaker", "")
    if speaker and msg["role"] == "system":
        return f"Room note · {speaker}"
    if speaker:
        role = _one_line(members.get(speaker, ""))
        return f"{speaker} — {role}" if role else speaker
    return _ROLE_LABELS.get(msg["role"], msg["role"].title())


def render_markdown(
    *,
    title: str,
    key: str,
    meta: dict,
    messages: list[dict],
    members: Mapping[str, str] | None = None,
) -> str:
    """A transcript as readable Markdown with a small provenance header.

    Message content is emitted as a blockquote so a transcript containing its own
    markdown headings can't restructure the document around it — a chat about markdown
    would otherwise produce an export whose outline is the chat's content, not the
    conversation.

    *members* is a room's roster (member name → role), for attributing each line.
    """
    safe_title = redact_field(title or key or "Conversation")
    lines = [f"# {safe_title}", ""]

    header: list[str] = []
    for label, field in (("Agent", "agent"), ("Model", "model"), ("Created", "created_at")):
        value = meta.get(field)
        if value:
            shown = _zoned(value, timespec="seconds") if field == "created_at" else str(value)
            header.append(f"- **{label}:** {redact_field(shown)}")
    exported = _content_messages(messages)
    header.append(f"- **Messages:** {len(exported)}")
    header.append("- **Redacted:** credentials and suspicious URLs are removed from this export.")
    lines.extend(header)
    lines.append("")

    for msg in exported:
        who = _who(msg, members or {})
        stamp = f" · {_zoned(msg['ts'], timespec='seconds')}" if msg.get("ts") else ""
        lines.append(f"## {who}{stamp}")
        lines.append("")
        for para in str(msg["content"]).split("\n"):
            lines.append(f"> {para}" if para.strip() else ">")
        if msg.get("ungated"):
            lines.extend([">", f"> {msg['ungated']}"])
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def render_json(
    *,
    title: str,
    key: str,
    meta: dict,
    messages: list[dict],
    members: Mapping[str, str] | None = None,
) -> str:
    """A transcript as JSON: the same redacted messages, plus declared provenance.

    A room's export also lists its roster, so a line's ``speaker`` resolves to a role.
    """
    created = meta.get("created_at", "")
    payload: dict[str, Any] = {
        "key": key,
        "title": redact_field(title or key),
        "agent": redact_field(str(meta.get("agent", "") or "")),
        "model": redact_field(str(meta.get("model", "") or "")),
        "created_at": _zoned(created) if created else "",
        # Stated in the artifact itself so a consumer never mistakes a redacted export
        # for a verbatim transcript.
        "redacted": True,
        "messages": _content_messages(messages),
    }
    if members is not None:
        payload["members"] = [
            {"name": redact_field(name), "role": _one_line(role)} for name, role in members.items()
        ]
    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"


def render(
    fmt: str,
    *,
    title: str,
    key: str,
    meta: dict,
    messages: list[dict],
    members: Mapping[str, str] | None = None,
) -> tuple[str, str]:
    """``(text, content_type)`` for *fmt*. Raises ValueError on an unknown format."""
    if fmt not in VALID_FORMATS:
        raise ValueError(f"unknown format {fmt!r}; expected one of {sorted(VALID_FORMATS)}")
    if fmt == "json":
        rendered = render_json(title=title, key=key, meta=meta, messages=messages, members=members)
        return rendered, "application/json"
    rendered = render_markdown(title=title, key=key, meta=meta, messages=messages, members=members)
    return rendered, "text/markdown"


def export_filename(title: str, key: str, fmt: str) -> str:
    """A filesystem-safe download name for one transcript.

    Redaction and sanitisation both live in
    :func:`~personalclaw.http_download.safe_download_stem`; only the ``<stem>.<fmt>`` shape is
    this function's own. It used to fold the name to ASCII because the route emitted the plain
    ``filename="…"`` form, which cannot carry anything else — so a chat titled ``日本語のチャット``
    downloaded as ``chat.json`` and lost its name entirely. The route now emits RFC 6266's
    ``filename*=UTF-8''`` alongside an ASCII fallback, so the fold is no longer needed and the
    user's own characters survive.
    """
    return f"{safe_download_stem(title, fallback=key) or 'chat'}.{fmt}"

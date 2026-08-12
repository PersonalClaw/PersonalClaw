"""Conversation export — one chat to Markdown or JSON, redacted (SESSION-MANAGEMENT S3).

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
"""

from __future__ import annotations

import json
import logging
from typing import Any

from personalclaw.security import redact_credentials, redact_exfiltration_urls

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


def redact_field(text: str) -> str:
    """Both redaction passes over one field. Applied to EVERY role — see the module
    docstring for why the write path's role exemption can't be inherited here.

    Public because ``session_share`` needs the SAME redaction for the artifact name it
    derives (SM-9). One implementation with two callers, never a second pass that redacts
    slightly less.
    """
    if not text:
        return ""
    safe, _ = redact_exfiltration_urls(str(text))
    safe, _ = redact_credentials(safe)
    return safe


def _content_messages(messages: list[dict]) -> list[dict]:
    """Conversation-bearing messages only, each with its content redacted."""
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
        ts = msg.get("ts")
        if ts:
            entry["ts"] = str(ts)
        out.append(entry)
    return out


def render_markdown(*, title: str, key: str, meta: dict, messages: list[dict]) -> str:
    """A transcript as readable Markdown with a small provenance header.

    Message content is emitted as a blockquote so a transcript containing its own
    markdown headings can't restructure the document around it — a chat about markdown
    would otherwise produce an export whose outline is the chat's content, not the
    conversation.
    """
    safe_title = redact_field(title or key or "Conversation")
    lines = [f"# {safe_title}", ""]

    header: list[str] = []
    for label, field in (("Agent", "agent"), ("Model", "model"), ("Created", "created_at")):
        value = meta.get(field)
        if value:
            header.append(f"- **{label}:** {redact_field(str(value))}")
    exported = _content_messages(messages)
    header.append(f"- **Messages:** {len(exported)}")
    header.append("- **Redacted:** credentials and suspicious URLs are removed from this export.")
    lines.extend(header)
    lines.append("")

    for msg in exported:
        who = _ROLE_LABELS.get(msg["role"], msg["role"].title())
        stamp = f" · {msg['ts']}" if msg.get("ts") else ""
        lines.append(f"## {who}{stamp}")
        lines.append("")
        for para in str(msg["content"]).split("\n"):
            lines.append(f"> {para}" if para.strip() else ">")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def render_json(*, title: str, key: str, meta: dict, messages: list[dict]) -> str:
    """A transcript as JSON: the same redacted messages, plus declared provenance."""
    payload = {
        "key": key,
        "title": redact_field(title or key),
        "agent": redact_field(str(meta.get("agent", "") or "")),
        "model": redact_field(str(meta.get("model", "") or "")),
        "created_at": meta.get("created_at", ""),
        # Stated in the artifact itself so a consumer never mistakes a redacted export
        # for a verbatim transcript.
        "redacted": True,
        "messages": _content_messages(messages),
    }
    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"


def render(fmt: str, *, title: str, key: str, meta: dict, messages: list[dict]) -> tuple[str, str]:
    """``(text, content_type)`` for *fmt*. Raises ValueError on an unknown format."""
    if fmt not in VALID_FORMATS:
        raise ValueError(f"unknown format {fmt!r}; expected one of {sorted(VALID_FORMATS)}")
    if fmt == "json":
        return render_json(title=title, key=key, meta=meta, messages=messages), "application/json"
    return render_markdown(title=title, key=key, meta=meta, messages=messages), "text/markdown"


def export_filename(title: str, key: str, fmt: str) -> str:
    """A filesystem-safe, ASCII-only download name.

    ASCII specifically, not just "safe": the route emits the plain
    ``Content-Disposition: attachment; filename="…"`` form, which cannot carry non-ASCII
    bytes. ``str.isalnum()`` is True for CJK and accented letters, so filtering on it
    alone produced names that broke the header for anyone whose chat titles aren't
    Latin — the fallback keeps the download working instead of failing on their locale.
    """
    source = title or key or "chat"
    stem = "".join(ch if (ch.isascii() and ch.isalnum()) or ch in "-_" else "-" for ch in source)
    stem = "-".join(p for p in stem.split("-") if p)[:60].strip("-")
    return f"{(stem or 'chat').lower()}.{fmt}"

"""ACP frame → AcpEvent decoders — the pure translation surface (P9 cutover step 2).

The one-session :class:`~personalclaw.acp.client.AcpClient` and the multi-session
:class:`~personalclaw.acp.session.AcpSession` both turn raw ``session/update`` frames
into :class:`~personalclaw.acp.types.AcpEvent`s. This module is that decoding logic in
ONE place so the two turn loops can never drift — a ``tool_call`` frame becomes the same
event whether it arrived on the inline client reader or a router-demuxed session queue.

Every function here is pure: no ``self``, no I/O, no process. The small per-turn caches
the decoders read/write (``tool_call_inputs`` keyed by ``toolCallId``, ``offered_options``
keyed by request id) are threaded in as explicit dict params — the caller owns them.
Dependencies are the leaf ``types`` module + the ``security`` redactors + stdlib, so both
the client and the session import from here with no import cycle.
"""

from __future__ import annotations

import base64
import difflib
import json
import logging
import re
from pathlib import Path

from personalclaw.acp.types import (
    EVENT_PERMISSION_REQUEST,
    EVENT_TOOL_CALL,
    EVENT_TOOL_CALL_UPDATE,
    EVENT_TOOL_RESULT,
    UPDATE_AGENT_MESSAGE_CHUNK,
    UPDATE_TOOL_CALL,
    UPDATE_TOOL_CALL_UPDATE,
    AcpEvent,
    JsonRpcMessage,
)
from personalclaw.security import redact_credentials, redact_exfiltration_urls

logger = logging.getLogger(__name__)


def make_unified_diff(old: str, new: str, path: str, max_len: int = 6000) -> str:
    """Generate a unified diff string from old/new text, handling empty inputs."""
    old_lines = (old if old.endswith("\n") else old + "\n").splitlines(keepends=True) if old else []
    new_lines = (new if new.endswith("\n") else new + "\n").splitlines(keepends=True) if new else []
    udiff = difflib.unified_diff(old_lines, new_lines, fromfile=path, tofile=path, n=3)
    return "".join(udiff).rstrip()[:max_len]


def coerce_tool_content(content: object) -> str:
    """Flatten ACP tool-result content blocks to text.

    Blocks look like ``{"type": "content", "content": {"type": "text",
    "text": "..."}}`` (claude-code) — pull out nested text; tolerate plain
    ``{"type": "text", "text": "..."}`` and bare strings too.
    """
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
            continue
        if not isinstance(block, dict):
            continue
        inner = block.get("content")
        if isinstance(inner, dict) and inner.get("text"):
            parts.append(str(inner["text"]))
        elif block.get("text"):
            parts.append(str(block["text"]))
    return "\n".join(p for p in parts if p)


def extract_text_chunk(msg: JsonRpcMessage) -> tuple[str | None, bool]:
    """Extract (text, is_thinking) from an agent_message_chunk session/update, or
    (None, False) if the frame isn't a text chunk. ``is_thinking`` is True when the
    content block type indicates reasoning/thinking content."""
    params = msg.params or {}
    update = params.get("update", {}) if isinstance(params, dict) else {}
    if update.get("sessionUpdate") == UPDATE_AGENT_MESSAGE_CHUNK:
        content = update.get("content", {}) or {}
        text = content.get("text")
        is_thinking = content.get("type", "text") in ("thinking", "reasoning")
        return text, is_thinking
    return None, False


def extract_tool_event(
    msg: JsonRpcMessage,
    tool_call_inputs: dict[str, str],
    tool_calls_sink: list[tuple[str, str]],
) -> AcpEvent | None:
    """Decode a ``tool_call`` frame into an ``EVENT_TOOL_CALL`` (or None).

    Caches the resolved, redacted tool input under ``toolCallId`` in
    ``tool_call_inputs`` so a following permission request can echo the full input,
    and appends ``(kind, title)`` to ``tool_calls_sink`` (the turn's prompt stats)."""
    params = msg.params or {}
    update = params.get("update", {})
    if update.get("sessionUpdate") == UPDATE_TOOL_CALL:
        title = update.get("title", "unknown")
        kind = update.get("kind", "unknown")
        raw_input = update.get("rawInput") or update.get("input") or update.get("params")
        purpose = raw_input.get("__tool_use_purpose", "") if isinstance(raw_input, dict) else ""
        logger.debug(
            "ACP tool_call raw: %s",
            {k: v for k, v in update.items() if k != "sessionUpdate"},
        )
        # Build initial tool input string from raw params
        tool_call_id = update.get("toolCallId", "")
        input_str = ""
        if tool_call_id and raw_input:
            input_str = (
                json.dumps(raw_input, indent=2)
                if isinstance(raw_input, (dict, list))
                else str(raw_input)
            )
        # For edit tools with diff content blocks, generate unified diff
        found_diff = False
        content_blocks = update.get("content", [])
        if isinstance(content_blocks, list):
            for cb in content_blocks:
                if isinstance(cb, dict) and cb.get("type") == "diff":
                    old = cb.get("oldText") or ""
                    new = cb.get("newText") or ""
                    path = cb.get("path", "")
                    diff_str = make_unified_diff(old, new, path)
                    if diff_str:
                        input_str = diff_str
                        found_diff = True
                    break
        # Fallback for strReplace when no diff content block was found
        if (
            not found_diff
            and isinstance(raw_input, dict)
            and raw_input.get("command") == "strReplace"
        ):
            old = raw_input.get("oldStr") or ""
            new = raw_input.get("newStr") or ""
            path = raw_input.get("path") or ""
            if old or new:
                diff_str = make_unified_diff(old, new, path)
                if diff_str:
                    input_str = diff_str
        # Redact sensitive content before caching/displaying
        if input_str:
            input_str, _ = redact_exfiltration_urls(input_str)
            input_str, _ = redact_credentials(input_str)
        if tool_call_id and input_str:
            tool_call_inputs[tool_call_id] = input_str
        # Redact LLM-influenced fields before dashboard display
        if purpose:
            purpose, _ = redact_exfiltration_urls(purpose)
            purpose, _ = redact_credentials(purpose)
        if title:
            title, _ = redact_exfiltration_urls(title)
            title, _ = redact_credentials(title)
        if kind:
            kind, _ = redact_exfiltration_urls(kind)
            kind, _ = redact_credentials(kind)
        tool_calls_sink.append((kind, title))
        return AcpEvent(
            kind=EVENT_TOOL_CALL,
            title=title,
            tool_kind=kind,
            tool_purpose=purpose,
            tool_input=input_str,
            tool_call_id=tool_call_id,
        )
    return None


def extract_tool_update_events(
    msg: JsonRpcMessage,
    tool_call_inputs: dict[str, str],
) -> list[AcpEvent]:
    """Handle a ``tool_call_update`` frame.

    Agents stream a tool call as an initial ``tool_call`` (often
    ``rawInput: {}`` + ``status: pending``) followed by ``tool_call_update``
    frames that fill in the resolved ``rawInput`` and, on completion, the
    result ``content``/``rawOutput``. Without handling these, both the tool
    input and output render empty. Yields an ``EVENT_TOOL_CALL_UPDATE``
    carrying the resolved input/title (refines the existing card in place,
    no re-fire of hooks) and, when ``status == completed``, an
    ``EVENT_TOOL_RESULT`` so the output lands on the same card.
    """
    params = msg.params or {}
    update = params.get("update", {})
    if update.get("sessionUpdate") != UPDATE_TOOL_CALL_UPDATE:
        return []
    tool_call_id = update.get("toolCallId", "")
    if not tool_call_id:
        return []
    events: list[AcpEvent] = []

    # 1) Resolved input + refined title (the initial frame was empty).
    raw_input = update.get("rawInput")
    input_str = ""
    if isinstance(raw_input, (dict, list)) and raw_input:
        input_str = json.dumps(raw_input, indent=2)
    elif isinstance(raw_input, str):
        input_str = raw_input
    title = update.get("title") or ""
    if input_str:
        input_str, _ = redact_exfiltration_urls(input_str)
        input_str, _ = redact_credentials(input_str)
        # cache so a following permission request can resolve full input
        tool_call_inputs[tool_call_id] = input_str
    if title:
        title, _ = redact_exfiltration_urls(title)
        title, _ = redact_credentials(title)
    if input_str or title:
        events.append(
            AcpEvent(
                kind=EVENT_TOOL_CALL_UPDATE,
                title=title,
                tool_input=input_str,
                tool_call_id=tool_call_id,
            )
        )

    # 2) Terminal status → result output. `failed` carries the error text in
    #    the same content/rawOutput shape as `completed`, and it's exactly
    #    what the user needs to see — surface both. Prefer the human-readable
    #    content blocks; fall back to rawOutput.
    if update.get("status") in ("completed", "failed"):
        output = coerce_tool_content(update.get("content"))
        if not output:
            raw_output = update.get("rawOutput")
            if isinstance(raw_output, (dict, list)):
                output = json.dumps(raw_output, indent=2)
            elif raw_output is not None:
                output = str(raw_output)
        output = (output or "")[:8000]
        output, _ = redact_exfiltration_urls(output)
        output, _ = redact_credentials(output)
        events.append(
            AcpEvent(
                kind=EVENT_TOOL_RESULT,
                tool_call_id=tool_call_id,
                tool_output=output,
            )
        )
    return events


def build_permission_event(
    msg: JsonRpcMessage,
    dialect,
    tool_call_inputs: dict[str, str],
    offered_options: dict[str, list[dict[str, str]]],
) -> AcpEvent:
    """Decode a ``session/request_permission`` frame into an ``EVENT_PERMISSION_REQUEST``.

    Records the options the agent offered under the request id in ``offered_options``
    (so a later approve can echo a real optionId) and resolves the full tool input from
    the ``tool_call_inputs`` cache populated by the preceding ``tool_call`` frame."""
    request_id = msg.id if msg.id is not None else ""
    params = msg.params or {}
    tool_call = params.get("toolCall", {})
    title = tool_call.get("title", "unknown")
    options = dialect.parse_permission_options(params.get("options", []))
    if not options:
        options = dialect.default_permission_options()
    # Remember what the agent offered so approve_tool can echo a real id.
    if request_id != "":
        offered_options[str(request_id)] = options

    # Resolve full tool input — the preceding ToolCall session/notification
    # carries the complete params that we cache by toolCallId.  The
    # request_permission message only has a truncated human-readable title.
    tool_input = ""
    tool_call_id = tool_call.get("toolCallId", "")

    # 1. Look up cached input from the ToolCall notification
    if tool_call_id and tool_call_id in tool_call_inputs:
        tool_input = tool_call_inputs.pop(tool_call_id)

    # 2. Fallback: check if toolCall itself carries input/params
    if not tool_input:
        raw_input = tool_call.get("input") or tool_call.get("params")
        if raw_input:
            tool_input = (
                json.dumps(raw_input, indent=2)
                if isinstance(raw_input, (dict, list))
                else str(raw_input)
            )

    logger.info("Permission requested for tool: %s (req=%s)", title, request_id)
    logger.debug("Permission toolCall payload: %s", tool_call)
    return AcpEvent(
        kind=EVENT_PERMISSION_REQUEST,
        request_id=request_id,
        title=title,
        options=options,
        tool_input=tool_input,
        tool_call_id=tool_call_id,
    )


# ── prompt encoding + terminal-frame helpers (shared, pure) ──────────────────

# The agent security-filter interrupt marker: when the backend's built-in filter
# cancels a turn's tools it streams this text and NEVER sends a `result`, so both
# turn loops synthesize an EVENT_COMPLETE on seeing it (else the caller waits out
# the full prompt timeout).
TOOL_INTERRUPTED_MARKER = "Tool uses were interrupted, waiting for the next user prompt"

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg"}
IMAGE_MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".svg": "image/svg+xml",
}
_IMAGE_PATH_RE = re.compile(r"(/[\w./@~\s()\-]+\.(?:png|jpg|jpeg|gif|webp|bmp))", re.IGNORECASE)


def is_tool_interrupted_marker(chunk: str) -> bool:
    """Exact match against the agent security-filter interrupt marker."""
    return chunk.strip() == TOOL_INTERRUPTED_MARKER


def extract_context_pct(msg: JsonRpcMessage) -> float | None:
    """Read ``contextUsagePercentage`` off a metadata frame, or None if absent."""
    params = msg.params or {}
    pct = params.get("contextUsagePercentage") if isinstance(params, dict) else None
    return float(pct) if pct is not None else None


def encode_prompt_content(message: str) -> list[dict]:
    """Build an ACP prompt content list from a message string, inlining any local
    image paths as base64 image blocks (unreadable paths are left as text)."""
    content: list[dict] = []
    remaining = message
    for match in _IMAGE_PATH_RE.finditer(message):
        p = Path(match.group(1).strip())
        if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS:
            try:
                data = base64.b64encode(p.read_bytes()).decode()
                media = IMAGE_MEDIA_TYPES.get(p.suffix.lower(), "image/png")
                content.append({"type": "image", "data": data, "mimeType": media})
                remaining = remaining.replace(match.group(1), f"[image: {p.name}]")
            except Exception:
                pass  # skip unreadable files
    content.insert(0, {"type": "text", "text": remaining})
    return content


def read_new_tool_results(jsonl_path: Path, pos: int) -> tuple[list[AcpEvent], int]:
    """Read new ``ToolResults`` entries from a per-session JSONL file starting at byte
    ``pos``; return ``(events, new_pos)``. Some ACP agents (opting in via
    ``session_files_dir``) persist structured tool results to this file instead of the
    protocol stream. Pure: the caller owns the file path + read position. A partial
    (newline-less) trailing line is left for the next call (pos not advanced past it)."""
    results: list[AcpEvent] = []
    if not jsonl_path.exists():
        return results, pos
    try:
        with open(jsonl_path, "r") as f:
            f.seek(pos)
            while True:
                line = f.readline()
                if not line:
                    break
                if not line.endswith("\n"):
                    break  # partial line — retry next call
                pos = f.tell()
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if entry.get("kind") == "ToolResults":
                    for c in entry.get("data", {}).get("content", []):
                        if c.get("kind") == "toolResult":
                            tr = c.get("data")
                            if not isinstance(tr, dict):
                                continue
                            tool_use_id = tr.get("toolUseId", "")
                            output_parts: list[str] = []
                            for rc in tr.get("content", []):
                                if isinstance(rc, dict):
                                    if rc.get("kind") == "json":
                                        d = rc.get("data", {})
                                        if isinstance(d, dict) and "stdout" in d:
                                            out = d.get("stdout", "")
                                            if out:
                                                output_parts.append(out[:4000])
                                        else:
                                            output_parts.append(json.dumps(d, indent=2)[:4000])
                                    elif rc.get("kind") == "text":
                                        output_parts.append(str(rc.get("data", ""))[:4000])
                            if output_parts:
                                results.append(
                                    AcpEvent(
                                        kind=EVENT_TOOL_RESULT,
                                        tool_call_id=tool_use_id,
                                        tool_output="\n".join(output_parts)[:8000],
                                    )
                                )
    except Exception:
        logger.debug("Failed to read JSONL for tool results", exc_info=True)
    if results:
        logger.debug("JSONL: read %d tool result(s) from %s", len(results), jsonl_path.name)
    return results, pos


def format_command_result(result: dict) -> str:
    """Extract displayable text from a ``commands/execute`` response — a message
    plus, if present, a JSON block of the structured ``data`` (minus agent/model
    metadata, which the caller surfaces separately)."""
    data = result.get("data")
    message = result.get("message", "")
    if isinstance(data, dict) and data:
        display = {k: v for k, v in data.items() if k not in ("agent", "model")}
        if display:
            return (
                f"{message}\n```json\n{json.dumps(display, indent=2)}\n```"
                if message
                else f"```json\n{json.dumps(display, indent=2)}\n```"
            )
    return message or ""

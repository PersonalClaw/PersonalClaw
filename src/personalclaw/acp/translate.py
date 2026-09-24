"""ACP frame → AcpEvent decoders — the pure translation surface (P9 cutover step 2).

The one-session :class:`~personalclaw.acp.client.AcpClient` and the multi-session
:class:`~personalclaw.acp.session.AcpSession` both turn raw ``session/update`` frames
into :class:`~personalclaw.acp.types.AcpEvent`s. This module is that decoding logic in
ONE place so the two turn loops can never drift — a ``tool_call`` frame becomes the same
event whether it arrived on the inline client reader or a router-demuxed session queue.

Every function here is pure: no ``self``, no I/O, no process. The small per-turn caches
the decoders read/write (``tool_call_inputs`` and ``tool_call_seen`` keyed by
``toolCallId``, ``offered_options`` keyed by request id) are threaded in as explicit dict
params — the caller owns them.
Dependencies are the leaf ``types`` module + the ``security`` redactors + stdlib, so both
the client and the session import from here with no import cycle.
"""

from __future__ import annotations

import base64
import difflib
import json
import logging
import re
from dataclasses import dataclass, replace
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


def _declared_file_change(path: object, old: object, new: object) -> dict[str, str] | None:
    """A file-change chip built ONLY from what an ACP ``diff`` block declared.

    ACP's diff content block carries the file's whole ``oldText``/``newText``, which is
    exactly the chip's contract (``chat_runner._flush_file_changes`` dedups per path
    keeping the earliest ``before`` and the latest ``after``). No path resolution and no
    disk read happens here: the native chip needs both because it reconstructs ``after``
    from a tool's arguments, and there is nothing to reconstruct when the agent states
    both sides.

    Returns ``None`` without a path — a chip keyed on ``""`` would collapse every
    unnamed edit in the turn into one row.
    """
    rel = str(path or "")
    if not rel:
        return None
    return {"path": rel, "before": str(old or ""), "after": str(new or "")}


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


@dataclass(frozen=True)
class SeenToolCall:
    """What the ``tool_call`` frame that OPENED a call declared about it.

    Correlation state, not a wire type. The ``session/request_permission`` frame that
    follows shares only ``toolCallId`` with that opening frame, and on some adapters the
    opening frame is the ONLY one that ever names the tool — so what it declared has to
    outlive it for the duration of the turn.

    Both fields hold the decoder's post-redaction values, and both may be empty: an
    absence must stay representable. ``kind`` may be the decoder's own ``unknown``
    placeholder, which means "declared nothing" and must never resolve permissive
    (`G10`); an empty ``title`` means the frame did not name the tool, and is what stops
    a filled-in title from being invented (`G18`).
    """

    kind: str = ""
    title: str = ""


def extract_tool_event(
    msg: JsonRpcMessage,
    tool_call_inputs: dict[str, str],
    tool_call_seen: dict[str, SeenToolCall],
    tool_calls_sink: list[tuple[str, str]],
) -> AcpEvent | None:
    """Decode a ``tool_call`` frame into an ``EVENT_TOOL_CALL`` (or None).

    Caches the resolved, redacted tool input under ``toolCallId`` in
    ``tool_call_inputs`` so a following permission request can echo the full input,
    caches the frame's declared ``kind`` AND ``title`` under the same key in
    ``tool_call_seen`` so that request can name both what kind of tool it is gating
    (`G10` — claude's ``session/request_permission`` payload carries no ``kind``) and
    WHICH tool (`G18` — codex's carries no ``title``), and appends ``(kind, title)``
    to ``tool_calls_sink`` (the turn's prompt stats).
    """
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
        file_change: dict[str, str] | None = None
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
                    # §2.5 gap 7: the same declaration also feeds the file-change chip.
                    # Recorded even when `make_unified_diff` returns "" (identical
                    # texts): the chip layer owns the no-op decision, and duplicating
                    # that judgement here is how the two surfaces drift apart.
                    file_change = _declared_file_change(path, old, new)
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
                # Deliberately NO `file_change` here. `oldStr`/`newStr` are the
                # FRAGMENTS being replaced, not the file's before/after contents, and
                # the chip's contract is whole-file snapshots (`_flush_file_changes`
                # dedups per path keeping the earliest before and latest after). Filing
                # a fragment as `before` would render a chip asserting the file
                # contained only that fragment. The unified diff above still shows the
                # user exactly what changed; only the chip is withheld.
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
        # Correlate what this frame declared onto the permission frame that follows
        # (keyed on toolCallId, the only id both frames share). ``unknown`` is the
        # decoder's own placeholder for "this frame declared none" — cache it too rather
        # than dropping it, so the permission path can tell an unmeasured kind apart from
        # one that measured as unclassifiable (`G10` requirement: absence stays
        # representable and must never resolve permissive). The title rides the same
        # correlation because the two gaps are one seam: a permission frame that cannot
        # say WHAT it is gating (`G10`) usually cannot say WHICH tool either (`G18`).
        if tool_call_id:
            tool_call_seen[tool_call_id] = SeenToolCall(kind=kind, title=title)
        tool_calls_sink.append((kind, title))
        return AcpEvent(
            kind=EVENT_TOOL_CALL,
            title=title,
            tool_kind=kind,
            tool_purpose=purpose,
            tool_input=input_str,
            # §2.5 gap 7: hand the OBJECT over too, not only the flattened string.
            # Carried unredacted on purpose, exactly like the native runtime's dict:
            # ``chat_runner._redact_tool_input_obj`` is the single redaction+cap point
            # for the structured shape, and redacting here as well would mask values
            # twice while leaving the two representations free to disagree.
            tool_input_obj=raw_input if isinstance(raw_input, dict) else None,
            file_change=file_change,
            tool_call_id=tool_call_id,
        )
    return None


# ── the tool-result FAILURE bit, derived runtime-agnostically ────────────────
#
# `AAP-6` §2.3 gap 5. The loop breaker, the procedural-outcome accumulator and the
# tool card all read ONE bit: did this tool call fail. Reading it off the ACP
# `status` field alone made that bit a per-CLI lottery, measured:
#
#   codex  → status="failed",    rawOutput {"formatted_output": "boom\n", "exit_code": 3}
#   kiro   → status="completed", rawOutput {"items":[{"Json":{"exit_status":
#                                "exit status: 3", "stdout": "", "stderr": "boom\n"}}]}
#
# Both ran `bash -c 'echo boom >&2; exit 3'`. kiro calls a non-zero-exit command a
# COMPLETED tool call — it completed the act of running it — so a status-only reading
# left every kiro failure signed as a success and the whole warn/block/circuit path
# inert on that runtime (measured live: ten consecutive failing calls, zero notices).
# Neither CLI is wrong; they answer different questions. So the host stops asking the
# CLI to agree and derives the bit itself: trust a DECLARED failure, and otherwise look
# for a declared non-zero process exit in the result payload.
#
# Deliberately KEY-BASED, never a scan of the output prose. `grep -c error`, a test
# runner printing "1 failed", or a file whose contents say "exit 1" are all successful
# tool calls, and a text heuristic would sign them failed — a breaker that aborts a
# healthy turn is worse than one that misses a failure. For the same reason the scan
# never touches `rawInput`: kiro's own input for the measured call is
# `{"command": "bash -c 'echo boom >&2; exit 3'"}`, so a whole-frame scan would call
# EVERY invocation of that command a failure regardless of how it exited.
_EXIT_STATUS_KEYS = ("exit_code", "exitCode", "exit_status", "exitStatus")
# The MCP tool-result failure flag — for a tool the CLI serves over MCP rather than
# running itself, which is how an app-provided tool reaches an ACP session.
_ERROR_FLAG_KEYS = ("isError", "is_error")
# Both measured runtimes bury the status two-to-three levels down (kiro: items → Json).
# Bounded so a pathological or cyclic-looking payload can't turn one frame into a walk.
_FAILURE_SCAN_MAX_DEPTH = 6


def _declares_nonzero_exit(value: object) -> bool:
    """True when ``value`` is a DECLARED non-zero process exit status.

    Accepts both measured spellings: codex's ``3`` (int) and kiro's
    ``"exit status: 3"`` (string). A bool is not an exit status — ``True`` is ``1`` in
    Python and would otherwise read as "exited 1" — and a string carrying no digits
    (``"unknown"``) is an absent status, not a failing one.
    """
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return value != 0
    if isinstance(value, str):
        m = re.search(r"-?\d+", value)
        return m is not None and int(m.group()) != 0
    return False


def _declares_failure(node: object, depth: int = 0) -> bool:
    """Walk a tool-result payload for a declared failure key. Keys only, never prose."""
    if depth > _FAILURE_SCAN_MAX_DEPTH:
        return False
    if isinstance(node, dict):
        if any(node.get(k) is True for k in _ERROR_FLAG_KEYS):
            return True
        if any(_declares_nonzero_exit(node[k]) for k in _EXIT_STATUS_KEYS if k in node):
            return True
        return any(_declares_failure(v, depth + 1) for v in node.values())
    if isinstance(node, list):
        return any(_declares_failure(v, depth + 1) for v in node)
    return False


def terminal_result_failed(update: dict) -> bool:
    """Did this terminal ``tool_call_update`` frame report a FAILED tool call?

    Runtime-agnostic by construction — it asks the payload, not the runtime:

    1. ``status == "failed"``: the CLI declared it. Always believed, so a runtime that
       does sign its failures (codex, claude-code-acp) is decided by its own word and
       the derivation below can only ever ADD a failure the CLI declined to name.
    2. Otherwise, a declared non-zero exit status or ``isError`` anywhere in the
       structured result (``rawOutput``/``content``) — which is what a runtime that
       reports a failing command as a ``completed`` tool call (kiro) leaves behind.

    A frame with neither is a success, so the ``ok`` key stays absent and no existing
    reader changes behaviour on a passing call.
    """
    if update.get("status") == "failed":
        return True
    return _declares_failure(update.get("rawOutput")) or _declares_failure(update.get("content"))


def extract_tool_update_events(
    msg: JsonRpcMessage,
    tool_call_inputs: dict[str, str],
    tool_call_seen: dict[str, SeenToolCall],
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
    # §2.5 gap 7. A `diff` content block on an UPDATE frame is the common case — the
    # opening `tool_call` usually has no content at all — so the chip has to be read
    # here as well as there. Only `file_change` is taken from it: rewriting `input_str`
    # into a unified diff on this path would change what existing ACP cards print,
    # which is a rendering decision this atom deliberately does not make.
    upd_file_change: dict[str, str] | None = None
    _upd_blocks = update.get("content")
    if isinstance(_upd_blocks, list):
        for _cb in _upd_blocks:
            if isinstance(_cb, dict) and _cb.get("type") == "diff":
                upd_file_change = _declared_file_change(
                    _cb.get("path", ""), _cb.get("oldText") or "", _cb.get("newText") or ""
                )
                break
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
    # An update MAY refine either declared field (ACP's ToolCallUpdate carries an
    # optional `kind` and `title`). Only a POSITIVE declaration overwrites the correlated
    # value, and each field is refined independently — an update that names the tool but
    # omits the kind must not erase the kind the opening `tool_call` frame declared, and
    # vice versa. `replace` on the frozen record is what keeps the untouched field.
    _upd_kind = str(update.get("kind") or "")
    if _upd_kind:
        _upd_kind, _ = redact_exfiltration_urls(_upd_kind)
        _upd_kind, _ = redact_credentials(_upd_kind)
    if tool_call_id and (_upd_kind or title):
        _seen = tool_call_seen.get(tool_call_id, SeenToolCall())
        if _upd_kind:
            _seen = replace(_seen, kind=_upd_kind)
        if title:
            _seen = replace(_seen, title=title)
        tool_call_seen[tool_call_id] = _seen
    # A frame that declares ONLY a diff (no resolved rawInput, no refined title) still
    # has to produce an event, or the chip it declared is dropped on the floor.
    if input_str or title or upd_file_change:
        events.append(
            AcpEvent(
                kind=EVENT_TOOL_CALL_UPDATE,
                title=title,
                tool_input=input_str,
                # The update frame is where an adapter that opened with ``rawInput: {}``
                # finally names its arguments, so this is the site that decides whether
                # the card can render fields at all (§2.5 gap 7).
                tool_input_obj=raw_input if isinstance(raw_input, dict) else None,
                file_change=upd_file_change,
                tool_call_id=tool_call_id,
            )
        )

    # 2) Terminal status → result output. `failed` carries the error text in
    #    the same content/rawOutput shape as `completed`, and it's exactly
    #    what the user needs to see — surface both. Prefer the human-readable
    #    content blocks; fall back to rawOutput.
    if update.get("status") in ("completed", "failed"):
        # The terminal frame decides the failure bit below, and the two CLIs disagree
        # about which key carries the failure. Log the payload once, at the one place
        # that reads it, so the next runtime's shape can be MEASURED rather than
        # guessed (same reason `build_permission_event` logs its `toolCall` payload).
        logger.debug("Terminal tool_call_update payload: %s", update)
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
        # Carry the FAILURE bit (§2.3 gap 5). `completed` and `failed` used to
        # produce a byte-identical event, so every consumer downstream — the tool
        # card's colour coding and, decisively, the loop breaker — could not tell a
        # failing ACP tool call from a succeeding one. `G6` measured the consequence:
        # six consecutive failures in one ACP turn produced no warn, no block and no
        # circuit trip, because the host was never told anything had failed. The key
        # is `ok`, matching the native runtime's tool_meta contract: present and
        # False ONLY on failure, absent on success, so no existing reader changes
        # behaviour on a passing call.
        #
        # DERIVED, not read off `status` (`G151`): reading the CLI's status field alone
        # made the bit a per-runtime lottery and left the whole breaker path inert on
        # every runtime that calls a failing command a `completed` tool call. See
        # `terminal_result_failed`.
        _meta = {"ok": False} if terminal_result_failed(update) else {}
        events.append(
            AcpEvent(
                kind=EVENT_TOOL_RESULT,
                tool_call_id=tool_call_id,
                tool_output=output,
                tool_meta=_meta,
            )
        )
    return events


def build_permission_event(
    msg: JsonRpcMessage,
    dialect,
    tool_call_inputs: dict[str, str],
    tool_call_seen: dict[str, SeenToolCall],
    offered_options: dict[str, list[dict[str, str]]],
) -> AcpEvent:
    """Decode a ``session/request_permission`` frame into an ``EVENT_PERMISSION_REQUEST``.

    Records the options the agent offered under the request id in ``offered_options``
    (so a later approve can echo a real optionId) and resolves the full tool input, the
    declared kind and the tool's title from the ``tool_call_inputs``/``tool_call_seen``
    caches populated by the preceding ``tool_call`` frame."""
    request_id = msg.id if msg.id is not None else ""
    params = msg.params or {}
    tool_call = params.get("toolCall", {})
    # The frame's OWN title, when it sends one. A MISSING key and an EMPTY string are
    # the same fact — "this adapter did not name the tool" — so both must fall through to
    # the correlation below. `.get(..., "unknown")` alone treated only the missing key as
    # absent and let an empty title through, which still renders a nameless card.
    title = str(tool_call.get("title") or "")
    # The declared kind (read/edit/execute/delete/…). Carried so the approval card,
    # the SEL row and the not-gateable residue check can NAME the tool even when the
    # adapter sends no title (codex sends `kind` but no `title`, which is why the card
    # said "unknown" — G18). Deliberately NOT fed to the task-mode gate: a CLI-declared
    # "read" must not be able to turn that gate's deny-by-default into an allow (§2.2
    # fails closed).
    #
    # `G10`: the frame's OWN kind is only present on some adapters — codex declares it,
    # claude-code-acp sends `{toolCallId, title}` and nothing else, which is why every
    # claude permission request arrived with `tool_kind: ""` and the kind→risk mapping
    # in `task_modes` had nothing to read on the approval path. The kind IS on the wire,
    # one frame earlier, on the `tool_call` that opened this call. Correlate on
    # `toolCallId` — the same key the input cache above already uses, and the only
    # identifier the two frames share. The frame's own declaration always wins; the
    # correlated value only fills an absence.
    kind = str(tool_call.get("kind") or "")
    if kind:
        kind, _ = redact_exfiltration_urls(kind)
        kind, _ = redact_credentials(kind)
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

    # Fill an absent kind AND an absent title from the correlated `tool_call` frame
    # (see above). `G18`: codex's permission payload is `{toolCallId, kind, status}` — the
    # human title lives one frame earlier, which is why every codex approval card and
    # every SEL decision row read `tool: "unknown"` while the tool's real name sat in the
    # cache. The frame's own declaration always wins; the correlated value only fills an
    # absence, and `unknown` survives only when NEITHER frame named the tool — so the card
    # never shows a name that was invented rather than declared.
    seen = tool_call_seen.get(tool_call_id) if tool_call_id else None
    if not kind and seen is not None:
        kind = seen.kind
    if not title and seen is not None:
        title = seen.title
    if not title:
        title = "unknown"

    # 1. Look up cached input from the ToolCall notification. READ, never pop: a call
    #    can reach this gate more than once (a re-requested permission after an
    #    interrupt), and a popped cache makes the second request look like a tool whose
    #    command the host cannot see — which is exactly the state that used to mint a
    #    risk verdict out of nothing (`G10`). The cache is per-turn and cleared at turn
    #    start (`AcpSession._stream_turn`), so keeping the entry costs one turn.
    if tool_call_id and tool_call_id in tool_call_inputs:
        tool_input = tool_call_inputs[tool_call_id]

    # 2. Fallback: check if toolCall itself carries the input inline. ACP types the
    #    permission frame's `toolCall` as a ToolCallUpdate, whose input field is named
    #    `rawInput` — the SAME key `extract_tool_event` reads above. Reading only
    #    `input`/`params` here meant a frame that carried the command inline still
    #    reached the gate with an empty input (`G10`, the ask-mode half: a read-only
    #    `ls` is indistinguishable from an unreadable command, and the title-hint
    #    fallback denies it).
    if not tool_input:
        raw_input = tool_call.get("rawInput") or tool_call.get("input") or tool_call.get("params")
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
        tool_kind=kind,
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

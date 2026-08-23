"""Neutral agent-event type — the provider-agnostic event the chat runner reads.

Defines the backend-neutral :class:`AgentEvent` that every backend (ACP via
``acp/adapter.py``, the native loop, the HTTP model providers) emits and the
chat runner consumes. ``LLMEvent`` aliases it.

The field set mirrors ``acp.types.AcpEvent`` (identical field names + defaults).
The event-kind constants live here as the canonical home; ``acp.types`` imports
them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ── Event-kind constants (canonical home) ──
EVENT_TEXT_CHUNK = "text_chunk"
EVENT_THINKING_CHUNK = "thinking_chunk"
EVENT_TOOL_CALL = "tool_call"
# Resolved input (and refined title/purpose) for a tool whose initial TOOL_CALL
# frame was empty — agents stream the real args in a later update. Refines the
# existing card in place; does NOT re-fire hooks/SEL/mirror like a fresh call.
EVENT_TOOL_CALL_UPDATE = "tool_call_update"
EVENT_TOOL_RESULT = "tool_result"
EVENT_PERMISSION_REQUEST = "permission_request"
EVENT_COMPLETE = "complete"
EVENT_COMPACTION_STATUS = "compaction_status"
EVENT_CLEAR_STATUS = "clear_status"
EVENT_AGENT_SWITCHED = "agent_switched"


@dataclass
class AgentEvent:
    """A neutral event from any agent/model backend's turn stream.

    Field names + defaults match ``acp.types.AcpEvent`` exactly so the chat
    runner consumes either without change. ``tool_input``/``tool_output`` are
    typed ``Any`` (the native loop may pass structured values; ACP passes str).
    """

    kind: str  # one of the EVENT_* constants above
    text: str = ""
    tool_call_id: str = ""
    title: str = ""
    tool_kind: str = ""
    tool_purpose: str = ""
    # Declared risk of the tool behind a TOOL_CALL / PERMISSION_REQUEST — the
    # tool's static ToolDefinition.risk_level ('safe'|'caution'|'destructive'),
    # or '' when the backend declared none (external ACP/MCP tools). The approval
    # gate resolves the per-invocation EFFECTIVE risk from this (a read-only bash
    # call downgrades to safe); it's also surfaced as a user-facing indicator.
    risk_level: str = ""
    #: Context-window usage the provider actually measured, or ``None`` when it
    #: measured none. A defaulted 0.0 made "unsupplied" and "a genuinely empty
    #: context" the same value, so every consumer printed a fabricated 0%.
    context_usage_pct: float | None = None
    stop_reason: str = ""
    request_id: str | int = ""
    options: Any = field(default_factory=list)
    tool_input: Any = ""
    #: The structured form of :attr:`tool_input`, for backends that carry BOTH a
    #: display string and the object behind it (ACP-AGENT-PARITY §2.5 gap 7). The
    #: native runtime needs nothing here — it already puts its dict straight into
    #: ``tool_input`` — but an ACP frame's ``tool_input`` is a redacted, pretty-printed
    #: string the card renders verbatim, so flattening the object into that field
    #: would have changed every ACP card's preview to buy the schema-driven fields.
    #: ``None`` means "no object was supplied", never "the object was empty".
    tool_input_obj: dict[str, Any] | None = None
    #: A file edit the BACKEND declared, as ``{"path", "before", "after"}``
    #: (ACP-AGENT-PARITY §2.5 gap 7). The native runtime leaves this empty and keeps
    #: driving chips off its own write-tool name set, because it can reconstruct
    #: ``after`` from the call arguments; a backend that states both sides outright
    #: fills this instead of asking the host to infer anything.
    file_change: dict[str, str] | None = None
    tool_output: Any = ""
    # usage / cost — read by chat_runner's EVENT_COMPLETE token block
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    cost_usd: float = 0.0
    num_turns: int = 0
    duration_ms: int = 0
    # Turn telemetry on the terminal EVENT_COMPLETE — provider-neutral aggregate
    # tallies the chat runner renders as the collapsed "Turn complete" stats line
    # (events seen this prompt, tool calls made). Both the native loop and the ACP
    # client populate these; live-only, never persisted.
    event_count: int = 0
    tool_call_count: int = 0
    # Typed tool I/O metadata for the rendering framework (tool-io-rendering) +
    # projection (tool-output-projection): on a TOOL_RESULT, carries
    # content_type / raw_ref / truncated / original_length; on a TOOL_CALL, may
    # carry the input schema + render hint. Empty for backends that don't supply
    # it (ACP) → the UI renders exactly as before. Mirror in acp.types.AcpEvent.
    tool_meta: dict[str, Any] = field(default_factory=dict)

"""ACP (Agent Client Protocol) types — vendor-neutral JSON-RPC primitives.

Holds the protocol-level method names, event kinds, permission outcomes,
and stop reasons used by ACP-compliant agents. The module contains no
agent-specific defaults — no binary lookups, env probes, or hardcoded paths.

Method names fall into two strata:

* **Core ACP** — ``initialize``, ``session/new``, ``session/prompt``,
  ``session/cancel``, ``session/load``, ``session/set_model``,
  ``session/set_mode``, ``session/request_permission``,
  ``session/update``. These are what every ACP-compliant agent speaks.

* **Vendor extensions** (``_vendor.dev/*``) — slash-command execution,
  metadata notifications, compaction/clear status, agent-switch
  notifications. These are properly-namespaced JSON-RPC extensions
  that ACP-compliant agents may opt in to. The client treats them as
  optional: extension notifications from agents that don't speak them
  simply never arrive; requests sent to such agents fail gracefully
  (timeout or JSON-RPC error). Keeping them here lets the same client
  code drive both vendor-neutral agents and vendor-extended ones.
"""

from dataclasses import dataclass, field
from typing import Any

# ── ACP Event Kinds ──
# The canonical home for these constants is ``llm/events.py`` (the neutral event
# layer). They are duplicated here as plain string literals — NOT imported —
# because ``acp`` is a lower layer than ``llm`` and importing upward creates a
# circular import (llm/__init__ eagerly loads acp_agent → acp.client → acp.types).
# ``llm/events.py`` and this module must agree on the values; a unit test pins
# the equality (test_event_constants_parity).
EVENT_TEXT_CHUNK = "text_chunk"
EVENT_THINKING_CHUNK = "thinking_chunk"
EVENT_TOOL_CALL = "tool_call"
EVENT_TOOL_CALL_UPDATE = "tool_call_update"
EVENT_TOOL_RESULT = "tool_result"
EVENT_PERMISSION_REQUEST = "permission_request"
EVENT_COMPLETE = "complete"
EVENT_COMPACTION_STATUS = "compaction_status"
EVENT_CLEAR_STATUS = "clear_status"
EVENT_AGENT_SWITCHED = "agent_switched"

# ── ACP Protocol Methods ──

METHOD_INITIALIZE = "initialize"
METHOD_SESSION_NEW = "session/new"
METHOD_SET_MODEL = "session/set_model"
METHOD_SET_MODE = "session/set_mode"
METHOD_PROMPT = "session/prompt"
METHOD_CANCEL = "session/cancel"
METHOD_REQUEST_PERMISSION = "session/request_permission"
METHOD_SESSION_UPDATE = "session/update"
METHOD_METADATA = "_vendor.dev/metadata"
METHOD_COMMANDS_EXECUTE = "_vendor.dev/commands/execute"
METHOD_SESSION_LOAD = "session/load"
METHOD_COMPACTION_STATUS = "_vendor.dev/compaction/status"
METHOD_CLEAR_STATUS = "_vendor.dev/clear/status"
METHOD_AGENT_SWITCHED = "_vendor.dev/agent/switched"

# ── ACP Session Update Types ──

UPDATE_AGENT_MESSAGE_CHUNK = "agent_message_chunk"
UPDATE_TOOL_CALL = "tool_call"
# Agents stream a tool call as an initial ``tool_call`` (often empty rawInput +
# status=pending) followed by one or more ``tool_call_update`` frames that fill in
# the resolved rawInput and, on completion, the result content/rawOutput.
UPDATE_TOOL_CALL_UPDATE = "tool_call_update"

# ── ACP Permission Outcomes ──

OUTCOME_SELECTED = "selected"
OUTCOME_CANCELLED = "cancelled"
OPTION_ALLOW_ONCE = "allow_once"
OPTION_ALLOW_ALWAYS = "allow_always"

# ── Stop Reasons ──

STOP_REASON_CANCELLED = "cancelled"
STOP_REASON_END_TURN = "end_turn"

# ── Approval Modes ──

APPROVAL_AUTO = "auto"
APPROVAL_INTERACTIVE = "interactive"


@dataclass
class JsonRpcRequest:
    """Outbound JSON-RPC 2.0 request."""

    method: str
    params: dict[str, Any]
    id: int
    jsonrpc: str = "2.0"

    def to_dict(self) -> dict[str, Any]:
        return {
            "jsonrpc": self.jsonrpc,
            "id": self.id,
            "method": self.method,
            "params": self.params,
        }


@dataclass
class JsonRpcMessage:
    """Inbound JSON-RPC 2.0 message (response or notification)."""

    id: Any = None
    method: str | None = None
    result: Any = None
    error: Any = None
    params: Any = None

    def is_response_for(self, req_id: int) -> bool:
        return self.id == req_id

    def is_method(self, name: str) -> bool:
        return self.method == name


@dataclass
class AcpEvent:
    """Structured event from an ACP agent stream."""

    kind: str  # text_chunk, tool_call, permission_request, complete
    text: str = ""
    tool_call_id: str = ""
    title: str = ""
    tool_kind: str = ""
    tool_purpose: str = ""
    context_usage_pct: float = 0.0
    stop_reason: str = ""
    request_id: str | int = ""
    options: list[dict[str, str]] = field(default_factory=list)
    tool_input: str = ""
    tool_output: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    cost_usd: float = 0.0
    num_turns: int = 0
    duration_ms: int = 0
    # Turn telemetry mirror of AgentEvent (events.py) — kept field-identical so
    # LLMEvent can point at either. Populated on the terminal complete event.
    event_count: int = 0
    tool_call_count: int = 0
    # Mirror of AgentEvent.tool_meta (typed tool I/O metadata). ACP backends leave
    # it empty; kept field-identical so LLMEvent can point at either.
    tool_meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class AcpPromptStats:
    """Stats from the last ACP prompt."""

    event_count: int = 0
    text_chunks: int = 0
    tool_calls: list[tuple[str, str]] = field(default_factory=list)
    context_pct: float = 0.0

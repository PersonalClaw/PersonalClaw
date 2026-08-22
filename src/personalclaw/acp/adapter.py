"""ACP ⇄ neutral-event translation.

The only place that knows both the ACP-internal :class:`AcpEvent` shape and the
neutral :class:`AgentEvent` shape. The ACP layer keeps owning ``AcpEvent``
(``acp/types.py``); everything downstream consumes ``AgentEvent``.
"""

from __future__ import annotations

from personalclaw.acp.types import AcpEvent
from personalclaw.llm.events import AgentEvent


def acp_event_to_agent_event(e: AcpEvent) -> AgentEvent:
    """Map an ACP stream event to the neutral agent event (field-for-field)."""
    return AgentEvent(
        kind=e.kind,
        text=e.text,
        tool_call_id=e.tool_call_id,
        title=e.title,
        tool_kind=e.tool_kind,
        tool_purpose=e.tool_purpose,
        context_usage_pct=e.context_usage_pct,
        stop_reason=e.stop_reason,
        request_id=e.request_id,
        options=e.options,
        tool_input=e.tool_input,
        tool_output=e.tool_output,
        input_tokens=e.input_tokens,
        output_tokens=e.output_tokens,
        cache_creation_tokens=e.cache_creation_tokens,
        cache_read_tokens=e.cache_read_tokens,
        cost_usd=e.cost_usd,
        num_turns=e.num_turns,
        duration_ms=e.duration_ms,
        event_count=e.event_count,
        tool_call_count=e.tool_call_count,
        # The tool-result FAILURE bit (`G6`/`G7`). `acp/translate.py` stamps
        # `tool_meta = {"ok": False}` on a failed tool result, but this mapping used to
        # omit the field — so every downstream consumer got the dataclass default `{}`
        # and the bit died one line after it was authored. Measured consequence: the
        # tool card could not colour a failure, and `chat_runner`'s ACP loop breaker
        # (`_acp_failed = _tool_ok is False`) could never be True, leaving a fully
        # implemented warn/block/circuit path inert. Field-for-field means all fields.
        tool_meta=e.tool_meta,
    )

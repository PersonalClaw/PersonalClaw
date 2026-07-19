"""Tests for AcpAgentProvider._to_llm_event field mapping.

The provider re-wraps the ACP client's AcpEvent into an LLMEvent. This
mapping MUST preserve usage/cost fields (input/output tokens, cache
tokens, cost, turns, duration) so the chat runner's ``token_usage``
broadcast — which drives the topbar token tickers — fires for ACP
agents just as it does for the OpenAI / Anthropic / Ollama providers.
"""

from personalclaw.acp.types import EVENT_COMPLETE, AcpEvent
from personalclaw.llm.acp_agent import AcpAgentProvider


def test_to_llm_event_preserves_usage_fields() -> None:
    """A complete event's token/cost fields survive the AcpEvent → LLMEvent map."""
    src = AcpEvent(
        kind=EVENT_COMPLETE,
        stop_reason="end_turn",
        context_usage_pct=37.5,
        input_tokens=1234,
        output_tokens=567,
        cache_creation_tokens=42,
        cache_read_tokens=99,
        cost_usd=0.0123,
        num_turns=3,
        duration_ms=4500,
    )

    mapped = AcpAgentProvider._to_llm_event(src)

    assert mapped.kind == EVENT_COMPLETE
    assert mapped.input_tokens == 1234
    assert mapped.output_tokens == 567
    assert mapped.cache_creation_tokens == 42
    assert mapped.cache_read_tokens == 99
    assert mapped.cost_usd == 0.0123
    assert mapped.num_turns == 3
    assert mapped.duration_ms == 4500
    assert mapped.context_usage_pct == 37.5
    assert mapped.stop_reason == "end_turn"


def test_to_llm_event_preserves_text_and_tool_fields() -> None:
    """Non-usage fields (text, tool call) continue to map correctly."""
    src = AcpEvent(
        kind="tool_call",
        text="hi",
        tool_call_id="call-1",
        title="read_file",
        tool_kind="read",
        tool_input='{"path": "x"}',
        tool_output="contents",
    )

    mapped = AcpAgentProvider._to_llm_event(src)

    assert mapped.kind == "tool_call"
    assert mapped.text == "hi"
    assert mapped.tool_call_id == "call-1"
    assert mapped.title == "read_file"
    assert mapped.tool_kind == "read"
    assert mapped.tool_input == '{"path": "x"}'
    assert mapped.tool_output == "contents"
    # Usage fields default to zero when the source event carries none.
    assert mapped.input_tokens == 0
    assert mapped.output_tokens == 0

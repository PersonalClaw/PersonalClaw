"""E2-P2: neutral AgentEvent + ACP→neutral adapter + decoupling.

Asserts:
- AgentEvent constructs with the full field union the chat_runner/providers touch.
- LLMEvent (llm/base) is now AgentEvent, NOT AcpEvent (G5 decoupled).
- acp_event_to_agent_event maps every AcpEvent field 1:1.
- The EVENT_* constants in acp.types and llm.events agree (duplicated, not shared,
  to avoid a circular import — this test pins the parity).
"""

from __future__ import annotations

from dataclasses import fields

from personalclaw.acp.adapter import acp_event_to_agent_event
from personalclaw.acp.types import AcpEvent
from personalclaw.llm.base import LLMEvent
from personalclaw.llm.events import EVENT_TOOL_CALL, AgentEvent


def test_llm_event_is_agent_event_not_acp():
    assert LLMEvent is AgentEvent
    assert LLMEvent is not AcpEvent


def test_agent_event_full_field_union():
    ev = AgentEvent(
        kind=EVENT_TOOL_CALL,
        text="t",
        tool_call_id="c1",
        title="Tool",
        tool_kind="bash",
        tool_purpose="p",
        context_usage_pct=0.5,
        stop_reason="end_turn",
        request_id=7,
        options=[{"a": "b"}],
        tool_input={"x": 1},
        tool_output="out",
        input_tokens=10,
        output_tokens=20,
        cache_creation_tokens=1,
        cache_read_tokens=2,
        cost_usd=0.01,
        num_turns=3,
        duration_ms=42,
    )
    assert ev.kind == EVENT_TOOL_CALL
    assert ev.input_tokens == 10 and ev.cost_usd == 0.01
    assert ev.tool_input == {"x": 1}  # native loop may pass structured input


def test_agent_event_field_names_superset_of_acp():
    acp_field_names = {f.name for f in fields(AcpEvent)}
    agent_field_names = {f.name for f in fields(AgentEvent)}
    # Every AcpEvent field exists on AgentEvent (so the adapter maps 1:1 and the
    # chat_runner reads either without change).
    missing = acp_field_names - agent_field_names
    assert not missing, f"AgentEvent missing AcpEvent fields: {missing}"


def test_adapter_maps_every_field():
    acp = AcpEvent(
        kind="tool_call",
        text="hi",
        tool_call_id="tc",
        title="T",
        tool_kind="k",
        tool_purpose="why",
        context_usage_pct=0.25,
        stop_reason="end_turn",
        request_id="r1",
        options=[{"o": "1"}],
        tool_input="in",
        tool_output="out",
        input_tokens=5,
        output_tokens=6,
        cache_creation_tokens=7,
        cache_read_tokens=8,
        cost_usd=0.5,
        num_turns=2,
        duration_ms=99,
        event_count=11,
        tool_call_count=3,
    )
    ev = acp_event_to_agent_event(acp)
    assert isinstance(ev, AgentEvent)
    for f in fields(AcpEvent):
        assert getattr(ev, f.name) == getattr(acp, f.name), f.name


def test_event_constants_parity():
    """acp.types and llm.events define the same EVENT_* values (duplicated to
    avoid a circular import; this pins they never drift)."""
    import personalclaw.acp.types as at
    import personalclaw.llm.events as le

    for name in (
        "EVENT_TEXT_CHUNK",
        "EVENT_THINKING_CHUNK",
        "EVENT_TOOL_CALL",
        "EVENT_TOOL_RESULT",
        "EVENT_PERMISSION_REQUEST",
        "EVENT_COMPLETE",
        "EVENT_COMPACTION_STATUS",
        "EVENT_CLEAR_STATUS",
        "EVENT_AGENT_SWITCHED",
    ):
        assert getattr(at, name) == getattr(le, name), name


def test_http_providers_are_model_providers():
    # openai/anthropic are the core-resident PROTOCOL clients. Model-provider
    # APPS (ollama/bedrock/vllm/…) assert their ModelProvider subclassing in
    # their own apps/<name>-models/tests/.
    from personalclaw.llm.anthropic import AnthropicProvider
    from personalclaw.llm.base import ModelProvider
    from personalclaw.llm.openai import OpenAIProvider

    for P in (OpenAIProvider, AnthropicProvider):
        assert issubclass(P, ModelProvider)

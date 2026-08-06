"""PCS-1 / F1: prompt-cache wire-order repair for the Anthropic translation.

Anthropic prompt caching matches on an EXACT prefix, and Anthropic serves the
out-of-band ``system=`` param AHEAD of ``messages[0]``. The native loop appends
exactly one per-turn ``role: "system"`` note (the turn_note — tool catalog +
group stubs) whose content CHANGES every turn. Before this fix ``_translate_messages``
hoisted that volatile note into ``system=``, so a volatile string led the served
prompt ahead of the stable assembled context (``messages[0]``, a user message),
structurally zeroing the cache hit rate.

The fix: the native runtime tags that note ``{"_volatile": True}``; ``_translate_messages``
routes an untagged ``system`` message into ``system=`` exactly as before, but relocates a
volatile note to the TAIL of the message list (carried as a trailing ``user`` message —
Anthropic has no trailing-system concept). The note moves position, never existence.

Guardrail-2 (byte-identical when off): a message list with NO volatile tag must produce
byte-for-byte the pre-PCS-1 ``(system, messages)``. This is pinned below.

Note: the plan's V1 "no comprehension regression" check — that the model still calls
``tool_schema`` after the catalog moved to the tail — is a live-model owner-validation step,
not headless-runnable. The structural property it depends on (the catalog still REACHES the
model, just late) is asserted here instead.
"""

from __future__ import annotations

import pytest

from personalclaw.agents.native.runtime import NativeAgentRuntime
from personalclaw.agents.provider import AgentRuntimeDefinition
from personalclaw.llm.anthropic import _VOLATILE_MESSAGE_KEY, _translate_messages
from personalclaw.llm.events import EVENT_COMPLETE, AgentEvent
from personalclaw.tool_providers.base import ToolDefinition, ToolProvider, ToolResult

# ── guardrail-2: byte-identical when no message is tagged volatile ──


def test_untagged_list_is_byte_identical_to_pre_pcs1_behavior():
    """A plain system + user + assistant list → exactly the pre-PCS-1 output.

    The expected ``(system, messages)`` is constructed by hand from the original
    logic: system content concatenated into ``system=``; plain user/assistant
    messages pass through as ``{role, content}``. No ``_volatile`` key anywhere.
    """
    messages = [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi there"},
    ]

    system, out = _translate_messages(messages)

    assert system == "You are a helpful assistant."
    assert out == [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi there"},
    ]


def test_untagged_multi_system_concatenation_unchanged():
    """Two untagged system messages still join with the historical ``\\n\\n`` separator."""
    messages = [
        {"role": "system", "content": "line one"},
        {"role": "system", "content": "line two"},
        {"role": "user", "content": "go"},
    ]

    system, out = _translate_messages(messages)

    assert system == "line one\n\nline two"
    assert out == [{"role": "user", "content": "go"}]


def test_untagged_tool_and_toolcall_shapes_unchanged():
    """A tool-call / tool-result round trip is untouched by the volatile routing."""
    messages = [
        {"role": "user", "content": "run it"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "call_1", "function": {"name": "echo", "arguments": '{"x": "hi"}'}}
            ],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": "OUT:hi"},
    ]

    system, out = _translate_messages(messages)

    assert system == ""
    assert out == [
        {"role": "user", "content": "run it"},
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "call_1", "name": "echo", "input": {"x": "hi"}}],
        },
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "call_1", "content": "OUT:hi"}],
        },
    ]


# ── volatile routing: stable system stays in system=, volatile note → tail ──


def test_volatile_note_routes_to_tail_not_system():
    """A ``_volatile`` system note is NOT hoisted; it is the LAST returned message."""
    messages = [
        {"role": "system", "content": "STABLE base prompt"},
        {"role": "user", "content": "the assembled context"},
        {
            "role": "system",
            "content": "[tool catalog] VOLATILE per-turn note",
            _VOLATILE_MESSAGE_KEY: True,
        },
    ]

    system, out = _translate_messages(messages)

    # Stable system content stays out-of-band (leads the served prompt).
    assert system == "STABLE base prompt"
    # The volatile note is NOT in system=.
    assert "VOLATILE per-turn note" not in system
    # It rides at the TAIL as a user message, content verbatim.
    assert out[-1] == {"role": "user", "content": "[tool catalog] VOLATILE per-turn note"}
    # The volatile note never carries the marker key downstream to the wire.
    assert _VOLATILE_MESSAGE_KEY not in out[-1]


def test_multiple_volatile_notes_each_ship_once_in_order():
    """If several volatile notes appear, each ships exactly once, in original order, at the tail."""
    messages = [
        {"role": "user", "content": "context"},
        {"role": "system", "content": "note A", _VOLATILE_MESSAGE_KEY: True},
        {"role": "system", "content": "note B", _VOLATILE_MESSAGE_KEY: True},
    ]

    system, out = _translate_messages(messages)

    assert system == ""
    assert out == [
        {"role": "user", "content": "context"},
        {"role": "user", "content": "note A"},
        {"role": "user", "content": "note B"},
    ]


# ── content-equivalence: every input content present exactly once ──


def test_content_equivalence_note_relocated_not_lost_or_duplicated():
    """Every input message's content appears exactly once across ``(system, messages)``."""
    messages = [
        {"role": "system", "content": "STABLE"},
        {"role": "user", "content": "USERCTX"},
        {"role": "assistant", "content": "PRIORREPLY"},
        {"role": "system", "content": "VOLATILE", _VOLATILE_MESSAGE_KEY: True},
    ]

    system, out = _translate_messages(messages)

    haystack = system + " " + " ".join(str(m.get("content", "")) for m in out)
    for token in ("STABLE", "USERCTX", "PRIORREPLY", "VOLATILE"):
        assert (
            haystack.count(token) == 1
        ), f"{token!r} must appear exactly once, not dropped/duplicated"


# ── stable prefix leads: the served prompt's head no longer changes per turn ──


def test_native_shape_stable_context_leads_volatile_at_tail():
    """Native-shaped list (user assembled-context + volatile turn_note).

    There is no stable base system message in the native loop, so ``system=`` is
    empty; the stable assembled context (the user message) leads at ``messages[0]``
    and the volatile note sits at the tail — exactly the reordering F1 requires.
    """
    messages = [
        {"role": "user", "content": "ASSEMBLED CONTEXT (stable across the turn)"},
        {"role": "system", "content": "[tool catalog] volatile", _VOLATILE_MESSAGE_KEY: True},
    ]

    system, out = _translate_messages(messages)

    assert system == ""
    assert out[0] == {"role": "user", "content": "ASSEMBLED CONTEXT (stable across the turn)"}
    assert out[-1] == {"role": "user", "content": "[tool catalog] volatile"}


def test_stable_system_leads_when_present():
    """With a stable base system + assembled context, system= carries the stable prefix."""
    messages = [
        {"role": "system", "content": "STABLE PREFIX"},
        {"role": "user", "content": "ASSEMBLED CONTEXT"},
        {"role": "system", "content": "volatile", _VOLATILE_MESSAGE_KEY: True},
    ]

    system, out = _translate_messages(messages)

    assert system == "STABLE PREFIX"
    assert out[0] == {"role": "user", "content": "ASSEMBLED CONTEXT"}
    assert out[-1] == {"role": "user", "content": "volatile"}


def test_v1_catalog_still_reaches_the_model_just_late():
    """V1 structural: the tool catalog (in the turn_note) is still in the final wire payload.

    A full-live-model recency check (does the model still call ``tool_schema`` after the
    catalog moved late?) is an owner-validation step, not a unit test. The structural
    property it rests on is: the catalog is delivered — present in ``messages`` — not
    dropped. It just no longer leads.
    """
    catalog_note = '[tool catalog] call tool_schema("name") to expand; nothing is disabled.'
    messages = [
        {"role": "user", "content": "assembled context"},
        {"role": "system", "content": catalog_note, _VOLATILE_MESSAGE_KEY: True},
    ]

    system, out = _translate_messages(messages)

    payload_text = "\n".join(str(m.get("content", "")) for m in out)
    assert catalog_note in payload_text  # delivered
    assert catalog_note not in system  # but not at the head


# ── runtime tagging: the native loop marks its turn_note volatile ──


class _ScriptedModel:
    """Minimal ModelProvider capturing the messages each ``complete()`` sees."""

    supports_tools = True
    _model = "scripted"

    def __init__(self) -> None:
        self.seen_messages: list[list[dict]] = []

    async def complete(self, messages, *, tools=None, model=None, reasoning_effort=""):
        self.seen_messages.append(list(messages))
        yield AgentEvent(kind=EVENT_COMPLETE)


class _ManyTools(ToolProvider):
    """Enough tools that per-turn retrieval reduces and emits a catalog turn_note."""

    def __init__(self, n: int) -> None:
        self._n = n

    @property
    def name(self) -> str:
        return "many"

    @property
    def display_name(self) -> str:
        return "Many"

    async def list_tools(self):
        return [
            ToolDefinition(
                name=f"niche_tool_{i}",
                description=f"does niche thing {i}",
                parameters={"type": "object", "properties": {"q": {"type": "string"}}},
                requires_approval=False,
                provider="many",
            )
            for i in range(self._n)
        ]

    async def invoke(self, tool_name, arguments):
        return ToolResult(success=True, output=f"ran {tool_name}")


@pytest.mark.asyncio
async def test_runtime_tags_turn_note_volatile():
    """The native loop's appended turn_note system message carries the volatile marker."""
    model = _ScriptedModel()
    rt = NativeAgentRuntime(
        definition=AgentRuntimeDefinition(name="T", provider="native", model="scripted"),
        model_provider=model,
        tool_providers=[_ManyTools(80)],
    )
    await rt.start()
    async for _ in rt.stream("do something unrelated to any niche tool"):
        pass

    sent = model.seen_messages[-1]
    sys_notes = [m for m in sent if m.get("role") == "system"]
    # A turn_note was emitted (catalog present) and it is tagged volatile.
    assert sys_notes, "expected a per-turn system note when retrieval reduces"
    assert all(m.get(_VOLATILE_MESSAGE_KEY) is True for m in sys_notes)
    assert any("[tool catalog]" in str(m.get("content", "")) for m in sys_notes)

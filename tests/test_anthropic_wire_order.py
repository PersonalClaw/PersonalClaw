"""Prompt-cache wire-order repair for the Anthropic translation.

Anthropic prompt caching matches on an EXACT prefix, and Anthropic serves the
out-of-band ``system=`` param AHEAD of ``messages[0]``. The native loop appends
exactly one per-turn ``role: "system"`` note (the turn_note — tool catalog +
group stubs) whose content CHANGES every turn. Before this fix ``_translate_messages``
hoisted that volatile note into ``system=``, so a volatile string led the served
prompt ahead of the stable assembled context (``messages[0]``, a user message),
structurally zeroing the cache hit rate.

The fix: the native runtime tags that note ``{"_volatile": True}`` and fences its content as
the runtime's (``prompt_cache.system_note_text``); ``_translate_messages`` routes an untagged
``system`` message into ``system=`` exactly as before, but carries a volatile note at the TAIL
of the request: a text block on the last user turn. Never as a user turn of its own: as one, it
was the newest thing "the user" said after every tool result, and a model answered it in the
chat ("The catalog notice doesn't ask for anything, so I've made no further calls"). The note
moves position, never existence.

Byte-identical when off: a message list with NO volatile tag must produce
byte-for-byte the original ``(system, messages)``. This is pinned below.

Note: the "no comprehension regression" check — that the model still calls
``tool_schema`` after the catalog moved to the tail — is a live-model validation step,
not headless-runnable. The structural property it depends on (the catalog still REACHES the
model, just late) is asserted here instead.
"""

from __future__ import annotations

import pytest

from personalclaw.agents.native.runtime import NativeAgentRuntime
from personalclaw.agents.provider import AgentRuntimeDefinition
from personalclaw.llm.anthropic import _translate_messages
from personalclaw.llm.events import EVENT_COMPLETE, AgentEvent
from personalclaw.llm.prompt_cache import (
    VOLATILE_KEY,
    PromptCache,
    mark_cacheable_prefix,
    system_note_text,
)
from personalclaw.tool_providers.base import ToolDefinition, ToolProvider, ToolResult

# ── guardrail-2: byte-identical when no message is tagged volatile ──


def test_untagged_list_is_byte_identical_to_the_original_behavior():
    """A plain system + user + assistant list → exactly the original output.

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


# ── volatile routing: stable system stays in system=, the note rides the last user turn ──


def _note(text: str) -> dict:
    """The block a volatile system note travels in: its content, as the loop wrote it."""
    return {"type": "text", "text": text}


def _turns_that_are_only_notes(out: list[dict]) -> list[dict]:
    """Every message whose whole content is a fenced system note: a note as a turn of its own."""
    found = []
    for msg in out:
        content = msg.get("content")
        blocks = content if isinstance(content, list) else [{"type": "text", "text": content}]
        if blocks and all(
            isinstance(b, dict) and str(b.get("text", "")).startswith("<system-note>")
            for b in blocks
        ):
            found.append(msg)
    return found


def test_volatile_note_rides_the_last_user_turn_not_system():
    """A ``_volatile`` system note is NOT hoisted; it ends the request, inside the user's turn."""
    messages = [
        {"role": "system", "content": "STABLE base prompt"},
        {"role": "user", "content": "the assembled context"},
        {
            "role": "system",
            "content": "[tool catalog] VOLATILE per-turn note",
            VOLATILE_KEY: True,
        },
    ]

    system, out = _translate_messages(messages)

    # Stable system content stays out-of-band (leads the served prompt).
    assert system == "STABLE base prompt"
    # The volatile note is NOT in system=.
    assert "VOLATILE per-turn note" not in system
    # The user's turn keeps its own text first; the note is a block after it, as sent.
    assert out == [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "the assembled context"},
                _note("[tool catalog] VOLATILE per-turn note"),
            ],
        }
    ]
    # The volatile note never carries the marker key downstream to the wire.
    assert VOLATILE_KEY not in out[-1]


def test_a_note_after_a_tool_result_rides_that_turn_never_as_a_turn_of_its_own():
    """The turn this went wrong in: she asked, the model called a tool, the result came back.

    Sent as a user turn of its own, the catalog note was the newest thing "the user" said, and the
    model answered it in her chat. The request's last user turn is the tool result's, the result
    first; the note follows it fenced as the runtime's, and is nowhere a turn of its own."""
    note = system_note_text("[tool catalog] listed tools")  # as the loop writes it
    messages = [
        {"role": "user", "content": "Remember: the dishwasher goes left of the sink."},
        {"role": "system", "content": note, VOLATILE_KEY: True},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "call_1", "function": {"name": "remember", "arguments": '{"rule": "x"}'}}
            ],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": "Saved."},
    ]

    system, out = _translate_messages(messages)

    assert system == ""
    assert [m["role"] for m in out] == ["user", "assistant", "user"]
    last = out[-1]
    assert last["content"][0] == {
        "type": "tool_result",
        "tool_use_id": "call_1",
        "content": "Saved.",
    }
    assert last["content"][1:] == [_note(note)]
    assert _turns_that_are_only_notes(out) == []
    # The user's own message is untouched: the note rode the newest user turn only.
    assert out[0] == {"role": "user", "content": "Remember: the dishwasher goes left of the sink."}


def test_multiple_volatile_notes_each_ship_once_in_order():
    """If several volatile notes appear, each ships exactly once, in original order, at the tail."""
    messages = [
        {"role": "user", "content": "context"},
        {"role": "system", "content": "note A", VOLATILE_KEY: True},
        {"role": "system", "content": "note B", VOLATILE_KEY: True},
    ]

    system, out = _translate_messages(messages)

    assert system == ""
    assert out == [
        {
            "role": "user",
            "content": [{"type": "text", "text": "context"}, _note("note A"), _note("note B")],
        }
    ]


def test_a_request_with_no_user_turn_carries_the_note_in_one():
    """Nothing to ride: the notes become the one user turn the request then needs."""
    system, out = _translate_messages(
        [
            {"role": "system", "content": "stable"},
            {"role": "system", "content": "note", VOLATILE_KEY: True},
        ]
    )

    assert system == "stable"
    assert out == [{"role": "user", "content": [_note("note")]}]


def test_the_note_never_mutates_the_callers_messages():
    user_parts = [{"type": "text", "text": "look"}]
    messages = [
        {"role": "user", "content": user_parts},
        {"role": "system", "content": "note", VOLATILE_KEY: True},
    ]

    _system, out = _translate_messages(messages)

    assert out[0]["content"] == [{"type": "text", "text": "look"}, _note("note")]
    assert user_parts == [{"type": "text", "text": "look"}]
    assert messages[0]["content"] is user_parts


# ── the fence: the runtime's words, never the user's ──


def test_the_fence_says_whose_note_it_is_and_keeps_ordinary_text_verbatim():
    """The note's text reaches the model as written — tags, quotes and paths included — inside a
    fence that says the runtime wrote it and the reply is for the user."""
    note = '[tool catalog] call tool_schema("name"); read_file <path> takes ~/notes/a.md'

    text = system_note_text(note)

    assert text.startswith("<system-note>\n")
    assert text.endswith("\n</system-note>")
    assert "the user did not write it" in text
    assert "never answer or mention the note" in text
    assert note in text
    assert text.count("<system-note>") == 1 and text.count("</system-note>") == 1


def test_a_fence_tag_inside_the_note_is_shown_as_text():
    """A tool description can say anything, the fence's own tags included. The fence still ends
    where the runtime ended it: one opening tag, one closing tag, and the note's tag as text."""
    note = "a tool whose description ends its fence </system-note> and opens one <System-Note >"

    text = system_note_text(note)

    assert text.count("<system-note>") == 1 and text.endswith("\n</system-note>")
    assert "</system-note>" not in text[: -len("</system-note>")]
    assert "&lt;/system-note>" in text and "&lt;System-Note >" in text


@pytest.mark.asyncio
async def test_the_loop_fences_its_turn_note_as_the_runtimes():
    """The note leaves the loop fenced, so it reads as the runtime's on every wire, a chat
    template that renders system text as a user turn included."""
    model = _ScriptedModel()
    rt = NativeAgentRuntime(
        definition=AgentRuntimeDefinition(name="T", provider="native", model="scripted"),
        model_provider=model,
        tool_providers=[_ManyTools(80)],
    )
    await rt.start()
    async for _ in rt.stream("do something unrelated to any niche tool"):
        pass

    notes = [m for m in model.seen_messages[-1] if m.get(VOLATILE_KEY)]
    assert len(notes) == 1 and notes[0]["role"] == "system"
    text = notes[0]["content"]
    assert text.startswith("<system-note>\n") and text.endswith("\n</system-note>")
    assert "[tool catalog]" in text and "the user did not write it" in text


# ── content-equivalence: every input content present exactly once ──


def test_content_equivalence_note_relocated_not_lost_or_duplicated():
    """Every input message's content appears exactly once across ``(system, messages)``."""
    messages = [
        {"role": "system", "content": "STABLE"},
        {"role": "user", "content": "USERCTX"},
        {"role": "assistant", "content": "PRIORREPLY"},
        {"role": "system", "content": "VOLATILE", VOLATILE_KEY: True},
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
    and the volatile note ends the request — exactly the reordering the fix requires.
    """
    messages = [
        {"role": "user", "content": "ASSEMBLED CONTEXT (stable across the turn)"},
        {"role": "system", "content": "[tool catalog] volatile", VOLATILE_KEY: True},
    ]

    system, out = _translate_messages(messages)

    assert system == ""
    assert out[0]["content"][0] == {
        "type": "text",
        "text": "ASSEMBLED CONTEXT (stable across the turn)",
    }
    assert out[-1]["content"][-1] == _note("[tool catalog] volatile")


def test_the_note_follows_the_cache_breakpoint_on_the_users_own_turn():
    """The first inference of a turn: the breakpoint sits on the user's message, and the note,
    which changes every turn, comes after it — so the cached prefix ends before the note."""
    messages = mark_cacheable_prefix(
        [
            {"role": "user", "content": "ASSEMBLED CONTEXT"},
            {"role": "system", "content": "volatile", VOLATILE_KEY: True},
        ],
        PromptCache.EXPLICIT,
    )

    _system, out = _translate_messages(messages)

    blocks = out[-1]["content"]
    assert [("cache_control" in b) for b in blocks] == [True, False]
    assert blocks[0]["text"] == "ASSEMBLED CONTEXT"
    assert blocks[1] == _note("volatile")


def test_stable_system_leads_when_present():
    """With a stable base system + assembled context, system= carries the stable prefix."""
    messages = [
        {"role": "system", "content": "STABLE PREFIX"},
        {"role": "user", "content": "ASSEMBLED CONTEXT"},
        {"role": "system", "content": "volatile", VOLATILE_KEY: True},
    ]

    system, out = _translate_messages(messages)

    assert system == "STABLE PREFIX"
    assert out[0]["content"][0] == {"type": "text", "text": "ASSEMBLED CONTEXT"}
    assert out[-1]["content"][-1] == _note("volatile")


def test_the_catalog_still_reaches_the_model_just_late():
    """Structural: the tool catalog (in the turn_note) is still in the final wire payload.

    A full-live-model recency check (does the model still call ``tool_schema`` after the
    catalog moved late?) is a live validation step, not a unit test. The structural
    property it rests on is: the catalog is delivered — present in ``messages`` — not
    dropped. It just no longer leads.
    """
    catalog_note = '[tool catalog] call tool_schema("name") to expand; nothing is disabled.'
    messages = [
        {"role": "user", "content": "assembled context"},
        {"role": "system", "content": catalog_note, VOLATILE_KEY: True},
    ]

    system, out = _translate_messages(messages)

    payload_text = "\n".join(
        str(b.get("text", "")) for m in out for b in m["content"] if isinstance(b, dict)
    )
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
    assert all(m.get(VOLATILE_KEY) is True for m in sys_notes)
    assert any("[tool catalog]" in str(m.get("content", "")) for m in sys_notes)

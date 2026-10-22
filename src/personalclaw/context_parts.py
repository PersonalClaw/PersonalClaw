"""What fills a chat's context window, part by part (#3823).

The context reading is one number, the provider's own measure of how full the window is. The page
also lists what takes the space, each part with its tokens and the action that frees it: the
instructions and tool definitions (review the tools), memory and profile (review what is recalled),
attached files, the conversation and tool results (compact). The parts are counted here, where the
request is assembled, with the token counter the headroom contract uses; nothing is estimated in the
browser. A part nothing here measures is left out, and the page says it is not measured.

Two sources:

* **The turn's assembly** (:func:`assembly_parts`): the context engine names every component it
  joins into the turn's message ("system prompt", "active recall", "the user's request", ...), so
  each one's kind is known. An engine that labelled nothing measures nothing.
* **A runtime that holds the conversation itself** (the native loop, :func:`history_parts`): at
  each request it measures the whole history it sends, splitting each turn's message the way that
  turn's assembly did. An agent CLI keeps its own history, so it measures no parts.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from typing import Any

from personalclaw.context_headroom import Component, count_tokens

#: The parts, in the order the page lists them and ``/context`` prints them.
PART_KINDS: tuple[str, ...] = ("instructions", "memory", "files", "conversation", "tool_results")

#: What the page offers for each part: the action that frees its space.
PART_ACTIONS: dict[str, str] = {
    "instructions": "tools",
    "memory": "memory",
    "files": "files",
    "conversation": "compact",
    "tool_results": "compact",
}

#: Component names (``context._Parts.add(name=...)`` and the engine's prepends) that carry memory.
_MEMORY = frozenset(
    {
        "active recall",
        "pushed memory",
        "episodic memory",
        "session context (memory · lessons · history)",
    }
)

#: Component names that carry the conversation: the request, and the history it is read with.
_CONVERSATION = frozenset({"the user's request", "thread context", "channel history"})

#: The fallback an engine's unlabelled assembly is measured as (``context_engine``): nothing in it
#: says which kind any of it is.
_UNLABELLED_PREFIX = "assembled prompt"


def kind_of(component_name: str) -> str:
    """The part a named component belongs to; every other named block is an instruction."""
    if component_name in _MEMORY:
        return "memory"
    if component_name in _CONVERSATION:
        return "conversation"
    return "instructions"


def assembly_parts(components: Sequence[Component]) -> dict[str, int] | None:
    """The tokens of each part in one turn's assembled message, or ``None`` when the assembly
    labelled nothing (an engine that names no components, or an empty one)."""
    if not components or any(c.name.startswith(_UNLABELLED_PREFIX) for c in components):
        return None
    out: dict[str, int] = {}
    for c in components:
        kind = kind_of(c.name)
        out[kind] = out.get(kind, 0) + count_tokens(c.text)
    return out


def parts_said(measured: dict[str, int]) -> list[dict[str, Any]]:
    """*measured* as the frame says it: ``{kind, tokens, action}`` in :data:`PART_KINDS` order,
    the kinds nothing measured left out."""
    return [
        {"kind": k, "tokens": int(measured[k]), "action": PART_ACTIONS[k]}
        for k in PART_KINDS
        if k in measured
    ]


def _text_of(message: dict) -> str:
    """The text of one history message as the provider is sent it: its content (the text of a
    list of parts) and, for a reply that called tools, the calls' names and arguments."""
    content = message.get("content")
    if isinstance(content, list):
        text = "".join(str(p.get("text", "")) for p in content if isinstance(p, dict))
    else:
        text = str(content or "")
    calls = message.get("tool_calls")
    if calls:
        text += json.dumps(calls, ensure_ascii=False, default=str)
    return text


def history_parts(
    messages: Iterable[dict],
    marks: Sequence[tuple[dict, dict[str, int], int]],
    tools: Sequence[Any] | None = None,
) -> list[dict[str, Any]]:
    """The parts of a request a runtime builds from the conversation it holds.

    *marks* are ``(message, parts, length)`` for each turn's message as its assembly measured it.
    A marked message still that long is split exactly as measured; one rewritten since (a
    compaction's digest, a compressed retry) is split in the same proportions, or counted as
    conversation when nothing is left of the split. Tool results are their own part, everything
    else the model said is conversation, and system notes and the tool definitions are
    instructions. Files are not measured: an attached image rides the request, not the history.
    """
    by_id = {id(m): (m, parts, length) for m, parts, length in marks}
    totals: dict[str, int] = {"instructions": 0, "memory": 0, "conversation": 0, "tool_results": 0}
    for message in messages:
        role = message.get("role")
        text = _text_of(message)
        tokens = count_tokens(text)
        if role == "tool":
            totals["tool_results"] += tokens
        elif role == "system":
            totals["instructions"] += tokens
        elif role == "user" and (mark := by_id.get(id(message))) and mark[0] is message:
            _, parts, length = mark
            measured = sum(parts.values())
            if len(text) == length:
                for kind, n in parts.items():
                    totals[kind] += n
            elif measured:
                for kind, n in parts.items():
                    totals[kind] += round(tokens * n / measured)
            else:
                totals["conversation"] += tokens
        else:
            totals["conversation"] += tokens
    if tools:
        totals["instructions"] += count_tokens(
            json.dumps(list(tools), ensure_ascii=False, default=str)
        )
    return parts_said(totals)

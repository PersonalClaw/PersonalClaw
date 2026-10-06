"""What its owner declined: the record her Deny of a tool call leaves, and how a sentence names it.

A Deny is her answer for the work it stopped. Whatever would put work back in front of her once
that work is over reads this record, so it does not ask her the same thing again by itself, and
says what she declined in words she recognises: a workflow loop's next cycle and the run's page
(`workflows.declines`), from what a subagent's calls came to (`subagent_tier`); a loop's cycle,
its page and its planner's pass (`loop.watchdog.LoopWatchdog.hold_after_decline`,
`planning.runner`), from what a chat turn kept (`_ChatSession._last_turn_declined`). Every one of
them says what she can do while the work waits for her the same way (:func:`waits_for_you`).

The record is the tool, as its approval card named it, and the first line of each of the first
eight values its input carries (a path, a command), masked as the card showed them and each cut
to 300 characters. Nothing past a value's first line is kept, so what a call would have written
is not.

Only her Deny makes one. An ask nobody answered in time, one that ended before anyone answered,
and one there was nowhere to put to her are not hers (`subagent_tier.SubagentTier.declined`).

Her Deny of a change to her memory is her answer for what its turn would keep, too
(:func:`declined_a_memory_change`, read from the approval rows the turn wrote): nothing the turn's
words or work hold is kept, not by the turn's own learning nor by the consolidation that reads the
conversation later (``own_words.her_turns``), so what she refused to save is not saved another way.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from typing import Any

from personalclaw.security import redact_credentials, redact_exfiltration_urls

#: How much of a declined call's input is kept: the first line of each of at most this many
#: values, each cut to this length. Its tool's name is cut to the last.
_NAMES, _NAME_CHARS, _TOOL_CHARS = 8, 300, 160

#: How many declined calls a sentence names before it counts the rest.
_NAMED_MAX = 2


def _masked(text: str) -> str:
    return redact_credentials(redact_exfiltration_urls(text)[0])[0].strip()


def declined_step(title: str, tool_input: object) -> dict[str, Any]:
    """A call she declined, as the work it stopped keeps it: ``{"tool", "names"}``."""
    raw = tool_input
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            pass
    names: list[str] = []
    for value in raw.values() if isinstance(raw, dict) else [raw]:
        for item in value if isinstance(value, list) else [value]:
            line = item.strip().split("\n", 1)[0] if isinstance(item, str) else ""
            if line.strip():
                names.append(_masked(line)[:_NAME_CHARS])
    return {"tool": _masked(title)[:_TOOL_CHARS], "names": names[:_NAMES]}


def named(step: Any) -> str:
    """One declined call as a sentence names it: its tool, and what it named first, in brackets,
    when its tool's own name does not already say it — ``write_file (notes/plan.md)``."""
    if not isinstance(step, dict):
        return "a step"
    tool = " ".join(str(step.get("tool") or "").split()) or "a step"
    names = [" ".join(str(n).split()) for n in step.get("names") or []]
    first = next((n for n in names if n), "")
    return f"{tool} ({first})" if first and first not in tool else tool


def said(steps: list[Any]) -> str:
    """The calls *steps* declined, as a sentence lists them, each once: ``a``, ``a and b``, or
    ``a, b and 2 more``."""
    shown = list(dict.fromkeys(named(step) for step in steps))
    if not shown:
        return "a step"
    if len(shown) > _NAMED_MAX + 1:
        return f"{', '.join(shown[:_NAMED_MAX])} and {len(shown) - _NAMED_MAX} more"
    if len(shown) == 1:
        return shown[0]
    return f"{', '.join(shown[:-1])} and {shown[-1]}"


def under(folder: str, steps: list[Any]) -> list[Any]:
    """*steps* with what each names inside *folder* said by its place there
    (``findings/cycle_001.json``): a sentence about the work of one folder does not spell the
    folder out each time."""
    base = str(folder or "").rstrip("/")
    if not base:
        return list(steps)
    inside = f"{base}/"

    def _short(text: Any) -> str:
        return str(text or "").replace(inside, "")

    return [
        (
            {
                **step,
                "tool": _short(step.get("tool")),
                "names": [_short(n) for n in step.get("names") or []],
            }
            if isinstance(step, dict)
            else step
        )
        for step in steps
    ]


def waits_for_you(noun: str = "loop", stop: str = "stop") -> str:
    """What work that waits for its owner after her Deny asks of her: ``The loop waits for you:
    tell it what to do instead, or resume or stop it.``"""
    return f"The {noun} waits for you: tell it what to do instead, or resume or {stop} it."


def changes_memory(title: str, tool_input: object = None) -> bool:
    """Whether a call, named as its approval card names it (*title*: the tool's own name, or the
    name an agent CLI gives one of PersonalClaw's tools) with its input, would change her memory:
    one of the memory tools that changes it (``mcp_memory.CHANGES_MEMORY``), or a call that names a
    place in the memory folders and does more than read it, as the screen of long-term memory reads
    a call (``file_scope.call_paths``): a file tool's path, a command's."""
    from personalclaw.acp.mcp_servers import core_tool_titled
    from personalclaw.file_scope import call_paths
    from personalclaw.mcp_memory import CHANGES_MEMORY
    from personalclaw.memory import in_memory_folders

    if (core_tool_titled(title) or title) in CHANGES_MEMORY:
        return True
    args = tool_input
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except ValueError:
            args = None
    command = args.get("command") if isinstance(args, Mapping) else None
    named, reads = call_paths(title, tool_input, command if isinstance(command, str) else "")
    return not reads and any(in_memory_folders(path) for _word, path in named)


def declined_a_memory_change(rows: Iterable[Mapping[str, Any]]) -> bool:
    """Whether her Deny answered, among *rows* (one turn's), a call that would have changed her
    memory (:func:`changes_memory`), read from the approval row the call wrote, which records her
    answer (``resolved``) and what the call carried: an ask nobody answered, or one its turn's
    Stop ended, is not hers, and a call the card held to be a read changes nothing."""
    for row in rows:
        if row.get("role") != "permission":
            continue
        try:
            card = json.loads(str(row.get("cls") or ""))
        except ValueError:
            continue
        if not isinstance(card, dict) or card.get("resolved") != "rejected":
            continue
        if card.get("is_read_only") != "1" and changes_memory(
            str(row.get("content") or ""), card.get("tool_input")
        ):
            return True
    return False

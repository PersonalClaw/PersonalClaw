"""What its owner declined: the record her Deny of a tool call leaves, and how a sentence names it.

A Deny is her answer for the work it stopped. Whatever would put work back in front of her once
that work is over reads this record, so it does not ask her the same thing again by itself, and
says what she declined in words she recognises: a workflow loop's next cycle and the run's page
(`workflows.declines`), from what a subagent's calls came to (`subagent_tier`).

The record is the tool, as its approval card named it, and the first line of each of the first
eight values its input carries (a path, a command), masked as the card showed them and each cut
to 300 characters. Nothing past a value's first line is kept, so what a call would have written
is not.

Only her Deny makes one. An ask nobody answered in time, one that ended before anyone answered,
and one there was nowhere to put to her are not hers (`subagent_tier.SubagentTier.declined`).
"""

from __future__ import annotations

import json
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

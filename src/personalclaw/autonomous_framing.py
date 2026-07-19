"""Shared framing for autonomous, no-user-present agent turns.

A scheduled run, a goal loop cycle, and a space member's turn all execute with no
human reachable to answer questions. Without an explicit signal the model falls
back to conversational affordances — asking clarifying questions, offering
``[OPTIONS: …]`` menus — which is wrong for an unattended run: the output is read
later as a report, not answered live.

This module is the single source of truth for that framing so every unattended
runner (cron, goal loops, spaces) prepends the same instruction. Callers wrap
their task prompt with :func:`with_autonomous_framing` (or read
:data:`AUTONOMOUS_TURN_PREAMBLE` directly when they assemble the prompt
themselves).

The preamble text lives in the prompt system as the bundled snippet
``autonomous-turn-preamble`` so it shows up in Settings → Prompts and can be
edited there. :data:`AUTONOMOUS_TURN_PREAMBLE` is the shipped fallback used when
the prompt store can't be resolved (and remains importable for callers that read
it directly).
"""

from __future__ import annotations

AUTONOMOUS_TURN_PREAMBLE = (
    "[AUTONOMOUS RUN — no user is present to reply]\n"
    "You are running unattended. Your output is read later as a report; no one "
    "can answer questions or pick options during this run. Therefore:\n"
    "- Do NOT ask the user questions or wait for input.\n"
    "- Do NOT offer interactive menus or option lists (no \"[OPTIONS: …]\", no "
    "\"which would you like?\").\n"
    "- Complete the task end-to-end with the tools available, then report what "
    "you did and found.\n"
    "- If a decision is genuinely blocked on missing input, state the blocker "
    "and your recommended default, and proceed with that default where safe.\n"
    "[END AUTONOMOUS RUN CONTEXT]"
)


def _preamble() -> str:
    """The autonomous-run preamble — the bound snippet, or the shipped fallback."""
    from personalclaw.prompt_providers.runtime import render_snippet_block

    return render_snippet_block("autonomous-turn-preamble") or AUTONOMOUS_TURN_PREAMBLE


def with_autonomous_framing(prompt: str) -> str:
    """Prepend the autonomous-run preamble to ``prompt``.

    Returns the framing alone when ``prompt`` is empty, so a bare trigger still
    carries the instruction.
    """
    preamble = _preamble()
    prompt = prompt or ""
    if not prompt.strip():
        return preamble
    return f"{preamble}\n\n{prompt}"

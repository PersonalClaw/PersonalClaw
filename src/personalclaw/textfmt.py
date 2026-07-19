"""Generic LLM-text utilities — provider-agnostic, no channel semantics.

These helpers operate on raw model output and are consumed across the core
(subagent result cleanup, dashboard chat mirroring) independent of any delivery
channel. Channel-specific rendering (Slack mrkdwn, Block Kit) lives in the
channel's own bundle, not here.
"""

import re

# Pattern: [OPTIONS: choice1 | choice2 | choice3] on its own trailing line.
_OPTIONS_RE = re.compile(r"\[OPTIONS:\s*(.+?)\]\s*$", re.MULTILINE)

# Inline thinking tags some models embed in their text output.
_THINKING_TAG_RE = re.compile(
    r"<(?:thinking|antml:thinking)>.*?</(?:thinking|antml:thinking)>",
    re.DOTALL,
)


def extract_options(text: str) -> tuple[str, list[str]]:
    """Extract ``[OPTIONS: a | b | c]`` choices from LLM output and strip the tag.

    Returns ``(cleaned_text, choices)``. If no OPTIONS tag is present, ``choices``
    is empty and ``text`` is returned unchanged.
    """
    m = _OPTIONS_RE.search(text)
    if not m:
        return text, []
    choices = [c.strip() for c in m.group(1).split("|") if c.strip()]
    cleaned = text[: m.start()].rstrip()
    return cleaned, choices


def strip_thinking_tags(text: str, *, strip_whitespace: bool = True) -> tuple[str, str]:
    """Strip inline ``<thinking>`` tags from text.

    Returns ``(cleaned_text, extracted_thinking)``.
    """
    thinking_parts: list[str] = []
    for m in _THINKING_TAG_RE.finditer(text):
        block = m.group(0)
        inner = re.sub(r"^<[^>]+>|<[^>]+>$", "", block).strip()
        if inner:
            thinking_parts.append(inner)
    cleaned = _THINKING_TAG_RE.sub("", text)
    if strip_whitespace:
        cleaned = cleaned.strip()
    return cleaned, "\n\n".join(thinking_parts)

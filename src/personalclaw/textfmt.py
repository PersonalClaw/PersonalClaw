"""Generic LLM-text utilities — provider-agnostic, no channel semantics.

These helpers operate on raw model output and are consumed across the core
(subagent result cleanup, dashboard chat mirroring) independent of any delivery
channel. Channel-specific rendering (Slack mrkdwn, Block Kit) lives in the
channel's own bundle, not here.
"""

import re

# The retired ``[OPTIONS: choice1 | choice2]`` marker's EXACT shape — ONE contract,
# implemented on both sides of the wire. Kept CHARACTER-IDENTICAL to the frontend
# stripper's pattern (``web/src/pages/chat/parseAssistant.ts`` ``OPTIONS_PATTERN``);
# ``parseAssistant.test.ts`` reads THIS file and asserts the two strings match, so the
# two implementations of one contract cannot silently drift apart again (#540). Both
# languages agree on this source text: ``\s*$`` absorbs a trailing newline, so Python's
# ``$``-without-MULTILINE and JS's ``$``-without-``m`` accept exactly the same inputs.
#
# ANCHORED AT THE END OF THE INPUT, and `re.MULTILINE` is deliberately absent. With
# MULTILINE, ``$`` matched at end-of-LINE, so a marker sitting MID-text matched and
# ``text[: m.start()]`` then deleted everything after it:
# ``"Answer.\n[OPTIONS: A | B]\nMore prose."`` returned just ``"Answer."``. The frontend
# had the mirror-image defect from the other direction (a case-insensitive, singular-
# tolerant pattern that ate ordinary trailing prose) — same class of bug, opposite
# widening, which is what two hand-maintained copies of one contract produce.
# `[ \t]*` (not `\s*`) after the colon, and `[^\]\n]+` for the labels: the retired emitter
# put the marker on ONE line, so a bracketed phrase that WRAPS ("See [OPTIONS:\nnot a
# marker]") must not match. `\s*` there let the label run start on the next line.
_OPTIONS_PATTERN = r"\[OPTIONS:[ \t]*([^\]\n]+)\]\s*$"
_OPTIONS_RE = re.compile(_OPTIONS_PATTERN)

# Inline thinking tags some models embed in their text output.
_THINKING_TAG_RE = re.compile(
    r"<(?:thinking|antml:thinking)>.*?</(?:thinking|antml:thinking)>",
    re.DOTALL,
)


def extract_options(text: str) -> tuple[str, list[str]]:
    """Extract ``[OPTIONS: a | b | c]`` choices from LLM output and strip the tag.

    Returns ``(cleaned_text, choices)``. If no OPTIONS tag is present, ``choices``
    is empty and ``text`` is returned unchanged.

    The ``[OPTIONS: …]`` mechanism is RETIRED: nothing instructs the model to emit
    the marker anymore, and the chat UI renders suggestions from the
    ``chat_followups`` event instead. This stays as a TOLERANCE path — text
    persisted before the retirement (and any model that emits the marker
    unprompted) must not surface a raw tag to the user — so callers keep stripping
    it. Expect ``choices`` to be empty for freshly generated text.

    Only the TRAILING marker(s) are stripped, so prose is never truncated: a
    mid-message ``[OPTIONS: …]`` mention is left as written, and text following a
    marker is never deleted. STACKED trailing markers are all removed, making this
    idempotent — ``extract_options(extract_options(t)[0])[0] == extract_options(t)[0]``
    — so no raw tag can survive one pass. ``choices`` reports the LAST (operative)
    marker's labels.
    """
    cleaned = text
    choices: list[str] = []
    while True:
        m = _OPTIONS_RE.search(cleaned)
        if not m:
            break
        if not choices:
            choices = [c.strip() for c in m.group(1).split("|") if c.strip()]
        # Each pass strictly shortens `cleaned` (the match is non-empty), so this ends.
        cleaned = cleaned[: m.start()].rstrip()
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

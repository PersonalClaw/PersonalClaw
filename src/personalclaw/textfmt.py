"""Generic LLM-text utilities — provider-agnostic, no channel semantics.

These helpers operate on raw model output and are consumed across the core
(subagent result cleanup, dashboard chat mirroring) independent of any delivery
channel. Channel-specific rendering (Slack mrkdwn, Block Kit) lives in the
channel's own bundle, not here.
"""

import logging
import re

from personalclaw.security import redact_credentials, redact_exfiltration_urls

logger = logging.getLogger(__name__)

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


# ── Title-generation replies ───────────────────────────────────────────────────────────
#
# ONE parser for every surface that names a conversation from a model's reply: the dashboard's
# auto-title (``dashboard.chat_title``) and a channel app's thread titles. It is published on
# ``personalclaw.sdk.channel`` because the Slack app had to mirror it (#124), private in the
# dashboard module, with a parity test pinned to a core-private name.

#: A code-fence line (three or more backticks or tildes, with or without an info string). What a
#: fence encloses is code the model wrote instead of a title, never the title itself.
_FENCE_RE = re.compile(r"^\s*(?:`{3,}|~{3,})")

#: The tag line the dashboard's title call is asked to append. One definition serves both
#: parsers: the title parser skips it — a model that writes it first has not written a title —
#: and ``dashboard.chat_title._parse_tags_line`` reads it, bold label and all.
TAGS_LINE_RE = re.compile(r"^[\s*`]*tags[\s*`]*:[\s*`]*", re.IGNORECASE)

#: Markdown structure a model puts in front of a line: a heading, a quote, a bullet, a list number.
_LEAD_MARK_RE = re.compile(r"^(?:#{1,6}\s+|>\s*|[-*+•]\s+|\d{1,2}[.)]\s+)+")

#: A label a model echoes before the title. The bundled prompt ENDS on a ``Title:`` cue and the tag
#: instructions are appended after it, so a model mirroring the shape it was shown writes the label
#: back — 30 of 45 titles on a real model read ``Title: …``. A closed vocabulary rather than "any
#: words before a colon", so a real title that has a colon in it (``Movie Titles: Best of 2025``)
#: is left alone: only a run of lead-in words that ENDS in "title" is a label.
_LABEL_RE = re.compile(
    r"^\**\s*"
    r"(?:(?:sure|ok|okay|certainly)[!,.]?\s+)?"
    r"(?:(?:here's|here’s|here\s+is|a|an|the|my|suggested|proposed|short|brief|final|new|chat|"
    r"conversation|session)\s+){0,4}"
    r"(?:title(?:\s+(?:for|of)\s+(?:this|the)\s+(?:conversation|chat|session))?|topic|subject)"
    r"\s*\**\s*(?::|\s[-–—](?=\s))\s*\**\s*",
    re.IGNORECASE,
)

#: A whole line wrapped in one emphasis/code marker (``**X**``, ``*X*``, `` `X` ``).
_WRAPPED_RE = re.compile(r"(\*\*|\*|`)(.+?)\1")

#: Quote characters a model wraps a title in; single quotes only when they wrap BOTH ends, so an
#: apostrophe in the title itself survives.
_DOUBLE_QUOTES = '"“”«»「」'
_SINGLE_QUOTES = "'‘’"


def _unwrap(line: str) -> str:
    """*line* without enclosing ``**``/``*``/backtick pairs (nested ones too)."""
    while True:
        m = _WRAPPED_RE.fullmatch(line)
        if m is None:
            return line
        line = m.group(2).strip()


def _clean_title_line(line: str) -> str:
    """One reply line reduced to the words a title could be: structure, label and quotes removed.

    Returns ``""`` for a line that was ONLY scaffolding — a bare ``Chat title:`` label, whose title
    (if the model wrote one) is on a later line.
    """
    s = _unwrap(_LEAD_MARK_RE.sub("", line.strip()))
    label = _LABEL_RE.match(s)
    if label is not None:
        s = s[label.end() :]
    s = _unwrap(s.strip().rstrip("*").strip())
    s = s.strip(_DOUBLE_QUOTES).strip()
    if len(s) >= 2 and s[0] in _SINGLE_QUOTES and s[-1] in _SINGLE_QUOTES:
        s = s[1:-1].strip()
    return s.rstrip(".").strip()


def parse_title(text: str) -> str:
    """The title in a raw title-generation reply, or ``""`` when the reply holds no plausible one.

    ``""`` is the caller's cue to keep the title it already has (a dashboard chat's
    first-message snippet, an untitled thread) and retry on the next exchange — so junk is never
    stored. The first line that carries words is the candidate: fenced code, the tag line, bare
    labels, lead-ins ending in ``:`` and punctuation-only lines are scaffolding and are skipped
    to reach it; the candidate is then accepted or refused as a whole, never traded for a later
    line.
    """
    in_fence = False
    for raw in text.splitlines():
        if _FENCE_RE.match(raw):
            in_fence = not in_fence
            continue
        if in_fence or not raw.strip() or TAGS_LINE_RE.match(raw):
            continue
        title = _clean_title_line(raw)
        if title.endswith(":") or not any(ch.isalnum() for ch in title):
            continue
        return _accept_title(title)
    logger.info("Title generation returned no title line — keeping the current title")
    return ""


def _accept_title(title: str) -> str:
    """*title* sanitized, or ``""`` when it is a SKIP, a conversation continuation, or too long."""
    if title.upper() == "SKIP":
        logger.info("Title generation returned SKIP — topic not clear yet")
        return ""
    lower = title.lower()
    if lower.startswith(("user:", "assistant:")) or len(title) > 60:
        logger.info("Title generation rejected (looks like continuation): %r", title[:80])
        return ""
    title, _ = redact_exfiltration_urls(title)
    title, _ = redact_credentials(title)
    logger.info("Title generated: %r", title[:80])
    return title[:60]

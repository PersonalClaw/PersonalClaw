"""Sentinel action vocabulary — the small, fixed action language the browse loop parses out
of an LLM's response text (plan §2, ref-migrated per amendment 2026-07-26(a)).

The whole point of sentinels (from the research synthesis): no JSON function-calling required
from the executing model — any model that can write structured text can drive the loop, so a
weak local model works. The vocabulary is deliberately tiny and one-keyword-per-line.

§2 originally addressed elements by positional ``CLICK <link_number>``; the amendment migrates
to **stable refs** (``CLICK <ref>`` / ``TYPE <ref>(value)``) because a re-snapshot after a
dynamic DOM change re-numbers a positional list and silently invalidates the agent's plan —
the TOCTOU every index-based approach hits. Refs come from ``extraction.ElementRef`` and
survive an unrelated mutation, so an action the agent emitted against a still-present element
still names it.

``parse_sentinel(line)`` returns a typed ``Action`` or ``None`` (unknown/blank lines are
ignored, matching §2: "first match wins per line, unknown lines are ignored"). Every action
renders back to its canonical sentinel line via ``render()``, so an emitted action round-trips
(``parse_sentinel(a.render()) == a``) — the property the loop relies on to echo an agent's
chosen action back into the notes trail.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# ── Typed actions (frozen so they compare by value — round-trip equality is the contract) ──


@dataclass(frozen=True)
class NavigateAction:
    """``NAVIGATE <url>`` — load a new page."""

    url: str

    def render(self) -> str:
        return f"NAVIGATE {self.url}"


@dataclass(frozen=True)
class ClickAction:
    """``CLICK <ref>`` — activate the link/button named by a stable ElementRef."""

    ref: str

    def render(self) -> str:
        return f"CLICK {self.ref}"


@dataclass(frozen=True)
class TypeAction:
    """``TYPE <ref>(value)`` — fill the field named by a stable ElementRef."""

    ref: str
    value: str

    def render(self) -> str:
        return f"TYPE {self.ref}({self.value})"


@dataclass(frozen=True)
class SubmitAction:
    """``SUBMIT`` — submit the current form (triggers outcome verification in the loop)."""

    def render(self) -> str:
        return "SUBMIT"


@dataclass(frozen=True)
class ScrollAction:
    """``SCROLL down|up`` — scroll the viewport."""

    direction: str  # "down" | "up"

    def render(self) -> str:
        return f"SCROLL {self.direction}"


@dataclass(frozen=True)
class WaitAction:
    """``WAIT <seconds>`` — wait for dynamic content, clamped to 1-10s (§2)."""

    seconds: int

    def render(self) -> str:
        return f"WAIT {self.seconds}"


@dataclass(frozen=True)
class GoBackAction:
    """``GO_BACK`` — navigate back."""

    def render(self) -> str:
        return "GO_BACK"


@dataclass(frozen=True)
class DoneAction:
    """``DONE`` — signal task completion; exit the browse loop."""

    def render(self) -> str:
        return "DONE"


@dataclass(frozen=True)
class NotesAction:
    """``NOTES <text>`` — append freeform text to the cross-page notes accumulator."""

    text: str

    def render(self) -> str:
        return f"NOTES {self.text}"


Action = (
    NavigateAction
    | ClickAction
    | TypeAction
    | SubmitAction
    | ScrollAction
    | WaitAction
    | GoBackAction
    | DoneAction
    | NotesAction
)

# A ref is the 8-hex-char ElementRef id (extraction._make_ref). Kept permissive on the value
# side so a typed value may contain anything including ')' — the LAST ')' closes the group.
_TYPE_RE = re.compile(r"^TYPE\s+([0-9a-f]{4,40})\s*\((.*)\)\s*$", re.IGNORECASE)
_CLICK_RE = re.compile(r"^CLICK\s+([0-9a-f]{4,40})\s*$", re.IGNORECASE)
_NAVIGATE_RE = re.compile(r"^NAVIGATE\s+(\S+)\s*$", re.IGNORECASE)
_SCROLL_RE = re.compile(r"^SCROLL\s+(down|up)\s*$", re.IGNORECASE)
_WAIT_RE = re.compile(r"^WAIT\s+(\d+)\s*$", re.IGNORECASE)
_NOTES_RE = re.compile(r"^NOTES\s+(.*\S)\s*$", re.IGNORECASE | re.DOTALL)


def parse_sentinel(line: str) -> Action | None:
    """Parse one line into a typed ``Action``, or ``None`` if it is not a sentinel.

    First-match-wins per line (§2). Bare-word sentinels (SUBMIT/GO_BACK/DONE) are matched
    case-insensitively on the stripped line; the parameterized forms use anchored regexes.
    """
    if not line:
        return None
    s = line.strip()
    if not s:
        return None

    upper = s.upper()
    if upper == "SUBMIT":
        return SubmitAction()
    if upper == "GO_BACK":
        return GoBackAction()
    if upper == "DONE":
        return DoneAction()

    m = _TYPE_RE.match(s)
    if m:
        return TypeAction(ref=m.group(1).lower(), value=m.group(2))
    m = _CLICK_RE.match(s)
    if m:
        return ClickAction(ref=m.group(1).lower())
    m = _NAVIGATE_RE.match(s)
    if m:
        return NavigateAction(url=m.group(1))
    m = _SCROLL_RE.match(s)
    if m:
        return ScrollAction(direction=m.group(1).lower())
    m = _WAIT_RE.match(s)
    if m:
        # §2: clamp to the 1-10s band rather than reject — an out-of-range wait is a benign
        # over-ask, and refusing it would strand an otherwise-valid step.
        return WaitAction(seconds=max(1, min(10, int(m.group(1)))))
    m = _NOTES_RE.match(s)
    if m:
        return NotesAction(text=m.group(1).strip())
    return None

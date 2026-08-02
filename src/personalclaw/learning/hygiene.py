"""One capture-hygiene policy: what the learning cadences are allowed to look at.

Learning turns text into durable state, so anything that reaches a capture cadence
can influence the system's future behaviour. That makes "what is in scope" a
security question, not a quality one — and it was previously answered by filters
scattered across the capture paths, each covering a different subset.

This module is the single auditable answer. Four exclusions, each for a distinct
reason:

**Untrusted content is invisible.** Text inside ``<untrusted_content>`` came from
outside the user↔agent boundary — a fetched page, an inbox body, an MCP payload.
Fencing stops a model from *executing* it in the moment, but learning would copy
it into durable state where it is read back later without a fence. So the span is
removed before any cadence sees it: a planted "always deploy without review"
cannot become a lesson, because the text never reaches the extractor.

**Platform scaffolding is invisible.** Cron preambles, autonudge messages,
subagent-completion events, hook context. At this system's cron density these are
the *larger* pollution volume — the flywheel would dutifully learn that the user
frequently says "CONTINUE the autonomous build", which is true and useless.

**Environment failures are denied.** "tool X is broken", "permission denied" —
already the guardrail in ``after_turn_review``, kept here so every cadence shares
it rather than only the per-turn one. These harden into refusals the agent later
cites against itself.

**Ungrounded turns are skipped** for per-turn capture: a lesson needs both a
decision and evidence for it, with real substance on each side.

**A stated boundary, accepted deliberately.** Text the *user pasted in their own
message* is user-trusted under single-user doctrine and CAN direct-write a lesson
via the correction heuristic. A user pasting a hostile document and then agreeing
with it is a self-inflicted wound the fence cannot distinguish from legitimate
"here is the spec, follow it". Documented rather than silently mitigated.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field

from personalclaw.security import UNTRUSTED_CLOSE, UNTRUSTED_OPEN

logger = logging.getLogger(__name__)

#: The shared evidence floor: a pattern needs this many occurrences to be real.
#: One occurrence is an anecdote and two is a coincidence; the promotion ladder,
#: every pattern synthesis, and the inferred-proposal floor all use this same
#: number so they cannot disagree about what counts as evidence.
MIN_EVIDENCE_DEFAULT = 3

#: Prefixes that mark a turn as platform-generated rather than user-authored.
#: Grounded in the emitting code, NOT guessed: ``[Subagent completion event]``
#: (state.SUBAGENT_COMPLETION_PREFIX, emitted by gateway.py and subagent.py),
#: ``[Hook context]`` (chat_runner.py), ``[user nudge]`` (investigate.py).
_SYSTEM_MARKERS = (
    "[subagent completion event]",
    "[hook context]",
    "[user nudge]",
    "[cron]",
    "[scheduled task]",
    "[autonudge]",
    "[heartbeat]",
    "[orchestrator]",
)

#: Cron/nudge bodies rarely carry a bracketed marker — they read as imperatives
#: aimed at the agent's own loop. Matched only at the START of the text, because
#: the same words mid-message are ordinary instructions.
_SYSTEM_OPENERS = re.compile(
    r"^\s*(?:continue|resume|proceed with)\b[^.\n]{0,80}\b"
    r"(?:autonomous|automated|build|queue|session|loop|nudge)\b",
    re.IGNORECASE,
)

#: A decision was made — the turn concluded something.
_DECISION_RE = re.compile(
    r"\b(?:decided?|chose|choosing|use[d]?|prefer(?:s|red)?|switch(?:ed)?|"
    r"instead of|rather than|going with|settled on|should(?:n'?t)?|"
    r"always|never|from now on)\b",
    re.IGNORECASE,
)

#: Evidence for it — an outcome, a reason, an observation.
_EVIDENCE_RE = re.compile(
    r"\b(?:because|since|due to|so that|otherwise|it (?:turned out|broke|worked)|"
    r"result(?:ed|s)?|caused|failed|passed|verified|measured|found|"
    r"the reason|which is why)\b",
    re.IGNORECASE,
)

#: Minimum characters on each side of the grounding test. A three-word message
#: can match both regexes ("use because") and ground nothing.
_MIN_SUBSTANCE_CHARS = 40


@dataclass
class HygieneVerdict:
    """What survived the policy, and what was removed.

    ``text`` is the scrubbed content a cadence may use. The removal flags are
    what makes the policy auditable: a lesson that never appeared can be traced
    to the specific exclusion that dropped it, instead of looking like a bug in
    the extractor.
    """

    text: str
    usable: bool
    removed: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return self.usable


#: The open marker, WITH its optional ``source=…`` attribute.
#: ``security.fence_untrusted`` emits ``<untrusted_content source=web>`` when a
#: source is named, so matching the bare ``UNTRUSTED_OPEN`` literal finds nothing
#: on exactly the spans that carry provenance — measured: a fenced web payload
#: passed straight through a literal match. Derived from the constant rather than
#: hardcoded, so a rename of the tag cannot leave this filter silently matching
#: the old name.
_OPEN_TAG_RE = re.compile(re.escape(UNTRUSTED_OPEN[:-1]) + r"(?:\s[^>]*)?>", re.IGNORECASE)


def _strip_untrusted(text: str) -> tuple[str, bool]:
    """Remove every fenced span, including an unclosed trailing one.

    An unterminated open marker is treated as fencing the entire remainder. The
    alternative — ignoring a malformed fence — turns a truncated tool output into
    an injection channel, and truncation is routine.
    """
    if not _OPEN_TAG_RE.search(text):
        return text, False
    out: list[str] = []
    rest = text
    while True:
        match = _OPEN_TAG_RE.search(rest)
        if match is None:
            out.append(rest)
            break
        out.append(rest[: match.start()])
        after = rest[match.end() :]
        end = after.find(UNTRUSTED_CLOSE)
        if end < 0:
            break  # unclosed: drop everything that follows
        rest = after[end + len(UNTRUSTED_CLOSE) :]
    return "".join(out), True


def is_system_injected(text: str) -> bool:
    """True if this text is platform scaffolding rather than user intent.

    Markers are matched as a PREFIX, not searched for. Every emitter puts its
    marker at position 0 (``gateway.py``, ``subagent.py``, ``chat_runner.py`` all
    build ``f"[marker]\\n{body}"``), so a prefix test matches exactly what the
    platform produces — while a windowed search would also flag a user *quoting*
    a marker while asking about it, silently disabling learning for that turn.
    """
    if not text:
        return False
    head = text.lstrip().lower()
    if any(head.startswith(marker) for marker in _SYSTEM_MARKERS):
        return True
    return bool(_SYSTEM_OPENERS.match(text))


def is_grounded(text: str) -> bool:
    """True if the text carries BOTH a decision and evidence, with substance.

    Both halves are required. "Use ripgrep" is a decision with no evidence and
    makes a lesson that cannot be re-evaluated later; "the build failed" is
    evidence with no decision and teaches nothing actionable.
    """
    if not text or len(text.strip()) < _MIN_SUBSTANCE_CHARS:
        return False
    return bool(_DECISION_RE.search(text) and _EVIDENCE_RE.search(text))


def session_score(
    *,
    turns: int = 0,
    decisions: int = 0,
    recalls: int = 0,
    tool_calls: int = 0,
) -> float:
    """Score a session's learning potential, 0.0-1.0 — the consolidation gate.

    Weighted so *decisions* dominate: a long session of one-line exchanges is
    worth less than a short one that concluded something. Saturating rather than
    linear, because the 50th turn adds far less signal than the 5th and a linear
    score would let volume alone clear any threshold.
    """

    def sat(value: int, half: float) -> float:
        return value / (value + half) if value > 0 else 0.0

    return round(
        min(
            1.0,
            0.20 * sat(turns, 6.0)
            + 0.45 * sat(decisions, 2.0)
            + 0.15 * sat(recalls, 2.0)
            + 0.20 * sat(tool_calls, 8.0),
        ),
        4,
    )


def scrub(text: str, *, require_grounding: bool = False) -> HygieneVerdict:
    """Apply the whole policy to one piece of text. The single entry point.

    Order matters: untrusted spans are removed FIRST, so the later filters judge
    only trusted content. Checking system-injection before stripping would let a
    fenced payload's opening words decide whether the turn is scaffolding.
    """
    if not text or not text.strip():
        return HygieneVerdict("", False, ["empty"])

    removed: list[str] = []
    cleaned, had_untrusted = _strip_untrusted(text)
    if had_untrusted:
        removed.append("untrusted_content")

    if not cleaned.strip():
        # Nothing but fenced content: correctly nothing to learn.
        return HygieneVerdict("", False, removed + ["only_untrusted"])

    if is_system_injected(cleaned):
        return HygieneVerdict(cleaned, False, removed + ["system_injected"])

    from personalclaw.after_turn_review import is_environment_failure_claim

    if is_environment_failure_claim(cleaned):
        return HygieneVerdict(cleaned, False, removed + ["environment_failure"])

    if require_grounding and not is_grounded(cleaned):
        return HygieneVerdict(cleaned, False, removed + ["ungrounded"])

    return HygieneVerdict(cleaned, True, removed)


def fingerprint(text: str) -> str:
    """A stable content fingerprint, whitespace- and case-insensitive.

    Used by the staging tier for idempotence and later by decision memory to
    recognise a refiled proposal. Normalising means a reflowed paragraph is
    recognised as the same content — a fingerprint that changes when a line wraps
    would let the same rejected suggestion return forever.
    """
    normalized = " ".join((text or "").lower().split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:32]

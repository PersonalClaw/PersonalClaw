"""Tiered template matching — deterministic first, embeddings as a tie-breaker, LLM last.

The system this replaces made one embedding call and took the top cosine hit. That fails three
ways at once: it cannot explain itself, it cannot run offline, and a 0.51 winner beats a 0.49
runner-up with no acknowledgement that the two were indistinguishable.

**Five tiers, cheapest first, and every tier can answer alone.**

* **T1 — keyword index** over each template's `keywords[]`. Deterministic, offline, auditable, and
  free. Most intents are decided here.
* **T2 — metadata scoring** over tags, name, description and example OUTPUTS. Outputs matter more
  than descriptions: a user's intent resembles what they want to END UP with far more than it
  resembles prose about a workflow.
* **T3 — shape filter** from the intent classifier's tuple. A monitor-shaped intent should not
  match a one-shot template however well its words line up.
* **T4 — embeddings, demoted to TIE-BREAKER.** Used only when the top candidates are within
  `TIE_BAND`, which is the one case where the deterministic tiers genuinely cannot choose.
* **T5 — an LLM that RE-ENTERS the deterministic scorer.** It summarizes the intent; it never
  emits a template id. A model naming a template directly can name one that does not exist, and a
  fuzzy-resolved hallucination is worse than no match because it looks like an answer.

**Every decision carries a reason string.** A router nobody can audit is one nobody can correct,
and "why did it pick that?" is the first question anyone asks.

**Nothing here hard-fails offline.** T4 needs an embedder and T5 needs a model; both are optional
and their absence degrades to the tier below with a recorded reason.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

#: Candidates within this band of the leader are treated as indistinguishable, which triggers the
#: embedding tie-breaker and, failing that, hybrid composition. The plan's number.
TIE_BAND = 0.15

#: Confidence ceiling. Never 1.0: the matcher is choosing among templates a human wrote for
#: purposes it cannot fully know, and a router reporting certainty invites nobody to check it.
MAX_CONFIDENCE = 0.95

#: Below this, no template is offered at all — generation from scratch is the honest answer. A weak
#: match dressed up as a match is worse than none, because the user reviews a plan built on the
#: wrong shape instead of one built on nothing.
MIN_CONFIDENCE = 0.25

#: Tier weights. T1 outranks T2 because a keyword an author DECLARED is a stronger statement of
#: intent than a word that happens to appear in a description.
_W_KEYWORD = 1.0
_W_TAG = 0.6
_W_NAME = 0.5
_W_OUTPUT = 0.45
_W_DESCRIPTION = 0.25

#: Words too common to carry a match. Kept small on purpose: an aggressive stoplist silently
#: removes the one word that distinguished two templates.
_STOP = frozenset(
    {
        "the",
        "a",
        "an",
        "and",
        "or",
        "for",
        "to",
        "of",
        "in",
        "on",
        "with",
        "my",
        "me",
        "i",
        "it",
        "this",
        "that",
        "is",
        "are",
        "be",
        "do",
        "does",
        "please",
        "can",
        "you",
        "want",
        "need",
        "would",
        "should",
        "some",
        "any",
        "all",
        "from",
        "by",
        "at",
        "as",
    }
)


@dataclass
class Candidate:
    """One template's standing in a match."""

    name: str
    score: float = 0.0
    tier: str = ""
    reasons: list[str] = field(default_factory=list)
    #: Why this candidate was REJECTED, from its own `when_not_to_use`. Rendered in plan review so
    #: a near-miss explains itself instead of silently vanishing.
    rejected_because: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "score": round(self.score, 4),
            "tier": self.tier,
            "reasons": list(self.reasons),
            "rejected_because": self.rejected_because,
        }


@dataclass
class MatchResult:
    """What the matcher decided, and everything a review needs to argue with it."""

    primary: str = ""
    confidence: float = 0.0
    tier: str = "none"
    reason: str = ""
    alternates: list[Candidate] = field(default_factory=list)
    #: Two or three templates to COMPOSE as subworkflows, when the leaders were indistinguishable.
    #: Forcing an arbitrary winner there is how a router picks confidently and wrongly.
    compose: list[str] = field(default_factory=list)
    #: A template's declared cheaper route (direct answer / single subagent) for a trivial intent.
    lighter_path: str = ""
    #: Named starter parameterizations the matched template ships.
    presets: list[str] = field(default_factory=list)
    #: At most ONE, and only when the choice materially changes what runs.
    clarifying_question: str = ""

    @property
    def matched(self) -> bool:
        return bool(self.primary) and self.confidence >= MIN_CONFIDENCE

    def to_dict(self) -> dict[str, Any]:
        return {
            "primary": self.primary,
            "confidence": round(self.confidence, 4),
            "tier": self.tier,
            "reason": self.reason,
            "matched": self.matched,
            "alternates": [c.to_dict() for c in self.alternates],
            "compose": list(self.compose),
            "lighter_path": self.lighter_path,
            "presets": list(self.presets),
            "clarifying_question": self.clarifying_question,
        }


@dataclass
class TemplateProfile:
    """The matchable surface of one template. A projection, so the matcher stays pure and testable
    without a def registry."""

    name: str
    description: str = ""
    tags: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    #: Phrases describing what the template PRODUCES. Weighted near keywords because an intent
    #: resembles its desired output far more than it resembles prose about a workflow.
    example_outputs: list[str] = field(default_factory=list)
    #: Shapes this template serves (`monitor`, `review`, `triage`, `compare`). Empty = any.
    shapes: list[str] = field(default_factory=list)
    when_not_to_use: str = ""
    lighter_path: str = ""
    presets: list[str] = field(default_factory=list)
    match_text: str = ""

    @classmethod
    def from_def(cls, spec: Any) -> TemplateProfile:
        """Build from a `WorkflowDef` or a raw spec dict.

        Tolerant of both because bundled templates arrive as dicts and stored ones as objects, and
        a matcher that only understood one would silently score half the library at zero.
        """
        get = spec.get if isinstance(spec, dict) else lambda k, d=None: getattr(spec, k, d)
        raw_meta = get("metadata", {}) or {}
        # `metadata` is a TYPED `DefMetadata` on a WorkflowDef and a plain dict on a raw spec.
        # Measured: calling `.get()` on the dataclass silently returned nothing, so every bundled
        # template scored with zero keywords while the matcher reported matches anyway.
        if hasattr(raw_meta, "to_dict"):
            meta = raw_meta.to_dict()
        elif isinstance(raw_meta, dict):
            meta = raw_meta
        else:
            meta = {}
        return cls(
            name=str(get("name", "") or ""),
            description=str(get("description", "") or ""),
            tags=[str(t) for t in (get("tags", []) or [])],
            keywords=[str(k) for k in (meta.get("keywords") or [])],
            example_outputs=[str(o) for o in (meta.get("example_outputs") or [])],
            shapes=[str(s) for s in (meta.get("shapes") or [])],
            when_not_to_use=str(meta.get("when_not_to_use", "") or ""),
            lighter_path=str(meta.get("lighter_path", "") or ""),
            presets=[str(p) for p in (meta.get("presets") or [])],
            match_text=str(meta.get("match_text", "") or ""),
        )


def match_template(
    intent_text: str,
    profiles: list[TemplateProfile],
    *,
    shape: str = "",
    embedder: Any = None,
    summarizer: Any = None,
) -> MatchResult:
    """Match an intent to a template through the tier ladder.

    `embedder` and `summarizer` are INJECTED and both optional: T4 and T5 are enhancements, and a
    matcher that required either would hard-fail offline — which is the state a personal tool spends
    a meaningful fraction of its life in.
    """
    tokens = _tokens(intent_text)
    if not tokens or not profiles:
        return MatchResult(reason="nothing to match" if profiles else "no templates available")

    candidates = [_score(profile, tokens, intent_text) for profile in profiles]
    candidates = [c for c in candidates if c.score > 0]
    if not candidates:
        # T5's one legitimate job: an intent whose vocabulary simply does not overlap the library.
        # It re-enters THIS function with a rephrasing — it never returns a name of its own.
        if summarizer is not None:
            return _summarize_and_rematch(intent_text, profiles, shape=shape, embedder=embedder)
        return MatchResult(
            reason="no template shares any vocabulary with this intent — generating from scratch"
        )

    if shape:
        filtered = _apply_shape(candidates, profiles, shape)
        if not filtered:
            # Everything was shape-excluded: no template serves this shape. A legitimate no-match,
            # not a crash — `candidates[0]` on the empty list raised before this guard existed.
            return MatchResult(
                reason=(
                    f"no template serves a {shape}-shaped intent "
                    f"(closest: {candidates[0].name}) — generating from scratch"
                )
            )
        candidates = filtered

    candidates.sort(key=lambda c: (-c.score, c.name))
    leader = candidates[0]
    contenders = [c for c in candidates[1:] if leader.score - c.score <= TIE_BAND]

    if contenders and embedder is not None:
        broken = _break_tie(intent_text, [leader] + contenders, profiles, embedder)
        if broken is not None:
            leader = broken
            contenders = [c for c in candidates if c.name != leader.name][:3]

    result = MatchResult(
        primary=leader.name,
        tier=leader.tier,
        alternates=[c for c in candidates if c.name != leader.name][:3],
    )
    result.confidence = _confidence(leader, contenders, candidates)
    result.reason = f"{leader.tier}: {'; '.join(leader.reasons[:3])}"

    if contenders and embedder is None:
        # Indistinguishable leaders with no tie-breaker available: COMPOSE rather than guess.
        # Picking one at random and reporting it confidently is the failure this avoids.
        result.compose = [leader.name] + [c.name for c in contenders[:2]]
        result.reason += f"; tied with {', '.join(c.name for c in contenders[:2])} — composing"

    matched_profile = next((p for p in profiles if p.name == leader.name), None)
    if matched_profile is not None:
        result.lighter_path = matched_profile.lighter_path
        result.presets = list(matched_profile.presets)

    for alternate in result.alternates:
        profile = next((p for p in profiles if p.name == alternate.name), None)
        if profile is not None and profile.when_not_to_use:
            alternate.rejected_because = profile.when_not_to_use

    result.clarifying_question = _clarifying_question(leader, contenders, profiles)
    return result


# ── tiers ──


def _score(profile: TemplateProfile, tokens: set[str], raw_intent: str) -> Candidate:
    """T1 + T2 for one template. Deterministic and explainable by construction."""
    candidate = Candidate(name=profile.name)
    lowered = raw_intent.lower()

    keyword_hits = [k for k in profile.keywords if _matches(k, tokens, lowered)]
    if keyword_hits:
        candidate.score += _W_KEYWORD * len(keyword_hits)
        candidate.tier = "T1"
        candidate.reasons.append(f"keywords[{','.join(keyword_hits[:4])}]")

    tag_hits = [t for t in profile.tags if _matches(t, tokens, lowered)]
    if tag_hits:
        candidate.score += _W_TAG * len(tag_hits)
        candidate.tier = candidate.tier or "T2"
        candidate.reasons.append(f"tags[{','.join(tag_hits[:3])}]")

    name_tokens = _tokens(profile.name.replace("-", " "))
    name_overlap = tokens & name_tokens
    if name_overlap:
        candidate.score += _W_NAME * len(name_overlap)
        candidate.tier = candidate.tier or "T2"
        candidate.reasons.append(f"name[{','.join(sorted(name_overlap)[:3])}]")

    # Example OUTPUTS before description: a user says what they want to end up with, not what a
    # workflow is. Measured intent phrasing resembles outputs far more than prose.
    output_overlap = tokens & _tokens(" ".join(profile.example_outputs))
    if output_overlap:
        candidate.score += _W_OUTPUT * len(output_overlap)
        candidate.tier = candidate.tier or "T2"
        candidate.reasons.append(f"output-example[{','.join(sorted(output_overlap)[:3])}]")

    description_overlap = tokens & _tokens(f"{profile.description} {profile.match_text}")
    if description_overlap:
        candidate.score += _W_DESCRIPTION * len(description_overlap)
        candidate.tier = candidate.tier or "T2"
        candidate.reasons.append(f"description[{','.join(sorted(description_overlap)[:3])}]")

    return candidate


def _apply_shape(
    candidates: list[Candidate], profiles: list[TemplateProfile], shape: str
) -> list[Candidate]:
    """T3 — constrain by intent shape.

    A shape MISMATCH is a penalty, not an exclusion. A monitor-shaped intent should prefer a
    monitor template, but a library with no monitor template must still return its best answer
    rather than nothing — and templates that declare no shapes are shape-agnostic by construction,
    so they are never penalised.
    """
    by_name = {p.name: p for p in profiles}
    # Does the library HAVE a template for this shape at all? That decides whether the shape is a
    # hard constraint or a soft preference. Measured: a 0.6x penalty was not enough to unseat a
    # strong keyword hit, so "review the situation" with a monitor-shaped intent still matched the
    # review template — the classifier said monitor and the router ignored it.
    shape_exists = any(shape in (p.shapes or []) for p in profiles)

    out: list[Candidate] = []
    for candidate in candidates:
        profile = by_name.get(candidate.name)
        shapes = profile.shapes if profile else []
        if shapes and shape in shapes:
            candidate.score *= 1.5
            candidate.tier = "T3"
            candidate.reasons.append(f"shape[{shape}]")
        elif shapes and shape_exists:
            # A shape-specific template exists and this is not it: hard-exclude. Emitting a one-shot
            # for a monitor-shaped intent produces a run that answers once and never watches, which
            # is not a worse version of what was asked for — it is a different thing.
            candidate.score = 0.0
            candidate.reasons.append(f"shape-excluded[wants {'/'.join(shapes)}, intent is {shape}]")
        elif shapes:
            # No template serves this shape, so the best available answer still beats nothing.
            candidate.score *= 0.6
            candidate.reasons.append(f"shape-mismatch[wants {'/'.join(shapes)}]")
        out.append(candidate)
    # Zeroed candidates are dropped rather than ranked last: a shape-excluded template must not
    # surface as an "alternate" the user might pick.
    return [c for c in out if c.score > 0]


def _break_tie(
    intent_text: str,
    tied: list[Candidate],
    profiles: list[TemplateProfile],
    embedder: Any,
) -> Candidate | None:
    """T4 — embeddings, used ONLY on a tie.

    Returns None on any failure, and the caller keeps the deterministic leader. That is the whole
    demotion: the old system let a cosine number decide everything, including cases the keyword
    tiers had already answered correctly.
    """
    by_name = {p.name: p for p in profiles}
    try:
        from personalclaw.knowledge.retrieval import HybridRetriever

        intent_vector = embedder(intent_text)
        if not intent_vector:
            return None
        best, best_score = None, -1.0
        for candidate in tied:
            profile = by_name.get(candidate.name)
            if profile is None:
                continue
            text = profile.match_text or f"{profile.name} {profile.description}"
            vector = embedder(text)
            if not vector:
                continue
            score = HybridRetriever._cosine_similarity(intent_vector, vector)
            if score > best_score:
                best, best_score = candidate, score
        if best is None:
            return None
        best.tier = "T4"
        best.reasons.append(f"embedding tie-break ({best_score:.2f})")
        return best
    except Exception:
        logger.debug(
            "embedding tie-break unavailable — keeping the deterministic leader", exc_info=True
        )
        return None


def _summarize_and_rematch(
    intent_text: str,
    profiles: list[TemplateProfile],
    *,
    shape: str,
    embedder: Any,
) -> MatchResult:
    """T5 — an LLM rephrases the intent, and the DETERMINISTIC scorer decides.

    The model never returns a template id. A model naming a template can name one that does not
    exist, and fuzzy-resolving that hallucination produces a confident wrong answer — which is
    strictly worse than "no match", because the user then reviews a plan built on the wrong shape.
    """
    try:
        summary = str(_summarizer_result(intent_text) or "").strip()
    except Exception:
        logger.debug("T5 summarize failed — degrading to no-match", exc_info=True)
        return MatchResult(
            reason="rephrasing failed; no template matched — generating from scratch"
        )
    if not summary:
        return MatchResult(reason="rephrasing produced nothing; generating from scratch")

    # No `summarizer` passed through: re-entry is deterministic by construction, so T5 cannot
    # recurse into itself and spend a second call on the same dead end.
    result = match_template(summary, profiles, shape=shape, embedder=embedder)
    if result.primary:
        result.tier = "T5"
        result.reason = f"T5 (rephrased: {summary[:60]}) → {result.reason}"
        # Penalised: the match is against a MODEL'S restatement of the intent, not the user's own
        # words, and a paraphrase can drift.
        result.confidence = min(result.confidence, 0.6)
    return result


_summarizer_hook: Any = None


def _summarizer_result(intent_text: str) -> str:
    """Indirection so the summarizer is injectable in tests without a live model."""
    if _summarizer_hook is None:
        return ""
    return str(_summarizer_hook(intent_text) or "")


# ── confidence + questions ──


def _confidence(
    leader: Candidate, contenders: list[Candidate], all_candidates: list[Candidate]
) -> float:
    """Assembled from the candidate GAP and the raw score, clamped below 1.0.

    The gap carries most of the weight: a template scoring 3.0 with a 2.9 runner-up is a coin flip,
    while 1.2 against 0.2 is a clear answer at a lower raw score. Reporting the raw score as
    confidence would rank the coin flip higher.
    """
    runner_up = max((c.score for c in all_candidates if c.name != leader.name), default=0.0)
    gap = leader.score - runner_up

    # Measured: dividing the gap by the LEADER'S OWN score gave every uncontested match a full 0.5,
    # so confidence pinned at the 0.95 ceiling for a one-keyword hit and a five-signal hit alike —
    # a number that never varies carries no information, and a review that always reads "95%" trains
    # the user to ignore it.
    #
    # Both components now scale against the EVIDENCE. A declared keyword is worth ~1.0, so a
    # three-signal match reaches ~0.75 and a single weak description overlap sits near 0.35.
    # No free floor. Confidence is earned from evidence, because a leader that merely happened to be
    # first should not start at 0.2 — measured, that floor plus two saturating components pinned
    # every clean match at the 0.95 ceiling and made the number carry no information.
    gap_component = min(0.4, gap / 4.0)
    score_component = min(0.5, leader.score / 8.0)
    confidence = gap_component + score_component
    if contenders:
        confidence *= 0.8  # an unbroken tie is not a confident match
    return round(min(MAX_CONFIDENCE, confidence), 4)


def _clarifying_question(
    leader: Candidate, contenders: list[Candidate], profiles: list[TemplateProfile]
) -> str:
    """At most ONE question, and only when the choice materially changes what runs.

    Two templates that differ only in emphasis do not warrant interrupting the user — on low-risk
    ambiguity the router picks the likeliest and states its assumption in the plan. Asking about
    every near-tie trains the user to stop reading the questions.
    """
    if not contenders:
        return ""
    by_name = {p.name: p for p in profiles}
    leader_shapes = set(by_name.get(leader.name, TemplateProfile(name="")).shapes)
    for contender in contenders:
        contender_shapes = set(by_name.get(contender.name, TemplateProfile(name="")).shapes)
        if leader_shapes and contender_shapes and not (leader_shapes & contender_shapes):
            # Different SHAPES means genuinely different actions — worth one question.
            return (
                f"Did you want this run as {'/'.join(sorted(leader_shapes))} "
                f"({leader.name}) or {'/'.join(sorted(contender_shapes))} ({contender.name})?"
            )
    return ""


# ── text ──


def _tokens(text: str) -> set[str]:
    return {
        token for token in re.findall(r"[a-z0-9]{2,}", (text or "").lower()) if token not in _STOP
    }


def _matches(term: str, tokens: set[str], lowered: str) -> bool:
    """A declared term matches when its words are present.

    A single token must appear as a token. A multi-word keyword matches either as a literal PHRASE
    or when all of its CONTENT words are present — measured, the literal-only form made
    `"why did it fail"` miss "why did that run fail", which is the same question with one different
    word, and a keyword list that only fires on exact phrasing is a keyword list that mostly
    does not fire.

    Content words only (stopwords dropped) so "why ... fail" still carries the match, while the
    two-content-word minimum keeps "cold start" from firing on a cold drink and a race start —
    both words must be there, which a coincidence rarely satisfies.
    """
    term = (term or "").strip().lower()
    if not term:
        return False
    if " " not in term:
        return term in tokens
    if term in lowered:
        return True
    content = _tokens(term)
    return bool(content) and content <= tokens

"""Intent classification — a no-LLM heuristic that routes rigor before anything expensive runs.

Every planning decision downstream depends on one question: how much machinery does this intent
deserve? "Rename a variable" and "migrate the auth system" both arrive as a sentence, and spending
a deep grill on the first is as wrong as skipping it on the second.

**Keyword heuristics, zero tokens, offline-safe.** A model call here would put a cost and a
failure mode in front of every plan, including the ones whose whole answer is "this is trivial".
The tuple is also the bucketing key the LEARNING-FLYWHEEL uses for outcome learning, so it has to
be reproducible — the same intent must classify the same way next week, which a sampled model
cannot promise.

**Four dimensions, not one score.** `(complexity, uncertainty, stakes, time_pressure)` stay
separate because they route differently: complexity picks the shape, uncertainty triggers the
grill, stakes drive approval gating, and time pressure vetoes the deep path. Collapsing them into
"difficulty" loses exactly the distinctions the router needs — a simple, certain, high-stakes
change (rotate a production key) needs a gate and no grill, and one number cannot say that.

**The classifier reports its own confidence, and low confidence is a real answer.** An intent with
no signal at all is not "simple" — it is unclassified, and the router should ask rather than
assume.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Level(str, Enum):
    """One dimension's reading. Three levels, because five invites false precision from a
    keyword count and one collapses the distinction the dimension exists to make."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Rigor(str, Enum):
    """How much planning machinery the intent earns.

    `FAST` skips the grill entirely; `DEEP` runs the full protocol. `TRIVIAL` is the rung the
    plan calls `lighter_path` — a direct answer or one subagent, no run at all, because wrapping
    a one-line question in a workflow costs more than the question.
    """

    TRIVIAL = "trivial"
    FAST = "fast"
    STANDARD = "standard"
    DEEP = "deep"


#: Signals per dimension. Weighted so a single strong word cannot carry a dimension alone —
#: "just" appears in plenty of complex requests.
#: ACTION-scale complexity: verbs and shapes that describe how much WORK is involved. Only these
#: vote for the deep path, and only two DISTINCT ones do — measured, counting the domain noun
#: alongside the verb made "refactor the ingestion pipeline" (ordinary work) escalate to DEEP.
_COMPLEXITY_HIGH = (
    "migrate",
    "refactor",
    "rearchitect",
    "redesign",
    "rewrite",
    "overhaul",
    "port",
    "end-to-end",
    "orchestrate",
    "multi-step",
    "rollout",
)

#: Domain NOUNS that raise complexity but do not, alone, earn the deep path. They name the target;
#: `_COMPLEXITY_HIGH` names the scale of the work.
_COMPLEXITY_DOMAIN = (
    "pipeline",
    "infrastructure",
    "distributed",
    "integrate",
    "across",
    "architecture",
)
_COMPLEXITY_LOW = (
    "rename",
    "typo",
    "fix a typo",
    "one-line",
    "add a comment",
    "bump",
    "tweak",
    "explain",
    "look up",
    "remind me",
    "list",
    "show me",
    # Question forms. A question is not work, and the list previously had only "what is"/"what's" —
    # so "what does the frontier function do", the most natural phrasing, missed entirely.
    "what is",
    "what's",
    "what does",
    "what do",
    "where is",
    "where's",
    "how does",
    "how do i",
    "which one",
    "who owns",
    "when did",
)

_UNCERTAINTY_HIGH = (
    "figure out",
    "investigate",
    "explore",
    "research",
    "find out",
    "diagnose",
    "not sure",
    "unclear",
    "somehow",
    "maybe",
    "root cause",
    "unknown",
    "options",
    "compare",
    "evaluate",
    "decide whether",
    # Measured: the list had "why is"/"why does" but not the bare "why" or "look into", so "look
    # into why the sync job is slow" — a request entirely ABOUT not knowing — read as having no
    # uncertainty at all.
    "why",
    "look into",
    "dig into",
    "understand why",
    "what's causing",
    "whats causing",
)
_UNCERTAINTY_LOW = ("exactly", "specifically", "just", "simply", "as follows", "per the spec")

#: What makes the TARGET matter. Deliberately no destructive VERBS: "delete" and "drop" live only
#: in `_IRREVERSIBLE` below, because whether an action can be undone is a different claim from
#: whether its target is consequential. With them in both lists, "delete the scratch file" read as
#: high-stakes (HIGH wins a tie in `_dimension`) and routed DEEP — the `scratch` de-escalator was
#: unreachable for exactly the verbs that most need it.
_STAKES_HIGH = (
    "production",
    "prod",
    "customer",
    "billing",
    "payment",
    "credential",
    "secret",
    "password",
    "auth",
    "security",
    "irreversible",
    "migration",
    "public",
    "publish",
    "release",
    "deploy",
    "legal",
    "compliance",
    "pii",
)
_STAKES_LOW = ("draft", "sandbox", "scratch", "experiment", "local", "test", "prototype", "toy")

#: Irreversible actions. A SEPARATE signal from stakes, because the two route differently: high
#: stakes on a clear small action wants a gate, while an irreversible action wants the deep path
#: even when it is perfectly well specified. Measured: "delete every customer record older than two
#: years from the production billing database" routed FAST — it is not complex and not uncertain,
#: and its clarity is precisely what makes it dangerous. Nothing else in the tuple can say that.
_IRREVERSIBLE = (
    "delete",
    "drop",
    "purge",
    "wipe",
    "truncate",
    "revoke",
    "rotate",
    "terminate",
    "destroy",
    "remove all",
    "force-push",
    "reset --hard",
)

#: `quick` is deliberately ABSENT: "draft a quick note" is a size, not a deadline, and measured it
#: made every casual request read as urgent — which then vetoed the deep path for real work. The
#: phrases kept here cannot be read as anything but time pressure.
_TIME_PRESSURE = (
    "urgent",
    "asap",
    "right now",
    "immediately",
    "hotfix",
    "on fire",
    "outage",
    "blocking",
    "deadline",
    "by today",
    "before the end of day",
)

#: Breadth words. They CANCEL a simplicity signal rather than adding complexity: "rename x to y"
#: is trivial and "rename x to y everywhere it's used" is scoped work — the verb is the same and
#: the scope is not. Without this, the simplicity word won and a codebase-wide rename routed
#: TRIVIAL.
_BREADTH = ("everywhere", "every ", "all the", "across", "throughout", "each of", "whole")

#: Intents that pre-map to a fixed decision shape rather than an agentic loop. When one matches,
#: the planner emits a sequence/branch with `infer` nodes — an agentic loop for a question with a
#: known decision tree pays for exploration that was already done.
_DECISION_TREE_SHAPES = {
    "triage": ("triage", "which of these", "prioritize", "categorize", "route"),
    "review": ("review", "critique", "audit", "check whether", "verify"),
    "compare": ("compare", "versus", " vs ", "trade-off", "tradeoff", "which is better"),
    "monitor": ("monitor", "watch", "track", "keep an eye", "notify me when", "alert me"),
}


@dataclass
class Intent:
    """The classification, with everything the router and the flywheel need.

    `reason` is not decoration: it is what a plan review shows the user, and a routing decision
    nobody can see is one nobody can correct.
    """

    complexity: Level = Level.MEDIUM
    uncertainty: Level = Level.MEDIUM
    stakes: Level = Level.LOW
    time_pressure: Level = Level.LOW
    #: The action cannot be undone. Tracked separately from `stakes` because it routes differently:
    #: stakes decide whether to GATE, irreversibility decides whether to THINK first.
    irreversible: bool = False
    rigor: Rigor = Rigor.STANDARD
    confidence: float = 0.0
    shape: str = ""  # a pre-mapped decision-tree shape, when one matched
    reason: str = ""
    signals: dict[str, list[str]] = field(default_factory=dict)

    @property
    def tuple(self) -> tuple[str, str, str, str]:
        """The bucketing key. A tuple rather than a dict so it can key a counter directly."""
        return (
            self.complexity.value,
            self.uncertainty.value,
            self.stakes.value,
            self.time_pressure.value,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "complexity": self.complexity.value,
            "uncertainty": self.uncertainty.value,
            "stakes": self.stakes.value,
            "time_pressure": self.time_pressure.value,
            "irreversible": self.irreversible,
            "rigor": self.rigor.value,
            "confidence": round(self.confidence, 3),
            "shape": self.shape,
            "reason": self.reason,
            "signals": {k: list(v) for k, v in self.signals.items() if v},
        }


def classify(text: str) -> Intent:
    """Classify an intent. Pure, deterministic, and free.

    Deterministic matters as much as free: the tuple buckets outcome learning, so the same intent
    must classify identically next week. A sampled model cannot promise that, which would make the
    flywheel's buckets drift under it.
    """
    lowered = f" {' '.join((text or '').lower().split())} "
    if not lowered.strip():
        # An empty intent is UNCLASSIFIED, not simple. Defaulting it to trivial would send a
        # blank request down the cheapest path and produce a confident answer to nothing.
        # `rigor=STANDARD` here would read as a routing DECISION rather than the absence of one.
        # FAST is the honest floor for "we know nothing": it does the least on the strength of no
        # evidence, and the zero confidence tells the router to ask rather than proceed.
        return Intent(rigor=Rigor.FAST, confidence=0.0, reason="no intent text to classify")

    signals: dict[str, list[str]] = {}
    complexity = _dimension(lowered, _COMPLEXITY_HIGH, _COMPLEXITY_LOW, signals, "complexity")
    domain_hits = _hits(lowered, _COMPLEXITY_DOMAIN)
    if domain_hits:
        # Recorded for legibility and to raise a MEDIUM, but tracked separately so the two-signal
        # DEEP rule cannot count a target noun as a second unit of scale.
        signals.setdefault("domain", []).extend(domain_hits)
        if complexity == Level.MEDIUM:
            complexity = Level.HIGH
    uncertainty = _dimension(lowered, _UNCERTAINTY_HIGH, _UNCERTAINTY_LOW, signals, "uncertainty")
    stakes = _dimension(lowered, _STAKES_HIGH, _STAKES_LOW, signals, "stakes")
    pressure_hits = _hits(lowered, _TIME_PRESSURE)
    if pressure_hits:
        signals["time_pressure"] = pressure_hits
    time_pressure = Level.HIGH if pressure_hits else Level.LOW

    # Length is a signal on its own: a two-word intent is not complex, and a paragraph usually is,
    # regardless of vocabulary. It only NUDGES — a long simple request stays simple.
    breadth = _hits(lowered, _BREADTH)
    if breadth:
        signals.setdefault("complexity", []).extend(breadth)

    words = len(lowered.split())
    if words <= 4 and complexity == Level.MEDIUM and uncertainty != Level.HIGH:
        # NOT when uncertainty is high. "Investigate the flaky test" is four words and wide open;
        # marking it simple on length alone sent an open-ended investigation down the standard
        # path. Terseness is evidence about scope, and says nothing about how much is unknown.
        complexity = Level.LOW
        signals.setdefault("complexity", []).append(f"terse ({words} words)")
    elif words >= 25 and complexity in (Level.LOW, Level.MEDIUM):
        # Scope IS complexity evidence when the vocabulary does not name it. Measured: "delete
        # every customer record older than two years from the production billing database, after
        # checking the retention policy and confirming with legal" contains no complexity word and
        # routed FAST — a 25-word irreversible action taking the cheapest path. The nudge fires
        # from MEDIUM as well as LOW for exactly that case.
        complexity = Level.HIGH if words >= 40 else Level.MEDIUM
        signals.setdefault("complexity", []).append(f"long ({words} words)")

    irreversible = _hits(lowered, _IRREVERSIBLE)
    if irreversible:
        signals["irreversible"] = irreversible

    shape = _decision_shape(lowered)
    if shape:
        signals["shape"] = [shape]

    intent = Intent(
        complexity=complexity,
        uncertainty=uncertainty,
        stakes=stakes,
        time_pressure=time_pressure,
        irreversible=bool(irreversible),
        shape=shape,
        signals=signals,
    )
    intent.rigor = route_rigor(intent)
    intent.confidence = _confidence(signals, words)
    intent.reason = _reason(intent)
    return intent


def route_rigor(intent: Intent) -> Rigor:
    """Which planning path the tuple earns.

    Order matters and encodes the trade-offs:

    1. **High stakes never route trivial.** A one-word request to delete a production table is
       simple and consequential, and the cheap path has no gate to stop it.
    2. **Time pressure vetoes DEEP.** A deep grill on an outage is the right answer to the wrong
       question — the user needs a plan now, and thoroughness they cannot wait for is unhelp.
    3. **Complex AND uncertain earns DEEP.** Either alone does not: a complex-but-specified
       migration needs care, not exploration, and an uncertain one-liner needs a question.
    """
    if intent.time_pressure == Level.HIGH:
        # BEFORE the stakes branch. Measured: an outage on production ("production is on fire, fix
        # it now") routed DEEP, because stakes were checked first — a deep grill is the right
        # answer to the wrong question when the user needs a plan in the next minute. High stakes
        # still gate the RUN; they just do not buy a grill nobody can wait for.
        return Rigor.FAST

    if intent.irreversible and intent.stakes == Level.HIGH:
        # An irreversible high-stakes action earns the deep path even when it is perfectly clear.
        # Every other branch reasons about how much is UNKOWN; this one is about what cannot be
        # taken back, and a well-specified `DELETE FROM production` is the case where those two
        # readings diverge most.
        return Rigor.DEEP

    if intent.stakes == Level.HIGH:
        # High stakes means "gate it", not "grill it" — the gate is the protection, and a grill
        # would only delay a change the user has already decided on. But a COMPLEX high-stakes
        # change earns the deep path: that is where an unnoticed consequence actually hides.
        if _is_complex(intent):
            return Rigor.DEEP
        if intent.complexity == Level.LOW:
            return Rigor.FAST
        # A signal-less MEDIUM falls THROUGH to the ordinary path rather than short-circuiting to
        # FAST. Measured: "write the changelog entry for this release" matched `release` and got
        # LESS planning than "add a retry to the ingest queue" — high stakes should never buy a
        # request fewer steps than an unremarkable one.

    if intent.complexity == Level.LOW and _has_breadth(intent):
        # A LOW-complexity action applied BROADLY is mechanical but not trivial: "rename x to y
        # everywhere it's used" is a find-and-replace with a known answer over an unknown number of
        # sites. FAST is exactly that rung. Cancelling the simplicity signal to MEDIUM instead
        # overshot to STANDARD — the breadth is scope, not difficulty.
        return Rigor.FAST

    if intent.stakes == Level.LOW and intent.uncertainty != Level.HIGH and not _is_complex(intent):
        # An explicitly LOW-STAKES request is trivial on its own evidence: "draft a quick note in my
        # scratch dir" says plainly that nothing is at stake. Distinct from a SIGNAL-LESS request,
        # where stakes merely default low and the right reading is ordinary work — this branch needs
        # the negative signal to have actually fired.
        return Rigor.TRIVIAL

    if intent.complexity == Level.LOW and intent.uncertainty != Level.HIGH:
        # LOW, not merely not-complex. A signal-less MEDIUM is ORDINARY WORK, and measured on
        # unseen fixtures, treating it as trivial sent "add a retry to the ingest queue" and
        # "split the settings page into tabs" down the cheapest path — 68% routing accuracy
        # against an 85% bar. Absence of a complexity signal is not evidence of simplicity.
        #
        # Uncertainty is still `!= HIGH` rather than `== LOW`: "rename x to y" genuinely has no
        # uncertainty to signal, and requiring an explicit "exactly" would make this unreachable.
        return Rigor.TRIVIAL

    if intent.time_pressure == Level.HIGH:
        return Rigor.FAST

    if intent.complexity == Level.HIGH and intent.uncertainty == Level.HIGH:
        return Rigor.DEEP

    # Units of SCALE: action-scale verbs plus breadth words. A domain noun is deliberately not one
    # — it names the target, not the size of the job, and counting it made "refactor the ingestion
    # pipeline" escalate. Breadth IS a unit: "port the WHOLE pipeline" is sweeping in a way "port
    # the pipeline" is not.
    scale_signals = [
        hit
        for hit in (intent.signals.get("complexity") or [])
        if hit in _COMPLEXITY_HIGH or hit in _BREADTH or hit.strip() in _BREADTH
    ]
    if intent.complexity == Level.HIGH and len(scale_signals) >= 2:
        # HIGH complexity with MULTIPLE signals earns the deep path even when nothing is unknown.
        # Measured: "port the whole ingestion pipeline to the new provider interface" routed
        # STANDARD — a sweeping migration can be perfectly well understood and still be the thing
        # most worth planning carefully. One signal alone stays STANDARD, so a single strong word
        # ("refactor the ingestion pipeline") does not over-escalate.
        return Rigor.DEEP

    return Rigor.STANDARD


def _has_breadth(intent: Intent) -> bool:
    """Did a breadth word fire? Scope evidence, distinct from difficulty."""
    return any(
        hit in _BREADTH or hit.strip() in _BREADTH
        for hit in (intent.signals.get("complexity") or [])
    )


def _is_complex(intent: Intent) -> bool:
    """Does this intent carry real complexity, as opposed to merely lacking a simplicity signal?

    MEDIUM means two different things and they route differently: "no complexity signal fired"
    (which is evidence of a single well-specified action) versus "the length nudge moved it here"
    (which is evidence of scope). Treating both as complex sent every unremarkable one-line request
    down a path it did not need.
    """
    if intent.complexity == Level.HIGH:
        return True
    if intent.complexity == Level.LOW:
        return False
    return bool(intent.signals.get("complexity"))


def _dimension(
    text: str,
    high_words: tuple[str, ...],
    low_words: tuple[str, ...],
    signals: dict[str, list[str]],
    name: str,
) -> Level:
    """One dimension's level, from competing signals.

    HIGH wins a tie. The two error directions are not symmetric: over-classifying costs some
    unnecessary planning, while under-classifying skips a gate or a grill on something that needed
    one — so the tie goes to caution.
    """
    high = _hits(text, high_words)
    low = _hits(text, low_words)
    if high:
        signals.setdefault(name, []).extend(high)
    if low:
        signals.setdefault(name, []).extend(f"-{w}" for w in low)
    if high:
        return Level.HIGH
    if low:
        return Level.LOW
    return Level.MEDIUM


def _hits(text: str, words: tuple[str, ...]) -> list[str]:
    """Which signal words appear, matched on WORD BOUNDARIES.

    Substring matching would fire "prod" inside "produce" and "test" inside "latest" — and a
    stakes classifier that reads "produce a summary" as production work escalates every writing
    task to an approval gate.
    """
    found: list[str] = []
    for word in words:
        pattern = re.escape(word)
        # Multi-word phrases already carry their own boundaries; single tokens need them added.
        if " " not in word:
            pattern = rf"\b{pattern}\b"
        if re.search(pattern, text):
            found.append(word)
    return found


def _decision_shape(text: str) -> str:
    for shape, words in _DECISION_TREE_SHAPES.items():
        if _hits(text, words):
            return shape
    return ""


def _confidence(signals: dict[str, list[str]], words: int) -> float:
    """How much to trust this classification.

    Driven by how many DIMENSIONS produced a signal, not by how many words matched. Ten
    complexity signals and nothing else is still a guess about stakes, and counting raw hits
    would report that as high confidence.
    """
    dimensions = sum(1 for key in ("complexity", "uncertainty", "stakes") if signals.get(key))
    base = {0: 0.25, 1: 0.5, 2: 0.7, 3: 0.85}[min(3, dimensions)]
    if words <= 2:
        # Two words cannot carry a four-dimension reading however clear they look.
        base = min(base, 0.4)
    return base


def _reason(intent: Intent) -> str:
    """The one-line rationale a plan review shows. A routing decision nobody can see is one
    nobody can correct."""
    parts: list[str] = []
    if intent.irreversible:
        parts.append("irreversible")
    for name in ("complexity", "uncertainty", "stakes", "time_pressure"):
        level = getattr(intent, name)
        hits = intent.signals.get(name) or []
        if level != Level.MEDIUM or hits:
            shown = ", ".join(hits[:3]) if hits else "no signal"
            parts.append(f"{name}={level.value} ({shown})")
    if intent.shape:
        parts.append(f"shape={intent.shape}")
    return (
        f"rigor={intent.rigor.value}; " + "; ".join(parts)
        if parts
        else f"rigor={intent.rigor.value}"
    )

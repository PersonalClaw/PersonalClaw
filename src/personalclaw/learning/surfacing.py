"""One ranked slot allocator — what reaches the prompt, and in what shape.

Four entity families (lessons, skills, memory, retrieved context) each decided
independently how much prompt weight to take. That is not a budget; it is four
budgets that happen to share a window, and the visible consequence is in
`context.py`: the lesson block gets **character-truncated mid-sentence** when it
runs long. Lessons are the user's own corrections — the single most authoritative
thing in the prompt — and they were the block being cut, because whoever appended
last won.

**A budget is a ranking algorithm, not a token counter.** Counting tokens tells you
that you are over; it does not tell you what to drop. So:

**Per-entity thresholds stay as ENTRY gates.** The 0.55 skill gate was calibrated
for short descriptions and the routing gate at 0.62 for longer match text — the
code comments document the calibration. A single joint threshold would silently
recalibrate both. Named profiles carry the rationale forward; recalibration happens
when measurement shows the split unjustified, not when it looks untidy.

**One salience pool after the gates.** Candidates from every family compete on
`(0.55·query_overlap + 0.45·score) × 0.85^rank × entity_prior`, with priors near
1.0 — relevance must dominate source identity, or the pool becomes the old
per-family allocation wearing a ranking costume.

**Slots have priorities, and exactly one is sacrificial.** Truncation applies only
to retrieved context. Instructions and lessons are never crowded out. An oversized
item **skips rather than truncates** — half a lesson is worse than no lesson,
because the reader cannot tell it is half.

**Tiered rendering degrades before it drops.** Every entity carries L0 (one line),
L1 (operational summary), L2 (full body). The allocator lowers tier before removing
an item, and closes with an L0 catalogue of near-misses so the model knows what
exists and can ask — a dropped item the model never learns about is a silent gap.

**The authority preamble counters a measured failure.** Perfect injection, and the
agent re-searches everything anyway. Three lines stating that injected content is
authoritative over model priors, and that a question already answered here is not
novel.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)

# ── Entry gates: per-entity, calibrated, kept ──

#: Semantic thresholds per entity kind. These are NOT interchangeable — each was
#: calibrated against a different text profile, and the numbers come from the
#: existing code rather than being re-chosen here:
#:   skill  0.55 — short descriptions (skills/surfacing.DEFAULT_SEMANTIC_THRESHOLD)
#:   route  0.62 — longer match text (config agents_routing.min_confidence)
#: A single joint threshold would silently recalibrate both.
THRESHOLD_PROFILES: dict[str, float] = {
    "skill": 0.55,
    "lesson": 0.55,
    "memory": 0.55,
    "template": 0.62,
    "route": 0.62,
    "context": 0.50,
}

#: Entity priors, deliberately near 1.0. A prior far from 1.0 would let source
#: identity outrank relevance, which is the per-family allocation this replaces.
ENTITY_PRIORS: dict[str, float] = {
    "lesson": 1.05,
    "skill": 1.0,
    "memory": 1.0,
    "template": 0.98,
    "context": 0.95,
}

#: Salience weights. Query overlap slightly outweighs the entity's own score
#: because a high-scoring item about something else is still about something else.
W_QUERY_OVERLAP = 0.55
W_SCORE = 0.45
#: Rank decay within a source, so one source cannot fill the pool with its tail.
RANK_DECAY = 0.85
#: Reciprocal-rank-fusion constant, the standard k.
RRF_K = 60
#: Max items from any single source after fusion — diversification BEFORE trimming,
#: so a rich source cannot crowd out a sparse but relevant one.
MAX_PER_SOURCE = 3

#: Kinds EXEMPT from the diversification cap. Found by driving the real dev home:
#: the cap silently dropped a 4th lesson while 3588 of 4000 tokens sat unused.
#: Diversification exists to stop a *rich* source crowding out a sparse one — it was
#: never meant to ration the user's own corrections, which are the thing the whole
#: slot policy exists to protect. Lessons are bounded by the budget and the
#: `lessons` slot, not by a per-source quota.
#:
#: `skill` joined for the same reason (CE2-9). Once skill BODIES allocate here, the
#: candidates in a turn are the matched set the user's own triggers selected; a
#: per-source quota of 3 would drop the 4th before the budget was even consulted —
#: silently, since `fuse` runs before the allocator can catalogue a near-miss. Skills
#: are bounded by the aggregate cap and by each skill's DECLARED per-skill cap
#: (`Candidate.max_tokens`), which are visible decisions rather than a hidden quota.
UNCAPPED_KINDS = frozenset({"lesson", "skill"})

#: Kinds whose L2 (full body) is the DEFAULT rather than a grant.
#:
#: `L2_MAX_ITEMS`/`L2_SCORE_FRACTION` ration L2 because for a lesson or a memory the
#: full body is an expensive luxury over its summary. For a skill the full body IS the
#: payload — its steps are the thing the agent is meant to follow — so reduction is the
#: exception, not the norm. Rationing skill bodies to three per turn would make a
#: perfectly affordable fourth skill load reduced with budget to spare, which is the
#: "why did my skill not take effect" complaint wearing a budget costume. What bounds a
#: skill instead is its declared cap and the aggregate — both stated, both reported.
FULL_BODY_KINDS = frozenset({"skill"})


class Tier(str, Enum):
    """Rendering detail. The allocator degrades tier before dropping items."""

    L0 = "l0"  # one line — the catalogue entry
    L1 = "l1"  # operational summary
    L2 = "l2"  # full body


#: L2 is expensive: only for items very close to the top, and never many.
L2_SCORE_FRACTION = 0.9
L2_MAX_ITEMS = 3


@dataclass
class Candidate:
    """One thing that could be injected, at three levels of detail."""

    kind: str
    key: str
    score: float
    l0: str
    l1: str = ""
    l2: str = ""
    #: Deterministic surfacing signal: this candidate names a file the turn touched.
    path_match: bool = False
    source_rank: int = 0
    salience: float = 0.0
    tier: Tier = Tier.L1
    #: The candidate's OWN declared ceiling in tokens, independent of what is left in the
    #: budget (CE2-9). 0 = uncapped, which is every pre-existing caller. A tier whose
    #: rendering costs more than this is not offerable even on an empty budget, so a
    #: 40,000-token skill cannot spend a 200k window's slack just because it is first.
    #: Honored INSIDE `allocate` on purpose: "the allocator honors a cap per skill and in
    #: aggregate" is one mechanism enforcing two bounds, not a pre-filter plus a budget.
    max_tokens: int = 0
    #: WHICH match path produced this candidate (LEARN-R4 / §2.5 — S71). Attribution is what makes
    #: per-arm precision measurable: an exact-name hit and an embedding neighbour that both score
    #: 0.7 are not equally trustworthy, and "a single scalar can't be calibrated per-arm". Empty
    #: means unattributed, which `measure.per_arm_precision` charges to the WEAKEST arm rather
    #: than dropping — so an un-instrumented path reads as weak instead of vanishing from its
    #: own report.
    arm: str = ""

    def text(self, tier: Tier) -> str:
        if tier is Tier.L2:
            return self.l2 or self.l1 or self.l0
        if tier is Tier.L1:
            return self.l1 or self.l0
        return self.l0


@dataclass
class Slot:
    """One named region of the prompt, with a priority and a truncation policy."""

    name: str
    priority: int
    #: Only a sacrificial slot may be trimmed. Everything else is all-or-nothing.
    sacrificial: bool = False
    items: list[Candidate] = field(default_factory=list)


#: Slot order, taken from what `context.py build_session_context` already assembles
#: (stop notes → memory → working memory → persona → facets → skills → ephemeral
#: skills → lessons). That ordering becomes the contract, so four families can no
#: longer independently accrete weight.
SLOT_ORDER = (
    ("system", 0, False),
    ("constraints", 1, False),
    ("lessons", 2, False),
    ("skills", 3, False),
    ("memory", 4, False),
    # The ONLY sacrificial slot. Retrieved context is re-derivable next turn;
    # a truncated lesson is a corrupted instruction.
    ("retrieved_context", 5, True),
)


# ── Intent-adaptive weights ──

_DEBUG_RE = re.compile(
    r"\b(bug|broken|fail(?:ed|ing|ure)?|error|crash|traceback|stack ?trace|"
    r"regress(?:ion|ed)|why (?:is|does|did)|not working|debug)\b",
    re.IGNORECASE,
)
_IDEATION_RE = re.compile(
    r"\b(design|approach|options?|brainstorm|should we|trade-?offs?|architect|"
    r"alternatives?|what if|explore)\b",
    re.IGNORECASE,
)


def classify_intent(query: str) -> str:
    """debug | ideation | default — a lexical classification, zero cost.

    Cheap on purpose: this modulates weights, and paying a model call to decide how
    to weight a model call is a cost with no ceiling.
    """
    if not query:
        return "default"
    if _DEBUG_RE.search(query):
        return "debug"
    if _IDEATION_RE.search(query):
        return "ideation"
    return "default"


#: Per-intent weight profiles. Debugging wants the specific and the recent; ideation
#: wants the durable and the general.
INTENT_WEIGHTS: dict[str, dict[str, float]] = {
    "debug": {"overlap": 0.65, "score": 0.35, "path_bonus": 0.25},
    "ideation": {"overlap": 0.45, "score": 0.55, "path_bonus": 0.05},
    "default": {"overlap": W_QUERY_OVERLAP, "score": W_SCORE, "path_bonus": 0.15},
}


def query_overlap(query: str, text: str) -> float:
    """Token-overlap in [0,1]. No embedder required — the gates already used one
    if available, and this must work on the no-embedder path."""
    qa = {w for w in re.findall(r"[a-z0-9]+", (query or "").lower()) if len(w) > 2}
    ta = {w for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(w) > 2}
    if not qa or not ta:
        return 0.0
    return len(qa & ta) / len(qa)


#: The heuristics an ablation sweep can switch off, one at a time (§2.5's ablation-delta
#: rule: "every surfacing heuristic ships with a measured delta and is removed if ~0").
#:
#: Closed, and validated on the way in. A typo'd ablation name that silently ablated
#: nothing would report a delta of 0.0 — indistinguishable from a heuristic that does
#: nothing, which is the exact conclusion the sweep exists to draw.
ABLATABLE: tuple[str, ...] = (
    "intent",
    "path_bonus",
    "entity_prior",
    "rank_decay",
    "diversification",
)


def _check_ablate(ablate: str) -> str:
    if ablate and ablate not in ABLATABLE:
        raise ValueError(f"unknown ablation {ablate!r} — expected one of {ABLATABLE}")
    return ablate


def env_ablate() -> str:
    """The heuristic an ES-7 ablation CHILD was told to switch off, or ``""``.

    The §3.1 runner toggles a component through an overlay that exists only inside the
    spawned cell (:mod:`personalclaw.evals.overlay`) — never by editing live config. For a
    surfacing heuristic the toggle has nowhere in config to live, so it rides the child's
    own process env. Absent env var ⇒ ``""`` ⇒ the shipped path, byte for byte.
    """
    import os

    from personalclaw.evals.overlay import ABLATE_SURFACING_ENV

    return str(os.environ.get(ABLATE_SURFACING_ENV) or "")


def score_candidate(
    cand: Candidate, query: str, intent: str = "default", *, ablate: str = ""
) -> float:
    """Salience: relevance, rank-decayed, nudged by prior and path match.

    ``ablate`` switches ONE named heuristic off so its contribution can be measured.
    Threaded as a parameter rather than patched onto module globals because the sweep
    runs beside live traffic — a monkeypatched global would change the ranking of
    whatever turn happened to be in flight.
    """
    _check_ablate(ablate)
    if ablate == "intent":
        intent = "default"
    weights = INTENT_WEIGHTS.get(intent, INTENT_WEIGHTS["default"])
    overlap = query_overlap(query, f"{cand.l0} {cand.l1}")
    base = weights["overlap"] * overlap + weights["score"] * max(0.0, min(1.0, cand.score))
    decay = 1.0 if ablate == "rank_decay" else RANK_DECAY
    decayed = base * (decay ** max(0, cand.source_rank))
    prior = 1.0 if ablate == "entity_prior" else ENTITY_PRIORS.get(cand.kind, 1.0)
    salience = decayed * prior
    if cand.path_match and ablate != "path_bonus":
        # A candidate naming a file this turn touched is deterministically relevant;
        # no similarity score should be able to argue with that.
        salience += weights["path_bonus"]
    return round(salience, 6)


# ── Fusion and diversification ──


def _stronger_arm(left: str, right: str) -> str:
    """The more trustworthy of two match arms (S71).

    Compared by confidence rather than by name so the ordering follows the calibrated table instead
    of a second hard-coded precedence list. An empty arm loses to any real one — unattributed is the
    weakest possible evidence, not a tie.
    """
    if not left:
        return right
    if not right:
        return left
    from personalclaw.learning.measure import arm_confidence

    return left if arm_confidence(left) >= arm_confidence(right) else right


def fuse(sources: dict[str, list[Candidate]], *, ablate: str = "") -> list[Candidate]:
    """Reciprocal-rank fusion across sources, then per-source diversification.

    Diversification runs BEFORE trimming, deliberately: trimming first lets a rich
    source fill every slot and the diversification cap then has nothing to spread.
    """
    _check_ablate(ablate)
    scored: list[tuple[float, Candidate]] = []
    for _source, candidates in sources.items():
        for rank, cand in enumerate(candidates):
            cand.source_rank = rank
            scored.append((1.0 / (RRF_K + rank + 1), cand))

    fused: dict[str, tuple[float, Candidate]] = {}
    for rrf, cand in scored:
        key = f"{cand.kind}\x1f{cand.key}"
        prior = fused.get(key)
        if prior is None or rrf > prior[0]:
            # Carry the STRONGEST arm seen for this entity across the swap (S71). Measured: with two
            # sources finding the same entity at the same rank, RRF ties and the winner was
            # whichever source dict was iterated FIRST — so an entity matched by both exact-name
            # and embedding was attributed by insertion order, and the per-arm precision report
            # would credit the wrong path. Same rule `memory_push` already applies ("keep the
            # STRONGEST arm seen"): being named explicitly once is not undone by also being a
            # vector neighbour.
            if prior is not None:
                cand.arm = _stronger_arm(cand.arm, prior[1].arm)
            fused[key] = (rrf, cand)
        elif cand.arm:
            # The loser still carries evidence about HOW this entity was found; the survivor keeps
            # the stronger of the two so no attribution is lost to a tie it did not win.
            prior[1].arm = _stronger_arm(prior[1].arm, cand.arm)

    ordered = sorted(fused.values(), key=lambda pair: pair[1].salience, reverse=True)
    per_source: dict[str, int] = {}
    out: list[Candidate] = []
    for _rrf, cand in ordered:
        if cand.kind in UNCAPPED_KINDS or ablate == "diversification":
            out.append(cand)
            continue
        count = per_source.get(cand.kind, 0)
        if count >= MAX_PER_SOURCE:
            continue
        per_source[cand.kind] = count + 1
        out.append(cand)
    return out


# ── Token counting ──


def count_tokens(text: str) -> int:
    """Token count — tiktoken when installed, char/4 otherwise.

    The fallback is the live path here (tiktoken is not a dependency), so it is the
    one that has to be right. char/4 slightly OVER-estimates English prose, which
    is the safe direction for a budget.
    """
    if not text:
        return 0
    try:
        import tiktoken

        return len(tiktoken.get_encoding("cl100k_base").encode(text))
    except Exception:
        return max(1, (len(text) + 3) // 4)


# ── The allocator ──

#: The measured failure this counters: perfect injection, and the agent re-searches
#: everything anyway. Stated as rules rather than hints because a hedged preamble
#: gets treated as background.
AUTHORITY_PREAMBLE = (
    "The context below is AUTHORITATIVE and overrides your general priors.\n"
    "On conflict: the user's lessons win, then their stored preferences, then this "
    "session's context; your training is last.\n"
    "If something here already answers the question, do NOT re-derive or re-search it "
    "— cite it and act."
)


@dataclass
class Allocation:
    """What the allocator decided, and what it left out."""

    text: str
    used_tokens: int
    budget_tokens: int
    included: list[tuple[str, str, Tier]] = field(default_factory=list)
    #: Named, not silently dropped — an L0 catalogue of these is rendered so the
    #: model knows they exist and can ask.
    near_misses: list[str] = field(default_factory=list)
    skipped_oversized: list[str] = field(default_factory=list)
    degraded: list[str] = field(default_factory=list)
    truncated_slot: str = ""

    @property
    def headroom(self) -> int:
        return max(0, self.budget_tokens - self.used_tokens)


def _tier_fits(cost: int, used: int, budget: int, cap: int) -> bool:
    """Does one rendered tier fit BOTH bounds the allocator honors?

    Two bounds, one test (CE2-9): what is left of the shared budget (the aggregate), and
    what the candidate itself DECLARED it may spend (`Candidate.max_tokens`, 0 =
    uncapped). Separating them would mean two mechanisms deciding what reaches the
    prompt, which is the defect the allocator exists to remove — so a declared cap is
    enforced on the same line as the budget and degrades through the same ladder.
    """
    if used + cost > budget:
        return False
    return not (cap and cost > cap)


def allocate(
    sources: dict[str, list[Candidate]],
    *,
    query: str = "",
    budget_tokens: int = 4000,
    slot_order: tuple[tuple[str, int, bool], ...] = SLOT_ORDER,
    include_preamble: bool = True,
    ablate: str = "",
) -> Allocation:
    """Rank, tier, and fit — the one place prompt weight is decided.

    The order is load-bearing: score, fuse+diversify, assign to slots by priority,
    then degrade tier before dropping anything, and only ever trim the sacrificial
    slot. Reordering these produces a budget that is a token counter again.

    ``budget_tokens`` is the AGGREGATE bound; a candidate may additionally declare its
    own per-item ceiling in ``Candidate.max_tokens``. Both are checked by the same test
    (:func:`_tier_fits`) and both degrade through the same tier ladder, so "a cap per
    item and in aggregate" stays one mechanism (CE2-9).

    ``ablate`` names one heuristic to switch off, for `ablation_deltas`. Default "" falls
    back to :func:`env_ablate` (the ES-7 ablation child's process-scoped overlay) and is
    otherwise the live path, paying nothing for the parameter's existence.
    """
    # An explicit argument always wins; the env is consulted only when the caller named
    # nothing, so `ablation_deltas`' own sweep is unaffected and the live path (no env,
    # no argument) is unchanged.
    ablate = _check_ablate(ablate or env_ablate())
    intent = classify_intent(query)
    for candidates in sources.values():
        for cand in candidates:
            cand.salience = score_candidate(cand, query, intent, ablate=ablate)

    pool = fuse(sources, ablate=ablate)
    if not pool:
        return Allocation(text="", used_tokens=0, budget_tokens=budget_tokens)

    top = max((c.salience for c in pool), default=0.0)
    l2_granted = 0
    for cand in pool:
        if cand.l2 and cand.kind in FULL_BODY_KINDS:
            # Full body by default (see FULL_BODY_KINDS) and deliberately NOT counted
            # against `l2_granted`: sharing the grant budget would let three skills
            # starve a lesson of its L2, coupling two policies that answer different
            # questions. This candidate's ceiling is `max_tokens` + the budget.
            cand.tier = Tier.L2
        elif (
            cand.l2
            and l2_granted < L2_MAX_ITEMS
            and top > 0
            and cand.salience >= L2_SCORE_FRACTION * top
        ):
            cand.tier = Tier.L2
            l2_granted += 1
        else:
            cand.tier = Tier.L1

    slots = {name: Slot(name, priority, sacrificial) for name, priority, sacrificial in slot_order}
    slot_for = {
        "lesson": "lessons",
        "skill": "skills",
        "memory": "memory",
        "template": "skills",
        "context": "retrieved_context",
    }
    for cand in pool:
        slots.setdefault(
            slot_for.get(cand.kind, "retrieved_context"),
            Slot(slot_for.get(cand.kind, "retrieved_context"), 9, True),
        ).items.append(cand)

    allocation = Allocation(text="", used_tokens=0, budget_tokens=budget_tokens)
    rendered: list[str] = []
    used = 0
    if include_preamble:
        # Only if it FITS. Measured: the preamble is ~73 tokens, so adding it
        # unconditionally blew a 50-token budget before a single item was
        # considered — and a preamble with nothing under it is pure overhead
        # asserting authority over an empty block.
        preamble_cost = count_tokens(AUTHORITY_PREAMBLE)
        if preamble_cost <= budget_tokens:
            used += preamble_cost
            rendered.append(AUTHORITY_PREAMBLE)

    for slot in sorted(slots.values(), key=lambda s: s.priority):
        if not slot.items:
            continue
        block: list[str] = []
        for cand in sorted(slot.items, key=lambda c: c.salience, reverse=True):
            text = cand.text(cand.tier)
            cost = count_tokens(text)
            if not _tier_fits(cost, used, budget_tokens, cand.max_tokens):
                # Degrade before dropping: a lower tier may still fit.
                for lower in (Tier.L1, Tier.L0):
                    if lower is cand.tier:
                        continue
                    text = cand.text(lower)
                    cost = count_tokens(text)
                    # An EMPTY rendering is not a fit. A candidate that declared no
                    # lower tier renders "" here, which costs 0 and would "fit" — and
                    # the slot would gain a blank block that reads as a loaded item
                    # while carrying nothing. Skip the tier; if none render, the
                    # for/else below refuses and NAMES it, which is the honest outcome.
                    if not text:
                        continue
                    if _tier_fits(cost, used, budget_tokens, cand.max_tokens):
                        cand.tier = lower
                        allocation.degraded.append(cand.key)
                        break
                else:
                    # Even L0 does not fit. SKIP, never truncate — half a lesson is
                    # worse than none, because the reader cannot tell it is half.
                    if slot.sacrificial:
                        allocation.truncated_slot = slot.name
                    else:
                        allocation.skipped_oversized.append(cand.key)
                    # Catalogued either way: a dropped item the model never hears
                    # about is a silent gap it cannot ask about, and that is just as
                    # true for a skipped lesson as for a trimmed context item.
                    allocation.near_misses.append(cand.l0 or cand.key)
                    continue
            used += cost
            block.append(text)
            allocation.included.append((cand.kind, cand.key, cand.tier))
        if block:
            rendered.append("\n".join(block))

    # The L0 catalogue of what didn't fit: a dropped item the model never hears
    # about is a silent gap it cannot ask about.
    if allocation.near_misses:
        catalogue = "Also available on request (ask and it will be loaded):\n" + "\n".join(
            f"- {m}" for m in allocation.near_misses[:10]
        )
        if used + count_tokens(catalogue) <= budget_tokens:
            used += count_tokens(catalogue)
            rendered.append(catalogue)

    allocation.text = "\n\n".join(p for p in rendered if p)
    allocation.used_tokens = used
    return allocation


# ── Ablation-delta sweep (§2.5) ──

#: Below this, a heuristic changed nothing about what got injected. §2.5: "every
#: surfacing heuristic ships with a measured delta and is removed if ~0 — honest
#: reporting of null results is a feature." Not exactly zero: a delta of one position
#: in a twenty-item render is noise, not evidence of value.
NULL_DELTA = 0.02


def _signature(alloc: Allocation) -> list[tuple[str, str, str]]:
    """What this allocation injected, in order — the thing a delta compares."""
    return [(kind, key, tier.value) for kind, key, tier in alloc.included]


def _delta(baseline: list[tuple[str, str, str]], ablated: list[tuple[str, str, str]]) -> float:
    """Disagreement in [0, 1] between two injected sequences.

    Position-sensitive, because §2.4's position policy makes ORDER part of the
    outcome: the same items in a different order is a different render, and a
    set-difference metric would score that change as zero.
    """
    span = max(len(baseline), len(ablated), 1)
    agree = sum(1 for i in range(min(len(baseline), len(ablated))) if baseline[i] == ablated[i])
    return round(1.0 - agree / span, 6)


def ablation_deltas(
    sources: dict[str, list[Candidate]],
    *,
    query: str = "",
    budget_tokens: int = 4000,
) -> list[dict[str, Any]]:
    """Measure each heuristic's contribution by switching it off. Pure.

    Returns one row per heuristic in `ABLATABLE`, worst-earning first, each with the
    measured `delta` and a `verdict` of "earns_its_place" or "no_effect". A sweep that
    reports "no_effect" for a heuristic on real inputs is the evidence for deleting it
    — which is the point, and why the null result is reported rather than hidden.

    Deliberately NOT a per-turn measurement. Running five extra allocations on every
    turn to learn something that changes monthly is a cost with no matching benefit;
    the caller runs this on a cadence.
    """
    baseline = _signature(allocate(sources, query=query, budget_tokens=budget_tokens))
    rows: list[dict[str, Any]] = []
    for name in ABLATABLE:
        ablated = _signature(
            allocate(sources, query=query, budget_tokens=budget_tokens, ablate=name)
        )
        delta = _delta(baseline, ablated)
        rows.append(
            {
                "heuristic": name,
                "delta": delta,
                "verdict": "no_effect" if delta <= NULL_DELTA else "earns_its_place",
                "items": len(baseline),
            }
        )
    rows.sort(key=lambda row: row["delta"])
    return rows

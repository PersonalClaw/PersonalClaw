"""Skill bodies allocate on the ONE prompt budget (CONTEXT-ECONOMY CE2-9).

Before this module a matched skill's full body was **concatenated straight into the
prompt** — `context.py` did `parts.add(f"[Skill: {name}]\\n{body}\\n[End of skill]")` for
every forced and every surfaced skill, with nothing measuring the result. Two properties
followed, and both are defects:

* **Load order beat priority.** Whoever appended got the room. A 40,000-token skill did
  not compete with anything; it simply took the window and the conversation got what was
  left. The allocator (`learning/surfacing.allocate`) existed, declared a `skills` slot,
  and never saw a skill body — only the skill *index*.
* **Nothing was answerable.** "Why did my skill not take effect?" had no answer, because
  no decision was recorded anywhere. Either the body was in the prompt or the provider
  returned a 400.

So skill bodies now enter :func:`personalclaw.learning.surfacing.allocate` as candidates
in its existing ``skills`` slot, and the outcome is a **declared value** per skill in a
closed set of three (:class:`SkillLoadState`).

**Two caps, one mechanism.** Each skill declares its **resource tier** — the frontmatter
key is ``context_tier`` (:data:`CONTEXT_TIERS`) because "resource tier" already names
WF2LEA-10's ``resources:`` block in this very frontmatter, and one file cannot carry two
meanings of the phrase. That tier is the skill's **per-skill** ceiling, and the turn's
skills share an **aggregate** ceiling (:data:`AGGREGATE_CAP_TOKENS`). Both are honored
INSIDE the allocator by the same test (``surfacing._tier_fits``) and degrade through the
same tier ladder — a per-skill pre-filter beside a budget would be two mechanisms again,
which is precisely what this atom removes.

**Reduced, never truncated.** A body over its allocation is replaced by the skill's own
**declared** summary (`description`) and entry points (its `resources:` block) —
:func:`reduced_block`. A body cut at a byte boundary is worse than a shorter complete one:
the reader cannot tell it is half, and half a procedure is a procedure that fails at step
four. Nothing here ever slices a body.

**A skill that declares neither is REFUSED, not "reduced" to nothing.** There is no
summary to fall back to, and synthesizing one by taking the first N characters of the body
IS the byte-boundary cut. What loads instead is a one-line pointer
(:func:`pointer_line`) naming the skill and the tool that loads it on demand — an honest
refusal the agent can act on, reported as ``REFUSED`` because no part of the skill's
content reached the prompt.

**The decision is visible and observable.** Every non-admitted skill produces a notice
naming *which* skill and *why*, which rides CE2-8's existing assembly-notice channel
(``notices_out`` → ``AssembledContext.notices`` → ``activity_event {kind:"headroom"}``);
no new channel. The full triple (admitted / reduced / refused) is logged once per turn and
handed back as :attr:`SkillAllocation.decisions` for programmatic readers.

**Continued cost is re-evaluated, not paid once.** This runs inside `build_message`, which
`chat_runner` calls on **every** turn that has a context builder (`chat_runner.py:2041`),
not only on a new session. Nothing caches an admission: a skill admitted at full body on
turn one is re-scored against turn five's query and re-fitted against turn five's
aggregate, and is reduced the moment it no longer earns the room. That is a structural
property of allocating at the per-turn seam rather than a background sweep bolted on.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum

from personalclaw.learning.surfacing import Candidate, allocate, count_tokens
from personalclaw.skills.loader import SkillResource, SkillsLoader, parse_frontmatter

logger = logging.getLogger(__name__)

#: The declarable tiers and the per-skill token ceiling each one grants.
#:
#: MEASURED against the shipped library (17 bundled skills, `count_tokens`): median 1,183
#: tokens, largest 4,202 (`visual-output`), total 23,025. So:
#:   light    1,000 — below the median: a one-page skill that states a rule or a format.
#:   standard 3,000 — clears 16 of the 17 bundled skills. The DEFAULT, so a skill that
#:                    declares nothing still gets a real allocation rather than a token.
#:   heavy    8,000 — nearly twice the largest thing we ship. A skill only spends this by
#:                    SAYING so, which is the whole point: the cost is declared, and a
#:                    reviewer reading the frontmatter can see which skills are expensive.
#: Numbers, not a knob: the atom asks for a declared tier per skill, and a global override
#: would let one config edit re-crowd the window that the tiers exist to protect.
CONTEXT_TIERS: dict[str, int] = {"light": 1_000, "standard": 3_000, "heavy": 8_000}

#: Tier assumed when a skill declares none, or declares one we do not know. Fail-OPEN
#: (a real allocation) rather than fail-closed (`light`): a typo in frontmatter must not
#: silently reduce a skill the user relies on. The unknown value is logged.
DEFAULT_TIER = "standard"

#: The aggregate ceiling for ALL skill bodies in one turn.
#:
#: 8 is the progressive-disclosure threshold (`SkillsConfig.progressive_disclosure_
#: threshold`) — above it only an index loads, so 8 is the most bodies a turn ever
#: carries. At the measured median all 8 cost 9,464 tokens and nothing is reduced; at the
#: `standard` cap they would want 24,000. 16,000 is therefore the point where the
#: aggregate starts to bind exactly when several skills are simultaneously near their own
#: ceiling — which is the crowd-out case — and never in the ordinary one.
#:
#: Deliberately model-BLIND. `build_message` is synchronous and holds no model ref, and
#: CE2-8's contract already measures the assembled prompt against the bound model's real
#: window downstream. This cap's job is to stop skills being unbounded in absolute terms;
#: fitting the specific window is the headroom contract's job, and duplicating it here
#: would be the second budget mechanism this atom deletes.
AGGREGATE_CAP_TOKENS = 16_000

#: Salience floor for a skill the user explicitly confirmed (a goal loop's `skill_ids`).
#: 1.0 — above every passively surfaced match, because the user picked it in Plan Review.
FORCED_SCORE = 1.0

#: Salience floor for a passively surfaced skill (trigger/semantic match).
SURFACED_SCORE = 0.9

#: Entry points listed in a reduced form before it says "and N more". A reduced skill has
#: to stay readable; forty paths is a directory listing, not a summary.
MAX_ENTRY_POINTS = 8


class SkillLoadState(str, Enum):
    """What the allocator decided about one skill. Closed at three, per turn.

    ``ADMITTED`` — the full body reached the prompt.
    ``REDUCED``  — the skill's own declared summary/entry points reached the prompt
                   instead of its body. Nothing was truncated.
    ``REFUSED``  — none of the skill's content reached the prompt. It is still NAMED — by
                   a pointer line when even that fits, and always in the turn's notice —
                   so the agent can load it on demand rather than never learning it
                   matched.

    A fourth state would make "why did my skill not take effect" un-exhaustive again.
    """

    ADMITTED = "admitted"
    REDUCED = "reduced"
    REFUSED = "refused"


@dataclass(frozen=True)
class SkillRequest:
    """One skill this turn asked for, with the text already read.

    ``content`` is the raw SKILL.md (frontmatter included) the caller loaded, passed in
    rather than re-read here: `load_skill` renders the accepted-refinement overlay, and
    reading it twice per turn would double that work and could observe two different
    files.
    """

    name: str
    content: str
    score: float = SURFACED_SCORE
    forced: bool = False


@dataclass(frozen=True)
class SkillDecision:
    """The allocator's verdict on one skill, with the numbers behind it."""

    name: str
    state: SkillLoadState
    #: The `context_tier` this skill DECLARED (or the default that stood in for it).
    tier: str
    #: The per-skill ceiling that tier grants.
    cap_tokens: int
    #: What the full body would have cost.
    body_tokens: int
    #: What actually reached the prompt (0 when nothing did).
    loaded_tokens: int
    #: Why, in words, when the state is not ADMITTED. "" when it is.
    reason: str = ""
    forced: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "state": self.state.value,
            "tier": self.tier,
            "cap_tokens": self.cap_tokens,
            "body_tokens": self.body_tokens,
            "loaded_tokens": self.loaded_tokens,
            "reason": self.reason,
            "forced": self.forced,
        }


@dataclass
class SkillAllocation:
    """What one turn's skill allocation produced."""

    #: ``(skill name, block text)`` in the allocator's chosen order. Each becomes its own
    #: labelled `skill: <name>` component, so CE2-8 can name it in a refusal.
    blocks: list[tuple[str, str]] = field(default_factory=list)
    decisions: list[SkillDecision] = field(default_factory=list)
    #: One human-readable line per non-admitted skill, for the assembly notice channel.
    notices: list[str] = field(default_factory=list)
    used_tokens: int = 0
    budget_tokens: int = 0

    @property
    def loaded(self) -> list[str]:
        """Skills whose content reached the prompt — the use counter's input.

        Reduced counts as loaded: its declared summary is in the prompt and the agent can
        act on it. Refused does not: nothing of it was used.
        """
        return [
            d.name
            for d in self.decisions
            if d.state in (SkillLoadState.ADMITTED, SkillLoadState.REDUCED)
        ]

    @property
    def counts(self) -> dict[str, int]:
        """The closed triple, every state present even at zero.

        Present-at-zero on purpose: a report that omits empty states cannot be read as
        "nothing was refused" — it reads identically to "refusals are not counted".
        """
        out = {state.value: 0 for state in SkillLoadState}
        for decision in self.decisions:
            out[decision.state.value] += 1
        return out

    @property
    def summary(self) -> str:
        counts = self.counts
        return (
            f"{counts['admitted']} admitted, {counts['reduced']} reduced, "
            f"{counts['refused']} refused — {self.used_tokens:,}/{self.budget_tokens:,} tokens"
        )


def resolve_tier(declared: str, *, name: str = "") -> str:
    """The tier a skill declared, normalized. Unknown or absent → :data:`DEFAULT_TIER`."""
    key = (declared or "").strip().lower()
    if not key:
        return DEFAULT_TIER
    if key in CONTEXT_TIERS:
        return key
    logger.warning(
        "skill %r declares unknown context_tier %r — allocating as %r",
        name or "?",
        declared,
        DEFAULT_TIER,
    )
    return DEFAULT_TIER


def full_block(name: str, body: str) -> str:
    """The admitted form — byte-identical to what `context.py` concatenated before."""
    return f"[Skill: {name}]\n{body}\n[End of skill]\n\n"


def reduced_block(name: str, summary: str, resources: list[SkillResource]) -> str:
    """The REDUCED form: the skill's DECLARED summary and entry points, complete.

    Returns ``""`` when the skill declared neither — there is nothing to reduce TO, and
    inventing a summary from the body's first N characters is the byte-boundary cut this
    atom exists to refuse. The caller reports that as REFUSED with the reason named.
    """
    if not summary and not resources:
        return ""
    lines = [f"[Skill: {name} — REDUCED to its declared summary; the full body is over budget]"]
    if summary:
        lines.append(summary)
    if resources:
        lines.append(f'Entry points (read with skill_resource(skill="{name}", path="…")):')
        for res in resources[:MAX_ENTRY_POINTS]:
            lines.append(f"- {res.path}: {res.description}" if res.description else f"- {res.path}")
        if len(resources) > MAX_ENTRY_POINTS:
            lines.append(f"- …and {len(resources) - MAX_ENTRY_POINTS} more")
    lines.append(
        f"The steps are NOT loaded. Call skill_invoke{{{name}}} for the complete body "
        "before following this skill."
    )
    lines.append("[End of skill]")
    return "\n".join(lines) + "\n\n"


def pointer_line(name: str, summary: str) -> str:
    """The refusal form: names the skill and the way to load it. Never its content.

    This — not the allocator's near-miss catalogue — is how a refused skill stays audible
    in the prompt, and the reason is arithmetic rather than taste. The catalogue renders
    only when ``used + tokens(catalogue) <= budget`` while the item's own L0 already
    failed ``used + tokens(l0) <= budget``, and the catalogue is L0 plus a header, so for
    any candidate with a non-empty L0 those two conditions cannot both hold. MEASURED on a
    representative pointer: 43 tokens for the line, 57 for the catalogue that would carry
    it. A catalogue block on the skill path would therefore have shipped inert; the
    per-skill pointer lands exactly where the skill would have been and says more.
    """
    tail = f" — {summary}" if summary else ""
    return (
        f"[Skill: {name} MATCHED but NOT LOADED (over the context budget){tail}. "
        f"Call skill_invoke{{{name}}} to load it.]\n\n"
    )


def allocate_skills(
    loader: SkillsLoader,
    requests: list[SkillRequest],
    *,
    query: str = "",
    budget_tokens: int | None = None,
) -> SkillAllocation:
    """Fit this turn's skill bodies through the one allocator. Never raises.

    ``requests`` is in caller priority order (forced skills first); ``score`` is what the
    candidate competes on, so a confirmed skill outranks a passively surfaced one at equal
    query overlap rather than by virtue of being appended first.
    """
    budget = AGGREGATE_CAP_TOKENS if budget_tokens is None else max(0, budget_tokens)
    result = SkillAllocation(budget_tokens=budget)
    cands: list[Candidate] = []
    facts: dict[str, tuple[SkillRequest, str, int, Candidate]] = {}
    # Ordered by DECLARED score before the pool sees them, and this is load-bearing.
    # `fuse` stamps `source_rank` from position and `score_candidate` decays salience by
    # `RANK_DECAY ** source_rank`, so a caller that appended a confirmed skill second would
    # hand a 0.9-score guess (rank 0, decay 1.0) a higher salience than a 1.0-score
    # confirmation (rank 1, decay 0.85) — MEASURED at 0.405 vs 0.383. That is load order
    # beating priority again, one layer down, which is the exact defect this atom removes.
    # Sorting here makes rank decay a tie-breaker among EQUAL declarations instead of a
    # second ranking; `sorted` is stable, so equal scores keep the caller's order.
    requests = sorted(requests, key=lambda r: -r.score)
    for req in requests:
        body = SkillsLoader.strip_frontmatter(req.content).strip()
        if not body:
            continue
        meta = parse_frontmatter(req.content)
        tier = resolve_tier(meta.get("context_tier", ""), name=req.name)
        cap = CONTEXT_TIERS[tier]
        summary = " ".join((meta.get("description") or "").split())
        try:
            resources = loader.resources_for(req.name)
        except Exception:  # pragma: no cover - resources_for is already never-raise
            logger.debug("resources_for(%r) failed", req.name, exc_info=True)
            resources = []
        cand = Candidate(
            kind="skill",
            key=req.name,
            score=req.score,
            l0=pointer_line(req.name, summary),
            l1=reduced_block(req.name, summary, resources),
            l2=full_block(req.name, body),
            max_tokens=cap,
            arm="skill_forced" if req.forced else "skill_surfaced",
        )
        cands.append(cand)
        facts[req.name] = (req, tier, cap, cand)
    if not cands:
        return result

    alloc = allocate(
        {"skills": cands},
        query=query,
        budget_tokens=budget,
        # The authority preamble is the ambient block's (`learning.ambient.render`), which
        # already states it once per prompt. A second copy would spend ~73 tokens of the
        # skills budget asserting authority the prompt has already asserted.
        include_preamble=False,
    )
    result.used_tokens = alloc.used_tokens

    seen: set[str] = set()
    for _kind, key, _tier in alloc.included:
        req, tier, cap, cand = facts[key]
        seen.add(key)
        text = cand.text(cand.tier)
        # Classified by the text that REALLY reached the prompt, not by `cand.tier`.
        # `Candidate.text` falls back down the chain (`l1 or l0`), so a skill with no
        # declared summary is marked tier L1 while rendering the L0 pointer — trusting the
        # tier label would report that as a REDUCED load of content that is not there.
        if text == cand.l2:
            state, reason = SkillLoadState.ADMITTED, ""
        elif cand.l1 and text == cand.l1:
            state = SkillLoadState.REDUCED
            reason = _why_not_full(count_tokens(cand.l2), cap, tier, budget)
        else:
            state = SkillLoadState.REFUSED
            reason = (
                f"it declares neither a `description` nor a `resources:` block, so it has "
                f"no summary to reduce to, and its {count_tokens(cand.l2):,}-token body is "
                f"over the {cap:,}-token cap its `{tier}` context tier declares"
                if not cand.l1
                else f"not even its declared summary fit the remaining skill budget "
                f"({budget:,} tokens for all skills this turn)"
            )
        result.blocks.append((key, text))
        result.decisions.append(
            SkillDecision(
                name=key,
                state=state,
                tier=tier,
                cap_tokens=cap,
                body_tokens=count_tokens(cand.l2),
                loaded_tokens=count_tokens(text),
                reason=reason,
                forced=req.forced,
            )
        )
    for name, (req, tier, cap, cand) in facts.items():
        if name in seen:
            continue
        result.decisions.append(
            SkillDecision(
                name=name,
                state=SkillLoadState.REFUSED,
                tier=tier,
                cap_tokens=cap,
                body_tokens=count_tokens(cand.l2),
                loaded_tokens=0,
                reason=(
                    f"nothing of it fit the {budget:,}-token aggregate skill budget for "
                    "this turn"
                ),
                forced=req.forced,
            )
        )
    result.decisions.sort(key=lambda d: (d.state != SkillLoadState.ADMITTED, d.name))
    for decision in result.decisions:
        if decision.state is SkillLoadState.ADMITTED:
            continue
        result.notices.append(_notice(decision))
    # Observable per turn, in all three states, whether or not anything went wrong: a
    # report that only appears on a problem cannot answer "did my skill load?".
    logger.info("skill allocation: %s", result.summary)
    return result


def _why_not_full(body_tokens: int, cap: int, tier: str, budget: int) -> str:
    """Which of the two caps actually bound this skill. Naming the wrong one misleads."""
    if body_tokens > cap:
        return (
            f"its body is {body_tokens:,} tokens, over the {cap:,}-token cap its `{tier}` "
            f"context tier declares"
        )
    return (
        f"its body is {body_tokens:,} tokens and fits its `{tier}` tier, but the turn's "
        f"skills had already used the {budget:,}-token aggregate budget"
    )


def _notice(decision: SkillDecision) -> str:
    """The user-visible sentence for one non-admitted skill. Names the skill AND why."""
    if decision.state is SkillLoadState.REDUCED:
        return (
            f'Skill "{decision.name}" loaded in REDUCED form (its declared summary and '
            f"entry points, not its steps): {decision.reason}. "
            f"Call skill_invoke{{{decision.name}}} for the full body."
        )
    return (
        f'Skill "{decision.name}" was NOT loaded: {decision.reason}. '
        f"Call skill_invoke{{{decision.name}}} to load it explicitly."
    )

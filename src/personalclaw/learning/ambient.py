"""The ambient render, put under ONE budget (§2.4 / §7 crit 5 — S80).

§7's criterion 5: "the lesson block, skill INDEX, template suggestion, voice/facet blocks, and
self-model snapshot fit ONE per-turn slot-allocated token budget; lessons are never crowded out
(sacrificial-slot truncation only); the authority preamble renders."

S71 built the allocator (`learning/surfacing.py`) and deliberately stopped short of owning the
render, recording why: "replacing the whole render is behaviour-visible and needs §2.5's measurement
floor to prove nothing stops surfacing." That floor landed in S71's own measure module. This is the
adapter that finally routes the named blocks through it.

**Measured before writing — three inert controls, not one.**

1. `allocate()` and `AUTHORITY_PREAMBLE` had ZERO callers outside their own module and tests.
   The ranking algorithm existed; nothing ranked.
2. `learning.context_budget_tokens` is a fully round-tripped config knob (dataclass + `_meta` +
   `load()` + `to_dict()` + `_EDITABLE_CONFIG`) that NOTHING read. Its help text promises "only
   retrieved context is ever trimmed" — a promise no code kept.
3. The blocks were bounded by per-block CHARACTER caps that sum to ~36,750 tokens against a declared
   4,000-token budget — 9x. Driven with 120 realistic lessons the render passes the budget by 1,576
   tokens; at 400+ it reaches 10,101 tokens, 2.5x, with no joint check anywhere. On the same
   input the allocator holds 3,999 and keeps the query-relevant lesson.

**Two measurements that decided the design.**

**The budget must scale with the model window.** `context._memory_caps` scales its sections by
`window/200k` clamped to [1,5], so at 1M the memory sections get 5x more room. A FLAT budget
next to that silently inverts the plan's own adaptive-recall design: the ambient blocks would be the
only thing that does not grow. `budget_for_window` applies the SAME multiple, so the two halves of
the prompt scale together.

**The skill INDEX is a catalogue, not a candidate set.** Fed as one candidate per entry, the
diversification cap (`MAX_PER_SOURCE = 3`) kept 3 of 12 skills while 3,539 tokens sat unused — it
would HIDE most of the user's skills to enforce source diversity on a block whose whole purpose is
completeness. So the index is ONE candidate with the plan's three-step degradation (full →
hint-capped descriptions → names only), which is also why it can shrink instead of vanishing.

**🔴 The skills block holds TWO populations, and the first version dropped one.** Caught by the
EXISTING suite (`test_context.py::test_skills_injected`), not by anything written here: an
always-loaded skill renders as `### Skill: <name>` plus its full body, while on-demand skills render
as the `- **name**: …` index. An index-only parser silently dropped every always-loaded skill — the
ones the user marked never-optional. `always_body` and `index_candidate` are now a pair, each with
its own `[Skills:]` frame (either can be dropped independently, so one shared wrapper would
sometimes frame nothing), and the bodies rank above the index because a pointer list should yield
before content does.

**Scope is the five blocks the criterion names**, not the whole eight-part assembly. The memory
context keeps `_memory_caps` — it is a different, already-window-scaled mechanism, and swapping a
working recall render for an unproven one is what S71 refused to do blind. Two of the five have no
producer yet (see `SLOT_KINDS`); their slot mapping is asserted so a future producer joins it
instead of appending beside it.
"""

from __future__ import annotations

import logging

from personalclaw.learning.surfacing import (
    AUTHORITY_PREAMBLE,
    Allocation,
    Candidate,
    allocate,
    count_tokens,
)

logger = logging.getLogger(__name__)

#: The window the base budget is calibrated for, and the max multiple — the SAME pair
#: `context._memory_caps` uses. Restated as a reference to that calibration rather than a second
#: scale: two different multiples would make the memory half and the learning half of one prompt
#: disagree about how big the window is.
BASELINE_WINDOW = 200_000
MAX_BUDGET_MULTIPLE = 5.0

#: Which allocator KIND each named block enters the pool as. Reused from the allocator's existing
#: vocabulary rather than extended — `allocate()` maps kind → slot internally, and a sixth kind
#: would need a sixth slot, which is how "one budget" becomes six again.
#:
#: `template` and `self_model` have NO live producer: nothing on the chat path matches a query to a
#: workflow def, and nothing persists `user.selfmodel.*` (S72 built the decisions, not the store).
#: They are mapped anyway, and `test_every_named_block_has_a_slot` asserts it, so whoever builds
#: those producers finds a budgeted slot waiting instead of appending a sixth independent block.
SLOT_KINDS: dict[str, str] = {
    "lessons": "lesson",
    "skill_index": "skill",
    "template": "template",
    "voice": "memory",
    "self_model": "memory",
}

#: Cap on an index entry's description at the middle tier. From §2's R12 note ("an 80-char hint
#: cap on the agent-side index") — the plan's own number, so the middle degradation step is not one
#: this module invented.
HINT_CHARS = 80

#: Header lines the block renderers emit. Kept so a degraded render still LOOKS like the block it
#: replaces: a bare list of skills with no `[Skills:]` marker reads as prose the model may ignore.
_SKILL_HEADER = "[Skills:]"
_SKILL_FOOTER = "[End of skills]"


def budget_for_window(window: int | None, base: int) -> int:
    """The per-turn ambient budget, scaled to the model window.

    Same multiple and same clamp as `context._memory_caps`: `window/200k` in [1.0, 5.0]. An unknown
    window returns the base — the safe direction, because guessing a large window would let the
    ambient blocks crowd a small one.
    """
    if base <= 0:
        return 0
    win = window or BASELINE_WINDOW
    mult = max(1.0, min(MAX_BUDGET_MULTIPLE, win / BASELINE_WINDOW))
    return int(base * mult)


def lesson_candidates(block: str) -> list[Candidate]:
    """One candidate per LESSON, so the pool ranks corrections individually.

    Per-lesson rather than one block candidate: a 40-lesson block is either wholly in or wholly out,
    and "wholly out" is precisely the crowd-out the criterion forbids. Ranked individually, the
    query-relevant correction survives a budget that cannot hold all forty — measured: at 800
    lessons the allocator kept 112 including the one matching the query, where the block form
    injected 8,733 tokens or nothing.

    The header is NOT a candidate. It is the block's frame, re-emitted by `render` when any lesson
    survives, so it can never itself consume the budget that its own content needs.
    """
    out: list[Candidate] = []
    for line in (block or "").split("\n"):
        text = line.strip()
        if not text.startswith("- "):
            continue
        body = text[2:].strip()
        if not body:
            continue
        out.append(
            Candidate(
                kind=SLOT_KINDS["lessons"],
                key=f"lesson:{len(out)}",
                # A stored lesson carries no similarity score — it was TAUGHT, not retrieved. 1.0
                # rather than 0.0: the entry gate already passed (it is in the store), and a 0.0
                # score would rank the user's own corrections below every retrieved neighbour.
                score=1.0,
                # The `- ` bullet is KEPT in the rendered text. Measured: stripping it rendered the
                # user's corrections as bare prose lines under a header promising a list of rules,
                # and it also broke `frame`'s header placement, which locates the lessons by their
                # bullet. The body without the bullet is what gets MATCHED; the bullet is what gets
                # RENDERED.
                l0=f"- {body}",
                l1=f"- {body}",
                arm="lesson_store",
            )
        )
    return out


def _index_entries(block: str) -> list[str]:
    """The `- **name**: description (dir: …)` lines of a skill INDEX."""
    return [line for line in (block or "").split("\n") if line.strip().startswith("- **")]


def _hint(line: str) -> str:
    """One index entry with its description cut to `HINT_CHARS` — the middle degradation step.

    Cuts the DESCRIPTION only, never the name: a skill the model cannot name is a skill it cannot
    invoke, so the identifier survives every tier while the prose is what shrinks.
    """
    head, sep, rest = line.partition(": ")
    if not sep:
        return line
    if len(rest) <= HINT_CHARS:
        return line
    return f"{head}: {rest[:HINT_CHARS].rstrip()}…"


def always_body(block: str) -> str:
    """The ALWAYS-LOADED skill bodies from a skills block, or "".

    🔴 Found by the existing suite, not by these tests: `skills/loader.get_context` emits TWO
    populations in one block — always-loaded skills as `### Skill: <name>` with their FULL body, and
    on-demand skills as the `- **name**: …` INDEX. Parsing only the index lines silently dropped
    every always-loaded skill, and an always-loaded skill is one the user marked never-optional.

    They are separated rather than merged because they are different KINDS of thing under a budget:
    an index entry is a pointer that degrades to a name, while a body is content that must not be
    cut in half. This returns the bodies verbatim for an all-or-nothing candidate.
    """
    lines = (block or "").split("\n")
    out: list[str] = []
    keeping = False
    for line in lines:
        if line.startswith("### Skill: "):
            keeping = True
        elif line.startswith("## Available Skills") or line.startswith("[End of skills]"):
            keeping = False
        if keeping:
            out.append(line)
    # Trailing `---` separators belong to the block's own joining, not to a body.
    while out and out[-1].strip() in ("", "---"):
        out.pop()
    return "\n".join(out).strip()


def index_candidate(block: str) -> Candidate | None:
    """The skill INDEX as ONE tiered candidate.

    Measured: as one candidate per entry, `MAX_PER_SOURCE = 3` kept 3 of 12 skills and left 3,539
    tokens unused. Diversification exists so a rich source cannot crowd out a sparse one; applied to
    a catalogue it just deletes most of the catalogue. So the index competes as a single item and
    DEGRADES rather than losing entries to a quota:

    * **L2** — the block verbatim, descriptions and dirs intact.
    * **L1** — descriptions cut to `HINT_CHARS`; every skill still named and invocable.
    * **L0** — names only.

    Covers the ON-DEMAND population only; `always_body` handles the always-loaded bodies in the same
    block. Returns None for a block with no `- **` entries — an always-loaded-skills-only render has
    no index to budget, and a candidate whose text is just the header would spend tokens saying
    nothing. Returning None here was what dropped the always-loaded skills until `always_body`
    existed, so the two functions are a pair: neither is complete alone.
    """
    entries = _index_entries(block)
    if not entries:
        return None
    names: list[str] = []
    for line in entries:
        head, _sep, _rest = line.partition(": ")
        names.append(head)
    return Candidate(
        kind=SLOT_KINDS["skill_index"],
        key="skill_index",
        # The index is a catalogue, not a match: it earns its place by being the model's only route
        # to the skills, not by similarity to this turn's query.
        score=0.9,
        l0=f"{_SKILL_HEADER}\n" + "\n".join(names) + f"\n{_SKILL_FOOTER}",
        l1=f"{_SKILL_HEADER}\n" + "\n".join(_hint(e) for e in entries) + f"\n{_SKILL_FOOTER}",
        l2=(block or "").strip(),
        arm="skill_index",
    )


def block_candidate(name: str, block: str, *, score: float = 0.85) -> Candidate | None:
    """A pre-rendered block (persona, USER PROFILE facets, self-model snapshot) as one candidate.

    All-or-nothing by construction: `l0 == l1 == l2`. These blocks are already internally capped by
    their own renderers (`_MAX_RENDERED` facets, a `limit` of persona traits, `SNAPSHOT_MAX_CHARS`),
    and re-truncating a block that already dropped whole entries to fit would cut mid-entry — the
    exact corruption the lesson fix removed. Under this budget they either fit or are catalogued as
    a near-miss.
    """
    text = (block or "").strip()
    if not text:
        return None
    kind = SLOT_KINDS.get(name, "memory")
    return Candidate(kind=kind, key=name, score=score, l0=text, l1=text, l2=text, arm=name)


def render(
    *,
    lessons: str = "",
    skill_index: str = "",
    voice: str = "",
    persona: str = "",
    self_model: str = "",
    template: str = "",
    query: str = "",
    budget_tokens: int = 4000,
    window: int | None = None,
) -> Allocation:
    """Rank the named blocks into ONE budget and render them. The criterion's mechanism.

    `window` scales the budget when given; pass None to use `budget_tokens` as-is.

    Empty in, empty out — and deliberately not an empty PREAMBLE. A render asserting that the
    context below is authoritative, with nothing below it, is overhead that also teaches a model to
    discount the assertion.
    """
    budget = budget_for_window(window, budget_tokens) if window is not None else budget_tokens
    if budget <= 0:
        return Allocation(text="", used_tokens=0, budget_tokens=0)

    ceiling = budget
    sources: dict[str, list[Candidate]] = {}
    header_cost = 0
    lesson_cands = lesson_candidates(lessons)
    if lesson_cands:
        sources["lessons"] = lesson_cands
        # RESERVE the header's cost before allocating. Found by driving a 600-token budget: the
        # framed output came back at 622 tokens, because `frame` restores the header AFTER the
        # allocator has spent the budget. A budget that a later step can add to is not a budget —
        # and this is the one block whose frame is mandatory, so its cost belongs inside.
        # The `+ 2` covers the blank-line separator `frame` joins the header on with. Measured: the
        # framed text ran 3 tokens above `used_tokens` without it: the separator is real text
        # that no candidate paid for. Reserving slightly more than the header costs is the safe
        # direction — the alternative is a budget that is right on average and exceeded in practice.
        header_cost = count_tokens(lesson_header(lessons)) + 2
        budget = max(0, budget - header_cost)
        if budget <= 0:
            return Allocation(text="", used_tokens=0, budget_tokens=ceiling)
    skills: list[Candidate] = []
    # The always-loaded bodies rank ABOVE the index (0.95 vs 0.9): the user marked them
    # never-optional, so under pressure the pointer list yields before the content does.
    #
    # Each population carries its OWN `[Skills:]` frame. Measured: with only always-loaded skills
    # present the render came out unframed, because the frame was attached to the index candidate
    # and there was no index — and an unmarked body reads as prose the model may treat as
    # commentary. Two frames is right rather than one shared wrapper, because either population can
    # be dropped by the budget independently, and a frame around nothing is the defect on the other
    # side.
    always = always_body(skill_index)
    if always:
        framed = f"{_SKILL_HEADER}\n{always}\n{_SKILL_FOOTER}"
        body = block_candidate("skill_index", framed, score=0.95)
        if body is not None:
            body.key = "skills_always"
            skills.append(body)
    idx = index_candidate(skill_index)
    if idx is not None:
        skills.append(idx)
    tmpl = block_candidate("template", template)
    if tmpl is not None:
        skills.append(tmpl)
    if skills:
        sources["skills"] = skills
    memory: list[Candidate] = []
    for name, text in (("voice", voice), ("voice_persona", persona), ("self_model", self_model)):
        cand = block_candidate("voice" if name == "voice_persona" else name, text)
        if cand is not None:
            # Distinct keys: `fuse` dedupes on (kind, key), so persona and the facet profile sharing
            # a key would silently drop one of them — measured while wiring, and invisible because
            # both are plausible-looking blocks that render fine alone.
            cand.key = name
            memory.append(cand)
    if memory:
        sources["memory"] = memory

    if not sources:
        return Allocation(text="", used_tokens=0, budget_tokens=ceiling)

    alloc = allocate(sources, query=query, budget_tokens=budget)

    # NOTHING MAY CROWD OUT A LESSON — not the preamble, not the lesson block's own header.
    #
    # Measured, twice. (a) At a 120-token budget the 73-token preamble fit, left 23 tokens, and ZERO
    # lessons survived — while at 60 tokens (preamble skipped as oversized) one did. The authority
    # statement was outranking the corrections it exists to assert authority ON BEHALF OF. (b) The
    # header costs 29 tokens and one lesson costs 31, so at a 60-token budget RESERVING the header
    # left 29 and no lesson fit either.
    #
    # Both are the same mistake: spending the budget on FRAMING before its CONTENT. So when no
    # lesson survived, retry with the framing released — the header reservation returned and the
    # preamble suppressed. A lesson with no header is still the user's rule; a header with no
    # lessons is a promise of rules the model then cannot find.
    if lesson_cands and not any(kind == SLOT_KINDS["lessons"] for kind, _k, _t in alloc.included):
        retry = allocate(sources, query=query, budget_tokens=ceiling, include_preamble=False)
        if any(kind == SLOT_KINDS["lessons"] for kind, _k, _t in retry.included):
            alloc = retry
            header_cost = 0

    # Report against the CEILING, with the reserved header counted as spent. The caller's audit
    # question is "did the ambient render stay inside the configured budget", and an Allocation that
    # reported the post-reservation budget would answer a different, easier question.
    if header_cost and any(kind == SLOT_KINDS["lessons"] for kind, _k, _t in alloc.included):
        alloc.used_tokens += header_cost
    alloc.budget_tokens = ceiling
    return alloc


def lesson_header(block: str) -> str:
    """The lesson block's own header lines, to re-frame surviving lessons.

    Taken from the incoming block rather than restated, so `vector_memory.get_lessons_context`
    remains the single author of the wording — a copy here would drift and the drift would be
    invisible (both versions read like the real header).
    """
    lines: list[str] = []
    for line in (block or "").split("\n"):
        if line.strip().startswith("- "):
            break
        if line.strip():
            lines.append(line)
    return "\n".join(lines)


def frame(alloc: Allocation, *, lessons_block: str = "") -> str:
    """The final ambient text: the allocation, with the lesson header restored.

    The header is re-added only when a lesson actually survived. A "[Learned corrections — ALWAYS
    follow these]" header over zero lessons is worse than no header: it asserts the presence of
    rules the model then cannot find, which is how a model starts inventing them.
    """
    text = alloc.text
    if not text:
        return ""
    kept_lesson = any(kind == SLOT_KINDS["lessons"] for kind, _key, _tier in alloc.included)
    if not kept_lesson:
        return text
    header = lesson_header(lessons_block)
    if not header or header in text:
        return text
    # AFFORDABILITY IS CHECKED AGAINST THE RENDERED TEXT, not against `used_tokens`. `render`
    # already counts the reserved header into `used_tokens`, so checking that would DOUBLE-COUNT it.
    # Measured: a 600-token budget reporting 574 used dropped a header that plainly fit, because
    # 574 + 29 + 2 cleared the ceiling on paper while the actual text was well under it.
    #
    # The check has to exist at all because `render` RELEASES the reservation when reserving it
    # would have cost the last lesson (a 29-token header against a 31-token lesson at a 60-token
    # budget). There the header genuinely is unaffordable, and adding it anyway would reintroduce
    # the over-budget defect the reservation was added to fix.
    if count_tokens(text) + count_tokens(header) + 2 > alloc.budget_tokens:
        return text
    # Inserted before the first lesson line, not prepended to the whole block: the preamble stays
    # first, or the authority statement ends up buried under the content it governs.
    marker = "\n\n"
    parts = text.split(marker)
    for i, part in enumerate(parts):
        if part.lstrip().startswith("- ") or _is_lesson_block(part, alloc):
            parts.insert(i, header)
            break
    else:
        parts.append(header)
    return marker.join(parts)


def _is_lesson_block(part: str, alloc: Allocation) -> bool:
    """Whether this rendered chunk is the lessons slot's output."""
    first = part.strip().split("\n", 1)[0]
    return any(
        kind == SLOT_KINDS["lessons"] and first.startswith(key.split(":", 1)[0])
        for kind, key, _tier in alloc.included
    )


def report(alloc: Allocation) -> dict[str, object]:
    """A structured account of one ambient allocation, for logging and the panel.

    Includes what was LEFT OUT. An allocator that reports only what it injected cannot be audited
    for crowd-out — the whole failure this criterion is about is invisible from the surviving text.
    """
    kinds: dict[str, int] = {}
    for kind, _key, _tier in alloc.included:
        kinds[kind] = kinds.get(kind, 0) + 1
    return {
        "used_tokens": alloc.used_tokens,
        "budget_tokens": alloc.budget_tokens,
        "headroom": alloc.headroom,
        "by_kind": dict(sorted(kinds.items())),
        "near_misses": len(alloc.near_misses),
        "degraded": list(alloc.degraded),
        "skipped_oversized": list(alloc.skipped_oversized),
        "truncated_slot": alloc.truncated_slot,
        "preamble": AUTHORITY_PREAMBLE in alloc.text,
    }


def preamble_cost() -> int:
    """Token cost of the authority preamble — measured, not asserted.

    Exposed because it is the one fixed overhead in the budget: a caller choosing a very small
    `context_budget_tokens` deserves to know the floor, and S71 already measured that adding the
    preamble unconditionally blew a 50-token budget before a single item was considered.
    """
    return count_tokens(AUTHORITY_PREAMBLE)

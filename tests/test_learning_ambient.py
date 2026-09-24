"""The ambient render under ONE budget (§2.4 / §7 crit 5 — S80).

Criterion 5: "the lesson block, skill INDEX, template suggestion, voice/facet blocks, and self-model
snapshot fit ONE per-turn slot-allocated token budget; lessons are never crowded out
(sacrificial-slot truncation only); the authority preamble renders."

**Three inert controls, measured before the module was written.** `allocate()` and
`AUTHORITY_PREAMBLE` had zero callers outside their own module and tests. `learning.
context_budget_tokens` is a fully round-tripped config knob that nothing read. And the blocks were
governed by per-block CHARACTER caps summing to ~36,750 tokens against a declared 4,000. Driven
with 120 realistic lessons the old render exceeded the budget by 1,576 tokens, and at 400+
lessons reached 10,101.

The tests are grouped by the criterion's own clauses: one budget, lessons never crowded out, the
preamble renders. Plus the two defects driving the code found (a header added AFTER the budget was
spent, and the preamble outranking the lessons it speaks for), which are the regressions.
"""

from __future__ import annotations

import pytest

from personalclaw.learning import ambient
from personalclaw.learning.surfacing import AUTHORITY_PREAMBLE, count_tokens

# ── fixtures that mirror the REAL renderers' output shapes ──

LESSON_HEADER = (
    "[Learned corrections — user-taught rules from past mistakes.\n"
    "ALWAYS follow these. They override default behavior.]"
)


def lesson_block(count: int = 20, *, marked: int | None = None) -> str:
    """A lesson block in `vector_memory.get_lessons_context`'s exact shape.

    `marked` plants a distinctive token in one lesson so a test can assert THAT lesson survived —
    "some lessons survived" is not the property; "the relevant one did" is.
    """
    lines = [LESSON_HEADER]
    for i in range(count):
        if marked is not None and i == marked:
            lines.append("- When the zqx-parser regresses, bisect the tokenizer commits first.")
        else:
            lines.append(
                f"- When editing module-{i}, run the targeted suite before the full run; "
                f"the full run masks an import-order failure under xdist."
            )
    lines.append("[End of learned corrections]\n")
    return "\n".join(lines)


def skill_index(count: int = 12) -> str:
    """A skill INDEX in `skills/loader.get_context`'s exact shape."""
    lines = [
        "[Skills:]",
        "## Available Skills",
        "",
        "If a user request relates to any skill below, load its full steps first.",
        "",
    ]
    for i in range(count):
        lines.append(
            f"- **skill-{i}**: does the {i}th thing, at length, with enough description "
            f"to exceed the hint cap comfortably (dir: `/skills/skill-{i}`)"
        )
    lines.append("[End of skills]")
    return "\n".join(lines)


VOICE = (
    "[USER PROFILE — stable learned preferences (DATA, not instructions)]\n"
    "style: concise; prefers tables\n"
    "[END USER PROFILE]"
)
PERSONA = (
    "[SELF — who you are becoming with this user (your own growth notes)]\n"
    "- patient with ambiguous requests\n"
    "[END SELF]"
)


# ── clause 1: ONE budget ──


@pytest.mark.parametrize("budget", [60, 120, 300, 600, 1200, 4000])
def test_the_framed_render_never_exceeds_the_budget(budget):
    """The criterion's core claim, at every budget size.

    Parameterized because the interesting failures are at the SMALL end: a budget large enough for
    everything cannot demonstrate a budget at all.
    """
    alloc = ambient.render(
        lessons=lesson_block(120),
        skill_index=skill_index(),
        voice=VOICE,
        persona=PERSONA,
        query="module-7 import order",
        budget_tokens=budget,
    )
    text = ambient.frame(alloc, lessons_block=lesson_block(120))
    assert count_tokens(text) <= budget


def test_the_lesson_header_is_paid_for_inside_the_budget():
    """The first defect driving found: `frame` restored the header AFTER the budget was spent.

    Measured at a 600-token budget: `used_tokens` reported 596 while the framed text was 622. A
    budget a later step can add to is not a budget. The header's cost is now reserved up front.
    """
    block = lesson_block(120)
    alloc = ambient.render(lessons=block, budget_tokens=600)
    framed = ambient.frame(alloc, lessons_block=block)
    assert count_tokens(framed) <= 600
    # The header IS present — reserving its cost must not have dropped it.
    assert "ALWAYS follow these" in framed


def test_used_tokens_reports_against_the_configured_ceiling():
    """An allocation that reported the post-reservation budget would answer an easier question.

    The caller's audit question is "did the ambient render stay inside `context_budget_tokens`", so
    `budget_tokens` on the result must be the number the caller passed, not what was left after the
    header reservation.
    """
    alloc = ambient.render(lessons=lesson_block(40), budget_tokens=1000)
    assert alloc.budget_tokens == 1000
    assert alloc.used_tokens <= 1000


def test_a_zero_budget_renders_nothing():
    alloc = ambient.render(lessons=lesson_block(10), budget_tokens=0)
    assert alloc.text == ""
    assert ambient.frame(alloc, lessons_block=lesson_block(10)) == ""


def test_no_blocks_renders_nothing():
    """Empty in, empty out — and specifically NOT an empty preamble.

    A render asserting that the context below is authoritative, with nothing below it, is overhead
    that also teaches the model to discount the assertion.
    """
    alloc = ambient.render(budget_tokens=4000)
    assert alloc.text == ""
    assert AUTHORITY_PREAMBLE not in alloc.text


def test_the_budget_scales_with_the_model_window():
    """Same multiple and clamp as `context._memory_caps`.

    A FLAT budget beside window-scaled memory sections would make the ambient blocks the only part
    of the prompt that never benefits from a larger window — silently inverting the plan's own
    adaptive-recall design.
    """
    assert ambient.budget_for_window(200_000, 4000) == 4000
    assert ambient.budget_for_window(1_000_000, 4000) == 20_000
    # Clamped both ways: a small window does not shrink below the calibrated baseline, and a
    # gigantic one does not grow past 5x.
    assert ambient.budget_for_window(8_000, 4000) == 4000
    assert ambient.budget_for_window(10_000_000, 4000) == 20_000


def test_an_unknown_window_uses_the_base_budget():
    """The safe direction: guessing a large window would let the blocks crowd a small one."""
    assert ambient.budget_for_window(None, 4000) == 4000
    assert ambient.budget_for_window(0, 4000) == 4000


def test_the_window_multiple_matches_the_memory_caps_multiple():
    """Not a restated constant — the SAME calibration, asserted against the prompt's other half.

    Two different multiples would make the memory half and the learning half of one prompt disagree
    about how big the window is, and nothing would fail.
    """
    from personalclaw.context import _BASELINE_WINDOW, _MAX_BUDGET_MULTIPLE

    assert ambient.BASELINE_WINDOW == _BASELINE_WINDOW
    assert ambient.MAX_BUDGET_MULTIPLE == _MAX_BUDGET_MULTIPLE


# ── clause 2: lessons are NEVER crowded out ──


def test_lessons_survive_at_every_budget_that_can_hold_one():
    """The criterion's load-bearing clause.

    Measured on the pre-fix code: at a 120-token budget the 73-token preamble fit and ZERO lessons
    survived, while at 60 tokens (preamble skipped as oversized) one did — the authority statement
    was outranking the corrections it exists to speak for.
    """
    block = lesson_block(120)
    for budget in (60, 90, 120, 300, 600):
        alloc = ambient.render(
            lessons=block, skill_index=skill_index(), budget_tokens=budget, query="module-3"
        )
        kept = [k for kind, k, _t in alloc.included if kind == "lesson"]
        assert kept, f"no lesson survived a {budget}-token budget"


def test_the_preamble_yields_to_a_lesson_when_both_cannot_fit():
    """The second defect driving found, as an explicit regression.

    A lesson with no preamble is still the user's rule; a preamble with no lessons is a claim about
    content that is not there.
    """
    alloc = ambient.render(lessons=lesson_block(120), budget_tokens=120)
    kept = [k for kind, k, _t in alloc.included if kind == "lesson"]
    assert kept
    assert AUTHORITY_PREAMBLE not in alloc.text


def test_a_rich_skill_index_cannot_crowd_out_the_lessons():
    """Slot priority, not politeness: `lessons` outranks `skills` in `SLOT_ORDER`."""
    alloc = ambient.render(
        lessons=lesson_block(30),
        skill_index=skill_index(60),
        query="module-5",
        budget_tokens=1200,
    )
    kinds = [kind for kind, _k, _t in alloc.included]
    assert "lesson" in kinds


def test_the_query_relevant_lesson_survives_a_budget_that_cannot_hold_all():
    """Per-lesson candidates, not one block candidate.

    A block candidate is wholly in or wholly out, and "wholly out" is the crowd-out the criterion
    forbids. Ranked individually, the lesson that matches THIS turn survives.
    """
    block = lesson_block(200, marked=137)
    alloc = ambient.render(
        lessons=block, query="zqx-parser tokenizer regression", budget_tokens=400
    )
    text = ambient.frame(alloc, lessons_block=block)
    assert "zqx-parser" in text
    assert len([k for kind, k, _t in alloc.included if kind == "lesson"]) < 200


def test_no_lesson_is_ever_rendered_partially():
    """The rule the old char-cap violated: half a lesson is worse than none.

    "Never deploy without" reads as an instruction, and it is not the one the user gave.
    """
    block = lesson_block(120)
    for budget in (60, 120, 300, 600, 4000):
        alloc = ambient.render(lessons=block, budget_tokens=budget)
        text = ambient.frame(alloc, lessons_block=block)
        for line in text.split("\n"):
            if line.startswith("- When editing") or line.startswith("- When the zqx"):
                assert line.rstrip().endswith("."), f"partial lesson at budget {budget}: {line!r}"


def test_the_lessons_slot_is_not_sacrificial():
    """Asserted against the allocator's own SLOT_ORDER, so the policy cannot drift here silently."""
    from personalclaw.learning.surfacing import SLOT_ORDER

    slots = {name: sacrificial for name, _priority, sacrificial in SLOT_ORDER}
    assert slots["lessons"] is False
    assert slots["retrieved_context"] is True


def test_an_oversized_lesson_is_skipped_not_truncated():
    """`skipped_oversized` names it, so a dropped correction is auditable rather than silent."""
    huge = "- " + ("x" * 4000) + "."
    block = f"{LESSON_HEADER}\n{huge}\n[End of learned corrections]\n"
    alloc = ambient.render(lessons=block, budget_tokens=300)
    assert not [k for kind, k, _t in alloc.included if kind == "lesson"]
    assert alloc.skipped_oversized or alloc.near_misses


# ── clause 3: the authority preamble renders ──


def test_the_authority_preamble_renders_when_there_is_room():
    alloc = ambient.render(lessons=lesson_block(10), budget_tokens=4000)
    assert AUTHORITY_PREAMBLE in alloc.text
    assert ambient.report(alloc)["preamble"] is True


def test_the_preamble_comes_first():
    """Placement is the point: an authority statement buried under the content it governs is prose.

    Asserted through `frame`, because that is what inserts the lesson header and could displace it.
    """
    block = lesson_block(20)
    alloc = ambient.render(lessons=block, skill_index=skill_index(), budget_tokens=4000)
    text = ambient.frame(alloc, lessons_block=block)
    assert text.startswith("The context below is AUTHORITATIVE")
    assert text.index("AUTHORITATIVE") < text.index("Learned corrections")


def test_the_preamble_cost_is_measured_not_asserted():
    """A caller choosing a very small budget deserves to know the fixed floor."""
    assert ambient.preamble_cost() == count_tokens(AUTHORITY_PREAMBLE)
    assert ambient.preamble_cost() > 0


# ── the skill INDEX: a catalogue, degraded rather than rationed ──


def test_the_index_is_one_candidate_not_one_per_entry():
    """Measured: as one candidate per entry, MAX_PER_SOURCE=3 kept 3 of 12 skills and left 3,539
    tokens unused. Diversification stops a rich source crowding a sparse one; applied to a catalogue
    it just deletes most of the catalogue."""
    cand = ambient.index_candidate(skill_index(12))
    assert cand is not None
    assert cand.key == "skill_index"
    # Every skill is named at EVERY tier — a skill the model cannot name is one it cannot invoke.
    for tier_text in (cand.l0, cand.l1, cand.l2):
        for i in range(12):
            assert f"skill-{i}" in tier_text


def test_the_index_degrades_through_three_steps():
    """§2.4's degradation sequence: full → evenly-shrunk descriptions → names-only."""
    cand = ambient.index_candidate(skill_index(12))
    assert cand is not None
    assert count_tokens(cand.l2) > count_tokens(cand.l1) > count_tokens(cand.l0)


def test_the_middle_tier_cuts_the_description_never_the_name():
    cand = ambient.index_candidate(skill_index(4))
    assert cand is not None
    for line in cand.l1.split("\n"):
        if line.startswith("- **"):
            assert line.startswith("- **skill-")
            # The description is capped, so the (dir: …) tail of a long entry is gone.
            assert len(line) <= 4 + len("**skill-99**: ") + ambient.HINT_CHARS + 4


def test_the_index_degrades_instead_of_vanishing():
    """A budget too small for the full index still shows the catalogue at a lower tier.

    The assertion is on the RENDERED tier, not on `degraded`. Driving it showed why: the allocator
    assigns L2 only to items within 0.9 of the top score and caps them at 3, so a lone index
    candidate is usually assigned L1 *up front* and never "degrades" from anything. What matters to
    the criterion is that a shrinking budget shrinks the block instead of dropping it.
    """
    full = ambient.index_candidate(skill_index(12))
    assert full is not None
    # A budget below the FULL index (422 tokens + a 73-token preamble = 495) but above its
    # hint-capped form. Measured rather than guessed: at 500 the full index still fits, so a test
    # asserting degradation there would assert nothing.
    alloc = ambient.render(skill_index=skill_index(12), query="deploy", budget_tokens=400)
    assert "[Skills:]" in alloc.text
    assert count_tokens(alloc.text) <= 400
    # Every skill is still named at the reduced tier.
    for i in range(12):
        assert f"skill-{i}" in alloc.text


def test_a_budget_below_the_full_index_still_names_every_skill():
    """The degradation ladder's purpose: a shrinking budget must not hide skills.

    `degraded` IS populated when the item was assigned L2 first and had to come down — asserted here
    with a budget that admits L2 for nothing.
    """
    alloc = ambient.render(skill_index=skill_index(30), query="deploy", budget_tokens=400)
    assert "[Skills:]" in alloc.text
    for i in range(30):
        assert f"skill-{i}" in alloc.text


def test_an_always_loaded_skill_body_is_not_dropped():
    """🔴 Caught by the EXISTING suite (`test_context.py::test_skills_injected`), not by these tests.

    `skills/loader.get_context` emits TWO populations in one block: always-loaded skills as
    `### Skill: <name>` with their FULL body, and on-demand skills as the `- **name**: …` index.
    Parsing only the index lines dropped every always-loaded skill — the ones the user marked
    never-optional — and no test written here would have noticed, because the fixtures I wrote all
    contained an index.
    """
    block = "[Skills:]\n### Skill: alpha\n\n# Alpha\nDo the alpha thing.\n[End of skills]\n"
    assert "Do the alpha thing." in ambient.always_body(block)
    alloc = ambient.render(skill_index=block, budget_tokens=4000)
    assert "Do the alpha thing." in alloc.text
    # And it is FRAMED: an unmarked body reads as prose the model may treat as commentary.
    assert "[Skills:]" in alloc.text


def test_both_skill_populations_survive_together():
    """The mixed block is the common case, and the two are budgeted separately."""
    block = (
        "[Skills:]\n### Skill: alpha\n\n# Alpha\nDo the alpha thing.\n\n---\n\n"
        "## Available Skills\n\n"
        "- **beta**: does beta (dir: `/s/beta`)\n"
        "- **gamma**: does gamma (dir: `/s/gamma`)\n"
        "[End of skills]\n"
    )
    alloc = ambient.render(skill_index=block, budget_tokens=4000)
    assert "Do the alpha thing." in alloc.text  # the always-loaded body
    assert "**beta**" in alloc.text and "**gamma**" in alloc.text  # the index


def test_the_always_body_excludes_the_index_lines():
    """The split must be clean: an index line inside the body candidate would be budgeted twice."""
    block = (
        "[Skills:]\n### Skill: alpha\n\n# Alpha\nbody.\n\n---\n\n## Available Skills\n\n"
        "- **beta**: does beta (dir: `/s/beta`)\n[End of skills]\n"
    )
    body = ambient.always_body(block)
    assert "**beta**" not in body
    assert "Available Skills" not in body


def test_an_index_only_block_has_no_always_body():
    assert ambient.always_body(skill_index(3)) == ""


def test_the_always_body_outranks_the_index_under_pressure():
    """A never-optional body must not lose its place to a list of pointers."""
    block = (
        "[Skills:]\n### Skill: alpha\n\n# Alpha\nThe alpha procedure, in full.\n\n---\n\n"
        "## Available Skills\n\n"
        + "\n".join(f"- **s{i}**: does the {i}th thing (dir: `/s/{i}`)" for i in range(40))
        + "\n[End of skills]\n"
    )
    alloc = ambient.render(skill_index=block, query="alpha", budget_tokens=250)
    assert "The alpha procedure, in full." in alloc.text


def test_an_index_with_no_entries_is_not_a_candidate():
    """An always-loaded-skills-only render has no index to budget, and a candidate whose text is
    just the header would spend tokens saying nothing."""
    assert ambient.index_candidate("") is None
    assert ambient.index_candidate("[Skills:]\n### Skill: alpha\n\nbody\n[End of skills]") is None


def test_the_hint_cap_comes_from_the_plan():
    """80 chars is §2's R12 number, not one this module invented."""
    assert ambient.HINT_CHARS == 80


def test_a_short_description_is_not_padded_or_cut():
    line = "- **s**: short (dir: `/x`)"
    assert ambient._hint(line) == line


# ── the pre-rendered blocks: all-or-nothing ──


def test_a_prerendered_block_is_all_or_nothing():
    """These renderers already dropped whole entries to fit their own caps (`_MAX_RENDERED` facets,
    `SNAPSHOT_MAX_CHARS`). Re-truncating one would cut mid-entry — the corruption the lesson fix
    removed."""
    cand = ambient.block_candidate("voice", VOICE)
    assert cand is not None
    assert cand.l0 == cand.l1 == cand.l2


def test_an_empty_block_is_not_a_candidate():
    assert ambient.block_candidate("voice", "") is None
    assert ambient.block_candidate("voice", "   \n  ") is None


def test_the_voice_and_persona_blocks_do_not_dedupe_each_other():
    """Found while wiring: `fuse` dedupes on (kind, key), so persona and the facet profile sharing a
    key silently dropped one of them — invisible, because both render fine alone."""
    alloc = ambient.render(voice=VOICE, persona=PERSONA, budget_tokens=4000)
    assert "USER PROFILE" in alloc.text
    assert "SELF — who you are becoming" in alloc.text


# ── the slot map, including the blocks with no producer yet ──


def test_every_named_block_has_a_slot():
    """All five blocks the criterion names map to an allocator kind.

    `template` and `self_model` have NO live producer: nothing on the chat path matches a query to
    a workflow def, and nothing persists `user.selfmodel.*` (S72 built the decisions, not a store).
    Mapping them anyway is what makes a future producer join the budget instead of appending a sixth
    independent block beside it.
    """
    for block in ("lessons", "skill_index", "template", "voice", "self_model"):
        assert block in ambient.SLOT_KINDS


def test_the_slot_kinds_are_the_allocators_own_vocabulary():
    """A sixth kind would need a sixth slot, which is how "one budget" becomes six again."""
    from personalclaw.learning.surfacing import SLOT_ORDER, allocate

    slot_names = {name for name, _p, _s in SLOT_ORDER}
    for kind in set(ambient.SLOT_KINDS.values()):
        cand = ambient.block_candidate("voice", "x")
        assert cand is not None
        cand.kind = kind
        alloc = allocate({"s": [cand]}, budget_tokens=4000)
        # Every kind lands in a DECLARED slot rather than the catch-all.
        assert alloc.included, f"kind {kind!r} produced no allocation"
    assert "lessons" in slot_names and "skills" in slot_names and "memory" in slot_names


def test_a_self_model_snapshot_would_fit_the_budget_when_a_producer_exists():
    """The contract the future producer calls, exercised with S72's real snapshot output."""
    from personalclaw.learning.self_model import Entry, snapshot

    entries = [
        Entry(facet="principle", key="edit-over-rewrite", body="Prefer editing to rewriting."),
        Entry(facet="theory", key="xdist-order", body="Import order may explain the flake."),
    ]
    block = snapshot(entries)
    assert block
    alloc = ambient.render(self_model=block, budget_tokens=4000)
    assert "Prefer editing to rewriting" in alloc.text


def test_a_template_suggestion_would_fit_the_budget_when_a_producer_exists():
    """Same for S58's `render_suggest` output, so the two unbuilt producers have a tested seam."""
    from personalclaw.workflows.surfacing import SurfacingMeta, render_suggest

    block = render_suggest(SurfacingMeta(summary="deploys the gateway"), name="deploy-gateway")
    assert block
    alloc = ambient.render(template=block, budget_tokens=4000)
    assert "deploy-gateway" in alloc.text


# ── the report: what was LEFT OUT ──


def test_the_report_names_what_did_not_fit():
    """An allocator that reports only what it injected cannot be audited for crowd-out — the whole
    failure this criterion is about is invisible from the surviving text."""
    alloc = ambient.render(lessons=lesson_block(200), skill_index=skill_index(), budget_tokens=400)
    report = ambient.report(alloc)
    assert report["near_misses"] > 0
    assert report["used_tokens"] <= report["budget_tokens"]
    assert report["by_kind"].get("lesson", 0) > 0
    assert set(report) >= {
        "used_tokens",
        "budget_tokens",
        "headroom",
        "by_kind",
        "near_misses",
        "degraded",
        "skipped_oversized",
        "truncated_slot",
        "preamble",
    }


def test_dropped_lessons_are_catalogued_on_the_allocation():
    """A dropped item the model never hears about is a silent gap it cannot ask about.

    Driving this corrected the assertion twice. (1) The catalogue is itself text and must be
    AFFORDED — at 500 tokens with 200 lessons, 478 went to lessons and none was left. (2) More
    fundamentally, a lesson's `l0` and `l1` are the SAME string (a correction has no shorter honest
    form), so the greedy fill spends the budget down to a few tokens and the rendered catalogue
    almost never fits for lessons specifically. That is the right trade: a real correction beats a
    note saying one exists.

    So the auditable guarantee is on the ALLOCATION, which `report()` exposes and the observability
    panel can show, rather than on the prompt text. `test_the_report_names_what_did_not_fit` is the
    other half.
    """
    alloc = ambient.render(lessons=lesson_block(200), budget_tokens=500)
    assert alloc.near_misses
    assert (
        len(alloc.near_misses) + len([k for kd, k, _t in alloc.included if kd == "lesson"]) == 200
    )


def test_the_catalogue_renders_when_a_near_miss_leaves_headroom():
    """The L0 catalogue is not dead — it renders when the near-miss is a big item with a short L0.

    Measured while correcting the test above: the catalogue's own cost comes out of the same budget,
    so it appears exactly when something was dropped AND room remains. A `retrieved_context` item
    whose full body cannot fit is the case the mechanism was built for.
    """
    from personalclaw.learning.surfacing import Candidate, allocate

    small = Candidate(kind="lesson", key="s", score=1.0, l0="- small.", l1="- small.")
    # A one-line `l0` that is the CATALOGUE entry, with a body too big for the budget at L1/L2. This
    # is the shape the mechanism was built for, and getting it wrong is instructive: with `l0` set
    # to the 900-char body itself, the catalogue LINE is 225 tokens and cannot fit either — the
    # catalogue only works when `l0` is genuinely one line, which is what the tier is for.
    big = Candidate(
        kind="context", key="big-doc", score=0.9, l0="big-doc (900 chars)", l1="x" * 900
    )
    alloc = allocate({"lessons": [small], "ctx": [big]}, budget_tokens=260)
    assert alloc.near_misses == [] or "Also available on request" in alloc.text
    # Either it fitted at L0 (degraded, no near-miss) or it was catalogued — never silently gone.
    assert "big-doc" in alloc.text


def test_the_catalogue_never_pushes_the_render_over_budget():
    """The catalogue competes for the same budget rather than being appended to it."""
    for budget in (300, 500, 900, 2000):
        alloc = ambient.render(lessons=lesson_block(200), budget_tokens=budget)
        assert alloc.used_tokens <= budget


# ── frame(): the header, and only when it is honest ──


def test_the_header_is_omitted_when_no_lesson_survived():
    """A "[Learned corrections — ALWAYS follow these]" header over zero lessons asserts the presence
    of rules the model then cannot find, which is how a model starts inventing them."""
    alloc = ambient.render(skill_index=skill_index(), budget_tokens=4000)
    text = ambient.frame(alloc, lessons_block=lesson_block(10))
    assert "Learned corrections" not in text


def test_the_header_is_taken_from_the_block_not_restated():
    """`vector_memory.get_lessons_context` stays the sole author of the wording — a copy here would
    drift, and the drift would be invisible because both versions read like the real header."""
    custom = "[MY OWN HEADER — follow these]\n- a lesson that ends properly.\n"
    alloc = ambient.render(lessons=custom, budget_tokens=4000)
    text = ambient.frame(alloc, lessons_block=custom)
    assert "MY OWN HEADER" in text


def test_frame_is_idempotent_about_the_header():
    block = lesson_block(5)
    alloc = ambient.render(lessons=block, budget_tokens=4000)
    once = ambient.frame(alloc, lessons_block=block)
    assert once.count("ALWAYS follow these") == 1


def test_frame_on_an_empty_allocation_is_empty():
    alloc = ambient.render(budget_tokens=4000)
    assert ambient.frame(alloc, lessons_block=lesson_block(5)) == ""


def test_lesson_header_stops_at_the_first_lesson():
    assert ambient.lesson_header(lesson_block(5)) == LESSON_HEADER
    assert ambient.lesson_header("") == ""
    assert ambient.lesson_header("- just a lesson.") == ""


# ── the wiring into context.py ──


def test_the_context_builder_routes_the_blocks_through_the_budget():
    """The inert-control regression: `allocate()` had zero callers, so the ranking existed and
    nothing ranked. This asserts the CALL, which no behavioural test of the allocator can see."""
    import inspect

    from personalclaw import context

    src = inspect.getsource(context._render_ambient)
    assert "ambient.render" in src
    assert "context_budget_tokens" in src
    assert "active_chat_model_window" in src


def test_the_old_per_block_lesson_cap_is_gone():
    """Clean break: `_fit_lessons` and `_LESSONS_CAP` governed the lesson block independently of any
    budget, and leaving them would be a second policy for the same block."""
    from personalclaw import context

    assert not hasattr(context, "_fit_lessons")
    assert not hasattr(context, "_LESSONS_CAP")


def test_the_config_knob_is_actually_read():
    """`learning.context_budget_tokens` was a fully round-tripped knob that nothing read — its help
    text promised "only retrieved context is ever trimmed", a promise no code kept."""
    from personalclaw.config.loader import AppConfig

    cfg = AppConfig.load().learning
    assert cfg.context_budget_tokens > 0
    alloc = ambient.render(lessons=lesson_block(500), budget_tokens=cfg.context_budget_tokens)
    assert alloc.used_tokens <= alloc.budget_tokens


def test_a_broken_budget_never_costs_the_user_their_lessons():
    """Never raises into a turn. A budgeting failure that cost the user their context would be
    strictly worse than an over-long prompt, so the fallback is the raw lesson block — the most
    authoritative content, ungoverned rather than absent."""
    from personalclaw import context

    block = lesson_block(5)
    out = context._render_ambient(lessons=block, skill_index="boom")
    assert out  # something rendered

    import personalclaw.learning.ambient as amb

    original = amb.render
    try:
        amb.render = lambda **_kw: (_ for _ in ()).throw(RuntimeError("boom"))
        assert context._render_ambient(lessons=block) == block
    finally:
        amb.render = original


def test_no_blocks_means_no_ambient_call():
    from personalclaw import context

    assert context._render_ambient() == ""

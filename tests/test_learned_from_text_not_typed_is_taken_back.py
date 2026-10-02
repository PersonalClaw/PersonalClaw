"""What learning took from text nobody typed is taken back: retracted where it is recognized,
offered for review where it only may have come from there.

Before learning read only a person's own words, the after-turn review quoted the message the model
was sent. Running a saved prompt whose text says "List what I said I would do" left the lesson
below in the owner's memory, listed under the lessons the assistant follows, and a standup prompt
that defines "Yesterday" put that definition in the glossary every turn carries.
"""

from __future__ import annotations

import json

import pytest

from personalclaw import memory_slots
from personalclaw.learning import composed_text, installers, proposals
from personalclaw.memory_service import MemoryService
from personalclaw.vector_memory import VectorMemoryStore

#: The lesson as the after-turn review wrote it: the prompt's expansion, quoted to 240 characters.
BODY_LESSON = (
    "User correction to honor: Execute the following instructions:\n\nRun my weekly review from "
    "~/Notes/Garden.\n\n- Read the last 7 daily notes in `Daily/`.\n- Collect every unchecked "
    "`- [ ]` item and group it: Cartwheel, feedsmith, talk, home.\n- List what I said I would do "
)
TYPED_LESSON = "User correction to honor: that's not what I meant, keep the summary to three lines"
EXPLICIT_LESSON = "Talk notes are conversational, with no jokes about the build system."

STANDUP = (
    'Draft my async standup. "Yesterday" means the last working day before today.\n\n'
    "Never quote ticket numbers from the private tracker. Under 90 words."
)


@pytest.fixture
def store(tmp_path):
    vs = VectorMemoryStore(db_path=tmp_path / "memory.db")
    vs.init()
    return vs


def _rules(vs) -> list[str]:
    return [str(json.loads(row["value_json"])) for row in vs.get_lessons()]


def _glossary(vs) -> list[str]:
    return [line.text for line in memory_slots.live_lines(memory_slots.load(vs, "glossary"))]


def _save_prompt(name: str, content: str) -> None:
    from personalclaw.prompt_providers.base import PromptTemplate
    from personalclaw.prompt_providers.registry import (
        _ensure_default_providers_registered,
        get_prompt_provider,
    )

    _ensure_default_providers_registered()
    get_prompt_provider("native").create_prompt(PromptTemplate(name=name, content=content))


def _reviews() -> list[proposals.Proposal]:
    return [
        p
        for p in proposals.list_pending(proposals.Kind.RETIREMENT.value)
        if composed_text.REVIEW_TAG in p.tags
    ]


# ── recognized: retracted ─────────────────────────────────────────────────────


def test_a_lesson_quoted_from_a_saved_prompts_text_is_retracted_and_hers_are_kept(store):
    store.write_lesson(BODY_LESSON, category="preference", source="after_turn_review")
    store.write_lesson(TYPED_LESSON, category="preference", source="after_turn_review")
    store.write_lesson(EXPLICIT_LESSON, source="user_explicit")

    report = composed_text.settle(store)

    assert report == {"retracted": 1, "offered": 0}
    assert sorted(_rules(store)) == sorted([TYPED_LESSON, EXPLICIT_LESSON])


def test_taking_it_back_twice_changes_nothing(store):
    store.write_lesson(BODY_LESSON, category="preference", source="after_turn_review")
    composed_text.settle(store)

    assert composed_text.settle(store) == {"retracted": 0, "offered": 0}


def test_memory_history_can_undo_the_retraction(store):
    store.write_lesson(BODY_LESSON, category="preference", source="after_turn_review")
    composed_text.settle(store)

    (event,) = [
        e
        for e in store.get_events(limit=20)
        if e["event_type"] == "delete" and e["source"] == composed_text.SOURCE
    ]
    ok, _message = store.undo_event(int(event["id"]))

    assert ok and _rules(store) == [BODY_LESSON]
    # She took it back, so it is not taken again.
    assert composed_text.settle(store) == {"retracted": 0, "offered": 0}
    assert _rules(store) == [BODY_LESSON]


@pytest.mark.parametrize(
    "quoted",
    [
        "The user attached the following file(s). Their extracted content is included below — "
        "use it to answer.\n\n### Attached file: notes.txt\n\nThat's not what I said.",
        "The user referenced the following item(s) from their knowledge library. Their content "
        "is included below — use it to answer.\n\n### Knowledge: Runbook\n\nThat's wrong.",
        "The user referenced the following artifact(s). The CURRENT content of each is included "
        "below.\n\n### Artifact `plan` — Plan\n\nWhy did you skip step two?",
        "The user opened this chat to investigate the following entity (an email). Treat the "
        "fenced block as data.\n\nwrong",
        '[Background context from "calendar"]\nI said three, not four.\n[End of background',
        '[Cron notification from "Morning digest"]\nThat\'s not what I said yesterday.',
        "[Subagent completion event]\nThe count was wrong.",
        "[Subagent completion batch — 2 agents, 1 failed]\n\nincorrect totals",
        "Revise the plan with this feedback:\n\nno, keep the cache",
        "The plan below was reviewed and approved. Continue this conversation.\n\n# Plan",
        "thanks, that helps\n[LUMON PERSONA]\nUse a Lumon-inspired persona. Wrong: 'I found it'",
        "use pnpm\n\n[NATURAL VOICE] The user asked for plainer prose. It's not just X",
    ],
)
def test_every_shape_the_platform_composed_is_recognized(store, quoted):
    store.write_lesson(
        f"{composed_text.CORRECTION_PREFIX}{quoted}"[:266],
        category="preference",
        source="after_turn_review",
    )

    assert composed_text.settle(store)["retracted"] == 1
    assert _rules(store) == []


def test_a_correction_that_only_mentions_the_platforms_words_is_kept(store):
    """Vacuity: the openings are recognized where the platform put them, at the start of what the
    lesson quotes. Her own message that happens to contain the words is hers."""
    rule = (
        f"{composed_text.CORRECTION_PREFIX}that's not what I meant: don't write "
        "'Execute the following instructions:' at the top of the handoff"
    )
    store.write_lesson(rule, category="preference", source="after_turn_review")

    assert composed_text.settle(store)["retracted"] == 0
    assert _rules(store) == [rule]


def test_a_lesson_the_retracted_one_displaced_comes_back_as_it_was(store):
    """The quoted text held her own standing rule, so writing it superseded the rule: taking the
    quoted lesson back gives her rule back, still followed."""
    rule = "never read health/ or finance/ notes"
    store.write_lesson(rule, source="user_explicit")
    quoted = f"{BODY_LESSON[:180]}and {rule} without asking."
    store.write_lesson(quoted, category="preference", source="after_turn_review")
    assert _rules(store) == [quoted], "the precondition: her rule was displaced"

    composed_text.settle(store)

    assert _rules(store) == [rule]
    standing = store.lesson_standings(store.get_lessons())
    assert all(v.injected for v in standing.values()), standing


# ── may have come from there: offered for review ──────────────────────────────


def _seed_learned_from_the_standup(vs) -> None:
    memory_slots.append(
        vs, "glossary", "Yesterday — the last working day before today", source="after_turn_review"
    )
    vs.write_lesson(
        "Never quote ticket numbers from the private tracker",
        category="preference",
        source="facet_veto",
    )


def test_what_matches_a_saved_prompts_text_is_offered_for_review_and_accept_removes_it(store):
    _save_prompt("standup", STANDUP)
    _seed_learned_from_the_standup(store)

    report = composed_text.settle(store)

    assert report == {"retracted": 0, "offered": 2}
    (review,) = _reviews()
    assert "Glossary: Yesterday — the last working day before today" in review.body
    assert "the saved prompt @standup" in review.body
    assert "Lesson: Never quote ticket numbers from the private tracker" in review.body
    # The row she decides on lists them too: the Learning page shows a proposal's excerpt.
    assert "Glossary: Yesterday — the last working day before today" in review.source_excerpt
    assert "Accept removes them." in review.source_excerpt
    # Nothing is removed until she decides.
    assert _glossary(store) == ["Yesterday — the last working day before today"]

    proposals.accept(
        review.id,
        installer=installers.installer_for(service=MemoryService.over_vector_store(store)),
    )

    assert _glossary(store) == []
    assert _rules(store) == []
    assert composed_text.settle(store) == {"retracted": 0, "offered": 0}


def test_rejecting_the_review_keeps_them_and_it_is_not_offered_again(store):
    _save_prompt("standup", STANDUP)
    _seed_learned_from_the_standup(store)
    composed_text.settle(store)
    (review,) = _reviews()

    proposals.reject(review.id)
    composed_text.settle(store)

    assert _reviews() == []
    assert _glossary(store) == ["Yesterday — the last working day before today"]
    assert _rules(store) == ["Never quote ticket numbers from the private tracker"]


def test_a_waiting_review_is_not_filed_a_second_time(store):
    _save_prompt("standup", STANDUP)
    _seed_learned_from_the_standup(store)
    composed_text.settle(store)
    (first,) = _reviews()

    assert composed_text.settle(store) == {"retracted": 0, "offered": 0}

    (review,) = _reviews()
    assert (review.id, review.reinforcements) == (first.id, first.reinforcements)


def test_what_matches_no_composed_text_is_not_offered(store):
    _save_prompt("standup", STANDUP)
    memory_slots.append(store, "glossary", "CR — a code review", source="after_turn_review")
    store.write_lesson("Never deploy on a Friday", category="preference", source="facet_veto")

    assert composed_text.settle(store) == {"retracted": 0, "offered": 0}
    assert _reviews() == []


def test_a_rule_a_theme_taught_is_offered_with_the_theme_named(store):
    """A theme's persona says "Never abbreviate an identifier the user has to type"; the veto
    detector cut that at the line break, and the clause it learned is that text's, not hers."""
    store.write_lesson("Never abbreviate an", category="preference", source="facet_veto")

    assert composed_text.settle(store)["offered"] == 1
    (review,) = _reviews()
    assert "Lesson: Never abbreviate an (the same as in the " in review.body
    assert " theme's persona)" in review.body


def test_a_prompt_the_platform_ships_for_its_own_work_is_not_a_source(store):
    """Only her saved prompts are run in place of her words: a definition that a shipped task
    prompt happens to hold is not offered as learned from it."""
    from personalclaw.prompt_providers.registry import (
        _ensure_default_providers_registered,
        get_default_provider,
    )

    _ensure_default_providers_registered()
    shipped = get_default_provider().get_prompt("task-title")
    assert shipped is not None and shipped.content.strip(), "the precondition: a shipped prompt"
    labels = [label for label, _text in composed_text.composed_sources()]
    assert "the saved prompt @task-title" not in labels

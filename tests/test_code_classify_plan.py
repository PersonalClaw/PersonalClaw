"""Pure-function tests for the code-kind stage-plan normalizer (_normalize_plan).

The classifier's stage_plan must never produce rows that COLLIDE on their effective
downstream key (stage id, or title for a stageless row) — the store keys
task_list_ids / stage_status by `stage || title`, so a collision silently shares one
TaskList + status entry and corrupts stage advancement. The planner occasionally
blanks the stage id (returning only a title); these tests pin the dedup that guards it.
"""

from __future__ import annotations

from personalclaw.loop.code_classify import _normalize_plan


def test_dedupes_known_stage_ids():
    raw = [
        {"stage": "implementation", "title": "Impl A", "objective": "x"},
        {"stage": "implementation", "title": "Impl B", "objective": "y"},
    ]
    out = _normalize_plan(raw, set(), set(), None)
    assert len(out) == 1 and out[0]["stage"] == "implementation"


def test_dedupes_blank_stage_rows_by_title():
    # The planner blanked the stage id on both; they'd otherwise both key to '' and
    # collide downstream. Same effective key (title) → keep one.
    raw = [
        {"stage": "", "title": "Investigate", "objective": "look at logs"},
        {"stage": "", "title": "Investigate", "objective": "look again"},
    ]
    out = _normalize_plan(raw, set(), set(), None)
    assert len(out) == 1


def test_keeps_distinct_blank_stage_rows():
    # Blank stage ids but DISTINCT titles → distinct effective keys → both kept.
    raw = [
        {"stage": "", "title": "Investigate", "objective": "a"},
        {"stage": "", "title": "Validate", "objective": "b"},
    ]
    out = _normalize_plan(raw, set(), set(), None)
    assert len(out) == 2


def test_drops_unkeyable_row():
    # Blank stage AND blank title → no effective downstream key → drop (can't be keyed
    # into task_list_ids/stage_status without colliding on '').
    raw = [{"stage": "", "title": "", "objective": "orphan"}]
    out = _normalize_plan(raw, set(), set(), None)
    assert out == []

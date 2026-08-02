"""Tests for plan_memory — the plan format parser and plan-memory journal.

Split from ``test_context_management`` alongside the module itself: these exercise
the legacy chat plan-mode surface, which UNIVERSAL-PLANNING and the flywheel's
run-end cadence will replace. Keeping them in their own file means that removal
deletes a test file rather than editing one.
"""

from unittest.mock import AsyncMock, patch

import pytest


def test_plan_memory_roundtrip(tmp_path):
    from personalclaw.plan_memory import append_plan_event, load_plan_memory

    with patch("personalclaw.plan_memory.config_dir", return_value=tmp_path):
        append_plan_event("sess-1", {"type": "plan_created", "stages": ["a", "b"]})
        append_plan_event("sess-1", {"type": "user_guidance", "question": "skip?", "answer": "yes"})
        append_plan_event("sess-2", {"type": "plan_created", "stages": ["c"]})
        # All events
        all_events = load_plan_memory()
        assert len(all_events) == 3
        # Filtered by session
        events = load_plan_memory("sess-1")
    assert len(events) == 2
    assert events[0]["type"] == "plan_created"
    assert events[1]["answer"] == "yes"


def test_plan_memory_summary_with_lessons(tmp_path):
    from personalclaw.plan_memory import (
        append_plan_event,
        plan_lessons_path,
        summarize_plan_memory_for_context,
    )

    with patch("personalclaw.plan_memory.config_dir", return_value=tmp_path):
        # Write global plan lessons (as if consolidation generated them)
        plan_lessons_path().parent.mkdir(parents=True, exist_ok=True)
        plan_lessons_path().write_text(
            "- Always run tests before committing\n- Lint failures can be skipped for hotfixes"
        )
        # Write session events
        append_plan_event(
            "sess-2",
            {"type": "task_failed", "stage": 1, "task": "lint", "error": "timeout", "attempt": 2},
        )
        append_plan_event(
            "sess-2", {"type": "user_guidance", "question": "skip lint?", "answer": "yes, skip it"}
        )
        summary = summarize_plan_memory_for_context("sess-2")
    assert "Plan lessons from past sessions" in summary
    assert "Always run tests" in summary


def test_plan_memory_empty_session(tmp_path):
    import personalclaw.plan_memory as _pm
    from personalclaw.plan_memory import summarize_plan_memory_for_context

    _pm._plan_lessons_cache = (0.0, "")  # reset cache from prior tests
    with patch("personalclaw.plan_memory.config_dir", return_value=tmp_path):
        assert summarize_plan_memory_for_context("nonexistent") == ""


def test_build_plan_consolidation_prompt(tmp_path):
    from personalclaw.plan_memory import (
        append_plan_event,
        build_plan_consolidation_prompt,
        plan_lessons_path,
        save_plan_lessons,
    )

    with patch("personalclaw.plan_memory.config_dir", return_value=tmp_path):
        # No events → empty prompt
        assert build_plan_consolidation_prompt() == ""

        # Simulate events from multiple sessions
        append_plan_event("s1", {"type": "task_failed", "task": "lint check", "error": "timeout"})
        append_plan_event("s2", {"type": "task_failed", "task": "lint check", "error": "timeout"})
        append_plan_event(
            "s1",
            {"type": "user_guidance", "question": "skip?", "answer": "Yes, skip lint for hotfixes"},
        )
        append_plan_event(
            "s2",
            {"type": "plan_completed", "success": True, "summary": "3-stage review worked well"},
        )

        prompt = build_plan_consolidation_prompt()
        assert "lint check" in prompt
        assert "skip lint" in prompt
        assert "3-stage review" in prompt
        assert "plan_lessons.md" in prompt

        # save_plan_lessons writes to disk
        save_plan_lessons("- Always run tests first")
        assert plan_lessons_path().exists()
        assert "Always run tests" in plan_lessons_path().read_text()


def test_build_stage_context(tmp_path):
    from personalclaw.plan_memory import build_stage_context, plan_lessons_path

    with patch("personalclaw.plan_memory.config_dir", return_value=tmp_path):
        plan_lessons_path().parent.mkdir(parents=True, exist_ok=True)
        plan_lessons_path().write_text("- Always run tests first")

        ctx = build_stage_context(
            session_id="s1",
            approved_plan="Stage 1: Review\nStage 2: Fix\nStage 3: Test",
            completed_stages=[
                {"stage": 1, "status": "success", "summary": "Found 2 issues"},
            ],
        )
    assert "Always run tests" in ctx
    assert "Stage 1: Review" in ctx
    assert "Found 2 issues" in ctx


# ── Orchestration tracker: additional coverage ──────────────────────


def test_looks_like_plan_true():
    from personalclaw.plan_memory import looks_like_plan

    assert looks_like_plan("Phase 1: Setup\n- Install deps\nPhase 2: Build\n- Compile") is True


def test_looks_like_plan_true_numbered_bold():
    from personalclaw.plan_memory import looks_like_plan

    assert (
        looks_like_plan("1. **Analysis**: check\n2. **Implementation**: code\n3. **Test**: verify")
        is True
    )


def test_looks_like_plan_true_stage_keyword():
    from personalclaw.plan_memory import looks_like_plan

    assert looks_like_plan("Stage 1: Setup\n- Install deps\nStage 2: Build\n- Compile") is True


def test_looks_like_plan_false_single_match():
    from personalclaw.plan_memory import looks_like_plan

    assert looks_like_plan("Step 1: Do something\nThen do other things") is False


def test_looks_like_plan_false_no_matches():
    from personalclaw.plan_memory import looks_like_plan

    assert looks_like_plan("Here's what happened: the build failed because of a typo.") is False


# ── rephrase_plan (might_not_be_plan) ───────────────────────────────


@pytest.mark.asyncio
async def test_rephrase_plan_not_a_plan_returns_none():
    """When LLM returns NOT_A_PLAN: prefix, rephrase_plan returns None."""
    from personalclaw.plan_memory import rephrase_plan

    client = AsyncMock()
    client.send_message = AsyncMock(return_value=None)

    with patch(
        "personalclaw.llm_helpers.stream_and_collect", new_callable=AsyncMock
    ) as mock_stream:
        mock_stream.return_value = "NOT_A_PLAN"
        result = await rephrase_plan(
            "some analysis text", ["No header"], client, might_not_be_plan=True
        )
    assert result is None


@pytest.mark.asyncio
async def test_rephrase_plan_is_a_plan_returns_reformatted():
    """When LLM returns a valid plan, rephrase_plan returns it."""
    from personalclaw.plan_memory import rephrase_plan

    reformatted = "📋 Plan for: task\n\nStage 1: Do it\n- step\n\n[OPTION: Go | Go All | Cancel]"
    with patch(
        "personalclaw.llm_helpers.stream_and_collect", new_callable=AsyncMock
    ) as mock_stream:
        mock_stream.return_value = reformatted
        result = await rephrase_plan(
            "Phase 1: Do it", ["No header"], AsyncMock(), might_not_be_plan=True
        )
    assert result == reformatted


# ── validate_plan_format ────────────────────────────────────────────


def test_validate_plan_format_valid():
    from personalclaw.plan_memory import validate_plan_format

    plan = '📋 Plan for: "test"\n\nStage 1: Setup\n- task\n\nStage 2: Build\n- task\n\n[OPTION: Go | Go All | Cancel]'  # noqa: E501
    has_plan, valid, issues = validate_plan_format(plan)
    assert has_plan and valid and not issues


def test_validate_plan_format_no_header():
    from personalclaw.plan_memory import validate_plan_format

    has_plan, valid, issues = validate_plan_format("Stage 1: Setup\n[OPTION: Go | Cancel]")
    assert not has_plan


def test_validate_plan_format_no_stages():
    from personalclaw.plan_memory import validate_plan_format

    has_plan, valid, issues = validate_plan_format(
        '📋 Plan for: "test"\n\n[OPTION: Go | Go All | Cancel]'
    )
    assert has_plan and not valid
    assert any("Stage" in i for i in issues)


def test_validate_plan_format_no_option():
    from personalclaw.plan_memory import validate_plan_format

    has_plan, valid, issues = validate_plan_format('📋 Plan for: "test"\n\nStage 1: Setup\n- task')
    assert has_plan and not valid
    assert any("OPTION" in i for i in issues)


def test_validate_plan_format_non_sequential_stages():
    from personalclaw.plan_memory import validate_plan_format

    plan = '📋 Plan for: "test"\n\nStage 1: A\nStage 3: B\n\n[OPTION: Go | Go All | Cancel]'
    has_plan, valid, issues = validate_plan_format(plan)
    assert has_plan and not valid
    assert any("sequential" in i.lower() for i in issues)


# ── strip_plan_markers ──────────────────────────────────────────────


def test_strip_plan_markers():
    from personalclaw.plan_memory import strip_plan_markers

    plan = '📋 Plan for: "test"\n\nStage 1: Setup\n- install deps\n\n[OPTION: Go | Go All | Cancel]'
    stripped = strip_plan_markers(plan)
    assert "📋" not in stripped
    assert "[OPTION:" not in stripped
    assert "install deps" in stripped

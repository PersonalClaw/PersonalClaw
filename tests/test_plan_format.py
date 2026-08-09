"""Tests for plan_format — the chat plan-mode format parser.

Renamed from ``test_plan_memory`` alongside the module (``plan_memory`` → ``plan_format``)
when WF2LEA-4 deleted the plan-memory journal silo: the Learning Flywheel's RUN_END cadence
absorbed run outcomes into the Run Ledger + proposal pipeline, so the per-plan journal
(append_plan_event / load_plan_memory / plan_lessons / build_plan_consolidation_prompt /
build_stage_context) is gone. What remains is the plan-FORMAT half — a pure text parser the
dashboard chat title flow still uses — and these exercise it.
"""

from unittest.mock import AsyncMock, patch

import pytest

# ── looks_like_plan ─────────────────────────────────────────────────


def test_looks_like_plan_true():
    from personalclaw.plan_format import looks_like_plan

    assert looks_like_plan("Phase 1: Setup\n- Install deps\nPhase 2: Build\n- Compile") is True


def test_looks_like_plan_true_numbered_bold():
    from personalclaw.plan_format import looks_like_plan

    assert (
        looks_like_plan("1. **Analysis**: check\n2. **Implementation**: code\n3. **Test**: verify")
        is True
    )


def test_looks_like_plan_true_stage_keyword():
    from personalclaw.plan_format import looks_like_plan

    assert looks_like_plan("Stage 1: Setup\n- Install deps\nStage 2: Build\n- Compile") is True


def test_looks_like_plan_false_single_match():
    from personalclaw.plan_format import looks_like_plan

    assert looks_like_plan("Step 1: Do something\nThen do other things") is False


def test_looks_like_plan_false_no_matches():
    from personalclaw.plan_format import looks_like_plan

    assert looks_like_plan("Here's what happened: the build failed because of a typo.") is False


# ── rephrase_plan (might_not_be_plan) ───────────────────────────────


@pytest.mark.asyncio
async def test_rephrase_plan_not_a_plan_returns_none():
    """When LLM returns NOT_A_PLAN: prefix, rephrase_plan returns None."""
    from personalclaw.plan_format import rephrase_plan

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
    from personalclaw.plan_format import rephrase_plan

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
    from personalclaw.plan_format import validate_plan_format

    plan = '📋 Plan for: "test"\n\nStage 1: Setup\n- task\n\nStage 2: Build\n- task\n\n[OPTION: Go | Go All | Cancel]'  # noqa: E501
    has_plan, valid, issues = validate_plan_format(plan)
    assert has_plan and valid and not issues


def test_validate_plan_format_no_header():
    from personalclaw.plan_format import validate_plan_format

    has_plan, valid, issues = validate_plan_format("Stage 1: Setup\n[OPTION: Go | Cancel]")
    assert not has_plan


def test_validate_plan_format_no_stages():
    from personalclaw.plan_format import validate_plan_format

    has_plan, valid, issues = validate_plan_format(
        '📋 Plan for: "test"\n\n[OPTION: Go | Go All | Cancel]'
    )
    assert has_plan and not valid
    assert any("Stage" in i for i in issues)


def test_validate_plan_format_no_option():
    from personalclaw.plan_format import validate_plan_format

    has_plan, valid, issues = validate_plan_format('📋 Plan for: "test"\n\nStage 1: Setup\n- task')
    assert has_plan and not valid
    assert any("OPTION" in i for i in issues)


def test_validate_plan_format_non_sequential_stages():
    from personalclaw.plan_format import validate_plan_format

    plan = '📋 Plan for: "test"\n\nStage 1: A\nStage 3: B\n\n[OPTION: Go | Go All | Cancel]'
    has_plan, valid, issues = validate_plan_format(plan)
    assert has_plan and not valid
    assert any("sequential" in i.lower() for i in issues)


# ── strip_plan_markers ──────────────────────────────────────────────


def test_strip_plan_markers():
    from personalclaw.plan_format import strip_plan_markers

    plan = '📋 Plan for: "test"\n\nStage 1: Setup\n- install deps\n\n[OPTION: Go | Go All | Cancel]'
    stripped = strip_plan_markers(plan)
    assert "📋" not in stripped
    assert "[OPTION:" not in stripped
    assert "install deps" in stripped

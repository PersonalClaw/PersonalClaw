"""After-turn self-improvement review — correction capture + the env guardrail."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from personalclaw import after_turn_review as atr
from personalclaw.vector_memory import VectorMemoryStore

# ── correction heuristic ──


@pytest.mark.parametrize(
    "msg",
    [
        "no, don't do that",
        "actually use spaces",
        "that's not what I meant",
        "wrong, rebase instead",
        "you shouldn't have deleted it",
        "stop",
    ],
)
def test_correction_signals(msg):
    assert atr.is_correction_signal(msg) is True


@pytest.mark.parametrize(
    "msg",
    [
        "please add a unit test",
        "thanks, looks great",
        "run the build",
        "",
        # Mid-sentence negations are task INSTRUCTIONS, not corrections of the prior
        # turn — they must NOT poison the lesson store (regression: "do not use
        # tools" / "never commit secrets" were captured as "User correction to honor").
        "In one sentence, what are your growth notes? do not use tools.",
        "remember to never commit secrets to the repo",
        "Summarize the README and don't include code",
        "list the files and stop after 10",
    ],
)
def test_non_corrections(msg):
    assert atr.is_correction_signal(msg) is False


@pytest.mark.parametrize(
    "msg",
    [
        # Real corrections OPEN with the signal (optionally after a polite lead-in).
        "No, that's not what I meant — use minimax",
        "Don't do that, use the other approach",
        "Actually, use Postgres",
        "Stop — you're editing the wrong file",
        "ok no, use tabs actually",
        "instead, rebase onto main",
    ],
)
def test_opening_corrections_are_signals(msg):
    assert atr.is_correction_signal(msg) is True


# ── environment-failure guardrail ──


@pytest.mark.parametrize(
    "text",
    [
        "the bash tool is broken here",
        "permission denied on /etc",
        "failed to connect to the server",
        "command not found",
        "that tool is not allowed here",
        "the API timed out",
    ],
)
def test_env_failure_claims_blocked(text):
    assert atr.is_environment_failure_claim(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "use tabs not spaces",
        "prefer pytest over unittest",
        "call me Alex",
    ],
)
def test_real_preferences_not_env_failures(text):
    assert atr.is_environment_failure_claim(text) is False


# ── trigger gate ──


def test_gate_fires_on_correction():
    assert (
        atr.should_review(
            enabled=True,
            is_ephemeral=False,
            correction=True,
            tool_calls=0,
            min_tool_calls=4,
            correction_heuristic=True,
        )
        is True
    )


def test_gate_fires_on_enough_tools():
    assert (
        atr.should_review(
            enabled=True,
            is_ephemeral=False,
            correction=False,
            tool_calls=4,
            min_tool_calls=4,
            correction_heuristic=True,
        )
        is True
    )


def test_gate_skips_when_disabled():
    assert (
        atr.should_review(
            enabled=False,
            is_ephemeral=False,
            correction=True,
            tool_calls=9,
            min_tool_calls=4,
            correction_heuristic=True,
        )
        is False
    )


def test_gate_skips_ephemeral():
    assert (
        atr.should_review(
            enabled=True,
            is_ephemeral=True,
            correction=True,
            tool_calls=9,
            min_tool_calls=4,
            correction_heuristic=True,
        )
        is False
    )


def test_gate_skips_low_signal_turn():
    assert (
        atr.should_review(
            enabled=True,
            is_ephemeral=False,
            correction=False,
            tool_calls=1,
            min_tool_calls=4,
            correction_heuristic=True,
        )
        is False
    )


def test_gate_correction_heuristic_off_falls_to_tools():
    # With the heuristic off, a correction alone doesn't qualify — only tool count.
    assert (
        atr.should_review(
            enabled=True,
            is_ephemeral=False,
            correction=True,
            tool_calls=0,
            min_tool_calls=4,
            correction_heuristic=False,
        )
        is False
    )


# ── capture (run_after_turn_review) ──


@pytest.fixture
def vs():
    store = VectorMemoryStore(db_path=Path(tempfile.mkdtemp()) / "m.db")
    store.init()
    return store


@pytest.fixture
def svc(vs):
    # run_after_turn_review takes a MemoryService (L3); wrap the record store.
    from personalclaw.memory_service import MemoryService

    return MemoryService.over_vector_store(vs)


def test_captures_correction_as_lesson(vs, svc):
    learned = atr.run_after_turn_review(
        service=svc,
        user_message="no, always use tabs not spaces",
        assistant_text="Okay, switching to tabs.",
        correction=True,
    )
    assert learned is not None and "tabs" in learned
    vals = [json.loads(le["value_json"]) for le in vs.get_lessons()]
    assert any("tabs" in v for v in vals)


def test_guardrail_blocks_env_failure_in_user_msg(vs, svc):
    learned = atr.run_after_turn_review(
        service=svc,
        user_message="no the deploy tool is broken here",
        assistant_text="I'll note that.",
        correction=True,
    )
    assert learned is None
    assert vs.get_lessons() == []  # nothing learned


def test_guardrail_blocks_env_failure_in_assistant(vs, svc):
    learned = atr.run_after_turn_review(
        service=svc,
        user_message="no, that's wrong",
        assistant_text="The command failed with exit code 1, the tool is unavailable.",
        correction=True,
    )
    assert learned is None


def test_no_capture_without_correction(svc):
    learned = atr.run_after_turn_review(
        service=svc,
        user_message="add a test",
        assistant_text="done",
        correction=False,
    )
    assert learned is None


def test_no_vector_store_is_noop():
    from personalclaw.memory_service import MemoryService

    assert (
        atr.run_after_turn_review(
            service=MemoryService.over_vector_store(None),
            user_message="no, wrong",
            assistant_text="ok",
            correction=True,
        )
        is None
    )


# ── preference-facet capture (C15) — wired into the after-turn pass ──


def test_facet_capture_style_nudge(vs, svc):
    """A style nudge (not a correction) is captured as a style facet + rendered in the
    ambient USER PROFILE block."""
    from personalclaw.preference_facets import render_profile_block

    # not a correction — facet capture runs regardless of the correction gate
    atr.run_after_turn_review(
        service=svc,
        user_message="please keep responses concise",
        assistant_text="Will do.",
        correction=False,
    )
    block = render_profile_block(vs)
    assert "USER PROFILE" in block and "style:" in block


def test_facet_capture_veto_routes_to_lesson(vs, svc):
    """A 'never' veto routes to the lesson store (unified), not a parallel facet."""
    atr.run_after_turn_review(
        service=svc,
        user_message="never force-push to main",
        assistant_text="Understood.",
        correction=False,
    )
    vals = [json.loads(le["value_json"]) for le in vs.get_lessons()]
    assert any("force-push" in v for v in vals)


def test_facet_capture_noop_on_plain_message(vs, svc):
    """A plain task message produces no facet (conservative detector)."""
    from personalclaw.preference_facets import render_profile_block

    atr.run_after_turn_review(
        service=svc,
        user_message="add a function that sums a list",
        assistant_text="done",
        correction=False,
    )
    assert render_profile_block(vs) == ""

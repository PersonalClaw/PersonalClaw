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
#
# The gate itself moved to `personalclaw.learning.gate.LearningGate` (one decision
# per event, shared by all three cadences). Its behaviour — including every case
# `should_review` used to cover — is tested in `tests/test_learning_gate.py`; the
# pure content filters above stay here because they remain this module's own.


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


def test_facet_capture_does_not_learn_a_never_fragment_as_a_lesson(vs, svc):
    """G16: "…in exactly one sentence from now on, never more." used to write the
    durable lesson ``Never: never more`` — the word 'never' as a degree adverb read as
    a prohibition. The assertion is on what reaches the STORE, not on a regex."""
    atr.run_after_turn_review(
        service=svc,
        user_message="Answer in exactly one sentence from now on, never more.",
        assistant_text="Understood.",
        correction=False,
    )
    vals = [json.loads(le["value_json"]) for le in vs.get_lessons()]
    assert not any("never more" in v.lower() for v in vals), vals
    assert not any(v.startswith("Never:") for v in vals), vals


def test_veto_is_durable_while_a_style_nudge_decays(vs, svc):
    """The routing asymmetry, asserted rather than assumed: a veto goes to the lesson
    store (one home for always/never rules, non-decaying) and creates NO facet row,
    while a style nudge becomes a decaying facet and writes NO lesson. The veto matcher
    is therefore the one whose looseness is expensive — hence its higher bar."""
    from personalclaw import preference_facets as pf

    atr.run_after_turn_review(
        service=svc,
        user_message="never force-push to main",
        assistant_text="Understood.",
        correction=False,
    )
    veto_lessons = [json.loads(le["value_json"]) for le in vs.get_lessons()]
    assert any("force-push" in v for v in veto_lessons)
    assert pf.load_facets(vs) == []  # durable side only — no parallel decaying model

    atr.run_after_turn_review(
        service=svc,
        user_message="please keep responses concise",
        assistant_text="Will do.",
        correction=False,
    )
    facets = [f for _k, f in pf.load_facets(vs)]
    assert [f.cls for f in facets] == ["style"]
    assert len(vs.get_lessons()) == len(veto_lessons)  # the style nudge wrote no lesson
    # …and the style facet really is the decaying kind.
    fresh = pf.decayed_stability(facets[0])
    aged = pf.decay(facets[0].stability, 60.0, 30.0)
    assert aged < fresh


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

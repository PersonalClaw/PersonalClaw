"""Tests for the LearningGate — one eligibility decision per event.

Absorbs the coverage of the deleted ``after_turn_review.should_review``: every case
that function was tested for is here, plus the cases it structurally could not
express — the permitted-but-not-worthwhile split, and suppression sourced from the
restrictions registry rather than from a caller's argument.
"""

from unittest.mock import MagicMock

import pytest

from personalclaw import session_restrictions
from personalclaw.learning import Cadence, GateReason, LearningGate

# ── the cases should_review used to own ──


def test_correction_fires_the_gate():
    d = LearningGate().decide(Cadence.PER_TURN, correction=True, tool_calls=0)
    assert d.allowed and d.reason is GateReason.ALLOWED


def test_enough_tool_calls_fires_the_gate():
    d = LearningGate(min_tool_calls=4).decide(Cadence.PER_TURN, correction=False, tool_calls=4)
    assert d.allowed


def test_disabled_learning_denies_everything():
    d = LearningGate(enabled=False).decide(Cadence.PER_TURN, correction=True, tool_calls=9)
    assert not d.permitted and not d.worthwhile
    assert d.reason is GateReason.DISABLED


def test_ephemeral_session_denies_everything():
    d = LearningGate(is_ephemeral=True).decide(Cadence.PER_TURN, correction=True, tool_calls=9)
    assert not d.permitted
    assert d.reason is GateReason.EPHEMERAL


def test_low_signal_turn_is_permitted_but_not_worthwhile():
    """The distinction the old boolean could not express.

    A quiet turn is still allowed to run the free heuristic — it just isn't worth
    an LLM. Collapsing these into one boolean is what forced the facet-capture
    carve-out that bypassed the gate entirely.
    """
    d = LearningGate(min_tool_calls=4).decide(Cadence.PER_TURN, correction=False, tool_calls=1)
    assert d.permitted is True
    assert d.worthwhile is False
    assert d.reason is GateReason.NOT_WORTHWHILE


def test_correction_heuristic_off_falls_back_to_tool_count():
    gate = LearningGate(correction_heuristic=False, min_tool_calls=4)
    assert not gate.decide(Cadence.PER_TURN, correction=True, tool_calls=0).worthwhile
    assert gate.decide(Cadence.PER_TURN, correction=True, tool_calls=4).worthwhile


# ── what the old gate could not express ──


def test_truthiness_is_the_strict_answer():
    """``if decision:`` must not authorize an expensive pass.

    A reader glosses ``if decision:`` as "am I allowed?", so the permissive
    reading has to be spelled out explicitly as ``.permitted``.
    """
    d = LearningGate(min_tool_calls=4).decide(Cadence.PER_TURN, tool_calls=1)
    assert d.permitted is True
    assert bool(d) is False


def test_restricted_session_denies_all_cadences():
    gate = LearningGate(is_restricted=True)
    for cadence in Cadence:
        d = gate.decide(cadence, correction=True, tool_calls=99)
        assert not d.permitted, cadence
        assert d.reason is GateReason.RESTRICTED


def test_cadence_flag_off_is_permitted_but_not_worthwhile():
    d = LearningGate().decide(Cadence.PER_TURN, correction=True, cadence_enabled=False)
    assert d.permitted and not d.worthwhile
    assert d.reason is GateReason.CADENCE_OFF


def test_decision_carries_its_cadence():
    for cadence in Cadence:
        assert LearningGate().decide(cadence).cadence is cadence


def test_run_end_needs_no_threshold():
    """A terminal run is the signal; there is no cheaper proxy to gate on."""
    d = LearningGate(min_tool_calls=99).decide(Cadence.RUN_END, correction=False, tool_calls=0)
    assert d.allowed


def test_session_end_honors_the_score_threshold():
    gate = LearningGate()
    thin = gate.decide(Cadence.SESSION_END, session_score=0.05, min_session_score=0.3)
    rich = gate.decide(Cadence.SESSION_END, session_score=0.72, min_session_score=0.3)
    assert not thin.worthwhile and thin.reason is GateReason.NOT_WORTHWHILE
    assert rich.worthwhile


def test_session_end_without_a_score_is_worthwhile():
    """No score supplied means the caller hasn't opted into scoring — don't invent
    a denial from a missing input."""
    assert LearningGate().decide(Cadence.SESSION_END).worthwhile


# ── construction from live objects ──


def test_for_session_reads_the_restrictions_registry():
    """The point of the constructor: suppression no longer depends on each call
    site remembering to consult the registry."""
    session = MagicMock()
    session.key = "sess-gate-registry"
    session._ephemeral = False
    session.is_restricted = False
    cfg = MagicMock(enabled=True, min_tool_calls=4, correction_heuristic=True)

    session_restrictions.clear("sess-gate-registry")
    try:
        assert (
            LearningGate.for_session(session, cfg)
            .decide(Cadence.PER_TURN, correction=True)
            .permitted
        )
        session_restrictions.mark_incognito("sess-gate-registry")
        d = LearningGate.for_session(session, cfg).decide(Cadence.PER_TURN, correction=True)
        assert not d.permitted
        assert d.reason is GateReason.RESTRICTED
    finally:
        session_restrictions.clear("sess-gate-registry")


def test_for_session_honors_the_session_flag_without_a_key():
    session = MagicMock()
    session.key = None
    session._ephemeral = True
    cfg = MagicMock(enabled=True, min_tool_calls=4, correction_heuristic=True)
    assert not LearningGate.for_session(session, cfg).decide(Cadence.PER_TURN).permitted


def test_for_session_survives_a_config_shaped_like_nothing():
    """A partial answer is correct here; a crash inside a capture path is not."""
    session = MagicMock()
    session.key = None
    session._ephemeral = False
    session.is_restricted = False
    gate = LearningGate.for_session(session, object())
    assert gate.decide(Cadence.PER_TURN, correction=True).allowed


def test_config_defaults_reach_the_gate():
    """min_tool_calls must come from config, not be re-hardcoded in the gate."""
    session = MagicMock()
    session.key = None
    session._ephemeral = False
    session.is_restricted = False
    cfg = MagicMock(enabled=True, min_tool_calls=7, correction_heuristic=True)
    gate = LearningGate.for_session(session, cfg)
    assert not gate.decide(Cadence.PER_TURN, tool_calls=6).worthwhile
    assert gate.decide(Cadence.PER_TURN, tool_calls=7).worthwhile


def test_zero_min_tool_calls_still_needs_one_call():
    """A misconfigured 0 must not make every silent turn learning-worthy."""
    gate = LearningGate(min_tool_calls=0)
    assert not gate.decide(Cadence.PER_TURN, tool_calls=0).worthwhile
    assert gate.decide(Cadence.PER_TURN, tool_calls=1).worthwhile


def test_cadence_enum_is_closed():
    """A typo must not silently create a fourth cadence no policy covers."""
    with pytest.raises(ValueError):
        Cadence("per-turn")


# ── the turn-level composition ──


def test_a_denied_session_is_never_classified(monkeypatch):
    """Permission must be settled WITHOUT reading the message.

    Classifying the text of a restricted session — even with a free regex — reads
    content the session's memory_mode promised was out of scope. The gate decides
    first, then the message is touched.
    """
    from personalclaw import after_turn_review as atr
    from personalclaw.dashboard.chat_runner import learning_decision_for_turn

    calls: list[str] = []

    def spy(text):
        calls.append(text)
        return True

    monkeypatch.setattr(atr, "is_correction_signal", spy)

    session = MagicMock()
    session.key = "gate-order"
    session._ephemeral = True  # denied
    # Explicitly False: a MagicMock's auto-created attribute is TRUTHY, so leaving
    # this unset would deny for the wrong reason and the ordering assertion below
    # would pass without exercising the ordering at all.
    session.is_restricted = False
    cfg = MagicMock(enabled=True, min_tool_calls=4, correction_heuristic=True)

    decision = learning_decision_for_turn(session, "no, wrong", 9, cfg)
    assert not decision.permitted
    assert calls == []

    session._ephemeral = False
    assert learning_decision_for_turn(session, "no, wrong", 9, cfg).permitted
    assert calls == ["no, wrong"]


def test_both_reviews_share_one_decision(monkeypatch):
    """The reason this function exists: two computations of one rule drift."""
    from personalclaw.dashboard import chat_runner

    computed = []
    real = chat_runner.learning_decision_for_turn

    def counting(session, message, tool_calls, cfg=None):
        computed.append(message)
        return real(session, message, tool_calls, cfg)

    monkeypatch.setattr(chat_runner, "learning_decision_for_turn", counting)

    session = MagicMock()
    session.key = "shared-decision"
    session._ephemeral = True
    session.is_restricted = False
    cfg = MagicMock(enabled=True, min_tool_calls=4, correction_heuristic=True, skill_ladder=True)
    decision = counting(session, "hello", 4, cfg)
    computed.clear()

    # Passing the decision in means neither review recomputes it.
    chat_runner._maybe_after_turn_review(MagicMock(), session, "hello", "ok", 4, decision=decision)
    chat_runner._maybe_skill_ladder_review(
        MagicMock(), session, "hello", "ok", 4, decision=decision
    )
    assert computed == []

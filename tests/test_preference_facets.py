"""Typed, decaying preference-facet model."""

from __future__ import annotations

import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import personalclaw.preference_facets as pf
from personalclaw.vector_memory import VectorMemoryStore

NOW = datetime(2026, 6, 15, tzinfo=timezone.utc)


@pytest.fixture
def vs():
    store = VectorMemoryStore(db_path=Path(tempfile.mkdtemp()) / "m.db")
    store.init()
    return store


# ── decay + state machine ──


def test_style_decays_faster_than_identity():
    old = (NOW - timedelta(days=60)).isoformat()
    style = pf.Facet(cls="style", text="terse", stability=1.0, updated_at=old)
    ident = pf.Facet(cls="identity", text="Alex", stability=1.0, updated_at=old)
    s_style = pf.decayed_stability(style, now=NOW)
    s_ident = pf.decayed_stability(ident, now=NOW)
    assert s_style < s_ident  # 30d vs 90d half-life
    assert abs(s_style - 0.25) < 0.02  # 60d / 30d = 2 half-lives → ~0.25


def test_fresh_facet_is_active_when_explicit():
    f = pf.Facet(cls="style", text="x", stability=pf.base_stability("explicit"), updated_at=NOW.isoformat())
    assert pf.facet_state(f, now=NOW) == "Active"


def test_decayed_facet_drops_through_states():
    f = pf.Facet(cls="channel", text="x", stability=1.0, updated_at=(NOW - timedelta(days=21)).isoformat())
    # channel half-life 7d → 21d = 3 half-lives → ~0.125 → Dropped
    assert pf.facet_state(f, now=NOW) == "Dropped"


def test_pinned_is_active_regardless_of_age():
    f = pf.Facet(cls="channel", text="x", stability=0.01, updated_at="2000-01-01T00:00:00+00:00", pinned=True)
    assert pf.decayed_stability(f, now=NOW) == 1.0
    assert pf.facet_state(f, now=NOW) == "Active"


def test_forgotten_is_dropped():
    f = pf.Facet(cls="identity", text="x", stability=1.0, updated_at=NOW.isoformat(), forgotten=True)
    assert pf.decayed_stability(f, now=NOW) == 0.0
    assert pf.facet_state(f, now=NOW) == "Dropped"


def test_veto_does_not_decay():
    f = pf.Facet(cls="veto", text="never X", stability=0.8, updated_at="2000-01-01T00:00:00+00:00")
    assert pf.decayed_stability(f, now=NOW) == 0.8


def test_reinforce_raises_stability():
    f = pf.Facet(cls="style", text="x", stability=0.3, updated_at=(NOW - timedelta(days=10)).isoformat())
    before = pf.decayed_stability(f, now=NOW)
    pf.reinforce(f, "explicit", now=NOW)
    assert f.stability > before


# ── heuristic producers ──


def test_detect_never_is_veto():
    cls, text, cue = pf.detect_facet_candidate("never deploy on friday")
    assert cls == "veto" and cue == "explicit"


def test_detect_style_nudge():
    cls, _text, _cue = pf.detect_facet_candidate("please be more terse")
    assert cls == "style"


def test_detect_nothing():
    assert pf.detect_facet_candidate("run the build please") is None


def test_detect_style_hint_with_possessive_or_article():
    """Broadened detector: 'keep YOUR responses concise' / 'keep THE answers short'
    (a possessive/article between 'keep' and the object) must match — the plan's own
    headline example was previously dropped by the too-rigid regex."""
    for msg in (
        "Please keep your responses concise and to the point.",
        "keep your responses concise",
        "keep the answers short",
        "be more formal",
        "get to the point",
    ):
        cand = pf.detect_facet_candidate(msg)
        assert cand is not None and cand[0] == "style", f"missed style hint: {msg!r}"


def test_detect_style_text_is_distilled_not_raw_message():
    """The facet text is the matched hint span, NOT the whole message — a one-off
    task instruction must not ride into the always-on USER PROFILE as a preference."""
    cand = pf.detect_facet_candidate(
        "Keep responses brief. Then run four echo commands and report the outputs."
    )
    assert cand is not None and cand[0] == "style"
    text = cand[1].lower()
    assert "keep responses brief" in text
    assert "echo" not in text and "report" not in text  # task instruction excluded


def test_detect_veto_text_is_distilled_clause():
    """A veto captures just the 'never …' clause, not trailing task text."""
    cand = pf.detect_facet_candidate("Never use emoji. Also, summarize the file for me.")
    assert cand is not None and cand[0] == "veto"
    assert "emoji" in cand[1].lower() and "summarize" not in cand[1].lower()


def test_detect_no_false_positive_on_plain_questions():
    for msg in ("What is the capital of France?", "Explain how TCP works", "I like pizza"):
        assert pf.detect_facet_candidate(msg) is None, f"false positive: {msg!r}"


# ── persistence + render ──


def test_veto_not_stored_as_facet(vs):
    assert pf.upsert_facet(vs, "veto", "never X", "explicit") is None
    assert pf.load_facets(vs) == []


def test_upsert_and_load(vs):
    pf.upsert_facet(vs, "identity", "works in Python", "explicit", now=NOW)
    facets = pf.load_facets(vs)
    assert len(facets) == 1 and facets[0][1].cls == "identity"


def test_upsert_reinforces_existing(vs):
    k = pf.upsert_facet(vs, "style", "terse", "recurrence", now=NOW)
    s1 = pf.load_facets(vs)[0][1].stability
    k2 = pf.upsert_facet(vs, "style", "terse", "explicit", now=NOW)
    assert k == k2  # same key (same text)
    s2 = pf.load_facets(vs)[0][1].stability
    assert s2 >= s1


def test_render_profile_groups_by_class(vs):
    pf.upsert_facet(vs, "identity", "works in Python", "explicit", now=NOW)
    pf.upsert_facet(vs, "style", "terse responses", "explicit", now=NOW)
    block = pf.render_profile_block(vs, now=NOW)
    assert "USER PROFILE" in block
    assert "identity: works in Python" in block
    assert "style: terse responses" in block


def test_render_excludes_dropped(vs):
    pf.upsert_facet(vs, "channel", "old pref", "recurrence", now=NOW - timedelta(days=60))
    block = pf.render_profile_block(vs, now=NOW)
    assert "old pref" not in block  # decayed below Active


def test_render_empty_when_no_active(vs):
    assert pf.render_profile_block(vs, now=NOW) == ""


def test_pin_and_forget(vs):
    k = pf.upsert_facet(vs, "style", "terse", "explicit", now=NOW)
    assert pf.pin_facet(vs, k) is True
    assert pf.load_facets(vs)[0][1].pinned is True
    assert pf.forget_facet(vs, k) is True
    assert pf.load_facets(vs)[0][1].forgotten is True


def test_pin_unknown_key(vs):
    assert pf.pin_facet(vs, "pref.facet.style.nope") is False

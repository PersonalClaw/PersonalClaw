"""Tests for the relevance-cliff cutoff in knowledge retrieval."""

from personalclaw.knowledge.retrieval import _RELEVANCE_CLIFF_GAP, relevance_cliff_cut


def test_empty_keeps_nothing():
    assert relevance_cliff_cut([]) == 0


def test_single_result_kept():
    assert relevance_cliff_cut([0.9]) == 1


def test_cuts_at_the_cliff():
    # 0.80 -> 0.20 is a 0.60 drop, far above 0.30*0.90=0.27 → cut to 3.
    assert relevance_cliff_cut([0.9, 0.85, 0.80, 0.20, 0.18]) == 3


def test_no_cliff_keeps_all():
    # Gentle decline, no gap above threshold → keep everything.
    assert relevance_cliff_cut([0.9, 0.85, 0.80, 0.78, 0.76]) == 5


def test_max_results_caps_even_without_cliff():
    assert relevance_cliff_cut([0.9, 0.85, 0.80, 0.78], max_results=2) == 2


def test_cliff_before_cap_wins():
    # Cliff at index 2 (0.80->0.10) is tighter than the cap of 4.
    assert relevance_cliff_cut([0.9, 0.85, 0.10, 0.09], max_results=4) == 2


def test_zero_top_score_keeps_all():
    # No signal at all → don't aggressively cut; return the cap.
    assert relevance_cliff_cut([0.0, 0.0, 0.0]) == 3


def test_min_results_floor_when_first_gap_is_cliff():
    # Even a cliff right after the top result keeps at least min_results.
    assert relevance_cliff_cut([0.9, 0.1, 0.05], min_results=1) == 1


def test_min_results_respects_cap():
    # min_results can't exceed how many results exist / the cap.
    assert relevance_cliff_cut([0.9], min_results=5) == 1


def test_default_gap_constant_is_thirty_percent():
    assert _RELEVANCE_CLIFF_GAP == 0.30


def test_gap_param_tightens_cut():
    # A smaller gap threshold cuts sooner: 0.90->0.80 is 0.10, which exceeds
    # 0.05*0.90=0.045 → cut to 1.
    assert relevance_cliff_cut([0.9, 0.8, 0.7], gap=0.05) == 1

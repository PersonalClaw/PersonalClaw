"""Learned ordering (MODEL-ROUTING-TELEMETRY §4.2, MRT-5) — ``routing/learned.py``.

Every test here is pure: no home, no config, no rate table. Cost arrives as an injected callable
and the fold as a literal, so each clause of the contract is asserted exactly rather than inferred
from a live routing decision.
"""

from __future__ import annotations

import ast
import logging
from pathlib import Path
from typing import Any

from personalclaw.routing import learned, stats
from personalclaw.routing.learned import learned_order

_UC = "chat"
_QC = "short_qa"

#: Two local refs (same provider, different models) for the tests where the cloud margin must not
#: interfere, plus one cloud ref for the margin tests.
_LOCAL_A = "ollama:llama3.1-8b"
_LOCAL_B = "ollama:qwen2.5-7b"
_LOCAL_C = "ollama:phi4-14b"
_CLOUD = "openai:gpt-4o"
_LOCAL_KEYS = {"ollama"}


def _row(n: int, success: Any, *, feedback: float = 0.0, feedback_n: int = 0) -> dict[str, Any]:
    """One fold row in ``stats.fold_record``'s shape."""
    return {
        "n": n,
        "success_rate": success,
        "feedback": feedback,
        "feedback_n": feedback_n,
        "avg_ms": 900.0,
        "avg_cost_usd": 0.0,
        "score": success,
        "updated_at": "2026-08-23T00:00:00Z",
    }


def _fold(rows: dict[str, Any]) -> dict[str, Any]:
    """A whole fold around one ``(use_case, query_class)`` bucket."""
    return {"version": stats.STATS_VERSION, "use_cases": {_UC: {_QC: rows}}}


def _call(refs: list[str], fold: Any, **over: Any) -> list[str]:
    """One call with the DEFAULT knobs, so an unchanged/reordered pair differs only in ``fold``."""
    kwargs: dict[str, Any] = {
        "use_case": _UC,
        "query_class": _QC,
        "stats": fold,
        "hysteresis": 0.05,
        "cloud_quality_margin": 0.10,
        "local_keys": _LOCAL_KEYS,
        "min_samples": 5,
    }
    kwargs.update(over)
    return learned_order(refs, **kwargs)


# --------------------------------------------------------------------------------------------
# Quality ordering
# --------------------------------------------------------------------------------------------


def test_clear_winner_is_promoted_and_near_equals_stay_put() -> None:
    """A ref that beats the field by more than ``hysteresis`` moves up; the two near-equals below
    it keep their incoming order, because a 0.02 score difference is noise."""
    refs = [_LOCAL_A, _LOCAL_B, _LOCAL_C]
    fold = _fold(
        {
            _LOCAL_A: _row(50, 0.60),
            _LOCAL_B: _row(50, 0.95),
            _LOCAL_C: _row(50, 0.58),
        }
    )
    assert _call(refs, fold) == [_LOCAL_B, _LOCAL_A, _LOCAL_C]


def test_sub_threshold_ref_keeps_its_slot_and_cannot_leapfrog() -> None:
    """A 1-sample 100%-success ref has NO opinion: it is neither promoted over a 50-sample 0.85 ref
    nor demoted below the 0.60 one. It keeps its incoming slot while the two measured refs are
    reordered around it."""
    refs = [_LOCAL_A, _LOCAL_B, _LOCAL_C]
    fold = _fold(
        {
            _LOCAL_A: _row(50, 0.60),
            _LOCAL_B: _row(1, 1.0),  # one lucky call
            _LOCAL_C: _row(50, 0.85),
        }
    )
    assert _call(refs, fold) == [_LOCAL_C, _LOCAL_B, _LOCAL_A]


def test_exactly_min_samples_counts_as_an_opinion() -> None:
    """The floor is ``n >= min_samples``, not ``>``."""
    refs = [_LOCAL_A, _LOCAL_B]
    fold = _fold({_LOCAL_A: _row(5, 0.20), _LOCAL_B: _row(5, 0.95)})
    assert _call(refs, fold) == [_LOCAL_B, _LOCAL_A]


def test_one_opinion_alone_reorders_nothing() -> None:
    """With a single opinionated ref there is nothing to compare it against."""
    refs = [_LOCAL_A, _LOCAL_B]
    fold = _fold({_LOCAL_A: _row(2, 0.10), _LOCAL_B: _row(50, 0.99)})
    assert _call(refs, fold) == refs


# --------------------------------------------------------------------------------------------
# Hysteresis: cost may reorder INSIDE the band and nowhere else
# --------------------------------------------------------------------------------------------


def test_cost_reorders_inside_the_band() -> None:
    """0.90 vs 0.88 is inside a 0.05 band, so the cheaper of the two near-equals goes first."""
    refs = [_LOCAL_A, _LOCAL_B]
    fold = _fold({_LOCAL_A: _row(50, 0.90), _LOCAL_B: _row(50, 0.88)})
    cost = {_LOCAL_A: 10.0, _LOCAL_B: 1.0}
    assert _call(refs, fold, cost_of=cost.__getitem__) == [_LOCAL_B, _LOCAL_A]


def test_cost_does_not_reorder_across_bands() -> None:
    """0.90 vs 0.70 is NOT near-equal, so the better ref is promoted over the cheaper one — a cost
    comparison applied globally would leave the cheap-but-worse ref in front."""
    refs = [_LOCAL_B, _LOCAL_A]  # cheap-and-worse first, so the expectation is a real reorder
    fold = _fold({_LOCAL_A: _row(50, 0.90), _LOCAL_B: _row(50, 0.70)})
    cost = {_LOCAL_A: 10.0, _LOCAL_B: 1.0}
    assert _call(refs, fold, cost_of=cost.__getitem__) == [_LOCAL_A, _LOCAL_B]


def test_band_is_anchored_at_its_best_score() -> None:
    """A band's width is exactly ``hysteresis``: 0.84 is within 0.05 of 0.86 but NOT of the band's
    best 0.90, so cost cannot pull it up. Chained grouping would let it drift in."""
    refs = [_LOCAL_A, _LOCAL_B, _LOCAL_C]
    fold = _fold(
        {
            _LOCAL_A: _row(50, 0.90),
            _LOCAL_B: _row(50, 0.86),
            _LOCAL_C: _row(50, 0.84),
        }
    )
    cost = {_LOCAL_A: 10.0, _LOCAL_B: 10.0, _LOCAL_C: 0.01}
    assert _call(refs, fold, cost_of=cost.__getitem__) == [_LOCAL_A, _LOCAL_B, _LOCAL_C]


def test_unknown_price_keeps_its_slot_and_never_raises() -> None:
    """A lookup that raises for one ref degrades to "no cost opinion" for that ref alone."""
    refs = [_LOCAL_A, _LOCAL_B]
    fold = _fold({_LOCAL_A: _row(50, 0.90), _LOCAL_B: _row(50, 0.88)})

    def cost_of(ref: str) -> float:
        raise KeyError(ref)

    assert _call(refs, fold, cost_of=cost_of) == refs


# --------------------------------------------------------------------------------------------
# The cloud margin is asymmetric
# --------------------------------------------------------------------------------------------


def test_equal_scoring_cloud_does_not_beat_local() -> None:
    """Free and private wins ties: an equal-scoring cloud ref is demoted below local even though it
    was bound first."""
    refs = [_CLOUD, _LOCAL_A]
    fold = _fold({_CLOUD: _row(50, 0.90), _LOCAL_A: _row(50, 0.90)})
    assert _call(refs, fold) == [_LOCAL_A, _CLOUD]


def test_cloud_beating_the_margin_wins() -> None:
    """A cloud ref that clears the margin is preferred."""
    refs = [_LOCAL_A, _CLOUD]
    fold = _fold({_CLOUD: _row(50, 0.98), _LOCAL_A: _row(50, 0.70)})
    assert _call(refs, fold) == [_CLOUD, _LOCAL_A]


def test_a_bigger_margin_can_hold_a_better_cloud_ref_back() -> None:
    """The margin is the knob: the same fold flips when the user demands more of the cloud."""
    refs = [_LOCAL_A, _CLOUD]
    fold = _fold({_CLOUD: _row(50, 0.98), _LOCAL_A: _row(50, 0.70)})
    assert _call(refs, fold, cloud_quality_margin=0.50) == [_LOCAL_A, _CLOUD]


# --------------------------------------------------------------------------------------------
# Degradation — every one of these returns the input unchanged, and none raises
# --------------------------------------------------------------------------------------------


def test_vacuity_floor_the_same_call_does_reorder_with_a_decisive_fold() -> None:
    """The floor under every "unchanged" assertion below: identical refs and knobs, only the fold
    differs, and a decisive fold DOES change the order."""
    refs = [_LOCAL_A, _LOCAL_B]
    decisive = _fold({_LOCAL_A: _row(50, 0.10), _LOCAL_B: _row(50, 0.99)})
    assert _call(refs, decisive) == [_LOCAL_B, _LOCAL_A]
    assert _call(refs, decisive) != refs


def test_missing_and_empty_folds_return_the_input_unchanged() -> None:
    refs = [_LOCAL_A, _LOCAL_B]
    for fold in (
        {},
        None,
        "not a fold",
        {"version": 1},
        {"version": 1, "use_cases": {}},
        {"version": 1, "use_cases": {_UC: {}}},
        {"version": 1, "use_cases": {_UC: {_QC: {}}}},
        {"version": 1, "use_cases": {_UC: {"other_class": {_LOCAL_B: _row(50, 0.99)}}}},
        {"version": 1, "use_cases": {"other_use_case": {_QC: {_LOCAL_B: _row(50, 0.99)}}}},
        {"version": 1, "use_cases": {_UC: {_QC: "corrupt"}}},
        _fold({}),
    ):
        assert _call(refs, fold) == refs, fold


def test_a_corrupt_entry_is_handled_by_the_scoring_path_not_the_failsafe(
    caplog: Any,
) -> None:
    """A string where a number belongs makes that ref opinionless — it does not raise, and it does
    not reach the fail-safe catch. If it ever does, this stage's failures become invisible."""
    refs = [_LOCAL_A, _LOCAL_B]
    corrupt = _fold({_LOCAL_A: _row(50, "high"), _LOCAL_B: _row(50, 0.99)})
    with caplog.at_level(logging.DEBUG, logger=learned.__name__):
        assert _call(refs, corrupt) == refs
    assert [r for r in caplog.records if r.name == learned.__name__] == []


def test_other_corrupt_shapes_degrade_without_raising(caplog: Any) -> None:
    refs = [_LOCAL_A, _LOCAL_B]
    for bad in (
        {"n": "50", "success_rate": 0.99},
        {"n": 50, "success_rate": None},
        {"n": 50, "success_rate": 0.99, "feedback": "good", "feedback_n": 3},
        {"n": 50, "success_rate": 0.99, "feedback": 0.9, "feedback_n": "3"},
        {"n": True, "success_rate": 0.99},
        {"n": float("nan"), "success_rate": 0.99},
        "a string row",
        None,
        [],
    ):
        fold = _fold({_LOCAL_A: bad, _LOCAL_B: _row(50, 0.99)})
        with caplog.at_level(logging.DEBUG, logger=learned.__name__):
            assert _call(refs, fold) == refs, bad
    assert [r for r in caplog.records if r.name == learned.__name__] == []


def test_degenerate_inputs_return_the_input_unchanged() -> None:
    decisive = _fold({_LOCAL_A: _row(50, 0.10), _LOCAL_B: _row(50, 0.99)})
    assert _call([], decisive) == []
    assert _call([_LOCAL_A], decisive) == [_LOCAL_A]
    # Negative knobs clamp to 0 rather than inverting the comparison.
    assert _call([_LOCAL_A, _LOCAL_B], decisive, hysteresis=-1.0) == [_LOCAL_B, _LOCAL_A]
    assert _call([_LOCAL_A, _LOCAL_B], decisive, cloud_quality_margin=-1.0) == [_LOCAL_B, _LOCAL_A]


def test_the_result_is_always_a_permutation() -> None:
    refs = [_LOCAL_A, _LOCAL_B, _LOCAL_C, _CLOUD]
    fold = _fold(
        {
            _LOCAL_A: _row(50, 0.60),
            _LOCAL_B: _row(3, 0.99),
            _LOCAL_C: _row(50, 0.88),
            _CLOUD: _row(50, 0.95),
        }
    )
    out = _call(refs, fold, cost_of=lambda ref: 1.0)
    assert sorted(out) == sorted(refs)
    assert len(out) == len(refs)
    assert _call(refs, fold, cost_of=lambda ref: 1.0) == out  # deterministic


# --------------------------------------------------------------------------------------------
# Contract with the fold's producer and with the one scoring formula
# --------------------------------------------------------------------------------------------


def test_reads_the_shape_fold_record_actually_writes() -> None:
    """Build the fold through ``stats.fold_record`` — the producer — so this stage's key and entry
    assumptions are pinned to the writer rather than to a hand-written literal."""
    fold: dict[str, Any] = {"version": stats.STATS_VERSION, "use_cases": {}}
    for i in range(6):
        stats.fold_record(
            fold,
            {
                "use_case": _UC,
                "query_class": _QC,
                "provider": "ollama",
                "model": "llama3.1-8b",
                "passed": True,
                "latency_ms": 800.0,
                "dollars_est": 0.0,
            },
            now=f"2026-08-2{i}T00:00:00Z",
        )
        stats.fold_record(
            fold,
            {
                "use_case": _UC,
                "query_class": _QC,
                "provider": "ollama",
                "model": "qwen2.5-7b",
                "passed": False,
                "latency_ms": 800.0,
                "dollars_est": 0.0,
            },
            now=f"2026-08-2{i}T00:00:00Z",
        )
    good = stats.ref_of("ollama", "llama3.1-8b")
    bad = stats.ref_of("ollama", "qwen2.5-7b")
    assert set(fold["use_cases"][_UC][_QC]) == {good, bad}
    assert _call([bad, good], fold) == [good, bad]


def test_scoring_is_delegated_to_stats_score() -> None:
    """``stats._score`` is the ONE place the 0.60/0.40 weighting lives. This module must call it,
    not re-derive it, so a future weight change cannot leave two answers."""
    assert learned._stats is stats

    source = Path(learned.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)

    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "_score"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "_stats"
    ]
    assert calls, "learned.py must score via stats._score"

    weights = {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, float)
        and node.value in (0.6, 0.4)
    }
    assert not weights, f"learned.py must not restate the scoring weights: {weights}"


def test_feedback_is_honoured_through_score() -> None:
    """A ref with feedback is scored 0.60/0.40 by ``stats._score``; the ordering follows that, so
    feedback can outrank raw success once it exists."""
    refs = [_LOCAL_A, _LOCAL_B]
    fold = _fold(
        {
            _LOCAL_A: _row(50, 0.90, feedback=0.0, feedback_n=0),  # score 0.90
            _LOCAL_B: _row(50, 0.80, feedback=1.0, feedback_n=9),  # 0.6*0.80 + 0.4*1.0 = 0.88
        }
    )
    assert stats._score(0.80, 1.0, 9) == 0.88
    # 0.90 vs 0.88 is inside the band, so nothing moves without a cost signal...
    assert _call(refs, fold) == refs
    # ...and a wider gap in feedback's favour does move it.
    fold["use_cases"][_UC][_QC][_LOCAL_A] = _row(50, 0.50)
    assert _call(refs, fold) == [_LOCAL_B, _LOCAL_A]

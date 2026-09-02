"""LV-7 — the skills-on/off verdict sibling reuses `fanout_measure`, it does not re-derive it.

The protocol's §5 names `harness/fanout_measure.py`'s three constants as THE thresholds and rules
out relabelling `skills_on` as `fanout` to get a green run. These tests pin both halves: that the
constants and the comparison logic are the SAME objects (not copies that can drift), and that the
only thing the sibling adds is a directional relabel.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from harness import fanout_measure, learning_verdict

MODULE = Path(learning_verdict.__file__)


def _trials(*scores, tokens=1000):
    return [learning_verdict.Trial(score=float(s), tokens=tokens) for s in scores]


# ── reuse, proved by identity rather than by equal numbers ───────────────────


def test_the_three_thresholds_are_the_SAME_objects_not_equal_copies():
    """Equal numbers would drift silently; the same binding cannot.

    A copied `5.0` passes an equality test forever after `fanout_measure` changes its band."""
    assert learning_verdict.INCONCLUSIVE_BAND_POINTS is fanout_measure.INCONCLUSIVE_BAND_POINTS
    assert learning_verdict.TOKEN_MATCH_TOLERANCE is fanout_measure.TOKEN_MATCH_TOLERANCE
    assert learning_verdict.MIN_TRIALS_PER_ARM is fanout_measure.MIN_TRIALS_PER_ARM
    assert learning_verdict.Arm is fanout_measure.Arm
    assert learning_verdict.Trial is fanout_measure.Trial


def test_the_sibling_defines_no_threshold_of_its_own():
    """The structural half: an AST census, so a future edit that re-derives a threshold reds here
    rather than passing because the number happened to match."""
    tree = ast.parse(MODULE.read_text(encoding="utf-8"))
    numeric_consts = {
        target.id: node.value.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, (int, float))
        if not isinstance(node.value.value, bool)
    }
    assert (
        numeric_consts == {}
    ), f"the sibling minted its own numeric threshold(s): {numeric_consts}"


def test_the_sibling_calls_compare_rather_than_reimplementing_the_check_order(monkeypatch):
    """§5's check order (trials → tokens → band → spread) has exactly one implementation.

    Proved by observing the call, not by matching outputs: two implementations can agree on the
    cases a test happens to pick and diverge on the one it does not."""
    seen = {}

    def _spy(work, fanout, single):
        seen["work"] = work
        seen["fanout"] = fanout.name
        seen["single"] = single.name
        return fanout_measure.compare(work, fanout, single)

    monkeypatch.setattr(learning_verdict, "compare", _spy)
    learning_verdict.verdict_task(
        task_id="sk_grill",
        skill="grill",
        on_trials=_trials(80, 80, 80),
        off_trials=_trials(60, 60, 60),
    )
    assert seen == {
        "work": "sk_grill",
        "fanout": learning_verdict.ARM_SKILLS_ON,
        "single": learning_verdict.ARM_SKILLS_OFF,
    }


# ── the relabel is a relabel: same closed set size, three identities ─────────


def test_the_verdict_vocabulary_is_the_same_size_as_fanout_measures():
    assert len(learning_verdict.VERDICTS) == len(fanout_measure.VERDICTS)
    assert "fanout_wins" not in learning_verdict.VERDICTS
    assert "single_wins" not in learning_verdict.VERDICTS


@pytest.mark.parametrize(
    "verdict",
    ["inconclusive", "not_token_matched", "insufficient_trials"],
)
def test_the_withheld_verdicts_pass_through_byte_identical(verdict):
    """They are withheld for reasons that have nothing to do with which arm is which, so renaming
    them would mint vocabulary for no gain."""
    assert verdict in learning_verdict.VERDICTS
    assert verdict in fanout_measure.VERDICTS


def test_a_positive_delta_favours_the_skills_on_arm():
    tv = learning_verdict.verdict_task(
        task_id="sk_grill",
        skill="grill",
        on_trials=_trials(80, 80, 80),
        off_trials=_trials(60, 60, 60),
        spend_observed=True,
    )
    assert tv.verdict == learning_verdict.VERDICT_SKILLS_ON_WINS
    assert tv.delta_points == 20.0


def test_a_negative_delta_favours_the_skills_off_arm_and_is_published_the_same_way():
    """§8: a skills-off win is a publishable outcome with the same prominence as a win. Nothing
    here suppresses or softens it."""
    tv = learning_verdict.verdict_task(
        task_id="sk_grill",
        skill="grill",
        on_trials=_trials(50, 50, 50),
        off_trials=_trials(70, 70, 70),
        spend_observed=True,
    )
    assert tv.verdict == learning_verdict.VERDICT_SKILLS_OFF_WINS
    assert tv.delta_points == -20.0


# ── the refusals ─────────────────────────────────────────────────────────────


def test_an_empty_arm_yields_NO_VERDICT_not_insufficient_trials():
    """ "We measured nothing" and "we measured too little" are different claims. `compare` would
    return `insufficient_trials` for an empty arm, which reads as the latter."""
    tv = learning_verdict.verdict_task(
        task_id="sk_grill", skill="grill", on_trials=_trials(80, 80, 80), off_trials=[]
    )
    assert tv.verdict is None
    assert tv.delta_points is None
    assert "skills_off" in tv.reason
    assert "not a tie and not a zero delta" in tv.reason
    # 🔑 ONE arm is the ORDINARY case here, not an edge: a paired run where a single arm produced
    # nothing. This sentence is not a log line — the runner writes it into the persisted report,
    # `GET /api/evals/learning-benchmark` serves it as `BenchmarkTaskRow.reason`, and
    # `learning/BenchmarkPanel.tsx` renders it VERBATIM. It read `arm(s) skills_off …` until
    # 2026-09-02, i.e. it was wrong on its commonest input.
    assert tv.reason.startswith("arm skills_off produced no scored cell"), tv.reason
    assert "(s)" not in tv.reason, tv.reason


def test_both_arms_empty_names_them_BOTH_and_pluralises():
    """The other side of the boundary, which a one-arm fixture cannot certify.

    The plural and the singular are separate paths through the same expression, so asserting only
    one leaves the other free to drift — this suite's own `(s)` was introduced and survived
    precisely because nothing crossed the boundary.
    """
    tv = learning_verdict.verdict_task(
        task_id="sk_grill", skill="grill", on_trials=[], off_trials=[]
    )
    assert tv.reason.startswith("arms skills_on, skills_off produced no scored cell"), tv.reason
    assert "(s)" not in tv.reason, tv.reason


def test_to_dict_never_substitutes_a_number_for_an_absent_verdict():
    """The rule the results page depends on: an unmeasured task must reach the wire as `null`,
    because a surface handed 0.0 cannot tell it from a measured zero."""
    payload = learning_verdict.verdict_task(
        task_id="sk_grill", skill="grill", on_trials=[], off_trials=[]
    ).to_dict()
    assert payload["verdict"] is None
    assert payload["verdict_class"] is None
    assert payload["delta_points"] is None
    assert payload["token_ratio"] is None


def test_two_trials_per_arm_is_refused_at_the_stated_floor():
    tv = learning_verdict.verdict_task(
        task_id="sk_grill", skill="grill", on_trials=_trials(80, 80), off_trials=_trials(60, 60)
    )
    assert tv.verdict == learning_verdict.VERDICT_INSUFFICIENT_TRIALS


def test_a_sub_band_delta_is_inconclusive_including_in_our_favour():
    tv = learning_verdict.verdict_task(
        task_id="sk_grill",
        skill="grill",
        on_trials=_trials(62, 62, 62),
        off_trials=_trials(60, 60, 60),
        spend_observed=True,
    )
    assert tv.verdict == learning_verdict.VERDICT_INCONCLUSIVE
    assert tv.delta_points == 2.0


def test_zero_observed_spend_is_not_token_matched():
    """With no spend rows both arms total zero tokens, and a comparison against a free arm
    measures nothing. The verdict says so instead of reporting the score delta."""
    tv = learning_verdict.verdict_task(
        task_id="sk_grill",
        skill="grill",
        on_trials=_trials(80, 80, 80, tokens=0),
        off_trials=_trials(60, 60, 60, tokens=0),
    )
    assert tv.verdict == learning_verdict.VERDICT_NOT_TOKEN_MATCHED


def test_unobserved_spend_annotates_the_verdict_rather_than_being_dropped():
    tv = learning_verdict.verdict_task(
        task_id="sk_grill",
        skill="grill",
        on_trials=_trials(80, 80, 80),
        off_trials=_trials(60, 60, 60),
        spend_observed=False,
    )
    assert any("NOT observed" in n for n in tv.notes)


def test_estimated_spend_is_said_out_loud():
    """§4: "any published token ratio must carry that word"."""
    tv = learning_verdict.verdict_task(
        task_id="sk_grill",
        skill="grill",
        on_trials=_trials(80, 80, 80),
        off_trials=_trials(60, 60, 60),
        spend_observed=True,
        spend_estimated=True,
    )
    assert any("ESTIMATED" in n for n in tv.notes)


def test_verdict_class_maps_an_unknown_string_to_itself():
    """An unrecognised verdict must never compare EQUAL to a recognised one — that is how a
    reproduction check would certify two runs that disagree."""
    assert learning_verdict.verdict_class("wat") == "wat"
    assert learning_verdict.verdict_class("inconclusive") == "inconclusive"

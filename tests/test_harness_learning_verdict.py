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


# ── the relabel reaches the NOTES, not just the verdict strings ───────────────
#
# §8 publishes the verdict WITH its notes, so a note is part of the published result. `compare()`
# writes its notes in fan-out vocabulary, and until this landed they were forwarded verbatim: a
# MEASURED skills report (`learnbench-20260907T003211Z`, k=5 against a local Ollama) published
#
#     "token spend differs by 13.7% (fanout 42517 vs single 37409) … give the cheaper arm more
#      budget (more single-agent samples, or a wider fan-out) … the largest published fan-out win
#      was ~3.75x tokens"
#
# about a suppressed SKILL. The module's own docstring already promised "an output file about
# skills does not say `fanout_wins`"; the notes broke that promise in the same file.


def _spend(on_scores, off_scores, *, on_tokens, off_tokens):
    return learning_verdict.verdict_task(
        task_id="sk_grill",
        skill="grill",
        on_trials=[learning_verdict.Trial(score=float(s), tokens=on_tokens) for s in on_scores],
        off_trials=[learning_verdict.Trial(score=float(s), tokens=off_tokens) for s in off_scores],
        spend_observed=True,
    )


@pytest.mark.parametrize("word", ["fanout", "fan-out", "single-agent", "topology"])
def test_no_published_note_speaks_the_fanout_vocabulary(word):
    """The vacuity floor for the case below: asserted as an ABSENCE across every verdict that
    carries a note, not as the presence of one good sentence. A single spot-check would pass on a
    module that fixed one note and forwarded the other two."""
    cases = [
        # not_token_matched (the 13.7% case, real numbers off the measured run)
        _spend((50, 100, 100, 100, 100), (100, 50, 100, 100, 100), on_tokens=8503, off_tokens=7482),
        # not_token_matched via a zero-spend arm
        _spend((80, 80, 80), (60, 60, 60), on_tokens=1000, off_tokens=0),
        # inconclusive by within-arm spread (delta clears the band, spread swamps it)
        _spend((100, 50, 100, 100, 100), (40, 40, 40, 40, 40), on_tokens=1000, off_tokens=1000),
        # inconclusive by the band
        _spend((80, 80, 80), (79, 79, 79), on_tokens=1000, off_tokens=1000),
    ]
    for tv in cases:
        joined = " ".join(tv.notes).lower()
        assert word not in joined, f"{tv.verdict} note still says {word!r}: {tv.notes}"


def test_the_replacement_note_keeps_compares_OWN_numbers():
    """A relabel that re-derived the numbers would be a second implementation of the check.

    The replacement sentence must carry the same per-arm totals AND the same percentage
    ``compare()`` prints — asserted against ``compare()``'s own note rather than a literal, so a
    rounding path that drifts by a tenth reddens this. It did: deriving the percentage from the
    payload's 4-decimal ``token_ratio`` printed 13.7% where ``compare()`` printed 13.6%.
    """
    on_tokens, off_tokens, k = 8503, 7482, 5
    tv = _spend(
        (50, 100, 100, 100, 100),
        (100, 50, 100, 100, 100),
        on_tokens=on_tokens,
        off_tokens=off_tokens,
    )
    assert tv.verdict == learning_verdict.VERDICT_NOT_TOKEN_MATCHED
    note = next(n for n in tv.notes if "token spend differs" in n)
    assert f"{learning_verdict.ARM_SKILLS_ON} {on_tokens * k}" in note
    assert f"{learning_verdict.ARM_SKILLS_OFF} {off_tokens * k}" in note

    def _arm(name, tokens):
        return fanout_measure.Arm(
            name=name,
            trials=[fanout_measure.Trial(score=1.0, tokens=tokens) for _ in range(k)],
        )

    upstream = fanout_measure.compare("sk_grill", _arm("a", on_tokens), _arm("b", off_tokens))
    printed = next(n for n in upstream.notes if "token spend differs by " in n)
    percentage = printed.split("token spend differs by ", 1)[1].split(" ", 1)[0]
    assert percentage.endswith("%")
    assert percentage in note, f"compare() printed {percentage!r}; the relabel printed {note!r}"
    # …and it must not read as a finding about the skill, which is the misreading §5's tolerance
    # exists to prevent.
    assert "declining a question it did not ask" in note


def test_the_literature_citation_that_justifies_the_band_is_NOT_reworded():
    """The inconclusive-band note cites the noise floor this module IMPORTS its 5 points from.
    Rewording a citation to suit a different experiment would misquote it, so that note passes
    through as written even though it says "architecture"."""
    tv = _spend((80, 80, 80), (79, 79, 79), on_tokens=1000, off_tokens=1000)
    assert tv.verdict == learning_verdict.VERDICT_INCONCLUSIVE
    assert any("scorer swaps move scores further than architecture does" in n for n in tv.notes)


def test_unequal_arms_say_so_beside_the_ratio_they_distort():
    """MEASURED: `sk_task_project` lost two `skills_on` cells to `VERIFIER_ABSENT` and was
    verdicted on 3 trials against 5. `compare()` divides arm TOTALS, so the ratio came out 0.5139
    — a "48.6% spend difference" that is mostly two missing trials (8,099/trial against
    9,456/trial is 0.857). §6 counts absent cells; it does not make the survivors comparable."""
    tv = learning_verdict.verdict_task(
        task_id="sk_task_project",
        skill="task-and-project",
        on_trials=[learning_verdict.Trial(score=100.0, tokens=8099) for _ in range(3)],
        off_trials=[learning_verdict.Trial(score=100.0, tokens=9456) for _ in range(5)],
        absent_cells=2,
        spend_observed=True,
    )
    assert tv.verdict == learning_verdict.VERDICT_NOT_TOKEN_MATCHED
    note = next(n for n in tv.notes if "UNEQUAL" in n)
    assert "skills_on 3 trial(s) against skills_off 5" in note
    assert "not a per-trial comparison" in note
    # The ratio itself is NOT quietly corrected — that would change a number `compare()` produced.
    assert tv.token_ratio == round((8099 * 3) / (9456 * 5), 4)


def test_a_balanced_pair_carries_no_imbalance_note():
    """The vacuity floor for the case above: the note must be absent when the arms are equal, or
    it is decoration that fires on every task and tells a reader nothing."""
    tv = _spend((80, 80, 80), (60, 60, 60), on_tokens=1000, off_tokens=1000)
    assert not any("UNEQUAL" in n for n in tv.notes)

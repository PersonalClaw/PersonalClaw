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

    def _spy(work, fanout, single, *, spend_basis):
        seen["work"] = work
        seen["fanout"] = fanout.name
        seen["single"] = single.name
        seen["spend_basis"] = spend_basis
        return fanout_measure.compare(work, fanout, single, spend_basis=spend_basis)

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
        # The one thing this module declares rather than inherits, asserted at the call and not
        # only in the answer: a keyword that stopped being passed would default to TOTALS.
        "spend_basis": fanout_measure.SPEND_PER_TRIAL,
    }


# ── the relabel is a relabel: the five map in, plus one declared member ──────


def test_the_verdict_vocabulary_is_the_relabel_plus_exactly_one_declared_member():
    """The relabel is still a pure relabel; the ONE addition is declared and named.

    This asserted equal SIZES until #2540, encoding the module's premise that "the only thing added
    is a directional relabel". The premise moved: a task whose provider reported no token usage is
    in a state `fanout_measure` has no member for, and none of the five could carry it honestly —
    `not_token_matched` asserts a comparison HAPPENED, and a `None` verdict asserts the arms were
    never ASSEMBLED. So the invariant asserted here is the honest one, and it still reds if a future
    edit mints a second word for something the five already say.
    """
    assert len(learning_verdict.VERDICTS) == len(fanout_measure.VERDICTS) + 1
    declared = set(learning_verdict.VERDICTS) - set(learning_verdict._RELABEL.values())
    assert declared == {learning_verdict.VERDICT_TOKENS_UNRECORDED}
    assert learning_verdict.VERDICT_TOKENS_UNRECORDED not in fanout_measure.VERDICTS
    assert "fanout_wins" not in learning_verdict.VERDICTS
    assert "single_wins" not in learning_verdict.VERDICTS
    # Spelled with the ONE word, not a synonym of it.
    assert learning_verdict.UNRECORDED in learning_verdict.VERDICT_TOKENS_UNRECORDED
    # And every member is still its own reproduction class, so nothing compares EQUAL by falling
    # through `VERDICT_CLASS`'s default.
    for verdict in learning_verdict.VERDICTS:
        assert learning_verdict.verdict_class(verdict) == verdict


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

    The replacement sentence must carry the same per-arm spend AND the same percentage
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
    # PER TRIAL — the quantity the gate divided. The arm totals are `k` times these and are in the
    # published `arms` payload; a note that printed totals would name a number the gate did not use.
    assert f"{learning_verdict.ARM_SKILLS_ON} {on_tokens} over {k} trial(s)" in note
    assert f"{learning_verdict.ARM_SKILLS_OFF} {off_tokens} over {k}" in note

    def _arm(name, tokens):
        return fanout_measure.Arm(
            name=name,
            trials=[fanout_measure.Trial(score=1.0, tokens=tokens) for _ in range(k)],
        )

    upstream = fanout_measure.compare(
        "sk_grill",
        _arm("a", on_tokens),
        _arm("b", off_tokens),
        spend_basis=fanout_measure.SPEND_PER_TRIAL,
    )
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


def test_a_balanced_pair_carries_no_imbalance_note():
    """The vacuity floor for the unequal-arm cases below: the note must be absent when the arms are
    equal, or it is decoration that fires on every task and tells a reader nothing."""
    tv = _spend((80, 80, 80), (60, 60, 60), on_tokens=1000, off_tokens=1000)
    assert not any("UNEQUAL" in n for n in tv.notes)


# ── the token gate divides PER TRIAL, because this is a paired design (#2587) ──
#
# Protocol §3 runs `k` trials per arm over IDENTICAL work, so per-trial and total spend are the same
# comparison whenever a run is whole. They diverge in exactly one case and it is §6's: an arm that
# lost cells to `VERIFIER_ABSENT`. `compare()`'s default basis is TOTALS and is right for the
# experiment it belongs to — amendment (e) matches budget by giving the cheaper arm more samples, so
# ITS arms are unequal on purpose. Reusing that denominator here published, about a real run:
#
#     token_ratio 0.5139 — "token spend differs by 48.6%"
#
# for `sk_task_project` in `learnbench-20260907T003211Z`, which ran 3 `skills_on` trials against 5
# and whose per-trial spends are 8,099 and 9,456 — a 14.3% difference. Two thirds of that "48.6%"
# was the two missing attempts.


def test_the_ratio_of_UNEQUAL_arms_is_PER_TRIAL_not_over_totals():
    """The measured case, with the run's own numbers. The published ratio must be the per-trial one
    and must NOT be the totals one — asserted as both, because a single assertion on the wanted
    value passes on any arithmetic that happens to land near it."""
    tv = learning_verdict.verdict_task(
        task_id="sk_task_project",
        skill="task-and-project",
        on_trials=[learning_verdict.Trial(score=100.0, tokens=8099) for _ in range(3)],
        off_trials=[learning_verdict.Trial(score=100.0, tokens=9456) for _ in range(5)],
        absent_cells=2,
        spend_observed=True,
    )
    assert tv.token_ratio == round(8099 / 9456, 4) == 0.8565
    assert tv.token_ratio != round((8099 * 3) / (9456 * 5), 4)
    # …and correcting the arithmetic does NOT move the verdict class, which is what makes this a
    # miscomputation fix rather than a protocol edit: 14.3% is over the 5% tolerance too.
    assert tv.verdict == learning_verdict.VERDICT_NOT_TOKEN_MATCHED


def test_equal_TOTALS_over_unequal_trial_counts_is_NOT_a_token_match():
    """The rail with teeth, and the failure the totals gate could not see at all.

    Three trials at 10,000 each against five at 6,000 each: 30,000 in total both ways, so the
    totals gate calls it perfectly matched (ratio 1.0) and hands the score delta a direction. Per
    attempt the arms spent 10,000 against 6,000 — 66.7% apart. A gate that certifies a token match
    that does not exist is worse than one that refuses a real one, because the verdict it releases
    is the budget wearing the treatment's name.
    """
    tv = learning_verdict.verdict_task(
        task_id="sk_task_project",
        skill="task-and-project",
        on_trials=[learning_verdict.Trial(score=90.0, tokens=10_000) for _ in range(3)],
        off_trials=[learning_verdict.Trial(score=60.0, tokens=6_000) for _ in range(5)],
        absent_cells=2,
        spend_observed=True,
    )
    assert tv.verdict == learning_verdict.VERDICT_NOT_TOKEN_MATCHED
    assert tv.token_ratio == round(10_000 / 6_000, 4)
    # The shape the totals gate would have produced, spelled out so this test states what it
    # prevents rather than only what it wants.
    on = fanout_measure.Arm(
        name="skills_on", trials=[fanout_measure.Trial(score=90.0, tokens=10_000)] * 3
    )
    off = fanout_measure.Arm(
        name="skills_off", trials=[fanout_measure.Trial(score=60.0, tokens=6_000)] * 5
    )
    on_totals = fanout_measure.compare("sk_task_project", on, off)
    assert on_totals.token_ratio == 1.0
    assert on_totals.verdict == fanout_measure.VERDICT_FANOUT_WINS


def test_the_basis_is_verified_off_the_ANSWER_not_only_passed_into_the_call(monkeypatch):
    """The rail proper. `compare()`'s default is TOTALS, so an edit that drops the keyword — or
    that flips `SPEND_BASIS` — would silently restore the incommensurable comparison. It raises
    instead, and the raise names both trial counts so the report of it is diagnosable."""
    monkeypatch.setattr(learning_verdict, "SPEND_BASIS", fanout_measure.SPEND_TOTAL)
    with pytest.raises(learning_verdict.IncommensurableSpendError) as exc:
        learning_verdict.verdict_task(
            task_id="sk_task_project",
            skill="task-and-project",
            on_trials=[learning_verdict.Trial(score=100.0, tokens=8099) for _ in range(3)],
            off_trials=[learning_verdict.Trial(score=100.0, tokens=9456) for _ in range(5)],
            spend_observed=True,
        )
    assert "3 skills_on trial(s) against 5 skills_off" in str(exc.value)


def test_the_unequal_arm_note_says_the_ratio_is_per_trial_and_the_delta_is_not_paired():
    """§8 publishes the notes, and §6's absent cells cost this pair its pairing. The ratio survives
    as a per-trial observation; the score delta beside it does not survive as a paired one, and the
    published sentence has to say which is which."""
    tv = learning_verdict.verdict_task(
        task_id="sk_task_project",
        skill="task-and-project",
        on_trials=[learning_verdict.Trial(score=100.0, tokens=8099) for _ in range(3)],
        off_trials=[learning_verdict.Trial(score=100.0, tokens=9456) for _ in range(5)],
        absent_cells=2,
        spend_observed=True,
    )
    note = next(n for n in tv.notes if "UNEQUAL" in n)
    assert "skills_on 3 trial(s) against skills_off 5" in note
    assert "PER-TRIAL comparison" in note
    assert "NOT a paired result" in note


# ══ #2540 — the run-level token ratio REFUSES rather than averaging ═══════════


def _paired(tokens_on: int, tokens_off: int, n: int = 5):
    return (
        [learning_verdict.Trial(score=80.0, tokens=tokens_on) for _ in range(n)],
        [learning_verdict.Trial(score=60.0, tokens=tokens_off) for _ in range(n)],
    )


def test_an_unrecorded_token_count_refuses_the_ratio_rather_than_dividing_a_placeholder_zero():
    """`Trial.tokens` is an `int`, so a cell whose provider omitted `usage` arrives as a `0`.

    Dividing it publishes a spend match nobody measured. §4's ratio exists to answer whether the
    arms were token-matched, and averaging across cells that could not report is not a weaker answer
    to that question — it is not an answer.
    """
    on, off = _paired(0, 0)
    tv = learning_verdict.verdict_task(
        task_id="sk_grill",
        skill="grill",
        on_trials=on,
        off_trials=off,
        spend_observed=True,
        tokens_recorded=False,
        unrecorded_spend_cells=3,
    )
    assert tv.verdict == learning_verdict.VERDICT_TOKENS_UNRECORDED
    assert tv.token_ratio is None
    assert tv.tokens_recorded is False
    assert tv.unrecorded_spend_cells == 3
    # The SCORES were measured, so they are published — that is why this is not a `None` verdict.
    assert tv.delta_points == 20.0
    assert tv.arms["skills_on"]["mean_score"] == 80.0
    assert tv.arms["skills_off"]["trials"] == 5
    # And the arm token figures are `None`, not the placeholder zeros the trials carry.
    assert tv.arms["skills_on"]["tokens"] is None
    assert tv.arms["skills_off"]["tokens"] is None
    assert tv.arms["skills_on"]["tokens_per_point"] is None
    assert "REFUSED, not zero" in " ".join(tv.notes)
    assert "3 contributing cell(s) reported no token usage" in tv.reason


def test_the_refusal_is_not_the_same_claim_as_not_measured_or_not_token_matched():
    """Three distinct states, and collapsing any two of them is the defect at a new site.

    * `None` — the arms were never assembled. No scores at all.
    * `not_token_matched` — the arms WERE compared and their spend differed. A measurement.
    * `tokens_unrecorded` — the arms were scored and their spend was never reported.
    """
    unrecorded = learning_verdict.verdict_task(task_id="t", skill="s", **_kw(tokens_recorded=False))
    unmeasured = learning_verdict.verdict_task(
        task_id="t", skill="s", on_trials=[], off_trials=[], spend_observed=True
    )
    mismatched = learning_verdict.verdict_task(
        task_id="t", skill="s", **_kw(tokens_on=10_000, tokens_off=1_000)
    )
    assert unrecorded.verdict == learning_verdict.VERDICT_TOKENS_UNRECORDED
    assert unmeasured.verdict is None
    assert mismatched.verdict == learning_verdict.VERDICT_NOT_TOKEN_MATCHED
    assert len({str(unrecorded.verdict), str(unmeasured.verdict), str(mismatched.verdict)}) == 3
    # The mismatched one is a MEASUREMENT: its ratio is a number, not a refusal.
    assert mismatched.token_ratio == 10.0
    assert mismatched.tokens_recorded is True
    # `verdict_class` keeps them apart for §8 reproduction too, so a re-run cannot cross states and
    # still "reproduce".
    classes = {
        learning_verdict.verdict_class(str(unrecorded.verdict)),
        learning_verdict.verdict_class(str(mismatched.verdict)),
    }
    assert len(classes) == 2


def _kw(*, tokens_on: int = 0, tokens_off: int = 0, tokens_recorded: bool = True) -> dict:
    on, off = _paired(tokens_on, tokens_off)
    return {
        "on_trials": on,
        "off_trials": off,
        "spend_observed": True,
        "tokens_recorded": tokens_recorded,
    }


def test_the_token_gate_is_UNREACHABLE_with_an_unrecorded_count(monkeypatch):
    """The rail, proved by making the gate explode if it is ever reached.

    Deleting the refusal branch is the edit this guards: the placeholder zeros would flow into
    `compare()` and a ratio would be published over them. `compare` is replaced with a bomb, so a
    green run here means the refusal happened BEFORE it, not that the numbers came out nicely.
    """

    def _bomb(*_a, **_kw):  # pragma: no cover - reaching it is the failure
        raise AssertionError("compare() was reached with an unrecorded token count")

    monkeypatch.setattr(learning_verdict, "compare", _bomb)
    tv = learning_verdict.verdict_task(task_id="t", skill="s", **_kw(tokens_recorded=False))
    assert tv.verdict == learning_verdict.VERDICT_TOKENS_UNRECORDED
    # VACUITY FLOOR: the same bomb with a RECORDED count does go off, so the test above proves the
    # refusal short-circuits rather than that `compare` is never called at all.
    with pytest.raises(AssertionError, match="compare\\(\\) was reached"):
        learning_verdict.verdict_task(task_id="t", skill="s", **_kw(tokens_on=100, tokens_off=100))


def test_the_second_rail_exists_and_is_deliberately_unreachable_in_normal_operation():
    """Two guards on one flag: the refusal, then a raise between it and `compare`.

    The raise CANNOT be reached while the refusal above it stands, which is the point — it is a
    mutation rail, not a runtime path, and a test that claimed to exercise it would be lying about
    its own precondition. What is asserted here is that it is present and that it names the right
    condition; what proves it bites is deleting the refusal branch, which makes it fire and reds
    `test_an_unrecorded_token_count_refuses_the_ratio_rather_than_dividing_a_placeholder_zero`
    instead of publishing a ratio.
    """
    src = MODULE.read_text(encoding="utf-8")
    tree = ast.parse(src)
    raises = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Raise)
        and isinstance(node.exc, ast.Call)
        and isinstance(node.exc.func, ast.Name)
        and node.exc.func.id == "IncommensurableSpendError"
    ]
    # One for the spend BASIS, one for the unrecorded COUNT. Both bases the gate cannot use.
    assert len(raises) == 2, "both IncommensurableSpendError rails must stand"
    assert "the token-match gate was reached" in src
    # And the raise sits BEFORE the `compare` call, not after it — after would be a post-mortem.
    assert src.index("the token-match gate was reached") < src.index("comparison = compare(task_id")


def test_a_recorded_zero_token_arm_is_still_a_measurement_not_a_refusal():
    """The vacuity floor for the refusal: a genuine zero must NOT be swept into it.

    An arm that really called no model spent zero tokens, and `fanout_measure` already has the right
    answer for that (`not_token_matched`, "an arm spent zero tokens"). Folding it into
    `tokens_unrecorded` would hide a real, disqualifying observation behind an absence.
    """
    tv = learning_verdict.verdict_task(
        task_id="t", skill="s", **_kw(tokens_on=1000, tokens_off=0, tokens_recorded=True)
    )
    assert tv.verdict == learning_verdict.VERDICT_NOT_TOKEN_MATCHED
    assert tv.tokens_recorded is True
    assert tv.token_ratio == 0.0
    assert "spent zero tokens" in " ".join(tv.notes)


def test_the_payload_carries_both_spend_facts_because_they_are_different_facts():
    """`spend_observed` is "the cell could read its own audit file"; `tokens_recorded` is "the
    provider reported the numbers in it". `observed: true, tokens_recorded: false` is the exact
    state #2540 measured, and a reader that had only one boolean could not name it."""
    payload = learning_verdict.verdict_task(
        task_id="t", skill="s", **_kw(tokens_recorded=False)
    ).to_dict()
    assert payload["spend_observed"] is True
    assert payload["tokens_recorded"] is False
    assert payload["token_ratio"] is None
    assert payload["verdict_class"] == learning_verdict.VERDICT_TOKENS_UNRECORDED

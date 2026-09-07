"""Tests for the token-matched fan-out measurement (WORK-CONTAINERS amendment (e), C2.3).

The property under test is a REFUSAL, not a calculation. Amendment (e) exists because the fan-out
literature's own noise floor exceeds most of its reported architecture deltas — run-to-run variance
1-3 points, a scorer swap moving one result 79.0 -> 25.6, n=24-100 benchmarks, and no paper
token-matching its single-agent baseline. So the thing that must be true of this module is that it
declines to report a win it cannot see: a sub-5-point delta is `inconclusive`, an unmatched token
spend is `not_token_matched`, and one trial per arm is not a measurement at all.

The failure mode these tests guard is a measurement that only ever reports wins, which the
amendment's own risk register names as the way this row gets useless.
"""

import json

import pytest

from harness import fanout_measure as fm
from harness.cli import main


def _arm(name: str, scores: list[float], tokens_each: int) -> fm.Arm:
    return fm.Arm(name=name, trials=[fm.Trial(score=s, tokens=tokens_each) for s in scores])


def _observations(tmp_path, fanout: dict, single: dict, work="rank 8 config files by risk"):
    path = tmp_path / "obs.json"
    path.write_text(
        json.dumps({"work": work, "arms": {"fanout": fanout, "single": single}}), encoding="utf-8"
    )
    return path


def _trials(scores: list[float], tokens_each: int) -> dict:
    return {"trials": [{"score": s, "tokens": tokens_each} for s in scores]}


# ── the inconclusive band (the C2.3 done_when) ──


def test_a_THREE_point_delta_reports_INCONCLUSIVE_and_NOT_a_win():
    """The done_when, exactly. Three points is inside the band the literature reports for its OWN
    results, so calling it a fan-out win would be reading noise as topology."""
    result = fm.compare(
        "identical work",
        _arm("fanout", [63.0, 63.0, 63.0], 10_000),
        _arm("single", [60.0, 60.0, 60.0], 10_000),
    )
    assert result.verdict == fm.VERDICT_INCONCLUSIVE
    assert result.conclusive is False
    assert result.verdict != fm.VERDICT_FANOUT_WINS
    assert result.delta_points == pytest.approx(3.0)


def test_a_three_point_delta_the_OTHER_way_is_ALSO_inconclusive():
    """Symmetry matters: a module that called a 3-point loss "single wins" but a 3-point gain
    "inconclusive" would be a thumb on the scale in the direction of restraint rather than a
    measurement."""
    result = fm.compare(
        "identical work",
        _arm("fanout", [57.0, 57.0, 57.0], 10_000),
        _arm("single", [60.0, 60.0, 60.0], 10_000),
    )
    assert result.verdict == fm.VERDICT_INCONCLUSIVE


def test_INCONCLUSIVE_is_part_of_the_declared_VOCABULARY():
    """A first-class outcome, not an error path. A caller must be able to assert on it rather than
    string-matching a message."""
    assert fm.VERDICT_INCONCLUSIVE in fm.VERDICTS
    assert fm.INCONCLUSIVE_BAND_POINTS == 5.0


def test_a_delta_ABOVE_the_band_at_matched_tokens_IS_a_verdict():
    """The module withholds, it does not refuse forever. A gate that could never say "yes" would be
    ignored the first time someone wanted an answer."""
    result = fm.compare(
        "identical work",
        _arm("fanout", [70.0, 70.0, 70.0], 10_000),
        _arm("single", [60.0, 60.0, 60.0], 10_000),
    )
    assert result.verdict == fm.VERDICT_FANOUT_WINS
    assert result.conclusive is True


def test_the_SINGLE_agent_can_win_too():
    """MASLab's re-benchmark had only 2 of 9 multi-agent methods beat the single agent, and
    Agentless beat SWE-agent 32.00% vs 18.33% at ~28% of the cost. A harness that could only
    report a fan-out win would be unable to record the literature's most common result."""
    result = fm.compare(
        "identical work",
        _arm("fanout", [50.0, 50.0, 50.0], 10_000),
        _arm("single", [62.0, 62.0, 62.0], 10_000),
    )
    assert result.verdict == fm.VERDICT_SINGLE_WINS


def test_a_delta_SMALLER_than_the_WITHIN_ARM_spread_is_inconclusive():
    """Six points between arms means nothing when one arm varies by ten across its own trials. A
    reader shown only the six would stop reading."""
    result = fm.compare(
        "identical work",
        _arm("fanout", [55.0, 65.0, 72.0], 10_000),
        _arm("single", [58.0, 58.0, 58.0], 10_000),
    )
    assert result.delta_points > fm.INCONCLUSIVE_BAND_POINTS
    assert result.verdict == fm.VERDICT_INCONCLUSIVE
    assert any("spread" in n for n in result.notes)


# ── token matching ──


def test_an_UNMATCHED_token_spend_refuses_to_report_a_winner():
    """The largest published fan-out win (+90.2%) came with ~3.75x the tokens, and its own
    regression says token usage alone explains 80% of the variance. An unmatched comparison measures
    the budget and credits the topology."""
    result = fm.compare(
        "identical work",
        _arm("fanout", [80.0, 80.0, 80.0], 40_000),
        _arm("single", [60.0, 60.0, 60.0], 10_000),
    )
    assert result.verdict == fm.VERDICT_NOT_TOKEN_MATCHED
    assert result.conclusive is False
    assert result.token_ratio == pytest.approx(4.0)


def test_a_spend_WITHIN_tolerance_counts_as_matched():
    result = fm.compare(
        "identical work",
        _arm("fanout", [70.0, 70.0, 70.0], 10_200),
        _arm("single", [60.0, 60.0, 60.0], 10_000),
    )
    assert result.verdict == fm.VERDICT_FANOUT_WINS


def test_a_ZERO_TOKEN_arm_is_not_a_comparison():
    """A comparison against a free arm measures nothing about topology, and a zero denominator would
    otherwise produce a token_ratio of 0.0 that reads as "perfectly cheap"."""
    result = fm.compare(
        "identical work",
        _arm("fanout", [70.0, 70.0, 70.0], 10_000),
        _arm("single", [60.0, 60.0, 60.0], 0),
    )
    assert result.verdict == fm.VERDICT_NOT_TOKEN_MATCHED


def test_the_report_names_the_TOKENS_PER_POINT_of_each_arm():
    """Fan-out costs 4-15x for single-digit gains where it helps at all, and the best cost outcomes
    in the literature come from PRUNING communication. A ratio makes that visible where two raw
    totals do not."""
    result = fm.compare(
        "identical work",
        _arm("fanout", [50.0, 50.0, 50.0], 30_000),
        _arm("single", [60.0, 60.0, 60.0], 30_000),
    )
    payload = result.to_dict()
    # 3 trials x 30_000 tokens = 90_000 per arm; over a mean of 50 vs 60 points.
    assert payload["arms"]["fanout"]["tokens_per_point"] == pytest.approx(1800.0)
    assert payload["arms"]["single"]["tokens_per_point"] == pytest.approx(1500.0)


# ── the spend basis: which denominator the gate divides (#2587) ──
#
# "The two arms spent the same" is not one quantity. Amendment (e) matches budget by giving the
# cheaper arm more samples, so ITS arms' trial counts are unequal on purpose and TOTALS are the
# question. A PAIRED design runs both arms over identical work the same number of times, so its
# totals are commensurable only while the counts match. The default stays totals: this module's own
# experiment is the fan-out one, and a paired caller must say so.


def test_the_DEFAULT_basis_is_TOTALS_because_this_modules_own_design_matches_BUDGET():
    """Amendment (e)'s shape, and the reason per-trial cannot be the default here: three fan-out
    trials at 10,000 against ten single-agent samples at 3,000 is 30,000 both ways, which is what
    "at equal token spend" MEANS in that design. Dividing by trial count would refuse the
    comparison the amendment exists to make."""
    result = fm.compare(
        "identical work",
        _arm("fanout", [70.0, 70.0, 70.0], 10_000),
        _arm("single", [60.0] * 10, 3_000),
    )
    assert result.spend_basis == fm.SPEND_TOTAL
    assert result.token_ratio == pytest.approx(1.0)
    assert result.verdict == fm.VERDICT_FANOUT_WINS


def test_the_SAME_arms_on_the_PER_TRIAL_basis_are_NOT_spend_matched():
    """The other side of the same numbers, and the whole point of naming the basis: 10,000 per
    attempt against 3,000 per attempt is 233% apart, so a PAIRED reading of those arms must refuse.
    One pair of arms, two honest answers, because the two designs ask different questions."""
    result = fm.compare(
        "identical work",
        _arm("fanout", [70.0, 70.0, 70.0], 10_000),
        _arm("single", [60.0] * 10, 3_000),
        spend_basis=fm.SPEND_PER_TRIAL,
    )
    assert result.spend_basis == fm.SPEND_PER_TRIAL
    assert result.token_ratio == pytest.approx(10_000 / 3_000)
    assert result.verdict == fm.VERDICT_NOT_TOKEN_MATCHED


def test_the_per_trial_basis_leaves_an_EQUAL_pair_untouched():
    """The vacuity floor. §3's paired design runs `k` trials per arm, so the basis only changes an
    answer when a trial was lost — a basis that moved every equal-count verdict would be a
    threshold change wearing a denominator's clothes."""
    kwargs = dict(work="identical work")
    on = _arm("fanout", [70.0, 70.0, 70.0], 10_200)
    off = _arm("single", [60.0, 60.0, 60.0], 10_000)
    totals = fm.compare(fanout=on, single=off, **kwargs)
    per_trial = fm.compare(fanout=on, single=off, spend_basis=fm.SPEND_PER_TRIAL, **kwargs)
    assert totals.verdict == per_trial.verdict == fm.VERDICT_FANOUT_WINS
    assert totals.token_ratio == pytest.approx(per_trial.token_ratio)


def test_an_ARMS_per_trial_spend_is_available_beside_its_total():
    result = fm.compare(
        "identical work",
        _arm("fanout", [70.0, 70.0, 70.0], 10_000),
        _arm("single", [60.0, 60.0, 60.0], 10_000),
    )
    assert result.fanout.tokens == 30_000
    assert result.fanout.tokens_per_trial == pytest.approx(10_000.0)
    assert result.fanout.spend(fm.SPEND_TOTAL) == pytest.approx(30_000.0)
    assert result.fanout.spend(fm.SPEND_PER_TRIAL) == pytest.approx(10_000.0)


def test_the_published_payload_NAMES_the_basis_it_divided():
    """A ratio whose denominator has to be inferred was published once already, and it read as a
    48.6% spend difference that was two missing trials."""
    result = fm.compare(
        "identical work",
        _arm("fanout", [70.0, 70.0, 70.0], 10_000),
        _arm("single", [60.0, 60.0, 60.0], 10_000),
        spend_basis=fm.SPEND_PER_TRIAL,
    )
    assert result.to_dict()["spend_basis"] == fm.SPEND_PER_TRIAL


def test_the_note_says_WHICH_spend_it_measured():
    """§8-style: the sentence travels with the number. "differs by 26%" is a different claim per
    trial than in total, and a reader cannot tell them apart from the percentage."""
    unmatched = dict(
        work="identical work",
        fanout=_arm("fanout", [80.0, 80.0, 80.0], 40_000),
        single=_arm("single", [60.0, 60.0, 60.0], 10_000),
    )
    totals = fm.compare(**unmatched)
    per_trial = fm.compare(**unmatched, spend_basis=fm.SPEND_PER_TRIAL)
    assert any("in total)" in n for n in totals.notes)
    assert any("per trial)" in n for n in per_trial.notes)


@pytest.mark.parametrize("basis", ["", "totals", "mean", None])
def test_an_UNKNOWN_basis_is_REFUSED_rather_than_defaulted(basis):
    """A silent fallback to totals is exactly how the incommensurable comparison reached a published
    table: nobody chose it, so nobody reviewed it."""
    with pytest.raises(fm.MeasurementError, match="unknown spend basis"):
        fm.compare(
            "identical work",
            _arm("fanout", [70.0, 70.0, 70.0], 10_000),
            _arm("single", [60.0, 60.0, 60.0], 10_000),
            spend_basis=basis,
        )


def test_an_UNKNOWN_basis_is_refused_even_when_a_GATE_would_short_circuit_first():
    """The basis is validated before the check order runs, not lazily where the division happens.
    Two trials per arm returns `insufficient_trials` without ever dividing a spend, so a lazily
    validated basis would record a bad one on the answer and be believed."""
    with pytest.raises(fm.MeasurementError, match="unknown spend basis"):
        fm.compare(
            "identical work",
            _arm("fanout", [70.0, 70.0], 10_000),
            _arm("single", [60.0, 60.0], 10_000),
            spend_basis="mean",
        )


def test_an_observation_FILE_is_measured_on_the_fanout_designs_basis(tmp_path):
    """The file shape is a `fanout`/`single` pair, which IS amendment (e)'s design, so
    `measure_file` does not offer a basis to choose — and the answer says which one it used."""
    assert fm.SPEND_BASES == {fm.SPEND_TOTAL, fm.SPEND_PER_TRIAL}
    path = _observations(
        tmp_path, _trials([62.0, 63.0, 61.0], 10_000), _trials([60.0, 60.0, 61.0], 10_000)
    )
    assert fm.measure_file(path).spend_basis == fm.SPEND_TOTAL


# ── trial count ──


def test_ONE_trial_per_arm_is_NOT_a_measurement():
    """With run-to-run variance at 1-3 points, n=1 cannot distinguish a result from a re-run."""
    result = fm.compare(
        "identical work", _arm("fanout", [90.0], 10_000), _arm("single", [50.0], 10_000)
    )
    assert result.verdict == fm.VERDICT_INSUFFICIENT_TRIALS
    assert result.conclusive is False


def test_the_trial_floor_is_checked_BEFORE_token_matching():
    """Order of checks is order of honesty: an unmatched two-trial comparison should report the more
    fundamental problem, because fixing the token match would still leave it unmeasurable."""
    result = fm.compare(
        "identical work",
        _arm("fanout", [90.0, 90.0], 40_000),
        _arm("single", [50.0, 50.0], 10_000),
    )
    assert result.verdict == fm.VERDICT_INSUFFICIENT_TRIALS


# ── the observation file ──


def test_a_file_with_no_WORK_declaration_is_refused(tmp_path):
    """The arms must be measured on IDENTICAL work, and a comparison that cannot name the work
    cannot claim they were."""
    path = tmp_path / "obs.json"
    path.write_text(
        json.dumps({"arms": {"fanout": _trials([1.0], 1), "single": _trials([1.0], 1)}}),
        encoding="utf-8",
    )
    with pytest.raises(fm.MeasurementError, match="declares no `work`"):
        fm.load_observations(path)


def test_a_MISSING_ARM_is_an_error_not_an_empty_arm(tmp_path):
    """One arm plus a default is a single measurement wearing a comparison's clothes."""
    path = tmp_path / "obs.json"
    path.write_text(
        json.dumps({"work": "w", "arms": {"fanout": _trials([1.0], 1)}}), encoding="utf-8"
    )
    with pytest.raises(fm.MeasurementError, match="missing the 'single' arm"):
        fm.load_observations(path)


def test_a_trial_MISSING_TOKENS_is_refused_rather_than_defaulted(tmp_path):
    """A silent zero would report a token-matched comparison between an arm that spent and an arm
    that did not."""
    path = tmp_path / "obs.json"
    path.write_text(
        json.dumps(
            {
                "work": "w",
                "arms": {
                    "fanout": {"trials": [{"score": 60.0}]},
                    "single": _trials([60.0], 100),
                },
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(fm.MeasurementError, match="missing 'tokens'"):
        fm.load_observations(path)


def test_a_missing_file_is_a_typed_error(tmp_path):
    with pytest.raises(fm.MeasurementError, match="no observation file"):
        fm.load_observations(tmp_path / "nope.json")


def test_a_well_formed_file_ROUND_TRIPS_to_a_verdict(tmp_path):
    path = _observations(
        tmp_path, _trials([62.0, 63.0, 61.0], 10_000), _trials([60.0, 60.0, 61.0], 10_000)
    )
    result = fm.measure_file(path)
    assert result.work == "rank 8 config files by risk"
    assert result.verdict == fm.VERDICT_INCONCLUSIVE


# ── the CLI surface ──


def test_the_CLI_exits_ZERO_on_an_INCONCLUSIVE_verdict(tmp_path, capsys):
    """A non-zero exit on the honest answer would make "inconclusive" look like a broken run, and
    the amendment's risk register says the failure mode to guard is a harness that only reports
    wins."""
    path = _observations(
        tmp_path, _trials([63.0, 63.0, 63.0], 10_000), _trials([60.0, 60.0, 60.0], 10_000)
    )
    assert main(["fanout-measure", str(path)]) == 0
    out = capsys.readouterr().out
    assert "inconclusive" in out
    assert "fanout_wins" not in out


def test_the_CLI_exits_TWO_on_a_MALFORMED_observation_file(tmp_path):
    """A measurement that did not happen. Distinct from a verdict it declined to give."""
    path = tmp_path / "obs.json"
    path.write_text("{not json", encoding="utf-8")
    assert main(["fanout-measure", str(path)]) == 2


def test_the_CLI_can_print_the_MACHINE_READABLE_dict(tmp_path, capsys):
    path = _observations(
        tmp_path, _trials([70.0, 70.0, 70.0], 10_000), _trials([60.0, 60.0, 60.0], 10_000)
    )
    assert main(["fanout-measure", str(path), "--json"]) == 0
    out = capsys.readouterr().out
    payload = json.loads(out[out.index("{") :])
    assert payload["verdict"] == fm.VERDICT_FANOUT_WINS
    assert payload["inconclusive_band_points"] == 5.0
    assert payload["token_matched"] is True

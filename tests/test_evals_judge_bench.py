"""EVALUATION-SUBSTRATE ES-4 — the judge benchmark harness → tier-recommendation table.

Every test here runs with a STUB judge. No model is called, which is the point: a
benchmark that needed a live provider could not be regression-tested at all, and the
properties worth protecting are arithmetic, not model behaviour.

The load-bearing group is "the axes are consumed". A declared matrix axis that nothing
reads produces N identical runs wearing different labels — a fabricated comparison that
looks real in every artifact — so `judge_samples` and `tier` each have a test that fails
if the executor stops honouring them. What is NOT exercised here is stated plainly in the
module docstring of `evals/judge_bench.py`: whether a REAL model at a given tier clears
the floors is a live-provider question, and only the shape of the answer is tested.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from personalclaw.evals import judge_bench as jb
from personalclaw.evals import store
from personalclaw.evals.matrix import FAILED, PASSED, VERIFIER_ABSENT, MatrixSpec, expand_cells

# ── helpers ──────────────────────────────────────────────────────────────────


def _pass_answer(fixture: jb.JudgeFixture, *, quality: float = 4.5) -> str:
    """A well-formed PASS the contract accepts: proof cited AND every declared criterion
    scored at target. Built from the fixture's own rubric so a criterion rename here can
    never silently turn these into protocol errors."""
    scores = {c.criterion: c.clamp_target() for c in fixture.hints().rubric}
    return json.dumps(
        {
            "reasoning": "the artifact names a command and its output",
            "verdict": "PASS",
            "scores": scores,
            "proof": "pytest -q: 6 passed",
            "evidence_refs": ["tests/test_http_retry.py"],
            "quality_score": quality,
            "marginal_value": 3.0,
        }
    )


def _reject_answer(fixture: jb.JudgeFixture, *, quality: float = 0.5) -> str:
    scores = {c.criterion: 0 for c in fixture.hints().rubric}
    return json.dumps(
        {
            "reasoning": "no work is described and nothing is cited",
            "verdict": "REJECT",
            "scores": scores,
            "quality_score": quality,
        }
    )


class StubJudge:
    """A counting judge. ``answer_for(index, prompt, use_case)`` decides each reply, so a
    test can make the reply depend on the sample index (does the count matter?) or on the
    use case (does the tier matter?)."""

    def __init__(self, answer_for):
        self.calls: list[dict] = []
        self._answer_for = answer_for

    async def __call__(self, prompt: str, *, use_case: str) -> jb.JudgeCall:
        index = len(self.calls)
        self.calls.append({"prompt": prompt, "use_case": use_case})
        text, cost = self._answer_for(index, prompt, use_case)
        return jb.JudgeCall(text=text, elapsed_secs=0.01, cost_usd=cost, model=f"Stub:{use_case}")


def _fixture(**over) -> jb.JudgeFixture:
    base = {
        "id": "f1",
        "rubric_class": "convergence",
        "kind": jb.FIXTURE_REAL,
        "artifact": "did the thing, here is the command output",
        "goal": "g",
        "dod": "d",
        "known_verdict": jb.VERDICT_PASS,
        "rubric": {"rubric": [{"criterion": "evidence is cited", "target_score": 2}]},
    }
    base.update(over)
    return jb.JudgeFixture(**base)


def _obs(**over) -> jb.Observation:
    base = {
        "fixture_id": "f1",
        "rubric_class": "convergence",
        "kind": jb.FIXTURE_REAL,
        "known_verdict": jb.VERDICT_PASS,
        "tier": "fast",
        "samples": 1,
        "position": jb.POSITION_FIRST,
        "outcome": PASSED,
        "verdict": jb.VERDICT_PASS,
        "quality_score": 4.5,
        "direction": jb.DIRECTION_AGREEMENT,
        "calls": 1,
        "cost_usd": 0.01,
        "elapsed_secs": 0.1,
    }
    base.update(over)
    return jb.Observation(**base)


@pytest.fixture()
def bench_home(tmp_path, monkeypatch):
    """An isolated home the bench can PIN a run against — nothing touches the real
    ``~/.personalclaw``."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    (tmp_path / "config.json").write_text(
        json.dumps({"providers": [{"name": "Acme"}]}), encoding="utf-8"
    )
    (tmp_path / "active_models.json").write_text(
        json.dumps({"reasoning": ["Acme:big"], "background": ["Acme:small"]}), encoding="utf-8"
    )
    return tmp_path


# ── THE load-bearing group: the axes are consumed ─────────────────────────────


def test_judge_samples_axis_is_consumed_call_for_call():
    """`judge_samples: 5` must cost five judge calls. If the executor ignored the axis,
    1/3/5 would be one call three times and the three columns of the table would be the
    same number wearing different labels — the exact fabricated comparison this atom
    exists to make impossible."""
    fixture = _fixture()
    for samples in jb.SAMPLE_COUNTS:
        stub = StubJudge(lambda i, p, u, f=fixture: (_pass_answer(f), 0.001))
        obs = asyncio.run(
            jb.observe_cell(
                fixture, None, tier="fast", samples=samples, position=jb.POSITION_FIRST, caller=stub
            )
        )
        assert len(stub.calls) == samples
        assert obs.calls == samples
        assert len(obs.sample_verdicts) == samples


def test_the_sample_axis_changes_the_verdict_not_just_the_call_count():
    """A count that is honoured but not AGGREGATED would still fabricate the comparison.
    With one PASS then two REJECTs, samples=1 passes and samples=3 must not: the contract's
    strict-majority rule is what makes the extra spend buy something."""
    fixture = _fixture()

    def answer(i, prompt, use_case):
        return (_pass_answer(fixture) if i == 0 else _reject_answer(fixture)), 0.001

    one = asyncio.run(
        jb.observe_cell(
            fixture,
            None,
            tier="fast",
            samples=1,
            position=jb.POSITION_FIRST,
            caller=StubJudge(answer),
        )
    )
    three = asyncio.run(
        jb.observe_cell(
            fixture,
            None,
            tier="fast",
            samples=3,
            position=jb.POSITION_FIRST,
            caller=StubJudge(answer),
        )
    )
    assert one.outcome == PASSED
    assert three.outcome == FAILED
    assert one.verdict != three.verdict


def test_tier_axis_reaches_the_provider_as_a_use_case():
    """The tier must select the model axis. A stub that answers differently per use case
    produces different rows; an executor that dropped the coord would produce identical
    ones for every tier."""
    fixture = _fixture()

    def answer(i, prompt, use_case):
        if use_case == "reasoning":
            return _pass_answer(fixture), 0.01
        return "not json at all", 0.0001

    seen = {}
    for tier in jb.TIERS:
        stub = StubJudge(answer)
        obs = asyncio.run(
            jb.observe_cell(
                fixture, None, tier=tier, samples=1, position=jb.POSITION_FIRST, caller=stub
            )
        )
        seen[tier] = (stub.calls[0]["use_case"], obs.outcome)
    assert seen["reasoning"] == ("reasoning", PASSED)
    assert seen["fast"][1] == VERIFIER_ABSENT
    assert len({v[0] for v in seen.values()}) == len(jb.TIERS)


def test_position_axis_reorders_the_rendered_candidates():
    """Position swap must actually move the artifact in the prompt, or the flip rate
    measures nothing."""
    strong = _fixture(id="s", known_verdict=jb.VERDICT_PASS, artifact="STRONG-BODY: ran the suite")
    null = _fixture(
        id="n",
        kind=jb.FIXTURE_NULL,
        known_verdict=jb.VERDICT_REJECT,
        pairs_with="s",
        artifact="NULL-BODY: did nothing",
    )
    first = jb.render_bench_prompt(null, strong, position=jb.POSITION_FIRST)
    second = jb.render_bench_prompt(null, strong, position=jb.POSITION_SECOND)
    assert first != second
    assert "Judge CANDIDATE A" in first
    assert "Judge CANDIDATE B" in second
    assert first.index(null.artifact) < first.index(strong.artifact)
    assert second.index(null.artifact) > second.index(strong.artifact)


def test_the_matrix_axes_are_the_shared_expansion():
    """The bench crosses its axes through the SAME `expand_cells` the scenario runner
    uses. A private copy would be a second crossing rule, and two rules over one product
    is how an axis silently stops being crossed."""
    fixture_set = jb.load_fixture_set("starter")
    paired, unpaired = jb.build_specs(fixture_set, tiers=("fast",), sample_counts=(1, 3))
    assert len(expand_cells(paired)) == 8 * 1 * 2 * 2  # fixtures × tiers × samples × positions
    assert unpaired is not None
    assert len(expand_cells(unpaired)) == 4 * 1 * 2 * 1
    combos = jb.bench_cells(paired, unpaired)
    assert {c["position"] for c in combos} == set(jb.POSITIONS)
    assert all("_trial" not in c for c in combos)


# ── null probes must actually separate ───────────────────────────────────────


def test_a_strong_null_pair_that_collapses_is_inadequate():
    """A null probe the judge scores like the strong case is a blind judge. The table must
    show the collapse numerically and the row must NOT be recommendable."""
    rows = jb.build_table(
        [
            _obs(fixture_id="s", counterpart_id="n", quality_score=4.0),
            _obs(
                fixture_id="n",
                counterpart_id="s",
                kind=jb.FIXTURE_NULL,
                known_verdict=jb.VERDICT_REJECT,
                outcome=FAILED,
                verdict=jb.VERDICT_REJECT,
                quality_score=4.0,
            ),
            _obs(
                fixture_id="s", counterpart_id="n", position=jb.POSITION_SECOND, quality_score=4.0
            ),
            _obs(
                fixture_id="n",
                counterpart_id="s",
                kind=jb.FIXTURE_NULL,
                known_verdict=jb.VERDICT_REJECT,
                outcome=FAILED,
                verdict=jb.VERDICT_REJECT,
                position=jb.POSITION_SECOND,
                quality_score=4.0,
            ),
        ]
    )
    assert len(rows) == 1
    row = rows[0]
    assert row.separation == 0.0
    assert not row.adequate
    assert any("separation" in r and "distinguish" in r for r in row.inadequate_reasons)


def test_a_separated_pair_clears_the_floor():
    rows = jb.build_table(
        [
            _obs(fixture_id="s", counterpart_id="n", quality_score=4.5),
            _obs(
                fixture_id="n",
                counterpart_id="s",
                kind=jb.FIXTURE_NULL,
                known_verdict=jb.VERDICT_REJECT,
                outcome=FAILED,
                verdict=jb.VERDICT_REJECT,
                quality_score=0.5,
            ),
            _obs(
                fixture_id="s", counterpart_id="n", position=jb.POSITION_SECOND, quality_score=4.5
            ),
            _obs(
                fixture_id="n",
                counterpart_id="s",
                kind=jb.FIXTURE_NULL,
                known_verdict=jb.VERDICT_REJECT,
                outcome=FAILED,
                verdict=jb.VERDICT_REJECT,
                position=jb.POSITION_SECOND,
                quality_score=0.5,
            ),
        ]
    )
    assert rows[0].separation == 4.0
    assert rows[0].adequate, rows[0].inadequate_reasons


def test_separation_uses_the_DECLARED_counterpart_not_any_strong_in_the_class():
    """Measured while driving the harness: with TWO pairs in one rubric class, matching a
    null against "some strong in the class" compared `null-b` to `strong-a` and reported the
    wrong difference under the right-looking name. The pairing is declared, so it is read."""
    obs = [
        _obs(fixture_id="strong-a", counterpart_id="null-a", quality_score=5.0),
        _obs(
            fixture_id="null-a",
            counterpart_id="strong-a",
            kind=jb.FIXTURE_NULL,
            known_verdict=jb.VERDICT_REJECT,
            outcome=FAILED,
            verdict=jb.VERDICT_REJECT,
            quality_score=1.0,
        ),
        _obs(fixture_id="strong-b", counterpart_id="null-b", quality_score=2.0),
        # Its OWN pair separates by 0.0 — a collapse the wrong pairing (5.0 - 2.0) hides.
        _obs(
            fixture_id="null-b",
            counterpart_id="strong-b",
            kind=jb.FIXTURE_NULL,
            known_verdict=jb.VERDICT_REJECT,
            outcome=FAILED,
            verdict=jb.VERDICT_REJECT,
            quality_score=2.0,
        ),
    ]
    rows = jb.build_table(obs)
    # mean of (5.0-1.0) and (2.0-2.0) = 2.0. Pairing null-b to strong-a would give 3.0.
    assert rows[0].separation == 2.0
    assert any("strong-b vs null-b" in n for n in rows[0].notes)


def test_a_pair_collapse_is_reported_once_not_once_per_slot():
    """The same collapse seen in both positions is ONE finding. Printing it twice buries
    every other note in the row, which was the measured effect before the dedup."""
    obs = []
    for position in jb.POSITIONS:
        obs.append(_obs(fixture_id="s", counterpart_id="n", position=position, quality_score=3.0))
        obs.append(
            _obs(
                fixture_id="n",
                counterpart_id="s",
                kind=jb.FIXTURE_NULL,
                known_verdict=jb.VERDICT_REJECT,
                outcome=FAILED,
                verdict=jb.VERDICT_REJECT,
                position=position,
                quality_score=3.0,
            )
        )
    rows = jb.build_table(obs)
    collapse_notes = [n for n in rows[0].notes if n.startswith("s vs n:")]
    assert len(collapse_notes) == 1


def test_the_separation_floor_is_the_loop_canary_not_a_second_number():
    from personalclaw.loop.instrument import _CANARY_MIN_SEPARATION

    assert jb.MIN_SEPARATION == _CANARY_MIN_SEPARATION


# ── unmeasured is never adequate ─────────────────────────────────────────────


def test_an_unmeasured_separation_is_inadequate_not_clean():
    """A rail that matched nothing must not read clean. With no paired fixture there is no
    separation number, and reporting the row as adequate would recommend a tier on a test
    that never ran."""
    rows = jb.build_table([_obs(), _obs(position=jb.POSITION_SECOND)])
    assert rows[0].separation is None
    assert not rows[0].adequate
    assert any("never measured" in r for r in rows[0].inadequate_reasons)


def test_an_unmeasured_flip_rate_is_inadequate_not_zero():
    rows = jb.build_table([_obs()])
    assert rows[0].flip_rate is None
    assert not rows[0].adequate
    assert any(
        "position-swap flip rate was never measured" in r for r in rows[0].inadequate_reasons
    )


def test_a_verdict_that_flips_with_position_is_measured_and_rejected():
    rows = jb.build_table(
        [
            _obs(fixture_id="s", counterpart_id="n", quality_score=4.5),
            _obs(
                fixture_id="n",
                counterpart_id="s",
                kind=jb.FIXTURE_NULL,
                known_verdict=jb.VERDICT_REJECT,
                outcome=FAILED,
                verdict=jb.VERDICT_REJECT,
                quality_score=0.5,
            ),
            _obs(fixture_id="s", position=jb.POSITION_SECOND, quality_score=4.5),
            # The null PASSES when it moves to the second slot — a positional flip.
            _obs(
                fixture_id="n",
                kind=jb.FIXTURE_NULL,
                known_verdict=jb.VERDICT_REJECT,
                outcome=PASSED,
                verdict=jb.VERDICT_PASS,
                direction=jb.DIRECTION_FALSE_PASS,
                position=jb.POSITION_SECOND,
                quality_score=0.5,
            ),
        ]
    )
    row = rows[0]
    assert row.flip_rate == 0.5
    assert row.swapped_fixtures == 2
    assert not row.adequate
    assert any("flip rate" in r for r in row.inadequate_reasons)
    assert any("flipped with position" in n for n in row.notes)


# ── the forbidden-mode disqualifier ──────────────────────────────────────────


def test_one_missed_forbidden_mode_disqualifies_a_tier_outright():
    """A disqualifier is a fact, not an opinion (`aggregate_samples` rule 2). A tier that
    passes a hardcoded-value admission is unusable however good its aggregate looks."""
    good = [
        _obs(fixture_id=f"ok{i}", counterpart_id=("n" if i == 0 else ""), quality_score=4.5)
        for i in range(9)
    ] + [
        _obs(
            fixture_id="n",
            counterpart_id="ok0",
            kind=jb.FIXTURE_NULL,
            known_verdict=jb.VERDICT_REJECT,
            outcome=FAILED,
            verdict=jb.VERDICT_REJECT,
            quality_score=0.5,
        ),
        _obs(
            fixture_id="ok0",
            counterpart_id="n",
            position=jb.POSITION_SECOND,
            quality_score=4.5,
        ),
        _obs(
            fixture_id="n",
            counterpart_id="ok0",
            kind=jb.FIXTURE_NULL,
            known_verdict=jb.VERDICT_REJECT,
            outcome=FAILED,
            verdict=jb.VERDICT_REJECT,
            position=jb.POSITION_SECOND,
            quality_score=0.5,
        ),
    ]
    clean = jb.build_table(list(good))
    assert clean[0].adequate, clean[0].inadequate_reasons

    with_miss = jb.build_table(
        good
        + [
            _obs(
                fixture_id="forbidden-1",
                kind=jb.FIXTURE_FORBIDDEN,
                known_verdict=jb.VERDICT_REJECT,
                outcome=PASSED,
                verdict=jb.VERDICT_PASS,
                direction=jb.DIRECTION_FALSE_PASS,
                quality_score=3.0,
                note="admits hardcoding a value",
            )
        ]
    )
    row = with_miss[0]
    assert row.forbidden_missed == 1
    assert not row.adequate
    assert any("forbidden-success-mode" in r for r in row.inadequate_reasons)
    assert any("admits hardcoding a value" in n for n in row.notes)


# ── verifier_absent is never a wrong answer ──────────────────────────────────


def test_an_unparseable_judge_is_absent_not_wrong():
    fixture = _fixture()
    stub = StubJudge(lambda i, p, u: ("I think it looks fine, honestly", None))
    obs = asyncio.run(
        jb.observe_cell(
            fixture, None, tier="fast", samples=3, position=jb.POSITION_FIRST, caller=stub
        )
    )
    assert obs.outcome == VERIFIER_ABSENT
    assert obs.protocol_errors == 3
    assert obs.calls == 3
    assert obs.direction == ""


def test_absent_cells_are_counted_separately_never_averaged_into_agreement():
    rows = jb.build_table(
        [
            _obs(fixture_id="a"),
            _obs(fixture_id="b", outcome=VERIFIER_ABSENT, verdict="", direction="", note="garbage"),
        ]
    )
    row = rows[0]
    assert row.scored_cells == 1
    assert row.verifier_absent == 1
    assert row.agreement == 1.0  # the absent cell is NOT a 0.0 dragging the mean down


def test_a_row_with_no_verdict_at_all_reports_nothing_measured():
    rows = jb.build_table([_obs(outcome=VERIFIER_ABSENT, verdict="", direction="", note="x")])
    assert rows[0].agreement is None
    assert not rows[0].adequate
    assert any("nothing was measured" in r for r in rows[0].inadequate_reasons)


def test_a_provider_exception_is_an_absent_cell_not_a_crash():
    fixture = _fixture()

    class Boom:
        async def __call__(self, prompt, *, use_case):
            raise RuntimeError("provider down")

    obs = asyncio.run(
        jb.observe_cell(
            fixture, None, tier="fast", samples=2, position=jb.POSITION_FIRST, caller=Boom()
        )
    )
    assert obs.outcome == VERIFIER_ABSENT
    assert "provider down" in obs.note


# ── the recommendation ───────────────────────────────────────────────────────


def _adequate_group(tier: str, samples: int, cost: float) -> list[jb.Observation]:
    return [
        _obs(
            fixture_id="s",
            counterpart_id="n",
            tier=tier,
            samples=samples,
            cost_usd=cost,
            quality_score=4.5,
        ),
        _obs(
            fixture_id="n",
            counterpart_id="s",
            kind=jb.FIXTURE_NULL,
            known_verdict=jb.VERDICT_REJECT,
            outcome=FAILED,
            verdict=jb.VERDICT_REJECT,
            tier=tier,
            samples=samples,
            cost_usd=cost,
            quality_score=0.5,
        ),
        _obs(
            fixture_id="s",
            counterpart_id="n",
            position=jb.POSITION_SECOND,
            tier=tier,
            samples=samples,
            cost_usd=cost,
            quality_score=4.5,
        ),
        _obs(
            fixture_id="n",
            counterpart_id="s",
            kind=jb.FIXTURE_NULL,
            known_verdict=jb.VERDICT_REJECT,
            outcome=FAILED,
            verdict=jb.VERDICT_REJECT,
            position=jb.POSITION_SECOND,
            tier=tier,
            samples=samples,
            cost_usd=cost,
            quality_score=0.5,
        ),
    ]


def test_the_cheapest_adequate_row_is_the_recommendation():
    rows = jb.build_table(_adequate_group("reasoning", 5, 0.50) + _adequate_group("fast", 1, 0.01))
    recs = jb.recommend(rows, resolve_ref=lambda tier: f"Stub:{tier}")
    assert len(recs) == 1
    rec = recs[0]
    assert rec.verdict == jb.REC_RECOMMENDED
    assert rec.tier == "fast"
    assert rec.samples == 1
    assert rec.use_case == "background"
    assert rec.model_ref == "Stub:fast"


def test_no_adequate_tier_names_every_tiers_reasons():
    rows = jb.build_table([_obs(tier=t) for t in jb.TIERS])
    recs = jb.recommend(rows, resolve_ref=lambda tier: "")
    assert recs[0].verdict == jb.REC_NO_ADEQUATE_TIER
    assert not recs[0].tier
    assert len(recs[0].notes) == len(jb.TIERS)


def test_an_unpriced_adequate_tier_is_never_called_cheapest():
    """`dollars_est` is 0.0 for an unpriced model — "an honest unknown". Ordering by it
    would make the unpriced tier win outright, which is the one ranking the table must
    refuse to invent."""
    group = _adequate_group("fast", 1, 0.01)
    unpriced = [
        jb.Observation(**{**o.to_dict(), "cost_usd": None, "quality_score": o.quality_score})
        for o in group
    ]
    rows = jb.build_table(unpriced)
    assert rows[0].adequate
    assert rows[0].cost_usd is None
    recs = jb.recommend(rows, resolve_ref=lambda tier: "Stub:x")
    assert recs[0].verdict == jb.REC_COST_UNKNOWN
    assert not recs[0].tier
    assert any("cannot be identified" in n for n in recs[0].notes)


def test_recommendations_are_per_rubric_class():
    rows = jb.build_table(
        _adequate_group("fast", 1, 0.01)
        + [
            jb.Observation(**{**o.to_dict(), "rubric_class": "deliverable"})
            for o in _adequate_group("reasoning", 3, 0.20)
        ]
    )
    recs = jb.recommend(rows, resolve_ref=lambda tier: f"Stub:{tier}")
    assert [r.rubric_class for r in recs] == ["convergence", "deliverable"]
    assert recs[0].tier == "fast"
    assert recs[1].tier == "reasoning"


# ── determinism ──────────────────────────────────────────────────────────────


def test_the_table_is_byte_identical_across_renders():
    """A table that moved between renders could not recommend anything. The executor's
    nondeterminism is the model; the TABLE is a pure function of the recorded
    observations, and that is what makes it quotable."""
    observations = _adequate_group("fast", 1, 0.01) + _adequate_group("reasoning", 3, 0.2)
    first = jb.render_table_tsv(jb.build_table(observations))
    second = jb.render_table_tsv(jb.build_table(list(reversed(observations))))
    assert first == second
    assert first.splitlines()[0].split("\t") == list(jb.TABLE_COLUMNS)


def test_within_cell_sample_disagreement_is_the_recorded_nondeterminism_budget():
    rows = jb.build_table([_obs(sample_verdicts=["PASS", "REJECT", "PASS"], samples=3)])
    assert any("samples disagreed within a cell" in n for n in rows[0].notes)


# ── fixture-set validation ───────────────────────────────────────────────────


def test_a_null_probe_labelled_pass_is_refused():
    with pytest.raises(jb.JudgeBenchError, match="must be labelled REJECT"):
        jb.parse_fixture_set(
            {
                "name": "bad",
                "fixtures": [
                    {
                        "id": "n",
                        "kind": "null",
                        "known_verdict": "PASS",
                        "artifact": "nothing",
                        "rubric_class": "convergence",
                    }
                ],
            }
        )


def test_a_dangling_pair_reference_is_refused():
    with pytest.raises(jb.JudgeBenchError, match="names no fixture"):
        jb.parse_fixture_set(
            {
                "name": "bad",
                "fixtures": [
                    {
                        "id": "a",
                        "kind": "real",
                        "known_verdict": "PASS",
                        "artifact": "x",
                        "rubric_class": "c",
                        "pairs_with": "nope",
                    }
                ],
            }
        )


@pytest.mark.parametrize(
    "bad,match",
    [
        ({"name": "", "fixtures": [{}]}, "no `name`"),
        ({"name": "x", "fixtures": []}, "no `fixtures`"),
        (
            {"name": "x", "fixtures": [{"id": "a", "kind": "nope", "known_verdict": "PASS"}]},
            "is not one of",
        ),
        (
            {
                "name": "x",
                "fixtures": [{"id": "a", "kind": "real", "known_verdict": "PASS", "artifact": "y"}],
            },
            "no `rubric_class`",
        ),
        (
            {
                "name": "x",
                "fixtures": [
                    {"id": "a", "kind": "real", "known_verdict": "PASS", "rubric_class": "c"}
                ],
            },
            "no `artifact`",
        ),
    ],
)
def test_malformed_sets_are_refused_not_defaulted(bad, match):
    with pytest.raises(jb.JudgeBenchError, match=match):
        jb.parse_fixture_set(bad)


def test_duplicate_fixture_ids_are_refused():
    entry = {
        "id": "a",
        "kind": "real",
        "known_verdict": "PASS",
        "artifact": "x",
        "rubric_class": "c",
    }
    with pytest.raises(jb.JudgeBenchError, match="duplicate fixture id"):
        jb.parse_fixture_set({"name": "x", "fixtures": [entry, dict(entry)]})


def test_an_unpaired_fixture_cannot_be_position_swapped():
    with pytest.raises(jb.JudgeBenchError, match="not position-swappable"):
        jb.render_bench_prompt(_fixture(), None, position=jb.POSITION_SECOND)


def test_an_unknown_tier_is_refused_before_anything_spends():
    with pytest.raises(jb.JudgeBenchError, match="unknown judge tier"):
        jb.use_case_for_tier("turbo")
    with pytest.raises(jb.JudgeBenchError, match="unknown judge tier"):
        jb.build_specs(jb.load_fixture_set("starter"), tiers=("turbo",))


def test_a_missing_fixture_set_names_what_ships():
    with pytest.raises(jb.JudgeBenchError, match="shipped sets: starter"):
        jb.load_fixture_set("no-such-set")


# ── the shipped set ──────────────────────────────────────────────────────────


def test_the_shipped_set_carries_all_three_fixture_families():
    """§6 names three: real judged runs, deliberately-bad null probes, and forbidden-
    success-mode cases. A set missing one silently drops a column of the table."""
    fixture_set = jb.load_fixture_set("starter")
    kinds = {f.kind for f in fixture_set.fixtures}
    assert kinds == set(jb.FIXTURE_KINDS)
    assert len(fixture_set.fixtures) >= 12
    assert len(fixture_set.rubric_classes()) >= 2
    paired = [f for f in fixture_set.fixtures if fixture_set.counterpart(f)]
    assert len(paired) >= 4
    for fixture in fixture_set.fixtures:
        assert fixture.hints().rubric, f"{fixture.id} declares no rubric to score"
        if fixture.kind != jb.FIXTURE_REAL:
            assert fixture.known_verdict == jb.VERDICT_REJECT


def test_every_shipped_pair_spans_one_rubric_class():
    """A pair whose halves sit in different classes would compute a separation across two
    different jobs."""
    fixture_set = jb.load_fixture_set("starter")
    for fixture in fixture_set.fixtures:
        mate = fixture_set.counterpart(fixture)
        if mate is not None:
            assert fixture.rubric_class == mate.rubric_class


def test_a_home_fixture_set_wins_over_the_shipped_one(bench_home):
    target = store.judge_benchmarks_dir() / "starter.json"
    target.write_text(
        json.dumps(
            {
                "name": "starter",
                "fixtures": [
                    {
                        "id": "mine",
                        "kind": "real",
                        "known_verdict": "PASS",
                        "artifact": "x",
                        "rubric_class": "c",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    fixture_set = jb.load_fixture_set("starter")
    assert [f.id for f in fixture_set.fixtures] == ["mine"]
    assert "starter" in jb.list_fixture_sets()


# ── vocabulary ratchets (drift is a silent measurement change) ───────────────


def test_the_tiers_are_the_engines_own_tier_table():
    from personalclaw.workflows.engine import DEFAULT_MODEL_TIERS

    assert set(jb.TIERS) == set(DEFAULT_MODEL_TIERS)
    assert [jb.use_case_for_tier(t) for t in jb.TIERS] == [DEFAULT_MODEL_TIERS[t] for t in jb.TIERS]


def test_the_top_sample_column_is_the_engines_ceiling():
    """Benchmarking a sample count no live gate can ask for would recommend an
    unreachable configuration."""
    from personalclaw.workflows.engine import MAX_JUDGE_SAMPLES

    assert max(jb.SAMPLE_COUNTS) == MAX_JUDGE_SAMPLES
    assert min(jb.SAMPLE_COUNTS) == 1


def test_the_direction_vocabulary_is_the_live_divergence_records():
    from personalclaw.workflows.judge_calibration import DivergenceRecord

    def direction(judge, human):
        return DivergenceRecord(
            run_id="r", node_id="n", template="t", judge_verdict=judge, human_verdict=human
        ).direction

    assert jb.DIRECTION_AGREEMENT == direction("PASS", "PASS")
    assert jb.DIRECTION_FALSE_PASS == direction("PASS", "REJECT")
    assert jb.DIRECTION_FALSE_REJECT == direction("REJECT", "PASS")
    assert jb.direction_for("PASS", "REJECT", fixture_id="f") == jb.DIRECTION_FALSE_PASS


# ── the whole run ────────────────────────────────────────────────────────────


def test_a_full_run_persists_the_table_the_pin_and_a_ledger_row(bench_home):
    fixture_set = jb.load_fixture_set("starter")

    def answer(i, prompt, use_case):
        # Answer from the artifact itself so PASS/REJECT tracks the fixture rather than
        # the call index: the strong artifacts name a command, the nulls do not.
        fixture = next(
            (f for f in fixture_set.fixtures if f.artifact in prompt and f.kind != jb.FIXTURE_NULL),
            fixture_set.fixtures[0],
        )
        cited = "pytest" in prompt or "wc -w" in prompt
        return (_pass_answer(fixture) if cited else _reject_answer(fixture)), 0.002

    result = asyncio.run(
        jb.run_judge_bench(
            "starter",
            caller=StubJudge(answer),
            tiers=("fast",),
            sample_counts=(1,),
            bench_id="bench-test",
            resolve_ref=lambda tier: f"Stub:{tier}",
        )
    )
    d = store.matrix_dir("bench-test")
    for name in (
        "experiment.json",
        "aggregates.json",
        "trials.json",
        "observations.json",
        "table.json",
        "table.tsv",
        "recommendations.json",
        "pin.json",
    ):
        assert (d / name).exists(), name

    assert len(result.observations) == 8 * 2 + 4  # paired × 2 positions + unpaired
    assert result.table
    assert result.recommendations
    # The ledger row carries the pin, and the pin's subject is the fixture SET's hash.
    rows = store.read_results()
    assert len(rows) == 1
    assert rows[0]["kind"] == "judge_bench"
    assert rows[0]["scenario_id"] == "starter"
    assert rows[0]["scenario_sha256"] == fixture_set.sha256
    assert rows[0]["model_fp"]

    view = jb.latest_bench_view()
    assert view is not None
    assert view["bench_id"] == "bench-test"
    assert view["rows"] == [r.to_dict() for r in result.table]
    assert view["floors"]["separation"] == jb.MIN_SEPARATION
    # The table recomputes from the persisted observations, byte for byte.
    reread = jb.read_observations("bench-test")
    assert jb.render_table_tsv(jb.build_table(reread)) == jb.render_table_tsv(result.table)


def test_no_bench_run_reads_as_none_not_an_empty_table(bench_home):
    assert jb.latest_bench_view() is None
    assert jb.list_bench_runs() == []


def test_an_incomplete_pin_refuses_the_run_before_spending(tmp_path, monkeypatch):
    """A run whose result could never enter the ledger is wasted spend, not evidence."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    (tmp_path / "config.json").write_text("{}", encoding="utf-8")
    stub = StubJudge(lambda i, p, u: ("{}", 0.0))
    with pytest.raises(store.PinRequiredError, match="model_fingerprint"):
        asyncio.run(jb.run_judge_bench("starter", caller=stub, tiers=("fast",), sample_counts=(1,)))
    assert stub.calls == []


def test_the_bench_verdict_is_fail_when_no_tier_is_adequate():
    assert jb._bench_verdict([]) == VERIFIER_ABSENT
    assert (
        jb._bench_verdict([jb.Recommendation(rubric_class="c", verdict=jb.REC_RECOMMENDED)])
        == "pass"
    )
    assert (
        jb._bench_verdict([jb.Recommendation(rubric_class="c", verdict=jb.REC_NO_ADEQUATE_TIER)])
        == "fail"
    )


def test_matrix_spec_axes_name_the_documented_four():
    """The axes the atom promises: fixtures × tiers × judge_samples × position."""
    paired, _ = jb.build_specs(jb.load_fixture_set("starter"))
    assert set(paired.axes) == {"fixture", "tier", "judge_samples", "position"}
    assert paired.scorer == "judge"
    assert isinstance(paired, MatrixSpec)

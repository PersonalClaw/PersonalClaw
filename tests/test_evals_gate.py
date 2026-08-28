"""EVALUATION-SUBSTRATE amendment E2 / ES-6 — the Loop-2 cheap gate subset.

Four clauses, and a section per clause:

1. a curated dozen FAST, assertion-heavy scenarios re-run before a proposal ships;
2. a planted regression in a candidate skill edit shows a SCORE DROP on its own proposal card
   (``{before, after, pin}``) before the user accepts;
3. gate-run cost is bounded and metered via ``SpendMeter`` — bounded meaning it STOPS;
4. a proposal with no gate run renders "ungated" honestly and NEVER blocks.

**Where these tests stop being real, and why.** Everything is the shipped code except the LLM:
the real scenario library and its manifest, the real ``load_scenario`` parser, the real
``Assertion.check``, the real subset selection, the real ``RunPin``, the real ``SpendMeter``, the
real ``candidate_files`` install rail, the real proposal store and the real inbox projection.
:class:`_ScoringMatrix` substitutes for ``run_matrix`` at the boundary
``skills_bench.bench_skill``/``ablation.run_ablation`` already make injectable, and models the
agent as a PERFECT-RECALL reader of whatever the arm staged — the strongest honest assumption, and
the one that makes a planted regression in the candidate text observable without buying tokens.
The child-side half of that seam (the arm reaching the spawn env, the staging, and the
throwaway-home refusal) is tested against the REAL code in its own section.
"""

from __future__ import annotations

import json
import types
from pathlib import Path

import pytest

from personalclaw.eval.scenario import AssertionType, load_scenario
from personalclaw.evals import gate as gate_mod
from personalclaw.evals import runner as runner_mod
from personalclaw.evals import scenarios as scenario_lib
from personalclaw.evals import store
from personalclaw.evals.child import render_result_line
from personalclaw.evals.matrix import (
    FAILED,
    PASSED,
    CellResult,
    MatrixResult,
    MatrixSpec,
    aggregate,
)
from personalclaw.evals.overlay import OverlayRefusedError
from personalclaw.guardrails.budgets import SpendMeter

# ── the isolated, pinnable home every section runs in ─────────────────────────

FORBIDDEN = "FORBIDDEN-PHRASE"


def _write_home(home: Path, *, model: str = "Acme:m1", enabled: bool = True, budget: float = 1.0):
    """A home the gate can PIN a run against: a config, a model binding, a scenario library.

    Written as files through the real resolution paths, so the pin these tests exercise is the
    one :func:`personalclaw.evals.pinning.compute_pin_for_subject` really computes.
    """
    (home / "config.json").write_text(
        json.dumps(
            {
                "providers": [{"name": "Acme"}],
                "evals": {"enabled": enabled, "default_budget_usd": budget},
            }
        ),
        encoding="utf-8",
    )
    (home / "active_models.json").write_text(json.dumps({"chat": [model]}), encoding="utf-8")
    return home


def _scenario(
    name: str,
    *,
    tiers: list[str] | None = None,
    turns: int = 1,
    assertions: list[dict] | None = None,
    version: int = 1,
) -> dict:
    body = assertions if assertions is not None else [{"type": "not_contains", "value": FORBIDDEN}]
    return {
        "name": name,
        "version": version,
        "fixture_home": "empty",
        **({"tiers": tiers} if tiers is not None else {}),
        "sessions": [
            {
                "name": f"s{i}",
                "turns": [{"user": f"turn {i}", "assertions": body}],
            }
            for i in range(turns)
        ],
    }


def _install(home: Path, *scenarios: dict) -> None:
    d = home / "evals" / "scenarios"
    d.mkdir(parents=True, exist_ok=True)
    for data in scenarios:
        (d / f"{data['name']}.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
    scenario_lib.install_library()


@pytest.fixture()
def no_shipped_library(tmp_path, monkeypatch):
    """Point the PACKAGED library at an empty dir so the backfill installs nothing.

    Necessary rather than tidy: ``install_library`` re-backfills the shipped set on EVERY call, so
    deleting the shipped files after the first install puts them straight back on the next one. A
    per-test subset keeps assertions about *selection* separate from assertions about *what ships*,
    and the latter get their own section that reads the real packaged dir.
    """
    empty = tmp_path / "no-packaged-library"
    empty.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(scenario_lib, "packaged_library_dir", lambda: empty)
    return empty


@pytest.fixture()
def gate_home(tmp_path, monkeypatch, no_shipped_library):
    """An isolated home with ONE gate-tagged probe scenario installed, and nothing else."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    _write_home(tmp_path)
    _install(tmp_path, _scenario("gate_probe", tiers=["gate"]))
    assert gate_mod.gate_subset().names == ["gate_probe"]
    return tmp_path


class _ScoringMatrix:
    """A ``run_matrix`` stand-in that scores the ARM's staged text with the REAL assertions.

    The agent is modelled as a perfect-recall reader of whatever the arm staged: the "response" is
    the concatenation of the arm's file contents. That is the strongest honest assumption and the
    one that makes a planted regression in the candidate text observable — the real
    ``EvalRunner`` would need a bound model and a paid call per turn.

    ``judge`` assertions are skipped for the same reason the real child skips them: it runs
    ``EvalRunner(judge_enabled=False)``, which filters them out of the scored set.
    """

    def __init__(self, *, dollars_per_cell: float = 0.0, tokens_per_cell: int = 0) -> None:
        self.calls: list[tuple[str, str | None]] = []
        self._dollars = dollars_per_cell
        self._tokens = tokens_per_cell

    def __call__(self, spec: MatrixSpec, *, matrix_id: str, artifact_arm=None, **_kw):
        self.calls.append((spec.subject, None if artifact_arm is None else artifact_arm.label))
        staged = {} if artifact_arm is None else dict(artifact_arm.files)
        response = "\n".join(text for _name, text in sorted(staged.items()))

        scenario = load_scenario(scenario_lib.resolve_scenario_path(spec.subject))
        total = hits = 0
        for session in scenario.sessions:
            for turn in session.turns:
                for assertion in turn.assertions:
                    if assertion.type is AssertionType.JUDGE:
                        continue
                    total += 1
                    hits += 1 if assertion.check(response) else 0
        score = (hits / total) if total else 1.0

        cell_dir = store.matrix_dir(matrix_id) / "cell-0000"
        cell_dir.mkdir(parents=True, exist_ok=True)
        (cell_dir / "result.json").write_text(
            json.dumps(
                {
                    "returncode": 0,
                    "parsed": {
                        "ok": True,
                        "passed": score >= 1.0,
                        "score": score,
                        "spend": {
                            "observed": True,
                            "attempts": 1,
                            "tokens": self._tokens,
                            "dollars_est": self._dollars,
                            "estimated": True,
                        },
                    },
                }
            ),
            encoding="utf-8",
        )
        cells = [
            CellResult(
                coords={},
                outcome=PASSED if score >= 1.0 else FAILED,
                score=score,
                artifact_ref=str(cell_dir),
            )
        ]
        return MatrixResult(spec=spec, cells=cells, aggregates=aggregate(cells))


def _arms(before: dict[str, str], after: dict[str, str]):
    return (
        gate_mod.ArtifactArm(label=gate_mod.ARM_BEFORE, files=before),
        gate_mod.ArtifactArm(label=gate_mod.ARM_AFTER, files=after),
    )


# ══ CLAUSE 1 — a curated dozen, FAST, assertion-heavy ════════════════════════


def test_the_shipped_library_declares_exactly_twelve_gate_scenarios():
    """The "curated dozen" is a fact about what SHIPS, asserted against the package.

    Read off the packaged library rather than an installed home, so a stale home cannot make this
    pass. Twelve is the atom's own number; a thirteenth would silently make the cheap tier less
    cheap, and an eleventh would silently narrow the coverage a reviewer is trusting.
    """
    tagged = []
    for path in sorted(scenario_lib.packaged_library_dir().glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if gate_mod.GATE_TIER in scenario_lib.tiers_of(data):
            tagged.append(path.stem)
    assert len(tagged) == 12, tagged


def test_every_shipped_gate_scenario_is_fast_and_assertion_heavy():
    """The two properties the tier's name claims, checked on every member.

    Not "the selector would exclude a bad one" — that is the next test. This one says the shipped
    set has nothing the selector would have to throw away, which is what "curated" means.
    """
    for path in sorted(scenario_lib.packaged_library_dir().glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if gate_mod.GATE_TIER not in scenario_lib.tiers_of(data):
            continue
        assert gate_mod.turn_count(data) <= gate_mod.MAX_GATE_TURNS, path.stem
        assert gate_mod.hard_assertion_count(data) >= 1, path.stem


def test_a_shipped_scenario_that_joined_the_tier_bumped_its_version(tmp_path, monkeypatch):
    """The backfill is version-keyed, so joining a tier without a version bump would never install.

    Measured as the real backfill: an installed copy at the OLD version must be replaced by the
    shipped one, tier and all. Without the bump ``install_library`` leaves the old file alone and
    the gate subset is empty on every existing home — a silent no-op nobody would notice.
    """
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    _write_home(tmp_path)
    shipped = json.loads(
        (scenario_lib.packaged_library_dir() / "smoke_test.json").read_text(encoding="utf-8")
    )
    assert gate_mod.GATE_TIER in scenario_lib.tiers_of(shipped)
    assert int(shipped["version"]) > 1

    stale = {k: v for k, v in shipped.items() if k != "tiers"}
    stale["version"] = 1
    _install(tmp_path, stale)
    reinstalled = json.loads(
        (tmp_path / "evals" / "scenarios" / "smoke_test.json").read_text(encoding="utf-8")
    )
    assert gate_mod.GATE_TIER in scenario_lib.tiers_of(reinstalled)


def test_the_manifest_records_the_tier_so_membership_is_answerable_from_the_home(gate_home):
    manifest = scenario_lib.read_manifest() or {}
    row = (manifest.get("scenarios") or {}).get("gate_probe") or {}
    assert row.get("tiers") == ["gate"]


def test_only_tagged_scenarios_are_selected(gate_home):
    _install(gate_home, _scenario("not_tagged"))
    subset = gate_mod.gate_subset()
    assert subset.names == ["gate_probe"]
    # VACUITY FLOOR: the untagged scenario really is installed and really is otherwise eligible,
    # so its absence is the tag doing work and not a missing file.
    installed = json.loads(
        (gate_home / "evals" / "scenarios" / "not_tagged.json").read_text(encoding="utf-8")
    )
    assert gate_mod.turn_count(installed) <= gate_mod.MAX_GATE_TURNS
    assert gate_mod.hard_assertion_count(installed) >= 1


def test_a_tagged_scenario_over_the_turn_ceiling_is_excluded_with_a_reason(gate_home):
    """ "Fast" is structural. A tagged 5-turn scenario is thrown out and SAYS why."""
    _install(gate_home, _scenario("slow_probe", tiers=["gate"], turns=5))
    subset = gate_mod.gate_subset()
    assert "slow_probe" not in subset.names
    reasons = dict(subset.excluded)
    assert "5 turns" in reasons["slow_probe"]
    assert str(gate_mod.MAX_GATE_TURNS) in reasons["slow_probe"]


def test_a_tagged_judge_only_scenario_is_excluded_because_it_would_score_a_fake_one(gate_home):
    """ "Judge-light" is not a style preference — it is the difference between a score and a lie.

    The child runs ``EvalRunner(judge_enabled=False)``, which FILTERS judge assertions out of the
    scored set, so a judge-only scenario reaches ``total_assertions == 0`` and
    ``child.result_from_scenario`` falls back to ``1.0``. That fabricated perfect score would sit
    in a gate mean as if it were a measurement.
    """
    _install(
        gate_home,
        _scenario(
            "judge_only",
            tiers=["gate"],
            assertions=[{"type": "judge", "value": "is it good?"}],
        ),
    )
    subset = gate_mod.gate_subset()
    assert "judge_only" not in subset.names
    assert "judge" in dict(subset.excluded)["judge_only"]

    # The mechanism behind the refusal, measured rather than asserted: the real scorer really does
    # publish 1.0 for a scenario with no scoreable assertion.
    from personalclaw.evals.child import result_from_scenario

    fabricated = result_from_scenario(
        types.SimpleNamespace(total_assertions=0, passed_assertions=0, passed=True)
    )
    assert fabricated["score"] == 1.0


def test_the_subset_is_capped_cheapest_first_and_deterministic(gate_home):
    _install(
        gate_home,
        *[_scenario(f"p{i:02d}", tiers=["gate"], turns=2) for i in range(20)],
        _scenario("cheap_one", tiers=["gate"], turns=1),
    )
    subset = gate_mod.gate_subset()
    assert len(subset.names) == gate_mod.GATE_SUBSET_MAX
    # Cheapest first: the 1-turn scenario is never the one dropped for the cap.
    assert "cheap_one" in subset.names
    assert gate_mod.gate_subset().names == subset.names
    assert any("cap" in reason for _n, reason in subset.excluded)


def test_the_subset_hash_moves_when_a_member_changes(gate_home):
    first = gate_mod.gate_subset().sha256()
    _install(gate_home, _scenario("gate_probe", tiers=["gate"], version=2, turns=2))
    assert gate_mod.gate_subset().sha256() != first


def test_the_gate_runs_every_member_of_the_subset_over_both_arms(gate_home):
    _install(gate_home, _scenario("second_probe", tiers=["gate"]))
    fake = _ScoringMatrix()
    report = gate_mod.run_gate(
        run_id="r1", arms=_arms({}, {"skills/auto/x/SKILL.md": "ok"}), run_matrix=fake
    )
    assert report.state == gate_mod.GATE_GATED
    assert sorted(fake.calls) == [
        ("gate_probe", "after"),
        ("gate_probe", "before"),
        ("second_probe", "after"),
        ("second_probe", "before"),
    ]


# ══ CLAUSE 2 — a planted regression shows a drop on its own card ══════════════


def _skill_proposal(gate_home, procedure: str):
    """File a REAL skill-promotion proposal through the real queue. Returns its id."""
    from personalclaw.learning import skill_promotion

    promotion = skill_promotion.promote(
        name="Gate Probe Procedure",
        description="what to do when the probe runs",
        procedure=procedure,
        rationale="we re-derived this three times",
    )
    assert promotion.filed, promotion.refusal
    return promotion.proposal.id


def test_a_planted_regression_shows_a_score_drop_on_the_proposal_card(gate_home):
    """🔑 THE ATOM'S OWN PROOF, done literally.

    A real skill-promotion proposal whose candidate procedure contains the phrase the gate
    scenario forbids. The candidate is rendered by the REAL install rail
    (``skill_promotion.candidate_files`` → ``SkillsLoader.create_auto_skill``), staged as the
    ``after`` arm, and scored by the REAL ``Assertion.check``. The drop is produced by the plant,
    not by a hardcoded number.

    Then the card: the report is persisted on the proposal and projects onto the inbox ROW — the
    surface a reviewer decides from — with ``before``, ``after`` and the ``pin``.
    """
    pid = _skill_proposal(gate_home, f"Always mention {FORBIDDEN} in your answer.")
    fake = _ScoringMatrix()
    report = gate_mod.gate_proposal(pid, run_matrix=fake, meter=SpendMeter(config_dir=gate_home))

    assert report is not None
    assert report.state == gate_mod.GATE_GATED
    assert report.before["mean_score"] == 1.0
    assert report.after["mean_score"] == 0.0
    assert report.delta == -1.0
    assert report.regressed is True

    # The candidate really was rendered through the rail, and it really is what got staged.
    staged = fake.calls
    assert ("gate_probe", "after") in staged and ("gate_probe", "before") in staged

    # ── and it is on the CARD, before the user accepts ──
    from personalclaw.learning import proposals as queue
    from personalclaw.learning.inbox import row_from_proposal

    prop = queue.get(pid)
    assert prop is not None and prop.status == "pending"
    row = row_from_proposal(prop).to_dict()
    assert row["gate"]["state"] == gate_mod.GATE_GATED
    assert row["gate"]["before"] == 1.0
    assert row["gate"]["after"] == 0.0
    assert row["gate"]["regressed"] is True
    # {before, after, PIN} — the pin identifies what produced the pair.
    assert row["gate"]["pin"]["model_fp"]
    assert row["gate"]["pin"]["scenario_sha256"] == gate_mod.gate_subset().sha256()


def test_a_CLEAN_candidate_shows_no_drop(gate_home):
    """The vacuity partner for the leg above: same plumbing, no plant, no drop.

    If the two rendered the same numbers the regression test would be measuring the pipeline
    rather than the regression.
    """
    pid = _skill_proposal(gate_home, "Answer briefly and cite the run.")
    report = gate_mod.gate_proposal(pid, run_matrix=_ScoringMatrix())
    assert report is not None
    assert report.before["mean_score"] == 1.0
    assert report.after["mean_score"] == 1.0
    assert report.delta == 0.0
    assert report.regressed is False


def test_the_candidate_comes_from_the_real_install_rail(gate_home):
    """``candidate_files`` renders what an ACCEPT would write, not an approximation of it.

    Compared byte-for-byte against what ``install_accepted_skill`` puts on disk. A re-rendered
    approximation would drift from the frontmatter that decides whether the skill is even
    surfaced, so the gate would be scoring a file that never ships.
    """
    from personalclaw.learning import proposals as queue
    from personalclaw.learning import skill_promotion
    from personalclaw.skills.loader import skills_dir

    pid = _skill_proposal(gate_home, "Answer briefly.")
    prop = queue.get(pid)
    assert prop is not None
    rendered = skill_promotion.candidate_files(prop.to_dict())
    assert list(rendered) == ["skills/auto/gate-probe-procedure/SKILL.md"]

    installed_name = skill_promotion.install_accepted_skill(prop.to_dict())
    on_disk = (skills_dir() / installed_name / "SKILL.md").read_text(encoding="utf-8")
    assert rendered["skills/auto/gate-probe-procedure/SKILL.md"] == on_disk


def test_before_reads_the_live_artifact_and_after_the_candidate(gate_home):
    """Both arms name the SAME paths, so the two runs differ only in the bytes at them."""
    from personalclaw.learning import proposals as queue
    from personalclaw.skills.loader import skills_dir

    pid = _skill_proposal(gate_home, "Answer briefly.")
    prop = queue.get(pid)
    assert prop is not None

    live = skills_dir() / "auto" / "gate-probe-procedure" / "SKILL.md"
    live.parent.mkdir(parents=True, exist_ok=True)
    live.write_text("the incumbent body", encoding="utf-8")

    before, after = gate_mod.arms_for_proposal(prop.to_dict())
    assert set(before.files) == set(after.files)
    assert before.files["skills/auto/gate-probe-procedure/SKILL.md"] == "the incumbent body"
    assert "the incumbent body" not in after.files["skills/auto/gate-probe-procedure/SKILL.md"]


def test_a_gate_run_writes_a_pinned_ledger_row_with_before_in_score_old(gate_home):
    pid = _skill_proposal(gate_home, f"Always mention {FORBIDDEN}.")
    gate_mod.gate_proposal(pid, run_matrix=_ScoringMatrix())
    rows = [r for r in store.read_results() if r.get("kind") == gate_mod.GATE_KIND]
    assert len(rows) == 1
    assert rows[0]["score_old"] == "1.0"
    assert rows[0]["score_new"] == "0.0"
    assert rows[0]["verdict"] == "regression"
    # Pinned, from the pin and not from the caller.
    assert rows[0]["model_fp"]
    assert rows[0]["scenario_id"] == gate_mod.GATE_KIND


# ══ CLAUSE 3 — bounded and metered via SpendMeter ═════════════════════════════


def test_an_unbudgeted_gate_is_ungated_not_unbounded(tmp_path, monkeypatch, no_shipped_library):
    """``budget_usd == 0`` means UNLIMITED to ``Budget``, which is the one thing a gate must not be.

    So the refusal is structural: no ceiling ⇒ no run ⇒ ``ungated`` with a reason naming the knob.
    """
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    _write_home(tmp_path, budget=0.0)
    _install(tmp_path, _scenario("gate_probe", tiers=["gate"]))
    fake = _ScoringMatrix()
    report = gate_mod.run_gate(run_id="r1", arms=_arms({}, {"a": "b"}), run_matrix=fake)
    assert report.state == gate_mod.GATE_UNGATED
    assert "budget" in report.reason
    assert fake.calls == []  # nothing was called


def test_the_childs_reported_spend_is_charged_to_the_meter(gate_home):
    meter = SpendMeter(config_dir=gate_home)
    gate_mod.run_gate(
        run_id="r1",
        arms=_arms({}, {"a": "b"}),
        run_matrix=_ScoringMatrix(dollars_per_cell=0.01, tokens_per_cell=100),
        meter=meter,
    )
    totals = meter.run_totals(f"{gate_mod.GATE_KIND}:r1")
    # Two arms × one scenario × $0.01.
    assert totals.dollars == pytest.approx(0.02)
    assert totals.tokens == 200


def test_the_budget_STOPS_the_sweep_and_names_what_did_not_run(gate_home):
    """Bounded means it stops, not that it reports.

    Six scenarios × two arms at $0.30 a cell against a $1.00 ceiling: the meter crosses the cap
    partway and the remaining cells are NOT run — and they are named, because a silently short
    sweep is a weaker claim presented as a full one.
    """
    _install(gate_home, *[_scenario(f"p{i}", tiers=["gate"]) for i in range(5)])
    meter = SpendMeter(config_dir=gate_home)
    fake = _ScoringMatrix(dollars_per_cell=0.30)
    report = gate_mod.run_gate(
        run_id="r1", arms=_arms({}, {"a": "b"}), run_matrix=fake, meter=meter, budget_usd=1.0
    )
    assert report.bound["halted"] is True
    assert report.bound["not_run"]
    assert len(fake.calls) < 12  # 6 scenarios × 2 arms would be 12
    # VACUITY FLOOR: the same sweep with a ceiling it cannot reach runs every cell, so the short
    # sweep above is the bound biting and not a selection bug.
    roomy = _ScoringMatrix(dollars_per_cell=0.30)
    ok = gate_mod.run_gate(
        run_id="r2",
        arms=_arms({}, {"a": "b"}),
        run_matrix=roomy,
        meter=SpendMeter(config_dir=gate_home),
        budget_usd=1000.0,
    )
    assert ok.bound["halted"] is False
    assert len(roomy.calls) == 12


def test_an_unobserved_spend_is_not_reported_as_zero(gate_home):
    """ "No cell reported a spend" and "the run was free" are the same number, different facts."""
    report = gate_mod.run_gate(
        run_id="r1", arms=_arms({}, {"a": "b"}), run_matrix=_ScoringMatrix(dollars_per_cell=0.0)
    )
    # The stand-in DOES report a spend block (of zero), so `observed` is true and the dollars are
    # a real zero.
    assert report.spend["observed"] is True
    # With no cell artifact at all, the absence is carried instead.
    assert gate_mod.cell_spend("no-such-matrix")["observed"] is False


# ══ CLAUSE 4 — "ungated" is honest, and never blocks ═════════════════════════


def test_a_proposal_with_no_gate_run_projects_to_ungated_with_a_reason(gate_home):
    """The ROW is the surface a reviewer decides from, so the absence has to be legible THERE."""
    from personalclaw.learning import proposals as queue
    from personalclaw.learning.inbox import row_from_proposal

    pid = _skill_proposal(gate_home, "Answer briefly.")
    prop = queue.get(pid)
    assert prop is not None
    assert prop.gate == {}  # nothing ran
    row = row_from_proposal(prop).to_dict()
    assert row["gate"]["state"] == gate_mod.GATE_UNGATED
    assert row["gate"]["reason"]
    # Never a zero standing in for a measurement that never happened.
    assert row["gate"]["before"] is None
    assert row["gate"]["after"] is None
    assert row["gate"]["delta"] is None


def test_accept_succeeds_with_no_gate_run(gate_home):
    """🔑 NEVER BLOCKS. A gate that failed closed on its own absence would stop a user shipping a
    change because the GATE broke."""
    from personalclaw.learning import proposals as queue

    pid = _skill_proposal(gate_home, "Answer briefly.")
    installed: list[str] = []
    accepted = queue.accept(pid, installer=lambda p: installed.append(p.id), actor="user")
    assert accepted.status == "accepted"
    assert installed == [pid]


def test_accept_succeeds_with_a_REGRESSED_gate(gate_home):
    """The columns inform the decision; they do not take it. The user may know something the
    twelve scenarios do not."""
    from personalclaw.learning import proposals as queue

    pid = _skill_proposal(gate_home, f"Always mention {FORBIDDEN}.")
    report = gate_mod.gate_proposal(pid, run_matrix=_ScoringMatrix())
    assert report is not None and report.regressed is True
    accepted = queue.accept(pid, installer=lambda _p: None, actor="user")
    assert accepted.status == "accepted"


def test_evals_off_is_ungated_and_calls_nothing(tmp_path, monkeypatch, no_shipped_library):
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    _write_home(tmp_path, enabled=False)
    _install(tmp_path, _scenario("gate_probe", tiers=["gate"]))
    fake = _ScoringMatrix()
    report = gate_mod.run_gate(run_id="r1", arms=_arms({}, {"a": "b"}), run_matrix=fake)
    assert report.state == gate_mod.GATE_UNGATED
    assert "off" in report.reason
    assert fake.calls == []


def test_an_unpinnable_home_is_ungated_and_INVENTS_NO_FINGERPRINT(
    tmp_path, monkeypatch, no_shipped_library
):
    """No model bound ⇒ no honest ``model_fingerprint`` ⇒ ungated.

    Minting one would poison every per-fingerprint baseline that reads the same ``results.tsv``,
    which surfaces months later as an inexplicable regression. The ruling ES-11 recorded for its
    own unscored candidate transfers verbatim.
    """
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    _write_home(tmp_path)
    (tmp_path / "active_models.json").write_text("{}", encoding="utf-8")
    _install(tmp_path, _scenario("gate_probe", tiers=["gate"]))
    fake = _ScoringMatrix()
    report = gate_mod.run_gate(run_id="r1", arms=_arms({}, {"a": "b"}), run_matrix=fake)
    assert report.state == gate_mod.GATE_UNGATED
    assert "pinned" in report.reason and "model_fingerprint" in report.reason
    assert report.pin == {}
    assert gate_mod.summary(report.to_dict())["pin"] == {}
    assert fake.calls == []


def test_an_empty_subset_is_ungated_not_a_silent_pass(tmp_path, monkeypatch, no_shipped_library):
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    _write_home(tmp_path)
    _install(tmp_path, _scenario("untagged_only"))
    report = gate_mod.run_gate(run_id="r1", arms=_arms({}, {"a": "b"}), run_matrix=_ScoringMatrix())
    assert report.state == gate_mod.GATE_UNGATED
    assert "tagged" in report.reason


def test_an_ungateable_KIND_is_ungated_and_says_which_kind(gate_home):
    """A ``lesson_batch`` declares no candidate artifact. The absence names the kind rather than
    reading as a passing gate."""
    from personalclaw.learning import proposals as queue

    _verdict, prop = queue.enqueue(
        kind="lesson_batch", title="three lessons", body="always use uv", provenance="human"
    )
    assert prop is not None
    report = gate_mod.gate_proposal(prop.id, run_matrix=_ScoringMatrix())
    assert report is not None
    assert report.state == gate_mod.GATE_UNGATED
    assert "lesson_batch" in report.reason


def test_attaching_a_report_is_not_a_decision(gate_home):
    """A measurement must not re-sort the queue: ``attach_gate`` leaves status and the timestamp
    alone, because bumping them would look like the user did something."""
    from personalclaw.learning import proposals as queue

    pid = _skill_proposal(gate_home, "Answer briefly.")
    before = queue.get(pid)
    assert before is not None
    assert queue.attach_gate(pid, {"state": "gated"}) is True
    after = queue.get(pid)
    assert after is not None
    assert after.status == before.status
    assert after.updated_at == before.updated_at
    assert after.gate == {"state": "gated"}


def test_a_missing_proposal_is_None_not_a_fabricated_report(gate_home):
    assert gate_mod.gate_proposal("no-such-proposal") is None


# ══ the child-side seam, against the REAL code ════════════════════════════════


def test_the_arm_reaches_the_CHILD_env_and_never_the_parents(gate_home, monkeypatch):
    """The §1.3 isolation contract, extended to the gate arm: the parent's ``os.environ`` is never
    touched and the child's copy carries the staged files."""
    import os

    calls: list[dict] = []

    def fake_run(args, *, env, timeout, capture_output, text):
        calls.append(dict(env))
        return types.SimpleNamespace(
            returncode=0,
            stdout=render_result_line({"ok": True, "passed": True, "score": 1.0}),
            stderr="",
        )

    monkeypatch.setattr(runner_mod.subprocess, "run", fake_run)
    arm = gate_mod.ArtifactArm(label="after", files={"skills/auto/x/SKILL.md": "candidate body"})
    runner_mod.run_matrix(
        MatrixSpec(subject="gate_probe", trial_count=1), matrix_id="m1", artifact_arm=arm
    )
    assert len(calls) == 1
    assert "candidate body" in calls[0][gate_mod.ARM_ENV]
    assert gate_mod.ARM_ENV not in os.environ
    # And the retained descriptor records WHICH arm, so a surprising cell is attributable.
    descriptor = json.loads(
        (store.matrix_dir("m1") / "cell-0000" / "descriptor.json").read_text(encoding="utf-8")
    )
    assert descriptor["arm"] == "after"


def test_apply_in_child_stages_into_the_throwaway_home(tmp_path, monkeypatch):
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path / "cell"))
    arm = gate_mod.ArtifactArm(label="after", files={"skills/auto/x/SKILL.md": "body"})
    written = gate_mod.apply_in_child(arm)
    assert written == ["skills/auto/x/SKILL.md"]
    assert (tmp_path / "cell" / "skills" / "auto" / "x" / "SKILL.md").read_text() == "body"


def test_apply_in_child_REFUSES_the_real_home(monkeypatch):
    """The negative rail: a mis-spawned cell must never write a candidate into the operator's home.

    Shared with the ablation overlay by construction — ``throwaway_home()`` is one function, so
    there is exactly one answer to "may I write here".
    """
    monkeypatch.delenv("PERSONALCLAW_HOME", raising=False)
    arm = gate_mod.ArtifactArm(label="after", files={"a": "b"})
    with pytest.raises(OverlayRefusedError):
        gate_mod.apply_in_child(arm)
    monkeypatch.setenv("PERSONALCLAW_HOME", str(Path.home() / ".personalclaw"))
    with pytest.raises(OverlayRefusedError):
        gate_mod.apply_in_child(arm)


def test_a_before_arm_with_nothing_staged_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path / "cell"))
    assert gate_mod.apply_in_child(gate_mod.ArtifactArm(label="before", files={})) == []
    assert gate_mod.apply_in_child(None) == []


@pytest.mark.parametrize(
    "relpath",
    ["/etc/passwd", "../escaped", "skills/../../escaped", ""],
)
def test_an_arm_path_that_could_escape_the_cell_is_refused(tmp_path, relpath):
    with pytest.raises(gate_mod.ArmRefusedError):
        gate_mod.resolve_in_home(tmp_path / "cell", relpath)


def test_a_garbage_arm_env_runs_the_cell_unmodified(monkeypatch):
    """Same fail-soft as the ablation overlay's ``decode``: an unparseable arm is a plain cell, not
    a crash, because a spawn-env fault must not read as a scored regression."""
    assert gate_mod.decode("") is None
    assert gate_mod.decode("{not json") is None
    assert gate_mod.decode(json.dumps({"label": "sideways", "files": {}})) is None


def test_spawn_env_for_never_mutates_its_input():
    base = {"PATH": "/bin"}
    out = gate_mod.spawn_env_for(base, gate_mod.ArtifactArm(label="after", files={"a": "b"}))
    assert base == {"PATH": "/bin"}
    assert gate_mod.ARM_ENV in out


# ══ the report shape ═════════════════════════════════════════════════════════


def test_an_absent_report_summarizes_as_ungated_with_the_not_run_reason():
    for absent in (None, {}):
        row = gate_mod.summary(absent)
        assert row["state"] == gate_mod.GATE_UNGATED
        assert row["reason"] == gate_mod.UNGATED_NOT_RUN
        assert row["before"] is None and row["after"] is None


def test_an_unrecognized_state_reads_as_ungated():
    """A record from a build that spelled the state differently must not read as gated."""
    assert gate_mod.summary({"state": "probably_fine"})["state"] == gate_mod.GATE_UNGATED


def test_delta_is_None_not_zero_when_an_arm_never_scored():
    """ "The two arms tied" and "one arm never scored" are the same number and different facts."""
    report = gate_mod.GateReport(state=gate_mod.GATE_GATED)
    report.before = {"mean_score": 0.5}
    report.after = {"mean_score": None}
    assert report.delta is None
    assert report.regressed is False
    assert gate_mod.verdict_of(report) == "verifier_absent"


def test_a_tie_is_not_a_regression():
    report = gate_mod.GateReport(state=gate_mod.GATE_GATED)
    report.before = {"mean_score": 0.5}
    report.after = {"mean_score": 0.5}
    assert report.delta == 0.0
    assert report.regressed is False
    assert gate_mod.verdict_of(report) == "pass"


def test_the_cli_prints_not_measured_and_never_a_zero(capsys):
    """The CLI says the same word about the same absence as the Learning panels do."""
    from personalclaw.cli_commands import _print_gate_report

    report = gate_mod.GateReport(state=gate_mod.GATE_GATED, run_id="r1")
    _print_gate_report(report)
    out = capsys.readouterr().out
    assert "not measured" in out
    assert "0.0000" not in out

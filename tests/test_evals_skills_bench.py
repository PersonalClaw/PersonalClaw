"""EVALUATION-SUBSTRATE ES-7 §3.3 — the skills/templates impact bench.

The clause is "replays consulted runs with a skill surfaced-vs-suppressed", and it has two
easy-to-fake halves:

* **The population.** "Consulted" is the WF2-R13 ledger event, so the bench must read that
  event — not a proxy. ``test_consulted_runs_reads_the_wf2_r13_ledger_event`` writes real
  events through the real emitter (``Journal.consulted``) and asserts the reader finds them.
* **The suppression.** A suppressed arm whose prompt still carries the skill body measures
  nothing. The negative ("the body is absent") is trivially satisfied by an empty prompt, so
  every assertion of it here is PAIRED with the positive ("the body is present in the
  surfaced arm") — that pairing is the vacuity floor, and
  ``test_an_absent_skill_does_not_count_as_verified_suppression`` proves the pair reds when
  only the negative holds.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from personalclaw.evals import ablation, harvest
from personalclaw.evals import overlay as overlay_lib
from personalclaw.evals import skills_bench
from personalclaw.evals.matrix import FAILED, PASSED, VERIFIER_ABSENT, CellResult, MatrixResult
from personalclaw.skills import SkillsLoader
from personalclaw.skills.suppression import SUPPRESSED_SKILLS_ENV, is_suppressed

NOW = datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)

SKILL_NAME = "release-flow"
# The tail is what the presence probe samples, so it has to be distinctive: the allocator's
# REDUCED tier emits the title + description, and a probe taken from the top would report a
# summarized skill as fully present.
SKILL_BODY = (
    "# Release flow\n\n"
    "Cut a release branch, run the gate, then publish.\n\n"
    "## Steps\n"
    "1. Branch from main.\n"
    "2. Run the full gate.\n"
    "3. Publish and announce.\n\n"
    "UNIQUE-TAIL-MARKER: the distinctive closing paragraph the presence probe samples, long "
    "enough that no summary would reproduce it verbatim by accident whatsoever.\n"
)


@pytest.fixture()
def bench_home(tmp_path, monkeypatch):
    """Isolate the run store AND the evals scenario library under a tmp home.

    `$PERSONALCLAW_HOME` is what actually redirects an import-bound store: `workflows.store` and
    `evals.store` bind `config_dir` as a module-level SYMBOL, and `config_dir()` re-reads the env
    var on every call. Deliberately NOT also patching `personalclaw.config.loader.config_dir` here:
    conftest's real-home rail already re-points every binding of that function object, and adding a
    second patch on top of it was MEASURED to leak the run store between tests in one process — six
    tests in this file reuse the fixed run id `run-a` and started failing on
    `UNIQUE constraint failed: runs.id`. The redirect is asserted rather than assumed.
    """
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    monkeypatch.delenv(SUPPRESSED_SKILLS_ENV, raising=False)
    from personalclaw.evals import scenarios as _sc
    from personalclaw.workflows import store as _wf_store

    assert _sc.installed_dir().is_relative_to(home), "the library must not resolve to the real home"
    assert _wf_store._db_path().is_relative_to(
        home
    ), "the run store must not resolve to the real home"
    return home


@pytest.fixture()
def loader(bench_home):
    skills = bench_home / "skills"
    (skills / SKILL_NAME).mkdir(parents=True)
    (skills / SKILL_NAME / "SKILL.md").write_text(SKILL_BODY, encoding="utf-8")
    return SkillsLoader(skills_path=skills, install_builtins=False)


# ── the choke point ───────────────────────────────────────────────────────────


def test_suppression_is_enforced_at_load_skill(loader, monkeypatch):
    """The ONE place a body is read. Suppressing here covers forced, surfaced and
    ``skill_invoke`` alike — a per-caller filter would leave one path leaking."""
    assert loader.load_skill(SKILL_NAME), "vacuity floor: the skill loads when not suppressed"
    monkeypatch.setenv(SUPPRESSED_SKILLS_ENV, SKILL_NAME)
    assert is_suppressed(SKILL_NAME)
    # None, not "": every caller already guards with `if content:`, so a suppressed skill is
    # indistinguishable from an absent one and cannot reach a prompt by any route.
    assert loader.load_skill(SKILL_NAME) is None


def test_an_absent_env_var_is_exactly_the_shipped_behaviour(loader, monkeypatch):
    monkeypatch.delenv(SUPPRESSED_SKILLS_ENV, raising=False)
    shipped = loader.load_skill(SKILL_NAME)
    monkeypatch.setenv(SUPPRESSED_SKILLS_ENV, "")
    assert loader.load_skill(SKILL_NAME) == shipped, "empty must mean 'suppress nothing'"
    monkeypatch.setenv(SUPPRESSED_SKILLS_ENV, "some/other-skill")
    assert loader.load_skill(SKILL_NAME) == shipped, "another name must not suppress this one"


# ── the prompt-level negative, WITH its positive ──────────────────────────────


def test_the_suppressed_arms_prompt_does_not_contain_the_skill_body(loader):
    probe = skills_bench.body_probe(loader, SKILL_NAME)
    # Sampled from the END: the allocator's REDUCED tier emits the title + description, so a
    # probe taken from the top would report a summarized skill as fully present.
    assert probe and SKILL_BODY.strip().endswith(probe)
    assert probe not in SKILL_BODY[: len(SKILL_BODY) // 2]

    surfaced = skills_bench.arm_prompt(loader, SKILL_NAME, query="cut a release")
    # THE POSITIVE HALF — the vacuity floor for the negative below. Without it, an empty
    # prompt set would satisfy "the body is absent" and the arms would be identical.
    assert probe in surfaced, "the surfaced arm must actually carry the body"
    assert len(surfaced) > len(probe)

    with skills_bench._suppressing(SKILL_NAME):
        suppressed = skills_bench.arm_prompt(loader, SKILL_NAME, query="cut a release")
    # THE NEGATIVE — this is what makes the comparison mean anything.
    assert probe not in suppressed
    assert suppressed == ""


def test_suppression_restores_the_env_exactly(loader, monkeypatch):
    import os

    monkeypatch.delenv(SUPPRESSED_SKILLS_ENV, raising=False)
    with skills_bench._suppressing(SKILL_NAME):
        assert os.environ[SUPPRESSED_SKILLS_ENV] == SKILL_NAME
    # Absence restored AS absence: an empty string would leave the var present, a different
    # state from never having been set.
    assert SUPPRESSED_SKILLS_ENV not in os.environ


def test_verify_suppression_requires_both_halves(loader):
    check = skills_bench.verify_suppression(loader, SKILL_NAME, query="cut a release")
    assert check.verified is True
    assert check.present_surfaced is True and check.present_suppressed is False
    assert check.surfaced_prompt_chars > 0 and check.suppressed_prompt_chars == 0
    assert check.to_dict()["verified"] is True


def test_an_absent_skill_does_not_count_as_verified_suppression(loader):
    """THE VACUITY FLOOR for the negative.

    An unknown skill's prompt is empty in BOTH arms, so "the body is absent from the
    suppressed arm" holds trivially. The predicate must still report *not verified* — this
    is the exact shape that would let the bench report a fabricated 0.0 delta.
    """
    check = skills_bench.verify_suppression(loader, "no/such-skill")
    assert check.present_suppressed is False, "the negative holds trivially here"
    assert check.verified is False, "...and must not be enough on its own"
    assert "no loadable body" in check.reason


def test_a_suppression_that_does_not_bite_is_reported_as_unverified(loader, monkeypatch):
    """The other half of the floor: a suppression seam that silently no-ops.

    Simulated by breaking the choke point — which is what a future refactor that moved the
    body read off ``load_skill`` would do — and asserting the check catches it rather than
    reporting identical arms as a real measurement.
    """
    monkeypatch.setattr(
        "personalclaw.skills.suppression.is_suppressed", lambda name, env=None: False
    )
    check = skills_bench.verify_suppression(loader, SKILL_NAME, query="cut a release")
    assert check.present_surfaced is True and check.present_suppressed is True
    assert check.verified is False
    assert "STILL carries the body" in check.reason


# ── the consulted population (WF2-R13) ────────────────────────────────────────


def _seed_consulted_run(run_id: str, refs: list[str]) -> None:
    from personalclaw.workflows import store as wf_store
    from personalclaw.workflows.journal import Journal
    from personalclaw.workflows.models import WorkflowRun

    wf_store.create(WorkflowRun(id=run_id, workflow_name="release-triage"))
    journal = Journal(run_id=run_id)
    for index, ref in enumerate(refs):
        journal.consulted(f"nodes/{index}", f"n{index}", ref=ref)


def test_consulted_runs_reads_the_wf2_r13_ledger_event(bench_home):
    """Read through the REAL emitter, so a change to the event's shape breaks this."""
    _seed_consulted_run("run-a", [f"skill:{SKILL_NAME}", "skill:other"])
    _seed_consulted_run("run-b", ["skill:other"])

    rows = skills_bench.consulted_runs(SKILL_NAME)
    assert [r["run_id"] for r in rows] == ["run-a"]
    assert rows[0]["ref"] == f"skill:{SKILL_NAME}"
    assert rows[0]["workflow_name"] == "release-triage"
    # Vacuity floor: the scan DID see run-b's events, it just did not match them.
    assert skills_bench.consulted_runs("other") != []


def test_ref_matching_is_the_shared_matcher_and_not_a_substring_test():
    """The bench must not own a second copy of this predicate.

    Both readers of a `consulted` ref — the live event scan here and the frozen
    `harvest.consulted_refs` scope the replay population uses — go through ONE function, or they
    disagree about `foo` vs `foo-bar` and one skill's bench replays another skill's runs.
    """
    assert skills_bench.harvest.ref_names_skill is harvest.ref_names_skill
    assert not hasattr(skills_bench, "_ref_names_skill")
    assert harvest.ref_names_skill("skill:code/foo", "code/foo") is True
    assert harvest.ref_names_skill("skill:code/foo", "foo") is True
    assert harvest.ref_names_skill("skill:code/foo-bar", "foo") is False


def test_no_consulted_run_is_a_refusal_not_a_zero_delta(bench_home, loader):
    report = skills_bench.bench_skill(SKILL_NAME, subject="triage-scenario", loader=loader, now=NOW)
    assert report.verdict == ablation.INCONCLUSIVE
    assert report.delta is None
    assert "no replay population" in report.reason
    assert report.consulted_run_ids == []


# ── the replay population: the consulted runs' OWN harvested inputs ───────────


def _harvest_case(workflow: str, refs: tuple[str, ...]) -> str:
    """Harvest one terminal run that consulted `refs`, and return its case name."""
    from personalclaw.evals import harvest as hv
    from personalclaw.workflows import store as wf_store
    from personalclaw.workflows.journal import Journal
    from personalclaw.workflows.models import InstanceState, RunStatus, WorkflowRun

    run = wf_store.create(WorkflowRun(id="", workflow_name=workflow))
    run.status = RunStatus.COMPLETE
    run = wf_store.save(run)
    journal = Journal(run_id=run.id)
    journal.run_started(workflow, inputs={"topic": workflow}, spec_version=1)
    journal.step_completed("root.fetch", "fetch", epoch=1, cache_key="ck", state=InstanceState.DONE)
    for index, ref in enumerate(refs):
        journal.consulted("root.fetch", f"n{index}", ref=ref)
    journal.run_finished(RunStatus.COMPLETE.value, elapsed_secs=1.0, tokens=1)
    report = hv.harvest()
    return next(c.name for c in report.cases if c.run_id == run.id)


def test_the_default_subject_is_a_consulted_runs_own_harvested_case(bench_home, loader):
    """The clause: "replays consulted runs" — in the INPUTS, not only in the population.

    Both directions in one call: the case whose run consulted this skill is what gets replayed,
    and the case whose run consulted a DIFFERENT skill is not, even though both are in the suite.
    """
    from personalclaw.evals import harvest as hv

    mine = _harvest_case("release-triage", (f"skill:{SKILL_NAME}",))
    theirs = _harvest_case("other-flow", ("skill:some/other",))
    assert {c["name"] for c in hv.load_harvested_suite()} == {mine, theirs}, "vacuity floor"

    population = skills_bench.replay_population(SKILL_NAME)
    assert type(population) is skills_bench.ReplayPopulation
    assert population.subject == mine
    assert theirs not in population.candidates

    seen: list = []
    report = skills_bench.bench_skill(
        SKILL_NAME,
        loader=loader,
        trials=1,
        now=NOW,
        run_matrix=_fake_matrix(
            {skills_bench.ARM_SURFACED: [0.9], skills_bench.ARM_SUPPRESSED: [0.4]}, seen=seen
        ),
    )
    spec, _matrix_id = seen[0]
    # The subject handed to the REAL MatrixSpec is the harvested case, not a hand-named scenario.
    assert spec.subject == mine
    assert report.subject == mine
    assert report.subject_origin == "harvested"
    assert report.subject_run_id and report.subject_run_id in report.consulted_run_ids
    assert report.subject_candidates == [mine]
    assert report.verdict == ablation.KEEP


def test_an_explicit_subject_is_recorded_as_the_operators_and_not_a_replay(bench_home, loader):
    """The two claims must stay distinguishable in the report."""
    _harvest_case("release-triage", (f"skill:{SKILL_NAME}",))
    report = skills_bench.bench_skill(
        SKILL_NAME,
        subject="triage-scenario",
        loader=loader,
        trials=1,
        now=NOW,
        run_matrix=_fake_matrix(
            {skills_bench.ARM_SURFACED: [0.9], skills_bench.ARM_SUPPRESSED: [0.4]}
        ),
    )
    assert report.subject == "triage-scenario"
    assert report.subject_origin == "operator"
    assert report.subject_run_id == ""
    assert report.subject_candidates == []


def test_a_consulted_run_with_no_harvested_case_refuses_before_spending(bench_home, loader):
    """The live ledger says the skill was consulted, but no case was harvested from it.

    Replaying SOMETHING ELSE here would attribute a delta to a skill the artifact never loaded,
    so the harvest's own refusal sentence is carried through and no matrix is spent.
    """
    _seed_consulted_run("run-a", [f"skill:{SKILL_NAME}"])

    def _must_not_run(spec, *, matrix_id, **kwargs):  # pragma: no cover - asserted absent
        raise AssertionError("the bench must not replay an artifact the skill never touched")

    report = skills_bench.bench_skill(SKILL_NAME, loader=loader, now=NOW, run_matrix=_must_not_run)
    assert report.consulted_run_ids == ["run-a"], "vacuity floor: the population WAS found"
    assert report.verdict == ablation.INCONCLUSIVE
    assert report.delta is None
    assert report.subject == ""
    assert "no replay population" in report.reason
    assert f"consulted {SKILL_NAME!r}" in report.reason


def test_the_cli_bench_reaches_the_filter_with_no_subject_flag(bench_home, loader, capsys):
    """The CALL SITE, driven the way a user types it: `personalclaw ablation --skill <name>`.

    `--subject` defaults to `""`, so before this the default invocation refused outright — a
    defaulted field is an unsupplied input, and the bench was dead in practice. This asserts the
    real dispatch chain resolves the subject from the consulted runs instead.
    """
    import argparse

    from personalclaw import cli, cli_commands

    assert cli._ablation is cli_commands._ablation, "the CLI must dispatch to this function"
    mine = _harvest_case("release-triage", (f"skill:{SKILL_NAME}",))
    _harvest_case("other-flow", ("skill:some/other",))

    cli_commands._ablation(
        argparse.Namespace(
            list_components=False, skill=SKILL_NAME, subject="", dry_run=True, trials=1, budget=0.0
        )
    )
    out = capsys.readouterr().out
    assert mine in out
    assert "(harvested)" in out
    assert "harvested candidates: 1" in out
    assert "--dry-run: nothing was called." in out


# ── the bench end to end ──────────────────────────────────────────────────────


def _fake_matrix(scores, *, seen=None):
    def _run(spec, *, matrix_id: str, **kwargs):
        if seen is not None:
            seen.append((spec, matrix_id))
        cells = []
        for arm, values in scores.items():
            for value in values:
                if value is None:
                    cells.append(
                        CellResult(coords={overlay_lib.ARM_AXIS: arm}, outcome=VERIFIER_ABSENT)
                    )
                else:
                    cells.append(
                        CellResult(
                            coords={overlay_lib.ARM_AXIS: arm},
                            outcome=PASSED if value >= 0.5 else FAILED,
                            score=value,
                        )
                    )
        return MatrixResult(spec=spec, cells=cells, aggregates={})

    return _run


def test_bench_replays_consulted_runs_surfaced_vs_suppressed(bench_home, loader):
    _seed_consulted_run("run-a", [f"skill:{SKILL_NAME}"])
    seen: list = []
    report = skills_bench.bench_skill(
        SKILL_NAME,
        subject="triage-scenario",
        loader=loader,
        trials=2,
        query="cut a release",
        now=NOW,
        run_matrix=_fake_matrix(
            {skills_bench.ARM_SURFACED: [0.9, 0.9], skills_bench.ARM_SUPPRESSED: [0.5, 0.5]},
            seen=seen,
        ),
    )
    spec, _matrix_id = seen[0]
    assert spec.axes[overlay_lib.ARM_AXIS] == [
        skills_bench.ARM_SURFACED,
        skills_bench.ARM_SUPPRESSED,
    ]
    assert spec.component["kind"] == overlay_lib.KIND_SKILL
    assert spec.component["target"] == SKILL_NAME
    assert report.consulted_run_ids == ["run-a"]
    assert report.suppression["verified"] is True
    assert report.verdict == ablation.KEEP
    assert report.delta == pytest.approx(0.4)
    assert report.reason == ""


def test_a_no_delta_skill_benches_as_remove(bench_home, loader):
    _seed_consulted_run("run-a", [f"skill:{SKILL_NAME}"])
    report = skills_bench.bench_skill(
        SKILL_NAME,
        subject="triage-scenario",
        loader=loader,
        trials=1,
        now=NOW,
        run_matrix=_fake_matrix(
            {skills_bench.ARM_SURFACED: [0.90], skills_bench.ARM_SUPPRESSED: [0.895]}
        ),
    )
    assert report.verdict == ablation.REMOVE
    assert report.delta == pytest.approx(0.005)


def test_an_unverified_suppression_refuses_before_spending_a_run(bench_home, loader, monkeypatch):
    _seed_consulted_run("run-a", [f"skill:{SKILL_NAME}"])
    monkeypatch.setattr(
        "personalclaw.skills.suppression.is_suppressed", lambda name, env=None: False
    )

    def _must_not_run(spec, *, matrix_id, **kwargs):  # pragma: no cover - asserted absent
        raise AssertionError("the bench must not spend a matrix run it cannot interpret")

    report = skills_bench.bench_skill(
        SKILL_NAME,
        subject="triage-scenario",
        loader=loader,
        now=NOW,
        run_matrix=_must_not_run,
    )
    assert report.verdict == ablation.INCONCLUSIVE
    assert report.delta is None
    assert "STILL carries the body" in report.reason


def test_the_bench_never_mutates_live_state(bench_home, loader):
    _seed_consulted_run("run-a", [f"skill:{SKILL_NAME}"])
    (bench_home / "config.json").write_text('{"a": 1}\n', encoding="utf-8")

    def _leaky(spec, *, matrix_id, **kwargs):
        (bench_home / "config.json").write_text('{"a": 2}\n', encoding="utf-8")
        return MatrixResult(spec=spec, cells=[], aggregates={})

    with pytest.raises(ablation.LiveStateMutatedError, match="config.json"):
        skills_bench.bench_skill(
            SKILL_NAME, subject="triage-scenario", loader=loader, now=NOW, run_matrix=_leaky
        )


def test_an_unmeasured_arm_is_reported_not_averaged(bench_home, loader):
    _seed_consulted_run("run-a", [f"skill:{SKILL_NAME}"])
    report = skills_bench.bench_skill(
        SKILL_NAME,
        subject="triage-scenario",
        loader=loader,
        now=NOW,
        run_matrix=_fake_matrix(
            {skills_bench.ARM_SURFACED: [0.9], skills_bench.ARM_SUPPRESSED: [None]}
        ),
    )
    assert report.verdict == ablation.INCONCLUSIVE
    assert report.delta is None
    assert report.reason == "an arm produced no scored cell"

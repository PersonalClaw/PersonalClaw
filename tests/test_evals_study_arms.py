"""EVALUATION-SUBSTRATE ES-5 — what DRIVES a pre-registered template study (§2).

`test_evals_studies.py` covers the instrument. This file covers the two `done_when` clauses
the instrument could not satisfy on its own, and it covers them at the CALL SITE rather than
at the mechanism — because "the mechanism exists and nothing invokes it" is the exact defect
this atom was reopened for.

* **"a flywheel template-diff RUNS a pre-registered study"** →
  `test_filing_a_template_diff_PRE_REGISTERS_a_study` asserts on
  `refiner_tools.file_template_diff`'s own return and on the registration landing on disk,
  and `test_run_study_uses_the_PRODUCTION_arm_runner_by_default` asserts the default
  `arm_runner` is reached when the caller supplies none — the asymmetry with
  `caller=live_judge_caller` that made a template arm unexecutable.
* **"over the harvested suite"** → `test_the_suite_comes_from_the_HARVEST` builds cases from
  a real installed harvested library, and `test_an_empty_harvest_is_a_REFUSAL...` proves the
  `EmptyHarvestError` path is neither a crash nor a silent zero-case green.

Every negative assertion here carries a vacuity floor, in both directions:
`assert_arms_differ` is proved to RAISE on identical arms AND to pass on differing ones, and
the flywheel hook's best-effort guard is proved to actually swallow (`study_id == ""` with
the proposal still filed) rather than being decorative.
"""

from __future__ import annotations

import argparse
import asyncio
import json

import pytest

from personalclaw.evals import scenarios, store, studies, study_arms

WORKFLOW = "es5-suite-demo"
OLD_SPEC = {
    "name": WORKFLOW,
    "version": 1,
    "inputs": {"target": {"type": "string", "required": True}},
    "root": {
        "kind": "infer",
        "id": "answer",
        "config": {"prompt": "Summarize {{inputs.target}} in one paragraph."},
    },
}
DIFF_OPS = [
    {
        "op": "update_node",
        "node_id": "answer",
        "fields": {"prompt": "Summarize {{inputs.target}} in exactly three bullets."},
    }
]


@pytest.fixture()
def eval_home(tmp_path, monkeypatch):
    """An isolated home with a bound fingerprint — the same rail `test_evals_studies` uses.

    `config_dir()` re-reads `PERSONALCLAW_HOME` per call, so the env var IS the isolation.
    The fingerprint is patched because an empty home has no `active_models.json` and ES-2's
    `append_result` rightly refuses an unattributable row.
    """
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    monkeypatch.setattr(
        "personalclaw.evals.pinning.model_fingerprint",
        lambda: {"chat": "Fake:model-a", "eval_judge": "Fake:model-j"},
    )
    return tmp_path


def _install_harvested(count: int, *, workflow: str = WORKFLOW) -> None:
    """Write `count` harvested cases into the installed scenario library."""
    lib = scenarios.installed_dir()
    lib.mkdir(parents=True, exist_ok=True)
    for i in range(count):
        case = {
            "name": f"harvested_{workflow}_run{i}",
            "version": 1,
            "fixture_home": scenarios.DEFAULT_FIXTURE_HOME,
            "judge_criteria": f"carry out run{i}'s recorded request at least as well",
            "harvest": {
                "run_id": f"run{i}",
                "workflow_name": workflow,
                "status": "completed",
                "run_started_at": f"2026-08-1{i}T00:00:00Z",
                "inputs": {"target": f"subsystem-{i}"},
            },
            "sessions": [{"name": "harvested_run", "turns": [{"user": "x", "assertions": []}]}],
        }
        (lib / f"{case['name']}.json").write_text(json.dumps(case), encoding="utf-8")


# ── clause: "over the harvested suite" ───────────────────────────────────────


def test_the_suite_comes_from_the_HARVEST(eval_home):
    """The corpus is the installed harvested library, pinned by scenario hash."""
    _install_harvested(3)
    suite = study_arms.harvested_study_cases(workflow_name=WORKFLOW)

    assert suite.population == 3
    assert not suite.low_power
    assert not suite.refusal
    # The pin names the case AND its content hash, so a later suite cannot be swapped in
    # under the same pre-registration.
    assert all("@" in pin for pin in suite.input_pins)
    # `case_input` is the RECORDED inputs, canonically — the variable an A/B holds still.
    assert json.loads(suite.cases[0].case_input) == {"target": "subsystem-0"}
    assert suite.cases[0].goal.startswith("carry out run0")


def test_an_empty_harvest_is_a_REFUSAL_not_a_zero_case_green(eval_home):
    """`EmptyHarvestError` becomes a labelled refusal — never a crash, never `[]`.

    Both halves matter. A crash would abort the flywheel's filing path over a missing eval
    artifact; a silent zero-case suite would be scored and reported as a green nobody
    measured. So the suite must be EMPTY, must be LOW POWER, and must SAY why.
    """
    suite = study_arms.harvested_study_cases(workflow_name=WORKFLOW)

    assert suite.population == 0
    assert suite.low_power
    assert "no replay population" in suite.refusal


def test_low_power_is_a_LABEL_and_its_threshold_is_the_studies_constant(eval_home):
    """Below `LOW_POWER_CASES` is labelled; at or above it is not. Both directions.

    The floor is the second assertion: a `low_power` property hardwired to True would pass
    the first check and fail this one.
    """
    _install_harvested(studies.LOW_POWER_CASES - 1)
    assert study_arms.harvested_study_cases(workflow_name=WORKFLOW).low_power

    _install_harvested(studies.LOW_POWER_CASES)
    assert not study_arms.harvested_study_cases(workflow_name=WORKFLOW).low_power


# ── the arms, and the vacuity gate over them ─────────────────────────────────


def test_the_arm_prompt_binds_the_cases_recorded_inputs():
    """`{{inputs.*}}` resolves through the engine's own resolver, not a second dialect."""
    body = json.dumps(OLD_SPEC)
    prompt = study_arms.render_arm_prompt(body, case_input=json.dumps({"target": "the ledger"}))

    assert prompt == "Summarize the ledger in one paragraph."
    assert "{{" not in prompt


def test_the_two_arms_render_DIFFERENT_prompts_from_the_diffs_own_ops():
    """OLD is the stored spec, NEW is the ops applied — and nothing is written."""
    old_body, new_body = study_arms.arm_bodies_for_ops(OLD_SPEC, DIFF_OPS)
    case_input = json.dumps({"target": "the ledger"})

    old = study_arms.render_arm_prompt(old_body, case_input=case_input)
    new = study_arms.render_arm_prompt(new_body, case_input=case_input)

    assert old == "Summarize the ledger in one paragraph."
    assert new == "Summarize the ledger in exactly three bullets."
    # The candidate was never installed: measuring a template you had to install first is
    # measuring the live template, not a candidate.
    assert json.loads(old_body)["root"]["config"]["prompt"].endswith("one paragraph.")


def test_identical_arms_are_REFUSED_because_a_tie_would_look_confident():
    """The vacuity gate fires — an A/B whose arms render alike measures nothing."""
    cases = [studies.StudyCase(case_id="c0", goal="g", case_input=json.dumps({"target": "x"}))]
    body = json.dumps(OLD_SPEC)

    with pytest.raises(study_arms.StudyArmError, match="IDENTICAL"):
        study_arms.assert_arms_differ(cases, old_template_body=body, new_template_body=body)


def test_the_identical_arms_gate_can_PASS(eval_home):
    """The floor for the test above: differing arms clear the gate and are counted.

    Without this, a gate hardwired to raise would look like a working guard.
    """
    cases = [studies.StudyCase(case_id="c0", goal="g", case_input=json.dumps({"target": "x"}))]
    old_body, new_body = study_arms.arm_bodies_for_ops(OLD_SPEC, DIFF_OPS)

    assert (
        study_arms.assert_arms_differ(cases, old_template_body=old_body, new_template_body=new_body)
        == 1
    )


def test_an_empty_case_list_is_NOT_reported_as_identical_arms():
    """Zero cases is a low-power suite, a different statement — reporting the wrong one
    would send a user hunting a diff bug when their ledger is simply empty."""
    body = json.dumps(OLD_SPEC)
    assert study_arms.assert_arms_differ([], old_template_body=body, new_template_body=body) == 0


# ── the production ArmRunner ─────────────────────────────────────────────────


def test_the_arm_runner_writes_its_output_into_its_OWN_workspace(eval_home):
    """Per (case, trial, arm), so `locked/` checks read THIS arm's answer.

    A shared workspace would make every locked check report the same outcome for both arms,
    which reads as "the checks found nothing" and is really "one arm was measured twice".
    """

    async def fake(prompt, *, use_case):
        return f"answer for: {prompt}"

    runner = study_arms.TemplateArmRunner(completion=fake)
    payloads = [
        studies.WorkerPayload(
            study_id="st-x",
            case_id="c0",
            arm=arm,
            trial=0,
            template_body=json.dumps(OLD_SPEC),
            case_input=json.dumps({"target": f"target-{arm}"}),
        )
        for arm in (studies.ARM_OLD, studies.ARM_NEW)
    ]
    outs = [asyncio.run(runner(p)) for p in payloads]

    assert all(o.ok for o in outs)
    assert outs[0].workspace != outs[1].workspace
    for out in outs:
        landed = (
            store.study_dir("st-x")
            / "arms"
            / out.workspace.rsplit("/", 1)[-1]
            / study_arms.ARM_OUTPUT_FILENAME
        )
        assert landed.read_text(encoding="utf-8") == out.output


def test_a_failed_arm_is_ok_False_and_NOT_an_empty_answer(eval_home):
    """An exploded arm is a MISSING measurement, not a bad one.

    An empty output handed to the judge would be scored as a poor answer and would move the
    win rate — a study that silently converts infrastructure failure into evidence.
    """

    async def boom(prompt, *, use_case):
        raise RuntimeError("provider down")

    out = asyncio.run(
        study_arms.TemplateArmRunner(completion=boom)(
            studies.WorkerPayload(
                study_id="st-y",
                case_id="c0",
                arm=studies.ARM_OLD,
                trial=0,
                template_body=json.dumps(OLD_SPEC),
                case_input="{}",
            )
        )
    )

    assert out.ok is False
    assert out.output == ""
    assert "provider down" in out.detail


def test_an_unfinished_arm_is_NOT_judged_as_an_empty_answer(eval_home, monkeypatch):
    """A provider outage must not become evidence that the other template is better.

    `ArmOutput.ok` was an UNREAD field: nothing in `run_study` looked at it, and this module
    is the first thing that ever sets it False. Left unread, an arm that failed would hand
    the judge `""` and the other arm would win the pair on infrastructure failure. So a pair
    with an unfinished arm is unjudgeable AND the judge is never called — asserted on the
    judge's call count, because a pair marked unjudgeable that still paid for six judge calls
    is only half the fix.
    """
    judge_calls: list[str] = []

    async def judge(prompt, *, use_case):  # pragma: no cover - must never run
        judge_calls.append(prompt)
        raise AssertionError("the judge was asked about an arm that never ran")

    async def half_dead(payload):
        if payload.arm == studies.ARM_NEW:
            return studies.ArmOutput(output="", ok=False, detail="provider down")
        return studies.ArmOutput(output="a real answer", ok=True)

    reg = studies.register_study(
        subject={"template_id": WORKFLOW},
        hypothesis="h",
        inputs=("p",),
        rubric_text=study_arms.TEMPLATE_AB_RUBRIC,
        k=1,
    )
    result = asyncio.run(
        studies.run_study(
            reg,
            cases=[studies.StudyCase(case_id="c0", goal="g", case_input="{}")],
            old_template_body="OLD",
            new_template_body="NEW",
            arm_runner=half_dead,
            live_rubric_text=study_arms.TEMPLATE_AB_RUBRIC,
            caller=judge,
            samples=3,
        )
    )

    assert judge_calls == []
    assert result.no_signal == 1
    assert result.wins == 0 and result.losses == 0 and result.ties == 0
    # Unmeasurable agreement is None, never 0.0 — the study could not measure it.
    assert result.agreement is None
    pair = result.cases[0].pairs[0]
    assert pair.judgeable is False
    assert "provider down" in pair.direct_samples[0]


def test_a_pair_whose_arms_BOTH_finish_IS_judged(eval_home):
    """The floor for the test above: the unfinished-arm gate is not blocking everything.

    Without this, a gate that skipped the judge unconditionally would look like a working
    guard while the study measured nothing at all.
    """
    judge_calls: list[str] = []

    async def judge(prompt, *, use_case):
        """Votes on CONTENT, not on position.

        A judge that always names slot A would flip with the swap and produce `no_signal` —
        which is §2.3 working, and would make this floor unable to tell a judged pair from a
        skipped one.
        """
        from personalclaw.evals.judge_bench import JudgeCall

        judge_calls.append(prompt)
        winner = "A" if prompt.index("answer-new") < prompt.index("answer-old") else "B"
        return JudgeCall(text=json.dumps({"winner": winner, "cannot_judge": ""}))

    async def healthy(payload):
        return studies.ArmOutput(output=f"answer-{payload.arm}", ok=True)

    reg = studies.register_study(
        subject={"template_id": WORKFLOW},
        hypothesis="h",
        inputs=("p",),
        rubric_text=study_arms.TEMPLATE_AB_RUBRIC,
        k=1,
    )
    result = asyncio.run(
        studies.run_study(
            reg,
            cases=[studies.StudyCase(case_id="c0", goal="g", case_input="{}")],
            old_template_body="OLD",
            new_template_body="NEW",
            arm_runner=healthy,
            live_rubric_text=study_arms.TEMPLATE_AB_RUBRIC,
            caller=judge,
            samples=1,
        )
    )

    assert len(judge_calls) == 2  # both positions of the one pair
    assert result.no_signal == 0
    assert result.cases[0].pairs[0].judgeable is True


def test_the_arm_runner_screens_its_model_output_exactly_once(eval_home):
    """The one screen, at the point the model's text enters.

    Composed text is never re-screened: `redact_credentials` is not idempotent over a
    `key: value` line, so a trailing chokepoint would rewrite
    `api_key: [REDACTED: credential]` into garbage and lose the field name.
    """

    async def leaky(prompt, *, use_case):
        return "here it is: sk-ant-api03-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"

    out = asyncio.run(
        study_arms.TemplateArmRunner(completion=leaky)(
            studies.WorkerPayload(
                study_id="st-z",
                case_id="c0",
                arm=studies.ARM_OLD,
                trial=0,
                template_body=json.dumps(OLD_SPEC),
                case_input="{}",
            )
        )
    )

    assert "sk-ant-api03-AAAA" not in out.output
    assert "REDACTED" in out.output
    # Screened exactly once — a second pass is what garbles a composed key/value line.
    assert out.output.count("REDACTED") == 1


# ── clause: "a flywheel template-diff RUNS a pre-registered study" ───────────


def test_run_study_uses_the_PRODUCTION_arm_runner_by_default(eval_home, monkeypatch):
    """`arm_runner=None` reaches `study_arms.live_arm_runner`.

    THE regression this atom exists for: `arm_runner` was a required kwarg with no default
    while `caller` fell back to `live_judge_caller`, so nothing could execute a template arm
    without first inventing one. This asserts the default is WIRED, by observing the
    production runner get called when the caller passes none.
    """
    seen: list[str] = []

    async def recording_runner(payload):
        seen.append(f"{payload.case_id}/{payload.arm}")
        return studies.ArmOutput(output=f"out-{payload.arm}", ok=True)

    monkeypatch.setattr(study_arms, "live_arm_runner", recording_runner)

    reg = studies.register_study(
        subject={"template_id": WORKFLOW},
        hypothesis="bullets beat paragraphs",
        inputs=("harvested_x@abc",),
        rubric_text=study_arms.TEMPLATE_AB_RUBRIC,
        k=1,
    )

    async def judge(prompt, *, use_case):
        from personalclaw.evals.judge_bench import JudgeCall

        return JudgeCall(text=json.dumps({"winner": "A", "cannot_judge": ""}))

    asyncio.run(
        studies.run_study(
            reg,
            cases=[studies.StudyCase(case_id="c0", goal="g", case_input="{}")],
            old_template_body="OLD",
            new_template_body="NEW",
            live_rubric_text=study_arms.TEMPLATE_AB_RUBRIC,
            caller=judge,
            samples=1,
        )
    )

    assert seen == ["c0/old", "c0/new"]


def test_filing_a_template_diff_PRE_REGISTERS_a_study(eval_home, monkeypatch):
    """🔴 THE call site. `file_template_diff` returns a study id and the study is on disk.

    Asserted on the FILING path's own return value, not on `register_study` being callable:
    the defect being closed was a module with zero production importers, and only a call
    site can falsify that.
    """
    _install_harvested(3)
    from personalclaw.learning import refiner_tools

    out = refiner_tools.file_template_diff(
        WORKFLOW,
        ops=DIFF_OPS,
        rationale="one-paragraph summaries get skimmed; bullets get read",
        run_ids=["run0", "run1", "run2"],
    )

    assert out["filed"] is True
    assert out["study_id"]
    raw = store.read_study_registration(out["study_id"])
    assert raw is not None
    reg = studies.registration_from_dict(raw)
    # The registration names the template, the proposal that motivated it, and the corpus
    # it will be measured over — so a run cannot substitute a different subject later.
    assert reg.subject["template_id"] == WORKFLOW
    assert reg.subject["diff_proposal_id"] == out["proposal_id"]
    assert reg.subject["corpus"] == "harvested"
    assert reg.subject["corpus_population"] == 3
    assert len(reg.inputs) == 3
    assert reg.rubric_sha256 == studies.rubric_sha256(study_arms.TEMPLATE_AB_RUBRIC)


def test_a_failed_pre_registration_does_NOT_lose_the_filed_proposal(eval_home, monkeypatch):
    """The vacuity floor for the hook: its best-effort guard really swallows.

    Without this, the `try/except` above is decorative — nothing would show that a filed
    proposal survives a broken eval store, and nothing would show `study_id` is an honest
    `""` rather than a fabricated id.
    """

    def boom(**kwargs):
        raise RuntimeError("eval store unwritable")

    monkeypatch.setattr(study_arms, "register_template_study", boom)
    from personalclaw.learning import refiner_tools

    out = refiner_tools.file_template_diff(
        WORKFLOW, ops=DIFF_OPS, rationale="still worth filing", run_ids=["r0", "r1", "r2"]
    )

    assert out["filed"] is True
    assert out["proposal_id"]
    assert out["study_id"] == ""


def test_a_rejected_diff_registers_NOTHING(eval_home):
    """A diff the frozen-region gate refused must not leave a study behind.

    A registration is immutable, so a study minted for a diff that was never filed is an
    artifact nobody can delete and nobody can run.
    """
    from personalclaw.learning import refiner_tools

    out = refiner_tools.file_template_diff(
        WORKFLOW,
        ops=[{"op": "update_node", "node_id": "answer", "fields": {"id": "renamed"}}],
        rationale="rename the node",
        run_ids=["r0", "r1", "r2"],
    )

    assert out["filed"] is False
    assert out.get("study_id", "") == ""
    assert store.list_study_ids() == []


# ── the invocation surface (the `_judge_bench` shape) ────────────────────────


def test_the_cli_command_is_wired_to_the_dispatch():
    """A module with no production importer is not done. This is the importer.

    The same rail `test_evals_harvest` puts on `eval-harvest`: asserting the parser AND the
    dispatch arm, because a command that parses and dispatches nowhere is a help page.
    """
    import pathlib

    from personalclaw import cli, cli_commands

    assert cli._study is cli_commands._study
    source = pathlib.Path(cli.__file__).read_text(encoding="utf-8")
    assert 'sub.add_parser(\n        "study"' in source
    assert 'args.command == "study"' in source


def test_the_preflight_counts_BOTH_positions_of_every_pair(eval_home):
    """`cases x k x 2` arm calls and twice that many judge calls per sample.

    Counting one position would understate the judge spend by half — and the whole point of
    printing a preflight is that the number is the one the user will pay.
    """
    reg = studies.register_study(
        subject={"template_id": WORKFLOW},
        hypothesis="h",
        inputs=("p",),
        rubric_text=study_arms.TEMPLATE_AB_RUBRIC,
        k=5,
    )
    cases = [
        studies.StudyCase(case_id=f"c{i}", goal="g", case_input=json.dumps({"target": f"t{i}"}))
        for i in range(3)
    ]
    old_body, new_body = study_arms.arm_bodies_for_ops(OLD_SPEC, DIFF_OPS)

    pre = study_arms.preflight(
        reg, cases=cases, old_template_body=old_body, new_template_body=new_body, samples=3
    )

    assert pre.arm_calls == 3 * 5 * 2
    assert pre.judge_calls == 3 * 5 * 2 * 3
    assert pre.differing_cases == 3
    assert "30 arm + 90 judge" in pre.render()


def test_the_cli_dry_run_prints_the_spend_and_CALLS_NOTHING(eval_home, monkeypatch, capsys):
    """The `--dry-run` contract, asserted on the money seam rather than on the text.

    A dry run that printed the right number and still spent would pass a text-only check.
    So the model call itself is replaced with a detonator.
    """
    _install_harvested(3)
    from personalclaw.cli_commands import _study
    from personalclaw.workflows.native_defs import NativeWorkflowDefProvider

    asyncio.run(NativeWorkflowDefProvider().save_def(**OLD_SPEC))
    from personalclaw.learning import refiner_tools

    filed = refiner_tools.file_template_diff(
        WORKFLOW, ops=DIFF_OPS, rationale="bullets beat paragraphs", run_ids=["r0", "r1", "r2"]
    )

    async def detonate(prompt, *, use_case):  # pragma: no cover - must never run
        raise AssertionError("--dry-run spent money")

    monkeypatch.setattr(study_arms, "_one_shot_completion", detonate)
    # `studies` import-binds `live_judge_caller`, so patching `judge_bench.live_judge_caller`
    # would replace a name nothing reads. Patch the binding the caller actually resolves.
    monkeypatch.setattr(studies, "live_judge_caller", detonate)

    asyncio.run(
        _study(
            argparse.Namespace(list=False, view="", run=filed["study_id"], dry_run=True, samples=0)
        )
    )

    out = capsys.readouterr().out
    assert "30 arm + 90 judge" in out
    assert "--dry-run: nothing was called." in out
    assert store.read_study_verdict(filed["study_id"]) is None


def test_the_cli_refuses_a_study_whose_corpus_is_EMPTY(eval_home, capsys):
    """An empty population exits 1 with the refusal sentence, not with a verdict."""
    from personalclaw.cli_commands import _study
    from personalclaw.workflows.native_defs import NativeWorkflowDefProvider

    asyncio.run(NativeWorkflowDefProvider().save_def(**OLD_SPEC))
    _install_harvested(3)
    from personalclaw.learning import refiner_tools

    filed = refiner_tools.file_template_diff(
        WORKFLOW, ops=DIFF_OPS, rationale="bullets", run_ids=["r0", "r1", "r2"]
    )
    for path in scenarios.installed_dir().glob("harvested_*.json"):
        path.unlink()

    with pytest.raises(SystemExit) as exc:
        asyncio.run(
            _study(
                argparse.Namespace(
                    list=False, view="", run=filed["study_id"], dry_run=True, samples=0
                )
            )
        )

    assert exc.value.code == 1
    assert "no replay population" in capsys.readouterr().out


def test_the_cli_can_run_a_registered_study_end_to_end(eval_home, monkeypatch, capsys):
    """The whole path: flywheel files → CLI runs → verdict persists.

    Both money seams are injected (there is no model in a unit run), but every other step is
    the production one: the registration read back off disk, the arms derived from the filed
    proposal's own ops, the harvested corpus, the per-arm workspaces, the persisted verdict.
    """
    _install_harvested(3)
    from personalclaw.cli_commands import _study
    from personalclaw.evals.judge_bench import JudgeCall
    from personalclaw.learning import refiner_tools
    from personalclaw.workflows.native_defs import NativeWorkflowDefProvider

    asyncio.run(NativeWorkflowDefProvider().save_def(**OLD_SPEC))
    filed = refiner_tools.file_template_diff(
        WORKFLOW, ops=DIFF_OPS, rationale="bullets beat paragraphs", run_ids=["r0", "r1", "r2"]
    )

    async def arm(prompt, *, use_case):
        return "- a\n- b\n- c" if "bullets" in prompt else "One long paragraph."

    async def judge(prompt, *, use_case):
        better = "B" if prompt.index("- a") > prompt.index("One long") else "A"
        return JudgeCall(text=json.dumps({"winner": better, "cannot_judge": ""}))

    monkeypatch.setattr(study_arms, "_one_shot_completion", arm)
    monkeypatch.setattr(studies, "live_judge_caller", judge)

    asyncio.run(
        _study(
            argparse.Namespace(list=False, view="", run=filed["study_id"], dry_run=False, samples=1)
        )
    )

    out = capsys.readouterr().out
    assert "Verdict:" in out
    verdict = store.read_study_verdict(filed["study_id"])
    assert verdict is not None
    assert verdict["verdict"] in studies.VERDICTS
    # The per-arm workspaces are real and distinct — 3 cases x k x 2 arms.
    arms_dir = store.study_dir(filed["study_id"]) / "arms"
    assert len(list(arms_dir.iterdir())) == 3 * verdict["k"] * 2

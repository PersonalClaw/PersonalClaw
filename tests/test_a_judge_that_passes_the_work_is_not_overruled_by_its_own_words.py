"""A judge's PASS stands unless the judge itself names a forbidden pass the work made.

Measured on a first-run loop (`general-project`, a one-cycle budget, as the onboarding card starts
it): the judge returned PASS with full scores, evidence references and a proof, and the run ended
"Stopped at its budget … its pass was set aside: forbidden success mode admitted: work claimed
without evidence". Nothing in the work was forbidden. The contract matched four-letter stems of each
forbidden mode's words as substrings anywhere in the judge's reasoning and fired on any two: "work"
in a workspace path, "clai" in "its factual claims", "with" in "with a PR number"; and "exit
condition reinterpreted to fit" fired on the judge's own sentence saying the work did NOT
reinterpret the exit condition. The template tells its judge to check those passes did not happen,
so a judge that reported doing the check was overruled by its own report.

Now whether a forbidden pass happened is what the judge STATES, in `forbidden_modes_found`, read
like the rest of the contract: an empty list is none, and a PASS that names one is set aside with
that mode as the reason. The judge's prose is never matched. The loop cases drive the REAL bundled
specs through a REAL `RunController`; only the model calls are scripted.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from personalclaw.workflows import journal as J
from personalclaw.workflows import loop_view, store
from personalclaw.workflows.bundled_defs import read_template
from personalclaw.workflows.controller import EngineServices, RunController
from personalclaw.workflows.judge_contract import (
    JudgeHints,
    Verdict,
    aggregate_samples,
    hints_from_dict,
    judge_instruction,
    validate_verdict,
)
from personalclaw.workflows.models import RunStatus, WorkflowRun

RUN_TIMEOUT = 20.0

#: A general-project judge's account of the onboarding note, in the shape a real one had: a
#: workspace path, "its factual claims", "with a PR number", and the check its prompt asks for,
#: reported as done. Every word of it is ordinary; none of it names a forbidden pass.
ORDINARY_GENERAL = (
    "Read ~/.personalclaw/workspace/agent-ideas-this-week.md: five ideas for the week, one "
    "sentence each. Checked its factual claims against the task; none needs a source, and the one "
    "about a pull request does not invent one with a PR number. It does not reinterpret the exit "
    "condition: the task asked for a short note in the workspace, and the note is there. No work "
    "was claimed without evidence."
)

#: A goal-pursuit judge's account, ordinary in the same way for that template's two modes.
ORDINARY_GOAL = (
    "Read findings.md in the scope: the output names the link stage as the cause and cites "
    "build.log lines 40-52. Each criterion is met as written, and the deliverable is not a "
    "placeholder: it holds the measured timings."
)

#: Reasoning that says nothing a word matcher could catch: an admission here can only be the field.
PLAIN = "Read the note against the task."


def _hints(template: str) -> JudgeHints:
    spec = read_template(template)
    assert spec is not None, template
    return hints_from_dict(spec.to_dict()["runtime_hints"]["judge"])


def _verdict(reasoning: str, scores: dict[str, int], **extra: Any) -> dict[str, Any]:
    return {
        "reasoning": reasoning,
        "verdict": "PASS",
        "scores": scores,
        "evidence_refs": ["agent-ideas-this-week.md"],
        "proof": "cat agent-ideas-this-week.md printed the five ideas",
        "cannot_judge": "",
        **extra,
    }


GENERAL_SCORES = {"the step accomplished something real": 2, "evidence is checkable": 2}
GOAL_SCORES = {"progress is real and evidenced": 2, "claims cite artifacts, not prose": 2}


# ── 1. the contract: a pass stands on what the judge says, not on the words it uses ──────────


@pytest.mark.parametrize(
    ("template", "reasoning", "scores"),
    [
        ("general-project", ORDINARY_GENERAL, GENERAL_SCORES),
        ("goal-pursuit-open-ended", ORDINARY_GOAL, GOAL_SCORES),
    ],
)
@pytest.mark.parametrize("report", [{"forbidden_modes_found": []}, {}], ids=["none", "omitted"])
def test_a_pass_in_ordinary_words_stands(
    template: str, reasoning: str, scores: dict[str, int], report: dict[str, Any]
) -> None:
    """🔴 Before: set aside as "forbidden success mode admitted" for words like "workspace",
    "claims", "with", "placeholder", and for the judge saying a forbidden pass did NOT happen. An
    omitted report is not an admission either: the judge was told not to pass forbidden work, and a
    pass is its word that it found none."""
    verdict = validate_verdict(_verdict(reasoning, scores, **report), _hints(template))
    assert verdict.passed, verdict.invalid_reason
    assert verdict.valid and not verdict.invalid_reason
    assert verdict.forbidden_modes_found == []


@pytest.mark.parametrize(
    ("template", "named", "scores"),
    [
        ("general-project", "work claimed without evidence", GENERAL_SCORES),
        ("general-project", "exit condition reinterpreted to fit", GENERAL_SCORES),
        ("goal-pursuit-open-ended", "deliverable stubbed or placeholder", GOAL_SCORES),
    ],
)
def test_a_pass_that_names_a_forbidden_pass_is_set_aside_naming_it(
    template: str, named: str, scores: dict[str, int]
) -> None:
    """🔴 Before: passed, because the reasoning carried no matched stems and the field was never
    read. The judge said in so many words that the work did a forbidden pass."""
    verdict = validate_verdict(
        _verdict(PLAIN, scores, forbidden_modes_found=[named]), _hints(template)
    )
    assert not verdict.passed
    assert not verdict.valid
    assert not verdict.protocol_error, "naming a forbidden pass is about the work, not the shape"
    assert verdict.invalid_reason == f"PASS names a forbidden success mode: {named}"
    assert verdict.forbidden_modes_found == [named]
    record = verdict.to_dict()
    assert record["forbidden_modes_found"] == [named]
    assert (record["passed"], record["valid"]) == (False, False)


@pytest.mark.parametrize(
    ("said", "named"),
    [
        # Restated in the judge's own case and punctuation: named in the template's wording.
        (["Work claimed without evidence."], "work claimed without evidence"),
        (["work claimed"], "work claimed without evidence"),
        # A lone string is a one-item list.
        ("exit condition reinterpreted to fit", "exit condition reinterpreted to fit"),
        # Not one of the declared modes: still the judge's word that a forbidden pass happened,
        # named in its own words.
        (["the worker rewrote the task"], "the worker rewrote the task"),
    ],
)
def test_what_the_judge_names_is_read_as_it_wrote_it(said: Any, named: str) -> None:
    verdict = validate_verdict(
        _verdict(PLAIN, GENERAL_SCORES, forbidden_modes_found=said), _hints("general-project")
    )
    assert not verdict.passed
    assert verdict.forbidden_modes_found == [named]
    assert verdict.invalid_reason == f"PASS names a forbidden success mode: {named}"


def test_two_named_passes_are_both_in_the_reason() -> None:
    both = ["work claimed without evidence", "exit condition reinterpreted to fit"]
    verdict = validate_verdict(
        _verdict(PLAIN, GENERAL_SCORES, forbidden_modes_found=both), _hints("general-project")
    )
    assert verdict.invalid_reason == "PASS names forbidden success modes: " + "; ".join(both)


@pytest.mark.parametrize("unreadable", [True, 1, {"work claimed without evidence": False}])
def test_a_report_the_engine_cannot_read_is_not_a_pass(unreadable: Any) -> None:
    """Neither a list nor text: the engine cannot tell whether a forbidden pass happened, so the
    pass is not taken, and the reason says the judge answered in the wrong shape."""
    verdict = validate_verdict(
        _verdict(PLAIN, GENERAL_SCORES, forbidden_modes_found=unreadable),
        _hints("general-project"),
    )
    assert not verdict.passed
    assert verdict.protocol_error
    assert verdict.invalid_reason == (
        "PASS gives its forbidden success modes as neither a list nor text"
    )


def test_one_sample_that_names_a_forbidden_pass_outweighs_a_passing_majority() -> None:
    """A disqualifier is a fact: one sample that names it beats two that did not — a REJECT that
    names it included, which the reasoning matcher could never see (it read only a PASS)."""
    hints = _hints("general-project")
    clean = _verdict(ORDINARY_GENERAL, GENERAL_SCORES, forbidden_modes_found=[])
    named = {
        **_verdict(PLAIN, GENERAL_SCORES),
        "verdict": "REJECT",
        "forbidden_modes_found": ["work claimed without evidence"],
    }
    samples = [validate_verdict(clean, hints), validate_verdict(clean, hints)]
    samples.append(validate_verdict(named, hints))
    decided = aggregate_samples(samples, hints)
    assert not decided.passed
    assert decided.verdict is Verdict.REJECT
    assert decided.forbidden_modes_found == ["work claimed without evidence"]


def test_the_judge_is_told_how_to_name_a_forbidden_pass() -> None:
    """The instruction is generated from the hints the validation reads, so the field the engine
    sets a pass aside on is named to the judge, with what an empty list means."""
    text = judge_instruction("judge it", JudgeHints(forbidden_success_modes=["test deleted"]))
    assert '"forbidden_modes_found"' in text
    assert "leave it [] when it did none" in text
    assert "set aside" in text
    bare = judge_instruction("judge it", JudgeHints(forbidden_success_modes=[]))
    assert "forbidden_modes_found" not in bare


# ── 2. the loop: a passed loop ends passed ───────────────────────────────────────────────────


class _Info:
    def __init__(self, agent_id: str, result: str) -> None:
        self.id = agent_id
        self.done = True
        self.error = ""
        self.result = result
        self.reaped = False
        self.agent = ""


class _Answers:
    """Each spawned step finishes at once with its node's answer (keyed by node id)."""

    def __init__(self, answers: dict[str, Any]) -> None:
        self.answers = answers
        self.infos: dict[str, _Info] = {}

    def spawn(self, **kw: Any) -> _Info:
        node_id = str(kw["parent_session_key"]).rsplit(":", 1)[-1]
        info = _Info(f"sub{len(self.infos) + 1}", json.dumps(self.answers[node_id]))
        self.infos[info.id] = info
        return info

    def get(self, agent_id: str) -> _Info | None:
        return self.infos.get(agent_id)


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: home)
    monkeypatch.setattr("personalclaw.workflows.leases.config_dir", lambda: home)
    return home


def _drive(
    template: str,
    inputs: dict[str, Any],
    answers: dict[str, Any],
    *,
    gate: list[dict[str, Any]] | None = None,
    tmp_path: Path,
    **run: Any,
) -> RunController:
    """The bundled template with a one-cycle budget, every model step answered from *answers* and
    a judge gate's samples, in order, from *gate* (then a plain pass)."""
    samples = list(gate or [])

    async def complete(_prompt: str, **_kw: Any) -> str:
        return json.dumps(samples.pop(0) if samples else GATE_PLAIN)

    spec = read_template(template)
    assert spec is not None, template
    created = store.create(
        WorkflowRun(
            id="",
            workflow_name=template,
            inputs=inputs,
            policy_overrides={"max_cycles": 1},
            **run,
        )
    )
    store.write_spec(created.id, spec.to_dict())
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    controller = RunController(
        created,
        spec.to_dict(),
        services=EngineServices(
            subagents=_Answers(answers), completion=complete, cwd=str(workspace)
        ),
    )
    asyncio.run(controller.run_to_completion(timeout=RUN_TIMEOUT))
    return controller


def _general(judge: dict[str, Any], tmp_path: Path) -> RunController:
    return _drive(
        "general-project",
        {
            "task": "Draft a short note describing what I could use an agent for this week.",
            "exit_condition": "The task is complete and the evidence shows it",
        },
        {
            "work": {
                "summary": "wrote agent-ideas-this-week.md in the workspace",
                "meaningful_progress": True,
                "evidence": "agent-ideas-this-week.md, 5 lines",
            },
            "judge": judge,
        },
        tmp_path=tmp_path,
        loop_kind="general",
        title="First loop",
    )


def test_a_one_cycle_loop_whose_judge_passed_in_ordinary_words_ends_complete(
    tmp_path: Path,
) -> None:
    """🔴 Before: escalated, "Stopped at its budget", "The judge passed the last one, but its pass
    was set aside: forbidden success mode admitted: work claimed without evidence"."""
    judge = _verdict(ORDINARY_GENERAL, GENERAL_SCORES, forbidden_modes_found=[])
    run = _general(judge, tmp_path).run

    assert run.status is RunStatus.COMPLETE, (run.status, run.attention, run.error_message)
    assert not run.attention, run.attention
    assert not run.error_message, run.error_message
    outcomes = [str(e.get("outcome") or "") for e in J.journal_records(run.id, kinds={"iteration"})]
    assert outcomes == ["judge_done"], outcomes
    row = loop_view.get_loop_view(run.id)
    assert row is not None
    assert (row["status"], row["stop_reason"]) == ("complete", "done"), row


def test_a_one_cycle_loop_whose_judge_named_a_forbidden_pass_stops_at_its_budget_saying_so(
    tmp_path: Path,
) -> None:
    """🔴 Before: complete, because what the judge said in `forbidden_modes_found` was never
    read and its reasoning carried no matched stems."""
    judge = _verdict(PLAIN, GENERAL_SCORES, forbidden_modes_found=["work claimed without evidence"])
    run = _general(judge, tmp_path).run

    assert run.status is RunStatus.ESCALATED, run.status
    attention = run.attention or {}
    assert attention.get("cause") == "budget", attention
    assert attention.get("detail") == (
        "It used its budget of 1 cycle. The judge passed the last one, but its pass was set aside: "
        "PASS names a forbidden success mode: work claimed without evidence."
    ), attention


# ── 3. a second template: a loop with a judge stage, then a judge gate ───────────────────────

GOAL_INPUTS = {
    "task": "find why the build got slower",
    "success_criteria": "a named cause",
    "scope": ".",
}


def _goal_answers(judge: dict[str, Any]) -> dict[str, Any]:
    return {
        "intake": {
            "sub_goals": ["time the build"],
            "execution_plan": [{"phase": "measure", "objective": "time each stage"}],
            "first_step": "time the build",
        },
        "cycle": {
            "summary": "the link stage doubled after the toolchain update",
            "key_insight": "the linker is the slow part",
            "new_findings_count": 1,
            "evidence": "build.log lines 40-52",
        },
        "judge": {**judge, "marginal_value": 1.0},
        "deliverable": {
            "report": "the toolchain update doubled the link stage",
            "key_findings": ["the linker is the slow part"],
            "recommendations": ["pin the previous linker"],
            "could_not_establish": [],
        },
    }


#: The terminal `accept` gate's sample, ordinary in the same way as the stage judge's.
GATE_ORDINARY = {
    "reasoning": "The deliverable states a named cause and cites what it measured; nothing in it "
    "is a placeholder.",
    "verdict": "PASS",
    "scores": GOAL_SCORES,
    "proof": "read the deliverable against the goal",
    "evidence_refs": [],
    "forbidden_modes_found": [],
    "cannot_judge": "",
}
GATE_PLAIN = {**GATE_ORDINARY, "reasoning": PLAIN}


def test_a_goal_loop_whose_judges_passed_in_ordinary_words_completes(tmp_path: Path) -> None:
    """🔴 Before: its cycle's judge was set aside ("deliverable stubbed or placeholder", on "the
    deliverable is not a placeholder"), so the loop stopped at its budget and nothing after it
    ran; and every sample of its terminal gate was refused the same way."""
    judge = _verdict(ORDINARY_GOAL, GOAL_SCORES, forbidden_modes_found=[])
    controller = _drive(
        "goal-pursuit-open-ended",
        GOAL_INPUTS,
        _goal_answers(judge),
        gate=[dict(GATE_ORDINARY) for _ in range(3)],
        tmp_path=tmp_path,
    )
    run = controller.run
    assert run.status is RunStatus.COMPLETE, (run.status, run.attention, run.error_message)


def test_a_goal_loop_whose_cycle_judge_named_a_forbidden_pass_stops_at_its_budget(
    tmp_path: Path,
) -> None:
    judge = _verdict(
        PLAIN, GOAL_SCORES, forbidden_modes_found=["deliverable stubbed or placeholder"]
    )
    controller = _drive(
        "goal-pursuit-open-ended", GOAL_INPUTS, _goal_answers(judge), gate=[], tmp_path=tmp_path
    )
    run = controller.run
    assert run.status is RunStatus.ESCALATED, (run.status, run.error_message)
    attention = run.attention or {}
    assert attention.get("cause") == "budget", attention
    assert str(attention.get("detail") or "").endswith(
        "The judge passed the last one, but its pass was set aside: PASS names a forbidden success "
        "mode: deliverable stubbed or placeholder."
    ), attention


def test_a_terminal_gate_sample_that_names_a_forbidden_pass_refuses_the_pass(
    tmp_path: Path,
) -> None:
    """🔴 Before: complete — three PASS samples, and the one that named a forbidden pass in its
    `forbidden_modes_found` was read as a pass like the other two."""
    judge = _verdict(ORDINARY_GOAL, GOAL_SCORES, forbidden_modes_found=[])
    named = {
        **GATE_ORDINARY,
        "reasoning": PLAIN,
        "forbidden_modes_found": ["deliverable stubbed or placeholder"],
    }
    controller = _drive(
        "goal-pursuit-open-ended",
        GOAL_INPUTS,
        _goal_answers(judge),
        gate=[dict(GATE_ORDINARY), named, dict(GATE_ORDINARY)],
        tmp_path=tmp_path,
    )
    run = controller.run
    assert run.status is not RunStatus.COMPLETE, run.status
    accept = next(
        inst for path, inst in controller.instances.items() if path.endswith("children[3]")
    )
    assert accept.failure is not None, accept
    assert accept.failure.cause_plain == (
        "judge PASS refused by the contract: PASS names a forbidden success mode: deliverable "
        "stubbed or placeholder"
    ), accept.failure

"""A workflow's judge reads the checks the engine ran for it, and is never told to run one.

🔴 The defect (Backlog: "Workflow judges are told to re-run the checks themselves, but cannot run a
command"). Five bundled workflows end their work on a judge step told to verify by running commands
("Re-run the verify command yourself", "Re-run any command the evidence cites"), and each declares
`tools_posture: verify`, which makes it a research step whose tools only read: a shell call is
refused. A judge gate given its run's declared checks (`proof_command`,
`hidden_validation_commands`) was told "Run these checks yourself", and a judge gate is one model
call with no tools at all. So no judge could do what it was told, and the answer check counted a
citation naming a declared command as grounded, so a judge that never ran it could cite output it
made up.

Now the engine runs a judge's checks just before it, through `loop.gates.run_verify_command`, and
the judge reads what each printed (`judge_checks.measured`): the ones its step declares
(`config.checks`) and its run's, shown only to judges. The prompts and the contract's instruction
say so, and only output the engine recorded grounds a citation.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from personalclaw.workflows import command_inputs, store
from personalclaw.workflows.bundled_defs import read_template, template_names
from personalclaw.workflows.controller import EngineServices, RunController
from personalclaw.workflows.engine import stage_capability
from personalclaw.workflows.judge_checks import declared
from personalclaw.workflows.judge_contract import JudgeHints
from personalclaw.workflows.models import Node, NodeKind, RunStatus, WorkflowRun, walk

#: An instruction to run a command: "re-run", "rerun", "execute", "run … yourself" or "run these
#: checks". A sentence that says the step cannot run one ("your tools … have no shell") is not one.
_TOLD_TO_RUN = re.compile(
    r"(?i)\b(re-?run|execute)\b|\brun\b[^.;\n]*\byourself\b|\brun these checks\b"
)


def _reads_only(node: Node) -> bool:
    cfg = node.config or {}
    if node.kind == NodeKind.STAGE:
        return stage_capability(cfg) == "research"
    return node.kind == NodeKind.GATE and str(cfg.get("kind", "")) == "judge"


def test_no_bundled_step_that_cannot_run_a_command_is_told_to_run_one():
    """🔴 Before: code-project, general-project, goal-pursuit-open-ended (its judge and its
    intake's note), goal-pursuit-verifiable and diagnose-run."""
    told: list[str] = []
    walked = 0
    for name in sorted(template_names()):
        template = read_template(name)
        if template is None:
            continue
        for _path, node in walk(template.root):
            if not _reads_only(node):
                continue
            walked += 1
            for found in _TOLD_TO_RUN.finditer(str((node.config or {}).get("prompt", ""))):
                told.append(f"{name}/{node.id}: {found.group(0)!r}")
    assert walked >= 5, "vacuity floor: the bundled read-only steps were walked"
    assert told == [], told


def test_the_rail_reads_an_instruction_to_run_and_not_a_step_that_says_it_cannot():
    for said in (
        "Re-run any command the evidence cites.",
        "Run the verify command yourself.",
        "Run these checks and report what they returned.",
        "rerun the tests",
    ):
        assert _TOLD_TO_RUN.search(said), said
    assert not _TOLD_TO_RUN.search("Your tools read files and have no shell.")
    assert not _TOLD_TO_RUN.search("If the artifacts are missing or unreadable, say so.")


def test_hidden_checks_reach_only_a_judge():
    hints = JudgeHints(proof_command="make proof", hidden_validation_commands=["make hidden"])
    judge = Node.from_dict(
        {
            "kind": "stage",
            "id": "judge",
            "config": {"prompt": "p", "judge_contract": True, "checks": ["make own"]},
        }
    )
    worker = Node.from_dict(
        {"kind": "stage", "id": "work", "config": {"prompt": "p", "checks": ["make own"]}}
    )
    assert [c.command for c in declared(judge, hints)] == ["make own", "make proof", "make hidden"]
    assert declared(worker, hints) == []


# ── driven runs ─────────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    """A scratch home and a scratch project folder the checks run in."""
    home = tmp_path / "home"
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    monkeypatch.setattr("personalclaw.workflows.leases.config_dir", lambda: home)
    folder = tmp_path / "project"
    folder.mkdir()
    monkeypatch.setattr("personalclaw.config.loader.default_workspace_dir", lambda: str(folder))
    return folder


class _Checks:
    """`loop.gates.run_verify_command` as the engine calls it: each call recorded, the first
    failing and every later one passing, each saying which run of it this is."""

    def __init__(self, events: list[tuple[str, str]]) -> None:
        self.calls: list[tuple[str, str]] = []
        self.events = events

    async def __call__(self, cmd, cwd, *, label="verify", report=None, per_test=False):
        self.calls.append((cmd, str(cwd)))
        n = len(self.calls)
        self.events.append(("check", f"run {n}"))
        if report is not None:
            report.exit_code = 1 if n == 1 else 0
            # Printed braces stay as printed: a check's output is a value, never a template.
            report.output = f"check run {n}: " + ("3 failed" if n == 1 else "all 12 passed")
            report.output += " {{ printed by the tool }}"
        return n > 1


@pytest.fixture
def checks(monkeypatch) -> _Checks:
    ran = _Checks([])
    monkeypatch.setattr("personalclaw.loop.gates.run_verify_command", ran)
    return ran


class _Info:
    def __init__(self, agent_id: str, task: str) -> None:
        self.id = agent_id
        self.prompt = task
        self.done = False
        self.error = ""
        self.result = ""
        self.reaped = False
        self.agent = ""


class _Helpers:
    """The run's step helpers, each answering as its prompt asks."""

    def __init__(self, events: list[tuple[str, str]]) -> None:
        self.infos: dict[str, _Info] = {}
        self.events = events
        self.judged = 0
        self.fixed = 0

    def spawn(self, **kw: Any) -> _Info:
        info = _Info(f"sub{len(self.infos) + 1}", str(kw.get("task") or ""))
        self.infos[info.id] = info
        return info

    def get(self, agent_id: str) -> _Info | None:
        info = self.infos.get(agent_id)
        if info is None or info.done:
            return info
        info.done = True
        task = info.prompt
        if task.startswith("Plan the change"):
            payload: dict[str, Any] = {"diagnosis": "d", "files_to_change": [], "approach": "a"}
        elif task.startswith("Make the smallest change"):
            self.events.append(("fix", task))
            self.fixed += 1
            # The second attempt says its command passed, which ends the loop (its `until`).
            passed = self.fixed >= 2
            payload = {"summary": "edited", "command_passed": passed, "evidence": "edited a.py"}
        else:
            self.events.append(("judge", task))
            self.judged += 1
            payload = {
                "reasoning": "read it",
                "verdict": "RETRY" if self.judged == 1 else "PASS",
                "scores": {"the verify command passes": 2, "the change is minimal and scoped": 2},
                "evidence_refs": ["all 12 passed"],
                "proof": "the engine's check",
                "forbidden_modes_found": [],
                "cannot_judge": "",
            }
        info.result = json.dumps(payload)
        return info


def _result(output: dict[str, Any]) -> MagicMock:
    return MagicMock(
        success=True,
        stdout=json.dumps(output),
        outcome="",
        error="",
        exit_code=0,
        stderr="",
        agent_error=None,
    )


class _Bash:
    async def execute(self, cfg, ctx, timeout=30):
        return _result({"exit_code": 1, "stdout": "baseline: 3 failed"})


async def _passes(block: dict[str, Any]) -> bool:
    return True


def _drive(
    spec: dict[str, Any], inputs: dict[str, Any], folder: Path, **services: Any
) -> tuple[WorkflowRun, dict[str, Any]]:
    # The owner's start, which records the values she gave (`command_inputs.stamped`): a check
    # runs an input only as its owner chose it.
    run = store.create(
        WorkflowRun(
            id="",
            workflow_name=str(spec.get("name", "w")),
            inputs=inputs,
            extra=command_inputs.stamped({}, inputs, ""),
        )
    )
    store.write_spec(run.id, spec)
    controller = RunController(
        run,
        spec,
        services=EngineServices(
            get_provider=lambda name: {"bash": _Bash()}[name], cwd=str(folder), **services
        ),
    )
    asyncio.run(controller.run_to_completion(timeout=30.0))
    return store.get(run.id), store.read_state(run.id)


def test_the_verifiable_loops_judge_reads_the_check_run_after_that_passs_fix(home, checks):
    """🔴 Before: the judge inside the loop was told "Run the verify command yourself", with no
    tool that could, and the only run of the command was the gate after the loop."""
    template = read_template("goal-pursuit-verifiable")
    assert template is not None
    helpers = _Helpers(checks.events)
    scope = home / "repo"
    scope.mkdir()
    _drive(
        template.to_dict(),
        {"task": "make the tests pass", "verify_command": "make check", "scope": str(scope)},
        home,
        subagents=helpers,
        verify=_passes,
    )
    kinds = [kind for kind, _ in checks.events]
    assert kinds[:6] == ["fix", "check", "judge", "fix", "check", "judge"], kinds
    first, second = (said for kind, said in checks.events if kind == "judge")
    assert "check run 1: 3 failed" in first and "failed (exit 1)" in first, first[-900:]
    assert "check run 2: all 12 passed" in second and "passed (exit 0)" in second, second[-900:]
    assert "{{ printed by the tool }}" in second, second[-900:]
    assert checks.calls[0] == ("make check", str(scope)), "in the folder the check names"
    for said in (first, second):
        # Told to read its artifacts itself, which its tools can; never to run a command.
        told = said.split("Rubric")[-1].split("Return JSON")[0]
        assert not re.search(r"\b(run|re-run)\b[^.]*\byourself\b", told, re.I), said[-1500:]


def _gate_spec(**judge: Any) -> dict[str, Any]:
    return {
        "name": "judged",
        "version": 1,
        "root": {
            "kind": "sequence",
            "id": "root",
            "children": [
                {"kind": "stage", "id": "work", "config": {"prompt": "Make the tests pass."}},
                {
                    "kind": "gate",
                    "id": "accept",
                    "config": {"kind": "judge", "prompt": "Is the work done?"},
                },
            ],
        },
        "runtime_hints": {"judge": {"rubric": [], "forbidden_success_modes": [], **judge}},
    }


class _Worker(_Helpers):
    def get(self, agent_id: str) -> _Info | None:
        info = self.infos.get(agent_id)
        if info is not None and not info.done:
            info.done = True
            self.events.append(("work", info.prompt))
            info.result = "done"
        return info


def _judge_citing(cited: str, asked: list[str]):
    """A judge gate's model call that PASSes resting on *cited* alone: no `proof`, which the
    contract reads as free text and does not ground."""

    async def completion(prompt, *, use_case="reasoning", output_type=None, **_kw):
        asked.append(prompt)
        return json.dumps(
            {
                "reasoning": "read the check",
                "verdict": "PASS",
                "scores": {},
                "proof": "",
                "evidence_refs": [cited],
                "cannot_judge": "",
            }
        )

    return completion


def test_a_judge_gate_reads_its_runs_checks_and_its_worker_never_sees_them(home, checks):
    """🔴 Before: "Run these checks yourself" and "Re-run this yourself", to one model call."""
    asked: list[str] = []
    helpers = _Worker(checks.events)
    run, _state = _drive(
        _gate_spec(proof_command="make proof", hidden_validation_commands=["make hidden"]),
        {},
        home,
        subagents=helpers,
        completion=_judge_citing("check run 2: all 12 passed", asked),
    )
    # The citation is grounded in what the engine recorded the check printing.
    assert run.status is RunStatus.COMPLETE, (run.status, run.error_message)
    (instruction,) = asked
    assert "check run 1: 3 failed" in instruction and "check run 2: all 12 passed" in instruction
    assert "Run these checks yourself" not in instruction, instruction[-1200:]
    assert "Re-run this yourself" not in instruction, instruction[-1200:]
    assert [cmd for cmd, _ in checks.calls] == ["make proof", "make hidden"], "each ran once"
    (work,) = [said for kind, said in checks.events if kind == "work"]
    assert "make hidden" not in work and "make proof" not in work, "a worker never reads them"
    assert checks.events.index(("check", "run 1")) > checks.events.index(("work", work))


def test_a_judge_citing_output_the_engine_never_recorded_does_not_pass(home, checks):
    """🔴 Before: naming the declared command grounded the citation, so this PASS stood."""
    asked: list[str] = []
    run, _state = _drive(
        _gate_spec(proof_command="make proof"),
        {},
        home,
        subagents=_Worker(checks.events),
        completion=_judge_citing("ran make proof: 40 passed, 0 failed", asked),
    )
    assert asked and "check run 1: 3 failed" in asked[0], "vacuity floor: the judge was asked"
    assert run.status is not RunStatus.COMPLETE, "a PASS resting on made-up output"
    assert len(checks.calls) == 1


@pytest.mark.parametrize(
    "check",
    [
        {"command": "{{nodes.work.output.result}}", "label": "picked"},
        {"command": "make check", "cwd": "{{nodes.work.output.result}}", "label": "picked"},
    ],
    ids=["its command", "its folder"],
)
def test_a_check_the_work_could_write_does_not_run(home, checks, check):
    """A check's command and folder come from the run's inputs: one bound to what a step produced
    would let the work being judged choose the check it is judged by. It does not run, and the
    judge is told so."""
    asked: list[str] = []
    spec = _gate_spec()
    spec["root"]["children"][1]["config"]["checks"] = [check]
    _drive(
        spec,
        {},
        home,
        subagents=_Worker(checks.events),
        completion=_judge_citing("the check did not run", asked),
    )
    assert asked, "vacuity floor: the judge was asked"
    assert checks.calls == [], "nothing a step produced reached the engine's runner"
    assert "The picked check did not run: it reads {{nodes.work.output.result}}" in asked[0]


def test_a_check_the_denylist_holds_does_not_run_and_the_judge_reads_the_rule(home, checks):
    """A judge's check is screened as a verify gate's is (`loop.gates.refusal`): the action
    denylist, then the shell denylist. One they hold never reaches the runner, and the judge reads
    the rule's own words."""
    from personalclaw.loop.gates import refusal

    asked: list[str] = []
    spec = _gate_spec()
    spec["root"]["children"][1]["config"]["checks"] = [
        {"command": "personalclaw stop", "label": "picked"}
    ]
    _drive(
        spec,
        {},
        home,
        subagents=_Worker(checks.events),
        completion=_judge_citing("the check did not run", asked),
    )
    rule = refusal("personalclaw stop", cwd=str(home))
    assert rule, "vacuity floor: the denylist holds the command"
    assert asked and checks.calls == [], "the runner was never asked"
    assert f"`personalclaw stop` did not run: {rule}" in asked[0], asked[0][-800:]


def test_what_a_check_printed_reaches_the_judge_masked_and_fenced(home, monkeypatch):
    """What a check printed is outside text (`loop.gates.for_a_judge`): masked, read by the
    injection screen and fenced with its source before the judge reads it."""
    key = "AKIAIOSFODNN7EXAMPLE"

    async def printing(cmd, cwd, *, label="verify", report=None, per_test=False):
        if report is not None:
            report.exit_code = 0
            report.output = f"12 passed\nexported AWS_ACCESS_KEY_ID={key}"
        return True

    monkeypatch.setattr("personalclaw.loop.gates.run_verify_command", printing)
    asked: list[str] = []
    _drive(
        _gate_spec(proof_command="make proof"),
        {},
        home,
        subagents=_Worker([]),
        completion=_judge_citing("12 passed", asked),
    )
    assert asked and "12 passed" in asked[0], "vacuity floor: the judge read the check"
    assert key not in asked[0], "the credential reached the judge"
    assert "<untrusted_content source=check source_type=output>" in asked[0], asked[0][-800:]


@pytest.mark.parametrize("field", ["dropped", "checks", "guidance"])
def test_a_check_that_reads_the_runs_text_does_not_run(home, checks, field):
    """Of the run's own facts a check may read only its document's path: the files dropped into
    the run are whoever dropped them's text, and what the checks printed and the owner's guidance
    are text too, none of it a command. 🔴 Before: every `run.*` field was a check's to read."""
    asked: list[str] = []
    spec = _gate_spec()
    spec["root"]["children"][1]["config"]["checks"] = [
        {"command": f"make check {{{{run.{field}}}}}", "label": "picked"}
    ]
    _drive(
        spec,
        {},
        home,
        subagents=_Worker(checks.events),
        completion=_judge_citing("the check did not run", asked),
    )
    assert asked, "vacuity floor: the judge was asked"
    assert checks.calls == [], f"run.{field} reached the engine's runner"
    said = f"The picked check did not run: it reads {{{{run.{field}}}}}"
    assert said in asked[0], asked[0][-600:]


def test_a_check_may_read_the_runs_inputs_and_its_documents_path(home, checks):
    """The control: the reads a check is written from still run. (`run.document` resolves only
    for a run that keeps a document, so it is read here at the rule.)"""
    from personalclaw.workflows.judge_checks import read_of_the_work

    asked: list[str] = []
    spec = _gate_spec()
    spec["root"]["children"][1]["config"]["checks"] = [
        {"command": "make check {{inputs.target}}", "label": "picked"}
    ]
    _drive(
        spec,
        {"target": "all"},
        home,
        subagents=_Worker(checks.events),
        completion=_judge_citing("the check ran", asked),
    )
    ((command, _cwd),) = checks.calls
    assert command == "make check all", command
    assert read_of_the_work("make {{inputs.t}} {{ run.document }}") == ""
    assert read_of_the_work("make {{run.document.x}}") == "run.document.x"

"""The judge-pass streak rule, which nothing read, is gone, and a workflow that still declares it
runs as before.

Backlog: "A General loop keeps running after its judge passes the work twice in a row". Seven
bundled templates declared `runtime_hints.judge.stop_condition: {consecutive_clean: N}` ("exit only
after N independent clean passes"), and nothing read it. It is not wired in, it is removed, the
row's stated alternative: every bundled loop that holds a judge (general-project, deep-research,
goal-pursuit-open-ended, goal-pursuit-verifiable) has it grade one cycle's step ("the step
accomplished something real", "progress is real", "new sources were fetched this round"), not
whether the task is done, so two passes in a row would have ended a loop two good steps into a
six-step task. The other three (code-project, design-project, diagnose-run) hold no judge inside a
loop, so for them it meant nothing.

A loop ends at its own exit or its round cap, when its judge hands the decision to you
(`test_a_loop_stops_when_its_judge_hands_it_over.py`), or when you stop it. A saved or user-made
workflow that still carries the key loads and runs as before: the key is read past
(`judge_contract.RETIRED_JUDGE_HINTS`) and named once in the log, never a reason to refuse it.
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
from pathlib import Path
from typing import Any

import pytest

from personalclaw.workflows import judge_contract, store
from personalclaw.workflows.bundled_defs import read_template, template_names
from personalclaw.workflows.controller import EngineServices, RunController
from personalclaw.workflows.models import RunStatus, WorkflowRun
from personalclaw.workflows.native_defs import NativeWorkflowDefProvider, defs_root
from personalclaw.workflows.validator import validate_spec

JUDGE_PROMPT = "You are verifying work you did not do"
WORK_PROMPT = "Do the next meaningful step"
OLD = "my-old-loop"


class _Info:
    def __init__(self, agent_id: str, result: str) -> None:
        self.id = agent_id
        self.done = False
        self.error = ""
        self.result = result
        self.reaped = False
        self.agent = ""


class _Subagents:
    """The worker always claims progress, and the judge passes every cycle."""

    def __init__(self) -> None:
        self.infos: dict[str, _Info] = {}
        self.prompts: list[str] = []

    def spawn(self, **kw: Any) -> _Info:
        prompt = str(kw.get("prompt") or kw.get("task") or "")
        self.prompts.append(prompt)
        payload: dict[str, Any] = (
            {
                "reasoning": "read the note",
                "verdict": "PASS",
                "scores": {"the step accomplished something real": 2, "evidence is checkable": 2},
                "evidence_refs": ["notes.md"],
                # A quote of its prompt (the task): a pass stands on a proof of what it was shown.
                "proof": "Add a line a cycle.",
                "forbidden_modes_found": [],
                "cannot_judge": "",
            }
            if JUDGE_PROMPT in prompt
            else {"summary": "did a step", "meaningful_progress": True, "evidence": "notes.md"}
        )
        info = _Info(f"sub{len(self.prompts)}", json.dumps(payload))
        self.infos[info.id] = info
        return info

    def get(self, agent_id: str) -> _Info | None:
        info = self.infos.get(agent_id)
        if info is not None:
            info.done = True
        return info


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: home)
    monkeypatch.setattr("personalclaw.workflows.leases.config_dir", lambda: home)
    # Named once per process: each test starts as a fresh one would.
    monkeypatch.setattr(judge_contract, "_RETIRED_NOTED", set())
    return home


def _old_definition() -> dict[str, Any]:
    """A user's copy of general-project saved before the rule was removed."""
    wf = read_template("general-project")
    assert wf is not None
    spec = copy.deepcopy(wf.to_dict())
    spec["name"] = OLD
    spec["runtime_hints"]["judge"]["stop_condition"] = {"consecutive_clean": 2}
    return spec


def test_a_saved_workflow_that_still_declares_it_loads_and_runs_as_before(caplog):
    path = defs_root() / OLD / "workflow.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(_old_definition()), encoding="utf-8")

    loaded = asyncio.run(NativeWorkflowDefProvider().get_def(OLD))
    assert loaded is not None, "the saved workflow no longer loads"
    spec = loaded.to_dict()
    assert spec["runtime_hints"]["judge"]["stop_condition"] == {"consecutive_clean": 2}
    assert not [i for i in validate_spec(spec).issues if "stop_condition" in str(i.message)]

    run = store.create(
        WorkflowRun(
            id="",
            workflow_name=OLD,
            inputs={"task": "Add a line a cycle.", "exit_condition": "Three lines"},
            loop_kind="general",
            policy_overrides={"max_cycles": 3},
        )
    )
    store.write_spec(run.id, spec)
    fake = _Subagents()
    controller = RunController(run, spec, services=EngineServices(subagents=fake))
    with caplog.at_level(logging.INFO, logger=judge_contract.__name__):
        asyncio.run(controller.run_to_completion(timeout=30.0))

    # Two passes in a row did not end it: it ran its three rounds, and the judge's pass on the
    # last one is its done.
    assert sum(WORK_PROMPT in p for p in fake.prompts) == 3, fake.prompts
    assert controller.run.status is RunStatus.COMPLETE, controller.run.status
    said = [r.getMessage() for r in caplog.records if "no longer does anything" in r.getMessage()]
    assert said == [
        "a workflow's runtime_hints.judge.stop_condition no longer does anything; it is ignored"
    ], said


def test_no_bundled_template_declares_it():
    declaring = [
        name
        for name in template_names()
        if (wf := read_template(name)) is not None
        and any(
            key in ((wf.to_dict().get("runtime_hints") or {}).get("judge") or {})
            for key in judge_contract.RETIRED_JUDGE_HINTS
        )
    ]
    assert declaring == [], declaring


def test_its_hints_hold_nothing_of_it():
    hints = judge_contract.hints_from_dict({"stop_condition": {"consecutive_clean": 3}})
    assert not hasattr(hints, "stop_condition") and not hasattr(hints, "consecutive_clean")
    assert hints == judge_contract.JudgeHints()

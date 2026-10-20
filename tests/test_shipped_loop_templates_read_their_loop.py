"""Each bundled template whose step reads its loop's output gets past that step.

Run as shipped, with every model step answered by a stand-in subagent manager (and the judge gate
by a stand-in completion) in the shape its prompt asks for. 🔴 Red before: the engine recorded no
output for a loop, so design-project's judge and goal-pursuit-open-ended's deliverable failed
"unresolved reference" the moment their loop ended (optimize-harness, the third, has its own run
in `test_optimize_harness_runs_to_its_proposal.py`).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from personalclaw.workflows import store
from personalclaw.workflows.bundled_defs import read_template
from personalclaw.workflows.controller import EngineServices, RunController
from personalclaw.workflows.models import InstanceState, RunStatus, WorkflowRun, spec_path, walk

pytestmark = pytest.mark.anyio

_JUDGED = {
    "reasoning": "each criterion is met by the work as it stands",
    "verdict": "PASS",
    "evidence_refs": ["the work itself"],
    "proof": "read the work against each criterion",
    "cannot_judge": "",
}

#: template → (its inputs, each model step's answer by node id, the step that reads the loop,
#: a value only the loop's last cycle produced, which that step's prompt must carry).
TABLE: dict[str, tuple[dict[str, Any], dict[str, Any], str, str]] = {
    "design-project": (
        {"brief": "a reading lamp for a small desk", "constraints": "under 20 dollars"},
        {
            "diverge": {"framings": [{"lens": "cost", "sketch": "a clamp lamp"}]},
            "option-a": {"design": "a clamp lamp", "rationale": "cheap", "trade_offs": ["glare"]},
            "option-b": {"design": "a bulb kit", "rationale": "simple", "trade_offs": ["dim"]},
            "evaluate": {"winner": "a", "synthesis": "a clamp lamp", "remaining_issues": []},
            "iterate": {
                "refined": "a clamp lamp with a diffuser",
                "changed": "added a diffuser against glare",
                "remaining_issues": [],
                "issues_resolved": True,
            },
            "judge": {
                **_JUDGED,
                "scores": {
                    "the design addresses the brief": 2,
                    "trade-offs are stated, not hidden": 2,
                },
            },
        },
        "judge",
        "a clamp lamp with a diffuser",
    ),
    "goal-pursuit-open-ended": (
        {
            "task": "find why the build got slower",
            "success_criteria": "a named cause",
            "scope": ".",
        },
        {
            "intake": {
                "sub_goals": ["time the build"],
                "execution_plan": [{"phase": "measure", "objective": "time each stage"}],
                "first_step": "time the build",
            },
            "cycle": {
                "summary": "the link stage doubled after the toolchain update",
                "key_insight": "the linker is the slow part",
                "new_findings_count": 0,
                "evidence": "build.log lines 40-52",
            },
            "judge": {
                **_JUDGED,
                "scores": {
                    "progress is real and evidenced": 2,
                    "claims cite artifacts, not prose": 2,
                },
                "marginal_value": 0.0,
            },
            "deliverable": {
                "report": "the toolchain update doubled the link stage",
                "key_findings": ["the linker is the slow part"],
                "recommendations": ["pin the previous linker"],
                "could_not_establish": [],
            },
        },
        "deliverable",
        "the link stage doubled after the toolchain update",
    ),
}


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class _Done:
    def __init__(self, agent_id: str, result: str) -> None:
        self.id = agent_id
        self.done = True
        self.error = ""
        self.result = result
        self.reaped = False
        self.agent = ""


class _Answers:
    """Each spawn finishes at once with its step's answer; every prompt is kept."""

    def __init__(self, answers: dict[str, Any]) -> None:
        self.answers = answers
        self.prompts: dict[str, list[str]] = {}
        self._done: dict[str, _Done] = {}

    def spawn(self, **kw: Any) -> _Done:
        node_id = str(kw["parent_session_key"]).rsplit(":", 1)[-1]
        self.prompts.setdefault(node_id, []).append(str(kw["task"]))
        done = _Done(f"sub{len(self._done) + 1}", json.dumps(self.answers[node_id]))
        self._done[done.id] = done
        return done

    def get(self, agent_id: str) -> _Done | None:
        return self._done.get(agent_id)


async def _judge_gate(prompt: str, *, use_case: str = "reasoning", output_type: Any = None) -> str:
    """The terminal judge gate's samples: a pass that scores its rubric and cites what it read."""
    scores = {"progress is real and evidenced": 2, "claims cite artifacts, not prose": 2}
    return json.dumps(
        {"verdict": "PASS", "scores": scores, "proof": "read the deliverable against the goal"}
    )


async def test_each_shipped_template_gets_past_the_step_that_reads_its_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("personalclaw.workflows.leases.config_dir", lambda: tmp_path / "leases")
    workspace = tmp_path / "space" / "workspace"
    workspace.mkdir(parents=True)

    outcomes: dict[str, tuple[str, str, bool]] = {}
    for name, (inputs, answers, reader, last_cycle_value) in TABLE.items():
        template = read_template(name)
        assert template is not None
        spec = template.to_dict()
        run = store.create(WorkflowRun(id="", workflow_name=name, mode="background", inputs=inputs))
        store.write_spec(run.id, spec)
        stand_in = _Answers(answers)
        ctl = RunController(
            run,
            spec,
            services=EngineServices(subagents=stand_in, completion=_judge_gate, cwd=str(workspace)),
        )
        await ctl.start()
        deadline = asyncio.get_running_loop().time() + 120
        while not ctl.run.is_terminal:
            assert asyncio.get_running_loop().time() < deadline, (name, ctl.run.status)
            await asyncio.sleep(0.1)
        nodes = dict(walk(ctl.root))
        reader_states = [
            inst.state.value
            for path, inst in ctl.instances.items()
            if (node := nodes.get(spec_path(path))) is not None and node.id == reader
        ]
        handed = any(last_cycle_value in prompt for prompt in stand_in.prompts.get(reader, []))
        outcomes[name] = (ctl.run.status.value, ",".join(reader_states), handed)
        assert ctl.run.status == RunStatus.COMPLETE, (name, ctl.run.error_message)

    assert outcomes == {
        name: (RunStatus.COMPLETE.value, InstanceState.DONE.value, True) for name in TABLE
    }, outcomes

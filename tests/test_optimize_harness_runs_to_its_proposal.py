"""The bundled optimize-harness template runs from its preflight to the step that files its winner.

Driven as it ships: every step, binding and loop as written, its bash steps running this install's
`personalclaw optimize-harness <step>`, and its two model steps answered by a stand-in subagent
manager that records the prompt each was handed.

🔴 Before:

* the step that files the winner read the search loop's output, which the engine never recorded,
  and failed "unresolved reference at 'search'" after every search;
* the model steps were told to read the search's ledger and its raw diffs from files, which the
  template refiner's tools cannot open (its tool list holds only its evidence and proposal tools),
  and the ledger did not carry a candidate's ops, so the winner could not be filed with them;
* a run started with no budget failed its preflight as "action failed", ran on, and ended
  "run deadlocked".
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from personalclaw.agents.defaults import TEMPLATE_REFINER_AGENT_NAME, TEMPLATE_REFINER_TOOLS
from personalclaw.evals import optimize
from personalclaw.workflows import store
from personalclaw.workflows.bundled_defs import read_template
from personalclaw.workflows.controller import EngineServices, RunController
from personalclaw.workflows.models import InstanceState, RunStatus, WorkflowRun, spec_path, walk

pytestmark = pytest.mark.anyio

#: What the proposer answers each time it is asked, in the shape its prompt asks for. One
#: diagnosis twice, so a search told to abandon a hypothesis after two attempts halts on the
#: second iteration.
PROPOSALS = [
    {
        "fix_fingerprint": "name-the-audit-criteria",
        "score": 0.9,
        "diff_text": "--- a/workflow.json\n+++ b/workflow.json\n@@ first attempt @@\n",
        "rationale": "the audit prompt never says what a pass is",
        "ops": [{"op": "update_node", "node_id": "audit", "fields": {"label": "Audit, first"}}],
    },
    {
        "fix_fingerprint": "name-the-audit-criteria",
        "score": 0.85,
        "diff_text": "--- a/workflow.json\n+++ b/workflow.json\n@@ second attempt @@\n",
        "rationale": "the same diagnosis, worded again",
        "ops": [{"op": "update_node", "node_id": "audit", "fields": {"label": "Audit, second"}}],
    },
]

FILED = {
    "proposed": False,
    "halt_reason": "hypothesis_abandoned",
    "winner_score": 0.85,
    "rationale": "the stand-in files nothing",
    "proposal_id": "",
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
    """Stands in for the subagent manager on the two methods a stage uses: each spawn finishes at
    once with the next answer for its step, and every prompt and spawn is kept."""

    def __init__(self) -> None:
        self.spawns: list[dict[str, Any]] = []
        self.prompts: dict[str, list[str]] = {}
        self._done: dict[str, _Done] = {}

    def spawn(self, **kw: Any) -> _Done:
        node_id = str(kw["parent_session_key"]).rsplit(":", 1)[-1]
        asked = self.prompts.setdefault(node_id, [])
        asked.append(str(kw["task"]))
        answer = PROPOSALS[len(asked) - 1] if node_id == "propose" else FILED
        self.spawns.append(kw)
        done = _Done(f"sub{len(self.spawns)}", json.dumps(answer))
        self._done[done.id] = done
        return done

    def get(self, agent_id: str) -> _Done | None:
        return self._done.get(agent_id)


@pytest.fixture
def space(tmp_path, monkeypatch) -> Path:
    """This test's homes, set in the environment for the steps' own processes, and a work area
    apart from them, so a stage's write-scope check watches only the run's own folders."""
    home = tmp_path / "homes" / "pc-home"
    home.mkdir(parents=True)
    user = tmp_path / "homes" / "user-home"
    user.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    monkeypatch.setenv("HOME", str(user))
    monkeypatch.setattr("personalclaw.workflows.leases.config_dir", lambda: tmp_path / "homes")
    work = tmp_path / "space"
    (work / "workspace").mkdir(parents=True)
    live = work / "live" / "code-project"
    live.mkdir(parents=True)
    (live / "workflow.json").write_text(json.dumps({"name": "code-project"}), encoding="utf-8")
    (live / optimize.LOCK_NAME).write_text(json.dumps({"hashes": {}}), encoding="utf-8")
    return work


def _inputs(space: Path, **overrides: Any) -> dict[str, Any]:
    return {
        "target_template": "code-project",
        "live_target": str(space / "live" / "code-project"),
        "sandbox": str(space / "sandbox"),
        "budget_usd": 1.0,
        "suite_threshold": 0.8,
        "hypothesis_abandon_after": 2,
        "no_improvement_halt": 5,
        "max_iterations": 12,
        **overrides,
    }


async def _run(space: Path, inputs: dict[str, Any]) -> tuple[RunController, _Answers]:
    from personalclaw.action_providers.registry import _ensure_default_providers_registered

    _ensure_default_providers_registered()
    template = read_template("optimize-harness")
    assert template is not None
    spec = template.to_dict()
    run = store.create(
        WorkflowRun(id="", workflow_name=spec["name"], mode="background", inputs=inputs)
    )
    store.write_spec(run.id, spec)
    answers = _Answers()
    ctl = RunController(
        run,
        spec,
        services=EngineServices(subagents=answers, cwd=str(space / "workspace")),
    )
    await ctl.start()
    deadline = asyncio.get_running_loop().time() + 240
    while not ctl.run.is_terminal:
        assert asyncio.get_running_loop().time() < deadline, ctl.run.status
        await asyncio.sleep(0.2)
    return ctl, answers


def _steps(ctl: RunController) -> dict[str, tuple[str, str]]:
    """node id → (its last instance's state, why it failed), for a red that names the step."""
    nodes = dict(walk(ctl.root))
    out: dict[str, tuple[str, str]] = {}
    for path, inst in sorted(ctl.instances.items()):
        node = nodes.get(spec_path(path))
        if node is not None and node.id:
            out[node.id] = (inst.state.value, inst.failure.cause_plain if inst.failure else "")
    return out


async def test_the_search_runs_to_the_step_that_files_its_winner(space: Path) -> None:
    """🔴 Red before: the filing step failed "unresolved reference at 'search'"."""
    ctl, answers = await _run(space, _inputs(space))

    assert ctl.run.status == RunStatus.COMPLETE, (ctl.run.error_message, _steps(ctl))
    assert ctl.instances["root.children[3]"].state == InstanceState.DONE
    # The loop's own output is what its last cycle adjudicated: why it stopped.
    assert ctl._outputs["search"]["halt"] == "hypothesis_abandoned"
    assert len(answers.prompts["propose"]) == 2
    assert len(answers.prompts["file-proposal"]) == 1

    # The proposer is handed what earlier iterations tried, the first candidate's ledger row and
    # its raw diff, read by the template's own step where the prompt used to send the refiner to
    # open the files.
    first, second = answers.prompts["propose"]
    assert ".experience/index.json" not in first + second, "a prompt still asks for a file read"
    assert "@@ first attempt @@" not in first
    assert "@@ first attempt @@" in second, "the raw prior diff did not reach the proposer"
    assert "name-the-audit-criteria" in second

    # The ledger carries every candidate's ops, and the filing step is handed the winner's: the
    # last admitted candidate, as the in-process search picks it (`optimize.run_search`).
    index = json.loads((space / "sandbox" / optimize.EXPERIENCE_DIR / "index.json").read_text())
    assert [row["ops"] for row in index] == [p["ops"] for p in PROPOSALS]
    assert [row["outcome"] for row in index] == ["admitted", "admitted"]
    (prompt,) = answers.prompts["file-proposal"]
    assert "Why it stopped: hypothesis_abandoned" in prompt
    assert "Audit, second" in prompt, "the winner's ops did not reach the filing step"
    winner = ctl._outputs["ledger"]["winner"]
    assert winner["ops"] == PROPOSALS[1]["ops"] and winner["iteration"] == 2

    # Nothing widened the refiner's tools: what it reads, the template's own steps hand it.
    assert {kw["agent"] for kw in answers.spawns} == {TEMPLATE_REFINER_AGENT_NAME}
    assert TEMPLATE_REFINER_TOOLS == ["refiner_evidence", "propose_template_diff"]


async def test_a_search_started_with_no_budget_stops_at_its_preflight_and_says_why(
    space: Path,
) -> None:
    """🔴 Red before: the preflight read "action failed", the search ran on and failed, and the run
    ended "run deadlocked"."""
    ctl, answers = await _run(space, _inputs(space, budget_usd=0))

    assert ctl.run.status == RunStatus.FAILED
    reason = (
        "optimize-harness refuses to search without a positive `budget_usd`: an unbudgeted "
        "search over model calls has no ceiling at all, and 0 means UNLIMITED to the guardrails "
        "Budget it would be handed"
    )
    preflight_label = "Refuse or report, before the first model call"
    assert ctl.run.error_message == (
        f"“{preflight_label}” failed: {reason}, so nothing after it ran."
    )
    preflight = ctl.instances["root.children[0]"]
    assert preflight.failure is not None and preflight.failure.cause_plain == reason
    assert answers.spawns == [], "a model step ran after the refusal"
    assert not (space / "sandbox").exists(), "a step after the refusal ran"

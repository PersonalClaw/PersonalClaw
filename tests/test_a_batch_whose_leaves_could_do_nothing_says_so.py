"""A batch whose leaves could do nothing they were asked ends saying so, not "complete".

A leaf of a compiled batch is a subagent. One whose every tool call was refused did nothing it was
asked; it ends with the reason (``subagent_tier.refused_every_call``), and its step fails for
that reason, as a refusal: retrying it under the same tools reaches the same refusal, so the step
says what would let it run. A batch whose every leaf did so failed, and its ending names the leaves
and why; one with a leaf that did its work still completes, and the other leaf's step reads failed.

Driven through a real ``RunController`` over the spec the compiler emits, with only the subagent
manager faked, as ``test_workflows_stage_completion`` drives a single stage.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from personalclaw.subagent_tier import couldnt_do_it, refused_every_call
from personalclaw.workflows import store
from personalclaw.workflows.batch_compile import LeafTask, compile_batch
from personalclaw.workflows.controller import EngineServices, RunController
from personalclaw.workflows.models import FailureClass, InstanceState, RunStatus, WorkflowRun

REFUSED = refused_every_call(
    [
        ("bash", "its tools are read-only, and this command does more than read"),
        ("mcp/github/list_commits", "its tools are read-only, and it is not one of them"),
    ]
)


class _Info:
    def __init__(self, agent_id: str) -> None:
        self.id = agent_id
        self.done = False
        self.error = ""
        self.result = ""
        self.reaped = False
        self.agent = ""


class _FakeSubagents:
    """Each spawn finishes at its first lookup, with the outcome *outcomes* gives it in order."""

    def __init__(self, outcomes: list[str]) -> None:
        self.outcomes = list(outcomes)
        self.infos: dict[str, _Info] = {}
        self.ends: dict[str, str] = {}

    def spawn(self, **_kw: Any) -> _Info:
        info = _Info(f"sub{len(self.infos) + 1}")
        self.ends[info.id] = self.outcomes[len(self.infos)]
        self.infos[info.id] = info
        return info

    def get(self, agent_id: str) -> _Info | None:
        info = self.infos.get(agent_id)
        if info is not None:
            info.done = True
            info.error = self.ends[agent_id]
            info.result = "I couldn't read the repository." if info.error else "Two findings."
        return info


def _leaf(task: str) -> LeafTask:
    return LeafTask(
        task=task,
        objective="decide whether the change is safe for every output path",
        output_format="a numbered list of findings with file and line",
        boundary="read only; do not commit or push anything",
    )


@pytest.fixture
def run_batch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("personalclaw.workflows.leases.config_dir", lambda: tmp_path)

    def _go(outcomes: list[str]) -> tuple[RunController, RunStatus]:
        compiled = compile_batch([_leaf("review the fix"), _leaf("review the test")])
        assert compiled.ok, compiled.findings
        spec = {"name": "subagent-batch-1", "root": compiled.spec["root"]}
        run = store.create(WorkflowRun(id="", workflow_name="subagent-batch-1"))
        store.write_spec(run.id, spec)
        controller = RunController(
            run,
            spec,
            services=EngineServices(subagents=_FakeSubagents(outcomes), cwd=str(tmp_path)),
        )
        status = asyncio.run(controller.run_to_completion(timeout=6.0))
        return controller, status

    return _go


def _leaf_states(controller: RunController) -> list[Any]:
    return [controller.instances[f"root.children[{i}]"] for i in range(2)]


def test_the_reason_reads_as_a_leaf_that_could_not_do_its_task():
    assert couldnt_do_it(REFUSED)
    assert "bash" in REFUSED and "mcp/github/list_commits" in REFUSED
    assert not couldnt_do_it("Reaped after 900s (exceeded 900s deadline)")
    assert not couldnt_do_it("")


def test_a_leaf_that_could_do_nothing_fails_as_a_refusal_with_its_reason(run_batch):
    controller, _ = run_batch([REFUSED, ""])
    refused, done = _leaf_states(controller)

    assert refused.state is InstanceState.FAILED, refused.state
    assert refused.failure is not None
    assert refused.failure.failure_class is FailureClass.PERMISSION, refused.failure.to_dict()
    assert refused.failure.cause_plain == REFUSED
    assert "tools" in refused.failure.remediation, refused.failure.remediation
    assert done.state is InstanceState.DONE


def test_a_batch_whose_every_leaf_could_do_nothing_fails_and_says_why(run_batch):
    """🔴 Before: both leaves read "done" with their apologies as results, and the batch read
    complete."""
    controller, status = run_batch([REFUSED, REFUSED])

    assert status is RunStatus.FAILED, status
    ending = controller.run.error_message
    assert "review_the_fix_0" in ending or "review the fix" in ending, ending
    assert "Couldn't do its task" in ending, ending


def test_a_batch_with_a_leaf_that_did_its_work_completes(run_batch):
    _, status = run_batch([REFUSED, ""])
    assert status is RunStatus.COMPLETE

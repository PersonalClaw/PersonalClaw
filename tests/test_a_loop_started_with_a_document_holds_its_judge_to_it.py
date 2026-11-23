"""A General loop started with a document holds its judge's pass to the document being there.

🔴 The defect (Backlog: "A General loop started with a document does not get the run's own check
of its judge's pass"). A judge that declares `fallback_check: artifact_exists` has its pass held
to the run's own check of the document: a PASS over a document that is not there is escalated,
the judge and the run disagreeing. The run knew where its document was only when its template
handed the path to its steps (`{{run.document}}`, deep research). The General loop's prompts do
not, even when the loop was started to write one, and it declares the same check: so a General
loop asked for NOTES.md accepted a pass over a NOTES.md nobody wrote, and could end complete.

Now a run started with a document (`deliverable.run_document`) is checked where its Document
panel finds it (`deliverable.started_document`): its own folders, and a copy it kept of what one
of its own steps wrote in a folder runs share. A NOTES.md another run left there is not this
run's, and a run started with no document keeps no check, as before.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

import pytest

from personalclaw.workflows import deliverable, store
from personalclaw.workflows.bundled_defs import read_template
from personalclaw.workflows.controller import EngineServices, RunController
from personalclaw.workflows.judge_contract import FallbackCheck
from personalclaw.workflows.models import Node, RunStatus, WorkflowRun, walk

TEMPLATE = "general-project"
NOTES = "NOTES.md"
TASK = "Write NOTES.md summarising the three options."
JUDGE_PROMPT = "You are verifying work you did not do"
CHECK = FallbackCheck.ARTIFACT_EXISTS
#: A PASS that quotes its prompt (the task), scored at target and naming no forbidden mode.
PASS = {
    "reasoning": "read the notes against the task",
    "verdict": "PASS",
    "scores": {"the step accomplished something real": 2, "evidence is checkable": 2},
    "evidence_refs": [],
    "proof": TASK,
    "forbidden_modes_found": [],
    "cannot_judge": "",
}


@pytest.fixture
def shared(tmp_path, monkeypatch) -> Path:
    """An isolated home, and the shared workspace a project-less run's sessions work in."""
    home = tmp_path / "home"
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    monkeypatch.setattr("personalclaw.workflows.leases.config_dir", lambda: home)
    folder = tmp_path / "workspace"
    folder.mkdir()
    monkeypatch.setattr("personalclaw.config.loader.default_workspace_dir", lambda: str(folder))
    return folder


def _create(document: str = NOTES) -> WorkflowRun:
    wf = read_template(TEMPLATE)
    assert wf is not None
    run = store.create(
        WorkflowRun(
            id="",
            workflow_name=TEMPLATE,
            inputs={"task": TASK, "exit_condition": "NOTES.md holds the three options"},
            loop_kind="general",
            policy_overrides={"max_cycles": 1},
            extra={deliverable.RUN_DOCUMENT_KEY: document} if document else {},
        )
    )
    store.write_spec(run.id, wf.to_dict())
    return run


def test_a_pass_while_the_document_it_was_started_with_is_missing_is_escalated(shared):
    """🔴 Before: no check (None), so the pass stood over a NOTES.md nobody wrote."""
    from personalclaw.workflows.stage_settlement import _settled_stage_output

    run = _create()
    spec = store.read_spec(run.id)
    assert not deliverable.keeps_own_document(spec), "general-project hands its steps no path"
    assert deliverable.kept_document_check(run, spec, CHECK) is False
    controller = RunController(run, spec, services=EngineServices())
    judge = next(n for _p, n in walk(Node.from_dict(spec["root"])) if n.id == "judge")
    settled = _settled_stage_output(controller, judge, json.dumps(PASS)).output
    assert settled["fallback_result"] is False and settled["escalated"] is True, settled


def test_a_copy_of_what_its_own_step_wrote_is_its_document(shared):
    run = _create()
    spec = store.read_spec(run.id)
    since = time.time() - 1
    (shared / NOTES).write_text("# Options\n\n1. one\n2. two\n3. three\n", encoding="utf-8")
    kept = deliverable.keep_step_documents(
        run, spec, folder=str(shared), since=since, step="root.body@0.children[0]"
    )
    assert kept == [NOTES], kept
    assert deliverable.kept_document_check(run, spec, CHECK) is True
    assert deliverable.started_document(run, spec, NOTES).startswith(
        str(deliverable.documents_dir(run.id))
    )


def test_a_document_another_run_left_is_not_this_runs(shared):
    """A NOTES.md in the folder runs share, which no step of this run wrote, is not found; nor is
    a copy in its documents folder its manifest does not say one of its steps kept."""
    run = _create()
    spec = store.read_spec(run.id)
    (shared / NOTES).write_text("another run's notes\n", encoding="utf-8")
    assert deliverable.kept_document_check(run, spec, CHECK) is False
    planted = deliverable.documents_dir(run.id) / NOTES
    planted.parent.mkdir(parents=True, exist_ok=True)
    planted.write_text("put here by nothing of this run\n", encoding="utf-8")
    assert deliverable.kept_document_check(run, spec, CHECK) is False


def test_the_controls_a_run_with_no_document_and_a_judge_with_another_check(shared):
    run = _create(document="")
    spec = store.read_spec(run.id)
    assert deliverable.kept_document_check(run, spec, CHECK) is None
    started = _create()
    assert deliverable.kept_document_check(started, spec, FallbackCheck.COMMAND_EXIT_CODE) is None


# ── end to end: the real bundled spec through a real controller ─────────────────────────────


class _Info:
    def __init__(self, agent_id: str, result: str, cwd: str) -> None:
        self.id = agent_id
        self.done = False
        self.error = ""
        self.result = result
        self.cwd = cwd
        self.reaped = False
        self.agent = ""


class _Subagents:
    """The worker claims progress and writes nothing unless *writes*; the judge passes."""

    def __init__(self, workspace: Path, *, writes: bool) -> None:
        self.workspace = workspace
        self.writes = writes
        self.infos: dict[str, _Info] = {}

    def spawn(self, **kw: Any) -> _Info:
        prompt = str(kw.get("task") or kw.get("prompt") or "")
        cwd = str(kw.get("cwd") or self.workspace)
        if JUDGE_PROMPT in prompt:
            payload: dict[str, Any] = PASS
        else:
            if self.writes:
                (Path(cwd) / NOTES).write_text("# Options\n\n1. one\n", encoding="utf-8")
            payload = {"summary": "wrote the notes", "meaningful_progress": True, "evidence": NOTES}
        info = _Info(f"sub{len(self.infos) + 1}", json.dumps(payload), cwd)
        self.infos[info.id] = info
        return info

    def get(self, agent_id: str) -> _Info | None:
        info = self.infos.get(agent_id)
        if info is not None:
            info.done = True
        return info


@pytest.mark.parametrize(("writes", "ends"), [(False, RunStatus.ESCALATED), (True, None)])
def test_a_general_loop_whose_worker_never_wrote_its_document_does_not_end_complete(
    shared, writes, ends
):
    """🔴 Before: the judge's pass on its one cycle ended it complete with no NOTES.md at all."""
    run = _create()
    spec = store.read_spec(run.id)
    fake = _Subagents(shared, writes=writes)
    controller = RunController(run, spec, services=EngineServices(subagents=fake))
    asyncio.run(controller.run_to_completion(timeout=30.0))
    status = controller.run.status
    if ends is None:
        assert status is RunStatus.COMPLETE, (status, controller.run.attention)
    else:
        assert status is ends, (status, controller.run.attention)

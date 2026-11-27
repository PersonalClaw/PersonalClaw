"""A self-qa run seals its proof bundle in a folder of its own, in a project or not.

🔴 The defect (Backlog: "The self-qa workflow cannot seal its evidence when its run has no
project"). A self-qa run started outside a project had no folder of its own, and the commit watch
starts every run that way: the watch is an automation, and an automation's fire runs in no
project. Its drive step was told to capture "under the run workspace", but with no folder bound
its helper worked in the shared agent workspace, and the next step, "Seal the proof bundle",
refused with "selfqa-evidence has no run workspace to seal — the engine did not thread a workspace
into the action payload". Every project-less scenario ended at the seal: no evidence artifact, and
neither "Did it pass?" nor "File the finding" ran.

Now the template declares a scratch workspace, so every run, in a project or not, works in a folder
of its own: the drive step captures there, and the seal reads the same folder and registers the
evidence. A workflow that drops the declaration and runs outside a project is refused in words
that say what it lacks and how to give it one.

Driven as the watch starts it: the bundled template run by its controller with no project, the
real seal (``selfqa-evidence``) and the real artifact store under a scratch home, a triage that
routes one commit forward, and step helpers that do what each prompt says.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from personalclaw.action_providers.selfqa_evidence_provider import SelfQaEvidenceActionProvider
from personalclaw.workflows import store
from personalclaw.workflows.bundled_defs import read_template
from personalclaw.workflows.controller import EngineServices, RunController
from personalclaw.workflows.models import RunStatus, WorkflowRun

SHA = "5f1c2ab9d0e4"

#: The PNG signature and a header: a screenshot as the drive step's browser writes one.
_PNG = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"


@pytest.fixture
def shared(tmp_path, monkeypatch) -> Path:
    """A scratch home, and the shared workspace a session with no folder of its own works in."""
    home = tmp_path / "home"
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    monkeypatch.setattr("personalclaw.workflows.leases.config_dir", lambda: home)
    folder = tmp_path / "workspace"
    folder.mkdir()
    monkeypatch.setattr("personalclaw.config.loader.default_workspace_dir", lambda: str(folder))
    monkeypatch.setattr("personalclaw.selfqa.evidence.ffmpeg_available", lambda **_: False)
    return folder


class _Info:
    def __init__(self, agent_id: str, task: str, cwd: str) -> None:
        self.id = agent_id
        self.prompt = task
        self.cwd = cwd
        self.done = False
        self.error = ""
        self.result = ""
        self.reaped = False
        self.agent = ""


class _Helpers:
    """The run's step helpers. The scenario step designs one scenario; the drive step captures a
    screenshot and a recording in the folder it was started in, as its prompt says."""

    def __init__(self, shared: Path) -> None:
        self.shared = shared
        self.infos: dict[str, _Info] = {}
        self.drive_folders: list[str] = []

    def spawn(self, **kw: Any) -> _Info:
        info = _Info(
            f"sub{len(self.infos) + 1}", str(kw.get("task") or ""), str(kw.get("cwd") or "")
        )
        self.infos[info.id] = info
        return info

    def get(self, agent_id: str) -> _Info | None:
        info = self.infos.get(agent_id)
        if info is None or info.done:
            return info
        info.done = True
        if info.prompt.startswith("Design ONE scenario"):
            payload: dict[str, Any] = {
                "scenario_id": "rename-a-chat",
                "title": "Rename a chat from its header",
                "entry_surface": "Chat",
                "steps": ["Open a chat", "Rename it from the header"],
                "mutation": "the chat's title is the new name",
                "backend_check": "GET /api/chat/sessions lists the new title",
                "fault_injection": "",
                "restart_arc": "",
                "growth_assertion": "",
            }
        else:
            # Into the scenario's own folder, which its prompt names under the run's folder.
            named = re.search(r"`(scenarios/[^`]+)`", info.prompt)
            folder = Path(info.cwd or self.shared) / (named.group(1) if named else "")
            self.drive_folders.append(str(folder))
            (folder / "screenshots").mkdir(parents=True, exist_ok=True)
            (folder / "screenshots" / "renamed.png").write_bytes(_PNG)
            (folder / "recording.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42 recording")
            payload = {
                "passed": True,
                "observed": "renamed",
                "failure_detail": "",
                "repro_steps": [],
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


class _Triage:
    """The commit triage: the one commit could be noticed, so it gets a scenario."""

    async def execute(self, cfg, ctx, timeout=30):
        return _result(
            {
                "has_impactful": True,
                "impactful": [
                    {
                        "sha": SHA,
                        "subject": "feat: rename a chat from its header",
                        "rationale": "a user renames chats",
                        "paths": ["web/src/pages/chat/Header.tsx"],
                    }
                ],
            }
        )


def _spec(*, without_workspace: bool = False) -> dict[str, Any]:
    template = read_template("self-qa")
    assert template is not None, "self-qa did not load through the bundled provider"
    spec = template.to_dict()
    if without_workspace:
        spec.pop("workspace", None)
    return spec


def _drive(shared: Path, spec: dict[str, Any]) -> tuple[WorkflowRun, _Helpers, dict[str, Any]]:
    run = store.create(
        WorkflowRun(
            id="",
            workflow_name="self-qa",
            inputs={
                "repo": str(shared),
                "commits": [SHA],
                "base_url": "http://127.0.0.1:10000",
                "fix_branch_enabled": False,
            },
        )
    )
    assert not run.project_id, "vacuity floor: the watch's runs have no project"
    store.write_spec(run.id, spec)
    helpers = _Helpers(shared)
    seal = SelfQaEvidenceActionProvider()
    providers = {"selfqa-triage": _Triage(), "selfqa-evidence": seal}
    services = EngineServices(subagents=helpers, get_provider=lambda name: providers[name])
    controller = RunController(run, spec, services=services)
    asyncio.run(controller.run_to_completion(timeout=30.0))
    return store.get(run.id), helpers, store.read_state(run.id)


def _failures(state: dict[str, Any]) -> str:
    return " ".join(inst.failure.cause_plain for inst in state.values() if inst.failure)


def test_a_run_with_no_project_seals_its_evidence_from_its_own_folder(shared):
    """🔴 Before: the drive step captured into the shared workspace, and the seal refused."""
    run, helpers, state = _drive(shared, _spec())
    assert run.status is RunStatus.COMPLETE, (run.status, run.error_message, _failures(state))
    (folder,) = helpers.drive_folders
    assert folder != str(shared) and Path(folder).is_dir(), "it works in a folder of its own"
    assert not (shared / "recording.mp4").exists(), "nothing is captured into the shared workspace"
    assert str(store.run_dir(run.id)) in folder or run.id in folder, folder
    sealed = json.loads((Path(folder) / "manifest.json").read_text())
    kinds = sorted({entry["kind"] for entry in sealed["files"]})
    assert {"screenshot", "recording"} <= set(kinds), kinds


def test_the_evidence_is_registered_and_holds_the_captures(shared):
    from personalclaw.artifacts.registry import get_provider

    run, helpers, state = _drive(shared, _spec())
    assert run.status is RunStatus.COMPLETE, (run.status, run.error_message, _failures(state))
    (folder,) = helpers.drive_folders
    sealed = json.loads((Path(folder) / "manifest.json").read_text())
    shots = {e["sha256"] for e in sealed["files"] if e["kind"] == "screenshot"}
    assert shots, sealed
    assert get_provider().list(), "no evidence artifact was registered"


def test_a_workflow_that_drops_its_folder_is_refused_in_words_that_say_how_to_give_it_one(shared):
    run, helpers, state = _drive(shared, _spec(without_workspace=True))
    assert run.status is not RunStatus.COMPLETE
    said = _failures(state)
    assert "has no folder of this run's to seal" in said, said[:3000]
    assert '"workspace": {"mode": "scratch"}' in said and "run it in a project" in said, said
    assert "did not thread a workspace" not in said
    (seal,) = [
        i.failure for i in state.values() if i.failure and "to seal" in i.failure.cause_plain
    ]
    assert seal.failure_class.value == "user", "the fix is the workflow's, not the engine's"

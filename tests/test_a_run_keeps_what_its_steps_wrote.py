"""A run's Document panel shows what its steps wrote, and reads only what the run keeps.

The panel read the folders a run owns — its provisioned workspace, its run directory. A project-less
run provisions no workspace, and its steps work in the shared workspace every session defaults to,
so `deep-research` kept its RESEARCH.md where the panel never looked and the panel said the worker
had not written it. Reading the shared folder instead would serve whatever is in it: another
run's RESEARCH.md, or yesterday's. So as each step settles the run keeps its own copy of the
documents that step changed there, and the panel reads the copy, saying whose writing it is and
where it was written.
"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from typing import Any

import pytest

from personalclaw.workflows import deliverable as D
from personalclaw.workflows import run_cockpit, store
from personalclaw.workflows.controller import EngineServices, RunController
from personalclaw.workflows.models import RunStatus, WorkflowRun

WORKFLOW = "deep-research"


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


def _declared() -> str:
    name = D.resolve_name(WORKFLOW).name
    assert name, "vacuity floor: the research template declares no document to look for"
    return name


class _Info:
    def __init__(self, agent_id: str) -> None:
        self.id = agent_id
        self.done = False
        self.error = ""
        self.result = ""
        self.reaped = False
        self.agent = ""
        self.cwd = ""  # spawned with none: the session defaulted to the shared workspace


class _WritesInTheSharedWorkspace:
    """A subagent manager whose one stage writes the run's document where a real one would."""

    def __init__(self, folder: Path, name: str, body: str) -> None:
        self.folder, self.name, self.body = folder, name, body
        self.infos: dict[str, _Info] = {}

    def spawn(self, **kw: Any) -> _Info:
        info = _Info(f"sub{len(self.infos) + 1}")
        self.infos[info.id] = info
        return info

    def get(self, agent_id: str) -> _Info | None:
        info = self.infos.get(agent_id)
        if info is not None and not info.done:
            (self.folder / self.name).write_text(self.body, encoding="utf-8")
            info.done, info.result = True, "done"
        return info


def _run_project_less(manager: Any) -> str:
    spec = {
        "name": WORKFLOW,
        "root": {
            "kind": "sequence",
            "id": "root",
            "children": [
                {"kind": "stage", "id": "research", "config": {"prompt": "Research the topic."}}
            ],
        },
    }
    run = store.create(WorkflowRun(id="", workflow_name=WORKFLOW))
    store.write_spec(run.id, spec)
    controller = RunController(run, spec, services=EngineServices(subagents=manager))
    assert controller.services.cwd == "", "vacuity floor: this run must be project-less"
    status = asyncio.run(controller.run_to_completion(timeout=6.0))
    assert status is RunStatus.COMPLETE
    return run.id


def test_what_a_project_less_runs_step_wrote_reaches_its_panel(shared):
    """🔴 Red before: the step wrote the document in the shared workspace, and the panel, reading
    only the run's own folders, said the worker had not written it."""
    name = _declared()
    run_id = _run_project_less(_WritesInTheSharedWorkspace(shared, name, "# Findings\n"))

    report = run_cockpit.run_deliverable(run_id)["report"]

    assert report["present"] is True, report
    assert report["content"] == "# Findings\n"
    assert report["found_in"] == D.ROOT_KEPT
    assert report["kept_by"] == "research"
    assert report["kept_from"] == str(shared.resolve())


def test_a_document_no_step_of_this_run_touched_is_not_its_document(shared):
    """The shared folder holds other runs' writing too: a document older than the step is left."""
    name = _declared()
    (shared / name).write_text("another run's findings", encoding="utf-8")
    old = time.time() - 3600
    os.utime(shared / name, (old, old))

    class _WritesNothing(_WritesInTheSharedWorkspace):
        def get(self, agent_id: str) -> _Info | None:
            info = self.infos.get(agent_id)
            if info is not None:
                info.done, info.result = True, "done"
            return info

    run_id = _run_project_less(_WritesNothing(shared, name, ""))

    report = run_cockpit.run_deliverable(run_id)["report"]
    assert report["present"] is False
    assert report["absent_reason"] == D.NOT_WRITTEN


class TestKeeping:
    def _run(self) -> Any:
        run = store.create(WorkflowRun(id="", workflow_name=WORKFLOW))
        store.write_spec(run.id, {"root": {"kind": "action"}})
        return run

    def test_a_link_under_the_documents_name_is_never_followed(self, shared, tmp_path):
        """A link in a shared folder named like the document must not carry a file out of it."""
        name = _declared()
        secret = tmp_path / "elsewhere.md"
        secret.write_text("not the run's", encoding="utf-8")
        (shared / name).symlink_to(secret)
        run = self._run()

        kept = D.keep_step_documents(run, None, folder=str(shared), since=1.0, step="research")

        assert kept == []
        assert not (store.run_dir(run.id) / D.KEPT_DIRNAME / name).exists()

    def test_a_name_that_is_not_a_plain_document_file_is_not_kept(self, shared):
        """The name can come from a template: only one plain document file name is ever copied."""
        (shared / "notes").mkdir()
        (shared / "notes" / "RESULT.md").write_text("inside a folder", encoding="utf-8")
        run = self._run()
        for stated in ("notes/RESULT.md", "../RESULT.md", ".hidden.md", "RESULT.sh"):
            spec = {"root": {"kind": "action"}, D.DOCUMENT_KEY: stated}
            assert (
                D.keep_step_documents(run, spec, folder=str(shared), since=1.0, step="s") == []
            ), stated

    def test_a_folder_the_run_owns_is_read_in_place_not_copied(self, shared):
        run = self._run()
        own = store.run_dir(run.id)
        (own / _declared()).write_text("the run's own", encoding="utf-8")

        assert D.keep_step_documents(run, None, folder=str(own), since=1.0, step="s") == []
        assert not (own / D.KEPT_DIRNAME).exists()

    def test_a_later_step_replaces_the_copy(self, shared):
        name = _declared()
        run = self._run()
        for body in ("round one", "round two"):
            (shared / name).write_text(body, encoding="utf-8")
            assert D.keep_step_documents(
                run, None, folder=str(shared), since=1.0, step="research"
            ) == [name]
        assert (store.run_dir(run.id) / D.KEPT_DIRNAME / name).read_text() == "round two"

    def test_a_copy_reports_the_size_of_the_file_it_was_taken_from(self, shared):
        """A document too long to keep whole still reads as truncated, at its real size."""
        name = _declared()
        (shared / name).write_bytes(b"word " * (D.KEEP_MAX_BYTES // 5 + 1000))
        run = self._run()
        D.keep_step_documents(run, None, folder=str(shared), since=1.0, step="research")

        roots = D.run_roots(store.get(run.id))
        document = D.read_document(roots, name)

        assert document.found_in == D.ROOT_KEPT
        assert document.bytes == (shared / name).stat().st_size
        assert document.truncated is True

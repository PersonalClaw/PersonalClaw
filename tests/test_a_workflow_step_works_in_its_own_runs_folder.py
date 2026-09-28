"""A workflow run's steps work in the folder the run owns, whatever kind of run it is.

The engine spawns every step of a run in one folder, and which folder depends on the run:

* a run whose template declares a scratch workspace: a folder of its own under its run directory
  (a compiled batch declares one, so this is every batch run);
* a run whose template declares a worktree: its own git worktree of the codebase its project
  binds;
* a run whose template declares it works in place: the folder its project is bound to, the real
  tree;
* a project's run with none of those: that project's context folder, where the engine keeps a
  project's work so what the run learns stays the project's own.

The spawn working-directory allowlist (``subagent.validate_cwd``) admitted only the workspace and
the folders the owner added, and none of those folders is either. So every step of every such run
was refused (``cwd is not in the workspace ... or under any allowed root``), and the run failed on
its first step. An in-place run was not even pointed at its tree: the engine gave a run its
workspace as the folder to work in only when that workspace was isolated, so an in-place run's
steps worked in the project's context folder.

The allowlist now admits the folder of the run a step belongs to, and nothing broader: the folder
comes from that run's own record (for an in-place run, from its project's record), never from the
request; it counts only when it is the location this code makes for that run; and only a spawn
the engine dispatches for that run gets it.

Driven end to end with the shipped offline provider (``ScriptedProvider``) under a real
``SubagentManager`` and a real ``RunController``: the provisioning, the record, the dispatch and
the allowlist are all the code that runs in the gateway.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest
from test_workflows_stage_usage_end_to_end import (  # noqa: F401 - scripted_home is a fixture
    _ctx_builder,
    _sessions_over,
    scripted_home,
)

from personalclaw.workflows import provisioning, store
from personalclaw.workflows.controller import EngineServices, RunController
from personalclaw.workflows.models import InstanceState, RunStatus, WorkflowRun

STAGE_PATH = "root.children[0]"

#: Every test runs in the offline provider's scratch home: the run store, the project store and
#: the worktrees all live under it.
pytestmark = pytest.mark.usefixtures("scripted_home")


@pytest.fixture(autouse=True)
def _workspace_is_the_homes_own_folder(monkeypatch: pytest.MonkeyPatch) -> None:
    """The workspace the allowlist always admits stays inside the scratch home, where none of the
    folders these runs work in is."""
    monkeypatch.delenv("PERSONALCLAW_WORKSPACE", raising=False)


@pytest.fixture
def codebase(tmp_path: Path) -> Path:
    """A git repository with one commit: the codebase a project binds."""
    repo = tmp_path / "codebase"
    repo.mkdir()
    (repo / "notes.txt").write_text("one\n")
    for args in (("init", "-q"), ("add", "notes.txt"), ("commit", "-qm", "first")):
        subprocess.run(
            ["git", "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgSign=false", *args],
            cwd=repo,
            check=True,
            capture_output=True,
        )
    return repo


def _spec(workspace: dict[str, Any] | None = None) -> dict[str, Any]:
    spec: dict[str, Any] = {
        "name": "run-folder-e2e",
        "root": {
            "kind": "sequence",
            "id": "root",
            "children": [{"kind": "stage", "id": "work", "config": {"prompt": "do the thing"}}],
        },
    }
    if workspace is not None:
        spec["workspace"] = workspace
    return spec


def _project(name: str, workspace_dir: str = "") -> Any:
    from personalclaw.tasks.hierarchy import HierarchyStore

    return HierarchyStore().create_project(name, workspace_dir=workspace_dir)


def _controller(
    spec: dict[str, Any], *, project_id: str = "", cwd: str = ""
) -> tuple[RunController, Any]:
    from personalclaw.llm.scripted import ScriptedProvider
    from personalclaw.subagent import SubagentManager

    manager = SubagentManager(
        sessions=_sessions_over(ScriptedProvider()),
        ctx_builder=_ctx_builder(),
        is_yolo=lambda: True,
    )
    run = store.create(WorkflowRun(id="", workflow_name=spec["name"], project_id=project_id))
    store.write_spec(run.id, spec)
    # The gateway gives the engine no folder of its own (`cwd=""`): the one a step works in is
    # the run's.
    controller = RunController(run, spec, services=EngineServices(subagents=manager, cwd=cwd))
    return controller, manager


def _works_in(controller: RunController, manager: Any, folder: str | Path) -> None:
    """Drive the run, and assert its one step worked in *folder* instead of being refused."""
    status = asyncio.run(controller.run_to_completion(timeout=25.0))
    assert controller.services.cwd == str(folder), "the engine put the step somewhere else"
    inst = controller.instances[STAGE_PATH]
    assert inst.subagent_id, "no step was spawned"
    child = manager.get(inst.subagent_id)
    assert child is not None and child.done, child
    assert not child.error, f"the step was refused: {child.error}"
    assert child.cwd == os.path.realpath(folder), child.cwd
    assert inst.state is InstanceState.DONE, inst.state
    assert status is RunStatus.COMPLETE, status


def _refused(cwd: str | Path, own: str) -> bool:
    """Whether the allowlist refuses *cwd* for a step of the run whose folder is *own*. *cwd*
    exists, so a refusal is the allowlist's and not a missing folder's."""
    from personalclaw.subagent import validate_cwd

    assert Path(cwd).is_dir(), cwd
    _resolved, err = validate_cwd(str(cwd), [], run_workdir=own)
    return bool(err)


def _record_path(run_id: str, path: str | Path) -> None:
    """Rewrite the path the run's record says it was given, as a forged record would."""
    record = store.get(run_id)
    record.extra["workspace"]["path"] = str(path)
    store.save(record)


# ── each kind of run, driven ──


def test_a_step_of_a_scratch_workspace_run_works_in_the_runs_own_folder():
    """🔴 Red before the fix: the step was refused and the run failed on its first step."""
    controller, manager = _controller(_spec({"mode": "scratch"}))
    _works_in(controller, manager, store.run_dir(controller.run.id) / "workspace")


def test_a_step_of_a_projects_run_works_in_the_projects_context_folder():
    """🔴 Red before the fix. Every workflow a project runs without a workspace of its own puts
    its steps here."""
    from personalclaw import projects

    project = _project("Garden Planner")
    controller, manager = _controller(_spec(), project_id=project.id)
    _works_in(controller, manager, projects.context_dir(project.id))


def test_a_step_of_a_worktree_run_works_in_the_runs_own_worktree(codebase):
    """🔴 Red before the fix. The run gets a git worktree of the codebase its project binds."""
    from personalclaw.loop.worktree import worktree_path

    project = _project("Garden Planner", workspace_dir=str(codebase))
    controller, manager = _controller(_spec({"mode": "worktree"}), project_id=project.id)
    _works_in(controller, manager, worktree_path("", controller.run.id, project.id))
    recorded = provisioning.workspace_state(store.get(controller.run.id))
    assert recorded["mode"] == "worktree" and recorded["isolated"] is True, recorded


def test_a_step_of_a_worktree_run_with_no_project_works_in_the_runs_own_worktree(codebase):
    """🔴 Red before the fix. A run with no project branches its worktree from the folder the
    engine was given, under a root keyed by that folder, which its record does not name."""
    from personalclaw.loop.worktree import worktree_path

    controller, manager = _controller(_spec({"mode": "worktree"}), cwd=str(codebase))
    _works_in(controller, manager, worktree_path(str(codebase), controller.run.id))


def test_a_worktree_run_whose_project_binds_no_codebase_works_in_the_projects_context_folder():
    """The worktree could not be made, so the run is not isolated, and the engine puts its step
    in the project's context folder rather than the scratch folder the run fell back to."""
    from personalclaw import projects

    project = _project("Garden Planner")
    controller, manager = _controller(_spec({"mode": "worktree"}), project_id=project.id)
    _works_in(controller, manager, projects.context_dir(project.id))
    recorded = provisioning.workspace_state(store.get(controller.run.id))
    assert recorded["isolated"] is False and recorded["degraded_reason"], recorded
    assert _refused(recorded["path"], provisioning.run_workdir(controller.run.id))


def test_a_step_of_an_in_place_run_works_in_the_tree_its_project_is_bound_to(codebase):
    """🔴 Red before the fix: the engine put the step in the project's context folder, because it
    gave a run its workspace to work in only when the workspace was isolated. In place means the
    project's own tree, and the step is admitted there."""
    project = _project("Garden Planner", workspace_dir=str(codebase))
    controller, manager = _controller(_spec({"mode": "in_place"}), project_id=project.id)
    _works_in(controller, manager, codebase)
    recorded = provisioning.workspace_state(store.get(controller.run.id))
    assert recorded["mode"] == "in_place" and recorded["isolated"] is False, recorded


def test_an_in_place_run_whose_project_binds_no_tree_works_in_the_projects_context_folder():
    """With no tree to work in, the engine keeps the run's steps in the project's context
    folder, and that is the folder they are admitted to."""
    from personalclaw import projects

    project = _project("Garden Planner")
    controller, manager = _controller(_spec({"mode": "in_place"}), project_id=project.id)
    _works_in(controller, manager, projects.context_dir(project.id))


# ── and only that folder ──


def test_a_scratch_run_admits_only_its_own_folder():
    from personalclaw.subagent import _run_workdir_for

    controller, _manager = _controller(_spec({"mode": "scratch"}))
    asyncio.run(controller.run_to_completion(timeout=25.0))
    run_id = controller.run.id
    own = provisioning.run_workdir(run_id)
    assert own == str(store.run_dir(run_id) / "workspace"), own

    other = store.create(WorkflowRun(id="", workflow_name="another-run"))
    elsewhere = store.run_dir(other.id) / "workspace"
    elsewhere.mkdir(parents=True)
    inside = Path(own) / "nested"
    inside.mkdir()
    assert not _refused(own, own) and not _refused(inside, own)
    # Another run's scratch folder, and this run's own directory, where its journal is.
    assert _refused(elsewhere, own) and _refused(store.run_dir(run_id), own)

    # A step of this run is dispatched as `workflow:<run_id>`; nothing else names a run.
    assert _run_workdir_for(f"workflow:{run_id}") == own
    assert _run_workdir_for(run_id) == "" and _run_workdir_for("") == ""
    assert _run_workdir_for(f"workflow:{run_id}:work") == "", "a session key is not a run"
    # A run with neither a workspace nor a project admits nothing, and neither does a record
    # that names a folder which is not the run's own.
    assert provisioning.run_workdir(other.id) == ""
    _record_path(run_id, elsewhere)
    assert provisioning.run_workdir(run_id) == ""


def test_a_worktree_run_admits_only_its_own_worktree(codebase):
    from personalclaw import projects
    from personalclaw.loop.worktree import worktree_path

    project = _project("Garden Planner", workspace_dir=str(codebase))
    controller, _manager = _controller(_spec({"mode": "worktree"}), project_id=project.id)
    asyncio.run(controller.run_to_completion(timeout=25.0))
    run_id = controller.run.id
    own = worktree_path("", run_id, project.id)
    assert provisioning.run_workdir(run_id) == own
    # The codebase the worktree branched from, and the project's context folder, are not where
    # the engine put this run's step.
    assert _refused(codebase, own) and _refused(projects.context_dir(project.id), own)

    other = store.create(WorkflowRun(id="", workflow_name="another-run", project_id=project.id))
    for forged in (worktree_path("", other.id, project.id), codebase, Path(own) / "nested"):
        Path(forged).mkdir(parents=True, exist_ok=True)
        _record_path(run_id, forged)
        assert provisioning.run_workdir(run_id) == "", forged


def test_a_worktree_run_with_no_project_admits_only_its_own_worktree(codebase):
    from personalclaw.loop.worktree import worktree_path

    controller, _manager = _controller(_spec({"mode": "worktree"}), cwd=str(codebase))
    asyncio.run(controller.run_to_completion(timeout=25.0))
    run_id = controller.run.id
    own = worktree_path(str(codebase), run_id)
    assert provisioning.run_workdir(run_id) == own

    other = store.create(WorkflowRun(id="", workflow_name="another-run"))
    # Another run's worktree under the same root, a folder inside this run's, and one that has
    # this run's name but not under a worktrees root.
    for forged in (
        worktree_path(str(codebase), other.id),
        Path(own) / "nested",
        Path(codebase).parent / "worktrees" / run_id,
    ):
        Path(forged).mkdir(parents=True, exist_ok=True)
        _record_path(run_id, forged)
        assert provisioning.run_workdir(run_id) == "", forged


def test_a_projects_run_admits_only_its_own_projects_context_folder(codebase):
    from personalclaw import projects

    mine = _project("Garden Planner", workspace_dir=str(codebase))
    theirs = _project("Reading List")
    controller, _manager = _controller(_spec(), project_id=mine.id)
    asyncio.run(controller.run_to_completion(timeout=25.0))
    own = provisioning.run_workdir(controller.run.id)
    assert own == projects.context_dir(mine.id), own
    assert not _refused(own, own)
    # Another project's context folder, and the codebase this project binds: the engine put
    # this run's step in neither.
    assert _refused(projects.context_dir(theirs.id), own) and _refused(codebase, own)


def test_an_in_place_run_admits_only_its_own_projects_tree(codebase, tmp_path):
    """The tree comes from the project's record: a path forged on the run's record changes
    nothing, a run of the same project that does not work in place is kept to the context
    folder, and another project's in-place run gets that project's tree, not this one."""
    from personalclaw import projects

    mine = _project("Garden Planner", workspace_dir=str(codebase))
    their_tree = tmp_path / "another-tree"
    their_tree.mkdir()
    theirs = _project("Reading List", workspace_dir=str(their_tree))

    in_place, _manager = _controller(_spec({"mode": "in_place"}), project_id=mine.id)
    asyncio.run(in_place.run_to_completion(timeout=25.0))
    run_id = in_place.run.id
    own = provisioning.run_workdir(run_id)
    assert own == str(codebase), own
    assert not _refused(codebase / ".git", own), "a folder inside the tree is in the tree"
    # Another project's tree, and this project's context folder: the engine put this run's
    # step in neither.
    assert _refused(their_tree, own) and _refused(projects.context_dir(mine.id), own)

    # A run of the same project that does not declare in place is kept to the context folder.
    plain, _manager = _controller(_spec(), project_id=mine.id)
    asyncio.run(plain.run_to_completion(timeout=25.0))
    assert provisioning.run_workdir(plain.run.id) == projects.context_dir(mine.id)
    assert _refused(codebase, provisioning.run_workdir(plain.run.id))

    # What the run's own record says it was given does not decide it; the project does.
    _record_path(run_id, their_tree)
    assert provisioning.run_workdir(run_id) == str(codebase)

    other, _manager = _controller(_spec({"mode": "in_place"}), project_id=theirs.id)
    asyncio.run(other.run_to_completion(timeout=25.0))
    assert provisioning.run_workdir(other.run.id) == str(their_tree)
    assert _refused(codebase, provisioning.run_workdir(other.run.id))


def test_a_scratch_workspace_that_is_not_isolated_admits_nothing():
    """The allowlist reads the record's own isolation flag, as the engine does: a workspace that
    could not be made separate is not where the engine put the run's steps."""
    controller, _manager = _controller(_spec({"mode": "scratch"}))
    asyncio.run(controller.run_to_completion(timeout=25.0))
    assert provisioning.run_workdir(controller.run.id), "the control: an isolated one admits"
    record = store.get(controller.run.id)
    record.extra["workspace"]["isolated"] = False
    store.save(record)
    assert provisioning.run_workdir(controller.run.id) == ""

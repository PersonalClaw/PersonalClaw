"""A loop's planner works in a folder of its own, beside the plan it drafts.

The planner's file tools reach its workspace and the folder the walkthrough gives it
(``plan_walkthrough._loop_folder``, which the runner adds to its roots): ``<loop>/planner/``, where
its walkthrough files go (``plan_steps.json``, ``step_artifact.json`` and its STOP file), apart from
the files the gateway keeps for the loop. So its file tools, and an Unattended loop's planner's
shell (`run_bounds`), write its own files and not the loop's plan.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from test_loop_plan_route_lifecycle_guard import _create, _tmp_home  # noqa: F401

from personalclaw import file_scope, run_bounds
from personalclaw.loop import files as loop_files
from personalclaw.loop import plan_walkthrough as pw
from personalclaw.loop import store
from personalclaw.loop.loop import LoopStatus


@pytest.fixture
def planner(tmp_path) -> tuple[Path, Path, list[str]]:
    """A planning loop's folder, its planner's workspace, and the roots its runner gives it."""
    cid = _create(LoopStatus.PLANNING.value)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return loop_files.loop_dir(cid), workspace, [pw._loop_folder(store.get(cid))]


@pytest.mark.parametrize(
    "tool, arguments",
    [
        ("write_file", {"content": "{}"}),
        ("edit_file", {"old_string": "approved", "new_string": "approved"}),
    ],
    ids=["write", "edit"],
)
def test_its_file_tools_do_not_reach_the_plan(planner, tool, arguments):
    folder, workspace, roots = planner
    call = {**arguments, "path": str(folder / "plan_session.json")}
    refused = file_scope.refusal(tool, call, cwd=workspace, extra_roots=roots)
    assert refused is not None, f"{tool} reached {folder / 'plan_session.json'} from {roots}"


def test_its_file_tools_write_its_own_files(planner):
    _folder, workspace, roots = planner
    [own] = roots
    call = {"path": str(Path(own) / pw.ARTIFACT_SENTINEL), "content": "{}"}
    assert file_scope.refusal("write_file", call, cwd=workspace, extra_roots=roots) is None


def test_an_unattended_planners_shell_does_not_write_the_plan(planner):
    folder, workspace, roots = planner
    reach, _within, _scratch = run_bounds.asked_call_reach(
        "",
        "bash",
        "execute",
        {"command": f"printf x > {folder / 'plan_session.json'}"},
        session_key="dashboard:loop-plan-x",
        workspace=str(workspace),
        extra_roots=roots,
        unattended=True,
    )
    assert str(folder / "plan_session.json") in reach.paths


def test_an_unattended_planners_shell_writes_its_own_files(planner):
    _folder, workspace, roots = planner
    [own] = roots
    reach, _within, _scratch = run_bounds.asked_call_reach(
        "",
        "bash",
        "execute",
        {"command": f"printf x > {Path(own) / pw.ARTIFACT_SENTINEL}"},
        session_key="dashboard:loop-plan-x",
        workspace=str(workspace),
        extra_roots=roots,
        unattended=True,
    )
    assert not reach.paths

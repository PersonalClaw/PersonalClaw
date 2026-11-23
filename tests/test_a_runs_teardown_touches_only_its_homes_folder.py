"""A run's deletion and its pruning act only on the folder this home made for it.

🔴 The defect (Backlog, private). A run with an isolated workspace keeps the folder it worked in as
an absolute path on its record, and its deletion, or its pruning by run retention, read that path
and ran the workspace's teardown command there, committed outstanding work there and removed the
folder. A replace restore of one home's snapshot into a second home on the same machine brings
the finished runs back with paths into the first home, so deleting one in the second home (or
letting retention prune it) ran its teardown in the first home's folder and deleted it, uncommitted
work included, with nothing saying so.

Now teardown resolves the recorded folder through the check the working-folder allowlist uses
(`provisioning._own_isolated_dir`): a folder that is not the one this home makes for the run gets
no teardown step, no commit and no removal, nor does the container that worked in it, and the
teardown says so in one line, which the run's journal keeps.

🔴 The same held for a run that worked in place (Backlog, private): its record names its project's
tree, and its deletion or pruning ran the teardown command in whatever tree the record named,
though it removed nothing. Restored into a second home whose project binds another folder, or
that has no such project, the run's teardown ran in the first home's tree. Now an in-place
record's folder is the run's only when it is the tree its project binds in this home
(`provisioning._is_own_tree`), the one folder the allowlist admits for its steps
(`provisioning.run_workdir`).
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from personalclaw.workflows import journal as journal_mod
from personalclaw.workflows import provisioning, service, store
from personalclaw.workflows.models import RunStatus, WorkflowRun
from personalclaw.workflows.workspace import Mode, WorkspaceSpec

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class _Homes:
    """Two homes under one account on one machine, and which one is active."""

    def __init__(self, tmp_path: Path, monkeypatch) -> None:
        self.first = tmp_path / "first"
        self.second = tmp_path / "second"
        self.first.mkdir()
        self.second.mkdir()
        self.active = self.first
        self._mp = monkeypatch
        monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: self.active)
        self.use(self.first)

    def use(self, home: Path) -> None:
        self.active = home
        self._mp.setenv("PERSONALCLAW_HOME", str(home))


@pytest.fixture
def homes(tmp_path, monkeypatch) -> _Homes:
    return _Homes(tmp_path, monkeypatch)


@pytest.fixture
def ran(monkeypatch) -> list[tuple[str, str]]:
    """Every teardown step run, and where."""
    steps: list[tuple[str, str]] = []

    async def spy(command, cwd, **_kw):
        steps.append((str(command), str(cwd)))
        return True, "ok"

    monkeypatch.setattr(provisioning, "run_step", spy)
    return steps


async def _finished_run(name: str = "wsp", created: str = "") -> tuple[WorkflowRun, Path]:
    """A finished run of the active home with a scratch workspace and a teardown command, and
    its folder holding uncommitted work."""
    run = store.create(
        WorkflowRun(id="", workflow_name=name, status=RunStatus.COMPLETE, created_at=created)
    )
    spec = WorkspaceSpec(mode=Mode.SCRATCH, teardown="compose-down")
    made = await provisioning.provision(spec, run_id=run.id, workspace_dir="")
    provisioning.stamp_run(run, made, spec)
    store.save(run)
    folder = Path(made.path)
    (folder / "draft.md").write_text("work nobody committed\n")
    return run, folder


def _restored(homes: _Homes, run: WorkflowRun) -> WorkflowRun:
    """*run*'s record as a replace restore brings it into the second home: as written."""
    homes.use(homes.second)
    store.save(run)
    return store.get(run.id)


async def test_deleting_a_restored_run_leaves_the_other_homes_folder(homes, ran):
    """🔴 Before: the teardown ran in the first home's folder and removed it."""
    run, folder = await _finished_run()
    restored = _restored(homes, run)
    out = await service.delete_run(restored.id)
    assert out["ok"] is True and store.get(restored.id) is None, "the second home's row goes"
    assert folder.is_dir() and (folder / "draft.md").read_text() == "work nobody committed\n"
    assert ran == [], "no teardown step ran in another home's folder"
    (said,) = out["teardown"]["failed"]
    assert str(folder) in said and "not a folder this home made for this run" in said, said
    assert out["teardown"]["removed"] is False and out["teardown"]["committed"] is False


async def test_pruning_restored_runs_leaves_the_other_homes_folders(homes, ran):
    from personalclaw.workflows.watchdog import prune_runs

    made = [await _finished_run("ret", f"2026-01-0{i + 1}T00:00:00Z") for i in range(3)]
    for run, _folder in made:
        _restored(homes, run)
    assert await prune_runs("ret", keep=1) == 2
    assert all(folder.is_dir() and (folder / "draft.md").is_file() for _run, folder in made)
    assert ran == []


async def test_the_journal_says_it_left_the_folder(homes, ran):
    run, folder = await _finished_run()
    restored = _restored(homes, run)
    torn = await service.teardown_workspace(restored, reason="delete")
    rows = journal_mod.journal_records(restored.id, kinds={journal_mod.WORKSPACE_TEARDOWN})
    assert rows, "a teardown is journaled"
    said = (rows[-1].get("workspace") or {}).get("failed") or []
    assert any(str(folder) in line for line in said), rows[-1]
    assert torn.failed == list(said)


async def test_a_container_that_worked_in_another_homes_folder_is_left(homes, ran, monkeypatch):
    from personalclaw.workflows.container_env import CliContainerBackend

    removed: list[str] = []

    async def remove(self, container_id):
        removed.append(container_id)
        raise AssertionError("a container another home's run worked in was removed")

    monkeypatch.setattr(CliContainerBackend, "remove", remove)
    run, folder = await _finished_run()
    run.extra[provisioning.WORKSPACE_KEY].update(
        {"container_id": "c0ffee", "container_backend": "docker"}
    )
    store.save(run)
    restored = _restored(homes, run)
    torn = await provisioning.teardown(restored)
    assert removed == [] and folder.is_dir()
    (said,) = torn.failed
    assert "the container that worked in it was left as it is" in said, said


async def test_this_homes_own_scratch_folder_is_still_torn_down(homes, ran):
    """The control: the guard refuses another home's folder, never the run's own."""
    homes.use(homes.second)
    run, folder = await _finished_run()
    out = await service.delete_run(run.id)
    assert out["teardown"]["failed"] == [] and out["teardown"]["removed"] is True
    assert ran == [("compose-down", str(folder))]
    assert not folder.is_dir()


@pytest.mark.parametrize("stamped", ["with its mode", "before records named a mode"])
async def test_this_homes_own_worktree_is_still_torn_down(homes, ran, tmp_path, stamped):
    import subprocess

    homes.use(homes.second)
    repo = tmp_path / "repo"
    repo.mkdir()
    for args in (
        ["init", "-q"],
        ["config", "user.email", "t@t"],
        ["config", "user.name", "T"],
        ["commit", "-q", "--allow-empty", "-m", "init"],
    ):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)
    run = store.create(WorkflowRun(id="", workflow_name="wt", project_id="p-1"))
    spec = WorkspaceSpec(mode=Mode.WORKTREE, teardown="compose-down")
    made = await provisioning.provision(
        spec, run_id=run.id, project_id="p-1", workspace_dir=str(repo)
    )
    assert not made.degraded_reason, made.degraded_reason
    provisioning.stamp_run(run, made, spec)
    if stamped != "with its mode":
        run.extra[provisioning.WORKSPACE_KEY].pop("mode")
    torn = await provisioning.teardown(run, workspace_dir=str(repo))
    assert torn.failed == [] and torn.removed is True
    assert ran == [("compose-down", made.path)] and not os.path.isdir(made.path)


async def test_a_record_that_reaches_its_own_folder_through_a_link_is_its_own(homes, tmp_path):
    """The proof reads where a record's path leads, not how it is spelled: a link to the run's own
    scratch folder is that folder, and a link to another home's folder is not this run's."""
    homes.use(homes.second)
    run, folder = await _finished_run()
    state = provisioning.workspace_state(run)
    to_own = tmp_path / "to-own"
    os.symlink(folder, to_own)
    assert provisioning._own_isolated_dir(run, str(to_own), state) == str(to_own)
    elsewhere = homes.first / "workflows" / "runs" / run.id / "workspace"
    elsewhere.mkdir(parents=True)
    to_other = tmp_path / "to-other"
    os.symlink(elsewhere, to_other)
    assert provisioning._own_isolated_dir(run, str(to_other), state) == ""


@pytest.mark.parametrize("project_id", ["p-1", ""], ids=["a project's run", "a project-less run"])
def test_another_runs_worktree_is_not_this_runs(homes, project_id):
    """A worktree record is this run's only at the worktree this home makes for THIS run: the
    same place made for another run, in the same home, is another run's."""
    from personalclaw.config.loader import config_dir
    from personalclaw.loop import worktree as loop_worktree

    homes.use(homes.second)
    run = WorkflowRun(id="r-mine", workflow_name="wt", project_id=project_id)
    state = {"mode": Mode.WORKTREE.value, "isolated": True}
    if project_id:
        mine = loop_worktree.worktree_path("", run.id, project_id)
        other = loop_worktree.worktree_path("", "r-other", project_id)
    else:
        root = config_dir() / "code" / "worktrees" / "0123456789ab"
        mine, other = str(root / run.id), str(root / "r-other")
    os.makedirs(mine)
    os.makedirs(other)
    assert provisioning._own_isolated_dir(run, mine, {**state, "path": mine}) == mine
    assert provisioning._own_isolated_dir(run, other, {**state, "path": other}) == ""


# ── a run that worked in place ──


def _project(tree: Path, name: str = "proj") -> str:
    """A project of the active home bound to *tree*."""
    from personalclaw.tasks.hierarchy import HierarchyStore

    tree.mkdir(exist_ok=True)
    return HierarchyStore().create_project(name, workspace_dir=str(tree)).id


async def _finished_in_place_run(project_id: str, created: str = "") -> WorkflowRun:
    """A finished run of the active home that worked in its project's tree, with a teardown."""
    run = store.create(
        WorkflowRun(
            id="",
            workflow_name="inp",
            status=RunStatus.COMPLETE,
            project_id=project_id,
            created_at=created,
        )
    )
    spec = WorkspaceSpec(mode=Mode.IN_PLACE, teardown="compose-down")
    made = await provisioning.provision(
        spec,
        run_id=run.id,
        project_id=project_id,
        workspace_dir=provisioning.project_tree(project_id),
    )
    assert made.path and not made.isolated, made
    provisioning.stamp_run(run, made, spec)
    store.save(run)
    return run


def _restored_with(homes: _Homes, run: WorkflowRun, *, projects: bool) -> WorkflowRun:
    """*run*'s record restored into the second home, with the first home's projects or not."""
    if projects:
        shutil.copytree(homes.first / "projects", homes.second / "projects", dirs_exist_ok=True)
    return _restored(homes, run)


def _bind(project_id: str, tree: Path) -> None:
    """Point the active home's project *project_id* at *tree*."""
    from personalclaw.tasks.hierarchy import HierarchyStore

    tree.mkdir(exist_ok=True)
    assert HierarchyStore().update_project(project_id, workspace_dir=str(tree)) is not None


@pytest.mark.parametrize("here", ["its project binds another folder", "it has no such project"])
async def test_deleting_a_restored_in_place_run_runs_nothing_in_the_tree_it_names(
    homes, ran, tmp_path, here
):
    """🔴 Before: the teardown ran in the first home's tree."""
    first_tree = tmp_path / "first-tree"
    run = await _finished_in_place_run(_project(first_tree))
    restored = _restored_with(homes, run, projects=here.startswith("its project"))
    if here.startswith("its project"):
        _bind(run.project_id, tmp_path / "second-tree")
    out = await service.delete_run(restored.id)
    assert out["ok"] is True and store.get(restored.id) is None, "the second home's row goes"
    assert ran == [], "no teardown step ran in a tree this home does not bind for the run"
    (said,) = out["teardown"]["failed"]
    assert str(first_tree) in said, said
    assert "not the folder this run's project binds in this home" in said, said
    assert out["teardown"]["removed"] is False and first_tree.is_dir()


async def test_pruning_restored_in_place_runs_runs_nothing_in_the_tree_they_name(
    homes, ran, tmp_path
):
    from personalclaw.workflows.watchdog import prune_runs

    project_id = _project(tmp_path / "first-tree")
    made = [
        await _finished_in_place_run(project_id, f"2026-01-0{i + 1}T00:00:00Z") for i in range(3)
    ]
    for run in made:
        _restored_with(homes, run, projects=False)
    assert await prune_runs("inp", keep=1) == 2
    assert ran == []


@pytest.mark.parametrize("spelled", ["as its project binds it", "through a link to it"])
async def test_this_homes_own_in_place_run_still_runs_its_teardown(homes, ran, tmp_path, spelled):
    """The control: the guard refuses a tree this home does not bind for the run, never the tree
    its project binds here, however the record spells it. Nothing is removed: it is the real
    tree."""
    homes.use(homes.second)
    tree = tmp_path / "tree"
    run = await _finished_in_place_run(_project(tree))
    if spelled != "as its project binds it":
        link = tmp_path / "to-tree"
        os.symlink(tree, link)
        run.extra[provisioning.WORKSPACE_KEY]["path"] = str(link)
        store.save(run)
    recorded = provisioning.workspace_state(store.get(run.id))["path"]
    out = await service.delete_run(run.id)
    assert out["teardown"]["failed"] == [], out["teardown"]
    assert ran == [("compose-down", recorded)]
    assert out["teardown"]["removed"] is False and tree.is_dir()

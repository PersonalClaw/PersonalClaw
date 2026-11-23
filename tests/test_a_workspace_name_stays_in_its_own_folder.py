"""A named workspace works in a folder of its own under ``workspaces/``, or nowhere.

🔴 The defect (CORE, on _DtBI). A named workspace's folder is its name with every character but
letters, digits, ``-``, ``_`` and ``.`` replaced, under the workflows store's ``workspaces/``. So
``.`` and ``..`` survived as folder names: a workflow declaring ``workspace: {mode: scratch, name:
".."}`` ran its steps, and its setup and teardown commands, in the workflows store itself, beside
every run's records, dropped files and workspaces, other chats' among them; ``.`` was the folder
every named workspace shares. Nothing refused it at parse or at save: the workspace block is no
posture a save asks the owner about, so the editor's save route wrote one with no ask, for her and
for an app granted the workflows permission alike (an agent's own save tools hand on no workspace
block). And a run's teardown proof (``provisioning._own_isolated_dir``) read a record naming
``..`` as the run's own folder.

Now a name whose folder would be one of those (``..``, ``.``, or any name starting with ``.``) is a
fatal parse issue, so the run is refused before it starts; ``scratch_location`` raises for it, and
for a folder under ``workspaces/`` that resolves elsewhere, and every caller reads that as no folder
of the run's. A save of one is refused too, at the write every door makes, so whoever saves it
reads why.
"""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from personalclaw.workflows import provisioning, store
from personalclaw.workflows.workspace import parse_workspace

pytestmark = pytest.mark.security


@pytest.fixture(autouse=True)
def _isolated_store(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / "workflows").mkdir(parents=True)
    monkeypatch.setattr("personalclaw.workflows.store.workflows_dir", lambda: home / "workflows")
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: home)
    return home


ESCAPING = ["..", ".", ".hidden", "../runs", "..."]


@pytest.mark.parametrize("name", ESCAPING)
def test_a_name_that_leaves_its_folder_is_refused_at_parse(name):
    """🔴 Before: no issue at all, and the run worked in the store or the shared folder."""
    _spec, issues = parse_workspace({"mode": "scratch", "name": name})
    (issue,) = [i for i in issues if i.code == "workspace_name_escapes"]
    assert issue.fatal and "names no folder of its own" in issue.message, issue


@pytest.mark.parametrize("name", ESCAPING)
def test_its_folder_is_never_handed_out(name):
    """🔴 Before: `..` was the workflows store itself, and `_scratch_dir` made the run work there."""
    with pytest.raises(ValueError):
        provisioning.scratch_location("r-1", name)
    assert provisioning._scratch_dir("r-1", name, None) == ""


@pytest.mark.parametrize("name", ["build", "a.b", "release-2", "x/../y"])
def test_a_name_of_its_own_still_gets_its_folder(name):
    """The control: each lands in a folder of its own directly under `workspaces/`."""
    _spec, issues = parse_workspace({"mode": "scratch", "name": name})
    assert not [i for i in issues if i.code == "workspace_name_escapes"], issues
    folder = Path(provisioning._scratch_dir("r-1", name, None))
    assert folder.is_dir() and folder.parent == store.workflows_dir() / "workspaces"


def test_a_folder_that_is_a_link_elsewhere_is_never_handed_out(tmp_path):
    shared = store.workflows_dir() / "workspaces"
    shared.mkdir(parents=True, exist_ok=True)
    os.symlink(store.workflows_dir(), shared / "planted")
    with pytest.raises(ValueError):
        provisioning.scratch_location("r-1", "planted")
    assert provisioning._scratch_dir("r-1", "planted", None) == ""


def test_a_run_whose_record_names_the_store_is_not_its_own():
    """The teardown proof reads the record's name through `scratch_location`: a record naming `..`
    with the store as its path is another folder, so its teardown runs nothing there."""
    run = SimpleNamespace(id="r-1", project_id="")
    state = {"mode": "scratch", "name": "..", "isolated": True, "path": str(store.workflows_dir())}
    assert provisioning._own_isolated_dir(run, state["path"], state) == ""
    out = provisioning.TornDown()
    assert provisioning._another_homes_folder(run, state, out) is True
    assert out.failed and "nothing ran there" in out.failed[0], out.failed


@pytest.mark.asyncio
async def test_a_run_declaring_one_is_refused_before_it_starts():
    """Through the real run start: the run fails naming the refusal, and no folder is made."""
    from personalclaw.workflows import run_start
    from personalclaw.workflows.controller import EngineServices, RunController
    from personalclaw.workflows.models import RunStatus, WorkflowRun

    spec = {
        "name": "dot-dot",
        "workspace": {"mode": "scratch", "name": "..", "setup": "touch planted"},
        "root": {"kind": "transform", "id": "t", "config": {"expr": {"ok": True}}},
    }
    run = store.create(WorkflowRun(id="", workflow_name="dot-dot"))
    store.write_spec(run.id, spec)
    ctl = RunController(run, spec, services=EngineServices())
    assert await run_start.provision_workspace(ctl) is False
    assert ctl.run.status == RunStatus.FAILED
    assert "workspace declaration refused" in ctl.run.error_message
    assert "names no folder of its own" in ctl.run.error_message
    assert not (store.workflows_dir() / "planted").exists()


@pytest.fixture
def providers():
    """The user's own definitions, as the dashboard saves them."""
    from personalclaw.workflows import defs as defs_mod
    from personalclaw.workflows.native_defs import NativeWorkflowDefProvider

    provider = NativeWorkflowDefProvider()
    added = defs_mod.get_provider(provider.name) is None
    if added:
        defs_mod.register_provider(provider)
    yield
    if added:
        defs_mod.unregister_provider(provider.name)


ROOT = {"kind": "transform", "id": "t", "config": {"expr": {"ok": True}}}


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["..", ".", ".hidden"])
async def test_a_save_of_one_is_refused_where_its_writer_reads_the_answer(providers, name):
    """🔴 Before: it saved, an app's save included, with no ask, and only its run was refused.
    Refused now at the write every door makes (`service._write_definition`), in words the writer
    reads, and nothing is written."""
    from personalclaw.workflows import service, versions
    from personalclaw.workflows.native_defs import defs_root

    fields = {"name": "dot-dot", "root": ROOT, "workspace": {"mode": "scratch", "name": name}}
    said = await service.author_def(**fields)
    assert said.get("ok") is False and said.get("code") == "WF_DEF_WORKSPACE_REFUSED", said
    assert "names no folder of its own" in said.get("error", said.get("message", "")), said
    app = await service.author_def(**fields, saved_by=versions.APP)
    assert app.get("code") == "WF_DEF_WORKSPACE_REFUSED", app
    assert not (defs_root() / "dot-dot").exists(), "nothing was written"


@pytest.mark.asyncio
async def test_a_save_with_a_name_of_its_own_still_saves(providers):
    from personalclaw.workflows import service

    fields = {"name": "own-folder", "root": ROOT, "workspace": {"mode": "scratch", "name": "build"}}
    said = await service.author_def(**fields)
    assert said.get("ok") is True, said

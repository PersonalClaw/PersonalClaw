"""A loop's detail names the directory its worker's own files land in (`work_dir`).

Measured 2026-09-25: a Goal loop wrote `packing.md` into its working directory while the cockpit
said "No outputs saved yet" — the view carried `files_dir` (the loop's engine-file dir) and nothing
that named where a goal/general worker actually writes, which is `loop.effective_dir`: the bound
workspace, the project's context dir, or the workspace root. The cockpit now renders this field with
an "open it in Files" link; this pins that the field is the SAME resolver the supervisor's
ground-truth checks read.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from personalclaw.loop import store
from personalclaw.loop.loop import Loop, effective_dir


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr("personalclaw.loop.files.config_dir", lambda: tmp_path)
    ws = tmp_path / "workspace"
    ws.mkdir()
    monkeypatch.setenv("PERSONALCLAW_WORKSPACE", str(ws))
    return tmp_path


def test_a_project_less_goal_loop_works_in_the_workspace_root(tmp_path: Path) -> None:
    loop = store.create(Loop(id="", name="Packing", kind="goal", task="write a packing note"))
    view = store.get_redacted(loop.id)
    assert view is not None
    assert view["work_dir"] == effective_dir(store.get(loop.id))
    assert Path(view["work_dir"]).resolve() == (tmp_path / "workspace").resolve()
    # …which is NOT the loop's own engine-file dir, the only directory the view used to name.
    assert view["work_dir"] != view["files_dir"]


def test_a_bound_workspace_is_the_work_dir(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    loop = store.create(
        Loop(id="", name="Fix", kind="goal", task="tidy the readme", workspace_dir=str(repo))
    )
    assert store.get_redacted(loop.id)["work_dir"] == str(repo)

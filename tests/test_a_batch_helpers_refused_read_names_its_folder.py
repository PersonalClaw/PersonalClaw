"""A batch helper's refused read says why: the folder it works in, and how to let it read yours.

🔴 Red before: a subagent_run of two or more tasks runs as one batch, whose helpers work in their
run's own folder. Given no ``cwd``, a helper's file tools reach that folder and no other of the
chat's, so its ``read_file`` of a file in the workspace is refused by the file tools' own control,
``file_scope``. The refusal said the path was "outside every folder the file tools reach: the
workspace, …" of a path that IS in the workspace, and the helper ended "every tool call it made
was refused — read_file: it was refused", naming no reason. Seen on 2026-11-25 in a three-task
batch with no ``cwd``, whose two helpers that read the chat's folder failed; a single-task helper
an hour before, which is not a batch and works in the workspace, had read the same folder.

The reach itself is unchanged: a batch's helpers still read only their own folder and what the
batch was given. Only the words change.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from personalclaw import file_scope, subagent_tier
from personalclaw.agents.native.builtin_tools import NativeBuiltinToolProvider
from personalclaw.llm.events import TOOL_META_REFUSED_BY

CHECKLIST = "orb_demo/states_round2.md"


@pytest.fixture
def folders(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """The workspace, holding the chat's checklist, and a batch run's own folder beside it."""
    workspace = tmp_path / "workspace"
    (workspace / "orb_demo").mkdir(parents=True)
    (workspace / CHECKLIST).write_text("- the orb\n", encoding="utf-8")
    run_folder = tmp_path / "runs" / "batch-1" / "scratch"
    run_folder.mkdir(parents=True)
    monkeypatch.setenv("PERSONALCLAW_WORKSPACE", str(workspace))
    return workspace, run_folder


async def _read(cwd: Path, path: Path) -> tuple[str, str]:
    provider = NativeBuiltinToolProvider(cwd, session_key="subagent:helper")
    refused = await provider.preflight("read_file", {"path": str(path)})
    assert refused is not None, "the read was not refused"
    return str((refused.metadata or {}).get(TOOL_META_REFUSED_BY)), str(refused.error)


@pytest.mark.asyncio
async def test_a_helpers_refused_read_of_the_workspace_names_the_folder_it_works_in(folders):
    workspace, run_folder = folders
    control, error = await _read(run_folder, workspace / CHECKLIST)
    assert control == "file_scope"
    assert "this session's own folder (" in error and str(run_folder.name) in error, error
    assert "reach: the workspace" not in error, error


@pytest.mark.asyncio
async def test_a_helper_whose_every_read_is_refused_ends_saying_why_and_what_lets_it(folders):
    workspace, run_folder = folders
    control, _ = await _read(run_folder, workspace / CHECKLIST)
    tally = subagent_tier.CallTally()
    tally.result("c1", "read_file", {TOOL_META_REFUSED_BY: control}, grant_said="")
    ended = tally.verdict()
    assert "it was refused" not in ended, ended
    assert "outside the folders this helper's file tools reach" in ended
    assert "start the batch with cwd" in ended


def test_each_file_tool_control_is_named_in_plain_words():
    for control in ("file_scope", "owner_only", file_scope.MEMORY_WITHHELD):
        tally = subagent_tier.CallTally()
        tally.result("c1", "read_file", {TOOL_META_REFUSED_BY: control}, grant_said="")
        assert "it was refused" not in tally.verdict(), control


@pytest.mark.asyncio
async def test_a_session_in_the_workspace_still_names_the_workspace(folders, tmp_path):
    workspace, _ = folders
    outside = tmp_path / "elsewhere.txt"
    outside.write_text("x\n", encoding="utf-8")
    _, error = await _read(workspace, outside)
    assert "reach: the workspace, the allowed working directories" in error, error


@pytest.mark.asyncio
async def test_a_helpers_refused_change_in_the_workspace_names_the_folder_it_works_in(folders):
    """The change refusal names the helper's own folder too, not "may change: the workspace"."""
    workspace, run_folder = folders
    provider = NativeBuiltinToolProvider(run_folder, session_key="subagent:helper")
    refused = await provider.preflight(
        "write_file", {"path": str(workspace / "orb_demo" / "batch_marker.txt"), "content": "x"}
    )
    assert (
        refused is not None and (refused.metadata or {}).get(TOOL_META_REFUSED_BY) == "file_scope"
    )
    assert "may change: this session's own folder (" in str(refused.error), refused.error
    assert "may change: the workspace" not in str(refused.error)

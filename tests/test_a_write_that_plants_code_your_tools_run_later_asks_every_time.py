"""A write that plants code your own tools run later asks you every time.

An agent could write the editor settings a project keeps (``.vscode/settings.json``, a
``.code-workspace`` file, ``.zed/settings.json``), which name the programs your editor starts when
you open the folder: an interpreter, a formatter, a terminal. Under Trust, YOLO or a standing grant
the write ran unasked, and your editor later ran what it named, as you.

Now such a write is put to you every time, whatever grant stands (``run_bounds.put_to_a_person``),
by every write path: a shell command's redirect, ``tee``, ``cp``, ``mv`` and ``ln``, the native
``write_file`` and ``edit_file``, and an agent CLI's edit. Its card says which file and what runs
it, and unattended work, with nobody to ask, is refused it. A read asks nothing, and an ordinary
project file is written as before (``persistence_paths``).
"""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

from personalclaw import run_bounds

SETTINGS = ".vscode/settings.json"


@pytest.fixture
def project(tmp_path):
    """A repository the chat works in, with an ordinary script beside where the settings go."""
    root = tmp_path / "project"
    (root / ".git").mkdir(parents=True)
    (root / "scripts").mkdir()
    return root


def _shell(command: str) -> SimpleNamespace:
    return SimpleNamespace(
        title="bash", tool_kind="", tool_input={"command": command}, risk_level="caution"
    )


def _native(tool: str, path: str) -> SimpleNamespace:
    return SimpleNamespace(
        title=tool, tool_kind="", tool_input={"path": path, "content": "{}"}, risk_level="caution"
    )


def _cli_edit(path: str) -> SimpleNamespace:
    """An agent CLI's edit, which names its file by absolute path (``acp.translate``)."""
    return SimpleNamespace(
        title="Edit settings", tool_kind="edit", tool_input="{}", risk_level="", named_files=(path,)
    )


WRITES = {
    "a redirect": lambda root: _shell(f"echo '{{}}' > {SETTINGS}"),
    "tee": lambda root: _shell(f"echo '{{}}' | tee {SETTINGS}"),
    "cp": lambda root: _shell(f"cp /tmp/theirs.json {SETTINGS}"),
    "mv": lambda root: _shell("mv /tmp/theirs.json app.code-workspace"),
    "ln": lambda root: _shell("ln -s /tmp/theirs.json .zed/settings.json"),
    "cp into the folder": lambda root: _shell("cp src/settings.json .vscode/"),
    "mv into the folder": lambda root: _shell("mv src/settings.json .vscode/"),
    "ln -sf into the folder": lambda root: _shell("ln -sf ../src/settings.json .vscode/"),
    "cp -t the folder": lambda root: _shell("cp -t .vscode src/settings.json"),
    "write_file": lambda root: _native("write_file", SETTINGS),
    "edit_file, named in another case": lambda root: _native("edit_file", ".VSCode/Settings.json"),
    "an agent CLI's edit": lambda root: _cli_edit(str(root / SETTINGS)),
}


@pytest.mark.parametrize("how", list(WRITES))
def test_each_write_path_is_put_to_you_whatever_grant_stands(project, how):
    """🔴 Red before: the write ran on Trust, YOLO or a standing grant, unasked."""
    event = WRITES[how](project)
    assert run_bounds.put_to_a_person(event, session_key="dashboard:chat-1", cwd=str(project))


def test_a_copy_into_the_folder_named_bare_asks_when_it_is_one(project):
    """🔴 Red before: what lands in a folder is the folder's entry of the source's name, which the
    command names only by the folder. Named bare, the folder on disk says it is one."""
    copy = _shell("cp src/settings.json .vscode")
    assert not run_bounds.put_to_a_person(copy, session_key="s", cwd=str(project))  # a file
    (project / ".vscode").mkdir()
    assert run_bounds.put_to_a_person(copy, session_key="s", cwd=str(project))


def test_a_copy_reads_where_it_lands_and_compares_as_before():
    from dataclasses import replace

    from personalclaw.command_effects import command_effects

    assert command_effects("cp a.json b.json .vscode/").into == {
        (".vscode/", "a.json"),
        (".vscode/", "b.json"),
    }
    assert command_effects("cp -t .vscode a.json").into == {(".vscode/", "a.json")}
    copy = command_effects("cp a b")
    assert copy.into and replace(copy, into=frozenset()) == copy  # what lands is not compared


def test_its_card_says_which_file_and_what_runs_it(project):
    """🔴 Red before: the card said nothing of it."""
    event = _native("write_file", SETTINGS)
    note = run_bounds.event_note(event, "dashboard:chat-1", cwd=str(project))
    from personalclaw.persistence_paths import EDITOR_SETTINGS

    assert note == f"This writes {SETTINGS}, which {EDITOR_SETTINGS.what}; it asks every time."


def test_unattended_native_work_is_refused_it(project):
    """🔴 Red before: a write inside the run's own folder was inside its bounds."""
    reach, within = run_bounds.tool_call_reach(
        "caution",
        "write_file",
        {"path": SETTINGS, "content": "{}"},
        session_key="cron:t1",
        cwd=str(project),
        roots=lambda: [str(project)],
    )
    meta: dict = {}
    check = run_bounds.NativeCheck(reach, True, "write_file", "cron:t1", tuple(within))
    refused = check.refuse(meta)
    assert refused and SETTINGS in refused and "only the user changes" in refused
    assert meta["ok"] is False


def test_unattended_agent_cli_work_is_refused_it(project):
    """🔴 Red before: the same, for an agent CLI's shell and its edit."""
    for event in (_shell(f"echo '{{}}' > {SETTINGS}"), _cli_edit(str(project / SETTINGS))):
        reach, within, scratch = run_bounds.asked_call_reach(
            event.risk_level,
            event.title,
            event.tool_kind,
            event.tool_input,
            session_key="cron:t1",
            workspace=str(project),
            unattended=True,
            named=run_bounds.named_files(event),
        )
        assert reach and SETTINGS in run_bounds.refusal(reach, within, scratch=scratch)


def test_an_attended_native_write_goes_to_its_host_whatever_its_policy(project):
    """🔴 Red before: the native runtime's own YOLO answered it, so no host was asked."""
    reach, _ = run_bounds.tool_call_reach(
        "caution", "write_file", {"path": SETTINGS}, session_key="s", cwd=str(project)
    )
    assert reach.asks


def test_a_link_to_the_settings_is_one_too(project):
    (project / ".vscode").mkdir()
    (project / SETTINGS).write_text("{}", encoding="utf-8")
    os.symlink(project / SETTINGS, project / "cfg.json")
    event = _native("write_file", "cfg.json")
    assert run_bounds.put_to_a_person(event, session_key="dashboard:chat-1", cwd=str(project))


def test_the_repositorys_settings_count_from_a_folder_inside_it(project):
    event = _native("write_file", f"../{SETTINGS}")
    assert run_bounds.put_to_a_person(event, session_key="s", cwd=str(project / "scripts"))


@pytest.mark.parametrize(
    "event",
    [
        _shell(f"cat {SETTINGS}"),
        SimpleNamespace(
            title="read_file", tool_kind="", tool_input={"path": SETTINGS}, risk_level="safe"
        ),
        _shell("echo 'make' > scripts/build.sh"),
        _native("write_file", "scripts/build.sh"),
        _native("write_file", "vendor/pkg/.vscode/settings.json"),
    ],
    ids=["shell read", "native read", "script", "native script", "settings no editor opens"],
)
def test_a_read_and_an_ordinary_file_ask_nothing_of_it(project, event):
    assert not run_bounds.put_to_a_person(event, session_key="dashboard:chat-1", cwd=str(project))

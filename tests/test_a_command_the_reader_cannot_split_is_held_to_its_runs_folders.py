"""A write in a command the shell reader can't split is held to an unattended run's folders (#5730).

🔴 THE DEFECT (``origin/main``). An unattended run changes files only inside its own folders, and
the bound reads a shell command's writes from its text. A line the reader declines (a subshell, a
group, a substitution, a background ``&``) wrote nothing at all: ``command_effects`` answered
"unread" with no write facet, so ``( echo x > ~/elsewhere )``, ``echo x > ~/elsewhere &`` or
``{ cp a ~/notes/a; }`` took a write outside the run's folders past the bound.

Now such a line's writes are read from its parts (``shell_syntax.parts``), as #5727 reads its hosts:
each part's paths count as any command's do. A part that still can't be read, or one that runs a
program it does not name, writes a path it does not name, and is refused. Where a part runs is
followed coarsely: a part that moves the shell (``cd``, ``eval``) may have moved the others, so a
relative path in them names no place, and one that changes the shell's environment may send them
elsewhere.

Nothing here writes: an unattended call past the bound is refused before it runs, and the calls
inside run against a stand-in shell.
"""

from __future__ import annotations

import pytest
from test_an_unattended_run_stays_inside_its_bounds import _allow_hosts, _result, _run

from personalclaw import owner_only
from personalclaw.approval_confirms import DELETES, OUTSIDE_WORKSPACE, call_confirms
from personalclaw.command_effects import command_effects
from personalclaw.llm.events import TOOL_META_REFUSED_BY
from personalclaw.task_modes import UNCHECKED, read_call

UNNAMED = "a path its command does not name"

#: The shapes a write to *path* takes in a line the reader can't split, and one it can.
SHAPES = {
    "alone": "echo x > {path}",
    "inside a subshell": "( echo x > {path} )",
    "in the background": "echo x > {path} &",
    "in a group": "{ cp notes.md {path}; }",
    "after a substitution": "echo $(cat notes.md) > {path}",
    "inside a substitution": "echo $(echo x > {path})",
    "inside a backgrounded subshell": "( printf x > {path} & )",
    "after a backgrounded subshell": "( sleep 1 & ); tee {path} < notes.md",
    "beside a process substitution": "diff <(cat notes.md) notes.md > {path}",
    "under a here-document with a substitution": "cat > {path} <<EOF\n$(date)\nEOF",
    "behind a pipe of both streams": "make |& tee {path}",
}


def _command(shape: str, path: str) -> str:
    return SHAPES[shape].replace("{path}", path)


@pytest.mark.asyncio
@pytest.mark.parametrize("shape", list(SHAPES))
async def test_an_unattended_run_is_refused_a_write_outside_its_folders_in_any_shape(
    tmp_path, shape
):
    """🔴 Red on main for every shape but the first."""
    _allow_hosts()

    events, invoked, _ = await _run(
        tmp_path, "bash", {"command": _command(shape, "~/elsewhere/x")}, unattended=True
    )

    assert invoked == [], f"{shape}: the call ran"
    result = _result(events)
    assert result.tool_meta.get(TOOL_META_REFUSED_BY) == "run_bounds"
    assert "it writes ~/elsewhere/x, outside the folders this run works in" in result.tool_output


@pytest.mark.asyncio
async def test_a_write_past_the_root_in_the_background_is_refused(tmp_path):
    """🔴 Red on main: the issue's ``printf x > /tmp/../etc/x &``."""
    _allow_hosts()

    events, invoked, _ = await _run(
        tmp_path, "bash", {"command": "printf x > /tmp/../etc/x &"}, unattended=True
    )

    assert invoked == []
    assert "/tmp/../etc/x" in _result(events).tool_output


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["out/x.txt", "$TMPDIR/x.txt"], ids=["the worktree", "scratch"])
@pytest.mark.parametrize("shape", list(SHAPES))
async def test_the_same_write_inside_its_folders_runs(tmp_path, shape, path):
    _allow_hosts()

    _events, invoked, _ = await _run(
        tmp_path, "bash", {"command": _command(shape, path)}, unattended=True
    )

    assert [name for name, _ in invoked] == ["bash"], shape


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "command",
    [
        "cd /srv && ( echo y > notes.txt )",
        "{ cd /srv; }; echo y > notes.txt",
        '( eval "$X" ); echo y > notes.txt',
        "export HOME=/srv; ( echo y > ~/notes.txt )",
    ],
    ids=["after a cd", "after a group's cd", "after an eval", "after an export"],
)
async def test_a_part_another_part_may_have_moved_writes_a_path_it_does_not_name(tmp_path, command):
    """🔴 Red on main. ``parts`` does not say where a part sits, nor whether it was a group (whose
    ``cd`` moves the line after it), so once one part moves the shell, or changes what its programs
    are given, a path in the others is not known."""
    _allow_hosts()

    events, invoked, _ = await _run(tmp_path, "bash", {"command": command}, unattended=True)

    assert invoked == [], command
    assert UNNAMED in _result(events).tool_output


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "command",
    ["(cd sub && make > build.log)", "( make 2>&1 | tee build.log & )", "( cd sub ) && echo y"],
)
async def test_a_part_that_moves_only_itself_still_names_its_paths(tmp_path, command):
    """A part places its own paths by its own reading, a leading ``cd`` included."""
    _allow_hosts()

    _events, invoked, _ = await _run(tmp_path, "bash", {"command": command}, unattended=True)

    assert [name for name, _ in invoked] == ["bash"], command


@pytest.mark.parametrize(
    "command",
    [
        "case $x in a) echo y > notes.txt;; esac",
        "echo $'\\t' > notes.txt",
        "echo ${A:-${B}} > notes.txt",
        "f() { echo y > notes.txt; }; f",
        "( echo y > notes.txt",
        "( $CP notes.md ~/notes.md & )",
        "( $(which cp) notes.md copy.md & )",
    ],
    ids=[
        "a case branch",
        "an ANSI-C quote",
        "a nested expansion",
        "a function",
        "unclosed",
        "a program named by a variable",
        "a program a substitution prints",
    ],
)
def test_what_still_cannot_be_read_writes_a_path_it_does_not_name(command):
    """Held, never let through for what it might name. An unattended run is refused these lines
    by their network facet already (#5727); this is the write facet's own word on them."""
    effects = command_effects(command)
    assert effects.writes and effects.target_unread, command


def test_an_environment_change_in_one_part_sends_what_the_others_write_elsewhere():
    """A variable set in one part may send what a program in another writes elsewhere (a home, a
    tool's own settings). Coarser than a line the reader can split, whose redirects still go where
    they say: a part's writes are held together."""
    assert command_effects("export DEST=/srv; ( make install > install.log )").target_unread


def test_the_write_facet_takes_each_parts_paths():
    effects = command_effects("( echo x > a.txt & ); { cp a.txt ~/b.txt; }; echo $(cat a.txt) > c")
    assert sorted(effects.targets) == ["a.txt", "c", "~/b.txt"]
    assert not effects.target_unread
    assert command_effects("(cd sub && make > log)").targets == {"sub/log"}


def test_a_line_the_reader_can_split_is_read_as_before():
    effects = command_effects("cd /srv && echo y > f")
    assert effects.targets == {"/srv/f"} and not effects.target_unread
    assert command_effects("echo a; cd /srv; echo y > f").target_unread


def test_the_delete_half_and_the_risk_are_as_they_were():
    """A delete in such a line is still held by name (``removes_unread``), its path is a write as
    on any line, and the call stays UNCHECKED: the parts' facet carries no ``deletes``."""
    effects = command_effects("( rm -rf build & )")
    assert effects.removes_unread and not effects.deletes
    assert effects.targets == {"build"}
    assert read_call("destructive", "bash", "", {"command": "( rm -rf build & )"}).risk == UNCHECKED


def test_a_part_reaches_the_hosts_it_names_as_before():
    effects = command_effects("( curl -s http://localhost/x & ); echo done")
    assert effects.hosts == {"localhost"} and not effects.host_unread


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("( echo hi > /etc/hosts.bak & )", (DELETES, OUTSIDE_WORKSPACE)),
        ("( echo hi > notes.txt & )", (DELETES,)),
    ],
)
def test_the_second_confirmation_reads_the_parts_writes(tmp_path, command, expected):
    """🔴 Red on main for the first. Unread, the line may destroy (DELETES), as before; a write of
    its outside the chat's folder now asks that confirmation too."""
    got = call_confirms(
        "destructive",
        "bash",
        "",
        {"command": command},
        cwd=str(tmp_path),
        consequences=None,
        own=True,
    )
    assert got == expected


def test_the_owner_only_fence_reads_the_parts_writes(tmp_path):
    """🔴 Red on main: ``git config`` in a subshell writes the repository's own settings, which no
    word of its command names."""
    named = owner_only.named_in(
        "( git config core.hooksPath hooks & )", cwd=str(tmp_path), home=tmp_path / "home"
    )
    assert named is not None and named.kind == owner_only.GIT

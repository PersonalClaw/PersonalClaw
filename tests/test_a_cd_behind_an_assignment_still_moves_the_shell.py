"""A ``cd`` or ``eval`` behind an assignment or a reserved word still moves the shell (#5740).

🔴 THE DEFECT (``origin/main``). The reading follows the folder a command's writes land in: a
leading ``cd`` is read as the folder, and a relative path after any other move names no place, so
an unattended run is refused it. The move was recognised by the command's first word, and behind
a leading assignment that word is the assignment: ``FOO=1 cd /x && echo y > f`` read ``f`` as a
path in the folder the command starts in, though bash changes directory there. So
``X=1 cd /srv && echo y >> notes.txt`` passed an unattended run's write bound as a write
inside its own folder. The same held for ``eval``, ``command cd``, ``builtin cd`` and ``pushd``,
and for a move behind one of the shell's reserved words, which run the command after them in the
shell itself: ``time cd /x``, ``! cd /x``, ``if cd /x; then …``, ``while cd /x; do …``, and a ``cd``
after ``then``, ``else`` or ``do``.

Now the program a command runs is read past its leading assignments and reserved words wherever
a move is recognised (``command_shell._program_of``), on a line the reader can split and in the
parts of one it can't (#5730). A move behind either is not read as a leading ``cd``: the paths
after it name no place, which fails closed.
"""

from __future__ import annotations

import pytest
from test_an_unattended_run_stays_inside_its_bounds import _allow_hosts, _result, _run

from personalclaw.command_effects import command_effects
from personalclaw.llm.events import TOOL_META_REFUSED_BY

#: The moves, each behind an assignment or a word that runs the command after it.
MOVES = {
    "cd": "X=1 cd /srv",
    "two assignments and cd": "A=1 B=2 cd /srv",
    "eval": "X=1 eval 'cd /srv'",
    "command cd": "X=1 command cd /srv",
    "builtin cd": "X=1 builtin cd /srv",
    "pushd": "X=1 pushd /srv",
    "time cd": "time cd /srv",
    "time -p cd": "time -p cd /srv",
    "an assignment behind time": "time X=1 cd /srv",
    "! cd": "! cd /srv",
    "zsh's noglob cd": "noglob cd /srv",
    "zsh's nocorrect cd": "nocorrect cd /srv",
}

#: Where the write after the move sits: on a line the reader can split, and in the parts of one
#: it can't.
FORMS = {
    "after it": "{move}; echo y > notes.txt",
    "after it with &&": "{move} && echo y > notes.txt",
    "in a subshell after it": "{move}; ( echo y > notes.txt )",
    "after it in a backgrounded subshell": "( {move}; echo y > notes.txt & )",
}


@pytest.mark.parametrize("form", list(FORMS))
@pytest.mark.parametrize("move", list(MOVES))
def test_a_move_behind_an_assignment_loses_where_a_relative_path_lies(move, form):
    """🔴 Red on main for each."""
    command = FORMS[form].replace("{move}", MOVES[move])
    effects = command_effects(command)
    assert effects.writes and effects.target_unread, command
    assert "notes.txt" not in effects.targets, command


#: A ``cd`` in a compound command, behind the reserved word it follows, before a write.
COMPOUND = {
    "if": "if cd /srv; then {write}; fi",
    "elif": "if false; then :; elif cd /srv; then {write}; fi",
    "while": "while cd /srv; do {write}; break; done",
    "until": "until cd /srv; do {write}; done",
    "then": "if true; then cd /srv; fi; {write}",
    "else": "if false; then :; else cd /srv; fi; {write}",
    "for's do": "for i in 1; do cd /srv; done; {write}",
    "select's do": "select i in 1; do cd /srv; break; done; {write}",
}

#: The write, on a line the reader can split, in a part of one it can't, and with the whole
#: line in a backgrounded subshell.
WRITES = {
    "a write": ("echo y > notes.txt", "{line}"),
    "a write in a subshell": ("( echo y > notes.txt )", "{line}"),
    "in a backgrounded subshell": ("echo y > notes.txt", "( {line} & )"),
}


@pytest.mark.parametrize("write", list(WRITES))
@pytest.mark.parametrize("word", list(COMPOUND))
def test_a_cd_behind_a_compound_commands_word_loses_where_a_relative_path_lies(word, write):
    """🔴 Red on main for each, but for a subshell right after ``then`` or ``do`` (``if``,
    ``elif``, ``while``, ``until``), which ``parts`` can't cut, and so already holds."""
    written, wrapped = WRITES[write]
    command = wrapped.replace("{line}", COMPOUND[word].replace("{write}", written))
    effects = command_effects(command)
    assert effects.writes and effects.target_unread, command
    assert "notes.txt" not in effects.targets, command


@pytest.mark.asyncio
async def test_an_unattended_run_is_refused_a_write_behind_an_assigned_cd(tmp_path):
    """🔴 Red on main: the issue's line, in a folder no credential-path rule names."""
    _allow_hosts()

    events, invoked, _ = await _run(
        tmp_path,
        "bash",
        {"command": "X=1 cd /srv && echo y >> notes.txt"},
        unattended=True,
    )

    assert invoked == []
    result = _result(events)
    assert result.tool_meta.get(TOOL_META_REFUSED_BY) == "run_bounds"
    assert "a path its command does not name" in result.tool_output


def test_a_leading_cd_and_an_assignment_before_a_program_read_as_before():
    assert command_effects("cd /srv && echo y > f").targets == {"/srv/f"}
    effects = command_effects("FOO=1 make > out.txt")
    assert effects.targets == {"out.txt"} and not effects.target_unread
    assert command_effects("X=1 command -v jq > found.txt").targets == {"found.txt"}
    assert command_effects("X=1; echo y > notes.txt").targets == {"notes.txt"}
    assert command_effects("if true; then echo y > notes.txt; fi").targets == {"notes.txt"}
    assert command_effects("time make > out.txt").targets == {"out.txt"}
    # A coprocess runs in a subshell of its own: its `cd` moves only that.
    assert command_effects("coproc cd /srv; echo y > notes.txt").targets == {"notes.txt"}

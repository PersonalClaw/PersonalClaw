"""A command the shell reader can't split is held to the allowed hosts like any other (#5727).

🔴 THE DEFECT (``origin/main``). The hold reads a command's hosts from its text, and a line the
reader declines (a subshell, a ``$(…)``, a backtick, a group, a background ``&``) reached no host at
all: ``command_effects`` answered "unread" with no network facet, so nothing held it. A chat with
YOLO on ran ``( server & ); curl http://127.0.0.1:…`` unasked twice, beside two plain ``curl`` calls
to the same host that asked. An unattended run's grant let the same lines through to any host.

Now such a line is read in its parts (``shell_syntax.parts``): the line with its subshells, groups
and substitutions taken out, and each of those as a command of its own. Each part's hosts count as
any command's do, so a listed host still runs unasked; a part that still can't be read, or one that
runs a program it does not name, reaches a host it does not name, and is asked about (attended) or
refused (unattended).

Every host here is a reserved name (RFC 2606) or this machine, so nothing is ever reached.
"""

from __future__ import annotations

import pytest
from test_an_unattended_run_stays_inside_its_bounds import _allow_hosts, _result, _run

from personalclaw.command_effects import command_effects
from personalclaw.llm.events import EVENT_PERMISSION_REQUEST, TOOL_META_REFUSED_BY
from personalclaw.shell_syntax import NO_OP, UNKNOWN_WORD, parts

UNLISTED = "pkgs.invalid"
LISTED = "localhost"

#: The shapes a curl to *url* takes in a line the reader can't split, and two it can.
SHAPES = {
    "alone": "curl -s {url}",
    "after a cd": "cd /tmp && curl -s {url}",
    "after a backgrounded subshell": "( python3 -m http.server 8000 & ); curl -s {url}",
    "inside a substitution": "echo $(curl -s {url})",
    "inside a subshell": "( curl -s {url} )",
    "inside a backgrounded subshell": "( curl -s {url} 2>&1 | tee log & )",
    "in the background": "curl -s {url} &",
    "in a group": "{ curl -s {url}; }",
    "in backticks": "x=`curl -s {url}`",
    "inside arithmetic": "echo $(( $(curl -s {url}) + 1 ))",
    # Not arithmetic: `$((` whose inner `(` closes before the outer `)` is a command substitution.
    "in a substitution that starts with a subshell": "echo $((true) && (curl -s {url}))",
    "the same, in double quotes": 'echo "[$((true) && (curl -s {url}))]"',
    "in a process substitution": "diff <(curl -s {url}) expected.json",
    "in a here-document's body": "cat > out.txt <<EOF\n$(curl -s {url})\nEOF",
    "behind a pipe of both streams": "curl -s {url} |& tee log",
}


def _command(shape: str, host: str) -> str:
    return SHAPES[shape].replace("{url}", f"http://{host}/simple/")


@pytest.mark.asyncio
@pytest.mark.parametrize("shape", list(SHAPES))
async def test_an_unattended_run_is_refused_an_unlisted_host_in_any_shape(tmp_path, shape):
    """🔴 Red on main for every shape but the first two."""
    _allow_hosts(LISTED)

    events, invoked, _ = await _run(
        tmp_path, "bash", {"command": _command(shape, UNLISTED)}, unattended=True
    )

    assert invoked == [], f"{shape}: the call ran"
    assert not any(e.kind == EVENT_PERMISSION_REQUEST for e in events)
    result = _result(events)
    assert result.tool_meta.get(TOOL_META_REFUSED_BY) == "run_bounds"
    assert UNLISTED in result.tool_output


@pytest.mark.asyncio
@pytest.mark.parametrize("shape", list(SHAPES))
async def test_an_attended_run_is_asked_about_an_unlisted_host_whatever_its_grant(tmp_path, shape):
    """🔴 Red on main for every shape but the first two: the run's standing grant answered."""
    _allow_hosts(LISTED)

    events, invoked, _ = await _run(
        tmp_path, "bash", {"command": _command(shape, UNLISTED)}, unattended=False
    )

    asks = [e for e in events if e.kind == EVENT_PERMISSION_REQUEST]
    assert len(asks) == 1, f"{shape}: not asked"
    assert invoked == []


@pytest.mark.asyncio
@pytest.mark.parametrize("unattended", [True, False], ids=["unattended", "attended"])
@pytest.mark.parametrize("shape", list(SHAPES))
async def test_a_listed_host_in_any_shape_runs_unasked(tmp_path, shape, unattended):
    _allow_hosts(LISTED)

    events, invoked, _ = await _run(
        tmp_path, "bash", {"command": _command(shape, LISTED)}, unattended=unattended
    )

    assert not any(e.kind == EVENT_PERMISSION_REQUEST for e in events), shape
    assert [name for name, _ in invoked] == ["bash"], shape


@pytest.mark.parametrize(
    "command",
    [
        f"curl -s http://{LISTED}/x 2>&1 | tee log",
        f"( curl -s http://{LISTED}/x 2>&1 | tee log & )",
        f"( curl -s http://{LISTED}/x >&2 & )",
        f"( curl -s http://{LISTED}/x &> log & )",
        f"( wc -l <&3 & ); curl -s http://{LISTED}/x",
    ],
)
def test_an_ampersand_in_a_redirect_is_no_background_job(command):
    """``2>&1``, ``>&2``, ``&>`` and ``<&3`` copy or open a descriptor: they split nothing, so the
    host is still the one named, and nothing else is unread about where it goes."""
    effects = command_effects(command)
    assert effects.network and effects.hosts == {LISTED} and not effects.host_unread, command
    unlisted = command_effects(command.replace(LISTED, UNLISTED))
    assert unlisted.hosts == {UNLISTED}


@pytest.mark.parametrize(
    "command",
    [
        "curl -s $(cat url.txt)",
        f"$(which curl) http://{LISTED}/x",
        f"( $CURL http://{LISTED}/x & )",
    ],
)
def test_a_word_or_program_a_substitution_makes_is_a_host_it_does_not_name(command):
    """What a substitution prints is not known: as a URL it names no host, and as a program it
    may be any program, so the line reaches a host it does not name."""
    assert command_effects(command).host_unread, command


@pytest.mark.parametrize(
    "command",
    [
        f"case $x in a) curl -s http://{LISTED}/x;; esac",
        f"echo $'\\t'; curl -s http://{LISTED}/x",
        f"echo ${{A:-${{B}}}} && curl -s http://{LISTED}/x",
        f"f() {{ curl -s http://{LISTED}/x; }}; f",
        f"( curl -s http://{LISTED}/x",
        f"echo $((a)+(b)); curl -s http://{LISTED}/x",
    ],
    ids=[
        "a case branch",
        "an ANSI-C quote",
        "a nested expansion",
        "a function",
        "unclosed",
        "a substitution of a subshell and a word",
    ],
)
def test_what_still_cannot_be_read_reaches_a_host_it_does_not_name(command):
    """A part the reader can't read at all is held, never let through for what it might name."""
    effects = command_effects(command)
    assert effects.network and effects.host_unread, command


def test_a_line_with_no_network_program_in_its_parts_reaches_none():
    """Reading the parts is what keeps a line with nothing to hold from being asked about: a
    backgrounded build, a brace expansion, ``find``'s ``{}``, arithmetic, a quoted here-document,
    a comment."""
    for command in [
        "make 2>&1 | tee build.log &",
        "( cd sub && make ) && echo done",
        "mkdir -p src/{a,b}",
        "find . -name '*.py' -exec wc -l {} \\;",
        "for i in 1 2; do echo $(( i + 1 )); done",
        "cat > s.py <<EOF\nprint('$HOME')\nEOF",
        "ls # curl http://pkgs.invalid/x",
    ]:
        assert not command_effects(command).network, command


def test_the_parts_of_a_line_are_its_commands_with_its_substitutions_taken_out():
    assert parts("( a & ); b 2>&1 &") == [f"{NO_OP}; b 2>&1 ;", " a & "]
    assert parts("echo $(curl x) `y`") == [f"echo {UNKNOWN_WORD} {UNKNOWN_WORD}", "curl x", "y"]
    assert parts("{ a; b; }") == [NO_OP, " a; b; "]
    assert parts('echo "n=$(( $(z) + 1 ))"') == [f'echo "{UNKNOWN_WORD}"', "z"]
    assert parts("cat <<A\n$(w)\nA") == ["cat <<'A'\n$(w)\nA", "w"]
    assert parts("cat <<'A'\n$(w)\nA") == ["cat <<'A'\n$(w)\nA"]
    assert parts("x=(1 2)") is None  # an array: a `(` that starts no command


def test_a_delete_in_a_line_the_reader_cannot_split_still_asks():
    """The delete half is as it was: a named delete program in such a line removes a path this
    reading cannot name."""
    assert command_effects('( rm -rf "$X" & )').removes_unread

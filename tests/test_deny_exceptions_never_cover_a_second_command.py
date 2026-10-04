"""A deny-list exception covers the one command it names, never a second one joined to it.

`*git*push*` is in the built-in deny list, which holds whatever the approval mode, and
`* stash push*` is its exception: `git stash push` keeps work on this machine and pushes nothing.
The exception is applied only to a line the shell reads as one command
(`shell_syntax.one_command`, the reading an operator's auto-approve pattern is held to), so a push
joined to a stash by any operator the shell runs, the background `&` included, or run inside it,
is refused as the push it is.

The lines are built from the reader: every spelling of the characters an operator, a redirect or a
substitution is written with, put between `git stash push` and a push. Where the reader reads the
line as one command, it is a stash push with more arguments, and the exception holds.
"""

from __future__ import annotations

import itertools

import pytest

from personalclaw import shell_syntax
from personalclaw.security import is_denied

OPERATOR_CHARS = ";&|<>\n"
OTHER_CHARS = "`$(){}#"
PUSH = "git push origin main --force"


def _lines() -> list[str]:
    spellings = [
        "".join(p)
        for n in (1, 2)
        for p in itertools.product(OPERATOR_CHARS + OTHER_CHARS, repeat=n)
    ]
    spellings += ["".join(p) for p in itertools.product(OPERATOR_CHARS, repeat=3)]
    return [f"git stash push {s} {PUSH}" for s in dict.fromkeys(spellings)]


LINES = _lines()


def test_a_push_joined_to_a_stash_is_refused_however_it_is_joined():
    """The defect: the old separator check missed the background `&`, so
    `git stash push & git push --force` matched the exception and the push ran past the floor."""
    more = [line for line in LINES if shell_syntax.one_command(line) is None]
    assert more, "vacuity"
    allowed = [line for line in more if is_denied(line) is None]
    assert not allowed, allowed[:10]


def test_a_stash_push_the_reader_reads_as_one_command_keeps_its_exception():
    """The other half: the exception is not withdrawn wholesale. A line the reader reads as one
    command is `git stash push` with more arguments (a redirect sends its output to a file), and
    a stash pushes nothing."""
    one = [line for line in LINES if shell_syntax.one_command(line) is not None]
    assert one, "vacuity"
    refused = [line for line in one if is_denied(line) is not None]
    assert not refused, refused[:10]


@pytest.mark.parametrize(
    "line",
    [
        "git stash push & git push origin main --force",
        "git stash push |& git push origin main",
        "git stash push <(git push origin main)",
        "git stash push; git push origin main --force",
        "git stash push && git push origin main",
        'git stash push -m "$(git push origin main --force)"',
        "git stash push -m `git push origin main`",
        "git stash push\ngit push origin main",
    ],
    ids=[
        "background",
        "both-streams",
        "process-substitution",
        "semicolon",
        "and",
        "substitution",
        "backtick",
        "newline",
    ],
)
def test_a_second_command_is_never_covered(line):
    assert is_denied(line) is not None


@pytest.mark.parametrize(
    "line",
    [
        "git stash push",
        "git stash push -m 'wip; and more'",
        "git -C /home/user/src/app stash push",
        "git stash push 2>&1",
    ],
)
def test_the_exception_holds_for_the_stash_it_names(line):
    assert is_denied(line) is None


def test_a_push_is_refused_on_the_command_an_agent_cli_runs_as_well_as_its_title():
    """At the call site: the screen every approval path asks reads the command the call carries,
    so a stash title cannot carry a joined push past the floor."""
    from personalclaw.acp.permission_authority import screen_tool_call
    from personalclaw.hooks import TOOL_DENY, HookManager, HooksConfig

    hooks = HookManager(HooksConfig(auto_approve_tools=["*"]))
    command = '{"command": "git stash push & git push origin main --force"}'
    verdict = screen_tool_call(hooks, "Running: git stash push", command, tool_kind="execute")
    assert verdict.action == TOOL_DENY, verdict
    assert (
        screen_tool_call(
            hooks, "Running: git stash push", '{"command": "git stash push"}', tool_kind="execute"
        ).action
        != TOOL_DENY
    ), "and the stash itself still runs"

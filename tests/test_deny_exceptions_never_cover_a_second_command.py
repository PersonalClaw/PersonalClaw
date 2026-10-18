"""A stash push is judged by what the line runs, never waved through by its text.

`*git*push*` is in the built-in deny list, which holds whatever the approval mode. It used to have
a text exception, `* stash push*`, applied to a line the shell reads as one command. A text match
over the whole line can be met by words that are not a stash push at all: a value given to git's
own `-c` that holds " stash push", in front of a real push, let that push past the rule. The
exception is gone. For a shell call the rule refuses only what may run a push
(`security._may_push`), which reads `git stash push` as stash's own action, so a stash push is
judged one way whether it stands alone or sits in a chain.

The lines are built from the reader: every spelling of the characters an operator, a redirect or a
substitution is written with, put between `git stash push` and a push. Each one is refused: a
second command joined by an operator runs the push, and a word after git that holds "push" (a
redirect's target included) is taken for one.
"""

from __future__ import annotations

import itertools

import pytest

from personalclaw.security import is_denied

pytestmark = pytest.mark.security

RUNNING = "Running: "
GIT = "git"
PUSH = "push"
STASH = f"{GIT} stash {PUSH}"

OPERATOR_CHARS = ";&|<>\n"
OTHER_CHARS = "`$(){}#"
A_PUSH = f"{GIT} {PUSH} origin main --force"


def _refused(command: str) -> bool:
    return is_denied(f"{RUNNING}{command}") is not None


def _lines() -> list[str]:
    spellings = [
        "".join(p)
        for n in (1, 2)
        for p in itertools.product(OPERATOR_CHARS + OTHER_CHARS, repeat=n)
    ]
    spellings += ["".join(p) for p in itertools.product(OPERATOR_CHARS, repeat=3)]
    return [f"{STASH} {s} {A_PUSH}" for s in dict.fromkeys(spellings)]


LINES = _lines()


def test_a_push_beside_a_stash_is_refused_however_it_is_spelled():
    """Every spelling, whether the reader reads it as one command or more: a redirect into a file
    named `git` is refused too, since the line still names a push after git."""
    assert len(LINES) > 100, "vacuity"
    allowed = [line for line in LINES if not _refused(line)]
    assert not allowed, allowed[:10]


@pytest.mark.parametrize(
    "line",
    [
        f"{STASH} & {A_PUSH}",
        f"{STASH} |& {GIT} {PUSH} origin main",
        f"{STASH} <({GIT} {PUSH} origin main)",
        f"{STASH}; {A_PUSH}",
        f"{STASH} && {GIT} {PUSH} origin main",
        f'{STASH} -m "$({A_PUSH})"',
        f"{STASH} -m `{GIT} {PUSH} origin main`",
        f"{STASH}\n{GIT} {PUSH} origin main",
        f"{STASH} < {A_PUSH}",
        f"{STASH} > {A_PUSH}",
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
        "redirect-from",
        "redirect-to",
    ],
)
def test_a_second_command_is_never_covered(line):
    assert _refused(line)


# The review's case and its variants: text that holds " stash push" in front of a real push. The
# old exception matched each one and let the push through.
TEXT_THAT_LOOKS_LIKE_A_STASH = [
    f'{GIT} -c x.y=" stash {PUSH}" {PUSH} --force origin main',
    f"{GIT} -c x.y=' stash {PUSH}' {PUSH} --force origin main",
    f'{GIT} -c "x.y= stash {PUSH}" {PUSH} --force origin main',
    f"{GIT} -c 'x.y= stash {PUSH}' {PUSH} --force origin main",
    f'{GIT} -c x.y=" stash {PUSH}" {PUSH} origin main',
    f'{GIT} -c x.y=" stash {PUSH}" {PUSH} origin --delete main',
    f'{GIT} {PUSH} -o " stash {PUSH}" --force origin main',
    f'X=" stash {PUSH}" {GIT} {PUSH} --force origin main',
]


@pytest.mark.parametrize("line", TEXT_THAT_LOOKS_LIKE_A_STASH)
def test_text_that_looks_like_a_stash_push_does_not_carry_a_push(line):
    """🔴 Red before: the text exception matched " stash push" anywhere on the line. Judged both as
    a shell call and as a bare name, which is how an agent CLI may title the call."""
    assert _refused(line), line
    assert is_denied(line) is not None, line


@pytest.mark.parametrize(
    "line",
    [
        STASH,
        f"{STASH} -m 'wip; and more'",
        f'{STASH} -m "wip"',
        f"{GIT} -C /home/user/src/app stash {PUSH}",
        f"{STASH} 2>&1",
    ],
)
def test_a_stash_push_alone_is_not_refused(line):
    assert not _refused(line)


def test_a_push_is_refused_on_the_command_an_agent_cli_runs_as_well_as_its_title():
    """At the call site: the screen every approval path asks reads the command the call carries,
    so a stash title cannot carry a joined push past the floor."""
    from personalclaw.acp.permission_authority import screen_tool_call
    from personalclaw.hooks import TOOL_DENY, HookManager, HooksConfig

    hooks = HookManager(HooksConfig(auto_approve_tools=["*"]))
    command = '{"command": "%s & %s"}' % (STASH, A_PUSH)
    verdict = screen_tool_call(hooks, f"{RUNNING}{STASH}", command, tool_kind="execute")
    assert verdict.action == TOOL_DENY, verdict
    assert (
        screen_tool_call(
            hooks, f"{RUNNING}{STASH}", '{"command": "%s"}' % STASH, tool_kind="execute"
        ).action
        != TOOL_DENY
    ), "and the stash itself still runs"

"""The built-in tool-name push rule (``*git*push*``) refuses a push, not a command that names one.

The rule is an fnmatch glob over a tool's name, and a shell call's name is its command
(``Running: <command>``). Matched as text, it refused every command holding "git" and later
"push": a read of ``.github/workflows`` for its ``push:`` triggers, or a commit whose message
says "push". For a shell command it now refuses only what may run a push
(``security._may_push``). Everything else stays as it was: a tool named like a push is refused as
a name, every shell spelling of a push stays refused, and the owner's Git push switch, its one
plain-push exception and the deny-exception event it records behave as before
(``tests/test_branch_publish_switch.py``).

Each rule ``_may_push`` applies has a case here that only that rule decides, so removing or
loosening the rule fails it.
"""

from __future__ import annotations

import json
import os
import shlex
import signal
import subprocess
import sys

import cpu_budget
import pytest

from personalclaw import security
from personalclaw.config.loader import config_dir
from personalclaw.hooks import TOOL_DENY, HookManager, HooksConfig

pytestmark = pytest.mark.security

#: What a shell call's title is: the hook chain reads the rest as the command.
RUNNING = "Running: "
GIT = "git"
PUSH = "push"


def _refused(command: str) -> bool:
    """Whether the built-in list refuses *command* as a shell call names it, the form the hook
    chain hands it over in on every revision (``Running: <command>``)."""
    return security.is_denied(f"{RUNNING}{command}") is not None


def _sel_rows(event_type: str) -> list[dict]:
    from personalclaw.sel import sel

    return [r for r in sel().recent(500) if r.get("event_type") == event_type]


def _set_switch(on: bool) -> None:
    path = config_dir() / "config.json"
    data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    data.setdefault("security", {})["allow_git_push"] = on
    path.write_text(json.dumps(data), encoding="utf-8")


# ── a shell push, in every form the shell denylist's push pattern catches, stays refused ────────

SHELL_PUSHES = [
    f"{GIT} {PUSH}",
    f"{GIT} {PUSH} origin main",
    f"{GIT} {PUSH} --force origin main",
    f"{GIT} --no-pager {PUSH} origin main",
    f"{GIT} -C /work/repo {PUSH} origin main",
    f"{GIT} -c user.name=x {PUSH} origin main",
    f"/usr/bin/{GIT} {PUSH} origin main",
    f'bash -c "{GIT} {PUSH} origin main"',
    f"sh -c 'cd /work/repo && {GIT} {PUSH}'",
    f"cd /work/repo && {GIT} {PUSH} origin main",
    f"ls; {GIT} {PUSH} origin main",
    f"{GIT} status;{GIT} {PUSH}",
    f"env GIT_TRACE=1 {GIT} {PUSH} origin main",
    f"{GIT} commit -m wip && {GIT} {PUSH}",
    f"{GIT} stash {PUSH}; {GIT} {PUSH} origin main --force",
]


@pytest.mark.parametrize("command", SHELL_PUSHES)
def test_a_shell_push_stays_refused(command):
    assert _refused(command), command


# ── each rule, by the case only it decides ──────────────────────────────────────────────────────


def _nested(innermost: str, levels: int) -> str:
    command = innermost
    for _ in range(levels):
        command = f"bash -c {shlex.quote(command)}"
    return command


RULE_CASES = {
    # The shell denylist's own push pattern: a git wrapper the reader does not take for git.
    "never narrower than the shell denylist": f"my{GIT} {PUSH} origin main",
    # Syntax the reader does not parse: a subshell, a substitution, a background job.
    "a subshell": f"({GIT} {PUSH})",
    "a substitution": f"echo $({GIT} {PUSH})",
    "a background job": f"{GIT} {PUSH} &",
    # A here-document's body is a command line the reader does not read.
    "a here-document": f"bash <<EOF\n({GIT} {PUSH})\nEOF",
    # A word whose value the reader cannot know, after git (the line names a push later on).
    "a variable after git": f"{GIT} $SUB origin main && echo {PUSH}ed",
    "a glob after git": f"{GIT} pu* origin main && echo {PUSH}ed",
    # A word that holds "push", not only one that is it: an alias defined on the line.
    "an alias defined on the line": f"{GIT} -c alias.p={PUSH} p",
    # A word with no space that holds both words is a name: a program, a script, a file.
    "a program named like a push": f"{GIT}_{PUSH} origin main",
    "a script named like a push": f"./deploy-{GIT}-{PUSH}.sh",
    # A word that is a command line is read as one: a push the regex cannot see inside it.
    "a command line inside bash -c": f'bash -c "({GIT} {PUSH})"',
    "a command line inside sh -c": f"sh -c '{GIT} status;{GIT} {PUSH}'",
    # Nested past the bound: a command line that names a push, five `bash -c` deep, is taken for a
    # push rather than read for ever, even when the innermost one is only a read.
    "nested past the bound": _nested(f"cat .{GIT}hub/ci.yml | grep {PUSH}", 5),
}


@pytest.mark.parametrize("case", sorted(RULE_CASES))
def test_each_rule_refuses_the_case_only_it_decides(case):
    assert _refused(RULE_CASES[case]), RULE_CASES[case]


def test_the_message_skip_is_kept_to_the_commands_that_store_a_message():
    """Fails closed: only a commit's, a tag's, a merge's or a note's message is skipped. Any other
    subcommand's words are read in full, even where that refuses a read."""
    assert _refused(f"{GIT} log -m --grep={PUSH}")


# ── a tool named like a push stays refused ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "name",
    [f"{GIT}_{PUSH}", f"{GIT}hub_{PUSH}_files", f"{GIT}hub {PUSH} files", f"{GIT} {PUSH} origin"],
)
def test_a_tool_named_like_a_push_stays_refused(name):
    """A tool's name is matched as a name, as before, spaces and all."""
    assert security.is_denied(name) is not None


def test_a_pattern_the_owner_added_is_matched_as_written():
    """Only the built-in rule reads a shell command; the same glob added by the owner refuses
    what it matches."""
    read = f"{RUNNING}cat .{GIT}hub/workflows/ci.yml | grep -n {PUSH}"
    assert security.is_denied(read, [f"*{GIT}*{PUSH}*"]) is not None


# ── a read, or a message, that only names a push is no longer refused ───────────────────────────
#
# Each one holds "git" before "push", which is what the glob matches; a read that names "push"
# first (`grep -n push .github/…`) was never refused.

NOT_PUSHES = [
    f"cat .{GIT}hub/workflows/ci.yml | grep -n {PUSH}",
    f"sed -n 1,20p .{GIT}hub/workflows/nix.yml | grep {PUSH}",
    f"grep -rn on: .{GIT}hub/workflows | grep {PUSH}",
    f"cd .{GIT}hub/workflows && grep -c {PUSH} ci.yml",
    f'{GIT} commit -s -m "fix: refuse only a {PUSH}"',
    f"{GIT} commit -am '{PUSH} the fix later'",
    f"{GIT} commit --message='{PUSH} it' --allow-empty",
    f'{GIT} tag -a v1 -m "{PUSH}ed"',
]


@pytest.mark.parametrize("command", NOT_PUSHES)
def test_a_command_that_only_names_a_push_is_not_refused(command):
    """🔴 Red before: refused as a push, because the glob matched its text."""
    assert not _refused(command), command


def test_the_hook_chain_reads_a_shell_call_as_a_command():
    """The call a session makes, end to end through the hook chain: a read passes the built-in
    list and a push does not."""
    hooks = HookManager(HooksConfig())
    read = hooks.on_tool_call(f"{RUNNING}cat .{GIT}hub/workflows/ci.yml | grep -n {PUSH}")
    assert read.action != TOOL_DENY, read.reason
    pushed = hooks.on_tool_call(f"{RUNNING}{GIT} {PUSH} origin main")
    assert pushed.action == TOOL_DENY


# ── the owner's switch, as before ───────────────────────────────────────────────────────────────


def test_with_the_switch_on_a_plain_push_still_records_its_exception():
    _set_switch(True)
    before = len(_sel_rows("deny_exception"))
    assert security.is_denied(f"{RUNNING}{GIT} {PUSH} origin feature-x") is None
    assert len(_sel_rows("deny_exception")) == before + 1


def test_a_read_records_no_exception_whatever_the_switch():
    """A command that is not a push is not an exception to the push rule, so nothing is recorded
    as one."""
    for on in (False, True):
        _set_switch(on)
        before = len(_sel_rows("deny_exception"))
        assert security.is_denied(f"{RUNNING}cat .{GIT}hub/workflows/ci.yml | grep {PUSH}") is None
        assert len(_sel_rows("deny_exception")) == before


def test_with_the_switch_on_a_push_it_does_not_cover_stays_refused():
    _set_switch(True)
    assert _refused(f"{GIT} {PUSH} --force origin feature-x")
    assert _refused(f"cd /work && {GIT} {PUSH} origin feature-x")


# ── a stash push keeps work on this machine, alone or in a chain ────────────────────────────────

STASH = f"{GIT} stash {PUSH}"

STASH_PUSHES = [
    STASH,
    f"{STASH} -m 'wip'",
    f"{STASH} -m '{PUSH} it later'",
    f"{GIT} -C /work/repo stash {PUSH}",
    f"{STASH} && {GIT} checkout main",
    f"{GIT} status; {STASH}; {GIT} pull --rebase",
    f"{STASH} && {GIT} stash pop",
    f"cd /work/repo && {STASH} -m wip",
    # Only the message skip lets this through: its message holds the word.
    f"{STASH} -m '{PUSH} it later' && {GIT} stash pop",
]


@pytest.mark.parametrize("command", STASH_PUSHES)
def test_a_stash_push_is_not_refused_alone_or_in_a_chain(command):
    """🔴 Red before, for a chain: the stash exception holds only for one command, so a stash push
    joined to anything was refused as a push."""
    assert not _refused(command), command


@pytest.mark.parametrize(
    "command",
    [
        f"{STASH} && {GIT} {PUSH}",
        f"{STASH}; {GIT} {PUSH} origin main --force",
        f"{STASH} && {GIT} -C /work/repo {PUSH}",
        f"{GIT} stash list {PUSH}",
        f'{GIT} stash {PUSH} -m "$({GIT} {PUSH})"',
        # Only stash's own `push` action is skipped: an unknown action holding the word is not.
        f"{STASH}-all && {GIT} status",
    ],
)
def test_a_real_push_beside_a_stash_push_stays_refused(command):
    assert _refused(command), command


# ── a call whose title is its own command ───────────────────────────────────────────────────────


def _screen(title: str, command: str):
    from personalclaw.acp.permission_authority import screen_tool_call

    return screen_tool_call(
        HookManager(HooksConfig()), title, json.dumps({"command": command}), tool_kind="execute"
    )


@pytest.mark.parametrize(
    "title",
    [f"{STASH} -m wip", f"`{STASH} -m wip`", f"{RUNNING}{STASH} -m wip", f"  {STASH}   -m wip "],
    ids=["bare", "backticked", "running", "spaced"],
)
def test_a_title_that_is_its_own_stash_command_is_judged_as_that_command(title):
    """🔴 Red without the same-command rule: an agent CLI titles a shell call with the command
    itself, and the title, judged as a name, refused the stash push its command runs."""
    assert _screen(title, f"{STASH} -m wip").action != TOOL_DENY


@pytest.mark.parametrize(
    "title",
    [f"{GIT}_{PUSH}", f"{STASH} -m wip && {GIT} {PUSH}", f"`{GIT} {PUSH}`"],
    ids=["a-push-tool-name", "a-longer-command", "a-push-in-backticks"],
)
def test_a_title_that_differs_from_its_command_is_still_judged_as_a_name(title):
    """The rule skips only a title that is the very command: one that differs is judged by name,
    whatever its command is."""
    assert _screen(title, f"{STASH} -m wip").action == TOOL_DENY


def test_a_title_that_is_its_command_is_still_refused_when_the_command_is_a_push():
    for title in (f"{GIT} {PUSH} origin main", f"`{GIT} {PUSH} origin main`"):
        assert _screen(title, f"{GIT} {PUSH} origin main").action == TOOL_DENY


# ── a long line is judged in bounded time ───────────────────────────────────────────────────────

MEGABYTE = 1_000_000

#: The CPU seconds a 1 MB line may take, set by the regression it guards, not by a runner. The
#: quadratic search (the push pattern's leading ``.*`` kept) takes about 1400 s on a 1 MB line that
#: holds no push, on a bare-metal Mac (2.3 s at 40 KB, four times that at each doubling): 280 times
#: this bar. The linear rule takes 0.17 s there, and read 1.21 s on a loaded macOS virtual machine
#: (#5643's run, python-suite-macos 2/5): a bar of one second failed it on the runner's speed.
BAR = 5.0

_READ = f"cat .{GIT}hub/workflows/ci.yml | grep {PUSH} " + "a" * MEGABYTE


@pytest.mark.parametrize(
    ("line", "refused"),
    [
        (_READ, False),
        (f'{GIT} commit -m "' + f"{PUSH} " * (MEGABYTE // 5) + '"', False),
        ("a" * MEGABYTE + f" ; {GIT} {PUSH} origin main", True),
    ],
    ids=["a-read-with-one-long-word", "a-long-commit-message", "a-push-at-the-end"],
)
def test_a_one_megabyte_line_is_judged_within_the_bar(line, refused):
    """🔴 Red before: the push pattern's leading `.*` made the search quadratic, and a 1 MB read
    took minutes. Measured on the push rule alone (`_may_push`), the part this rule adds, as the CPU
    time of its work, a tracer's set aside (`tests/cpu_budget.py`): other processes on a loaded
    machine stretch the wall clock without adding any work, and coverage's tracer adds its own.
    The bar is :data:`BAR`, and why it is that number is said there."""
    took = cpu_budget.seconds(lambda: security._may_push(line))
    assert security._may_push(line) is refused
    assert took < BAR, f"{took:.2f}s"


#: The child the plant below starts: the push rule with its pattern's leading ``.*`` put back, on
#: the line it reads from stdin, with the CPU it may use capped at what its start used plus
#: :data:`BAR` (``RLIMIT_CPU`` counts whole seconds of the process's CPU, so rounded up). Killed by
#: the kernel's ``SIGXCPU``, the search used the whole bar; done, it prints ``done``.
_QUADRATIC_CHILD = """
import math, re, resource, sys
from personalclaw import security
from personalclaw.shell_denylist import GIT_PUSH_BASELINE_PATTERN
line = sys.stdin.read()
if sys.argv[1] == "quadratic":
    security._BASELINE_PUSH_RE = re.compile(GIT_PUSH_BASELINE_PATTERN, re.IGNORECASE)
used = sum(resource.getrusage(resource.RUSAGE_SELF)[:2])
cap = math.ceil(used + float(sys.argv[2]))
resource.setrlimit(resource.RLIMIT_CPU, (cap, cap + 5))
security._may_push(line)
print("done")
"""


@pytest.mark.skipif(sys.platform == "win32", reason="RLIMIT_CPU and SIGXCPU are POSIX")
def test_the_quadratic_search_it_guards_against_does_not_finish_within_the_bar(tmp_path):
    """The bar still catches the regression: with the push pattern's leading `.*` put back, the
    1 MB read uses all of :data:`BAR` in CPU and is stopped there, while the rule as it is finishes
    on the same line in the same child. A child, so a search that would run for minutes is cut at
    the bar by the kernel, with a home of its own as the real-home guard requires. It runs about
    6.5 s on a bare-metal Mac (the bar's 5 s of CPU, the linear child and two starts), under the
    suite's 30 s default cap: about 4.6 times its time, so a hang here costs its leg little."""
    from personalclaw.shell_denylist import GIT_PUSH_BASELINE_PATTERN

    assert GIT_PUSH_BASELINE_PATTERN.startswith(".*"), "premise: the pattern has the leading .*"
    env = {k: v for k, v in os.environ.items() if k != "PERSONALCLAW_HOME"}
    env["HOME"] = str(tmp_path / "home")

    def run(which: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-c", _QUADRATIC_CHILD, which, str(BAR)],
            input=_READ,
            env=env,
            capture_output=True,
            text=True,
            timeout=100,
            check=False,
        )

    linear = run("linear")
    assert (linear.returncode, linear.stdout.strip()) == (0, "done"), linear.stderr[-2000:]
    quadratic = run("quadratic")
    assert quadratic.returncode == -signal.SIGXCPU, (
        f"the quadratic search exited {quadratic.returncode} within {BAR}s of CPU:"
        f" {quadratic.stdout.strip()!r} {quadratic.stderr[-2000:]}"
    )

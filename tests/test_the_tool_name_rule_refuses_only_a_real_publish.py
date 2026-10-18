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
import shlex

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


@pytest.mark.parametrize(
    ("line", "refused"),
    [
        (f"cat .{GIT}hub/workflows/ci.yml | grep {PUSH} " + "a" * MEGABYTE, False),
        (f'{GIT} commit -m "' + f"{PUSH} " * (MEGABYTE // 5) + '"', False),
        ("a" * MEGABYTE + f" ; {GIT} {PUSH} origin main", True),
    ],
    ids=["a-read-with-one-long-word", "a-long-commit-message", "a-push-at-the-end"],
)
def test_a_one_megabyte_line_is_judged_in_under_a_second(line, refused):
    """🔴 Red before: the push pattern's leading `.*` made the search quadratic, and a 1 MB read
    took minutes. Measured on the push rule alone (`_may_push`), the part this rule adds, as the CPU
    time this process spends: other processes on a loaded machine stretch the wall clock without
    adding any work."""
    import time

    started = time.process_time()
    may = security._may_push(line)
    took = time.process_time() - started
    assert may is refused
    assert took < 1.0, f"{took:.2f}s"

"""A usage error exits 2 with the usage on stderr, and `--help` stays exit 0 on stdout.

`personalclaw cron` with none of its commands printed a hand-written usage line on stdout and
exited 0, and so did `spawn`, `agent`, `security`, `learn`, `memory` and `automation`, so a script
read a bare group as a command that had run. `project` exited 1 and `inbound`, `capture`,
`backup` and `app` exited 2, all on stdout; `config` exited 1 on stderr. The parser now refuses
every group that needs one of its commands the way it refuses any malformed command line: the
usage on stderr and exit 2. A bare `personalclaw` does the same, with the whole help.

The usage errors only a command can see (`cron update` with nothing to change, `config set` with
no key, an argument given as an empty string, `study` with nothing to do) exit 2 on stderr too,
and `cron add`'s one-cadence rule moved into the parser. A value that parses and is refused (a TTL
over the limit, an unknown surface) is a refusal, not a usage error: it keeps exit 1, on stderr
(`test_a_cli_refusal_prints_on_stderr.py`).
"""

from __future__ import annotations

import argparse
import sys

import pytest

from personalclaw import cli
from personalclaw.cli import RUN_WITHOUT_A_SUBCOMMAND, build_parser


def _groups() -> dict[str, argparse.ArgumentParser]:
    """Every top-level command that has commands of its own, read from the real parser."""
    parser = build_parser()
    top = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    return {
        name: group
        for name, group in top.choices.items()
        if any(isinstance(a, argparse._SubParsersAction) for a in group._actions)
    }


_NEED_A_COMMAND = sorted(set(_groups()) - RUN_WITHOUT_A_SUBCOMMAND)


def test_the_walk_reaches_every_group():
    """The parametrized tests below are only as good as the walk that feeds them."""
    assert {
        "agent",
        "app",
        "automation",
        "backup",
        "capture",
        "config",
        "cron",
        "inbound",
        "learn",
        "memory",
        "project",
        "security",
        "service",
        "spawn",
        "workflow",
    } <= set(_NEED_A_COMMAND)
    # Each exempt name is a real group: a stale entry would silently exempt nothing.
    assert RUN_WITHOUT_A_SUBCOMMAND <= set(_groups())


@pytest.mark.parametrize("group", _NEED_A_COMMAND)
def test_a_group_with_none_of_its_commands_is_a_usage_error(group, capsys):
    with pytest.raises(SystemExit) as exited:
        build_parser().parse_args([group])

    assert exited.value.code == 2
    out, err = capsys.readouterr()
    assert out == ""
    assert err.startswith(f"usage: personalclaw {group} ")
    # The group's commands name what is missing, not its `dest` (`cron_action`).
    assert f"personalclaw {group}: error: the following arguments are required: {{" in err


@pytest.mark.parametrize("group", _NEED_A_COMMAND)
def test_help_is_not_an_error(group, capsys):
    with pytest.raises(SystemExit) as exited:
        build_parser().parse_args([group, "--help"])

    assert exited.value.code == 0
    out, err = capsys.readouterr()
    assert out.startswith(f"usage: personalclaw {group} ") and err == ""


@pytest.mark.parametrize("group", ["auth", "incident", "push", "skills"])
def test_a_group_whose_bare_name_does_something_still_parses(group):
    """`auth`, `incident` and `push` show their status bare, and `skills` lists the skills.

    Named here, not read from `RUN_WITHOUT_A_SUBCOMMAND`: a name dropped from the set must fail
    this test, not quietly leave it."""
    assert build_parser().parse_args([group]).command == group


def _run_main(monkeypatch, *argv: str) -> int:
    monkeypatch.setattr(sys, "argv", ["personalclaw", *argv])
    with pytest.raises(SystemExit) as exited:
        cli.main()
    return int(exited.value.code or 0)


def test_a_bare_personalclaw_is_a_usage_error_with_the_whole_help(monkeypatch, capsys):
    assert _run_main(monkeypatch) == 2
    out, err = capsys.readouterr()
    assert out == ""
    # The whole help, which lists every command, not argparse's one usage line.
    assert "usage: personalclaw" in err and "Manage scheduled jobs" in err


def test_personalclaw_help_is_still_on_stdout(monkeypatch, capsys):
    assert _run_main(monkeypatch, "--help") == 0
    out, err = capsys.readouterr()
    assert "Manage scheduled jobs" in out and err == ""


@pytest.mark.parametrize(
    ("argv", "said"),
    [
        (["cron", "add", "ops", "check"], "one of the arguments --every --cron is required"),
        (
            ["cron", "add", "ops", "check", "--every", "60", "--cron", "0 9 * * *"],
            "argument --cron: not allowed with argument --every",
        ),
        (
            ["cron", "update", "clock:ops", "--every", "60", "--cron", "0 9 * * *"],
            "argument --cron: not allowed with argument --every",
        ),
    ],
)
def test_the_parser_refuses_a_cadence_that_is_missing_or_doubled(argv, said, capsys):
    with pytest.raises(SystemExit) as exited:
        build_parser().parse_args(argv)

    assert exited.value.code == 2
    out, err = capsys.readouterr()
    assert out == "" and said in err


@pytest.mark.parametrize(
    ("argv", "said"),
    [
        (["cron", "update", "clock:ops"], "Provide at least one field to update"),
        (["config", "set"], "Usage: personalclaw config set <key> <value>"),
        (["pair", ""], "Usage: personalclaw pair <provider>"),
        (["memory", "import", ""], "Usage: personalclaw memory import <file>"),
        (["study"], "Nothing to do. Pass --list, --view <id> or --run <id>."),
        (["eval-gate"], "name a proposal id to gate (or pass --list)"),
        (["restore"], "snapshot file is required (unless --list-components is given)"),
        (["auth", "revoke"], "Usage: personalclaw auth revoke --all"),
        (["app", "new"], "Usage: personalclaw app new NAME --type TYPE"),
    ],
)
def test_a_usage_error_only_the_command_sees_exits_2_on_stderr(argv, said, monkeypatch, capsys):
    """Through the real entry point, as typed: the parser lets these through, the command
    cannot run them, and says so as the usage error it is."""
    assert _run_main(monkeypatch, *argv) == 2
    out, err = capsys.readouterr()
    assert said in err and said not in out

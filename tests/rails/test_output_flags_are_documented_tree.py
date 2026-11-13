"""Every output flag a command takes is in the CLI reference, and every ``--json`` reads one way.

``--json``, ``--explain``, ``--remote`` and ``--bearer`` change what a command prints, which is
what a script depends on, so each command that takes one has a row in ``docs/reference/cli.md``
naming it and the flag. And every ``--json`` is read as ``args.as_json``: two of them were
``args.json``, so a caller passing ``as_json`` was quietly ignored, and no helper could read them
alike. The walk is over the real parser (``cli.build_parser``), so a flag added without its row,
or with another dest, fails here.

It reads the CLI reference, so it is a rail: it runs once per CI run, in the rails job.
"""

from __future__ import annotations

import argparse

import source_index
from rails_repo import REPO

_CLI_REFERENCE = REPO / "docs" / "reference" / "cli.md"

#: The flags whose rows this holds to.
OUTPUT_FLAGS = ("--json", "--explain", "--remote", "--bearer")

#: Commands that take an output flag today: the floor, so a walk that finds fewer fails.
FLOOR = {
    "token",
    "pair",
    "status",
    "cron list",
    "cron add",
    "spawn list",
    "skills list",
    "skills search",
    "incident status",
    "memory partitions",
    "snapshot",
    "footprint",
    "workflow replay",
    "auth status",
    "auth totp",
    "auth enroll",
    "inbound token",
    "inbound webhook create",
}


def _commands(
    parser: argparse.ArgumentParser, path: str = ""
) -> dict[str, argparse.ArgumentParser]:
    """Every command in *parser*'s tree, by its words after ``personalclaw``."""
    found: dict[str, argparse.ArgumentParser] = {}
    if path:
        found[path] = parser
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for name, child in action.choices.items():
                found.update(_commands(child, f"{path} {name}".strip()))
    return found


def _flags(parser: argparse.ArgumentParser) -> dict[str, argparse.Action]:
    return {
        option: action
        for action in parser._actions
        for option in action.option_strings
        if option in OUTPUT_FLAGS
    }


def _rows() -> list[str]:
    """The command cell of each table row: ``personalclaw cron list`` or, in a group's own table,
    ``cron list``."""
    return [
        line.split(" | ", 1)[0].removeprefix("| ").strip("`")
        for line in source_index.text(_CLI_REFERENCE).splitlines()
        if line.startswith("| `")
    ]


def _names(cell: str, path: str) -> bool:
    """Whether a row whose command cell is *cell* is the row of the command *path*: its own row,
    or its group's row that names each of its commands (``incident [on … | status [--json]]``)."""
    bare = cell.removeprefix("personalclaw ")
    if bare == path or bare.startswith(f"{path} "):
        return True
    group, _, command = path.partition(" ")
    return bool(command) and bare.startswith(f"{group} [") and command in bare.split()


def _taking_a_flag() -> dict[str, dict[str, argparse.Action]]:
    from personalclaw import cli

    return {
        path: flags
        for path, parser in _commands(cli.build_parser()).items()
        if (flags := _flags(parser))
    }


def test_every_command_taking_an_output_flag_has_a_row_naming_it():
    taking = _taking_a_flag()
    assert FLOOR <= set(taking), FLOOR - set(taking)
    rows = _rows()
    missing = []
    for path, flags in sorted(taking.items()):
        mine = [cell for cell in rows if _names(cell, path)]
        for flag in flags:
            if not any(flag in cell for cell in mine):
                missing.append(f"personalclaw {path} {flag}")
    assert (
        not missing
    ), "a command's output flag has no row in docs/reference/cli.md:\n" + "\n".join(
        f"  {each}" for each in missing
    )


def test_every_json_flag_is_read_as_as_json():
    wrong = sorted(
        f"personalclaw {path}: dest {flags['--json'].dest!r}"
        for path, flags in _taking_a_flag().items()
        if "--json" in flags and flags["--json"].dest != "as_json"
    )
    assert not wrong, wrong

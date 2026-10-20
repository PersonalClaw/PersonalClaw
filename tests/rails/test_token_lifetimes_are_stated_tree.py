"""The lifetimes the CLI help and the CLI reference state for a sign-in are the code's.

The tree half of ``tests/test_token_lifetimes_are_stated.py``, which says what each lifetime is
and why, and tests what the commands print. It reads the CLI reference, so it is a rail: it runs
once per CI run, in the rails job.
"""

from __future__ import annotations

import argparse
import re

import source_index
from rails_repo import REPO

from personalclaw.dashboard import token_auth

_CLI_REFERENCE = REPO / "docs" / "reference" / "cli.md"


def _subcommand_help(name: str) -> str:
    from personalclaw import cli

    parser = cli.build_parser()
    sub = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    return " ".join(sub.choices[name].format_help().split())


def test_the_lifetimes_the_help_and_docs_state_are_the_codes():
    gateway_help = _subcommand_help("gateway")
    token_help = _subcommand_help("token")
    cli_doc = source_index.text(_CLI_REFERENCE)
    ready_row = next(line for line in cli_doc.splitlines() if line.startswith("| `--json-ready`"))

    assert "20 hours" not in ready_row, "the harness token lasts 30 days, not 20 hours"
    assert "30 days" in ready_row and "auth.session_ttl" in ready_row, ready_row
    assert "up to 20 hours" not in gateway_help, "the --json-ready help still says 20 hours"
    assert "30 days" in gateway_help and "auth.session_ttl" in gateway_help
    assert "20h" in token_help and "Settings → Devices" in token_help, token_help
    docstring = token_auth.generate_token.__doc__ or ""
    assert "capped at 20 hours" not in docstring and "5 minutes" not in docstring
    token_row = next(
        line for line in cli_doc.splitlines() if line.startswith("| `personalclaw token")
    )
    assert "20h" in token_row and "Settings → Devices" in token_row, token_row
    assert re.search(r"90d|90 days", token_row), "the ceiling a caller may ask for"
    assert not re.search(r"1 year|a year|8760h", token_row), "a year is no longer on offer"

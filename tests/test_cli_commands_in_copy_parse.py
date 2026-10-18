"""Every ``personalclaw …`` command PersonalClaw tells a user to run parses against the real CLI.

🔴 The Backups panel told users to run ``personalclaw restore --replace`` twice, and a replace
restore refused over HTTP said the same. The flag is ``--mode replace`` (the ``restore`` parser in
``cli.py``), so the command a user copied failed with ``unrecognized arguments: --replace``.
Nothing held copy to the parser, so the two drifted and nobody could tell.

What this reads:

* **The SPA** (``web/src``, tests excluded): the text of a ``<code>`` element, a backticked
  command inside a quoted string (a hint, a label, a toast), the same inside a template literal
  (written ``\\`…\\```), and a quoted string that is a command on its own
  (``cmd('personalclaw setup')``). A bare backtick span in TypeScript is a template literal or a
  comment, and is not copy.
* **PersonalClaw's own messages**: every string constant under ``src/personalclaw`` that is not a
  docstring, with an f-string's holes read as placeholders. These reach users as CLI output, API
  errors and Doctor rows.

A placeholder (``<archive>``, ``NAME=…``, a ``{…}`` hole) becomes a sample value, so this checks
the words and flags a user types, not the values they fill in.
"""

from __future__ import annotations

import pytest
from cli_commands_in_copy_parse_shared import _INVALID_SAMPLE, _argv, _parse_error


def test_the_parser_refuses_what_this_rail_exists_to_catch():
    """Control: the check sees the defect it was written for."""
    assert "unrecognized arguments: --replace" in _parse_error(
        _argv("personalclaw restore --replace")
    )
    assert _parse_error(_argv("personalclaw restore <archive> --mode replace")) == ""


@pytest.mark.parametrize(
    "said",
    [
        "x: error: argument --store: invalid choice: 'sample1' (choose from 'both', 'memory')",
        "x: error: argument --store: invalid choice: 'sample1' (choose from both, memory)",
    ],
    ids=["quoted", "bare"],
)
def test_a_sample_in_a_choices_slot_is_retried_with_the_first_choice(said):
    """A ``{store}`` hole the copy fills at run time is retried with a value the parser takes,
    whichever way this Python's argparse prints the choices."""
    match = _INVALID_SAMPLE.search(said)
    assert match is not None and match.groups() == ("sample1", "both"), said

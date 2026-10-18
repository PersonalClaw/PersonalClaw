"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_cli_commands_in_copy_parse.py``

Its checks:
``tests/rails/test_cli_commands_in_copy_parse_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

import contextlib
import html
import io
import re
import shlex

from personalclaw.cli import build_parser

_HOLE = re.compile(r"\$?\{[^{}]*\}")


_SAMPLE = "sample{}"


def _argv(command: str) -> list[str]:
    """The words after ``personalclaw``, each placeholder replaced by its own sample value."""
    samples = iter(range(1, 1000))
    text = _HOLE.sub(lambda _m: _SAMPLE.format(next(samples)), html.unescape(command))
    words = shlex.split(text)[1:]
    return [
        (
            _SAMPLE.format(next(samples))
            if word.startswith("<") or word.endswith(">")
            else word.replace("…", _SAMPLE.format(next(samples)))
        )
        for word in words
    ]


_PARSER = build_parser()


#: argparse names the choices quoted on Python 3.13 (``choose from 'both', 'knowledge'``) and bare
#: on 3.12 (``choose from both, knowledge``); both name the first one first.
_INVALID_SAMPLE = re.compile(r"invalid choice: '(sample\d+)' \(choose from '?([^',)]+)")


def _parse_error(argv: list[str]) -> str:
    """What the real parser says about *argv*, or ``""`` when it takes it.

    A sample standing in for a value the copy fills in at run time (``--store {store}``) can
    land where the parser takes only certain values. Then the first value it names is used and
    the parse runs again, so the rest of the command is still checked.
    """
    for _ in range(len(argv) + 1):
        stderr = io.StringIO()
        try:
            with contextlib.redirect_stderr(stderr), contextlib.redirect_stdout(io.StringIO()):
                _PARSER.parse_args(argv)
        except SystemExit as exc:
            if exc.code in (0, None):
                return ""
            said = stderr.getvalue().strip().splitlines()[-1]
            choice = _INVALID_SAMPLE.search(said)
            if not choice:
                return said
            argv = [choice.group(2) if word == choice.group(1) else word for word in argv]
            continue
        return ""
    return "a placeholder never fitted any value the parser takes"

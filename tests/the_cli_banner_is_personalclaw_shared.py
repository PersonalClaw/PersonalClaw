"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_the_cli_banner_is_personalclaw.py``

Its checks:
``tests/rails/test_the_cli_banner_is_personalclaw_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations


def _drawn(line: str) -> bool:
    """A row of drawn letters: mostly the strokes figlet draws with, hardly any letters."""
    chars = [c for c in line.strip() if not c.isspace()]
    return (
        len(chars) >= 6
        and sum(c.isalnum() for c in chars) <= 0.25 * len(chars)
        and sum(c in "_|/\\" for c in chars) >= 3
    )


def _drawn_rows(text: str) -> int:
    """The most drawn rows in a row in *text*."""
    best = run = 0
    for line in text.split("\n"):
        run = run + 1 if _drawn(line) else 0
        best = max(best, run)
    return best

"""What a command prints, and where it goes (docs/reference/cli.md, "Output").

stdout carries only what a command makes, the same at a terminal and in a pipe, so
``personalclaw token | pbcopy`` copies the link and ``url=$(personalclaw token)`` captures it.
What the value is, how long it lasts and what to do next is guidance: it prints on stderr, only
with ``--explain``, after the value and never repeating it. ``--json`` prints one document.

Both writes go through the streams :func:`personalclaw.cli._show_the_streams_as_text` wraps, so
every line still reaches the terminal as text; ``json.dumps`` output is ASCII and passes it
unchanged.
"""

from __future__ import annotations

import json
import sys
from typing import Any


def explain(args: object, *lines: str) -> None:
    """Print *lines* on stderr, one per line, only when the command was run with ``--explain``.

    An empty line is skipped, so a caller can pass a note that may not apply (a container's port
    note outside a container) without testing it first."""
    if not getattr(args, "explain", False):
        return
    for line in lines:
        if line:
            print(line, file=sys.stderr)


def print_json(doc: Any) -> None:
    """Print *doc* as exactly ``json.dumps(doc, indent=2)`` and a newline: the ``--json`` form."""
    print(json.dumps(doc, indent=2))

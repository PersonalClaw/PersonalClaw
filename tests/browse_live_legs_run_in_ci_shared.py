"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_browse_live_legs_run_in_ci.py``

Its checks:
``tests/rails/test_browse_live_legs_run_in_ci_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

import pathlib
import re

_ROOT = pathlib.Path(__file__).resolve().parents[1]


_CI = _ROOT / ".github" / "workflows" / "ci.yml"


def jobs(text: str) -> dict[str, str]:
    """Job id → the block of ``text`` belonging to it.

    A job id is the only key at two-space indent in this file: a job's own keys (``runs-on``,
    ``env``, ``steps``) sit at four, and steps deeper still. So the scan needs no YAML.
    """
    found: dict[str, list[str]] = {}
    current: str | None = None
    in_jobs = False
    for line in text.splitlines():
        if re.match(r"^jobs:\s*$", line):
            in_jobs = True
            continue
        if not in_jobs:
            continue
        if line.strip() and not line.startswith(" ") and not line.startswith("#"):
            break  # back to a top-level key: the jobs mapping ended
        header = re.match(r"^ {2}([A-Za-z][\w-]*):\s*(#.*)?$", line)
        if header:
            current = header.group(1)
            found[current] = []
            continue
        if current is not None:
            found[current].append(line)
    return {name: "\n".join(body) for name, body in found.items()}


def run_commands(block: str) -> list[str]:
    """Every shell command a job's steps run — single-line ``run:`` values and scalar bodies.

    Block scalars matter here: an install step is exactly the sort of thing someone later folds
    into a ``run: |``, and a scan that only saw single lines would then report "this leg does not
    install a browser" (a false red) or, worse, miss a moved pytest invocation.
    """
    out: list[str] = []
    body_indent: int | None = None
    for line in block.splitlines():
        if body_indent is not None:
            stripped = line.strip()
            indent = len(line) - len(line.lstrip())
            if stripped and indent < body_indent:
                body_indent = None
            elif stripped and not stripped.startswith("#"):
                out.append(stripped)
                continue
        head = re.match(r"^(\s*)-?\s*run:\s*(.*?)\s*$", line)
        if not head:
            continue
        value = head.group(2)
        if value in ("|", ">", "|-", ">-", "|+", ">+"):
            body_indent = len(head.group(1)) + 2
        elif value and not value.startswith("#"):
            out.append(value)
            body_indent = None
    return out

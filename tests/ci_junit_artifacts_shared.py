"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_ci_junit_artifacts.py``

Its checks:
``tests/rails/test_ci_junit_artifacts_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]


#: The directory every CI JUnit report is written under. Asserted rather than free-form so
#: ``.gitignore`` can cover the class with one entry instead of one per job.
_REPORT_DIR = "reports/"


def jobs(text: str) -> dict[str, str]:
    """Job id → the block of ``text`` belonging to it.

    A job id is the only key at two-space indent in a workflow: a job's own keys sit at
    four and steps deeper still, so this needs no YAML parser (PyYAML is not a declared
    test dependency).
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
    return {k: "\n".join(v) for k, v in found.items()}


def steps(block: str) -> list[str]:
    """A job block split into its steps — each ``- name:``/``- uses:`` entry and its keys.

    Six spaces is the step-list indent in both workflows; a ``run:`` block scalar's body
    sits at ten or more, so no shell line can be mistaken for a step boundary.
    """
    out: list[str] = []
    current: list[str] | None = None
    for line in block.splitlines():
        if re.match(r"^ {6}-\s", line):
            if current is not None:
                out.append("\n".join(current))
            current = [line]
        elif current is not None:
            current.append(line)
    if current is not None:
        out.append("\n".join(current))
    return out


def commands(step: str) -> list[str]:
    """The shell commands a step runs, one per element, comments dropped.

    The three ``run:`` forms are normalized, because which one a step uses is a legibility
    choice and a rail that cared would break on a reflow:

    * ``run: cmd`` → one command;
    * ``run: |`` (literal) → one per line, joining ``\\``-continuations;
    * ``run: >`` (folded) → the whole payload joined into ONE command, which is what the
      shell receives. ``security-corpus`` spells its invocation this way, so a per-line
      scan would read the flag as living on a different command from the ``pytest``.
    """
    lines = step.splitlines()
    for i, raw in enumerate(lines):
        head = re.match(r"^(\s*)-?\s*run:\s*(.*?)\s*$", raw)
        if not head:
            continue
        indent, inline = len(head.group(1)), head.group(2)
        if inline and inline not in ("|", ">", "|-", ">-", "|+", ">+"):
            return [inline]
        folded = inline.startswith(">")
        body: list[str] = []
        for follow in lines[i + 1 :]:
            if not follow.strip():
                continue
            if len(follow) - len(follow.lstrip()) <= indent:
                break
            text = follow.strip()
            if not text.startswith("#"):
                body.append(text)
        if folded:
            return [" ".join(body)] if body else []
        joined: list[str] = []
        for text in body:
            if joined and joined[-1].endswith("\\"):
                joined[-1] = joined[-1][:-1].rstrip() + " " + text
            else:
                joined.append(text)
        return joined
    return []


def pytest_commands(step: str) -> list[str]:
    """Commands in ``step`` that INVOKE pytest — not ones that merely say the word."""
    return [c for c in commands(step) if re.search(r"(?:^|[\s/])(?:-m\s+)?pytest(?:\s|$)", c)]


def key(step: str, name: str) -> str | None:
    """The value of a step's OWN key (``if``, ``uses``, the display ``name``, …)."""
    hit = re.search(rf"^\s*-?\s*{re.escape(name)}:\s*(.*?)\s*$", step, flags=re.MULTILINE)
    return hit.group(1) if hit else None


def with_key(step: str, name: str) -> str | None:
    """The value of a key inside a step's ``with:`` mapping.

    Separate from :func:`key` because an upload step has TWO keys called ``name``: its own
    display name and the artifact name under ``with:``. MEASURED while mutating this rail —
    a scan that took the first ``name:`` read the display name (which happens to interpolate
    ``${{ matrix.shard }}``), so replacing the ARTIFACT name with a fixed string left the
    collision rail below green. A rail that reads the wrong key is not a rail.
    """
    lines = step.splitlines()
    for i, raw in enumerate(lines):
        head = re.match(r"^(\s*)with:\s*$", raw)
        if not head:
            continue
        base = len(head.group(1))
        for follow in lines[i + 1 :]:
            if not follow.strip():
                continue
            if len(follow) - len(follow.lstrip()) <= base:
                break
            hit = re.match(rf"^\s*{re.escape(name)}:\s*(.*?)\s*$", follow)
            if hit:
                return hit.group(1)
    return None


def junit_uploads(block: str) -> list[str]:
    """Steps in a job that upload a JUnit report."""
    return [
        s
        for s in steps(block)
        if "actions/upload-artifact" in s and "junit" in (with_key(s, "path") or "")
    ]

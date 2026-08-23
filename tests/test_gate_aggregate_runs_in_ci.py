"""The gate AGGREGATE must actually execute in CI, not merely be mentioned there.

`scripts/gate_report.py` is the one surface that reports all six ratchets together
(config-baseline, inert-surface, docs-lint, and the three structural gates). It ran in
**no workflow**: every gate reached CI only through its own pytest counterpart, so a
green build never showed the table an operator reads, and a gate without a pytest twin
reached CI not at all.

This rail asserts the CALL SITE. The distinction matters more than usual here, because
the step's own explanatory comment contains the words ``make gates`` — so a substring
search over ``ci.yml`` would report the aggregate "wired" even if the ``run:`` line were
deleted, which is precisely the shape of the defect this file exists to prevent. Every
assertion below keys on a parsed ``run:`` line.
"""

from __future__ import annotations

import pathlib
import re

import pytest

_REPO = pathlib.Path(__file__).resolve().parents[1]
_CI = _REPO / ".github" / "workflows" / "ci.yml"

#: The script the aggregate lives in. `make gates` is a thin wrapper over it, and CI
#: invokes the script directly so a failure names the gate rather than "make: *** Error 1".
_AGGREGATE = "scripts/gate_report.py"


def _run_lines(text: str) -> list[str]:
    """Every shell line reachable from a ``run:`` key, comments excluded.

    Handles both ``run: cmd`` and the block forms (``run: |`` / ``run: >``). A line whose
    first non-space character is ``#`` is dropped, because a comment cannot execute.
    """
    out: list[str] = []
    lines = text.split("\n")
    for i, raw in enumerate(lines):
        m = re.match(r"^(\s*)-?\s*run:\s*(.*)$", raw)
        if not m:
            continue
        indent, inline = m.group(1), m.group(2).strip()
        if inline and inline not in ("|", ">", "|-", ">-", "|+", ">+"):
            out.append(inline)
            continue
        # Block scalar: take the more-indented lines that follow.
        base = len(indent)
        for follow in lines[i + 1 :]:
            if not follow.strip():
                continue
            if len(follow) - len(follow.lstrip()) <= base:
                break
            body = follow.strip()
            if not body.startswith("#"):
                out.append(body)
    return out


@pytest.fixture(scope="module")
def ci_text() -> str:
    assert _CI.is_file(), f"{_CI} moved — re-point this rail"
    return _CI.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def runs(ci_text: str) -> list[str]:
    return _run_lines(ci_text)


def test_the_parser_found_a_real_workflow(runs: list[str], ci_text: str) -> None:
    """Vacuity floor #1: a matcher that parsed nothing would make every assertion below
    trivially true, which is exactly how a rail reads clean forever."""
    assert len(ci_text) > 2000, "ci.yml is implausibly short — did the read succeed?"
    assert len(runs) > 15, f"parsed only {len(runs)} run-lines out of ci.yml; parser is broken"
    # A control the parser must find, unrelated to this rail's subject.
    assert any(
        "pytest" in r for r in runs
    ), "no pytest step found — the parser is not reading steps"


def test_the_gate_aggregate_is_executed(runs: list[str]) -> None:
    """The load-bearing assertion: some CI step RUNS the aggregate."""
    hits = [r for r in runs if _AGGREGATE in r]
    assert hits, (
        f"no CI step runs {_AGGREGATE}. The six ratchets then reach CI only through their "
        "individual pytest counterparts, and a gate without one does not reach CI at all."
    )


def test_a_comment_alone_does_not_satisfy_this_rail(ci_text: str, runs: list[str]) -> None:
    """Vacuity floor #2, and the reason this file parses instead of grepping.

    The step's own comment says ``make gates``. Assert that the phrase really is present in
    a comment, and that stripping every ``run:`` line would therefore leave a substring
    search satisfied while the aggregate no longer executes.
    """
    commented = [
        ln for ln in ci_text.split("\n") if ln.strip().startswith("#") and "make gates" in ln
    ]
    assert commented, (
        "expected the step's explanatory comment to mention `make gates` — if that comment "
        "was reworded, this floor no longer proves the parser is doing real work"
    )
    # The floor: the phrase in the comment is NOT what satisfies the rail above.
    assert not any("make gates" in r for r in runs) or any(
        _AGGREGATE in r for r in runs
    ), "the rail must key on the aggregate script in a run-line, never on prose"


def test_every_gate_in_the_aggregate_is_named_by_the_script() -> None:
    """The aggregate must still cover the six gates it claims to.

    Guards the other direction: a step that runs `gate_report.py` proves nothing if the
    script silently stopped reporting a gate.
    """
    src = (_REPO / "scripts" / "gate_report.py").read_text(encoding="utf-8")
    expected = (
        "config-baseline",
        "inert-surface",
        "docs-lint",
        "structural-size",
        "structural-import-direction",
        "structural-duplication",
    )
    missing = [g for g in expected if g not in src]
    assert not missing, f"gate_report.py no longer names these gates: {missing}"

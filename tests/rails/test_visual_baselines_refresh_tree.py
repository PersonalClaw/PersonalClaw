"""The Refresh visual baselines workflow concludes on what it wrote (#3943).

``.github/workflows/visual-baselines.yml`` rewrites the UI matrix's baselines and the visual
goldens against the pinned browser. A difference from the committed set is the change it writes,
not a failure (``stepVerdict`` in ``web/e2e/surfaceChecks.ts``), so the run is green only when the
set it wrote is complete, and its summary lists each file it changed. The rules, each failed by a
planted edit below:

1. The matrix's update writes a JSON report, and ``e2e/assert-no-route-skips.mjs`` reads that
   report after it: a route scan that skipped, or ran fewer steps than the matrix has, is a
   surface with no output, and a red.
2. ``e2e/visual-coverage.mjs`` runs after the visual update: a surface the visual spec
   snapshots with no golden is a red.
3. ``e2e/baseline-changes.mjs`` lists the changed files after both updates.
4. Each of the three runs before the upload and after a failed step (``if: ${{ !cancelled() }}``),
   so a red run still says what it found and what it wrote.
5. The matrix runs again as a compare after its update and before the upload: a baseline it did
   not write, one a second run does not reproduce, or a dark tree that differs from the light one
   (one file holds both) is red there.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest
import yaml
from rails_repo import REPO

_WORKFLOW = ".github/workflows/visual-baselines.yml"
_MATRIX = "node e2e/run-pinned.mjs --update -- e2e/surfaces.spec.ts"
_VISUAL = "node e2e/run-pinned.mjs --update -- e2e/visual.spec.ts"
_ROUTES = "node e2e/assert-no-route-skips.mjs"
_COVERAGE = "node e2e/visual-coverage.mjs"
_LIST = "node e2e/baseline-changes.mjs"
_COMPARE = "node e2e/run-pinned.mjs -- e2e/surfaces.spec.ts"
_AFTER_A_FAILURE = "${{ !cancelled() }}"


def _steps() -> list[dict[str, Any]]:
    workflow = yaml.safe_load((REPO / _WORKFLOW).read_text(encoding="utf-8"))
    return list(workflow["jobs"]["refresh"]["steps"])


def _at(steps: list[dict[str, Any]], command: str) -> int | None:
    return next((i for i, s in enumerate(steps) if str(s.get("run", "")).strip() == command), None)


def _problems(steps: list[dict[str, Any]]) -> list[str]:
    matrix, visual = _at(steps, _MATRIX), _at(steps, _VISUAL)
    upload = next(
        (
            i
            for i, s in enumerate(steps)
            if str(s.get("uses", "")).startswith("actions/upload-artifact@")
        ),
        None,
    )
    if matrix is None or visual is None or upload is None:
        return [f"{_WORKFLOW} no longer runs `{_MATRIX}`, `{_VISUAL}` and the upload"]
    bad: list[str] = []
    report = (steps[matrix].get("env") or {}).get("PLAYWRIGHT_JSON_OUTPUT_NAME")
    if not report:
        bad.append(f"`{_MATRIX}` writes no JSON report (PLAYWRIGHT_JSON_OUTPUT_NAME)")
    checks = [(f"{_ROUTES} {report}", matrix), (_COVERAGE, visual), (_LIST, max(matrix, visual))]
    for command, after in checks:
        i = _at(steps, command)
        if i is None:
            bad.append(f"{_WORKFLOW} does not run `{command}`")
        elif not after < i < upload:
            bad.append(
                f"`{command}` does not run after `{steps[after]['run']}` and before the upload"
            )
        elif steps[i].get("if") != _AFTER_A_FAILURE:
            bad.append(f"`{command}` does not run after a failed step (if: {_AFTER_A_FAILURE})")
    compare = _at(steps, _COMPARE)
    if compare is None:
        bad.append(f"{_WORKFLOW} does not compare the matrix's set (`{_COMPARE}`)")
    elif not matrix < compare < upload:
        bad.append(f"`{_COMPARE}` does not run after `{_MATRIX}` and before the upload")
    return bad


def test_the_refresh_checks_what_it_wrote_and_lists_it() -> None:
    assert _problems(_steps()) == []


def _without(command: str) -> list[dict[str, Any]]:
    return [s for s in _steps() if not str(s.get("run", "")).startswith(command)]


def _moved_first(command: str) -> list[dict[str, Any]]:
    steps = _steps()
    i = next(i for i, s in enumerate(steps) if str(s.get("run", "")).startswith(command))
    return [steps[i], *steps[:i], *steps[i + 1 :]]


def _unconditional(command: str) -> list[dict[str, Any]]:
    steps = copy.deepcopy(_steps())
    for step in steps:
        if str(step.get("run", "")).startswith(command):
            step.pop("if", None)
    return steps


def _no_report() -> list[dict[str, Any]]:
    steps = copy.deepcopy(_steps())
    for step in steps:
        if str(step.get("run", "")).strip() == _MATRIX:
            step.pop("env", None)
    return steps


@pytest.mark.parametrize(
    "plant, says",
    [
        (lambda: _without(_ROUTES), f"does not run `{_ROUTES}"),
        (lambda: _without(_COVERAGE), f"does not run `{_COVERAGE}`"),
        (lambda: _without(_LIST), f"does not run `{_LIST}`"),
        (lambda: _moved_first(_ROUTES), "and before the upload"),
        (lambda: _moved_first(_COVERAGE), "and before the upload"),
        (lambda: _moved_first(_LIST), "and before the upload"),
        (lambda: _unconditional(_COVERAGE), "after a failed step"),
        (lambda: _unconditional(_LIST), "after a failed step"),
        (_no_report, "writes no JSON report"),
        (lambda: _without(_COMPARE), "does not compare the matrix's set"),
        (lambda: _moved_first(_COMPARE), f"`{_COMPARE}` does not run after"),
    ],
    ids=[
        "no-route-check",
        "no-coverage",
        "no-list",
        "route-check-first",
        "coverage-first",
        "list-first",
        "coverage-unconditional",
        "list-unconditional",
        "no-report",
        "no-compare",
        "compare-first",
    ],
)
def test_a_planted_refresh_fails(plant: Any, says: str) -> None:
    found = _problems(plant())
    assert any(says in p for p in found), found

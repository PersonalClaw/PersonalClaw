"""The exemplars back the done-when: they discover, they run, and they prove the mechanism.

Atom SV-8 (§4.1): `harness/exemplars/` holds a runnable exemplar per landed WF2 slice — a
standalone spec + a ≤30s smoke script + a rationale note — and "a test proves they run".
This is that test. It is the "proves they run" half of the done-when.

Each smoke script drives its exemplar through the REAL engine with a fake/seeded model (no
network, no real LLM), under an isolated PERSONALCLAW_HOME, and exits 0 only when the slice's
mechanism produced its expected outcome (e.g. Slice 2's run ends FAILED with the
required_artifacts gate unsatisfied). Running the smoke script — the artifact a human runs —
is what makes this a regression anchor rather than a paraphrase of it.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from harness.exemplars import discover_exemplars, exemplars_root, incomplete_slices

_REPO_ROOT = Path(__file__).resolve().parent.parent

#: The WF2 slices the plan names as landed and in scope for the SV-8 backfill (§4.1: Slices
#: 0-5, with Slice 2 as the named required_artifacts example). Pinning the set here means a
#: slice dropped from the tree fails this test rather than silently shrinking coverage.
_EXPECTED_SLICES = {"slice_0", "slice_1", "slice_2", "slice_3", "slice_4", "slice_5"}

_EXEMPLARS = discover_exemplars()
_IDS = [e.slice for e in _EXEMPLARS]


def test_the_landed_slices_each_have_a_complete_exemplar() -> None:
    """Every expected slice has all three contract files, and none is half-landed.

    `incomplete_slices` is the mechanical half of the plan's same-PR rule: a slice directory
    missing its exemplar/smoke/rationale shows up here (a slice merged without its exemplar
    is visible), instead of being quietly skipped by discovery.
    """
    assert (
        not incomplete_slices()
    ), "these slice dirs are missing one of exemplar.py / smoke.sh / RATIONALE.md: " + ", ".join(
        incomplete_slices()
    )
    found = set(_IDS)
    assert (
        _EXPECTED_SLICES <= found
    ), f"missing exemplars for landed WF2 slices: {sorted(_EXPECTED_SLICES - found)}"


def test_there_is_at_least_one_exemplar() -> None:
    """A guard against the discovery silently finding nothing (a passing-because-empty test
    is how a backfill obligation rots)."""
    assert _EXEMPLARS, "no exemplars discovered under harness/exemplars/"


@pytest.mark.parametrize("exemplar", _EXEMPLARS, ids=_IDS)
def test_each_exemplar_smoke_script_runs_and_proves_its_mechanism(exemplar) -> None:
    """The smoke script runs to completion and its exemplar self-asserts the outcome.

    Exit 0 IS the proof: each `exemplar.py::main` returns non-zero if the slice mechanism did
    not behave (e.g. Slice 2's run must end FAILED, not COMPLETE). A 60s ceiling here is
    double the plan's 30s target — comfortably a failure signal, not a tight race.
    """
    proc = subprocess.run(
        ["bash", str(exemplar.smoke)],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, (
        f"{exemplar.slice} smoke failed (exit {proc.returncode}).\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    # The exemplar prints a single "PASS <slice>: ..." line naming what it proved.
    assert (
        f"PASS {exemplar.slice}" in proc.stdout
    ), f"{exemplar.slice} smoke exited 0 but did not print its PASS line:\n{proc.stdout}"


def test_slice_2_is_the_required_artifacts_example() -> None:
    """The done-when names Slice 2 specifically: a 3-node run with a failing
    required_artifacts gate. Running it directly (in-process) proves that exact shape, and
    keeps the named example from drifting away from a generic gate demo."""
    root = exemplars_root() / "slice_2"
    result = subprocess.run(
        [sys.executable, "-m", "harness.exemplars.slice_2.exemplar"],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        env=_isolated_env(),
    )
    assert result.returncode == 0, f"slice_2 exemplar failed:\n{result.stdout}\n{result.stderr}"
    assert "artifact gate failed" in result.stdout
    assert (root / "exemplar.py").is_file()


def _isolated_env() -> dict[str, str]:
    import os
    import tempfile

    env = dict(os.environ)
    env["PERSONALCLAW_HOME"] = tempfile.mkdtemp(prefix="pclaw-exemplar-")
    return env


# ── The interpreter the smoke scripts run on (#2718) ────────────────────────────────────────────
#
# Every smoke script used to resolve `.venv/bin/python` against its OWN repo root and fall back to
# a bare `python3`. A `git worktree` has no `.venv`, so in a worktree the fallback took an
# interpreter with no `personalclaw` installed and the exemplar died on `ModuleNotFoundError` — in
# THIS file, which the change under review never touched. Three contributors each spent a
# diagnosis on it, and the conclusion was recorded as folklore rather than fixed.
#
# These pin the two properties that keep it fixed. The first is the one that matters: six copies of
# the resolution is what let one bug live in all six at once, so the rail is that there is exactly
# ONE copy.

_RESOLVER = exemplars_root() / "resolve_py.sh"


def test_the_interpreter_rule_has_exactly_one_owner() -> None:
    """No smoke script may resolve its own interpreter — they all source the shared resolver.

    This is the anti-recurrence half: a seventh slice added by copying an existing smoke script
    inherits the sourced line, and a slice that hand-rolls the old `|| PY="python3"` fallback fails
    here by name instead of silently reintroducing #2718 for whoever next works in a worktree.
    """
    smokes = [e.smoke for e in _EXEMPLARS]
    assert smokes, "no exemplars discovered — this rail would pass vacuously"
    assert _RESOLVER.is_file(), f"the shared resolver is missing: {_RESOLVER}"

    unsourced, rerolled = [], []
    for smoke in smokes:
        body = smoke.read_text(encoding="utf-8")
        if "resolve_py.sh" not in body:
            unsourced.append(smoke.name and f"{smoke.parent.name}/{smoke.name}")
        # The exact shape that was wrong: a bare `python3` accepted as an interpreter.
        if 'PY="python3"' in body:
            rerolled.append(f"{smoke.parent.name}/{smoke.name}")
    assert not unsourced, f"these smoke scripts do not source resolve_py.sh: {unsourced}"
    assert not rerolled, (
        "these smoke scripts fall back to a bare `python3`, which has none of this repo's dev "
        f"dependencies and turns a wrong-interpreter error into a missing-module one: {rerolled}"
    )


def test_an_unresolvable_interpreter_says_so_instead_of_failing_later(tmp_path: Path) -> None:
    """With nothing to resolve, the resolver exits 1 naming the knob — not a module error.

    Driven for real in a directory that is not a git repo and has no `.venv`, because the whole
    defect was a fallback that *succeeded* at picking an interpreter and failed 30 lines later. A
    test asserting the message without exercising the no-candidate path would not have caught it.
    """
    (tmp_path / "resolve_py.sh").write_text(_RESOLVER.read_text(encoding="utf-8"), encoding="utf-8")
    driver = tmp_path / "drive.sh"
    driver.write_text(
        'set -euo pipefail\nREPO_ROOT="$(pwd)"\nsource ./resolve_py.sh\necho "PY=$PY"\n',
        encoding="utf-8",
    )
    env = {k: v for k, v in __import__("os").environ.items() if k != "PERSONALCLAW_PY"}
    proc = subprocess.run(
        ["bash", str(driver)], cwd=tmp_path, capture_output=True, text=True, timeout=60, env=env
    )
    assert proc.returncode == 1, f"expected a loud refusal, got {proc.returncode}:\n{proc.stdout}"
    assert "PERSONALCLAW_PY" in proc.stderr, f"the refusal must name the knob:\n{proc.stderr}"
    assert "python3" in proc.stderr, "it must say why a bare python3 is not used"
    assert "PY=" not in proc.stdout, "it must not resolve anything on the no-candidate path"


def test_personalclaw_py_wins_when_set(tmp_path: Path) -> None:
    """An explicit interpreter overrides both search paths — how CI and non-venv setups pin it."""
    (tmp_path / "resolve_py.sh").write_text(_RESOLVER.read_text(encoding="utf-8"), encoding="utf-8")
    driver = tmp_path / "drive.sh"
    driver.write_text(
        'set -euo pipefail\nREPO_ROOT="$(pwd)"\nsource ./resolve_py.sh\necho "PY=$PY"\n',
        encoding="utf-8",
    )
    env = dict(__import__("os").environ)
    env["PERSONALCLAW_PY"] = "/bin/sh"
    proc = subprocess.run(
        ["bash", str(driver)], cwd=tmp_path, capture_output=True, text=True, timeout=60, env=env
    )
    assert proc.returncode == 0, f"{proc.stdout}\n{proc.stderr}"
    assert "PY=/bin/sh" in proc.stdout, proc.stdout

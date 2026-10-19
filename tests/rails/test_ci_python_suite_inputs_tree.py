"""``python-suite``'s declared inputs cover every path the suite reads (#3945).

The planner leaves ``python-suite`` out of a plan when no changed path is one of its inputs, so an
input list that misses a path the suite reads would let a change to that path skip the suite. Two
census halves say what the suite reads:

- **The whole-tree sweep.** ``tests/test_licence_governance.py`` checks every tracked text file of
  the repository, through ``licence_governance_shared._walk`` and the sweep's own filter
  (``_CANDIDATE_NAMES``, ``_TEXT_SUFFIXES``, ``_SKIP_FILES``). This rail walks with the same
  helper and the same filter, so the two cannot drift apart.
- **The paths a suite module names.** Every string literal in a suite module (``tests/`` outside
  ``tests/rails``, helpers included) that names a tracked path, or whose first part is a tracked
  top-level entry, joined or not.

Each path either half finds must be reached by ``python-suite``'s inputs. It reads the tree, so it
is a rail. The planted controls run the same check on an input list with one entry dropped.
"""

from __future__ import annotations

import ast
import subprocess
from functools import lru_cache
from pathlib import Path

import pytest
from licence_governance_shared import _CANDIDATE_NAMES, _SKIP_FILES, _TEXT_SUFFIXES, _walk
from rails_repo import REPO

from tools.ci import catalogue
from tools.ci import plan as planner

_INPUTS = catalogue.read(REPO).job("python-suite").inputs


@lru_cache(maxsize=1)
def _tracked_tops() -> frozenset[str]:
    listed = subprocess.run(
        ["git", "ls-tree", "--name-only", "HEAD"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return frozenset(listed.split())


@lru_cache(maxsize=1)
def swept() -> tuple[str, ...]:
    """Every file the licence sweep reads, relative to the root, as the sweep filters them."""
    out = []
    for path in _walk(REPO):
        if path.name in _SKIP_FILES:
            continue
        if not _CANDIDATE_NAMES.match(path.name) and path.suffix.lower() not in _TEXT_SUFFIXES:
            continue
        out.append(path.relative_to(REPO).as_posix())
    return tuple(sorted(out))


def _suite_modules() -> list[Path]:
    return [p for p in sorted((REPO / "tests").rglob("*.py")) if "rails" not in p.parts]


@lru_cache(maxsize=1)
def named() -> dict[str, tuple[str, ...]]:
    """Each repository path a suite module names, with the modules that name it."""
    tops = _tracked_tops()
    found: dict[str, set[str]] = {}
    for module in _suite_modules():
        try:
            tree = ast.parse(module.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        rel = module.relative_to(REPO).as_posix()
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
                continue
            text = node.value.strip().removeprefix("./")
            if not text or "\n" in text or len(text) > 300 or " " in text:
                continue
            first = text.split("/")[0]
            if first in tops:
                found.setdefault(text.rstrip("/"), set()).add(rel)
    return {path: tuple(sorted(mods)) for path, mods in sorted(found.items())}


def covered(inputs: tuple[str, ...], path: str) -> bool:
    """Whether ``inputs`` reach ``path``. A path the census found may name a directory (a module
    that joins ``"docs"`` onto the root, say), and the suite may read any file under it, so a
    directory counts as covered only when an arbitrary file under it is reached."""
    return planner.reaches(inputs, path) or planner.reaches(inputs, path.rstrip("/") + "/x")


def uncovered(inputs: tuple[str, ...]) -> list[str]:
    """Each path the census finds that ``inputs`` do not reach, with where it came from."""
    out = [f"{p} (the licence sweep)" for p in swept() if not planner.reaches(inputs, p)]
    for path, modules in named().items():
        if not covered(inputs, path):
            out.append(f"{path} (named by {', '.join(modules[:3])})")
    return out


def test_the_census_is_not_vacuous() -> None:
    assert len(swept()) > 3000, len(swept())
    assert len(named()) > 200, len(named())
    for top in ("src", "tests", "web", "docs", "pyproject.toml"):
        assert any(p == top or p.startswith(top + "/") for p in swept()), top


def test_python_suites_inputs_cover_every_path_the_suite_reads() -> None:
    missing = uncovered(_INPUTS)
    assert not missing, (
        "python-suite's inputs (tools/ci/catalogue.toml) miss paths the suite reads, so a change"
        " to one would plan the suite out:\n  " + "\n  ".join(missing[:30])
    )


def test_python_suites_inputs_are_an_explicit_list_not_the_whole_tree() -> None:
    """A whole-tree input names no path, so a src/ diff would plan the whole tier."""
    assert not any(planner.whole_tree(spec) for spec in _INPUTS), _INPUTS


@pytest.mark.parametrize("dropped", ["docs/", "README.md", "src/", ".githooks/", "uv.lock"])
def test_an_input_list_missing_an_entry_the_suite_reads_fails(dropped: str) -> None:
    """The planted control: drop one entry and the rail names what it no longer covers."""
    assert dropped in _INPUTS, dropped
    narrower = tuple(spec for spec in _INPUTS if spec != dropped)
    missing = uncovered(narrower)
    stem = dropped.rstrip("/")
    assert any(m == stem or m.startswith(stem + "/") or m.startswith(stem + " ") for m in missing)


def test_only_the_flake_is_outside_python_suites_inputs() -> None:
    """Today the suite reads the whole tree but the flake; each entry that leaves the list as the
    whole-tree readers move to tests/rails is a reviewed edit of this row."""
    outside = sorted(t for t in _tracked_tops() if not covered(_INPUTS, t))
    assert outside == ["flake.lock", "flake.nix"], outside

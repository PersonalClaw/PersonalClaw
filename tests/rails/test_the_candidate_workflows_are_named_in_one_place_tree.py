"""The workflows a candidate's checks run in are named in one place, so the flip strands no reader.

Until the change that retires ``ci.yml`` (#3915), a candidate's checks run in two shapes:
``ci.yml``'s one run today, ``pr.yml``'s and ``merge.yml``'s pair beside it on dispatch and in its
place after.
``tools/ci/workflows.py`` names the three, and a runtime reader that takes its names from there
reads both shapes while both exist. A reader that spells ``ci.yml`` itself handles one shape, and
the day ``ci.yml`` goes it reads nothing, silently: the telemetry collector would stop collecting,
the rails floor would stop measuring. So:

1. No file under ``tools/``, ``scripts/``, ``src/`` or ``.github/`` spells ``ci.yml`` in its code (a
   Python string other than a docstring; any other file's lines but its comments), other than the
   registry and ``ci.yml`` itself, unless ``tests/rails/baselines/ci-yml-readers.sites.jsonl``
   lists the file with the reason it still does. Each line is a reader the flip converts, and the
   flip lands with the list empty. So the list only shrinks, and that is held, not hoped: against
   the gate's base commit a line the base lacks fails, though another baseline would take it as a
   reviewed exception with ``added`` and a reason.
2. No ``workflow_run`` listener follows ``CI`` without following the tier workflow that takes its
   place, unless the list names it the same way (``<path>::workflow_run CI``).
3. ``workflows.NAMES`` is each workflow's own ``name:``.

The suite's tests are not held here: one that reads ``ci.yml`` fails loudly when the flip deletes
it, and the flip moves or retires each such row with a map in its commit body.
"""

from __future__ import annotations

import ast
import json
import os
import re
import subprocess
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import git_helper_guard
import pytest
import site_baseline
import yaml
from rails_repo import REPO

from personalclaw.sdk.testing import neutral_git_env
from tools.ci import workflows

#: The readers that still spell ci.yml, each with its reason.
CI_YML_READERS = "tests/rails/baselines/ci-yml-readers.sites.jsonl"
#: Where a runtime reader lives.
_ROOTS = ("tools", "scripts", "src", ".github")
#: The registry, and the workflow the flip deletes.
_EXEMPT = frozenset({"tools/ci/workflows.py", f".github/workflows/{workflows.LEGACY}"})
#: Another workflow whose file name ends in the same letters, never a read of ci.yml.
_NOT_IT = re.compile(r"[\w.-]ci\.yml")
#: The lines of a file other than Python that are its comments.
_COMMENT = re.compile(r"^\s*(#|//|\*|/\*)")


def _names_it(text: str) -> bool:
    return workflows.LEGACY in _NOT_IT.sub("", text)


def _docstrings(tree: ast.AST) -> set[int]:
    out: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                out.add(id(body[0].value))
    return out


def spells_ci_yml(rel: str, text: str) -> bool:
    """Whether a file's code names ci.yml: a Python string other than a docstring, or a line of
    any other file but its comments."""
    if rel.endswith(".py"):
        try:
            tree = ast.parse(text)
        except SyntaxError:
            return _names_it(text)
        skip = _docstrings(tree)
        return any(
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in skip
            and _names_it(node.value)
            for node in ast.walk(tree)
        )
    return any(_names_it(line) for line in text.splitlines() if not _COMMENT.match(line))


def _tracked(root: Path) -> list[str]:
    done = subprocess.run(
        ["git", "ls-files", "-z", "--", *_ROOTS], cwd=root, capture_output=True, check=True
    )
    return sorted(p for p in done.stdout.decode().split("\0") if p)


def _listener_sites(rel: str, text: str) -> list[str]:
    """A workflow_run listener of CI that does not follow the tier workflow taking its place."""
    if not rel.startswith(".github/workflows/") or not rel.endswith((".yml", ".yaml")):
        return []
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        return []
    on = (data or {}).get(True) or (data or {}).get("on") or {}
    run = on.get("workflow_run") if isinstance(on, dict) else None
    followed = set((run or {}).get("workflows") or []) if isinstance(run, dict) else set()
    legacy = workflows.NAMES[workflows.LEGACY]
    tiers = {workflows.NAMES[workflows.FEEDBACK], workflows.NAMES[workflows.MERGE]}
    if legacy in followed and not followed & tiers:
        return [f"{rel}::workflow_run {legacy}"]
    return []


def sites(root: Path = REPO) -> list[str]:
    """Each reader of one shape in the tree: a file that spells ci.yml, and a listener of CI
    alone."""
    found: list[str] = []
    for rel in _tracked(root):
        path = root / rel
        if rel in _EXEMPT or not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if spells_ci_yml(rel, text):
            found.append(rel)
        found += _listener_sites(rel, text)
    return found


def check_readers(
    found: Iterable[str], base: Any = site_baseline.FROM_THE_GATE, *, root: Path = REPO
) -> None:
    """Hold the readers *found* to the list (``site_baseline.check``), and the list to shrinking:
    each line it holds that *base* lacks fails, ``added`` and a reason or not. With no base, a
    local run, only the first holds."""
    added = site_baseline.check(found, CI_YML_READERS, base, root=root)
    assert not added, "\n".join(
        f"{CI_YML_READERS} only shrinks until the flip empties it, and this branch adds "
        f"{line.site}: take its workflow names from tools/ci/workflows.py, so it reads both "
        "shapes, and drop its line"
        for line in added
    )


def test_no_reader_handles_only_ci_yml_unlisted() -> None:
    """Every runtime reader of ci.yml is listed with its reason, a listed one that no longer
    reads it fails on its stale line, and against the gate's base no line is new."""
    found = sites()
    assert len(found) >= 1, "the scan found no reader at all: the walk is broken, not the code"
    check_readers(found)


def test_each_workflow_is_named_as_its_file_names_it() -> None:
    for name, title in workflows.NAMES.items():
        data = yaml.safe_load((REPO / ".github" / "workflows" / name).read_text(encoding="utf-8"))
        assert data["name"] == title, name


@pytest.mark.parametrize(
    ("rel", "text", "reads"),
    [
        ("tools/ci/x.py", 'WORKFLOW = "ci.yml"\n', True),
        ("tools/ci/x.py", 'PATH = f".github/workflows/{name}"\nNAME = "ci.yml"\n', True),
        ("tools/ci/x.py", '"""Reads ci.yml\'s runs."""\nX = 1\n', False),
        ("tools/ci/x.py", "# ci.yml\nX = 1\n", False),
        ("tools/ci/x.py", 'X = "ci-telemetry.yml"\n', False),
        ("tools/ci/x.py", 'X = "nix-ci.yml"\n', False),
        (".github/workflows/x.yml", "jobs:\n  a:\n    run: cat .github/workflows/ci.yml\n", True),
        (".github/workflows/x.yml", "# reads ci.yml\njobs: {}\n", False),
        ("tools/ci/x.toml", 'inputs = [".github/workflows/ci.yml"]\n', True),
        ("scripts/x.mjs", "// ci.yml runs this\nconst x = 1;\n", False),
    ],
)
def test_what_counts_as_spelling_ci_yml(rel: str, text: str, reads: bool) -> None:
    assert spells_ci_yml(rel, text) is reads


@pytest.mark.parametrize(
    ("followed", "site"),
    [
        (["CI"], True),
        (["CI", "Breadth"], True),
        (["CI", "Merge", "Breadth"], False),
        (["CI", "PR"], False),
        (["Merge"], False),
        (["Breadth"], False),
    ],
)
def test_a_listener_of_ci_alone_is_a_site(followed: list[str], site: bool) -> None:
    text = yaml.safe_dump({"name": "X", "on": {"workflow_run": {"workflows": followed}}})
    assert bool(_listener_sites(".github/workflows/x.yml", text)) is site


def test_a_planted_reader_fails_by_name_and_a_converted_one_by_its_line(tmp_path: Path) -> None:
    """The positive control, on the real list: the tree's readers with a planted one fail naming
    it, and the tree's less one listed reader fail on that reader's stale line."""
    listed = (REPO / CI_YML_READERS).read_text(encoding="utf-8")
    found = sites()
    with pytest.raises(AssertionError, match=r"Site tools/ci/planted\.py is not listed"):
        site_baseline.check([*found, "tools/ci/planted.py"], CI_YML_READERS, None, prune=False)
    gone, *rest = sorted(found)
    with pytest.raises(AssertionError, match=f"Site {re.escape(gone)} is listed in"):
        site_baseline.check(rest, CI_YML_READERS, None, prune=False)
    assert (REPO / CI_YML_READERS).read_text(encoding="utf-8") == listed


def _git(repo: Path, *args: str) -> str:
    env = os.environ | neutral_git_env(git_helper_guard.own_global_config())
    done = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, env=env, check=True
    )
    return done.stdout.strip()


def _commit(repo: Path, lines: list[dict[str, Any]]) -> str:
    """Commit *lines* as the list in *repo*, and return the commit."""
    (repo / CI_YML_READERS).write_text(
        "".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8"
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "the list")
    return _git(repo, "rev-parse", "HEAD")


def test_a_branch_that_adds_a_reader_fails_though_its_line_has_a_reason(tmp_path: Path) -> None:
    """Shrink-only, held: in a repository of its own holding the real list and its registry line,
    a branch that adds one reader with ``added`` and a reason, which another baseline takes as a
    reviewed exception, fails naming it against the base without it, and passes with no base. A
    branch that deletes a reader passes against the base that has it."""
    real = site_baseline.load(REPO / CI_YML_READERS)
    lines = [{"site": line.site, "reason": line.reason} for line in real]
    registry = (REPO / site_baseline.REGISTRY).read_text(encoding="utf-8").splitlines()
    repo = tmp_path / "repo"
    (repo / CI_YML_READERS).parent.mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    (repo / site_baseline.REGISTRY).write_text(
        "".join(line + "\n" for line in registry if CI_YML_READERS in line), encoding="utf-8"
    )
    without_first = _commit(repo, lines[1:])
    every = [str(line["site"]) for line in lines]
    added = [{**lines[0], "added": "2026-10-23"}, *lines[1:]]
    with_first = _commit(repo, added)
    with pytest.raises(AssertionError, match=f"this branch adds {re.escape(every[0])}:") as grew:
        check_readers(every, without_first, root=repo)
    assert str(grew.value).count("this branch adds") == 1, grew.value
    check_readers(every, None, root=repo)
    _commit(repo, lines[1:])
    check_readers(every[1:], with_first, root=repo)

"""The workflows a candidate's checks run in are named in one place, and the deleted one by none.

``ci.yml`` ran a candidate's checks until the flip (#3915) gave its triggers to ``pr.yml`` and
``merge.yml`` and deleted it. ``tools/ci/workflows.py`` names the two, and every runtime reader of a
candidate's runs takes its names from there. A reader that still named ``ci.yml`` would read
nothing, silently: a collector that collects no run, a floor that measures none. Before the flip
the readers that named it were listed, the list only shrank, and the flip landed with it empty.
So now:

1. No file under ``tools/``, ``scripts/``, ``src/`` or ``.github/`` names ``ci.yml`` in its code (a
   Python string other than a docstring; any other file's lines but its comments).
2. No ``workflow_run`` listener follows ``CI``, the deleted workflow's name, which no run carries.
3. ``workflows.NAMES`` is each workflow's own ``name:``.

The suite's tests are not held here: a test that read ``ci.yml`` would fail loudly on the missing
file, and the flip moved or retired each one, with a map in its commit body.
"""

from __future__ import annotations

import ast
import re
import subprocess
from pathlib import Path

import pytest
import yaml
from rails_repo import REPO

from tools.ci import workflows

#: The workflow the flip deleted, and the name its runs carried.
DELETED, DELETED_NAME = "ci.yml", "CI"
#: Where a runtime reader lives.
_ROOTS = ("tools", "scripts", "src", ".github")
#: Another workflow whose file name ends in the same letters, never a read of ci.yml.
_NOT_IT = re.compile(r"[\w.-]ci\.yml")
#: The lines of a file other than Python that are its comments.
_COMMENT = re.compile(r"^\s*(#|//|\*|/\*)")


def _names_it(text: str) -> bool:
    return DELETED in _NOT_IT.sub("", text)


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
    """A workflow_run listener that follows the deleted workflow's name."""
    if not rel.startswith(".github/workflows/") or not rel.endswith((".yml", ".yaml")):
        return []
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        return []
    on = (data or {}).get(True) or (data or {}).get("on") or {}
    run = on.get("workflow_run") if isinstance(on, dict) else None
    followed = set((run or {}).get("workflows") or []) if isinstance(run, dict) else set()
    return [f"{rel}::workflow_run {DELETED_NAME}"] if DELETED_NAME in followed else []


def sites(root: Path = REPO) -> list[str]:
    """Each reader of the deleted workflow in the tree: a file that names it, and a listener of
    its name."""
    found: list[str] = []
    for rel in _tracked(root):
        path = root / rel
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if spells_ci_yml(rel, text):
            found.append(rel)
        found += _listener_sites(rel, text)
    return found


def test_no_reader_names_the_deleted_workflow() -> None:
    """The tree reads the deleted workflow nowhere, and the scan reads the tree."""
    assert len(_tracked(REPO)) > 1000, "the scan read too few files: the walk is broken"
    found = sites()
    assert not found, (
        f"these name {DELETED}, which the flip deleted (#3915), so they read nothing: take the"
        f" workflow names from tools/ci/workflows.py: {found}"
    )


def test_each_workflow_is_named_as_its_file_names_it() -> None:
    for name, title in workflows.NAMES.items():
        data = yaml.safe_load((REPO / ".github" / "workflows" / name).read_text(encoding="utf-8"))
        assert data["name"] == title, name


def test_the_deleted_workflow_is_gone() -> None:
    assert not (REPO / ".github" / "workflows" / DELETED).exists()
    assert DELETED not in workflows.NAMES and DELETED_NAME not in workflows.NAMES.values()


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
def test_what_counts_as_naming_the_deleted_workflow(rel: str, text: str, reads: bool) -> None:
    assert spells_ci_yml(rel, text) is reads


@pytest.mark.parametrize(
    ("followed", "site"),
    [
        (["CI"], True),
        (["CI", "Merge", "Breadth"], True),
        (["Merge", "Breadth"], False),
        (["PR"], False),
    ],
)
def test_a_listener_of_the_deleted_workflow_is_a_site(followed: list[str], site: bool) -> None:
    text = yaml.safe_dump({"name": "X", "on": {"workflow_run": {"workflows": followed}}})
    assert bool(_listener_sites(".github/workflows/x.yml", text)) is site


def test_a_planted_reader_is_found_by_name(tmp_path: Path) -> None:
    """The positive control: a tracked file of a repository of its own that names the deleted
    workflow, and a listener of its name, are each found."""
    repo = tmp_path / "repo"
    (repo / "tools").mkdir(parents=True)
    (repo / ".github" / "workflows").mkdir(parents=True)
    (repo / "tools" / "reader.py").write_text('WORKFLOW = "ci.yml"\n', encoding="utf-8")
    listener = yaml.safe_dump({"on": {"workflow_run": {"workflows": ["CI"]}}})
    (repo / ".github" / "workflows" / "l.yml").write_text(listener, encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    assert sites(repo) == [".github/workflows/l.yml::workflow_run CI", "tools/reader.py"]

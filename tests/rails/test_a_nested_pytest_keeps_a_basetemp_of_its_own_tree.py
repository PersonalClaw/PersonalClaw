"""Every pytest a test starts keeps a base temporary folder of its own (tests/nested_pytest.py).

A nested pytest given no ``--basetemp`` shares the ``pytest-of-<user>`` root with every other
session, and at its end deletes their old finished trees: breadth's random-order leg spent a
nested pytest's whole 30 s that way (run 40199713134). So a test in ``tests/`` starts a pytest
through :mod:`nested_pytest` alone: a command that starts with ``*nested_pytest.argv(...)``, or
one that puts its ``basetemp`` right after ``"pytest"``.
This rail reads every module under ``tests/`` for a command that spells ``-m pytest``, a list or
a tuple with ``"-m"`` then ``"pytest"``, and fails on each one that takes no
``nested_pytest.basetemp`` and that :data:`ALLOWED` does not name with why it runs no pytest;
and it holds the helper to giving that ``--basetemp`` under the folder it is given.

A pytest started as ``python -c <code>``, code that calls ``pytest.main``, is one too: the code a
string, an f-string or a module-level name bound to one. Its arguments are built apart from the
command, so the function that starts it takes ``nested_pytest.basetemp`` for them.
"""

from __future__ import annotations

import ast
from pathlib import Path

import nested_pytest
import pytest
from rails_repo import REPO

TESTS = REPO / "tests"

#: The helper, the one place a nested pytest's command is spelled, and this rail, which spells it
#: to look for it.
HELPER = "tests/nested_pytest.py"
SELF = "tests/rails/test_a_nested_pytest_keeps_a_basetemp_of_its_own_tree.py"

#: Each site that spells ``-m pytest`` and starts no pytest, by its file and the function it is in,
#: with why.
ALLOWED: dict[tuple[str, str], str] = {
    ("tests/rails/test_ci_contract.py", "_root_pytest"): "matches a workflow's command, runs none",
    ("tests/test_real_home_guard.py", "test_other_children_are_not_this_guard_s_business"): (
        "a command the guard judges, never run"
    ),
    ("tests/test_site_baseline.py", "test_prune_runs_every_registered_rail_in_prune_mode"): (
        "the command site_baseline builds, as asserted"
    ),
}


def _spells_pytest(node: ast.AST) -> bool:
    """A list or tuple with ``"-m"`` then ``"pytest"`` that takes no ``nested_pytest.basetemp``."""
    if not isinstance(node, (ast.List, ast.Tuple)):
        return False
    words = [e.value if isinstance(e, ast.Constant) else None for e in node.elts]
    if not any(words[i : i + 2] == ["-m", "pytest"] for i in range(len(words) - 1)):
        return False
    return not any(_asks_the_helper(e) for e in node.elts)


def _text(node: ast.AST, strings: dict[str, str]) -> str | None:
    """The code *node* spells: a string, an f-string's own text, a name bound to a string,
    ``textwrap.dedent`` of one, or strings added together."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(
            part.value
            for part in node.values
            if isinstance(part, ast.Constant) and isinstance(part.value, str)
        )
    if isinstance(node, ast.Name):
        return strings.get(node.id)
    if isinstance(node, ast.Call) and node.args:
        name = getattr(node.func, "attr", getattr(node.func, "id", ""))
        return _text(node.args[0], strings) if name == "dedent" else None
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _text(node.left, strings), _text(node.right, strings)
        return None if left is None and right is None else (left or "") + (right or "")
    return None


def _strings(tree: ast.Module) -> dict[str, str]:
    """The module's names bound to a string: code a ``-c`` command can run."""
    found: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            text = _text(node.value, {})
            if isinstance(target, ast.Name) and text is not None:
                found[target.id] = text
    return found


def _runs_pytest_code(node: ast.AST, strings: dict[str, str]) -> bool:
    """A list or tuple ``[..., "-c", <code>, ...]`` whose code calls ``pytest.main``."""
    if not isinstance(node, (ast.List, ast.Tuple)):
        return False
    for word, code in zip(node.elts, node.elts[1:]):
        if isinstance(word, ast.Constant) and word.value == "-c":
            text = _text(code, strings)
            if text is not None and "pytest.main(" in text:
                return True
    return False


def _asks_the_helper(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in ("basetemp", "argv")
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "nested_pytest"
    )


def sites(source: str) -> list[tuple[str, int]]:
    """Each command in *source* that spells ``-m pytest`` with no basetemp of the helper's, or
    runs ``pytest.main`` code from a function that takes none, as the function it is in
    (``<module>`` outside any) and its line."""
    tree = ast.parse(source)
    strings = _strings(tree)
    found: list[tuple[str, int]] = []

    def visit(node: ast.AST, scope: str, owner: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            named = isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            inner, inner_owner = (child.name, child) if named else (scope, owner)
            if _spells_pytest(child) or (
                _runs_pytest_code(child, strings)
                and not any(_asks_the_helper(n) for n in ast.walk(owner))
            ):
                found.append((scope, child.lineno))
            visit(child, inner, inner_owner)

    visit(tree, "<module>", tree)
    return found


def stray(root: Path) -> list[str]:
    """Each command under *root*'s ``tests/`` that spells ``-m pytest`` outside the helper and
    :data:`ALLOWED`, as ``file:line (function)``."""
    out: list[str] = []
    for path in sorted((root / "tests").rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        if rel in (HELPER, SELF):
            continue
        for scope, line in sites(path.read_text(encoding="utf-8")):
            if (rel, scope) not in ALLOWED:
                out.append(f"{rel}:{line} ({scope})")
    return out


@pytest.mark.timeout(60)
def test_every_nested_pytest_starts_through_the_helper():
    found = stray(REPO)
    assert not found, (
        "these start a pytest of their own with no basetemp from nested_pytest, so it shares the "
        "pytest-of-<user> root and cleans other sessions' trees at its end. Start the command "
        "with *nested_pytest.argv(tmp_path), or put nested_pytest.basetemp(tmp_path) right after "
        '"pytest" (or, for a pytest.main run by -c, among its arguments), or name a site that '
        f"runs no pytest in ALLOWED: {found}"
    )


def test_every_allowed_site_still_spells_pytest():
    """Parses only the files ALLOWED names: the census of every module is the test above's."""
    named = {rel for rel, _ in ALLOWED}
    files = [
        path for path in sorted(TESTS.rglob("*.py")) if path.relative_to(REPO).as_posix() in named
    ]
    seen = {
        (path.relative_to(REPO).as_posix(), scope)
        for path in files
        for scope, _ in sites(path.read_text(encoding="utf-8"))
    }
    assert sorted(set(ALLOWED) - seen) == [], "an ALLOWED entry names nothing now; remove it"


def test_the_helper_gives_a_basetemp_under_the_folder_it_is_given(tmp_path):
    command = nested_pytest.argv(tmp_path, "-q", "tests")
    assert command[1:3] == ["-m", "pytest"] and command[-2:] == ["-q", "tests"], command
    given = [word for word in command if word.startswith("--basetemp=")]
    assert len(given) == 1, f"no --basetemp, or more than one: {command}"
    assert Path(given[0].removeprefix("--basetemp=")).parent == tmp_path, given


def test_a_command_that_spells_pytest_itself_fails_and_one_through_the_helper_passes(tmp_path):
    """The planted tree: one test spells the command, list and tuple both, one names a basetemp
    of its own spelling, and one takes the helper's, both ways; one runs pytest.main code by -c,
    a module's name and an f-string, with no basetemp, and one gives that code the helper's. Only
    the spelled ones and the code alone are named."""
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_planted.py").write_text(
        "import subprocess, sys\n"
        "import nested_pytest\n\n\n"
        "def test_spelled(tmp_path):\n"
        '    subprocess.run([sys.executable, "-m", "pytest", "-q"])\n'
        '    subprocess.run((sys.executable, "-m", "pytest"))\n'
        '    subprocess.run([sys.executable, "-m", "pytest", f"--basetemp={tmp_path}"])\n\n\n'
        "def test_through_the_helper(tmp_path):\n"
        '    subprocess.run(nested_pytest.argv(tmp_path, "-q"))\n'
        '    subprocess.run([sys.executable, "-m", "pytest", nested_pytest.basetemp(tmp_path)])\n'
        "\n\n"
        'CHILD = "import pytest, sys; sys.exit(pytest.main(sys.argv[1:]))"\n\n\n'
        "def test_code_alone(tmp_path):\n"
        '    args = ["-q"]\n'
        '    subprocess.run([sys.executable, "-c", CHILD, "-q"])\n'
        '    subprocess.run([sys.executable, "-c", f"import pytest; pytest.main({args!r})"])\n\n\n'
        "def test_code_given_the_helpers_basetemp(tmp_path):\n"
        '    subprocess.run([sys.executable, "-c", CHILD, nested_pytest.basetemp(tmp_path)])\n',
        encoding="utf-8",
    )
    assert stray(tmp_path) == [
        "tests/test_planted.py:6 (test_spelled)",
        "tests/test_planted.py:7 (test_spelled)",
        "tests/test_planted.py:8 (test_spelled)",
        "tests/test_planted.py:21 (test_code_alone)",
        "tests/test_planted.py:22 (test_code_alone)",
    ]

"""A test undoes only the patches it made itself, never the suite's.

🔴 The defect. ``monkeypatch`` is one object per test, shared by the test and by every autouse
fixture that asked for it before the test began, the suite's home isolation among them
(``conftest.py``'s ``_isolate_real_home_writers`` points ``config_dir()`` at a scratch home). So a
bare ``monkeypatch.undo()`` inside a test undid more than the test's own patches: everything the
test ran after it ran with the developer's real home as the home. A write there is refused by the
real-home guard, and the test failed on the guard instead of on what it exists to show. One of the
scanner's meta-tests failed that way on every CI leg that ran it once the skill installer's lock
file started going through the atomic writer, which asks ``config_dir()`` where the home is.

A patch a test wants to take back before it ends goes in a context of its own, whose exit undoes
only what was patched inside it::

    with monkeypatch.context() as m:
        m.setattr(module, "name", value)
        ...

What this file holds: a rail over every module under ``tests/`` that no function calls
``.undo()`` on the ``monkeypatch`` fixture it was handed, or on a name or attribute it stored the
fixture in; and the rail's detector against the shapes it must catch and the ones it must pass.
A helper that takes the fixture under another parameter name is out of its reach.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

_TESTS = Path(__file__).resolve().parent

#: The pytest fixture's name, which is what a test's parameter must be called to receive it.
_FIXTURE = "monkeypatch"


def _takes_the_fixture(node: ast.AST) -> bool:
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return False
    args = node.args
    return any(arg.arg == _FIXTURE for arg in (*args.posonlyargs, *args.args, *args.kwonlyargs))


def _undo_target(node: ast.AST) -> str | None:
    """What ``X.undo()`` is called on, as written, or ``None`` if *node* is not such a call."""
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "undo"
        and not node.args
        and not node.keywords
    ):
        return ast.unparse(node.func.value)
    return None


def _undos_of_the_fixture(tree: ast.AST) -> list[tuple[int, str]]:
    """Every ``.undo()`` called on the ``monkeypatch`` fixture, as ``(line, function)``.

    Followed into the names a function gives the fixture (``mp = monkeypatch``), into closures
    over it, and into attributes it is stored on (``self._mp = monkeypatch``), which may be undone
    from another method of the class.
    """
    found: dict[tuple[int, int], str] = {}
    stored_on: set[str] = set()
    for fn in ast.walk(tree):
        if not _takes_the_fixture(fn):
            continue
        names = {_FIXTURE}
        for node in ast.walk(fn):
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Name):
                if node.value.id in names:
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            names.add(target.id)
                        elif isinstance(target, ast.Attribute):
                            stored_on.add(ast.unparse(target))
        for node in ast.walk(fn):
            if _undo_target(node) in names:
                found.setdefault((node.lineno, node.col_offset), fn.name)
    for scope in ast.walk(tree):
        if not isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for node in ast.walk(scope):
            if _undo_target(node) in stored_on:
                found.setdefault((node.lineno, node.col_offset), scope.name)
    return sorted((line, fn) for (line, _col), fn in found.items())


# ── the detector ──────────────────────────────────────────────────────────────────────────────

_CAUGHT = {
    "the fixture itself": (
        "def test_x(monkeypatch):\n    monkeypatch.setattr(m, 'a', 1)\n    monkeypatch.undo()\n"
    ),
    "an async test": "async def test_x(monkeypatch):\n    monkeypatch.undo()\n",
    "a name it was given to": "def test_x(monkeypatch):\n    mp = monkeypatch\n    mp.undo()\n",
    "a helper it was handed to": "def _helper(monkeypatch):\n    monkeypatch.undo()\n",
    "a closure over it": (
        "def test_x(monkeypatch):\n    def later():\n        monkeypatch.undo()\n    later()\n"
    ),
    "an attribute it was stored on": (
        "class Harness:\n"
        "    def __init__(self, monkeypatch):\n"
        "        self._mp = monkeypatch\n"
        "    def reset(self):\n"
        "        self._mp.undo()\n"
    ),
}

_PASSED = {
    "a context of the fixture": (
        "def test_x(monkeypatch):\n"
        "    with monkeypatch.context() as m:\n"
        "        m.setattr(x, 'a', 1)\n"
        "        m.undo()\n"
    ),
    "a private instance": "def test_x():\n    monkey = pytest.MonkeyPatch()\n    monkey.undo()\n",
    "a private instance beside the fixture": (
        "def test_x(monkeypatch):\n    mp = pytest.MonkeyPatch()\n    mp.undo()\n"
    ),
    "another object's undo": "def test_x(monkeypatch):\n    guard.undo()\n",
    "the name, outside any function that takes the fixture": (
        "def test_x():\n    monkeypatch.undo()\n"
    ),
}


@pytest.mark.parametrize("shape", sorted(_CAUGHT))
def test_the_detector_catches(shape: str) -> None:
    assert _undos_of_the_fixture(ast.parse(_CAUGHT[shape])), f"{shape} was not caught"


@pytest.mark.parametrize("shape", sorted(_PASSED))
def test_the_detector_passes(shape: str) -> None:
    assert _undos_of_the_fixture(ast.parse(_PASSED[shape])) == [], f"{shape} was caught"


# ── the rail ──────────────────────────────────────────────────────────────────────────────────


#: A call of ``.undo`` spells the word in the module's text, so a module without it has none.
_MENTIONS_UNDO = re.compile(r"\bundo\b")


def test_no_test_undoes_the_monkeypatch_it_shares_with_the_suite() -> None:
    modules = sorted(_TESTS.rglob("*.py"))
    read = taking = undos = 0
    offenders: list[str] = []
    for path in modules:
        source = path.read_text(encoding="utf-8")
        if not _MENTIONS_UNDO.search(source):
            continue
        read += 1
        tree = ast.parse(source, filename=str(path))
        taking += sum(1 for node in ast.walk(tree) if _takes_the_fixture(node))
        undos += sum(1 for node in ast.walk(tree) if _undo_target(node) is not None)
        offenders += [
            f"{path.relative_to(_TESTS)}:{line} in {fn}" for line, fn in _undos_of_the_fixture(tree)
        ]
    assert len(modules) > 1000, f"the scan found {len(modules)} modules under {_TESTS}"
    # The scan saw the calls it judges, and the functions it judges them in, so a pass is a
    # finding and not a scan that matched nothing.
    assert undos > 0, f"no `.undo()` call in the {read} modules that mention it"
    assert taking > 0, f"no function takes the fixture in the {read} modules read"
    assert offenders == [], (
        "these undo every patch on the fixture, the suite's home isolation included; put the "
        "patches they mean to take back in `with monkeypatch.context() as m:` instead:\n  "
        + "\n  ".join(offenders)
    )

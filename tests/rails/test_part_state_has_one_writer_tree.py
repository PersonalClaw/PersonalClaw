"""A part's state changes only through ``turns.advance``, so every move is one §3.4 checks.

Shared contracts §3.4: a tool part's state moves along a closed table of edges, and the approval,
question, sign-in and helper parts along their own. ``turns.advance`` is the one function that
holds the tables. A second writer skips them: a part built straight into ``completed``, or copied
with a new ``state``, may say a call finished that never started. So this rail fails, anywhere in
the package but ``turns.py``, on:

- a part class of ``turns`` called with a ``state=`` keyword (a part is made in its first state);
- ``replace(…, state=…)`` (``dataclasses.replace``, by either spelling) in a module that imports
  ``personalclaw.turns``;
- ``object.__setattr__(…, "state", …)``, the way past a frozen dataclass.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable
from pathlib import Path

import source_index

from personalclaw import turns

HOME = "src/personalclaw/turns.py"
PART_CLASSES = frozenset(cls.__name__ for cls in turns.PART_KINDS.values())


def _imports_turns(tree: ast.Module) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module == "personalclaw.turns" or (
                node.module == "personalclaw" and any(a.name == "turns" for a in node.names)
            ):
                return True
        elif isinstance(node, ast.Import) and any(
            a.name == "personalclaw.turns" for a in node.names
        ):
            return True
    return False


def _callee(node: ast.Call) -> str:
    func = node.func
    return (
        func.id
        if isinstance(func, ast.Name)
        else func.attr if isinstance(func, ast.Attribute) else ""
    )


def _findings(files: Iterable[tuple[str, ast.Module]]) -> list[str]:
    found: list[str] = []
    for rel, tree in files:
        if rel == HOME:
            continue
        uses_turns = _imports_turns(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = _callee(node)
            sets_state = any(k.arg == "state" for k in node.keywords)
            if sets_state and name in PART_CLASSES:
                found.append(
                    f"{rel}:{node.lineno}: {name}(state=…) makes a part past its first state"
                )
            elif sets_state and name == "replace" and uses_turns:
                found.append(f"{rel}:{node.lineno}: replace(…, state=…) moves a part past advance")
            elif (
                name == "__setattr__"
                and len(node.args) >= 2
                and isinstance(node.args[1], ast.Constant)
                and node.args[1].value == "state"
                and uses_turns
            ):
                found.append(
                    f"{rel}:{node.lineno}: __setattr__(…, 'state', …) moves a part past advance"
                )
    return sorted(found)


def _package() -> list[tuple[str, ast.Module]]:
    trees = source_index.package_trees()
    assert len(trees) > 500, f"walked {len(trees)} modules — vacuous"
    return [(source_index.rel(Path(path)), tree) for path, tree in trees]


def test_no_module_but_turns_moves_a_parts_state() -> None:
    assert _findings(_package()) == []


def test_the_one_writer_is_walked_and_skipped() -> None:
    assert HOME in {rel for rel, _ in _package()}, "turns.py is not where this rail looks for it"


def test_each_way_past_advance_is_caught() -> None:
    planted = ast.parse(
        "import dataclasses\n"
        "from personalclaw import turns\n"
        "from personalclaw.turns import ToolPart\n"
        "a = ToolPart(id='p', at='t', updated_at='t', state='completed')\n"
        "b = turns.ApprovalPart(id='p', at='t', updated_at='t', state='approved')\n"
        "c = dataclasses.replace(a, state='failed')\n"
        "object.__setattr__(a, 'state', 'unknown')\n"
        "d = ToolPart(id='p', at='t', updated_at='t', title='made in its first state')\n"
        "e = dataclasses.replace(a, title='a new title')\n"
    )
    lines = [int(f.split(":")[1]) for f in _findings([("src/personalclaw/planted.py", planted)])]
    assert lines == [4, 5, 6, 7]
    assert _findings([(HOME, planted)]) == [], "turns.py is the one place a state is set"
    elsewhere = ast.parse("import dataclasses\nx = dataclasses.replace(job, state='done')\n")
    assert _findings([("src/personalclaw/jobs.py", elsewhere)]) == [], "another type's state"

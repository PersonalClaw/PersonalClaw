"""No function in core grows past a readable size (#4015).

A function longer than :data:`MAX_LINES` lines, or with a cyclomatic complexity over
:data:`MAX_COMPLEXITY`, is one nobody can hold in their head: the native loop's ``stream`` was 544
lines and 76 branches when it was split into phases, and the chat runner's ``run_chat`` is over
3,600 lines. The rail (``tests/rails/test_function_size_tree.py``) measures every function under
``src/personalclaw`` and fails one over either limit that :data:`BASELINE` does not list. The ones
over when the rail came are listed there, one line each, a site baseline
(``tests/site_baseline.py``) that only shrinks: a function brought under both limits has its line
deleted, a listed function that grew past the size its line records fails, and a line this branch
adds is named to the review.

The measure, the same for every function, a method or a nested one alike:

- **lines**: the span of the ``def``, from its ``def`` line to its last (its decorators not
  counted), comments and blank lines included, since they are read too;
- **complexity**: 1, plus one for each ``if`` (``elif`` included), ``for``, ``async for``,
  ``while``, ``except`` and ``case``, each operand of a boolean operator after its first, each
  ``if`` of a comprehension, and each conditional expression (``x if c else y``).

A nested function is measured on its own and counts in its parent too, lines and branches alike:
a closure is read as part of the function it sits in, and a long body must not pass by moving
into a nested ``def``. A ``lambda`` counts in the function it sits in.

A site is ``path::qualified.name`` (``Class.method``, ``outer.inner``); two functions of one name
in one module (an overload's stubs and its body) are told apart, in source order, as ``#2``.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable, Iterator
from dataclasses import dataclass

from site_baseline import Line, numbered

#: The list of the functions still allowed over a limit, from the repository's root.
BASELINE = "tests/rails/baselines/function-size.sites.jsonl"

MAX_LINES = 300
MAX_COMPLEXITY = 30

_FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)
#: The statements and clauses that each add one branch.
_BRANCHES = (ast.If, ast.For, ast.AsyncFor, ast.While, ast.ExceptHandler, ast.match_case, ast.IfExp)


def complexity(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    """*fn*'s cyclomatic complexity by the rule above, its nested functions included."""
    count = 1
    for node in ast.walk(fn):
        if isinstance(node, _BRANCHES):
            count += 1
        elif isinstance(node, ast.BoolOp):
            count += len(node.values) - 1
        elif isinstance(node, ast.comprehension):
            count += len(node.ifs)
    return count


def lines(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    """*fn*'s span in lines, from its ``def`` line to its last."""
    return (fn.end_lineno or fn.lineno) - fn.lineno + 1


@dataclass(frozen=True)
class Measure:
    """One function: its site, and its size by the two measures."""

    site: str
    lines: int
    complexity: int

    @property
    def over(self) -> bool:
        return self.lines > MAX_LINES or self.complexity > MAX_COMPLEXITY


def _functions(
    tree: ast.AST, scope: str = ""
) -> Iterator[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]]:
    """Every function under *tree* in source order, with its qualified name."""
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, _FUNCTIONS):
            name = f"{scope}{node.name}"
            yield name, node
            yield from _functions(node, f"{name}.")
        elif isinstance(node, ast.ClassDef):
            yield from _functions(node, f"{scope}{node.name}.")
        else:
            yield from _functions(node, scope)


def measure(rel: str, tree: ast.AST) -> list[Measure]:
    """Every function in one module's *tree*, named for *rel*, in source order."""
    found = list(_functions(tree))
    sites = numbered(f"{rel}::{name}" for name, _ in found)
    return [Measure(site, lines(fn), complexity(fn)) for site, (_, fn) in zip(sites, found)]


def measure_all(trees: Iterable[tuple[str, ast.AST]]) -> list[Measure]:
    """Every function of *trees*, ``(path, tree)`` pairs, in order."""
    return [m for rel, tree in trees for m in measure(rel, tree)]


def over_the_limits(measures: Iterable[Measure]) -> list[Measure]:
    return [m for m in measures if m.over]


def grown(measures: Iterable[Measure], listed: Iterable[Line]) -> list[str]:
    """Each listed function that is now larger than its line records, by either measure, and
    each line that records no size: the list holds a function at the size it had, never more."""
    recorded = {line.site: line.fields for line in listed}
    failures: list[str] = []
    for m in measures:
        fields = recorded.get(m.site)
        if fields is None:
            continue
        was_lines, was_complexity = fields.get("lines"), fields.get("complexity")
        if not isinstance(was_lines, int) or not isinstance(was_complexity, int):
            failures.append(f"{m.site}: its line records no `lines` and `complexity` as counts")
        elif m.lines > was_lines or m.complexity > was_complexity:
            failures.append(
                f"{m.site} grew to {m.lines} lines and complexity {m.complexity}, past the "
                f"{was_lines} lines and complexity {was_complexity} its line records: split it "
                "instead of letting it grow"
            )
    return failures

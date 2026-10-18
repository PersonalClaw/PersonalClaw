"""The source-tree checks split from
``tests/test_computer_use_live_view.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import ast
import inspect

import computer_use_live_view_shared
from computer_use_live_view_shared import SRC

from personalclaw.computer_use import service

_isolated = computer_use_live_view_shared._isolated


def _registered_computer_use_routes() -> set[tuple[str, str]]:
    """(verb, path) for every /api/computer-use route the route table registers, by AST."""
    tree = ast.parse((SRC / "dashboard" / "routes.py").read_text(encoding="utf-8"))
    found: set[tuple[str, str]] = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        verb = node.func.attr
        if not verb.startswith("add_"):
            continue
        args = [a.value for a in node.args if isinstance(a, ast.Constant)]
        paths = [a for a in args if isinstance(a, str) and a.startswith("/api/computer-use")]
        for path in paths:
            found.add((verb.removeprefix("add_"), path))
    return found


def test_the_computer_use_route_surface_is_pinned():
    """One POST that can act, one GET that can only look. Exact equality against a non-empty
    expected set is its own vacuity floor: a scanner gone blind returns ``set()`` and reds."""
    assert _registered_computer_use_routes() == {
        ("post", "/api/computer-use/dispatch"),
        ("get", "/api/computer-use/live-view"),
    }, (
        "the /api/computer-use route surface changed. The views must add no route that can "
        "act — a new verb here needs the whole dispatch chain and its own change, not a view."
    )


def test_the_observation_runs_after_the_audit_and_before_the_driver():
    """Source-order companion (the `test_the_dispatch_calls_the_screens_in_source_order`
    idea): the observe call sits after the LAST `_audit` (the approved row — a refused
    attempt must never be observed as a landing) and before the LAST `_run_driver` (the
    acting call — "will land" must stay the true tense)."""
    tree = ast.parse(inspect.getsource(service.computer_dispatch))
    calls = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = (
                node.func.attr
                if isinstance(node.func, ast.Attribute)
                else getattr(node.func, "id", "")
            )
            if name in {"_audit", "observe_action", "_run_driver"}:
                calls.append((node.lineno, name))
    order = [name for _line, name in sorted(calls)]
    assert "observe_action" in order, "the dispatch no longer feeds the overlay"
    observe = order.index("observe_action")
    assert (
        max(i for i, n in enumerate(order) if n == "_audit")
        < observe
        < max(i for i, n in enumerate(order) if n == "_run_driver")
    ), order

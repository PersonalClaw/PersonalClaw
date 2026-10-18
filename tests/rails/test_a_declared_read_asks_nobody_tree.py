"""The source-tree checks split from
``tests/test_a_declared_read_asks_nobody.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import ast

import source_index
from rails_repo import REPO

_SRC = REPO / "src" / "personalclaw"


def _tool_definition_calls() -> list[tuple[str, ast.Call]]:
    """Every call that builds a ``ToolDefinition`` in the package, under any alias it is
    imported as (the runtime builds its meta-tools as ``_TD``)."""
    found: list[tuple[str, ast.Call]] = []
    for path, tree in source_index.package_trees():
        names = {"ToolDefinition"} | {
            alias.asname
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            for alias in node.names
            if alias.name == "ToolDefinition" and alias.asname
        }
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                called = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
                if called in names:
                    found.append((f"{path.relative_to(_SRC)}:{node.lineno}", node))
    return found


def _is_safe_literal(expr: ast.expr) -> bool:
    return isinstance(expr, ast.Attribute) and expr.attr == "SAFE"


def _is_level_literal(expr: ast.expr) -> bool:
    return isinstance(expr, ast.Attribute) and expr.attr in ("SAFE", "CAUTION", "DESTRUCTIVE")


def test_the_census_finds_the_tool_definitions():
    """The floor: a walk that found none would pass every rule below on nothing."""
    assert len(_tool_definition_calls()) >= 40


def test_every_tool_core_defines_states_what_it_does_and_whether_it_asks():
    """A tool that states nothing is a tool nobody decided about. The defaults fail closed (a
    change that asks), which is safe, but silent: a new read that forgets to say so asks in a
    chat and is declined in every headless run, so the rail makes the decision explicit."""
    silent = [
        where
        for where, call in _tool_definition_calls()
        if not {"risk_level", "requires_approval"} <= {kw.arg for kw in call.keywords}
    ]
    assert not silent, f"definitions that leave risk_level or requires_approval unsaid: {silent}"


def test_a_tool_that_declares_a_read_asks_nobody():
    """A literal `SAFE` comes with a literal `requires_approval=False`, and a declaration that is
    computed decides whether the tool asks from the same value, never a hard-coded `True`."""
    wrong = []
    for where, call in _tool_definition_calls():
        kw = {k.arg: k.value for k in call.keywords}
        risk, asks = kw.get("risk_level"), kw.get("requires_approval")
        if risk is None or asks is None:
            continue  # the rule above names these
        if _is_safe_literal(risk):
            if not (isinstance(asks, ast.Constant) and asks.value is False):
                wrong.append(f"{where}: declares SAFE but asks")
        elif not _is_level_literal(risk):
            risk_names = {n.id for n in ast.walk(risk) if isinstance(n, ast.Name)}
            asks_names = {n.id for n in ast.walk(asks) if isinstance(n, ast.Name)}
            if not risk_names & asks_names:
                wrong.append(f"{where}: its declaration is computed but whether it asks is not")
    assert not wrong, wrong

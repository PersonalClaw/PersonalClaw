"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_browser_gate_stays_out_of_pytest.py``

Its checks:
``tests/rails/test_browser_gate_stays_out_of_pytest_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

import ast
import pathlib

_ROOT = pathlib.Path(__file__).resolve().parents[1]


_TESTS = _ROOT / "tests"


#: The target that owns the browser leg.
BROWSER_TARGET = "test-e2e"


#: Tokens that mean "this call launches the browser gate".
_BROWSER_TOKENS = ("playwright", "npx")


#: Callables that hand a command line to the OS.
_SPAWNERS = {
    "run",
    "Popen",
    "call",
    "check_call",
    "check_output",
    "system",
    "spawn",
    "create_subprocess_exec",
    "create_subprocess_shell",
}


def spawns_the_browser_gate(source: str) -> list[str]:
    """Subprocess-style calls in `source` whose literal arguments name the browser gate.

    Takes source text so the vacuity tests below can drive the SAME function with synthetic
    modules in both directions.
    """
    hits: list[str] = []
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = None
        if isinstance(node.func, ast.Attribute):
            name = node.func.attr
        elif isinstance(node.func, ast.Name):
            name = node.func.id
        if name not in _SPAWNERS:
            continue
        words: list[str] = []
        for arg in [*node.args, *(kw.value for kw in node.keywords)]:
            for sub in ast.walk(arg):
                if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                    words.append(sub.value.lower())
        joined = " ".join(words)
        if any(tok in joined for tok in _BROWSER_TOKENS):
            hits.append(f"{name}() at line {node.lineno}")
    return hits

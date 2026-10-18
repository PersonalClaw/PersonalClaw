"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_workflows_validator_supervisor.py``

Its checks:
``tests/rails/test_workflows_validator_supervisor_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

import ast


def _count_policy_callers(tree: ast.AST) -> int:
    """Count CALLS that construct a ``SupervisorPolicy`` or invoke ``parse_supervisor_policy``.

    Call-based, matching the `detectors.gate` census precedent: a "production caller" is code
    that WIRES the policy into behaviour, not code that merely imports the closed field set to
    validate against it. Catches both bare (`parse_supervisor_policy(...)`) and attribute
    (`sp.SupervisorPolicy(...)`) call shapes.
    """
    count = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute):
            name = func.attr
        elif isinstance(func, ast.Name):
            name = func.id
        else:
            name = ""
        if name in ("SupervisorPolicy", "parse_supervisor_policy"):
            count += 1
    return count

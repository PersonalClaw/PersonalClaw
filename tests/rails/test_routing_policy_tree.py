"""The source-tree checks split from
``tests/test_routing_policy.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import ast

import routing_policy_shared
from routing_policy_shared import BRIDGE

_ollama_is_installed = routing_policy_shared._ollama_is_installed


def _bridge_function(name: str) -> ast.FunctionDef:
    tree = ast.parse(BRIDGE.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name} not found in provider_bridge.py")


def test_route_refs_is_called_inside_resolve_provider_for_use_case() -> None:
    """The anti-dead-control rail: ``route_refs`` must be INVOKED at the resolution seam.

    Asserted on the AST of ``resolve_provider_for_use_case`` itself, so moving the call to a
    helper that nothing runs, or reducing it to a bare import, fails here.
    """
    fn = _bridge_function("resolve_provider_for_use_case")
    called = {
        node.func.id
        for node in ast.walk(fn)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "route_refs" in called, "resolve_provider_for_use_case does not CALL route_refs"
    assert "routing_active" in called, "the routing seam does not check per-use-case enablement"

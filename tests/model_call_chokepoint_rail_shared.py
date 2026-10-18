"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_model_call_chokepoint_rail.py``

Its checks:
``tests/rails/test_model_call_chokepoint_rail_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

import ast
import pathlib

SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "personalclaw"


BRIDGE = SRC / "providers" / "provider_bridge.py"


#: The public resolver every caller uses to obtain a model provider.
RESOLVER = "resolve_provider_for_use_case"


#: Functions allowed to produce the resolver's return value, each verified to funnel into the
#: chokepoint. Adding an entry here is the explicit, reviewable act of declaring a new path
#: guarded — which is the point: it cannot happen by accident.
ALLOWED_BUILDERS = {
    # wraps directly, at the single point where the entry name + model are known
    "_resolve_from_config_registry",
    # native-agent runtime: its INNER inference provider resolves through the same path
    "_build_native_runtime",
    # ACP agent runtime (``acp:<cli>``): an EXTERNAL CLI owns the model call, out of
    # process and on its own vendor account. There is no host-side inference for
    # ModelCallGuard to wrap — the same reason the pooled ACP claim in SessionManager has
    # never gone through the chokepoint either. On a metered axis its turns are metered by
    # the provider itself (``acp.spend.AcpTurnMeter``: the budgets, the charge and the audit
    # row, from what the CLI reports at the end of each turn).
    "_build_acp_runtime",
}


def _find_func(tree: ast.Module, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(
        f"{name}() is gone from {BRIDGE.name}. This rail is pinned to it by name; re-derive the "
        f"rail against whatever replaced it rather than deleting the rail."
    )


def _call_name(node: ast.AST) -> str | None:
    """The called function's bare name, for `f(...)` and `mod.f(...)` alike."""
    if not isinstance(node, ast.Call):
        return None
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def _names_bound_from_allowed(func: ast.AST) -> set[str]:
    """Local names assigned (anywhere in the function) from an allowlisted builder call.

    Deliberately not flow-sensitive: this rail asks "was this name EVER produced by a guarded
    builder", which is the question that makes a bypass visible. A name assigned from both a
    guarded and an unguarded source would pass — but that shape does not exist here, and the
    `return <raw call>` form (the one an accident actually takes) is caught directly below.
    """
    bound: set[str] = set()
    for node in ast.walk(func):
        if isinstance(node, ast.Assign):
            if _call_name(node.value) in ALLOWED_BUILDERS:
                for tgt in node.targets:
                    if isinstance(tgt, ast.Name):
                        bound.add(tgt.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.value is not None and _call_name(node.value) in ALLOWED_BUILDERS:
                bound.add(node.target.id)
    return bound


def _unguarded_returns(func: ast.AST) -> list[tuple[int, str]]:
    """`(lineno, source-ish)` for each return whose value is not traceable to a guarded builder."""
    guarded_names = _names_bound_from_allowed(func)
    bad: list[tuple[int, str]] = []
    for node in ast.walk(func):
        if not isinstance(node, ast.Return) or node.value is None:
            continue
        v = node.value
        # `return None` / `return ""` — not a provider.
        if isinstance(v, ast.Constant):
            continue
        # `return _resolve_from_config_registry(...)`
        if _call_name(v) in ALLOWED_BUILDERS:
            continue
        # `x = _resolve_from_config_registry(...); … ; return x`
        if isinstance(v, ast.Name) and v.id in guarded_names:
            continue
        bad.append((node.lineno, ast.dump(v)[:120]))
    return bad

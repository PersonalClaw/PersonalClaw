"""The source-tree checks split from
``tests/test_a_private_chat_controls_only_its_own_runs.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import ast

import source_index

from personalclaw import mcp_workflows

_HANDLERS = source_index.REPO / "src" / "personalclaw" / "workflows" / "handlers.py"


def _operations_guarded(tree: ast.Module) -> set[str]:
    """The operations a route of the workflow handlers asks the rule about: the string argument
    of each ``_guard(request, "<op>")`` call, and of each ``_reentry(request, "<op>", ...)`` call,
    which asks ``_guard`` for the operation it is handed."""
    found: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in {"_guard", "_reentry"}
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and isinstance(node.args[1].value, str)
        ):
            found.add(node.args[1].value)
    return found


def test_every_workflow_tool_operation_is_one_a_gateway_route_guards():
    """Each operation a workflow tool that changes something names (``mcp_workflows._CHANGES``)
    is one a gateway route asks the private-chat rule about.

    Why a rail and not a behaviour test: a tool and its route are two doors to the same change,
    and a Temporary or Incognito chat may only make it through either when the rule allows. A
    tool naming an operation no route guards is held to a rule nobody else applies under that
    name, so the two doors drift apart; driving every route under every restricted mode to show
    this would be a test per operation for a property that is the shape of one table."""
    tree = source_index.tree(_HANDLERS)
    guarded = _operations_guarded(tree)
    assert len(guarded) >= 5, "the handlers' guards were not found, so this reads nothing"
    unguarded = set(mcp_workflows._CHANGES.values()) - guarded
    assert not unguarded, f"no gateway route guards {sorted(unguarded)}"


def test_the_shared_reentry_route_asks_the_rule_for_the_operation_it_is_handed():
    """``_reentry`` serves two routes (rewind and run-from) and counts as their guard above, so
    it must itself ask ``_guard`` for the operation its caller hands it."""
    tree = source_index.tree(_HANDLERS)
    (reentry,) = [
        n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "_reentry"
    ]
    asks = [
        n
        for n in ast.walk(reentry)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == "_guard"
        and len(n.args) >= 2
        and isinstance(n.args[1], ast.Name)
        and n.args[1].id == "operation"
    ]
    assert asks, "_reentry no longer asks the rule for the operation it serves"

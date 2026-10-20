"""The source-tree checks split from
``tests/test_guardrails_rung_routing.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import ast
import inspect

import guardrails_rung_routing_shared
import pytest

_clean_provider_registry = guardrails_rung_routing_shared._clean_provider_registry
_isolated_home = guardrails_rung_routing_shared._isolated_home


#: Every module that resolves an action provider and RUNS it, mirroring
#: `test_action_provider_chokepoints.EXECUTION_SITES`. The manual Run path
#: (`dashboard/handlers/trigger_runs`) is excluded: a user pressing Run IS the approval a rung
#: withholds for, and routing it would refuse the click that authorised the action.
ROUTED_SEAMS: tuple[tuple[str, str], ...] = (
    ("personalclaw.hooks", "the lifecycle-hook fire path"),
    # A data-event trigger is a store row, so its fires take this seam too.
    ("personalclaw.gateway", "the clock/file/webhook/event store-trigger fire path"),
)


@pytest.mark.parametrize("module_name,label", ROUTED_SEAMS)
def test_every_unattended_dispatch_seam_ROUTES(module_name, label):
    """🔴 THE INVARIANT that was missing. A seam that reaches a provider without asking
    the ladder is how a declared floor quietly stops applying — and a new seam is exactly
    the case a behavioural test cannot fail on, because it does not know it exists.

    Asserted through the AST rather than a substring so an alias (`as _route_action`) counts
    and a mention inside a comment does not.
    """
    import importlib

    tree = ast.parse(inspect.getsource(importlib.import_module(module_name)))
    local_names = {
        (alias.asname or alias.name)
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and (node.module or "").endswith("guardrails.rungs")
        for alias in node.names
        if alias.name == "route_provider_action"
    }
    assert local_names, f"{label} never imports route_provider_action"
    called = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert local_names & called, f"{label} imports the router but never calls it"

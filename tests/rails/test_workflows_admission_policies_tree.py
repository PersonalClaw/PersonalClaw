"""The source-tree checks split from
``tests/test_workflows_admission_policies.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

from personalclaw.workflows.admission import Lease


def test_the_lease_policy_decides_with_the_pools_own_acquire():
    """A RAIL, not a docstring promise: `Lease.capacity` must CALL `pool.acquire`.

    A second lease implementation is forbidden because the naive one was measured losing 36 of
    40 races. A re-implementation would pass every behavioural test in this file — the semantics are
    easy to copy and the race is not — so the reuse itself is what has to be asserted.
    """
    tree = ast.parse(inspect.getsource(Lease))
    calls = {
        f"{node.func.value.id}.{node.func.attr}"
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
    }
    assert "pool.acquire" in calls, (
        "Lease must decide with pool.acquire — the compare-and-swap decision the task pool's "
        f"flocked claim path uses. Calls found: {sorted(calls)}"
    )
    # And it must not have grown its own expiry arithmetic alongside it: two answers to "is this
    # lease still live" is the drift this change exists to prevent.
    source = Path(inspect.getsourcefile(Lease) or "").read_text(encoding="utf-8")
    assert "acquired_at" not in source
    assert "def expired" not in source

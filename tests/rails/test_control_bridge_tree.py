"""The source-tree checks split from ``tests/test_control_bridge.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import ast

import source_index

_BRIDGE = source_index.REPO / "src" / "personalclaw" / "inbound" / "bridge.py"


def test_the_bridge_writes_one_file_of_its_own():
    """The control bridge calls ``atomic_write`` once, for its discovery file; every change it
    makes to tasks and automations goes through the dashboard's own services.

    Why a rail: a second writer in the bridge would pass every behaviour test of the bridge
    while bypassing the validation the dashboard's service gains later, and no request can show
    the absence of a writer that is not there yet."""
    tree = source_index.tree(_BRIDGE)
    writes = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, (ast.Name, ast.Attribute))
        and (n.func.id if isinstance(n.func, ast.Name) else n.func.attr) == "atomic_write"
    ]
    assert len(writes) == 1, f"the bridge writes {len(writes)} files itself"

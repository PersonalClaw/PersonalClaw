"""The source-tree checks split from
``tests/test_run_now_reports_where_its_trigger_reports.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import ast
import inspect

from personalclaw.dashboard.handlers import trigger_runs
from personalclaw.dashboard.handlers import triggers as handlers


def test_every_run_of_yours_hands_the_dispatch_its_state():
    """A caller that passes no state reports nowhere, which is the defect again one caller later.
    Three callers: your Run now, your answer to a parked run, and the restart review's Run now."""
    callers = 0
    for module in (trigger_runs, handlers):
        for node in ast.walk(ast.parse(inspect.getsource(module))):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if name != "_dispatch_store_action":
                continue
            callers += 1
            assert "state" in {k.arg for k in node.keywords}, ast.unparse(node)
    # Three callers remain since webhook fires and view refreshes moved to the fire dispatch. The
    # floor is that count, so a caller the scan stops seeing fails here instead of passing empty.
    assert callers >= 3, f"found only {callers} callers; the scan is not seeing them"

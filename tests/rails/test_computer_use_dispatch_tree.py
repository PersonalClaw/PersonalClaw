"""The source-tree checks split from
``tests/test_computer_use_dispatch.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import ast
import inspect

import computer_use_dispatch_shared

from personalclaw.computer_use import service

_isolated = computer_use_dispatch_shared._isolated


def _dispatch_call_order() -> list[str]:
    """The screens as they appear in ``computer_dispatch``'s SOURCE, in line order."""
    tree = ast.parse(inspect.getsource(service.computer_dispatch))
    watched = {
        "require_enabled",
        "check_app",
        "_require_fresh_element",
        "check_input_target",
        "_audit",
        "_run_driver",
    }
    calls = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = (
                node.func.attr
                if isinstance(node.func, ast.Attribute)
                else getattr(node.func, "id", "")
            )
            if name in watched:
                calls.append((node.lineno, node.col_offset, name))
    return [name for _line, _col, name in sorted(calls)]


def test_the_dispatch_calls_the_screens_in_source_order():
    """A structural companion to the runtime trace, catching the case the trace cannot: a step
    that exists on a branch the happy path never takes. The runtime rail proves the order that
    RAN; this proves the order that is WRITTEN, so a screen relocated below the driver call
    reds even if no test happens to exercise that branch."""
    order = _dispatch_call_order()
    assert order[0] == "require_enabled", f"the keystone is not the first call: {order}"
    assert (
        order.index("check_app")
        < order.index("_require_fresh_element")
        < order.index("check_input_target")
        < order.index("_run_driver")
    ), order
    # Every _audit call precedes the acting driver call: the audit at step 5 is written BEFORE
    # the desktop is touched, so a driver that wedges or is killed still leaves evidence.
    assert max(i for i, name in enumerate(order) if name == "_audit") < max(
        i for i, name in enumerate(order) if name == "_run_driver"
    ), order

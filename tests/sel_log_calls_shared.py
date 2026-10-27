"""How a census of the security log's writes reads one handed to ``sel_log_async`` (#5605).

``sel_log_async(write, *args, **kwargs)`` runs ``write(*args, **kwargs)`` in a worker thread, so
the write is off the event loop. A census that reads a write by its call, its callee and its
arguments, reads such a call as the one it runs. A write moved off the loop is then read exactly
as it was read on it, and nothing a census checked drops out of it.

Used by every census that reads the log's writes by their shape:
``tests/rails/test_sel_subject_census_tree.py``,
``tests/rails/test_stale_write_refusals_read_right_tree.py`` and
``tests/session_key_census_shared.py``.
"""

from __future__ import annotations

import ast

#: Calls that run their first argument with the rest of their arguments.
RUNS_ITS_FIRST_ARGUMENT = frozenset({"sel_log_async"})


def _callee(call: ast.Call) -> str:
    func = call.func
    return func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")


def the_call_it_runs(call: ast.Call) -> ast.Call:
    """*call*, or the call it runs when it hands its first argument the rest of its arguments:
    ``sel_log_async(sel().log_api_access, caller=...)`` is read as
    ``sel().log_api_access(caller=...)``, at *call*'s place in the source."""
    if _callee(call) in RUNS_ITS_FIRST_ARGUMENT and call.args:
        ran = ast.Call(func=call.args[0], args=call.args[1:], keywords=call.keywords)
        return ast.copy_location(ran, call)
    return call

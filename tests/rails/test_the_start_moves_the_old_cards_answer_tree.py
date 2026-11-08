"""The dashboard's start schedules the move of the old card's explanations answer, with its state.

The move itself (``server._adopt_card_answer``) and the task it is held by
(``server._start_card_answer_move``) are driven in ``tests/test_explanations.py``. This holds the
one thing a run cannot show: that ``start_dashboard`` calls the scheduler, once, with the state,
so a start moves an answer the old card kept as a pinned preference.
"""

from __future__ import annotations

import ast
import inspect


def test_start_dashboard_schedules_the_move_with_its_state():
    from personalclaw.dashboard import server

    tree = ast.parse(inspect.getsource(server.start_dashboard).lstrip())
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and ast.unparse(node) == "_start_card_answer_move(state)"
    ]
    assert len(calls) == 1, "start_dashboard does not schedule the move once, with its state"

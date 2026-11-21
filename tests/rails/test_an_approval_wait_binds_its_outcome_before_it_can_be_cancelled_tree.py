"""A chat's wait on an approval binds its outcome before the wait can be cancelled (#1536).

The wait binds ``outcome`` on its success path and in its timeout handler, but not when the wait
itself is cancelled (pytest-timeout, the gateway stopping, a client going away). With nothing bound
before the ``try``, the ``finally`` read an unbound ``outcome`` and raised ``UnboundLocalError``,
which replaced the cancellation in the traceback. Bound to "rejected" first, the cancellation
propagates unmasked, and a wait nobody answered never runs the call.
``tests/test_approval_outcome_bound_on_cancel.py`` drives the cancelled wait itself.

Read from the source tree, so a comment or a docstring cannot satisfy it and the binding's form
(alone or in a tuple) does not matter. Every wait that parks an approval's future is checked: the
chat turn's (``chat_runner``) and the one a restart carries on (``parked_chat_turns``).
"""

from __future__ import annotations

import ast

import pytest
import source_index
from rails_repo import REPO

DASHBOARD = REPO / "src" / "personalclaw" / "dashboard"
WAITS = [DASHBOARD / "chat_runner.py", DASHBOARD / "parked_chat_turns.py"]


def _parks_the_future(stmt: ast.stmt) -> bool:
    """``session._approval_futures[request_id] = fut``: the wait's future, parked."""
    return (
        isinstance(stmt, ast.Assign)
        and len(stmt.targets) == 1
        and isinstance(stmt.targets[0], ast.Subscript)
        and ast.unparse(stmt.targets[0]) == "session._approval_futures[request_id]"
        and ast.unparse(stmt.value) == "fut"
    )


def _binds_outcome_rejected(stmt: ast.stmt) -> bool:
    """``outcome = "rejected"``, alone or as one name of a tuple assignment."""
    if not isinstance(stmt, ast.Assign) or len(stmt.targets) != 1:
        return False
    target, value = stmt.targets[0], stmt.value
    pairs = (
        zip(target.elts, value.elts)
        if isinstance(target, ast.Tuple) and isinstance(value, ast.Tuple)
        else [(target, value)]
    )
    return any(
        isinstance(name, ast.Name)
        and name.id == "outcome"
        and isinstance(bound, ast.Constant)
        and bound.value == "rejected"
        for name, bound in pairs
    )


@pytest.mark.parametrize("path", WAITS, ids=lambda p: p.stem)
def test_an_approval_wait_binds_its_outcome_before_the_try(path):
    waits = [
        f
        for f in ast.walk(source_index.tree(path))
        if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef))
        and any(_parks_the_future(s) for s in f.body)
    ]
    assert waits, f"{path.name} parks no approval future: the rail is reading nothing"
    for wait in waits:
        body = wait.body
        parked = next(k for k, s in enumerate(body) if _parks_the_future(s))
        after = range(parked + 1, len(body))
        tried = next((k for k in after if isinstance(body[k], ast.Try)), None)
        assert tried is not None, f"{wait.name}: no try after the future is parked"
        assert any(_binds_outcome_rejected(s) for s in body[parked + 1 : tried]), (
            f'{wait.name}: outcome must be bound to "rejected" after the future is parked and '
            "BEFORE the try, or the cancel path reads it unbound"
        )

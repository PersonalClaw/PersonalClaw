"""Rail for #1536 — the approval-wait `finally` must not raise UnboundLocalError.

The approval wait binds ``outcome`` on the success path and via the TimeoutError handler —
but NOT when the wait itself is cancelled (pytest-timeout, gateway shutdown, client
disconnect, navigation away). On that path the ``finally`` referenced an unbound
``outcome`` and raised ``UnboundLocalError``, which REPLACED the cancellation in the
traceback, so a CI hang read as an unrelated error (the reported symptom).

Binding ``outcome = "rejected"`` before the try fixes it: the cancellation propagates unmasked,
and the ``finally`` still runs its bookkeeping — which is now expiring the approval on every
surface that lists it (the registry row, the Inbox row, open cards), so a torn-down turn never
leaves anything asking for a decision it can no longer receive.
"""

from __future__ import annotations

import ast
import asyncio
import inspect

import pytest
from test_dashboard_approval import (  # reuse the file's harness
    _complete_event,
    _context_builder,
    _make_session,
    _make_state,
    _permission_event,
    _set_stream,
)

import personalclaw.dashboard.chat_runner as cr
from personalclaw.dashboard import parked_chat_turns
from personalclaw.dashboard.chat import run_chat


@pytest.mark.asyncio
async def test_cancelling_an_approval_wait_expires_it_everywhere_and_propagates(tmp_path):
    state, client = _make_state(tmp_path, context_builder=_context_builder())
    session = _make_session()
    _set_stream(client, [_permission_event(), _complete_event()])

    task = asyncio.create_task(run_chat(state, session, "hello"))
    # Wait until the approval is parked AND published — i.e. we're blocked on the wait.
    for _ in range(400):
        await asyncio.sleep(0.005)
        if "req-1" in session._approval_futures and state._pending_approvals:
            break
    assert state._pending_approvals, "the approval was never published"

    task.cancel()
    # run_chat may absorb the mid-turn cancellation as a normal turn-end or let it propagate —
    # either is fine. What must NOT happen is the finally raising UnboundLocalError (which would
    # surface here as that error). Suppress the cancellation and assert the invariant below.
    try:
        await task
    except asyncio.CancelledError:
        pass

    # Expired rather than stranded, and said so on the one signal every surface acts on.
    assert state._pending_approvals == {}
    resolved = [
        c.args[1] for c in state.broadcast_ws.call_args_list if c.args[0] == "approval_resolved"
    ]
    assert resolved and resolved[-1]["approved"] is False and resolved[-1]["request_id"] == "req-1"


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


@pytest.mark.parametrize(
    "module", [cr, parked_chat_turns], ids=["chat_runner", "parked_chat_turns"]
)
def test_outcome_is_bound_before_the_try(module):
    """Source-contract guard: in every wait that parks an approval's future, ``outcome`` is bound
    to "rejected" after the future is parked and before the try that can be cancelled, so the
    finally can never read it unbound, and a wait nobody answered never runs the call. Read from
    the syntax tree, so a comment or a docstring cannot satisfy it and the binding's form (alone
    or in a tuple) does not matter."""
    tree = ast.parse(inspect.getsource(module))
    waits = [
        f
        for f in ast.walk(tree)
        if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef))
        and any(_parks_the_future(s) for s in f.body)
    ]
    assert waits, f"{module.__name__} parks no approval future: the guard is reading nothing"
    for wait in waits:
        body = wait.body
        parked = next(k for k, s in enumerate(body) if _parks_the_future(s))
        tried = next(
            (k for k in range(parked + 1, len(body)) if isinstance(body[k], ast.Try)), None
        )
        assert tried is not None, f"{wait.name}: no try after the future is parked"
        assert any(_binds_outcome_rejected(s) for s in body[parked + 1 : tried]), (
            f'{wait.name}: outcome must be bound to "rejected" after the future is parked and '
            "BEFORE the try, or the cancel path reads it unbound"
        )

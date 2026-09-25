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

import asyncio

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


def test_outcome_is_bound_before_the_try(tmp_path):
    # Source-contract guard: `outcome` must be initialised before the try that
    # can be cancelled, so the finally can never read it unbound. Comments
    # stripped so the docstring's own words don't satisfy the assertion.
    import inspect

    src = inspect.getsource(cr)
    src = "\n".join(line for line in src.splitlines() if not line.lstrip().startswith("#"))
    anchor = "session._approval_futures[request_id] = fut"
    i = src.index(anchor)
    window = src[i : i + 600]
    assert (
        'outcome = "rejected"' in window
    ), "outcome must be bound right after the future is parked"
    assert window.index('outcome = "rejected"') < window.index(
        "try:"
    ), "the outcome default must come BEFORE the try, or the cancel path is still unbound"

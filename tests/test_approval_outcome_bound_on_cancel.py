"""Rail for #1536 — the approval-wait `finally` must not raise UnboundLocalError.

The mirrored-approval block binds ``outcome`` on the success and grace-timeout
paths and via the outer TimeoutError handler — but NOT when the inner
``wait_for(fut, 7200)`` is cancelled (pytest-timeout, gateway shutdown, client
disconnect, navigation away). On that path the ``finally`` referenced an unbound
``outcome`` and raised ``UnboundLocalError``, which:

- REPLACED the cancellation in the traceback, so a CI hang read as an unrelated
  error (the reported symptom), and
- skipped ``_resolve_mirrored_approval``, stranding the mirrored inbox item
  asking for a decision the turn was already tearing down (the production defect).

Binding ``outcome = "rejected"`` before the try fixes both: the mirror resolves
and the cancellation propagates unmasked.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest
from test_dashboard_approval import (  # reuse the file's harness
    _complete_event,
    _context_builder,
    _make_session,
    _make_state,
    _patch_stats,
    _permission_event,
    _set_stream,
)

import personalclaw.dashboard.chat_runner as cr
from personalclaw.dashboard.chat import run_chat


@pytest.mark.asyncio
async def test_cancelling_a_mirrored_approval_wait_resolves_the_mirror_and_propagates(tmp_path):
    state, client = _make_state(tmp_path, context_builder=_context_builder())
    session = _make_session()
    _set_stream(client, [_permission_event(), _complete_event()])

    resolve_calls: list[tuple[str, str]] = []

    with (
        _patch_stats(),
        # Grace ~0 so the wait mirrors immediately, then parks on the 7200s inner wait.
        patch.object(cr, "_APPROVAL_MIRROR_GRACE_SECS", 0.01),
        patch.object(cr, "_mirror_approval_to_inbox", MagicMock(return_value="inbox-1")),
        patch.object(
            cr,
            "_resolve_mirrored_approval",
            MagicMock(side_effect=lambda item, outcome: resolve_calls.append((item, outcome))),
        ),
    ):
        task = asyncio.create_task(run_chat(state, session, "hello"))

        # Wait until the approval future is registered AND the grace window has
        # mirrored it (mirror mock called) — i.e. we're parked on the inner wait.
        for _ in range(200):
            await asyncio.sleep(0.01)
            if cr._mirror_approval_to_inbox.called and "req-1" in session._approval_futures:
                break
        assert cr._mirror_approval_to_inbox.called, "the grace timeout should have mirrored"

        task.cancel()
        # run_chat may absorb the mid-turn cancellation as a normal turn-end or let
        # it propagate — either is fine. What must NOT happen is the finally raising
        # UnboundLocalError (which would surface here as that error, not the mirror
        # resolving). Suppress the cancellation and assert the invariant below.
        try:
            await task
        except asyncio.CancelledError:
            pass

    # The mirrored inbox item was resolved (default "rejected") rather than stranded,
    # and no UnboundLocalError replaced the real control flow.
    assert resolve_calls == [("inbox-1", "rejected")], resolve_calls


def test_outcome_is_bound_before_the_try(tmp_path):
    # Source-contract guard: `outcome` must be initialised before the try that
    # can be cancelled, so the finally can never read it unbound. Comments
    # stripped so the docstring's own words don't satisfy the assertion.
    import inspect

    src = inspect.getsource(cr)
    src = "\n".join(line for line in src.splitlines() if not line.lstrip().startswith("#"))
    anchor = 'mirrored_item = ""'
    i = src.index(anchor)
    window = src[i : i + 400]
    assert 'outcome = "rejected"' in window, "outcome must be bound right after mirrored_item"
    assert window.index('outcome = "rejected"') < window.index(
        "try:"
    ), "the outcome default must come BEFORE the try, or the cancel path is still unbound"

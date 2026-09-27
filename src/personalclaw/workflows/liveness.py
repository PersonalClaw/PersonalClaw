"""The stall clock: what keeps a working node's clock running, and what a silent node gets.

`timeout_stall` is meant to catch a SILENT node, not a slow one, so a node that is working has to
be able to say so. Two things say it, and both live here so one module answers "what counts as
progress":

* a nested run's heartbeat: `wait_with_progress` feeds the parent's stall clock while the child
  works, so a subworkflow that legitimately takes ten minutes is not killed as wedged;
* a model call that is still streaming: the guard stamps every event a provider sends on the call
  (`guardrails.calls.ModelCall.last_event_at`) and `last_heard` reads it. Before that, nothing a
  model-calling step did reached the clock. Measured with the stall window at 4s: a best-of-n
  whose model was streaming an 8s answer failed "no progress for 4s (timeout_stall)" 5s in.

A node that says nothing for its whole window is stopped by `enforce_stall_timeouts`, which the
controller runs on every tick that awaits in-flight work.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any

from personalclaw.workflows.models import (
    Failure,
    FailureClass,
    InstanceState,
    now_stamp,
    spec_path,
    walk,
)
from personalclaw.workflows.step_usage import measured

if TYPE_CHECKING:
    from personalclaw.guardrails.calls import CallLog
    from personalclaw.workflows.controller import RunController

#: How often a long wait feeds the parent's stall clock. Well under any sane `timeout_stall`, so a
#: working child can never be mistaken for a silent one.
HEARTBEAT_SECS = 0.5


async def wait_with_progress(controller: Any, timeout: float, on_progress: Any) -> Any:
    """Wait for a child run, feeding the parent's stall clock while it works.

    Each tick is one function call, which costs nothing next to a child run.
    """
    if not callable(on_progress):
        return await controller.wait_for_terminal(timeout=timeout)

    task = asyncio.ensure_future(controller.wait_for_terminal(timeout=timeout))
    while not task.done():
        on_progress()
        # `asyncio.wait` rather than a sleep-then-check: it returns as soon as the child settles, so
        # a fast child is not padded by the heartbeat interval.
        await asyncio.wait({task}, timeout=HEARTBEAT_SECS)
    return task.result()


def last_heard(last_progress: float, calls: CallLog) -> float:
    """When a node last showed it was working: its own progress, or its calls' latest event."""
    return max(last_progress, calls.last_activity or 0.0)


def _node_stall_window(ctl: RunController, path: str) -> int:
    """This node's stall window: its own `timeout_stall_secs`, else the run-level default.

    🔴 The per-node override was DECLARED BY FOUR SHIPPED TEMPLATES AND READ BY NOTHING (S147).
    `design-project.refine` asks 600s, `general-project.project` 900s,
    `goal-pursuit-open-ended.work` 900s and `goal-pursuit-verifiable.work` 1200s — and
    `enforce_stall_timeouts` consulted only `services.node_timeout_stall`, so every one of them
    silently got the 300s default and a legitimately slow node was killed as wedged.

    That is the WRONG DIRECTION to fail in. `timeout_stall` is supposed to mean "silent", not
    "slow" (the heartbeat in `wait_with_progress` exists precisely to keep that distinction), and a
    node whose author measured it needing 20 minutes being cancelled at 5 is the failure the knob
    was added to prevent.

    A node may only RAISE its window, never lower it below the run default: that value is the
    operator's floor for how long a silent node may sit, and letting a template shorten it would
    let a bundled spec tighten an operator's policy. Zero/invalid falls back to the default
    rather than disabling the check — a malformed knob must not switch a safety timeout off.
    """
    node = dict(walk(ctl.root)).get(spec_path(path))
    default = int(ctl.services.node_timeout_stall or 0)
    raw = (node.config or {}).get("timeout_stall_secs") if node is not None else None
    try:
        declared = int(raw) if raw is not None else 0
    except (TypeError, ValueError):
        declared = 0
    return max(default, declared) if declared > 0 else default


def enforce_stall_timeouts(ctl: RunController) -> None:
    """Kill nodes that have gone silent. The stall knob is separate from the total
    knob on purpose: a long-but-progressing node survives, a wedged one does not.

    The window is PER NODE (`_node_stall_window`) — see that function for the four shipped
    templates whose declared override was inert.

    A model call counts as progress while its provider is still sending (`last_heard`).
    """
    if not ctl.services.node_timeout_stall or ctl.services.node_timeout_stall <= 0:
        return
    now = time.time()
    for path, entry in list(ctl._inflight.items()):
        stall = _node_stall_window(ctl, path)
        if stall <= 0 or now - last_heard(entry.last_progress, entry.calls) < stall:
            continue
        entry.task.cancel()
        ctl._inflight.pop(path, None)
        inst = ctl._instance(path)
        failure = Failure(
            failure_class=FailureClass.TIMEOUT,
            cause_plain=f"no progress for {stall}s (timeout_stall)",
            remediation="the node produced no progress events; check the provider, raise this "
            "node's `timeout_stall_secs`, or raise "
            "workflows.default_node_timeout_stall_secs",
            recoverable=True,
        )
        inst.state = InstanceState.FAILED
        inst.failure = failure
        inst.completed_at = now_stamp()
        # Read before the cancel reaches the calls: each one still open is cut off by it.
        usage = measured(entry.calls)
        inst.tokens = usage.billable()
        inst.model_substituted = list(usage.substitutions)
        ctl.run.total_tokens += inst.tokens
        ctl.journal.step_failed(
            path,
            entry.ready.node.id,
            epoch=inst.epoch,
            failure=failure,
            usage=usage,
            attempt=inst.attempt,
            retries_exhausted=True,
        )
        ctl._publish(
            "workflow_node_done",
            {
                "node_id": entry.ready.node.id,
                "instance_path": path,
                "status": InstanceState.FAILED.value,
            },
        )

"""Slice 5 exemplar — the human-input contract: gates surface, and time out to FAILED (WF2-R7).

Slice 5 added the typed ask payload, gate timeouts, and the needs-input path. This exemplar
drives the two ends of a gate's life against the real controller:

1. an unanswered `approval` gate does not wedge and does not silently pass — the run
   settles at `needs_input`, carrying a typed ask payload the attention surface renders;
2. a gate with a `timeout_secs` that nobody answers times out to `FAILED` — emphatically
   NOT a pass. A timed-out gate reading as approval is how an unattended run would "approve"
   something no human ever saw (WF2-R7).

Runnable standalone: `python -m harness.exemplars.slice_5.exemplar` (or `smoke.sh`). The
timeout leg uses a 1s `timeout_secs`, so the whole exemplar stays well under 30s.
"""

from __future__ import annotations

import asyncio
from typing import Any

from personalclaw.workflows import store
from personalclaw.workflows.controller import EngineServices, RunController
from personalclaw.workflows.models import RunStatus, WorkflowRun

#: An approval gate with no timeout: an unanswered run parks at needs_input.
WAITS_SPEC: dict[str, Any] = {
    "name": "slice5-gate-waits",
    "root": {"kind": "gate", "id": "g", "config": {"kind": "approval", "prompt": "ship it?"}},
}

#: An approval gate with a short timeout: unanswered, it FAILS (never a silent approval).
TIMES_OUT_SPEC: dict[str, Any] = {
    "name": "slice5-gate-times-out",
    "root": {"kind": "gate", "id": "g", "config": {"kind": "approval", "timeout_secs": 1}},
}


async def _drive() -> str | None:
    # 1. Unanswered, no timeout → needs_input with a typed ask payload. `needs_input` is a
    #    STOPPING point, not a terminal state, so `wait_for_terminal` (which returns there)
    #    is the right waiter — `run_to_completion` would block until its own timeout. `stop()`
    #    then tears down the background tick loop without marking the run failed.
    waits = store.create(WorkflowRun(id="", workflow_name=WAITS_SPEC["name"]))
    store.write_spec(waits.id, WAITS_SPEC)
    c_waits = RunController(waits, WAITS_SPEC, services=EngineServices())
    try:
        waits_status = await c_waits.wait_for_terminal(timeout=10)
    finally:
        await c_waits.stop()
    if waits_status is not RunStatus.NEEDS_INPUT:
        return f"expected an unanswered gate to surface NEEDS_INPUT, got {waits_status}"
    if not c_waits.run.attention or c_waits.run.attention.get("kind") != "approval":
        return f"expected a typed 'approval' ask payload, got {c_waits.run.attention}"

    # 2. Unanswered, with a timeout → FAILED (a real terminal state), never a silent pass.
    timed = store.create(WorkflowRun(id="", workflow_name=TIMES_OUT_SPEC["name"]))
    store.write_spec(timed.id, TIMES_OUT_SPEC)
    c_timed = RunController(timed, TIMES_OUT_SPEC, services=EngineServices())
    timed_status = await c_timed.run_to_completion(timeout=20)
    if timed_status is not RunStatus.FAILED:
        return f"expected a timed-out unattended gate to FAIL, got {timed_status}"
    return None


def main() -> int:
    err = asyncio.run(_drive())
    if err:
        print(f"FAIL: {err}")
        return 1
    print(
        "PASS slice_5: an unanswered gate surfaced NEEDS_INPUT with a typed 'approval' ask "
        "payload, and a gate that timed out unattended FAILED rather than silently passing."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

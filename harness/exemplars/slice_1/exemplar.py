"""Slice 1 exemplar — the pure frontier, the engine, and the journal-backed run (WF2 §Slice 1).

A three-node sequence driven end to end against a temp home with only the model call faked:
`seed` (transform) → `think` (infer, binding-fed) → `final` (transform, consuming think).
It exercises the load-bearing Slice-1 machinery at once: the pure `frontier()` scheduling
one node at a time in dependency order, the dispatchers, binding resolution threading a
value from node to node, terminal-status ownership, and the Run Ledger emission.

Runnable standalone: `python -m harness.exemplars.slice_1.exemplar` (or `smoke.sh`).
`main()` self-asserts and returns 0 on the expected COMPLETE outcome, non-zero otherwise.
"""

from __future__ import annotations

import asyncio
from typing import Any

from personalclaw.workflows import journal as J
from personalclaw.workflows import store
from personalclaw.workflows.controller import EngineServices, RunController
from personalclaw.workflows.models import InstanceState, RunStatus, WorkflowRun

#: `think`'s prompt binds `seed`'s output; `final`'s expr binds `think`'s output. If the
#: frontier scheduled out of order, or bindings did not thread, `final` would resolve to
#: nothing and the assertions below would catch it.
SPEC: dict[str, Any] = {
    "name": "slice1-frontier-and-journal",
    "root": {
        "kind": "sequence",
        "id": "root",
        "children": [
            {"kind": "transform", "id": "seed", "config": {"expr": {"n": 7}}},
            {
                "kind": "infer",
                "id": "think",
                "config": {"prompt": "double {{nodes.seed.output.n}}"},
            },
            {"kind": "transform", "id": "final", "config": {"expr": "got {{nodes.think.output}}"}},
        ],
    },
}


def _echo():
    """A fake model that records the prompts it saw, so binding-threading is observable."""
    calls: list[str] = []

    async def fn(prompt: str, *, use_case: str = "background", output_type: Any = None) -> str:
        calls.append(prompt)
        return f"out{len(calls)}"

    fn.calls = calls  # type: ignore[attr-defined]
    return fn


async def _run() -> tuple[str, RunStatus, dict[str, Any], list[str]]:
    run = store.create(WorkflowRun(id="", workflow_name=SPEC["name"]))
    store.write_spec(run.id, SPEC)
    fn = _echo()
    controller = RunController(run, SPEC, services=EngineServices(completion=fn))
    status = await controller.run_to_completion(timeout=20)
    return run.id, status, store.read_state(run.id), fn.calls  # type: ignore[attr-defined]


def main() -> int:
    run_id, status, instances, calls = asyncio.run(_run())

    if status is not RunStatus.COMPLETE:
        print(f"FAIL: expected run status COMPLETE, got {status}")
        return 1

    # Binding threaded seed→think: the resolved prompt saw seed's value, not the raw template.
    if calls != ["double 7"]:
        print(f"FAIL: expected the infer node to see the bound prompt ['double 7'], got {calls}")
        return 1

    # Every node reached DONE, and there are exactly three.
    if len(instances) != 3 or not all(i.state is InstanceState.DONE for i in instances.values()):
        print(f"FAIL: expected 3 DONE nodes, got {[(k, v.state) for k, v in instances.items()]}")
        return 1

    # The run row carries terminal metadata (started/completed timestamps, token total).
    saved = store.get(run_id)
    if saved is None or not (saved.started_at and saved.completed_at):
        print("FAIL: the run row is missing its terminal timestamps")
        return 1

    # The journal recorded a completion per node — the Run Ledger the flywheel/UI read.
    completed = [r for r in J.ledger(run_id) if r["kind"] == J.STEP_COMPLETED]
    if len(completed) != 3:
        print(f"FAIL: expected 3 STEP_COMPLETED ledger records, got {len(completed)}")
        return 1

    print(
        "PASS slice_1: the frontier scheduled the 3-node sequence in dependency order, "
        "bindings threaded seed→think→final, the run reached COMPLETE, and the journal "
        "recorded a completion per node."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

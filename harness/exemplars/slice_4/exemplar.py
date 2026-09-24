"""Slice 4 exemplar — mid-flight mutation: the binding cascade + the resume cache (WF2-R2/A1).

Slice 4 added mutation, checkpoints and fork. This exemplar exercises the correctness core:
editing a node re-runs exactly its BINDING closure — every node transitively downstream
through `{{nodes.x.output}}` references — and NOTHING else. The subtlety it defends (WF2-R2)
is that a later SIBLING that consumes an edited node's output is not a tree descendant; a
tree-based reset would leave it holding a stale input, a silently inconsistent run.

Two mechanisms, one pure and one end-to-end:

1. `binding_closure` / `cascade_preview` compute the re-run set from bindings, not the tree
   — the sibling consumer is in the closure, the unrelated node is not.
2. driven through a real controller: run a sequence to completion, edit the middle node's
   prompt, resume — and assert from the LEDGER that exactly the closure re-ran (the resume
   cache served the untouched prefix at zero model calls). This is the acceptance bar
   Slice 4 set: answerable from the ledger, not from logs.

Runnable standalone: `python -m harness.exemplars.slice_4.exemplar` (or `smoke.sh`).
"""

from __future__ import annotations

import asyncio
from typing import Any

from personalclaw.workflows import journal as J
from personalclaw.workflows import mutations as M
from personalclaw.workflows import store
from personalclaw.workflows.controller import EngineServices, RunController
from personalclaw.workflows.models import InstanceState, Node, RunStatus, WorkflowRun


#: `n2` binds `n1`; `n3` binds `n2`; `n_unrelated` binds nothing. Editing `n2` must re-run
#: `n2` and `n3` (its binding closure), leave `n1` cached, and never touch `n_unrelated`.
def _spec(second_prompt: str) -> dict[str, Any]:
    return {
        "name": "slice4-cascade",
        "root": {
            "kind": "sequence",
            "id": "root",
            "children": [
                {"kind": "infer", "id": "n1", "config": {"prompt": "first"}},
                {"kind": "infer", "id": "n2", "config": {"prompt": second_prompt}},
                {"kind": "infer", "id": "n3", "config": {"prompt": "third {{nodes.n2.output}}"}},
                {"kind": "infer", "id": "n_unrelated", "config": {"prompt": "aside"}},
            ],
        },
    }


def _echo():
    calls: list[str] = []

    async def fn(prompt: str, *, use_case: str = "background", output_type: Any = None) -> str:
        calls.append(prompt)
        return f"out{len(calls)}"

    fn.calls = calls  # type: ignore[attr-defined]
    return fn


def _check_pure_closure() -> str | None:
    """The WF2-R2 shape, checked on the pure functions. Return an error string or None."""
    root = Node.from_dict(_spec("second")["root"])
    closure = M.binding_closure(root, {"n2"})
    if closure != {"n2", "n3"}:
        return f"expected the binding closure of n2 to be {{n2, n3}}, got {closure}"
    if "n_unrelated" in closure:
        return "n_unrelated has no binding to n2 and must not be in the closure"
    return None


async def _reschedule_all(controller: RunController) -> None:
    for inst in controller.instances.values():
        inst.state = InstanceState.PENDING


async def _drive() -> str | None:
    """Run, edit n2's prompt, resume; assert the ledger shows exactly the closure re-ran."""
    fn = _echo()
    run = store.create(WorkflowRun(id="", workflow_name="slice4-cascade"))
    spec_v1 = _spec("second")
    store.write_spec(run.id, spec_v1)
    run.status = RunStatus.RUNNING
    c1 = RunController(run, spec_v1, services=EngineServices(completion=fn))
    if await c1.run_to_completion(timeout=20) is not RunStatus.COMPLETE:
        return "the initial run did not COMPLETE"
    if len(fn.calls) != 4:
        return f"expected 4 model calls on the first run, got {len(fn.calls)}"

    # Edit n2's prompt and resume. The resume cache keys on (path, epoch, inputs, spec) so
    # n2's changed config invalidates n2, and n3's changed input invalidates n3.
    before = len(fn.calls)
    spec_v2 = _spec("second EDITED")
    store.write_spec(run.id, spec_v2)
    resumed = store.get(run.id)
    if resumed is None:
        return "the run vanished from the store between edit and resume"
    resumed.status = RunStatus.RUNNING
    c2 = RunController(resumed, spec_v2, services=EngineServices(completion=fn))
    await _reschedule_all(c2)
    if await c2.run_to_completion(timeout=20) is not RunStatus.COMPLETE:
        return "the resumed run did not COMPLETE"

    reran = len(fn.calls) - before
    if reran != 2:
        return f"expected exactly the closure (n2, n3 = 2 calls) to re-run, got {reran}"
    if fn.calls[-2] != "second EDITED" or not fn.calls[-1].startswith("third "):
        return f"the re-run did not execute the edited closure in order: {fn.calls[-2:]}"

    # The ledger is the acceptance surface: cached hits for the untouched nodes.
    cached = [r for r in J.ledger(run.id) if r["kind"] == J.STEP_CACHED]
    if not any(r.get("cached") for r in cached):
        return "no STEP_CACHED ledger records — the resume cache did not serve the prefix"
    return None


def main() -> int:
    pure_err = _check_pure_closure()
    if pure_err:
        print(f"FAIL: {pure_err}")
        return 1

    drive_err = asyncio.run(_drive())
    if drive_err:
        print(f"FAIL: {drive_err}")
        return 1

    print(
        "PASS slice_4: the binding closure of an edited node is its downstream consumers "
        "(sibling included, unrelated node excluded), and resuming after the edit re-ran "
        "exactly that closure — the untouched prefix was served from the resume cache "
        "(STEP_CACHED on the ledger)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

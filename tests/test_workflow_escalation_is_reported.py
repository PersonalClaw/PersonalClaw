"""The escalation record is the ONLY account a failed run has (issue 565).

The frontend half of #565 (a panel that renders the record) rests on two facts about the
server, and neither is obvious from reading the handler:

1. **`GET /api/workflows/runs/{id}` returns `attention` for a terminal run.** `service.status`
   reads `run.attention` unconditionally, so the data was never missing from the wire — the
   client simply never read it. If that ever changes, the panel goes blank and only this test
   would say why.

2. **`error` names the step that failed and its cause, and `attention` keeps the rest.** A node
   that exhausts its retries makes the frontier's outcome FAILED, and the run goes terminal
   through the completion path, whose ending names the failed step and why
   (`ending_sentence.for_failures`): "“i” failed: ConnectionError: network down." That field
   used to be empty on exactly these runs — the completion path's terminal write took no
   `error` — so the run page's one explanation slot (`run.error && <p>`) rendered a failed run
   with a blank reason. The escalation on `attention` still holds what the sentence does not:
   the reason, the per-attempt evidence and the engine's fix instruction.

Measured here rather than asserted from reading. The cause now appears twice on the wire, in the
sentence and as the escalation's `detail`, and the panel's detail-deduplication
(`EscalationPanel`'s `runError` prop) is what keeps it from appearing twice on the page.
"""

from __future__ import annotations

import pytest
import workflow_escalation_is_reported_shared

from personalclaw.workflows import journal as J
from personalclaw.workflows import service, store
from personalclaw.workflows.controller import EngineServices, RunController
from personalclaw.workflows.models import RunStatus, WorkflowRun
from personalclaw.workflows.resilience import ESCALATION_OPTIONS

_isolated_home = workflow_escalation_is_reported_shared._isolated_home

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def _run_that_escalates(max_attempts: int = 2) -> WorkflowRun:
    """A run whose only node fails every attempt, so the retry budget is spent."""

    async def always_down(prompt, *, use_case="background", output_type=None):
        raise ConnectionError("network down")

    spec = {
        "name": "esc",
        "root": {
            "kind": "infer",
            "id": "i",
            "config": {"prompt": "go", "retry": {"max_attempts": max_attempts}},
        },
    }
    run = store.create(WorkflowRun(id="", workflow_name="esc"))
    store.write_spec(run.id, spec)
    controller = RunController(run, spec, services=EngineServices(completion=always_down))
    status = await controller.run_to_completion(timeout=25)
    assert status == RunStatus.FAILED, f"the run did not fail: {status}"
    return controller.run


async def test_the_run_names_the_failed_step_and_attention_keeps_the_whole_account():
    """🔑 Both halves in one place: the sentence the run page renders, and the record under it."""
    run = await _run_that_escalates()
    payload = service.status(run.id)

    # Half one: the field the run page renders names the step and its cause. It was empty.
    attention = payload["attention"]
    assert payload["error"] == f"“i” failed: {attention['detail']}.", payload["error"]

    # Half two: the whole account, on a field the client received and discarded.
    assert attention["kind"] == "escalation"
    assert attention["reason"] == "retries_exhausted"
    assert attention["detail"], "the cause is what makes the reason actionable"
    assert [a["attempt"] for a in attention["attempts"]] == [1, 2]
    assert all(
        a["fix_instruction"] for a in attention["attempts"]
    ), "the engine's derived next move is the single most actionable field in the record"


async def test_a_terminal_run_still_serves_the_record():
    """The panel renders on a TERMINAL run — that is the whole point — so a filter added here
    later (mirroring the one the frontend fold used to apply) would empty it silently."""
    run = await _run_that_escalates()
    assert run.status == RunStatus.FAILED
    assert service.status(run.id)["attention"] is not None


async def test_the_ledger_keeps_the_record_too():
    """`attention` is the live copy and the ledger is the durable one. Both, deliberately:
    `attention` is overwritten by the next gate, and a later reader (the flywheel, a bug
    report) needs the escalation after the run is long over."""
    run = await _run_that_escalates()
    rows = [r for r in J.ledger(run.id) if r["kind"] == J.STEP_ESCALATED]
    assert len(rows) == 1
    assert rows[0]["reason"] == "retries_exhausted"
    assert list(rows[0]["options"]) == list(ESCALATION_OPTIONS)

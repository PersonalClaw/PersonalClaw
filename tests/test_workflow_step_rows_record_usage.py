"""Every step row says what its model calls used: completed, failed, retried and stopped alike.

Measured on `main` before this change: a stall-killed best-of-n step had made two model calls and
its `step_failed` row carried no usage at all, so Introspect read "Models: none recorded". The same
was true of every failed attempt a retry replaced. `run_totals` and `run_stats` folded completed
rows only, and the run row charged a node only when it succeeded, so a token budget never saw what
a failed attempt spent. And no row ever named its provider: `NodeResult.provider` had no writer.

The action providers below stand in for the guard the way `guardrails.calls` is fed in production:
each call is published to the step's call log with `open_call` and then settled with what its
provider reported. The best-of-n harness (`test_best_of_n_provider_outage.py`) drives the same
rows through the real guard and a real HTTP provider.
"""

from __future__ import annotations

from typing import Any

import pytest

from personalclaw.guardrails.calls import ABANDONED, DONE, ModelCall, open_call
from personalclaw.ledger.reader import run_totals
from personalclaw.workflows import journal as J
from personalclaw.workflows import service, store
from personalclaw.workflows.controller import EngineServices, RunController
from personalclaw.workflows.models import RunStatus, WorkflowRun

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: home)
    return home


def _answered(tokens_in: int, tokens_out: int) -> ModelCall:
    """One model call as the guard settles it: published to the step's log, then answered."""
    call = open_call("cloud-a", "model-a", temperature=None)
    assert call is not None, "the step's dispatch had no call log bound"
    call.state = DONE
    call.input_tokens, call.output_tokens = tokens_in, tokens_out
    call.usage_reported = True
    call.cost_usd = 0.001 * (tokens_in + tokens_out)
    return call


class _Result:
    """An `ActionResult` as a provider returns it."""

    def __init__(self, success: bool, *, error: str = "", failure_class: str = "") -> None:
        self.success = success
        self.stdout = '{"ok": true}' if success else ""
        self.outcome = ""
        self.error = error
        self.exit_code = 0 if success else 1
        self.stderr = ""
        self.agent_error = None
        self.failure_class = failure_class
        self.retry_after = 0.0


def _run(config: dict[str, Any], provider: Any) -> RunController:
    spec = {
        "name": "one-action",
        "root": {
            "kind": "sequence",
            "id": "s",
            "children": [{"kind": "action", "id": "ask", "config": config}],
        },
    }
    run = store.create(WorkflowRun(id="", workflow_name=spec["name"]))
    store.write_spec(run.id, spec)
    return RunController(run, spec, services=EngineServices(get_provider=lambda name: provider))


async def test_a_retried_attempt_records_its_usage_and_the_run_is_charged_for_both():
    class FailsOnce:
        attempts = 0

        async def execute(self, cfg, ctx, timeout=30):
            self.attempts += 1
            if self.attempts == 1:
                _answered(100, 20)
                return _Result(False, error="upstream 503", failure_class="transient")
            _answered(40, 10)
            return _Result(True)

    provider = FailsOnce()
    c = _run({"provider": "flaky", "retry": {"max_attempts": 2}}, provider)
    assert await c.run_to_completion(timeout=20) == RunStatus.COMPLETE
    assert provider.attempts == 2, "the control: the first attempt really was retried"

    (failed,) = J.ledger(c.run.id, kinds={J.STEP_FAILED})
    assert failed["retries_exhausted"] is False
    # The attempt record beside it, which the escalation artifact carries, says the same.
    (attempt,) = J.ledger(c.run.id, kinds={J.STEP_ATTEMPT})
    assert attempt["tokens"] == 120
    assert (failed["tokens"], failed["model"], failed["provider"]) == (120, "model-a", "cloud-a")
    assert failed["cost_usd"] == pytest.approx(0.12)
    (done,) = J.ledger(c.run.id, kinds={J.STEP_COMPLETED})
    assert (done["tokens"], done["provider"]) == (50, "cloud-a")

    totals = run_totals(store, c.run.id)
    assert (totals["tokens"], totals["tokens_recorded"]) == (170, True)
    assert totals["cost_usd"] == pytest.approx(0.17)
    # The run row, which a token budget reads, is charged for the attempt that failed too.
    assert store.get(c.run.id).total_tokens == 170
    assert service.introspect(c.run.id)["stats"]["tokens"] == 170


async def test_a_step_that_called_no_model_records_a_measured_zero_not_an_unknown():
    """A failure with no model call behind it cost nothing, and its row says so. Absent usage
    would read as "not recorded" and make every run with a failed transform unpriced."""

    class Refuses:
        async def execute(self, cfg, ctx, timeout=30):
            return _Result(False, error="bad input", failure_class="user")

    c = _run({"provider": "refuses"}, Refuses())
    assert await c.run_to_completion(timeout=20) == RunStatus.FAILED
    (failed,) = J.ledger(c.run.id, kinds={J.STEP_FAILED})
    assert (failed["tokens"], failed["cost_usd"], failed["model"], failed["provider"]) == (
        0,
        0.0,
        "",
        "",
    )
    assert failed["model_calls_open"] == 0
    totals = run_totals(store, c.run.id)
    assert (totals["tokens_recorded"], totals["priced"]) == (True, True)


async def test_a_completed_step_that_left_a_call_behind_records_what_was_reported():
    """One call answered and one was walked away from. The answered one is still a measurement,
    so the row carries it as a floor beside the count of calls nobody reported, rather than
    dropping both to `null`."""

    class AbandonsOne:
        async def execute(self, cfg, ctx, timeout=30):
            _answered(30, 5)
            left = open_call("cloud-a", "model-a", temperature=None)
            assert left is not None
            left.state = ABANDONED
            return _Result(True)

    c = _run({"provider": "abandons"}, AbandonsOne())
    assert await c.run_to_completion(timeout=20) == RunStatus.COMPLETE
    (done,) = J.ledger(c.run.id, kinds={J.STEP_COMPLETED})
    assert (done["tokens"], done["model_calls_open"]) == (35, 1), done

    stats = service.introspect(c.run.id)["stats"]
    assert stats["calls_cut_off"] == 1
    assert stats["tokens_recorded"] is False, "35 is what was reported, not what was spent"

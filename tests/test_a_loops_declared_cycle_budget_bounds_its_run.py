"""A loop node's declared cycle budget (`supervisor.budget.max_cycles`) bounds its run.

It reached only the convergence tick, whose budget check reads a cycle count the run path never
hands it, so a template that declared a budget of 2 cycles ran to its `max_iterations` of 6. The
run's own `max_cycles` override still stands in for both, as it did.
"""

from __future__ import annotations

import pytest

from personalclaw.workflows import journal as J
from personalclaw.workflows import store, supervisor_policy
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


def _spec(supervisor: dict | None) -> dict:
    config: dict = {"mode": "until_dry", "streak": 1, "max_iterations": 6}
    if supervisor is not None:
        config["supervisor"] = supervisor
    return {
        "name": "sweeps",
        "root": {
            "kind": "loop",
            "id": "l",
            "config": config,
            "body": {"kind": "infer", "id": "b", "config": {"prompt": "sweep {{iter}}"}},
        },
    }


async def _iterations(spec: dict, **run_kw) -> tuple[int, RunStatus]:
    async def always_finds(prompt, *, use_case="background", output_type=None):
        # A different finding each cycle, so no breaker trips and only a cycle cap ends the loop.
        return f"found something in {prompt}"

    run = store.create(WorkflowRun(id="", workflow_name=spec["name"], **run_kw))
    store.write_spec(run.id, spec)
    c = RunController(run, spec, services=EngineServices(completion=always_finds))
    status = await c.run_to_completion(timeout=30)
    return len([r for r in J.ledger(run.id) if r["kind"] == J.ITERATION]), status


async def test_a_declared_cycle_budget_stops_the_loop_at_it():
    count, status = await _iterations(_spec({"budget": {"max_cycles": 2}}))
    assert status == RunStatus.ESCALATED
    assert count == 2


async def test_with_none_declared_the_loop_runs_to_its_own_cap():
    count, _status = await _iterations(_spec(None))
    assert count == 6


async def test_the_runs_own_override_stands_in_for_both():
    count, _status = await _iterations(
        _spec({"budget": {"max_cycles": 2}}), policy_overrides={"max_cycles": 3}
    )
    assert count == 3


def test_what_the_loop_surfaces_count_toward_is_what_the_engine_stops_at():
    config = _spec({"budget": {"max_cycles": 2}})["root"]["config"]
    assert supervisor_policy.loop_cycle_cap(config, {}) == 2
    assert supervisor_policy.loop_cycle_cap(config, {"max_cycles": 4}) == 4
    assert supervisor_policy.loop_cycle_cap({"max_iterations": 6}, None) == 6
    assert supervisor_policy.loop_cycle_cap({}, None) == 0

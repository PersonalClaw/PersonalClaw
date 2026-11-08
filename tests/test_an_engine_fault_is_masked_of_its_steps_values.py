"""What an engine fault says of a step is masked of every value the step's bindings filled in.

A step's dispatch that fails inside the engine, not in its provider, is recorded with what the
exception said, and that can hold what the step was handed: it is masked of those values as the
step's own failure is.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.security

VALUE = "ev-8d1f02aa-ORCHARD-FAULT-VALUE"


@pytest.mark.asyncio
async def test_an_engine_fault_is_masked_of_the_values_its_step_was_handed(tmp_path, monkeypatch):
    """🔴 Red before: the run's record held the value."""
    from personalclaw.workflows import step_dispatch, store
    from personalclaw.workflows.controller import EngineServices, RunController
    from personalclaw.workflows.models import RunStatus, WorkflowRun

    home: Path = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: home)
    monkeypatch.setattr("personalclaw.workflows.leases.config_dir", lambda: home)

    async def _faulting(ctl, item, ctx):
        ctx.filled.append(VALUE)  # what its bindings filled in, as they resolve
        raise RuntimeError(f"the engine broke on {VALUE}")

    monkeypatch.setattr(step_dispatch, "execute", _faulting)
    step = {"kind": "transform", "id": "t", "config": {"expr": "{{inputs.x}}"}}
    spec = {"name": "orchard-fault", "root": {"kind": "sequence", "id": "s", "children": [step]}}
    run = store.create(WorkflowRun(id="", workflow_name=spec["name"], inputs={"x": "1"}))
    store.write_spec(run.id, spec)
    controller = RunController(run, spec, services=EngineServices())

    status = await controller.run_to_completion(timeout=20)

    assert status == RunStatus.FAILED
    files = [p for p in store.run_dir(run.id).rglob("*") if p.is_file()]
    assert [str(p) for p in files if VALUE.encode() in p.read_bytes()] == []


#: Where the cut falls: a failure's cause is kept to this many characters.
CUT = 500


def _no_piece_kept(home: Path) -> list[str]:
    """Every file under the home holding the value's first part, the piece a cut leaves."""
    piece = VALUE[:10].encode()
    return sorted(str(p) for p in home.rglob("*") if p.is_file() and piece in p.read_bytes())


@pytest.mark.asyncio
async def test_an_engine_fault_is_masked_before_its_cut(tmp_path, monkeypatch):
    """A fault whose message holds the value across the cut keeps no piece of it: the run's
    index, its state, its journal and its events. 🔴 Red before: masked after the cut, the
    value's first part was kept."""
    from personalclaw.workflows import step_dispatch, store
    from personalclaw.workflows.controller import EngineServices, RunController
    from personalclaw.workflows.models import RunStatus, WorkflowRun

    home: Path = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: home)
    monkeypatch.setattr("personalclaw.workflows.leases.config_dir", lambda: home)

    async def _faulting(ctl, item, ctx):
        ctx.filled.append(VALUE)
        raise RuntimeError("p" * (CUT - len("RuntimeError: ") - 10) + VALUE)

    monkeypatch.setattr(step_dispatch, "execute", _faulting)
    step = {"kind": "transform", "id": "t", "config": {"expr": "{{inputs.x}}"}}
    spec = {"name": "orchard-fault", "root": {"kind": "sequence", "id": "s", "children": [step]}}
    run = store.create(WorkflowRun(id="", workflow_name=spec["name"], inputs={"x": "1"}))
    store.write_spec(run.id, spec)

    status = await RunController(run, spec, services=EngineServices()).run_to_completion(timeout=20)

    assert status == RunStatus.FAILED
    assert any(p.name == "state.json" for p in home.rglob("*")), "vacuity: the run kept its state"
    assert _no_piece_kept(home) == []


@pytest.mark.asyncio
async def test_a_steps_own_failure_is_masked_before_its_cut(tmp_path, monkeypatch):
    """A step whose provider raises, saying the value across the cut, keeps no piece of it.
    🔴 Red before: what it raised was cut first, so the value's first part was kept."""
    from personalclaw.workflows import store
    from personalclaw.workflows.controller import EngineServices, RunController
    from personalclaw.workflows.models import RunStatus, WorkflowRun

    home: Path = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: home)
    monkeypatch.setattr("personalclaw.workflows.leases.config_dir", lambda: home)

    ran: list[bool] = []

    class _Fails:
        async def execute(self, action_config, ctx, timeout=30):
            from personalclaw import filled_secrets

            ran.append(VALUE in filled_secrets.held_here())
            raise RuntimeError("p" * (CUT - len("RuntimeError: ") - 10) + VALUE)

    def _filled(key: str) -> str:
        return VALUE

    step = {"provider": "orchard", "with": {"key": "{{secret:ORCHARD_KEY}}"}}
    children = [{"kind": "action", "id": "a", "config": step}]
    spec = {"name": "orchard-fails", "root": {"kind": "sequence", "id": "s", "children": children}}
    run = store.create(WorkflowRun(id="", workflow_name=spec["name"], inputs={}))
    store.write_spec(run.id, spec)
    monkeypatch.setattr(
        "personalclaw.workflows.node_bindings._secrets_for", lambda *a, **k: _filled
    )
    services = EngineServices(get_provider=lambda name: _Fails())

    status = await RunController(run, spec, services=services).run_to_completion(timeout=20)

    assert status == RunStatus.FAILED
    assert ran == [True], "vacuity: the provider ran, holding the value"
    assert _no_piece_kept(home) == []

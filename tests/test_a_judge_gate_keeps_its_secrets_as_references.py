"""A judge gate keeps its secrets as references, as every step whose text a model reads does.

A judge's ``prompt`` and ``evidence`` are what its model is sent, so each ``{{secret:NAME}}`` there
stays the reference, however the gate's kind is written, and its ``verify`` check is still handed
the value. A judge handed a value anyway is refused before its model is asked.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from personalclaw.config import credentials as cred
from personalclaw.config import loader

pytestmark = pytest.mark.security

NAME = "ORCHARD_JUDGE_TOKEN"
#: Long, unique, sharing no substring with a key name, so a match can only be the VALUE.
VALUE = "jv-5d20c4e1-ORCHARD-JUDGE-VALUE"
REFERENCE = "{{secret:" + NAME + "}}"
#: Long enough for the judge's free tier to read it as something to judge.
BEDS = "the beds were watered twice and the tomatoes are staked along the south fence"


@pytest.fixture
def home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """An isolated home. These tests write a secret, so never the real one."""
    cfg = tmp_path / "home"
    cfg.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(cfg))
    monkeypatch.setattr(loader, "config_dir", lambda: cfg)
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: cfg)
    monkeypatch.setattr("personalclaw.workflows.leases.config_dir", lambda: cfg)
    monkeypatch.delenv(cred.CREDENTIAL_BACKEND_ENV, raising=False)
    # Registered first, so monkeypatch removes whatever a save mirrors in.
    monkeypatch.setenv(NAME, "x")
    monkeypatch.delenv(NAME)
    for key in ("__wf_depth", "__wf_run_id", "__wf_project_id", "__wf_node_id"):
        monkeypatch.delenv(key, raising=False)
    assert loader.env_path() == cfg / ".env", "the .env redirect must hold"
    cred.save_credential(NAME, VALUE)
    os.environ.pop(NAME, None)
    return cfg


class _Model:
    """The judge's model: it keeps every prompt it is sent and passes the work."""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def __call__(self, prompt: str, **_kw: Any) -> str:
        self.prompts.append(prompt)
        return json.dumps({"verdict": "PASS", "reasoning": "the work holds"})


class _Checks:
    """The engine's check runner: it keeps every block it is asked to check, and passes it."""

    def __init__(self) -> None:
        self.blocks: list[Any] = []

    async def __call__(self, block: Any) -> bool:
        self.blocks.append(json.loads(json.dumps(block, default=str)))
        return True


class _Info:
    def __init__(self, agent_id: str) -> None:
        self.id = agent_id
        self.done = True
        self.error = ""
        self.result = "done"
        self.reaped = False
        self.agent = ""


class _Subagents:
    """The agent service, keeping the task each agent is started with."""

    def __init__(self) -> None:
        self.tasks: list[str] = []
        self.infos: dict[str, _Info] = {}

    def spawn(self, **kw: Any) -> _Info:
        self.tasks.append(str(kw.get("task", "")))
        info = _Info(f"sub{len(self.infos) + 1}")
        self.infos[info.id] = info
        return info

    def get(self, agent_id: str) -> _Info | None:
        return self.infos.get(agent_id)


def _spec(*children: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": "orchard-judged",
        "inputs": {
            "task": {"type": "string", "required": False},
            "k": {"type": "string", "required": False},
        },
        "root": {"kind": "sequence", "id": "s", "children": list(children)},
    }


def _gate(**config: Any) -> dict[str, Any]:
    return {"kind": "gate", "id": "judge", "config": config}


async def _run(
    spec: dict[str, Any], *, held: dict[str, list[str]] | None = None, **inputs: Any
) -> tuple[Any, _Model, _Checks, _Subagents]:
    """A run of *spec* with *inputs*, *held* naming the inputs it was handed a reference in, its
    judge's model, check runner and agents scripted."""
    from personalclaw.workflows import input_secrets, store
    from personalclaw.workflows.controller import EngineServices, RunController
    from personalclaw.workflows.models import WorkflowRun

    extra = {input_secrets.EXTRA_KEY: held} if held else {}
    run = store.create(WorkflowRun(id="", workflow_name=spec["name"], inputs=inputs, extra=extra))
    store.write_spec(run.id, spec)
    model, checks, agents = _Model(), _Checks(), _Subagents()
    controller = RunController(
        run, spec, services=EngineServices(completion=model, verify=checks, subagents=agents)
    )
    await controller.run_to_completion(timeout=20)
    return controller, model, checks, agents


def _secret_rows(run_id: str) -> list[dict]:
    from personalclaw.workflows import journal

    return [e for e in journal.ledger(run_id) if e.get("kind") == "secret_read"]


def _holders(home: Path) -> list[str]:
    """Every file under the home that holds the value, but the credential store's own."""
    return sorted(
        str(p)
        for p in home.rglob("*")
        if p.is_file() and p.name != ".env" and VALUE.encode() in p.read_bytes()
    )


def _sent(model: _Model) -> str:
    assert model.prompts, "the judge's model was asked"
    return "\n".join(model.prompts)


@pytest.mark.asyncio
async def test_a_judge_prompt_is_handed_the_reference(home):
    """🔴 Red before: the judge's model was sent the value."""
    controller, model, _, _ = await _run(_spec(_gate(kind="judge", prompt=f"judge {REFERENCE}")))

    assert REFERENCE in _sent(model)
    assert VALUE not in _sent(model)
    assert _holders(home) == []


@pytest.mark.asyncio
async def test_a_judge_evidence_is_handed_the_reference(home):
    """🔴 Red before: the evidence the judge was shown held the value."""
    gate = _gate(kind="judge", prompt="judge the beds", evidence=f"{BEDS}, gate {REFERENCE}")

    _, model, _, _ = await _run(_spec(gate))

    assert REFERENCE in _sent(model)
    assert VALUE not in _sent(model)


@pytest.mark.asyncio
async def test_a_secret_input_a_judge_reads_is_handed_the_reference(home):
    """A run handed a reference in an input, read by a judge. 🔴 Red before: the judge's model was
    sent the value."""
    gate = _gate(kind="judge", prompt="judge {{inputs.task}}")

    _, model, _, _ = await _run(_spec(gate), held={"task": [NAME]}, task=f"water {REFERENCE}")

    assert REFERENCE in _sent(model)
    assert VALUE not in _sent(model)


@pytest.mark.asyncio
async def test_a_judge_whose_kind_is_bound_keeps_its_references(home):
    """A judge's kind read from the run is a judge's, as its dispatch reads it. 🔴 Red before: the
    judge's model was sent the value."""
    gate = _gate(kind="{{inputs.k}}", prompt=f"judge {REFERENCE}")

    _, model, _, _ = await _run(_spec(gate), k="judge")

    assert REFERENCE in _sent(model)
    assert VALUE not in _sent(model)


@pytest.mark.asyncio
async def test_a_stage_and_a_judge_reading_one_held_input_both_get_the_reference(home):
    """A bundled template's shape: a stage and its judge read the same input, which holds a
    reference. 🔴 Red before: the stage's agent got the reference and the judge's model the
    value."""
    stage = {"kind": "stage", "id": "work", "config": {"prompt": "do {{inputs.task}}"}}
    gate = _gate(kind="judge", prompt="judge whether {{inputs.task}} was done")

    _, model, _, agents = await _run(
        _spec(stage, gate), held={"task": [NAME]}, task=f"water {REFERENCE}"
    )

    assert agents.tasks and all(REFERENCE in task and VALUE not in task for task in agents.tasks)
    assert REFERENCE in _sent(model)
    assert VALUE not in _sent(model)


@pytest.mark.asyncio
async def test_a_judge_records_no_read_of_a_kept_secret(home):
    """A judge's model works for no agent of the run, so nothing reads the reference it keeps
    there. 🔴 Red before: the value was read and recorded."""
    controller, _, _, _ = await _run(_spec(_gate(kind="judge", prompt=f"judge {REFERENCE}")))

    assert _secret_rows(controller.run.id) == []


@pytest.mark.asyncio
async def test_a_judge_gates_check_is_handed_the_value(home):
    """A control: the check a judge runs is handed the value, while its prompt keeps the
    reference."""
    gate = _gate(
        kind="judge", prompt=f"judge {REFERENCE}", verify={"command": f"check {REFERENCE}"}
    )

    _, model, checks, _ = await _run(_spec(gate))

    assert checks.blocks == [{"command": f"check {VALUE}"}]
    assert VALUE not in _sent(model)


@pytest.mark.asyncio
async def test_a_verify_command_gate_is_handed_the_value(home):
    """A control: a gate that runs a check is handed the value."""
    gate = _gate(kind="verify_command", verify={"command": f"check {REFERENCE}"})

    _, model, checks, _ = await _run(_spec(gate))

    assert checks.blocks == [{"command": f"check {VALUE}"}]
    assert model.prompts == []


#: Each text a judge's model is sent, naming the secret.
_SENT = {
    "its-prompt": {"prompt": f"judge {REFERENCE}"},
    "its-evidence": {"prompt": "judge the beds", "evidence": f"{BEDS}, gate {REFERENCE}"},
}


@pytest.mark.asyncio
@pytest.mark.parametrize("where", sorted(_SENT))
async def test_the_judge_is_refused_a_filled_value(home, where):
    """Whatever chose the gate's resolver, its model is never sent a value. 🔴 Red before: the
    model was asked, with the value."""
    from personalclaw.workflows import engine
    from personalclaw.workflows.bindings import BindingContext
    from personalclaw.workflows.models import (
        REFUSED_TO_START,
        FailureClass,
        InstanceState,
        Node,
        NodeKind,
    )

    node = Node(kind=NodeKind.GATE, id="judge", config={"kind": "judge", **_SENT[where]})
    model = _Model()
    ctx = BindingContext(secret_resolver=lambda key: VALUE)

    result = await engine.dispatch_gate(node, ctx, now=0.0, completion=model)

    assert result.state == InstanceState.FAILED
    assert result.failure.failure_class == FailureClass.PERMISSION
    assert result.failure.terminal_reason == REFUSED_TO_START
    assert model.prompts == [], "the model is never asked"
    assert VALUE not in f"{result.failure.cause_plain} {result.failure.remediation}"

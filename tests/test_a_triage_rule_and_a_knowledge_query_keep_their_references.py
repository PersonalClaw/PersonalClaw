"""A triage rule and a knowledge query keep each secret as its reference.

The triage digest's rules are what its model classifies by, and a knowledge retrieval's query is
what its reranking model reads, so both providers say their config goes to a model, and a
``{{secret:NAME}}`` there is handed to them as the reference, from an automation's fire and from a
workflow step alike.
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

NAME = "ORCHARD_RULE_TOKEN"
#: Long, unique, sharing no substring with a key name, so a match can only be the VALUE.
VALUE = "rv-2a7c90d4-ORCHARD-RULE-VALUE"
REFERENCE = "{{secret:" + NAME + "}}"

#: Each provider, and a config naming the secret where its model reads it.
CONFIGS = {
    "triage-digest": {"filter_rules": [{"source": "inbox", "rule": f"skip {REFERENCE}"}]},
    "knowledge-retrieve": {"query": f"cold start {REFERENCE}"},
}


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


def _provider(name: str) -> Any:
    from personalclaw.action_providers.registry import (
        _ensure_default_providers_registered,
        get_action_provider,
    )

    _ensure_default_providers_registered()
    return get_action_provider(name)


@pytest.mark.parametrize("name", sorted(CONFIGS))
def test_an_automations_fire_hands_it_the_reference(home, name):
    """🔴 Red before: the fire filled the value in."""
    from personalclaw.triggers.secrets import resolve_for

    handed, filled = resolve_for(_provider(name), CONFIGS[name])

    assert handed == CONFIGS[name]
    assert filled == ()


@pytest.mark.asyncio
@pytest.mark.parametrize("name", sorted(CONFIGS))
async def test_a_workflow_step_hands_it_the_reference(home, monkeypatch, name):
    """🔴 Red before: the step's provider was handed the value."""
    from personalclaw.action_providers.base import ActionResult
    from personalclaw.workflows import store
    from personalclaw.workflows.controller import EngineServices, RunController
    from personalclaw.workflows.models import RunStatus, WorkflowRun

    provider = _provider(name)
    handed: list[Any] = []

    async def _execute(cfg: Any, ctx: Any, timeout: int = 30) -> ActionResult:
        handed.append(json.loads(json.dumps(cfg, default=str)))
        return ActionResult(success=True, stdout="{}")

    monkeypatch.setattr(provider, "execute", _execute)
    step = {"kind": "action", "id": "ask", "config": {"provider": name, "with": CONFIGS[name]}}
    spec = {"name": "orchard-ask", "root": {"kind": "sequence", "id": "s", "children": [step]}}
    run = store.create(WorkflowRun(id="", workflow_name=spec["name"], inputs={}))
    store.write_spec(run.id, spec)
    controller = RunController(
        run, spec, services=EngineServices(get_provider={name: provider}.get)
    )

    status = await controller.run_to_completion(timeout=20)

    assert status == RunStatus.COMPLETE
    assert handed == [CONFIGS[name]]

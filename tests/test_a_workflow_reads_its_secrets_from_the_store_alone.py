"""A workflow's ``{{secret:NAME}}`` is read from the Secrets store alone, never from the gateway's
own environment.

A workflow's ``{{secret:NAME}}`` could be filled from the gateway's own environment, not only from
Settings → Secrets. Now a workflow reads the store alone, at run time and at run start: a name only
the gateway's environment holds is a secret that isn't set, and a name both hold is read from the
store. And the first sign-in password a container enrolls from the environment is taken out of the
gateway's environment, and out of the one its Restart starts with, once it has been read.
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

NAME = "HOSTONLY_GARDEN_TOKEN"
#: Long, unique, sharing no substring with a key name, so a match can only be a VALUE.
HOST_VALUE = "hv-2f8d4c1e-HOST-ENVIRONMENT-VALUE"
STORED_VALUE = "sv-9a3e7b60-STORED-SECRET-VALUE"
REFERENCE = "{{secret:" + NAME + "}}"


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
    # Registered first, so monkeypatch removes whatever a test or a save puts there.
    monkeypatch.setenv(NAME, "x")
    monkeypatch.delenv(NAME)
    for key in ("__wf_depth", "__wf_run_id", "__wf_project_id", "__wf_node_id"):
        monkeypatch.delenv(key, raising=False)
    assert loader.env_path() == cfg / ".env", "the .env redirect must hold"
    return cfg


class _Result:
    success = True
    stdout = '{"ok": true}'
    outcome = ""
    error = ""
    exit_code = 0
    stderr = ""
    agent_error = None
    failure_class = ""
    retry_after = 0.0


class _Sender:
    """An action that uses its config itself, as a shell, a fetch or a notify action does."""

    hands_config_to_a_model = False

    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def execute(self, cfg, ctx, timeout=30):
        self.sent.append(json.loads(json.dumps(cfg, default=str)))
        return _Result()


#: One step of each kind that uses its config itself, each naming the secret.
STEPS: dict[str, dict[str, Any]] = {
    "an-action": {
        "kind": "action",
        "id": "use",
        "config": {"provider": "sends-it", "with": {"token": f"Bearer {REFERENCE}"}},
    },
    "a-transform": {"kind": "transform", "id": "use", "config": {"expr": f"Bearer {REFERENCE}"}},
    "a-gate": {
        "kind": "gate",
        "id": "use",
        "config": {"kind": "expression", "expr": "1 == 1", "message": f"token {REFERENCE}"},
    },
}


async def _run(step: dict[str, Any]) -> tuple[Any, Any, _Sender]:
    from personalclaw.workflows import store
    from personalclaw.workflows.controller import EngineServices, RunController
    from personalclaw.workflows.models import WorkflowRun

    spec = {
        "name": "garden-host",
        "root": {"kind": "sequence", "id": "s", "children": [step]},
    }
    sender = _Sender()
    run = store.create(WorkflowRun(id="", workflow_name=spec["name"]))
    store.write_spec(run.id, spec)
    controller = RunController(
        run, spec, services=EngineServices(get_provider={"sends-it": sender}.get)
    )
    status = await controller.run_to_completion(timeout=20)
    return status, controller, sender


def _failure_of(controller: Any) -> str:
    inst = controller.instances["root.children[0]"]
    failure = inst.failure
    return f"{failure.cause_plain} {failure.remediation}" if failure else ""


def _what_it_was_handed(controller: Any, sender: _Sender) -> str:
    """What the step was handed: the action's config, or the transform's or gate's output."""
    return json.dumps([sender.sent, controller._outputs.get("use")], default=str)


def _in_env(key: str) -> bool:
    """Whether *key* is in this process's environment. A bool, so a failing assertion prints no
    part of the environment."""
    return key in os.environ


def _holders(home: Path, value: str) -> list[str]:
    """Every file under the home that holds *value*, but the credential store's own."""
    return sorted(
        str(p)
        for p in home.rglob("*")
        if p.is_file() and p.name != ".env" and value.encode() in p.read_bytes()
    )


# ── a run ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", sorted(STEPS))
async def test_a_name_only_the_gateways_environment_holds_is_a_secret_that_is_not_set(
    home, monkeypatch, kind
):
    """🔴 Red before: the step was handed the environment's value."""
    from personalclaw.workflows.models import RunStatus

    monkeypatch.setenv(NAME, HOST_VALUE)

    status, controller, sender = await _run(STEPS[kind])

    assert status == RunStatus.FAILED
    assert f"secret {NAME!r} is not set" in _failure_of(controller), _failure_of(controller)
    assert "Settings → Secrets" in _failure_of(controller)
    assert HOST_VALUE not in _what_it_was_handed(controller, sender)
    assert _holders(home, HOST_VALUE) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", sorted(STEPS))
async def test_a_secret_saved_in_the_store_fills_as_before(home, kind):
    """The control: a secret saved in Settings → Secrets fills each kind of step."""
    from personalclaw.workflows.models import RunStatus

    cred.save_credential(NAME, STORED_VALUE)
    os.environ.pop(NAME, None)

    status, controller, sender = await _run(STEPS[kind])

    assert status == RunStatus.COMPLETE, _failure_of(controller)
    if kind == "an-action":
        assert sender.sent == [{"token": f"Bearer {STORED_VALUE}"}]


@pytest.mark.asyncio
async def test_the_store_is_read_whatever_the_gateways_environment_holds_by_that_name(
    home, monkeypatch
):
    """A step is handed the stored secret, not another value the gateway's environment holds under
    its name. An action, because it shows what it was handed: a transform's or a gate's output is
    masked of every value its step was filled with. 🔴 Red before: it got the environment's."""
    from personalclaw.workflows.models import RunStatus

    cred.save_credential(NAME, STORED_VALUE)
    monkeypatch.setenv(NAME, HOST_VALUE)

    status, controller, sender = await _run(STEPS["an-action"])

    assert status == RunStatus.COMPLETE, _failure_of(controller)
    assert sender.sent == [{"token": f"Bearer {STORED_VALUE}"}]


# ── run start ────────────────────────────────────────────────────────────────


def _spec_naming_it() -> dict[str, Any]:
    return {"name": "garden-host", "root": STEPS["an-action"]}


def test_run_start_reports_a_name_only_the_gateways_environment_holds_as_missing(home, monkeypatch):
    """Run start checks a secret as the run will read it. 🔴 Red before: it counted the name as
    set, and the run then filled the environment's value."""
    from personalclaw.workflows.preflight import preflight

    monkeypatch.setenv(NAME, HOST_VALUE)

    result = preflight(_spec_naming_it())

    codes = [f.code for f in result.findings]
    assert "WF_PRE_CREDENTIAL_MISSING" in codes, codes
    assert HOST_VALUE not in json.dumps([f.message for f in result.findings])


def test_run_start_finds_a_secret_saved_in_the_store(home):
    from personalclaw.workflows.preflight import preflight

    cred.save_credential(NAME, STORED_VALUE)
    os.environ.pop(NAME, None)

    result = preflight(_spec_naming_it())

    assert [f.code for f in result.findings if f.kind == "credentials"] == []


# ── the first sign-in password a container enrolls ───────────────────────────

PASSWORD = "a-deploy-time-password-long-enough"


@pytest.mark.parametrize(
    ("user", "password", "already_set", "enrolled"),
    [
        ("deploy", PASSWORD, False, True),
        ("deploy", PASSWORD, True, False),
        ("", PASSWORD, False, False),
        ("deploy", "tiny", False, False),
    ],
    ids=["enrolled", "already-set", "no-user", "too-short"],
)
def test_the_first_sign_in_password_is_out_of_the_environment_once_read(
    tmp_path, monkeypatch, user, password, already_set, enrolled
):
    """The gateway's start reads the password once, and nothing reads it again: so it is taken out
    of the gateway's environment, which every program it starts inherits, and out of the
    environment it was launched with, which a restart starts its new image with, whether or not it
    was enrolled. 🔴 Red before: it stayed in both."""
    from personalclaw import env
    from personalclaw.auth import credentials as creds

    monkeypatch.setattr(creds, "config_dir", lambda: tmp_path)
    if already_set:
        creds.set_password("jordan", "the-rotated-password-long-enough")
    if user:
        monkeypatch.setenv("PERSONALCLAW_LOGIN_USER", user)
    else:
        monkeypatch.delenv("PERSONALCLAW_LOGIN_USER", raising=False)
    monkeypatch.setenv("PERSONALCLAW_LOGIN_PASSWORD", password)
    # The environment the gateway was launched with, as its entry point records it.
    monkeypatch.setattr(env, "_startup", dict(os.environ))

    assert creds.bootstrap_from_env() is enrolled

    assert _in_env("PERSONALCLAW_LOGIN_PASSWORD") is False
    launched_with_it = "PERSONALCLAW_LOGIN_PASSWORD" in env.launch_env()
    assert launched_with_it is False
    assert creds.verify_password(user or "deploy", password) is enrolled

"""One credential store: every reader and writer uses the one Settings → Secrets writes.

Two stores held secrets. ``config.credentials`` (the OS keychain when it is on, else
``<home>/.env`` at 0600) is the one Settings → Secrets, every settings record's ``{{secret:…}}``,
the channels and the apps' setup steps write. ``<home>/credentials.json``, read through
``llm.credentials.CredentialStore``, is the one the workflow engine, triggers, knowledge connector
packs, a provider entry's ``credential`` and the apps' ``personalclaw.sdk.credentials`` read. It
fell back to ``.env`` only for a name it held a descriptor for, and never read the keychain.
Measured on ``main``:

* A secret saved in Settings → Secrets never reached a workflow step or a trigger that named it:
  ``CredentialStore.resolve`` raised ``KeyError`` for every name without a descriptor.
* ``personalclaw setup --credential NAME=VALUE`` wrote ``credentials.json``, which Settings →
  Secrets does not list and the channels and apps do not read.
* A connector pack import rebuilt ``credentials.json`` from names alone, so every other inline
  value in it was deleted, including the web push keys.
* An app could read another owner's key. The SDK's ``CredentialStore`` could save a descriptor
  named ``PCSECRET_…`` and then resolve it, which read that key out of ``.env`` past the owner
  check #3626 added to ``{{secret:…}}`` references.

Every test here writes credentials, so the home is redirected (``PERSONALCLAW_HOME`` and
``loader.config_dir``) and the fixture asserts the redirect before anything is written.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from pathlib import Path
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.config import credentials as cred
from personalclaw.config import loader

pytestmark = pytest.mark.anyio

SECRETS_VALUE = "sv-5d1e-SETTINGS-SECRETS-VALUE"
CLI_VALUE = "cv-77aa-SETUP-CREDENTIAL-VALUE"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """An isolated home. These tests write secrets, so never the real one."""
    cfg = tmp_path / "home"
    cfg.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(cfg))
    monkeypatch.setattr(loader, "config_dir", lambda: cfg)
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: cfg)
    monkeypatch.delenv(cred.CREDENTIAL_BACKEND_ENV, raising=False)
    assert loader.env_path() == cfg / ".env", "the .env redirect must hold"
    return cfg


def _forget_env(monkeypatch: pytest.MonkeyPatch, *names: str) -> None:
    """Unset *names* now and at teardown. ``save_credential`` mirrors a named key into
    ``os.environ``; registering the variable first makes monkeypatch remove it afterwards."""
    for name in names:
        monkeypatch.setenv(name, "x")
        monkeypatch.delenv(name)


async def _save_in_settings_secrets(name: str, value: str) -> None:
    """What Settings → Secrets does: ``POST /api/secrets``."""
    from personalclaw.dashboard.handlers.secrets import register_secrets_routes

    app = web.Application()
    register_secrets_routes(app)
    async with TestClient(TestServer(app)) as client:
        resp = await client.post("/api/secrets", json={"name": name, "value": value})
        assert resp.status == 200, await resp.text()


def _action_spec(name: str, config: dict[str, Any]) -> dict:
    return {
        "name": name,
        "root": {
            "kind": "sequence",
            "id": "s",
            "children": [{"kind": "action", "id": "send", "config": config}],
        },
    }


class _Result:
    """An ``ActionResult`` as a provider returns it."""

    success = True
    stdout = '{"ok": true}'
    outcome = ""
    error = ""
    exit_code = 0
    stderr = ""
    agent_error = None
    failure_class = ""
    retry_after = 0.0


async def _run_action(spec: dict) -> tuple[Any, Any, list[dict]]:
    """Run *spec* to the end with an action provider that records the config it received."""
    from personalclaw.workflows import store
    from personalclaw.workflows.controller import EngineServices, RunController
    from personalclaw.workflows.models import WorkflowRun

    sent: list[dict] = []

    class Recorder:
        async def execute(self, cfg, ctx, timeout=30):
            sent.append(json.loads(json.dumps(cfg, default=str)))
            return _Result()

    run = store.create(WorkflowRun(id="", workflow_name=spec["name"]))
    store.write_spec(run.id, spec)
    controller = RunController(
        run, spec, services=EngineServices(get_provider=lambda _name: Recorder())
    )
    status = await controller.run_to_completion(timeout=20)
    return status, controller, sent


# ── a secret saved in Settings → Secrets reaches everything that names it ─────────


async def test_a_secret_saved_in_settings_secrets_reaches_a_workflow_step(home, monkeypatch):
    from personalclaw.workflows import preflight
    from personalclaw.workflows.models import RunStatus

    _forget_env(monkeypatch, "WF_TOKEN")
    await _save_in_settings_secrets("WF_TOKEN", SECRETS_VALUE)
    # After a restart the value is in the store, not in this process's environment.
    monkeypatch.delenv("WF_TOKEN")
    spec = _action_spec(
        "uses-a-vault-secret",
        {"provider": "notify", "with": {"token": "Bearer {{secret:WF_TOKEN}}"}},
    )

    assert preflight.preflight(spec).ok, "run start refused a secret Settings → Secrets holds"
    status, _controller, sent = await _run_action(spec)

    assert status == RunStatus.COMPLETE
    assert f"Bearer {SECRETS_VALUE}" in json.dumps(sent), sent


async def test_a_secret_saved_in_settings_secrets_reaches_a_trigger_action(home, monkeypatch):
    from personalclaw.triggers import secrets as trigger_secrets

    _forget_env(monkeypatch, "TRIGGER_TOKEN")
    await _save_in_settings_secrets("TRIGGER_TOKEN", SECRETS_VALUE)
    monkeypatch.delenv("TRIGGER_TOKEN")

    resolved = trigger_secrets.resolve({"command": "curl -H 'X: {{secret:TRIGGER_TOKEN}}'"})

    assert resolved == {"command": f"curl -H 'X: {SECRETS_VALUE}'"}


async def test_a_provider_credential_and_an_app_read_a_settings_secrets_secret(home, monkeypatch):
    """A provider entry's ``credential`` and an app's ``sdk.credentials.CredentialStore`` read
    the name through the same interface; both answered ``KeyError`` for a vault secret."""
    from personalclaw.config.loader import config_dir
    from personalclaw.llm.branded_specs import resolve_credential

    # The class ``personalclaw.sdk.credentials`` re-exports for apps (the same object).
    from personalclaw.llm.credentials import CredentialStore
    from personalclaw.llm.registry import ProviderEntry

    _forget_env(monkeypatch, "PROVIDER_TOKEN")
    await _save_in_settings_secrets("PROVIDER_TOKEN", SECRETS_VALUE)
    monkeypatch.delenv("PROVIDER_TOKEN")
    entry = ProviderEntry(name="p", type="t", model="", credential="PROVIDER_TOKEN")

    credential = resolve_credential(
        entry, {"credential_store": CredentialStore(config_dir())}, label="p"
    )

    assert credential is not None and credential.secret == SECRETS_VALUE
    assert CredentialStore(config_dir()).resolve("PROVIDER_TOKEN").secret == SECRETS_VALUE


async def test_test_connection_and_discovery_get_the_key_a_provider_credential_names(
    home, monkeypatch
):
    """A catalog (Test connection, the model list) is built from the entry's options, so an
    entry authenticating with a named credential reached it with no key at all: "No API key
    configured", while a chat turn through the same entry had the key."""
    from personalclaw.llm.registry import ProviderEntry, ProviderRegistry

    _forget_env(monkeypatch, "CATALOG_TOKEN")
    await _save_in_settings_secrets("CATALOG_TOKEN", SECRETS_VALUE)
    monkeypatch.delenv("CATALOG_TOKEN")
    seen: list[dict] = []
    registry = ProviderRegistry()
    registry.register_catalog("t", lambda options, *, model="": seen.append(options) or object())

    registry.build_catalog(ProviderEntry(name="p", type="t", model="", credential="CATALOG_TOKEN"))
    registry.build_catalog(
        ProviderEntry(
            name="q", type="t", model="", options={"api_key": "own"}, credential="CATALOG_TOKEN"
        )
    )

    assert [options.get("api_key") for options in seen] == [SECRETS_VALUE, "own"]

    # The Models page builds its entry from the raw config record; it carries the credential.
    from personalclaw.dashboard.handlers import model_registry

    monkeypatch.setattr("personalclaw.llm.registry.get_default_registry", lambda: registry)
    model_registry._catalog_for_config_provider(
        {"name": "r", "type": "t", "model": "", "credential": "CATALOG_TOKEN"}
    )
    assert seen[-1].get("api_key") == SECRETS_VALUE


# ── the writers ──────────────────────────────────────────────────────────────────


def test_setup_credential_saves_into_the_store_settings_secrets_lists(home, monkeypatch):
    from personalclaw import secrets_vault
    from personalclaw.cli_setup import _setup_noninteractive

    _forget_env(monkeypatch, "CLI_TOKEN")

    _setup_noninteractive(credential=f"CLI_TOKEN={CLI_VALUE}")

    assert cred.get_credential("CLI_TOKEN") == CLI_VALUE
    rows = {row.name: row.scope for row in secrets_vault.list_presence()}
    assert rows.get("CLI_TOKEN") == secrets_vault.SCOPE_GLOBAL, rows
    assert not (home / "credentials.json").exists()


def test_setup_credential_help_says_where_the_secret_goes():
    from personalclaw.cli import build_parser

    (commands,) = [a for a in build_parser()._actions if isinstance(a, argparse._SubParsersAction)]
    (flag,) = [a for a in commands.choices["setup"]._actions if "--credential" in a.option_strings]

    assert "Settings → Secrets" in flag.help, flag.help


def test_a_connector_pack_import_keeps_every_other_credential(home, monkeypatch):
    from personalclaw import push
    from personalclaw.cli_setup import _setup_noninteractive
    from personalclaw.llm.credentials import CredentialStore
    from personalclaw.packs.connectors import _save_credentials

    _forget_env(monkeypatch, "CLI_TOKEN", "PACK_TOKEN")
    _setup_noninteractive(credential=f"CLI_TOKEN={CLI_VALUE}")
    keys = push.push_init()
    monkeypatch.delenv("CLI_TOKEN", raising=False)

    _save_credentials(["PACK_TOKEN"], {"PACK_TOKEN": "pack-value"})

    assert CredentialStore(home).resolve("CLI_TOKEN").secret == CLI_VALUE
    assert push.vapid_keys() == keys, "the pack import deleted the web push keys"
    assert cred.get_credential("PACK_TOKEN") == "pack-value"


# ── an owned key is read only through the record that owns it ──────────────────


def _owned_key() -> str:
    from personalclaw.config.secret_refs import provider_owner

    return provider_owner("someones-provider").key("api_key")


async def test_a_workflow_step_naming_an_owned_key_is_refused_and_says_why(home):
    from personalclaw.workflows.models import RunStatus

    owned = _owned_key()
    cred.save_credential(owned, "sk-owned-by-a-provider")
    spec = _action_spec(
        "reads-an-owned-key", {"provider": "notify", "with": {"k": f"{{{{secret:{owned}}}}}"}}
    )

    status, controller, sent = await _run_action(spec)

    assert status == RunStatus.FAILED
    assert sent == [], "the step ran with another record's key"
    failure = controller.instances["root.children[0]"].failure
    text = f"{failure.cause_plain} {failure.remediation}"
    assert "Settings → Secrets" in text and "is not set" not in text, text


def test_an_owned_key_an_app_saved_a_descriptor_for_is_refused(home):
    """What ``CredentialStore.save`` let an app do on ``main``: plant a descriptor for another
    owner's key, then resolve it out of ``.env``."""
    # The class ``personalclaw.sdk.credentials`` re-exports for apps (the same object).
    from personalclaw.llm.credentials import CredentialStore

    owned = _owned_key()
    cred.save_credential(owned, "sk-owned-by-a-provider")
    (home / "credentials.json").write_text(
        json.dumps({owned: {"type": "api_key"}}), encoding="utf-8"
    )

    with pytest.raises(KeyError) as refused:  # on main this returned the provider's key
        CredentialStore(home).resolve(owned)

    assert type(refused.value).__name__ == "OwnedCredentialRefused"
    assert owned in str(refused.value) and "Settings → Secrets" in str(refused.value)
    assert "sk-owned-by-a-provider" not in str(refused.value)


# ── the boot move: credentials.json into the store, verified before it is deleted ──


def _write_credentials_file(home: Path, descriptors: dict[str, dict[str, Any]]) -> Path:
    path = home / "credentials.json"
    path.write_text(json.dumps(descriptors, indent=2), encoding="utf-8")
    path.chmod(0o600)
    return path


def test_the_boot_move_keeps_the_file_when_a_value_does_not_verify(home, monkeypatch, caplog):
    from personalclaw.llm import credentials as legacy

    _forget_env(monkeypatch, "KEPT_TOKEN", "LOST_TOKEN")
    path = _write_credentials_file(
        home,
        {
            "KEPT_TOKEN": {"type": "api_key", "value": "kept-value"},
            "LOST_TOKEN": {"type": "api_key", "value": "lost-value"},
        },
    )
    before = path.read_bytes()
    real_find = cred.find_credential
    # The store "keeps" every value but one: the read-back is what catches it.
    monkeypatch.setattr(
        cred,
        "find_credential",
        lambda key, **kw: ("", "") if key == "LOST_TOKEN" else real_find(key, **kw),
    )

    with caplog.at_level(logging.WARNING):
        report = legacy.move_credentials_file()

    assert path.read_bytes() == before, "credentials.json changed although a value did not verify"
    assert report.removed is False
    assert [leftover.name for leftover in report.leftovers] == ["LOST_TOKEN"]
    assert "LOST_TOKEN" in caplog.text
    assert "lost-value" not in caplog.text and "kept-value" not in caplog.text


def test_the_boot_move_stores_every_value_then_removes_the_file(home, monkeypatch):
    from personalclaw import push, secrets_vault
    from personalclaw.llm import credentials as legacy

    _forget_env(monkeypatch, "OLD_TOKEN")
    _write_credentials_file(
        home,
        {
            "OLD_TOKEN": {"type": "api_key", "value": "old-value"},
            "PERSONALCLAW_VAPID_PUBLIC": {"type": "static_token", "value": "pub-b64"},
            "PERSONALCLAW_VAPID_PRIVATE": {"type": "static_token", "value": "priv-b64"},
            "KEYLESS": {"type": "none"},
        },
    )

    report = legacy.move_credentials_file()

    assert report.removed is True and report.leftovers == []
    assert not (home / "credentials.json").exists()
    assert cred.get_credential("OLD_TOKEN") == "old-value"
    assert push.vapid_keys() == ("pub-b64", "priv-b64")
    listed = {row.name for row in secrets_vault.list_presence()}
    assert "OLD_TOKEN" in listed
    assert not any("VAPID" in name for name in listed), "the push keys are not a user secret"
    # Idempotent: a second start finds nothing to do.
    assert legacy.move_credentials_file().removed is False


def test_a_descriptor_that_read_another_variable_stays_until_the_secret_is_stored(
    home, monkeypatch
):
    from personalclaw.llm import credentials as legacy
    from personalclaw.resilience import doctor

    _forget_env(monkeypatch, "RENAMED_TOKEN")
    path = _write_credentials_file(
        home, {"RENAMED_TOKEN": {"type": "api_key", "value_env": "SOME_OTHER_VARIABLE"}}
    )

    report = legacy.move_credentials_file()

    assert report.removed is False and path.exists()
    (leftover,) = report.leftovers
    assert leftover.name == "RENAMED_TOKEN" and "SOME_OTHER_VARIABLE" in leftover.reason
    probe = next(p for p in doctor.all_probes() if p.id == "security.credentials_file")
    result = asyncio.run(probe.run(doctor.DoctorContext(home=home)))
    assert result.ok is False
    assert "RENAMED_TOKEN" in result.detail and "Settings → Secrets" in result.remedy

    cred.save_credential("RENAMED_TOKEN", "now-stored")
    assert legacy.move_credentials_file().removed is True
    assert not path.exists()
    result = asyncio.run(probe.run(doctor.DoctorContext(home=home)))
    assert result.ok is True

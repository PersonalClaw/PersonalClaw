"""An app's secret settings live in the credential store; its settings file holds a reference.

Measured on the real image: slack-channel's Bot and App tokens sat in plaintext in
``apps/slack-channel/data/config.json`` and SURVIVED ``DELETE /api/apps/slack-channel?remove=1``
in the parked ``data/`` copy. Three writers persisted a declared-sensitive setting inline:
``ProviderSettings.save`` (Settings → Providers), ``app_config.write_config`` (the Apps config
form) and the multi-instance store (``extensions/<app>/instances/<id>.json``).

Each test reads the bytes on disk rather than trusting a return value, and the uninstall legs
install a real fixture bundle and run the real removal rungs.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from personalclaw.apps import app_config, app_manager, manager
from personalclaw.config import loader as config_loader
from personalclaw.config.credentials import credential_names
from personalclaw.providers import instances
from personalclaw.providers.settings import ProviderSettings

TOKEN = "xoxb-fixture-4d3c2b1a-never-plaintext"
APP_TOKEN = "xapp-fixture-9e8d7c6b-never-plaintext"
PASSCODE = "fixture-passcode-1a2b3c4d"
INSTANCE_KEY = "sk-fixture-instance-6f5e4d3c"
ROTATED = "sk-fixture-instance-rotated-0a9b8c7d"

APP = "fixture-secret-app"

CONFIG_SCHEMA = {
    "type": "object",
    "properties": {
        # Declared sensitive, but NOT credential-shaped by name — only the declaration can
        # route it, so this field is what proves the manifest's `x-meta.sensitive` is read.
        "passcode": {"type": "string", "x-meta": {"sensitive": True}},
        "bot_token": {"type": "string", "x-meta": {"sensitive": True}},
        "channel": {"type": "string"},
    },
}


@pytest.fixture(autouse=True)
def home(monkeypatch):
    monkeypatch.setattr("personalclaw.config.credentials._usable_keyring", lambda: None)
    previous = os.umask(0o022)
    try:
        yield config_loader.config_dir()
    finally:
        os.umask(previous)


def _hits(home: Path, needle: str) -> list[str]:
    out = []
    for path in sorted(home.rglob("*")):
        if path.is_file() and not path.is_symlink() and needle.encode() in path.read_bytes():
            out.append(str(path.relative_to(home)))
    return out


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def _bundle(tmp_path: Path) -> Path:
    src = tmp_path / "bundle" / APP
    src.mkdir(parents=True)
    (src / "app.json").write_text(
        json.dumps(
            {
                "name": APP,
                "version": "1.0.0",
                "displayName": "Secret Fixture",
                "description": "An app whose settings hold secrets",
                "setup": {"configSchema": CONFIG_SCHEMA},
            }
        ),
        encoding="utf-8",
    )
    return src


def test_provider_settings_secret_never_lands_in_the_app_config_file(home):
    ProviderSettings.save(APP, {"bot_token": TOKEN, "app_token": APP_TOKEN, "command": "pc"})

    path = ProviderSettings.config_path(APP)
    text = path.read_text(encoding="utf-8")
    assert TOKEN not in text and APP_TOKEN not in text, "a token reached the app's config file"
    assert json.loads(text)["command"] == "pc", "non-secret settings stay in the file"

    loaded = ProviderSettings.load(APP)
    assert loaded["bot_token"] == TOKEN and loaded["app_token"] == APP_TOKEN
    assert _hits(home, TOKEN) == [".env"]


def test_app_config_file_is_0600_in_a_0700_directory(home):
    ProviderSettings.save(APP, {"bot_token": TOKEN})

    path = ProviderSettings.config_path(APP)
    assert _mode(path) == 0o600, oct(_mode(path))
    assert _mode(path.parent) == 0o700, oct(_mode(path.parent))


def test_a_declared_sensitive_field_goes_to_the_store(home):
    """The Apps config form's writer: `passcode` is secret only because the schema says so."""
    saved = app_config.write_config(
        APP, {"passcode": PASSCODE, "channel": "general"}, CONFIG_SCHEMA
    )

    text = (manager.app_dir(APP) / "data" / "config.json").read_text(encoding="utf-8")
    assert PASSCODE not in text
    assert saved["passcode"] == PASSCODE, "the caller still gets the value it saved"
    assert app_config.read_config(APP)["passcode"] == PASSCODE
    assert _hits(home, PASSCODE) == [".env"]


def test_an_instance_secret_lives_in_the_store_through_create_update_delete(home):
    inst = instances.create_instance(
        "fixture-multi", "Work", {"api_key": INSTANCE_KEY, "endpoint": "https://x.invalid"}
    )
    assert _hits(home, INSTANCE_KEY) == [".env"]
    assert instances.get_instance("fixture-multi", inst.id).config["api_key"] == INSTANCE_KEY
    assert instances.list_instances("fixture-multi")[0].config["api_key"] == INSTANCE_KEY

    instances.update_instance(
        "fixture-multi", inst.id, config={"api_key": ROTATED, "endpoint": "https://x.invalid"}
    )
    assert _hits(home, INSTANCE_KEY) == [], "the rotated-away key lingered"
    assert _hits(home, ROTATED) == [".env"]

    assert instances.delete_instance("fixture-multi", inst.id) is True
    assert _hits(home, ROTATED) == [], "deleting the instance left its key in the store"


def test_keep_data_uninstall_removes_the_apps_stored_secrets(home, tmp_path):
    assert app_manager.install(_bundle(tmp_path), confirm=True).ok
    app_config.write_config(APP, {"passcode": PASSCODE, "bot_token": TOKEN}, CONFIG_SCHEMA)

    assert app_manager.uninstall_keep_data(APP) is True

    assert _hits(home, TOKEN) == [] and _hits(home, PASSCODE) == [], (
        "the uninstalled app's secrets survived: " f"{_hits(home, TOKEN) + _hits(home, PASSCODE)}"
    )
    assert not [k for k in credential_names() if k.startswith("PCSECRET_")]


def test_force_uninstall_removes_the_apps_stored_secrets(home, tmp_path):
    assert app_manager.install(_bundle(tmp_path), confirm=True).ok
    app_config.write_config(APP, {"passcode": PASSCODE, "bot_token": TOKEN}, CONFIG_SCHEMA)

    assert app_manager.force_uninstall(APP) is True

    assert _hits(home, TOKEN) == [] and _hits(home, PASSCODE) == []


def test_uninstall_preview_counts_the_secrets_it_will_remove(home, tmp_path):
    assert app_manager.install(_bundle(tmp_path), confirm=True).ok
    app_config.write_config(APP, {"passcode": PASSCODE, "bot_token": TOKEN}, CONFIG_SCHEMA)

    facts = app_manager.describe_app_data(APP)
    assert facts["secrets"] == 2
    assert TOKEN not in json.dumps(facts) and PASSCODE not in json.dumps(facts)


def test_owned_secrets_are_in_the_store_but_not_in_the_vault(home):
    """The Secrets panel must not offer a provider's or an app's key for deletion: deleting it
    there would leave the owning settings pointing at nothing."""
    from personalclaw.secrets_vault import list_presence

    ProviderSettings.save(APP, {"bot_token": TOKEN})

    owned = [k for k in credential_names() if k.startswith("PCSECRET_")]
    assert owned, "the token is not in the credential store at all"
    assert not [r for r in list_presence() if r.name in owned]


def test_the_vault_hides_exactly_the_prefix_the_store_owns():
    """Pin: the vault's literal and the producer's constant cannot drift apart."""
    from personalclaw.config.credentials import OWNED_KEY_PREFIX
    from personalclaw.secrets_vault import RESERVED_KEY_PREFIXES

    assert OWNED_KEY_PREFIX in RESERVED_KEY_PREFIXES


def test_plaintext_settings_from_before_this_change_move_on_boot(home):
    """App config, a parked keep-data copy, and a multi-instance record — the three shapes an
    upgraded home can hold. The parked copy belongs to an UNINSTALLED app, so its secret is
    dropped rather than moved: uninstall removes an app's secrets, and this one was missed."""
    live = manager.app_dir(APP) / "data" / "config.json"
    live.parent.mkdir(parents=True)
    live.write_text(json.dumps({"bot_token": TOKEN, "command": "pc"}), encoding="utf-8")

    parked = manager.apps_dir() / f".{APP}-old.data" / "config.json"
    parked.parent.mkdir(parents=True)
    parked.write_text(json.dumps({"bot_token": APP_TOKEN, "command": "pc"}), encoding="utf-8")

    record = config_loader.config_dir() / "extensions" / "fixture-multi" / "instances" / "i1.json"
    record.parent.mkdir(parents=True)
    record.write_text(
        json.dumps(
            {
                "id": "i1",
                "extension_name": "fixture-multi",
                "display_name": "Work",
                "config": {"api_key": INSTANCE_KEY},
                "enabled": True,
            }
        ),
        encoding="utf-8",
    )

    from personalclaw.config.secret_refs import migrate_plaintext_secrets

    migrate_plaintext_secrets()

    assert _hits(home, TOKEN) == [".env"]
    assert _hits(home, INSTANCE_KEY) == [".env"]
    assert _hits(home, APP_TOKEN) == [], "the uninstalled app's parked token was kept"
    assert ProviderSettings.load(APP)["bot_token"] == TOKEN
    assert instances.get_instance("fixture-multi", "i1").config["api_key"] == INSTANCE_KEY
    assert json.loads(parked.read_text(encoding="utf-8")) == {"command": "pc"}

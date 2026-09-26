"""Snapshots and exports never carry a credential value.

Measured on the real image: ``personalclaw snapshot`` captured ``config.json`` (with the provider
key typed in Settings) and ``apps/slack-channel/data/config.json`` (with both Slack tokens), and
it captured the credential store itself on purpose — ``backup_entries()`` included every
``secret=True`` entry, ``CORE_FILES["security"]`` named ``credentials.json``. A snapshot is a file
that gets copied to a USB stick or a cloud drive; a plaintext key inside it is a leaked key.

After the fix the settings files carry ``{{secret:…}}`` references and the credential store does
not travel. Every member of the archive is read back and searched, because a manifest that
omits a file is not evidence the bytes are absent.
"""

from __future__ import annotations

import asyncio
import io
import json
import tarfile
import uuid
import zipfile

import pytest
from aiohttp.test_utils import make_mocked_request

from personalclaw.config import loader as config_loader
from personalclaw.config.credentials import save_credential
from personalclaw.dashboard.handlers import providers as H
from personalclaw.llm.branded_specs import BrandedProviderSpec
from personalclaw.llm.credentials import CredentialStore
from personalclaw.llm.registry import get_default_registry
from personalclaw.providers.settings import ProviderSettings
from personalclaw.sdk.provider_helpers import register_branded_app

PROVIDER_KEY = "sk-fixture-snapshot-provider-7c6b5a49"
APP_TOKEN = "xoxb-fixture-snapshot-app-3e2d1c0b"
VAULT_VALUE = "ghp_fixturesnapshotvault0123456789ab"
DESCRIPTOR_VALUE = "sk-fixture-snapshot-descriptor-5b4a3928"
LOCAL_SECRET = "fixture-local-secret-2f1e0d9c8b7a"
APP_PROXY_SECRET = "fixture-app-proxy-secret-1d0c9b8a7f6e"

SECRETS = {
    "provider key typed in Settings": PROVIDER_KEY,
    "app settings token": APP_TOKEN,
    "credential-store value (.env)": VAULT_VALUE,
    "credentials.json inline value": DESCRIPTOR_VALUE,
    "gateway .local_secret": LOCAL_SECRET,
    "per-app .app_secret": APP_PROXY_SECRET,
}

FIXTURE_TYPE = "fixture-snapshot-openai"
APP = "fixture-snapshot-app"


async def _coro(v):
    return v


@pytest.fixture
def home(monkeypatch):
    monkeypatch.setattr("personalclaw.config.credentials._usable_keyring", lambda: None)
    monkeypatch.setattr(H, "_refresh_media_registries", lambda: None)
    register_branded_app(
        BrandedProviderSpec(
            type=FIXTURE_TYPE, protocol="openai", default_base_url="https://x.invalid/v1"
        )
    )
    home = config_loader.config_dir()
    name = f"fx-{uuid.uuid4().hex[:8]}"

    body = {"name": name, "type": FIXTURE_TYPE, "model": "", "options": {"api_key": PROVIDER_KEY}}
    req = make_mocked_request("POST", "/api/model-providers")
    req.json = lambda: _coro(body)
    assert asyncio.run(H.api_provider_create(req)).status == 200

    ProviderSettings.save(APP, {"bot_token": APP_TOKEN, "command": "pc"})
    # `save_credential` mirrors a vault secret into os.environ; recording the name first lets
    # monkeypatch restore the process environment at teardown.
    monkeypatch.delenv("FIXTURE_VAULT_TOKEN", raising=False)
    save_credential("FIXTURE_VAULT_TOKEN", VAULT_VALUE)
    CredentialStore(home).save({"legacy": {"type": "api_key", "value": DESCRIPTOR_VALUE}})
    (home / ".local_secret").write_text(LOCAL_SECRET, encoding="utf-8")
    (home / "apps" / APP / ".app_secret").write_text(APP_PROXY_SECRET, encoding="ascii")
    yield home
    get_default_registry().unregister_entry(name)


def _leaks(members: dict[str, bytes]) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for label, secret in SECRETS.items():
        where = [n for n, data in members.items() if secret.encode() in data]
        if where:
            found[label] = where
    return found


def test_snapshot_contains_no_credential_value(home, tmp_path):
    from personalclaw.snapshot import snapshot_main

    out = tmp_path / "snaps"
    assert snapshot_main([str(out)]) == 0
    [archive] = list(out.glob("personalclaw-snapshot-*.tar.gz"))

    members: dict[str, bytes] = {}
    with tarfile.open(archive, "r:gz") as tar:
        for info in tar.getmembers():
            if info.isfile():
                members[info.name] = tar.extractfile(info).read()

    assert _leaks(members) == {}
    # The settings files still travel — as references a restore can resolve on this machine.
    config_member = next(n for n in members if n.endswith("/config.json") and "/apps/" not in n)
    assert "{{secret:" in members[config_member].decode()


def test_export_contains_no_credential_value(home):
    from personalclaw.portability import create_export_zip

    data, _manifest = create_export_zip()
    members: dict[str, bytes] = {}
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        for info in zf.infolist():
            if not info.is_dir():
                members[info.filename] = zf.read(info)

    assert _leaks(members) == {}
    assert any(n.endswith("/config.json") for n in members), json.dumps(sorted(members))[:500]

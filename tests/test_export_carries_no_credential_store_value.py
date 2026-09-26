"""No value the credential store holds appears in an export or a snapshot.

Settings → Portability says "Credentials are never included." This is the test that holds the
sentence to account, as a PROPERTY rather than a list: every writer that takes a credential is
driven — a provider key, an app's secret setting, a Secrets-panel credential, an MCP server's env
value and header, the webhook token through the CLI — every value the store then holds is
enumerated, and every member of the export and of the snapshot is searched for every one. A writer
added later that puts a value both in the store and in a file an archive carries fails here without
anyone having to remember this file.

Red before the MCP/webhook fix: those three values were never in the store (they stayed in
``mcp.json`` and ``config.json``, which both archives carry), so the first assertion — the store
holds every credential the product was given — fails.
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import tarfile
import uuid
import zipfile

import pytest
from aiohttp.test_utils import make_mocked_request

from personalclaw.config import loader as config_loader
from personalclaw.config.credentials import credential_names, get_credential, save_credential
from personalclaw.config.secret_refs import migrate_plaintext_secrets
from personalclaw.dashboard.handlers import providers as H
from personalclaw.llm.branded_specs import BrandedProviderSpec
from personalclaw.llm.registry import get_default_registry
from personalclaw.providers.settings import ProviderSettings
from personalclaw.sdk.provider_helpers import register_branded_app

PLANTED = {
    "provider key": "sk-fixture-property-provider-81a2b3c4",
    "app secret setting": "xoxb-fixture-property-app-5d6e7f80",
    "Secrets-panel credential": "ghp_fixturePropertyVault0123456789ab",
    "MCP server env value": "ghp_fixturePropertyMcpEnv9876543210cd",
    "MCP server header": "Bearer fixture-property-mcp-header-11aa22bb",
    "webhook token": "whk_fixturePropertyWebhook_33cc44dd55ee",
}

FIXTURE_TYPE = "fixture-property-openai"


async def _coro(value):
    return value


@pytest.fixture
def home(monkeypatch):
    monkeypatch.setattr("personalclaw.config.credentials._usable_keyring", lambda: None)
    monkeypatch.setattr(H, "_refresh_media_registries", lambda: None)
    home = config_loader.config_dir()
    from personalclaw.dashboard.handlers import mcp as mcp_mod

    register_branded_app(
        BrandedProviderSpec(
            type=FIXTURE_TYPE, protocol="openai", default_base_url="https://x.invalid/v1"
        )
    )
    name = f"fx-{uuid.uuid4().hex[:8]}"

    # A provider key, typed in Settings → Providers → Add instance.
    body = {"name": name, "type": FIXTURE_TYPE, "options": {"api_key": PLANTED["provider key"]}}
    req = make_mocked_request("POST", "/api/model-providers")
    req.json = lambda: _coro(body)
    assert asyncio.run(H.api_provider_create(req)).status == 200
    # An app's secret setting, through the SDK the apps use.
    ProviderSettings.save("fixture-property-app", {"bot_token": PLANTED["app secret setting"]})
    # A Secrets-panel credential.
    monkeypatch.delenv("FIXTURE_PROPERTY_VAULT", raising=False)
    save_credential("FIXTURE_PROPERTY_VAULT", PLANTED["Secrets-panel credential"])
    # An MCP server's env value, typed in Tools → Add tool server.
    req = make_mocked_request("PUT", "/api/mcp/servers/prop", match_info={"name": "prop"})
    req.json = lambda: _coro({"command": "npx", "env": {"TOKEN": PLANTED["MCP server env value"]}})
    assert asyncio.run(mcp_mod.api_mcp_server_detail(req)).status == 200
    # A remote MCP server's header, written into mcp.json by an earlier release.
    doc = json.loads((home / "mcp.json").read_text(encoding="utf-8"))
    doc["mcpServers"]["remote"] = {
        "url": "https://mcp.invalid/x",
        "headers": {"Authorization": PLANTED["MCP server header"]},
    }
    (home / "mcp.json").write_text(json.dumps(doc))
    migrate_plaintext_secrets()
    # The webhook token, through the CLI.
    from personalclaw.cli_config import _config_cmd

    _config_cmd(
        argparse.Namespace(
            config_action="set",
            key="hooks",
            value=json.dumps({"webhook_token": PLANTED["webhook token"]}),
            file=None,
        )
    )
    yield home
    get_default_registry().unregister_entry(name)


#: A stored value shorter than this cannot be told apart from ordinary text, so it is not searched
#: for: every MCP `env` value is stored unless marked plain, and a moved `LOG_LEVEL=info` put
#: "info" in the store, which matched 15 unrelated files of a real export. Every credential is
#: longer; everything this file plants is well over it.
_SEARCHABLE = 12


def _store_values() -> dict[str, str]:
    values = {name: get_credential(name) for name in credential_names()}
    return {name: v for name, v in values.items() if len(v) >= _SEARCHABLE}


def _leaks(members: dict[str, bytes], values: dict[str, str]) -> dict[str, list[str]]:
    return {
        name: where
        for name, value in values.items()
        if value and (where := [m for m, data in members.items() if value.encode() in data])
    }


def test_the_store_holds_every_credential_the_product_was_given(home):
    held = set(_store_values().values())
    assert {label: v for label, v in PLANTED.items() if v not in held} == {}


def test_an_export_contains_no_credential_store_value(home):
    from personalclaw.portability import create_export_zip

    values = _store_values()
    assert values
    data, _manifest = create_export_zip()
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        members = {i.filename: zf.read(i) for i in zf.infolist() if not i.is_dir()}
    assert members
    assert _leaks(members, values) == {}


def test_a_snapshot_contains_no_credential_store_value(home, tmp_path):
    from personalclaw.snapshot import snapshot_main

    values = _store_values()
    out = tmp_path / "snaps"
    assert snapshot_main([str(out)]) == 0
    [archive] = list(out.glob("personalclaw-snapshot-*.tar.gz"))
    with tarfile.open(archive, "r:gz") as tar:
        members = {i.name: tar.extractfile(i).read() for i in tar.getmembers() if i.isfile()}
    assert members
    assert _leaks(members, values) == {}

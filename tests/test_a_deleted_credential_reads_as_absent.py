"""A setting whose saved credential was deleted reads as not set, on every settings form.

A secret setting's value lives in the credential store, and the app's settings file holds only a
reference to it. Uninstalling an app while keeping its data deletes the app's credentials, as its
dialog says ("Its saved credential is deleted … Reinstall it and you enter it again"), and keeps
the settings file with its references in it. Reinstall the app and its Bot Token field then said
"saved — leave blank to keep" under a status line reading "No bot token configured": the settings
routes counted every reference as a saved secret, whether or not the store still held it.

Now a reference counts as saved only while the credential store holds its key (by name: no value
is read), so the form shows the field empty and asks for the token again. One rule, in the one
masking policy both settings routes use.
"""

from __future__ import annotations

import json

import pytest
from test_app_api import _app_src
from test_app_api import _client as _apps_client
from test_app_api import _consented_install, _save_config
from test_provider_config_secrets import _client as _providers_client
from test_provider_config_secrets import _config_file, _save

from personalclaw.apps.secret_fields import SECRET_MASK
from personalclaw.config import secret_refs

_SCHEMA = {
    "type": "object",
    "properties": {
        "api_key": {"type": "string", "x-meta": {"label": "API Key", "sensitive": True}},
        "endpoint": {"type": "string"},
    },
}


@pytest.mark.asyncio
async def test_after_a_keep_data_reinstall_the_deleted_key_reads_as_not_set(tmp_path):
    async with _apps_client(tmp_path) as client:
        src = _app_src(tmp_path, "sec", setup={"configSchema": _SCHEMA})
        assert (await _consented_install(client, src)).status == 201
        r = await _save_config(client, "sec", {"api_key": "fake-key-1", "endpoint": "https://x"})
        assert r.status == 200, await r.text()

        removed = await client.delete("/api/apps/sec?remove=1")
        assert removed.status == 200, await removed.text()
        assert (await _consented_install(client, src)).status == 201

        body = await (await client.get("/api/apps/sec/config")).json()
        assert body["_secret_set"] == [], "a deleted key was reported as saved"
        assert body["config"]["api_key"] == "", body["config"]
        assert body["config"]["endpoint"] == "https://x", "the kept settings came back"

        # Entering the key again saves it, and it reads as saved from then on.
        r = await _save_config(client, "sec", {"api_key": "fake-key-2", "endpoint": "https://x"})
        assert r.status == 200, await r.text()
        body = await (await client.get("/api/apps/sec/config")).json()
        assert body["_secret_set"] == ["api_key"]
        assert body["config"]["api_key"] == SECRET_MASK


@pytest.mark.asyncio
async def test_settings_providers_reads_a_deleted_token_as_not_set(tmp_path):
    """The Settings → Providers form, whose Bot Token field showed "saved" after the reinstall."""
    async with _providers_client(tmp_path) as client:
        r = await _save(client, {"bot_token": "fake-bot-token-1", "command": "pclaw"})
        assert r.status == 200, await r.text()
        # What a keep-data uninstall does to the app's credentials, and nothing else.
        assert secret_refs.purge(secret_refs.app_owned_prefixes("fake-channel")) == 1
        stored = json.loads(_config_file(tmp_path).read_text(encoding="utf-8"))
        assert secret_refs.ref_key(stored["bot_token"]), "the settings file keeps its reference"

        body = await (await client.get("/api/providers/fake-channel/config")).json()

        assert body["_secret_set"] == [], "a deleted token was reported as saved"
        assert body["config"]["bot_token"] == ""
        assert body["config"]["command"] == "pclaw"


@pytest.mark.asyncio
async def test_a_token_still_in_the_store_still_reads_as_saved(tmp_path):
    async with _providers_client(tmp_path) as client:
        await _save(client, {"bot_token": "fake-bot-token-1"})

        body = await (await client.get("/api/providers/fake-channel/config")).json()

        assert body["_secret_set"] == ["bot_token"]
        assert body["config"]["bot_token"] == SECRET_MASK

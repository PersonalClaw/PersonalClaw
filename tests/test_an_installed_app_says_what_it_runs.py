"""An installed app's row says what it gets and runs, as its install consent did.

The Store's panel and the install consent show one disclosure of what an app gets and runs:
the permissions the gateway enforces, its scheduled jobs, and "What it runs on this machine"
(its provider modules loaded into the gateway, its lifecycle hooks, its setup and doctor steps).
Once the app was installed, ``GET /api/apps`` carried only the raw permissions and the
prerequisites, so its panel in the Library left out everything it runs.

Now each row carries the same projection consent reads (``apps.disclosure.describe``), of the
installed copy's manifest, and the panel renders it with the consent's own component.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.apps.disclosure import describe
from personalclaw.apps.manifest import AppManifest
from personalclaw.config import loader as config_loader

MANIFEST = {
    "name": "fixture-search",
    "version": "0.1.0",
    "displayName": "Fixture Search",
    "description": "A search provider for tests.",
    "permissions": {"network": True},
    "cli": {"setup": "cli_setup:run", "doctor": "cli_doctor:probe"},
    "setup": {"onEnable": "./on-enable.sh"},
    "provider": {
        "type": "search",
        "implementation": "provider:create_provider",
        "capabilities": ["search"],
    },
}


def _plant(home: Path, manifest: dict) -> None:
    assert home != Path.home() / ".personalclaw", "refusing to plant a test app in the real home"
    root = home / "apps" / manifest["name"]
    root.mkdir(parents=True)
    (root / "installed.json").write_text(
        json.dumps(
            {"name": manifest["name"], "version": "0.1.0", "enabled": True, "origin": "local"}
        )
    )
    (root / "app.json").write_text(json.dumps(manifest))


async def _rows() -> dict[str, dict]:
    from personalclaw.dashboard.handlers import apps as A

    app = web.Application()
    app.router.add_get("/api/apps", A.api_apps_list)
    async with TestClient(TestServer(app)) as client:
        body = await (await client.get("/api/apps")).json()
    return {a["name"]: a for a in body["apps"]}


@pytest.mark.asyncio
async def test_the_row_carries_the_disclosure_consent_showed():
    _plant(config_loader.config_dir(), MANIFEST)

    row = (await _rows())["fixture-search"]

    assert row["disclosure"] == describe(AppManifest.from_dict(MANIFEST))


@pytest.mark.asyncio
async def test_what_it_runs_on_this_machine_is_in_it():
    _plant(config_loader.config_dir(), MANIFEST)

    disclosure = (await _rows())["fixture-search"]["disclosure"]

    assert disclosure["providers"] == [
        {"type": "search", "implementation": "provider:create_provider", "execution": "in-process"}
    ]
    assert disclosure["onEnable"] == "./on-enable.sh"
    assert disclosure["cliSetup"] == "cli_setup:run"
    assert disclosure["runsAsYou"], "the sentence saying what runs as you is missing"

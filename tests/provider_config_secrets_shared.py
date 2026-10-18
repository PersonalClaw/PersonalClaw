"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_provider_config_secrets.py``

Its checks:
``tests/rails/test_provider_config_secrets_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import patch

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

_SCHEMA = {
    "type": "object",
    "properties": {
        "bot_token": {"type": "string", "x-meta": {"label": "Bot Token", "sensitive": True}},
        "app_token": {"type": "string", "x-meta": {"label": "App Token", "sensitive": True}},
        "command": {"type": "string"},
    },
}


class _FakeProviderConfig:
    type = "channel"
    entity = ""
    capabilities: list[str] = []
    multiInstance = False
    settingsSchema = _SCHEMA


class _FakeExt:
    name = "fake-channel"
    enabled = False  # keep PATCH off the registry re-cycle path
    error = ""
    provider_config = _FakeProviderConfig()


class _FakeRegistry:
    def get(self, name):
        return _FakeExt() if name == "fake-channel" else None


@asynccontextmanager
async def _client(tmp_path: Path):
    from personalclaw.apps import manager
    from personalclaw.providers import routes as provider_routes

    with (
        patch("personalclaw.config.loader.config_dir", return_value=tmp_path),
        patch.object(manager, "config_dir", return_value=tmp_path),
        patch.object(provider_routes, "get_provider_registry", lambda: _FakeRegistry()),
    ):
        app = web.Application()
        provider_routes.register_routes(app)
        async with TestClient(TestServer(app)) as client:
            yield client


async def _save(client, values: dict):
    """PATCH the form the way Settings → Providers does: over the revision of the config it read
    — the form sends every field it shows, so a save names the copy it replaces
    (`personalclaw/stale_write.py`)."""
    read = await client.get("/api/providers/fake-channel/config")
    revision = (await read.json())["revision"]
    return await client.patch(
        "/api/providers/fake-channel/config", json=values, headers={"If-Match": f'"{revision}"'}
    )


def _config_file(tmp_path: Path) -> Path:
    return tmp_path / "apps" / "fake-channel" / "data" / "config.json"

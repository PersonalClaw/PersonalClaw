"""An installed app's row names what it needs that PersonalClaw doesn't install.

Install consent and the Store card read an app's ``requires`` (#3704), and the Store card only
for an app that is not installed. ``GET /api/apps``, which the Library's panel for an installed
app reads, carried no such field, so once Local Image Generation was in nothing said it needs a
ComfyUI server. The row now carries the installed copy's whole disclosure, which names them.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.config import loader as config_loader

COMFYUI = {
    "name": "ComfyUI",
    "why": "Every image is made by a ComfyUI server running on this machine.",
    "how": "Start ComfyUI on this machine and set its address on this app's Configure page.",
}


def _plant(home: Path, name: str, **extra) -> None:
    assert home != Path.home() / ".personalclaw", "refusing to plant a test app in the real home"
    root = home / "apps" / name
    root.mkdir(parents=True)
    (root / "installed.json").write_text(
        json.dumps({"name": name, "version": "0.1.0", "enabled": True, "origin": "local"})
    )
    manifest = {"name": name, "version": "0.1.0", "displayName": name, "description": "x."}
    (root / "app.json").write_text(json.dumps({**manifest, **extra}))


@pytest.mark.asyncio
async def test_the_row_carries_each_prerequisite_as_consent_showed_it():
    from personalclaw.dashboard.handlers import apps as A

    home = config_loader.config_dir()
    _plant(home, "needs-comfyui", requires=[COMFYUI])
    _plant(home, "needs-nothing")

    app = web.Application()
    app.router.add_get("/api/apps", A.api_apps_list)
    async with TestClient(TestServer(app)) as client:
        rows = {a["name"]: a for a in (await (await client.get("/api/apps")).json())["apps"]}

    assert rows["needs-comfyui"]["disclosure"]["requires"] == [COMFYUI]
    assert rows["needs-nothing"]["disclosure"]["requires"] == []

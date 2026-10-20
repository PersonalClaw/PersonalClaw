"""``GET /api/apps`` carries each provider app's DECLARED capabilities.

The Store catalog has always sent ``providerCapabilities`` for an app that is not installed
yet, because ``providerType`` alone cannot tell a chat model from a speech one: faster-whisper
(``stt``) and piper-tts (``tts``) are both ``type: model``. The installed-apps list did not,
so a surface sorting INSTALLED apps by what they do had nothing to sort them by.

That is how onboarding's essential-apps step came to offer "Install" for a speech app that
was already installed, and to lose it from its lane after a reload: the step could only
place an app it found in the catalog, and the catalog (correctly) never lists an installed
one. These pin the field on the installed side, so both reads classify by the same
declaration.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from unittest.mock import patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.apps import backend_runtime, manager
from personalclaw.dashboard.handlers.apps import register_app_routes


@asynccontextmanager
async def _client(tmp_path):
    from personalclaw import inbox as _inbox
    from personalclaw.apps import catalog as _catalog
    from personalclaw.providers import entity_routes as _er

    with (
        patch("personalclaw.config.loader.config_dir", return_value=tmp_path),
        patch.object(manager, "config_dir", return_value=tmp_path),
        patch.object(_catalog, "config_dir", return_value=tmp_path),
        patch.object(_er, "config_dir", return_value=tmp_path),
        patch.object(_inbox, "config_dir", return_value=tmp_path),
    ):
        backend_runtime._supervisor = backend_runtime.BackendSupervisor()
        app = web.Application()
        register_app_routes(app)
        async with TestClient(TestServer(app)) as client:
            try:
                yield client
            finally:
                backend_runtime.get_backend_supervisor().stop_all()


def _installed(tmp_path, name: str, manifest: dict) -> None:
    """Put *name* in the Library the way an install leaves it: its manifest plus the
    ``installed.json`` record ``list_apps`` reads. Written directly, so this pins the READ
    and does not depend on the install route's consent flow."""
    d = tmp_path / "apps" / name
    d.mkdir(parents=True)
    (d / "app.json").write_text(json.dumps({"name": name, **manifest}), encoding="utf-8")
    with patch.object(manager, "config_dir", return_value=tmp_path):
        manager._write_installed(
            name, manager.InstalledApp(name=name, version=manifest["version"], origin="local")
        )


_SPEECH = {
    "version": "0.1.0",
    "displayName": "Faster Whisper",
    "description": "Speech-to-text fixture",
    "provider": {
        "type": "model",
        "implementation": "provider:create_provider",
        "capabilities": ["stt"],
    },
}


@pytest.mark.asyncio
async def test_an_installed_provider_app_lists_the_capabilities_it_declares(tmp_path):
    _installed(tmp_path, "faster-whisper", _SPEECH)
    async with _client(tmp_path) as client:
        apps = (await (await client.get("/api/apps")).json())["apps"]
    row = next(a for a in apps if a["name"] == "faster-whisper")
    # `model` is not enough to say what it is for — this is what makes it a SPEECH app.
    assert row["providerType"] == "model"
    assert row["providerCapabilities"] == ["stt"]


@pytest.mark.asyncio
async def test_an_app_that_provides_nothing_lists_no_capabilities(tmp_path):
    # Present and empty, never absent: a reader must not have to guess what a missing key means.
    _installed(
        tmp_path, "notes", {"version": "1.0.0", "displayName": "Notes", "description": "plain"}
    )
    async with _client(tmp_path) as client:
        apps = (await (await client.get("/api/apps")).json())["apps"]
    row = next(a for a in apps if a["name"] == "notes")
    assert row["isProvider"] is False
    assert row["providerCapabilities"] == []

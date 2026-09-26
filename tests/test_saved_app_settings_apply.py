"""A setting saved on the Apps page reaches the app's live provider now, for every app.

``PUT /api/apps/{name}/config`` (Configure → Save) wrote the app's settings file and rebuilt
nothing. A provider instance is built from its settings once, at enable, so every app kept running
on what it was built with until a restart: a channel's saved bot token read "No bot token
configured" on Test and Connect (PersonalClawApps #124 measured it on all four channels), and a
model or search app's saved key reached nothing. ``PATCH /api/providers/{name}/config`` — the same
file — already rebuilt the provider; both routes now share
``providers.routes.apply_saved_settings``.

A rebuilt CHANNEL also takes over the running inbound receiver. Rebuilding one used to leave the
old instance's receiver connected on the old token while outbound moved to the new instance.
"""

from __future__ import annotations

import json
import sys
import textwrap
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

# Imported HERE, before any test patches ``config_dir``: the probe apps import it, and a module
# first imported under a patch keeps the mock bound (``sdk.channel.config_dir``) for every later
# test on the worker.
import personalclaw.sdk.channel  # noqa: F401
from personalclaw import channel_transports
from personalclaw.apps import manager
from personalclaw.apps.manifest import AppManifest
from personalclaw.dashboard.handlers.apps import register_app_routes
from personalclaw.providers import registry as registry_module
from personalclaw.providers import routes as provider_routes

_SCHEMA = {
    "type": "object",
    "properties": {"token": {"type": "string", "default": "", "x-meta": {"sensitive": True}}},
}

# One module, two provider types: a channel transport that records what inbound it runs, and a
# task provider (any non-channel app) that records the settings it was built with.
_PROVIDER = textwrap.dedent("""
    from personalclaw.sdk.channel import ChannelTransportProvider

    BUILT = []

    class Transport(ChannelTransportProvider):
        def __init__(self, config):
            self.config = dict(config or {})
            if self.config.get("token") == "unbuildable":
                raise ValueError("this token builds no transport")
            self.inbound = []
            BUILT.append(self)

        name = property(lambda self: "probe")
        display_name = property(lambda self: "Probe")

        async def connect(self):
            return True

        async def disconnect(self):
            return None

        async def send(self, message):
            return True

        async def start_inbound(self, services):
            self.inbound.append(("start", services))

        async def stop_inbound(self):
            self.inbound.append(("stop", None))

    class TaskProvider:
        def __init__(self, config):
            self.config = dict(config or {})
            self.name = "probe-tasks"
            BUILT.append(self)

    def create_channel(config=None):
        return Transport(config)

    def create_tasks(config=None):
        return TaskProvider(config)
    """)


def _install(tmp_path: Path, name: str, provider_type: str, factory: str) -> AppManifest:
    d = tmp_path / "apps" / name
    d.mkdir(parents=True)
    (d / "provider.py").write_text(_PROVIDER, encoding="utf-8")
    manifest = {
        "name": name,
        "version": "1.0.0",
        "displayName": name,
        "description": name,
        "provider": {
            "type": provider_type,
            "implementation": f"provider:{factory}",
            "settingsSchema": _SCHEMA,
        },
    }
    (d / "app.json").write_text(json.dumps(manifest), encoding="utf-8")
    (d / "installed.json").write_text(
        json.dumps({"name": name, "version": "1.0.0", "enabled": True}), encoding="utf-8"
    )
    return AppManifest.from_dict(manifest)


@asynccontextmanager
async def _gateway(tmp_path: Path):
    """The apps + providers routes over a registry holding the real channel and task handlers."""
    registry = registry_module.ProviderRegistry()
    registry.register_type_handler("channel", registry_module.ChannelTypeHandler())
    registry.register_type_handler("task", registry_module.TaskTypeHandler())
    with (
        patch("personalclaw.config.loader.config_dir", return_value=tmp_path),
        patch.object(manager, "config_dir", return_value=tmp_path),
        patch.object(provider_routes, "get_provider_registry", lambda: registry),
    ):
        app = web.Application()
        register_app_routes(app)
        provider_routes.register_routes(app)
        async with TestClient(TestServer(app)) as client:
            yield client, registry
    for name in list(registry._extensions):
        registry.disable(name)


def _built(name: str) -> list:
    return sys.modules[f"_pclaw_app_{name.replace('-', '_')}__provider"].BUILT


@pytest.mark.asyncio
async def test_saving_a_channel_setting_rebuilds_its_transport_and_moves_its_inbound(tmp_path):
    manifest = _install(tmp_path, "probe-channel", "channel", "create_channel")
    async with _gateway(tmp_path) as (client, registry):
        registry.register(manifest, enabled=True)
        old = channel_transports.get_transport("probe")
        services = object()
        await channel_transports.start_inbound(old, services)  # what the gateway does at boot

        resp = await client.put("/api/apps/probe-channel/config", json={"token": "xoxb-saved"})
        assert resp.status == 200, await resp.text()

        new = channel_transports.get_transport("probe")
        assert new is not old, "the saved setting reached no live transport"
        assert new.config["token"] == "xoxb-saved"
        assert old.inbound[-1] == ("stop", None), "the old receiver was left running"
        assert new.inbound == [("start", services)], "the new transport has no receiver"


@pytest.mark.asyncio
async def test_saving_any_apps_setting_rebuilds_its_provider(tmp_path):
    """Not just channels: a task provider app's saved setting reaches its live instance."""
    manifest = _install(tmp_path, "probe-tasks", "task", "create_tasks")
    async with _gateway(tmp_path) as (client, registry):
        registry.register(manifest, enabled=True)
        before = registry.get("probe-tasks").provider_instance
        resp = await client.put("/api/apps/probe-tasks/config", json={"token": "sk-saved"})
        assert resp.status == 200, await resp.text()
        after = registry.get("probe-tasks").provider_instance
        assert after is not before and after.config["token"] == "sk-saved"


@pytest.mark.asyncio
async def test_a_transport_whose_inbound_never_started_is_not_started_by_a_save(tmp_path):
    """A channel enabled after boot has no receiver; saving its settings does not invent one."""
    manifest = _install(tmp_path, "probe-late", "channel", "create_channel")
    async with _gateway(tmp_path) as (client, registry):
        registry.register(manifest, enabled=True)
        resp = await client.put("/api/apps/probe-late/config", json={"token": "t"})
        assert resp.status == 200
        new = channel_transports.get_transport("probe")
        assert new.config["token"] == "t" and new.inbound == []


@pytest.mark.asyncio
async def test_a_disabled_app_is_saved_but_not_rebuilt(tmp_path):
    manifest = _install(tmp_path, "probe-off", "task", "create_tasks")
    async with _gateway(tmp_path) as (client, registry):
        registry.register(manifest, enabled=False)
        resp = await client.put("/api/apps/probe-off/config", json={"token": "x"})
        assert resp.status == 200
        assert registry.get("probe-off").provider_instance is None


@pytest.mark.asyncio
async def test_the_providers_route_moves_a_channels_inbound_too(tmp_path):
    """PATCH /api/providers/{name}/config rebuilt the transport and orphaned its receiver."""
    manifest = _install(tmp_path, "probe-patch", "channel", "create_channel")
    async with _gateway(tmp_path) as (client, registry):
        registry.register(manifest, enabled=True)
        old = channel_transports.get_transport("probe")
        await channel_transports.start_inbound(old, "svc")
        resp = await client.patch("/api/providers/probe-patch/config", json={"token": "n"})
        assert resp.status == 200, await resp.text()
        new = channel_transports.get_transport("probe")
        assert old.inbound[-1] == ("stop", None) and new.inbound == [("start", "svc")]


@pytest.mark.asyncio
async def test_settings_that_build_no_transport_stop_the_old_receiver(tmp_path):
    """The rebuild fails: the app is unregistered and shows the error, and its old receiver is
    stopped rather than left running on the old token where nothing could reach it again."""
    manifest = _install(tmp_path, "probe-bad", "channel", "create_channel")
    async with _gateway(tmp_path) as (client, registry):
        registry.register(manifest, enabled=True)
        old = channel_transports.get_transport("probe")
        await channel_transports.start_inbound(old, "svc")
        resp = await client.put("/api/apps/probe-bad/config", json={"token": "unbuildable"})
        assert resp.status == 200, await resp.text()
        assert "builds no transport" in registry.get("probe-bad").error
        assert channel_transports.get_transport("probe") is None
        assert old.inbound[-1] == ("stop", None), "the old receiver was orphaned"
        assert channel_transports._inbound == {}

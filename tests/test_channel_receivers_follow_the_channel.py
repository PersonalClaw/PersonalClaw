"""A channel starts and stops receiving the moment it is enabled, changed or removed.

Receivers used to start ONCE, at gateway boot (``GatewayOrchestrator._start_channel_inbound``), and
nothing but the process exiting stopped one. Measured on a dev gateway against a fake Telegram Bot
API (2026-09-26): a channel installed, enabled or configured after boot never received a message
until a restart; one disabled or uninstalled — force-uninstalled, its token purged — kept
long-polling and answering on that token; an update left the old instance's receiver running and
started none for the new one; a token rotated in the Secrets vault kept the receiver on the old one.

The contract now: at any moment exactly one receiver runs per enabled, configured channel, on the
instance registered NOW, and none for a disabled, unconfigured or uninstalled one — one rule,
``channel_transports.reconcile_inbound``, run by every change to the transport registry and by boot.

Each test boots through the gateway's own hook, changes the channel with the request a user's click
sends, and watches a probe channel the way the fake server watched Telegram: which receivers are
attached to the wire, and whether a message sent down it reaches the platform's inbound door.
"""

from __future__ import annotations

import asyncio
import json
import sys
import textwrap
import types
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

# Imported before any test patches `config_dir` (see test_saved_app_settings_apply): the probe app
# imports the SDK, and a module first imported under a patch keeps the mock bound.
import personalclaw.sdk.channel  # noqa: F401
from personalclaw import channel_transports
from personalclaw.apps import app_manager, manager
from personalclaw.channel_delivery import delivery_for
from personalclaw.dashboard.handlers.apps import register_app_routes
from personalclaw.dashboard.handlers.channels import api_channel_test, api_channels_list
from personalclaw.dashboard.handlers.secrets import register_secrets_routes
from personalclaw.providers import registry as registry_module
from personalclaw.providers import routes as provider_routes

TOKEN_ENV = "CHANLIFE_PROBE_TOKEN"

# The probe channel app. Its receiver attaches to a test-owned wire (`_chanlife_wire`) and hands
# every message sent down it to the platform's inbound door — the shape of every shipped channel's
# poll loop. The token is its settings' `token`, else the plain credential TOKEN_ENV, read live the
# way Telegram's app falls back to TELEGRAM_BOT_TOKEN; `health()` says `error` when the receiver
# runs on a token that is no longer the configured one, as Telegram's, Slack's and Discord's do.
_PROVIDER = textwrap.dedent('''
    import asyncio
    import os
    import sys

    from personalclaw.sdk.channel import ChannelTransportProvider

    NAME = {name!r}


    class Delivery:
        """The outbound handle a receiver registers at start, as every shipped channel does."""


    class Transport(ChannelTransportProvider):
        def __init__(self, config):
            self.config = dict(config or {{}})
            self.started_on = None
            self.entry = None

        name = property(lambda self: NAME)
        display_name = property(lambda self: NAME.title())

        def token(self):
            return self.config.get("token") or os.environ.get({env!r}, "")

        async def connect(self):
            return bool(self.token())

        async def disconnect(self):
            return None

        async def send(self, message):
            return True

        async def health(self):
            token = self.token()
            if not token:
                return {{"state": "offline", "detail": "No token configured"}}
            if self.started_on not in (None, token):
                return {{"state": "error", "detail": "Receiving on the token it started with"}}
            return {{"state": "ready", "detail": "Token configured"}}

        async def start_inbound(self, services):
            token = self.token()
            if token == "explode":
                raise RuntimeError("the probe refused to start.")
            if token == "hang":
                await asyncio.Event().wait()
            self.started_on = token
            if not token:
                return
            services.register_channel_delivery(Delivery(), NAME)
            self.entry = sys.modules["_chanlife_wire"].attach(self, token, services)

        async def stop_inbound(self):
            if self.entry is not None:
                sys.modules["_chanlife_wire"].detach(self.entry)
                self.entry = None


    def create_provider(config=None):
        return Transport(config)
    ''')


class _Wire:
    """The fake channel server: who is receiving, and a way to send them a message."""

    def __init__(self) -> None:
        self.live: list[dict[str, Any]] = []
        self.most: dict[str, int] = {}

    def attach(self, transport: Any, token: str, services: Any) -> dict[str, Any]:
        from personalclaw.channel_transports.base import ChannelMessage

        queue: asyncio.Queue[str] = asyncio.Queue()
        entry: dict[str, Any] = {"transport": transport, "name": transport.name, "token": token}

        async def pump() -> None:
            while True:
                text = await queue.get()
                msg = ChannelMessage(
                    channel_id="c1", text=text, sender="u1", metadata={"token": token}
                )
                await services.deliver_channel_inbound(transport.name, msg, is_dm=True)

        entry["queue"] = queue
        entry["task"] = asyncio.ensure_future(pump())
        self.live.append(entry)
        self.most[transport.name] = max(
            self.most.get(transport.name, 0), len(self.on(transport.name))
        )
        return entry

    def detach(self, entry: dict[str, Any]) -> None:
        entry["task"].cancel()
        if entry in self.live:
            self.live.remove(entry)

    def on(self, name: str) -> list[dict[str, Any]]:
        return [e for e in self.live if e["name"] == name]

    def send(self, name: str, text: str) -> None:
        for entry in self.on(name):
            entry["queue"].put_nowait(text)


@pytest.fixture
def wire():
    w = _Wire()
    module = types.ModuleType("_chanlife_wire")
    module.attach, module.detach = w.attach, w.detach  # type: ignore[attr-defined]
    sys.modules["_chanlife_wire"] = module
    yield w
    for entry in list(w.live):
        w.detach(entry)
    sys.modules.pop("_chanlife_wire", None)


@pytest.fixture
def home(tmp_path, monkeypatch):
    """An isolated home and a fresh provider registry — the one app installs register into."""
    import personalclaw.config.loader as loader

    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(manager, "config_dir", lambda: tmp_path)
    monkeypatch.delenv(TOKEN_ENV, raising=False)
    registry_module.reset_provider_registry()
    yield tmp_path
    # Disabled, not just forgotten: a reset registry deregisters nothing, and a transport left in
    # the process-wide transport registry would be the next test's channel at its boot.
    registry = registry_module.get_provider_registry()
    for name in list(registry._extensions):
        registry.disable(name)
    registry_module.reset_provider_registry()


def _source(root: Path, app: str, name: str, *, version: str = "1.0.0") -> Path:
    """A probe channel app's source directory, for the Store to install from."""
    d = root / f"src-{app}-{version}" / app
    d.mkdir(parents=True)
    (d / "provider.py").write_text(_PROVIDER.format(name=name, env=TOKEN_ENV), encoding="utf-8")
    manifest = {
        "name": app,
        "version": version,
        "displayName": name.title(),
        "description": "A probe channel whose receiver the test can watch.",
        "provider": {
            "type": "channel",
            "implementation": "provider:create_provider",
            "settingsSchema": {
                "type": "object",
                "properties": {"token": {"type": "string", "default": ""}},
            },
        },
    }
    (d / "app.json").write_text(json.dumps(manifest), encoding="utf-8")
    return d


def _installed(root: Path, app: str, name: str, *, token: str = "", enabled: bool = True) -> None:
    """Install before the gateway boots — the state boot's discovery registers."""
    from personalclaw.providers.settings import ProviderSettings

    result = app_manager.install(_source(root, app, name), confirm=True)
    assert result.ok, result.error
    if token:
        ProviderSettings.update(app, {"token": token})
        registry_module.get_provider_registry().rebuild(app)
    if not enabled:
        assert app_manager.disable(app)


class _Gateway:
    def __init__(self, client: TestClient, orch: Any, wire: _Wire) -> None:
        self.client, self.orch, self.wire = client, orch, wire
        self.inbound: list[tuple[str, str, str]] = []

    async def boot(self, *receiving: str) -> None:
        """The gateway's boot hook — then wait for the channels named to be receiving.

        Boot does not wait for the receivers it starts (one slow channel must not hold it up), so
        a test that sends a message straight after it waits for the receiver the way a user would.
        """
        await asyncio.wait_for(self.orch._start_channel_inbound(), timeout=5)
        for name in receiving:
            assert await _eventually(
                lambda n=name: _running_on_the_registered_instance(self.wire, n)
            ), f"{name} is not receiving after boot"

    async def call(self, method: str, path: str, body: Any = None) -> Any:
        resp = await self.client.request(method, path, json=body)
        assert resp.status < 300, f"{method} {path} → {resp.status}: {await resp.text()}"
        return await resp.json()

    async def install(self, source: Path) -> None:
        review = await self.call("POST", "/api/apps/preview", {"source": str(source)})
        await self.call("POST", "/api/apps", {"source": str(source), "consent": review["consent"]})

    async def update(self, app: str, source: Path) -> None:
        review = await self.call("POST", "/api/apps/preview", {"source": str(source), "name": app})
        await self.call(
            "POST", f"/api/apps/{app}/update", {"source": str(source), "consent": review["consent"]}
        )

    async def health(self, name: str) -> dict[str, Any]:
        listing = await self.call("GET", "/api/channels")
        return next(c["health"] for c in listing["channels"] if c["name"] == name)


@asynccontextmanager
async def _gateway(wire: _Wire):
    """The routes a user's clicks send, over the gateway's own inbound door (recorded here)."""
    from personalclaw.config.loader import AppConfig
    from personalclaw.gateway import GatewayOrchestrator

    cfg = AppConfig()
    with patch.object(cfg, "load_credentials", return_value={}):
        orch = GatewayOrchestrator(cfg)
    app = web.Application()
    register_app_routes(app)
    provider_routes.register_routes(app)
    register_secrets_routes(app)
    app.router.add_get("/api/channels", api_channels_list)
    app.router.add_post("/api/channels/{name}/test", api_channel_test)
    async with TestClient(TestServer(app)) as client:
        gw = _Gateway(client, orch, wire)

        async def door(provider: str, msg: Any, *, is_dm: bool = True) -> None:
            gw.inbound.append((provider, msg.text, msg.metadata.get("token", "")))

        orch.deliver_channel_inbound = door
        yield gw
        await channel_transports.unbind_inbound()  # what the gateway's shutdown does


async def _eventually(condition, timeout: float = 3.0) -> bool:
    """Whether ``condition()`` holds within ``timeout`` — the loop keeps running meanwhile."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not condition():
        if loop.time() > deadline:
            return False
        await asyncio.sleep(0.01)
    return True


async def _receives(gw: _Gateway, wire: _Wire, name: str, token: str) -> bool:
    """Send one message down the wire: does exactly one copy reach the door, on ``token``?"""
    before = len(gw.inbound)
    wire.send(name, f"hello {token}")
    if not await _eventually(lambda: len(gw.inbound) > before, timeout=2.0):
        return False
    await asyncio.sleep(0.05)  # a second receiver would deliver a second copy by now
    return gw.inbound[before:] == [(name, f"hello {token}", token)]


def _running_on_the_registered_instance(wire: _Wire, name: str) -> bool:
    receivers = wire.on(name)
    return len(receivers) == 1 and receivers[0]["transport"] is channel_transports.get_transport(
        name
    )


# ── starting: a channel that becomes enabled + configured after boot ──────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("surface", ["apps", "providers"])
async def test_a_channel_enabled_after_boot_receives_a_message(home, wire, surface):
    _installed(home, "probe-channel", "probe", token="tok-a", enabled=False)
    async with _gateway(wire) as gw:
        await gw.boot()
        assert wire.on("probe") == []
        await gw.call("POST", f"/api/{surface}/probe-channel/enable")
        assert await _eventually(
            lambda: _running_on_the_registered_instance(wire, "probe")
        ), "the channel enabled after boot has no receiver"
        assert await _receives(gw, wire, "probe", "tok-a")
        assert (await gw.health("probe"))["state"] == "ready"


@pytest.mark.asyncio
async def test_a_channel_installed_after_boot_receives_once_it_is_configured(home, wire):
    async with _gateway(wire) as gw:
        await gw.boot()
        await gw.install(_source(home, "probe-channel", "probe"))
        await asyncio.sleep(0.1)
        assert wire.on("probe") == [], "a channel with no token must not receive"
        assert (await gw.health("probe"))["state"] == "offline"

        await gw.call("PUT", "/api/apps/probe-channel/config", {"token": "tok-a"})
        assert await _eventually(
            lambda: _running_on_the_registered_instance(wire, "probe")
        ), "saving the token of a channel installed after boot started no receiver"
        assert await _receives(gw, wire, "probe", "tok-a")


@pytest.mark.asyncio
async def test_a_token_saved_after_an_enable_after_boot_moves_the_receiver(home, wire):
    """Rotation through Configure on a channel whose receiver core started after boot."""
    _installed(home, "probe-channel", "probe", token="tok-a", enabled=False)
    async with _gateway(wire) as gw:
        await gw.boot()
        await gw.call("POST", "/api/apps/probe-channel/enable")
        await gw.call("PATCH", "/api/providers/probe-channel/config", {"token": "tok-b"})
        assert await _eventually(
            lambda: [e["token"] for e in wire.on("probe")] == ["tok-b"]
        ), f"receivers after the save: {[e['token'] for e in wire.on('probe')]}"
        assert await _receives(gw, wire, "probe", "tok-b")
        assert wire.most["probe"] == 1, "two receivers ran at once"


# ── stopping: disabled, uninstalled ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("surface", ["apps", "providers"])
async def test_disabling_a_channel_stops_its_receiver(home, wire, surface):
    _installed(home, "probe-channel", "probe", token="tok-a")
    async with _gateway(wire) as gw:
        await gw.boot("probe")
        assert await _receives(gw, wire, "probe", "tok-a")
        assert delivery_for("probe") is not None

        await gw.call("POST", f"/api/{surface}/probe-channel/disable")
        assert await _eventually(
            lambda: wire.on("probe") == []
        ), "the disabled channel's receiver is still attached"
        wire.send("probe", "after the disable")
        await asyncio.sleep(0.1)
        assert [text for _p, text, _t in gw.inbound] == [
            "hello tok-a"
        ], "a message reached the platform through a disabled channel"
        assert delivery_for("probe") is None, "the disabled channel still takes outbound delivery"


@pytest.mark.asyncio
@pytest.mark.parametrize("rung", ["", "?remove=1", "?force=1"])
async def test_uninstalling_a_channel_leaves_nothing_running(home, wire, rung):
    _installed(home, "probe-channel", "probe", token="tok-a")
    async with _gateway(wire) as gw:
        await gw.boot("probe")
        assert await _receives(gw, wire, "probe", "tok-a")

        await gw.call("DELETE", f"/api/apps/probe-channel{rung}")
        assert await _eventually(
            lambda: wire.on("probe") == []
        ), f"DELETE /api/apps/probe-channel{rung} left its receiver running"
        assert delivery_for("probe") is None
        assert channel_transports._receivers == {}


# ── replacing: an update ────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_updating_a_channel_replaces_its_receiver_once(home, wire):
    _installed(home, "probe-channel", "probe", token="tok-a")
    async with _gateway(wire) as gw:
        await gw.boot("probe")
        before = channel_transports.get_transport("probe")
        assert _running_on_the_registered_instance(wire, "probe")

        await gw.update("probe-channel", _source(home, "probe-channel", "probe", version="1.1.0"))
        assert await _eventually(
            lambda: _running_on_the_registered_instance(wire, "probe")
            and channel_transports.get_transport("probe") is not before
        ), "the updated channel's receiver is not the one running"
        assert wire.most["probe"] == 1, "the old and the new receiver ran at once"
        assert await _receives(gw, wire, "probe", "tok-a"), "not exactly one copy was handled"


# ── credentials rotated in the Secrets vault ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_vault_credential_starts_moves_and_stops_the_receiver(home, wire):
    _installed(home, "probe-channel", "probe")  # no token in its settings: it reads TOKEN_ENV
    async with _gateway(wire) as gw:
        await gw.boot()
        assert wire.on("probe") == []

        await gw.call("POST", "/api/secrets", {"name": TOKEN_ENV, "value": "tok-v1"})
        assert await _eventually(
            lambda: [e["token"] for e in wire.on("probe")] == ["tok-v1"]
        ), "a token stored in the vault after boot started no receiver"
        await gw.call("POST", "/api/secrets", {"name": TOKEN_ENV, "value": "tok-v2"})
        assert await _eventually(
            lambda: [e["token"] for e in wire.on("probe")] == ["tok-v2"]
        ), f"after rotating, receivers are on {[e['token'] for e in wire.on('probe')]}"
        assert await _receives(gw, wire, "probe", "tok-v2")
        assert (await gw.health("probe"))["state"] == "ready"

        await gw.call("DELETE", f"/api/secrets?name={TOKEN_ENV}")
        assert await _eventually(
            lambda: wire.on("probe") == []
        ), "the receiver kept running on a token deleted from the vault"
        assert wire.most["probe"] == 1


@pytest.mark.asyncio
async def test_a_credential_change_leaves_a_channel_it_did_not_touch_running(home, wire):
    _installed(home, "probe-channel", "probe", token="tok-a")
    async with _gateway(wire) as gw:
        await gw.boot("probe")
        running = wire.on("probe")
        await gw.call("POST", "/api/secrets", {"name": "SOMETHING_ELSE", "value": "x"})
        await asyncio.sleep(0.1)
        assert wire.on("probe") == running, "an unrelated secret restarted the channel"


# ── a receiver that cannot start ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_receiver_that_fails_to_start_says_why_and_the_others_still_run(home, wire):
    _installed(home, "probe-channel", "probe", token="explode")
    _installed(home, "other-channel", "other", token="tok-o")
    async with _gateway(wire) as gw:
        await gw.boot()
        assert await _eventually(lambda: len(wire.on("other")) == 1)
        assert await _receives(gw, wire, "other", "tok-o")

        health = await gw.health("probe")
        assert health["state"] == "error", health
        assert health["detail"] == (
            "Probe is not receiving messages — its receiver did not start: RuntimeError: the probe "
            "refused to start. Fix its settings, or turn it off and on, to try again."
        )
        # The Test button agrees with the status beside it, not with the transport's own probe.
        probe = await gw.call("POST", "/api/channels/probe/test")
        assert probe["ok"] is False and health["detail"] in probe["detail"], probe

        await gw.call("PUT", "/api/apps/probe-channel/config", {"token": "tok-p"})
        assert await _eventually(
            lambda: _running_on_the_registered_instance(wire, "probe")
        ), "fixing the settings did not start the receiver"
        assert (await gw.health("probe"))["state"] == "ready"


@pytest.mark.asyncio
async def test_a_receiver_that_never_finishes_starting_holds_up_no_other(home, wire):
    """Boot used to await each channel's start in turn, so one that hung held up every other."""
    _installed(home, "probe-channel", "probe", token="hang")
    _installed(home, "other-channel", "other", token="tok-o")
    async with _gateway(wire) as gw:
        await gw.boot()  # bounded: it must return while one channel's start is still stuck
        assert await _eventually(lambda: len(wire.on("other")) == 1)
        assert await _receives(gw, wire, "other", "tok-o")
        assert await gw.health("probe") == {
            "state": "starting",
            "detail": "Probe is starting to receive messages.",
        }
        # Turned off mid-start: the start is abandoned, and nothing of it is left behind.
        await gw.call("POST", "/api/apps/probe-channel/disable")
        assert await _eventually(lambda: "probe" not in channel_transports._receivers)
        assert wire.on("probe") == [] and len(wire.on("other")) == 1


@pytest.mark.asyncio
async def test_a_start_that_outlives_its_bound_says_so(home, wire, monkeypatch):
    monkeypatch.setattr(channel_transports, "START_TIMEOUT_SECS", 0.5)
    _installed(home, "probe-channel", "probe", token="hang")
    async with _gateway(wire) as gw:
        await gw.boot()
        assert (await gw.health("probe"))["state"] == "starting"
        await asyncio.sleep(0.7)  # past the bound
        assert await gw.health("probe") == {
            "state": "error",
            "detail": "Probe is not receiving messages — its receiver did not start: it did not "
            "finish starting within 0.5 seconds. Fix its settings, or turn it off and on, to try "
            "again.",
        }

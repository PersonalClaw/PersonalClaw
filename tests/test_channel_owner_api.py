"""The Configure page's owner routes, and the owner in each channel's status.

``GET /api/channels/{name}/owner`` says who core reaches the owner as on that channel and whether a
pairing is running; ``POST …/owner/pairing`` mints the code the page shows once; ``DELETE`` cancels
it. ``/api/channels`` carries the owner beside the health, so a channel's status says whether core
can reach its owner at all. Pairing is offered only by a channel that declares
``ChannelCapabilities.owner_pairing`` — one whose DMs cross the guarded door, where the code is
redeemed, and which reads its owner when it needs it.
"""

from __future__ import annotations

import os
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw import channel_transports
from personalclaw import channel_trust as ct
from personalclaw.apps import permissions
from personalclaw.channel_transports.base import ChannelTransportProvider
from personalclaw.config.credentials import owner_id_credential, save_credential
from personalclaw.config.loader import CRED_OWNER_ID

# What a channel app declares — the published name.
from personalclaw.sdk.channel import ChannelCapabilities


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    import personalclaw.config.loader as cfg
    import personalclaw.providers.entity_routes as er

    monkeypatch.setattr(cfg, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(
        er, "_entity_settings_path", lambda entity: tmp_path / "entity_settings" / f"{entity}.json"
    )
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    keys = (CRED_OWNER_ID, *(owner_id_credential(p) for p in ("fakechat", "plain")))
    for key in keys:
        monkeypatch.delenv(key, raising=False)
    yield tmp_path
    for key in keys:
        os.environ.pop(key, None)
    for name in ("fakechat", "plain"):
        channel_transports.unregister_transport(name)


class _Channel(ChannelTransportProvider):
    def __init__(self, name: str, display: str, *, pairing: bool) -> None:
        self._name, self._display, self._pairing = name, display, pairing

    name = property(lambda self: self._name)
    display_name = property(lambda self: self._display)

    def capabilities(self) -> ChannelCapabilities:
        return ChannelCapabilities(inbound=True, owner_pairing=self._pairing)

    async def connect(self) -> bool:
        return True

    async def disconnect(self) -> None:
        return None

    async def send(self, message: Any) -> bool:
        return True

    async def health(self) -> dict[str, Any]:
        return {"state": "ready", "detail": "fake"}


def _app() -> web.Application:
    from personalclaw.dashboard.handlers.channel_owner import (
        api_channel_owner,
        api_channel_owner_pairing_cancel,
        api_channel_owner_pairing_start,
    )
    from personalclaw.dashboard.handlers.channels import api_channel_get, api_channels_list

    app = web.Application()
    app.router.add_get("/api/channels", api_channels_list)
    app.router.add_get("/api/channels/{name}", api_channel_get)
    app.router.add_get("/api/channels/{name}/owner", api_channel_owner)
    app.router.add_post("/api/channels/{name}/owner/pairing", api_channel_owner_pairing_start)
    app.router.add_delete("/api/channels/{name}/owner/pairing", api_channel_owner_pairing_cancel)
    return app


@pytest.fixture
def channels():
    channel_transports.register_transport(_Channel("fakechat", "FakeChat", pairing=True))
    channel_transports.register_transport(_Channel("plain", "Plain", pairing=False))


@pytest.mark.asyncio
async def test_pairing_from_the_page_makes_the_sender_the_owner(channels):
    async with TestClient(TestServer(_app())) as c:
        before = await (await c.get("/api/channels/fakechat/owner")).json()
        assert before["owner_id"] == "" and before["pairing_supported"] is True
        assert before["pairing"]["active"] is False

        started = await c.post("/api/channels/fakechat/owner/pairing")
        assert started.status == 200
        body = await started.json()
        code = body["code"]
        assert len(code) == ct.PAIRING_CODE_DIGITS and body["expires_at"]

        during = await (await c.get("/api/channels/fakechat/owner")).json()
        assert during["pairing"]["active"] is True
        assert code not in str(during), "the code is shown once, by the POST, and never read back"

        # The owner sends it to the bot in a DM; the channel's inbound crosses the gate.
        verdict = ct.guard_inbound(None, "fakechat", "4242", is_dm=True, text=code)
        assert verdict.reason == "owner_paired"

        after = await (await c.get("/api/channels/fakechat/owner")).json()
        assert after["owner_id"] == "4242" and after["source"] == "channel"
        assert after["pairing"]["active"] is False and after["pairing"]["ended"] == "paired"


@pytest.mark.asyncio
async def test_the_channel_status_says_whether_an_owner_is_known(channels):
    async with TestClient(TestServer(_app())) as c:
        listed = {e["name"]: e for e in (await (await c.get("/api/channels")).json())["channels"]}
        assert listed["fakechat"]["owner"] == {"id": "", "source": ""}
        assert listed["fakechat"]["capabilities"]["owner_pairing"] is True
        assert listed["plain"]["capabilities"]["owner_pairing"] is False

        save_credential(owner_id_credential("fakechat"), "4242")
        save_credential(CRED_OWNER_ID, "U0SHARED")
        listed = {e["name"]: e for e in (await (await c.get("/api/channels")).json())["channels"]}
        assert listed["fakechat"]["owner"] == {"id": "4242", "source": "channel"}
        # A channel with no key of its own is reached with the shared one — and the status says
        # so, because that id may belong to another platform.
        assert listed["plain"]["owner"] == {"id": "U0SHARED", "source": "shared"}
        one = await (await c.get("/api/channels/plain")).json()
        assert one["owner"] == {"id": "U0SHARED", "source": "shared"}


@pytest.mark.asyncio
async def test_a_channel_that_cannot_pair_refuses_a_code(channels):
    async with TestClient(TestServer(_app())) as c:
        resp = await c.post("/api/channels/plain/owner/pairing")
        assert resp.status == 409
        assert (await resp.json())["error"]["code"] == "channel_pairing_unsupported"
        assert ct.owner_pairing_status("plain")["active"] is False


@pytest.mark.asyncio
async def test_an_unknown_channel_is_a_404(channels):
    async with TestClient(TestServer(_app())) as c:
        assert (await c.get("/api/channels/nope/owner")).status == 404
        assert (await c.post("/api/channels/nope/owner/pairing")).status == 404


@pytest.mark.asyncio
async def test_cancel_ends_the_pairing(channels):
    async with TestClient(TestServer(_app())) as c:
        code = (await (await c.post("/api/channels/fakechat/owner/pairing")).json())["code"]
        assert (await c.delete("/api/channels/fakechat/owner/pairing")).status == 200
        status = (await (await c.get("/api/channels/fakechat/owner")).json())["pairing"]
        assert status["active"] is False and status["ended"] == "cancelled"
        verdict = ct.guard_inbound(None, "fakechat", "4242", is_dm=True, text=code)
        assert verdict.reason != "owner_paired"


def test_an_app_cannot_mint_or_cancel_an_owner_code():
    """The code makes its sender the owner — whom core sends approvals to. Minting one is the
    owner's act, never an app's."""
    for route in (
        "POST /api/channels/{name}/owner/pairing",
        "DELETE /api/channels/{name}/owner/pairing",
    ):
        assert isinstance(permissions.ROUTE_AUTHZ.get(route), permissions.OwnerOnly), route

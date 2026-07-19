"""Normalized channel contract (#40) — ChannelMessage + ChannelCapabilities + ABC."""

from __future__ import annotations

import asyncio

import pytest

from personalclaw.channel_transports.base import (
    ChannelCapabilities,
    ChannelMessage,
    ChannelTransportProvider,
    OutboundMessage,
)
from personalclaw.channel_transports.webui import WebUITransport

# NOTE: SlackTransport moved to the standalone slack-channel app (apps/slack-channel/);
# its capability test lives there as apps/slack-channel/test_provider.py. This module
# covers the generic channel contract + the core-built-in WebUI transport only.


def test_channel_message_shape():
    m = ChannelMessage(channel_id="C1", text="hi", sender="u", thread_id="t", message_id="m", ts=1.0)
    assert m.channel_id == "C1" and m.attachments == [] and m.metadata == {}


def test_capabilities_defaults_conservative():
    c = ChannelCapabilities()
    assert c.inbound is False and c.threads is False and c.max_text_len == 0


def test_capabilities_to_dict():
    d = ChannelCapabilities(inbound=True, threads=True, max_text_len=100).to_dict()
    assert d["inbound"] is True and d["threads"] is True and d["max_text_len"] == 100


# ── ABC defaults ──


class _BareTransport(ChannelTransportProvider):
    name = "bare"  # type: ignore[assignment]
    display_name = "Bare"  # type: ignore[assignment]

    async def connect(self): return True
    async def disconnect(self): return None
    async def send(self, message: OutboundMessage): return True


def test_default_capabilities_text_out_only():
    t = _BareTransport()
    assert t.capabilities() == ChannelCapabilities()


def test_default_receive_raises():
    t = _BareTransport()
    with pytest.raises(NotImplementedError):
        asyncio.run(anext(t.receive()))


def test_info_includes_capabilities():
    t = _BareTransport()
    assert "capabilities" in t.info()


# ── concrete adapters declare real capabilities ──


def test_webui_capabilities():
    c = WebUITransport().capabilities()
    assert c.inbound and c.rich_text and c.attachments
    assert c.reactions is False  # web UI has no emoji reactions


def test_adapters_expose_caps_in_info():
    info = WebUITransport().info()
    assert info["capabilities"]["inbound"] is True

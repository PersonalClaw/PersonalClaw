"""Reference echo channel transport (#41) — proves the ADDING_A_CHANNEL example works."""

from __future__ import annotations

import asyncio

from personalclaw.channel_transports.base import ChannelMessage, OutboundMessage
from personalclaw.channel_transports.reference_echo import ReferenceEchoTransport


def _run(coro):
    return asyncio.run(coro)


def test_identity_and_capabilities():
    t = ReferenceEchoTransport()
    assert t.name == "reference-echo"
    c = t.capabilities()
    assert c.inbound and c.threads and c.rich_text and c.max_text_len == 4000


def test_send_requires_connect():
    t = ReferenceEchoTransport()
    assert _run(t.send(OutboundMessage(channel_id="c", text="hi"))) is False  # not connected


def test_send_records_and_echoes_inbound():
    async def go():
        t = ReferenceEchoTransport()
        await t.connect()
        ok = await t.send(OutboundMessage(channel_id="c", text="hello"))
        assert ok and t.sent[0].text == "hello"
        # the echo surfaces back via receive()
        msg = await anext(t.receive())
        await t.disconnect()
        return msg
    msg = _run(go())
    assert isinstance(msg, ChannelMessage)
    assert msg.text == "echo: hello" and msg.sender == "reference-echo"


def test_simulate_inbound_yields_normalized():
    async def go():
        t = ReferenceEchoTransport()
        await t.connect()
        await t._simulate_inbound("a real user message")
        msg = await anext(t.receive())
        await t.disconnect()
        return msg
    msg = _run(go())
    assert msg.text == "a real user message" and msg.sender == "user"


def test_info_exposes_capabilities():
    info = ReferenceEchoTransport().info()
    assert info["name"] == "reference-echo"
    assert info["capabilities"]["inbound"] is True

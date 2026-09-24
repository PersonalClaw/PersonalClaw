"""Unit tests for chat_channel.py — channel link, handoff, channel listing."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from chat_test_helpers import _make_state


def _make_channel_app(state):
    from personalclaw.dashboard.chat_channel import (
        api_channel_reply_targets,
        api_chat_session_channel_link,
        api_chat_session_handoff,
    )

    app = web.Application()
    app["state"] = state
    app.router.add_post("/api/chat/sessions/{session}/channel-link", api_chat_session_channel_link)
    app.router.add_get("/api/channels/reply-targets", api_channel_reply_targets)
    app.router.add_post("/api/chat/sessions/{session}/handoff", api_chat_session_handoff)
    return app


class TestChannelLink:
    @pytest.mark.asyncio
    async def test_session_not_found(self, tmp_path, monkeypatch):
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        async with TestClient(TestServer(_make_channel_app(state))) as client:
            resp = await client.post("/api/chat/sessions/nope/channel-link")
            assert resp.status == 404

    @pytest.mark.asyncio
    async def test_no_channel_delivery(self, tmp_path, monkeypatch):
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        state.get_or_create_session("s1")
        state.channel_delivery = None
        async with TestClient(TestServer(_make_channel_app(state))) as client:
            resp = await client.post("/api/chat/sessions/s1/channel-link")
            assert resp.status == 503

    @pytest.mark.asyncio
    async def test_link_success(self, tmp_path, monkeypatch):
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        session = state.get_or_create_session("s1")
        session.append("user", "hello")
        session.drain()
        state.channel_delivery = MagicMock()
        state.channel_delivery.open_dm = AsyncMock(return_value="C123")
        state.channel_delivery.deliver_text = AsyncMock(return_value="ts123")
        state.owner_id = "U123"
        state.sessions.get_channel_link = MagicMock(return_value=(None, None))
        state.sessions.set_channel_link = MagicMock()
        state.push_sessions_update = MagicMock()
        async with TestClient(TestServer(_make_channel_app(state))) as client:
            resp = await client.post("/api/chat/sessions/s1/channel-link", json={})
            assert resp.status == 200
            data = await resp.json()
            assert data["ok"] is True
            assert data["thread_ts"] == "ts123"


class TestChannelReplyTargets:
    @pytest.mark.asyncio
    async def test_list_channels_from_channel_delivery(self, tmp_path, monkeypatch):
        """Channel list comes from the active channel app via ChannelDelivery —
        core holds no channel config."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        state.channel_delivery = MagicMock()
        state.channel_delivery.list_reply_channels = MagicMock(
            return_value=[
                {"id": "dm", "name": "Direct Message"},
                {"id": "C1", "name": "general"},
            ]
        )
        async with TestClient(TestServer(_make_channel_app(state))) as client:
            resp = await client.get("/api/channels/reply-targets")
            assert resp.status == 200
            data = await resp.json()
            assert data[0]["id"] == "dm"
            assert any(c["id"] == "C1" for c in data)

    @pytest.mark.asyncio
    async def test_list_channels_no_delivery_falls_back_to_dm(self, tmp_path, monkeypatch):
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        state.channel_delivery = None
        async with TestClient(TestServer(_make_channel_app(state))) as client:
            resp = await client.get("/api/channels/reply-targets")
            assert resp.status == 200
            assert await resp.json() == [{"id": "dm", "name": "Direct Message"}]


class TestHandoff:
    @pytest.mark.asyncio
    async def test_handoff_no_channel(self, tmp_path, monkeypatch):
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        state.get_or_create_session("s1")
        state.channel_delivery = None
        async with TestClient(TestServer(_make_channel_app(state))) as client:
            resp = await client.post("/api/chat/sessions/s1/handoff")
            assert resp.status == 503

"""Unit tests for chat_voice.py — streaming Piper synthesis endpoint.

The voice + speed resolve from the unified store via
``tts.registry.active_voice_params``; this module only streams synthesis.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from chat_test_helpers import _make_state


def _make_voice_app(state):
    from personalclaw.dashboard.chat_voice import api_voice_synthesize

    app = web.Application()
    app["state"] = state
    app.router.add_post("/api/voice/synthesize", api_voice_synthesize)
    return app


class TestVoiceSynthesize:
    @pytest.mark.asyncio
    async def test_synthesize_empty_text_rejected(self, tmp_path, monkeypatch):
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        async with TestClient(TestServer(_make_voice_app(state))) as client:
            resp = await client.post("/api/voice/synthesize", json={"text": "", "session": "s1"})
            assert resp.status == 400

    @pytest.mark.asyncio
    async def test_synthesize_no_voice_selected(self, tmp_path, monkeypatch):
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        monkeypatch.setattr("personalclaw.dashboard.chat_voice.active_voice_params", lambda: None)
        state = _make_state(tmp_path)
        async with TestClient(TestServer(_make_voice_app(state))) as client:
            resp = await client.post(
                "/api/voice/synthesize", json={"text": "Hello", "session": "s1"},
            )
            assert resp.status == 503

    @pytest.mark.asyncio
    async def test_synthesize_success(self, tmp_path, monkeypatch):
        from unittest.mock import MagicMock as _MM

        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        monkeypatch.setattr(
            "personalclaw.dashboard.chat_voice.active_voice_params",
            lambda: {
                "provider": _MM(), "voice": "en_US-lessac-medium", "speed": 1.0,
                "speech_voice": "", "enabled": True, "auto_speak": False,
            },
        )

        async def mock_stream(*a, **kw):
            yield 0, "Hello", b"\x00\x01\x02"

        monkeypatch.setattr("personalclaw.dashboard.chat_voice.streaming_voice_reply", mock_stream)
        monkeypatch.setattr("personalclaw.dashboard.chat_voice.stitch_wavs", AsyncMock(return_value=None))

        state = _make_state(tmp_path)
        state.broadcast_ws = MagicMock()
        async with TestClient(TestServer(_make_voice_app(state))) as client:
            resp = await client.post(
                "/api/voice/synthesize",
                json={"text": "Hello world", "session": "s1"},
            )
            assert resp.status == 200
            data = await resp.json()
            assert data["ok"] is True
            assert data["chunks"] == 1
        state.broadcast_ws.assert_called()

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
        # MI-1: the resolver takes surface/profile_id keywords now (§3.2).
        monkeypatch.setattr(
            "personalclaw.dashboard.chat_voice.active_voice_params", lambda **_kw: None
        )
        state = _make_state(tmp_path)
        async with TestClient(TestServer(_make_voice_app(state))) as client:
            resp = await client.post(
                "/api/voice/synthesize",
                json={"text": "Hello", "session": "s1"},
            )
            assert resp.status == 503

    @pytest.mark.asyncio
    async def test_synthesize_success(self, tmp_path, monkeypatch):
        from unittest.mock import MagicMock as _MM

        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        monkeypatch.setattr(
            "personalclaw.dashboard.chat_voice.active_voice_params",
            lambda **_kw: {
                "provider": _MM(),
                "voice": "en_US-lessac-medium",
                "speed": 1.0,
                "speech_voice": "",
                "enabled": True,
                "auto_speak": False,
            },
        )

        async def mock_stream(*a, **kw):
            yield 0, "Hello", b"\x00\x01\x02"

        monkeypatch.setattr("personalclaw.dashboard.chat_voice.streaming_voice_reply", mock_stream)
        monkeypatch.setattr(
            "personalclaw.dashboard.chat_voice.stitch_wavs", AsyncMock(return_value=None)
        )

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


class TestTheEnabledToggleIsHonored:
    """`active_voice_params` has always published `enabled` and nothing read it (#651).

    The shape is worth naming: the registry LOADS the key into a dict, so a grep for
    `enabled` finds a "reader" that is really a pass-through, and the toggle looks wired. It
    persisted, it round-tripped, and Speak synthesized whether it was on or off.
    """

    @pytest.mark.asyncio
    async def test_synthesis_is_refused_when_the_toggle_is_off(self, tmp_path, monkeypatch):
        provider = MagicMock()
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        monkeypatch.setattr(
            "personalclaw.dashboard.chat_voice.active_voice_params",
            lambda **_kw: {
                "provider": provider,
                "voice": "en_US-lessac-medium",
                "speed": 1.0,
                "speech_voice": "",
                "enabled": False,
                "auto_speak": False,
            },
        )
        state = _make_state(tmp_path)
        async with TestClient(TestServer(_make_voice_app(state))) as client:
            resp = await client.post(
                "/api/voice/synthesize", json={"text": "Hello", "session": "s1"}
            )

            assert resp.status == 503
            body = await resp.json()
            assert body["error"]["code"] == "tts_disabled"
            # The message names the switch — an unavailability the user cannot act on is
            # barely better than synthesizing anyway.
            assert "Speak replies aloud" in body["error"]["message"]
        provider.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_missing_enabled_key_is_treated_as_off(self, tmp_path, monkeypatch):
        """Fail closed on a params dict that predates the key. A default of True would make
        the guard vacuous for exactly the callers that never set it."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        monkeypatch.setattr(
            "personalclaw.dashboard.chat_voice.active_voice_params",
            lambda **_kw: {
                "provider": MagicMock(),
                "voice": "v",
                "speed": 1.0,
                "speech_voice": "",
            },
        )
        state = _make_state(tmp_path)
        async with TestClient(TestServer(_make_voice_app(state))) as client:
            resp = await client.post(
                "/api/voice/synthesize", json={"text": "Hello", "session": "s1"}
            )
            assert resp.status == 503
            assert (await resp.json())["error"]["code"] == "tts_disabled"

    @pytest.mark.asyncio
    async def test_nothing_is_marked_as_spoken_when_synthesis_is_refused(
        self, tmp_path, monkeypatch
    ):
        """`record_spoken` marks text as ours so a hands-free transcription can recognise
        speaker bleed. Adding a refusal made the pre-existing order wrong: text we decline to
        speak must not be marked as ours, or the echo filter drops a phrase the USER said."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        monkeypatch.setattr(
            "personalclaw.dashboard.chat_voice.active_voice_params",
            lambda **_kw: {
                "provider": MagicMock(),
                "voice": "v",
                "speed": 1.0,
                "speech_voice": "",
                "enabled": False,
                "auto_speak": False,
            },
        )
        state = _make_state(tmp_path)
        state.record_spoken = MagicMock()
        async with TestClient(TestServer(_make_voice_app(state))) as client:
            resp = await client.post(
                "/api/voice/synthesize", json={"text": "Hello", "session": "s1"}
            )
            assert resp.status == 503
        assert not state.record_spoken.called, (
            "text we refused to speak was marked as ours, so the echo filter would drop a "
            "phrase the USER said that happened to match it"
        )

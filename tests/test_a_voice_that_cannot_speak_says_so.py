"""Every synthesis a user hears asks the provider first whether it can speak with the voice.

``TtsProvider.can_synthesize`` answers for the voice itself (a Piper voice that is not on disk, a
missing runtime, a provider with no key), and nothing in core called it: a bound voice that could
not speak was asked to anyway, and Speak answered that synthesis "produced no audio". Each path is
driven here with a provider that says no, and none of them may ask it for audio.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from chat_test_helpers import _make_state

from personalclaw.tts.registry import TtsNotReady, can_speak, route_synthesis
from personalclaw.voice_reply import streaming_voice_reply, synthesize_speech

VOICE = "en_US-fakevoice-medium"


class _Provider:
    """A text-to-speech provider that says whether it can speak, and records being asked to."""

    name = "fake-tts"
    display_name = "Fake TTS"

    def __init__(self, *, ready: bool) -> None:
        self.ready = ready
        self.asked: list[str] = []
        self.synthesized: list[str] = []

    async def can_synthesize(self, voice: str = "") -> bool:
        self.asked.append(voice)
        return self.ready

    async def synthesize(self, text, voice="", output_path="", *, speed=1.0, **opts):
        self.synthesized.append(text)
        return None


def _params(provider: object) -> dict:
    return {
        "provider": provider,
        "voice": VOICE,
        "speed": 1.0,
        "speech_voice": "",
        "enabled": True,
        "auto_speak": False,
    }


@pytest.mark.asyncio
async def test_speak_refuses_a_voice_its_provider_cannot_speak_with(tmp_path, monkeypatch):
    from personalclaw.dashboard.chat_voice import api_voice_synthesize

    provider = _Provider(ready=False)
    monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
    monkeypatch.setattr(
        "personalclaw.dashboard.chat_voice.active_voice_params", lambda **_kw: _params(provider)
    )
    state = _make_state(tmp_path)
    state.record_spoken = MagicMock()
    app = web.Application()
    app["state"] = state
    app.router.add_post("/api/voice/synthesize", api_voice_synthesize)
    async with TestClient(TestServer(app)) as client:
        resp = await client.post("/api/voice/synthesize", json={"text": "Hello", "session": "s1"})
        body = await resp.json()
    assert resp.status == 503
    assert body["error"]["code"] == "tts_not_ready"
    assert body["error"]["message"] == (
        f"Fake TTS says it cannot speak with {VOICE} right now. "
        "Check the text-to-speech model in Settings → Models."
    )
    assert provider.asked == [VOICE]
    assert provider.synthesized == []
    assert not state.record_spoken.called, "text we did not speak was marked as ours"


@pytest.mark.asyncio
async def test_the_stream_refuses_before_its_first_chunk():
    provider = _Provider(ready=False)
    with pytest.raises(TtsNotReady) as refused:
        async for _chunk in streaming_voice_reply(provider, "Hello. World.", voice=VOICE):
            pass
    assert refused.value.status == 503
    assert f"cannot speak with {VOICE}" in refused.value.message
    assert provider.synthesized == []


@pytest.mark.asyncio
async def test_a_channel_voice_reply_is_not_asked_of_a_voice_that_cannot_speak(caplog):
    provider = _Provider(ready=False)
    with caplog.at_level(logging.WARNING, logger="personalclaw.voice_reply"):
        assert await synthesize_speech(provider, "Hello there.", voice=VOICE) is None
    assert provider.synthesized == []
    assert f"cannot speak with {VOICE}" in caplog.text


@pytest.mark.asyncio
async def test_a_routed_synthesis_refuses_first():
    provider = _Provider(ready=False)
    with pytest.raises(TtsNotReady):
        await route_synthesis(_params(provider), "Selftest.")
    assert provider.asked == [VOICE] and provider.synthesized == []


@pytest.mark.asyncio
async def test_a_voice_that_can_speak_is_spoken():
    """The control: the same provider saying yes is asked for audio, once per sentence."""
    provider = _Provider(ready=True)
    chunks = [c async for c in streaming_voice_reply(provider, "Hello. World.", voice=VOICE)]
    assert chunks == []  # the fake returns no audio file, so no chunk is yielded
    assert provider.asked == [VOICE]
    assert provider.synthesized == ["Hello.", "World."]


@pytest.mark.asyncio
async def test_an_adapter_without_the_method_makes_no_claim():
    """An adapter that does not subclass ``TtsProvider`` may leave ``can_synthesize`` out; its
    synthesis then answers for itself, as it did before."""

    class _Bare:
        async def synthesize(self, text, voice="", output_path="", *, speed=1.0, **opts):
            return None

    assert await can_speak(_Bare(), VOICE) is True

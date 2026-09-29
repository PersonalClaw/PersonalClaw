"""Voice input says why speech-to-text failed or is unavailable, in the bound provider's words.

🔴 Before: the speech-to-text contract had no error channel. ``is_available()`` False came back
from the microphone's endpoint as "STT not available", which the composer turned into "configure
a speech-to-text model" for a model that was already bound — when what had failed was the
provider signing in. And a transcription the provider could not run came back as an empty
transcript, which reads as a recording with no speech in it.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from aiohttp import FormData, web
from aiohttp.test_utils import TestClient, TestServer
from chat_test_helpers import _make_state

from personalclaw.sdk.stt import SttError  # raised as an app's provider raises it
from personalclaw.stt.provider import SttProvider

REASON = "No credentials were found for this account. Sign in, then try again."


class _Bound(SttProvider):
    """A bound speech-to-text provider whose availability and reason a test sets."""

    def __init__(self, *, available: bool, reason: str | None = None) -> None:
        self._available = available
        self._reason = reason

    @property
    def name(self) -> str:
        return "work-cloud"

    @property
    def display_name(self) -> str:
        return "Work cloud"

    async def is_available(self) -> bool:
        return self._available

    async def unavailable_reason(self) -> str:
        if self._reason is None:
            return await super().unavailable_reason()
        return self._reason

    async def transcribe(self, audio_path: str, model: str = "", language: str = "") -> str | None:
        raise AssertionError("the endpoint must not transcribe with an unavailable provider")


@pytest.fixture
def home(tmp_path, monkeypatch):
    """Every reader the microphone's endpoint touches reads this scratch home."""
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
    return tmp_path


def _app(home):
    from personalclaw.dashboard.handlers.core import api_stt_transcribe

    app = web.Application()
    app["state"] = _make_state(home)
    app.router.add_post("/api/stt/transcribe", api_stt_transcribe)
    return app


def _bind(provider: SttProvider):
    return patch("personalclaw.stt.registry.active_stt", return_value=(provider, "stt-v1"))


async def _post(home):
    form = FormData()
    form.add_field("audio", b"\x00\x01\x02", filename="recording.webm", content_type="audio/webm")
    async with TestClient(TestServer(_app(home))) as client:
        resp = await client.post("/api/stt/transcribe", data=form)
        return resp.status, await resp.json()


@pytest.mark.asyncio
async def test_the_microphone_says_why_the_bound_provider_is_unavailable(home):
    with _bind(_Bound(available=False, reason=REASON)):
        status, body = await _post(home)

    assert (status, body) == (503, {"error": REASON, "code": "stt_unavailable"})


@pytest.mark.asyncio
async def test_a_provider_that_cannot_say_leaves_the_microphone_its_own_words(home):
    with _bind(_Bound(available=False)):
        status, body = await _post(home)

    assert (status, body) == (
        503,
        {
            "error": "The speech-to-text model can't run right now, and its provider doesn't say "
            "why. Check the gateway log, or choose another model under Speech-to-text in "
            "Settings → Models.",
            "code": "stt_unavailable",
        },
    )


@pytest.mark.asyncio
async def test_the_microphone_says_no_model_is_chosen(home):
    """🔴 Red before: "STT not available", which the composer matched on its words to give its
    setup advice. The sentence is the gateway's now, and the code is what a composer keys on."""
    with patch("personalclaw.stt.registry.active_stt", return_value=None):
        status, body = await _post(home)

    assert (status, body) == (
        503,
        {
            "error": "No speech-to-text model is chosen, so nothing can be transcribed. Choose one "
            "under Speech-to-text in Settings → Models.",
            "code": "stt_unavailable",
        },
    )


@pytest.mark.asyncio
async def test_the_microphone_says_speech_to_text_is_turned_off(home):
    """A model is chosen and Enable speech-to-text is off: "configure a model" was not the fix."""
    with (
        _bind(_Bound(available=True)),
        patch(
            "personalclaw.providers.use_cases.load_use_case_settings",
            return_value={"enabled": False},
        ),
    ):
        status, body = await _post(home)

    assert (status, body) == (
        503,
        {
            "error": "Speech-to-text is turned off. Turn on Enable speech-to-text in Settings → "
            "Speech & Transcription.",
            "code": "stt_unavailable",
        },
    )


@pytest.mark.asyncio
async def test_the_microphone_says_why_a_transcription_could_not_run(home, monkeypatch):
    """The provider was available and then could not transcribe, and said why: that sentence is
    the answer — not an empty transcript, which the composer shows as silence."""

    async def _refused(_path, **_kw):
        raise SttError(REASON)

    monkeypatch.setattr("personalclaw.transcribe.transcribe_audio_detailed", _refused)
    with (
        _bind(_Bound(available=True)),
        patch("personalclaw.transcribe._ffmpeg_present", return_value=True),
    ):
        status, body = await _post(home)

    assert (status, body) == (502, {"error": REASON}), "no code: the composer names it a failure"


class _GivesBackNothing(_Bound):
    """Available, and then answers ``None``: it could not transcribe and did not say why."""

    async def transcribe(self, audio_path: str, model: str = "", language: str = "") -> str | None:
        return None


@pytest.mark.asyncio
async def test_the_microphone_says_a_transcription_came_back_with_nothing(home):
    """🔴 Red before: ``{"text": ""}`` with a 200, which the composer shows as a recording with no
    speech in it. Driven through the real transcribe path with the bound provider."""
    from personalclaw.transcribe import NO_TRANSCRIPT_NO_REASON

    with (
        _bind(_GivesBackNothing(available=True)),
        patch("personalclaw.transcribe._ffmpeg_present", return_value=True),
    ):
        status, body = await _post(home)

    assert (status, body) == (502, {"error": NO_TRANSCRIPT_NO_REASON})

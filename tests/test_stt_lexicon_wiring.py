"""Issue #658 — the mic STT route gets the same Lexicon halves knowledge ingestion has.

Before the fix, the composer's mic path called the flat ``transcribe_audio`` (a function
with no ``bias_terms`` parameter at all) and never ran the learned-corrections pass — so
the whole Vocabulary section was inert for dictation while four copy sites claimed it
biased "mic input". These tests pin the wiring:

* the user's Lexicon terms flow into the detailed transcribe call as ``bias_terms``,
* learned auto-corrections rewrite the mic transcript,
* a Lexicon failure never breaks dictation (best-effort doctrine, mirroring ingestion),
* credential redaction stays DOWNSTREAM of correction — ``correct()`` re-derives the
  flat text from raw segment words, which would undo any upstream redaction.
"""

from unittest.mock import AsyncMock

import pytest
from aiohttp import FormData, web
from aiohttp.test_utils import TestClient, TestServer
from chat_test_helpers import _make_state

from personalclaw.lexicon.service import LexiconService
from personalclaw.lexicon.store import LexiconStore
from personalclaw.stt.provider import TranscriptResult, TranscriptSegment, TranscriptWord


def _stt_app(state):
    from personalclaw.dashboard.handlers.core import api_stt_transcribe

    app = web.Application()
    app["state"] = state
    app.router.add_post("/api/stt/transcribe", api_stt_transcribe)
    return app


def _result(words: list[str]) -> TranscriptResult:
    """A one-segment TranscriptResult whose flat text mirrors its words."""
    text = " ".join(words)
    return TranscriptResult(
        text=text,
        segments=[
            TranscriptSegment(
                start=0.0,
                end=float(len(words)),
                text=text,
                words=[
                    TranscriptWord(float(i), float(i) + 0.9, w + " ", 1.0)
                    for i, w in enumerate(words)
                ],
            )
        ],
    )


def _lexicon_on(monkeypatch, tmp_path) -> LexiconService:
    """A real LexiconService on a tmp DB, installed as the process singleton."""
    svc = LexiconService(store=LexiconStore(str(tmp_path / "lexicon.db")))
    monkeypatch.setattr("personalclaw.lexicon.service._service", svc)
    return svc


async def _transcribe(client) -> dict:
    form = FormData()
    form.add_field("audio", b"\x00\x01\x02", filename="recording.webm", content_type="audio/webm")
    resp = await client.post("/api/stt/transcribe", data=form)
    assert resp.status == 200
    return await resp.json()


@pytest.fixture
def voice_home(tmp_path, monkeypatch):
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
    return tmp_path


@pytest.fixture(autouse=True)
def _stt_available(monkeypatch):
    monkeypatch.setattr("personalclaw.transcribe.is_available", AsyncMock(return_value=True))


class TestMicLexiconWiring:
    @pytest.mark.asyncio
    async def test_lexicon_terms_bias_the_mic_decode(self, voice_home, monkeypatch, tmp_path):
        """The mic route resolves the Lexicon's bias terms and hands them to the decoder."""
        svc = _lexicon_on(monkeypatch, tmp_path)
        svc.add_manual_term("kubectl")
        spy = AsyncMock(return_value=_result(["open", "the", "logs"]))
        monkeypatch.setattr("personalclaw.transcribe.transcribe_audio_detailed", spy)
        async with TestClient(TestServer(_stt_app(_make_state(voice_home)))) as client:
            body = await _transcribe(client)
        assert body["text"] == "open the logs"
        assert spy.await_count == 1
        assert "kubectl" in (spy.await_args.kwargs.get("bias_terms") or [])

    @pytest.mark.asyncio
    async def test_empty_lexicon_decodes_unbiased(self, voice_home, monkeypatch, tmp_path):
        """No terms → bias_terms is None (today's behavior), and the text flows through."""
        _lexicon_on(monkeypatch, tmp_path)
        spy = AsyncMock(return_value=_result(["hello", "there"]))
        monkeypatch.setattr("personalclaw.transcribe.transcribe_audio_detailed", spy)
        async with TestClient(TestServer(_stt_app(_make_state(voice_home)))) as client:
            body = await _transcribe(client)
        assert body["text"] == "hello there"
        assert spy.await_args.kwargs.get("bias_terms") is None

    @pytest.mark.asyncio
    async def test_learned_corrections_rewrite_the_mic_transcript(
        self, voice_home, monkeypatch, tmp_path
    ):
        """An always-apply learned fix (the Vocabulary UI's add-a-fix) corrects dictation."""
        svc = _lexicon_on(monkeypatch, tmp_path)
        svc.learn_correction("kubecuddle", "kubectl", always=True)
        monkeypatch.setattr(
            "personalclaw.transcribe.transcribe_audio_detailed",
            AsyncMock(return_value=_result(["run", "kubecuddle", "get", "pods"])),
        )
        async with TestClient(TestServer(_stt_app(_make_state(voice_home)))) as client:
            body = await _transcribe(client)
        assert "kubectl" in body["text"]
        assert "kubecuddle" not in body["text"]

    @pytest.mark.asyncio
    async def test_lexicon_failure_never_breaks_dictation(self, voice_home, monkeypatch):
        """Best-effort doctrine: a broken Lexicon leaves the transcript untouched, not a 500.

        Patches the PACKAGE bindings (``personalclaw.lexicon.*``) — the names the route
        resolves at call time — so both halves genuinely raise into the handler."""

        def _boom(*_a, **_kw):
            raise RuntimeError("lexicon store unavailable")

        async def _aboom(*_a, **_kw):
            raise RuntimeError("lexicon store unavailable")

        monkeypatch.setattr("personalclaw.lexicon.select_bias_terms", _aboom)
        monkeypatch.setattr("personalclaw.lexicon.get_lexicon_service", _boom)
        monkeypatch.setattr(
            "personalclaw.transcribe.transcribe_audio_detailed",
            AsyncMock(return_value=_result(["still", "works"])),
        )
        async with TestClient(TestServer(_stt_app(_make_state(voice_home)))) as client:
            body = await _transcribe(client)
        assert body["text"] == "still works"

    @pytest.mark.asyncio
    async def test_redaction_applies_to_the_corrected_transcript(
        self, voice_home, monkeypatch, tmp_path
    ):
        """Security ordering pin: correct() re-derives flat text from RAW segment words,
        so the route's credential redaction must run after it — a credential spoken into
        the mic never reaches the composer, corrections or not."""
        svc = _lexicon_on(monkeypatch, tmp_path)
        svc.learn_correction("kubecuddle", "kubectl", always=True)
        monkeypatch.setattr(
            "personalclaw.transcribe.transcribe_audio_detailed",
            AsyncMock(return_value=_result(["kubecuddle", "key", "AKIAIOSFODNN7EXAMPLE"])),
        )
        async with TestClient(TestServer(_stt_app(_make_state(voice_home)))) as client:
            body = await _transcribe(client)
        assert "AKIAIOSFODNN7EXAMPLE" not in body["text"]
        assert "kubectl" in body["text"]

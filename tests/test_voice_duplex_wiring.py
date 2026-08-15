"""MULTIMODAL-IO §4 — the duplex pack where it meets the endpoints and config.

The pure rules live in ``test_voice_duplex.py``. This module pins the wiring the
atom promises: cleaning applied after redaction on the synthesis path, the last
spoken text recorded per session, the echo consult on a duplex transcribe, the
voice disclaimer + ``input_origin`` reaching the session JSONL, and VoiceConfig
round-tripping through all four config points.
"""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import FormData, web
from aiohttp.test_utils import TestClient, TestServer
from chat_test_helpers import _make_app, _make_state

from personalclaw.config.loader import AppConfig, VoiceConfig
from personalclaw.dashboard.handlers.core import _EDITABLE_CONFIG
from personalclaw.voice.duplex import (
    DEFAULT_CONFIRMATION_PHRASES,
    DEFAULT_EXIT_PHRASES,
    VOICE_DISCLAIMER,
)

_SPOKEN = "The deployment finished and everything looks healthy."


def _voice_app(state):
    from personalclaw.dashboard.chat_voice import api_voice_synthesize

    app = web.Application()
    app["state"] = state
    app.router.add_post("/api/voice/synthesize", api_voice_synthesize)
    return app


def _stt_app(state):
    from personalclaw.dashboard.handlers.core import api_stt_transcribe

    app = web.Application()
    app["state"] = state
    app.router.add_post("/api/stt/transcribe", api_stt_transcribe)
    return app


def _write_voice_config(tmp_path, **fields):
    (tmp_path / "config.json").write_text(json.dumps({"voice": fields}))


@pytest.fixture
def voice_home(tmp_path, monkeypatch):
    """Isolate config_dir for every reader the voice path touches."""

    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
    return tmp_path


def _stub_synthesis(monkeypatch, sentences):
    monkeypatch.setattr(
        "personalclaw.dashboard.chat_voice.active_voice_params",
        lambda: {
            "provider": MagicMock(),
            "voice": "en_US-lessac-medium",
            "speed": 1.0,
            "speech_voice": "",
            "enabled": True,
            "auto_speak": False,
        },
    )

    async def mock_stream(_provider, text, **_kw):
        sentences.append(text)
        yield 0, text, b"\x00\x01\x02"

    monkeypatch.setattr("personalclaw.dashboard.chat_voice.streaming_voice_reply", mock_stream)
    monkeypatch.setattr(
        "personalclaw.dashboard.chat_voice.stitch_wavs", AsyncMock(return_value=None)
    )


# ── §4.3 clean_for_speech on the synthesis path ──


class TestSynthesisCleaning:
    @pytest.mark.asyncio
    async def test_synthesis_speaks_cleaned_text(self, voice_home, monkeypatch):
        spoken: list[str] = []
        _stub_synthesis(monkeypatch, spoken)
        state = _make_state(voice_home)
        state.broadcast_ws = MagicMock()
        async with TestClient(TestServer(_voice_app(state))) as client:
            resp = await client.post(
                "/api/voice/synthesize",
                json={
                    "text": (
                        "Edit src/personalclaw/voice/duplex.py — " "see https://docs.example.com/x"
                    ),
                    "session": "s1",
                },
            )
            assert resp.status == 200
        assert spoken == ["Edit duplex.py — see docs.example.com"]

    @pytest.mark.asyncio
    async def test_cleaning_is_switchable(self, voice_home, monkeypatch):
        _write_voice_config(voice_home, clean_for_speech_enabled=False)
        spoken: list[str] = []
        _stub_synthesis(monkeypatch, spoken)
        state = _make_state(voice_home)
        state.broadcast_ws = MagicMock()
        async with TestClient(TestServer(_voice_app(state))) as client:
            resp = await client.post(
                "/api/voice/synthesize",
                json={"text": "Run `pytest --no-cov` now", "session": "s1"},
            )
            assert resp.status == 200
        assert spoken == ["Run `pytest --no-cov` now"]

    @pytest.mark.asyncio
    async def test_synthesis_records_the_spoken_text_for_the_session(self, voice_home, monkeypatch):
        _stub_synthesis(monkeypatch, [])
        state = _make_state(voice_home)
        state.broadcast_ws = MagicMock()
        assert state.last_spoken("s1") == ""
        async with TestClient(TestServer(_voice_app(state))) as client:
            await client.post("/api/voice/synthesize", json={"text": _SPOKEN, "session": "s1"})
        assert state.last_spoken("s1") == _SPOKEN
        assert state.last_spoken("other") == ""

    def test_last_spoken_is_bounded_and_latest_wins(self, voice_home):
        state = _make_state(voice_home)
        limit = state._LAST_SPOKEN_MAX_SESSIONS
        for i in range(limit + 5):
            state.record_spoken(f"s{i}", f"line {i}")
        assert len(state._last_spoken) == limit
        assert state.last_spoken("s0") == ""  # oldest evicted
        assert state.last_spoken(f"s{limit + 4}") == f"line {limit + 4}"
        state.record_spoken("s0", "first")
        state.record_spoken("s0", "second")
        assert state.last_spoken("s0") == "second"
        state.record_spoken("s0", "   ")
        assert state.last_spoken("s0") == "second"  # blank never overwrites


# ── §4.2 echo consult on the transcribe path ──


class TestTranscribeEchoConsult:
    async def _transcribe(self, client, query=""):
        form = FormData()
        form.add_field(
            "audio", b"\x00\x01\x02", filename="recording.webm", content_type="audio/webm"
        )
        return await client.post(f"/api/stt/transcribe{query}", data=form)

    @pytest.fixture(autouse=True)
    def _stub_stt(self, monkeypatch):
        monkeypatch.setattr("personalclaw.transcribe.is_available", AsyncMock(return_value=True))
        self.heard = "the deployment finished and everything"
        monkeypatch.setattr(
            "personalclaw.transcribe.transcribe_audio",
            AsyncMock(side_effect=lambda *_a, **_kw: self.heard),
        )

    @pytest.mark.asyncio
    async def test_duplex_request_filters_the_assistants_own_speech(self, voice_home, monkeypatch):
        state = _make_state(voice_home)
        state.record_spoken("s1", _SPOKEN)
        async with TestClient(TestServer(_stt_app(state))) as client:
            resp = await self._transcribe(client, "?duplex=true&session=s1")
            assert resp.status == 200
            body = await resp.json()
        # Empty text with a stated reason — the dashboard can say why, instead of
        # looking deaf.
        assert body == {"text": "", "filtered": "echo"}

    @pytest.mark.asyncio
    async def test_non_duplex_request_is_never_echo_filtered(self, voice_home):
        state = _make_state(voice_home)
        state.record_spoken("s1", _SPOKEN)
        async with TestClient(TestServer(_stt_app(state))) as client:
            body = await (await self._transcribe(client, "?session=s1")).json()
        assert body["text"] == self.heard
        assert "filtered" not in body

    @pytest.mark.asyncio
    async def test_echo_filter_is_switchable(self, voice_home):
        _write_voice_config(voice_home, echo_filter_enabled=False)
        state = _make_state(voice_home)
        state.record_spoken("s1", _SPOKEN)
        async with TestClient(TestServer(_stt_app(state))) as client:
            body = await (await self._transcribe(client, "?duplex=true&session=s1")).json()
        assert body["text"] == self.heard
        assert "filtered" not in body

    @pytest.mark.asyncio
    async def test_genuine_speech_survives_a_duplex_request(self, voice_home):
        state = _make_state(voice_home)
        state.record_spoken("s1", _SPOKEN)
        self.heard = "open the door and check the logs"
        async with TestClient(TestServer(_stt_app(state))) as client:
            body = await (await self._transcribe(client, "?duplex=true&session=s1")).json()
        assert body["text"] == self.heard
        assert "filtered" not in body

    @pytest.mark.asyncio
    async def test_nothing_spoken_yet_cannot_filter(self, voice_home):
        state = _make_state(voice_home)
        async with TestClient(TestServer(_stt_app(state))) as client:
            body = await (await self._transcribe(client, "?duplex=true&session=s1")).json()
        assert body["text"] == self.heard

    @pytest.mark.asyncio
    async def test_response_carries_the_origin_and_the_disclaimer(self, voice_home):
        state = _make_state(voice_home)
        async with TestClient(TestServer(_stt_app(state))) as client:
            body = await (await self._transcribe(client)).json()
        assert body["input_origin"] == "voice"
        assert body["disclaimer"] == VOICE_DISCLAIMER

    @pytest.mark.asyncio
    async def test_disclaimer_is_switchable(self, voice_home):
        _write_voice_config(voice_home, voice_disclaimer_enabled=False)
        state = _make_state(voice_home)
        async with TestClient(TestServer(_stt_app(state))) as client:
            body = await (await self._transcribe(client)).json()
        assert body["input_origin"] == "voice"
        assert "disclaimer" not in body


# ── §4.4 disclaimer + input_origin into the session JSONL ──


class TestVoiceOriginTurn:
    @pytest.fixture(autouse=True)
    def _stub_runner(self, monkeypatch):
        monkeypatch.setattr(
            "personalclaw.dashboard.chat_handlers._run_chat", AsyncMock(return_value=None)
        )

    async def _send(self, client, **body):
        return await client.post("/api/chat?ws=1", json={"session": "s1", **body})

    def _user_turn(self, state, tmp_path):
        # Persist through the product's own writer, then read what landed on disk —
        # the atom's claim is about the session JSONL, not an in-memory list.
        from personalclaw.dashboard.chat_persistence import _save_session_to_history

        _save_session_to_history(state, state.get_or_create_session("s1"))
        matches = list(tmp_path.rglob("*.jsonl"))
        assert matches, f"no session JSONL written under {tmp_path}"
        records = [
            json.loads(line)
            for path in matches
            for line in path.read_text().splitlines()
            if line.strip()
        ]
        users = [r for r in records if r.get("role") == "user"]
        assert users, f"no user turn in {records}"
        return users[-1]

    @pytest.mark.asyncio
    async def test_voice_turn_carries_disclaimer_and_origin(self, voice_home):
        state = _make_state(voice_home)
        state.broadcast_ws = MagicMock()
        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await self._send(client, message="deploy the beta", input_origin="voice")
            assert resp.status == 200
        turn = self._user_turn(state, voice_home)
        assert turn["content"].startswith("deploy the beta")
        assert VOICE_DISCLAIMER in turn["content"]
        assert turn["meta"]["input_origin"] == "voice"

    @pytest.mark.asyncio
    async def test_typed_turn_carries_neither(self, voice_home):
        state = _make_state(voice_home)
        state.broadcast_ws = MagicMock()
        async with TestClient(TestServer(_make_app(state))) as client:
            assert (await self._send(client, message="deploy the beta")).status == 200
        turn = self._user_turn(state, voice_home)
        assert turn["content"] == "deploy the beta"
        assert VOICE_DISCLAIMER not in turn["content"]
        assert (turn.get("meta") or {}).get("input_origin") is None

    @pytest.mark.asyncio
    async def test_unknown_origin_is_treated_as_typed(self, voice_home):
        state = _make_state(voice_home)
        state.broadcast_ws = MagicMock()
        async with TestClient(TestServer(_make_app(state))) as client:
            assert (
                await self._send(client, message="deploy it", input_origin="telepathy")
            ).status == 200
        turn = self._user_turn(state, voice_home)
        assert VOICE_DISCLAIMER not in turn["content"]

    @pytest.mark.asyncio
    async def test_origin_recorded_without_the_disclaimer_when_disabled(self, voice_home):
        _write_voice_config(voice_home, voice_disclaimer_enabled=False)
        state = _make_state(voice_home)
        state.broadcast_ws = MagicMock()
        async with TestClient(TestServer(_make_app(state))) as client:
            assert (
                await self._send(client, message="deploy the beta", input_origin="voice")
            ).status == 200
        turn = self._user_turn(state, voice_home)
        assert VOICE_DISCLAIMER not in turn["content"]
        assert turn["meta"]["input_origin"] == "voice"


# ── §4.5 VoiceConfig through the four wiring points ──


class TestVoiceConfigRoundTrip:
    FIELDS = (
        "confirmation_phrases",
        "exit_phrases",
        "echo_filter_enabled",
        "duplex_mute_enabled",
        "clean_for_speech_enabled",
        "voice_disclaimer_enabled",
    )

    def test_six_fields_with_labels_and_help(self):
        fields = VoiceConfig.__dataclass_fields__
        assert tuple(fields) == self.FIELDS
        for name in self.FIELDS:
            meta = fields[name].metadata
            assert meta.get("label"), name
            assert meta.get("help"), name

    def test_defaults(self):
        cfg = VoiceConfig()
        assert cfg.confirmation_phrases == list(DEFAULT_CONFIRMATION_PHRASES)
        assert cfg.exit_phrases == list(DEFAULT_EXIT_PHRASES)
        assert cfg.echo_filter_enabled is True
        assert cfg.duplex_mute_enabled is True
        assert cfg.clean_for_speech_enabled is True
        assert cfg.voice_disclaimer_enabled is True

    def test_load_reads_every_field(self, voice_home):
        _write_voice_config(
            voice_home,
            confirmation_phrases=["engage", "  ", 7, "make it so"],
            exit_phrases=["abort"],
            echo_filter_enabled=False,
            duplex_mute_enabled=False,
            clean_for_speech_enabled=False,
            voice_disclaimer_enabled=False,
        )
        cfg = AppConfig.load().voice
        assert cfg.confirmation_phrases == ["engage", "make it so"]
        assert cfg.exit_phrases == ["abort"]
        assert cfg.echo_filter_enabled is False
        assert cfg.duplex_mute_enabled is False
        assert cfg.clean_for_speech_enabled is False
        assert cfg.voice_disclaimer_enabled is False

    def test_load_falls_back_to_defaults_on_junk(self, voice_home):
        (voice_home / "config.json").write_text(json.dumps({"voice": "not-a-dict"}))
        assert AppConfig.load().voice == VoiceConfig()

    def test_load_falls_back_when_a_phrase_list_empties(self, voice_home):
        # Hands-free must stay operable: an empty list would make the mode deaf to
        # every confirmation.
        _write_voice_config(voice_home, confirmation_phrases=[], exit_phrases=["", "  "])
        cfg = AppConfig.load().voice
        assert cfg.confirmation_phrases == list(DEFAULT_CONFIRMATION_PHRASES)
        assert cfg.exit_phrases == list(DEFAULT_EXIT_PHRASES)

    def test_to_dict_exposes_the_section(self, voice_home):
        section = AppConfig.load().to_dict()["voice"]
        assert set(section) == set(self.FIELDS)

    def test_patch_allowlist_covers_all_six(self):
        for name in self.FIELDS:
            assert f"voice.{name}" in _EDITABLE_CONFIG, name
        assert _EDITABLE_CONFIG["voice.confirmation_phrases"]["type"] == "str_list"
        assert _EDITABLE_CONFIG["voice.echo_filter_enabled"]["type"] == "bool"

"""Tests for remote (OpenAI-compatible) STT/TTS model-provider adapters.

A remote STT/TTS model selected in ``active_models.json`` must resolve through
the typed registries exactly like a bundled local one: the registry builds one
adapter per OpenAI-family config provider, keyed by the provider's config name.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _write_config(tmp_path, providers):
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"providers": providers}))
    return cfg


# ── openai_family_providers (shared config reader) ───────────────────────────


class TestOpenAIFamilyProviders:
    def test_filters_to_openai_family(self, tmp_path, monkeypatch):
        from personalclaw.providers import use_cases as uc

        _write_config(tmp_path, [
            {"name": "MyOpenAI", "type": "openai", "model": "gpt-4o",
             "options": {"api_key": "sk-x", "endpoint": "https://api.openai.com/v1"}},
            {"name": "LocalLlama", "type": "ollama", "model": "llama3",
             "options": {"endpoint": "http://localhost:11434"}},
            {"name": "Groqq", "type": "groq", "model": "whisper-large",
             "options": {"api_key": "gsk-y"}},
        ])
        monkeypatch.setattr("personalclaw.config.loader.config_path", lambda: tmp_path / "config.json")

        found = uc.openai_family_providers()
        names = {p["name"] for p in found}
        assert names == {"MyOpenAI", "Groqq"}  # ollama excluded
        my = next(p for p in found if p["name"] == "MyOpenAI")
        assert my["api_key"] == "sk-x"
        assert my["endpoint"] == "https://api.openai.com/v1"

    def test_no_config_returns_empty(self, tmp_path, monkeypatch):
        from personalclaw.providers import use_cases as uc
        monkeypatch.setattr("personalclaw.config.loader.config_path", lambda: tmp_path / "missing.json")
        assert uc.openai_family_providers() == []


# ── STT registry resolves a remote selection ─────────────────────────────────


class TestRemoteSttResolution:
    def test_active_stt_resolves_remote_provider(self, monkeypatch):
        from personalclaw.stt import registry as sr
        from personalclaw.providers import use_cases as uc

        monkeypatch.setattr(sr, "_providers", {}, raising=False)
        monkeypatch.setattr(
            uc, "openai_family_providers",
            lambda: [{"name": "MyOpenAI", "endpoint": "", "api_key": "sk-x"}],
        )
        monkeypatch.setattr(
            uc, "active_model_refs",
            lambda u: ["MyOpenAI:whisper-1"] if u == "stt" else [],
        )

        resolved = sr.active_stt()
        assert resolved is not None
        provider, model_id = resolved
        assert provider.name == "MyOpenAI"
        assert model_id == "whisper-1"

    def test_local_provider_resolves_alongside_remote(self, monkeypatch):
        # The local faster-whisper backend is an APP now: the loader registers its
        # provider into the stt registry (name "faster_whisper"). Simulate that here
        # (register a stand-in), then confirm active_stt() resolves the local binding
        # alongside the remote OpenAI-family adapter.
        from personalclaw.stt import registry as sr
        from personalclaw.providers import use_cases as uc

        class _FakeWhisper:
            name = "faster_whisper"
            display_name = "Faster Whisper"

        monkeypatch.setattr(sr, "_providers", {"faster_whisper": _FakeWhisper()}, raising=False)
        monkeypatch.setattr(
            uc, "openai_family_providers",
            lambda: [{"name": "MyOpenAI", "endpoint": "", "api_key": "sk-x"}],
        )
        monkeypatch.setattr(
            uc, "active_model_refs",
            lambda u: ["faster-whisper:turbo"] if u == "stt" else [],
        )

        resolved = sr.active_stt()
        assert resolved is not None
        provider, model_id = resolved
        assert provider.name == "faster_whisper"
        assert model_id == "turbo"

    def test_refresh_providers_clears_only_remote_adapters(self, monkeypatch):
        """refresh_providers drops the config-built REMOTE adapters but PRESERVES the
        app-registered bundled backend (faster-whisper). Clearing everything was a
        regression — a provider-config edit silently unregistered local STT until the
        next gateway restart."""
        from personalclaw.stt import registry as sr

        bundled = object()  # stands in for the app-registered faster-whisper provider
        remote = object()
        monkeypatch.setattr(sr, "_providers", {"faster_whisper": bundled, "MyOpenAI": remote}, raising=False)
        monkeypatch.setattr(sr, "_remote_names", {"MyOpenAI"}, raising=False)
        # No config providers → _ensure_registered re-adds nothing; refresh drops remote only.
        monkeypatch.setattr(
            "personalclaw.providers.use_cases.openai_family_providers", lambda: []
        )
        sr.refresh_providers()
        assert sr._providers == {"faster_whisper": bundled}  # bundled survived
        assert sr._remote_names == set()


# ── TTS registry resolves a remote selection ─────────────────────────────────


class TestRemoteTtsResolution:
    def test_active_tts_resolves_remote_provider(self, monkeypatch):
        from personalclaw.tts import registry as tr
        from personalclaw.providers import use_cases as uc

        monkeypatch.setattr(tr, "_providers", {}, raising=False)
        monkeypatch.setattr(
            uc, "openai_family_providers",
            lambda: [{"name": "MyOpenAI", "endpoint": "", "api_key": "sk-x"}],
        )
        monkeypatch.setattr(
            uc, "active_model_refs",
            lambda u: ["MyOpenAI:tts-1"] if u == "tts" else [],
        )

        resolved = tr.active_tts()
        assert resolved is not None
        provider, voice_id = resolved
        assert provider.name == "MyOpenAI"
        assert voice_id == "tts-1"

    def test_active_voice_params_provider_neutral(self, monkeypatch):
        from personalclaw.tts import registry as tr
        from personalclaw.providers import use_cases as uc

        prov = MagicMock()
        monkeypatch.setattr(tr, "active_tts", lambda: (prov, "tts-1"))
        monkeypatch.setattr(
            uc, "load_use_case_settings",
            lambda u: {"enabled": True, "auto_speak": True, "speed": 1.2, "speech_voice": "nova"},
        )

        params = tr.active_voice_params()
        assert params["provider"] is prov
        assert params["voice"] == "tts-1"
        assert params["speed"] == 1.2
        assert params["speech_voice"] == "nova"
        assert params["enabled"] is True
        assert params["auto_speak"] is True


# ── Adapter behavior ─────────────────────────────────────────────────────────


class TestOpenAISttProvider:
    @pytest.mark.asyncio
    async def test_unavailable_without_key(self):
        from personalclaw.stt.openai_provider import OpenAISttProvider
        prov = OpenAISttProvider(provider_name="X", endpoint="", api_key="")
        with patch.dict("os.environ", {}, clear=True):
            assert await prov.is_available() is False

    @pytest.mark.asyncio
    async def test_transcribe_returns_text(self, tmp_path):
        from personalclaw.stt.openai_provider import OpenAISttProvider

        audio = tmp_path / "a.webm"
        audio.write_bytes(b"x")

        fake_client = MagicMock()
        fake_client.audio.transcriptions.create = AsyncMock(
            return_value=MagicMock(text="hello there"),
        )
        fake_client.close = AsyncMock()
        fake_openai = MagicMock()
        fake_openai.AsyncOpenAI = MagicMock(return_value=fake_client)

        prov = OpenAISttProvider(provider_name="X", endpoint="", api_key="sk-x")
        with patch.dict("sys.modules", {"openai": fake_openai}):
            out = await prov.transcribe(str(audio), model="whisper-1", language="en-US")
        assert out == "hello there"
        kwargs = fake_client.audio.transcriptions.create.call_args.kwargs
        assert kwargs["model"] == "whisper-1"
        assert kwargs["language"] == "en"

    def test_remote_stt_is_inference_only_no_management(self):
        """Decoupled axes: a REMOTE STT provider implements ONLY inference (transcribe).
        It must NOT carry local-model management methods — those are the separate
        LocalModelProvider axis that only local backends (faster-whisper) implement. Its
        hosted models surface for binding via the config-provider catalog, not here."""
        from personalclaw.local_models.provider import LocalModelProvider
        from personalclaw.stt.openai_provider import OpenAISttProvider
        prov = OpenAISttProvider(provider_name="X", endpoint="", api_key="sk-x")
        assert not isinstance(prov, LocalModelProvider)
        assert not hasattr(prov, "list_models")
        assert not hasattr(prov, "download_model")
        assert not hasattr(prov, "delete_model")
        assert callable(prov.transcribe)  # inference axis present


class TestOpenAITtsProvider:
    @pytest.mark.asyncio
    async def test_synthesize_writes_audio(self, tmp_path):
        from personalclaw.tts.openai_provider import OpenAITtsProvider

        out = tmp_path / "out.mp3"
        fake_resp = MagicMock()
        fake_resp.read = MagicMock(return_value=b"ID3audio-bytes")
        fake_client = MagicMock()
        fake_client.audio.speech.create = AsyncMock(return_value=fake_resp)
        fake_client.close = AsyncMock()
        fake_openai = MagicMock()
        fake_openai.AsyncOpenAI = MagicMock(return_value=fake_client)

        prov = OpenAITtsProvider(provider_name="X", endpoint="", api_key="sk-x")
        with patch.dict("sys.modules", {"openai": fake_openai}):
            result = await prov.synthesize(
                "hello", voice="tts-1", output_path=str(out),
                speech_voice="nova", speed=1.1,
            )
        assert result == str(out)
        assert out.read_bytes() == b"ID3audio-bytes"
        kwargs = fake_client.audio.speech.create.call_args.kwargs
        assert kwargs["model"] == "tts-1"
        assert kwargs["voice"] == "nova"
        assert kwargs["speed"] == 1.1

    @pytest.mark.asyncio
    async def test_can_synthesize_requires_key(self):
        from personalclaw.tts.openai_provider import OpenAITtsProvider
        prov = OpenAITtsProvider(provider_name="X", endpoint="", api_key="")
        with patch.dict("os.environ", {}, clear=True):
            assert await prov.can_synthesize("tts-1") is False


class TestRemoteAudioEndpointGating:
    """Regression for the #38 class extended to STT/TTS: the registries build one
    adapter per OpenAI-*compatible* config provider, but a non-OpenAI endpoint
    (Alibaba/Groq/…) doesn't serve OpenAI's whisper-1/tts-1. So the curated lists
    surface ONLY on OpenAI's own endpoint; other endpoints list nothing and refuse
    an unpinned default rather than sending whisper-1/tts-1 to the wrong service."""

    @pytest.mark.asyncio
    async def test_stt_non_openai_unpinned_transcribe_refuses(self, tmp_path):
        # The #38 gating now lives on the INFERENCE axis (transcribe): a non-OpenAI
        # endpoint has no known default transcriber, so an unpinned call refuses rather
        # than sending whisper-1 to the wrong service. (Model DISCOVERY for binding is
        # the config-provider catalog's job, not the adapter's — see the decoupling.)
        from personalclaw.stt.openai_provider import OpenAISttProvider
        audio = tmp_path / "a.wav"
        audio.write_bytes(b"RIFFxxxx")
        prov = OpenAISttProvider(
            provider_name="Alibaba",
            endpoint="https://dashscope-intl.aliyuncs.com/compatible-mode/v1", api_key="k")
        assert await prov.transcribe(str(audio)) is None

    def test_remote_tts_is_inference_only_no_management(self):
        """Decoupled axes: a REMOTE TTS provider implements ONLY inference (synthesize);
        no list_voices/download_voice/delete_voice — those are the LocalModelProvider
        axis for local backends (piper)."""
        from personalclaw.local_models.provider import LocalModelProvider
        from personalclaw.tts.openai_provider import OpenAITtsProvider
        prov = OpenAITtsProvider(provider_name="X", endpoint="", api_key="k")
        assert not isinstance(prov, LocalModelProvider)
        assert not hasattr(prov, "list_voices")
        assert not hasattr(prov, "download_voice")
        assert callable(prov.synthesize)  # inference axis present

    @pytest.mark.asyncio
    async def test_tts_non_openai_unpinned_synthesize_refuses(self, tmp_path):
        from personalclaw.tts.openai_provider import OpenAITtsProvider
        prov = OpenAITtsProvider(
            provider_name="Alibaba",
            endpoint="https://dashscope-intl.aliyuncs.com/compatible-mode/v1", api_key="k")
        # No voice= (model) → must NOT default to tts-1 on a non-OpenAI endpoint.
        assert await prov.synthesize("hello", output_path=str(tmp_path / "o.mp3")) is None

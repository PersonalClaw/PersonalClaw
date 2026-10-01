"""Every model row in Settings → Models has a Test for the use case it is listed under.

The Test was a local-model affordance. A row showed it when the model carried a ``downloaded``
flag, and it called ``POST /api/models/local/{provider}/selftest``. An image-generation row from a
hosted provider carries that flag too (a hosted image model "needs no download"), so its Test
called the local route with a provider the local registry has never heard of and answered
"Unknown provider 'bedrock'". A hosted embedding row carries no flag, so it had no Test at all,
while the local rows beside it did.

Now there is one Test path for every use case and every provider: ``POST /api/models/test``
names the use case and the ``provider:model`` ref, and the model is called the way that use case
calls it at runtime, with the smallest input that proves something. A use case or a provider that
cannot be tested says so, on the row (``untestable`` on ``GET /api/models/available``) and as the
route's refusal. No real vendor is called: every provider here is a fake.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from types import SimpleNamespace
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from personalclaw.diarization import registry as diarization_registry
from personalclaw.diarization.provider import DiarizationProvider
from personalclaw.embedding_providers import registry as embedding_registry
from personalclaw.embedding_providers.base import EmbeddingProvider
from personalclaw.image_gen import registry as image_registry
from personalclaw.image_gen.provider import (
    ImageGenError,
    ImageGenModel,
    ImageGenProvider,
    ImageResult,
)
from personalclaw.llm.base import EVENT_COMPLETE, EVENT_TEXT_CHUNK, LLMEvent
from personalclaw.llm.capabilities import Capability, ProviderCapability
from personalclaw.llm.registry import ProviderEntry, ProviderRegistry
from personalclaw.stt import registry as stt_registry
from personalclaw.stt.provider import SttError, SttProvider
from personalclaw.tts import registry as tts_registry
from personalclaw.tts.provider import TtsProvider
from personalclaw.video_gen import registry as video_registry
from personalclaw.video_gen.provider import VideoGenModel, VideoGenProvider

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def system_temp(tmp_path, monkeypatch):
    """The system temp folder, as a Test and the providers see it: this test's own."""
    folder = tmp_path / "system-temp"
    folder.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(folder))
    return folder


# ── the route, driven as the page drives it ─────────────────────────────────────────────────


async def _test(use_case: str, ref: str) -> tuple[int, dict]:
    from personalclaw.dashboard.handlers.model_registry import api_model_test

    request = make_mocked_request("POST", "/api/models/test", app=web.Application())

    async def _json():
        return {"use_case": use_case, "model": ref}

    request.json = _json  # type: ignore[assignment]
    response = await api_model_test(request)
    return response.status, json.loads(response.body.decode())


# ── fakes for the typed registries ──────────────────────────────────────────────────────────


class _Images(ImageGenProvider):
    """A hosted image provider: its models need no download, so they read ``downloaded``."""

    def __init__(self, name: str, *, makes: bool = True) -> None:
        self._name = name
        self.makes = makes
        self.asked: list[dict[str, Any]] = []

    @property
    def name(self) -> str:
        return self._name

    @property
    def display_name(self) -> str:
        return "Lab images"

    async def is_available(self) -> bool:
        return True

    async def list_models(self) -> list[ImageGenModel]:
        return [ImageGenModel(name="canvas-1", sizes=["1024x1024", "512x512", "1280x720"])]

    async def generate(self, prompt, *, model="", size="", n=1, **opts) -> list[ImageResult]:
        self.asked.append({"prompt": prompt, "model": model, "size": size, "n": n})
        return [ImageResult(b64="aGVsbG8=")] if self.makes else []

    async def edit(self, prompt, *, source_image, mask="", model="", size="", n=1, **opts):
        raise ImageGenError("no edits here")


class _Video(VideoGenProvider):
    @property
    def name(self) -> str:
        return "lab-video"

    @property
    def display_name(self) -> str:
        return "Lab video"

    async def is_available(self) -> bool:
        return True

    async def list_models(self) -> list[VideoGenModel]:
        return [VideoGenModel(name="reel-1")]

    async def generate(self, prompt, *, model="", duration_seconds=5.0, aspect_ratio="", **opts):
        raise AssertionError("a Test never makes a video")


class _Embedder(EmbeddingProvider):
    """An app's own embedding adapter: it answers a failed embedding with None, and says why."""

    def __init__(self, *, vector: list[float] | None) -> None:
        self.vector = vector
        self.asked: list[tuple[str, str]] = []

    @property
    def name(self) -> str:
        return "lab-embed"

    @property
    def display_name(self) -> str:
        return "Lab embeddings"

    async def is_available(self) -> bool:
        return True

    async def unavailable_reason(self) -> str:
        return "" if self.vector else "Your account has no access to lab-embed-v2 yet."

    async def embed(self, text, model=""):
        self.asked.append((text, model))
        return self.vector

    async def embed_batch(self, texts, model=""):
        return [self.vector for _ in texts]


class _Ears(SttProvider):
    def __init__(self, *, hears: str | None = "", raises: Exception | None = None) -> None:
        self.hears = hears
        self.raises = raises
        self.clips: list[tuple[str, bool, str]] = []

    @property
    def name(self) -> str:
        return "lab-ears"

    @property
    def display_name(self) -> str:
        return "Lab ears"

    async def is_available(self) -> bool:
        return True

    async def transcribe(self, audio_path, model="", language=""):
        self.clips.append((audio_path, os.path.isfile(audio_path), model))
        if self.raises is not None:
            raise self.raises
        return self.hears


class _Voice(TtsProvider):
    """A voice engine that writes ``writes`` into the path it is handed."""

    def __init__(self, *, writes: bytes = b"RIFF\x24\x00\x00\x00WAVE", clones: bool = False):
        self.writes = writes
        self.supports_cloning = clones
        self.seen: dict[str, Any] = {}

    @property
    def name(self) -> str:
        return "lab-voice"

    @property
    def display_name(self) -> str:
        return "Lab voice"

    async def is_available(self) -> bool:
        return True

    async def synthesize(self, text, voice="", output_path="", *, speed=1.0, **opts):
        reference = str(opts.get("ref_audio", "") or "")
        self.seen = {
            "text": text,
            "voice": voice,
            "ref_audio": reference,
            "reference_existed": bool(reference) and os.path.isfile(reference),
        }
        with open(output_path, "wb") as fh:
            fh.write(self.writes)
        return output_path


class _Speakers(DiarizationProvider):
    def __init__(self, *, raises: Exception | None = None) -> None:
        self.raises = raises

    @property
    def name(self) -> str:
        return "lab-speakers"

    @property
    def display_name(self) -> str:
        return "Lab speakers"

    async def is_available(self) -> bool:
        return True

    async def diarize(
        self, audio_path, *, model="", num_speakers=None, min_speakers=None, max_speakers=None
    ):
        if self.raises is not None:
            raise self.raises
        return []


@pytest.fixture
def registered():
    """Register a fake in its typed registry for one test, and take it out again after."""
    undo: list[Any] = []

    def _register(registry, provider):
        registry.register_provider(provider)
        undo.append((registry, provider.name))
        return provider

    yield _register
    for registry, name in undo:
        registry.unregister_provider(name)


# ── the hosted image row that answered "Unknown provider" ──────────────────────────────────


async def test_a_hosted_image_models_test_makes_one_image_at_its_smallest_size(registered):
    """🔴 On integration there is no ``POST /api/models/test``: the row's Test called the local
    selftest, which answered 404 "Unknown provider" for every hosted image provider."""
    images = registered(image_registry, _Images("lab-images"))

    status, body = await _test("image_gen", "lab-images:canvas-1")

    assert status == 200, body
    assert body["ok"] is True, body
    assert body["detail"] == "Made one 512×512 image."
    assert images.asked == [
        {"prompt": images.asked[0]["prompt"], "model": "canvas-1", "size": "512x512", "n": 1}
    ]
    assert body["use_case"] == "image_gen" and body["model"] == "lab-images:canvas-1"


async def test_an_image_model_that_makes_nothing_fails_its_test(registered):
    registered(image_registry, _Images("lab-images", makes=False))

    status, body = await _test("image_gen", "lab-images:canvas-1")

    assert status == 200
    assert (body["ok"], body["detail"], body["reason"]) == (False, "Made no image.", "no_image")


# ── embedding: the hosted rows had no Test at all ───────────────────────────────────────────


async def test_a_hosted_embedding_models_test_embeds_one_word(registered):
    embedder = registered(embedding_registry, _Embedder(vector=[0.1] * 1024))

    status, body = await _test("embedding", "lab-embed:lab-embed-v2")

    assert status == 200, body
    assert (body["ok"], body["detail"]) == (True, "Embedded a test word into 1,024 dimensions.")
    assert embedder.asked == [("hello", "lab-embed-v2")]


async def test_an_embedding_that_came_back_empty_says_why_in_the_providers_words(registered):
    registered(embedding_registry, _Embedder(vector=None))

    _, body = await _test("embedding", "lab-embed:lab-embed-v2")

    assert body["ok"] is False
    assert body["detail"] == "Your account has no access to lab-embed-v2 yet."
    assert body["reason"] == "no_vector"


# ── chat, its routing sub-uses and image understanding: the model bridge ─────────────────────


class _Chat:
    """A scripted chat model: what it was asked, and the reply it gives."""

    def __init__(self, reply: str, asked: list) -> None:
        self.reply = reply
        self.asked = asked

    async def start(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None

    async def _answer(self, messages):
        self.asked.append(messages)
        if self.reply:
            yield LLMEvent(kind=EVENT_TEXT_CHUNK, text=self.reply)
        yield LLMEvent(kind=EVENT_COMPLETE, input_tokens=5, output_tokens=1)

    def complete(self, messages, **_kw):
        return self._answer(messages)

    def stream(self, message):
        return self._answer([{"role": "user", "content": message}])


@pytest.fixture
def chat_world(monkeypatch):
    """Two chat provider types — one takes images, one doesn't — and what each model was asked,
    with the build kwargs each was built with."""
    world: dict[str, Any] = {"reply": "OK", "asked": [], "built": []}

    def _factory(**kw):
        world["built"].append(kw)
        return _Chat(world["reply"], world["asked"])

    registry = ProviderRegistry()
    for type_, sees in (("lab-chat", True), ("lab-blind", False)):
        registry.register_type(
            ProviderCapability(
                type=type_,
                capabilities=frozenset(
                    {Capability.CHAT, Capability.VISION} if sees else {Capability.CHAT}
                ),
                supports_streaming=True,
                supports_tools=False,
                supports_embeddings=False,
                supports_vision=sees,
                max_context_tokens=8_000,
            ),
            _factory,
        )
    registry.register_entry(ProviderEntry(name="lab", type="lab-chat", model="lab-large"))
    registry.register_entry(ProviderEntry(name="blind", type="lab-blind", model="blind-1"))
    monkeypatch.setattr("personalclaw.llm.registry.get_default_registry", lambda: registry)
    # Nothing is bound: a Test runs on the model it names, never on a binding.
    monkeypatch.setattr("personalclaw.providers.use_cases.load_active_models", lambda: {})
    from personalclaw.providers import image_input

    image_input.clear_cache()
    yield world
    image_input.clear_cache()


@pytest.mark.parametrize("use_case", ["chat", "reasoning", "background", "code_tools"])
async def test_a_chat_models_test_is_one_short_reply_on_that_model(chat_world, use_case):
    status, body = await _test(use_case, "lab:lab-large")

    assert status == 200, body
    assert (body["ok"], body["detail"]) == (True, "Replied “OK”."), body
    assert len(chat_world["asked"]) == 1
    assert chat_world["asked"][0][0]["content"] == "Reply with the single word OK."
    built = chat_world["built"][-1]
    assert built["model"] == "lab-large", built
    assert built["max_tokens"] <= 1024, "a Test is a small call"


async def test_an_empty_reply_is_not_a_working_model(chat_world):
    chat_world["reply"] = ""

    _, body = await _test("chat", "lab:lab-large")

    assert (body["ok"], body["detail"], body["reason"]) == (
        False,
        "Answered with an empty reply.",
        "empty_reply",
    )


async def test_an_image_models_test_shows_it_a_picture(chat_world):
    chat_world["reply"] = "Red."

    status, body = await _test("image_modality", "lab:lab-vision-1")

    assert status == 200, body
    assert body["ok"] is True, body
    assert body["detail"] == "Looked at a small red square and replied “Red.”"
    (message,) = chat_world["asked"][0]
    kinds = [part["type"] for part in message["content"]]
    assert kinds == ["text", "image_url"]
    assert message["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")


async def test_a_provider_that_takes_no_images_has_no_image_test(chat_world):
    status, body = await _test("image_modality", "blind:blind-vision-1")

    assert status == 409
    assert body["error"]["code"] == "model_untestable"
    assert body["error"]["message"] == (
        "blind can't be handed an image, so an image Test has nothing to show it."
    )
    assert chat_world["asked"] == [], "nothing was called"


async def test_a_chat_model_test_writes_its_usage_row_as_an_evaluation(chat_world):
    from personalclaw.usage_ledger import _iter_rows

    await _test("chat", "lab:lab-large")

    rows = _iter_rows()
    assert [(r["source"], r["provider"], r["model"]) for r in rows] == [
        ("eval", "lab", "lab-large")
    ]


# ── embedding through a configured model provider's own embed() ────────────────────────────


async def test_a_model_providers_embedding_model_is_tested_through_its_embed(monkeypatch):
    built: list[dict] = []

    class _Embeds:
        async def start(self):
            return None

        async def shutdown(self):
            return None

        async def embed(self, inputs):
            return [[0.5, 0.25, 0.125] for _ in inputs]

    def _factory(**kw):
        built.append(kw)
        return _Embeds()

    registry = ProviderRegistry()
    registry.register_type(
        ProviderCapability(
            type="lab-server",
            capabilities=frozenset({Capability.CHAT, Capability.EMBEDDING}),
            supports_streaming=True,
            supports_tools=False,
            supports_embeddings=True,
            supports_vision=False,
            max_context_tokens=8_000,
        ),
        _factory,
    )
    registry.register_entry(ProviderEntry(name="server", type="lab-server", model="chat-1"))
    monkeypatch.setattr("personalclaw.llm.registry.get_default_registry", lambda: registry)

    status, body = await _test("embedding", "server:embed-small")

    assert status == 200, body
    assert (body["ok"], body["detail"]) == (True, "Embedded a test word into 3 dimensions.")
    assert built[-1]["embedding_model"] == "embed-small"


# ── speech, voices and speakers: the typed registries ──────────────────────────────────────


async def test_a_speech_to_text_test_transcribes_a_real_clip(registered, system_temp):
    ears = registered(stt_registry, _Ears(hears=""))

    _, body = await _test("stt", "lab-ears:whisper-small")

    assert body["ok"] is True, body
    assert body["detail"] == (
        "Transcribed a half-second test tone. It holds no speech, so no words came back."
    )
    clip, existed, model = ears.clips[0]
    assert existed and model == "whisper-small"
    assert list(system_temp.iterdir()) == [], "the clip is removed"


async def test_a_providers_own_failure_is_the_tests_answer(registered):
    registered(stt_registry, _Ears(raises=SttError("The transcription service needs a bucket.")))

    _, body = await _test("stt", "lab-ears:whisper-small")

    assert (body["ok"], body["detail"], body["reason"]) == (
        False,
        "The transcription service needs a bucket.",
        "error:SttError",
    )


async def test_a_voices_test_speaks_through_the_synthesis_chokepoint(registered, system_temp):
    voice = registered(tts_registry, _Voice())

    _, body = await _test("tts", "lab-voice:en-amy")

    assert (body["ok"], body["detail"]) == (True, "Spoke a one-word test line."), body
    assert voice.seen["voice"] == "en-amy" and voice.seen["ref_audio"] == ""
    assert list(system_temp.iterdir()) == [], "the audio is removed"


async def test_a_voice_that_wrote_no_audio_fails_its_test(registered):
    """The path a Test hands the engine exists before the engine runs, so a path coming back
    proves nothing: only audio in it does."""
    registered(tts_registry, _Voice(writes=b""))

    _, body = await _test("tts", "lab-voice:en-amy")

    assert (body["ok"], body["detail"], body["reason"]) == (False, "Made no audio.", "no_audio")


async def test_a_cloning_voice_is_tested_through_a_real_reference_clip(registered, system_temp):
    voice = registered(tts_registry, _Voice(clones=True))

    _, body = await _test("tts", "lab-voice:zero-shot")

    assert body["detail"] == "Spoke a one-word test line in a voice cloned from a test clip."
    assert voice.seen["reference_existed"] is True, "the clone path ran on a clip on disk"
    assert list(system_temp.iterdir()) == [], "the reference clip and the audio are removed"


async def test_a_sidecar_death_surfaces_its_typed_reason(registered):
    class _Crashed(RuntimeError):
        typed_reason = "sidecar_crashed:signal_11"

    class _Dies(_Voice):
        async def synthesize(self, text, voice="", output_path="", *, speed=1.0, **opts):
            raise _Crashed("The voice engine stopped while speaking.")

    registered(tts_registry, _Dies())

    _, body = await _test("tts", "lab-voice:en-amy")

    assert (body["ok"], body["detail"], body["reason"]) == (
        False,
        "The voice engine stopped while speaking.",
        "sidecar_crashed:signal_11",
    )


async def test_a_diarization_contract_break_fails_on_the_api_surface(registered):
    registered(
        diarization_registry,
        _Speakers(raises=AttributeError("'DiarizeOutput' object has no attribute 'itertracks'")),
    )

    _, body = await _test("diarization", "lab-speakers:pipeline-3")

    assert body["ok"] is False
    assert body["reason"] == "error:AttributeError"
    assert "itertracks" in body["detail"]


async def test_a_diarization_test_runs_the_pipeline(registered):
    registered(diarization_registry, _Speakers())

    _, body = await _test("diarization", "lab-speakers:pipeline-3")

    assert (body["ok"], body["detail"]) == (
        True,
        "Ran on a half-second test tone and found 0 speaker turns.",
    )


async def test_a_voice_file_the_provider_put_somewhere_else_is_removed_too(registered, system_temp):
    class _OwnFile(_Voice):
        async def synthesize(self, text, voice="", output_path="", *, speed=1.0, **opts):
            fd, own = tempfile.mkstemp(suffix=".wav")
            os.write(fd, self.writes)
            os.close(fd)
            return own

    registered(tts_registry, _OwnFile())

    _, body = await _test("tts", "lab-voice:en-amy")

    assert body["ok"] is True, body
    assert list(system_temp.iterdir()) == []


def test_the_test_clip_is_a_real_decodable_wav(tmp_path):
    import wave

    from personalclaw.media_fixtures import write_tone_wav

    path = str(tmp_path / "tone.wav")
    write_tone_wav(path)
    with wave.open(path, "rb") as w:
        assert (w.getnchannels(), w.getframerate(), w.getnframes()) == (1, 16000, 8000)


# ── a provider that can't run now says why, and is not called ──────────────────────────────


async def test_an_unavailable_provider_says_why_instead_of_being_called(registered):
    class _Down(_Ears):
        async def is_available(self) -> bool:
            return False

        async def unavailable_reason(self) -> str:
            return "The speech engine isn't installed. Install it from Apps."

    ears = registered(stt_registry, _Down())

    _, body = await _test("stt", "lab-ears:whisper-small")

    assert (body["ok"], body["detail"], body["reason"]) == (
        False,
        "The speech engine isn't installed. Install it from Apps.",
        "unavailable",
    )
    assert ears.clips == []


async def test_a_local_runtime_that_cannot_run_answers_in_its_own_masked_words(registered):
    from personalclaw.local_models.provider import LocalModelProvider

    class _LocalEars(_Ears, LocalModelProvider):
        async def availability_detail(self) -> tuple[bool, str]:
            return False, "could not reach https://ada:hunter2-secret@runtime.example.com/v1"

        async def list_models(self):
            return []

        async def download_model(self, model_name: str) -> bool:
            return False

        async def delete_model(self, model_name: str) -> bool:
            return False

    registered(stt_registry, _LocalEars())

    _, body = await _test("stt", "lab-ears:whisper-small")

    assert body["ok"] is False and body["reason"] == "unavailable"
    assert "hunter2-secret" not in body["detail"]
    assert body["detail"].startswith("could not reach https://")


async def test_availability_detail_default_reports_ready_and_unavailable():
    from personalclaw.local_models.provider import LocalModelProvider

    class _Local(LocalModelProvider):
        up = True

        @property
        def name(self) -> str:
            return "local-fake"

        @property
        def display_name(self) -> str:
            return "Local fake"

        async def is_available(self) -> bool:
            if self.up is None:
                raise RuntimeError("torch import blew up")
            return self.up

        async def list_models(self):
            return []

        async def download_model(self, model_name: str) -> bool:
            return True

        async def delete_model(self, model_name: str) -> bool:
            return True

    local = _Local()
    assert await local.availability_detail() == (True, "ready")
    local.up = False
    ok, message = await local.availability_detail()
    assert ok is False and "not available" in message
    local.up = None
    ok, message = await local.availability_detail()
    assert ok is False and "RuntimeError" in message, "it never raises"


# ── what can't be tested says so ────────────────────────────────────────────────────────────


async def test_a_video_model_has_no_test_and_says_why(registered):
    registered(video_registry, _Video())

    status, body = await _test("video_gen", "lab-video:reel-1")

    assert status == 409
    assert body["error"]["code"] == "model_untestable"
    assert "whole video clip" in body["error"]["message"]


@pytest.mark.parametrize("use_case", ["audio_modality", "audio_gen", "video_modality"])
async def test_a_use_case_nothing_calls_a_model_for_has_no_test(chat_world, use_case):
    status, body = await _test(use_case, "lab:lab-large")

    assert status == 409
    assert body["error"]["message"].startswith("PersonalClaw doesn't ")
    assert chat_world["asked"] == []


async def test_a_provider_that_declares_it_cannot_be_tested_is_not_called(registered):
    class _Dear(_Images):
        def untestable_reason(self) -> str:
            return "Every image this service makes is billed at full price."

    images = registered(image_registry, _Dear("lab-images"))

    status, body = await _test("image_gen", "lab-images:canvas-1")

    assert (status, body["error"]["message"]) == (
        409,
        "Every image this service makes is billed at full price.",
    )
    assert images.asked == []


async def test_a_provider_that_is_not_set_up_has_no_test():
    status, body = await _test("image_gen", "nowhere:canvas-1")

    assert status == 409
    assert body["error"]["message"] == (
        "nowhere isn't set up for image generation here, so its models can't be called."
    )


async def test_every_use_case_has_a_test_or_says_why_not():
    """Total over the vocabulary: a use case added without a Test or a sentence reds here."""
    from personalclaw.providers import model_test
    from personalclaw.providers.use_cases import CAPABILITIES

    untested = [
        c for c in CAPABILITIES if c not in model_test._PROBES and c not in model_test._NO_TEST
    ]
    assert untested == []
    assert not set(model_test._PROBES) & set(model_test._NO_TEST)


# ── the row says it before the click ────────────────────────────────────────────────────────


async def test_the_models_list_marks_only_the_rows_whose_test_cannot_run(registered):
    from personalclaw.providers.model_test import mark_untestable

    registered(image_registry, _Images("lab-images"))
    registered(video_registry, _Video())
    rows = [
        {
            "name": "lab-images",
            "type": "image_gen",
            "models": [
                {"id": "canvas-1", "provider": "lab-images", "capabilities": ["image_gen"]},
            ],
        },
        {
            "name": "lab-video",
            "type": "video_gen",
            "models": [
                {"id": "reel-1", "provider": "lab-video", "capabilities": ["video_gen"]},
            ],
        },
    ]

    mark_untestable(rows)

    assert "untestable" not in rows[0]["models"][0]
    assert set(rows[1]["models"][0]["untestable"]) == {"video_gen"}


async def test_the_available_models_read_carries_the_marks(registered, monkeypatch):
    from personalclaw.dashboard.handlers import model_registry

    registered(video_registry, _Video())
    monkeypatch.setattr(model_registry, "_get_providers_from_config", lambda: [])
    host = SimpleNamespace(
        total_ram_bytes=0, unified_memory=False, gpu_model="", memory_measured=False
    )
    monkeypatch.setattr(model_registry, "_fit_probe", lambda: (host, None, False))
    request = make_mocked_request("GET", "/api/models/available", app=web.Application())

    response = await model_registry.api_models_available(request)

    rows = json.loads(response.body.decode())["providers"]
    (video,) = [r for r in rows if r["name"] == "lab-video"]
    assert "whole video clip" in video["models"][0]["untestable"]["video_gen"]


# ── bounds ──────────────────────────────────────────────────────────────────────────────────


async def test_a_test_that_runs_past_its_bound_is_stopped_and_says_so(registered, monkeypatch):
    from personalclaw.providers import model_test

    class _Slow(_Ears):
        async def transcribe(self, audio_path, model="", language=""):
            await asyncio.sleep(5)
            return "too late"

    registered(stt_registry, _Slow())
    monkeypatch.setattr(model_test, "_timeout_s", lambda: 0.2)

    _, body = await _test("stt", "lab-ears:whisper-small")

    assert (body["ok"], body["detail"], body["reason"]) == (
        False,
        "No answer within 0.2 seconds.",
        "timeout",
    )


async def test_a_provider_that_cannot_be_reached_is_said_in_words_that_name_the_check(
    registered,
):
    """An HTTP client's words for a refused connection ("All connection attempts failed") say
    neither what happened nor what to check, so the refusal found in its cause chain is said."""

    def _refused() -> Exception:
        try:
            try:
                raise ConnectionRefusedError(61, "Connect call failed")
            except ConnectionRefusedError as cause:
                raise OSError("All connection attempts failed") from cause
        except OSError as wrapped:
            return wrapped

    registered(stt_registry, _Ears(raises=_refused()))

    _, body = await _test("stt", "lab-ears:whisper-small")

    assert body["detail"].startswith("Could not reach that endpoint — the connection was refused.")
    assert body["reason"] == "error:OSError"


async def test_a_providers_own_timeout_is_its_failure_not_the_tests_bound(registered):
    registered(stt_registry, _Ears(raises=TimeoutError("The service took too long to answer.")))

    _, body = await _test("stt", "lab-ears:whisper-small")

    assert (body["detail"], body["reason"]) == (
        "The service took too long to answer.",
        "error:TimeoutError",
    )


async def test_a_second_test_of_the_same_provider_waits_its_turn(registered):
    from personalclaw.concurrency import single_flight

    registered(stt_registry, _Ears())

    with single_flight("model-test:lab-ears") as held:
        assert held
        status, body = await _test("stt", "lab-ears:whisper-small")

    assert status == 409
    assert body["error"]["code"] == "model_test_running"


@pytest.mark.parametrize(
    ("use_case", "ref"),
    [("nope", "lab:x"), ("chat", "lab-only"), ("chat", "lab:"), ("chat", ":x")],
    ids=["unknown-use-case", "no-colon", "no-model", "no-provider"],
)
async def test_a_request_that_names_no_model_or_use_case_is_refused(use_case, ref):
    status, body = await _test(use_case, ref)

    assert status == 400
    assert body["error"]["code"] == "invalid_request"


# ── the local-only route is gone ────────────────────────────────────────────────────────────


async def test_the_local_only_test_and_health_routes_are_gone():
    from personalclaw.dashboard.handlers.model_downloads import register_model_download_routes
    from personalclaw.dashboard.handlers.model_registry import register_model_registry_routes

    app = web.Application()
    register_model_download_routes(app)
    register_model_registry_routes(app)
    routes = {(r.method, r.resource.canonical) for r in app.router.routes()}

    assert ("POST", "/api/models/test") in routes
    assert (
        not {
            ("POST", "/api/models/local/{provider}/selftest"),
            ("GET", "/api/models/local/{provider}/health"),
        }
        & routes
    )

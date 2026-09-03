"""MI-2a — the cloning-capable TTS capability surface.

Three contracts, no torch and no model spend anywhere:

1. ``supports_cloning`` / ``supports_voice_design`` exist on BOTH the per-model
   :class:`CapabilityMatrix` and the :class:`TtsProvider` ABC, default False, and are
   declarable True (so the bundled piper/OpenAI backends stay valid untouched while a
   cloning engine opts in).
2. A clone-kind synth request (one carrying a reference clip) routed to a provider that
   does not declare cloning is refused with a typed HTTP 409 ``cloning_unsupported:<provider>``
   — fail-closed, provider never invoked.
3. A cloning-capable STUB receives the reference clip and renders through
   :func:`route_synthesis`, asserting the clone contract end to end.
"""

import pytest

from personalclaw.local_models.provider import CapabilityMatrix, LocalModel, _matrix_from_dict
from personalclaw.tts.provider import TtsProvider
from personalclaw.tts.registry import (
    CloningUnsupportedError,
    guard_synthesis_capability,
    is_clone_request,
    route_synthesis,
)


class _StubTts(TtsProvider):
    """A zero-cost TTS backend that records what it was asked to synthesize.

    Non-cloning by default (inherits ``supports_cloning = False``), so it stands in for
    piper/OpenAI in the refusal path without importing either.
    """

    def __init__(self, name: str = "stub"):
        self._name = name
        self.calls: list[dict] = []

    @property
    def name(self) -> str:
        return self._name

    @property
    def display_name(self) -> str:
        return self._name

    async def is_available(self) -> bool:
        return True

    async def synthesize(self, text, voice="", output_path="", *, speed=1.0, **opts):
        self.calls.append({"text": text, "voice": voice, "speed": speed, **opts})
        return output_path or "/tmp/stub-out.wav"


class _CloningTts(_StubTts):
    """A backend that DECLARES cloning — the fixture the clone selftest renders through."""

    supports_cloning = True


# ── Contract 1: the flags default False and are declarable True ──────────────────────


def test_capability_matrix_cloning_flags_default_false_and_declarable():
    default = CapabilityMatrix()
    assert default.supports_cloning is False
    assert default.supports_voice_design is False

    declared = CapabilityMatrix(supports_cloning=True, supports_voice_design=True)
    assert declared.supports_cloning is True
    assert declared.supports_voice_design is True

    # A model card declares the flags through the same fields() filter every other
    # capability uses; an absent flag stays False, not unknown.
    parsed = _matrix_from_dict({"supports_cloning": True})
    assert parsed.supports_cloning is True
    assert parsed.supports_voice_design is False

    # ...and they round-trip out onto the wire via LocalModel.to_dict().
    wire = LocalModel(name="voice-x", matrix=declared).to_dict()
    assert wire["matrix"]["supports_cloning"] is True
    assert wire["matrix"]["supports_voice_design"] is True


def test_tts_provider_capability_flags_default_false_and_declarable():
    # Declared on the ABC, so every provider inherits the fail-closed default.
    assert TtsProvider.supports_cloning is False
    assert TtsProvider.supports_voice_design is False
    assert _StubTts().supports_cloning is False
    assert _StubTts().supports_voice_design is False

    # A cloning engine opts in by overriding the class attribute.
    assert _CloningTts().supports_cloning is True

    # The bundled remote provider compiles unchanged and reads as non-cloning.
    from personalclaw.tts.openai_provider import OpenAITtsProvider

    remote = OpenAITtsProvider(provider_name="openai")
    assert remote.supports_cloning is False
    assert remote.supports_voice_design is False


# ── Contract 2: a clone request to a non-cloning provider is a typed HTTP 409 ────────


def test_is_clone_request_is_the_presence_of_a_reference_clip():
    assert is_clone_request({"ref_audio": "/abs/ref.wav"}) is True
    assert is_clone_request({"ref_audio": ""}) is False
    assert is_clone_request({}) is False
    # design kind (no reference) is NOT a clone request
    assert is_clone_request({"design_params": {"pitch": "low"}, "instruct": "calm"}) is False


@pytest.mark.asyncio
async def test_clone_request_to_noncloning_provider_raises_409():
    provider = _StubTts(name="piper")
    params = {"provider": provider, "voice": "en_US", "speed": 1.0, "ref_audio": "/abs/ref.wav"}

    with pytest.raises(CloningUnsupportedError) as excinfo:
        await route_synthesis(params, "hello")

    err = excinfo.value
    assert err.status == 409
    assert err.reason == "cloning_unsupported"
    assert err.provider == "piper"
    assert str(err) == "cloning_unsupported:piper"
    # fail-closed: the engine was never asked to synthesize the wrong-voice audio.
    assert provider.calls == []


def test_guard_refuses_a_clone_request_on_a_noncloning_provider():
    with pytest.raises(CloningUnsupportedError) as excinfo:
        guard_synthesis_capability(_StubTts(name="piper"), {"ref_audio": "/abs/ref.wav"})
    assert excinfo.value.status == 409
    assert excinfo.value.provider == "piper"


def test_guard_allows_a_clone_request_on_a_cloning_provider():
    # A cloning-capable provider passes the gate (no raise); a non-clone request always does.
    guard_synthesis_capability(_CloningTts(name="xtts"), {"ref_audio": "/abs/ref.wav"})
    guard_synthesis_capability(_StubTts(name="piper"), {"voice": "en_US"})


@pytest.mark.asyncio
async def test_nonclone_request_is_allowed_on_a_noncloning_provider():
    provider = _StubTts(name="piper")
    params = {"provider": provider, "voice": "en_US", "speed": 1.1}  # no ref_audio

    out = await route_synthesis(params, "hi")

    assert out == "/tmp/stub-out.wav"
    assert len(provider.calls) == 1
    assert provider.calls[0]["voice"] == "en_US"


# ── Contract 3: the fixture clone selftest — render through a reference, zero spend ──


@pytest.mark.asyncio
async def test_fixture_selftest_synthesizes_through_a_clone_reference():
    provider = _CloningTts(name="fixture-clone")
    params = {
        "provider": provider,
        "voice": "vp-abc",
        "speed": 1.0,
        "ref_audio": "/abs/ref.wav",
        "ref_text": "the quick brown fox",
        "seed": 7,
        "instruct": "warm",
        "design_params": {"style": "narration"},
    }
    assert is_clone_request(params)

    out = await route_synthesis(params, "hello world", output_path="/tmp/clone.wav")

    assert out == "/tmp/clone.wav"
    assert len(provider.calls) == 1
    call = provider.calls[0]
    # The reference clip and its conditioning actually reached the engine — the whole
    # point of routing a clone request to a cloning-capable backend.
    assert call["text"] == "hello world"
    assert call["voice"] == "vp-abc"
    assert call["ref_audio"] == "/abs/ref.wav"
    assert call["ref_text"] == "the quick brown fox"
    assert call["seed"] == 7
    assert call["instruct"] == "warm"
    assert call["design_params"] == {"style": "narration"}

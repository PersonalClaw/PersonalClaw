"""A speech, embedding or image engine that runs on this machine is free, by the one local rule.

Model prices listed the local speech engines a fresh install binds (Faster Whisper, Piper, the
ONNX diarization engine) as "No price: … a daily dollar cap refuses them", beside Ollama models
reading "$0 … free: it runs on this machine". The rule that prices a model free when it runs here
(``llm.registry.served_on_this_machine``) knew only configured model-provider entries, and an
engine an app registers is none: its binding names the app (``faster-whisper:turbo``).

The rule now answers for an engine from what the engine declares, never from its name or its kind:

* a local-model engine (``LocalModelProvider``: its models are downloaded into this home and run
  here) that sends its requests nowhere runs here;
* an engine that serves its model through a runtime at an address (``endpoint``) runs here only
  when it declares that the runtime runs the model where it is (``hosts_model``, as a model server
  such as ComfyUI does) and that address is on this machine. An address elsewhere is not here, and
  an address here that only passes requests on (a proxy for a cloud service) is not either;
* anything else, including a name nothing registered, is not local.
"""

from __future__ import annotations

from typing import Any

import pytest

import personalclaw.sdk.model  # noqa: F401 — package import order (sdk.model first)
from personalclaw.guardrails.budgets import Budget, SpendMeter
from personalclaw.guardrails.model_call import call_cost
from personalclaw.image_gen import registry as image_registry
from personalclaw.image_gen.provider import ImageGenProvider
from personalclaw.llm.registry import served_on_this_machine
from personalclaw.local_models import registry as local_registry
from personalclaw.local_models.provider import LocalModelProvider
from personalclaw.routing import rates as rates_mod
from personalclaw.routing.policy import is_local_ref
from personalclaw.routing.rates import ModelRate, rate_for, rates_view
from personalclaw.stt import registry as stt_registry
from personalclaw.stt.provider import SttProvider


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    rates_mod._overlay_cache = None
    held = [(reg._providers, dict(reg._providers)) for reg in (image_registry, stt_registry)]
    yield tmp_path
    rates_mod._overlay_cache = None
    for providers, before in held:
        providers.clear()
        providers.update(before)


class _Weights(LocalModelProvider):
    """The management half every local-model engine implements."""

    def __init__(self, name: str) -> None:
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    @property
    def display_name(self) -> str:
        return self._name

    async def is_available(self) -> bool:
        return True

    async def list_models(self) -> list:
        return []

    async def download_model(self, model_name: str) -> bool:
        return False

    async def delete_model(self, model_name: str) -> bool:
        return False


class _LocalTranscriber(_Weights, SttProvider):
    """A speech-to-text engine whose weights live in this home, as Faster Whisper's do."""

    async def transcribe(self, audio_path: str, model: str = "", language: str = "") -> str:
        return ""


class _Painter(ImageGenProvider):
    """An image engine that sends each request to a runtime at an address."""

    def __init__(self, name: str, endpoint: str = "", *, hosts_model: bool = False) -> None:
        self._name = name
        self._endpoint = endpoint
        self.hosts_model = hosts_model

    @property
    def name(self) -> str:
        return self._name

    @property
    def display_name(self) -> str:
        return self._name

    @property
    def endpoint(self) -> str:
        return self._endpoint

    async def is_available(self) -> bool:
        return True

    async def list_models(self) -> list:
        return []

    async def generate(self, prompt: str, **opts: Any) -> list:
        return []

    async def edit(self, prompt: str, **opts: Any) -> list:
        return []


def _register_local_engine(app: str, engine: Any, caps: list[str]) -> None:
    """What the app loader does for a ``type: model`` app whose engine manages local models."""
    local_registry.register_provider(engine, capabilities=caps, name=app)
    if "stt" in caps:
        stt_registry.register_provider(engine)


@pytest.mark.parametrize(
    ("app", "model", "caps"),
    [
        ("faster-whisper", "turbo", ["stt"]),
        ("piper-tts", "en_US-lessac-medium", ["tts"]),
        ("diarization-onnx", "sherpa-onnx-pyannote-segmentation-3.0", ["diarization"]),
        ("sentence-transformers", "all-MiniLM-L6-v2", ["embedding"]),
    ],
)
def test_a_local_model_engine_is_priced_at_its_known_zero(app, model, caps, tmp_path):
    engine = _LocalTranscriber(app.replace("-", "_")) if caps == ["stt"] else _Weights(app)
    _register_local_engine(app, engine, caps)

    rate = rate_for(app, model, home=tmp_path)

    assert rate == ModelRate(0.0, 0.0) and rate is not None and rate.source == "local"
    assert served_on_this_machine(app, model) is True
    assert is_local_ref(f"{app}:{model}") is True
    (row,) = rates_view([(app, model)], home=tmp_path)["models"]
    assert (row["priced"], row["source"]) == (True, "local")


def test_a_local_engine_is_not_refused_by_a_dollar_cap(tmp_path):
    _register_local_engine("faster-whisper", _LocalTranscriber("faster_whisper"), ["stt"])
    meter = SpendMeter(config_dir=tmp_path)

    verdict = meter.admit(
        call_cost("faster-whisper", "turbo", prompt_chars=400), day=Budget(max_dollars=4.0)
    )

    assert verdict is None, "a model known to cost nothing is not weighed by a dollar cap"


def test_an_image_runtime_on_this_machine_that_runs_its_model_there_is_free(tmp_path):
    image_registry.register_provider(
        _Painter("local-image", "http://127.0.0.1:8188", hosts_model=True)
    )

    rate = rate_for("local-image", "flux1-schnell.safetensors", home=tmp_path)

    assert rate is not None and rate.source == "local"
    assert served_on_this_machine("local-image", "flux1-schnell.safetensors") is True


@pytest.mark.parametrize(
    ("endpoint", "hosts_model"),
    [
        ("http://192.0.2.7:8188", True),  # the same runtime on another machine
        ("http://images.example.com", True),  # a name, which may point anywhere
        ("http://127.0.0.1:4000", False),  # an address here that passes requests on
        ("", False),  # a cloud engine that names no address
    ],
)
def test_an_engine_is_not_free_by_its_kind(endpoint, hosts_model, tmp_path):
    image_registry.register_provider(_Painter("painter", endpoint, hosts_model=hosts_model))

    assert served_on_this_machine("painter", "studio-xl") is False
    assert rate_for("painter", "studio-xl", home=tmp_path) is None


def test_a_local_model_engine_that_sends_to_an_address_elsewhere_is_not_free(tmp_path):
    class _RemoteWeights(_LocalTranscriber):
        endpoint = "http://192.0.2.9:9000"
        hosts_model = True

    _register_local_engine("whisper-box", _RemoteWeights("whisper_box"), ["stt"])

    assert served_on_this_machine("whisper-box", "turbo") is False
    assert rate_for("whisper-box", "turbo", home=tmp_path) is None


def test_a_name_nothing_registered_is_not_local(tmp_path):
    assert served_on_this_machine("nothing-by-this-name", "turbo") is False
    assert rate_for("nothing-by-this-name", "turbo", home=tmp_path) is None


def test_a_model_servers_download_card_never_makes_its_name_local():
    """Core gives a configured model server (Ollama) a download card in the local-model registry.
    Its models live wherever that server is: the entry answers for it, and once the entry is gone
    the card left behind says nothing about where a model runs."""
    from personalclaw.apps.native_contract import NATIVE_DIR, load_bundle_module
    from personalclaw.llm.registry import ProviderEntry, get_default_registry
    from personalclaw.providers.engines import runs_here

    load_bundle_module(NATIVE_DIR / "ollama-models", "ollama-models", "provider")
    registry = get_default_registry()
    for name, url in (("studio", "http://192.0.2.4:11434"), ("desk", "http://127.0.0.1:11434")):
        registry.register_entry(
            ProviderEntry(name=name, type="ollama", model="", options={"endpoint": url})
        )
    local_registry.register_config_model_managers()
    assert local_registry.get_provider("desk") is not None, "premise: the card is registered"

    assert served_on_this_machine("desk", "llama3.1") is True
    assert served_on_this_machine("studio", "llama3.1") is False
    assert runs_here(local_registry.get_provider("desk")) is False

    registry.unregister_entry("desk")
    assert served_on_this_machine("desk", "llama3.1") is False


def test_an_unattended_transcription_on_this_machine_runs_under_a_dollar_cap_at_nothing(
    tmp_path, monkeypatch
):
    """The seam every transcription goes through prices a local engine at $0, so a knowledge
    import's transcription is not refused by a daily dollar cap, and Usage counts its minutes
    under the name its binding spells (the app's), not the engine's own."""
    import asyncio
    import json

    from personalclaw import transcribe

    engine = _LocalTranscriber("faster_whisper")
    _register_local_engine("faster-whisper", engine, ["stt"])
    (tmp_path / "config.json").write_text(
        json.dumps({"guardrails": {"budgets": {"max_dollars_per_day": 4.0}}}), encoding="utf-8"
    )
    audio = tmp_path / "talk.wav"
    audio.write_bytes(b"RIFF")
    monkeypatch.setattr(transcribe, "_resolve", lambda path: (engine, "turbo", ""))
    monkeypatch.setattr(transcribe, "audio_seconds", lambda path: 600.0)

    result = asyncio.run(transcribe.transcribe_audio_detailed(str(audio), unattended=True))

    assert result.text == ""
    (row,) = [
        json.loads(line)
        for line in (tmp_path / "usage" / "turns.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert (row["provider"], row["unit"], row["quantity"], row["cost_usd"], row["local"]) == (
        "faster-whisper",
        "minute",
        10.0,
        0.0,
        True,
    )

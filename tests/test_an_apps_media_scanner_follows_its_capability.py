"""A media scanner an app registers as it loads acts only while its capability runs (#3843).

A model app registers its media scanners when its module is imported (Amazon Bedrock registers
``embedding``, ``image_gen``, ``video_gen`` and ``stt`` scanners), so they used to act whatever
its switches said: a capability switched off still built image and embedding adapters. A scanner
the app's code registered belongs to the app's capabilities that declare its kind (a provider's
``capabilities``), and acts while one of them is running. A kind no capability declares is the
app's own and acts while the app is on. A scanner core registers acts always.

The embedding registry keeps the adapters it built until the scanners change, so a switch counts
as a change: the next resolution builds from what acts now.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from aiohttp.test_utils import TestClient, TestServer
from test_each_capability_has_its_own_switch import _gateway

from personalclaw import app_code
from personalclaw.apps import app_manager, manager
from personalclaw.embedding_providers import registry as embeddings
from personalclaw.providers import media_scanners
from personalclaw.providers import registry as registry_module
from personalclaw.stt import registry as speech
from personalclaw.tts import registry as voices

pytestmark = pytest.mark.security

APP = "media-kit"

_PROVIDER_PY = """
from personalclaw.sdk.embedding import EmbeddingProvider
from personalclaw.sdk.model import register_scanner


class _Adapter:
    def __init__(self, name):
        self.name = name


class _Vectors(EmbeddingProvider):
    @property
    def name(self):
        return "kit-vectors"

    @property
    def display_name(self):
        return "Media Kit embeddings"

    async def is_available(self):
        return True

    async def embed(self, text, model=""):
        return [1.0, 0.0]

    async def embed_batch(self, texts, model=""):
        return [await self.embed(text, model) for text in texts]


def _pictures(entries):
    return [_Adapter("media-kit:pictures")]


def _vectors(entries):
    return [_Vectors()]


def _film(entries):
    return [_Adapter("media-kit:film")]


def _ears(entries):
    return [_Adapter("media-kit:ears")]


def _voice(entries):
    return [_Adapter("media-kit:voice")]


register_scanner("image_gen", _pictures)
register_scanner("embedding", _vectors)
register_scanner("video_gen", _film)
register_scanner("stt", _ears)
register_scanner("tts", _voice)


def create(config):
    return None


def _sketches(entries):
    return [_Adapter("media-kit:sketch")]


def create_sketch(config):
    register_scanner("image_gen", _sketches)
    return None
"""


class _Models:
    """A stand-in model handler: runs the capability's factory as the real one does (which
    imports the app's module), builds a placeholder, and a capability named in ``fail`` raises
    at build."""

    def __init__(self) -> None:
        self.fail: dict[str, str] = {}

    def create(self, ext: Any) -> Any:
        from personalclaw.providers.loader import load_factory

        load_factory(ext)({})
        if ext.capability in self.fail:
            raise RuntimeError(self.fail[ext.capability])
        return object()

    def register(self, ext: Any, instance: Any) -> None:
        return None

    def deregister(self, ext: Any, instance: Any) -> None:
        return None


def _model(id_: str, label: str, kinds: list[str]) -> dict[str, Any]:
    return {
        "type": "model",
        "implementation": "provider:create",
        "id": id_,
        "label": label,
        "providerType": APP,
        "capabilities": kinds,
    }


CAPABILITIES = [
    _model("talk", "Talk with the model", ["chat", "embedding", "stt", "tts"]),
    _model("images", "Make pictures", ["image_gen"]),
]


@pytest.fixture
def models(tmp_path, monkeypatch) -> _Models:
    import personalclaw.config.loader as loader

    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(manager, "config_dir", lambda: tmp_path)
    handler = _Models()
    reg = registry_module.ProviderRegistry()
    reg.register_type_handler("model", handler)  # type: ignore[arg-type]
    monkeypatch.setattr(registry_module, "_registry", reg)
    monkeypatch.setattr(media_scanners, "_scanners", {})
    monkeypatch.setattr(media_scanners, "_config_provider_entries", lambda: [])
    monkeypatch.setattr(embeddings, "_providers", {})
    monkeypatch.setattr(embeddings, "_scanned", {})
    monkeypatch.setattr(embeddings, "_scanned_generation", -1)
    monkeypatch.setattr(speech, "_providers", {})
    monkeypatch.setattr(speech, "_remote_names", set())
    monkeypatch.setattr(speech, "_scanned_names", set(), raising=False)
    monkeypatch.setattr(voices, "_providers", {})
    monkeypatch.setattr(voices, "_remote_names", set())
    monkeypatch.setattr(voices, "_scanned_names", set(), raising=False)
    monkeypatch.setattr("personalclaw.providers.use_cases.openai_family_providers", lambda: [])
    yield handler
    app_code.release(APP)


def _install(root: Path, providers: list[dict[str, Any]] | None = None) -> None:
    d = root / "bundle" / APP
    d.mkdir(parents=True)
    manifest = {
        "name": APP,
        "version": "1.0.0",
        "displayName": "Media Kit",
        "description": "A demo model app with pictures and embeddings.",
        "providers": providers or CAPABILITIES,
    }
    (d / "app.json").write_text(json.dumps(manifest), encoding="utf-8")
    (d / "provider.py").write_text(_PROVIDER_PY, encoding="utf-8")
    res = app_manager.install(d, confirm=True)
    assert res.ok, res.error


def _built(kind: str) -> set[str]:
    return {getattr(a, "name", "") for a in media_scanners.scan(kind)}


async def _switch(client: TestClient, capability: str, to: str) -> None:
    resp = await client.post(f"/api/apps/{APP}/capabilities/{capability}/{to}")
    assert resp.status == 200, await resp.text()


@pytest.mark.asyncio
async def test_a_media_kind_follows_the_capability_that_declares_it(tmp_path, models) -> None:
    _install(tmp_path)
    assert _built("image_gen") == {"media-kit:pictures"}
    async with TestClient(TestServer(_gateway())) as client:
        await _switch(client, "images", "off")
        assert _built("image_gen") == set(), "pictures is off: its scanner builds nothing"
        assert _built("embedding") == {"kit-vectors"}, "the other capability's kind still acts"
        await _switch(client, "images", "on")
        assert _built("image_gen") == {"media-kit:pictures"}


@pytest.mark.asyncio
async def test_the_embedding_registry_drops_an_adapter_whose_capability_went_off(
    tmp_path, models
) -> None:
    _install(tmp_path)
    assert embeddings.get_provider("kit-vectors") is not None
    async with TestClient(TestServer(_gateway())) as client:
        await _switch(client, "talk", "off")
        assert embeddings.get_provider("kit-vectors") is None, "a switch is a change"
        await _switch(client, "talk", "on")
        assert embeddings.get_provider("kit-vectors") is not None


@pytest.mark.asyncio
async def test_a_speech_adapter_leaves_when_its_capability_goes_off(tmp_path, models) -> None:
    """The speech registry kept every adapter a scanner once built: one leaves now when the app
    no longer builds it."""
    _install(tmp_path)
    assert speech.provider_named("media-kit:ears") is not None
    assert voices.provider_named("media-kit:voice") is not None
    async with TestClient(TestServer(_gateway())) as client:
        await _switch(client, "talk", "off")
        assert speech.provider_named("media-kit:ears") is None
        assert voices.provider_named("media-kit:voice") is None
        await _switch(client, "talk", "on")
        assert speech.provider_named("media-kit:ears") is not None
        assert voices.provider_named("media-kit:voice") is not None


@pytest.mark.asyncio
async def test_a_kind_no_capability_declares_is_the_apps_own(tmp_path, models) -> None:
    _install(tmp_path)
    async with TestClient(TestServer(_gateway())) as client:
        await _switch(client, "talk", "off")
        await _switch(client, "images", "off")
    assert _built("video_gen") == {"media-kit:film"}, "no capability declares it: the app's"
    app_manager.disable(APP)
    assert _built("video_gen") == set(), "and it leaves with the app"


def test_a_capability_that_failed_runs_none_of_its_kinds(tmp_path, models) -> None:
    models.fail["images"] = "the pictures service refused the key"
    _install(tmp_path)
    assert _built("image_gen") == set()
    assert _built("embedding") == {"kit-vectors"}


def test_a_scanner_core_registers_acts_whatever_the_switches_say(tmp_path, models) -> None:
    class _Core:
        name = "core:pictures"

    def core(entries: list[dict[str, Any]]) -> list[Any]:
        return [_Core()]

    media_scanners.register_scanner("image_gen", core)
    assert _built("image_gen") == {"core:pictures"}


def test_a_capability_state_that_cannot_be_read_acts_on_nothing(
    tmp_path, models, monkeypatch
) -> None:
    """As switches that cannot be read run nothing: the app's scanner sits out."""
    from personalclaw.apps import capability_switches

    _install(tmp_path)

    def unreadable(*_: Any) -> bool:
        raise OSError("installed.json could not be read")

    monkeypatch.setattr(capability_switches, "registration_acts", unreadable)
    assert _built("image_gen") == set()


def test_a_capability_torn_down_outside_the_switch_route_is_a_change(
    tmp_path, models, monkeypatch
) -> None:
    """A capability can stop running without the switch route, which refreshes the media
    registries itself (a boot that builds it late, a retire, a rebuild that fails): the embedding
    bound through its app stops embedding, and its speech adapters leave, all the same."""
    monkeypatch.setattr(
        embeddings, "_active_embedding_spec", lambda: ("kit-vectors", "probe-model")
    )
    _install(tmp_path)
    bound = embeddings.BoundEmbedding()
    fn, _ref = bound.current()
    assert fn is not None and fn("a heron") == [1.0, 0.0]
    assert speech.provider_named("media-kit:ears") is not None
    assert voices.provider_named("media-kit:voice") is not None

    registry_module.get_provider_registry().disable(APP, "talk")
    assert bound.current()[0] is None, "a teardown is a change: the bound embedding rebuilds"
    assert speech.provider_named("media-kit:ears") is None
    assert voices.provider_named("media-kit:voice") is None

    registry_module.get_provider_registry().enable(APP, "talk")
    fn, _ref = bound.current()
    assert fn is not None and fn("a heron") == [1.0, 0.0], "and a build is one too"


#: A capability that declares no media kind but whose build registers an image scanner.
SKETCH = _model("sketch", "Sketch with the model", ["vision"]) | {
    "implementation": "provider:create_sketch"
}


def test_a_scanner_a_build_registered_follows_that_capability_not_the_kinds(
    tmp_path, models, monkeypatch
) -> None:
    """A scanner a capability's build registered is that capability's, beside another that
    declares its kind: it acts while its own capability runs, whatever the declaring one does.
    The capabilities are held by the availability check, since a teardown would take the build's
    scanner back."""
    from personalclaw.apps import capability_switches

    _install(tmp_path, [*CAPABILITIES, SKETCH])
    assert _built("image_gen") == {"media-kit:pictures", "media-kit:sketch"}
    cannot_run: set[str] = {"sketch"}
    monkeypatch.setattr(
        capability_switches,
        "_unavailable",
        lambda rec: "it needs a graphics card here" if rec.capability in cannot_run else "",
    )
    assert _built("image_gen") == {
        "media-kit:pictures"
    }, "sketch is held: images running is not its"
    cannot_run.clear()
    cannot_run.add("images")
    assert _built("image_gen") == {"media-kit:sketch"}, "images is held, sketch runs: sketch's acts"


def test_a_hand_registration_after_an_unregister_stays_when_the_scanner_stops(
    tmp_path, models
) -> None:
    """The speech registries drop a scanner's adapter once its app stops building it, and only
    that: one registered by hand under the same name after the scanner's was unregistered is not
    the scanner's."""

    class _Hand:
        def __init__(self, name: str) -> None:
            self.name = name

    _install(tmp_path)
    assert speech.provider_named("media-kit:ears") is not None
    assert voices.provider_named("media-kit:voice") is not None
    ears, voice = _Hand("media-kit:ears"), _Hand("media-kit:voice")
    speech.unregister_provider("media-kit:ears")
    speech.register_provider(ears)  # type: ignore[arg-type]
    voices.unregister_provider("media-kit:voice")
    voices.register_provider(voice)  # type: ignore[arg-type]
    registry_module.get_provider_registry().disable(APP, "talk")
    assert speech.provider_named("media-kit:ears") is ears
    assert voices.provider_named("media-kit:voice") is voice

"""Image-generation capability — ABC contract, registry resolution, OpenAI adapter.

Mirrors tests/test_remote_audio_providers.py (the STT/TTS template): a remote
image model selected in active_models.json resolves through the typed registry,
which builds one adapter per OpenAI-family config provider keyed by config name.
The ABC's load-bearing deviation is async generate()/edit() so a provider can hide
its own poll loop — covered by a fake async-queue provider here.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from personalclaw.image_gen.provider import (
    ImageGenError,
    ImageGenModel,
    ImageGenProvider,
    ImageResult,
)


# ── A fake provider that proves the async-internally (submit->poll) contract ──
class _FakePollProvider(ImageGenProvider):
    """Hides an internal poll loop behind async generate() — the FAL shape."""

    def __init__(self) -> None:
        self.polls = 0

    @property
    def name(self) -> str:
        return "fake"

    @property
    def display_name(self) -> str:
        return "Fake"

    async def is_available(self) -> bool:
        return True

    async def list_models(self) -> list[ImageGenModel]:
        return [ImageGenModel(name="fake-1", sizes=["1024x1024"], supports_edit=True)]

    async def generate(self, prompt, *, model="", size="", n=1, **opts):
        # simulate submit -> poll-until-ready, fully hidden from the caller
        for _ in range(3):
            self.polls += 1
            await asyncio.sleep(0)
        return [ImageResult(local_path=f"/cache/{prompt[:4]}.png", mime="image/png") for _ in range(n)]

    async def edit(self, prompt, *, source_image, mask="", model="", size="", n=1, **opts):
        self.polls += 1
        return [ImageResult(local_path="/cache/edited.png", mime="image/png", revised_prompt=prompt)]


class TestImageGenABC:
    @pytest.mark.asyncio
    async def test_async_generate_hides_poll_loop(self):
        prov = _FakePollProvider()
        out = await prov.generate("a red cube", n=2)
        assert len(out) == 2
        assert prov.polls == 3  # the loop ran internally; caller never saw it
        assert all(r.local_path for r in out)

    @pytest.mark.asyncio
    async def test_edit_returns_revised_prompt(self):
        prov = _FakePollProvider()
        out = await prov.edit("make it blue", source_image="/tmp/x.png")
        assert out[0].revised_prompt == "make it blue"

    @pytest.mark.asyncio
    async def test_default_local_lifecycle_is_noop(self):
        prov = _FakePollProvider()
        assert await prov.download_model("x") is False
        assert await prov.delete_model("x") is False


# ── Registry resolves a remote selection (STT-pattern) ──
class TestImageGenRegistry:
    @pytest.fixture(autouse=True)
    def _reset_registry(self, monkeypatch):
        """Isolate the image-gen registry's MODULE-GLOBAL state so full-suite ordering
        can't leak in. Resetting only _providers isn't enough: _ensure_registered()
        early-returns on the _auto_registered flag, so if an earlier test set it, the
        mocked providers never get registered and _providers stays empty (the flaky
        full-suite failure). Reset BOTH."""
        from personalclaw.image_gen import registry as ir
        monkeypatch.setattr(ir, "_providers", {}, raising=False)
        monkeypatch.setattr(ir, "_auto_registered", False, raising=False)

    def test_active_image_gen_resolves_remote(self, monkeypatch):
        from personalclaw.image_gen import registry as ir
        from personalclaw.providers import use_cases as uc

        monkeypatch.setattr(ir, "_providers", {}, raising=False)
        monkeypatch.setattr(
            uc, "openai_family_providers",
            lambda: [{"name": "MyOpenAI", "endpoint": "", "api_key": "sk-x"}],
        )
        monkeypatch.setattr(
            uc, "active_model_refs",
            lambda u: ["MyOpenAI:gpt-image-1"] if u == "image_gen" else [],
        )

        resolved = ir.active_image_gen()
        assert resolved is not None
        provider, model_id = resolved
        assert provider.name == "MyOpenAI"
        assert model_id == "gpt-image-1"

    def test_no_selection_resolves_none(self, monkeypatch):
        from personalclaw.image_gen import registry as ir
        from personalclaw.providers import use_cases as uc
        monkeypatch.setattr(ir, "_providers", {}, raising=False)
        monkeypatch.setattr(uc, "openai_family_providers", lambda: [])
        monkeypatch.setattr(uc, "active_model_refs", lambda u: [])
        assert ir.active_image_gen() is None

    def test_refresh_drops_transient_keeps_bundle(self, monkeypatch):
        """refresh_providers() drops the auto-registered remote (by config name) +
        stub adapters so a config change re-reads, but PRESERVES a manifest-
        contributed bundle (FAL via ModelTypeHandler) — clearing it would orphan an
        enabled provider the lifecycle, not refresh, owns."""
        from personalclaw.image_gen import registry as ir
        from personalclaw.providers import use_cases as uc

        monkeypatch.setattr(uc, "openai_family_providers", lambda: [{"name": "MyOpenAI", "endpoint": "", "api_key": "k"}])
        monkeypatch.setattr(
            ir, "_providers",
            {"MyOpenAI": object(), "stub": object(), "fal": object()},
            raising=False,
        )
        monkeypatch.setattr(ir, "_auto_registered", True, raising=False)
        ir.refresh_providers()
        # remote (by config name) + stub gone; the bundle provider stays
        assert "MyOpenAI" not in ir._providers
        assert "stub" not in ir._providers
        assert "fal" in ir._providers
        # re-armed so the next resolution rebuilds the transient set
        assert ir._auto_registered is False

    @pytest.mark.asyncio
    async def test_list_models_for_provider_shape(self, monkeypatch):
        from personalclaw.image_gen import registry as ir
        prov = _FakePollProvider()
        monkeypatch.setattr(ir, "_providers", {"fake": prov}, raising=False)
        monkeypatch.setattr(ir, "_ensure_registered", lambda: None)
        models = await ir.list_models_for_provider("fake")
        assert models and models[0]["name"] == "fake-1"
        assert models[0]["sizes"] == ["1024x1024"]
        assert models[0]["supports_edit"] is True


class TestModelTypeHandlerImageGen:
    """The extension lifecycle wiring: a model-type manifest declaring the
    'image_gen' capability registers/unregisters its provider in the image_gen
    registry on enable/disable — the single source of truth for resolution."""

    def _ext(self):
        from personalclaw.providers.registry import RegisteredProvider

        class _Cfg:
            capabilities = ["image_gen"]
        # RegisteredProvider needs a provider_config with .capabilities; a tiny
        # stand-in keeps the test independent of the full manifest schema.
        rp = RegisteredProvider.__new__(RegisteredProvider)
        rp.name = "fake-img"
        rp.provider_config = _Cfg()
        return rp

    def test_register_adds_to_image_gen_registry(self, monkeypatch):
        from personalclaw.image_gen import registry as ir
        from personalclaw.providers.registry import ModelTypeHandler

        monkeypatch.setattr(ir, "_providers", {}, raising=False)
        prov = _FakePollProvider()
        ModelTypeHandler().register(self._ext(), prov)
        assert ir.get_provider("fake") is prov  # _FakePollProvider.name == "fake"

    def test_deregister_removes_from_image_gen_registry(self, monkeypatch):
        from personalclaw.image_gen import registry as ir
        from personalclaw.providers.registry import ModelTypeHandler

        prov = _FakePollProvider()
        monkeypatch.setattr(ir, "_providers", {"fake": prov}, raising=False)
        ModelTypeHandler().deregister(self._ext(), prov)
        assert ir.get_provider("fake") is None


# ── OpenAI adapter behavior ──
class TestOpenAIImageProvider:
    @pytest.mark.asyncio
    async def test_unavailable_without_key(self):
        from personalclaw.image_gen.openai_provider import OpenAIImageProvider
        prov = OpenAIImageProvider(provider_name="X", endpoint="", api_key="")
        with patch.dict("os.environ", {}, clear=True):
            assert await prov.is_available() is False

    @pytest.mark.asyncio
    async def test_generate_parses_b64(self):
        from personalclaw.image_gen.openai_provider import OpenAIImageProvider

        item = MagicMock(b64_json="aGVsbG8=", url=None, revised_prompt="a tidy red cube")
        fake_client = MagicMock()
        fake_client.images.generate = AsyncMock(return_value=MagicMock(data=[item]))
        fake_client.close = AsyncMock()
        fake_openai = MagicMock()
        fake_openai.AsyncOpenAI = MagicMock(return_value=fake_client)

        prov = OpenAIImageProvider(provider_name="X", endpoint="", api_key="sk-x")
        with patch.dict("sys.modules", {"openai": fake_openai}):
            out = await prov.generate("a red cube", model="gpt-image-1", size="1024x1024")
        assert len(out) == 1
        assert out[0].b64 == "aGVsbG8="
        assert out[0].revised_prompt == "a tidy red cube"
        kwargs = fake_client.images.generate.call_args.kwargs
        assert kwargs["model"] == "gpt-image-1"
        assert kwargs["size"] == "1024x1024"

    @pytest.mark.asyncio
    async def test_generate_parses_url(self):
        from personalclaw.image_gen.openai_provider import OpenAIImageProvider

        item = MagicMock(b64_json=None, url="https://x/img.png", revised_prompt="")
        fake_client = MagicMock()
        fake_client.images.generate = AsyncMock(return_value=MagicMock(data=[item]))
        fake_client.close = AsyncMock()
        fake_openai = MagicMock()
        fake_openai.AsyncOpenAI = MagicMock(return_value=fake_client)

        prov = OpenAIImageProvider(provider_name="X", endpoint="", api_key="sk-x")
        with patch.dict("sys.modules", {"openai": fake_openai}):
            out = await prov.generate("a red cube", model="gpt-image-1")
        assert out[0].url == "https://x/img.png"

    @pytest.mark.asyncio
    async def test_edit_sends_source_image(self, tmp_path):
        from personalclaw.image_gen.openai_provider import OpenAIImageProvider

        src = tmp_path / "src.png"
        src.write_bytes(b"\x89PNG\r\n")
        item = MagicMock(b64_json="ZWRpdA==", url=None, revised_prompt="")
        fake_client = MagicMock()
        fake_client.images.edit = AsyncMock(return_value=MagicMock(data=[item]))
        fake_client.close = AsyncMock()
        fake_openai = MagicMock()
        fake_openai.AsyncOpenAI = MagicMock(return_value=fake_client)

        prov = OpenAIImageProvider(provider_name="X", endpoint="", api_key="sk-x")
        with patch.dict("sys.modules", {"openai": fake_openai}):
            out = await prov.edit("make it blue", source_image=str(src), model="gpt-image-1")
        assert out[0].b64 == "ZWRpdA=="
        assert "image" in fake_client.images.edit.call_args.kwargs

    @pytest.mark.asyncio
    async def test_no_key_raises_clean_error(self):
        from personalclaw.image_gen.openai_provider import OpenAIImageProvider
        fake_openai = MagicMock()
        fake_openai.AsyncOpenAI = MagicMock()
        prov = OpenAIImageProvider(provider_name="X", endpoint="", api_key="")
        with patch.dict("sys.modules", {"openai": fake_openai}), patch.dict("os.environ", {}, clear=True):
            with pytest.raises(ImageGenError):
                await prov.generate("x")

    @pytest.mark.asyncio
    async def test_empty_response_raises(self):
        from personalclaw.image_gen.openai_provider import OpenAIImageProvider
        fake_client = MagicMock()
        fake_client.images.generate = AsyncMock(return_value=MagicMock(data=[]))
        fake_client.close = AsyncMock()
        fake_openai = MagicMock()
        fake_openai.AsyncOpenAI = MagicMock(return_value=fake_client)
        prov = OpenAIImageProvider(provider_name="X", endpoint="", api_key="sk-x")
        with patch.dict("sys.modules", {"openai": fake_openai}):
            with pytest.raises(ImageGenError):
                await prov.generate("x")

    @pytest.mark.asyncio
    async def test_list_models_marks_active(self, monkeypatch, _openai_image_catalog):
        from personalclaw.image_gen.openai_provider import OpenAIImageProvider
        prov = OpenAIImageProvider(provider_name="X", provider_type="openai", endpoint="", api_key="sk-x")
        monkeypatch.setattr(
            "personalclaw.image_gen.registry.active_image_gen", lambda: (prov, "gpt-image-1"),
        )
        models = await prov.list_models()
        active = [m for m in models if m.active]
        assert len(active) == 1 and active[0].name == "gpt-image-1"
        assert all(m.downloaded for m in models)


class TestStubProvider:
    @pytest.mark.asyncio
    async def test_stub_generates_valid_png_deterministic(self):
        import base64
        from personalclaw.image_gen.stub_provider import StubImageProvider
        prov = StubImageProvider()
        a = await prov.generate("a red leaf")
        b = await prov.generate("a red leaf")
        c = await prov.generate("a blue car")
        assert base64.b64decode(a[0].b64)[:8] == b"\x89PNG\r\n\x1a\n"
        assert a[0].b64 == b[0].b64           # deterministic per prompt
        assert a[0].b64 != c[0].b64           # distinct per prompt
        assert "stub render of: a red leaf" in a[0].revised_prompt

    @pytest.mark.asyncio
    async def test_stub_edit_differs_from_generate(self):
        from personalclaw.image_gen.stub_provider import StubImageProvider
        prov = StubImageProvider()
        g = await prov.generate("x")
        e = await prov.edit("x", source_image="/tmp/x.png")
        assert g[0].b64 != e[0].b64           # edit is a visibly distinct shade


@pytest.fixture
def _openai_image_catalog():
    """Contribute OpenAI's image catalog under the ``openai`` provider type (the
    openai-models app does this on load) so the core adapter — now catalog-driven,
    not host-sniffing — can resolve it. Cleaned up after the test."""
    from personalclaw.media_catalogs import (
        MediaCatalog, MediaModel, register_media_catalog, unregister_media_catalogs,
    )
    register_media_catalog(
        "image_gen", "openai",
        MediaCatalog(
            models=(
                MediaModel(name="gpt-image-1", extra={"sizes": ["1024x1024"], "supports_edit": True}),
                MediaModel(name="dall-e-3", extra={"sizes": ["1024x1024"], "supports_edit": False}),
                MediaModel(name="dall-e-2", extra={"sizes": ["256x256"], "supports_edit": True}),
            ),
            default_model="gpt-image-1",
        ),
    )
    yield
    unregister_media_catalogs("openai")


class TestOpenAIImageCatalogByType:
    """The image adapter serves a vendor's curated catalog by PROVIDER TYPE, from the
    catalog that vendor's app contributed (personalclaw.media_catalogs) — no
    api.openai.com host-sniff, no OpenAI model ids hard-coded in core. A provider type
    with a contributed catalog (``openai``) surfaces + defaults to it; a type with no
    contribution (``openai_compatible`` = a bring-your-own/other-vendor endpoint like
    Alibaba) advertises nothing and refuses an unpinned default."""

    @pytest.mark.asyncio
    async def test_openai_type_lists_curated_models(self, _openai_image_catalog):
        from personalclaw.image_gen.openai_provider import OpenAIImageProvider
        prov = OpenAIImageProvider(provider_name="OpenAI", provider_type="openai", endpoint="", api_key="sk-x")
        ids = {m.name for m in await prov.list_models()}
        assert ids == {"gpt-image-1", "dall-e-3", "dall-e-2"}

    @pytest.mark.asyncio
    async def test_openai_type_lists_curated_models_regardless_of_endpoint(self, _openai_image_catalog):
        from personalclaw.image_gen.openai_provider import OpenAIImageProvider
        # Catalog is keyed by TYPE, not endpoint host — an explicit OpenAI host still works.
        prov = OpenAIImageProvider(
            provider_name="OpenAI", provider_type="openai",
            endpoint="https://api.openai.com/v1", api_key="sk-x")
        assert {m.name for m in await prov.list_models()} == {"gpt-image-1", "dall-e-3", "dall-e-2"}

    @pytest.mark.asyncio
    async def test_uncontributed_type_lists_no_models(self):
        from personalclaw.image_gen.openai_provider import OpenAIImageProvider
        prov = OpenAIImageProvider(
            provider_name="Alibaba", provider_type="openai_compatible",
            endpoint="https://dashscope-intl.aliyuncs.com/compatible-mode/v1", api_key="sk-x")
        assert await prov.list_models() == []  # no contributed catalog for this type

    @pytest.mark.asyncio
    async def test_uncontributed_type_unpinned_generate_raises(self):
        from personalclaw.image_gen.openai_provider import OpenAIImageProvider
        prov = OpenAIImageProvider(
            provider_name="Alibaba", provider_type="openai_compatible",
            endpoint="https://dashscope-intl.aliyuncs.com/compatible-mode/v1", api_key="sk-x")
        with pytest.raises(ImageGenError) as exc:
            await prov.generate("a cat")  # no model= → must NOT fall back to a bogus id
        assert "no contributed default" in str(exc.value)

    def test_pinned_model_wins_on_any_type(self):
        from personalclaw.image_gen.openai_provider import OpenAIImageProvider
        prov = OpenAIImageProvider(
            provider_name="Alibaba", provider_type="openai_compatible",
            endpoint="https://dashscope-intl.aliyuncs.com/v1", api_key="k")
        # An explicit model is honored even for an uncontributed type.
        assert prov._default_model("wan2.7-image") == "wan2.7-image"

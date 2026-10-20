"""image_gen and video_gen providers surface in /api/models/available (IG5 — the binding UI's data).

The Settings → Models 'Image · Generation' row is the generic UseCaseRow; it needs
image_gen-capable models in the available list, one row per provider. This covers the backend
discovery that puts them there (bare id so the FE builds provider:model refs), and the rows of
providers that cannot generate right now and say why.
"""

from __future__ import annotations

import pytest

from personalclaw.image_gen.provider import ImageGenModel, ImageGenProvider


class _FakeImg(ImageGenProvider):
    @property
    def name(self) -> str:
        return "MyOpenAI"

    @property
    def display_name(self) -> str:
        return "MyOpenAI"

    async def is_available(self) -> bool:
        return True

    async def list_models(self):
        return [
            ImageGenModel(name="gpt-image-1", description="gen+edit", supports_edit=True),
            ImageGenModel(name="dall-e-3", description="gen", supports_edit=False),
        ]

    async def generate(self, prompt, **k):
        return []

    async def edit(self, prompt, *, source_image, **k):
        return []


class _UnavailableImg(_FakeImg):
    @property
    def name(self) -> str:
        return "Down"

    async def is_available(self) -> bool:
        return False


class _ReasonedImg(_UnavailableImg):
    """An unavailable provider that says why, as an app's adapter with no key does."""

    async def unavailable_reason(self) -> str:
        return "No key is set on Down. Add it on the Down instance in Settings → Providers."


class _FailingImg(_FakeImg):
    @property
    def name(self) -> str:
        return "Flaky"

    async def list_models(self):
        raise RuntimeError("the catalog endpoint answered HTTP 500")


def _image_providers(monkeypatch, *providers):
    from personalclaw.image_gen import registry as ig

    monkeypatch.setattr(ig, "_ensure_registered", lambda: None)
    monkeypatch.setattr(ig, "list_providers", lambda: list(providers))


class TestImageGenDiscovery:
    @pytest.mark.asyncio
    async def test_surfaces_models_bare_id_image_gen_capable(self, monkeypatch):
        from personalclaw.dashboard.handlers.model_registry import _media_rows

        _image_providers(monkeypatch, _FakeImg())

        [row] = await _media_rows("image_gen")
        assert (row["name"], row["type"]) == ("MyOpenAI", "image_gen")
        models = row["models"]
        ids = {m["id"] for m in models}
        assert ids == {"gpt-image-1", "dall-e-3"}  # BARE — FE prepends provider:
        assert all(m["capabilities"] == ["image_gen"] for m in models)
        assert all(m["provider"] == "MyOpenAI" for m in models)
        # supports_edit threaded for UI affordances
        assert next(m for m in models if m["id"] == "gpt-image-1")["supports_edit"] is True

    @pytest.mark.asyncio
    async def test_an_unavailable_provider_that_says_why_keeps_its_row(self, monkeypatch):
        """🔴 Red before: every unavailable provider was skipped, so an instance whose key was
        missing vanished from Image · Generation with nothing saying why."""
        from personalclaw.dashboard.handlers.model_registry import _media_rows

        _image_providers(monkeypatch, _FakeImg(), _ReasonedImg())

        rows = {row["name"]: row for row in await _media_rows("image_gen")}
        assert rows["Down"] == {
            "name": "Down",
            "type": "image_gen",
            "models": [],
            "error": "No key is set on Down. Add it on the Down instance in Settings → Providers.",
        }
        assert rows["MyOpenAI"]["models"]

    @pytest.mark.asyncio
    async def test_an_unavailable_provider_with_nothing_to_say_is_left_out(self, monkeypatch):
        """The OpenAI-Images adapter is built for every OpenAI-family provider, image vendor or
        not: one that makes no images says nothing, and stays out of the row."""
        from personalclaw.dashboard.handlers.model_registry import _media_rows

        _image_providers(monkeypatch, _FakeImg(), _UnavailableImg())
        assert {row["name"] for row in await _media_rows("image_gen")} == {"MyOpenAI"}

    @pytest.mark.asyncio
    async def test_a_listing_that_fails_is_its_rows_error(self, monkeypatch):
        """🔴 Red before: swallowed at DEBUG, and the provider simply had no row."""
        from personalclaw.dashboard.handlers.model_registry import _media_rows

        _image_providers(monkeypatch, _FailingImg())

        from personalclaw.providers.failure_copy import relayed_failure_copy

        [row] = await _media_rows("image_gen")
        assert row["models"] == []
        # Said the way a config provider's failed listing is said on the same page.
        assert row["error"] == relayed_failure_copy(
            RuntimeError("the catalog endpoint answered HTTP 500")
        )

    @pytest.mark.asyncio
    async def test_video_providers_get_the_same_rows(self, monkeypatch):
        from personalclaw.dashboard.handlers.model_registry import _media_rows
        from personalclaw.video_gen import registry as vg
        from personalclaw.video_gen.provider import VideoGenModel, VideoGenProvider

        class _Clip(VideoGenProvider):
            def __init__(self, name, available, reason=""):
                self._n, self._a, self._r = name, available, reason

            @property
            def name(self):
                return self._n

            @property
            def display_name(self):
                return self._n

            async def is_available(self):
                return self._a

            async def unavailable_reason(self):
                return self._r

            async def list_models(self):
                return [VideoGenModel(name="clip-1", description="a video model")]

            async def generate(self, prompt, **k):
                return []

        monkeypatch.setattr(
            vg,
            "list_providers",
            lambda: [
                _Clip("Reel", True),
                _Clip("NoBucket", False, "Set a bucket."),
                _Clip("Quiet", False),
            ],
        )
        rows = await _media_rows("video_gen")
        assert rows == [
            {
                "name": "Reel",
                "type": "video_gen",
                "models": [
                    {
                        "id": "clip-1",
                        "name": "clip-1",
                        "capabilities": ["video_gen"],
                        "description": "a video model",
                        "provider": "Reel",
                        "provider_type": "video_gen",
                    }
                ],
            },
            {"name": "NoBucket", "type": "video_gen", "models": [], "error": "Set a bucket."},
        ]

    @pytest.mark.asyncio
    async def test_binding_ref_resolves_back(self, monkeypatch, tmp_path):
        """A FE-built 'provider:model' ref from a bare id resolves via active_image_gen."""
        from personalclaw.image_gen import registry as ig
        from personalclaw.providers import use_cases as uc

        prov = _FakeImg()
        monkeypatch.setattr(ig, "_providers", {"MyOpenAI": prov}, raising=False)
        monkeypatch.setattr(ig, "_ensure_registered", lambda: None)
        # the FE would store "MyOpenAI:gpt-image-1" (provider + bare id)
        monkeypatch.setattr(
            uc, "active_model_refs", lambda u: ["MyOpenAI:gpt-image-1"] if u == "image_gen" else []
        )

        resolved = ig.active_image_gen()
        assert resolved is not None
        p, model_id = resolved
        assert p.name == "MyOpenAI" and model_id == "gpt-image-1"

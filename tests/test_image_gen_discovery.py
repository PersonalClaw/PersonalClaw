"""image_gen models surface in /api/models/available (IG5 — the binding UI's data).

The Settings → Models 'Image · Generation' row is the generic UseCaseRow; it just
needs image_gen-capable models in the available list. This covers the backend
discovery that puts them there (bare id so the FE builds provider:model refs).
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


class TestImageGenDiscovery:
    @pytest.mark.asyncio
    async def test_surfaces_models_bare_id_image_gen_capable(self, monkeypatch):
        from personalclaw.dashboard.handlers.model_registry import _discover_image_gen_models
        from personalclaw.image_gen import registry as ig

        monkeypatch.setattr(ig, "_ensure_registered", lambda: None)
        monkeypatch.setattr(ig, "list_providers", lambda: [_FakeImg()])

        models = await _discover_image_gen_models()
        ids = {m["id"] for m in models}
        assert ids == {"gpt-image-1", "dall-e-3"}  # BARE — FE prepends provider:
        assert all(m["capabilities"] == ["image_gen"] for m in models)
        assert all(m["provider"] == "MyOpenAI" for m in models)
        # supports_edit threaded for UI affordances
        assert next(m for m in models if m["id"] == "gpt-image-1")["supports_edit"] is True

    @pytest.mark.asyncio
    async def test_skips_unavailable_provider(self, monkeypatch):
        from personalclaw.dashboard.handlers.model_registry import _discover_image_gen_models
        from personalclaw.image_gen import registry as ig

        monkeypatch.setattr(ig, "_ensure_registered", lambda: None)
        monkeypatch.setattr(ig, "list_providers", lambda: [_FakeImg(), _UnavailableImg()])
        models = await _discover_image_gen_models()
        assert {m["provider"] for m in models} == {"MyOpenAI"}  # Down skipped (unavailable)

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

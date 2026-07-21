"""Active-model selections must not surface models from removed providers.

`active_models.json` stores selections as ``"<provider>:<model>"``. When a
provider is removed in Settings, its refs would otherwise linger and show as
ghost models in the Settings count, the app-wide dropdowns, and routing. The
single read seam ``load_active_models`` prunes refs whose provider is no longer
configured (config.json names + the bundled in-process providers).
"""

from __future__ import annotations

import json

import personalclaw.config.loader as loader
from personalclaw.providers import use_cases as uc


def _setup(monkeypatch, tmp_path, *, providers, active):
    """Point config_path + the active-models file at a tmp dir with given content."""
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"providers": providers}))
    monkeypatch.setattr(loader, "config_path", lambda: cfg)
    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    (tmp_path / "active_models.json").write_text(json.dumps(active))


def test_prunes_refs_from_removed_provider(monkeypatch, tmp_path):
    # Alibaba removed; only "openrouter" remains configured.
    _setup(
        monkeypatch,
        tmp_path,
        providers=[{"name": "openrouter", "type": "openai_compatible"}],
        active={
            "chat": [
                "alibaba:glm-5.1",
                "alibaba:qwen-max",
                "alibaba:qwen-plus",
                "openrouter:gpt-5",
            ]
        },
    )
    loaded = uc.load_active_models()
    assert loaded["chat"] == ["openrouter:gpt-5"], "Alibaba ghosts must be pruned"


def test_keeps_bundled_provider_refs(monkeypatch, tmp_path):
    # Bundled in-process providers have no config.json entry but are valid.
    _setup(
        monkeypatch,
        tmp_path,
        providers=[],
        active={
            "embedding": ["sentence-transformers:bge-small"],
            "stt": ["faster-whisper:base"],
            "tts": ["piper-tts:en_US-amy"],
        },
    )
    loaded = uc.load_active_models()
    assert loaded["embedding"] == ["sentence-transformers:bge-small"]
    assert loaded["stt"] == ["faster-whisper:base"]
    assert loaded["tts"] == ["piper-tts:en_US-amy"]


def test_keeps_image_gen_bundle_refs(monkeypatch, tmp_path):
    """A dynamically-registered image-gen bundle (fal) has no config.json entry,
    but its binding must NOT be pruned — the regression that silently dropped a
    just-bound fal:<model> from /api/models/active + routing (IG-GAP1)."""
    _setup(
        monkeypatch,
        tmp_path,
        providers=[],
        active={"image_gen": ["fal:fal-ai/flux/schnell"]},
    )
    # Simulate the fal provider being registered (as it is when FAL_KEY resolves).
    monkeypatch.setattr(uc, "_dynamic_media_provider_names", lambda: {"fal"})
    loaded = uc.load_active_models()
    assert loaded["image_gen"] == ["fal:fal-ai/flux/schnell"]


def test_prunes_image_gen_ref_when_bundle_absent(monkeypatch, tmp_path):
    """If the image-gen bundle is gone (no provider registered), its ref IS pruned
    — a removed bundle shouldn't leave a ghost binding."""
    _setup(
        monkeypatch,
        tmp_path,
        providers=[],
        active={"image_gen": ["fal:fal-ai/flux/schnell"]},
    )
    monkeypatch.setattr(uc, "_dynamic_media_provider_names", lambda: set())
    assert uc.load_active_models()["image_gen"] == []


def test_keeps_provider_agnostic_refs(monkeypatch, tmp_path):
    # A ref with no "provider:" prefix is provider-agnostic — never pruned.
    _setup(
        monkeypatch,
        tmp_path,
        providers=[],
        active={"chat": ["bare-model-id"]},
    )
    assert uc.load_active_models()["chat"] == ["bare-model-id"]


def test_does_not_prune_when_config_unreadable(monkeypatch, tmp_path):
    # Transient config read failure must not discard valid selections.
    (tmp_path / "active_models.json").write_text(json.dumps({"chat": ["alibaba:glm-5.1"]}))
    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    bad = tmp_path / "config.json"
    bad.write_text("{ this is not valid json")
    monkeypatch.setattr(loader, "config_path", lambda: bad)
    # config.json unreadable → known set is None → prune skipped, ref retained.
    assert uc.load_active_models()["chat"] == ["alibaba:glm-5.1"]


def test_empty_file_yields_empty(monkeypatch, tmp_path):
    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(loader, "config_path", lambda: tmp_path / "config.json")
    assert uc.load_active_models() == {}


# ── PUT /api/models/active/{use_case} — set-time provider validation ──
# Regression: the setter saved ANY string, so a ref naming an uninstalled provider
# (e.g. "NoProvider:no-model", or an embedding ref that got clobbered) silently
# stranded the use-case on a dead binding. It now rejects a ref whose PROVIDER
# prefix is unknown (config + bundled + media), fail-fast — matching the campaign's
# "block, don't silently accept unresolvable refs" principle. Conservative: only the
# provider prefix is validated, never the model id (a slow-to-enumerate real provider
# must not be false-rejected).

import pytest  # noqa: E402
from aiohttp import web  # noqa: E402
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

import personalclaw.dashboard.handlers.model_registry as mr  # noqa: E402


def _mr_app(monkeypatch, tmp_path, *, providers):
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"providers": providers}))
    monkeypatch.setattr(loader, "config_path", lambda: cfg)
    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    app = web.Application()
    app.router.add_put("/api/models/active/{use_case}", mr.api_models_active_set)
    return app


@pytest.mark.asyncio
async def test_set_rejects_unknown_provider_ref(monkeypatch, tmp_path):
    app = _mr_app(
        monkeypatch, tmp_path, providers=[{"name": "OpenAI", "type": "openai_compatible"}]
    )
    async with TestClient(TestServer(app)) as c:
        resp = await c.put("/api/models/active/chat", json={"models": ["NoProvider:no-model"]})
        assert resp.status == 400
        assert "Unknown provider" in (await resp.json())["error"]
        # And nothing was persisted for the use-case.
        assert (
            not (tmp_path / "active_models.json").exists()
            or "NoProvider" not in (tmp_path / "active_models.json").read_text()
        )


@pytest.mark.asyncio
async def test_set_accepts_known_provider_ref(monkeypatch, tmp_path):
    app = _mr_app(
        monkeypatch, tmp_path, providers=[{"name": "OpenAI", "type": "openai_compatible"}]
    )
    async with TestClient(TestServer(app)) as c:
        # A known config provider + an arbitrary (not-yet-enumerated) model id → accepted
        # (we validate the PREFIX, not the model catalog).
        resp = await c.put("/api/models/active/chat", json={"models": ["OpenAI:gpt-anything-99"]})
        assert resp.status == 200
        assert (await resp.json())["models"] == ["OpenAI:gpt-anything-99"]


@pytest.mark.asyncio
async def test_set_allows_bare_id_and_bundled(monkeypatch, tmp_path):
    app = _mr_app(monkeypatch, tmp_path, providers=[])
    async with TestClient(TestServer(app)) as c:
        # A bundled provider (sentence-transformers) is always known.
        r1 = await c.put(
            "/api/models/active/embedding",
            json={"models": ["sentence-transformers:all-MiniLM-L6-v2"]},
        )
        assert r1.status == 200
        # A bare id (no provider prefix) is left alone (some use-cases store bare ids).
        r2 = await c.put("/api/models/active/chat", json={"models": ["just-a-bare-id"]})
        assert r2.status == 200

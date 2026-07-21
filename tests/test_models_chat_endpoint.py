"""`GET /api/models/chat` is the ONE chat-model list (#33 — collapsed the
duplicate `/api/models`).

Pins the superset response shape every consumer depends on: each entry carries
both ``model_name`` (web composer pill + legacy provider adapters read this)
and ``model_id`` (agent/chat pickers read this), plus ``name``/``provider``/
``description``. A regression that drops either field silently breaks a UI.
"""

from __future__ import annotations

import asyncio
import json

from aiohttp.test_utils import make_mocked_request

from personalclaw.dashboard.handlers import model_registry as mr


def _call() -> list:
    req = make_mocked_request("GET", "/api/models/chat")
    resp = asyncio.run(mr.api_models_chat(req))
    return json.loads(resp.body.decode())


def test_active_models_carry_superset_shape(monkeypatch):
    monkeypatch.setattr(
        mr,
        "load_active_models",
        lambda: {"chat": ["Bedrock:global.anthropic.claude-opus-4-8", "bare-model"]},
    )
    rows = _call()
    assert len(rows) == 2
    qualified = next(r for r in rows if r["provider"] == "Bedrock")
    # qualified ref → name keeps the full ref; model_name/model_id are the bare id
    assert qualified["name"] == "Bedrock:global.anthropic.claude-opus-4-8"
    assert qualified["model_name"] == "global.anthropic.claude-opus-4-8"
    assert qualified["model_id"] == "global.anthropic.claude-opus-4-8"
    # every entry has the full superset of keys
    for r in rows:
        assert {"name", "model_name", "model_id", "provider", "description"} <= set(r)
    bare = next(r for r in rows if r["provider"] == "")
    assert bare["name"] == "bare-model" and bare["model_name"] == "bare-model"


def test_fallback_discovers_across_provider_families(monkeypatch):
    """With no active selection, the fallback covers every configured provider via
    its ModelCatalog (not a per-type switch) — chat-only, each entry superset-shaped.

    Each provider now discovers through ``registry.build_catalog(entry).list_models()``;
    the handler resolves that via ``_catalog_for_config_provider``. Inject fake
    catalogs keyed by provider name so the test is provider-agnostic (proving the
    generic seam, not any specific type)."""
    from personalclaw.llm.catalog import ModelCatalog, ModelInfo

    monkeypatch.setattr(mr, "load_active_models", lambda: {})
    monkeypatch.setattr(
        mr,
        "_get_providers_from_config",
        lambda: [
            {"type": "ollama", "name": "ollama", "options": {"endpoint": "http://x"}},
            {"type": "bedrock", "name": "Bedrock", "options": {"region": "us-east-1"}},
        ],
    )

    class _FakeCatalog(ModelCatalog):
        def __init__(self, models):
            self._models = models

        async def list_models(self):
            return self._models

    catalogs = {
        "ollama": _FakeCatalog(
            [
                ModelInfo(id="llama3", name="llama3", capabilities=["chat"]),
                ModelInfo(id="nomic-embed", name="nomic-embed", capabilities=["embedding"]),
            ]
        ),
        "Bedrock": _FakeCatalog(
            [
                ModelInfo(id="anthropic.claude-x", name="Claude X", capabilities=["chat"]),
            ]
        ),
    }
    monkeypatch.setattr(mr, "_catalog_for_config_provider", lambda p: catalogs.get(p.get("name")))

    rows = _call()
    ids = {r["model_id"] for r in rows}
    assert "llama3" in ids  # ollama chat model
    assert "anthropic.claude-x" in ids  # bedrock chat model
    assert "nomic-embed" not in ids  # embedding model excluded (chat-only)
    for r in rows:
        assert {"name", "model_name", "model_id", "provider", "description"} <= set(r)

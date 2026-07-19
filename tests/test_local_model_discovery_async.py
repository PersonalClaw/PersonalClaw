"""Local-model catalog discovery must be awaited, not asyncio.run()'d.

Regression for the app-split bug: the embedding / STT / TTS catalog helpers wrapped
the provider's async `list_models()` in `asyncio.run()`. Inside the gateway's aiohttp
event loop that raises "asyncio.run() cannot be called from a running event loop";
the surrounding `except` swallowed it and returned [], so every local model looked
"not downloaded" (embedding-status model_available=false; STT/TTS groups empty).
The fix makes the chain async. This guards that they work FROM a running loop.
"""

from __future__ import annotations

import asyncio

from personalclaw.embedding_providers import registry as emb_reg


class _FakeModel:
    def __init__(self, name, downloaded):
        self.name = name
        self.downloaded = downloaded
        self.dimension = 384
        self.size_mb = 80
        self.description = "test"


class _FakeProvider:
    name = "native"

    async def list_models(self):
        return [_FakeModel("all-MiniLM-L6-v2", True), _FakeModel("other", False)]


def test_list_native_models_awaitable_from_running_loop(monkeypatch):
    """list_native_models must resolve when awaited inside a live event loop —
    the exact context of the aiohttp handlers (a prior asyncio.run() raised here)."""
    monkeypatch.setattr(emb_reg, "native_provider", lambda: _FakeProvider())

    async def _run():
        models = await emb_reg.list_native_models()
        assert [m.name for m in models] == ["all-MiniLM-L6-v2", "other"]
        assert await emb_reg.is_native_model_downloaded("all-MiniLM-L6-v2") is True
        assert await emb_reg.is_native_model_downloaded("other") is False

    asyncio.new_event_loop().run_until_complete(_run())


def test_is_native_model_downloaded_false_when_no_provider(monkeypatch):
    monkeypatch.setattr(emb_reg, "native_provider", lambda: None)

    async def _run():
        assert await emb_reg.list_native_models() == []
        assert await emb_reg.is_native_model_downloaded("anything") is False

    asyncio.new_event_loop().run_until_complete(_run())

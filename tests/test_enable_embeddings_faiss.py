"""Tests for the faiss-cpu dependency check in the enable-embeddings handler."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import personalclaw.dashboard.handlers.memory as mem_mod

_MOD = "personalclaw.dashboard.handlers.memory"
_REG = "personalclaw.embedding_providers.registry"


def _make_app() -> web.Application:
    app = web.Application()
    app.router.add_post("/api/memory/enable-embeddings", mem_mod.api_memory_enable_embeddings)
    app["state"] = MagicMock(consolidator=None)
    return app


@pytest.fixture(autouse=True)
def _reset_status():
    mem_mod._embedding_setup_status = {"step": "idle", "error": ""}
    yield
    mem_mod._embedding_setup_status = {"step": "idle", "error": ""}


def _common_patches(cfg_path, faiss_available=False):
    """Return a dict of context managers for the common mocks.

    The active embedding selection is forced to a native model so the handler
    proceeds past the model-selection guard into the faiss-import check.
    """
    store = MagicMock()
    store.embed_fn = None
    store.load_faiss_index = MagicMock()

    faiss_mod = MagicMock() if faiss_available else None

    patches = {
        "spec": patch(
            f"{_REG}._active_embedding_spec",
            return_value=("native", "all-MiniLM-L6-v2"),
        ),
        "embed_fn": patch(f"{_REG}.get_active_embed_fn", return_value=lambda t: [0.0]),
        "cfg_path": patch("personalclaw.config.loader.config_path", return_value=cfg_path),
        # The native embedding backend is the sentence-transformers APP now: the
        # handler guards on native_provider() being registered + the model being
        # downloaded (both via the embedding registry), not the old embedding.py.
        "native": patch(f"{_REG}.native_provider", return_value=MagicMock()),
        "is_downloaded": patch(
            f"{_REG}.is_native_model_downloaded", new=AsyncMock(return_value=True)
        ),
        "faiss": patch.dict("sys.modules", {"faiss": faiss_mod}),
        "store": patch(f"{_MOD}._get_provider", return_value=store),
    }
    return patches, store


class TestFaissMissing:
    @pytest.mark.asyncio
    async def test_returns_400_when_faiss_not_installed(self, tmp_path: Path) -> None:
        cfg_path = tmp_path / "personalclaw.json"
        cfg_path.write_text("{}", encoding="utf-8")
        patches, store = _common_patches(cfg_path, faiss_available=False)

        with (
            patches["spec"],
            patches["embed_fn"],
            patches["cfg_path"],
            patches["native"],
            patches["is_downloaded"],
            patches["faiss"],
            patches["store"],
        ):
            async with TestClient(TestServer(_make_app())) as c:
                resp = await c.post("/api/memory/enable-embeddings")
                assert resp.status == 400
                body = await resp.json()
                assert "faiss-cpu is not installed" in body["error"]

        # Status is reset so the user can retry after installing faiss-cpu.
        assert mem_mod._embedding_setup_status["step"] == "idle"
        assert "faiss-cpu" in mem_mod._embedding_setup_status["error"]


class TestFaissAvailableSuccess:
    @pytest.mark.asyncio
    async def test_loads_index_when_faiss_importable(self, tmp_path: Path) -> None:
        cfg_path = tmp_path / "personalclaw.json"
        cfg_path.write_text("{}", encoding="utf-8")
        patches, store = _common_patches(cfg_path, faiss_available=True)

        with (
            patches["spec"],
            patches["embed_fn"],
            patches["cfg_path"],
            patches["native"],
            patches["is_downloaded"],
            patches["faiss"],
            patches["store"],
        ):
            async with TestClient(TestServer(_make_app())) as c:
                resp = await c.post("/api/memory/enable-embeddings")
                assert resp.status == 200
                assert (await resp.json()).get("ok") is True

            store.load_faiss_index.assert_called_once()


class TestLoadFaissIndexFailure:
    @pytest.mark.asyncio
    async def test_returns_500_when_load_faiss_raises(self, tmp_path: Path) -> None:
        cfg_path = tmp_path / "personalclaw.json"
        cfg_path.write_text("{}", encoding="utf-8")
        patches, store = _common_patches(cfg_path, faiss_available=True)
        store.load_faiss_index.side_effect = RuntimeError("corrupted index")

        with (
            patches["spec"],
            patches["embed_fn"],
            patches["cfg_path"],
            patches["native"],
            patches["is_downloaded"],
            patches["faiss"],
            patches["store"],
        ):
            async with TestClient(TestServer(_make_app())) as c:
                resp = await c.post("/api/memory/enable-embeddings")
                assert resp.status == 500
                body = await resp.json()
                assert "FAISS index load failed" in body["error"]

        assert mem_mod._embedding_setup_status["step"] == "idle"
        assert "FAISS index load failed" in mem_mod._embedding_setup_status["error"]

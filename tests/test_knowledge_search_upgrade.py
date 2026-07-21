"""Tests for knowledge search upgrade: embedding endpoints + search-for-context."""

import pytest

from personalclaw.knowledge.embedder import UnifiedEmbedder, create_embedder_from_config


class TestCreateEmbedderFromConfig:
    """create_embedder_from_config resolves through the one unified embedding path
    (get_active_embed_fn) — provider-agnostic, no per-provider hardcoding."""

    def test_returns_none_when_no_active_spec(self, monkeypatch):
        # Built from the Settings > Models active binding. Nothing bound → no embedder.
        monkeypatch.setattr(
            "personalclaw.embedding_providers.registry.get_active_embed_fn",
            lambda: None,
        )
        assert create_embedder_from_config({"memory": {"embedding_provider": "none"}}) is None

    def test_returns_none_when_memory_section_missing(self, monkeypatch):
        monkeypatch.setattr(
            "personalclaw.embedding_providers.registry.get_active_embed_fn",
            lambda: None,
        )
        assert create_embedder_from_config({}) is None

    def test_returns_embedder_when_a_model_is_bound(self, monkeypatch):
        # ANY bound provider (native/ollama/openai/…) yields a working embedder via
        # the resolved embed fn — the adapter is provider-agnostic.
        monkeypatch.setattr(
            "personalclaw.embedding_providers.registry.get_active_embed_fn",
            lambda: (lambda text: [0.5, 0.5]),
        )
        monkeypatch.setattr(
            "personalclaw.embedding_providers.registry.get_active_embedding_dim",
            lambda: 2,
        )
        emb = create_embedder_from_config({})
        assert isinstance(emb, UnifiedEmbedder)
        assert emb.embed("hello") == [0.5, 0.5]
        assert emb.dim() == 2

    def test_ignores_old_knowledge_embeddings_config(self, monkeypatch):
        """Old knowledge.embeddings.enabled path should NOT activate the embedder —
        only the Settings > Models active binding does."""
        monkeypatch.setattr(
            "personalclaw.embedding_providers.registry.get_active_embed_fn",
            lambda: None,
        )
        cfg = {"knowledge": {"embeddings": {"enabled": True}}}
        assert create_embedder_from_config(cfg) is None


class TestUnifiedEmbedderDegradation:
    """UnifiedEmbedder graceful degradation (empty text / no bound fn / provider error)."""

    def test_embed_returns_none_for_empty_text(self):
        emb = UnifiedEmbedder(lambda text: [1.0])
        assert emb.embed("") is None
        assert emb.embed("   ") is None

    def test_embed_returns_none_when_no_fn(self):
        emb = UnifiedEmbedder(None)
        assert emb.is_available() is False
        assert emb.embed("hello world") is None

    def test_embed_swallows_provider_error(self):
        def _boom(text):
            raise RuntimeError("provider down")

        emb = UnifiedEmbedder(_boom)
        assert emb.embed("hello") is None  # degrades, never raises

    def test_embed_for_item_combines_title_and_summary(self):
        seen = {}

        def _capture(text):
            seen["text"] = text
            return [0.0]

        emb = UnifiedEmbedder(_capture)
        emb.embed_for_item("My Title", "A summary of the content")
        assert "My Title" in seen["text"] and "A summary" in seen["text"]


@pytest.fixture
def mock_knowledge_app(tmp_path):
    """Create a minimal aiohttp app mock with knowledge store."""
    from personalclaw.knowledge.store import KnowledgeStore

    db_path = str(tmp_path / "knowledge.db")
    store = KnowledgeStore(db_path)
    # Add some test items (one logical doc each)
    store.create_typed_item(
        item_type="document",
        title="Auth Token Refresh",
        content="JWT refresh flow using rotating keys",
        summary="How auth tokens are refreshed",
    )
    store.create_typed_item(
        item_type="document",
        title="Database Schema",
        content="PostgreSQL schema for user tables",
        summary="DB schema overview",
    )
    return store


class TestSearchForContext:
    """search_for_context endpoint logic."""

    def test_estimate_tokens(self):
        # Import the real function from the handler module
        from personalclaw.dashboard.handlers.knowledge import _estimate_tokens

        assert _estimate_tokens("hello world") == 2  # 11 chars // 4
        assert _estimate_tokens("") == 0

    def test_knowledge_fetch_defaults(self):
        from personalclaw.dashboard.handlers import knowledge as H

        assert H.KNOWLEDGE_FETCH_TOP_N == 3
        assert H.KNOWLEDGE_FETCH_MAX_TOKENS == 4096

    def _ctx(self, store, query_string):
        import asyncio
        from types import SimpleNamespace

        from aiohttp import web
        from aiohttp.test_utils import make_mocked_request

        from personalclaw.dashboard.handlers import knowledge as H

        app = web.Application()
        app["state"] = SimpleNamespace(knowledge_store=store)
        req = make_mocked_request(
            "GET", "/api/knowledge/search-for-context?" + query_string, app=app
        )
        resp = asyncio.get_event_loop().run_until_complete(H.search_for_context(req))
        import json

        return json.loads(resp.body)

    def test_max_tokens_query_param_overrides_default(self, mock_knowledge_app):
        body = self._ctx(mock_knowledge_app, "q=auth&max_tokens=5")
        assert body["max_tokens"] == 5  # honored the param, not the 4096 default

    def test_max_tokens_clamped_to_ceiling(self, mock_knowledge_app):
        from personalclaw.dashboard.handlers.knowledge import _CONTEXT_MAX_TOKENS_CEILING

        body = self._ctx(mock_knowledge_app, "q=auth&max_tokens=999999999")
        assert body["max_tokens"] == _CONTEXT_MAX_TOKENS_CEILING

    def test_max_tokens_default_when_absent(self, mock_knowledge_app):
        body = self._ctx(mock_knowledge_app, "q=auth")
        assert body["max_tokens"] == 4096

    def test_large_item_does_not_starve_other_matches(self, tmp_path):
        """One huge item must not consume the whole token budget and drop the other
        relevant results — each card gets a capped, even share so breadth is preserved."""
        from personalclaw.knowledge.store import KnowledgeStore

        store = KnowledgeStore(str(tmp_path / "k.db"))
        # Two items both matching 'kafka'; the first is enormous.
        store.create_typed_item(
            item_type="document",
            title="Kafka Deep Dive",
            content=("kafka " * 4000),
            summary="huge kafka doc",
        )
        store.create_typed_item(
            item_type="note",
            title="Kafka Quick Tip",
            content="kafka consumer group rebalance gotcha",
            summary="kafka tip",
        )
        # Small budget the big doc alone would otherwise exhaust.
        body = self._ctx(store, "q=kafka&max_tokens=200")
        titles = [c["title"] for c in body["results"]]
        assert len(body["results"]) >= 2, f"both matches should survive, got {titles}"
        assert "Kafka Quick Tip" in titles

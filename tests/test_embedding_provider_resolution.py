"""Active embedding provider resolves the in-process native provider regardless
of which name the active ref uses.

Regression: the active embedding ref is stored with the bundled extension name
``sentence-transformers`` (hyphen), but the native-name check only listed
``sentence_transformers`` (underscore). The mismatch sent the lookup to the LLM
provider registry, which raised ``ProviderResolutionError: unknown provider
entry 'sentence-transformers'`` on every memory-embed attempt during a chat
turn. Both spellings (+ ``native``) must resolve to the one native provider.
"""

from __future__ import annotations

import pytest

from personalclaw.embedding_providers import registry as reg


@pytest.mark.parametrize(
    "provider_name", ["sentence-transformers", "sentence_transformers", "native"]
)
def test_native_names_take_the_in_process_path(provider_name, monkeypatch):
    """For any native spelling, get_active_embed_fn uses the native provider and
    NEVER falls through to the LLM registry (which would raise for a non-model
    provider name)."""
    monkeypatch.setattr(reg, "_active_embedding_spec", lambda: (provider_name, "all-MiniLM-L6-v2"))

    called = {"llm": False}

    def _boom(*a, **k):
        called["llm"] = True
        raise AssertionError("native embedding must not route through the LLM registry")

    monkeypatch.setattr(reg, "_llm_embed_fn", _boom)

    # Stub the native provider so we don't need the model downloaded.
    class _FakeNative:
        def get_embed_fn(self, model_id):
            return lambda text: [0.0, 0.1, 0.2]

    monkeypatch.setattr(reg, "ensure_registered", lambda: None)
    monkeypatch.setattr(reg, "_providers", {"native": _FakeNative()})

    fn = reg.get_active_embed_fn()
    assert fn is not None
    assert fn("hello") == [0.0, 0.1, 0.2]
    assert called["llm"] is False


def test_hyphen_name_resolves_dimension(monkeypatch):
    """The dimension lookup accepts the hyphenated native name and reads the
    registered native provider's catalog (the sentence-transformers app)."""
    monkeypatch.setattr(
        reg, "_active_embedding_spec", lambda: ("sentence-transformers", "all-MiniLM-L6-v2")
    )

    from personalclaw.embedding_providers.base import EmbeddingModel

    class _FakeNative:
        async def list_models(self):
            return [EmbeddingModel(name="all-MiniLM-L6-v2", dimension=384)]

    monkeypatch.setattr(reg, "_providers", {"native": _FakeNative()})
    assert reg.get_active_embedding_dim() == 384


def test_knowledge_embedder_uses_the_unified_path(monkeypatch):
    """create_embedder_from_config resolves through the ONE unified embedding path
    (get_active_embed_fn) — provider-agnostic. Any bound provider (native OR a model
    provider's .embed()) yields a working knowledge embedder; the returned adapter
    embeds via the resolved fn. Regression this replaces: the knowledge embedder used
    to hardcode native+ollama only, silently yielding None for a bound openai/etc.
    model (knowledge semantic search disabled)."""
    from personalclaw.knowledge import embedder as emb_mod

    monkeypatch.setattr(reg, "get_active_embed_fn", lambda: (lambda text: [0.1, 0.2, 0.3]))
    monkeypatch.setattr(reg, "get_active_embedding_dim", lambda: 3)

    emb = emb_mod.create_embedder_from_config({})
    assert isinstance(emb, emb_mod.UnifiedEmbedder)
    assert emb.is_available() is True
    assert emb.embed("hello") == [0.1, 0.2, 0.3]
    assert emb.dim() == 3


def test_knowledge_embedder_none_when_nothing_bound(monkeypatch):
    """No embedding model bound → no embedder (knowledge embedding gracefully off)."""
    from personalclaw.knowledge import embedder as emb_mod

    monkeypatch.setattr(reg, "get_active_embed_fn", lambda: None)
    assert emb_mod.create_embedder_from_config({}) is None

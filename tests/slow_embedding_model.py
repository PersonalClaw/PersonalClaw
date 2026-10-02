"""An embedding model bound through a configured model provider, answering as slowly as a test says.

It is reached the way an Ollama binding is: the embedding binding names a configured provider's
model, the model-provider registry builds that provider, and every embedding function the gateway
resolves (``get_active_embed_fn``, ``embed_fn_for``, ``embed_many_fn_for``) embeds through its
``embed``. So what a test counts here reached the server: each request, and the texts in it.
"""

from __future__ import annotations

import asyncio
import hashlib
import threading


class EmbeddingServer:
    """A configured model provider that embeds: each request it answered, and how slowly."""

    def __init__(self) -> None:
        self.requests: list[list[str]] = []
        #: How long each request takes to answer.
        self.secs = 0.3
        #: The vector a text is answered with; any other text gets :func:`vector`'s.
        self.answers: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    async def start(self) -> None:
        return None

    async def embed(self, inputs: list[str]) -> list[list[float]]:
        with self._lock:
            self.requests.append(list(inputs))
        await asyncio.sleep(self.secs)
        return [self.answers.get(text) or vector(text) for text in inputs]

    def asked(self, text: str) -> int:
        """How many requests carried ``text``."""
        with self._lock:
            return sum(1 for request in self.requests if text in request)


def vector(text: str) -> list[float]:
    """A fixed vector per text."""
    digest = hashlib.sha256(text.encode()).digest()
    return [b / 255.0 + 0.01 for b in digest[:8]]


class _Registry:
    def __init__(self, server: EmbeddingServer) -> None:
        self._server = server

    def build(self, name: str, **_kwargs: object) -> EmbeddingServer:
        return self._server


def bind(monkeypatch) -> EmbeddingServer:
    """Bind embedding to a configured provider's model that answers through a new server."""
    import personalclaw.llm.registry as llm_registry
    from personalclaw.embedding_providers import registry as embedding_registry

    server = EmbeddingServer()
    monkeypatch.setattr(llm_registry, "get_default_registry", lambda: _Registry(server))
    monkeypatch.setattr(
        embedding_registry, "_active_embedding_spec", lambda: ("local-embed", "embed-model")
    )
    monkeypatch.setattr(embedding_registry, "_ensure_scanned", lambda: None)
    monkeypatch.setattr(embedding_registry, "_providers", {})
    return server

"""Abstract base for embedding providers — the INFERENCE axis (``embed``).

Model MANAGEMENT (list/download/delete of local embedding models) is a SEPARATE axis:
a local backend (sentence-transformers) ALSO subclasses
:class:`~personalclaw.local_models.provider.LocalModelProvider`; a remote/hosted
embedder (OpenAI text-embedding-3) implements ONLY this inference axis. The two are
independent — a provider opts into management only if it owns local models.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Callable


@dataclass
class EmbeddingModel:
    """A local embedding model's catalog entry. Carries ``dimension`` (needed by the
    vector store to detect incompatible stored vectors) — richer than the management
    ``LocalModel`` shape, which the local-model registry adapts it down to."""

    name: str
    dimension: int
    size_mb: float = 0
    description: str = ""
    downloaded: bool = False
    active: bool = False


class EmbeddingProvider(ABC):
    """Provider interface for text embedding backends — INFERENCE only (``embed``)."""

    @property
    @abstractmethod
    def name(self) -> str:
        ...

    @property
    @abstractmethod
    def display_name(self) -> str:
        ...

    @abstractmethod
    async def is_available(self) -> bool:
        ...

    @abstractmethod
    async def embed(self, text: str, model: str = "") -> list[float] | None:
        """Embed a single text. Returns vector or None on failure."""
        ...

    @abstractmethod
    async def embed_batch(self, texts: list[str], model: str = "") -> list[list[float]]:
        """Embed multiple texts."""
        ...

    def get_embed_fn(self, model: str = "") -> Callable[[str], list[float] | None]:
        """Return a sync embedding function for use with vector stores."""
        import asyncio

        async def _embed(text: str) -> list[float] | None:
            return await self.embed(text, model)

        def _sync_embed(text: str) -> list[float] | None:
            try:
                asyncio.get_running_loop()
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    future = pool.submit(asyncio.run, _embed(text))
                    return future.result(timeout=30)
            except RuntimeError:
                return asyncio.run(_embed(text))

        return _sync_embed

    def info(self) -> dict[str, Any]:
        return {"name": self.name, "display_name": self.display_name}

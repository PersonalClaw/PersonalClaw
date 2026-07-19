"""Knowledge/memory text embedding — provider-agnostic, via the one unified path.

Embedding resolves through the ``embedding`` use-case binding (Settings → Models):
``create_embedder_from_config`` wraps ``embedding_providers.registry.get_active_embed_fn()``
so the bound model — native sentence-transformers OR any configured model provider
(ollama, openai, together, …) that implements ``.embed()`` — is used with no
per-provider hardcoding. Returns None (embedding gracefully off) when nothing is bound.
Also holds the shared vector-text composition + BLOB (de)serialization helpers.
"""
import logging
import struct

logger = logging.getLogger(__name__)


def compose_item_text(title: str, summary: str | None, content: str | None = None) -> str:
    """Build the text to embed for a knowledge item: title + summary, topped up with a
    slice of the body when the summary is thin/absent. A title-only vector — common for
    summary-less items (fleeting notes, skipped summary category, mid-enrichment) —
    gives poor semantic recall; the body anchors it. Kept here so the ingest pipeline,
    batch re-embed, and reembed_all all compose the same vector text."""
    title = (title or "").strip()
    summary = (summary or "").strip()
    parts = [p for p in (title, summary) if p]
    if len(summary) < 80:
        body = (content or "").strip()
        if body:
            parts.append(body[:1000])
    return " ".join(parts).strip()


def floats_to_bytes(vec: list[float]) -> bytes:
    """Serialize float list to compact binary for SQLite BLOB storage."""
    return struct.pack(f"{len(vec)}f", *vec)


def bytes_to_floats(data: bytes) -> list[float]:
    """Deserialize binary BLOB back to float list."""
    n = len(data) // 4
    return list(struct.unpack(f"{n}f", data))


class UnifiedEmbedder:
    """Embed via whatever model is bound to the ``embedding`` use-case — provider
    agnostic. Wraps ``embedding_providers.registry.get_active_embed_fn()`` (the one
    resolution path: native in-process OR any configured model provider that
    implements ``.embed()`` — ollama, openai, together, …), exposing the same
    ``embed``/``embed_for_item``/``dim``/``is_available`` contract knowledge + memory
    consume. Returns None from ``embed`` when nothing is bound or the resolved
    provider is unavailable (graceful degradation — no crash, embeddings simply off).
    """

    def __init__(self, embed_fn, dim_hint: int | None = None):
        self._embed_fn = embed_fn
        self._dim = dim_hint

    def is_available(self) -> bool:
        return self._embed_fn is not None

    @property
    def model_name(self) -> str:
        """The active embedding model id (bare, without the ``provider:`` prefix),
        or "" when nothing is bound. Read from the Settings→Models ``embedding``
        selection — the one source of truth. The old per-backend embedder exposed a
        ``.model`` attribute; the UnifiedEmbedder wraps an embed_fn instead, so
        callers that want a label (e.g. the knowledge stats endpoint) read this."""
        try:
            from personalclaw.embedding_providers.registry import _active_embedding_spec

            spec = _active_embedding_spec()
            return spec[1] if spec else ""
        except Exception:
            return ""

    def embed(self, text: str) -> list[float] | None:
        if not text.strip() or self._embed_fn is None:
            return None
        try:
            return self._embed_fn(text)
        except Exception as e:  # noqa: BLE001 — a provider hiccup disables embedding, never crashes ingest
            logger.debug("embedding failed: %s", e)
            return None

    def embed_for_item(self, title: str, summary: str | None, content: str | None = None) -> list[float] | None:
        return self.embed(compose_item_text(title, summary, content))

    def dim(self) -> int | None:
        """The active model's embedding dimension (probe once, cached), or None if
        unavailable. Lets callers detect stored vectors from a different model."""
        if self._dim is None:
            vec = self.embed("dimension probe")
            self._dim = len(vec) if vec else None
        return self._dim


def create_embedder_from_config(config: dict) -> "UnifiedEmbedder | None":
    """Create an embedder from the Settings > Models active ``embedding`` selection.

    Provider-agnostic: resolves through the single unified embedding path
    (``get_active_embed_fn``), so the bound embedding model — native
    sentence-transformers OR any configured model provider (ollama, openai,
    together, …) — is used, with no per-provider hardcoding here. Returns None when
    no embedding model is bound (knowledge/memory embedding stays gracefully off
    until the user configures one).
    """
    try:
        from personalclaw.embedding_providers.registry import (
            get_active_embed_fn,
            get_active_embedding_dim,
        )
        embed_fn = get_active_embed_fn()
    except Exception:
        return None
    if embed_fn is None:
        return None
    try:
        dim_hint = get_active_embedding_dim()
    except Exception:
        dim_hint = None
    return UnifiedEmbedder(embed_fn, dim_hint=dim_hint)

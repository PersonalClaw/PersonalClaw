"""Abstract base for Search providers — the Search entity's contributor interface.

Search is a first-class pluggable entity (mirrors Model): the user configures +
activates one or more providers and binds them to use-cases (search-general /
search-news / search-financial / fetch-article) in Settings → Search. Every
consumer — the ``web_search`` / ``web_fetch`` tools, the research loop — resolves a
use-case to a provider through :func:`resolve_search_provider_for_use_case`, so the
picker and what the runtime resolves never disagree.

A provider normalizes whatever its backend returns (links-only, answer-first,
content-bearing) into ONE :class:`SearchResult`. :meth:`capabilities` lets callers
degrade gracefully — a no-recency provider gets client-side date-sorting, a
no-content provider must ``web_fetch`` to read a result body.

The latency-vs-quality dial each backend ships (Tavily ``search_depth``, Exa
``type``, Perplexity ``search_context_size``) is normalized to ONE ``depth``
(``quick`` / ``balanced`` / ``deep``); each adapter maps it onto its native enum.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

# The normalized latency-vs-quality dial. Every adapter maps these three onto its
# native enum; callers never hardcode a backend-specific mode.
DEPTHS: tuple[str, ...] = ("quick", "balanced", "deep")
VALID_DEPTHS = frozenset(DEPTHS)
DEFAULT_DEPTH = "balanced"


@dataclass
class SearchCapabilities:
    """What a search provider can do — callers read this to degrade gracefully.

    * ``returns_content`` — results carry extracted body text (``raw_content``)
      so the caller need not ``web_fetch`` each link.
    * ``returns_answer`` — the backend synthesizes a direct answer (Perplexity,
      Tavily ``include_answer``).
    * ``returns_highlights`` — per-result relevant snippets/highlights (Exa).
    * ``supports_recency`` — a recency/freshness filter is honored server-side;
      else the caller date-sorts client-side.
    * ``supports_domains`` — server-side include/exclude-domain filtering.
    * ``supports_fetch`` — the provider exposes single-URL content extraction
      (implements :meth:`SearchProvider.fetch`), bindable to ``fetch-article``.
    * ``depths`` — the subset of :data:`DEPTHS` this provider distinguishes
      (a backend with no dial advertises just ``("balanced",)``).
    * ``keyless`` — the provider runs with NO API key / config (a zero-config
      floor). Core uses this to pick the out-of-box fallback provider WITHOUT
      naming a specific vendor: keyless providers sort last in the implicit
      fallback (so a keyed/configured provider always wins when present) and are
      the retry target when a bound provider errors. This is the role DuckDuckGo
      fills — declared by the provider, not hard-coded in core.
    """

    returns_content: bool = False
    returns_answer: bool = False
    returns_highlights: bool = False
    supports_recency: bool = False
    supports_domains: bool = False
    supports_fetch: bool = False
    depths: tuple[str, ...] = DEPTHS
    keyless: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "returns_content": self.returns_content,
            "returns_answer": self.returns_answer,
            "returns_highlights": self.returns_highlights,
            "supports_recency": self.supports_recency,
            "supports_domains": self.supports_domains,
            "supports_fetch": self.supports_fetch,
            "depths": list(self.depths),
            "keyless": self.keyless,
        }


@dataclass
class SearchHit:
    """One normalized result. ``raw_content`` is populated only by content-bearing
    providers; a links-only backend leaves it empty and the fetch pipeline fills it
    on demand."""

    url: str
    title: str = ""
    snippet: str = ""
    score: float | None = None
    published_date: str | None = None
    raw_content: str = ""

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"url": self.url, "title": self.title, "snippet": self.snippet}
        if self.score is not None:
            d["score"] = self.score
        if self.published_date:
            d["published_date"] = self.published_date
        if self.raw_content:
            d["raw_content"] = self.raw_content
        return d


@dataclass
class SearchResult:
    """The ONE normalized search shape across all backends.

    ``answer`` is set only by answer-first providers; ``sources`` is the citation
    URL list (deduped, in result order) every caller can attribute against.
    """

    results: list[SearchHit] = field(default_factory=list)
    answer: str = ""
    provider: str = ""
    query: str = ""
    depth: str = DEFAULT_DEPTH

    @property
    def sources(self) -> list[str]:
        """Citation source list — result URLs in order, deduped."""
        seen: set[str] = set()
        out: list[str] = []
        for h in self.results:
            if h.url and h.url not in seen:
                seen.add(h.url)
                out.append(h.url)
        return out

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "results": [h.to_dict() for h in self.results],
            "sources": self.sources,
            "provider": self.provider,
            "query": self.query,
            "depth": self.depth,
        }
        if self.answer:
            d["answer"] = self.answer
        return d


@dataclass
class FetchResult:
    """Single-URL content extraction result (for providers that ``supports_fetch``).

    The native fetch pipeline (§4) returns this same shape, so a provider ``fetch``
    and the native pipeline are interchangeable to callers. ``next_index`` is set
    when the content was truncated to ``max_tokens`` — call again with
    ``start_index=next_index`` to page through (the verified MCP-fetch pattern).
    """

    url: str
    content: str = ""
    title: str = ""
    char_count: int = 0
    truncated: bool = False
    next_index: int | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "url": self.url,
            "content": self.content,
            "title": self.title,
            "char_count": self.char_count,
            "truncated": self.truncated,
        }
        if self.next_index is not None:
            d["next_index"] = self.next_index
        return d


class SearchProvider(ABC):
    """Provider interface for web-search backends (the Search entity)."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Stable identifier (matches the bundled extension/manifest name)."""
        ...

    @property
    @abstractmethod
    def display_name(self) -> str: ...

    @abstractmethod
    async def is_available(self) -> bool:
        """Whether this provider can serve right now (credential/endpoint resolves)."""
        ...

    @abstractmethod
    def capabilities(self) -> SearchCapabilities: ...

    @abstractmethod
    async def search(
        self,
        query: str,
        *,
        depth: str = DEFAULT_DEPTH,
        recency: str | None = None,
        domains: list[str] | None = None,
        max_results: int = 10,
    ) -> SearchResult:
        """Run a search and return the normalized result."""
        ...

    async def fetch(
        self,
        url: str,
        *,
        max_tokens: int = 0,
        start_index: int = 0,
    ) -> FetchResult:
        """Single-URL content extraction. Only providers whose
        :attr:`SearchCapabilities.supports_fetch` is True implement this; the
        default raises so a mis-binding fails loudly rather than silently."""
        raise NotImplementedError(f"{self.name} does not support fetch")

    def normalize_depth(self, depth: str | None) -> str:
        """Clamp a requested depth to one this provider distinguishes.

        Falls back to :data:`DEFAULT_DEPTH` (or the provider's nearest advertised
        depth) for an unknown/unsupported value — never raises, so a caller's
        coarse dial always resolves to a real backend mode.
        """
        caps = self.capabilities()
        d = (depth or DEFAULT_DEPTH).strip().lower()
        if d in caps.depths:
            return d
        if DEFAULT_DEPTH in caps.depths:
            return DEFAULT_DEPTH
        return caps.depths[0] if caps.depths else DEFAULT_DEPTH

    def info(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "display_name": self.display_name,
            "capabilities": self.capabilities().to_dict(),
        }

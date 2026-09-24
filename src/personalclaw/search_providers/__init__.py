"""Search providers — the pluggable Search entity (web search + optional fetch).

Search is a first-class entity (mirrors Model): providers are configured +
activated and bound to use-cases (search-general / search-news / search-financial
/ fetch-article) in Settings → Search; the ``web_search`` / ``web_fetch`` tools and
the research loop resolve a use-case to a provider through
:func:`resolve_search_provider_for_use_case`.
"""

from personalclaw.search_providers.base import (
    DEFAULT_DEPTH,
    DEPTHS,
    FetchResult,
    SearchCapabilities,
    SearchHit,
    SearchProvider,
    SearchResult,
)

__all__ = [
    "DEFAULT_DEPTH",
    "DEPTHS",
    "FetchResult",
    "SearchCapabilities",
    "SearchHit",
    "SearchProvider",
    "SearchResult",
]

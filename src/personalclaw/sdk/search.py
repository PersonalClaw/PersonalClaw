"""SDK: the search-provider ABC + data types + the use-case-bound resolution helpers.

Stable re-export of ``personalclaw.search_providers.*`` — an app imports these, not the
core module directly, so the core path can move without breaking installed apps. Covers
the ABC/types a search adapter implements PLUS the resolution surface a tool provider
uses to run a search over whatever the user bound in Settings → Search
(``search_with_fallback`` + the use-case vocabulary).
"""

from personalclaw.search_providers.base import (  # noqa: F401
    DEFAULT_DEPTH,
    VALID_DEPTHS,
    SearchProvider,
    SearchCapabilities,
    SearchHit,
    SearchResult,
    FetchResult,
)
from personalclaw.search_providers.registry import search_with_fallback  # noqa: F401
from personalclaw.search_providers.use_cases import (  # noqa: F401
    DEFAULT_SEARCH_USE_CASE,
    VALID_SEARCH_USE_CASES,
)

__all__ = [
    'SearchProvider', 'SearchCapabilities', 'SearchHit', 'SearchResult',
    'FetchResult', 'DEFAULT_DEPTH', 'VALID_DEPTHS',
    'search_with_fallback', 'DEFAULT_SEARCH_USE_CASE', 'VALID_SEARCH_USE_CASES',
]

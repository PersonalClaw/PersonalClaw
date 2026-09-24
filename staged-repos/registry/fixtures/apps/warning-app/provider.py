"""The clean fixture's provider. Deliberately boring: nothing here trips a
scanner rule, so this fixture's verdict is `clean` for a reason a reader can see.
"""

from __future__ import annotations


class FixtureSearchProvider:
    """Returns one canned result. It is a fixture, not a search engine."""

    name = "registry-fixture-warning"

    def search(self, query: str, limit: int = 5) -> list[dict[str, str]]:
        return [{"title": f"fixture result for {query}", "url": "https://example.invalid/"}][:limit]


def create_provider() -> FixtureSearchProvider:
    return FixtureSearchProvider()

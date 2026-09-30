"""A search provider's key is checked, its state is what was measured, and a fallback is said.

Settings → Search read "ready" for a search app whose key was merely present: Brave Search with a
key it refused read the same as one that worked, and nothing anywhere tried the key (model
providers have a connection test, channels a Test). And when a keyed search failed, the search
quietly came back from the keyless engine instead, with nothing telling the agent or the user that
the answer came from somewhere else.

Now each provider carries the outcome of its last search, measured and never inferred from its
settings: the Test (``POST /api/search/providers/{name}/test``) runs one small search now, and every
real search records how it went. A provider nothing has measured says so. A search that falls back
to the keyless engine carries a notice naming the provider that failed and the one that answered.
"""

from __future__ import annotations

from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.search_providers import registry
from personalclaw.search_providers.base import (
    SearchCapabilities,
    SearchHit,
    SearchProvider,
    SearchResult,
)


class _Provider(SearchProvider):
    """A search provider whose searches answer, or fail with the words a refused key gives."""

    def __init__(
        self, name: str, display: str, *, keyless: bool = False, refuses: str = ""
    ) -> None:
        self._name = name
        self._display = display
        self._keyless = keyless
        self.refuses = refuses
        self.queries: list[str] = []

    @property
    def name(self) -> str:
        return self._name

    @property
    def display_name(self) -> str:
        return self._display

    async def is_available(self) -> bool:
        return True

    def capabilities(self) -> SearchCapabilities:
        return SearchCapabilities(keyless=self._keyless)

    async def search(self, query: str, **kw: Any) -> SearchResult:
        self.queries.append(query)
        if self.refuses:
            raise RuntimeError(self.refuses)
        return SearchResult(
            results=[SearchHit(url=f"https://{self._name}.example/1", title="a result")],
            provider=self._name,
            query=query,
        )


REFUSED = "Brave Search refused the API key (HTTP 401)."


@pytest.fixture
def providers(monkeypatch: pytest.MonkeyPatch) -> dict[str, _Provider]:
    monkeypatch.setattr(registry, "_providers", {})
    monkeypatch.setattr(registry, "_provider_app", {})
    monkeypatch.setattr(registry, "_checks", {})
    brave = _Provider("brave", "Brave Search", refuses=REFUSED)
    ddg = _Provider("duckduckgo", "DuckDuckGo", keyless=True)
    registry.register_provider(brave, app="brave-search")
    registry.register_provider(ddg, app="duckduckgo-search")
    return {"brave": brave, "duckduckgo": ddg}


async def _client():
    from personalclaw.dashboard.handlers.search_registry import register_search_registry_routes

    app = web.Application()
    register_search_registry_routes(app)
    return TestClient(TestServer(app))


async def _listed(client) -> dict[str, dict]:
    resp = await client.get("/api/search/providers")
    assert resp.status == 200
    return {p["name"]: p for p in (await resp.json())["providers"]}


@pytest.mark.asyncio
async def test_a_provider_nothing_has_measured_is_not_called_ready(providers):
    async with await _client() as client:
        rows = await _listed(client)

    assert rows["brave"]["check"] is None, "a key that was never tried was reported as working"
    assert rows["brave"]["app"] == "brave-search"
    assert providers["brave"].queries == [], "reading the list must not spend a search"


@pytest.mark.asyncio
async def test_the_test_tries_the_key_and_says_what_it_answered(providers):
    async with await _client() as client:
        resp = await client.post("/api/search/providers/brave/test")
        assert resp.status == 200, await resp.text()
        tested = (await resp.json())["provider"]
        rows = await _listed(client)

    assert len(providers["brave"].queries) == 1, "the Test did not run a search"
    assert tested["check"]["state"] == "failed"
    assert REFUSED in tested["check"]["detail"]
    assert rows["brave"]["check"] == tested["check"], "the list does not show what the Test found"


@pytest.mark.asyncio
async def test_a_key_that_works_reads_as_working(providers):
    providers["brave"].refuses = ""
    async with await _client() as client:
        tested = (await (await client.post("/api/search/providers/brave/test")).json())["provider"]

    assert tested["check"]["state"] == "ok"
    assert tested["check"]["checked_at"] is not None


@pytest.mark.asyncio
async def test_testing_a_provider_that_is_not_there_is_a_404(providers):
    async with await _client() as client:
        resp = await client.post("/api/search/providers/nowhere/test")
        body = await resp.json()

    assert resp.status == 404
    assert body["error"]["code"] == "not_found"


@pytest.mark.asyncio
async def test_a_failed_keyed_search_says_the_answer_came_from_the_fallback(providers):
    result = await registry.search_with_fallback("search-general", "feedparser changelog")

    assert result is not None
    assert result.provider == "duckduckgo"
    assert result.fallback is not None, "the fallback was silent"
    assert result.fallback.provider == "brave"
    notice = result.fallback.notice
    assert "DuckDuckGo" in notice and "Brave Search" in notice, notice
    assert REFUSED in result.fallback.reason
    assert result.to_dict()["fallback"]["notice"] == notice, "the agent is not told"


@pytest.mark.asyncio
async def test_when_the_fallback_fails_too_the_error_names_both(providers):
    """Nothing answered, and the error says what each engine said, the bound one first. Naming
    only the keyless engine told the owner that a search they had bound to Brave Search went to
    DuckDuckGo, and hid why Brave Search failed."""
    providers["duckduckgo"].refuses = "DuckDuckGo did not answer (HTTP 503)."

    with pytest.raises(Exception) as caught:
        await registry.search_with_fallback("search-general", "one")

    said = str(caught.value)
    assert said.startswith("Brave Search failed: "), said
    assert REFUSED in said and "HTTP 503" in said, said
    assert said.index(REFUSED) < said.index("DuckDuckGo, tried instead, failed too"), said
    assert registry.last_check("duckduckgo").ok is False


@pytest.mark.asyncio
async def test_every_real_search_records_how_it_went(providers):
    await registry.search_with_fallback("search-general", "one")

    async with await _client() as client:
        rows = await _listed(client)

    assert rows["brave"]["check"]["state"] == "failed"
    assert REFUSED in rows["brave"]["check"]["detail"]
    assert rows["duckduckgo"]["check"]["state"] == "ok"


@pytest.mark.asyncio
async def test_a_search_that_did_not_fall_back_carries_no_notice(providers):
    providers["brave"].refuses = ""
    result = await registry.search_with_fallback("search-general", "one")

    assert result is not None and result.provider == "brave"
    assert result.fallback is None
    assert "fallback" not in result.to_dict()


@pytest.mark.asyncio
async def test_saving_a_providers_settings_forgets_what_its_old_settings_answered(providers):
    """A saved setting rebuilds the provider: its last answer was for the settings before."""
    await registry.search_with_fallback("search-general", "one")
    assert registry.last_check("brave") is not None

    registry.register_provider(_Provider("brave", "Brave Search"), app="brave-search")

    assert registry.last_check("brave") is None

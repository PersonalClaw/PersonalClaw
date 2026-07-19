"""WS3 — Settings → Search backend: the /api/search/* registry routes.

Drives the handlers directly with mocked aiohttp requests (no full server spin-up),
isolating the active-binding store to a tmp path and the provider registry to fakes.
"""

from __future__ import annotations

import json

import pytest
from aiohttp.test_utils import make_mocked_request

from personalclaw.dashboard.handlers import search_registry as sr
from personalclaw.search_providers import registry as reg
from personalclaw.search_providers import use_cases as uc
from personalclaw.search_providers.base import SearchCapabilities, SearchProvider, SearchResult


class _Fake(SearchProvider):
    def __init__(self, name, *, available=True, fetch=False):
        self._name, self._available, self._fetch = name, available, fetch

    @property
    def name(self): return self._name
    @property
    def display_name(self): return self._name.title()
    async def is_available(self): return self._available
    def capabilities(self): return SearchCapabilities(supports_fetch=self._fetch)
    async def search(self, query, **kw): return SearchResult(provider=self._name, query=query)


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(reg, "_providers", {})
    monkeypatch.setattr(uc, "_active_path", lambda: tmp_path / "active_search_providers.json")
    yield


async def _json(resp):
    return json.loads(resp.body.decode())


@pytest.mark.asyncio
async def test_providers_lists_capabilities_and_availability():
    reg.register_provider(_Fake("tavily", available=True, fetch=True))
    reg.register_provider(_Fake("searxng", available=False))
    resp = await sr.api_search_providers(make_mocked_request("GET", "/api/search/providers"))
    data = await _json(resp)
    by = {p["name"]: p for p in data["providers"]}
    assert by["tavily"]["available"] is True
    assert by["tavily"]["capabilities"]["supports_fetch"] is True
    assert by["searxng"]["available"] is False


@pytest.mark.asyncio
async def test_active_returns_all_use_cases():
    resp = await sr.api_search_active(make_mocked_request("GET", "/api/search/active"))
    data = await _json(resp)
    assert set(data["use_cases"]) == set(uc.SEARCH_USE_CASES)
    assert data["use_cases"]["search-general"] == []


@pytest.mark.asyncio
async def test_set_active_binds_provider():
    reg.register_provider(_Fake("tavily"))  # must be registered to bind (set-time validation)
    req = make_mocked_request("PUT", "/api/search/active/search-general",
                              match_info={"use_case": "search-general"})
    req.json = _async_return({"providers": ["tavily"]})
    resp = await sr.api_search_active_set(req)
    data = await _json(resp)
    assert data["ok"] is True
    assert data["providers"] == ["tavily"]
    assert uc.active_search_provider_names("search-general") == ["tavily"]


@pytest.mark.asyncio
async def test_set_active_rejects_unknown_provider():
    """Binding to a provider that isn't registered must fail-fast (400), not silently
    strand the use-case on a dead name. Regression for the set-time validation gap
    (the search sibling of model bug #16)."""
    reg.register_provider(_Fake("tavily"))
    req = make_mocked_request("PUT", "/api/search/active/search-general",
                              match_info={"use_case": "search-general"})
    req.json = _async_return({"providers": ["nosuchsearch"]})
    resp = await sr.api_search_active_set(req)
    assert resp.status == 400
    assert "Unknown search provider" in (await _json(resp))["error"]
    # nothing bound
    assert uc.active_search_provider_names("search-general") in ([], None)


@pytest.mark.asyncio
async def test_set_active_empty_clears_binding():
    uc.set_active_search_provider("search-news", "tavily")
    req = make_mocked_request("PUT", "/api/search/active/search-news",
                              match_info={"use_case": "search-news"})
    req.json = _async_return({"providers": []})
    resp = await sr.api_search_active_set(req)
    assert (await _json(resp))["providers"] == []
    # cleared → news now falls back to (empty) general
    assert uc.load_active_search_providers().get("search-news") in (None, [])


@pytest.mark.asyncio
async def test_set_active_rejects_invalid_use_case():
    req = make_mocked_request("PUT", "/api/search/active/bogus",
                              match_info={"use_case": "bogus"})
    req.json = _async_return({"providers": ["tavily"]})
    resp = await sr.api_search_active_set(req)
    assert resp.status == 400


@pytest.mark.asyncio
async def test_set_active_rejects_multiple_providers():
    req = make_mocked_request("PUT", "/api/search/active/search-general",
                              match_info={"use_case": "search-general"})
    req.json = _async_return({"providers": ["a", "b"]})
    resp = await sr.api_search_active_set(req)
    assert resp.status == 400


def _async_return(value):
    async def _f():
        return value
    return _f

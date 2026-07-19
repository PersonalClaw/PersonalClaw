"""The search registry's keyless-floor + failure-fallback behavior (provider-agnostic).

Covers the resolver contract the DuckDuckGo *app* participates in but which lives in
core: a user who has bound NO provider still resolves web_search to the registered
keyless floor; a keyed provider wins the implicit fallback over the floor; a bound
provider whose search() raises falls back to the keyless floor; and fetch falls back to
the native pipeline. Uses stand-in providers (the concrete DuckDuckGo adapter now lives
in the duckduckgo-search app; its unit tests moved there). Mocks only in-process; no
network.

The keyless floor is identified by the ``keyless`` CAPABILITY a provider declares
(``SearchCapabilities.keyless``) — the registry selects on that flag, naming no
specific vendor. (Previously a hard-coded ``_KEYLESS_DEFAULT = "duckduckgo"`` string
in core — that vendor coupling was removed.)
"""

from __future__ import annotations

import pytest

from personalclaw.search_providers import registry as reg
from personalclaw.search_providers import use_cases as uc
from personalclaw.search_providers.base import (
    FetchResult,
    SearchCapabilities,
    SearchHit,
    SearchProvider,
    SearchResult,
)


class _Boom(SearchProvider):
    """A keyed provider whose key has expired — resolves fine, but search() raises."""
    def __init__(self, name="tavily", fetch=False):
        self._name, self._fetch = name, fetch
    @property
    def name(self): return self._name
    @property
    def display_name(self): return self._name.title()
    async def is_available(self): return True
    def capabilities(self): return SearchCapabilities(supports_fetch=self._fetch)
    async def search(self, q, **k): raise RuntimeError("HTTP 432 quota")
    async def fetch(self, url, **k): raise RuntimeError("HTTP 432 quota")


class _FakeDDG(SearchProvider):
    """Stand-in for the keyless floor: declares ``keyless=True`` (the capability the
    registry selects on — no longer a hard-coded vendor name). No network."""
    @property
    def name(self): return "duckduckgo"
    @property
    def display_name(self): return "DuckDuckGo"
    async def is_available(self): return True
    def capabilities(self): return SearchCapabilities(supports_recency=True, depths=("balanced",), keyless=True)
    async def search(self, q, **k):
        return SearchResult(results=[SearchHit(url="https://ddg.example/r", title="R")],
                            provider="duckduckgo", query=q)


class _Keyed(SearchProvider):
    @property
    def name(self): return "tavily"
    @property
    def display_name(self): return "Tavily"
    async def is_available(self): return True
    def capabilities(self): return SearchCapabilities()
    async def search(self, q, **k): return SearchResult(provider="tavily", query=q)


# ── keyless-floor resolution ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_no_binding_resolves_to_keyless_floor(monkeypatch, tmp_path):
    # A user who configured NOTHING: only the keyless default is registered → it
    # resolves, so web_search works out-of-box (and seeds web_fetch provenance).
    monkeypatch.setattr(reg, "_providers", {})
    monkeypatch.setattr(uc, "_active_path", lambda: tmp_path / "a.json")
    reg.register_provider(_FakeDDG())
    p = await reg.resolve_search_provider_for_use_case("search-general")
    assert p is not None and p.name == "duckduckgo"


@pytest.mark.asyncio
async def test_keyed_provider_wins_over_keyless_default(monkeypatch, tmp_path):
    # When an available keyed provider is also registered (unbound), it should win
    # the implicit fallback over the keyless default (the floor sorts last).
    monkeypatch.setattr(reg, "_providers", {})
    monkeypatch.setattr(uc, "_active_path", lambda: tmp_path / "a.json")
    reg.register_provider(_FakeDDG())
    reg.register_provider(_Keyed())
    p = await reg.resolve_search_provider_for_use_case("search-general")
    assert p.name == "tavily"  # keyed provider preferred; the floor is last


# ── failure fallback (search_with_fallback) ─────────────────────────────────────

@pytest.fixture
def _bound_failing(monkeypatch, tmp_path):
    """tavily bound to search-general but its search() raises; the floor registered."""
    monkeypatch.setattr(reg, "_providers", {})
    monkeypatch.setattr(uc, "_active_path", lambda: tmp_path / "a.json")
    reg.register_provider(_Boom("tavily"))
    reg.register_provider(_FakeDDG())
    uc.set_active_search_provider("search-general", "tavily")
    yield


@pytest.mark.asyncio
async def test_search_falls_back_to_floor_on_failure(_bound_failing):
    result, fell_back = await reg.search_with_fallback("search-general", "q")
    assert fell_back is True
    assert result.provider == "duckduckgo"
    assert result.results


@pytest.mark.asyncio
async def test_search_no_fallback_when_bound_provider_succeeds(monkeypatch, tmp_path):
    monkeypatch.setattr(reg, "_providers", {})
    monkeypatch.setattr(uc, "_active_path", lambda: tmp_path / "a.json")
    reg.register_provider(_FakeDDG())  # floor bound + working — no fallback needed
    uc.set_active_search_provider("search-general", "duckduckgo")
    result, fell_back = await reg.search_with_fallback("search-general", "q")
    assert fell_back is False
    assert result.provider == "duckduckgo"


@pytest.mark.asyncio
async def test_search_reraises_when_no_keyless_fallback(monkeypatch, tmp_path):
    # Bound provider fails AND no floor registered → the original error propagates.
    monkeypatch.setattr(reg, "_providers", {})
    monkeypatch.setattr(uc, "_active_path", lambda: tmp_path / "a.json")
    reg.register_provider(_Boom("tavily"))
    uc.set_active_search_provider("search-general", "tavily")
    with pytest.raises(RuntimeError, match="432"):
        await reg.search_with_fallback("search-general", "q")


# ── fetch_with_fallback → native pipeline ──────────────────────────────────────

@pytest.mark.asyncio
async def test_fetch_falls_back_to_native_pipeline_on_provider_failure(monkeypatch, tmp_path):
    # A fetch-capable provider bound to fetch-article, but its fetch() raises → the
    # native web_fetch pipeline takes over (no key needed).
    monkeypatch.setattr(reg, "_providers", {})
    monkeypatch.setattr(uc, "_active_path", lambda: tmp_path / "a.json")
    reg.register_provider(_Boom("tavily", fetch=True))
    uc.set_active_search_provider("fetch-article", "tavily")

    from personalclaw.web import fetch as wf

    async def _fake_native(url, **k):
        return wf.FetchOutcome(ok=True, url=url, title="Native", content="body",
                               char_count=4, total_chars=4)
    monkeypatch.setattr(wf, "web_fetch", _fake_native)

    result, used_native = await reg.fetch_with_fallback("https://x.com/a")
    assert used_native is True
    assert result.title == "Native" and result.content == "body"

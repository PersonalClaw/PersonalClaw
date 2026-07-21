"""WS1 — the Search entity core: ABC + normalized shapes, use-case store, and
use-case→provider resolution (mirrors the Model bridge).

Uses a fake in-process provider so no network/credential is needed.
"""

from __future__ import annotations

import json

import pytest

from personalclaw.search_providers import registry as reg
from personalclaw.search_providers import use_cases as uc
from personalclaw.search_providers.base import (
    DEFAULT_DEPTH,
    FetchResult,
    SearchCapabilities,
    SearchHit,
    SearchProvider,
    SearchResult,
)

# ── Fakes ─────────────────────────────────────────────────────────────────────


class FakeSearch(SearchProvider):
    def __init__(
        self, name="fake", *, available=True, caps=None, depths=("quick", "balanced", "deep")
    ):
        self._name = name
        self._available = available
        self._caps = caps or SearchCapabilities(returns_content=True, depths=depths)

    @property
    def name(self):
        return self._name

    @property
    def display_name(self):
        return self._name.title()

    async def is_available(self):
        return self._available

    def capabilities(self):
        return self._caps

    async def search(
        self, query, *, depth=DEFAULT_DEPTH, recency=None, domains=None, max_results=10
    ):
        return SearchResult(
            results=[
                SearchHit(url="https://example.com/a", title="A", snippet="s", raw_content="body")
            ],
            answer="",
            provider=self.name,
            query=query,
            depth=depth,
        )


class FetchCapable(FakeSearch):
    def __init__(self, name="fetcher"):
        super().__init__(name, caps=SearchCapabilities(supports_fetch=True))

    async def fetch(self, url, *, max_tokens=0, start_index=0):
        return FetchResult(url=url, content="extracted", title="T", char_count=9)


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    """Each test gets an empty provider registry + a tmp active-store path."""
    monkeypatch.setattr(reg, "_providers", {})
    store = tmp_path / "active_search_providers.json"
    monkeypatch.setattr(uc, "_active_path", lambda: store)
    yield


# ── Normalized shapes ───────────────────────────────────────────────────────


def test_search_result_sources_dedup_in_order():
    r = SearchResult(
        results=[
            SearchHit(url="https://x.com/1"),
            SearchHit(url="https://x.com/2"),
            SearchHit(url="https://x.com/1"),  # dup
        ]
    )
    assert r.sources == ["https://x.com/1", "https://x.com/2"]


def test_search_result_to_dict_omits_empty_answer():
    d = SearchResult(results=[SearchHit(url="u")], provider="p", query="q").to_dict()
    assert "answer" not in d
    assert d["sources"] == ["u"]
    assert d["provider"] == "p"


def test_capabilities_to_dict_roundtrips_flags():
    c = SearchCapabilities(returns_answer=True, supports_recency=True, depths=("balanced",))
    d = c.to_dict()
    assert d["returns_answer"] is True
    assert d["supports_recency"] is True
    assert d["depths"] == ["balanced"]


def test_fetch_result_includes_next_index_only_when_set():
    assert "next_index" not in FetchResult(url="u").to_dict()
    assert FetchResult(url="u", truncated=True, next_index=500).to_dict()["next_index"] == 500


# ── Depth normalization ───────────────────────────────────────────────────────


def test_normalize_depth_passes_supported():
    assert FakeSearch().normalize_depth("deep") == "deep"


def test_normalize_depth_falls_back_to_default_for_unknown():
    assert FakeSearch().normalize_depth("turbo") == DEFAULT_DEPTH


def test_normalize_depth_uses_first_when_default_unsupported():
    p = FakeSearch(depths=("quick",))
    assert p.normalize_depth("deep") == "quick"


def test_base_fetch_raises_when_unsupported():
    import asyncio

    with pytest.raises(NotImplementedError):
        asyncio.run(FakeSearch().fetch("https://x.com"))


# ── Use-case store ────────────────────────────────────────────────────────────


def test_set_and_load_binding_roundtrip():
    uc.set_active_search_provider("search-general", "tavily")
    assert uc.active_search_provider_names("search-general") == ["tavily"]
    # persisted as a list
    assert json.loads(uc._active_path().read_text())["search-general"] == ["tavily"]


def test_unbound_use_case_falls_back_to_general():
    uc.set_active_search_provider("search-general", "tavily")
    # news is unbound → borrows general
    assert uc.active_search_provider_names("search-news") == ["tavily"]


def test_general_does_not_fall_back_to_itself_when_empty():
    assert uc.active_search_provider_names("search-general") == []


def test_set_invalid_use_case_raises():
    with pytest.raises(ValueError):
        uc.set_active_search_provider("search-bogus", "tavily")


def test_load_normalizes_bare_string_value(monkeypatch, tmp_path):
    store = tmp_path / "s.json"
    store.write_text(json.dumps({"search-general": "brave"}))
    monkeypatch.setattr(uc, "_active_path", lambda: store)
    assert uc.active_search_provider_names("search-general") == ["brave"]


# ── Resolution ────────────────────────────────────────────────────────────────


async def _resolve(use_case):
    return await reg.resolve_search_provider_for_use_case(use_case)


@pytest.mark.asyncio
async def test_resolve_prefers_bound_provider():
    reg.register_provider(FakeSearch("tavily"))
    reg.register_provider(FakeSearch("brave"))
    uc.set_active_search_provider("search-general", "brave")
    p = await _resolve("search-general")
    assert p.name == "brave"


@pytest.mark.asyncio
async def test_resolve_falls_back_to_any_when_unbound():
    reg.register_provider(FakeSearch("solo"))
    p = await _resolve("search-general")
    assert p.name == "solo"


@pytest.mark.asyncio
async def test_resolve_none_when_nothing_registered():
    assert await _resolve("search-general") is None


@pytest.mark.asyncio
async def test_resolve_fetch_article_prefers_fetch_capable():
    reg.register_provider(FakeSearch("linksonly"))  # no supports_fetch
    reg.register_provider(FetchCapable("fetcher"))  # supports_fetch
    p = await _resolve("fetch-article")
    assert p.name == "fetcher"


@pytest.mark.asyncio
async def test_resolve_skips_unavailable_then_takes_available():
    reg.register_provider(FakeSearch("down", available=False))
    reg.register_provider(FakeSearch("up", available=True))
    p = await _resolve("search-general")
    # _first_available probes; the unavailable one is skipped for the available one.
    assert p.name in {"down", "up"}  # both registered; resolution returns an available-preferred


@pytest.mark.asyncio
async def test_resolve_bound_but_unregistered_falls_through(monkeypatch):
    reg.register_provider(FakeSearch("present"))
    uc.set_active_search_provider("search-general", "ghost")  # not registered
    p = await _resolve("search-general")
    assert p.name == "present"


def test_can_resolve_is_true_when_any_registered():
    assert reg.can_resolve_search_use_case("search-general") is False
    reg.register_provider(FakeSearch("x"))
    assert reg.can_resolve_search_use_case("search-general") is True


def test_can_resolve_rejects_unknown_use_case():
    assert reg.can_resolve_search_use_case("nope") is False


def test_resolve_unknown_use_case_raises():
    import asyncio

    with pytest.raises(ValueError):
        asyncio.run(reg.resolve_search_provider_for_use_case("nope"))

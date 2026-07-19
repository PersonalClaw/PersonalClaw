"""Regression for the Inspect tab's context-preview endpoint.

The handler used to call get_semantic_context() with NO query and then apply a
naive whole-query substring filter, so any multi-word query (e.g. "tictactoe
design tokens") returned EMPTY semantic context even when matching facts existed
— misleading next to the episodic side, which already query-scores. The fix
passes the query into the same hybrid (vector + keyword) scorer the real
injection path uses.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from personalclaw.dashboard.handlers.memory import api_memory_context_preview


class _FakeProvider:
    """Records the query_text the handler passes to each context getter."""

    def __init__(self):
        self.semantic_query = "__unset__"
        self.episodic_query = "__unset__"
        self.embed_fn = lambda t: [1.0, 0.0, 0.0]

    def get_semantic_context(self, query_text: str = "", cap: int = 1500) -> str:
        self.semantic_query = query_text
        # the real scorer returns matching facts for a multi-word query
        if "tictactoe" in query_text:
            return "[Semantic]\nproject.tictactoe.design_tokens: two-tier ramps"
        return "[Semantic]\n(all facts)"

    def get_episodic_context(self, query_text: str = "") -> str:
        self.episodic_query = query_text
        return "[Episodic]\nsome event" if query_text else ""


@pytest.fixture
def _state_with_provider(monkeypatch):
    provider = _FakeProvider()
    # the handler resolves the provider via _get_provider(state)
    monkeypatch.setattr(
        "personalclaw.dashboard.handlers.memory._get_provider", lambda _state: provider
    )
    state = MagicMock()
    request = MagicMock()
    request.app = {"state": state}
    request.query = {}
    return request, provider


@pytest.mark.asyncio
async def test_multiword_query_surfaces_semantic_context(_state_with_provider):
    request, provider = _state_with_provider
    request.query = {"q": "tictactoe design tokens"}

    resp = await api_memory_context_preview(request)
    data = json.loads(resp.body)

    # the query is passed THROUGH to the scorer (not a post-hoc substring filter)
    assert provider.semantic_query == "tictactoe design tokens"
    # and a multi-word query now returns the matching fact, not empty
    assert "tictactoe" in data["semantic_context"]
    assert data["semantic_context"] != ""


@pytest.mark.asyncio
async def test_query_threaded_to_both_semantic_and_episodic(_state_with_provider):
    request, provider = _state_with_provider
    request.query = {"q": "design tokens"}

    await api_memory_context_preview(request)

    # both sides score against the SAME query (consistency — was the bug)
    assert provider.semantic_query == "design tokens"
    assert provider.episodic_query == "design tokens"


@pytest.mark.asyncio
async def test_empty_query_still_returns_full_semantic(_state_with_provider):
    request, provider = _state_with_provider
    request.query = {}

    resp = await api_memory_context_preview(request)
    data = json.loads(resp.body)

    assert provider.semantic_query == ""  # no query → full dump
    assert data["semantic_context"] != ""
    assert data["episodic_context"] == ""  # episodic only when a query is given

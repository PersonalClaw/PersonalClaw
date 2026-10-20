"""Batched link-summary resolve endpoint.

POST /api/chat/nav/resolve-links takes a batch of {url, context} and returns
{summaries: [...]} positionally aligned to the input. The whole batch is one
stateless Model-entity call (anti-N+1); a model failure soft-fails to empty
summaries so the UI keeps its structured fallback labels. Output is redacted.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.dashboard.chat_handlers import (
    _NAV_MAX_LINKS,
    _build_nav_links_prompt,
    _parse_nav_links_response,
    api_nav_resolve_links,
)

# ── pure helpers ──


class TestNavLinkHelpers:
    def test_prompt_numbers_each_link_in_order(self) -> None:
        prompt = _build_nav_links_prompt(
            [
                {"url": "https://github.com/a/b", "context": "the repo"},
                {"url": "https://x.io/y", "context": ""},
            ]
        )
        assert "0: https://github.com/a/b" in prompt
        assert "context: the repo" in prompt
        assert "1: https://x.io/y" in prompt

    def test_parse_aligns_by_index(self) -> None:
        assert _parse_nav_links_response("0: Repo A/B\n1: Y Page", 2) == ["Repo A/B", "Y Page"]

    def test_parse_fills_gaps_with_empty(self) -> None:
        # Model only answered index 1 → index 0 stays empty, alignment preserved.
        assert _parse_nav_links_response("1: Second Only", 2) == ["", "Second Only"]

    def test_parse_ignores_out_of_range_and_garbage(self) -> None:
        assert _parse_nav_links_response("5: too big\nnonsense\n0: Good", 2) == ["Good", ""]

    def test_parse_redacts_credentials_in_titles(self) -> None:
        out = _parse_nav_links_response("0: key AKIAIOSFODNN7EXAMPLE here", 1)
        assert "AKIAIOSFODNN7EXAMPLE" not in out[0]
        assert "[REDACTED" in out[0]


# ── handler ──


async def _client() -> TestClient:
    app = web.Application()
    app["state"] = MagicMock()
    app.router.add_post("/api/chat/nav/resolve-links", api_nav_resolve_links)
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


@pytest.mark.asyncio
async def test_resolves_batch_with_single_model_call() -> None:
    client = await _client()
    mock = AsyncMock(return_value="0: Anthropic SDK\n1: Example Page")
    try:
        with patch("personalclaw.llm_helpers.one_shot_completion", mock):
            resp = await client.post(
                "/api/chat/nav/resolve-links",
                json={
                    "links": [
                        {
                            "url": "https://github.com/anthropics/anthropic-sdk-python",
                            "context": "the sdk",
                        },
                        {"url": "https://example.com/page", "context": ""},
                    ]
                },
            )
            assert resp.status == 200
            body = await resp.json()
            assert body["summaries"] == ["Anthropic SDK", "Example Page"]
        mock.assert_awaited_once()  # one call for the whole batch, not per-link
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_empty_links_skips_model_call() -> None:
    client = await _client()
    mock = AsyncMock(return_value="should not be called")
    try:
        with patch("personalclaw.llm_helpers.one_shot_completion", mock):
            resp = await client.post("/api/chat/nav/resolve-links", json={"links": []})
            assert resp.status == 200
            assert (await resp.json())["summaries"] == []
        mock.assert_not_awaited()
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_model_failure_soft_fails_to_empty_summaries() -> None:
    client = await _client()
    mock = AsyncMock(side_effect=RuntimeError("no provider configured"))
    try:
        with patch("personalclaw.llm_helpers.one_shot_completion", mock):
            resp = await client.post(
                "/api/chat/nav/resolve-links",
                json={
                    "links": [
                        {"url": "https://x.io/a", "context": ""},
                        {"url": "https://x.io/b", "context": ""},
                    ]
                },
            )
            assert resp.status == 200
            # Soft-fail: aligned-length list of empty strings (UI keeps fallback labels).
            assert (await resp.json())["summaries"] == ["", ""]
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_caps_link_count_before_model_call() -> None:
    client = await _client()
    captured = {}

    async def _capture(prompt, *, use_case="background"):
        captured["prompt"] = prompt
        # Echo a title for every possible index so cap is the only limiter.
        return "\n".join(f"{i}: t{i}" for i in range(_NAV_MAX_LINKS + 50))

    try:
        with patch("personalclaw.llm_helpers.one_shot_completion", _capture):
            resp = await client.post(
                "/api/chat/nav/resolve-links",
                json={
                    "links": [
                        {"url": f"https://x.io/{i}", "context": ""}
                        for i in range(_NAV_MAX_LINKS + 20)
                    ]
                },
            )
            assert resp.status == 200
            body = await resp.json()
            assert len(body["summaries"]) == _NAV_MAX_LINKS  # request truncated to the cap
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_rejects_non_list_links() -> None:
    client = await _client()
    try:
        resp = await client.post("/api/chat/nav/resolve-links", json={"links": "nope"})
        assert resp.status == 400
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_drops_entries_without_url() -> None:
    client = await _client()
    mock = AsyncMock(return_value="0: Only Valid")
    try:
        with patch("personalclaw.llm_helpers.one_shot_completion", mock):
            resp = await client.post(
                "/api/chat/nav/resolve-links",
                json={"links": [{"context": "no url"}, {"url": "https://x.io/a", "context": ""}]},
            )
            assert resp.status == 200
            # One valid link survived → one summary.
            assert (await resp.json())["summaries"] == ["Only Valid"]
    finally:
        await client.close()

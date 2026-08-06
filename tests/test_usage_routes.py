"""COST-AND-TOKEN-OBSERVABILITY CATO-5 — the /api/usage read routes.

Read-only rollup + totals over the usage ledger, with the §2.2
{error:{code,message}} envelope on a bad group_by.
"""

from __future__ import annotations

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw import usage_ledger as ul
from personalclaw.dashboard.handlers.usage import register_usage_routes
from personalclaw.usage_ledger import TurnUsage


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    return tmp_path


def _seed():
    for src, cost in (("chat", 1.0), ("chat", 2.0), ("subagent", 0.5)):
        ul.record_turn(
            TurnUsage(
                ts="2026-08-06T12:00:00+00:00",
                session_key="s1",
                source=src,
                agent="",
                provider="anthropic",
                model="claude-opus-4.5",
                input_tokens=100,
                output_tokens=20,
                cost_usd=cost,
                priced=True,
            )
        )


async def _client() -> TestClient:
    app = web.Application()
    register_usage_routes(app)
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


@pytest.mark.asyncio
async def test_rollup_by_source(_home):
    _seed()
    c = await _client()
    try:
        resp = await c.get("/api/usage/rollup?group_by=source")
        assert resp.status == 200
        body = await resp.json()
        assert body["group_by"] == "source"
        by = {r["source"]: r for r in body["rows"]}
        assert by["chat"]["cost_usd"] == 3.0 and by["chat"]["turns"] == 2
        assert by["subagent"]["cost_usd"] == 0.5
    finally:
        await c.close()


@pytest.mark.asyncio
async def test_rollup_defaults_to_model(_home):
    _seed()
    c = await _client()
    try:
        body = await (await c.get("/api/usage/rollup")).json()
        assert body["group_by"] == "model"
        assert body["rows"][0]["model"] == "claude-opus-4.5"
    finally:
        await c.close()


@pytest.mark.asyncio
async def test_bad_group_by_is_400_envelope(_home):
    c = await _client()
    try:
        resp = await c.get("/api/usage/rollup?group_by=nonsense")
        assert resp.status == 400
        body = await resp.json()
        assert body["error"]["code"] == "bad_request"
        assert "group_by" in body["error"]["message"]
    finally:
        await c.close()


@pytest.mark.asyncio
async def test_totals(_home):
    _seed()
    c = await _client()
    try:
        body = await (await c.get("/api/usage/totals")).json()
        assert body["totals"]["cost_usd"] == 3.5
        assert body["totals"]["turns"] == 3
        assert body["totals"]["priced"] is True
    finally:
        await c.close()


@pytest.mark.asyncio
async def test_window_filter_threads_through(_home):
    ul.record_turn(
        TurnUsage(
            ts="2026-08-01T00:00:00+00:00",
            session_key="s",
            source="chat",
            agent="",
            provider="p",
            model="claude-opus-4.5",
            cost_usd=1.0,
            priced=True,
        )
    )
    ul.record_turn(
        TurnUsage(
            ts="2026-08-05T00:00:00+00:00",
            session_key="s",
            source="chat",
            agent="",
            provider="p",
            model="claude-opus-4.5",
            cost_usd=2.0,
            priced=True,
        )
    )
    c = await _client()
    try:
        body = await (await c.get("/api/usage/totals?since=2026-08-03T00:00:00+00:00")).json()
        assert body["totals"]["cost_usd"] == 2.0 and body["totals"]["turns"] == 1
    finally:
        await c.close()


@pytest.mark.asyncio
async def test_empty_ledger_is_ok_not_error(_home):
    c = await _client()
    try:
        r = await c.get("/api/usage/rollup?group_by=source")
        assert r.status == 200 and (await r.json())["rows"] == []
        t = await c.get("/api/usage/totals")
        assert t.status == 200 and (await t.json())["totals"]["turns"] == 0
    finally:
        await c.close()


@pytest.mark.asyncio
async def test_session_filter_threads_through(_home):
    for sk, cost in (("dashboard:a", 1.0), ("dashboard:a", 2.0), ("dashboard:b", 0.5)):
        ul.record_turn(
            TurnUsage(
                ts="2026-08-06T12:00:00+00:00",
                session_key=sk,
                source="chat",
                agent="",
                provider="anthropic",
                model="claude-opus-4.5",
                input_tokens=100,
                output_tokens=20,
                cost_usd=cost,
                priced=True,
            )
        )
    c = await _client()
    try:
        body = await (await c.get("/api/usage/totals?session=dashboard:a")).json()
        assert body["session"] == "dashboard:a"
        assert body["totals"]["cost_usd"] == 3.0 and body["totals"]["turns"] == 2
    finally:
        await c.close()

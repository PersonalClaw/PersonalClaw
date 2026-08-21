"""COST-AND-TOKEN-OBSERVABILITY CATO-5 — the /api/usage read routes.

Read-only rollup + totals over the usage ledger, with the shared
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


# ── GET /api/usage — the per-day spend fold (MRT-3) ─────────────────────────────────────
#
# The sibling routes above read the retained tail of the same ledger. This one reads the DURABLE
# per-day fold over it, grouped into the purpose vocabulary — and reports the guarded-attempt spend
# it deliberately does NOT sum (a loop's inner inference is in both records, with no shared id).


def _today() -> str:
    from datetime import datetime, timezone

    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")


def _seed_attempt(home, *, use_case: str, dollars: float, provider="anthropic", model="claude-x"):
    """Append one guarded-attempt row — the axis the fold censuses instead of summing."""
    import json
    import time

    rec = {
        "audit_id": "a1",
        "ts": time.time(),
        "use_case": use_case,
        "provider": provider,
        "model": model,
        "attempt": 1,
        "tokens_in": 100,
        "tokens_out": 10,
        "dollars_est": dollars,
        "estimated": True,
        "passed": True,
    }
    with (home / "model_calls.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec) + "\n")


def _seed_turn(source="chat", cost=1.0, model="claude-x"):
    ul.record_turn(
        TurnUsage(
            ts=f"{_today()}T12:00:00+00:00",
            session_key="s1",
            source=source,
            agent="",
            provider="anthropic",
            model=model,
            input_tokens=400,
            output_tokens=40,
            cost_usd=cost,
            priced=True,
        )
    )


@pytest.mark.asyncio
async def test_usage_fold_route_returns_rows_total_and_estimated_share(_home):
    _seed_turn(source="chat", cost=1.0)
    _seed_turn(source="loop", cost=0.25)
    c = await _client()
    try:
        body = await (await c.get("/api/usage?window=day&group=purpose")).json()
        assert body["window"] == "day" and body["group"] == "purpose"
        keyed = {r["key"]: r for r in body["rows"]}
        assert keyed["interactive"]["dollars_est"] == 1.0
        assert keyed["loop"]["dollars_est"] == 0.25
        assert body["total"]["calls"] == 2
        assert body["total"]["dollars_est"] == 1.25
        assert body["estimated_share"] == 1.0  # a turn carries no reported-cost flag
        assert len(body["series"]) == 1 and body["series"][0]["date"] == _today()
    finally:
        await c.close()


@pytest.mark.asyncio
async def test_usage_fold_route_states_the_spend_it_does_not_count(_home):
    """The honesty clause: unattended spend must be visible as an excluded figure, not omitted."""
    _seed_turn(source="chat", cost=1.0)
    _seed_attempt(_home, use_case="reasoning", dollars=0.4)
    _seed_attempt(_home, use_case="loops", dollars=0.6)
    c = await _client()
    try:
        body = await (await c.get("/api/usage?window=day")).json()
        assert body["total"]["dollars_est"] == 1.0  # NOT 1.0 + 0.4 + 0.6
        assert body["uncounted"]["calls"] == 2
        assert body["uncounted"]["total_dollars_est"] == 1.0
        assert body["uncounted"]["by_use_case"] == {"reasoning": 1, "loops": 1}
    finally:
        await c.close()


@pytest.mark.asyncio
async def test_usage_fold_route_names_the_app_that_spent(_home):
    _seed_turn(source="weather-app", cost=0.2)
    c = await _client()
    try:
        body = await (await c.get("/api/usage?window=day&group=purpose")).json()
        assert [r["key"] for r in body["rows"]] == ["app"]
        assert body["app_sources"] == {"weather-app": 1}
    finally:
        await c.close()


@pytest.mark.asyncio
async def test_usage_fold_route_self_heals_a_deleted_fold(_home):
    _seed_turn(source="chat", cost=0.4)
    c = await _client()
    try:
        first = await (await c.get("/api/usage")).json()
        assert first["total"]["dollars_est"] == 0.4
        assert (_home / "usage_stats.json").is_file()
        (_home / "usage_stats.json").unlink()
        second = await (await c.get("/api/usage")).json()
        assert second["total"] == first["total"]
        assert (_home / "usage_stats.json").is_file()
    finally:
        await c.close()


@pytest.mark.asyncio
async def test_bad_window_and_group_are_400_envelopes(_home):
    c = await _client()
    try:
        r = await c.get("/api/usage?window=fortnight")
        assert r.status == 400
        assert (await r.json())["error"]["code"] == "bad_request"
        r2 = await c.get("/api/usage?group=vibes")
        assert r2.status == 400
        assert "group must be one of" in (await r2.json())["error"]["message"]
    finally:
        await c.close()


@pytest.mark.asyncio
async def test_empty_home_is_an_empty_fold_not_an_error(_home):
    c = await _client()
    try:
        r = await c.get("/api/usage?window=month")
        assert r.status == 200
        body = await r.json()
        assert body["rows"] == [] and body["total"]["calls"] == 0
        assert body["estimated_share"] == 0.0
        assert body["uncounted"]["calls"] == 0
    finally:
        await c.close()

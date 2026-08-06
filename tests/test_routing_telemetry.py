"""MODEL-ROUTING-TELEMETRY §1.5 / MRT-1d — the telemetry read-model + route.

Per-model efficiency rows derived on request from the routing fold (routing_stats.json) + a
bounded model_calls.jsonl tail: fold supplies n/success/feedback/cost, the tail supplies read-time
p50/p95, and each row carries on_frontier (not dominated on quality/latency/cost). Read-only.
"""

from __future__ import annotations

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.dashboard.handlers.model_telemetry import register_model_telemetry_routes
from personalclaw.routing import stats
from personalclaw.routing.telemetry import _dominates, _percentile, telemetry_rows


class TestPercentile:
    def test_nearest_rank(self):
        vals = [10.0, 20.0, 30.0, 40.0]
        assert _percentile(sorted(vals), 50) == 20.0
        assert _percentile(sorted(vals), 95) == 40.0

    def test_empty_and_single(self):
        assert _percentile([], 50) == 0.0
        assert _percentile([7.0], 95) == 7.0


class TestDominance:
    def test_a_better_everywhere_dominates(self):
        a = {"success": 0.9, "p50_ms": 100.0, "avg_cost_usd": 0.0}
        b = {"success": 0.8, "p50_ms": 200.0, "avg_cost_usd": 0.01}
        assert _dominates(a, b) and not _dominates(b, a)

    def test_tradeoff_neither_dominates(self):
        # a cheaper+faster but lower quality; b higher quality but slow+costly → both on frontier.
        a = {"success": 0.7, "p50_ms": 100.0, "avg_cost_usd": 0.0}
        b = {"success": 0.95, "p50_ms": 500.0, "avg_cost_usd": 0.02}
        assert not _dominates(a, b) and not _dominates(b, a)

    def test_unknown_latency_never_dominates_on_latency(self):
        # a has no latency samples (p50=0 → treated as unknown/inf) — it can't knock b off on speed.
        a = {"success": 0.9, "p50_ms": 0.0, "avg_cost_usd": 0.0}
        b = {"success": 0.8, "p50_ms": 50.0, "avg_cost_usd": 0.0}
        # a still dominates here on success+cost with latency no-better (inf !<= 50 → not no_worse),
        # so a does NOT dominate b (its unknown latency is worse), and b doesn't dominate a either.
        assert not _dominates(a, b)


class TestRows:
    def _stats(self):
        s = {"use_cases": {}}
        # Two refs under reasoning/summarize.
        stats.fold_record(
            s,
            {
                "use_case": "reasoning",
                "query_class": "summarize",
                "provider": "ollama-models",
                "model": "qwen3:8b",
                "passed": True,
                "latency_ms": 2000.0,
                "dollars_est": 0.0,
            },
            now="t",
        )
        stats.fold_record(
            s,
            {
                "use_case": "reasoning",
                "query_class": "summarize",
                "provider": "anthropic",
                "model": "claude",
                "passed": True,
                "latency_ms": 800.0,
                "dollars_est": 0.02,
            },
            now="t",
        )
        return s

    def _audit(self):
        # JSONL tail rows for percentile derivation (ref spelling matches the fold).
        rows = []
        for ms in (1800.0, 2000.0, 2200.0, 5000.0):
            rows.append(
                {
                    "use_case": "reasoning",
                    "query_class": "summarize",
                    "provider": "ollama-models",
                    "model": "qwen3:8b",
                    "latency_ms": ms,
                }
            )
        for ms in (700.0, 800.0, 900.0):
            rows.append(
                {
                    "use_case": "reasoning",
                    "query_class": "summarize",
                    "provider": "anthropic",
                    "model": "claude",
                    "latency_ms": ms,
                }
            )
        return rows

    def test_rows_join_fold_and_latency(self):
        rows = telemetry_rows(self._stats(), self._audit(), "reasoning", "summarize")
        by = {r["ref"]: r for r in rows}
        assert set(by) == {"ollama-models:qwen3:8b", "anthropic:claude"}
        local = by["ollama-models:qwen3:8b"]
        assert local["n"] == 1 and local["avg_cost_usd"] == 0.0
        assert local["p50_ms"] > 0 and local["p95_ms"] >= local["p50_ms"]

    def test_frontier_marks_both_when_tradeoff(self):
        # local: free but slower; cloud: costs but faster → both on the frontier.
        rows = telemetry_rows(self._stats(), self._audit(), "reasoning", "summarize")
        assert all(r["on_frontier"] for r in rows)

    def test_dominated_row_is_off_frontier(self):
        s = {"use_cases": {}}
        # good: high success, will get fast latency; bad: lower success, no cost edge, slow.
        for _ in range(1):
            stats.fold_record(
                s,
                {
                    "use_case": "chat",
                    "query_class": "short_chat",
                    "provider": "P",
                    "model": "good",
                    "passed": True,
                    "latency_ms": 100.0,
                    "dollars_est": 0.0,
                },
                now="t",
            )
            stats.fold_record(
                s,
                {
                    "use_case": "chat",
                    "query_class": "short_chat",
                    "provider": "P",
                    "model": "bad",
                    "passed": False,
                    "latency_ms": 900.0,
                    "dollars_est": 0.01,
                },
                now="t",
            )
        audit = [
            {
                "use_case": "chat",
                "query_class": "short_chat",
                "provider": "P",
                "model": "good",
                "latency_ms": 100.0,
            },
            {
                "use_case": "chat",
                "query_class": "short_chat",
                "provider": "P",
                "model": "bad",
                "latency_ms": 900.0,
            },
        ]
        rows = {r["ref"]: r for r in telemetry_rows(s, audit, "chat", "short_chat")}
        assert rows["P:good"]["on_frontier"] is True
        assert rows["P:bad"]["on_frontier"] is False  # dominated on all three axes

    def test_empty_bucket_is_empty_rows(self):
        assert telemetry_rows({"use_cases": {}}, [], "nope", "nope") == []


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    return tmp_path


async def _client() -> TestClient:
    app = web.Application()
    register_model_telemetry_routes(app)
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


class TestRoute:
    @pytest.mark.asyncio
    async def test_missing_params_are_400(self, _home):
        c = await _client()
        try:
            assert (await c.get("/api/models/telemetry")).status == 400
            assert (await c.get("/api/models/telemetry?use_case=reasoning")).status == 400
        finally:
            await c.close()

    @pytest.mark.asyncio
    async def test_returns_rows_for_a_bucket(self, _home, monkeypatch):
        # Seed the fold on disk so the route reads it.
        s = {"use_cases": {}}
        stats.fold_record(
            s,
            {
                "use_case": "reasoning",
                "query_class": "summarize",
                "provider": "ollama-models",
                "model": "qwen3:8b",
                "passed": True,
                "latency_ms": 2000.0,
                "dollars_est": 0.0,
            },
            now="t",
        )
        stats.save_stats(_home, s)
        c = await _client()
        try:
            resp = await c.get("/api/models/telemetry?use_case=reasoning&query_class=summarize")
            assert resp.status == 200
            body = await resp.json()
            assert body["use_case"] == "reasoning" and body["query_class"] == "summarize"
            assert body["rows"][0]["ref"] == "ollama-models:qwen3:8b"
            assert body["rows"][0]["on_frontier"] is True
        finally:
            await c.close()

    @pytest.mark.asyncio
    async def test_empty_bucket_is_200_empty(self, _home):
        c = await _client()
        try:
            body = await (await c.get("/api/models/telemetry?use_case=x&query_class=y")).json()
            assert body["rows"] == []
        finally:
            await c.close()

"""MODEL-ROUTING-TELEMETRY §1.5 / MRT-1d — the telemetry read-model + route.

Per-model efficiency rows derived on request from the routing fold (routing_stats.json) + a
bounded model_calls.jsonl tail: fold supplies n/success/feedback/cost, the tail supplies read-time
p50/p95, and each row carries on_frontier (not dominated on quality/latency/cost). Read-only.

Plus the one WRITE this handler owns — ``PUT /api/models/routing-policy``'s ``order`` lever
(§6.2 lever 3). See :class:`TestRoutingPolicyWrite` for why those rails read the table back off
disk instead of watching for a call.
"""

from __future__ import annotations

import json

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


# ── the write path: PUT /api/models/routing-policy, ``order`` lever (§6.2) ───────

#: The use case / class every write rail below addresses. One cell, so "the order changed" is a
#: statement about a specific cell rather than about the file as a whole.
_UC = "reasoning"
_QC = "summarize"
#: What the fixture puts on disk before each rail runs. Every rail's assertion is stated against
#: this, which is what keeps "the order is what I sent" and "the bytes did not move" from both
#: being satisfiable by a file that was never written at all.
_SEEDED = ["seed:a", "seed:b"]


@pytest.fixture()
def policy_home(tmp_path, monkeypatch):
    """An isolated home that ``set_order``'s OWN home resolution actually reaches, pre-seeded.

    The handler calls ``set_order(use_case, query_class, order)`` with no ``home=``, so the write
    lands wherever :func:`policy._default_home` resolves — and that does
    ``from personalclaw.config import config_dir``, a binding ``personalclaw/config/__init__.py``
    made at import time. Patching ``personalclaw.config.loader.config_dir`` (which is all the
    module-level ``_home`` fixture above does) therefore does NOT reach it, so a rail built on that
    fixture alone would drive a real write into ``~/.personalclaw``. Both bindings are patched here
    and the redirect is ASSERTED rather than assumed.

    Seeding a real table first is load-bearing twice over: it gives the positive rail a
    before-state that differs from what it sends, and it makes every byte-identity assertion a
    comparison of a real file's bytes rather than the trivially-true ``absent == absent``.
    """
    import personalclaw.config as config_pkg
    import personalclaw.config.loader as config_loader
    from personalclaw.routing import policy

    monkeypatch.setattr(config_pkg, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(config_loader, "config_dir", lambda: tmp_path)
    assert policy._default_home() == tmp_path, "the fixture did not redirect the home it meant to"

    policy.set_order(_UC, _QC, _SEEDED, home=tmp_path)
    assert (tmp_path / "routing_policy.json").exists(), "the seed did not write the table"
    assert _stored_order(tmp_path, _UC, _QC) == _SEEDED
    return tmp_path


def _policy_bytes(home) -> bytes:
    path = home / "routing_policy.json"
    return path.read_bytes() if path.exists() else b"<absent>"


def _stored_order(home, use_case: str, query_class: str):
    """The recorded order as it is ON DISK.

    Read straight out of the JSON rather than through ``policy.table_order``: the defect these
    rails cover is a write that never happened, and routing that read back through a policy
    accessor would let a reader's own fallback ("no order recorded" → the input order) stand in
    for a persisted one.
    """
    path = home / "routing_policy.json"
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    entry = data.get("use_cases", {}).get(use_case) or {}
    return ((entry.get("classes") or {}).get(query_class) or {}).get("order")


async def _put(body: dict):
    """PUT the policy route once and return ``(status, json_body)``."""
    c = await _client()
    try:
        resp = await c.put("/api/models/routing-policy", json=body)
        return resp.status, await resp.json()
    finally:
        await c.close()


class TestRoutingPolicyWrite:
    """``policy::set_order`` was reachable from this route and railed by nothing.

    Measured on this branch's base: with the call at ``model_telemetry.py:135`` replaced by
    ``pass``, the endpoint still answered **200 with ``applied: ["order"]``** while
    ``routing_policy.json`` stayed byte-identical, and all **294** tests across the whole routing
    surface (11 files) stayed green. ``set_order`` was named in exactly one test file
    (``test_routing_proposals.py``, which is about the propose path), so nothing observed the HTTP
    write at all.

    Every rail below therefore asserts the EFFECT — the order that comes back out of the file —
    rather than that the handler called something. Monkeypatching ``set_order`` and asserting it
    was called is the weaker form: it passes for a handler that faithfully calls a function which
    writes nothing, which is the exact failure being ruled out.
    """

    @pytest.mark.asyncio
    async def test_an_accepted_order_is_persisted(self, policy_home):
        """A 200 that claims ``applied: ["order"]`` must be backed by a changed table on disk."""
        sent = ["anthropic:claude", "ollama-models:qwen3:8b"]
        assert sent != _SEEDED, "vacuity floor: the sent order must differ from the seeded one"

        status, body = await _put({"use_case": _UC, "query_class": _QC, "order": sent})

        assert status == 200
        assert body == {"ok": True, "use_case": _UC, "applied": ["order"]}
        assert _stored_order(policy_home, _UC, _QC) == sent

    @pytest.mark.asyncio
    async def test_the_persisted_order_keeps_the_ref_order_it_was_sent(self, policy_home):
        """Sending the same refs in the reverse order must persist the REVERSE order.

        A write that stored the refs as a set, or sorted them, would satisfy "the same refs came
        back" — but the whole point of lever 3 is *which one is tried first*, so the sequence is
        what is pinned.
        """
        first = ["p:one", "p:two", "p:three"]
        status, _ = await _put({"use_case": _UC, "query_class": _QC, "order": first})
        assert status == 200
        assert _stored_order(policy_home, _UC, _QC) == first

        status, _ = await _put({"use_case": _UC, "query_class": _QC, "order": first[::-1]})
        assert status == 200
        assert _stored_order(policy_home, _UC, _QC) == first[::-1]

    @pytest.mark.asyncio
    async def test_the_write_is_scoped_to_the_query_class_it_was_sent(self, policy_home):
        """``set_order``'s signature is ``(use_case, query_class, order)`` — a write that ignored
        the class would look identical on the cell being read back, so the OTHER class is asserted
        untouched too."""
        await _put({"use_case": _UC, "query_class": "long_reasoning", "order": ["p:other"]})
        assert _stored_order(policy_home, _UC, "long_reasoning") == ["p:other"]
        assert _stored_order(policy_home, _UC, _QC) == _SEEDED, "the write leaked across classes"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "body,why",
        [
            ({"use_case": _UC, "order": ["p:one"]}, "order with no query_class"),
            ({"use_case": _UC, "query_class": _QC, "order": "p:one"}, "order is not a list"),
            ({"use_case": _UC, "query_class": _QC, "order": ["p:one", 7]}, "a non-string ref"),
            ({"use_case": "nope", "query_class": _QC, "order": ["p:one"]}, "unknown use_case"),
            ({"use_case": _UC, "query_class": _QC}, "nothing to change"),
        ],
    )
    async def test_a_rejected_write_leaves_the_table_byte_identical(self, policy_home, body, why):
        """The other half, and the easy one to forget: a 400 must persist NOTHING.

        A handler that wrote first and validated afterwards would pass the accepted-order rail and
        still corrupt the table on every rejected request. Byte identity (not "the order I read
        back is still the old one") is asserted so a rewrite that happened to reproduce the same
        order — dropping the ``basis``, say — is caught too.
        """
        before = _policy_bytes(policy_home)
        assert (
            before != b"<absent>"
        ), "the fixture's seed is what makes this comparison mean something"

        status, payload = await _put(body)

        assert status == 400, why
        assert payload["error"]["code"] == "bad_request"
        assert _policy_bytes(policy_home) == before, f"a rejected write ({why}) moved the table"

    @pytest.mark.asyncio
    async def test_the_byte_harness_can_see_an_accepted_write(self, policy_home):
        """Vacuity floor for the rejection rails.

        If an ACCEPTED write did not move the bytes this helper reads, every byte-identity
        assertion above would hold for a handler that persisted nothing at all — which is exactly
        the swallowed write these rails exist to catch. So drive the accepted path through the
        same helper and prove the bytes move.
        """
        before = _policy_bytes(policy_home)
        status, _ = await _put({"use_case": _UC, "query_class": _QC, "order": ["p:moved"]})
        assert status == 200
        assert _policy_bytes(policy_home) != before

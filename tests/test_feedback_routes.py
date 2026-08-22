"""FEEDBACK-SIGNAL S1 — the /api/feedback route surface.

The shared error envelope, the kill-switch 404, and the app-namespace forcing
(an app-scoped token can never impersonate a core producer).
"""

from __future__ import annotations

import json

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw import feedback as fb
from personalclaw.dashboard.handlers.feedback import register_feedback_routes


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    import personalclaw.config.loader as cfg
    import personalclaw.providers.entity_routes as er

    monkeypatch.setattr(cfg, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(cfg, "config_path", lambda: tmp_path / "config.json")
    monkeypatch.setattr(
        er, "_entity_settings_path", lambda entity: tmp_path / "entity_settings" / f"{entity}.json"
    )
    fb._invalidate()
    yield tmp_path
    fb._invalidate()


def _make_app(app_token_name: str = "") -> web.Application:
    app = web.Application()
    if app_token_name:
        # Simulate the auth middleware's app-scoped-token stamping.
        @web.middleware
        async def stamp_app(request, handler):
            request["app"] = app_token_name
            return await handler(request)

        app.middlewares.append(stamp_app)
    register_feedback_routes(app)
    return app


BODY = {
    "target_kind": "inbox_classification",
    "target_id": "item-1",
    "verdict": "down",
    "reason": "wrong",
    "producer_kind": "prompt",
    "producer_id": "native:inbox-classify",
}


class TestRecordRoute:
    @pytest.mark.asyncio
    async def test_record_and_hydrate(self):
        async with TestClient(TestServer(_make_app())) as c:
            resp = await c.post("/api/feedback", json=BODY)
            assert resp.status == 200
            got = await (await c.get("/api/feedback/target/inbox_classification/item-1")).json()
            assert got["verdict"] == "down" and got["reason"] == "wrong"

    @pytest.mark.asyncio
    async def test_missing_target_hydrates_null(self):
        async with TestClient(TestServer(_make_app())) as c:
            got = await (await c.get("/api/feedback/target/inbox_classification/nope")).json()
            assert got["verdict"] is None

    @pytest.mark.asyncio
    async def test_bad_bodies_rejected(self):
        async with TestClient(TestServer(_make_app())) as c:
            assert (await c.post("/api/feedback", json={**BODY, "verdict": "meh"})).status == 400
            assert (
                await c.post("/api/feedback", json={**BODY, "target_kind": "chat"})
            ).status == 400
            assert (await c.post("/api/feedback", json={**BODY, "target_id": ""})).status == 400
            assert (await c.post("/api/feedback", data="not json")).status == 400

    @pytest.mark.asyncio
    async def test_app_caller_forcibly_namespaced(self):
        """An app-scoped token's producer is forced to app:<name>:<producer> —
        it can never impersonate a core producer (e.g. a bound prompt)."""
        async with TestClient(TestServer(_make_app(app_token_name="weather"))) as c:
            resp = await c.post("/api/feedback", json=BODY)
            assert resp.status == 200
        rec = fb.current_verdict("inbox_classification", "item-1")
        assert rec is not None
        assert rec.producer_kind == "app"
        assert rec.producer_id == "weather:native:inbox-classify"
        assert rec.source_app == "weather"

    @pytest.mark.asyncio
    async def test_kill_switch_404s_every_route(self, isolated):
        (isolated / "config.json").write_text(json.dumps({"feedback": {"enabled": False}}))
        async with TestClient(TestServer(_make_app())) as c:
            assert (await c.post("/api/feedback", json=BODY)).status == 404
            assert (await c.get("/api/feedback/target/inbox_classification/x")).status == 404
            assert (await c.get("/api/feedback/producers")).status == 404


class TestProducersRoute:
    @pytest.mark.asyncio
    async def test_min_n_gating(self):
        # 2 verdicts < min_n 5 → "collecting", no accuracy number
        for i in range(2):
            fb.record_feedback(
                target_kind="inbox_classification",
                target_id=f"i{i}",
                verdict="up",
                producer_kind="prompt",
                producer_id="native:inbox-classify",
            )
        async with TestClient(TestServer(_make_app())) as c:
            got = await (await c.get("/api/feedback/producers")).json()
        row = got["producers"][0]
        assert row["collecting"] is True and "accuracy" not in row

    @pytest.mark.asyncio
    async def test_accuracy_and_the_below_threshold_state(self):
        """``workflow_surfacing`` has no surfacing gate, so falling below the retire
        threshold proposes retirement and withholds NOTHING.

        This test used to assert ``suppressed is True`` here, which was the untrue claim
        `ENFORCED_SUPPRESSION_KINDS` exists to correct: only ``skill_synthesis`` can act on
        membership, and the Settings panel renders ``suppressed`` as "Stopped surfacing".
        The per-kind branches are covered in test_feedback_suppression_enforcement.py; what
        this route-level test owns is that ``accuracy`` is reported once ``min_n`` is met.
        """
        for i in range(5):
            fb.record_feedback(
                target_kind="proposal_content",
                target_id=f"d{i}",
                verdict="down",
                producer_kind="workflow_surfacing",
                producer_id="wf_x",
            )
        async with TestClient(TestServer(_make_app())) as c:
            got = await (await c.get("/api/feedback/producers")).json()
        row = next(r for r in got["producers"] if r["producer_id"] == "wf_x")
        assert row["accuracy"] == 0.0
        assert row.get("proposal_only") is True, "below threshold must report the honest state"
        assert "suppressed" not in row, "an unenforced kind must not claim it stopped surfacing"

    @pytest.mark.asyncio
    async def test_snooze_and_clear_round_trip(self):
        for i in range(5):
            fb.record_feedback(
                target_kind="proposal_content",
                target_id=f"d{i}",
                verdict="down",
                producer_kind="workflow_surfacing",
                producer_id="wf_x",
            )
        async with TestClient(TestServer(_make_app())) as c:
            body = {"producer_kind": "workflow_surfacing", "producer_id": "wf_x"}
            assert (await c.post("/api/feedback/producers/snooze", json=body)).status == 200
            assert ("workflow_surfacing", "wf_x") not in fb.suppressed_producers()
            assert (await c.post("/api/feedback/producers/clear", json=body)).status == 200
            assert (await c.post("/api/feedback/producers/snooze", json={})).status == 400

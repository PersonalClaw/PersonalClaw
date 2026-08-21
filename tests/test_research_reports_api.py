"""WF2KNO-12 — the /api/knowledge/reports surface.

CRUD round-trip, the two door-refusals (a malformed cron expression and an unknown citation
policy can never become a stored definition), the 404s, and the 409 a manual run answers
while a scheduled fire holds the lease.

**The lease test writes a REAL claim** with ``claims.write_claim`` and lets the handler read
it back through its own default base_dir, rather than mocking ``is_running``. A mocked
predicate proves the branch; only a real claim proves the two sides agree on the claim id
AND on which directory the claim store lives in — the two ways this idempotency has to fail.

The sibling persistence module (``personalclaw.knowledge.research_reports``) is landing in a
parallel change, so these tests run against a stub installed in ``sys.modules`` that
implements its frozen contract. That is deliberate, not a stopgap: it pins the API to the
CONTRACT, so the suite passes today and keeps passing once the real module arrives.
"""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass, field
from types import ModuleType
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.dashboard.handlers import research_reports as rr_api

_MODULE_PATH = "personalclaw.knowledge.research_reports"

CITATION_POLICIES = ("cite-source-only", "allow-citing-context")


@dataclass
class Scope:
    """The stub's scope — the contract's ``rr.Scope``."""

    tags: tuple[str, ...] = ()
    window_secs: int = 0


@dataclass
class ReportDefinition:
    """The stub's definition — the contract's ``rr.ReportDefinition``."""

    id: str = ""
    name: str = ""
    prompt: str = ""
    schedule: dict[str, Any] = field(default_factory=dict)
    tz: str = ""
    source: Scope = field(default_factory=Scope)
    context: Scope | None = None
    citation_policy: str = "cite-source-only"
    iteration_cap: int = 3
    enabled: bool = True
    created_ts: float = 0.0
    last_run_ts: float | None = None
    last_status: str = ""
    last_error: str = ""
    watermark_ts: float = 0.0


def _scope_from(raw: Any) -> Scope | None:
    if not isinstance(raw, dict):
        return None
    return Scope(
        tags=tuple(str(t) for t in (raw.get("tags") or ())),
        window_secs=int(raw.get("window_secs") or 0),
    )


def _make_stub() -> ModuleType:
    """A contract-faithful stand-in for the sibling persistence module."""
    mod = ModuleType(_MODULE_PATH)
    rows: dict[str, ReportDefinition] = {}

    def from_dict(raw: dict[str, Any]) -> ReportDefinition:
        d = dict(raw or {})
        return ReportDefinition(
            id=str(d.get("id") or ""),
            name=str(d.get("name") or ""),
            prompt=str(d.get("prompt") or ""),
            schedule=dict(d.get("schedule") or {}),
            tz=str(d.get("tz") or ""),
            source=_scope_from(d.get("source")) or Scope(),
            context=_scope_from(d.get("context")),
            citation_policy=str(d.get("citation_policy") or "cite-source-only"),
            iteration_cap=int(d.get("iteration_cap") or 3),
            enabled=bool(d.get("enabled", True)),
            created_ts=float(d.get("created_ts") or 0.0),
            last_run_ts=d.get("last_run_ts"),
            last_status=str(d.get("last_status") or ""),
            last_error=str(d.get("last_error") or ""),
            watermark_ts=float(d.get("watermark_ts") or 0.0),
        )

    def to_dict(defn: ReportDefinition) -> dict[str, Any]:
        return {
            "id": defn.id,
            "name": defn.name,
            "prompt": defn.prompt,
            "schedule": dict(defn.schedule),
            "tz": defn.tz,
            "source": {"tags": list(defn.source.tags), "window_secs": defn.source.window_secs},
            "context": (
                None
                if defn.context is None
                else {
                    "tags": list(defn.context.tags),
                    "window_secs": defn.context.window_secs,
                }
            ),
            "citation_policy": defn.citation_policy,
            "iteration_cap": defn.iteration_cap,
            "enabled": defn.enabled,
            "created_ts": defn.created_ts,
            "last_run_ts": defn.last_run_ts,
            "last_status": defn.last_status,
            "last_error": defn.last_error,
            "watermark_ts": defn.watermark_ts,
        }

    def save_report(defn: ReportDefinition) -> ReportDefinition:
        if not defn.id:
            defn.id = f"rep-{len(rows) + 1}"
        if not defn.created_ts:
            defn.created_ts = 1_700_000_000.0
        rows[defn.id] = defn
        return defn

    def load_reports() -> list[ReportDefinition]:
        return list(rows.values())

    def get_report(report_id: str) -> ReportDefinition | None:
        return rows.get(report_id)

    def delete_report(report_id: str) -> bool:
        return rows.pop(report_id, None) is not None

    mod.CITATION_POLICIES = CITATION_POLICIES  # type: ignore[attr-defined]
    mod.Scope = Scope  # type: ignore[attr-defined]
    mod.ReportDefinition = ReportDefinition  # type: ignore[attr-defined]
    mod.from_dict = from_dict  # type: ignore[attr-defined]
    mod.to_dict = to_dict  # type: ignore[attr-defined]
    mod.save_report = save_report  # type: ignore[attr-defined]
    mod.load_reports = load_reports  # type: ignore[attr-defined]
    mod.get_report = get_report  # type: ignore[attr-defined]
    mod.delete_report = delete_report  # type: ignore[attr-defined]
    mod._rows = rows  # type: ignore[attr-defined]
    return mod


class _Sel:
    """A SEL recorder that stays in memory — the audit path never reaches the real home."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def log_api_access(self, **kw: Any) -> None:
        self.calls.append(kw)


@pytest.fixture
def sel_log(monkeypatch):
    import personalclaw.sel as sel_mod

    recorder = _Sel()
    monkeypatch.setattr(sel_mod, "sel", lambda: recorder)
    return recorder


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch, sel_log):
    """Home → tmp_path, and the sibling module → the contract stub."""
    import personalclaw.config.loader as cfg
    import personalclaw.knowledge as knowledge_pkg

    monkeypatch.setattr(cfg, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(cfg, "config_path", lambda: tmp_path / "config.json")

    stub = _make_stub()
    monkeypatch.setitem(sys.modules, _MODULE_PATH, stub)
    monkeypatch.setattr(knowledge_pkg, "research_reports", stub, raising=False)
    return tmp_path


def _app() -> web.Application:
    app = web.Application()
    rr_api.setup_research_report_routes(app)
    return app


BODY = {
    "name": "Weekly AI digest",
    "prompt": "Summarize what changed in agent tooling this week.",
    "schedule": {"kind": "cron", "cron_expr": "0 9 * * 1"},
    "tz": "America/Los_Angeles",
    "source": {"tags": ["ai"], "window_secs": 604800},
    "context": {"tags": ["notes"], "window_secs": 0},
    "citation_policy": "cite-source-only",
    "iteration_cap": 2,
    "enabled": True,
}


class TestCrud:
    @pytest.mark.asyncio
    async def test_create_list_update_delete(self, sel_log):
        async with TestClient(TestServer(_app())) as c:
            created = await c.post("/api/knowledge/reports", json=BODY)
            assert created.status == 200
            report = (await created.json())["report"]
            rid = report["id"]
            assert rid
            assert report["name"] == "Weekly AI digest"
            assert report["schedule"] == {
                "kind": "cron",
                "every_secs": None,
                "at_ts": None,
                "cron_expr": "0 9 * * 1",
            }
            assert report["source"] == {"tags": ["ai"], "window_secs": 604800}
            assert report["iteration_cap"] == 2

            listed = await (await c.get("/api/knowledge/reports")).json()
            assert [r["id"] for r in listed["reports"]] == [rid]

            updated = await c.put(f"/api/knowledge/reports/{rid}", json={"enabled": False})
            assert updated.status == 200
            after = (await updated.json())["report"]
            assert after["enabled"] is False
            # An update touches only what it names.
            assert after["name"] == "Weekly AI digest"
            assert after["created_ts"] == report["created_ts"]

            deleted = await c.delete(f"/api/knowledge/reports/{rid}")
            assert deleted.status == 200
            assert await deleted.json() == {"ok": True}
            assert (await (await c.get("/api/knowledge/reports")).json())["reports"] == []

        ops = [call["operation"] for call in sel_log.calls]
        assert ops == [
            "knowledge_report.create",
            "knowledge_report.update",
            "knowledge_report.delete",
        ]

    @pytest.mark.asyncio
    async def test_every_and_at_schedules(self):
        async with TestClient(TestServer(_app())) as c:
            body = dict(BODY, schedule={"kind": "every", "every_secs": 3600})
            got = (await (await c.post("/api/knowledge/reports", json=body)).json())["report"]
            assert got["schedule"]["every_secs"] == 3600
            body = dict(BODY, schedule={"kind": "at", "at_ts": 1_800_000_000})
            got = (await (await c.post("/api/knowledge/reports", json=body)).json())["report"]
            assert got["schedule"]["at_ts"] == 1_800_000_000


class TestValidation:
    @pytest.mark.asyncio
    async def test_bad_cron_is_400_naming_the_expression(self):
        """A typo in a cron field never becomes a stored row the runner re-fails on."""
        async with TestClient(TestServer(_app())) as c:
            body = dict(BODY, schedule={"kind": "cron", "cron_expr": "0 99 * * *"})
            resp = await c.post("/api/knowledge/reports", json=body)
            assert resp.status == 400
            assert "'0 99 * * *'" in (await resp.json())["error"]["message"]
            # Nothing was stored.
            assert (await (await c.get("/api/knowledge/reports")).json())["reports"] == []

    @pytest.mark.asyncio
    async def test_bad_cron_on_update_is_400(self):
        async with TestClient(TestServer(_app())) as c:
            rid = (await (await c.post("/api/knowledge/reports", json=BODY)).json())["report"]["id"]
            resp = await c.put(
                f"/api/knowledge/reports/{rid}",
                json={"schedule": {"kind": "cron", "cron_expr": "not a cron"}},
            )
            assert resp.status == 400
            assert "'not a cron'" in (await resp.json())["error"]["message"]
            after = (await (await c.get("/api/knowledge/reports")).json())["reports"][0]
            assert after["schedule"]["cron_expr"] == "0 9 * * 1"

    @pytest.mark.asyncio
    async def test_unknown_citation_policy_is_400_listing_the_legal_values(self):
        async with TestClient(TestServer(_app())) as c:
            body = dict(BODY, citation_policy="cite-anything")
            resp = await c.post("/api/knowledge/reports", json=body)
            assert resp.status == 400
            message = (await resp.json())["error"]["message"]
            assert "cite-anything" in message
            for policy in CITATION_POLICIES:
                assert policy in message

    @pytest.mark.asyncio
    async def test_unknown_citation_policy_on_update_is_400(self):
        async with TestClient(TestServer(_app())) as c:
            rid = (await (await c.post("/api/knowledge/reports", json=BODY)).json())["report"]["id"]
            resp = await c.put(
                f"/api/knowledge/reports/{rid}", json={"citation_policy": "whatever"}
            )
            assert resp.status == 400

    @pytest.mark.asyncio
    async def test_required_fields_and_shapes(self):
        async with TestClient(TestServer(_app())) as c:
            for body, fragment in (
                ({"prompt": "p", "schedule": BODY["schedule"]}, "name is required"),
                ({"name": "n", "schedule": BODY["schedule"]}, "prompt is required"),
                ({"name": "n", "prompt": "p"}, "schedule is required"),
                (dict(BODY, schedule={"kind": "weekly"}), "schedule.kind must be one of"),
                (dict(BODY, schedule={"kind": "every"}), "every_secs must be a positive integer"),
                (dict(BODY, schedule={"kind": "cron"}), "cron_expr is required"),
                (dict(BODY, iteration_cap=0), "iteration_cap must be an integer >= 1"),
                (dict(BODY, enabled="yes"), "enabled must be a boolean"),
                (dict(BODY, source={"tags": [1]}), "source.tags must be a list of strings"),
                (dict(BODY, context={"window_secs": -1}), "context.window_secs"),
            ):
                resp = await c.post("/api/knowledge/reports", json=body)
                assert resp.status == 400, fragment
                assert fragment in (await resp.json())["error"]["message"]

    @pytest.mark.asyncio
    async def test_non_object_body_is_400(self):
        async with TestClient(TestServer(_app())) as c:
            resp = await c.post("/api/knowledge/reports", json=[1, 2])
            assert resp.status == 400
            assert (await resp.json())["error"] == {
                "code": "invalid_json",
                "message": "JSON body must be an object",
            }


class TestNotFound:
    @pytest.mark.asyncio
    async def test_404s(self):
        async with TestClient(TestServer(_app())) as c:
            assert (
                await c.put("/api/knowledge/reports/nope", json={"enabled": False})
            ).status == 404
            assert (await c.delete("/api/knowledge/reports/nope")).status == 404
            assert (await c.post("/api/knowledge/reports/nope/run")).status == 404


class _FakeResult:
    success = True
    error = ""


class _FakeProvider:
    name = rr_api.RUN_ACTION_PROVIDER
    display_name = "Knowledge report"
    supports_blocking = False

    def __init__(self) -> None:
        self.calls: list[tuple[dict[str, Any], Any]] = []

    async def execute(self, action_config, ctx, timeout: int = 30):
        self.calls.append((action_config, ctx))
        return _FakeResult()


@pytest.fixture
def fake_provider(monkeypatch):
    from personalclaw.action_providers import registry

    provider = _FakeProvider()
    monkeypatch.setitem(registry._providers, rr_api.RUN_ACTION_PROVIDER, provider)
    return provider


class TestManualRun:
    @pytest.mark.asyncio
    async def test_run_dispatches_the_action_provider(self, fake_provider):
        async with TestClient(TestServer(_app())) as c:
            rid = (await (await c.post("/api/knowledge/reports", json=BODY)).json())["report"]["id"]
            resp = await c.post(f"/api/knowledge/reports/{rid}/run")
            assert resp.status == 200
            assert (await resp.json())["ok"] is True
        assert len(fake_provider.calls) == 1
        config, ctx = fake_provider.calls[0]
        # `manual: True` rides the CONFIG as well as the payload now: the provider's dueness
        # pre-flight reads its config, because that is the surface a trigger row also fills —
        # and a trigger row never sets this key, so a scheduled fire cannot skip the window
        # check by accident.
        assert config == {"report_id": rid, "manual": True}
        assert ctx.payload["report_id"] == rid and ctx.payload["manual"] is True

    @pytest.mark.asyncio
    async def test_run_reports_an_unresolvable_provider_as_ok_false(self, monkeypatch):
        from personalclaw.action_providers import registry

        monkeypatch.delitem(registry._providers, rr_api.RUN_ACTION_PROVIDER, raising=False)
        monkeypatch.setattr(registry, "_ensure_default_providers_registered", lambda: None)
        async with TestClient(TestServer(_app())) as c:
            rid = (await (await c.post("/api/knowledge/reports", json=BODY)).json())["report"]["id"]
            resp = await c.post(f"/api/knowledge/reports/{rid}/run")
            assert resp.status == 200
            body = await resp.json()
            assert body["ok"] is False
            assert rr_api.RUN_ACTION_PROVIDER in body["result"]

    @pytest.mark.asyncio
    async def test_run_is_409_while_a_scheduled_fire_holds_the_lease(self, fake_provider):
        """A REAL claim, taken through the same default base_dir the handler reads."""
        from personalclaw.triggers import claims
        from personalclaw.triggers.scheduling import Claim

        async with TestClient(TestServer(_app())) as c:
            rid = (await (await c.post("/api/knowledge/reports", json=BODY)).json())["report"]["id"]
            claim_id = rr_api.report_claim_id(rid)
            assert claim_id == f"research-report:{rid}"
            claims.write_claim(
                Claim(
                    trigger_id=claim_id,
                    holder="scheduler",
                    claimed_at=time.time(),
                    max_duration_secs=3600.0,
                )
            )
            assert claims.is_running(claim_id) is True

            resp = await c.post(f"/api/knowledge/reports/{rid}/run")
            assert resp.status == 409
            body = await resp.json()
            assert body["reason"] == "already_running"
            assert body["error"]
            # The refusal is a REFUSAL: nothing was dispatched.
            assert fake_provider.calls == []

            # Releasing the lease makes the same request succeed — the 409 was the lease,
            # not a permanently broken route.
            claims.release_claim(claim_id)
            assert (await c.post(f"/api/knowledge/reports/{rid}/run")).status == 200
            assert len(fake_provider.calls) == 1


class TestModuleAbsent:
    @pytest.mark.asyncio
    async def test_every_route_answers_503_without_the_sibling_module(self, monkeypatch):
        """A build without the persistence module refuses cleanly — never a 500 at boot."""
        monkeypatch.setattr(rr_api, "_reports_module", lambda: None)
        async with TestClient(TestServer(_app())) as c:
            for resp in (
                await c.get("/api/knowledge/reports"),
                await c.post("/api/knowledge/reports", json=BODY),
                await c.put("/api/knowledge/reports/x", json={}),
                await c.delete("/api/knowledge/reports/x"),
                await c.post("/api/knowledge/reports/x/run"),
            ):
                assert resp.status == 503
                assert "not available" in (await resp.json())["error"]["message"]

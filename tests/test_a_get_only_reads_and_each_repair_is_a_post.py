"""A GET only reads; the memory purge and the workflow heal each run on a POST.

``GET /api/memory/lint`` ran the memory-health sweep's purge, a permanent delete of rows
superseded more than 90 days ago, and ``GET /api/workflows/audit?dry_run=false`` healed the run
store. The cross-site check (``csrf_middleware``) and the request log cover methods other than
GET, and the session cookie is ``SameSite=Lax``, which a top-level navigation from another site
carries: a page elsewhere could run both repairs with the owner's session, and leave no request
row. Now each GET reports what its repair would change, and the repair runs on a POST
(``POST /api/memory/lint``, ``POST /api/workflows/audit/heal``).
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from personalclaw.memory_lint import lint_memory
from personalclaw.vector_memory import VectorMemoryStore

OLD = "2020-01-01T00:00:00+00:00"


@pytest.fixture
def vs():
    store = VectorMemoryStore(db_path=Path(tempfile.mkdtemp()) / "m.db")
    store.init()
    store.set_semantic("pref.gone", "old", 1.0, "user_explicit")
    store.set_semantic("pref.new", "new", 1.0, "user_explicit")
    store.supersede_semantic("pref.gone", "pref.new", "user_explicit")
    store.db.execute("UPDATE semantic_memory SET invalidated_at=? WHERE key='pref.gone'", (OLD,))
    store.db.commit()
    return store


def _kept(vs) -> int:
    return vs.db.execute("SELECT COUNT(*) FROM semantic_memory WHERE key='pref.gone'").fetchone()[0]


def test_a_report_only_sweep_counts_the_purge_and_deletes_nothing(vs):
    """🔴 Red before: the sweep had no way to only read."""
    report = lint_memory(vs, purge=False)
    assert _kept(vs) == 1
    assert report.auto_fixed["superseded_purged"] == 0
    assert report.to_dict()["fixable"] == {"superseded_purged": 1}
    assert lint_memory(vs).auto_fixed["superseded_purged"] == 1  # the purge, as before
    assert _kept(vs) == 0


class _Service:
    def __init__(self, vs) -> None:
        self._vs = vs

    def lint(self, *, purge: bool = True) -> dict:
        return lint_memory(self._vs, purge=purge).to_dict()


@pytest.mark.parametrize("method,kept", [("GET", 1), ("POST", 0)])
@pytest.mark.asyncio
async def test_the_memory_lint_purges_only_on_a_post(vs, monkeypatch, method, kept):
    """🔴 Red before for GET: the Health tab's GET deleted the row."""
    import json

    from personalclaw.dashboard.handlers import memory as memory_handlers

    monkeypatch.setattr(memory_handlers, "_get_service", lambda request: _Service(vs))
    # The owner's own call, from a chat that keeps memory: the purge runs at once.
    monkeypatch.setattr(memory_handlers, "_is_restricted_session", lambda state, request: False)
    resp = await memory_handlers.api_memory_lint(_req(method, "/api/memory/lint"))
    assert resp.status == 200
    assert _kept(vs) == kept
    body = json.loads(resp.body)
    assert body["auto_fixed"]["superseded_purged"] == 1 - kept


def test_the_gateway_serves_the_memory_lint_on_a_get_and_a_post():
    """🔴 Red before for POST: the route took only a GET."""
    from gateway_routes_shared import handler_for

    from personalclaw.dashboard.handlers.memory import api_memory_lint

    assert handler_for("GET", "/api/memory/lint") is api_memory_lint
    assert handler_for("POST", "/api/memory/lint") is api_memory_lint


def _req(method: str, path: str):
    app = web.Application()
    app["state"] = None
    return make_mocked_request(method, path, app=app)


@pytest.mark.asyncio
async def test_a_get_of_the_workflow_audit_never_heals(monkeypatch):
    """🔴 Red before: ``dry_run=false`` on a GET healed the run store."""
    from personalclaw.workflows import handlers as H

    audit = MagicMock(return_value={"ok": True, "dry_run": True})
    monkeypatch.setattr(H.service, "audit", audit)
    resp = await H.api_audit(_req("GET", "/api/workflows/audit?dry_run=false"))
    assert resp.status == 400
    assert all(call.kwargs.get("dry_run") is not False for call in audit.call_args_list)
    resp = await H.api_audit(_req("GET", "/api/workflows/audit"))
    assert resp.status == 200 and audit.call_args.kwargs["dry_run"] is True


@pytest.mark.asyncio
async def test_the_workflow_heal_runs_on_its_post(monkeypatch):
    """🔴 Red before: there was no POST to heal on."""
    from personalclaw.workflows import handlers as H

    audit = MagicMock(return_value={"ok": True, "dry_run": False})
    monkeypatch.setattr(H.service, "audit", audit)
    resp = await H.api_audit_heal(_req("POST", "/api/workflows/audit/heal"))
    assert resp.status == 200 and audit.call_args.kwargs["dry_run"] is False


def test_the_gateway_serves_the_heal_on_a_post_alone_and_opens_it_to_the_agents_tools():
    """🔴 Red before: no route healed on a POST."""
    from gateway_routes_shared import handler_for

    from personalclaw.workflows import handlers as H
    from personalclaw.workflows.agent_routes import AGENT_TOOL_ROUTES

    assert handler_for("POST", "/api/workflows/audit/heal") is H.api_audit_heal
    assert handler_for("GET", "/api/workflows/audit/heal") is None
    assert handler_for("GET", "/api/workflows/audit") is H.api_audit
    assert "POST /api/workflows/audit/heal" in AGENT_TOOL_ROUTES


def test_the_agents_repair_tool_posts_the_heal(monkeypatch):
    """🔴 Red before: the tool sent ``GET /api/workflows/audit?dry_run=false``."""
    from personalclaw import mcp_workflows_gateway as gw

    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(gw, "_post", lambda path, body: calls.append(("POST", path)) or {})
    monkeypatch.setattr(gw, "_get", lambda path: calls.append(("GET", path)) or {})
    gw._repair({})
    assert calls == [("POST", "/api/workflows/audit/heal")]


@pytest.mark.asyncio
async def test_a_cross_site_post_to_either_repair_is_refused_by_the_real_gateway(
    tmp_path, monkeypatch, vs
):
    """Through the real gateway (``start_dashboard``'s own middleware, its sign-in on): a POST to
    each repair with another site's ``Origin`` is refused with 403 before it runs, so nothing is
    purged and nothing healed. The same POST from this machine runs, the control that the route
    is the one the gateway serves."""
    from signed_in_gateway import signed_in_gateway

    from personalclaw.dashboard.handlers import memory as memory_handlers
    from personalclaw.workflows import handlers as H

    monkeypatch.setattr(memory_handlers, "_get_service", lambda request: _Service(vs))
    heal = MagicMock(return_value={"ok": True, "dry_run": False})
    monkeypatch.setattr(H.service, "audit", heal)
    foreign = {"Origin": "https://elsewhere.example"}

    async with signed_in_gateway(tmp_path, monkeypatch) as gw:
        lint_status, _ = await gw.as_owner("POST", "/api/memory/lint", headers=foreign)
        heal_status, _ = await gw.as_owner("POST", "/api/workflows/audit/heal", headers=foreign)
        assert (lint_status, heal_status) == (403, 403)
        assert _kept(vs) == 1
        assert not heal.called

        lint_status, _ = await gw.as_owner("POST", "/api/memory/lint")
        heal_status, _ = await gw.as_owner("POST", "/api/workflows/audit/heal")
    assert (lint_status, heal_status) == (200, 200)
    assert _kept(vs) == 0
    assert heal.call_args.kwargs["dry_run"] is False

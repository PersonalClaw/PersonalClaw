"""A switched-off feature's render reads answer one decided shape: ``200 {"enabled": false}``.

The convention (``docs/reference/api-overview.md``) was set by the evals reports: a read that a page
loads to render answers the flag alone while its feature is off, and the page draws its off state
from it. A read that answered 404 or 403 instead made the browser log a failed request for every
panel of a feature that was merely off, and the pages drew those as load failures with a Retry that
could never succeed.

The feedback and rooms reads are pinned beside their own routes (``test_feedback_routes.py``,
``test_rooms_api.py``), the learning summary and identity report beside theirs
(``test_lv3_learning_summary.py``, ``test_lv4_identity_report.py``), and the evals "not run" answer
in ``test_evals_routes.py``. This file holds the Doctor and the Learning page's census, the switch
that makes learning's off state reversible from the page, and the dashboard settings PUT, whose
answer is what lets Settings → Account stop re-reading after a save.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer, make_mocked_request

from personalclaw.dashboard.handlers import doctor as doctor_h
from personalclaw.dashboard.handlers import learning as learning_h

OFF = {"enabled": False}


def _body(resp: web.Response) -> dict:
    return json.loads(resp.body)


def _run(coro):
    return asyncio.run(coro)


def _get(path: str, **match_info: str) -> web.Request:
    return make_mocked_request("GET", path, match_info=match_info, app=web.Application())


# ── the Doctor (`resilience.doctor_enabled`) ─────────────────────────────────


@pytest.fixture()
def doctor_off(monkeypatch):
    monkeypatch.setattr(doctor_h, "_resilience_cfg", lambda: SimpleNamespace(doctor_enabled=False))


#: The three reads a page loads to render the Doctor: the Home strip and the Settings hub read the
#: report, the Doctor page reads all three.
_DOCTOR_READS = (
    (doctor_h.api_doctor, "/api/doctor"),
    (doctor_h.api_doctor_fixes, "/api/doctor/fixes"),
    (doctor_h.api_doctor_remediation, "/api/doctor/remediation"),
)


@pytest.mark.parametrize(("handler", "path"), _DOCTOR_READS, ids=[p for _, p in _DOCTOR_READS])
def test_the_doctors_render_reads_answer_off(doctor_off, handler, path):
    """As 404s these were drawn as "Couldn't load the doctor report" on the Doctor page, a failed
    tile on the Settings hub, and "Health unknown" on the Home strip — three claims that a probe
    failed, when none had been asked to run."""
    resp = _run(handler(_get(path)))
    assert resp.status == 200
    assert _body(resp) == OFF


def test_the_doctors_actions_and_drill_downs_still_refuse(doctor_off):
    """Each of these addresses one artifact of a surface that is off, or runs something on it, so
    none has a decided answer; the page does not ask for them while the report says off."""
    for coro in (
        doctor_h.api_doctor_capability(_get("/api/doctor/memory", capability="memory")),
        doctor_h.api_doctor_crash(_get("/api/doctor/crash/x.json", filename="x.json")),
        doctor_h.api_doctor_remediation_run(
            make_mocked_request("POST", "/api/doctor/remediation/run", app=web.Application())
        ),
    ):
        resp = _run(coro)
        assert resp.status == 404
        assert _body(resp)["error"]["code"] == "doctor_disabled"


# ── the Learning page (`learning.enabled`) ───────────────────────────────────


def _learning_gets() -> dict[str, object]:
    app = web.Application()
    learning_h.register_learning_routes(app)
    return {
        str(route.resource.canonical): route.handler
        for route in app.router.routes()
        if route.method == "GET"
    }


def test_every_learning_read_but_the_drill_down_answers_off(monkeypatch):
    """DERIVED from the route table, so a new read the Learning page loads cannot arrive answering
    off with a 404: every GET except one proposal's must answer the flag alone."""
    monkeypatch.setattr(learning_h, "_enabled", lambda: False)
    gets = _learning_gets()
    drill_down = "/api/learning/proposals/{id}"
    reads = {path: h for path, h in gets.items() if path != drill_down}
    # Vacuity floor: the proposal list, the week, health, the summary and the identity report.
    assert len(reads) == 5, sorted(reads)
    for path, handler in reads.items():
        resp = _run(handler(_get(path)))
        assert resp.status == 200, path
        assert _body(resp) == OFF, path


def test_one_proposal_and_every_decision_still_refuse_while_learning_is_off(monkeypatch):
    """A proposal, an accept and a dismiss each address a proposal, and there is no inbox while
    learning is off — 404, not 403, which would imply one behind a permission wall."""
    monkeypatch.setattr(learning_h, "_enabled", lambda: False)
    for coro in (
        learning_h.api_learning_proposal(_get("/api/learning/proposals/p1", id="p1")),
        learning_h.api_learning_proposal_accept(
            make_mocked_request(
                "POST",
                "/api/learning/proposals/p1/accept",
                match_info={"id": "p1"},
                app=web.Application(),
            )
        ),
        learning_h.api_learning_proposal_reject(
            make_mocked_request(
                "DELETE",
                "/api/learning/proposals/p1",
                match_info={"id": "p1"},
                app=web.Application(),
            )
        ),
    ):
        assert _run(coro).status == 404


# ── the way back on, and the save that answers what it stored ───────────────


@pytest.fixture()
def cfg_file(tmp_path):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"learning": {"enabled": False}}), encoding="utf-8")
    with patch("personalclaw.config.loader.config_path", return_value=p):
        yield p


@pytest.mark.asyncio
async def test_learning_can_be_turned_back_on_through_the_config_patch(cfg_file):
    """The Learning page's off state offers "Turn learning on", which is this PATCH. Before it the
    switch was in no write path at all, so learning turned off in `config.json` could only be
    turned back on there."""
    from personalclaw.config.loader import AppConfig
    from personalclaw.dashboard.handlers import api_personalclaw_config_patch

    app = web.Application()
    app.router.add_patch("/api/config/personalclaw", api_personalclaw_config_patch)
    assert AppConfig.load().learning.enabled is False, "vacuity floor: it starts off"
    async with TestClient(TestServer(app)) as client:
        resp = await client.patch(
            "/api/config/personalclaw", json={"path": "learning.enabled", "value": True}
        )
        assert resp.status == 200, await resp.text()
    assert AppConfig.load().learning.enabled is True


@pytest.mark.asyncio
async def test_the_dashboard_settings_put_answers_what_it_stored(cfg_file):
    """The server rewrites what it is sent — the handle is slugified, the name trimmed — so the
    PUT answers the stored settings. Settings → Account used to chain a second GET for them, and a
    GET that failed after a save that landed said "Couldn't save your username"."""
    from personalclaw.dashboard.handlers.files import api_dashboard_config

    app = web.Application()
    app.router.add_route("*", "/api/dashboard/config", api_dashboard_config)
    async with TestClient(TestServer(app)) as client:
        resp = await client.put(
            "/api/dashboard/config", json={"username": "Jo Smith", "user_name": "  Jo  "}
        )
        assert resp.status == 200
        body = await resp.json()
        assert body["ok"] is True
        assert body["username"] == "jo-smith"
        assert body["user_name"] == "Jo"
        # The same settings the GET answers, key for key — one view, not a second copy of it.
        read = await (await client.get("/api/dashboard/config")).json()
        assert {k: v for k, v in body.items() if k != "ok"} == read

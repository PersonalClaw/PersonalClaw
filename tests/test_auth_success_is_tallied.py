"""A session polling the gateway is one actor in the security log, not a row per request.

Measured on day 8: one idle Home tab made 428 requests in 3 minutes, `security_events.jsonl` grew
~5 MB an hour, and 94% of it was `dashboard.token_auth ok` — the same cookie proving the same
session again, one row per poll. ARCC's audit-logging guidance for this surface (BSC4 "Log Every
Security Event"; the SEL requirement to capture the who, what and when of each transaction) asks
for security EVENTS: failures and state changes. So a failure is still one row each, the first
success of a session in a window is written at once, and the rest of that window is one counted
summary — every success accounted for, at a row per session per quarter hour.

Driven through the REAL middleware in a real aiohttp app with real cookies; only the SEL writer is
captured.
"""

from __future__ import annotations

import asyncio

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import personalclaw.dashboard.token_auth as token_auth
from personalclaw.dashboard.token_auth import (
    bind_token_ip,
    generate_token,
    mark_consumed,
    revoke_all_sessions,
    token_auth_middleware,
)
from personalclaw.sel import SecurityEventLog

PORT = 10000
COOKIE = f"pc_token_{PORT}"


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    revoke_all_sessions()
    # A fresh tally per test (the gateway keeps one for its lifetime). Looked up rather than
    # assumed, so this file runs against a tree without one and fails on its assertions.
    tally = getattr(token_auth, "_SuccessTally", None)
    if tally is not None:
        monkeypatch.setattr(token_auth, "_SUCCESSES", tally())
    yield
    revoke_all_sessions()


@pytest.fixture
def audit(monkeypatch):
    rows: list = []
    monkeypatch.setattr(SecurityEventLog, "log", lambda self, event: rows.append(event))
    return rows


def _app(**mw_kwargs) -> web.Application:
    async def ok(_request: web.Request) -> web.Response:
        return web.json_response({"ok": True})

    app = web.Application(middlewares=[token_auth_middleware(port=PORT, **mw_kwargs)])
    for path in ("/api/loops", "/api/system", "/api/internal/ping"):
        app.router.add_get(path, ok)
    return app


def _session_token(user: str = "owner") -> str:
    token = generate_token(user, ttl_seconds=3600)
    bind_token_ip(token, "127.0.0.1")
    mark_consumed(token)
    return token


def _auth_rows(rows: list, outcome: str) -> list:
    return [r for r in rows if r.operation == "dashboard.token_auth" and r.outcome == outcome]


@pytest.mark.asyncio
async def test_a_polling_session_writes_one_row_and_a_count(audit):
    token = _session_token()
    async with TestClient(TestServer(_app())) as http:
        for i in range(40):
            resp = await http.get("/api/loops" if i % 2 else "/api/system", cookies={COOKIE: token})
            assert resp.status == 200

    assert len(_auth_rows(audit, "ok")) == 1, "a row per request is back"
    token_auth.flush_success_tally()
    first, summary = _auth_rows(audit, "ok")
    assert summary.metadata["summary"] is True
    assert summary.metadata["requests"] == 39
    assert summary.metadata["paths"] == ["/api/loops", "/api/system"]
    assert first.caller_identity == summary.caller_identity == "owner"


@pytest.mark.asyncio
async def test_every_failure_is_its_own_row(audit):
    """The baseline, on both trees: a refusal is an event, every time."""
    async with TestClient(TestServer(_app())) as http:
        for _ in range(5):
            resp = await http.get("/api/loops", cookies={COOKIE: "not-a-token"})
            assert resp.status == 403
    assert len(_auth_rows(audit, "denied")) == 5


@pytest.mark.asyncio
async def test_two_sessions_of_one_user_are_two_actors(audit):
    phone, laptop = _session_token(), _session_token()
    async with TestClient(TestServer(_app())) as http:
        for _ in range(3):
            await http.get("/api/loops", cookies={COOKIE: phone})
            await http.get("/api/loops", cookies={COOKIE: laptop})
    assert len(_auth_rows(audit, "ok")) == 2


@pytest.mark.asyncio
async def test_a_closed_window_is_summarized_by_the_next_request(audit, monkeypatch):
    monkeypatch.setattr(token_auth, "_SUCCESSES", token_auth._SuccessTally(window_secs=0.2))
    monkeypatch.setattr(token_auth, "_SUCCESS_SWEEP_SECS", 0.0)
    token = _session_token()
    async with TestClient(TestServer(_app())) as http:
        for _ in range(5):
            await http.get("/api/loops", cookies={COOKIE: token})
        await asyncio.sleep(0.3)
        await http.get("/api/loops", cookies={COOKIE: token})

    rows = _auth_rows(audit, "ok")
    summaries = [r for r in rows if r.metadata.get("summary")]
    assert [s.metadata["requests"] for s in summaries] == [4]
    assert len(rows) == 3  # the first row, its window's summary, the next window's first row


@pytest.mark.asyncio
async def test_an_internal_grant_is_one_row_family_not_two(audit):
    """An internal-secret grant wrote TWO rows per request (`internal_auth` and a
    `dashboard.token_auth` duplicate). It is one tallied family now."""
    app = _app(internal_paths=frozenset({"/api/internal/ping"}), internal_secret="s3cret")
    async with TestClient(TestServer(app)) as http:
        for _ in range(10):
            resp = await http.get("/api/internal/ping", headers={"X-Internal-Secret": "s3cret"})
            assert resp.status == 200
    granted = [r for r in audit if r.outcome == "granted"]
    assert [(r.operation, r.caller_identity) for r in granted] == [("internal_auth", "internal")]

"""A caller refused before any credential admits it writes its audit and Security-log rows at a
bounded rate.

On the MCP, OpenAI-compatible and A2A surfaces, a call to a surface that's off, from a peer the
surface refuses, or with no token or a wrong one is refused before its caller is known, and so is
a call from another machine that the origin check turns away. Each such refusal wrote an inbound
audit row and a Security-log row, with nothing to bound them, so any program that could reach a
surface (one on another machine too, with ``allow_remote`` on) could fill both logs at whatever
rate it could send.

Every such request is still refused, with the same answer. Its rows are held to
``REFUSED_ROWS_PER_WINDOW`` per caller and ``REFUSED_ROWS_PER_SURFACE`` per surface in a window,
the caller being the transport's peer (an IPv6 one by its /64), never a header. The rest are
counted, and one summary row in each log says how many, when they were and why, once the window
closes or the gateway stops. A signed-in caller's refusals are written one by one, as before.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.dashboard import server
from personalclaw.dashboard.origin import check_origin, origin_refusal
from personalclaw.inbound import a2a, audit, auth, mcp_http, openai_dialect
from personalclaw.sel import SecurityEventLog

PEER = "192.0.2.10"
OTHER = "192.0.2.20"
THIRD = "192.0.2.30"
PUBLIC = "claw.example"
PORT = 10000
FLOOD = 25

#: (surface, path, the audit row's route)
SURFACES = [
    ("mcp", "/mcp", "POST /mcp"),
    ("openai", "/v1/chat/completions", "/v1/chat/completions"),
    ("a2a", "/a2a/tasks", "/a2a/tasks"),
]


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """A private home, no surface token leaking out, and a fresh throttle per test (the gateway
    keeps one for its lifetime). Looked up rather than assumed, so this file runs against a tree
    without one and fails on its assertions."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    keys = [auth.token_env_key(surface) for surface in auth.surfaces()]
    for key in keys:
        monkeypatch.delenv(key, raising=False)
    rows = getattr(audit, "_RefusedRows", None)
    if rows is not None:
        monkeypatch.setattr(audit, "_REFUSED_ROWS", rows())
    yield
    for key in keys:
        os.environ.pop(key, None)


@pytest.fixture
def sel_rows(monkeypatch):
    rows: list = []
    monkeypatch.setattr(SecurityEventLog, "log", lambda self, event: rows.append(event))
    return rows


def _config(monkeypatch, *, remote: bool = True) -> None:
    """External access on, the three surfaces on with ``allow_remote`` set to *remote*, and each
    with a token, so a call without it reaches the bearer check."""
    from personalclaw.config.external_access import ExternalAccessConfig
    from personalclaw.config.external_access import ExternalAccessSurfaceConfig as Surface
    from personalclaw.config.loader import AppConfig

    cfg = AppConfig()
    cfg.external_access = ExternalAccessConfig(
        enabled=True,
        public_url=f"https://{PUBLIC}",
        a2a=Surface(enabled=True, allow_remote=remote),
        mcp=Surface(enabled=True, allow_remote=remote),
        openai=Surface(enabled=True, allow_remote=remote),
    )
    monkeypatch.setattr(AppConfig, "load", staticmethod(lambda *a, **k: cfg))
    for surface in ("a2a", "mcp", "openai"):
        auth.create_surface_token(surface)


_PEER: dict[str, str] = {"host": PEER}


def _peer(monkeypatch, host: str) -> None:
    """The transport's peer, as the surfaces, the origin check and the throttle read it."""
    monkeypatch.setattr(auth, "_peer_host", lambda request: _PEER["host"])
    monkeypatch.setitem(_PEER, "host", host)


@web.middleware
async def _as_peer(request: web.Request, handler):
    return await handler(request.clone(remote=_PEER["host"]))


@web.middleware
async def _csrf(request: web.Request, handler):
    """``server.csrf_middleware``'s check, its own pieces in its own order."""
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        if not check_origin(request, require=True, fallback_header="Referer"):
            if not server._origin_refusal_waived(request, PORT):
                return origin_refusal(request)
    return await handler(request)


async def _never(*_a, **_k):
    raise AssertionError("no turn runs for a refused call")


async def _client() -> TestClient:
    app = web.Application(middlewares=[_as_peer, _csrf])
    app["allowed_origins"] = {f"http://127.0.0.1:{PORT}"}
    a2a.register_routes(app)
    app.router.add_post("/mcp", mcp_http.handle_mcp)
    openai_dialect.register_routes(app, turn_runner=_never, agent_mover=_never)
    client = TestClient(TestServer(app), cookie_jar=aiohttp.DummyCookieJar())
    await client.start_server()
    return client


async def _flood(client: TestClient, path: str, n: int, headers=None) -> list[int]:
    statuses = []
    for i in range(n):
        extra = headers(i) if callable(headers) else (headers or {})
        resp = await client.post(path, data=b"{", headers={"Host": PUBLIC, **extra})
        statuses.append(resp.status)
        resp.release()
    return statuses


def _rows(surface: str) -> list[dict]:
    """The audit file's rows for *surface*, oldest first."""
    return [r for r in reversed(audit.recent(limit=10_000)) if r.get("surface") == surface]


def _denied(sel_rows: list) -> list:
    return [e for e in sel_rows if e.source == "inbound" and e.outcome == "denied"]


@pytest.mark.parametrize("surface,path,route", SURFACES, ids=[s[0] for s in SURFACES])
@pytest.mark.asyncio
async def test_a_flood_of_refusals_from_one_caller_writes_a_bounded_number_of_rows(
    monkeypatch, sel_rows, surface, path, route
):
    """🔴 Red before: each of the 25 refusals wrote its own audit row and Security-log row."""
    _config(monkeypatch)
    _peer(monkeypatch, PEER)
    client = await _client()
    try:
        statuses = await _flood(client, path, FLOOD)
    finally:
        await client.close()

    assert statuses == [401] * FLOOD  # every one is still refused, the same way
    written = _rows(surface)
    assert len(written) < FLOOD, "every refusal wrote its own row"
    limit = audit.REFUSED_ROWS_PER_WINDOW
    assert len(written) == limit
    assert len(_denied(sel_rows)) == limit

    audit.flush_refused_rows()
    written = _rows(surface)
    summary = written[-1]
    assert summary["suppressed"] == FLOOD - limit
    assert (summary["status"], summary["route"], summary["client_id"]) == (401, route, "")
    assert len(written) == limit + 1
    sel_summary = _denied(sel_rows)[-1]
    assert sel_summary.metadata["suppressed"] == FLOOD - limit
    assert len(_denied(sel_rows)) == limit + 1


@pytest.mark.asyncio
async def test_one_callers_flood_leaves_another_callers_rows_written(monkeypatch, sel_rows):
    _config(monkeypatch)
    _peer(monkeypatch, PEER)
    client = await _client()
    try:
        await _flood(client, "/mcp", FLOOD)
        _peer(monkeypatch, OTHER)
        statuses = await _flood(client, "/mcp", 3)
    finally:
        await client.close()
    assert statuses == [401] * 3
    assert len(_rows("mcp")) == audit.REFUSED_ROWS_PER_WINDOW + 3


@pytest.mark.asyncio
async def test_a_header_never_makes_a_caller_another(monkeypatch, sel_rows):
    """The caller is the transport's peer: a forwarding header changed on every call is still one
    caller."""
    _config(monkeypatch)
    _peer(monkeypatch, PEER)
    client = await _client()
    try:
        await _flood(
            client,
            "/mcp",
            FLOOD,
            headers=lambda i: {
                "X-Forwarded-For": f"198.51.100.{i}",
                "X-Real-IP": f"198.51.100.{i}",
                "Forwarded": f"for=198.51.100.{i}",
            },
        )
    finally:
        await client.close()
    assert len(_rows("mcp")) == audit.REFUSED_ROWS_PER_WINDOW


@pytest.mark.parametrize("surface,path", [("a2a", "/a2a/tasks"), ("mcp", "/mcp")])
@pytest.mark.asyncio
async def test_a_signed_in_callers_refusals_are_each_written(monkeypatch, sel_rows, surface, path):
    """The control: a caller with the surface's token is known, so each of its refusals is its own
    row, as before: A2A's to a body that isn't JSON, MCP's to its rate limit."""
    from personalclaw.inbound import caps

    _config(monkeypatch)
    _peer(monkeypatch, PEER)
    monkeypatch.setattr(caps, "check_rate_for_client", lambda *a, **k: surface == "a2a")
    token = {"Authorization": f"Bearer {auth.create_surface_token(surface)}"}
    client = await _client()
    try:
        statuses = await _flood(client, path, FLOOD, headers=token)
    finally:
        await client.close()
    assert statuses == [400 if surface == "a2a" else 429] * FLOOD
    assert len(_rows(surface)) == FLOOD


def test_a_home_change_writes_the_old_homes_count_to_its_own_file(tmp_path, sel_rows):
    """The windows are kept for one audit file. When the home changes, the old one's count goes
    to its own file, and not to the Security log now written, which is the new home's."""
    rows = audit._RefusedRows()
    old, new = tmp_path / "old" / "inbound_audit.jsonl", tmp_path / "new" / "inbound_audit.jsonl"
    for _ in range(audit.REFUSED_ROWS_PER_WINDOW + 3):
        rows.admit(old, "mcp", PEER, route="POST /mcp", status=401, refused="bad bearer")
    rows.sweep(new)
    assert [json.loads(line)["suppressed"] for line in old.read_text().splitlines()] == [3]
    assert not new.exists()
    assert sel_rows == []


@pytest.mark.asyncio
async def test_the_origin_checks_refusal_of_another_machine_is_held_too(monkeypatch, sel_rows):
    """With ``allow_remote`` off, a call from another machine is refused by the origin check
    before any surface reads it, and that refusal writes a row of its own."""
    _config(monkeypatch, remote=False)
    _peer(monkeypatch, PEER)
    client = await _client()
    try:
        statuses = await _flood(client, "/a2a/tasks", FLOOD)
    finally:
        await client.close()
    assert statuses == [403] * FLOOD
    assert len(_rows("a2a")) == audit.REFUSED_ROWS_PER_WINDOW
    audit.flush_refused_rows()
    assert _rows("a2a")[-1]["suppressed"] == FLOOD - audit.REFUSED_ROWS_PER_WINDOW


@pytest.mark.asyncio
async def test_the_next_window_writes_the_last_ones_count_first(monkeypatch, sel_rows):
    """The throttle's own clock is moved past the window, so the row can't race a real one."""
    clock = {"tick": 0.0}
    monkeypatch.setattr(audit, "_REFUSED_ROWS", audit._RefusedRows(clock=lambda: clock["tick"]))
    limit = audit.REFUSED_ROWS_PER_WINDOW
    _config(monkeypatch)
    _peer(monkeypatch, PEER)
    client = await _client()
    try:
        await _flood(client, "/mcp", limit + 2)
        clock["tick"] = audit.REFUSED_ROWS_WINDOW_SECS
        await _flood(client, "/mcp", 1)
    finally:
        await client.close()
    written = _rows("mcp")
    assert len(written) == limit + 2
    assert written[limit]["suppressed"] == 2
    assert "suppressed" not in written[limit + 1]


@pytest.mark.asyncio
async def test_callers_past_the_open_cap_share_one_window(monkeypatch, sel_rows):
    """The throttle keeps a window for at most so many callers at once; further callers share
    one, so a caller with many addresses can't grow it, and their rows are still bounded."""
    monkeypatch.setattr(audit, "_REFUSED_ROWS", audit._RefusedRows(max_open=1))
    limit = audit.REFUSED_ROWS_PER_WINDOW
    _config(monkeypatch)
    client = await _client()
    try:
        _peer(monkeypatch, PEER)
        await _flood(client, "/mcp", 1)
        _peer(monkeypatch, OTHER)
        await _flood(client, "/mcp", limit)
        _peer(monkeypatch, THIRD)
        await _flood(client, "/mcp", 4)
    finally:
        await client.close()
    assert len(_rows("mcp")) == 1 + limit
    audit.flush_refused_rows()
    assert _rows("mcp")[-1]["suppressed"] == 4


@pytest.mark.asyncio
async def test_the_gateway_writes_the_count_when_it_stops(tmp_path, monkeypatch):
    """Through the real gateway: its stop writes the open window's summary, so a count isn't
    lost with the process."""
    from signed_in_gateway import signed_in_gateway

    async with signed_in_gateway(tmp_path, monkeypatch) as gw:
        async with aiohttp.ClientSession(cookie_jar=aiohttp.DummyCookieJar()) as session:
            for _ in range(FLOOD):
                async with session.post(gw.url("/mcp"), data=b"{}") as resp:
                    assert resp.status == 404  # the surface is off
        assert len(_rows("mcp")) == audit.REFUSED_ROWS_PER_WINDOW
    written = _rows("mcp")
    assert written[-1]["suppressed"] == FLOOD - audit.REFUSED_ROWS_PER_WINDOW


def _at(second: int, *, hour: int = 2) -> datetime:
    return datetime(2026, 11, 21, hour, 0, second, tzinfo=timezone.utc)


def test_a_summary_written_after_a_quiet_gap_says_when_its_refusals_were(tmp_path, sel_rows):
    """🔴 Red before: the summary was dated when it was written, at the next row of any request
    after the window, which can be hours later, and said nothing of when the refusals were."""
    clock = {"tick": 0.0, "wall": _at(0)}
    rows = audit._RefusedRows(clock=lambda: clock["tick"], now=lambda: clock["wall"])
    path = tmp_path / "inbound_audit.jsonl"
    for i in range(FLOOD):
        clock.update(tick=float(i), wall=_at(i))
        rows.admit(path, "mcp", PEER, route="POST /mcp", status=401, refused="bad bearer")
    clock.update(tick=3 * 3600.0, wall=_at(0, hour=5))
    rows.sweep(path)
    (summary,) = [json.loads(line) for line in path.read_text().splitlines()]
    limit = audit.REFUSED_ROWS_PER_WINDOW
    assert summary["suppressed"] == FLOOD - limit
    assert summary["first_ts"] == _at(limit).isoformat()
    assert summary["ts"] == summary["last_ts"] == _at(FLOOD - 1).isoformat()
    assert "02:00:10 to 02:00:24 UTC" in summary["refused_reason"]
    (event,) = _denied(sel_rows)
    assert (event.metadata["first_ts"], event.metadata["last_ts"]) == (
        summary["first_ts"],
        summary["last_ts"],
    )


def test_a_distinct_refusal_inside_a_flood_is_counted_by_its_reason(tmp_path, sel_rows):
    """A revoked token's refusal behind a flood of bad ones from the same caller is counted by its
    own reason, not only as the last one's. Past so many reasons, the rest are counted together."""
    limit = audit.REFUSED_ROWS_PER_WINDOW
    path = tmp_path / "inbound_audit.jsonl"
    rows = audit._RefusedRows()
    for why in ["bad bearer"] * (limit * 2) + ["token revoked"] * 2:
        rows.admit(path, "mcp", PEER, route="POST /mcp", status=401, refused=why)
    rows.flush()
    assert json.loads(path.read_text())["reasons"] == {"bad bearer": limit, "token revoked": 2}

    path.unlink()
    rows = audit._RefusedRows()
    for why in ["bad bearer"] * limit + [f"reason {i}" for i in range(9)]:
        rows.admit(path, "mcp", PEER, route="POST /mcp", status=401, refused=why)
    rows.flush()
    reasons = json.loads(path.read_text())["reasons"]
    assert reasons == {**{f"reason {i}": 1 for i in range(5)}, "other reasons": 4}


def test_one_host_rotating_through_its_ipv6_64_is_one_caller(tmp_path):
    """🔴 Red before: each address was a caller of its own, so one /64 rotating 200 addresses
    wrote a window of rows for each of 64 of them."""
    rows = audit._RefusedRows()
    path = tmp_path / "inbound_audit.jsonl"
    written = sum(
        rows.admit(path, "mcp", f"2001:db8:1:2::{i:x}", route="POST /mcp", status=401, refused="x")
        for i in range(200)
        for _ in range(15)
    )
    assert written == audit.REFUSED_ROWS_PER_WINDOW
    other = rows.admit(path, "mcp", "2001:db8:1:3::1", route="POST /mcp", status=401, refused="x")
    assert other, "another /64 is another caller"


def test_an_ipv4_mapped_address_is_the_same_caller_as_its_ipv4_form(tmp_path):
    rows = audit._RefusedRows()
    path = tmp_path / "inbound_audit.jsonl"
    written = sum(
        rows.admit(path, "a2a", peer, route="/a2a/tasks", status=401, refused="x")
        for peer in [f"::ffff:{PEER}"] * 8 + [PEER] * 8
    )
    assert written == audit.REFUSED_ROWS_PER_WINDOW


def test_many_callers_at_once_write_at_most_the_surfaces_rows(tmp_path):
    """🔴 Red before: each of many callers wrote its own allowance. Every caller still unknown to
    one surface writes ``REFUSED_ROWS_PER_SURFACE`` between them, and another surface its own."""
    rows = audit._RefusedRows()
    path = tmp_path / "inbound_audit.jsonl"
    limit = audit.REFUSED_ROWS_PER_WINDOW
    written = sum(
        rows.admit(path, "mcp", f"198.51.100.{i}", route="POST /mcp", status=401, refused="x")
        for i in range(30)
        for _ in range(limit)
    )
    assert written == audit.REFUSED_ROWS_PER_SURFACE
    assert rows.admit(path, "a2a", PEER, route="/a2a/tasks", status=401, refused="x")


@pytest.mark.asyncio
async def test_a_throttle_that_fails_writes_the_row(monkeypatch, sel_rows):
    """Fail open to writing: a throttle that raises never loses a refusal's row."""

    def _broken(*_a, **_k):
        raise RuntimeError("throttle broken")

    monkeypatch.setattr(audit._REFUSED_ROWS, "admit", _broken)
    _config(monkeypatch)
    _peer(monkeypatch, PEER)
    client = await _client()
    try:
        assert await _flood(client, "/mcp", 3) == [401] * 3
    finally:
        await client.close()
    assert len(_rows("mcp")) == 3


def test_a_callers_window_ends_on_time_between_sweeps(tmp_path, sel_rows):
    """A caller's window ends when its time is up, not at the next sweep for closed windows: a
    refusal just past it, less than a sweep after the last, is written after the old count."""
    clock = {"tick": 0.0}
    rows = audit._RefusedRows(clock=lambda: clock["tick"])
    path = tmp_path / "inbound_audit.jsonl"
    limit, window = audit.REFUSED_ROWS_PER_WINDOW, audit.REFUSED_ROWS_WINDOW_SECS

    def admit() -> bool:
        return rows.admit(path, "mcp", PEER, route="POST /mcp", status=401, refused="bad bearer")

    assert sum(admit() for _ in range(limit + 2)) == limit
    clock["tick"] = window - 0.1
    assert not admit()  # still its first window, and the sweep this runs closes nothing
    clock["tick"] = window + 0.1
    assert admit(), "its window is over, though no sweep is due"
    (summary,) = [json.loads(line) for line in path.read_text().splitlines()]
    assert summary["suppressed"] == 3

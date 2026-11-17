"""An automation client's socket hears only its loops, and closes when its session ends
(scoped-pairing design §4, C3).

A scoped session's ``/api/ws`` socket is registered with its scope, its session's nonce and its
end. A frame reaches it only when ``device_scopes.FRAME_SCOPES`` names the frame's type under a
scope it holds and, for a loop's frame, the frame is about a loops-table row (F11): no chat's
frame, no notification, no approval and no connect-time session list. A sign-out, the idle end,
a mismatch and the session's own end close the socket, and a loop's stream, at once (F9); and
an open scoped socket counts as use, so it keeps the client from its idle end.
"""

from __future__ import annotations

import asyncio
import json
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from personalclaw.auth import pairing
from personalclaw.dashboard import session_store as ss
from personalclaw.dashboard import token_auth
from personalclaw.dashboard.handlers import devices as devices_h
from personalclaw.dashboard.state import DashboardState

DAY = 86400


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    import personalclaw.config.loader as loader

    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(pairing, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(ss, "config_dir", lambda: tmp_path, raising=False)
    monkeypatch.setattr("personalclaw.loop.files.config_dir", lambda: tmp_path)
    (tmp_path / "config.json").write_text(json.dumps({"auth": {}}), encoding="utf-8")
    token_auth.use_persistent_secret()
    token_auth.revoke_all_sessions()
    yield tmp_path
    token_auth.revoke_all_sessions()


@pytest.fixture()
def state() -> DashboardState:
    return DashboardState(sessions=MagicMock(count=0), start_time=0.0)


def _scoped(scope=("loops",), ttl: int = 30 * DAY):
    device = ss.DeviceInfo(id=ss.new_device_id(), name="Nightly runner", kind="automation")
    return token_auth.mint_session(
        devices_h.PAIRED_DEVICE_USER,
        ttl,
        issuer=ss.ISSUER_PAIR,
        device=device,
        bound=True,
        scope=tuple(scope),
    )


def _sock() -> MagicMock:
    return MagicMock(closed=False, send_str=AsyncMock(), close=AsyncMock())


def _loop_row() -> str:
    from personalclaw.loop import store
    from personalclaw.loop.loop import Loop

    return store.create(Loop(id="", name="nightly docs", kind="general", task="tidy the docs")).id


def _register(state, ws, minted, scope=("loops",)) -> None:
    state.register_ws(ws, scope=tuple(scope), nonce=minted.nonce, ends=minted.expires_at)


def _sent_types(ws) -> list[str]:
    return [json.loads(call.args[0])["type"] for call in ws.send_str.call_args_list]


# ── What it hears ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_scoped_socket_hears_its_loops_work_and_nothing_else(state) -> None:
    lid = _loop_row()
    client, owner = _sock(), _sock()
    _register(state, client, _scoped())
    state.register_ws(owner)
    state.broadcast_ws("work_phase", {"work": f"loop:{lid}", "phase": "working", "line": "x"})
    state.broadcast_ws("work_phase", {"work": "chat:abc", "phase": "working", "line": "x"})
    state.broadcast_ws("work_phase", {"work": "loop:not-a-row", "phase": "working", "line": "x"})
    state.broadcast_ws(
        "chat_message", {"session": "dashboard:c1", "role": "assistant", "content": "hi"}
    )
    state.broadcast_ws("approval", {"id": "a1", "session": f"loop-{lid}"})
    state.broadcast_ws("waiting_changed", {"count": 2})
    state.broadcast_ws("notification", {"title": "done", "body": "your answer"})
    state.broadcast_ws("sessions", [{"key": "dashboard:c1"}])
    await asyncio.sleep(0)
    assert _sent_types(client) == ["work_phase"]
    assert json.loads(client.send_str.call_args.args[0])["data"]["work"] == f"loop:{lid}"
    assert len(_sent_types(owner)) == 8, "the owner's socket hears everything, as before"


@pytest.mark.asyncio
async def test_a_status_socket_hears_no_loop(state) -> None:
    lid = _loop_row()
    client = _sock()
    _register(state, client, _scoped(("status",)), scope=("status",))
    state.broadcast_ws("work_phase", {"work": f"loop:{lid}", "phase": "working", "line": "x"})
    await asyncio.sleep(0)
    assert _sent_types(client) == []


@pytest.mark.asyncio
async def test_the_connect_time_session_list_is_not_sent(state) -> None:
    client = _sock()
    _register(state, client, _scoped())
    await state.send_ws_event(client, "sessions", [{"key": "dashboard:c1", "title": "secret"}])
    await state.send_ws_event(client, "spa_build", {"build": "abc"})
    assert _sent_types(client) == ["spa_build"], "the build it runs is the shell's; nothing else"


# ── It closes when its session ends (F9) ────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("how", ["signed_out", "idle", "mismatch"])
async def test_the_session_ending_closes_its_socket(state, how, monkeypatch) -> None:
    minted = _scoped()
    client, other = _sock(), _sock()
    _register(state, client, minted)
    _register(state, other, _scoped())
    if how == "signed_out":
        token_auth.sign_out([minted.nonce], ss.END_SIGNED_OUT_ELSEWHERE, actor="owner")
    elif how == "idle":
        later = time.time() + 15 * DAY
        monkeypatch.setattr(time, "time", lambda: later)
        assert not token_auth.validate_token(minted.token, use_session_exp=True)[0]
    else:
        token_auth._state._scopes[minted.nonce] = ("status",)
        assert not token_auth.validate_token(minted.token, use_session_exp=True)[0]
    await asyncio.sleep(0)
    client.close.assert_awaited()
    other.close.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_socket_closes_when_its_session_runs_out(state) -> None:
    minted = _scoped(ttl=1)
    client = _sock()
    state.register_ws(client, scope=("loops",), nonce=minted.nonce, ends=time.time() + 0.05)
    await asyncio.sleep(0.2)
    client.close.assert_awaited()


@pytest.mark.asyncio
async def test_a_loops_stream_closes_when_its_session_ends() -> None:
    """The stream registers itself the way the socket does, so a sign-out ends it too."""
    from personalclaw.dashboard import scoped_sessions

    minted = _scoped()
    closed: list[str] = []
    release = scoped_sessions.hold_open(minted.nonce, lambda: closed.append("stream"))
    token_auth.sign_out([minted.nonce], ss.END_SIGNED_OUT_ELSEWHERE, actor="owner")
    assert closed == ["stream"]
    release()
    token_auth.sign_out([minted.nonce], ss.END_SIGNED_OUT_ELSEWHERE, actor="owner")
    assert closed == ["stream"], "released: nothing is closed twice"


@pytest.mark.asyncio
async def test_a_loops_stream_served_to_a_client_ends_when_its_session_does(state) -> None:
    """Through the real stream route and the real gate: signed out, the open stream ends."""
    from aiohttp import DummyCookieJar, web
    from aiohttp.test_utils import TestClient, TestServer

    from personalclaw.dashboard.device_scopes import device_scope_middleware
    from personalclaw.dashboard.handlers import loop_routes

    lid = _loop_row()
    minted = _scoped()
    app = web.Application(
        middlewares=[token_auth.token_auth_middleware(port=10000), device_scope_middleware]
    )
    app["port"] = 10000
    app["allowed_origins"] = {"http://localhost:10000"}
    app["state"] = state
    app.router.add_get("/api/loops/{id}/stream", loop_routes.api_loop_stream)
    headers = {"Cookie": f"pc_token_10000={minted.token}", "X-PersonalClaw-Pair": minted.pair}
    async with TestClient(TestServer(app), cookie_jar=DummyCookieJar()) as client:
        resp = await client.get(f"/api/loops/{lid}/stream", headers=headers)
        assert resp.status == 200
        first = await asyncio.wait_for(resp.content.readline(), 5)
        assert first, "the snapshot arrives"
        token_auth.sign_out([minted.nonce], ss.END_SIGNED_OUT_ELSEWHERE, actor="owner")

        async def drained() -> None:
            try:
                while await resp.content.readline():
                    pass
            except Exception:  # noqa: BLE001 — a cut connection ends the stream too
                pass

        await asyncio.wait_for(drained(), 5)


# ── An open socket is use (R3-6) ────────────────────────────────────────


def test_an_open_socket_stamps_its_session_as_used(monkeypatch) -> None:
    from personalclaw.dashboard import scoped_sessions

    minted = _scoped()
    later = time.time() + 13 * DAY
    monkeypatch.setattr(time, "time", lambda: later)
    scoped_sessions.still_open(minted.nonce, ("loops",))
    monkeypatch.setattr(time, "time", lambda: later + 2 * DAY)
    assert token_auth.validate_token(minted.token, use_session_exp=True)[
        0
    ], "two days after the socket's last stamp is no idle end"


# ── The gate admits the socket and the stream now ───────────────────────


def test_the_socket_and_a_loops_stream_are_reachable_rows() -> None:
    from personalclaw.dashboard import device_scopes

    assert device_scopes.route_scope("GET", "/api/ws").scope == device_scopes.SHELL
    stream = device_scopes.route_scope("GET", "/api/loops/{id}/stream")
    assert stream.scope == "loops" and stream.target == device_scopes.TARGET_LOOP
    assert (
        device_scopes.route_scope("GET", "/api/ws/terminal/{session_id}") is device_scopes.OUTSIDE
    )

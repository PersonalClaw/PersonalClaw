"""Issue 2963 — EVERY WS send path honors `permissions.events`, not just `broadcast_ws`.

`broadcast_ws` is documented as the one WS producer and enforces the app allowlist. Three
other paths wrote to the same app-scoped socket without asking it:

* the on-connect `sessions` push — a direct `ws.send_json` right after `register_ws`, so
  session keys, titles and the yolo flag were the FIRST frame of every connection;
* `subscribe_logs` + the ring log handler — the socket was added to
  `_ws_log_subscribers` with no app check, and every record (plus the 1000-entry ring
  replay) went to it;
* `subscribe_subagents` + `broadcast_ws_subagent_subscribers` — subagent tasks, prompts
  and streaming text to a set that consulted no app identity at all.

So an app declaring `permissions.api: ["/api/ws"]` plus one private event received the
owner's whole backend log stream and session list. The Store shows `permissions.events` as
the enforced install-consent list; for those three paths it was decoration.

**Asserted at the socket, through the real route and the real token middleware** — the
app-scoped connection is opened exactly as the SDK opens it (`?token=` + `?app_token=`),
and every assertion reads the frames a client actually received rather than inspecting the
producer. Two floors keep that from passing vacuously: a DECLARED event must still arrive
on the app socket, and an owner socket must still receive `sessions`, the log replay, the
live log stream and subagent chunks exactly as before. "Filter everything" fails here.
"""

from __future__ import annotations

import asyncio
import json
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import ClientSession, WSMsgType, web
from aiohttp.test_utils import TestServer

from personalclaw.apps import manager as apps_manager
from personalclaw.dashboard import session_store as ss
from personalclaw.dashboard import state as state_mod
from personalclaw.dashboard import token_auth
from personalclaw.dashboard import ws as ws_mod
from personalclaw.dashboard.handlers.updates import _log_ring, _RingLogHandler
from personalclaw.dashboard.origin import build_allowed_origins
from personalclaw.dashboard.state import DashboardState

PORT = 10000

#: The probe app from the issue: it declares route access to the socket and exactly one
#: private event. `log`, `sessions`, `subagent_chunk` are all undeclared.
NOSY = "nosy-app"
DECLARED = "nosy_ping"
#: A second app, declaring the subagent chunk stream — the floor for that path.
CHATTY = "chatty-app"


def _install(home, name: str, events: list[str]) -> None:
    """An installed app on disk: `app.json` is what `checker_for` reads."""
    appdir = home / "apps" / name
    appdir.mkdir(parents=True, exist_ok=True)
    (appdir / "app.json").write_text(
        json.dumps(
            {
                "name": name,
                "version": "0.2.0",
                "displayName": name,
                "description": "issue-2963 probe",
                "permissions": {"api": ["/api/ws"], "events": events},
            }
        ),
        encoding="utf-8",
    )
    (appdir / "installed.json").write_text(
        json.dumps({"name": name, "version": "0.2.0", "enabled": True}), encoding="utf-8"
    )


@pytest.fixture
def home(tmp_path, monkeypatch):
    """An isolated home with both probe apps installed. The real home is never touched."""
    import personalclaw.config.loader as loader

    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(ss, "config_dir", lambda: tmp_path, raising=False)
    # `apps.manager` binds config_dir at import, and `checker_for` resolves the manifest
    # through it — patching only the loader would read the REAL home's apps/.
    monkeypatch.setattr(apps_manager, "config_dir", lambda: tmp_path, raising=False)
    monkeypatch.setattr(state_mod, "config_dir", lambda: tmp_path)
    assert apps_manager.app_dir(NOSY).parent == tmp_path / "apps", "apps/ redirect missed"
    _install(tmp_path, NOSY, [DECLARED])
    _install(tmp_path, CHATTY, ["subagent_chunk"])
    token_auth.use_persistent_secret()
    token_auth.revoke_all_sessions()
    yield tmp_path
    token_auth.revoke_all_sessions()


@pytest.fixture
def state(home) -> DashboardState:
    """A state whose `sessions` frame would carry something worth leaking."""
    st = DashboardState(sessions=MagicMock(count=0), start_time=0.0)
    session = MagicMock()
    session.to_dict.return_value = {"key": "chat-1", "title": "quarterly board notes"}
    st._sessions = {"chat-1": session}
    return st


def _server_app(st: DashboardState) -> web.Application:
    app = web.Application(
        middlewares=[token_auth.token_auth_middleware(port=PORT, local_only=False)]
    )
    app["allowed_origins"] = build_allowed_origins(PORT, False)
    app["state"] = st
    app.router.add_get("/api/ws", ws_mod.api_ws)
    return app


async def _drain(sock, window: float = 0.4) -> list[dict]:
    """Every frame the client can read within `window`. Empty list == nothing arrived."""
    frames: list[dict] = []
    while True:
        try:
            msg = await asyncio.wait_for(sock.receive(), window)
        except (asyncio.TimeoutError, TimeoutError):
            return frames
        if msg.type is not WSMsgType.TEXT:
            return frames
        frames.append(json.loads(msg.data))


def _types(frames: list[dict]) -> list[str]:
    return [f.get("type") for f in frames]


def _log_record(text: str) -> logging.LogRecord:
    return logging.LogRecord(
        name="personalclaw.dashboard.handlers.updates",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=text,
        args=(),
        exc_info=None,
    )


def _emit_live_log(st: DashboardState, text: str) -> None:
    """One record through the ring handler — the live `log` producer."""
    handler = _RingLogHandler(_log_ring)
    handler.setFormatter(logging.Formatter("%(message)s"))
    handler.set_state(st)
    handler.emit(_log_record(text))


# ── the app-scoped socket: the leak ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_app_socket_receives_neither_the_session_list_nor_the_log_stream(state) -> None:
    """The issue's repro, end to end: connect, subscribe_logs, raise a log line."""
    _log_ring.clear()
    _log_ring.append(json.dumps({"level": "INFO", "msg": "ring history the app never declared"}))

    server = TestServer(_server_app(state))
    await server.start_server()
    try:
        owner = token_auth.generate_token("owner")
        app_token = token_auth.generate_token("owner", app=NOSY)
        url = server.make_url(f"/api/ws?token={owner}&app_token={app_token}")
        async with ClientSession() as sess:
            async with sess.ws_connect(
                url, headers={"Origin": f"http://localhost:{server.port}"}
            ) as sock:
                # (A) the on-connect push — the FIRST frame of every connection
                assert "sessions" not in _types(
                    await _drain(sock)
                ), "the app got the session list (keys/titles/yolo) it never declared"

                # (B) the log stream: ring replay + every live record after
                await sock.send_json({"type": "subscribe_logs"})
                assert "log" not in _types(await _drain(sock)), "the app got the log ring replay"
                app_ws = next(w for w, name in state._ws_app.items() if name == NOSY)
                assert (
                    app_ws not in state._ws_log_subscribers
                ), "an app that cannot receive `log` was still added as a log subscriber"
                _emit_live_log(state, "owner-only backend detail")
                assert "log" not in _types(await _drain(sock)), "the app got a live log record"

                # (C) the floor: its ONE declared event still arrives, so the gate is a
                # filter and not a mute.
                state.broadcast_ws(DECLARED, {"ok": True})
                assert _types(await _drain(sock)) == [DECLARED]
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_app_socket_is_refused_the_subagent_chunk_stream(state) -> None:
    """`broadcast_ws_subagent_subscribers` iterated its set with no app filter at all."""
    nosy = MagicMock(closed=False, send_str=AsyncMock())
    chatty = MagicMock(closed=False, send_str=AsyncMock())
    owner = MagicMock(closed=False, send_str=AsyncMock())
    state.register_ws(nosy, app=NOSY)
    state.register_ws(chatty, app=CHATTY)
    state.register_ws(owner)

    assert state.subscribe_subagents(nosy) is False
    assert state.subscribe_subagents(chatty) is True
    assert state.subscribe_subagents(owner) is True

    state.broadcast_ws_subagent_subscribers(
        "subagent_chunk", {"id": "a1", "text": "the subagent's prompt and output"}
    )

    nosy.send_str.assert_not_called()
    # Both floors: the app that DECLARED the stream gets it, and so does the owner.
    chatty.send_str.assert_called_once()
    owner.send_str.assert_called_once()


@pytest.mark.asyncio
async def test_a_declared_log_event_is_still_delivered_to_an_app(state) -> None:
    """The log gate is the manifest's answer, not a blanket refusal for apps."""
    _install(state_mod.config_dir(), "loggy-app", ["log"])
    loggy = MagicMock(closed=False, send_str=AsyncMock())
    state.register_ws(loggy, app="loggy-app")
    assert state.subscribe_logs(loggy) is True
    _emit_live_log(state, "a record the app asked for")
    loggy.send_str.assert_called_once()
    assert json.loads(loggy.send_str.call_args[0][0])["type"] == "log"


# ── the owner socket: unchanged ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_owner_socket_still_gets_sessions_the_ring_replay_and_live_logs(state) -> None:
    """The fix must not narrow the owner's own connection — this is the whole floor."""
    _log_ring.clear()
    _log_ring.append(json.dumps({"level": "INFO", "msg": "history the owner should see"}))

    server = TestServer(_server_app(state))
    await server.start_server()
    try:
        owner = token_auth.generate_token("owner")
        url = server.make_url(f"/api/ws?token={owner}")
        async with ClientSession() as sess:
            async with sess.ws_connect(
                url, headers={"Origin": f"http://localhost:{server.port}"}
            ) as sock:
                first = await _drain(sock)
                assert _types(first) == ["sessions"]
                assert first[0]["data"] == [{"key": "chat-1", "title": "quarterly board notes"}]
                # The rows and nothing beside them: no approval posture rides the frame.
                assert set(first[0]) == {"type", "data"}, first[0]

                await sock.send_json({"type": "subscribe_logs"})
                replay = await _drain(sock)
                assert _types(replay) == ["log"]
                assert replay[0]["data"]["msg"] == "history the owner should see"

                _emit_live_log(state, "a live record")
                live = await _drain(sock)
                assert _types(live) == ["log"]
                assert live[0]["data"]["msg"] == "a live record"
    finally:
        await server.close()

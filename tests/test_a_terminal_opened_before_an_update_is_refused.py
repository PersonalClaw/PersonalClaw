"""A terminal session id that names no tier is refused: it was opened before an update.

Every id the create route makes names the tier its shell runs in, ``<id>@<tier>``, the host's
(``none``) included, and every open reads the tier from the id. An id from before ids carried a
tier says nothing about where its shell ran: reopening one after the gateway restarted opened a
shell on this computer, which for a tab opened in a sandbox tier was the host shell under a tab
that named the sandbox. So such an id is refused with one sentence, host terminals included. It is
a one-time cost: no id made now can be one.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from typing import Any
from unittest.mock import MagicMock

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw import tmux_substrate
from personalclaw.dashboard.handlers import terminal

pytestmark = pytest.mark.asyncio

LEGACY = "0123456789ab"
SENTENCE = "This terminal was opened before an update; open a new one."


class _FakeProcess:
    def __init__(self) -> None:
        self.pid = 4246
        self.returncode: int | None = None
        self._done = asyncio.Event()

    async def wait(self) -> int | None:
        await self._done.wait()
        return self.returncode


@pytest.fixture
def gateway(tmp_path, monkeypatch):
    """A just-restarted gateway: nothing in its registry, persistence as the test asks, and every
    shell recorded, never started."""
    terminal._enabled_cache[0] = True
    terminal._enabled_cache[1] = time.monotonic()
    cfg_file = tmp_path / "config.json"
    monkeypatch.setattr(terminal, "config_path", lambda: cfg_file)
    monkeypatch.setattr(terminal, "_sel", lambda: MagicMock())
    monkeypatch.setattr(tmux_substrate.shutil, "which", lambda _b: "/usr/bin/tmux")

    spawned: list[list[str]] = []
    worker_fds: list[int] = []

    async def _fake_spawn(*argv: str, **kwargs: Any) -> _FakeProcess:
        spawned.append(list(argv))
        worker_fds.append(os.dup(kwargs["stdin"]))
        return _FakeProcess()

    def _fake_signal(sess: Any, _sig: int) -> None:
        sess.proc.returncode = 0
        sess.proc._done.set()

    monkeypatch.setattr("personalclaw.sandbox.create_subprocess_limited", _fake_spawn)
    monkeypatch.setattr(terminal, "_signal_session", _fake_signal)

    def _configure(*, persist: bool) -> list[list[str]]:
        cfg_file.write_text(
            json.dumps(
                {
                    "dashboard": {
                        "terminal": {"enabled": True, "persist": persist, "shell": "/bin/sh"}
                    }
                }
            )
        )
        return spawned

    try:
        yield _configure
    finally:
        for fd in worker_fds:
            os.close(fd)
        terminal._enabled_cache[0] = False
        terminal._enabled_cache[1] = 0.0


def _app(registry: dict) -> web.Application:
    state = MagicMock()
    state._terminal_sessions = registry

    @web.middleware
    async def owner(request, handler):
        request["user"] = "owner"
        return await handler(request)

    app = web.Application(middlewares=[owner])
    app["state"] = state
    app.router.add_get("/api/ws/terminal/{session_id}", terminal.api_terminal_ws)
    app.router.add_post("/api/terminal/sessions", terminal.api_terminal_create)
    app.router.add_delete("/api/terminal/sessions/{session_id}", terminal.api_terminal_delete)
    return app


async def _first_frame(client: TestClient, sid: str) -> dict:
    async with client.ws_connect(f"/api/ws/terminal/{sid}") as ws:
        await ws.send_str(json.dumps({"type": "ping"}))
        for _ in range(20):
            msg = await ws.receive(timeout=3)
            if msg.type == web.WSMsgType.TEXT:
                return json.loads(msg.data)
            if msg.type in (web.WSMsgType.CLOSE, web.WSMsgType.CLOSED, web.WSMsgType.CLOSING):
                break
    return {}


@pytest.mark.parametrize("persist", [False, True])
async def test_a_legacy_id_opened_after_a_restart_is_refused_with_no_shell(gateway, persist):
    """On `main` the reopen ran this computer's shell (or attached a tmux client to one)."""
    spawned = gateway(persist=persist)

    async def _its_session_survived() -> list[tuple[str, str]]:
        return [(tmux_substrate.terminal_session_name(LEGACY), "")]

    registry: dict = {}
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(tmux_substrate, "list_sessions", _its_session_survived)
        async with TestClient(TestServer(_app(registry))) as client:
            frame = await _first_frame(client, LEGACY)

    assert spawned == [], f"a shell was started for an id that names no tier: {spawned}"
    assert frame == {"type": "error", "message": SENTENCE}, frame
    assert registry == {}


async def test_every_id_the_create_route_makes_names_its_tier(gateway, monkeypatch):
    gateway(persist=False)

    class _Provider:
        def available(self) -> bool:
            return True

    monkeypatch.setattr(
        "personalclaw.sandbox_providers.get_provider",
        lambda name: _Provider() if name == "fake-tier" else None,
    )
    async with TestClient(TestServer(_app({}))) as client:
        ids = []
        for body in ({}, {"sandbox": "none"}, {"sandbox": "fake-tier"}):
            resp = await client.post("/api/terminal/sessions", json=body)
            ids.append((await resp.json())["session_id"])

    host, host_named, tier = ids
    assert re.fullmatch(r"[0-9a-f]{12}@none", host), host
    assert re.fullmatch(r"[0-9a-f]{12}@none", host_named), host_named
    assert re.fullmatch(r"[0-9a-f]{12}@fake-tier", tier), tier


async def test_a_host_id_reopens_after_a_restart_on_the_host_shell(gateway):
    spawned = gateway(persist=False)
    registry: dict = {}
    async with TestClient(TestServer(_app(registry))) as client:
        assert await _first_frame(client, f"{LEGACY}@none") == {"type": "pong"}
        for sess in list(registry.values()):
            await terminal._kill_session(sess)

    assert spawned == [["/bin/sh", "-l"]]


async def test_a_legacy_tab_can_still_be_closed(gateway, monkeypatch):
    """Closing a tab whose session survived in tmux ends that session; the refusal is for
    opening it, not for cleaning it up."""
    gateway(persist=True)
    killed: list[str] = []

    async def _no_workers() -> list[tuple[str, str]]:
        return [(tmux_substrate.terminal_session_name(LEGACY), "")]

    async def _kill(name: str) -> None:
        killed.append(name)

    monkeypatch.setattr(tmux_substrate, "list_sessions", _no_workers)
    monkeypatch.setattr(tmux_substrate, "kill_session", _kill)
    async with TestClient(TestServer(_app({}))) as client:
        resp = await client.delete(f"/api/terminal/sessions/{LEGACY}")

    assert resp.status == 200, await resp.text()
    assert killed == [tmux_substrate.terminal_session_name(LEGACY)]

"""A run's durable worker is not a terminal: the terminal list, socket and close leave it alone.

Persistent terminals and durable workers share the home's tmux server and the ``pclaw-`` name
prefix, and the terminal list reported every ``pclaw-`` session there as a detached terminal. So
the Terminal page's restore opened a tab on each worker, which attached to its session: the tab's
keystrokes went into the worker's step, and closing the tab killed it.

Now a worker is marked when it is created, the list leaves marked sessions out, and the socket and
the close refuse a worker's session. Against a real tmux server.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw import tmux_substrate
from personalclaw.dashboard.handlers import terminal

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(shutil.which("tmux") is None, reason="needs the tmux binary"),
]

TERMINAL_ID = "0123456789ab"


class _FakeProcess:
    def __init__(self) -> None:
        self.pid = 4245
        self.returncode: int | None = None
        self._done = asyncio.Event()

    async def wait(self) -> int | None:
        await self._done.wait()
        return self.returncode


@pytest.fixture
def server(monkeypatch, tmp_path):
    """A short home with persistent terminals on, holding one detached terminal session and one
    durable worker. Yields ``(worker_id, spawned argv list)``."""
    root = Path(tempfile.mkdtemp(prefix="pct-", dir="/tmp")).resolve()
    (root / "user").mkdir()
    monkeypatch.setenv("HOME", str(root / "user"))  # the server reads no one's own tmux config
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: root)
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(
        json.dumps(
            {"dashboard": {"terminal": {"enabled": True, "persist": True, "shell": "/bin/sh"}}}
        )
    )
    monkeypatch.setattr(terminal, "config_path", lambda: cfg_file)
    monkeypatch.setattr(terminal, "_sel", lambda: MagicMock())
    terminal._enabled_cache[0] = True
    terminal._enabled_cache[1] = time.monotonic()

    spawned: list[list[str]] = []
    worker_fds: list[int] = []

    async def _fake_spawn(*argv: str, **kwargs: Any) -> _FakeProcess:
        spawned.append(list(argv))
        worker_fds.append(os.dup(kwargs["stdin"]))
        return _FakeProcess()

    monkeypatch.setattr("personalclaw.sandbox.create_subprocess_limited", _fake_spawn)

    ws = root / "ws"
    ws.mkdir()
    worker = tmux_substrate.durable_session_name("proj", "run", "wf")
    assert asyncio.run(
        tmux_substrate.new_session(worker, workspace=str(ws), command=["sleep", "60"])
    )
    detached = tmux_substrate.terminal_session_name(TERMINAL_ID)
    made = subprocess.run(
        tmux_substrate._argv("new-session", "-d", "-s", detached, "sleep", "60"), timeout=10
    )
    assert made.returncode == 0
    try:
        yield worker, spawned
    finally:
        for fd in worker_fds:
            os.close(fd)
        terminal._enabled_cache[0] = False
        terminal._enabled_cache[1] = 0.0
        tmux_substrate.kill_server(root)
        shutil.rmtree(root, ignore_errors=True)


def _app() -> web.Application:
    state = MagicMock()
    state._terminal_sessions = {}

    @web.middleware
    async def owner(request, handler):
        request["user"] = "owner"
        return await handler(request)

    app = web.Application(middlewares=[owner])
    app["state"] = state
    app.router.add_get("/api/ws/terminal/{session_id}", terminal.api_terminal_ws)
    app.router.add_get("/api/terminal/sessions", terminal.api_terminal_list)
    app.router.add_delete("/api/terminal/sessions/{session_id}", terminal.api_terminal_delete)
    return app


def _worker_id(worker: str) -> str:
    return worker[len("pclaw-") :]


async def test_the_terminal_list_shows_the_terminal_and_not_the_worker(server):
    async with TestClient(TestServer(_app())) as client:
        listed = await (await client.get("/api/terminal/sessions")).json()

    assert [s["session_id"] for s in listed["sessions"]] == [TERMINAL_ID], listed


async def test_the_terminal_socket_does_not_attach_to_a_worker(server):
    worker, spawned = server
    async with TestClient(TestServer(_app())) as client:
        async with client.ws_connect(f"/api/ws/terminal/{_worker_id(worker)}") as ws:
            msg = await ws.receive(timeout=5)

    assert msg.type == web.WSMsgType.TEXT, msg
    assert json.loads(msg.data) == {
        "type": "error",
        "message": "This session is a workflow run's worker, not a terminal, so it was not "
        "opened here.",
    }
    assert spawned == [], "a tmux client was started on the worker's session"


async def test_closing_a_worker_through_the_terminal_route_leaves_it_running(server):
    worker, _spawned = server
    async with TestClient(TestServer(_app())) as client:
        resp = await client.delete(f"/api/terminal/sessions/{_worker_id(worker)}")

    assert resp.status == 404
    assert await tmux_substrate.has_session(worker), "the terminal route killed a run's worker"

"""A terminal opened in a sandbox tier opens in that tier every time, or not at all.

The tier a session was created with sat in a map the WebSocket's FIRST connect popped. Every later
open of the same session id found nothing there: a reconnect after a gateway restart, or after the
orphan reaper ended the shell, opened a shell on this computer under a tab that still named the
tier. A tier that could not run at the first connect did the same thing on purpose, and a tier that
was not installed was dropped at create.

Now the tier is part of the session id (``<id>@<tier>``), read on every open. The shell opens in
the tier, or the socket is refused with a sentence that says why; the host shell is never the
fallback. The session list reports the tier, so a restored tab restarts in it.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any
from unittest.mock import MagicMock

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw import tmux_substrate
from personalclaw.dashboard.handlers import terminal
from personalclaw.sandbox_providers import SandboxUnavailableError

pytestmark = pytest.mark.asyncio

TIER = "fake-tier"


class _Handle:
    def __init__(self, argv: list[str]) -> None:
        self.argv = ["fake-tier-run", *argv]
        self.cleaned = 0

    def cleanup(self) -> None:
        self.cleaned += 1


class _Provider:
    name = TIER
    display_name = "Fake tier"

    def __init__(self, *, down: bool = False) -> None:
        self.down = down
        self.handles: list[_Handle] = []

    def available(self) -> bool:
        return not self.down

    def wrap(self, _spec: Any, argv: list[str]) -> _Handle:
        if self.down:
            raise SandboxUnavailableError(
                what="Fake sandbox requested but unavailable",
                why="its runtime is not running.",
                fix="start the runtime or choose a different sandbox tier.",
            )
        handle = _Handle(argv)
        self.handles.append(handle)
        return handle


class _FakeProcess:
    def __init__(self) -> None:
        self.pid = 4244
        self.returncode: int | None = None
        self._done = asyncio.Event()

    async def wait(self) -> int | None:
        await self._done.wait()
        return self.returncode


@pytest.fixture(autouse=True)
def _enabled() -> Any:
    terminal._enabled_cache[0] = True
    terminal._enabled_cache[1] = time.monotonic()
    yield
    terminal._enabled_cache[0] = False
    terminal._enabled_cache[1] = 0.0


@pytest.fixture
def gateway(tmp_path, monkeypatch):
    """A terminal gateway whose shells are recorded, never started, with persistence ON (the
    restart-surviving mode, where a host shell would also become a tmux session)."""
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(
        json.dumps(
            {"dashboard": {"terminal": {"enabled": True, "persist": True, "shell": "/bin/sh"}}}
        )
    )
    monkeypatch.setattr(terminal, "config_path", lambda: cfg_file)
    monkeypatch.setattr(terminal, "_sel", lambda: MagicMock())
    monkeypatch.setattr(tmux_substrate.shutil, "which", lambda _b: "/usr/bin/tmux")

    async def _no_tmux_sessions() -> list[tuple[str, str]]:
        return []

    monkeypatch.setattr(tmux_substrate, "list_sessions", _no_tmux_sessions)

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
    yield spawned
    for fd in worker_fds:
        os.close(fd)


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
    app.router.add_get("/api/terminal/sessions", terminal.api_terminal_list)
    app.router.add_delete("/api/terminal/sessions/{session_id}", terminal.api_terminal_delete)
    return app


def _providers(monkeypatch, **by_name: _Provider) -> None:
    monkeypatch.setattr("personalclaw.sandbox_providers.get_provider", by_name.get)


async def _open(client: TestClient, sid: str) -> dict:
    """Open *sid*'s socket and return its first control frame (a pong once a shell is open)."""
    async with client.ws_connect(f"/api/ws/terminal/{sid}") as ws:
        await ws.send_str(json.dumps({"type": "ping"}))
        for _ in range(20):
            msg = await ws.receive(timeout=3)
            if msg.type == web.WSMsgType.TEXT:
                return json.loads(msg.data)
            if msg.type in (web.WSMsgType.CLOSE, web.WSMsgType.CLOSED, web.WSMsgType.CLOSING):
                break
    return {}


async def _create(client: TestClient, sandbox: str) -> tuple[int, dict]:
    resp = await client.post("/api/terminal/sessions", json={"sandbox": sandbox})
    return resp.status, await resp.json()


async def test_a_tier_session_reopens_in_its_tier_after_a_gateway_restart(gateway, monkeypatch):
    provider = _Provider()
    _providers(monkeypatch, **{TIER: provider})

    before: dict = {}
    async with TestClient(TestServer(_app(before))) as client:
        status, body = await _create(client, TIER)
        assert status == 200, body
        sid = body["session_id"]
        assert sid.endswith(f"@{TIER}") and body["sandbox"] == TIER, body
        assert await _open(client, sid) == {"type": "pong"}

    # The gateway restarts: a new process, an empty registry, and the page reconnects the SAME id.
    restarted: dict = {}
    async with TestClient(TestServer(_app(restarted))) as client:
        assert await _open(client, sid) == {"type": "pong"}
        assert restarted[sid].persistent is False, "a tier shell is never a tmux client"
        for sess in (restarted[sid], before[sid]):
            await terminal._kill_session(sess)

    assert (
        gateway == [["fake-tier-run", "/bin/sh", "-l"]] * 2
    ), "a reopen of a tier session ran something other than the tier's own shell"
    assert [h.cleaned for h in provider.handles] == [1, 1], "the tier's handle was not released"


async def test_a_tier_that_is_gone_on_reopen_refuses_with_a_sentence(gateway, monkeypatch):
    _providers(monkeypatch)  # the tier's app is turned off since the tab was opened
    registry: dict = {}
    async with TestClient(TestServer(_app(registry))) as client:
        frame = await _open(client, f"0123456789ab@{TIER}")

    assert frame.get("type") == "error", frame
    assert frame["message"] == (
        f"This terminal is set to run in the {TIER} sandbox, which is not installed or is "
        "turned off. It was not opened, and it never falls back to a shell on this computer."
    )
    assert gateway == [], "a shell was started for a session whose tier is gone"
    assert registry == {}


async def test_a_tier_that_cannot_run_refuses_with_its_reason(gateway, monkeypatch):
    _providers(monkeypatch, **{TIER: _Provider(down=True)})
    async with TestClient(TestServer(_app({}))) as client:
        frame = await _open(client, f"0123456789ab@{TIER}")

    assert frame.get("type") == "error", frame
    assert frame["message"] == (
        "This terminal is set to run in the Fake tier sandbox, which is not available: its "
        "runtime is not running. It was not opened, and it never falls back to a shell on this "
        "computer. Start the runtime or choose a different sandbox tier."
    )
    assert gateway == []


async def test_creating_a_terminal_in_a_tier_that_is_not_installed_is_refused(gateway, monkeypatch):
    _providers(monkeypatch)
    async with TestClient(TestServer(_app({}))) as client:
        status, body = await _create(client, "lima")

    assert status == 409, body
    assert body["error"]["code"] == "sandbox_tier_unavailable"
    assert body["error"]["message"] == (
        "The lima sandbox is not installed or is turned off, so no terminal was opened. Pick "
        "another sandbox, or open the terminal without one."
    )


async def test_the_session_list_says_which_tier_a_session_is_in(gateway, monkeypatch):
    _providers(monkeypatch, **{TIER: _Provider()})
    registry: dict = {}
    async with TestClient(TestServer(_app(registry))) as client:
        _status, tier_body = await _create(client, TIER)
        _status, host_body = await _create(client, "none")
        for sid in (tier_body["session_id"], host_body["session_id"]):
            assert await _open(client, sid) == {"type": "pong"}
        listed = await (await client.get("/api/terminal/sessions")).json()
        for sess in list(registry.values()):
            await terminal._kill_session(sess)

    by_id = {s["session_id"]: s["sandbox"] for s in listed["sessions"]}
    assert by_id == {tier_body["session_id"]: TIER, host_body["session_id"]: ""}, by_id

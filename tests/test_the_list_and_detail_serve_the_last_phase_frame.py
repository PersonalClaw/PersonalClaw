"""The chat list and a chat's detail serve what its last phase frame said, and nothing else of it.

A chat's state used to be served as a handful of flags each route derived its own way — the list's
``running``, ``stopping``, ``stop_state``, ``pending_approval``, ``pending_approval_info`` and
``waiting_for_input``, the detail's ``running``, ``stopping``, ``steerable``, ``last_turn_outcome``
and ``pending_approval``, a project's chats' ``running`` — and every page reconstructed "running"
from them again. Now every route that lists a chat serves ``run_state``: the last ``work_phase``
payload the chat was sent, so a page opened, reloaded or reconnected reads exactly what a page that
heard every frame knows. Once nothing runs it is served idle, with how the last turn ended; a chat
that never ran, or one kept only on disk, is served the idle payload. The flags are gone.
"""

from __future__ import annotations

import asyncio
import dataclasses
import re
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer
from chat_test_helpers import _make_app
from test_work_phase_classifier import CHAT, _answer, _call, _result, _World

from personalclaw.approval_answer import YOU
from personalclaw.dashboard import chat_phase
from personalclaw.llm.events import EVENT_PERMISSION_REQUEST, AgentEvent

ROW_FLAGS = {
    "running",
    "stopping",
    "stop_state",
    "pending_approval",
    "pending_approval_info",
    "waiting_for_input",
}
DETAIL_FLAGS = {"running", "stopping", "steerable", "last_turn_outcome", "pending_approval"}


def idle(key: str) -> dict:
    """What a chat with nothing running and no turn on record is served."""
    return {
        "work": f"chat:{key}",
        "session": key,
        "kind": "chat",
        "phase": "idle",
        "line": "",
        "since": None,
        "turn": None,
        "last_step_at": None,
        "stopping": False,
        "steerable": False,
        "last_outcome": None,
        "queued_messages": 0,
        "seq": 0,
        "retry": None,
        "queue": None,
    }


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)


async def _served(http: TestClient, key: str = CHAT) -> tuple[dict, dict]:
    rows = await (await http.get("/api/chat/sessions")).json()
    detail = await (await http.get(f"/api/chat/sessions/{key}")).json()
    (row,) = [r for r in rows if r["key"] == key]
    assert not ROW_FLAGS & set(row), sorted(ROW_FLAGS & set(row))
    assert not DETAIL_FLAGS & set(detail), sorted(DETAIL_FLAGS & set(detail))
    return row["run_state"], detail["run_state"]


def _held(gate: asyncio.Event, *before: AgentEvent):
    async def stream() -> AsyncIterator[AgentEvent]:
        for event in before:
            yield event
        await gate.wait()
        for event in _answer():
            yield event

    return stream


@pytest.mark.asyncio
async def test_a_running_chat_serves_its_last_frame(tmp_path):
    gate = asyncio.Event()
    w = _World(tmp_path, _held(gate, _call("shell", "t1", tool_input={"command": "ls"})))
    task = w.start()
    await w.until("tool")
    async with TestClient(TestServer(_make_app(w.state))) as http:
        assert await _served(http) == (w.phases()[-1], w.phases()[-1])
    gate.set()
    await asyncio.wait_for(task, timeout=10)


@pytest.mark.asyncio
async def test_a_stopping_chat_serves_its_last_frame(tmp_path):
    gate, release = asyncio.Event(), asyncio.Event()
    w = _World(tmp_path, _held(gate, _call("shell", "t1", tool_input={"command": "ls"})))

    async def stop_turn(*_a, **_kw) -> str:
        await release.wait()
        gate.set()
        return "soft"

    w.state.sessions.stop_turn = stop_turn
    task = w.start()
    await w.until("tool")
    from unittest.mock import MagicMock, patch

    with patch("personalclaw.dashboard.chat_handlers.sel", MagicMock()):
        async with TestClient(TestServer(_make_app(w.state))) as http:
            pressed = asyncio.ensure_future(http.post(f"/api/chat/sessions/{CHAT}/stop"))
            for _ in range(500):
                if w.phases()[-1]["stopping"]:
                    break
                await asyncio.sleep(0.01)
            assert w.phases()[-1]["stopping"] is True
            assert await _served(http) == (w.phases()[-1], w.phases()[-1])
            release.set()
            assert (await pressed).status == 200
            await asyncio.wait_for(task, timeout=10)


@pytest.mark.asyncio
async def test_a_chat_waiting_on_her_ok_serves_its_last_frame(tmp_path):
    w = _World(
        tmp_path,
        lambda: _asks(),
    )
    w.session._trust = False
    task = w.start()
    await w.until("approval")
    async with TestClient(TestServer(_make_app(w.state))) as http:
        served = await _served(http)
    assert served == (w.phases()[-1], w.phases()[-1])
    assert served[0]["phase"] == "approval"
    w.state.decide_session_approval(w.session, "r1", "approved", by=YOU)
    await asyncio.wait_for(task, timeout=10)


async def _asks() -> AsyncIterator[AgentEvent]:
    yield _call("shell", "t1", tool_input={"command": "ls"})
    yield AgentEvent(
        kind=EVENT_PERMISSION_REQUEST,
        title="shell",
        request_id="r1",
        tool_call_id="t1",
        tool_input='{"command": "ls"}',
    )
    yield _result("t1")
    for event in _answer():
        yield event


@pytest.mark.asyncio
async def test_an_idle_chat_serves_its_last_frame_as_idle(tmp_path):
    w = _World(tmp_path, lambda: _streamed(_answer()))
    await w.turn()
    last = w.phases()[-1]
    assert last["phase"] == "done" and last["turn"] is None
    async with TestClient(TestServer(_make_app(w.state))) as http:
        served = await _served(http)
    assert served == ({**last, "phase": "idle", "line": "", "turn": None},) * 2
    assert served[0]["last_outcome"] == "complete"


@pytest.mark.asyncio
async def test_a_chat_that_never_ran_serves_the_idle_payload(tmp_path):
    w = _World(tmp_path, lambda: _streamed(_answer()))
    async with TestClient(TestServer(_make_app(w.state))) as http:
        assert await _served(http) == (idle(CHAT), idle(CHAT))


@pytest.mark.asyncio
async def test_a_chat_kept_only_on_disk_serves_the_idle_payload(tmp_path):
    from personalclaw.dashboard.chat_persistence import save_session_to_history

    w = _World(tmp_path, lambda: _streamed(_answer()))
    kept = w.state.get_or_create_session("kept-1")
    kept.append("user", "an old question", "msg msg-u")
    kept.drain()
    save_session_to_history(w.state, kept, force=True)
    w.state._sessions.pop("kept-1")
    async with TestClient(TestServer(_make_app(w.state))) as http:
        assert await _served(http, "kept-1") == (idle("kept-1"), idle("kept-1"))


async def _streamed(events: list[AgentEvent]) -> AsyncIterator[AgentEvent]:
    for event in events:
        yield event


@pytest.mark.asyncio
async def test_a_projects_chats_serve_their_run_state_too(tmp_path, monkeypatch):
    """A project's page lists its chats (``GET /api/projects/{id}/linked``) with the same run state
    the chat list serves, and no flag of its own."""
    from aiohttp import web

    from personalclaw.tasks import registry
    from personalclaw.tasks.handlers import register_task_routes

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    monkeypatch.setattr("personalclaw.tasks.hierarchy.config_dir", lambda: home)
    gate = asyncio.Event()
    w = _World(tmp_path, _held(gate, _call("shell", "t1", tool_input={"command": "ls"})))
    registry._providers.clear()
    app = web.Application()
    register_task_routes(app)
    app["state"] = w.state
    try:
        async with TestClient(TestServer(app)) as http:
            made = await http.post("/api/projects", json={"name": "Launch week"})
            pid = (await made.json())["id"]
            w.session.project_id = pid
            task = w.start()
            await w.until("tool")
            running = w.phases()[-1]
            linked = await (await http.get(f"/api/projects/{pid}/linked")).json()
            gate.set()
            await asyncio.wait_for(task, timeout=10)
            ended = await (await http.get(f"/api/projects/{pid}/linked")).json()
    finally:
        registry._providers.clear()

    (chat,) = linked["chats"]
    assert "running" not in chat
    assert chat["run_state"] == running
    (after,) = ended["chats"]
    assert after["run_state"] == {**w.phases()[-1], "phase": "idle", "line": "", "turn": None}


_WEB = Path(__file__).resolve().parent.parent / "web" / "src" / "lib"


@pytest.mark.skipif(not _WEB.exists(), reason="web sources not present")
def test_the_web_reads_and_seeds_exactly_the_fields_the_payload_carries():
    """The web types the payload as ``RunState`` (``api.ts``) and seeds a chat it has just made with
    ``idleRunState`` (``workPhase.ts``): both name every field the gateway serves, and no other."""
    fields = [f.name for f in dataclasses.fields(chat_phase.RunState)]
    assert len(fields) > 10, f"parsed {fields} — vacuous"
    assert list(chat_phase.idle_payload("x")) == fields

    api = (_WEB / "api.ts").read_text(encoding="utf-8")
    typed = api[api.index("export interface RunState {") :]
    typed = typed[: typed.index("\n}\n")]
    assert re.findall(r"^\s+(\w+)\??:", typed, re.M) == fields

    phases = (_WEB / "workPhase.ts").read_text(encoding="utf-8")
    seed = phases[phases.index("export function idleRunState(") :]
    seed = re.sub(r"`[^`]*`", "``", seed[seed.index("return {") + 8 : seed.index("\n  }\n")])
    assert re.findall(r"(?:^|[,{])\s*(\w+)(?=\s*[:,}]|\s*$)", seed) == fields

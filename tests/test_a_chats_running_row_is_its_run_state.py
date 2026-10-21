"""A chat's row in the running list is its run state, never a second reading of the chat (#3889).

The chat list and a chat's detail serve ``run_state``, the chat's last ``work_phase`` payload. The
running list's row for a chat turn is that same payload with the row's own fields beside it (its
name, its agent, its route, what waits on the owner for it), so the list and the chat can never
disagree about one chat: running, stopping and waiting on her OK alike.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest
from aiohttp.test_utils import TestClient, TestServer
from test_the_list_and_detail_serve_the_last_phase_frame import _asks, _held
from test_work_phase_classifier import CHAT, _call, _World

from personalclaw.approval_answer import YOU


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)


async def _row_and_run_state(state) -> tuple[dict, dict]:
    from chat_test_helpers import _make_app

    from personalclaw.dashboard.handlers.running import register_running_routes

    app = _make_app(state)
    register_running_routes(app)
    async with TestClient(TestServer(app)) as http:
        listed = await (await http.get("/api/running")).json()
        sessions = await (await http.get("/api/chat/sessions")).json()
    (row,) = [r for r in listed["running"] if r["work"] == f"chat:{CHAT}"]
    (session,) = [s for s in sessions if s["key"] == CHAT]
    return row, session["run_state"]


def _is_its_run_state(row: dict, run_state: dict) -> None:
    assert {k: row[k] for k in run_state} == run_state, "the row is the chat's run state"


@pytest.mark.asyncio
async def test_a_running_chats_row_is_its_run_state(tmp_path):
    gate = asyncio.Event()
    w = _World(tmp_path, _held(gate, _call("shell", "t1", tool_input={"command": "ls"})))
    task = w.start()
    await w.until("tool")
    row, run_state = await _row_and_run_state(w.state)
    _is_its_run_state(row, run_state)
    assert run_state["turn"] is not None and row["phase"] == "tool"
    gate.set()
    await asyncio.wait_for(task, timeout=10)


@pytest.mark.asyncio
async def test_a_stopping_chats_row_is_its_run_state(tmp_path):
    gate, release = asyncio.Event(), asyncio.Event()
    w = _World(tmp_path, _held(gate, _call("shell", "t1", tool_input={"command": "ls"})))

    async def stop_turn(*_a, **_kw) -> str:
        await release.wait()
        gate.set()
        return "soft"

    w.state.sessions.stop_turn = stop_turn
    task = w.start()
    await w.until("tool")
    from chat_test_helpers import _make_app

    with patch("personalclaw.dashboard.chat_handlers.sel", MagicMock()):
        async with TestClient(TestServer(_make_app(w.state))) as http:
            pressed = asyncio.ensure_future(http.post(f"/api/chat/sessions/{CHAT}/stop"))
            for _ in range(500):
                if w.phases()[-1]["stopping"]:
                    break
                await asyncio.sleep(0.01)
            row, run_state = await _row_and_run_state(w.state)
            _is_its_run_state(row, run_state)
            assert row["stopping"] is True
            release.set()
            assert (await pressed).status == 200
    await asyncio.wait_for(task, timeout=10)


@pytest.mark.asyncio
async def test_a_chat_waiting_on_her_ok_is_its_run_state_and_names_the_ask(tmp_path):
    w = _World(tmp_path, lambda: _asks())
    w.session._trust = False
    task = w.start()
    await w.until("approval")
    row, run_state = await _row_and_run_state(w.state)
    _is_its_run_state(row, run_state)
    assert row["phase"] == "approval"
    assert row["waiting"] == list(w.state._pending_approvals), "the ask it waits on is named"
    w.state.decide_session_approval(w.session, "r1", "approved", by=YOU)
    await asyncio.wait_for(task, timeout=10)

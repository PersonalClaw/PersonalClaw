"""A message that joins a chat's queue is counted on the chat's run state at once, by every way in.

A chat's run state says how many messages wait behind its running turn (``queued_messages``), and
the count is read when a frame is sent. A message typed in the chat or sent on its channel while a
turn ran said the new count as it joined the queue; four other ways in said nothing, so the frame
and every route that serves the run state kept the old count until the turn's next step, or its
re-send fifteen seconds on: a message that replaces the running turn, a prompt run on a busy chat
(a heartbeat's result, her reply from the Inbox), an automation's message to the chat it came from,
and a helper's report that waits for the chat's turn to end.

Each goes through its real door, on the real dashboard state and run state; the turn they queue
behind is held running.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from chat_test_helpers import _make_app, _make_state
from test_the_list_and_detail_serve_the_last_phase_frame import _served

from personalclaw.dashboard import chat_phase

CHAT = "chat-queue-1"


class _Busy:
    """One chat whose turn is running, on a state whose every frame is recorded."""

    def __init__(self, tmp_path) -> None:
        self.frames: list[tuple[str, dict]] = []
        self.state = _make_state(tmp_path)
        self.state.broadcast_ws = lambda kind, data=None: self.frames.append((kind, data or {}))
        self.state.push_sessions_update = MagicMock()
        self.session = self.state.get_or_create_session(CHAT)
        # Its turn was accepted and has not ended.
        self.session.task = asyncio.get_running_loop().create_future()
        chat_phase.begin(self.state, self.session, "2026-10-18T09:00:00+00:00")

    async def counts(self, waiting: int) -> None:
        """The chat's run state says *waiting* messages wait behind its turn: in the last frame it
        was sent, and as the chat list and its detail serve it."""
        assert self.session.queue_depth == waiting
        last = [data for kind, data in self.frames if kind == "work_phase"][-1]
        assert last["turn"] is not None, "the turn is still running"
        assert last["queued_messages"] == waiting, last
        async with TestClient(TestServer(_make_app(self.state))) as http:
            assert await _served(http, CHAT) == (last, last)

    def close(self) -> None:
        chat_phase.end(self.state, self.session)
        self.session.task.cancel()


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)


@pytest_asyncio.fixture
async def chat(tmp_path) -> AsyncIterator[_Busy]:
    busy = _Busy(tmp_path)
    await busy.counts(0)
    yield busy
    busy.close()


@pytest.mark.asyncio
async def test_a_message_that_replaces_the_running_turn_is_counted(chat):
    from personalclaw.config.loader import AppConfig
    from personalclaw.dashboard import chat_handlers

    policy = SimpleNamespace(
        resilience=SimpleNamespace(
            mid_turn_policy="cancel_and_replace", cancel_replace_min_interval_secs=0.0
        )
    )
    chat.state.sessions.stop_turn = AsyncMock(return_value="soft")
    with (
        patch.object(AppConfig, "load", staticmethod(lambda *a, **k: policy)),
        patch.object(chat_handlers, "sel", MagicMock()),
    ):
        async with TestClient(TestServer(_make_app(chat.state))) as http:
            resp = await http.post(
                "/api/chat?ws=1", json={"message": "do this instead", "session": CHAT}
            )
            assert resp.status == 200, await resp.text()
            replaced = await resp.json()
    assert replaced["disposition"] == "replaced", replaced
    await chat.counts(1)
    # Said while the turn it replaces still runs, before the page hears that turn is over.
    order = [
        kind
        for kind, data in chat.frames
        if kind == "chat_done" or (kind == "work_phase" and data["queued_messages"])
    ]
    assert order == ["work_phase", "chat_done"], order


@pytest.mark.asyncio
async def test_a_prompt_run_on_a_busy_chat_is_counted(chat):
    """A heartbeat's result and her reply from the Inbox both run a prompt on the chat."""
    from personalclaw.dashboard.chat_runner import run_chat

    ran = chat.session.enqueue_or_run_prompt(
        "The nightly check found two failures.", run_chat, chat.state
    )
    assert ran is False
    await chat.counts(1)


@pytest.mark.asyncio
async def test_an_automations_message_to_the_chat_it_came_from_is_counted(
    chat, tmp_path, monkeypatch
):
    from test_dashboard_file_io import _seed_cron_trigger

    from personalclaw.dashboard.handlers.messaging import api_send_message

    _seed_cron_trigger(tmp_path, monkeypatch, session_key=f"dashboard:{CHAT}")
    app = web.Application()
    app.router.add_post("/api/send-message", api_send_message)
    app["state"] = chat.state
    with patch("personalclaw.dashboard.handlers.sel", MagicMock()):
        async with TestClient(TestServer(app)) as http:
            sent = await http.post(
                "/api/send-message",
                json={
                    "text": "build failed",
                    "session": "origin",
                    "caller_session": "cron:abc12345",
                },
            )
            assert (await sent.json())["session"] is True
    await chat.counts(1)


@pytest.mark.asyncio
async def test_a_helpers_report_that_waits_for_the_turn_is_counted(chat, monkeypatch):
    from test_gateway import _make_orchestrator, _mock_sessions

    from personalclaw.subagent import SubagentInfo

    orch = _make_orchestrator()
    orch.sessions = _mock_sessions()
    orch.ctx_builder = MagicMock()
    orch.dashboard_state = chat.state
    with (
        patch("personalclaw.trust_mode.is_yolo_active", return_value=False),
        patch("personalclaw.gateway.SubagentManager") as manager_type,
    ):
        manager_type.return_value = MagicMock(
            running=[],
            running_agents_for=MagicMock(return_value=[]),
            get=MagicMock(return_value=None),
        )
        orch._init_subagents()
    # The report waits for the chat's turn to end, and stops waiting while that turn still runs.
    monkeypatch.setattr("personalclaw.gateway.INJECTION_TIMEOUT", 0.01)
    report = SubagentInfo(
        id="h1",
        task="Find the release notes",
        parent_session_key=f"dashboard:{CHAT}",
        done=True,
        result="The notes are in the changelog.",
    )
    assert orch._announce_subagents is not None
    await orch._announce_subagents([report])
    await chat.counts(1)

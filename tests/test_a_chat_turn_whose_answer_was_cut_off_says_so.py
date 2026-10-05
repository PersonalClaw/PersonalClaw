"""A chat turn whose model's answer was cut off ends as cut off, with what arrived kept and Retry.

Measured before: an OpenAI-compatible endpoint closed its stream after a sentence, with no
``finish_reason``. The adapter reported a complete answer, the chat runner counted the turn
answered and logged nothing, and the page said "Response complete." under half an answer.

Now the turn ends in its error, in words that say what happened, and the half that arrived stays
where it streamed, marked as the part it is. The error row records whose stream it was and what
never came, and the gateway log says the same. Retry sends the message again as it does for any
turn that ended in an error, asking first when the turn had finished a step that may have changed
something.

The model is the real OpenAI-compatible adapter on the real ``openai`` SDK, against a loopback
server that closes the body where the test says. The turn engine, the dashboard state, the
session manager and the Retry door are the real ones.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.agents.native.runtime import NativeAgentRuntime
from personalclaw.agents.provider import AgentRuntimeDefinition
from personalclaw.config import AppConfig
from personalclaw.context import ContextBuilder
from personalclaw.dashboard.chat_handlers import api_chat_session_approve
from personalclaw.dashboard.chat_regenerate import api_chat_session_regenerate
from personalclaw.dashboard.chat_runner import run_chat
from personalclaw.dashboard.chat_utils import persisted_history_key
from personalclaw.dashboard.state import DashboardState
from personalclaw.history import ConversationLog
from personalclaw.llm.credentials import Credential
from personalclaw.memory import MemoryStore
from personalclaw.session import SessionManager
from personalclaw.skills import SkillsLoader
from personalclaw.tool_providers.base import RiskLevel, ToolDefinition, ToolProvider, ToolResult
from tests.chat_test_helpers import _api_app
from tests.loopback_model_endpoint import (
    MODEL,
    ModelEndpoint,
    chunk,
    text,
    tool_delta,
    usage,
    whole_answer,
)

pytest.importorskip("openai", reason="the `openai` extra is not installed: the SDK drive is unrun")

QUESTION = "Summarize the Q3 numbers."
FIRST_HALF = "Revenue rose 12% in Q3, and"
CUT_OFF = (
    "The model's answer was cut off: its stream ended before the model said it was finished. "
    "Try again; if it keeps happening, check the model's provider and the gateway log."
)
#: The record the error row keeps of the cut.
RECORD = {"adapter": "OpenAI-compatible", "missing": "a finish_reason", "model": MODEL}


class _Notes(ToolProvider):
    """One tool that saves a note: it declares nothing about only reading, so a Retry after it
    ran asks first."""

    def __init__(self) -> None:
        self.ran: list[dict] = []

    @property
    def name(self) -> str:
        return "notes"

    @property
    def display_name(self) -> str:
        return "Notes"

    async def list_tools(self):
        return [
            ToolDefinition(
                name="save_note",
                description="Save a note.",
                parameters={"type": "object", "properties": {"path": {"type": "string"}}},
                risk_level=RiskLevel.CAUTION,
            )
        ]

    async def invoke(self, tool_name, arguments):
        self.ran.append(arguments)
        return ToolResult(success=True, output="saved")


async def _gateway(tmp_path: Path, endpoint: ModelEndpoint, notes: _Notes):
    from personalclaw.llm.openai import OpenAIProvider

    model = OpenAIProvider(
        model=MODEL,
        credential=Credential(name="key", kind="api_key", secret="fake-key-test", source="env"),
        base_url=f"{endpoint.url}/v1",
    )
    runtime = NativeAgentRuntime(
        definition=AgentRuntimeDefinition(name="PersonalClaw", provider="native", model=MODEL),
        model_provider=model,
        tool_providers=[notes],
        cwd=tmp_path,
    )
    runtime.set_approval_policy("auto")
    sessions = SessionManager(AppConfig(), provider_factory=lambda *_a, **_kw: runtime)
    log = ConversationLog(base_dir=tmp_path / "sessions")
    state = DashboardState(sessions=sessions, start_time=0.0, conversation_log=log)
    (tmp_path / "ws").mkdir()
    state.context_builder = ContextBuilder(
        memory=MemoryStore(workspace=tmp_path / "ws"),
        skills=SkillsLoader(skills_path=tmp_path / "skills", install_builtins=False),
        conversation_log=log,
    )
    # A hook store with no hooks: nothing blocks a step (a missing store refuses every one).
    state._hook_store = MagicMock(fire_for_ids=AsyncMock(return_value=[]))
    state.broadcast_ws = MagicMock()
    # The global stream a turn's settled rows reach the page on.
    state._broadcast = MagicMock()
    state.push_sessions_update = MagicMock()
    return state, sessions


async def _settled(chat) -> None:
    for _ in range(500):
        if not chat.running and not chat._queue:
            await asyncio.sleep(0.05)
            return
        await asyncio.sleep(0.01)
    raise AssertionError("the chat never finished its turns")


async def _ask(state, chat, words: str = QUESTION) -> None:
    chat.append("user", words, "msg msg-u")
    chat.task = asyncio.ensure_future(run_chat(state, chat, words))
    await _settled(chat)


def _turn_rows(rows: list[dict]) -> list[dict]:
    """The rows after the last user row: what the turn itself wrote."""
    start = max(i for i, m in enumerate(rows) if m.get("role") == "user")
    return [m for m in rows[start + 1 :] if m.get("role") in ("assistant", "error")]


def _frames(state, kind: str) -> list[dict]:
    return [c.args[1] for c in state.broadcast_ws.call_args_list if c.args and c.args[0] == kind]


def _doors(state):
    """The chat's Approve and Retry doors, as the page presses them."""
    app = _api_app(state)
    app.router.add_post("/api/chat/sessions/{session}/approve", api_chat_session_approve)
    app.router.add_post("/api/chat/sessions/{session}/regenerate", api_chat_session_regenerate)
    return app


async def _approve_when_asked(chat, client) -> None:
    for _ in range(500):
        if any(m.get("role") == "permission" for m in chat.messages):
            resp = await client.post(
                f"/api/chat/sessions/{chat.key}/approve", json={"action": "approved"}
            )
            assert resp.status == 200, await resp.text()
            return
        await asyncio.sleep(0.01)
    raise AssertionError("the step never asked to be approved")


@pytest.mark.asyncio
async def test_a_cut_off_answer_ends_the_turn_in_its_error_with_what_arrived_kept(tmp_path, caplog):
    async with ModelEndpoint([text(FIRST_HALF)]) as endpoint:
        state, sessions = await _gateway(tmp_path, endpoint, _Notes())
        try:
            with (
                patch("personalclaw.dashboard.chat_runner.sel", MagicMock()),
                caplog.at_level(logging.WARNING),
            ):
                chat = state.get_or_create_session()
                await _ask(state, chat)
        finally:
            await sessions.close_all()

    assert len(endpoint.requests) == 1, "the half answer was sent for again behind her back"
    partial, error = _turn_rows(chat.messages)
    assert (partial["role"], partial["content"]) == ("assistant", FIRST_HALF)
    assert partial["meta"]["finish_reason"] == "incomplete", "the half answer is not marked"
    assert (error["role"], error["content"]) == ("error", CUT_OFF)
    assert error["meta"] == {"cut_off": RECORD}
    # The failed-turn ending: an error, never "Response complete.", which offers Retry.
    assert chat._last_turn_outcome == "error"
    assert {"session": chat.key, "outcome": "error"} in _frames(state, "chat_done")
    (shown,) = [
        c.args[0]
        for c in state._broadcast.call_args_list
        if c.args[0].get("_type") == "chat_message" and c.args[0].get("role") == "error"
    ]
    assert (shown["content"], shown["meta"]) == (CUT_OFF, {"cut_off": RECORD})
    # Saved as shown (the gateway's flush of changed chats): a reload reads the same half answer,
    # its mark and the record.
    state._flush_dirty_sessions()
    saved = state.conversation_log.read_messages(
        persisted_history_key(state.conversation_log, chat.key)
    )
    saved_partial, saved_error = _turn_rows(saved)
    assert saved_partial["meta"]["finish_reason"] == "incomplete"
    assert saved_error["meta"] == {"cut_off": RECORD}
    # The log says what happened: whose stream, what never came, and which chat it ended.
    said = [r.getMessage() for r in caplog.records]
    assert (
        "the OpenAI-compatible stream for m ended before a finish_reason arrived: "
        "the answer was cut off"
    ) in said
    assert f"Chat turn in session {chat.key} failed: {CUT_OFF}" in said


@pytest.mark.asyncio
async def test_retry_on_the_cut_off_notice_asks_the_model_again(tmp_path):
    whole = "Revenue rose 12% in Q3, and margins held at 31%."
    async with ModelEndpoint([text(FIRST_HALF)], whole_answer(whole)) as endpoint:
        state, sessions = await _gateway(tmp_path, endpoint, _Notes())
        try:
            with patch("personalclaw.dashboard.chat_runner.sel", MagicMock()):
                chat = state.get_or_create_session()
                await _ask(state, chat)
                # Retry is offered on the notice the turn ended on.
                assert chat._last_turn_outcome == "error"
                assert _turn_rows(chat.messages)[-1]["content"] == CUT_OFF
                async with TestClient(TestServer(_doors(state))) as client:
                    resp = await client.post(f"/api/chat/sessions/{chat.key}/regenerate")
                    assert resp.status == 200, await resp.text()
                await _settled(chat)
        finally:
            await sessions.close_all()

    assert [m["content"] for m in chat.messages if m["role"] == "user"] == [QUESTION]
    (answer,) = _turn_rows(chat.messages)
    assert (answer["role"], answer["content"]) == ("assistant", whole)
    assert "finish_reason" not in (answer.get("meta") or {})
    assert chat._last_turn_outcome == "complete"


@pytest.mark.asyncio
async def test_retry_after_a_cut_that_followed_a_finished_step_asks_first(tmp_path):
    # The turn saved a note, then its next answer was cut off: sent again on its own, the turn
    # could save the note a second time, so Retry asks first, as it does for any such turn.
    save = [
        tool_delta('{"path": "notes/q3.md"}', name="save_note"),
        chunk({}, "tool_calls"),
        usage(),
        "[DONE]",
    ]
    notes = _Notes()
    async with ModelEndpoint(save, [text(FIRST_HALF)]) as endpoint:
        state, sessions = await _gateway(tmp_path, endpoint, notes)
        try:
            with patch("personalclaw.dashboard.chat_runner.sel", MagicMock()):
                chat = state.get_or_create_session()
                async with TestClient(TestServer(_doors(state))) as client:
                    chat.append("user", QUESTION, "msg msg-u")
                    chat.task = asyncio.ensure_future(run_chat(state, chat, QUESTION))
                    await _approve_when_asked(chat, client)
                    await _settled(chat)
                    rows_before = [dict(m) for m in chat.messages]
                    resp = await client.post(f"/api/chat/sessions/{chat.key}/regenerate")
                    body = await resp.json()
        finally:
            await sessions.close_all()

    assert notes.ran == [{"path": "notes/q3.md"}]
    assert _turn_rows(rows_before)[-1]["content"] == CUT_OFF
    assert resp.status == 409, body
    assert body["error"]["code"] == "retry_repeats_steps"
    assert body["error"]["detail"]["steps"] == [{"tool": "save_note", "target": "notes/q3.md"}]
    assert len(endpoint.requests) == 2, "the turn ran again before anyone said yes"

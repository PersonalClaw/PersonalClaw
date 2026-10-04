"""Regenerate and Rewind ask first, as Retry does, when the turn they run again finished steps that
may have changed something; and a chat moved to another agent while it answered is not answered
again on its own after such a step.

Regenerate on a completed answer deleted the turn's rows, the calls it finished among them, and ran
the turn again from its message. Rewind to an earlier message, with its own words, ran that turn
again after the chat's runtime was reset. Neither asked. A runtime built for a turn (after a
restart, a rewind, or a move to another agent) is handed the chat's messages and never its calls,
so the turn asked again could not see what the attempt it replaced had done, and did it again: a
note written twice. A move did the same with no door asked at all: the new agent was handed her
message, whatever the old one had already done with it.

Every door that runs a turn again now asks through one function (``repeated_steps.ask_first``),
under Retry's rule: it answers with Retry's question (``409 retry_repeats_steps`` and its
``confirm``) before anything runs, and runs the turn again only on her yes to exactly those steps.
A move, which nobody is there to answer, does not run the turn again on its own after such a step:
it ends the turn saying so, with Retry, which asks. A turn whose finished calls only read, or that
finished none, runs again unasked, as before.

Driven through the real routes, the real turn engine and session manager, and a native runtime
whose scripted model calls a note tool that records each call it gets.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import pytest_asyncio
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from chat_test_helpers import _api_app

from personalclaw.agents.native.runtime import NativeAgentRuntime
from personalclaw.agents.provider import AgentRuntimeDefinition
from personalclaw.config import AppConfig
from personalclaw.context import ContextBuilder
from personalclaw.dashboard import running_turn, turn_endings
from personalclaw.dashboard.chat_regenerate import (
    api_chat_session_edit_resend,
    api_chat_session_regenerate,
)
from personalclaw.dashboard.chat_runner import run_chat
from personalclaw.dashboard.state import DashboardState
from personalclaw.history import ConversationLog
from personalclaw.llm.events import EVENT_COMPLETE, EVENT_TEXT_CHUNK, EVENT_TOOL_CALL, AgentEvent
from personalclaw.memory import MemoryStore
from personalclaw.sel import SecurityEventLog
from personalclaw.session import SessionManager
from personalclaw.skills import SkillsLoader
from personalclaw.tool_providers.base import RiskLevel, ToolDefinition, ToolProvider, ToolResult

QUESTION = "Save the plan to notes/plan.md."
FOLLOW_UP = "Thanks. What is next on the list?"
LOOK = "Read me notes/plan.md."
ANSWER = "Saved the plan."
NEXT = "Next is the launch checklist."
WRITE = ("write_note", {"path": "notes/plan.md", "text": "Ship on Friday."})
READ = ("read_note", {"path": "notes/plan.md"})


class _Notes(ToolProvider):
    """A tool that writes a note and one that reads it, recording every call each one gets.
    ``write_note`` declares nothing, which makes it a change; ``read_note`` declares a read."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    @property
    def name(self) -> str:
        return "notes"

    @property
    def display_name(self) -> str:
        return "Notes"

    async def list_tools(self) -> list[ToolDefinition]:
        path = {"path": {"type": "string"}}
        return [
            ToolDefinition(
                name="write_note",
                description="Write a note.",
                parameters={"type": "object", "properties": {**path, "text": {"type": "string"}}},
                requires_approval=False,
            ),
            ToolDefinition(
                name="read_note",
                description="Read a note.",
                parameters={"type": "object", "properties": path},
                risk_level=RiskLevel.SAFE,
            ),
        ]

    async def invoke(self, tool_name: str, arguments: dict[str, Any]) -> ToolResult:
        self.calls.append((tool_name, dict(arguments)))
        return ToolResult(success=True, output="done")

    def made(self, tool: str) -> list[dict[str, Any]]:
        return [args for name, args in self.calls if name == tool]


class _Model:
    """Answers each message with the call its script names for it, then its answer. With
    ``thinks``, after the call it thinks until it is stopped."""

    supports_tools = True
    _model = "scripted"

    def __init__(self, script: dict[str, tuple[str, dict[str, str]]], *, thinks: bool = False):
        self.script = script
        self.thinks = thinks
        self.requests: list[list[dict]] = []
        self.thinking = asyncio.Event()
        self.stopped = asyncio.Event()

    async def complete(self, messages, *, tools=None, model=None, reasoning_effort=""):
        self.requests.append(list(messages))
        last = messages[-1] if messages else {}
        if last.get("role") == "tool":
            if self.thinks:
                self.thinking.set()
                await self.stopped.wait()
            else:
                yield AgentEvent(kind=EVENT_TEXT_CHUNK, text=ANSWER)
        else:
            said = str(last.get("content") or "")
            call = next((c for asked, c in self.script.items() if asked in said), None)
            if call is None:
                yield AgentEvent(kind=EVENT_TEXT_CHUNK, text=NEXT)
            else:
                yield AgentEvent(
                    kind=EVENT_TOOL_CALL,
                    tool_call_id=f"c{len(self.requests)}",
                    title=call[0],
                    tool_input=json.dumps(call[1]),
                )
        yield AgentEvent(kind=EVENT_COMPLETE)

    async def cancel(self, *, wait_ack_timeout: float = 0.0) -> str:
        self.stopped.set()
        return "acked"

    def asked(self, text: str) -> int:
        """How many of its requests were for *text*, as the message they ended on."""
        return sum(
            1
            for request in self.requests
            if request and text in str(request[-1].get("content") or "")
        )


class _World:
    """One chat on a native runtime; a chat moved to another agent runs on *moved_to*'s."""

    def __init__(self, tmp_path: Path, model: _Model, *, moved_to: _Model | None = None) -> None:
        self.tmp = tmp_path
        self.notes = _Notes()
        self.model = model
        self.models = [model, *([moved_to] if moved_to is not None else [])]
        self.built: list[NativeAgentRuntime] = []
        cfg = AppConfig()
        cfg.agent.soft_stop_budget_secs = 5.0
        self.sessions = SessionManager(cfg, provider_factory=self._runtime)
        log = ConversationLog(base_dir=tmp_path / "sessions")
        self.state = DashboardState(sessions=self.sessions, start_time=0.0, conversation_log=log)
        (tmp_path / "ws").mkdir()
        self.state.context_builder = ContextBuilder(
            memory=MemoryStore(workspace=tmp_path / "ws"),
            skills=SkillsLoader(skills_path=tmp_path / "skills", install_builtins=False),
            conversation_log=log,
        )
        self.state._hook_store = None
        self.state.broadcast_ws = MagicMock()
        self.state.push_sessions_update = MagicMock()

    def _runtime(self, *_a: Any, **_kw: Any) -> NativeAgentRuntime:
        model = self.models[min(len(self.built), len(self.models) - 1)]
        runtime = NativeAgentRuntime(
            definition=AgentRuntimeDefinition(name="PersonalClaw", provider="native", model="x"),
            model_provider=model,
            tool_providers=[self.notes],
            cwd=self.tmp,
        )
        runtime.set_approval_policy("auto")
        self.built.append(runtime)
        return runtime

    def app(self, *, app_name: str = "") -> web.Application:
        app = _api_app(self.state)
        if app_name:

            @web.middleware
            async def _as_app(request: web.Request, handler: Any) -> web.StreamResponse:
                request["app"] = app_name
                return await handler(request)

            app.middlewares.append(_as_app)
        app.router.add_post("/api/chat/sessions/{session}/regenerate", api_chat_session_regenerate)
        app.router.add_post(
            "/api/chat/sessions/{session}/edit-resend", api_chat_session_edit_resend
        )
        return app

    async def ask(self, chat: Any, text: str) -> None:
        chat.append("user", text, "msg msg-u")
        chat.task = asyncio.ensure_future(run_chat(self.state, chat, text))
        await settled(chat)

    async def post(
        self, chat: Any, door: str, body: dict | None = None, *, app_name: str = ""
    ) -> tuple[int, dict]:
        async with TestClient(TestServer(self.app(app_name=app_name))) as client:
            resp = await client.post(f"/api/chat/sessions/{chat.key}/{door}", json=body)
            data = await resp.json()
        await settled(chat)
        return resp.status, data

    async def close(self) -> None:
        await self.sessions.close_all()


async def settled(chat: Any) -> None:
    for _ in range(1000):
        if not chat.running and not chat._queue:
            await asyncio.sleep(0.05)  # what the turn's end sends goes out on its own
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"the chat never finished its turns: {chat.messages}")


@pytest.fixture
def audit():
    """The audit rows the doors write (``SecurityEventLog.log_api_access``), wherever they write
    them from; the turns' own rows are not this test's subject."""
    rows = MagicMock()
    with (
        patch.object(SecurityEventLog, "log_api_access", rows),
        patch("personalclaw.dashboard.chat_runner.sel", MagicMock()),
    ):
        yield rows


def _asked_about(audit: MagicMock) -> list[dict]:
    return [c.kwargs for c in audit.call_args_list]


def _row_of(chat: Any, text: str) -> tuple[int, dict]:
    return next((i, m) for i, m in enumerate(chat.messages) if m.get("content") == text)


@pytest_asyncio.fixture
async def world(tmp_path):
    made: list[_World] = []

    def build(model: _Model, **kw: Any) -> _World:
        w = _World(tmp_path, model, **kw)
        made.append(w)
        return w

    yield build
    for w in made:
        await w.close()


# ── Regenerate on a completed answer ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_regenerating_an_answer_whose_turn_wrote_asks_first_and_runs_nothing(world, audit):
    w = world(_Model({QUESTION: WRITE}))
    chat = w.state.get_or_create_session()
    await w.ask(chat, QUESTION)
    assert w.notes.made("write_note") == [WRITE[1]]
    before = [dict(m) for m in chat.messages]

    status, data = await w.post(chat, "regenerate")

    assert status == 409, data
    assert data["error"]["code"] == "retry_repeats_steps"
    detail = data["error"]["detail"]
    assert detail["title"] == "Run this turn again?"
    assert detail["steps"] == [{"tool": "write_note", "target": "notes/plan.md"}]
    assert detail["said"] == (
        "This turn finished 1 step that may have changed something. Running the turn again "
        "replaces this attempt and may repeat it."
    )
    assert w.model.asked(QUESTION) == 1, "the turn ran again before anyone said yes"
    assert w.notes.made("write_note") == [WRITE[1]]
    assert chat.messages == before, "the answer and its steps were deleted before anyone said yes"
    assert _asked_about(audit)[-1] == {
        "caller": "dashboard",
        "operation": "chat.regenerate",
        "outcome": "needs_confirm",
        "source": "dashboard",
        "resources": chat.key,
        "metadata": {"repeats": ["write_note (notes/plan.md)"]},
    }

    status, data = await w.post(chat, "regenerate", {"confirm": detail["confirm"]})

    assert status == 200, data
    assert w.model.asked(QUESTION) == 2
    # She was told the step may repeat, and said yes: it did.
    assert w.notes.made("write_note") == [WRITE[1], WRITE[1]]
    (answer,) = [m for m in chat.messages if m.get("role") == "assistant"]
    variants = [v["content"] for v in answer.get("variants") or []]
    assert len(variants) == 2 and variants[0] == ANSWER, "the replaced answer is not kept"
    assert _asked_about(audit)[-1]["operation"] == "chat.regenerate"
    assert _asked_about(audit)[-1]["outcome"] == "allowed"
    assert _asked_about(audit)[-1]["metadata"] == {
        "confirmed": True,
        "repeats": ["write_note (notes/plan.md)"],
    }


@pytest.mark.asyncio
async def test_an_answer_whose_turn_only_read_regenerates_unasked(world, audit):
    w = world(_Model({LOOK: READ}))
    chat = w.state.get_or_create_session()
    await w.ask(chat, LOOK)

    status, data = await w.post(chat, "regenerate")

    assert status == 200, data
    assert w.model.asked(LOOK) == 2
    assert w.notes.made("read_note") == [READ[1], READ[1]]
    assert not _asked_about(audit)[-1].get("metadata"), "a run nobody was asked about says it was"


# ── Rewind to an earlier message, and a resend she did not change ──────────────────────────


@pytest.mark.asyncio
async def test_rewinding_to_an_earlier_message_with_its_own_words_asks_first(world, audit):
    w = world(_Model({QUESTION: WRITE}))
    chat = w.state.get_or_create_session()
    await w.ask(chat, QUESTION)
    await w.ask(chat, FOLLOW_UP)
    before = [dict(m) for m in chat.messages]
    at, row = _row_of(chat, QUESTION)
    rewind = {"content": QUESTION, "ts": row["ts"], "index": at, "rewind": True}

    status, data = await w.post(chat, "edit-resend", rewind)

    assert status == 409, data
    assert data["error"]["code"] == "retry_repeats_steps"
    detail = data["error"]["detail"]
    assert detail["steps"] == [{"tool": "write_note", "target": "notes/plan.md"}]
    assert w.model.asked(QUESTION) == 1, "the turn ran again before anyone said yes"
    assert chat.messages == before, "the later turns came off before anyone said yes"
    assert _asked_about(audit)[-1]["operation"] == "chat.rewind"
    assert _asked_about(audit)[-1]["outcome"] == "needs_confirm"

    status, data = await w.post(chat, "edit-resend", {**rewind, "confirm": detail["confirm"]})

    assert status == 200, data
    assert w.model.asked(QUESTION) == 2
    assert w.notes.made("write_note") == [WRITE[1], WRITE[1]]
    _at, rerun = _row_of(chat, QUESTION)
    assert rerun.get("rewound"), "the turns it replaced are not kept on the message"
    assert _asked_about(audit)[-1]["metadata"] == {
        "confirmed": True,
        "repeats": ["write_note (notes/plan.md)"],
    }


@pytest.mark.asyncio
async def test_rewind_asks_only_about_the_turn_it_runs_again(world, audit):
    # The earlier turn only read; the later one wrote. Answering the earlier message again does
    # not repeat the later turn's steps, which answered another message.
    w = world(_Model({LOOK: READ, FOLLOW_UP: WRITE}))
    chat = w.state.get_or_create_session()
    await w.ask(chat, LOOK)
    await w.ask(chat, FOLLOW_UP)
    at, row = _row_of(chat, LOOK)

    status, data = await w.post(
        chat, "edit-resend", {"content": LOOK, "ts": row["ts"], "index": at, "rewind": True}
    )

    assert status == 200, data
    assert w.model.asked(LOOK) == 2


@pytest.mark.asyncio
async def test_resending_her_last_message_unchanged_asks_first(world, audit):
    w = world(_Model({QUESTION: WRITE}))
    chat = w.state.get_or_create_session()
    await w.ask(chat, QUESTION)
    at, row = _row_of(chat, QUESTION)

    status, data = await w.post(
        chat, "edit-resend", {"content": f"  {QUESTION}\n", "ts": row["ts"], "index": at}
    )

    assert status == 409, data
    assert data["error"]["detail"]["steps"] == [{"tool": "write_note", "target": "notes/plan.md"}]
    assert w.model.asked(QUESTION) == 1
    assert _asked_about(audit)[-1]["operation"] == "chat.edit_resend"


@pytest.mark.asyncio
async def test_an_edit_that_changes_her_message_is_a_new_message_and_runs_unasked(world, audit):
    edited = "Save the plan to notes/launch.md instead."
    w = world(_Model({QUESTION: WRITE}))
    chat = w.state.get_or_create_session()
    await w.ask(chat, QUESTION)
    at, row = _row_of(chat, QUESTION)

    status, data = await w.post(
        chat, "edit-resend", {"content": edited, "ts": row["ts"], "index": at}
    )

    assert status == 200, data
    assert w.model.asked(edited) == 1


@pytest.mark.asyncio
async def test_the_page_says_when_it_sends_the_message_again_as_it_shows_it(world, audit):
    """An optimized message keeps the optimized text, and the page shows what she typed: the
    words it sends back are not the row's, so the page says it is sending the message again."""
    optimized = f"{QUESTION} Use the plan template."
    w = world(_Model({QUESTION: WRITE}))
    chat = w.state.get_or_create_session()
    chat.append("user", optimized, "msg msg-u", meta={"original": QUESTION})
    chat.task = asyncio.ensure_future(run_chat(w.state, chat, optimized))
    await settled(chat)
    at, row = _row_of(chat, optimized)
    shown = {"content": QUESTION, "ts": row["ts"], "index": at}

    status, data = await w.post(chat, "edit-resend", {**shown, "again": True})

    assert status == 409, data
    assert w.model.asked(QUESTION) == 1


@pytest.mark.asyncio
async def test_an_app_is_told_to_rewind_from_the_dashboard(world, audit):
    w = world(_Model({QUESTION: WRITE}))
    chat = w.state.get_or_create_session()
    await w.ask(chat, QUESTION)
    await w.ask(chat, FOLLOW_UP)
    before = [dict(m) for m in chat.messages]
    at, row = _row_of(chat, QUESTION)

    status, data = await w.post(
        chat,
        "edit-resend",
        {"content": QUESTION, "ts": row["ts"], "index": at, "rewind": True},
        app_name="notes-helper",
    )

    assert status == 409, data
    assert data["error"]["code"] == "retry_repeats_steps"
    assert data["error"]["message"].endswith(
        "It was not run: retry it from the dashboard, where you can confirm it."
    )
    assert "detail" not in data["error"], "an app was handed the owner's question"
    assert chat.messages == before
    assert w.model.asked(QUESTION) == 1
    assert _asked_about(audit)[-1]["caller"] == "app:notes-helper"
    assert _asked_about(audit)[-1]["outcome"] == "refused"


# ── a chat moved to another agent while it answered ────────────────────────────────────────

MOVE = running_turn.Rebinding(fields={"model": "fast-model"}, persisted={"model": "fast-model"})


async def _moved_while_thinking(w: _World, text: str) -> tuple[Any, str]:
    """Send *text*, and once its call has finished and its model is thinking, move the chat.
    Returns the chat and what the chat calls where it moved."""
    chat = w.state.get_or_create_session()
    chat.append("user", text, "msg msg-u")
    chat.task = asyncio.ensure_future(run_chat(w.state, chat, text))
    await asyncio.wait_for(w.model.thinking.wait(), timeout=10)
    to = running_turn.answering_label(chat, MOVE)
    assert await running_turn.rebind(w.state, chat, MOVE), "the move found no turn to move"
    await settled(chat)
    return chat, to


@pytest.mark.asyncio
async def test_a_moved_turn_that_wrote_is_not_answered_again_on_its_own(world, audit):
    moved_to = _Model({QUESTION: WRITE})
    w = world(_Model({QUESTION: WRITE}, thinks=True), moved_to=moved_to)

    chat, to = await _moved_while_thinking(w, QUESTION)

    assert moved_to.requests == [], "the agent it moved to was asked to do it all again"
    assert w.notes.made("write_note") == [WRITE[1]]
    assert chat.model == "fast-model", "the move itself was not made"
    said = turn_endings.moved_without_answering_notice(to, 1)
    assert chat.messages[-1]["role"] == "error", chat.messages
    assert chat.messages[-1]["content"] == said
    assert "it is answering your message" not in json.dumps(chat.messages)


@pytest.mark.asyncio
async def test_a_moved_turn_that_only_read_is_answered_again_where_it_moved(world, audit):
    moved_to = _Model({LOOK: READ})
    w = world(_Model({LOOK: READ}, thinks=True), moved_to=moved_to)

    chat, to = await _moved_while_thinking(w, LOOK)

    assert moved_to.asked(LOOK) == 1, "the agent it moved to was never asked"
    assert turn_endings.moved_turn_notice(to) in [
        m["content"] for m in chat.messages if m.get("role") == "notice"
    ]
    assert [m["content"] for m in chat.messages if m.get("role") == "assistant"][-1] == ANSWER

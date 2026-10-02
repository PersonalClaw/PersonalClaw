"""Changing what answers a chat while it answers ends that turn honestly and answers her there.

Measured on a running gateway: a chat's routing chip ("oncall-triage may fit better — route this
chat to it?") was pressed while the default agent's turn waited on a permission card. The agent
switch rebuilt the chat's runtime under the turn, so the turn died with nothing said: the page
kept "Thinking…", the permission card and the Steer composer; her steer went to the queue; her
later Allow read "approved", then the tool "Error: cancelled", then "The reply stopped before it
finished"; her first message was never answered.

The rule now, for every door that changes what answers a chat (its agent, its agent CLI, its
model, its reasoning effort): the running turn ends as stopped, its pending approval answered
cancelled as a Stop answers it; her message is answered again on the new runtime as the same
turn; and the chat says so where the conversation is. The composer offers Steer only while the
running turn takes one.

Driven through the real chat runner, the real session manager, the real ACP client and the real
handlers, against ``scripted_acp_agent.py`` over stdio: the runtime the chat starts on asks for
approval and waits, and the runtime it is moved to answers at once.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from aiohttp.test_utils import TestClient, TestServer
from chat_test_helpers import _api_app
from scripted_acp_agent import PLAIN_ANSWER
from test_dashboard_approval import (
    _complete_event,
    _context_builder,
    _make_hook_store,
    _make_session,
    _set_stream,
)

from personalclaw.approval_answer import YOU
from personalclaw.config.loader import AppConfig
from personalclaw.dashboard import running_turn
from personalclaw.dashboard.approval_state import chat_approval_id
from personalclaw.dashboard.chat import (
    api_chat_session_acp_agent,
    api_chat_session_agent,
    api_chat_session_detail,
    api_chat_session_model,
    api_chat_session_reasoning_effort,
    run_chat,
)
from personalclaw.dashboard.chat_runner import TURN_CUT_SHORT_NOTICE
from personalclaw.dashboard.state import DashboardState
from personalclaw.history import ConversationLog
from personalclaw.llm.acp_agent import AcpAgentProvider
from personalclaw.llm.base import EVENT_PERMISSION_REQUEST, EVENT_TEXT_CHUNK, LLMEvent
from personalclaw.session import SessionManager

AGENT = Path(__file__).with_name("scripted_acp_agent.py")
CHAT = "chat-route-1"
#: The scripted agent's permission request id (``scripted_acp_agent.PERMISSION_ID``).
ASKED = "900"
HER_MESSAGE = "sentry is lighting up for the carrier adapter again"


def _moved_to(kwargs: dict[str, Any]) -> bool:
    """Whether the runtime being built is one of those the chat was moved to."""
    return (
        kwargs.get("agent") in ("oncall-triage", "reviewer")
        or kwargs.get("model_override") == "fast-model"
        or kwargs.get("reasoning_effort_override") == "high"
    )


class _World:
    """One chat whose first runtime asks and waits, and whose next one answers."""

    def __init__(self, tmp_path: Path):
        self.tmp = tmp_path
        self.built: list[tuple[str, AcpAgentProvider]] = []

        def factory(session_key=None, **kwargs):
            if not str(session_key or "").startswith("dashboard:"):
                raise RuntimeError(f"{session_key!r} does not run on the scripted agent")
            scenario = "answers" if _moved_to(kwargs) else "wait-for-stop"
            provider = AcpAgentProvider(
                command=[sys.executable, str(AGENT), scenario, str(self.record(scenario)), "spec"],
                cwd=tmp_path / "work",
                dialect="codex",
                runtime_id="acp:codex",
                session_key=session_key,
            )
            self.built.append((scenario, provider))
            return provider

        cfg = AppConfig()
        cfg.agent.soft_stop_budget_secs = 5.0
        self.sessions = SessionManager(cfg, provider_factory=factory)
        self.log = ConversationLog(base_dir=tmp_path / "history")
        self.state = DashboardState(
            sessions=self.sessions, start_time=0.0, conversation_log=self.log
        )
        self.state.context_builder = _context_builder()
        # The runtime is handed the message as it was asked, so the record says what it was asked.
        self.state.context_builder.build_message.side_effect = lambda text, *a, **k: (text, None)
        self.state._hook_store = _make_hook_store()
        self.frames: list[tuple[str, dict]] = []
        self.state.broadcast_ws = lambda kind, data=None: self.frames.append((kind, data or {}))
        self.state.push_sessions_update = lambda *a, **k: None
        self.session = _make_session(CHAT)
        self.state._sessions[self.session.key] = self.session

    def record(self, scenario: str) -> Path:
        return self.tmp / f"{scenario}-wire.jsonl"

    def wire(self, scenario: str, kind: str) -> list[dict]:
        path = self.record(scenario)
        if not path.exists():
            return []
        rows = [json.loads(line) for line in path.read_text().splitlines() if line]
        return [r for r in rows if r["kind"] == kind]

    def prompts(self, scenario: str) -> list[str]:
        return [
            json.dumps(r["params"])
            for r in self.wire(scenario, "received")
            if r["method"] == "session/prompt"
        ]

    def rows(self, role: str) -> list[str]:
        return [m["content"] for m in self.session.messages if m.get("role") == role]

    def frames_of(self, kind: str) -> list[dict]:
        return [d for k, d in self.frames if k == kind]

    def start(self) -> None:
        assert self.session.enqueue_or_run_prompt(HER_MESSAGE, run_chat, self.state)

    async def asked(self) -> None:
        for _ in range(1000):
            if ASKED in self.session._approval_futures:
                return
            await asyncio.sleep(0.01)
        raise AssertionError(f"the chat never asked: {self.session.messages}")

    async def answered(self) -> None:
        """Until the chat is idle with the answer of the runtime it was moved to."""
        for _ in range(2000):
            task = self.session.task
            if (task is None or task.done()) and PLAIN_ANSWER in self.rows("assistant"):
                return
            await asyncio.sleep(0.01)
        raise AssertionError(f"her message was never answered: {self.session.messages}")

    async def close(self) -> None:
        task = self.session.task
        if task is not None and not task.done():
            task.cancel()
            try:
                await asyncio.wait_for(task, timeout=10)
            except (asyncio.CancelledError, Exception):  # noqa: BLE001 - teardown only
                pass
        await self.sessions.close_all()
        for _, provider in self.built:
            await provider.shutdown()


@pytest_asyncio.fixture
async def world(tmp_path, monkeypatch):
    # The handshake's settle wait for MCP start-up notices: this agent sends none.
    monkeypatch.setattr("personalclaw.acp.client._DRAIN_DURATION", 0.05)
    w = _World(tmp_path)
    app = _api_app(w.state)
    app.router.add_post("/api/chat/sessions/{session}/agent", api_chat_session_agent)
    app.router.add_post("/api/chat/sessions/{session}/acp-agent", api_chat_session_acp_agent)
    app.router.add_post("/api/chat/sessions/{session}/model", api_chat_session_model)
    app.router.add_post(
        "/api/chat/sessions/{session}/reasoning-effort", api_chat_session_reasoning_effort
    )
    app.router.add_get("/api/chat/sessions/{session}", api_chat_session_detail)
    client = TestClient(TestServer(app))
    await client.start_server()
    w.client = client
    try:
        yield w
    finally:
        await client.close()
        await w.close()


#: Each door that changes what answers a chat, what it is sent, how the chat names where it
#: moved, and the session field that then says so.
DOORS = [
    ("agent", {"agent": "oncall-triage"}, "oncall-triage", ("agent", "oncall-triage")),
    (
        "acp-agent",
        {"provider": "acp:codex", "provider_agent": "reviewer"},
        "reviewer",
        ("acp_provider_agent", "reviewer"),
    ),
    ("model", {"model": "fast-model"}, "PersonalClaw on fast-model", ("model", "fast-model")),
    (
        "reasoning-effort",
        {"reasoning_effort": "high"},
        "PersonalClaw at high reasoning effort",
        ("reasoning_effort", "high"),
    ),
]


@pytest.mark.parametrize(("door", "body", "named", "now"), DOORS, ids=[d[0] for d in DOORS])
@pytest.mark.asyncio
async def test_a_change_mid_turn_ends_the_waiting_approval_and_answers_her_message_there(
    world, door, body, named, now
):
    """Red before the fix: the change rebuilt the runtime under the turn, the approval stayed
    pending (answerable, resuming nothing), and her message was never answered."""
    w = world
    w.start()
    await w.asked()
    aid = chat_approval_id(w.session.key, ASKED)

    answer = await (await w.client.post(f"/api/chat/sessions/{CHAT}/{door}", json=body)).json()
    await w.answered()

    # No permission card stays live for the turn that ended: every surface was told it ended
    # cancelled, the transcript row says so, and the agent was answered the way the protocol
    # asks of a cancelled turn.
    assert aid not in w.state._pending_approvals
    assert [d["outcome"] for d in w.frames_of("approval_resolved")] == ["cancelled"]
    (permission,) = [m for m in w.session.messages if m.get("role") == "permission"]
    assert json.loads(permission["cls"]).get("resolved") == "cancelled"
    assert [a["outcome"] for a in w.wire("wait-for-stop", "permission_answer")] == ["cancelled"]

    # The turn ended as the move it was, not as a fault, and the chat says where it moved.
    assert TURN_CUT_SHORT_NOTICE not in w.rows("error") and w.rows("error") == []
    notice = f"Moved to {named} — it is answering your message."
    assert w.rows("notice") == [notice]
    assert {"session": CHAT, "role": "notice", "content": notice} in w.frames_of("chat_message")

    # Her message was asked once of the runtime she moved away from, and answered by the new
    # one, without a second bubble of hers.
    assert len(w.prompts("wait-for-stop")) == 1
    (asked_again,) = w.prompts("answers")
    assert HER_MESSAGE in asked_again
    assert w.rows("user") == [HER_MESSAGE]
    assert w.rows("assistant") == [PLAIN_ANSWER]
    # The old runtime is gone, and the new one served the answer.
    assert [scenario for scenario, _ in w.built] == ["wait-for-stop", "answers"]
    assert not w.built[0][1].is_process_alive()

    # The chat runs on what she picked, and every open page was told.
    attr, value = now
    assert getattr(w.session, attr) == value
    (binding,) = w.frames_of("session_binding")
    assert binding["session"] == CHAT and binding[attr] == value, binding
    # And the caller is told the running turn was moved.
    assert answer["ok"] is True and answer["moved"] is True, answer


@pytest.mark.asyncio
async def test_a_move_before_the_prompt_goes_out_asks_the_old_runtime_nothing(world):
    """The chip appears right after she sends, so the move can land while the turn is still being
    put together, before anything is on the runtime to stop. The turn ends there."""
    w = world
    w.start()
    answer = await (
        await w.client.post(f"/api/chat/sessions/{CHAT}/agent", json={"agent": "oncall-triage"})
    ).json()
    await w.answered()

    assert w.prompts("wait-for-stop") == [], "the runtime she moved away from was asked"
    (asked,) = w.prompts("answers")
    assert HER_MESSAGE in asked
    assert w.rows("notice") == ["Moved to oncall-triage — it is answering your message."]
    assert w.rows("error") == []
    assert w.session.agent == "oncall-triage"
    assert answer["moved"] is True, answer


@pytest.mark.asyncio
async def test_a_change_between_turns_applies_at_once_and_says_nothing(world):
    """With no turn running there is nothing to move: the runtime is rebuilt for the next turn."""
    w = world
    answer = await (
        await w.client.post(f"/api/chat/sessions/{CHAT}/agent", json={"agent": "oncall-triage"})
    ).json()
    assert w.session.agent == "oncall-triage"
    assert w.rows("notice") == []
    # Every open page of the chat is told, not only the one that made the change.
    (binding,) = w.frames_of("session_binding")
    assert binding["agent"] == "oncall-triage"
    assert answer == {"ok": True, "agent": "oncall-triage", "workspace_dir": "", "moved": False}


@pytest.mark.asyncio
async def test_a_turn_whose_runtime_takes_no_steer_says_so(world):
    """An agent CLI that cannot be handed a message mid-turn is not offered one: the chat serves
    `steerable: false` while its turn runs, so the composer says Queue, not Steer."""
    w = world
    w.start()
    await w.asked()
    detail = await (await w.client.get(f"/api/chat/sessions/{CHAT}")).json()
    assert detail["running"] is True
    assert detail["steerable"] is False
    assert w.frames_of("turn_steerable")[-1] == {"session": CHAT, "steerable": False}


# ── A runtime that takes steers: what the chat says follows the turn ───────────────────────────


@pytest.mark.asyncio
async def test_the_chat_says_its_turn_takes_a_steer_only_while_that_turn_runs(tmp_path):
    """Steer while the turn's runtime pulls messages in, nothing once the turn is over."""
    sessions: Any = SessionManager(AppConfig())
    client = AsyncMock()
    client.set_steer_source = MagicMock(return_value=True)
    sessions.get_or_create = AsyncMock(return_value=(client, True, False))
    sessions.record_failure = AsyncMock()
    sessions.check_context_usage = MagicMock()
    sessions.get_pid = MagicMock(return_value=None)
    state = DashboardState(
        sessions=sessions, start_time=0.0, conversation_log=ConversationLog(base_dir=tmp_path)
    )
    state.context_builder = _context_builder()
    state._hook_store = _make_hook_store()
    state.push_sessions_update = MagicMock()  # type: ignore[method-assign]
    frames: list[tuple[str, dict]] = []

    def _frame(kind: str, data: dict | None = None) -> None:
        frames.append((kind, data or {}))

    state.broadcast_ws = _frame  # type: ignore[method-assign]
    session = _make_session("chat-steer-1")
    state._sessions[session.key] = session
    _set_stream(
        client,
        [
            LLMEvent(
                kind=EVENT_PERMISSION_REQUEST,
                title="bash",
                tool_kind="execute",
                request_id="req-1",
                tool_call_id="tc-1",
                tool_input=json.dumps({"command": "ls"}),
            ),
            LLMEvent(kind=EVENT_TEXT_CHUNK, text="Listed."),
            _complete_event(),
        ],
    )
    app = _api_app(state)
    app.router.add_get("/api/chat/sessions/{session}", api_chat_session_detail)
    async with TestClient(TestServer(app)) as http:
        assert session.enqueue_or_run_prompt("list the files", run_chat, state)
        for _ in range(500):
            if "req-1" in session._approval_futures:
                break
            await asyncio.sleep(0.01)
        running = await (await http.get("/api/chat/sessions/chat-steer-1")).json()
        assert running["running"] is True and running["steerable"] is True
        said = [d["steerable"] for k, d in frames if k == "turn_steerable"]
        assert said == [True]

        state.decide_session_approval(session, "req-1", "approved", by=YOU)
        await asyncio.wait_for(session.task, timeout=10)
        idle = await (await http.get("/api/chat/sessions/chat-steer-1")).json()
        assert idle["running"] is False and idle["steerable"] is False
        assert [d["steerable"] for k, d in frames if k == "turn_steerable"] == [True, False]


# ── How a moved turn ends: its answer stands, or her message is asked again once ───────────────


class _Told:
    """The dashboard state's surfaces a moved turn's ending speaks to."""

    def __init__(self) -> None:
        self.frames: list[tuple[str, dict]] = []
        self.told: list[str] = []
        self._background_tasks: set = set()

    def broadcast_ws(self, kind: str, data: dict | None = None) -> None:
        self.frames.append((kind, data or {}))

    async def tell_linked_channel(self, key: str, note: str) -> None:
        self.told.append(note)


def _moved_session() -> Any:
    session = _make_session("chat-moved-1")
    session._rebinding = running_turn.Rebinding(fields={"agent": "oncall-triage"})
    return session


@pytest.mark.asyncio
async def test_a_move_that_lands_after_the_answer_keeps_it_and_applies_from_her_next_message():
    told, session, asked_again = _Told(), _moved_session(), []
    moved = running_turn.say_moved(
        told, session, "dashboard:chat-moved-1", True, lambda: asked_again.append(1)
    )
    await asyncio.sleep(0)
    assert moved is True
    assert asked_again == [], "an answer she already has was asked for again"
    note = "Switched to oncall-triage. It answers your next message."
    assert [m["content"] for m in session.messages if m["role"] == "notice"] == [note]
    assert told.told == [note]


@pytest.mark.asyncio
async def test_a_moved_turn_that_queued_its_own_retry_asks_her_message_once():
    told, session, asked_again = _Told(), _moved_session(), []
    session.queue_retry(HER_MESSAGE)
    running_turn.say_moved(
        told, session, "dashboard:chat-moved-1", False, lambda: asked_again.append(1)
    )
    assert asked_again == []
    assert [q["content"] for q in session._queue] == [HER_MESSAGE]
    assert [m["content"] for m in session.messages if m["role"] == "notice"] == [
        "Moved to oncall-triage — it is answering your message."
    ]


def test_a_turn_nobody_moved_ends_as_it_did():
    session = _make_session("chat-moved-2")
    assert running_turn.say_moved(_Told(), session, "dashboard:chat-moved-2", False, None) is False
    assert session.messages == []

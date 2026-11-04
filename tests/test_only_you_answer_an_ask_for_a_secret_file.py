"""Only you answer an ask for a project's secret file, and only with Allow for this session or Deny.

A file tool refused a project's secret file asks the owner for it (``secret_file_grants``). Nothing
that answers other calls on its own answers this one: not a declared read, a hook's auto-approve
pattern, Trust reads, Trust, YOLO or a standing grant (``run_bounds.put_to_a_person``), not a Trust
or YOLO switch pressed while it waits, and not a channel's one-press prompt, which is sent the link
instead. The chat's approve route takes Allow and Deny for it and refuses a standing grant.

Measured before this change, each 🔴 test below: no request could carry the ask, so a declared
read's own answer approved any read the owner was asked about, YOLO or Trust approved the rest,
and the route granted Trust on an approval it was aimed at.

Each has its control: a call that is not such an ask is answered as it always was.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import personalclaw.config.loader as loader
from personalclaw import approval_answer, approval_grants, run_bounds
from personalclaw.dashboard.approval_state import chat_approval_id
from personalclaw.dashboard.state import DashboardState
from personalclaw.history import ConversationLog
from personalclaw.llm.events import (
    EVENT_COMPLETE,
    EVENT_PERMISSION_REQUEST,
    EVENT_TEXT_CHUNK,
    EVENT_TOOL_CALL,
    AgentEvent,
)

NAME = "SHOP_NAME=Lantern Goods"
ENV = f"{NAME}\nSHOP_API_KEY=lantern4242\n"
REFUSED = "is a credential or secret file, which this tool does not reach"


def _ask(**more: Any) -> AgentEvent:
    """The native loop's ask for reading a project's ``.env``: a call whose tool declares it only
    reads, which every other read is answered for without asking."""
    return AgentEvent(
        kind=EVENT_PERMISSION_REQUEST,
        request_id="c1:secret-file:1",
        tool_call_id="c1",
        title="read_file",
        tool_input={"path": ".env"},
        risk_level="safe",
        **more,
    )


@pytest.fixture(autouse=True)
def _no_grant_outlives_its_test():
    yield
    grants = sys.modules.get("personalclaw.secret_file_grants")
    if grants is not None:
        grants.clear()


# ── nothing answers it on its own ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "grant",
    [
        approval_grants.HOOK_PATTERN,
        approval_grants.TRUST_READS,
        approval_grants.TRUST,
        approval_grants.YOLO,
        approval_grants.PARENT_TRUST,
    ],
)
def test_no_grant_and_no_declaration_answers_it(grant, tmp_path):
    """🔴 A declared read was answered without asking, and a grant stood for it."""
    asked = _ask(secret_file="read")
    assert approval_grants.declared_answer(asked) == ""
    assert run_bounds.put_to_a_person(asked, session_key="dashboard:shop", cwd=str(tmp_path))
    assert not approval_grants.stands_for_call(
        grant, session_key="dashboard:shop", event=asked, cwd=str(tmp_path)
    )
    note = run_bounds.event_note(asked, "dashboard:shop", cwd=str(tmp_path))
    assert "Allow lets this chat read it until the chat is deleted" in note
    assert "Trust, YOLO and saved grants never do" in note


@pytest.mark.parametrize("held", ["", "delete", MagicMock()], ids=["none", "unknown", "mock"])
def test_a_read_that_asks_for_no_secret_file_is_answered_as_before(tmp_path, held):
    """No ask but ``read`` or ``write`` is one: a word another build wrote, or a stand-in event
    whose every attribute answers, is the read it always was."""
    asked = _ask()
    asked.secret_file = held
    assert approval_grants.declared_answer(asked) == approval_grants.DECLARED_READ
    assert not run_bounds.put_to_a_person(asked, session_key="dashboard:shop", cwd=str(tmp_path))
    assert run_bounds.event_note(asked, "dashboard:shop", cwd=str(tmp_path)) == ""


# ── the chat's card, its route and its switches ──────────────────────────────────────────────────


def _state(tmp_path: Path) -> DashboardState:
    sessions = MagicMock(count=0)
    sessions.get_pid = MagicMock(return_value=None)
    sessions.remove = AsyncMock()
    return DashboardState(
        sessions=sessions, start_time=0.0, conversation_log=ConversationLog(base_dir=tmp_path)
    )


def _app(state: DashboardState) -> web.Application:
    from chat_test_helpers import _api_app

    from personalclaw.dashboard.chat import api_chat_mode, api_chat_session_approve

    app = _api_app(state)
    app.router.add_post("/api/chat/sessions/{session}/approve", api_chat_session_approve)
    app.router.add_post("/api/chat/mode", api_chat_mode)
    return app


async def _held(state: DashboardState, *, secret_file: str = "read") -> tuple[Any, Any]:
    """A chat waiting on the owner's answer to an ask for its project's ``.env``."""
    session = state.get_or_create_session("shop")
    fut: asyncio.Future[str] = asyncio.get_running_loop().create_future()
    session._approval_futures["c1:secret-file:1"] = fut
    await state.hold_session_approval(
        session,
        "c1:secret-file:1",
        tool="read_file",
        tool_input='{"path": ".env"}',
        tool_purpose="",
        agent="PersonalClaw",
        risk="safe",
        is_read_only=True,
        blast_radius=None,
        grant_agent="PersonalClaw",
        # What the chat's runner works out for a read of a file (`approval_confirms`): none.
        confirms=[],
        secret_file=secret_file,
    )
    return session, fut


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["trust", "trust_agent", "trust_reads", "yolo"])
async def test_the_chats_route_refuses_a_standing_grant_for_it(tmp_path, monkeypatch, action):
    """🔴 The route granted the chat's Trust, or YOLO, on the ask it was aimed at."""
    monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
    state = _state(tmp_path)
    session, fut = await _held(state)

    async with TestClient(TestServer(_app(state))) as client:
        resp = await client.post(
            "/api/chat/sessions/shop/approve",
            json={"action": action, "request_id": "c1:secret-file:1"},
        )
        body = await resp.json()

    assert resp.status == 409, body
    assert body["error"]["code"] == "approval_secret_file"
    assert not fut.done(), "still asking"
    assert session._trust is False and not state.is_yolo_active()


@pytest.mark.asyncio
async def test_allow_for_this_session_and_deny_answer_it(tmp_path, monkeypatch):
    monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
    for action, ends in (("approved", "approved"), ("rejected", "rejected")):
        state = _state(tmp_path)
        _session, fut = await _held(state)
        entry = state._pending_approvals[chat_approval_id("shop", "c1:secret-file:1")]
        assert entry["secret_file"] == "read"
        async with TestClient(TestServer(_app(state))) as client:
            resp = await client.post(
                "/api/chat/sessions/shop/approve",
                json={"action": action, "request_id": "c1:secret-file:1"},
            )
        assert resp.status == 200
        assert fut.result() == ends


@pytest.mark.asyncio
async def test_a_trust_or_yolo_switch_leaves_it_asking(tmp_path, monkeypatch):
    monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
    state = _state(tmp_path)
    _session, fut = await _held(state)
    assert state.answered_alone(chat_approval_id("shop", "c1:secret-file:1"))

    async with TestClient(TestServer(_app(state))) as client:
        for mode in ("trust", "yolo"):
            resp = await client.post("/api/chat/mode", json={"session": "shop", "mode": mode})
            assert resp.status == 200, await resp.text()

    assert not fut.done()


@pytest.mark.asyncio
async def test_a_channel_is_sent_the_link_and_its_press_answers_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
    state = _state(tmp_path)
    _session, fut = await _held(state)
    approval_id = chat_approval_id("shop", "c1:secret-file:1")
    said: list[str] = []

    async def _reach_owner(send, *, only):
        delivery = SimpleNamespace(deliver_text=AsyncMock())
        await send(delivery, "dm")
        said.append(delivery.deliver_text.await_args.args[1])
        return SimpleNamespace(delivered=True, no_channel=False)

    prompt = AsyncMock()
    monkeypatch.setattr("personalclaw.channel_delivery.reach_owner", _reach_owner)
    monkeypatch.setattr(
        "personalclaw.channel_delivery.delivery_for",
        lambda provider: SimpleNamespace(request_approval=prompt),
    )
    monkeypatch.setattr("personalclaw.config.credentials.owner_id_for", lambda p: "U0OWNER")
    entry = state._pending_approvals[approval_id]
    await state._approval_on_a_channel(approval_id, entry, ["slack"])
    prompt.assert_not_called()
    assert len(said) == 1 and "a project's secret file" in said[0]
    assert "answered in PersonalClaw" in said[0]

    pressed = approval_answer.on_channel("slack")
    assert not state.resolve_approval(approval_id, True, by=pressed)
    assert not fut.done()
    assert state.resolve_approval(approval_id, False, by=pressed), "a Deny answers"


# ── a chat's turn ────────────────────────────────────────────────────────────────────────────────


class _Model:
    """Reads the project's ``.env``, then answers with what it was handed."""

    supports_tools = True
    _model = "tiny"

    def __init__(self) -> None:
        self.calls = 0
        self.told = ""

    async def complete(self, messages, *, tools=None, model=None, reasoning_effort=""):
        self.calls += 1
        if self.calls == 1:
            yield AgentEvent(
                kind=EVENT_TOOL_CALL,
                tool_call_id="c1",
                title="read_file",
                tool_input=json.dumps({"path": ".env"}),
            )
            yield AgentEvent(kind=EVENT_COMPLETE)
            return
        self.told = str(messages[-1].get("content") or "")
        yield AgentEvent(kind=EVENT_TEXT_CHUNK, text="Read it.")
        yield AgentEvent(kind=EVENT_COMPLETE)

    async def cancel(self, *, wait_ack_timeout: float = 0.0) -> str:
        return "acked"


@pytest.fixture
def owner(tmp_path, monkeypatch):
    user = tmp_path / "user"
    pc = user / ".personalclaw"
    project = user / "projects" / "shop"
    project.mkdir(parents=True)
    pc.mkdir(parents=True)
    (project / ".env").write_text(ENV)
    monkeypatch.setenv("HOME", str(user))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: user))
    monkeypatch.setenv("PERSONALCLAW_HOME", str(pc))
    monkeypatch.setattr(loader, "config_dir", lambda: pc)
    monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: pc)
    return SimpleNamespace(user=user, home=pc, project=project, env=project / ".env")


async def _chat_turn(owner: Any, *, app: str = "", answer: str = "approved") -> SimpleNamespace:
    """A chat turn in the project, with the chat's Trust and YOLO on, whose agent reads ``.env``.
    Each ask for a secret file is answered *answer* from the chat's card, as the owner presses
    it; what was pending when it was answered is kept."""
    from personalclaw.agents.native.builtin_tools import NativeBuiltinToolProvider
    from personalclaw.agents.native.runtime import NativeAgentRuntime
    from personalclaw.agents.provider import AgentRuntimeDefinition
    from personalclaw.context import ContextBuilder
    from personalclaw.dashboard.chat_runner import run_chat
    from personalclaw.memory import MemoryStore
    from personalclaw.skills import SkillsLoader

    model = _Model()
    key = "dashboard:shop"
    runtime = NativeAgentRuntime(
        definition=AgentRuntimeDefinition(name="PersonalClaw", provider="native", model="tiny"),
        model_provider=model,
        tool_providers=[NativeBuiltinToolProvider(owner.project, session_key=key)],
        cwd=owner.project,
        session_key=key,
    )
    await runtime.start()
    state = _state(owner.home / "sessions")
    state.sessions._sessions = {}
    state.sessions.get_channel_link = MagicMock(return_value=(None, None))
    state.sessions.get_or_create = AsyncMock(return_value=(runtime, True, False))
    state.sessions.record_failure = AsyncMock()
    state.context_builder = ContextBuilder(
        memory=MemoryStore(workspace=owner.home / "workspace"),
        skills=SkillsLoader(skills_path=owner.home / "skills", install_builtins=False),
        conversation_log=state.conversation_log,
    )
    state._hook_store = None
    state.broadcast_ws = MagicMock()
    state.push_sessions_update = MagicMock()
    state.enable_yolo()
    session = state.get_or_create_session("shop")
    session._trust = True
    if app:
        session.created_by_app = app
    asked: list[dict] = []

    async def answer_it() -> None:
        while True:
            for entry in list(state._pending_approvals.values()):
                rid = str(entry.get("request_id") or "")
                held = session._approval_futures.get(rid)
                if held is not None and not held.done():
                    asked.append(dict(entry))
                    state.decide_session_approval(session, rid, answer, by=approval_answer.YOU)
            await asyncio.sleep(0.01)

    answering = asyncio.create_task(answer_it())
    try:
        session.append("user", "What is the shop called?", "msg msg-u")
        await asyncio.wait_for(run_chat(state, session, "What is the shop called?"), timeout=20)
    finally:
        answering.cancel()
    return SimpleNamespace(asked=asked, told=model.told, session=session, state=state)


@pytest.mark.asyncio
async def test_with_trust_and_yolo_on_the_chat_still_asks_you_and_your_allow_reads_it(owner):
    """🔴 The read was refused with no ask, and had it been asked, YOLO would have approved it."""
    turn = await _chat_turn(owner)

    assert [e.get("secret_file") for e in turn.asked] == ["read"], turn.asked
    assert "only your answer opens it" in turn.asked[0]["reach"].casefold()
    assert NAME in turn.told and "lantern4242" not in turn.told
    rows = [m for m in turn.session.messages if m.get("role") == "permission"]
    assert json.loads(rows[-1]["cls"])["secret_file"] == "read"


@pytest.mark.asyncio
async def test_your_deny_keeps_the_refusal(owner):
    turn = await _chat_turn(owner, answer="rejected")

    assert [e.get("secret_file") for e in turn.asked] == ["read"]
    assert REFUSED in turn.told


@pytest.mark.asyncio
async def test_a_conversation_an_app_started_is_refused_and_asks_no_one(owner):
    turn = await _chat_turn(owner, app="shop-helper")

    assert turn.asked == []
    assert REFUSED in turn.told


@pytest.mark.asyncio
async def test_deleting_the_chat_ends_what_it_was_allowed(owner):
    from personalclaw import secret_file_grants
    from personalclaw.dashboard.chat_forget import delete_chats

    turn = await _chat_turn(owner)
    real = os.path.realpath(owner.env)
    assert secret_file_grants.holds("dashboard:shop", real, "read")

    turn.state.sessions.destroy = AsyncMock()
    turn.state.push_refresh = MagicMock()
    await delete_chats(turn.state, ["shop"], by="dashboard")

    assert not secret_file_grants.holds("dashboard:shop", real, "read")

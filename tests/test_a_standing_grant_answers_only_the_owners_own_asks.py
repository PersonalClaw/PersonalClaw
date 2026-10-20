"""A standing grant of the owner's answers only the calls she asked for herself.

A shared channel thread can hold the owner and a colleague she lets talk to the agent. The owner
trusts the conversation (its Trust, YOLO, Trust reads, an agent's "Always allow"), so its calls run
without asking her. Those switches are hers, for what she asks: in a turn her colleague asked for,
none of them answers a call. The call is asked of her instead, on her own surfaces, and the card
names who asked; the audit log says so too. What a call's tool declares (a read) and an operator's
own hook pattern answer as they always have, and so does everything she asks for herself.

Driven as the turn drives it, on each runtime: the real gateway over a scratch home, the door a
channel's message crosses, the real turn engine on a native runtime whose session manager hands
it the approval policy the turn engine decides; the gate an agent CLI's permission requests are
put to; and the decision a channel that runs a conversation itself asks at each call.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from test_a_turn_someone_else_started_changes_no_memory_on_its_own import (
    _AUTH_SHORTCUTS,
    CHANNEL,
    COLLEAGUE,
    OWNER,
    PROVIDER,
    _Agent,
    _settled,
    _share_the_channel,
)

from personalclaw import approval_answer, approval_grants
from personalclaw import channel_inbound as ci
from personalclaw import session_restrictions, trust_mode
from personalclaw.channel_transports.base import ChannelMessage
from personalclaw.config.credentials import owner_id_credential
from personalclaw.config.loader import CRED_OWNER_ID
from personalclaw.history import ConversationLog
from personalclaw.turn_source import arrived_on

#: What the colleague's turn, and then hers, asks the agent to write.
NOTES = "Offsite: the venue opens at nine, so the first session starts at half past.\n"
#: How the card names the colleague, as the channel's trust list holds him.
JONAS = "Jonas (U0JONASCOL) on teamchat"
#: The line the card shows for a call he asked for.
ASKED_FOR = (
    f"{JONAS} asked for this, not you. Your Trust, Trust reads, YOLO and an agent's Always allow "
    "answer only what you ask for, so this call waits for your answer."
)
#: The thread the conversation runs on.
THREAD = "1790800000.000100"


@pytest.fixture(autouse=True)
def _a_shared_channel(unset_env):
    unset_env(CRED_OWNER_ID, owner_id_credential(PROVIDER))
    _share_the_channel()
    yield
    trust_mode.disable_yolo()
    for key in list(session_restrictions._asked_by):
        session_restrictions.clear(key)


@dataclass
class _Gateway:
    """The gateway, its home, the agent's model, and the door a channel's messages cross."""

    home: Path
    state: Any
    agent: _Agent
    sent: int = 0

    @property
    def dashboard_state(self) -> Any:
        return self.state

    @property
    def notes(self) -> Path:
        return self.home / "workspace" / "notes.md"

    def send(self, sender: str, text: str) -> "asyncio.Task[None]":
        """*sender* writes *text* in the thread; the door hands it to the thread's chat, whose
        turn runs on the real turn engine. The task ends when that turn has ended."""
        from personalclaw.dashboard.chat_runner import run_chat

        self.sent += 1
        msg = ChannelMessage(
            channel_id=CHANNEL,
            text=text,
            sender=sender,
            thread_id=THREAD,
            message_id=f"m-{self.sent}",
        )

        async def _deliver() -> None:
            verdict = await ci.deliver_inbound(
                self, PROVIDER, msg, is_dm=False, turn_runner=run_chat
            )
            assert verdict.allowed and not verdict.fenced_text, verdict
            await _settled(self.state)

        return asyncio.ensure_future(_deliver())

    async def say(self, sender: str, text: str) -> None:
        await self.send(sender, text)

    def chat(self) -> Any:
        session = self.state.get_linked_session(THREAD)
        assert session is not None, "the door linked no chat to the thread"
        return session

    def asks(self, tool: str) -> list[dict]:
        """The approvals waiting for the owner that ask about *tool*."""
        return [e for e in self.state._pending_approvals.values() if e.get("tool") == tool]

    async def asked(self, turn: "asyncio.Task[None]", tool: str) -> dict | None:
        """The approval *turn* waits on for a call to *tool*; None when the turn ended first."""
        for _ in range(500):
            found = self.asks(tool)
            if found:
                return found[0]
            if turn.done():
                return None
            await asyncio.sleep(0.02)
        raise AssertionError(f"the turn neither asked about {tool} nor ended")


@contextlib.asynccontextmanager
async def _gateway(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, tools: tuple[Any, ...] = ()
) -> AsyncIterator[_Gateway]:
    """The real gateway over a scratch home, whose chats' turns run on a native runtime with the
    file tools over the home's workspace (and *tools*). Its session manager hands the runtime the
    approval policy the turn engine sets for the chat, as the real one does, so the chat's Trust
    reaches the runtime only through the turn engine's decision."""
    from personalclaw.agents.native.builtin_tools import NativeBuiltinToolProvider
    from personalclaw.agents.native.runtime import NativeAgentRuntime
    from personalclaw.agents.provider import AgentRuntimeDefinition
    from personalclaw.context import ContextBuilder
    from personalclaw.context_engine import set_engine
    from personalclaw.memory import MemoryStore
    from personalclaw.skills import SkillsLoader

    home = tmp_path / "data"
    user_home = tmp_path / "user"
    user_home.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    monkeypatch.setenv("HOME", str(user_home))
    for name in _AUTH_SHORTCUTS:
        monkeypatch.delenv(name, raising=False)
    _share_the_channel()
    workspace = home / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    # No backups or history of the scratch home: their threads would outlive the test.
    (home / "config.json").write_text(
        json.dumps({"durability": {"auto_backup": False, "time_travel": False}}), encoding="utf-8"
    )

    agent = _Agent()
    runtime = NativeAgentRuntime(
        definition=AgentRuntimeDefinition(name="PersonalClaw", provider="native", model="scripted"),
        model_provider=agent,
        tool_providers=[NativeBuiltinToolProvider(workspace, sandbox_mode="off"), *tools],
        cwd=workspace,
    )
    await runtime.start()
    policy = {"now": ""}

    def _set_policy(_key: str, value: str) -> None:
        policy["now"] = value
        runtime.set_approval_policy(value)

    async def _runtime_for(key: str, *_a: Any, **_kw: Any):
        runtime.set_session_key(key)
        return runtime, True, False

    from chat_test_helpers import links_kept_in_a_session_map

    sessions = MagicMock(count=0)
    sessions._sessions = {}
    sessions.get_pid = MagicMock(return_value=None)
    # The thread stays linked to its chat, as the gateway keeps the link.
    links_kept_in_a_session_map(sessions)
    sessions.get_or_create = AsyncMock(side_effect=_runtime_for)
    sessions.record_failure = AsyncMock()
    sessions.set_approval_policy = MagicMock(side_effect=_set_policy)
    sessions.get_approval_policy = MagicMock(side_effect=lambda _key: policy["now"])

    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>spa</html>", encoding="utf-8")
    import personalclaw.dashboard.handlers.core as handlers_core_mod
    import personalclaw.dashboard.server as server_mod

    monkeypatch.setattr(server_mod, "_DIST_DIR", dist)
    monkeypatch.setattr(handlers_core_mod, "_DIST_DIR", dist)
    log = ConversationLog(base_dir=home / "sessions")
    runner, state = await server_mod.start_dashboard(
        sessions=sessions, port=0, conversation_log=log
    )
    monkeypatch.setenv("PERSONALCLAW_PORT", str(runner.addresses[0][1]))
    markdown = MemoryStore(workspace=workspace)
    markdown.init()
    state.context_builder = ContextBuilder(
        memory=markdown,
        skills=SkillsLoader(skills_path=home / "skills", install_builtins=False),
        conversation_log=log,
    )
    # The operator's lifecycle hooks: none are set up, so none blocks a call.
    hooks = MagicMock()
    hooks.fire_for_ids = AsyncMock(return_value=[])
    state._hook_store = hooks
    set_engine(None)
    try:
        yield _Gateway(home=home, state=state, agent=agent)
    finally:
        for approval_id in list(state._pending_approvals):
            state.cancel_approval(approval_id, reason="the test ended")
        await _settled(state)
        await runner.cleanup()


async def _a_trusted_conversation(gw: _Gateway) -> Any:
    """She opens the conversation in the thread and trusts it: its next calls run unasked."""
    await gw.say(OWNER, "Morning! I'll keep the offsite notes in this thread.")
    chat = gw.chat()
    chat._trust = True
    return chat


def _audit(operation: str) -> list[dict]:
    from personalclaw.sel import sel

    return [e for e in sel().recent(limit=500) if e.get("operation") == operation]


# ── the native runtime, through the door ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_colleagues_write_in_her_trusted_conversation_waits_for_her_and_names_him(
    tmp_path, monkeypatch
):
    """🔴 Red on integration: her Trust approved the colleague's write without asking her, and
    the file was written."""
    async with _gateway(tmp_path, monkeypatch) as gw:
        chat = await _a_trusted_conversation(gw)
        gw.agent.calls.append(("write_file", {"path": "notes.md", "content": NOTES}))
        turn = gw.send(COLLEAGUE, "Can you write the offsite notes file?")
        ask = await gw.asked(turn, "write_file")
        written_while_asking = gw.notes.exists()
        assert ask is not None, "the colleague's write ran without asking her"
        # Only a decision on it answers it: no switch of hers is an answer to his call.
        alone = gw.state.answered_alone(ask["id"])
        assert gw.state.resolve_approval(ask["id"], False, by=approval_answer.YOU)
        await turn
        rows = [m for m in chat.messages if m.get("role") == "permission"]

    assert not written_while_asking and not gw.notes.exists(), "nothing runs before her answer"
    # The card names who asked, on every surface that lists the approval, and says why it asks.
    assert ask["asked_for"] == ASKED_FOR, ask["asked_for"]
    assert alone
    assert any(json.loads(r.get("cls") or "{}").get("asked_for") == ASKED_FOR for r in rows), rows
    held = [e for e in _audit("approval.grant_held") if e.get("outcome") == "needs_human"]
    assert held and JONAS in json.dumps(held[-1]), held


@pytest.mark.asyncio
async def test_her_own_write_in_the_same_conversation_still_runs_unasked(tmp_path, monkeypatch):
    """The control: her own message, through the same door into the same trusted chat."""
    async with _gateway(tmp_path, monkeypatch) as gw:
        await _a_trusted_conversation(gw)
        gw.agent.calls.append(("write_file", {"path": "notes.md", "content": NOTES}))
        await gw.say(OWNER, "Write the offsite notes file, please.")
        asked = gw.asks("write_file")

    assert gw.notes.read_text(encoding="utf-8") == NOTES
    assert asked == []


@pytest.mark.asyncio
async def test_a_colleagues_turn_under_her_yolo_is_asked_too(tmp_path, monkeypatch):
    """🔴 Red on integration: YOLO is hers as well; it approved the colleague's write."""
    async with _gateway(tmp_path, monkeypatch) as gw:
        await gw.say(OWNER, "Morning! I'll keep the offsite notes in this thread.")
        trust_mode.enable_yolo(ttl_secs=600)
        gw.agent.calls.append(("write_file", {"path": "notes.md", "content": NOTES}))
        turn = gw.send(COLLEAGUE, "Can you write the offsite notes file?")
        ask = await gw.asked(turn, "write_file")
        assert ask is not None, "YOLO ran the colleague's write without asking her"
        assert gw.state.resolve_approval(ask["id"], False, by=approval_answer.YOU)
        await turn

    assert not gw.notes.exists()


@pytest.mark.asyncio
async def test_switching_trust_on_while_his_call_waits_leaves_it_waiting(tmp_path, monkeypatch):
    """🔴 Red on integration: a Trust switch answers every pending call it covers, and it
    answered his. Now it leaves his call waiting for her answer."""
    from aiohttp.test_utils import TestClient, TestServer
    from chat_test_helpers import _api_app

    from personalclaw.dashboard.chat import api_chat_mode

    async with _gateway(tmp_path, monkeypatch) as gw:
        await gw.say(OWNER, "Morning! I'll keep the offsite notes in this thread.")
        chat = gw.chat()
        gw.agent.calls.append(("write_file", {"path": "notes.md", "content": NOTES}))
        turn = gw.send(COLLEAGUE, "Can you write the offsite notes file?")
        ask = await gw.asked(turn, "write_file")
        assert ask is not None
        app = _api_app(gw.state)
        app.router.add_post("/api/chat/mode", api_chat_mode)
        async with TestClient(TestServer(app)) as client:
            switched = await client.post(
                "/api/chat/mode", json={"mode": "trust", "session": chat.key}
            )
            assert switched.status == 200, await switched.text()
        await asyncio.sleep(0.05)
        still = [a["id"] for a in gw.asks("write_file")]
        for waiting in gw.asks("write_file"):
            assert gw.state.resolve_approval(waiting["id"], False, by=approval_answer.YOU)
        await turn

    assert still == [ask["id"]], "her switch answered his call"
    assert not gw.notes.exists()


# ── an agent CLI's calls, put to the turn engine's gate ──────────────────────────────────────


async def _cli_turn(tmp_path: Path, sender: str, title: str, args: dict, *, kind: str = "edit"):
    """One turn of a chat with Trust on whose agent CLI asks about one call of its own, the turn's
    message sent by *sender* in a thread of the channel. Returns the CLI, the entry the call was
    asked with (None when it was not asked), and who decided it, by its audit row."""
    from test_acp_permission_authority import _context_builder, _make_state, _session, _set_stream

    from personalclaw.dashboard.chat_runner import run_chat
    from personalclaw.llm.base import EVENT_COMPLETE, EVENT_PERMISSION_REQUEST, LLMEvent

    state, client = _make_state(tmp_path, context_builder=_context_builder())
    session = _session(trust=True)
    session.append("user", "hello", "msg msg-u", source=arrived_on(THREAD, sender, PROVIDER))
    _set_stream(
        client,
        [
            LLMEvent(
                kind=EVENT_PERMISSION_REQUEST,
                title=title,
                tool_kind=kind,
                request_id="req-1",
                tool_input=json.dumps(args),
            ),
            LLMEvent(kind=EVENT_COMPLETE, stop_reason="end_turn"),
        ],
    )
    seen: dict[str, Any] = {}

    async def _answer() -> None:
        for _ in range(100):
            fut = session._approval_futures.get("req-1")
            if fut is not None and not fut.done():
                seen["entry"] = next(iter(state._pending_approvals.values()), None)
                fut.set_result("rejected")
                return
            await asyncio.sleep(0.01)

    answering = asyncio.ensure_future(_answer())
    rows = MagicMock()
    with (
        patch("personalclaw.dashboard.chat_runner.sel", rows),
        patch("personalclaw.acp.ungated.sel", rows),
    ):
        await run_chat(state, session, "hello")
    answering.cancel()
    decided = [
        (c.kwargs.get("outcome"), (c.kwargs.get("metadata") or {}))
        for c in rows.return_value.log_tool_invocation.call_args_list
        if c.kwargs.get("request_id") == "req-1"
    ]
    return client, seen.get("entry"), decided


_WRITE = ("Write notes.md", {"file_path": "notes.md", "content": NOTES})


@pytest.mark.asyncio
async def test_an_agent_clis_write_in_a_colleagues_turn_is_asked_whatever_her_trust(tmp_path):
    """🔴 Red on integration: the chat's Trust approved the agent CLI's write in his turn."""
    client, entry, decided = await _cli_turn(tmp_path, COLLEAGUE, *_WRITE)

    assert entry is not None, "the call was approved without asking her"
    assert entry["asked_for"] == ASKED_FOR, entry
    client.approve_tool.assert_not_awaited()
    client.reject_tool.assert_awaited_once_with("req-1")
    # Her Deny, recorded as hers, on a row that says who asked for the turn.
    ((outcome, meta),) = decided
    assert outcome == "rejected" and meta.get("decided_by") == approval_grants.YOU, decided
    assert meta.get("asked_by") == JONAS, meta


@pytest.mark.asyncio
async def test_an_agent_clis_write_in_her_own_turn_runs_on_her_trust(tmp_path):
    """The control: in her own turn the chat's Trust approves the call, as it always has."""
    client, entry, decided = await _cli_turn(tmp_path, OWNER, *_WRITE)

    assert entry is None
    client.approve_tool.assert_awaited_once_with("req-1")
    ((outcome, meta),) = decided
    assert outcome == "auto_approved" and meta.get("decided_by") == approval_grants.TRUST


@pytest.mark.asyncio
async def test_a_declared_read_in_a_colleagues_turn_asks_nobody(tmp_path):
    """What a call's tool declares still answers it: a read asks nobody, whoever asked."""
    from test_acp_permission_authority import _context_builder, _make_state, _session, _set_stream

    from personalclaw.dashboard.chat_runner import run_chat
    from personalclaw.llm.base import EVENT_COMPLETE, EVENT_PERMISSION_REQUEST, LLMEvent

    state, client = _make_state(tmp_path, context_builder=_context_builder())
    session = _session(trust=False)
    session.append("user", "hello", "msg msg-u", source=arrived_on(THREAD, COLLEAGUE, PROVIDER))
    request = LLMEvent(
        kind=EVENT_PERMISSION_REQUEST,
        title="memory_list",
        request_id="req-1",
        tool_input="{}",
        risk_level="safe",
    )
    _set_stream(client, [request, LLMEvent(kind=EVENT_COMPLETE, stop_reason="end_turn")])
    with patch("personalclaw.dashboard.chat_runner.sel"), patch("personalclaw.acp.ungated.sel"):
        await run_chat(state, session, "hello")

    client.approve_tool.assert_awaited_once_with("req-1")
    assert state._pending_approvals == {}


# ── a conversation a channel runs itself ─────────────────────────────────────────────────────


@pytest.fixture
def channel_state(tmp_path, monkeypatch):
    """The gateway's dashboard state, as a channel's own turn reaches it: the channel linked the
    conversation to its thread, and the settings are a scratch home's."""
    from chat_test_helpers import _make_state, links_kept_in_a_session_map

    import personalclaw.config.loader as cfg
    import personalclaw.dashboard.state as st
    import personalclaw.session_workspace as ws
    from personalclaw.guardrails import ceiling as C
    from personalclaw.inbox_providers import native_source

    monkeypatch.setattr(cfg, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(st, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(ws, "config_dir", lambda: tmp_path)
    _share_the_channel()
    C.reset_ceiling()
    state = _make_state(tmp_path)
    state.push_sessions_update = MagicMock()
    links_kept_in_a_session_map(state.sessions).set_channel_link(THREAD, THREAD, CHANNEL)
    before = native_source.get_dashboard_state()
    native_source.set_dashboard_state(state)
    yield state
    native_source.set_dashboard_state(before)
    C.reset_ceiling()


def _call(*, tool: str = "write_file", **declared: Any) -> SimpleNamespace:
    """A call the channel's own turn asks about, as its runtime hands it over."""
    return SimpleNamespace(
        title=tool,
        tool_kind="",
        risk_level=declared.get("risk_level", ""),
        work_asks=False,
        tool_input=json.dumps({"path": "notes.md"}),
        tool_purpose="write the offsite notes",
        request_id="req-1",
        tool_meta={},
    )


def _in_the_thread(sender: str, call: SimpleNamespace) -> str:
    """Who approves *call* without asking, in a turn of the thread *sender*'s message started."""
    from personalclaw.sdk.channel import chat_grant, turn_asked_by

    with turn_asked_by(THREAD, arrived_on(THREAD, sender, PROVIDER)):
        return chat_grant(THREAD, call)


def test_a_channels_own_turn_a_colleague_asked_for_takes_none_of_her_switches(channel_state):
    """🔴 Red on integration: the chat's Trust, and then YOLO, approved his call."""
    from personalclaw.sdk.channel import answer_in_chat

    assert answer_in_chat(THREAD, "trust", channel=PROVIDER) is True
    assert _in_the_thread(COLLEAGUE, _call()) == ""
    assert _in_the_thread(OWNER, _call()) == approval_grants.TRUST, "the control: hers answers"

    trust_mode.enable_yolo(ttl_secs=600)
    assert _in_the_thread(COLLEAGUE, _call()) == ""
    assert _in_the_thread(OWNER, _call()) == approval_grants.YOLO


def test_a_channels_own_turn_keeps_the_operators_pattern_and_what_a_call_declares(
    channel_state, tmp_path
):
    """An operator's pattern and a declared read answer his call as they answer hers."""
    (tmp_path / "config.json").write_text(
        json.dumps({"hooks": {"auto_approve_tools": ["write_file"]}}), encoding="utf-8"
    )
    assert _in_the_thread(COLLEAGUE, _call()) == approval_grants.HOOK_PATTERN
    read = _call(tool="memory_list", risk_level="safe")
    assert _in_the_thread(COLLEAGUE, read) == approval_grants.DECLARED_READ


def test_a_channels_own_prompt_in_his_turn_offers_no_allow_for_this_chat(channel_state):
    """The prompt offers what the card offers: in his turn, her answer for this call alone, and
    the line naming who asked."""
    from personalclaw.channel_delivery import ALLOW_FOR_THIS_CHAT
    from personalclaw.sdk.channel import approval_brief_for, turn_asked_by

    with turn_asked_by(THREAD, arrived_on(THREAD, COLLEAGUE, PROVIDER)):
        theirs = approval_brief_for(_call(), chat=THREAD)
    with turn_asked_by(THREAD, arrived_on(THREAD, OWNER, PROVIDER)):
        hers = approval_brief_for(_call(), chat=THREAD)

    assert theirs is not None and hers is not None
    assert ALLOW_FOR_THIS_CHAT.key not in {a["key"] for a in theirs["answers"]}
    assert theirs["asked_for"] == ASKED_FOR
    assert ALLOW_FOR_THIS_CHAT.key in {a["key"] for a in hers["answers"]}
    assert "asked_for" not in hers


def test_a_conversation_an_app_started_is_unchanged(channel_state, tmp_path):
    """An app's conversation approves nothing on her switches, whoever asked, and the operator's
    pattern still answers there."""
    from personalclaw.sdk.channel import chat_grant

    chat = channel_state.get_or_create_session(THREAD)
    chat.created_by_app = "notes-app"
    chat._trust = True
    assert chat_grant(THREAD, _call()) == ""
    assert _in_the_thread(OWNER, _call()) == ""
    (tmp_path / "config.json").write_text(
        json.dumps({"hooks": {"auto_approve_tools": ["write_file"]}}), encoding="utf-8"
    )
    assert chat_grant(THREAD, _call()) == approval_grants.HOOK_PATTERN


# ── the work such a turn starts ──────────────────────────────────────────────────────────────

#: The chat a subagent works for, and the subagent.
HERS = "dashboard:chat-41-1790800100"
AGENT = "ab12cd34"


@contextlib.contextmanager
def _started_in_a_turn_of(sender: str):
    """The subagent :data:`AGENT` was started in a turn of :data:`HERS` that *sender*'s message
    started, as its start marks it (``memory_writes.hand_on``)."""
    from personalclaw import memory_writes
    from personalclaw.subagent import agent_work_id

    with memory_writes.derived_from(HERS):
        memory_writes.asked_for(arrived_on(THREAD, sender, PROVIDER) if sender != OWNER else {})
        memory_writes.hand_on(agent_work_id(AGENT), HERS)
    try:
        yield
    finally:
        session_restrictions.clear(agent_work_id(AGENT))


@pytest.mark.asyncio
@pytest.mark.parametrize("grant", ["yolo", "the chat's trust"])
async def test_a_subagent_his_turn_started_is_asked_whatever_her_grants(grant):
    """🔴 Red on integration: the relay every background agent's call goes through approved the
    call of a subagent his turn started, on her YOLO or her chat's Trust."""
    from test_an_apps_agent_is_held_to_its_tier import _relay

    from personalclaw.llm.base import EVENT_PERMISSION_REQUEST, LLMEvent
    from personalclaw.subagent import SubagentInfo, tool_approval_id

    for sender, asked in ((COLLEAGUE, True), (OWNER, False)):
        info = SubagentInfo(id=AGENT, task="tidy the notes", parent_session_key=HERS)
        gateway = _relay(info)
        if grant == "the chat's trust":
            chat = SimpleNamespace(_trust=True, running=False)
            gateway.dashboard_state._sessions = {"chat-41-1790800100": chat}
        call = LLMEvent(
            kind=EVENT_PERMISSION_REQUEST, request_id=tool_approval_id(AGENT, 1), title="Bash"
        )
        relay = gateway._interactive_approval(
            "subagent", session_resolver=lambda _rid: "chat-41-1790800100"
        )
        with (
            _started_in_a_turn_of(sender),
            patch("personalclaw.trust_mode.is_yolo_active", return_value=grant == "yolo"),
        ):
            decision = await relay(call, HERS)
        asks = gateway.dashboard_state.request_approval.await_args_list
        if asked:
            assert not decision, f"{grant} approved a call of the subagent his turn started"
            ((_, kwargs),) = [(a.args, a.kwargs) for a in asks]
            assert kwargs["asked_for"] == ASKED_FOR, kwargs
        else:
            assert decision, f"the control: {grant} approves her own subagent's call"
            assert asks == []


def test_a_subagent_his_turn_started_starts_and_runs_on_none_of_her_grants():
    """🔴 Red on integration: her chat's Trust started the subagent his turn asked for, and let it
    run its calls unasked. The operator's spawn setting and a run's own approval mode still do."""
    from personalclaw.subagent import SubagentInfo, SubagentManager

    sessions = MagicMock()
    sessions.get_approval_policy = MagicMock(return_value="auto")
    builder = MagicMock()
    builder.hooks = SimpleNamespace(
        auto_approve_subagent_spawn=False, auto_approve_subagent_tools=False
    )
    manager = SubagentManager(sessions, builder, is_yolo=lambda: False)
    info = SubagentInfo(id=AGENT, task="tidy the notes", parent_session_key=HERS)

    with _started_in_a_turn_of(COLLEAGUE):
        assert manager._spawn_grant(info) == ""
        assert manager._standing_grant(info) == ""
        builder.hooks.auto_approve_subagent_spawn = True
        assert manager._spawn_grant(info) == approval_grants.HOOK_SETTING
        own = SubagentInfo(
            id=AGENT, task="tidy the notes", parent_session_key=HERS, approval_mode="auto"
        )
        assert manager._standing_grant(own) == approval_grants.APPROVAL_MODE
    builder.hooks.auto_approve_subagent_spawn = False
    with _started_in_a_turn_of(OWNER):
        assert manager._spawn_grant(info) == approval_grants.PARENT_TRUST
        assert manager._standing_grant(info) == approval_grants.PARENT_TRUST


def test_a_loops_own_grant_answers_its_cycles_and_her_switches_do_not(channel_state):
    """A loop's Unattended Mode is the loop's own grant, given when its start was allowed: its
    cycles run on it whoever asked for the loop. Her agent's "Always allow" seeded into one of its
    sessions is hers, and answers none of the cycles of a loop he asked for."""
    from personalclaw import memory_writes

    worker = channel_state.get_or_create_session("loop-5e1d0c47")
    worker._trust, worker._unattended = True, True
    floor = channel_state.get_or_create_session("loop-5e1d0c48")
    floor._trust, floor._unattended, floor._trust_from_floor = True, False, "auto"

    with memory_writes.derived_from("dashboard:loop-5e1d0c47"):
        memory_writes.asked_for(arrived_on(THREAD, COLLEAGUE, PROVIDER))
        assert channel_state.standing_grant(worker) == approval_grants.LOOP_MODE
        assert channel_state.chat_policy(worker) == "auto"
    with memory_writes.derived_from("dashboard:loop-5e1d0c48"):
        memory_writes.asked_for(arrived_on(THREAD, COLLEAGUE, PROVIDER))
        assert channel_state.standing_grant(floor) == approval_grants.TRUST
        assert channel_state.chat_policy(floor) == ""
    assert channel_state.chat_policy(floor) == "auto", "the control: in her own work it answers"


def test_the_report_of_his_subagent_takes_only_the_operators_hooks():
    """🔴 Red on integration: the turn that announces a subagent's report in a channel thread
    approved its own calls, the report of one his turn started included."""
    from personalclaw.gateway import injection_approval_policy
    from personalclaw.llm_helpers import ToolApprovalPolicy
    from personalclaw.sdk.channel import turn_asked_by

    with turn_asked_by(THREAD, arrived_on(THREAD, COLLEAGUE, PROVIDER)):
        theirs = injection_approval_policy(THREAD)
    with turn_asked_by(THREAD, arrived_on(THREAD, OWNER, PROVIDER)):
        hers = injection_approval_policy(THREAD)

    assert hers is ToolApprovalPolicy.AUTO_APPROVE
    assert theirs is not ToolApprovalPolicy.AUTO_APPROVE

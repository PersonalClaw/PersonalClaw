"""An unattended turn on an agent CLI asks PersonalClaw about each call, unless its loop says not.

Driven through the real chat runner, session manager, provider bridge and ACP client, against
``scripted_acp_agent.py``'s ``writes-a-file`` scenario over a real pipe: the agent asks to write a
file in its folder, and writes it only when the call is allowed, or at once, asking nothing, when
it was told a mode that lets it approve its own calls. The record file says which mode reached the
agent and whether it wrote; the file itself says whether anything was written.

* An unattended turn tells the CLI its asking mode, so its write reaches PersonalClaw's gate. A
  write no grant covers is refused at once, with the reason an unattended run is given, and
  nothing is written.
* The same turn with a grant covering the write runs it: the call is asked about and the grant
  answers it.
* A loop whose owner let its agent CLI approve its own calls runs as an unattended turn used to:
  the CLI is told its self-approving mode and writes without asking, and the owner's choice and
  the mode it gave are both in the audit log.
* A chat someone is watching is unchanged: the CLI is told its asking mode, and the call waits for
  her answer.
* A read-only ``personalclaw run`` turn on an agent CLI holds: the write is refused by its
  read-only mode, before anything could approve it.
* A chore built on the agent CLI (a chat's title, its follow-ups) runs none of the calls the CLI
  asks about: a chore answers in text.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest
from scripted_acp_agent import DID_NOT_WRITE, FILE_TEXT, WROTE
from test_dashboard_approval import _context_builder, _make_hook_store

from personalclaw.approval_answer import YOU
from personalclaw.config.loader import AppConfig
from personalclaw.dashboard.chat import run_chat
from personalclaw.dashboard.state import DashboardState, _ChatSession
from personalclaw.history import ConversationLog
from personalclaw.hooks import HookManager, HooksConfig
from personalclaw.loop import posture, store
from personalclaw.loop.loop import Loop
from personalclaw.providers.provider_bridge import create_provider_factory
from personalclaw.sel import sel
from personalclaw.session import SessionManager

AGENT = Path(__file__).with_name("scripted_acp_agent.py")
#: The agent CLI the loop and the chats run on: one whose not-gateable residual PersonalClaw
#: declares (`acp.permission_authority.NOT_GATEABLE`).
CLI = "acp:claude-code"
#: The scripted agent's permission request id (``scripted_acp_agent.PERMISSION_ID``).
ASKED = "900"


def _cli_entry(record: Path, target: Path):
    """The agent CLI's registry entry, launching the scripted agent with its record and its file."""
    from personalclaw.llm.acp_agent import ACP_AGENT_CAPABILITY
    from personalclaw.llm.registry import ProviderEntry

    return ProviderEntry(
        name=CLI,
        type="acp_agent",
        model="",
        options={
            "command": [
                sys.executable,
                str(AGENT),
                "writes-a-file",
                str(record),
                "spec",
                str(target),
            ],
            "dialect": "claude-code",
        },
        credential=None,
        declared_capabilities=ACP_AGENT_CAPABILITY.capabilities,
    )


@pytest.fixture
def world(tmp_path, monkeypatch):
    """The agent CLI registered as the runtime it is, launching the scripted agent; a folder the
    work happens in; and a record of what reached the agent."""
    from personalclaw.llm.registry import get_default_registry

    # The handshake's settle wait for MCP start-up notices: this agent sends none.
    monkeypatch.setattr("personalclaw.acp.client._DRAIN_DURATION", 0.05)
    work = tmp_path / "pantry"
    work.mkdir()
    record = tmp_path / "wire.jsonl"
    target = work / "pantry.md"
    registry = get_default_registry()
    registry.unregister_entry(CLI)
    registry.register_entry(_cli_entry(record, target))
    w = _World(work, record, target)
    yield w
    registry.unregister_entry(CLI)


class _World:
    def __init__(self, work: Path, record: Path, target: Path) -> None:
        self.work, self.record, self.target = work, record, target
        self.sessions = SessionManager(AppConfig(), provider_factory=create_provider_factory())
        self.state = DashboardState(
            sessions=self.sessions,
            start_time=0.0,
            conversation_log=ConversationLog(base_dir=work.parent / "history"),
        )
        # The context builder's model side is a stand-in; its hook chain is the real one, so the
        # deny-list and the owner-only screen read every call the agent asks about.
        self.state.context_builder = _context_builder()
        self.state.context_builder.hooks = HookManager(HooksConfig())
        self.state._hook_store = _make_hook_store()
        self.state.broadcast_ws = lambda kind, data=None: None
        self.state.push_sessions_update = lambda *a, **k: None

    def session(self, key: str) -> _ChatSession:
        session = _ChatSession(key, workspace_dir=str(self.work))
        session.acp_provider = CLI
        self.state._sessions[session.key] = session
        return session

    def loop(self, *, attended: bool = False) -> Loop:
        return store.create(
            Loop(
                id="",
                name="Pantry",
                kind="goal",
                task="Keep the pantry list up to date.",
                attended=attended,
                provider=CLI,
                workspace_dir=str(self.work),
            )
        )

    def loop_session(self, loop: Loop) -> _ChatSession:
        session = self.session(f"loop-{loop.id}")
        session._app = "loop"
        posture.arm(session, posture.of(loop))
        return session

    def wire(self, kind: str) -> list[dict]:
        if not self.record.exists():
            return []
        rows = [json.loads(line) for line in self.record.read_text().splitlines() if line]
        return [r for r in rows if r["kind"] == kind]

    def modes_told(self) -> list[str]:
        """The permission modes the client put on the wire, in order."""
        return [
            r["params"]["value"]
            for r in self.wire("received")
            if r["method"] == "session/set_config_option" and r["params"].get("configId") == "mode"
        ]

    async def turn(self, session: _ChatSession, message: str = "Save the pantry list.") -> None:
        await asyncio.wait_for(run_chat(self.state, session, message), timeout=30)

    async def close(self) -> None:
        await self.sessions.close_all()


def _said(session: _ChatSession, role: str) -> list[str]:
    return [m["content"] for m in session.messages if m.get("role") == role]


def _decided(rows: list[dict], outcome: str) -> list[dict]:
    """The audit rows of the agent's asked call that decided it as ``outcome``."""
    return [
        r
        for r in rows
        if r.get("event_type") == "tool_invocation"
        and r.get("outcome") == outcome
        and str(r.get("operation", "")).startswith("Write pantry.md")
    ]


@pytest.mark.asyncio
async def test_an_unattended_write_no_grant_covers_is_refused_and_nothing_is_written(world):
    """An Unattended loop whose run's grant has ended: nothing approves the write, and nobody is
    there to. 🔴 Before: the CLI was told ``bypassPermissions``, approved its own write without
    asking, and the file was written."""
    loop = world.loop()
    session = world.loop_session(loop)
    session._trust = False  # its trust window ended (`loop.manager.end_unattended_grant`)
    try:
        await world.turn(session)
    finally:
        await world.close()

    assert world.modes_told() == ["default"], "the CLI was told a mode that skips its asks"
    assert [a["outcome"] for a in world.wire("permission_answer")] == ["selected"]
    assert world.wire("wrote") == [] and not world.target.exists(), "the refused write ran"
    assert any(DID_NOT_WRITE in a for a in _said(session, "assistant")), session.messages
    [refused] = _decided(sel().recent(200), "denied")
    assert refused["metadata"]["reason"] == "unattended_fail_fast"
    assert refused["metadata"]["decided_by"] == "unattended_no_one_to_ask"


@pytest.mark.asyncio
async def test_the_same_write_runs_when_the_runs_grant_covers_it(world):
    """An Unattended loop's own grant (the Mode it runs under) answers the write the CLI asked
    about. 🔴 Before: the CLI approved its own write, so no grant was ever asked."""
    from personalclaw import approval_grants

    loop = world.loop()
    session = world.loop_session(loop)
    assert session._trust is True, "the loop armed its standing grant"
    try:
        await world.turn(session)
    finally:
        await world.close()

    assert world.modes_told() == ["default"]
    assert [a["option"] for a in world.wire("permission_answer")] == ["allow_once"]
    assert world.target.read_text(encoding="utf-8") == FILE_TEXT
    assert any(WROTE in a for a in _said(session, "assistant")), session.messages
    [approved] = _decided(sel().recent(200), "auto_approved")
    assert approved["metadata"]["decided_by"] == approval_grants.LOOP_MODE


@pytest.mark.asyncio
async def test_a_loop_allowed_to_approve_its_own_calls_runs_as_before_and_says_so(world):
    """Her choice for this one loop: the CLI is told its self-approving mode, asks nothing, and
    writes. The choice and the mode it gave are each an audit row."""
    from personalclaw import agent_cli_self_approval

    loop = world.loop()
    agent_cli_self_approval.set_for_loop(loop, True, caller="dashboard")
    session = world.loop_session(loop)
    try:
        await world.turn(session)
    finally:
        await world.close()

    assert world.modes_told() == ["bypassPermissions"]
    assert (
        world.wire("permission_answer") == []
    ), "the CLI asked, as it would have without the choice"
    assert world.target.read_text(encoding="utf-8") == FILE_TEXT
    rows = sel().recent(200)
    [chosen] = [r for r in rows if r.get("operation") == "loop.agent_cli_self_approval"]
    assert chosen["outcome"] == "enabled" and loop.id in chosen["resources"]
    [mode] = [r for r in rows if r.get("operation") == "mode_change:self_approval_allowed"]
    assert mode["outcome"] == "allowed" and "bypassPermissions" in mode["resources"]


@pytest.mark.asyncio
async def test_the_choice_is_read_at_each_turn(world):
    """Switched off between two turns, the next turn's CLI is told to ask again."""
    from personalclaw import agent_cli_self_approval

    loop = world.loop()
    agent_cli_self_approval.set_for_loop(loop, True, caller="dashboard")
    session = world.loop_session(loop)
    try:
        await world.turn(session)
        world.target.unlink()
        agent_cli_self_approval.set_for_loop(loop, False, caller="dashboard")
        session._trust = False
        await world.turn(session)
    finally:
        await world.close()

    assert world.modes_told() == ["bypassPermissions", "default"]
    assert not world.target.exists(), "the turn after she switched it off wrote without asking"


@pytest.mark.asyncio
async def test_her_choice_ends_with_the_loops_own_grant(world):
    """Her choice holds while the loop's own grant does. Once its trust window ended, a Trust of
    another kind seeded at a later turn (an agent's "Always allow") lets the CLI approve none of
    its calls: it is told to ask, and its write is asked about."""
    from personalclaw import agent_cli_self_approval

    loop = world.loop()
    agent_cli_self_approval.set_for_loop(loop, True, caller="dashboard")
    session = world.loop_session(loop)
    # Its trust window ended (`loop.manager.end_unattended_grant`), and the agent's "Always allow"
    # seeded the session's Trust again at a later turn (`chat_runner._apply_approval_floor`).
    session._trust = True
    session._trust_from_floor = "auto"
    try:
        await world.turn(session)
    finally:
        await world.close()

    assert world.modes_told() == ["default"], "a Trust the loop did not give let its CLI approve"
    assert [a["outcome"] for a in world.wire("permission_answer")] == ["selected"], "it never asked"


@pytest.mark.asyncio
async def test_a_watched_chat_still_asks_her(world):
    """The floor: a chat she is in tells the CLI its asking mode, and the write waits for her."""
    session = world.session("chat-1-pantry")
    try:
        task = asyncio.ensure_future(world.turn(session))
        for _ in range(1000):
            if ASKED in session._approval_futures:
                break
            await asyncio.sleep(0.01)
        assert ASKED in session._approval_futures, f"the write never asked her: {session.messages}"
        assert not world.target.exists()
        world.state.decide_session_approval(session, ASKED, "approved", by=YOU)
        await task
    finally:
        await world.close()

    assert world.modes_told() == ["default"]
    assert world.target.read_text(encoding="utf-8") == FILE_TEXT


@pytest.mark.asyncio
async def test_a_read_only_run_on_an_agent_cli_refuses_the_write(world):
    """``personalclaw run`` without ``--allow``: its read-only mode refuses the write the CLI asks
    about. 🔴 Before: the CLI approved its own calls on this turn, which is why ``run`` refused a
    read-only turn on an agent CLI outright."""
    from personalclaw.cli_run import session_key_for, task_mode_for

    session = world.session(session_key_for("pantry"))
    session._task_mode = task_mode_for(False)
    try:
        await world.turn(session)
    finally:
        await world.close()

    assert world.modes_told() == ["default"]
    assert world.wire("wrote") == [] and not world.target.exists()
    [refused] = _decided(sel().recent(200), "denied")
    assert refused["metadata"]["reason"] == "task_mode:ask"


@pytest.mark.asyncio
async def test_a_chore_on_the_agent_cli_runs_none_of_the_calls_it_asks_about(tmp_path, monkeypatch):
    """A chore (a chat's title, its follow-ups) answers in text, so it runs no tools. With nothing
    bound and the agent CLI the one provider there is, the chore is built on that CLI
    (``one_shot_completion``'s last resort), and its prompt quotes the chat. The write the CLI asks
    about is refused, and nothing is written. 🔴 Before: the chore approved every call its model
    asked about, so the file was written, after a read-only run had refused that very write."""
    from personalclaw import chores
    from personalclaw.llm import acp_agent
    from personalclaw.llm.registry import ProviderRegistry, ProviderResolutionError

    monkeypatch.setattr("personalclaw.acp.client._DRAIN_DURATION", 0.05)
    record = tmp_path / "wire.jsonl"
    target = tmp_path / "pantry.md"
    registry = ProviderRegistry()
    registry.register_type(acp_agent.ACP_AGENT_CAPABILITY, acp_agent._factory)
    registry.register_entry(_cli_entry(record, target))
    monkeypatch.setattr("personalclaw.llm.registry.get_default_registry", lambda: registry)
    monkeypatch.setattr("personalclaw.providers.use_cases.resolution_chain", lambda uc: [])

    def _nothing_bound(use_case: str, **_kw):
        raise ProviderResolutionError("no model answers the background axis")

    monkeypatch.setattr(
        "personalclaw.providers.provider_bridge.resolve_provider_for_use_case", _nothing_bound
    )

    answer = await asyncio.wait_for(
        chores.run_chore("user: Save the pantry list.", usage=chores.chore_usage()), timeout=30
    )

    rows = [json.loads(line) for line in record.read_text().splitlines() if line]
    assert [r["option"] for r in rows if r["kind"] == "permission_answer"] == ["reject_once"]
    assert [r for r in rows if r["kind"] == "wrote"] == [] and not target.exists()
    assert answer == DID_NOT_WRITE
    [refused] = _decided(sel().recent(200), "rejected")
    assert refused["metadata"]["decided_by"] == "reject_all_policy"

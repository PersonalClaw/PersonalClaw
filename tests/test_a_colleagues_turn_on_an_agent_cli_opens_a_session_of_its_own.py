"""A colleague's turn in a chat an agent CLI answers is sent to a CLI session of its own.

A chat that runs on an agent CLI keeps its conversation in the CLI's own session: every earlier
turn as its model got it (the owner's turn message, built with her context, which can carry her
memory), the calls those turns made and what they read, and the turns a helper's report started.
A colleague's turn in a shared thread whose chat runs there was sent to that same session, so it
was answered from all of it, and nothing PersonalClaw does can take a turn out of what a CLI holds.

Now a turn someone other than the owner asked for is sent to a CLI session opened for it alone,
which resumes nothing, and is handed the conversation as the channel showed it
(``chat_persistence.channel_view``), as the built-in runtime's turn is. Her next turn goes back to
her own session.

Driven as the turn drives it: the real gateway over a scratch home, the door a channel's message
crosses, the real turn engine, the real session manager and the real ACP client, against
``scripted_acp_agent.py`` over stdio, which records what it was sent and by which process.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import sys
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from scripted_acp_agent import OWN_SESSIONS, PLAIN_ANSWER, own_session_id
from test_a_colleagues_turn_sees_what_the_channel_showed import (  # noqa: F401 — autouse
    _the_thread_is_shown_its_answers,
)
from test_a_standing_grant_answers_only_the_owners_own_asks import (  # noqa: F401 — autouse
    _a_shared_channel,
    _Gateway,
)
from test_a_turn_someone_else_asked_for_reads_none_of_your_memory import FACT, _her_memory
from test_a_turn_someone_else_started_changes_no_memory_on_its_own import (
    _AUTH_SHORTCUTS,
    COLLEAGUE,
    OWNER,
    _settled,
    _share_the_channel,
)

from personalclaw.history import ConversationLog

AGENT = Path(__file__).with_name("scripted_acp_agent.py")
#: The agent CLI every chat of the gateway runs on.
CLI = "acp:claude-code"
#: What a helper's report to her held.
REPORT = "Helper report: the landlord's number is 555-0142"


@dataclass
class _Prompt:
    """One ``session/prompt`` the agent CLI was sent: the process it went to, the session it
    named, and everything it carried."""

    pid: int
    session: str
    text: str


class _Wire:
    """What the scripted agent CLI recorded of what it was sent."""

    def __init__(self, record: Path) -> None:
        self.record = record

    def frames(self) -> list[dict]:
        if not self.record.exists():
            return []
        return [json.loads(line) for line in self.record.read_text().splitlines() if line]

    def received(self, method: str) -> list[dict]:
        return [f for f in self.frames() if f["kind"] == "received" and f["method"] == method]

    def prompts(self) -> list[_Prompt]:
        return [
            _Prompt(f["pid"], f["params"]["sessionId"], json.dumps(f["params"]["prompt"]))
            for f in self.received("session/prompt")
        ]

    def opened_by(self, pid: int) -> list[tuple[str, str]]:
        """How process *pid* opened its session: ``("session/new", "")`` or the id it loaded."""
        return [
            (f["method"], str(f["params"].get("sessionId") or ""))
            for f in self.frames()
            if f["kind"] == "received"
            and f["pid"] == pid
            and f["method"] in ("session/new", "session/load")
        ]


def _factory(record: Path, folder: Path, built: list[Any]) -> Any:
    """The session manager's factory: every chat's runtime is the scripted agent CLI, a process
    per runtime, each naming the session it opens for itself."""
    from personalclaw.llm.acp_agent import AcpAgentProvider

    def factory(session_key: str | None = None, **_kwargs: Any) -> Any:
        if not str(session_key or "").startswith("dashboard:"):
            raise RuntimeError(f"{session_key!r} does not run on the scripted agent")
        provider = AcpAgentProvider(
            command=[sys.executable, str(AGENT), OWN_SESSIONS, str(record), "spec"],
            cwd=folder,
            dialect="claude-code",
            runtime_id=CLI,
            session_key=session_key,
        )
        built.append(provider)
        return provider

    return factory


@contextlib.asynccontextmanager
async def _cli_gateway(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[tuple[_Gateway, _Wire]]:
    """The real gateway over a scratch home, set to run its chats on an agent CLI, whose session
    manager builds each runtime on the scripted one."""
    import personalclaw.dashboard.handlers.core as handlers_core_mod
    import personalclaw.dashboard.server as server_mod
    from personalclaw.config.loader import AppConfig
    from personalclaw.context import ContextBuilder
    from personalclaw.context_engine import set_engine
    from personalclaw.memory import MemoryStore
    from personalclaw.session import SessionManager
    from personalclaw.skills import SkillsLoader

    # The handshake's settle wait for MCP start-up notices: this agent sends none.
    monkeypatch.setattr("personalclaw.acp.client._DRAIN_DURATION", 0.05)
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
    # No backups or history of the scratch home: their threads would outlive the test. The
    # default agent runs on the agent CLI, as Settings sets an agent's runtime.
    (home / "config.json").write_text(
        json.dumps(
            {
                "durability": {"auto_backup": False, "time_travel": False},
                "agents": {"PersonalClaw": {"provider": CLI}},
            }
        ),
        encoding="utf-8",
    )

    wire = _Wire(tmp_path / "wire.jsonl")
    built: list[Any] = []
    sessions = SessionManager(
        AppConfig.load(), provider_factory=_factory(wire.record, workspace, built)
    )
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>spa</html>", encoding="utf-8")
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
        yield _Gateway(home=home, state=state, agent=None), wire  # type: ignore[arg-type]
    finally:
        for approval_id in list(state._pending_approvals):
            state.cancel_approval(approval_id, reason="the test ended")
        await _settled(state)
        await runner.cleanup()
        await sessions.close_all()
        for provider in built:
            await provider.shutdown()


@pytest.mark.asyncio
async def test_a_colleagues_turn_is_sent_to_a_cli_session_of_its_own(tmp_path, monkeypatch):
    """🔴 Red before: his turn was sent to the CLI session that holds hers."""
    from personalclaw.dashboard.chat_utils import _history_key_for

    async with _cli_gateway(tmp_path, monkeypatch) as (gw, wire):
        _her_memory(gw)
        await gw.say(OWNER, "Morning! I'll keep the offsite notes in this thread.")
        await gw.say(COLLEAGUE, "What's the plan for the offsite?")
        await gw.say(OWNER, "Thanks, both.")
        kept = gw.state.sessions._session_map.get(_history_key_for(gw.chat().key))

        hers, his, hers_next = wire.prompts()
        assert FACT in hers.text, "the control: her turn is built with her saved fact"
        assert hers.session == own_session_id(hers.pid)
        assert his.session != hers.session and his.pid != hers.pid
        assert wire.opened_by(his.pid) == [("session/new", "")], "his session resumed one"
        # Her next turn goes back to her own session, which is still the one kept for the chat.
        assert hers_next.session == hers.session
        assert wire.opened_by(hers_next.pid) == [("session/load", hers.session)]
        assert kept == hers.session


@pytest.mark.asyncio
async def test_a_colleagues_cli_session_is_handed_what_the_channel_showed(tmp_path, monkeypatch):
    """His session is handed the conversation as the channel showed it, with a stub for the turn
    a helper's report started, and none of her turn's context. 🔴 Red before: his turn was sent
    to her session, which held all of it, and handed nothing of the conversation."""
    from personalclaw.dashboard.chat_runner import run_chat
    from personalclaw.dashboard.state import SUBAGENT_COMPLETION_PREFIX

    async with _cli_gateway(tmp_path, monkeypatch) as (gw, wire):
        _her_memory(gw)
        await gw.say(OWNER, "Morning! I'll keep the offsite notes in this thread.")
        chat = gw.chat()
        delivered = f"{SUBAGENT_COMPLETION_PREFIX}\n{REPORT}"
        chat.append("subagent", delivered)
        await run_chat(gw.state, chat, delivered)
        await _settled(gw.state)
        await gw.say(COLLEAGUE, "What did the helper find?")

        hers, report, his = wire.prompts()
        assert "555-0142" in report.text and report.session == hers.session, "the control"
        assert "keep the offsite notes in this thread" in his.text and PLAIN_ANSWER in his.text
        assert "A helper's report arrived here" in his.text
        assert "555-0142" not in his.text
        assert FACT not in his.text


@pytest.mark.asyncio
async def test_two_turns_acquiring_at_once_never_share_a_runtime_one_is_apart_on(
    tmp_path, monkeypatch
):
    """Her turn and one someone else asked for acquire the chat's runtime at once: each gets one
    built for it. 🔴 Red before: the one whose runtime was built second was handed the other's."""
    from personalclaw.config.loader import AppConfig
    from personalclaw.session import SessionManager

    monkeypatch.setattr("personalclaw.acp.client._DRAIN_DURATION", 0.05)
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path / "data"))
    built: list[Any] = []
    wire = _Wire(tmp_path / "wire.jsonl")
    sessions = SessionManager(AppConfig(), provider_factory=_factory(wire.record, tmp_path, built))
    key = "dashboard:chat-at-once-1"
    hers = asyncio.ensure_future(sessions.get_or_create(key))
    his = asyncio.ensure_future(sessions.get_or_create(key, asked_apart=True))
    try:
        done, _ = await asyncio.wait({hers, his}, return_when=asyncio.FIRST_COMPLETED)
        first = done.pop()
        sessions.release(key)
        second = his if first is hers else hers
        await asyncio.wait_for(second, timeout=30)
        sessions.release(key)
        assert len(built) == 3, "the runtime built second was not rebuilt"
        assert hers.result()[0] is not his.result()[0]
    finally:
        for turn in (hers, his):
            turn.cancel()
        await sessions.close_all()
        for provider in built:
            await provider.shutdown()

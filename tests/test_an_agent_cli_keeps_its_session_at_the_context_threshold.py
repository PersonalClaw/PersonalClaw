"""An agent CLI keeps its session at the context threshold when it compacts itself, and a restart
says it is one.

Driven through the real chat runner, the real session manager and the real ACP client, against
``scripted_acp_agent.py`` over stdio. The scripted CLI reports its context the way core reads it
(``contextUsagePercentage``): 95% after every answer, over the 90% Settings hold by default.

Measured before this change: the session manager ended EVERY agent CLI's process at the
threshold and forgot the session it held, and the chat was told "Auto-compacted at 95% of the
context window." Nothing had compacted. The next turn started a second process on a new session,
so whatever the CLI held, the results of its earlier tool calls among it, was gone, and the module
said it sent ``/compact`` first, which nothing did.

Now:

* A CLI whose app says it compacts its own conversation
  (``register_acp_cli_entry(compacts_itself=True)``) runs past the threshold on the same process
  and the same session, and the chat is told nothing happened, because nothing did.
* A CLI nobody says that for is restarted, since nothing in PersonalClaw can compact a
  conversation the CLI holds. The chat says it was restarted, at what use and why, and the next
  turn starts a fresh session that the chat's own history restores.
* A native chat is unchanged: its own loop compacts it, and a loop whose passes stopped freeing
  room is restarted, saying that.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest
from scripted_acp_agent import FIRST_REVIEW, FULL_CONTEXT_PCT, LATER_REVIEW, SESSION_ID
from test_dashboard_approval import _context_builder, _make_hook_store, _make_session

from personalclaw.config.loader import AppConfig
from personalclaw.dashboard.chat import run_chat
from personalclaw.dashboard.state import DashboardState
from personalclaw.history import ConversationLog
from personalclaw.llm.registry import get_default_registry
from personalclaw.session import SessionManager

AGENT = Path(__file__).with_name("scripted_acp_agent.py")
CHAT = "chat-acp-1"
KEY = f"dashboard:{CHAT}"
FIRST_ASK = "review the last commit"
NEXT_ASK = "and what should I check next?"
#: What the chat is told when its session is restarted at the threshold, for an agent CLI.
RESTARTED = (
    f"Restarted the agent's session at {FULL_CONTEXT_PCT:.0f}% of its context window: "
    "PersonalClaw cannot compact this agent's context. It continues from what was said here, "
    "without the results of its earlier tool calls."
)


def _builder():
    """The context builder, assembling what this test reads the way the real one does: a fresh
    runtime's message carries the chat's restored history ahead of the request."""
    builder = _context_builder()

    def build(text, is_new_session, **kwargs):
        history = kwargs.get("compressed_history") or ""
        return (f"{history}\n\n{text}" if history else text), None

    builder.build_message.side_effect = build
    return builder


class _World:
    """One chat on one agent CLI, registered the way its app registers it, with the records a
    test reads."""

    def __init__(self, tmp_path: Path, *, cli: str, compacts_itself: bool):
        from personalclaw.acp_bundles._register import unregister_acp_cli_entry
        from personalclaw.llm.acp_agent import _factory
        from personalclaw.sdk.acp import register_acp_cli_entry

        self.record = tmp_path / f"{cli}-wire.jsonl"
        self.built: list = []
        declared = {"compacts_itself": True} if compacts_itself else {}
        entry = register_acp_cli_entry(
            cli=cli,
            dialect="default",
            command=[sys.executable, str(AGENT), "fills-context", str(self.record), "spec"],
            **declared,
        )
        # Kept out of the shared registry: with no model bound, PersonalClaw's own one-off calls
        # (a chat's title, its follow-ups) fall back to the first entry there, and they would
        # prompt this CLI too.
        unregister_acp_cli_entry(cli)
        assert entry is not None
        work = tmp_path / "work"

        def factory(session_key=None, **_kwargs):
            if not str(session_key or "").startswith("dashboard:"):
                # The gateway's own background work (a chat's title) never runs on the CLI.
                raise RuntimeError(f"{session_key!r} does not run on the scripted agent")
            # Built from the entry by the factory the registry hands every ``acp:<cli>`` entry.
            provider = _factory(entry=entry, session_key=session_key, cwd=str(work))
            self.built.append(provider)
            return provider

        self.sessions = SessionManager(AppConfig(), provider_factory=factory)
        self.state = DashboardState(
            sessions=self.sessions,
            start_time=0.0,
            conversation_log=ConversationLog(base_dir=tmp_path / "history"),
        )
        self.state.context_builder = _builder()
        self.state._hook_store = _make_hook_store()
        self.frames: list[tuple[str, dict]] = []
        self.state.broadcast_ws = lambda kind, data=None: self.frames.append((kind, data or {}))
        self.state.push_sessions_update = lambda *a, **k: None
        self.state.wire_session_restart_callback()
        self.session = _make_session(CHAT)
        self.session.title = "Review the last commit"
        self.state._sessions[self.session.key] = self.session

    async def turn(self, message: str) -> None:
        """One turn to its end, and what the session manager did once it was over."""
        assert self.session.enqueue_or_run_prompt(message, run_chat, self.state)
        await asyncio.wait_for(asyncio.shield(self.session.task), timeout=30)
        await asyncio.gather(*list(self.sessions._background_tasks), return_exceptions=True)

    def wire(self, kind: str) -> list[dict]:
        rows = [json.loads(line) for line in self.record.read_text().splitlines() if line]
        return [r for r in rows if r["kind"] == kind]

    def requests(self) -> list[tuple[int, str]]:
        """Every request the CLI received, with the process that received it, in order."""
        return [(r["pid"], r["method"]) for r in self.wire("received") if r["method"]]

    def prompts(self) -> list[tuple[int, str]]:
        return [
            (r["pid"], "".join(b.get("text", "") for b in r["params"].get("prompt", [])))
            for r in self.wire("received")
            if r["method"] == "session/prompt"
        ]

    def said(self) -> list[str]:
        return [m["content"] for m in self.session.messages if m.get("role") == "assistant"]

    def session_lines(self) -> list[str]:
        """The activity line each turn opens with: whether its runtime was created, continued,
        resumed or restored from the chat's history."""
        return [
            d.get("text", "")
            for kind, d in self.frames
            if kind == "activity_event" and d.get("kind") == "session"
        ]

    async def close(self) -> None:
        await self.sessions.close_all()
        for provider in self.built:
            await provider.shutdown()


@pytest.fixture
def make_world(tmp_path, monkeypatch):
    # The handshake's settle wait for MCP start-up notices: this agent sends none.
    monkeypatch.setattr("personalclaw.acp.client._DRAIN_DURATION", 0.05)

    def make(*, cli: str, compacts_itself: bool) -> _World:
        return _World(tmp_path, cli=cli, compacts_itself=compacts_itself)

    return make


@pytest.mark.asyncio
async def test_a_cli_that_compacts_itself_runs_past_the_threshold_on_the_same_session(make_world):
    """🔴 Red before: its process was ended after the first answer and the second turn started a
    second one, on a new session, beside "Auto-compacted at 95% of the context window."."""
    w = make_world(cli="scripted-compacting", compacts_itself=True)
    try:
        await w.turn(FIRST_ASK)
        assert w.sessions.has_session(KEY), "the CLI was ended at the threshold"
        assert w.built[0].is_process_alive(), "the CLI's process was ended"
        assert w.sessions._session_map.get(KEY) == SESSION_ID, "its session was forgotten"

        await w.turn(NEXT_ASK)

        spawned = [r["pid"] for r in w.wire("spawn")]
        assert len(spawned) == 1, "a second CLI process was started for the same chat"
        assert w.requests() == [
            (spawned[0], "initialize"),
            (spawned[0], "session/new"),
            (spawned[0], "session/prompt"),
            (spawned[0], "session/prompt"),
        ]
        assert [text for _, text in w.prompts()] == [FIRST_ASK, NEXT_ASK]
        assert w.said() == [FIRST_REVIEW, LATER_REVIEW], "the chat was told something happened"
        assert w.session_lines()[-1].startswith("Session continued")
    finally:
        await w.close()


@pytest.mark.asyncio
async def test_a_cli_that_cannot_compact_is_restarted_says_so_and_keeps_the_conversation(
    make_world,
):
    """🔴 Red before: the chat said "Auto-compacted at 95% of the context window." for a restart
    that compacted nothing and dropped everything the CLI held."""
    w = make_world(cli="scripted-plain", compacts_itself=False)
    try:
        await w.turn(FIRST_ASK)

        assert not w.built[0].is_process_alive(), "control: the CLI was not restarted"
        assert w.said() == [FIRST_REVIEW, RESTARTED]
        assert not any("compacted" in text.lower() for text in w.said())
        # Its session is never loaded back: it holds the full context the restart leaves.
        assert w.sessions._session_map.get(KEY) is None

        await w.turn(NEXT_ASK)

        first, second = [r["pid"] for r in w.wire("spawn")]
        assert first != second
        assert [method for pid, method in w.requests() if pid == second] == [
            "initialize",
            "session/new",
            "session/prompt",
        ], "the restarted CLI was handed the session it replaced"
        # The fresh session starts from the chat's own history: what was asked and answered.
        [(pid, restored)] = [p for p in w.prompts() if p[0] == second]
        assert FIRST_ASK in restored and FIRST_REVIEW in restored, restored
        assert restored.endswith(NEXT_ASK)
        assert w.session_lines()[-1].startswith("Session restored from history")
    finally:
        await w.close()


def test_the_declaration_reaches_every_door_a_session_is_opened_by(monkeypatch):
    """The app's declaration is read from its entry on both doors: the runtime factory, and a
    pooled connection's session (``AcpSessionProvider``), which answers as the factory's does."""
    from personalclaw.acp_bundles._register import unregister_acp_cli_entry
    from personalclaw.llm.acp_session_provider import AcpSessionProvider
    from personalclaw.sdk.acp import register_acp_cli_entry

    for cli, declared in (("scripted-says", True), ("scripted-silent", False)):
        kwargs = {"compacts_itself": True} if declared else {}
        entry = register_acp_cli_entry(
            cli=cli, dialect="default", command=["/nonexistent/scripted-cli", "acp"], **kwargs
        )
        try:
            assert entry is not None
            assert ("compacts_itself" in entry.options) is declared
            built = get_default_registry().build(f"acp:{cli}", session_key=KEY)
            assert built.compacts_automatically is declared
        finally:
            unregister_acp_cli_entry(cli)
        connection, session = object(), object()  # never read for the declaration
        pooled = AcpSessionProvider(
            connection, session, runtime_id=f"acp:{cli}", compacts_itself=declared
        )
        assert pooled.compacts_automatically is declared


@pytest.mark.asyncio
async def test_a_native_chat_is_left_to_its_own_compaction_and_a_restart_says_why(tmp_path):
    """The native loop is unchanged: over the threshold it compacts its own history and the
    session manager leaves it alone; once two passes in a row freed under a tenth, it is
    restarted. Red before: that restart, too, was announced as "Auto-compacted"."""
    from test_one_compaction_threshold import _convo, _Model, _runtime

    runtime = _runtime(_Model(92.0))
    sessions = SessionManager(AppConfig(), provider_factory=lambda *a, **k: runtime)
    state = DashboardState(
        sessions=sessions,
        start_time=0.0,
        conversation_log=ConversationLog(base_dir=tmp_path / "history"),
    )
    state.broadcast_ws = lambda kind, data=None: None
    state.wire_session_restart_callback()
    chat = state.get_or_create_session("chat-native-1")
    key = f"dashboard:{chat.key}"
    try:
        await sessions.get_or_create(key)
        sessions.release(key)
        runtime._messages = _convo()
        runtime._last_context_pct = 92.0

        sessions.check_context_usage(key, runtime)
        await asyncio.gather(*list(sessions._background_tasks), return_exceptions=True)
        assert sessions.has_session(key), "a loop that still compacts itself was restarted"
        assert not chat.messages

        runtime._compaction_saves = [0.02, 0.03]
        sessions.check_context_usage(key, runtime)
        await asyncio.gather(*list(sessions._background_tasks), return_exceptions=True)
        assert not sessions.has_session(key)
        assert [m["content"] for m in chat.messages] == [
            "Restarted the agent's session at 92% of its context window: compacting its "
            "context no longer frees enough room. It continues from what was said here, "
            "without the results of its earlier tool calls."
        ]
    finally:
        await sessions.close_all()

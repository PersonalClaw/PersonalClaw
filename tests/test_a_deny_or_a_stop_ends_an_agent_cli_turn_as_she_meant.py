"""A Deny lets an agent CLI go on, and a Stop ends its turn as stopped — said in those words.

Driven through the real chat runner, the real session manager and the real ACP client, against
``scripted_acp_agent.py``: a process that speaks the protocol over stdio, so every frame crosses a
real pipe and the record file says what reached the agent.

* An agent can offer two ways to refuse a call: one that declines it and lets the agent carry on,
  and one that ends the agent's turn. Both are kinded ``reject_once``, so only the id and the name
  tell them apart. Deny means decline, whatever order they arrive in.
* An agent that still ends its turn after a Deny is said to have stopped after the Deny. The
  generic "the reply stopped before it finished" notice blames a fault nobody had.
* A Stop pressed while an approval waits ends the turn as stopped: never as a timeout, never as an
  error PersonalClaw doesn't recognize. The runner that served the turn is kept for the next one,
  and no second one is started.
"""

from __future__ import annotations

import asyncio
import gc
import json
import sys
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request
from scripted_acp_agent import ANSWER_WITHOUT_THE_COMMAND
from test_dashboard_approval import _context_builder, _make_hook_store, _make_session

from personalclaw.approval_answer import YOU
from personalclaw.config.loader import AppConfig
from personalclaw.dashboard.chat import api_chat_session_stop, run_chat
from personalclaw.dashboard.chat_runner import TURN_CUT_SHORT_NOTICE, TURN_STOPPED
from personalclaw.dashboard.state import DashboardState
from personalclaw.history import ConversationLog
from personalclaw.llm.acp_agent import AcpAgentProvider
from personalclaw.session import SessionManager

AGENT = Path(__file__).with_name("scripted_acp_agent.py")
CHAT = "chat-acp-1"
#: The scripted agent's permission request id (``scripted_acp_agent.PERMISSION_ID``).
ASKED = "900"


class _World:
    """One chat on one agent CLI, with the records a test reads."""

    def __init__(
        self, tmp_path: Path, scenario: str, *, dialect: str, runtime: str, keys: str, budget: float
    ):
        self.record = tmp_path / f"{scenario}-wire.jsonl"
        self.built: list[AcpAgentProvider] = []

        def factory(session_key=None, **_kwargs):
            if not str(session_key or "").startswith("dashboard:"):
                # The gateway's own background work (a chat's title) never runs on the CLI.
                raise RuntimeError(f"{session_key!r} does not run on the scripted agent")
            provider = AcpAgentProvider(
                command=[sys.executable, str(AGENT), scenario, str(self.record), keys],
                cwd=tmp_path / "work",
                dialect=dialect,
                runtime_id=runtime,
                session_key=session_key,
            )
            self.built.append(provider)
            return provider

        cfg = AppConfig()
        cfg.agent.soft_stop_budget_secs = budget
        self.sessions = SessionManager(cfg, provider_factory=factory)
        self.state = DashboardState(
            sessions=self.sessions,
            start_time=0.0,
            conversation_log=ConversationLog(base_dir=tmp_path / "history"),
        )
        self.state.context_builder = _context_builder()
        self.state._hook_store = _make_hook_store()
        self.frames: list[tuple[str, dict]] = []
        self.state.broadcast_ws = lambda kind, data=None: self.frames.append((kind, data or {}))
        self.state.push_sessions_update = lambda *a, **k: None
        self.session = _make_session(CHAT)
        self.session.title = "Review the last commit"
        self.state._sessions[self.session.key] = self.session

    def wire(self, kind: str) -> list[dict]:
        if not self.record.exists():
            return []
        rows = [json.loads(line) for line in self.record.read_text().splitlines() if line]
        return [r for r in rows if r["kind"] == kind]

    def methods(self, method: str) -> list[dict]:
        return [r for r in self.wire("received") if r["method"] == method]

    def errors(self) -> list[str]:
        return [m["content"] for m in self.session.messages if m.get("role") == "error"]

    def answers(self) -> list[str]:
        return [m["content"] for m in self.session.messages if m.get("role") == "assistant"]

    def outcomes(self) -> list[str]:
        return [d.get("outcome", "") for kind, d in self.frames if kind == "chat_done"]

    def start(self, message: str = "review the last commit") -> asyncio.Task:
        assert self.session.enqueue_or_run_prompt(message, run_chat, self.state)
        return self.session.task

    async def asked(self) -> None:
        for _ in range(1000):
            if ASKED in self.session._approval_futures:
                return
            await asyncio.sleep(0.01)
        raise AssertionError(f"the chat never asked about the command: {self.session.messages}")

    async def close(self) -> None:
        task = self.session.task
        if task is not None and not task.done():
            task.cancel()
            try:
                await asyncio.wait_for(task, timeout=10)
            except (asyncio.CancelledError, Exception):  # noqa: BLE001 - teardown only
                pass
        await self.sessions.close_all()
        for provider in self.built:
            await provider.shutdown()


@pytest.fixture
def make_world(tmp_path, monkeypatch):
    # The handshake's settle wait for MCP start-up notices: this agent sends none.
    monkeypatch.setattr("personalclaw.acp.client._DRAIN_DURATION", 0.05)

    def make(
        scenario: str, *, dialect="codex", runtime="acp:codex", keys="spec", budget=5.0
    ) -> _World:
        return _World(
            tmp_path, scenario, dialect=dialect, runtime=runtime, keys=keys, budget=budget
        )

    return make


@pytest.fixture
def unretrieved():
    """What the event loop reports as a future whose exception nobody read. ``install`` runs
    inside the test, where the loop is."""
    seen: list[str] = []

    def install() -> None:
        running = asyncio.get_running_loop()
        previous = running.get_exception_handler()

        def handler(loop, context):
            seen.append(str(context.get("message", "")))
            if previous is not None:
                previous(loop, context)

        running.set_exception_handler(handler)

    return seen, install


async def _turn_ends(task: asyncio.Task) -> None:
    await asyncio.wait_for(asyncio.shield(task), timeout=20)


@pytest.mark.asyncio
async def test_a_deny_declines_the_call_and_the_agent_answers_without_it(make_world):
    """The turn-ending refusal is offered FIRST. Red before the fix: the first ``reject_once``
    was picked, the agent ended its turn, and the chat said the reply stopped before it finished.
    """
    w = make_world("deny-continues")
    try:
        task = w.start()
        await w.asked()
        w.state.decide_session_approval(w.session, ASKED, "rejected", by=YOU)
        await _turn_ends(task)

        assert [(a["outcome"], a["option"]) for a in w.wire("permission_answer")] == [
            ("selected", "decline")
        ]
        assert w.errors() == [], "a Deny the agent went on from ended in a notice"
        assert any(ANSWER_WITHOUT_THE_COMMAND in a for a in w.answers()), w.session.messages
        assert w.outcomes()[-1:] == ["complete"]
        refused = [
            m
            for m in w.session.messages
            if m.get("role") == "tool" and "(rejected)" in m.get("content", "")
        ]
        assert len(refused) == 1, w.session.messages
        # The turn's tool row records what PersonalClaw answered on her behalf.
        assert "No, continue without running it" in json.dumps(refused[0].get("meta") or {})
    finally:
        await w.close()


@pytest.mark.asyncio
async def test_an_agent_that_ends_its_turn_after_a_deny_is_said_to_have(make_world):
    """Its one refusal ends its turn. The chat says why the turn ended, in the agent's name and
    the tool's, and not the notice for a reply cut short by a fault."""
    w = make_world("deny-ends")
    try:
        task = w.start()
        await w.asked()
        w.state.decide_session_approval(w.session, ASKED, "rejected", by=YOU)
        await _turn_ends(task)

        assert [(a["outcome"], a["option"]) for a in w.wire("permission_answer")] == [
            ("selected", "no")
        ]
        assert w.errors() == ["Codex stopped after you denied Run command."]
        assert TURN_CUT_SHORT_NOTICE not in w.errors()
        assert w.outcomes()[-1:] == [TURN_STOPPED]
    finally:
        await w.close()


@pytest.mark.parametrize(
    ("dialect", "runtime", "keys"),
    [
        ("codex", "acp:codex", "spec"),
        ("claude-code", "acp:claude-code", "spec"),
        ("default", "acp:kiro-cli", "id-label"),
    ],
)
@pytest.mark.asyncio
async def test_a_stop_while_an_approval_waits_ends_the_turn_stopped(
    make_world, unretrieved, dialect, runtime, keys
):
    """Red before the fix: the turn's drain quit the moment the cancel went out, so the turn
    raised "ACP prompt timed out" and the chat showed an error PersonalClaw doesn't recognize;
    the agent's answer to the cancel was never awaited, so the stop counted as unacknowledged,
    the runner was killed, a replacement was spawned, and the killed turn's reply future was
    left for the event loop to report as never retrieved."""
    seen, install = unretrieved
    install()
    w = make_world("wait-for-stop", dialect=dialect, runtime=runtime, keys=keys)
    try:
        task = w.start("summarise my notes")
        await w.asked()

        app = web.Application()
        app["state"] = w.state
        request = make_mocked_request(
            "POST", f"/api/chat/sessions/{CHAT}/stop", match_info={"session": CHAT}, app=app
        )
        answer = json.loads((await api_chat_session_stop(request)).body)
        await _turn_ends(task)

        assert answer == {"ok": True, "stopped": True}
        assert w.outcomes()[-1:] == [TURN_STOPPED]
        assert w.errors() == [], "a Stop she pressed ended in an error notice"
        # The pending request was answered the way the protocol asks of a cancelled turn.
        assert [a["outcome"] for a in w.wire("permission_answer")] == ["cancelled"]
        # The agent acknowledged the cancel, so the runner that served the turn stays for the
        # next one, and nothing was started in its place.
        assert len(w.wire("spawn")) == 1, w.wire("spawn")
        assert len(w.built) == 1
        assert w.built[0].is_process_alive()
        await asyncio.sleep(0.3)  # anything spawned "eagerly" would have started by now
        assert len(w.wire("spawn")) == 1
        # Nothing resumed the stopped session or asked the agent anything more on her behalf.
        assert w.methods("session/load") == [] and len(w.methods("session/prompt")) == 1
    finally:
        await w.close()
    gc.collect()
    await asyncio.sleep(0)
    assert not [m for m in seen if "never retrieved" in m], seen


@pytest.mark.asyncio
async def test_a_stop_the_agent_ignores_kills_it_and_starts_nothing_in_its_place(
    make_world, unretrieved
):
    """The agent never answers the cancel, so the Stop's wait runs out and the runner is killed.
    The turn still ends as stopped — not as a lost connection retried three times — and no
    runner is started for the chat until it has a turn. Red before the fix: a replacement was
    spawned straight away."""
    seen, install = unretrieved
    install()
    w = make_world("ignores-stop", budget=0.5)
    try:
        task = w.start("summarise my notes")
        await w.asked()

        app = web.Application()
        app["state"] = w.state
        request = make_mocked_request(
            "POST", f"/api/chat/sessions/{CHAT}/stop", match_info={"session": CHAT}, app=app
        )
        answer = json.loads((await api_chat_session_stop(request)).body)
        await _turn_ends(task)

        assert answer == {"ok": True, "stopped": True}
        assert w.outcomes()[-1:] == [TURN_STOPPED]
        assert w.errors() == [], "a Stop she pressed ended in an error notice"
        assert not w.session._queue, "the stopped message was queued to run again"
        await asyncio.sleep(0.3)  # anything spawned "eagerly" would have started by now
        assert len(w.wire("spawn")) == 1, w.wire("spawn")
        assert w.methods("session/load") == [], "the stopped session was resumed"
        assert not w.built[0].is_process_alive()
        assert not w.sessions.has_session(f"dashboard:{CHAT}")
    finally:
        await w.close()
    gc.collect()
    await asyncio.sleep(0)
    assert not [m for m in seen if "never retrieved" in m], seen

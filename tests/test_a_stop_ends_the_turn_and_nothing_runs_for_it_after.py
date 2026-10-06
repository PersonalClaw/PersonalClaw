"""Once she presses Stop, nothing more runs for the turn, and the Stop ends it, saying how.

Measured on a native install, on an agent CLI whose own settings let it run PersonalClaw's tools
without asking: she pressed Stop and the chat kept "Thinking…" for about three minutes while the
CLI called PersonalClaw's tools (a skill, the chat search, memory, the context, another helper) and
its own web search. Only a third Stop ended it, and each Stop's record in the chat still read
"stopping", with nothing said of how it ended. What made that, each closed here:

* The Stop ended the helper the turn had started (it was waiting for its start to be allowed), and
  the helper's ending was handed to the chat as a turn of its own, so the agent took the work up
  again at once. A second helper did the same after the second Stop. What a stopped turn started
  now hands its report to no turn.
* The tool server an agent CLI runs PersonalClaw's tools through ran every call it was sent,
  whatever had happened to the turn. A call made after the Stop is now answered as not made, and
  runs nothing.
* A Stop's record was closed only while the chat still read as stopping, and the stopped turn's own
  end set that back first, so no Stop the agent answered ever said how it ended.
* An approval the Stop ended was answered as her Deny when the Stop had work to end first, so an
  agent CLI whose refusal ends its turn was told to carry on without the call, after her Stop.
* A Stop pressed while the turn was being put together found nothing on the agent to stop, and the
  turn's prompt went out after it.

The agent CLI is ``scripted_acp_agent.py``: it speaks the protocol over stdio, and it calls
PersonalClaw's tools the way a CLI does, through the ``personalclaw mcp-core`` process the client
declares to it, started from that declaration. The first tests drive the real gateway
(``start_dashboard``, whose routes that process calls ask for a sign-in), the real session
manager, the real chat runner and the real ACP client; every Stop goes through the real stop
handler.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from aiohttp import web
from aiohttp.test_utils import make_mocked_request
from scripted_acp_agent import ANSWER_AFTER_CARRY_ON, NOTED_BEFORE, SAVED, TRIED_AFTER
from test_a_deny_or_a_stop_ends_an_agent_cli_turn_as_she_meant import (  # noqa: F401 - a fixture
    CHAT,
    _turn_ends,
    make_world,
)
from test_dashboard_approval import _context_builder, _make_hook_store

from personalclaw import started_work
from personalclaw.config.loader import AppConfig
from personalclaw.dashboard.chat import api_chat_session_stop, run_chat
from personalclaw.dashboard.chat_runner import TURN_STOPPED
from personalclaw.llm.acp_agent import AcpAgentProvider
from personalclaw.llm.base import EVENT_COMPLETE, EVENT_TEXT_CHUNK, LLMEvent
from personalclaw.session import SessionManager
from personalclaw.skills import ephemeral

AGENT = Path(__file__).with_name("scripted_acp_agent.py")
KEY = f"dashboard:{CHAT}"
#: How long a Stop waits for the agent to answer it before it ends the agent's process.
BUDGET = 3.0
#: How long, past that budget, a Stop may take to end the turn: the turn's read of the agent
#: wakes once a second, and ending a process tree takes a moment.
SLACK = 6.0

#: The agent CLIs' dialects, with the runtime each is served as and how its permission options
#: are keyed (the ACP specification's ``optionId``/``name``, or the older ``id``/``label``).
DIALECTS = pytest.mark.parametrize(
    ("dialect", "runtime", "keys"),
    [
        ("codex", "acp:codex", "spec"),
        ("claude-code", "acp:claude-code", "spec"),
        ("default", "acp:kiro-cli", "id-label"),
    ],
)

#: Every auth shortcut a test process might inherit: each would admit a call before the internal
#: credential is looked at.
_AUTH_SHORTCUTS = (
    "PERSONALCLAW_AUTH_MODE",
    "PERSONALCLAW_BYPASS_LOCAL_NETWORKS",
    "PERSONALCLAW_SESSION_KEY",
)


class _Chat:
    """One chat on the scripted agent CLI, on a running gateway, with the records a test reads."""

    def __init__(self, state: Any, sessions: SessionManager, record: Path, built: list) -> None:
        self.state = state
        self.sessions = sessions
        self.record = record
        self.built = built
        self.frames: list[tuple[str, dict]] = []
        state.broadcast_ws = lambda kind, data=None: self.frames.append((kind, data or {}))
        state.push_sessions_update = lambda *a, **k: None
        state.context_builder = _context_builder()
        state._hook_store = _make_hook_store()
        self.session = state.get_or_create_session(CHAT, memory_mode="persistent")
        self.session.title = "Review the last commit"

    def wire(self, kind: str) -> list[dict]:
        if not self.record.exists():
            return []
        rows = [json.loads(line) for line in self.record.read_text().splitlines() if line]
        return [r for r in rows if r["kind"] == kind]

    def prompts(self) -> list[dict]:
        return [r for r in self.wire("received") if r["method"] == "session/prompt"]

    def answers(self) -> dict[str, dict]:
        """What the tool server answered each call, by the note it tried to save."""
        return {r["title"]: r for r in self.wire("tool_answer")}

    def notes(self) -> set[str]:
        """The notes the agent's calls saved, as the store holds them."""
        return {draft.title for draft in ephemeral.list_drafts(KEY)}

    def outcomes(self) -> list[str]:
        return [d.get("outcome", "") for kind, d in self.frames if kind == "chat_done"]

    def errors(self) -> list[str]:
        return [m["content"] for m in self.session.messages if m.get("role") == "error"]

    def start(self) -> asyncio.Task:
        assert self.session.enqueue_or_run_prompt("review the last commit", run_chat, self.state)
        return self.session.task

    async def until(self, condition, what: str, within: float = 90.0) -> None:
        deadline = time.monotonic() + within
        while not condition():
            assert time.monotonic() < deadline, f"{what} never happened: {self.wire('tool_answer')}"
            await asyncio.sleep(0.05)

    async def stop(self) -> dict:
        app = web.Application()
        app["state"] = self.state
        request = make_mocked_request(
            "POST", f"/api/chat/sessions/{CHAT}/stop", match_info={"session": CHAT}, app=app
        )
        return json.loads((await api_chat_session_stop(request)).body)


def _stop_records(session: Any) -> list[dict]:
    """Each Stop's record in the chat (``stop_event``), as the chat keeps it."""
    records = []
    for message in session.messages:
        try:
            data = json.loads(message.get("cls") or "")
        except (TypeError, ValueError):
            continue
        if isinstance(data, dict) and data.get("kind") == "stop_event":
            records.append(data)
    return records


@pytest_asyncio.fixture
async def gateway_chat(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Start the real gateway, its home in a folder of its own and ``HOME`` elsewhere, whose chat
    :data:`CHAT` runs on the scripted agent CLI playing *scenario*; returns the chat."""
    home = tmp_path / "data"
    user_home = tmp_path / "user"
    user_home.mkdir()
    (tmp_path / "work").mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    monkeypatch.setenv("HOME", str(user_home))
    for name in _AUTH_SHORTCUTS:
        monkeypatch.delenv(name, raising=False)
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>spa</html>", encoding="utf-8")
    import personalclaw.dashboard.handlers.core as handlers_core_mod
    import personalclaw.dashboard.server as server_mod

    monkeypatch.setattr(server_mod, "_DIST_DIR", dist)
    monkeypatch.setattr(handlers_core_mod, "_DIST_DIR", dist)
    # The handshake's settle wait for MCP start-up notices: this agent sends none.
    monkeypatch.setattr("personalclaw.acp.client._DRAIN_DURATION", 0.05)
    running: list[tuple[Any, SessionManager, list]] = []

    async def start(scenario: str, *, dialect: str, runtime: str, keys: str) -> _Chat:
        record = tmp_path / f"{scenario}-wire.jsonl"
        built: list[AcpAgentProvider] = []

        def factory(session_key=None, **_kwargs):
            if not str(session_key or "").startswith("dashboard:"):
                raise RuntimeError(f"{session_key!r} does not run on the scripted agent")
            provider = AcpAgentProvider(
                command=[sys.executable, str(AGENT), scenario, str(record), keys],
                cwd=tmp_path / "work",
                dialect=dialect,
                runtime_id=runtime,
                session_key=session_key,
            )
            built.append(provider)
            return provider

        cfg = AppConfig()
        cfg.agent.soft_stop_budget_secs = BUDGET
        sessions = SessionManager(cfg, provider_factory=factory)
        runner, state = await server_mod.start_dashboard(sessions=sessions, port=0)
        running.append((runner, sessions, built))
        # Declared to the agent CLI's tool server, as the gateway declares the port it bound.
        monkeypatch.setenv("PERSONALCLAW_PORT", str(runner.addresses[0][1]))
        return _Chat(state, sessions, record, built)

    try:
        yield start
    finally:
        for runner, sessions, built in running:
            await sessions.close_all()
            for provider in built:
                await provider.shutdown()
            await runner.cleanup()


# ── an agent CLI that ignores the Stop ────────────────────────────────────────────────────────


@DIALECTS
@pytest.mark.asyncio
async def test_an_agent_that_ignores_the_stop_runs_none_of_personalclaws_tools_after_it(
    gateway_chat, dialect, runtime, keys
):
    """🔴 Red on integration: each call the agent made after the Stop ran, and saved its note.
    The Stop's record still read "stopping", with no outcome."""
    chat = await gateway_chat(
        "keeps-calling-after-stop", dialect=dialect, runtime=runtime, keys=keys
    )
    task = chat.start()
    await chat.until(lambda: NOTED_BEFORE in chat.answers(), "the first call")
    assert not chat.answers()[NOTED_BEFORE]["refused"], chat.answers()
    await chat.until(lambda: any(SAVED in m.get("content", "") for m in chat.session.messages), "")

    pressed = time.monotonic()
    answer = await chat.stop()
    await _turn_ends(task)
    took = time.monotonic() - pressed

    assert answer == {"ok": True, "stopped": True, "trust": False}
    # Every call it made after the Stop was answered as not made, saying why, and saved nothing.
    answers = chat.answers()
    assert set(answers) == {NOTED_BEFORE, *TRIED_AFTER}, answers
    for title in TRIED_AFTER:
        assert answers[title]["refused"], answers[title]
        assert "stopped" in answers[title]["text"], answers[title]
    assert chat.notes() == {NOTED_BEFORE}
    # It never answered the Stop, so within the Stop's budget its process was ended, and the turn
    # ended as stopped: no error, nothing started in its place, nothing asked of it again.
    assert took < BUDGET + SLACK, took
    assert chat.outcomes()[-1:] == [TURN_STOPPED]
    assert chat.errors() == []
    assert not chat.built[0].is_process_alive()
    await asyncio.sleep(0.5)
    assert len(chat.wire("spawn")) == 1 and len(chat.built) == 1
    assert len(chat.prompts()) == 1
    # The Stop's record says how it ended.
    [record] = _stop_records(chat.session)
    assert (record["state"], record["outcome"]) == ("stop_failed_reset", "hard"), record


@DIALECTS
@pytest.mark.asyncio
async def test_an_agent_that_answers_the_stop_ends_its_turn_soft_and_runs_nothing_after(
    gateway_chat, dialect, runtime, keys
):
    """🔴 Red on integration: the call it still made after answering the Stop ran and saved its
    note, and the Stop's record still read "stopping"."""
    chat = await gateway_chat("stops-then-calls", dialect=dialect, runtime=runtime, keys=keys)
    task = chat.start()
    await chat.until(lambda: NOTED_BEFORE in chat.answers(), "the first call")
    await chat.until(lambda: any(SAVED in m.get("content", "") for m in chat.session.messages), "")

    answer = await chat.stop()
    await _turn_ends(task)
    await chat.until(lambda: TRIED_AFTER[0] in chat.answers(), "the call after the Stop")

    assert answer == {"ok": True, "stopped": True, "trust": False}
    assert chat.answers()[TRIED_AFTER[0]]["refused"], chat.answers()
    assert chat.notes() == {NOTED_BEFORE}
    assert chat.outcomes()[-1:] == [TURN_STOPPED]
    assert chat.errors() == []
    # The agent answered the Stop, so the runner that served the turn stays for the next one.
    assert chat.built[0].is_process_alive()
    assert len(chat.wire("spawn")) == 1 and len(chat.prompts()) == 1
    [record] = _stop_records(chat.session)
    assert (record["state"], record["outcome"]) == ("stopped", "soft"), record


# ── every Stop says how it ended ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("scenario", "budget", "ending"),
    [
        ("wait-for-stop", 5.0, ("stopped", "soft")),
        ("ignores-stop", 0.5, ("stop_failed_reset", "hard")),
    ],
)
@pytest.mark.asyncio
async def test_every_stop_records_how_it_ended(
    make_world, scenario, budget, ending  # noqa: F811 - the imported fixture
):
    """A Stop the agent answers, and one it never answers, whose process is then ended. 🔴 Red on
    integration: both records still read "stopping", with no outcome, once the turn had ended."""
    w = make_world(scenario, budget=budget)
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

        assert answer["stopped"] is True
        [record] = _stop_records(w.session)
        assert (record["state"], record["outcome"]) == ending, record
        assert record.get("ts_end"), record
    finally:
        await w.close()


# ── what the Stop ends is never her Deny ──────────────────────────────────────────────────────


@DIALECTS
@pytest.mark.asyncio
async def test_a_stop_with_work_to_end_answers_the_waiting_approval_as_stopped_not_denied(
    make_world, dialect, runtime, keys  # noqa: F811 - the imported fixture
):
    """An agent whose one refusal ends its turn, and a Stop that has the turn's other work to end
    first (a helper's, which takes a moment). 🔴 Red on integration: the approval the Stop ended
    was answered with the agent's refusal, as her Deny is, the agent ended its turn, and the turn
    was carried on: a second prompt went out after her Stop and its answer was the turn's."""
    w = make_world("deny-only-cancel", dialect=dialect, runtime=runtime, keys=keys)

    async def ends_a_helper(_key: str) -> int:
        await asyncio.sleep(0.3)
        return 1

    w.sessions.register_child_stopper(ends_a_helper)
    try:
        task = w.start()
        await w.asked()
        app = web.Application()
        app["state"] = w.state
        request = make_mocked_request(
            "POST", f"/api/chat/sessions/{CHAT}/stop", match_info={"session": CHAT}, app=app
        )
        answer = json.loads((await api_chat_session_stop(request)).body)
        await _turn_ends(task)

        assert answer == {"ok": True, "stopped": True, "trust": False}
        assert [a["outcome"] for a in w.wire("permission_answer")] == ["cancelled"]
        assert len(w.prompts()) == 1, w.prompts()
        assert not any(ANSWER_AFTER_CARRY_ON in a for a in w.answers()), w.answers()
        assert w.outcomes()[-1:] == [TURN_STOPPED]
        [record] = _stop_records(w.session)
        assert (record["state"], record["outcome"]) == ("stopped", "soft"), record
    finally:
        await w.close()


# ── a Stop before the prompt goes out ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_stop_while_the_turn_is_put_together_ends_it_before_its_prompt(
    make_world,  # noqa: F811 - the imported fixture
):
    """🔴 Red on integration: the Stop found nothing on the agent to stop, the turn's prompt went
    out after it and the agent answered, and the Stop's record still read "stopping"."""
    w = make_world("answers")
    loop = asyncio.get_running_loop()
    assembling = asyncio.Event()
    go_on = asyncio.Event()
    built = w.state.context_builder.build_message

    def held(*args: Any, **kwargs: Any):
        # The turn is being put together, off the event loop, as its context is built.
        loop.call_soon_threadsafe(assembling.set)
        asyncio.run_coroutine_threadsafe(go_on.wait(), loop).result(timeout=30)
        return built(*args, **kwargs)

    w.state.context_builder.build_message = MagicMock(side_effect=held)
    try:
        task = w.start("summarise my notes")
        await asyncio.wait_for(assembling.wait(), timeout=30)
        app = web.Application()
        app["state"] = w.state
        request = make_mocked_request(
            "POST", f"/api/chat/sessions/{CHAT}/stop", match_info={"session": CHAT}, app=app
        )
        answer = json.loads((await api_chat_session_stop(request)).body)
        go_on.set()
        await _turn_ends(task)

        assert w.methods("session/prompt") == [], "the stopped turn's prompt went out"
        assert w.answers() == []
        assert w.outcomes()[-1:] == [TURN_STOPPED]
        assert answer["ok"] is True
        [record] = _stop_records(w.session)
        assert record["state"] == "stopped" and record["outcome"] == "idle", record
    finally:
        go_on.set()
        await w.close()


# ── what a stopped turn started hands its report to no turn ───────────────────────────────────


def _gateway(chat: Any) -> Any:
    """A gateway whose completion route delivers into the dashboard chat :data:`CHAT`."""
    from personalclaw.gateway import GatewayOrchestrator

    cfg = AppConfig()
    with patch.object(cfg, "load_credentials", return_value={}):
        orch = GatewayOrchestrator(cfg)
    orch.sessions = MagicMock()
    orch.ctx_builder = MagicMock()
    orch.dashboard_state = MagicMock(_sessions={}, _background_tasks=set())
    orch.dashboard_state.get_session = MagicMock(
        side_effect=lambda name: chat if name == CHAT else None
    )
    orch.dashboard_state.is_yolo_active.return_value = False
    with (
        patch("personalclaw.trust_mode.is_yolo_active", return_value=False),
        patch("personalclaw.gateway.SubagentManager") as manager,
    ):
        manager.return_value = MagicMock(
            running=[], running_agents_for=MagicMock(return_value=[]), get=MagicMock()
        )
        manager.return_value.get.return_value = None
        orch._init_subagents()
    return orch


def _helpers(release: asyncio.Event) -> MagicMock:
    """Sessions whose helper says what it found once *release* is set, and finishes."""
    sessions = MagicMock()
    sessions.get_pid = MagicMock(return_value=None)
    provider = AsyncMock()
    provider.context_usage_pct = lambda: 0.0

    async def _stream(*_a: Any, **_kw: Any):
        await release.wait()
        yield LLMEvent(kind=EVENT_TEXT_CHUNK, text="The retry library waits two seconds.")
        yield LLMEvent(kind=EVENT_COMPLETE)

    provider.stream = MagicMock(side_effect=lambda *a, **kw: _stream())
    sessions.get_or_create = AsyncMock(return_value=(provider, True, False))
    sessions.release = MagicMock()
    sessions.reset = AsyncMock()
    sessions.record_success = MagicMock()
    sessions.get_agent = MagicMock(return_value="")
    sessions.get_approval_policy = MagicMock(return_value="auto")
    return sessions


def _ctx() -> MagicMock:
    """A context builder whose hooks let a spawn start without asking."""
    ctx = MagicMock()
    ctx.build_message = MagicMock(return_value=("built_message", None))
    ctx.hooks.on_tool_call = MagicMock()
    ctx.hooks.auto_approve_subagent_spawn = True
    return ctx


class _Turn:
    """The chat's running turn, which ends when :meth:`end` is called."""

    def __init__(self) -> None:
        self.task = asyncio.get_running_loop().create_future()
        self.running = True
        self.title = "Retry libraries"
        self.queue_append = MagicMock()

    def end(self) -> None:
        self.running = False
        self.task.set_result(None)


async def _reports_after_the_stop(*, started_before_the_turn: bool, done_at_stop: bool) -> list:
    """A helper the chat started, a Stop of the chat's turn, and the turns its chat was then
    started with. The helper is still at work when the turn is stopped, or (*done_at_stop*) has
    finished just before, its report waiting for the turn to end; *started_before_the_turn* makes it
    an earlier turn's helper."""
    from personalclaw.subagent import SubagentManager

    chat = _Turn()
    orch = _gateway(chat)
    turns: list[str] = []

    async def _turn(_state: Any, _chat: Any, message: str, **_kw: Any) -> None:
        turns.append(message)

    release = asyncio.Event()
    with (
        patch("personalclaw.gateway.run_chat", new=AsyncMock(side_effect=_turn)),
        patch("personalclaw.subagent.Stats"),
        patch("personalclaw.subagent.sel"),
    ):
        manager = SubagentManager(
            sessions=_helpers(release),
            ctx_builder=_ctx(),
            on_done=orch._announce_subagents,
            delivery_coalesce_secs=0,
        )
        info = manager.spawn("Survey the retry libraries", parent_session_key=KEY)
        assert info is not None
        turn_began = info.started + (1.0 if started_before_the_turn else -1.0)
        if done_at_stop:
            release.set()
            await manager._tasks[info.id]
            # Its report is on its way: it waits for the chat's turn to end.
            await asyncio.sleep(0.1)
        await started_work.end_started(
            subagents=manager,
            supervisor=None,
            owns={CHAT, KEY}.__contains__,
            clause=started_work.TURN_STOPPED,
            since=turn_began,
        )
        await asyncio.sleep(0.1)
        chat.end()
        await manager.flush_deliveries()
        for _ in range(100):
            await asyncio.sleep(0.01)
    return turns


@pytest.mark.asyncio
async def test_a_helper_the_stopped_turn_ended_starts_no_turn_in_its_chat():
    """🔴 Red on integration: the helper the Stop ended was handed to the chat as a new turn, which
    told the agent its helper was "Cancelled because its chat turn was stopped", and the agent took
    the work up again."""
    assert await _reports_after_the_stop(started_before_the_turn=False, done_at_stop=False) == []


@pytest.mark.asyncio
async def test_a_helper_that_finished_as_its_turn_was_stopped_starts_no_turn_either():
    """Its report was waiting for the turn to end when she stopped it. 🔴 Red on integration: the
    turn ended and the report started the next one."""
    assert await _reports_after_the_stop(started_before_the_turn=False, done_at_stop=True) == []


@pytest.mark.asyncio
async def test_an_earlier_turns_helper_still_reports_to_its_chat():
    """What an earlier turn started is not the stopped turn's to end: its report reaches its
    chat."""
    turns = await _reports_after_the_stop(started_before_the_turn=True, done_at_stop=True)
    assert len(turns) == 1 and turns[0].startswith("[Subagent completion event]"), turns

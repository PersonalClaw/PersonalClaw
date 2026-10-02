"""A Deny lets an agent CLI go on, and a Stop ends its turn as stopped — said in those words.

Driven through the real chat runner, the real session manager and the real ACP client, against
``scripted_acp_agent.py``: a process that speaks the protocol over stdio, so every frame crosses a
real pipe and the record file says what reached the agent.

* An agent can offer two ways to refuse a call: one that declines it and lets the agent carry on,
  and one that ends the agent's turn. Both are kinded ``reject_once``, so only the id and the name
  tell them apart. Deny means decline, whatever order they arrive in.
* An agent can also offer ONLY the refusal that ends its turn (an escalation request does). The
  card says before the Deny that it ends the agent's turn, and when a refusal ends the turn the
  turn is carried on: the same session is told what was refused and asked to go on without it, the
  chat says so, and the agent's answer is the turn's. The audit row of the decision names every
  answer the agent offered.
* An agent whose turn cannot be carried on is said to have stopped after the Deny. The generic
  "the reply stopped before it finished" notice blames a fault nobody had.
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
from scripted_acp_agent import ANSWER_AFTER_CARRY_ON, ANSWER_WITHOUT_THE_COMMAND
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

    def notices(self) -> list[str]:
        return [m["content"] for m in self.session.messages if m.get("role") == "notice"]

    def prompts(self) -> list[str]:
        """The text of every prompt the agent received, in order."""
        return [
            "".join(block.get("text", "") for block in r["params"].get("prompt", []))
            for r in self.methods("session/prompt")
        ]

    def deny_effects(self) -> tuple[list[str], list[str]]:
        """What the card said a Deny does: on the live ``approval`` frame, and on the persisted
        permission row a reload draws the card from."""
        live = [d.get("deny_effect", "") for kind, d in self.frames if kind == "approval"]
        rows = [
            json.loads(m["cls"]).get("deny_effect", "")
            for m in self.session.messages
            if m.get("role") == "permission" and "resolved" not in json.loads(m["cls"])
        ]
        return live, rows

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
        # A refusal that lets the agent go on is offered, so a Deny is only a Deny: no warning.
        assert w.deny_effects() == ([""], [""])
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


#: What the card says before a Deny of a call the agent can refuse only by ending its turn.
DENY_ENDS_AND_CARRIES_ON = (
    "Codex offers no way to skip only this step: Deny ends its turn, and PersonalClaw then asks "
    "it to carry on without it."
)
#: What the chat says where a Deny ended the agent's turn and the turn was carried on.
CARRIED_ON = (
    "Codex ended its turn when you denied Run command, so PersonalClaw asked it to carry on "
    "without it."
)


@pytest.mark.asyncio
async def test_a_deny_of_an_escalation_is_said_first_and_the_turn_goes_on(make_world, monkeypatch):
    """The agent offers only its turn-ending refusal. Red before the fix: the card said nothing
    about it, the Deny ended the turn with "Codex stopped after you denied Run command." and no
    review, and nothing recorded what the agent had offered."""
    from unittest.mock import MagicMock

    audit = MagicMock()
    monkeypatch.setattr("personalclaw.dashboard.chat_runner.sel", lambda: audit)
    w = make_world("deny-only-cancel")
    try:
        task = w.start()
        await w.asked()
        assert w.deny_effects() == ([DENY_ENDS_AND_CARRIES_ON], [DENY_ENDS_AND_CARRIES_ON])
        w.state.decide_session_approval(w.session, ASKED, "rejected", by=YOU)
        await _turn_ends(task)

        assert [(a["outcome"], a["option"]) for a in w.wire("permission_answer")] == [
            ("selected", "cancel")
        ]
        # The same session was told what was refused and asked to go on without it.
        first, carry_on = w.prompts()
        assert "git show --stat HEAD" in carry_on and "was refused, so it did not run" in carry_on
        assert len(w.wire("spawn")) == 1 and len(w.methods("session/new")) == 1
        assert w.notices() == [CARRIED_ON]
        assert any(ANSWER_AFTER_CARRY_ON in a for a in w.answers()), w.session.messages
        assert w.errors() == [] and w.outcomes()[-1:] == ["complete"]
        # The decision's audit row names every answer the agent offered, and the one sent.
        (decided,) = [
            c.kwargs
            for c in audit.log_tool_invocation.call_args_list
            if c.kwargs.get("outcome") == "rejected"
        ]
        assert decided["metadata"]["answered"] == "cancel"
        assert decided["metadata"]["offered"] == [
            {"id": "accept", "kind": "allow_once", "name": "Yes, proceed"},
            {
                "id": "cancel",
                "kind": "reject_once",
                "name": "No, and tell me what to do differently",
            },
        ]
    finally:
        await w.close()


@pytest.mark.asyncio
async def test_an_agent_that_ends_its_turn_at_a_plain_decline_is_carried_on(make_world):
    """Its one refusal reads as a decline, so the card warns of nothing, but the agent ends its
    turn when it gets it. The turn is carried on all the same: a Deny means go on without it."""
    w = make_world("deny-ends")
    try:
        task = w.start()
        await w.asked()
        assert w.deny_effects() == ([""], [""])
        w.state.decide_session_approval(w.session, ASKED, "rejected", by=YOU)
        await _turn_ends(task)

        assert [(a["outcome"], a["option"]) for a in w.wire("permission_answer")] == [
            ("selected", "no")
        ]
        assert len(w.prompts()) == 2 and w.notices() == [CARRIED_ON]
        assert any(ANSWER_AFTER_CARRY_ON in a for a in w.answers()), w.session.messages
        assert w.errors() == [] and w.outcomes()[-1:] == ["complete"]
    finally:
        await w.close()


@pytest.mark.asyncio
async def test_an_agent_that_stops_again_when_carried_on_is_said_to_have_stopped(make_world):
    """Carried on, the agent ends that turn too and says nothing. The turn ends on why: it
    stopped after her Deny — not the notice for a reply a fault cut short — and is not resent."""
    w = make_world("deny-and-give-up")
    try:
        task = w.start()
        await w.asked()
        w.state.decide_session_approval(w.session, ASKED, "rejected", by=YOU)
        await _turn_ends(task)

        assert len(w.prompts()) == 2 and w.notices() == [CARRIED_ON]
        assert w.errors() == ["Codex stopped after you denied Run command."]
        assert TURN_CUT_SHORT_NOTICE not in w.errors()
        assert not w.session._queue, "the turn was queued to run again"
    finally:
        await w.close()


@pytest.mark.asyncio
async def test_a_turn_that_may_not_be_carried_on_again_says_it_stopped_after_the_deny(
    make_world, monkeypatch
):
    """Once a turn has been carried on as often as it may, a Deny that ends it ends it: the card
    says that much and no more, and the chat says why the turn ended, in the agent's name and
    the tool's, and not the notice for a reply cut short by a fault."""
    monkeypatch.setattr("personalclaw.acp.session._MAX_CARRY_ONS_PER_TURN", 0)
    w = make_world("deny-only-cancel")
    try:
        task = w.start()
        await w.asked()
        ends = "Codex offers no way to skip only this step: Deny ends its turn."
        assert w.deny_effects() == ([ends], [ends])
        w.state.decide_session_approval(w.session, ASKED, "rejected", by=YOU)
        await _turn_ends(task)

        assert len(w.prompts()) == 1 and w.notices() == []
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

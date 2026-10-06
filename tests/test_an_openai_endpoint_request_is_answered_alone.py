"""A request on the OpenAI-compatible endpoint is answered alone: from nothing another request
said, and with no answer another request is owed.

Measured on a running gateway, with a client that keeps no conversation (each of its requests
stands alone, so they all share the client's one session):

* Before each request the session's transcript was cleared and the id an agent CLI resumes its
  conversation from was purged, under a key the session manager never writes. The runtime the
  session manager held for the session was then reused as it stood, and a runtime keeps its own
  copy of every turn it has answered: the in-process loop its message history, an agent CLI its
  session. So the next request's model was handed the earlier request's words and its answer; and
  once the agent CLI's process had been reaped or the gateway restarted, the next one loaded the
  earlier conversation back by its id.
* Two requests at once on one session started two turns, and both readers read the one queue a
  session's turn is delivered on: one caller could be given the other's answer while the other
  waited until the turn deadline. So could a request that came in while the previous answer was
  still being read out to its caller.

The rule now: a request from a client that keeps no conversation runs on a runtime built for it,
and nothing that runtime is given comes from an earlier request; a client that keeps its
conversation still continues it. A session answers one request at a time: a request that arrives
while its session is busy is refused with the endpoint's typed error and a sentence saying what
the caller can do, and the answer in progress is left as it is.

Driven through the gateway's own route table, the real chat runner, the real session manager and
the production runtime factory. The model is a fake that records every message list it is asked
with; the agent CLI is ``scripted_acp_agent.py`` over stdio, which records every frame it receives.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from scripted_acp_agent import PLAIN_ANSWER, SESSION_ID

from personalclaw.config.external_access import ExternalAccessConfig
from personalclaw.config.external_access import ExternalAccessSurfaceConfig as Surface
from personalclaw.config.loader import AgentProfile, AppConfig
from personalclaw.config.transactions import mutate_config
from personalclaw.context import ContextBuilder
from personalclaw.dashboard.chat_utils import _history_key_for
from personalclaw.dashboard.routes import register_dashboard_routes
from personalclaw.dashboard.state import DashboardState
from personalclaw.history import ConversationLog
from personalclaw.inbound import auth, caps, clients
from personalclaw.inbound import openai_dialect as dialect
from personalclaw.llm.acp_agent import AcpAgentProvider
from personalclaw.llm.base import EVENT_COMPLETE, EVENT_TEXT_CHUNK, LLMEvent
from personalclaw.llm.capabilities import Capability, ProviderCapability
from personalclaw.llm.registry import ProviderEntry, ProviderRegistry
from personalclaw.memory import MemoryStore
from personalclaw.providers.provider_bridge import create_provider_factory
from personalclaw.providers.use_cases import save_active_models
from personalclaw.session import SessionManager
from personalclaw.skills import SkillsLoader

_SURFACES = ("OPENAI", "MCP", "A2A", "CAPTURE", "BRIDGE")
AGENT_CLI = Path(__file__).with_name("scripted_acp_agent.py")

ENTRY = "recorder"
AGENT = "researcher"
RULES = "Work as the research assistant: cite every source you read."
TAG = "release-notes"

#: What one request says, and what only that request says: the next request must not be handed it.
FIRST = "[q1] My locker code is 4417. Summarise the incident report."
SECOND = "[q2] What did I tell you before this?"
SECRET = "4417"


def _marker(text: str) -> str:
    """The request marker (``[q1]``) a message ends on, as the model reads it."""
    found = re.findall(r"\[q[A-Za-z0-9]+\]", text)
    return found[-1] if found else ""


def _asked_for(call: list[dict]) -> str:
    """The marker of the request a model call answers: the one its last user message ends on."""
    last = next((m for m in reversed(call) if m.get("role") == "user"), {})
    return _marker(str(last.get("content") or ""))


class _Model:
    """A chat model that answers each request by its marker and records what it was handed."""

    supports_tools = False

    def __init__(self, world: _World, model: str) -> None:
        self.world = world
        self._model = model

    async def start(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None

    async def complete(self, messages: list[dict], *, model: str | None = None, **_kw: Any):
        handed = [dict(m) for m in messages]
        self.world.asked.append(handed)
        self.world.was_asked.set()
        await self.world.hold.wait()
        yield LLMEvent(kind=EVENT_TEXT_CHUNK, text=f"answered {_asked_for(handed)}")
        yield LLMEvent(kind=EVENT_COMPLETE, input_tokens=3, output_tokens=2)

    async def stream(self, message: str):
        """The one-shot form a chat's background work (its title) asks with."""
        yield LLMEvent(kind=EVENT_TEXT_CHUNK, text="Incident notes")
        yield LLMEvent(kind=EVENT_COMPLETE, input_tokens=1, output_tokens=1)


class _World:
    """A gateway with one agent, a client that keeps its conversation, and a caller that keeps
    none (the surface's own token). Its runtimes are the production ones on a fake model, or, with
    ``cli``, the scripted agent CLI."""

    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, cli: bool) -> None:
        self.tmp = tmp_path
        #: Every message list the model was handed, in order, and the switch that holds a turn.
        self.asked: list[list[dict]] = []
        self.was_asked = asyncio.Event()
        self.hold = asyncio.Event()
        self.hold.set()
        registry = ProviderRegistry()
        registry.register_type(
            ProviderCapability(
                type=ENTRY,
                capabilities=frozenset({Capability.CHAT}),
                supports_streaming=True,
                supports_tools=False,
                supports_embeddings=False,
                supports_vision=False,
                max_context_tokens=32768,
            ),
            lambda *, entry, session_key=None, **kw: _Model(self, str(kw.get("model") or "")),
        )
        registry.register_entry(ProviderEntry(name=ENTRY, type=ENTRY, model="research-1"))
        monkeypatch.setattr("personalclaw.llm.registry.get_default_registry", lambda: registry)
        # Short deadlines, so a reader left waiting on a turn it will never be handed fails the
        # test in seconds rather than holding it for the endpoint's ten minutes.
        monkeypatch.setattr(dialect, "TURN_TIMEOUT_SECS", 8.0)
        monkeypatch.setattr(dialect, "_POLL_TIMEOUT_SECS", 0.5)

        cfg = AppConfig.load()
        cfg.agents[AGENT] = AgentProfile(system_prompt=RULES, model=f"{ENTRY}:research-1")
        cfg.default_agent = AGENT
        cfg.external_access = ExternalAccessConfig(
            enabled=True, openai=Surface(enabled=True, allow_remote=False)
        )
        cfg.save()
        # The model's provider is configured as one added in Settings is, and the chat chain is
        # bound in the store Settings → Models writes. The reader of that store is not patched: a
        # module first imported while such a patch stands keeps the fake after the test, and
        # every later test in the worker reads it.
        mutate_config(
            lambda doc: doc.setdefault("providers", []).append(
                {"name": ENTRY, "type": ENTRY, "model": "research-1"}
            )
        )
        save_active_models({"chat": [f"{ENTRY}:research-1"]})

        self.wire_path = tmp_path / "agent-cli-wire.jsonl"
        (tmp_path / "work").mkdir()
        production = create_provider_factory()
        #: Every runtime the session manager built, in order.
        self.built: list[Any] = []

        def factory(session_key: str | None = None, *args: Any, **kwargs: Any) -> Any:
            if cli:
                runtime: Any = AcpAgentProvider(
                    command=[sys.executable, str(AGENT_CLI), "answers", str(self.wire_path)],
                    cwd=tmp_path / "work",
                    dialect="codex",
                    runtime_id="acp:codex",
                    session_key=session_key,
                )
            else:
                runtime = production(session_key, *args, **kwargs)
            self.built.append(runtime)
            return runtime

        self.sessions = SessionManager(AppConfig.load(), provider_factory=factory)
        self.log = ConversationLog(base_dir=tmp_path / "history")
        self.state = DashboardState(
            sessions=self.sessions, start_time=0.0, conversation_log=self.log
        )
        self.state.context_builder = ContextBuilder(
            memory=MemoryStore(workspace=tmp_path / "ws"),
            skills=SkillsLoader(skills_path=tmp_path / "skills", install_builtins=False),
            conversation_log=self.log,
        )
        self.state._hook_store = None
        self.state.broadcast_ws = lambda *a, **k: None
        self.state.push_sessions_update = lambda *a, **k: None

        # A caller signing in with the surface's own token keeps no conversation: all of its
        # requests share the surface's one session. The registered client keeps its conversation,
        # one session per `user` value.
        self.surface_token = auth.create_surface_token(dialect.OPENAI_SURFACE)
        self.alone = dialect.session_key_for(dialect.OPENAI_SURFACE, dialect.DEFAULT_SESSION_TAG)
        record, self.token = clients.create_client("notes app", surfaces=["openai"])
        registered = clients.load_clients()
        registered[record.client_id].persistent_sessions = True
        clients.save_clients(registered)
        self.client_id = record.client_id

    def kept(self, tag: str = TAG) -> str:
        """The session a request of the client that keeps its conversation runs in."""
        return dialect.session_key_for(self.client_id, tag)

    async def start(self) -> None:
        app = web.Application()
        app["state"] = self.state
        # The gateway's own route table, so the endpoint is wired the way the gateway wires it.
        register_dashboard_routes(app)
        self.http = TestClient(TestServer(app))
        await self.http.start_server()

    async def post(
        self, text: str, *, keeps_conversation: bool = False, tag: str = TAG, stream: bool = False
    ) -> tuple[int, dict]:
        """One request, read to its end: ``(status, payload)``. A streamed answer is folded into
        the shape a buffered one has, so both are read the same way."""
        body: dict[str, Any] = {"model": AGENT, "messages": [{"role": "user", "content": text}]}
        if keeps_conversation:
            body["user"] = tag
        if stream:
            body["stream"] = True
        token = self.token if keeps_conversation else self.surface_token
        resp = await self.http.post(
            dialect.ROUTE_CHAT, data=json.dumps(body), headers={"Authorization": f"Bearer {token}"}
        )
        if not stream or resp.status != 200:
            return resp.status, await resp.json()
        parts: list[str] = []
        for line in (await resp.text()).splitlines():
            if not line.startswith("data: ") or line == "data: [DONE]":
                continue
            delta = json.loads(line[len("data: ") :])["choices"][0]["delta"]
            parts.append(str(delta.get("content") or ""))
        return 200, {"choices": [{"message": {"content": "".join(parts).strip()}}]}

    async def ask(self, text: str, **kw: Any) -> tuple[int, dict]:
        """One request, and the session's turn settled after it."""
        status, payload = await self.post(text, **kw)
        try:
            await self.settled(
                self.kept(kw.get("tag", TAG)) if kw.get("keeps_conversation") else ""
            )
        except AssertionError as exc:
            raise AssertionError(f"{exc}; the request was answered {status} {payload}") from None
        return status, payload

    async def settled(self, name: str = "") -> None:
        """Until the session's turn has finished its cleanup, not only answered."""
        for _ in range(1000):
            session = self.state._sessions.get(name or self.alone)
            if session is None or not session.running:
                return
            await asyncio.sleep(0.01)
        rows = [(m.get("role"), str(m.get("content"))[:200]) for m in session.messages[-8:]]
        frames = [(r["kind"], r.get("method", ""), r["pid"]) for r in self.wire()[-12:]]
        raise AssertionError(
            f"the turn never finished; the session's last rows: {rows}; "
            f"the agent CLI's last frames: {frames}"
        )

    def session(self, name: str = "") -> Any:
        return self.state._sessions[name or self.alone]

    def runtime(self, name: str = "") -> Any:
        """The runtime the session manager holds for the session now."""
        return self.sessions._sessions[_history_key_for(name or self.alone)].provider

    def handed(self, marker: str) -> list[dict]:
        """The message list the model was handed to answer the request marked *marker*."""
        calls = [call for call in self.asked if _asked_for(call) == marker]
        assert len(calls) == 1, f"the model was asked {len(calls)} times for {marker}"
        return calls[0]

    def wire(self) -> list[dict]:
        """Every line the agent CLI recorded: a ``spawn`` per process, every frame it received."""
        if not self.wire_path.exists():
            return []
        return [json.loads(line) for line in self.wire_path.read_text().splitlines() if line]

    def received(self, method: str) -> list[dict]:
        return [r for r in self.wire() if r["kind"] == "received" and r["method"] == method]

    async def close(self) -> None:
        self.hold.set()
        await self.http.close()
        for session in list(self.state._sessions.values()):
            task = session.task
            if task is not None and not task.done():
                task.cancel()
                try:
                    await asyncio.wait_for(task, timeout=10)
                except (asyncio.CancelledError, Exception):  # noqa: BLE001 - teardown only
                    pass
        await self.sessions.close_all()
        for runtime in self.built:
            if isinstance(runtime, AcpAgentProvider):
                await runtime.shutdown()


async def _world(tmp_path, monkeypatch, *, cli: bool):
    for surface in _SURFACES:
        monkeypatch.delenv(f"PERSONALCLAW_INBOUND_{surface}_TOKEN", raising=False)
    # The rate buckets are process-wide: no test inherits a budget another spent.
    caps.reset_for_tests()
    w = _World(tmp_path, monkeypatch, cli=cli)
    await w.start()
    return w


async def _end(w: _World) -> None:
    await w.close()
    caps.reset_for_tests()
    for surface in _SURFACES:
        os.environ.pop(f"PERSONALCLAW_INBOUND_{surface}_TOKEN", None)


@pytest_asyncio.fixture
async def world(tmp_path, monkeypatch):
    w = await _world(tmp_path, monkeypatch, cli=False)
    try:
        yield w
    finally:
        await _end(w)


@pytest_asyncio.fixture
async def cli_world(tmp_path, monkeypatch):
    # The handshake's settle wait for tool-server start-up notices: this agent sends none.
    monkeypatch.setattr("personalclaw.acp.client._DRAIN_DURATION", 0.05)
    w = await _world(tmp_path, monkeypatch, cli=True)
    try:
        yield w
    finally:
        await _end(w)


def _text(call: list[dict]) -> str:
    return "\n".join(str(m.get("content") or "") for m in call)


def _answer(payload: dict) -> str:
    return payload["choices"][0]["message"]["content"]


def _refusal(payload: dict) -> dict:
    return payload["error"]


# ── A client that keeps no conversation ────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_next_request_of_a_client_that_keeps_no_conversation_is_not_handed_the_last(
    world,
):
    """Red before the fix: the second request ran on the runtime the first had built, and its
    model was handed the first request's words and answer ahead of its own."""
    w = world
    status, first = await w.ask(FIRST)
    assert status == 200, first
    assert _answer(first) == "answered [q1]"
    assert SECRET in _text(w.handed("[q1]")), "vacuity floor: the model really read the first"

    status, second = await w.ask(SECOND)
    assert status == 200, second
    assert _answer(second) == "answered [q2]"
    handed = _text(w.handed("[q2]"))
    assert SECRET not in handed, "the second request's model was handed the first request"
    assert "answered [q1]" not in handed, "the second request's model was handed the first answer"
    # Answered as a request of its own is answered: under the agent's instructions, which a
    # runtime is given when it starts a conversation.
    assert RULES in handed
    # On a runtime built for it.
    assert len(w.built) == 2
    assert w.runtime() is w.built[1]
    assert SECRET not in "\n".join(str(m.get("content") or "") for m in w.runtime()._messages)
    assert SECRET not in json.dumps(w.session().messages)


@pytest.mark.asyncio
async def test_a_note_an_earlier_request_left_for_the_next_turn_is_not_handed_to_the_next_request(
    world,
):
    """A subagent the first request's turn started could not deliver its result, and left a note
    for the session's next turn. Red before the fix: the next request, which stands alone, was
    handed it ahead of its own words."""
    w = world
    status, _ = await w.ask(FIRST)
    assert status == 200
    note = "The subagent that read the locker log timed out; its result is in notes/run-58.md."
    w.session()._owed_subagent_endings.append(note)

    status, second = await w.ask(SECOND)
    assert status == 200, second
    assert "run-58" not in _text(w.handed("[q2]"))


@pytest.mark.asyncio
async def test_a_client_that_keeps_its_conversation_still_continues_it(world):
    """The control: the same two requests from the client that keeps its conversation are one
    conversation, on one runtime, and the second is answered knowing the first."""
    w = world
    status, first = await w.ask(FIRST, keeps_conversation=True)
    assert status == 200, first
    runtime = w.runtime(w.kept())

    status, second = await w.ask(SECOND, keeps_conversation=True)
    assert status == 200, second
    assert _answer(second) == "answered [q2]"
    handed = _text(w.handed("[q2]"))
    assert SECRET in handed, "the conversation the client keeps was not continued"
    assert "answered [q1]" in handed
    assert w.runtime(w.kept()) is runtime
    assert len(w.built) == 1


@pytest.mark.asyncio
async def test_an_agent_cli_answers_each_request_of_a_client_that_keeps_none_in_a_new_session(
    cli_world,
):
    """An agent CLI keeps the conversation in its own session. Red before the fix: the second
    request was prompted into the session the first had opened, in the same process."""
    w = cli_world
    status, first = await w.ask(FIRST)
    assert status == 200, first
    assert _answer(first) == PLAIN_ANSWER
    status, second = await w.ask(SECOND)
    assert status == 200, second
    assert _answer(second) == PLAIN_ANSWER

    prompts = w.received("session/prompt")
    assert len(prompts) == 2
    assert SECRET in json.dumps(prompts[0]["params"]), "vacuity floor: the CLI read the first"
    assert prompts[1]["pid"] != prompts[0]["pid"], "the second request went to the first's CLI"
    # Each request opened a session of its own, in a process of its own, and none loaded one.
    assert [r["kind"] for r in w.wire()].count("spawn") == 2
    assert [r["pid"] for r in w.received("session/new")] == [p["pid"] for p in prompts]
    assert w.received("session/load") == []
    later = [r for r in w.wire() if r["pid"] == prompts[1]["pid"]]
    assert SECRET not in json.dumps(later), "the second request's CLI was handed the first"


@pytest.mark.parametrize("gone", ["reaped", "restarted"])
@pytest.mark.asyncio
async def test_an_agent_cli_whose_process_is_gone_does_not_load_the_last_request_back(
    cli_world, gone
):
    """The agent CLI's process is reaped when idle, or the gateway restarts, and the session
    manager keeps the id its conversation resumes from. Red before the fix: the purge named a key
    the manager never writes, so the next request's CLI loaded the first request's session."""
    w = cli_world
    status, first = await w.ask(FIRST)
    assert status == 200, first
    if gone == "reaped":
        await w.sessions.remove(_history_key_for(w.alone))
    else:
        await w.sessions.close_all()
    assert (
        w.sessions._session_map.get(_history_key_for(w.alone)) == SESSION_ID
    ), "vacuity floor: the manager kept the id the conversation resumes from"

    status, second = await w.ask(SECOND)
    assert status == 200, second
    assert w.received("session/load") == [], "the next request loaded the first one's session"
    assert len(w.received("session/new")) == 2


@pytest.mark.asyncio
async def test_an_agent_cli_still_resumes_the_conversation_a_client_keeps(cli_world):
    """The control: for the client that keeps its conversation, the next process of a reaped CLI
    loads the session back, the same request whose stand-alone twin above loads nothing."""
    w = cli_world
    status, _ = await w.ask(FIRST, keeps_conversation=True)
    assert status == 200
    await w.sessions.remove(_history_key_for(w.kept()))

    status, _ = await w.ask(SECOND, keeps_conversation=True)
    assert status == 200
    assert [r["kind"] for r in w.wire()].count("spawn") == 2
    (loaded,) = w.received("session/load")
    assert loaded["params"]["sessionId"] == SESSION_ID
    assert len(w.received("session/new")) == 1


# ── One request at a time on a session ─────────────────────────────────────────


@pytest.mark.parametrize("streamed", [False, True], ids=["buffered", "streamed"])
@pytest.mark.asyncio
async def test_a_request_that_arrives_while_its_session_answers_is_refused_at_once(world, streamed):
    """Red before the fix: the second request started a second turn on the session, and the two
    readers shared one queue, so one caller was handed the other's answer or waited out the turn
    deadline."""
    w = world
    w.hold.clear()
    w.was_asked.clear()
    running = asyncio.create_task(w.post("[qA] Summarise the incident.", stream=streamed))
    try:
        await asyncio.wait_for(w.was_asked.wait(), timeout=10)
        assert w.session().running
        try:
            status, refused = await asyncio.wait_for(w.post("[qB] List the actions."), timeout=5)
        except asyncio.TimeoutError:
            pytest.fail("the request was taken in as a second turn beside the running one")
        assert status == 409, refused
        error = _refusal(refused)
        assert error["code"] == "session_busy"
        assert error["type"] == "invalid_request_error"
        assert "one at a time" in error["message"]
        # A client that keeps no conversation has one session, so it is not told to use another.
        assert "another session" not in error["message"]
        # Nothing of the refused request reached the model or the conversation.
        assert len(w.asked) == 1
        assert "[qB]" not in json.dumps(w.session().messages)
    finally:
        w.hold.set()
    status, answered = await asyncio.wait_for(running, timeout=10)
    assert status == 200, answered
    assert _answer(answered) == "answered [qA]"
    await w.settled()

    # Once that answer is over, the same request is answered, with its own answer.
    status, again = await w.ask("[qB] List the actions.")
    assert status == 200, again
    assert _answer(again) == "answered [qB]"


@pytest.mark.asyncio
async def test_a_request_that_arrives_while_the_last_answer_is_read_out_is_refused(
    world, monkeypatch
):
    """The turn has ended but its answer is still being read out to its caller. Red before the
    fix (and with a check on a running turn alone): the second request started its turn, and when
    the first caller's reader let go of the session it took the second turn's rows with it, so the
    second caller waited out the turn deadline for an answer it was never handed."""
    w = world
    reading = asyncio.Event()
    read_out = asyncio.Event()
    drain_turn = dialect._drain_turn

    async def _held_after_its_answer(session, **kw):
        outcome = await drain_turn(session, **kw)
        if not reading.is_set():
            reading.set()
            await read_out.wait()
        return outcome

    monkeypatch.setattr(dialect, "_drain_turn", _held_after_its_answer)
    first = asyncio.create_task(w.post("[qA] Summarise the incident."))
    try:
        await asyncio.wait_for(reading.wait(), timeout=10)
        await w.settled()
        assert not w.session().running, "vacuity floor: the first turn had ended"

        w.hold.clear()
        second = asyncio.create_task(w.post("[qB] List the actions."))
        done, _ = await asyncio.wait({second}, timeout=5)
        if not done:
            # The old way in: let the first reader go and the second turn answer, then read what
            # its caller was handed.
            read_out.set()
            w.hold.set()
            status, payload = await asyncio.wait_for(second, timeout=20)
            pytest.fail(
                "the request was taken in while the last answer was still being read out; "
                f"its caller got {status} {payload}"
            )
        status, refused = second.result()
        assert status == 409, refused
        assert _refusal(refused)["code"] == "session_busy"
    finally:
        read_out.set()
        w.hold.set()
    status, answered = await asyncio.wait_for(first, timeout=10)
    assert status == 200, answered
    assert _answer(answered) == "answered [qA]"

    status, again = await w.ask("[qB] List the actions.")
    assert status == 200, again
    assert _answer(again) == "answered [qB]"


@pytest.mark.asyncio
async def test_a_client_that_keeps_its_conversation_is_refused_per_session_only(world):
    """For the client that keeps its conversation, a second request in the same session is
    refused and told it may use another one; a request in another session runs beside it, and
    each caller is handed its own answer."""
    w = world
    w.hold.clear()
    w.was_asked.clear()
    running = asyncio.create_task(w.post("[qA] Summarise it.", keeps_conversation=True))
    beside: asyncio.Task | None = None
    try:
        await asyncio.wait_for(w.was_asked.wait(), timeout=10)
        try:
            status, refused = await asyncio.wait_for(
                w.post("[qB] And the actions?", keeps_conversation=True), timeout=5
            )
        except asyncio.TimeoutError:
            pytest.fail("the request was taken in as a second turn beside the running one")
        assert status == 409, refused
        error = _refusal(refused)
        assert error["code"] == "session_busy"
        assert "another session" in error["message"]

        w.was_asked.clear()
        beside = asyncio.create_task(
            w.post("[qC] Draft the summary.", keeps_conversation=True, tag="draft")
        )
        await asyncio.wait_for(w.was_asked.wait(), timeout=10)
        assert w.session(w.kept()).running and w.session(w.kept("draft")).running
    finally:
        w.hold.set()
    status, answered = await asyncio.wait_for(running, timeout=10)
    assert (status, _answer(answered)) == (200, "answered [qA]")
    assert beside is not None
    status, other = await asyncio.wait_for(beside, timeout=10)
    assert (status, _answer(other)) == (200, "answered [qC]")

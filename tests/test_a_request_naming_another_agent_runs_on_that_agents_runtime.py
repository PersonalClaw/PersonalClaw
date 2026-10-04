"""A request that names another agent for its session is answered by that agent's runtime.

Measured on a running gateway: on the OpenAI-compatible endpoint a client that keeps its
conversation asked ``researcher`` one question and then asked ``writer`` the next one in the same
session. The second request set the session's agent to ``writer`` and nothing else, and the session
manager handed the turn the runtime it already held: the one built for ``researcher``. So the
writer's turn ran on the researcher's model and declared tools, under the instructions the
researcher's first turn had sent (a runtime is told them once, when it is built), while the chat,
its saved transcript and the turn's usage row all said ``writer`` answered. A request that named
another agent while a turn was still running changed the agent under that turn.

The dashboard's own send door had the same assignment for a chat that had been answered by the
default agent: an API caller naming an agent for it moved the record and kept the runtime.

The rule now, for every door that names an agent for an existing conversation: the change goes
through the one change every door makes (``running_turn.rebind``), which retires the runtime, so
the next turn is built for the agent named. A request on the endpoint that names another agent
while the session's turn is running is refused with the endpoint's typed error and a sentence: that
turn is another request's, and its caller is waiting for the agent it named. Naming the agent the
session already runs keeps the runtime it has.

Driven through the gateway's own route table, the real chat runner, the real session manager and
the production runtime factory. Only the model is a fake, and its answer says which model it was
asked to run as.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw import usage_ledger
from personalclaw.config.external_access import ExternalAccessConfig
from personalclaw.config.external_access import ExternalAccessSurfaceConfig as Surface
from personalclaw.config.loader import AgentProfile, AppConfig
from personalclaw.context import ContextBuilder
from personalclaw.dashboard.chat_handlers import api_chat
from personalclaw.dashboard.chat_utils import _history_key_for, persisted_history_key
from personalclaw.dashboard.routes import register_dashboard_routes
from personalclaw.dashboard.state import DashboardState
from personalclaw.history import ConversationLog
from personalclaw.inbound import auth, clients
from personalclaw.inbound import openai_dialect as dialect
from personalclaw.llm.base import EVENT_COMPLETE, EVENT_TEXT_CHUNK, LLMEvent
from personalclaw.llm.capabilities import Capability, ProviderCapability
from personalclaw.llm.registry import ProviderEntry, ProviderRegistry
from personalclaw.memory import MemoryStore
from personalclaw.providers.provider_bridge import create_provider_factory
from personalclaw.session import SessionManager
from personalclaw.skills import SkillsLoader

_SURFACES = ("OPENAI", "MCP", "A2A", "CAPTURE", "BRIDGE")

ENTRY = "recorder"
RESEARCHER, WRITER = "researcher", "writer"
RESEARCHER_RULES = "Work as the research assistant: cite every source you read."
WRITER_RULES = "Work as the editor: answer in plain sentences, no lists."
#: What each agent declares, and the model it pins — what its runtime is built with.
PROFILES = {
    RESEARCHER: AgentProfile(
        system_prompt=RESEARCHER_RULES, model=f"{ENTRY}:research-1", tools=["read_file"]
    ),
    WRITER: AgentProfile(
        system_prompt=WRITER_RULES, model=f"{ENTRY}:write-1", tools=["write_file"]
    ),
}
TAG = "draft-review"


class _Model:
    """A chat model whose answer names the model it was asked to run as."""

    supports_tools = False

    def __init__(self, entry: str, model: str) -> None:
        self.entry = entry
        self._model = model
        #: Released by a test that needs a turn to stay running; set means "answer at once".
        self.hold = asyncio.Event()
        self.hold.set()
        self.asked = asyncio.Event()

    async def start(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None

    async def complete(self, messages: list[dict], *, model: str | None = None, **_kw: Any):
        self.asked.set()
        await self.hold.wait()
        yield LLMEvent(kind=EVENT_TEXT_CHUNK, text=f"answered on {model or self._model}")
        yield LLMEvent(kind=EVENT_COMPLETE, input_tokens=3, output_tokens=2)

    async def stream(self, message: str):
        """The one-shot form a chat's background work (its title) asks with."""
        yield LLMEvent(kind=EVENT_TEXT_CHUNK, text="Draft notes")
        yield LLMEvent(kind=EVENT_COMPLETE, input_tokens=1, output_tokens=1)


class _World:
    """A gateway with two agents, one client that keeps its conversation, and a fake model."""

    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.models: list[_Model] = []
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
            self._build_model,
        )
        registry.register_entry(ProviderEntry(name=ENTRY, type=ENTRY, model="research-1"))
        monkeypatch.setattr("personalclaw.llm.registry.get_default_registry", lambda: registry)
        chat_models = {"chat": [f"{ENTRY}:research-1", f"{ENTRY}:write-1"]}
        monkeypatch.setattr(
            "personalclaw.providers.use_cases.load_active_models", lambda: chat_models
        )

        cfg = AppConfig.load()
        cfg.agents.update(PROFILES)
        cfg.external_access = ExternalAccessConfig(
            enabled=True, openai=Surface(enabled=True, allow_remote=False)
        )
        cfg.save()

        production = create_provider_factory()
        #: Every runtime the session manager had built, in order — what each was built with is
        #: read off the runtime itself.
        self.built: list[Any] = []

        def factory(*args: Any, **kwargs: Any) -> Any:
            runtime = production(*args, **kwargs)
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
        self.frames: list[tuple[str, dict]] = []
        self.state.broadcast_ws = lambda kind, data=None: self.frames.append((kind, data or {}))
        self.state.push_sessions_update = lambda *a, **k: None

        # The surface serves only once it has its own token. A caller signing in with it keeps no
        # conversation, so every request it sends shares the surface's one session; the client
        # registered below keeps its conversation, one session per tag.
        self.surface_token = auth.create_surface_token(dialect.OPENAI_SURFACE)
        self.shared_chat = dialect.session_key_for(
            dialect.OPENAI_SURFACE, dialect.DEFAULT_SESSION_TAG
        )
        record, self.token = clients.create_client("drafts app", surfaces=["openai"])
        registered = clients.load_clients()
        registered[record.client_id].persistent_sessions = True
        clients.save_clients(registered)
        self.chat_name = dialect.session_key_for(record.client_id, TAG)

    def _build_model(self, *, entry: ProviderEntry, session_key: str | None = None, **kwargs):
        model = _Model(entry.name, str(kwargs.get("model") or entry.model))
        self.models.append(model)
        return model

    async def start(self) -> None:
        app = web.Application()
        app["state"] = self.state
        # The gateway's own route table, so the doorway is wired the way the gateway wires it.
        register_dashboard_routes(app)
        self.http = TestClient(TestServer(app))
        await self.http.start_server()

    async def ask(self, agent: str, text: str, *, keeps_conversation: bool = True):
        body = {"model": agent, "user": TAG, "messages": [{"role": "user", "content": text}]}
        token = self.token if keeps_conversation else self.surface_token
        resp = await self.http.post(
            dialect.ROUTE_CHAT,
            data=json.dumps(body),
            headers={"Authorization": f"Bearer {token}"},
        )
        payload = await resp.json()
        await self.settled(self.chat_name if keeps_conversation else self.shared_chat)
        return resp.status, payload

    async def settled(self, name: str) -> None:
        """Until the chat's turn has finished its cleanup, not only answered."""
        for _ in range(500):
            session = self.state._sessions.get(name)
            if session is None or not session.running:
                return
            await asyncio.sleep(0.01)
        raise AssertionError("the turn never finished")

    @property
    def session(self) -> Any:
        return self.state._sessions[self.chat_name]

    def runtime(self, name: str = "") -> Any:
        """The runtime the session manager holds for the chat now."""
        return self.sessions._sessions[_history_key_for(name or self.chat_name)].provider

    def usage(self, name: str = "") -> list[dict]:
        """The usage rows the chat's turns wrote (its background work writes its own)."""
        return _turn_usage(_history_key_for(name or self.chat_name))

    def persisted(self) -> dict:
        return self.log.get_metadata(persisted_history_key(self.log, self.chat_name))

    async def close(self) -> None:
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


@pytest_asyncio.fixture
async def world(tmp_path, monkeypatch):
    for surface in _SURFACES:
        monkeypatch.delenv(f"PERSONALCLAW_INBOUND_{surface}_TOKEN", raising=False)
    w = _World(tmp_path, monkeypatch)
    await w.start()
    try:
        yield w
    finally:
        await w.close()
        for surface in _SURFACES:
            os.environ.pop(f"PERSONALCLAW_INBOUND_{surface}_TOKEN", None)


def _turn_usage(key: str) -> list[dict]:
    rows = usage_ledger._iter_rows()
    return [row for row in rows if row.get("session_key") == key and row.get("source") == "chat"]


def _said(runtime: Any) -> str:
    """Everything the runtime's conversation holds: what its model is shown on every turn."""
    return "\n".join(str(m.get("content") or "") for m in runtime._messages)


def _answer(payload: dict) -> str:
    return payload["choices"][0]["message"]["content"]


@pytest.mark.asyncio
async def test_the_turn_after_a_switch_runs_on_a_runtime_built_for_the_agent_named(world):
    """Red before the fix: the writer's turn ran on the runtime built for the researcher."""
    w = world
    status, first = await w.ask(RESEARCHER, "What changed in the draft?")
    assert status == 200, first
    assert _answer(first) == "answered on research-1"
    researchers = w.runtime()
    assert researchers.agent_name == RESEARCHER

    status, second = await w.ask(WRITER, "Now tighten the opening paragraph.")
    assert status == 200, second

    # The turn ran on a runtime built for the writer, as the writer declares it.
    runtime = w.runtime()
    assert runtime is not researchers, "the writer's turn ran on the researcher's runtime"
    assert [r.agent_name for r in w.built] == [RESEARCHER, WRITER]
    assert runtime.agent_name == WRITER
    assert runtime._definition.tools.patterns == ("write_file",)
    assert runtime._definition.model == "write-1"
    assert _answer(second) == "answered on write-1"
    # Under the writer's instructions, and not the researcher's.
    assert WRITER_RULES in _said(runtime)
    assert RESEARCHER_RULES not in _said(runtime)
    # The runtime it moved off is gone.
    assert researchers not in [s.provider for s in w.sessions._sessions.values()]

    # What the conversation records names the agent that answered, on the model that ran.
    assert w.session.agent == WRITER
    assert w.persisted().get("agent") == WRITER
    assert [(row["agent"], row["model"].rpartition(":")[2]) for row in w.usage()] == [
        (RESEARCHER, "research-1"),
        (WRITER, "write-1"),
    ]
    # Every page showing the conversation was told what answers it now.
    (binding,) = [d for k, d in w.frames if k == "session_binding"]
    assert binding["session"] == w.chat_name and binding["agent"] == WRITER


@pytest.mark.asyncio
async def test_a_client_that_keeps_no_conversation_is_answered_by_each_agent_it_names(world):
    """Its requests all share one session, so naming another agent from one request to the next
    is the ordinary way in. Red before the fix: the writer was answered on the researcher's
    runtime."""
    w = world
    status, first = await w.ask(RESEARCHER, "Summarise the report.", keeps_conversation=False)
    assert status == 200, first
    assert _answer(first) == "answered on research-1"

    status, second = await w.ask(WRITER, "Write its abstract.", keeps_conversation=False)
    assert status == 200, second
    assert _answer(second) == "answered on write-1"
    runtime = w.runtime(w.shared_chat)
    assert runtime.agent_name == WRITER
    assert WRITER_RULES in _said(runtime)
    assert w.state._sessions[w.shared_chat].agent == WRITER


@pytest.mark.asyncio
async def test_naming_the_agent_the_session_runs_keeps_its_runtime(world):
    """The control: the same agent twice is one runtime, built once, carrying the conversation."""
    w = world
    status, first = await w.ask(WRITER, "Draft a title for the report.")
    assert status == 200, first
    runtime = w.runtime()

    status, again = await w.ask(WRITER, "Shorter, please.")
    assert status == 200, again
    assert w.runtime() is runtime
    assert [r.agent_name for r in w.built] == [WRITER]
    assert _answer(again) == "answered on write-1"
    assert "Draft a title for the report." in _said(runtime), "the conversation was not kept"
    assert [d for k, d in w.frames if k == "session_binding"] == []


@pytest.mark.asyncio
async def test_a_request_naming_another_agent_mid_turn_is_refused_and_the_turn_is_left_alone(
    world,
):
    """Red before the fix: the second request changed the agent under the running turn, and
    started a second turn on the session beside it."""
    w = world
    status, first = await w.ask(RESEARCHER, "List the sources.")
    assert status == 200, first
    researchers = w.runtime()
    model = researchers.model_provider
    model.hold.clear()
    model.asked.clear()

    running = asyncio.create_task(w.ask(RESEARCHER, "And the second chapter's?"))
    try:
        await asyncio.wait_for(model.asked.wait(), timeout=10)
        assert w.session.running

        try:
            resp = await asyncio.wait_for(
                w.http.post(
                    dialect.ROUTE_CHAT,
                    data=json.dumps(
                        {
                            "model": WRITER,
                            "user": TAG,
                            "messages": [{"role": "user", "content": "Rewrite."}],
                        }
                    ),
                    headers={"Authorization": f"Bearer {w.token}"},
                ),
                timeout=10,
            )
        except asyncio.TimeoutError:
            pytest.fail("the request was taken in as a second turn beside the running one")
        refusal = await resp.json()
        assert resp.status == 409, refusal
        error = refusal["error"]
        assert error["code"] == "agent_change_mid_turn"
        assert error["type"] == "invalid_request_error"
        assert "still answering" in error["message"]
        # The running turn kept its agent and its runtime, and nothing else was started.
        assert w.session.agent == RESEARCHER
        assert w.runtime() is researchers
        assert "Rewrite." not in [m.get("content") for m in w.session.messages]
    finally:
        model.hold.set()
    status, finished = await running
    assert status == 200, finished
    assert _answer(finished) == "answered on research-1"

    # Once that turn is over, the same request moves the conversation.
    status, moved = await w.ask(WRITER, "Rewrite.")
    assert status == 200, moved
    assert _answer(moved) == "answered on write-1"
    assert w.runtime().agent_name == WRITER


@pytest.mark.asyncio
async def test_the_dashboard_send_door_moves_a_default_agent_chat_to_the_agent_it_names(world):
    """Red before the fix: an API send naming an agent for a chat the default agent had answered
    set the chat's agent and kept the default agent's runtime."""
    w = world
    app = web.Application()
    app["state"] = w.state
    app.router.add_post("/api/chat", api_chat)
    chat = w.state.get_or_create_session("chat-notes")
    async with TestClient(TestServer(app)) as http:
        resp = await http.post("/api/chat?ws=1", json={"message": "hi", "session": "chat-notes"})
        assert resp.status == 200, await resp.text()
        await asyncio.wait_for(chat.task, timeout=10)
        key = _history_key_for("chat-notes")
        default_runtime = w.sessions._sessions[key].provider
        assert chat.agent == ""

        resp = await http.post(
            "/api/chat?ws=1",
            json={"message": "Tighten it.", "session": "chat-notes", "agent": WRITER},
        )
        assert resp.status == 200, await resp.text()
        await asyncio.wait_for(chat.task, timeout=10)

    runtime = w.sessions._sessions[key].provider
    assert runtime is not default_runtime, "the writer's turn ran on the default agent's runtime"
    assert runtime.agent_name == WRITER
    assert WRITER_RULES in _said(runtime)
    assert chat.agent == WRITER
    assert [(row["agent"], row["model"].rpartition(":")[2]) for row in _turn_usage(key)] == [
        ("", "research-1"),
        (WRITER, "write-1"),
    ]

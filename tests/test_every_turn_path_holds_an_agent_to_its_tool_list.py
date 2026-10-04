"""Every path that runs an agent's turn on PersonalClaw's own runtime holds it to its tool list.

The list is read where the native runtime is built (``provider_bridge._build_native_runtime``) and
held where the runtime builds a turn's tools and dispatches a call, so it holds wherever a turn of
that agent runs: a chat, a subagent it is spawned as, a loop's worker, an automation, a workflow
step, and the OpenAI-compatible endpoint. One test per path, each driving that path's own entry
(the route, the spawn, the loop's start, the action, the step's dispatch) through the real chat
runner or subagent manager, the real session manager and the production runtime factory. Only the
model is a fake: on its first request it calls a tool outside the agent's list and one inside it,
and it keeps the tool block and the results each request carried.

🔴 Red before, on every path: the turn was offered every tool, and the call outside the list ran.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
import pytest_asyncio
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.agents.native.tool_names import model_safe_name
from personalclaw.config.external_access import ExternalAccessConfig
from personalclaw.config.external_access import ExternalAccessSurfaceConfig as Surface
from personalclaw.config.loader import AgentProfile, AppConfig
from personalclaw.context import ContextBuilder
from personalclaw.dashboard.routes import register_dashboard_routes
from personalclaw.dashboard.state import DashboardState
from personalclaw.history import ConversationLog
from personalclaw.inbound import auth
from personalclaw.inbound import openai_dialect as dialect
from personalclaw.llm.base import EVENT_COMPLETE, EVENT_TEXT_CHUNK, LLMEvent
from personalclaw.llm.capabilities import Capability, ProviderCapability
from personalclaw.llm.events import EVENT_TOOL_CALL
from personalclaw.llm.registry import ProviderEntry, ProviderRegistry
from personalclaw.memory import MemoryStore
from personalclaw.providers.provider_bridge import create_provider_factory
from personalclaw.session import SessionManager
from personalclaw.skills import SkillsLoader
from personalclaw.subagent import SubagentManager
from personalclaw.tool_providers.base import RiskLevel, ToolDefinition, ToolProvider, ToolResult

_SURFACES = ("OPENAI", "MCP", "A2A", "CAPTURE", "BRIDGE")

ENTRY = "recorder"
AGENT = "researcher"
#: What the agent's list allows, and the tool every list keeps.
ALLOWED = {"read_file", "notes_read", "notes_write", "tool_result_get"}
#: Outside the list: the platform's own write, which every native session is built with.
OUTSIDE = "write_file"
REFUSAL = (
    f"the agent {AGENT} may use only the tools on its tool list, set on the Agents page, and "
    f"{OUTSIDE} is not one of them"
)


class _Notes(ToolProvider):
    """A provider of the test's own: a note it reads and one it writes, neither asking first."""

    def __init__(self) -> None:
        self.ran: list[str] = []

    @property
    def name(self) -> str:
        return "notes"

    @property
    def display_name(self) -> str:
        return "Notes"

    async def list_tools(self) -> list[ToolDefinition]:
        return [
            ToolDefinition(
                name=name,
                description=f"{verb} the shared notes.",
                parameters={"type": "object", "properties": {}},
                requires_approval=False,
                risk_level=risk,
            )
            for name, verb, risk in (
                ("notes_read", "Read", RiskLevel.SAFE),
                ("notes_write", "Write to", RiskLevel.CAUTION),
            )
        ]

    async def invoke(self, tool_name: str, arguments: dict[str, Any]) -> ToolResult:
        self.ran.append(tool_name)
        return ToolResult(success=True, output=f"{tool_name} done")


class _Recorder:
    """A tool-calling model. On the first request that offers it tools it calls one tool outside
    the agent's list and one inside it, then answers; it keeps each request's tool block and the
    tool results it was handed back."""

    supports_tools = True

    def __init__(self, session_key: str, model: str) -> None:
        self.session_key = session_key
        self._model = model
        self.offered: list[set[str]] = []
        self.results: dict[str, str] = {}

    async def start(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None

    async def complete(self, messages: list[dict], *, tools=None, **_kw: Any):
        for message in messages:
            if message.get("role") == "tool":
                self.results[str(message.get("tool_call_id"))] = str(message.get("content"))
        if tools:
            self.offered.append({t["function"]["name"] for t in tools})
        if tools and len(self.offered) == 1:
            for call_id, name in (("outside", OUTSIDE), ("inside", "notes_read")):
                args = {"path": "draft.md", "content": "x"} if name == OUTSIDE else {}
                yield LLMEvent(
                    kind=EVENT_TOOL_CALL,
                    tool_call_id=call_id,
                    title=name,
                    tool_input=json.dumps(args),
                )
        else:
            yield LLMEvent(kind=EVENT_TEXT_CHUNK, text="Checked the notes.")
        yield LLMEvent(kind=EVENT_COMPLETE, input_tokens=3, output_tokens=2)

    async def stream(self, message: str):
        """The one-shot form a chat's background work (its title) asks with."""
        yield LLMEvent(kind=EVENT_TEXT_CHUNK, text="Notes check")
        yield LLMEvent(kind=EVENT_COMPLETE, input_tokens=1, output_tokens=1)


class _World:
    """A gateway with one agent whose tool list narrows it, a tool surface of the platform's own
    tools and the test's notes, and a fake model."""

    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.tmp_path = tmp_path
        self.models: list[_Recorder] = []
        registry = ProviderRegistry()
        registry.register_type(
            ProviderCapability(
                type=ENTRY,
                capabilities=frozenset({Capability.CHAT, Capability.CODE_TOOLS}),
                supports_streaming=True,
                supports_tools=True,
                supports_embeddings=False,
                supports_vision=False,
                max_context_tokens=32768,
            ),
            self._build_model,
        )
        registry.register_entry(ProviderEntry(name=ENTRY, type=ENTRY, model="m1"))
        monkeypatch.setattr("personalclaw.llm.registry.get_default_registry", lambda: registry)
        monkeypatch.setattr(
            "personalclaw.providers.use_cases.load_active_models",
            lambda: {"chat": [f"{ENTRY}:m1"]},
        )
        self.workspace = tmp_path / "workspace"
        self.workspace.mkdir()
        monkeypatch.setenv("PERSONALCLAW_WORKSPACE", str(self.workspace))
        self.notes = _Notes()
        monkeypatch.setattr(
            "personalclaw.tool_providers.registry.list_providers", lambda: [self.notes]
        )

        cfg = AppConfig.load()
        cfg.agents[AGENT] = AgentProfile(provider="native", tools=["read_file", "notes_*"])
        cfg.external_access = ExternalAccessConfig(
            enabled=True, openai=Surface(enabled=True, allow_remote=False)
        )
        cfg.save()

        self.sessions = SessionManager(AppConfig.load(), provider_factory=create_provider_factory())
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
        self.state.broadcast_ws = lambda kind, data=None: None
        self.state.push_sessions_update = lambda *a, **k: None
        self.subagents = SubagentManager(
            sessions=self.sessions, ctx_builder=self.state.context_builder, is_yolo=lambda: False
        )

    def _build_model(self, *, entry: ProviderEntry, session_key: str | None = None, **kwargs):
        model = _Recorder(session_key or "", str(kwargs.get("model") or entry.model))
        self.models.append(model)
        return model

    async def start(self) -> None:
        app = web.Application()
        app["state"] = self.state
        # The gateway's own route table, so each door is wired the way the gateway wires it.
        register_dashboard_routes(app)
        self.http = TestClient(TestServer(app))
        await self.http.start_server()

    async def settled(self, name: str) -> None:
        """Until the chat's turn has finished, cleanup included."""
        for _ in range(1000):
            session = self.state._sessions.get(name)
            if session is None or not session.running:
                return
            await asyncio.sleep(0.01)
        raise AssertionError("the turn never finished")

    async def spawned(self, info: Any) -> None:
        """Until the subagent *info* names has ended; it ended saying the call outside its list
        was one its own limits refused."""
        assert info is not None and not getattr(info, "error", ""), getattr(info, "error", "")
        for _ in range(1000):
            if info.done:
                assert info.refused == [OUTSIDE], info.refused
                return
            await asyncio.sleep(0.01)
        raise AssertionError("the subagent never ended")

    def held(self) -> None:
        """The agent's turn was offered exactly the tools its list allows, its call outside the
        list was refused at dispatch and never ran, and its call inside the list ran."""
        (model,) = [m for m in self.models if m.offered]
        assert model.offered[0] == {model_safe_name(n) for n in ALLOWED}, sorted(model.offered[0])
        said = model.results["outside"]
        assert said.startswith(f"Error: tool `{OUTSIDE}` blocked by a security policy"), said
        assert REFUSAL in said
        assert not (self.workspace / "draft.md").exists(), "the call outside the list ran"
        assert model.results["inside"] == "notes_read done"
        assert self.notes.ran == ["notes_read"]

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
        with (
            patch("personalclaw.subagent_persistence._subagents_dir", lambda: tmp_path / "agents"),
            patch("personalclaw.subagent.check_memory_available", lambda **_kw: (True, 8.0)),
            patch("personalclaw.subagent.Stats"),
        ):
            yield w
    finally:
        await w.close()
        for surface in _SURFACES:
            os.environ.pop(f"PERSONALCLAW_INBOUND_{surface}_TOKEN", None)


@pytest.mark.asyncio
async def test_a_chat_with_the_agent(world):
    # As the chat page does it: a new chat with the agent picked, then the message.
    resp = await world.http.post("/api/chat/sessions", json={"name": "notes-chat", "agent": AGENT})
    assert resp.status == 200, await resp.text()
    resp = await world.http.post(
        "/api/chat?ws=1", json={"message": "Check the notes.", "session": "notes-chat"}
    )
    assert resp.status == 200, await resp.text()
    await world.settled("notes-chat")
    world.held()


@pytest.mark.asyncio
async def test_a_request_to_the_openai_compatible_endpoint_naming_the_agent(world):
    token = auth.create_surface_token(dialect.OPENAI_SURFACE)
    body = {"model": AGENT, "messages": [{"role": "user", "content": "Check the notes."}]}
    resp = await world.http.post(
        dialect.ROUTE_CHAT, data=json.dumps(body), headers={"Authorization": f"Bearer {token}"}
    )
    payload = await resp.json()
    assert resp.status == 200, payload
    await world.settled(
        dialect.session_key_for(dialect.OPENAI_SURFACE, dialect.DEFAULT_SESSION_TAG)
    )
    world.held()


@pytest.mark.asyncio
async def test_a_subagent_spawned_as_the_agent(world):
    info = world.subagents.spawn(
        "Check the notes.", agent=AGENT, approval_mode="auto", capability_class="mutating"
    )
    await world.spawned(info)
    world.held()


@pytest.mark.asyncio
async def test_an_automation_that_runs_the_agent(world):
    from personalclaw.action_providers.base import ActionContext
    from personalclaw.action_providers.run_prompt_provider import RunPromptActionProvider
    from personalclaw.action_providers.services import ActionServices, set_action_services

    set_action_services(ActionServices(state=world.state, subagents=world.subagents))
    try:
        result = await RunPromptActionProvider().execute(
            {"message": "Check the notes.", "agent": AGENT, "capability": "mutating"},
            ActionContext(event="schedule", trigger_id="trg-notes"),
        )
        assert result.success, result.error
        info = world.subagents.get(result.work_id.removeprefix("subagent:"))
        await world.spawned(info)
    finally:
        set_action_services(None)  # type: ignore[arg-type]
    world.held()


@pytest.mark.asyncio
async def test_a_workflow_step_that_runs_as_the_agent(world, tmp_path):
    from personalclaw.workflows import store
    from personalclaw.workflows.bindings import BindingContext
    from personalclaw.workflows.engine import dispatch_stage
    from personalclaw.workflows.models import Node, RunStatus, WorkflowRun

    store.create(WorkflowRun(id="run-notes", workflow_name="notes-check", status=RunStatus.RUNNING))
    node = Node.from_dict(
        {
            "kind": "stage",
            "id": "check",
            "config": {"prompt": "Check the notes.", "agent": AGENT, "capability": "mutating"},
        }
    )
    with patch("personalclaw.workflows.leases.config_dir", lambda: tmp_path):
        result = await dispatch_stage(
            node,
            BindingContext(),
            subagents=world.subagents,
            run_id="run-notes",
            instance_path="root",
            unattended=True,
        )
    info = world.subagents.get(result.output["subagent_id"])
    await world.spawned(info)
    world.held()


@pytest.mark.asyncio
async def test_a_loops_worker_running_as_the_agent(world):
    from personalclaw.dashboard.chat_runner import run_chat
    from personalclaw.loop import manager, store
    from personalclaw.loop.loop import Loop

    class _Nudges:
        """The loop's nudge service, which the gateway's nudge driver turns into worker turns."""

        def __init__(self) -> None:
            self.added: list[dict] = []

        async def add(self, **kwargs: Any) -> Any:
            self.added.append(kwargs)
            return None

        def get_by_session(self, _name: str) -> Any:
            return None

        def list_all(self) -> list:
            return []

    loop = store.create(
        Loop(
            id="",
            name="Notes check",
            kind="goal",
            task="check the notes",
            kind_config={"goal_type": "open_ended"},
            agent=AGENT,
            attended=False,
        )
    )
    nudges = _Nudges()
    await manager.start(world.state, nudges, loop.id)
    (armed,) = nudges.added
    worker = world.state._sessions[armed["session_name"]]
    assert worker.agent == AGENT
    # One cycle, as the nudge driver runs it: the nudge on the worker's session, then its turn.
    worker.append("nudge", armed["message"], "msg msg-nudge")
    await run_chat(world.state, worker, armed["message"])
    world.held()

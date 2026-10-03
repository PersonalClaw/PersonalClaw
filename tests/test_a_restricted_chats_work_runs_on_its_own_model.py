"""A Temporary or Incognito chat's subagents, batches and workflow steps run on the chat's model.

An Incognito or Temporary chat's words reach no model but the one it runs on. The work it starts
away from its own turn was held to that too, but nothing told that work which model the chat runs
on: the request the agent's ``subagent_run`` tool makes runs as the chat, and its scope never
learned the model the turn had named. So the subagent was refused every model, its chat's own
included ("…: here:tiny was not asked."), and a subagent could not run at all in such a chat. A
model-less spawn was also built on the Orchestration chain, a model beside the chat's, and a
subagent that started from another's freed slot, or a workflow step resumed after a restart, ran
in whatever work happened to start it: unmarked and on any model, or refused the model it should
have had.

The behaviour now, driven through the real turn engine, the gateway's spawn route behind its
memory-write middleware, the real subagent manager, the real workflow controller and the real
resolution seam, over scripted models:

* a Temporary chat's subagent, and an Incognito chat's, runs on the chat's own model, and the
  models bound for Orchestration and Background are never asked;
* the model is handed on the way the chat's mode is: to the requests the chat's tools make, to a
  subagent and its own subagents, to a queued subagent whichever agent's end frees its slot, and to
  a workflow run the chat starts, whose record keeps it, so its steps keep the mode and stay on the
  model after a restart;
* an agent CLI's chat stays on that CLI: its subagent is started on the same runtime;
* a start that cannot run on the chat's model says so, in words, before anything is sent.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from personalclaw import memory_writes, session_restrictions
from personalclaw.llm.base import EVENT_COMPLETE, EVENT_TEXT_CHUNK, LLMEvent
from personalclaw.llm.capabilities import Capability, ProviderCapability
from personalclaw.llm.events import EVENT_TOOL_CALL, AgentEvent
from personalclaw.llm.registry import ProviderEntry, ProviderRegistry
from personalclaw.tool_providers.base import ToolDefinition, ToolProvider, ToolResult

#: The chat's own model, on this machine; the model bound for Orchestration (and Reasoning); the
#: model bound for Background; an agent CLI a chat can run on. Invented names.
HERE, HERE_REF = "here", "here:tiny"
RELAY, RELAY_REF = "relay", "relay:swift"
FAR, FAR_REF = "far", "far:wide"
CLI = "acp:example-cli"

TEMPORARY_CHAT = "chat-21-1791000001"
INCOGNITO_CHAT = "chat-22-1791000002"
NORMAL_CHAT = "chat-23-1791000003"

TASK = "List the three cheapest kettles on the shopping note."
ANSWER = "Kettle A, Kettle B and Kettle C."

NOT_ASKED = "so nothing from it is sent to any model but the one it runs on"


# ── the models Settings holds ────────────────────────────────────────────────────────────────


class _Model:
    """A model the resolution seam builds for an entry: it answers, and records that it was asked.
    A model the test holds (``world.held``) answers only once it is let go."""

    supports_tools = False

    def __init__(self, entry: str, world: SimpleNamespace) -> None:
        self.entry = entry
        self.world = world

    async def start(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None

    async def complete(self, messages: list[dict], **_kw: Any):
        self.world.asked.append(self.entry)
        held = self.world.held.get(self.entry)
        if held is not None:
            await held.wait()
        yield LLMEvent(kind=EVENT_TEXT_CHUNK, text=ANSWER)
        yield LLMEvent(kind=EVENT_COMPLETE, input_tokens=40, output_tokens=6)


class _AgentCli:
    """An agent CLI's runtime as the registry builds it for its entry: it names its runtime, runs
    the task it is handed and records that it was asked."""

    def __init__(self, world: SimpleNamespace) -> None:
        self.world = world
        self.provider_id = CLI

    async def start(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None

    async def stream(self, message: str):
        self.world.asked.append(CLI)
        yield LLMEvent(kind=EVENT_TEXT_CHUNK, text=ANSWER)
        yield LLMEvent(kind=EVENT_COMPLETE, input_tokens=4, output_tokens=6)


def _capability(type_: str) -> ProviderCapability:
    return ProviderCapability(
        type=type_,
        capabilities=frozenset({Capability.CHAT}),
        supports_streaming=True,
        supports_tools=True,
        supports_embeddings=False,
        supports_vision=False,
        max_context_tokens=32_768,
    )


@pytest.fixture
def world(monkeypatch) -> SimpleNamespace:
    """Settings → Providers and Settings → Models: the chat's model on this machine, other models
    bound for Orchestration, Reasoning and Background, and an agent CLI. ``asked`` is every entry
    asked, in order."""
    from personalclaw.guardrails.breaker import reset_breakers
    from personalclaw.llm.acp_agent import ACP_AGENT_CAPABILITY

    w = SimpleNamespace(asked=[], held={})
    registry = ProviderRegistry()

    def _factory(*, entry: ProviderEntry, session_key: str | None = None, **_kw: Any):
        return _Model(entry.name, w)

    registry.register_type(_capability("offline-weights"), _factory)
    registry.register_type(_capability("cloud-api"), _factory)
    registry.register_type(ACP_AGENT_CAPABILITY, lambda **_kw: _AgentCli(w))
    registry.register_entry(ProviderEntry(name=HERE, type="offline-weights", model="tiny"))
    registry.register_entry(ProviderEntry(name=RELAY, type="cloud-api", model="swift"))
    registry.register_entry(ProviderEntry(name=FAR, type="cloud-api", model="wide"))
    registry.register_entry(ProviderEntry(name=CLI, type=ACP_AGENT_CAPABILITY.type, model=""))
    w.active = {
        "chat": [HERE_REF],
        "orchestration": [RELAY_REF],
        "reasoning": [RELAY_REF],
        "background": [FAR_REF],
    }
    monkeypatch.setattr("personalclaw.llm.registry.get_default_registry", lambda: registry)
    monkeypatch.setattr("personalclaw.providers.use_cases.load_active_models", lambda: w.active)
    reset_breakers()
    yield w
    reset_breakers()


# ── the gateway: its subagent manager, its spawn route, its turn engine ──────────────────────


class _Sessions:
    """The session manager's cold start for a subagent: the gateway's own provider factory builds
    its runtime, as ``SessionManager.get_or_create`` does for a key with no warm process."""

    def __init__(self) -> None:
        from personalclaw.providers.provider_bridge import create_provider_factory

        self.factory = create_provider_factory()
        self.built: list[dict[str, Any]] = []
        self.count = 0

    async def get_or_create(self, key: str, agent=None, channel_id=None, approval_policy="",
                            model=None, cwd=None, extra_env=None, *, approval_source=None,
                            **kwargs: Any):  # fmt: skip
        from personalclaw.session import _push_approval_policy

        self.built.append({"key": key, "model": model, **kwargs})
        provider = self.factory(
            key, agent=agent, channel_id=channel_id, model_override=model, cwd=cwd,
            extra_env=extra_env, **kwargs,
        )  # fmt: skip
        await provider.start()
        _push_approval_policy(provider, approval_policy, approval_source)
        return provider, True, False

    def get_agent(self, key: str) -> str:
        return ""

    def get_pid(self, key: str) -> None:
        return None

    def has_session(self, key: str) -> bool:
        return False

    def get_approval_policy(self, key: str) -> str:
        return ""

    def release(self, key: str, cleanup: bool = False) -> None:
        return None

    async def reset(self, key: str) -> None:
        return None

    async def record_failure(self, key: str) -> None:
        return None

    def record_success(self, key: str) -> None:
        return None


def _manager(*, max_concurrent: int = 4):
    from personalclaw.subagent import SubagentManager

    ctx = MagicMock()
    ctx.hooks.auto_approve_subagent_spawn = True
    ctx.build_message = MagicMock(side_effect=lambda msg, *_a, **_k: (msg, None))
    return SubagentManager(sessions=_Sessions(), ctx_builder=ctx, max_concurrent=max_concurrent)


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    """A home of the test's own, where subagents keep their folders and runs their records. Yields
    the list of keys the test marked, which are cleared from the process-wide registry after it,
    as are the chats' own."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    monkeypatch.setattr("personalclaw.workflows.leases.config_dir", lambda: tmp_path)
    chats = [
        k for c in (TEMPORARY_CHAT, INCOGNITO_CHAT, NORMAL_CHAT) for k in (c, f"dashboard:{c}")
    ]
    keys: list[str] = list(chats)
    for key in chats:
        session_restrictions.clear(key)
    with (
        patch("personalclaw.subagent_persistence._subagents_dir", lambda: tmp_path / "agents"),
        patch("personalclaw.subagent.Stats"),
        patch("personalclaw.subagent.sel"),
    ):
        yield keys
    for key in keys:
        session_restrictions.clear(key)


def _gateway(state, *extra_routes: tuple[str, Any]):
    """The gateway's API as an agent's tool reaches it: its memory-write middleware, which runs
    each request as the session it names, and the spawn route ``subagent_run`` posts to."""
    from aiohttp import web

    from personalclaw.dashboard.handlers.messaging import api_spawn
    from personalclaw.dashboard.memory_write_gate import memory_write_middleware

    app = web.Application(middlewares=[memory_write_middleware()])
    app["state"] = state
    app.router.add_post("/api/spawn", api_spawn)
    for path, handler in extra_routes:
        app.router.add_post(path, handler)
    return app


def _state(manager, *chats: tuple[str, str]):
    """The dashboard state the gateway holds: its subagent manager and the live chats."""
    from personalclaw.dashboard.state import DashboardState

    state = DashboardState(sessions=MagicMock(count=0), start_time=0.0, subagents=manager)
    state.push_sessions_update = MagicMock()
    for name, mode in chats:
        state.get_or_create_session(name, memory_mode=mode)
    return state


def _turn_named(name: str, mode: str, model: str = HERE_REF) -> None:
    """What a turn of the chat ``name`` leaves for its work once it has named the model it runs
    on (``chat_runner.run_chat``), in the turn's own scope."""
    with memory_writes.derived_from(f"dashboard:{name}", name, memory_mode=mode):
        memory_writes.answered_by(model)


async def _spawn(client, key: str, task: str = TASK) -> dict[str, Any]:
    """``subagent_run``'s request, as the agent's tool server makes it for the session ``key``."""
    resp = await client.post(
        "/api/spawn",
        json={"task": task, "parent_session": key},
        headers={"X-Session-Key": key},
    )
    return await resp.json()


async def _ended(manager, *, count: int = 1):
    """Every subagent the manager started, once ``count`` of them have ended."""
    for _ in range(1000):
        agents = manager.all_agents
        if len(agents) >= count and all(a.done for a in agents):
            return agents
        await asyncio.sleep(0.01)
    raise AssertionError(f"the subagents did not end: {manager.all_agents}")


class _SpawnTool(ToolProvider):
    """``subagent_run`` as the agent's tool server serves it: a request to the gateway's spawn
    route naming the chat it serves (``mcp_subagents``, over ``mcp_core._post``)."""

    def __init__(self, session_key: str) -> None:
        self.client: Any = None  # the gateway's, once it serves
        self.session_key = session_key
        self.answers: list[dict[str, Any]] = []

    @property
    def name(self) -> str:
        return "subagents"

    @property
    def display_name(self) -> str:
        return "Subagents"

    async def list_tools(self) -> list[ToolDefinition]:
        return [
            ToolDefinition(
                name="subagent_run",
                description="Start a subagent.",
                parameters={"type": "object"},
                requires_approval=False,
            )
        ]

    async def invoke(self, tool_name: str, arguments: dict[str, Any]) -> ToolResult:
        answer = await _spawn(self.client, self.session_key, arguments["task"])
        self.answers.append(answer)
        return ToolResult(success=answer.get("status") == "spawned", output=json.dumps(answer))


class _ChatModel:
    """The chat's own model as its turn calls it: it starts a subagent, then answers."""

    supports_tools = True
    _model = "tiny"
    served_ref = HERE_REF

    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, messages, *, tools=None, model=None, reasoning_effort=""):
        self.calls += 1
        if self.calls == 1:
            yield AgentEvent(
                kind=EVENT_TOOL_CALL,
                tool_call_id="c1",
                title="subagent_run",
                tool_input=json.dumps({"task": TASK}),
            )
            yield AgentEvent(kind=EVENT_COMPLETE)
            return
        yield AgentEvent(kind=EVENT_TEXT_CHUNK, text="A subagent is on it.")
        yield AgentEvent(kind=EVENT_COMPLETE)

    async def cancel(self, *, wait_ack_timeout: float = 0.0) -> str:
        return "acked"


async def _turn_that_starts_a_subagent(tmp_path: Path, name: str, mode: str):
    """A turn of the chat ``name`` in ``mode`` whose agent calls ``subagent_run``: the real turn
    engine names the chat's model, the tool's request reaches the real spawn route, and the
    subagent's runtime is built by the real resolution seam."""
    from aiohttp.test_utils import TestClient, TestServer

    from personalclaw.agents.native.runtime import NativeAgentRuntime
    from personalclaw.agents.provider import AgentRuntimeDefinition
    from personalclaw.context import ContextBuilder
    from personalclaw.dashboard.chat_runner import run_chat
    from personalclaw.history import ConversationLog
    from personalclaw.memory import MemoryStore
    from personalclaw.skills import SkillsLoader

    manager = _manager()
    tool = _SpawnTool(f"dashboard:{name}")
    chat_runtime = NativeAgentRuntime(
        definition=AgentRuntimeDefinition(name="PersonalClaw", provider="native", model="tiny"),
        model_provider=_ChatModel(),
        tool_providers=[tool],
    )
    await chat_runtime.start()
    chat_runtime.set_approval_policy("auto")
    state = _state(manager)
    state.sessions._sessions = {}
    state.sessions.get_pid = MagicMock(return_value=None)
    state.sessions.get_channel_link = MagicMock(return_value=(None, None))
    state.sessions.get_or_create = AsyncMock(return_value=(chat_runtime, True, False))
    state.sessions.record_failure = AsyncMock()
    log = ConversationLog(base_dir=tmp_path / "sessions")
    state.conversation_log = log
    state.context_builder = ContextBuilder(
        memory=MemoryStore(workspace=tmp_path / "ws"),
        skills=SkillsLoader(skills_path=tmp_path / "skills", install_builtins=False),
        conversation_log=log,
    )
    state._hook_store = None
    state.broadcast_ws = MagicMock()
    async with TestClient(TestServer(_gateway(state))) as client:
        tool.client = client
        session = state.get_or_create_session(name, memory_mode=mode)
        session.append("user", "Find me a kettle.", "msg msg-u")
        with patch("personalclaw.dashboard.chat_runner.sel", MagicMock()):
            await run_chat(state, session, "Find me a kettle.")
        agents = await _ended(manager)
    return SimpleNamespace(manager=manager, agents=agents, tool=tool, session=session)


# ── a Temporary chat's subagent, and an Incognito chat's ─────────────────────────────────────

RESTRICTED = [(TEMPORARY_CHAT, "temporary"), (INCOGNITO_CHAT, "incognito")]


@pytest.mark.asyncio
@pytest.mark.parametrize(("name", "mode"), RESTRICTED)
async def test_a_restricted_chats_subagent_runs_on_the_chats_model(
    tmp_path, world, isolated, name, mode
):
    """🔴 Red before the fix: the spawn's request never learned the model the chat's turn named,
    so the subagent ended "This chat is …, so nothing from it is sent to any model but the one it
    runs on: relay:swift was not asked." — and it was built on the Orchestration model at all."""
    ran = await _turn_that_starts_a_subagent(tmp_path, name, mode)
    isolated.extend(f"subagent:{a.id}" for a in ran.agents)

    assert ran.tool.answers and ran.tool.answers[0].get("status") == "spawned", ran.tool.answers
    (info,) = ran.agents
    assert info.error == "", info.error
    assert ANSWER in info.result, info.result
    assert world.asked == [HERE], f"the subagent asked {world.asked}, not the chat's own model"


@pytest.mark.asyncio
@pytest.mark.parametrize(("name", "mode"), RESTRICTED)
async def test_a_chat_whose_orchestration_model_is_its_own_still_runs_its_subagent(
    tmp_path, world, isolated, name, mode
):
    """🔴 Red before the fix: the subagent was refused the very model the chat runs on, "…:
    here:tiny was not asked.", so subagents could not run at all in such a chat."""
    world.active["orchestration"] = [HERE_REF]
    ran = await _turn_that_starts_a_subagent(tmp_path, name, mode)
    isolated.extend(f"subagent:{a.id}" for a in ran.agents)

    (info,) = ran.agents
    assert info.error == "", info.error
    assert world.asked == [HERE]


class _CloudEmbeddings:
    """An embedding model bound in Settings → Models, somewhere else: it keeps every text sent."""

    name = "cloud-embed"
    display_name = "Cloud embeddings"

    def __init__(self) -> None:
        self.sent: list[str] = []

    async def is_available(self) -> bool:
        return True

    async def embed(self, text: str, model: str = "") -> list[float] | None:
        self.sent.append(text)
        return [1.0] * 8

    async def embed_batch(self, texts: list[str], model: str = "") -> list[list[float] | None]:
        self.sent.extend(texts)
        return [[1.0] * 8 for _ in texts]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("name", "mode"), [(INCOGNITO_CHAT, "incognito"), (NORMAL_CHAT, "persistent")]
)
async def test_an_incognito_chats_subagent_reads_memory_without_the_embedding_model(
    tmp_path, world, isolated, name, mode
):
    """An Incognito chat reads memory, by keyword, and so does its subagent's first prompt, which
    the real assembler builds over the real memory: the bound embedding model, a model beside the
    chat's, is sent nothing of it, and the subagent still runs on the chat's model. A normal
    chat's subagent searches with it, as before."""
    from aiohttp.test_utils import TestClient, TestServer

    from personalclaw.context import ContextBuilder
    from personalclaw.context_engine import set_engine
    from personalclaw.embedding_providers import registry as embedding_registry
    from personalclaw.memory import MemoryStore
    from personalclaw.skills import SkillsLoader
    from personalclaw.subagent import SubagentManager
    from personalclaw.vector_memory import VectorMemoryStore

    embeddings = _CloudEmbeddings()
    embedding_registry.register_provider(embeddings)  # type: ignore[arg-type]
    world.active["embedding"] = ["cloud-embed:embed-v1"]
    markdown = MemoryStore(workspace=tmp_path / "workspace")
    markdown.init()
    store = VectorMemoryStore(db_path=tmp_path / "memory.db", confidence_threshold=0.0)
    store.init()
    markdown.vector_store = store
    store.write_episodic("The kettle on the shopping note must be under 40 EUR.", tags=["kettle"])
    embeddings.sent.clear()
    builder = ContextBuilder(
        memory=markdown,
        skills=SkillsLoader(skills_path=tmp_path / "skills", install_builtins=False),
    )
    builder.get_memory_for = staticmethod(  # type: ignore[method-assign]
        lambda cwd=None, memory_store=None: markdown
    )
    set_engine(None)
    manager = SubagentManager(sessions=_Sessions(), ctx_builder=builder, is_yolo=lambda: True)
    _turn_named(name, mode)
    try:
        state = _state(manager, (name, mode))
        async with TestClient(TestServer(_gateway(state))) as client:
            await _spawn(client, f"dashboard:{name}", "Which kettle note did I keep?")
            (info,) = await _ended(manager)
    finally:
        embedding_registry.unregister_provider(embeddings.name)
        store.close()
    isolated.append(f"subagent:{info.id}")

    assert info.error == "", info.error
    if mode == "incognito":
        assert embeddings.sent == [], "the Incognito chat's subagent reached the embedding model"
        assert world.asked == [HERE]
    else:
        assert embeddings.sent, "a normal chat's subagent no longer searches with its embeddings"
        assert world.asked == [RELAY]


@pytest.mark.asyncio
async def test_a_normal_chats_subagent_still_runs_on_the_orchestration_model(
    tmp_path, world, isolated
):
    ran = await _turn_that_starts_a_subagent(tmp_path, NORMAL_CHAT, "persistent")

    (info,) = ran.agents
    assert info.error == "", info.error
    assert world.asked == [RELAY], "a normal chat's subagent no longer runs on Orchestration"


@pytest.mark.asyncio
@pytest.mark.parametrize(("name", "mode"), RESTRICTED)
async def test_a_subagents_own_subagent_stays_on_the_chats_model(world, isolated, name, mode):
    """A subagent's tool names the subagent in its request: what it starts is handed the model
    the subagent was handed, so the chat's work never leaves it however deep it goes."""
    from aiohttp.test_utils import TestClient, TestServer

    _turn_named(name, mode)
    manager = _manager()
    async with TestClient(TestServer(_gateway(_state(manager, (name, mode))))) as client:
        first = await _spawn(client, f"dashboard:{name}")
        await _ended(manager)
        child = f"subagent:{first['id']}"
        second = await _spawn(client, child)
        agents = await _ended(manager, count=2)
    isolated.extend(["dashboard:" + name, name, *(f"subagent:{a.id}" for a in agents)])

    grandchild = manager.get(second["id"])
    assert grandchild is not None and grandchild.error == "", grandchild
    assert grandchild.parent_session_key == child
    assert world.asked == [HERE, HERE]
    blocks = memory_writes.blocks_memory_writes(f"subagent:{grandchild.id}")
    assert blocks, "the subagent's own subagent does not keep its chat's mode"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("waiting", "freeing", "runs_on"),
    [
        ((TEMPORARY_CHAT, "temporary"), (NORMAL_CHAT, "persistent"), HERE),
        ((NORMAL_CHAT, "persistent"), (TEMPORARY_CHAT, "temporary"), RELAY),
    ],
    ids=["temporary-waits-for-a-normal-chats-slot", "normal-waits-for-a-temporary-chats-slot"],
)
async def test_a_queued_subagent_runs_as_its_own_work_whoever_frees_its_slot(
    world, isolated, waiting, freeing, runs_on
):
    """A spawn past the limit waits, and starts when a running agent ends: from that agent's own
    run. 🔴 Red before the fix: it ran as that agent's work, so a Temporary chat's subagent freed
    by a normal chat's ran unrestricted on the Orchestration model, and a normal chat's subagent
    freed by a Temporary chat's was refused its model."""
    from aiohttp.test_utils import TestClient, TestServer

    for chat in (waiting, freeing):
        _turn_named(*chat, model=HERE_REF)
    first_model = HERE if freeing[1] == "temporary" else RELAY
    world.held[first_model] = asyncio.Event()
    manager = _manager(max_concurrent=1)
    state = _state(manager, waiting, freeing)
    async with TestClient(TestServer(_gateway(state))) as client:
        running = await _spawn(client, f"dashboard:{freeing[0]}")
        queued = await _spawn(client, f"dashboard:{waiting[0]}")
        assert manager.get(queued["id"]).queued, "the second spawn did not wait for a slot"
        world.held[first_model].set()
        agents = await _ended(manager, count=2)
    isolated.extend(f"dashboard:{c[0]}" for c in (waiting, freeing))
    isolated.extend(f"subagent:{a.id}" for a in agents)

    late = manager.get(queued["id"])
    assert late.error == "", late.error
    assert manager.get(running["id"]).error == ""
    assert world.asked == [first_model, runs_on], world.asked


# ── a workflow run a restricted chat starts ──────────────────────────────────────────────────


def _one_stage_spec() -> dict[str, Any]:
    return {
        "name": "kettle-shortlist",
        "root": {
            "kind": "sequence",
            "id": "root",
            "children": [{"kind": "stage", "id": "shortlist", "config": {"prompt": TASK}}],
        },
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(("name", "mode"), RESTRICTED)
async def test_a_run_a_restricted_chat_starts_records_the_chats_model_with_its_mode(
    world, isolated, monkeypatch, name, mode
):
    """A batch or a workflow the chat's agent starts is started by its request (the batch route,
    ``workflow_start``): the run's record keeps the chat's model beside its mode, which a restart
    replays. 🔴 Red before the fix: the record kept the mode alone."""
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer

    from personalclaw.workflows import defs, native_defs, ownership, service, store
    from personalclaw.workflows.models import OriginKind

    monkeypatch.setattr(defs, "_providers", {})
    defs.register_provider(native_defs.NativeWorkflowDefProvider())
    spec = _one_stage_spec()
    authored = await service.author_def(name=spec["name"], root=spec["root"], strict=False)
    assert authored.get("ok"), authored
    started: dict[str, Any] = {}

    async def _start(request: web.Request) -> web.Response:
        started.update(
            await service.start_run(
                name=spec["name"],
                origin_kind=OriginKind.SUBAGENT_TOOL,
                session_key=request.headers["X-Session-Key"],
            )
        )
        return web.json_response({})

    from personalclaw.history import ConversationLog

    # The chat's transcript as the dashboard keeps it, with its mode in its metadata.
    log = ConversationLog()
    log.append(f"dashboard:{name}", "user", "Shortlist the kettles in a batch.")
    log.update_metadata(f"dashboard:{name}", {"memory_mode": mode})
    _turn_named(name, mode)
    state = _state(_manager(), (name, mode))
    async with TestClient(TestServer(_gateway(state, ("/start", _start)))) as client:
        await client.post("/start", headers={"X-Session-Key": f"dashboard:{name}"})

    run = store.get(started["run_id"])
    assert ownership.run_mode(run).value == mode
    assert ownership.run_model(run) == HERE_REF, run.extra


@pytest.mark.parametrize("mode", ["incognito", "temporary"])
def test_a_restricted_runs_step_after_a_restart_keeps_the_mode_and_the_model(world, isolated, mode):
    """After a restart nothing in the process says what the run is: no request, no mark, no
    model. Its record does, and its steps inherit both from it. 🔴 Red before the fix: an
    Incognito run's step started unmarked, so it could write memory, and every restricted run's
    step ran on the Orchestration model."""
    from personalclaw.workflows import ownership, store
    from personalclaw.workflows.controller import EngineServices, RunController
    from personalclaw.workflows.models import OriginKind, RunOrigin, RunStatus, WorkflowRun

    spec = _one_stage_spec()
    run = store.create(
        WorkflowRun(
            id="",
            workflow_name=spec["name"],
            origin=RunOrigin(kind=OriginKind.CHAT, session_key=f"dashboard:{INCOGNITO_CHAT}"),
            extra=ownership.stamp_run_mode({}, ownership.MemoryMode(mode), model=HERE_REF),
        )
    )
    store.write_spec(run.id, spec)
    manager = _manager()
    controller = RunController(run, spec, services=EngineServices(subagents=manager))

    status = asyncio.run(controller.run_to_completion(timeout=25.0))

    step = manager.get(controller.instances["root.children[0]"].subagent_id)
    child = f"subagent:{step.id}"
    isolated.extend([child, f"dashboard:{INCOGNITO_CHAT}", ownership.owned_key(run.id, "run")])
    assert step.error == "", step.error
    assert status is RunStatus.COMPLETE, status
    assert session_restrictions.is_restricted(child), "the step started without its chat's mode"
    assert session_restrictions.is_temporary(child) is (mode == "temporary")
    assert world.asked == [HERE], f"the step asked {world.asked}, not its chat's own model"


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["incognito", "temporary"])
async def test_a_subworkflow_and_a_fork_of_a_restricted_run_keep_its_mode_and_model(
    world, isolated, monkeypatch, mode
):
    """A run a restricted run starts (a subworkflow node) and a fork of it continue its work, so
    they keep what it keeps on their own records. 🔴 Red before the fix: both were created with
    no mode, so after a restart their steps wrote memory and reached any model."""
    from personalclaw.workflows import checkpoints, defs, native_defs, ownership, service, store
    from personalclaw.workflows.controller import EngineServices
    from personalclaw.workflows.models import RunStatus, WorkflowRun
    from personalclaw.workflows.watchdog import WorkflowWatchdog

    monkeypatch.setattr(defs, "_providers", {})
    defs.register_provider(native_defs.NativeWorkflowDefProvider())
    child_root = {"kind": "transform", "id": "c", "config": {"expr": "the shortlist"}}
    assert (await service.author_def(name="shortlist", root=child_root, strict=False))["ok"]
    spec = {
        "name": "kettles",
        "root": {
            "kind": "sequence",
            "id": "s",
            "children": [{"kind": "subworkflow", "id": "nested", "config": {"ref": "shortlist"}}],
        },
    }
    extra = ownership.stamp_run_mode({}, ownership.MemoryMode(mode), model=HERE_REF)
    run = store.create(WorkflowRun(id="", workflow_name="kettles", extra=extra))
    store.write_spec(run.id, spec)
    isolated.append(ownership.owned_key(run.id, "run"))

    controller = await WorkflowWatchdog(None, EngineServices()).launch(run, spec)
    assert await controller.run_to_completion(timeout=25.0) is RunStatus.COMPLETE
    (child,) = [r for r in store.list_runs()[0] if r.parent_run_id == run.id]
    fork = checkpoints.fork_run(store.get(run.id), spec, controller.instances).child
    isolated.extend(ownership.owned_key(r.id, "run") for r in (child, fork))

    for started in (store.get(child.id), store.get(fork.id)):
        assert ownership.run_mode(started).value == mode, started.extra
        assert ownership.run_model(started) == HERE_REF, started.extra


def test_a_normal_runs_step_still_runs_on_the_orchestration_model(world, isolated):
    from personalclaw.workflows import store
    from personalclaw.workflows.controller import EngineServices, RunController
    from personalclaw.workflows.models import RunStatus, WorkflowRun

    spec = _one_stage_spec()
    run = store.create(WorkflowRun(id="", workflow_name=spec["name"]))
    store.write_spec(run.id, spec)
    manager = _manager()
    controller = RunController(run, spec, services=EngineServices(subagents=manager))

    assert asyncio.run(controller.run_to_completion(timeout=25.0)) is RunStatus.COMPLETE
    step = manager.get(controller.instances["root.children[0]"].subagent_id)
    assert not session_restrictions.is_restricted(f"subagent:{step.id}")
    assert world.asked == [RELAY]


# ── a chat on an agent CLI ───────────────────────────────────────────────────────────────────


def test_a_turn_names_the_model_its_runtime_runs_on():
    """PersonalClaw's own loop names the entry and model it sends to; an agent CLI names its
    runtime, which is all of what it runs on that PersonalClaw sees."""
    from personalclaw.providers.provider_bridge import turn_model_ref

    assert turn_model_ref(SimpleNamespace(served_model_ref=HERE_REF)) == HERE_REF
    assert turn_model_ref(SimpleNamespace(provider_id=CLI)) == CLI
    assert turn_model_ref(SimpleNamespace(provider_id="native")) == ""


@pytest.mark.asyncio
async def test_an_agent_cli_chats_subagent_starts_on_the_same_cli(world, isolated):
    """The CLI runs PersonalClaw's tools in a process of its own, which asks the gateway for each
    spawn as the chat. 🔴 Red before the fix: the chat's turn named no model at all, so its
    subagent could run on none."""
    from aiohttp.test_utils import TestClient, TestServer

    from personalclaw.providers.provider_bridge import turn_model_ref

    _turn_named(TEMPORARY_CHAT, "temporary", model=turn_model_ref(_AgentCli(world)))
    isolated.extend(["dashboard:" + TEMPORARY_CHAT, TEMPORARY_CHAT])
    manager = _manager()
    async with TestClient(
        TestServer(_gateway(_state(manager, (TEMPORARY_CHAT, "temporary"))))
    ) as client:
        await _spawn(client, f"dashboard:{TEMPORARY_CHAT}")
        (info,) = await _ended(manager)
    isolated.append(f"subagent:{info.id}")

    assert info.error == "", info.error
    assert ANSWER in info.result
    assert world.asked == [CLI], "the subagent left the agent CLI its chat runs on"
    (built,) = manager._sessions.built
    assert built["provider_kind"] == CLI and built["model"] is None, built


def test_a_restricted_chats_work_does_not_start_an_agent_cli_beside_its_model(world):
    """The agent CLI an agent's work would start is a model beside the chat's, refused before it
    is launched."""
    from personalclaw.providers.provider_bridge import resolve_provider_for_use_case

    with memory_writes.derived_from(f"dashboard:{TEMPORARY_CHAT}", memory_mode="temporary"):
        memory_writes.answered_by(HERE_REF)
        with pytest.raises(memory_writes.OtherModelRefused) as refused:
            resolve_provider_for_use_case(
                "chat",
                session_key="subagent:c0ffee01",
                provider_kind=CLI,
                model_axis="orchestration",
            )
    assert str(refused.value) == f"This chat is Temporary, {NOT_ASKED}: {CLI} was not asked."
    assert world.asked == []


# ── a start that cannot run on the chat's model ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_spawn_that_names_another_model_says_so(world, isolated):
    """A workflow step or an automation can name its own model. In a restricted chat's work that
    is a model beside the chat's: refused in words, and nothing is sent to it."""
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer

    async def _pinned(request: web.Request) -> web.Response:
        key = request.headers["X-Session-Key"]
        info = request.app["state"].subagents.spawn(TASK, parent_session_key=key, model=RELAY_REF)
        return web.json_response({"id": info.id})

    _turn_named(INCOGNITO_CHAT, "incognito")
    isolated.extend(["dashboard:" + INCOGNITO_CHAT, INCOGNITO_CHAT])
    manager = _manager()
    state = _state(manager, (INCOGNITO_CHAT, "incognito"))
    async with TestClient(TestServer(_gateway(state, ("/pinned", _pinned)))) as client:
        await client.post("/pinned", headers={"X-Session-Key": f"dashboard:{INCOGNITO_CHAT}"})
        (info,) = await _ended(manager)
    isolated.append(f"subagent:{info.id}")

    assert info.error == f"This chat is Incognito, {NOT_ASKED}: {RELAY_REF} was not asked."
    assert world.asked == []


@pytest.mark.asyncio
async def test_a_spawn_whose_chat_model_is_not_known_says_so_and_builds_nothing(world, isolated):
    """A chat whose turn has not named its model in this gateway (it last ran before a restart)
    leaves its work nothing to stay on. 🔴 Red before the fix: the subagent was built on the
    Orchestration model and refused there, naming a model it should never have tried."""
    from aiohttp.test_utils import TestClient, TestServer

    manager = _manager()
    async with TestClient(
        TestServer(_gateway(_state(manager, (TEMPORARY_CHAT, "temporary"))))
    ) as client:
        await _spawn(client, f"dashboard:{TEMPORARY_CHAT}")
        (info,) = await _ended(manager)
    isolated.append(f"subagent:{info.id}")

    assert info.error == (
        f"This chat is Temporary, {NOT_ASKED}, and this work was not told which model that is."
    )
    assert manager._sessions.built == [], "a runtime was built for a model nobody named"
    assert world.asked == []

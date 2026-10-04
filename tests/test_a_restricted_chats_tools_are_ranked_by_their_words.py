"""An Incognito or Temporary chat's words never reach the embedding model to rank its tools.

A turn whose catalog is larger than the schemas it may carry (more than 48 tools, or more than its
window affords) ranks the catalog against what was asked, and ranking by meaning embeds the request
with the embedding model bound in Settings → Models. In an Incognito or Temporary chat that model
is not the one the chat runs on, so the request is never handed to it: the chat's turn, every
subagent it starts and a side question asked beside it rank their tools by their words. A normal
chat still ranks by meaning.

The defect this pins: a side question asked beside such a chat ran as no chat's work, so the
embedding model was sent the question together with the conversation it reads. The chat's own
turn and its subagents were already held to the rule by the embedding functions' own check; these
pin that as well, over a real native runtime whose catalog has to be ranked, with an embedding
server that keeps every text it is sent.
"""

from __future__ import annotations

import asyncio
import hashlib
import threading
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from personalclaw import memory_writes, session_restrictions
from personalclaw.agents.native import tool_vectors
from personalclaw.agents.native.runtime import NativeAgentRuntime
from personalclaw.agents.provider import AgentRuntimeDefinition
from personalclaw.dashboard.state import _ChatSession
from personalclaw.embedding_providers import registry as embedding_registry
from personalclaw.llm.events import EVENT_COMPLETE, EVENT_TEXT_CHUNK, AgentEvent
from personalclaw.providers.provider_bridge import turn_model_ref
from personalclaw.tool_providers.base import ToolDefinition, ToolProvider, ToolResult

#: More tools than a turn carries in full (``tool_retrieval.DEFAULT_K`` is 48), so a turn ranks.
_TOOLS = 60

#: The chat's own model, on this machine. Invented.
HERE_REF = "here:tiny"
CHAT = "chat-31-1791000011"

#: What the person writes, and asks. Invented, in the shape of a training log.
REQUEST = "Plan my knee physio stretches for Tuesday mornings."
QUESTION = "Which stretch did I ask about?"
TASK = "Draft a weekly physio plan for the knee."
ANSWER = "Hamstring and calf stretches."
#: A word of the chat's that only its own text holds: no tool's description has it.
PRIVATE_WORD = "physio"

MODES = ["incognito", "temporary"]


class _EmbeddingServer:
    """A configured model provider that embeds: every text it was sent, in order."""

    def __init__(self) -> None:
        self.requests: list[list[str]] = []
        self._lock = threading.Lock()

    async def start(self) -> None:
        return None

    async def embed(self, inputs: list[str]) -> list[list[float]]:
        with self._lock:
            self.requests.append(list(inputs))
        return [_vector(text) for text in inputs]

    def sent(self, word: str) -> list[str]:
        """Every text sent that holds *word*."""
        return [t for request in self.requests for t in request if word in t]

    def tool_texts(self) -> list[str]:
        return [t for request in self.requests for t in request if t.startswith("tool_")]


def _vector(text: str) -> list[float]:
    digest = hashlib.sha256(text.encode()).digest()
    return [b / 255.0 + 0.01 for b in digest[:8]]


class _Registry:
    """The model-provider registry, holding the one configured embedding instance and no entry
    for anything else."""

    def __init__(self, server: _EmbeddingServer) -> None:
        self._server = server

    def build(self, name: str, **_kwargs: object) -> _EmbeddingServer:
        return self._server

    def get_entry(self, name: str) -> object:
        from personalclaw.llm.registry import ProviderResolutionError

        raise ProviderResolutionError(f"no configured entry {name!r}")


@pytest.fixture
def server(monkeypatch):
    """Embedding bound to a configured model provider's model, answering through the server, and
    an index of tool vectors of this test's own."""
    import personalclaw.llm.registry as llm_registry

    embedding = _EmbeddingServer()
    monkeypatch.setattr(llm_registry, "get_default_registry", lambda: _Registry(embedding))
    monkeypatch.setattr(
        embedding_registry, "_active_embedding_spec", lambda: ("local-embed", "embed-model")
    )
    monkeypatch.setattr(embedding_registry, "_ensure_scanned", lambda: None)
    monkeypatch.setattr(embedding_registry, "_providers", {})
    monkeypatch.setattr(tool_vectors, "_INDEX", tool_vectors.ToolVectors())
    keys = [CHAT, f"dashboard:{CHAT}"]
    for key in keys:
        session_restrictions.clear(key)
    yield embedding
    assert tool_vectors.tool_vectors().drain(20), "tool descriptions were still being embedded"
    for key in keys:
        session_restrictions.clear(key)


class _Model:
    """The chat's own model: it answers every turn at once."""

    supports_tools = True
    _model = "tiny"
    served_ref = HERE_REF

    async def complete(self, messages, *, tools=None, model=None, reasoning_effort=""):
        yield AgentEvent(kind=EVENT_TEXT_CHUNK, text=ANSWER)
        yield AgentEvent(kind=EVENT_COMPLETE)


class _Catalog(ToolProvider):
    @property
    def name(self) -> str:
        return "personalclaw-catalog"

    @property
    def display_name(self) -> str:
        return "Catalog"

    async def list_tools(self):
        return [
            ToolDefinition(
                name=f"tool_{i}",
                description=f"does thing {i}",
                provider="personalclaw-catalog",
                parameters={"type": "object", "properties": {}},
                requires_approval=False,
            )
            for i in range(_TOOLS)
        ]

    async def invoke(self, tool_name: str, arguments: dict) -> ToolResult:
        return ToolResult(success=True, output=f"ran {tool_name}")


async def _agent() -> NativeAgentRuntime:
    runtime = NativeAgentRuntime(
        definition=AgentRuntimeDefinition(name="a", provider="native", model="tiny", skills=[]),
        model_provider=_Model(),
        tool_providers=[_Catalog()],
    )
    await runtime.start()
    return runtime


async def _turn(runtime: NativeAgentRuntime, message: str) -> None:
    async for _ in runtime.stream(message):
        pass


async def _with_the_catalog_embedded(server: _EmbeddingServer) -> None:
    """Have the index hold every tool's vector, as a gateway that has run a while does, and forget
    what that sent: a turn compares its request only with tools that have one, so with none the
    request would not be embedded whatever the chat."""
    await _turn(await _agent(), "which tool does thing 1")
    assert await asyncio.to_thread(tool_vectors.tool_vectors().drain, 20)
    assert len(set(server.tool_texts())) == _TOOLS, "floor: the catalog was embedded"
    server.requests.clear()


def _ranked(runtime: NativeAgentRuntime) -> bool:
    """Whether the runtime's last turn ranked its catalog: some tools rode by name only."""
    retriever = runtime._tool_retriever
    return retriever.reduced() and retriever.hidden_count() > 0


# ── the chat's own turn ─────────────────────────────────────────────────────────────────────


@memory_writes.runs_as_its_session
async def _a_turn_of(state: Any, chat: _ChatSession, message: str, runtime) -> None:
    """A turn of *chat*, as the turn engine runs one (``chat_runner.run_chat``): as the chat's own
    work, naming the model its runtime runs on before anything is sent."""
    memory_writes.answered_by(turn_model_ref(runtime))
    await _turn(runtime, message)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", MODES)
async def test_a_restricted_chats_turn_ranks_its_tools_without_the_embedding_model(server, mode):
    await _with_the_catalog_embedded(server)
    runtime = await _agent()

    await _a_turn_of(None, _ChatSession(CHAT, memory_mode=mode), REQUEST, runtime)

    assert _ranked(runtime), "the catalog was not ranked, so nothing was measured"
    assert server.sent(PRIVATE_WORD) == [], f"the {mode} chat's request reached the embedding model"


@pytest.mark.asyncio
async def test_a_normal_chats_turn_still_ranks_its_tools_by_meaning(server):
    await _with_the_catalog_embedded(server)
    runtime = await _agent()

    await _a_turn_of(None, _ChatSession(CHAT, memory_mode="persistent"), REQUEST, runtime)

    assert _ranked(runtime)
    assert server.requests == [[REQUEST]], "a normal chat's request is no longer ranked by meaning"


# ── a subagent the chat starts ──────────────────────────────────────────────────────────────


class _Sessions:
    """The session manager as a subagent's run asks it: a runtime over the large catalog."""

    def __init__(self) -> None:
        self.built: list[NativeAgentRuntime] = []

    async def get_or_create(self, key: str, *_args: Any, **_kwargs: Any):
        runtime = await _agent()
        runtime.set_approval_policy("auto")
        self.built.append(runtime)
        return runtime, True, False

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


@pytest.fixture
def subagents(tmp_path):
    """The gateway's subagent manager, keeping its folders in the test's own, and the keys of the
    subagents it started, whose marks are cleared after the test."""
    from personalclaw.subagent import SubagentManager

    ctx = MagicMock()
    ctx.hooks.auto_approve_subagent_spawn = True
    ctx.build_message = MagicMock(side_effect=lambda msg, *_a, **_k: (msg, None))
    sessions = _Sessions()
    started: list[str] = []
    with (
        patch("personalclaw.subagent_persistence._subagents_dir", lambda: tmp_path / "agents"),
        patch("personalclaw.subagent.Stats"),
        patch("personalclaw.subagent.sel"),
    ):
        manager = SubagentManager(sessions=sessions, ctx_builder=ctx, is_yolo=lambda: True)
        yield SimpleNamespace(manager=manager, sessions=sessions, started=started)
    for key in started:
        session_restrictions.clear(key)


@memory_writes.runs_as_its_session
async def _a_turn_that_starts(state: Any, chat: _ChatSession, task: str, manager) -> None:
    """A turn of *chat* whose agent starts a subagent for *task*, once the turn has named its model
    (``subagent_run``'s request runs as the chat's work)."""
    memory_writes.answered_by(HERE_REF)
    manager.spawn(task, parent_session_key=f"dashboard:{chat.key}")


async def _spawned_by_a_turn(subagents, mode: str):
    """The subagent a turn of a chat in *mode* starts, run to its end."""
    await _a_turn_that_starts(None, _ChatSession(CHAT, memory_mode=mode), TASK, subagents.manager)
    for _ in range(1000):
        agents = subagents.manager.all_agents
        if agents and all(a.done for a in agents):
            subagents.started.extend(f"subagent:{a.id}" for a in agents)
            return agents
        await asyncio.sleep(0.01)
    raise AssertionError(f"the subagent did not end: {subagents.manager.all_agents}")


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", MODES)
async def test_a_restricted_chats_subagent_ranks_its_tools_without_the_embedding_model(
    server, subagents, mode
):
    await _with_the_catalog_embedded(server)

    (info,) = await _spawned_by_a_turn(subagents, mode)

    assert info.error == "", info.error
    (runtime,) = subagents.sessions.built
    assert _ranked(runtime), "the subagent's catalog was not ranked, so nothing was measured"
    assert server.sent(PRIVATE_WORD) == [], f"the {mode} chat's subagent's task was embedded"


@pytest.mark.asyncio
async def test_a_normal_chats_subagent_still_ranks_its_tools_by_meaning(server, subagents):
    await _with_the_catalog_embedded(server)

    (info,) = await _spawned_by_a_turn(subagents, "persistent")

    assert info.error == "", info.error
    (runtime,) = subagents.sessions.built
    assert _ranked(runtime)
    assert server.sent(PRIVATE_WORD), "a normal chat's subagent no longer ranks by meaning"


# ── a side question asked beside the chat ───────────────────────────────────────────────────


async def _side_question(mode: str) -> tuple[NativeAgentRuntime, Any]:
    """One side question beside a chat in *mode* that holds a turn of its own, through the side
    chat's own turn, its runtime over the large catalog."""
    from personalclaw.dashboard import side
    from personalclaw.dashboard.side_state import SideState

    runtime = await _agent()

    class _SideSessions:
        async def get_or_create(self, key: str, **_kwargs: Any):
            return runtime, True, False

        def release(self, key: str) -> None:
            return None

    state = SimpleNamespace(sessions=_SideSessions(), broadcast_ws=lambda *a, **k: None)
    chat = _ChatSession(CHAT, memory_mode=mode)
    chat.append("user", REQUEST, "msg msg-u", broadcast=False)
    chat.append("assistant", ANSWER, "msg msg-a", broadcast=False)
    chat._side = SideState(open=True)
    chat._side.last_run_id = "run-1"
    with patch("personalclaw.dashboard.chat_persistence.save_session_to_history"):
        await side._run_side_turn(state, CHAT, chat, chat._side, QUESTION, "run-1")
    return runtime, chat._side


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", MODES)
async def test_a_side_question_beside_a_restricted_chat_sends_the_embedding_model_nothing(
    server, mode
):
    """🔴 Red before the fix: the side question ran as no chat's work, so ranking its tools sent
    the question and the conversation it reads to the embedding model."""
    await _with_the_catalog_embedded(server)

    runtime, side_chat = await _side_question(mode)

    assert side_chat.messages[-1].role == "assistant", "the side question was not answered"
    assert ANSWER in side_chat.messages[-1].content
    assert _ranked(runtime), "the side turn's catalog was not ranked, so nothing was measured"
    assert server.sent(PRIVATE_WORD) == [], f"the {mode} chat's conversation was embedded"
    assert server.sent(QUESTION) == [], f"a question beside the {mode} chat was sent to be embedded"


@pytest.mark.asyncio
async def test_a_side_question_beside_a_normal_chat_is_still_ranked_by_meaning(server):
    await _with_the_catalog_embedded(server)

    runtime, side_chat = await _side_question("persistent")

    assert side_chat.messages[-1].role == "assistant"
    assert _ranked(runtime)
    assert server.sent(QUESTION), "a normal chat's side question is no longer ranked by meaning"

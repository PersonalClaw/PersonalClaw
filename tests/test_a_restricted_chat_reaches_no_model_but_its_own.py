"""An Incognito or Temporary chat's words reach no model but the one the chat runs on.

The defect this pins: an Incognito chat ran on a model on this machine, and its latest message was
still sent to the cloud embedding model bound in Settings → Models. Recall searched memory with it
on a worker thread, and the record of which chat that work was for did not reach the thread, so the
embedding functions could not tell they were working for an Incognito chat. A tool the agent used in
such a chat, such as structured extraction from a web page, ran on the Reasoning model, another
bound model that can be a cloud one, and nothing said so.

The behaviour now, driven through the real assembler, turn engine, native loop, resolution seam and
guard, over fake models:

* an Incognito turn reads memory without sending its words to the embedding model and leaves no
  mark on what it read, while a normal chat searches with its embedding model as before;
* a tool that needs a model answers on the chat's own model, stamped as serving in the bound
  model's place; a tool that needs a kind of model the chat's model is not (an image model) says it
  cannot run in this chat, and nothing is sent to that model;
* the tools an agent CLI runs, in a process of their own, run as the chat they serve: the gateway
  tells them what it is, and they hand nothing to another model for an Incognito one;
* a turn whose model fails, or could not be built, is not moved to another model;
* what the person gives the chat in a form its model cannot read (a file they attach, a screen they
  share) is read by the model they set up for it, as in any chat, and the chat still keeps nothing;
* every embedding call that reaches a model is recorded in the model-call log and, when it
  answers, in Usage, priced by the one pricing function; never its text.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from personalclaw import memory_writes
from personalclaw.embedding_providers import registry as embedding_registry
from personalclaw.embedding_providers.base import EmbeddingProvider
from personalclaw.llm.base import EVENT_COMPLETE, EVENT_TEXT_CHUNK, LLMEvent, ModelSubstitution
from personalclaw.llm.capabilities import Capability, ProviderCapability
from personalclaw.llm.events import EVENT_TOOL_CALL, AgentEvent
from personalclaw.llm.registry import ProviderEntry, ProviderRegistry
from personalclaw.tool_providers.base import ToolDefinition, ToolProvider, ToolResult

INCOGNITO = "dashboard:chat-7-1700000000"
NORMAL = "dashboard:chat-8-1700000100"

#: What the person writes. Invented content, in the shape of a training log.
_MESSAGE = "I skipped Tuesday's swim at the community pool. Adjust this week."
_REMEMBERED = "The user swims on Tuesdays at the community pool."

#: The chat's own model, on this machine, and the Reasoning model bound beside it, elsewhere.
HERE, HERE_REF = "here", "here:tiny"
RELAY, RELAY_REF = "relay", "relay:swift"
EMBED_REF = "cloud-embed:embed-v1"


def _vector(text: str) -> list[float]:
    """The text's words as a bag, so two texts that share words lie close together: what an
    embedding model does for these two, closely enough for recall to find one with the other."""
    vec = [0.0] * 16
    for word in re.findall(r"[a-z]+", text.lower()):
        stem = word[:-1] if word.endswith("s") else word
        vec[int(hashlib.sha256(stem.encode("utf-8")).hexdigest(), 16) % 16] += 1.0
    return vec


class _CloudEmbeddings(EmbeddingProvider):
    """An embedding model somewhere else: it keeps every text it is sent."""

    def __init__(self) -> None:
        self.sent: list[str] = []

    @property
    def name(self) -> str:
        return "cloud-embed"

    @property
    def display_name(self) -> str:
        return "Cloud embeddings"

    async def is_available(self) -> bool:
        return True

    async def embed(self, text: str, model: str = "") -> list[float] | None:
        self.sent.append(text)
        return _vector(text)

    async def embed_batch(self, texts: list[str], model: str = "") -> list[list[float] | None]:
        self.sent.extend(texts)
        return [_vector(t) for t in texts]


@pytest.fixture
def bindings(monkeypatch) -> dict[str, list[str]]:
    """Settings → Models, as the resolution seam reads it."""
    active: dict[str, list[str]] = {
        "chat": [HERE_REF],
        "reasoning": [RELAY_REF],
        "orchestration": [RELAY_REF],
        "embedding": [EMBED_REF],
    }
    monkeypatch.setattr("personalclaw.providers.use_cases.load_active_models", lambda: active)
    return active


@pytest.fixture
def cloud_embeddings(bindings):
    provider = _CloudEmbeddings()
    embedding_registry.register_provider(provider)
    yield provider
    embedding_registry.unregister_provider(provider.name)


@pytest.fixture
def memory(tmp_path):
    """The memory a turn reads, as the gateway wires it: markdown memory over a memory database
    that embeds with the bound embedding model."""
    from personalclaw.context import ContextBuilder
    from personalclaw.context_engine import set_engine
    from personalclaw.memory import MemoryStore
    from personalclaw.skills import SkillsLoader
    from personalclaw.vector_memory import VectorMemoryStore

    markdown = MemoryStore(workspace=tmp_path / "workspace")
    markdown.init()
    store = VectorMemoryStore(db_path=tmp_path / "memory.db", confidence_threshold=0.0)
    store.init()
    markdown.vector_store = store
    builder = ContextBuilder(
        memory=markdown,
        skills=SkillsLoader(skills_path=tmp_path / "skills", install_builtins=False),
    )
    builder.get_memory_for = staticmethod(  # type: ignore[method-assign]
        lambda cwd=None, memory_store=None: markdown
    )
    set_engine(None)
    yield builder, store
    store.close()


def _assemble(builder, key: str, mode: str):
    """One turn's context, assembled by the real engine for a chat in ``mode``, with active
    recall and the push reflex on: the work a turn does before its model is asked."""
    from personalclaw import context_engine

    with patch.object(context_engine, "_push_settings", lambda: (True, 0.0)):
        with memory_writes.derived_from(key, memory_mode=mode):
            return context_engine.assemble_context(
                builder,
                _MESSAGE,
                is_new_session=True,
                session_key=key,
                cwd=None,
                blocks_reads=mode == "temporary",
                blocks_writes=mode != "persistent",
            )


def _accessed(store) -> int:
    return store.db.execute(
        "SELECT COUNT(*) FROM episodic_memories WHERE last_accessed_at IS NOT NULL"
    ).fetchone()[0]


# ── memory reads: the embedding model ────────────────────────────────────────────────────────


def test_an_incognito_turn_sends_nothing_to_the_embedding_model(memory, cloud_embeddings):
    """🔴 Red before the fix: recall embedded the message on a worker thread that did not know
    which chat it worked for, so the cloud embedding model received it."""
    builder, store = memory
    store.write_episodic(_REMEMBERED, tags=["swim"])  # the owner's own record, outside any chat
    cloud_embeddings.sent.clear()

    assembled = _assemble(builder, INCOGNITO, "incognito")

    assert cloud_embeddings.sent == [], "an Incognito chat's words were sent to the embedding model"
    assert _REMEMBERED in assembled.message, "memory is still read for context"
    assert _accessed(store) == 0, "reading an Incognito chat's memory left a mark on it"


def test_a_normal_turn_still_searches_memory_with_its_embedding_model(memory, cloud_embeddings):
    builder, store = memory
    store.write_episodic(_REMEMBERED, tags=["swim"])
    cloud_embeddings.sent.clear()

    assembled = _assemble(builder, NORMAL, "persistent")

    assert _MESSAGE in cloud_embeddings.sent, "a normal chat's recall no longer embeds its message"
    assert _REMEMBERED in assembled.message


# ── tools that need a model ──────────────────────────────────────────────────────────────────


class _Answering:
    """A model the resolution seam builds for an entry: it answers an extraction, and records
    which entry was asked."""

    supports_tools = False

    def __init__(self, entry: str, asked: list[str]) -> None:
        self.entry = entry
        self.asked = asked

    async def start(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None

    async def complete(self, messages: list[dict], **_kw: Any):
        self.asked.append(self.entry)
        yield LLMEvent(kind=EVENT_TEXT_CHUNK, text='{"price": "12 EUR"}')
        yield LLMEvent(kind=EVENT_COMPLETE, input_tokens=40, output_tokens=6)

    async def stream(self, message: str):
        async for event in self.complete([{"role": "user", "content": message}]):
            yield event


def _capability(type_: str, **declared: Any) -> ProviderCapability:
    return ProviderCapability(
        type=type_,
        capabilities=frozenset({Capability.CHAT, Capability.VISION}),
        supports_streaming=True,
        supports_tools=False,
        supports_embeddings=False,
        supports_vision=True,
        max_context_tokens=32_768,
        **declared,
    )


@pytest.fixture
def models(monkeypatch, bindings) -> list[str]:
    """The model entries Settings → Providers holds: the chat's own model on this machine, and a
    cloud one bound for Reasoning and Orchestration. Returns the entries asked, in order."""
    from personalclaw.guardrails.breaker import reset_breakers

    asked: list[str] = []
    registry = ProviderRegistry()

    def _factory(*, entry: ProviderEntry, session_key: str | None = None, **_kw: Any):
        return _Answering(entry.name, asked)

    registry.register_type(_capability("offline-weights", in_process=True), _factory)
    registry.register_type(_capability("cloud-api"), _factory)
    registry.register_entry(ProviderEntry(name=HERE, type="offline-weights", model="tiny"))
    registry.register_entry(ProviderEntry(name=RELAY, type="cloud-api", model="swift"))
    monkeypatch.setattr("personalclaw.llm.registry.get_default_registry", lambda: registry)
    reset_breakers()
    yield asked
    reset_breakers()


class _WebTools(ToolProvider):
    """The structured-extraction tool, as the web tools app wires it: the page goes through
    core's guarded fetch, and its fields come from a model."""

    def __init__(self) -> None:
        self.results: list[ToolResult] = []

    @property
    def name(self) -> str:
        return "web-tools"

    @property
    def display_name(self) -> str:
        return "Web tools"

    async def list_tools(self) -> list[ToolDefinition]:
        return [
            ToolDefinition(
                name="web_extract",
                description="Extract structured fields from a web page.",
                parameters={"type": "object"},
                requires_approval=False,
            )
        ]

    async def invoke(self, tool_name: str, arguments: dict[str, Any]) -> ToolResult:
        from personalclaw.web.fetch import web_extract

        outcome = await web_extract(arguments["url"], arguments["instructions"])
        result = ToolResult(
            success=outcome.ok,
            output=json.dumps(outcome.data) if outcome.ok else outcome.error,
        )
        self.results.append(result)
        return result


class _ChatModel:
    """The chat's own model as its turn calls it: it asks for the page's price, then answers."""

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
                title="web_extract",
                tool_input=json.dumps(
                    {"url": "https://shop.example.com/kettle", "instructions": "the price"}
                ),
            )
            yield AgentEvent(kind=EVENT_COMPLETE)
            return
        yield AgentEvent(kind=EVENT_TEXT_CHUNK, text="The kettle costs 12 EUR.")
        yield AgentEvent(kind=EVENT_COMPLETE)

    async def cancel(self, *, wait_ack_timeout: float = 0.0) -> str:
        return "acked"


async def _page(url: str, **_kw: Any):
    from personalclaw.web.fetch import FetchOutcome

    return FetchOutcome(ok=True, url=url, title="Kettle", content="The kettle costs 12 EUR.")


async def _turn_state(tmp_path: Path, runtime):
    """A dashboard whose chat sessions run on ``runtime``, with the real turn engine."""
    from personalclaw.context import ContextBuilder
    from personalclaw.dashboard.state import DashboardState
    from personalclaw.history import ConversationLog
    from personalclaw.memory import MemoryStore
    from personalclaw.skills import SkillsLoader

    await runtime.start()
    runtime.set_approval_policy("auto")
    sessions = MagicMock(count=0)
    sessions._sessions = {}
    sessions.get_pid = MagicMock(return_value=None)
    sessions.get_channel_link = MagicMock(return_value=(None, None))
    sessions.get_or_create = AsyncMock(return_value=(runtime, True, False))
    sessions.record_failure = AsyncMock()
    log = ConversationLog(base_dir=tmp_path / "sessions")
    state = DashboardState(sessions=sessions, start_time=0.0, conversation_log=log)
    state.context_builder = ContextBuilder(
        memory=MemoryStore(workspace=tmp_path / "ws"),
        skills=SkillsLoader(skills_path=tmp_path / "skills", install_builtins=False),
        conversation_log=log,
    )
    state._hook_store = None
    state.broadcast_ws = MagicMock()
    state.push_sessions_update = MagicMock()
    return state


async def _send(state, name: str, mode: str, text: str):
    from personalclaw.dashboard.chat_runner import run_chat

    session = state.get_or_create_session(name, memory_mode=mode)
    session.append("user", text, "msg msg-u")
    with patch("personalclaw.dashboard.chat_runner.sel", MagicMock()):
        await run_chat(state, session, text)
    return session


def _chat_runtime(tools: list[ToolProvider]):
    from personalclaw.agents.native.runtime import NativeAgentRuntime
    from personalclaw.agents.provider import AgentRuntimeDefinition

    model = _ChatModel()
    runtime = NativeAgentRuntime(
        definition=AgentRuntimeDefinition(name="PersonalClaw", provider="native", model="tiny"),
        model_provider=model,
        tool_providers=tools,
    )
    return runtime, model


@pytest.mark.asyncio
async def test_a_tool_that_needs_a_model_answers_on_the_incognito_chats_own(
    tmp_path, models, monkeypatch
):
    """🔴 Red before the fix: the extraction ran on the Reasoning model, a cloud model, while the
    chat ran on a model on this machine."""
    monkeypatch.setattr("personalclaw.web.fetch.web_fetch", _page)
    tools = _WebTools()
    runtime, chat_model = _chat_runtime([tools])
    state = await _turn_state(tmp_path, runtime)

    await _send(state, "chat-7-1700000000", "incognito", "What does the kettle cost?")

    assert chat_model.calls == 2, "the turn asked for the tool and then answered"
    assert models == [HERE], "the tool's model call left the chat's own model"
    assert tools.results and tools.results[0].success, tools.results
    assert json.loads(tools.results[0].output) == {"price": "12 EUR"}


@pytest.mark.asyncio
async def test_a_normal_chats_tool_still_runs_on_its_bound_model(tmp_path, models, monkeypatch):
    monkeypatch.setattr("personalclaw.web.fetch.web_fetch", _page)
    tools = _WebTools()
    runtime, _chat = _chat_runtime([tools])
    state = await _turn_state(tmp_path, runtime)

    await _send(state, "chat-8-1700000100", "persistent", "What does the kettle cost?")

    assert models == [RELAY], "a normal chat's tool no longer runs on the bound Reasoning model"


def test_a_one_shot_call_inside_an_incognito_chats_work_says_where_it_ran(models):
    """The call is made on the chat's own model in the Reasoning model's place, and a call log
    that captures it says so, in the words every other substitution uses."""
    from personalclaw.guardrails.calls import capture_model_calls
    from personalclaw.llm_helpers import one_shot_completion

    async def call() -> str:
        with memory_writes.derived_from(INCOGNITO, memory_mode="incognito"):
            memory_writes.answered_by(HERE_REF)
            return await one_shot_completion("Extract the price.", use_case="reasoning")

    with capture_model_calls() as log:
        assert asyncio.run(call()) == '{"price": "12 EUR"}'
    assert models == [HERE]
    assert [(c.provider, c.model, c.substitution) for c in log.calls] == [
        (
            HERE,
            "tiny",
            f"ran on {HERE_REF} instead of {RELAY_REF}: this chat is Incognito, so nothing from "
            "it is sent to any model but the one it runs on",
        )
    ]


class _Images:
    """An image model somewhere else: it keeps every prompt it is sent."""

    name = "cloud-images"

    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def generate(self, prompt: str, **_kw: Any):
        from personalclaw.image_gen.provider import ImageGenError

        self.prompts.append(prompt)
        raise ImageGenError("the test stops here")


def test_a_tool_whose_kind_of_model_the_chat_has_not_says_it_cannot_run(monkeypatch):
    """🔴 Red before the fix: the image tool sent the prompt to the bound image model."""
    from personalclaw import mcp_artifacts

    images = _Images()
    monkeypatch.setattr("personalclaw.image_gen.registry.active_image_gen", lambda: (images, "i1"))

    def _audit(*_a: Any, **_kw: Any) -> None:
        return None

    with memory_writes.derived_from(INCOGNITO, memory_mode="incognito"):
        refused = mcp_artifacts._image_generate(object(), {"prompt": "a kettle"}, INCOGNITO, _audit)
    assert images.prompts == [], "an Incognito chat's prompt was sent to the image model"
    assert "Incognito" in refused and "cloud-images:i1" in refused, refused

    with memory_writes.derived_from(NORMAL, memory_mode="persistent"):
        mcp_artifacts._image_generate(object(), {"prompt": "a kettle"}, NORMAL, _audit)
    assert images.prompts == ["a kettle"], "a normal chat's image tool no longer reaches its model"


def test_a_model_on_another_axis_is_refused_in_an_incognito_chats_work(models):
    """A subagent's model, a loop's, a knowledge node's: every model built for anything but the
    chat's own turn passes the guard, and the guard refuses one that is not the chat's own."""
    from personalclaw.providers.provider_bridge import resolve_metered_model

    with memory_writes.derived_from(INCOGNITO, memory_mode="incognito"):
        with pytest.raises(Exception) as refused:
            resolve_metered_model("orchestration")
    assert type(refused.value).__name__ == "OtherModelRefused", repr(refused.value)
    assert str(refused.value) == (
        "This chat is Incognito, so nothing from it is sent to any model but the one it runs on: "
        f"{RELAY_REF} was not asked."
    )
    assert resolve_metered_model("orchestration") is not None, "ordinary work is untouched"
    assert models == [], "building a model asks it nothing"


def test_the_chats_own_model_serves_any_axis_its_work_asks_for(models):
    from personalclaw.providers.provider_bridge import resolve_metered_model

    with memory_writes.derived_from(INCOGNITO, memory_mode="incognito"):
        memory_writes.answered_by(HERE_REF)
        assert resolve_metered_model("orchestration", model_override=HERE_REF) is not None
        with pytest.raises(memory_writes.OtherModelRefused):
            resolve_metered_model("orchestration", model_override=RELAY_REF)


def test_an_image_reader_is_refused_in_an_incognito_chats_work(models, bindings):
    """The chat's work asking the image model to read an image (a browsing step's screenshot, a
    page's picture) is a model beside the chat's own: in an Incognito chat it is not handed over."""
    from personalclaw.providers.image_input import resolve_image_reader

    bindings["image_modality"] = [RELAY_REF]
    with memory_writes.derived_from(INCOGNITO, memory_mode="incognito"):
        with pytest.raises(Exception) as refused:
            asyncio.run(resolve_image_reader(metered=False))
    assert type(refused.value).__name__ == "OtherModelRefused", repr(refused.value)
    assert asyncio.run(resolve_image_reader(metered=False)) is not None


# ── what the person gives the chat ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_screen_the_person_shares_is_described_by_the_model_set_up_for_images(
    models, bindings
):
    """What the person hands an Incognito chat in a form its model cannot read is read by the
    model they set up for it, as in any chat; the chat's notice says so, and the allowance ends
    with the reading."""
    from personalclaw.dashboard.chat_runner import _describe_screen_frame
    from personalclaw.usage_ledger import Attribution

    bindings["image_modality"] = [RELAY_REF]
    with memory_writes.derived_from(INCOGNITO, memory_mode="incognito"):
        memory_writes.answered_by(HERE_REF)
        said = await _describe_screen_frame(
            "data:image/png;base64,AAAA", usage=Attribution(source="chat", session_key=INCOGNITO)
        )
        after = memory_writes.model_may_read(RELAY_REF)
    assert models == [RELAY], "the screen was not read by the model set up for images"
    assert said == '{"price": "12 EUR"}'
    assert after is False, "the chat's own work may still hand nothing to that model"


@pytest.mark.asyncio
async def test_a_file_the_person_attaches_is_read_by_the_models_set_up_for_it(
    monkeypatch, tmp_path
):
    """An attachment read for its text (a scan, a recording) is read the same way, from the turn
    that asks for it, and the reading still writes nothing for the chat."""
    from personalclaw.dashboard.attachment_extract import AttachmentExtractor
    from personalclaw.knowledge.extract import Extracted

    seen: list[tuple[bool, bool]] = []

    async def _extract(
        path: str, mime: str | None, *, name: str = "", surface: str = ""
    ) -> Extracted:
        seen.append((memory_writes.model_may_read(RELAY_REF), memory_writes.writes_refused()))
        return Extracted("A receipt: 12 EUR.", True)

    monkeypatch.setattr("personalclaw.knowledge.extract.extract_file", _extract)
    scan = tmp_path / "receipt.png"
    scan.write_bytes(b"\x89PNG\r\n")
    with memory_writes.derived_from(INCOGNITO, memory_mode="incognito"):
        memory_writes.answered_by(HERE_REF)
        got = await AttachmentExtractor().get(str(scan), "image/png")
    assert got.text == "A receipt: 12 EUR."
    assert seen == [(True, True)], "read by the model set up for it, and writing nothing"


# ── an agent CLI's tools, in their own process ───────────────────────────────────────────────


def _tool_process(monkeypatch, key: str, answers: list[dict]) -> list[str]:
    """The ``mcp-core`` process an agent CLI runs its PersonalClaw tools in, serving the chat
    ``key``, and what the gateway answers each time it is asked about that chat. Returns the
    paths it asked."""
    from personalclaw import mcp_core

    asked: list[str] = []

    def _get(path: str) -> dict:
        asked.append(path)
        return answers.pop(0)

    monkeypatch.setattr(mcp_core, "_SESSION_MODES", {})
    monkeypatch.setattr(mcp_core, "_resolve_session_key", lambda: key)
    monkeypatch.setattr(mcp_core, "_get", _get)
    return asked


def test_an_agent_clis_image_tool_says_it_cannot_run_in_an_incognito_chat(monkeypatch):
    """🔴 Red before the fix: an agent CLI runs PersonalClaw's tools in a process of their own,
    which held no session scope, so an Incognito chat's image request reached the image model."""
    from personalclaw import mcp_artifacts, mcp_core

    images = _Images()
    monkeypatch.setattr("personalclaw.image_gen.registry.active_image_gen", lambda: (images, "i1"))
    asked = _tool_process(monkeypatch, INCOGNITO, [{"memory_mode": "incognito"}])

    def _dispatch(name: str, args: dict[str, Any]) -> str:
        # What the server's dispatch reaches for this tool, with the tool's own audit.
        return mcp_artifacts._image_generate(object(), args, INCOGNITO, lambda *a, **kw: None)

    monkeypatch.setattr(mcp_core, "_aggregated_call_tool", _dispatch)
    first = str(mcp_core._call_as_its_session("image_generate", {"prompt": "a kettle"}))
    second = str(mcp_core._call_as_its_session("image_generate", {"prompt": "a mug"}))

    assert images.prompts == [], "an Incognito chat's prompt reached the image model"
    assert first == second
    assert (
        "This chat is Incognito, so nothing from it is sent to any model but the one it runs on: "
        "cloud-images:i1 was not asked." in first
    ), first
    assert asked == ["/api/chat/sessions/model-reach"], "the chat's mode is asked once"


def test_an_agent_clis_tools_for_a_normal_chat_run_as_before(monkeypatch):
    from personalclaw import mcp_artifacts, mcp_core

    images = _Images()
    monkeypatch.setattr("personalclaw.image_gen.registry.active_image_gen", lambda: (images, "i1"))
    _tool_process(monkeypatch, NORMAL, [{"memory_mode": "persistent"}])
    monkeypatch.setattr(
        mcp_core,
        "_aggregated_call_tool",
        lambda name, args: mcp_artifacts._image_generate(
            object(), args, NORMAL, lambda *a, **kw: None
        ),
    )

    mcp_core._call_as_its_session("image_generate", {"prompt": "a kettle"})

    assert images.prompts == ["a kettle"]


def test_a_tool_process_that_cannot_learn_its_chats_mode_hands_nothing_on(monkeypatch):
    """No answer is not "persistent": the call runs as for a chat that keeps nothing, and the next
    call asks again. A call made for no session is not asked about at all: it is not made, since
    it would run as no one's work."""
    from personalclaw import mcp_core

    asked = _tool_process(
        monkeypatch,
        INCOGNITO,
        [{"error": "HTTP 502: Bad Gateway"}, {"memory_mode": "persistent"}],
    )
    seen: list[tuple[bool, bool]] = []

    def _tool(name: str, args: dict[str, Any]) -> str:
        seen.append((memory_writes.model_may_read(RELAY_REF), memory_writes.writes_refused()))
        return "done"

    monkeypatch.setattr(mcp_core, "_aggregated_call_tool", _tool)
    mcp_core._call_as_its_session("visualize", {})
    mcp_core._call_as_its_session("visualize", {})
    monkeypatch.setattr(mcp_core, "_resolve_session_key", lambda: "")
    unnamed = mcp_core._call_as_its_session("visualize", {})

    assert seen == [(False, True), (True, False)]
    assert mcp_core.UNNAMED_CALL in str(unnamed)
    assert len(asked) == 2


def test_the_gateway_tells_a_tool_process_what_its_chat_is():
    """The answer the tool process runs under is the gateway's own for that session: the live
    chat's mode, a subagent its Incognito parent started, and nothing for no session."""
    from types import SimpleNamespace

    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer
    from chat_test_helpers import signed_in

    from personalclaw import session_restrictions
    from personalclaw.dashboard.chat_handlers import api_chat_session_model_reach
    from personalclaw.dashboard.memory_write_gate import memory_write_middleware

    sessions = {
        "chat-7-1700000000": SimpleNamespace(memory_mode="incognito"),
        "chat-8-1700000100": SimpleNamespace(memory_mode="persistent"),
        "chat-9-1700000200": SimpleNamespace(memory_mode="temporary"),
    }
    with memory_writes.derived_from(INCOGNITO, memory_mode="incognito"):
        # The Incognito chat's turn starts a subagent, which keeps nothing either.
        memory_writes.hand_on("subagent:a1b2c3", INCOGNITO)

    async def run() -> list[tuple[str, object]]:
        app = web.Application(middlewares=[signed_in, memory_write_middleware()])
        app["state"] = SimpleNamespace(_sessions=sessions)
        app.router.add_get("/api/chat/sessions/model-reach", api_chat_session_model_reach)
        out: list[tuple[str, object]] = []
        async with TestClient(TestServer(app)) as client:
            for key in (
                INCOGNITO,
                NORMAL,
                "dashboard:chat-9-1700000200",
                "subagent:a1b2c3",
                "dashboard:ui",
                "",
            ):
                headers = {"X-Session-Key": key} if key else {}
                resp = await client.get("/api/chat/sessions/model-reach", headers=headers)
                out.append((key, (await resp.json())["memory_mode"]))
        return out

    try:
        answers = asyncio.run(run())
    finally:
        session_restrictions.clear("subagent:a1b2c3")
    assert answers == [
        (INCOGNITO, "incognito"),
        (NORMAL, "persistent"),
        ("dashboard:chat-9-1700000200", "temporary"),
        ("subagent:a1b2c3", "incognito"),
        ("dashboard:ui", "persistent"),
        ("", "persistent"),
    ]


# ── the turn's own model ─────────────────────────────────────────────────────────────────────

DOWN, DOWN_REF = "down-oai", "down-oai:down-1"
UP, UP_REF = "up-oai", "up-oai:up-1"
OVERLOADED = "Error code: 529 - {'type': 'error', 'error': {'type': 'overloaded_error'}}"


class _Scripted:
    """A provider for one entry: answers with who it is, or fails as told to."""

    supports_tools = False

    def __init__(self, entry: str, model: str, world: "_World") -> None:
        self.entry = entry
        self.model = model
        self.world = world
        self.served_ref = f"{entry}:{model}"

    async def start(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None

    async def complete(self, messages: list[dict], **kw: Any):
        self.world.calls.append(self.entry)
        failure = self.world.failures.get(self.entry)
        if failure is not None:
            raise failure
        yield LLMEvent(kind=EVENT_TEXT_CHUNK, text=f"answered by {self.entry}")
        yield LLMEvent(kind=EVENT_COMPLETE, input_tokens=3, output_tokens=2)


class _World:
    def __init__(self) -> None:
        self.failures: dict[str, Exception] = {}
        self.calls: list[str] = []
        self.active: dict[str, list[str]] = {}


@pytest.fixture
def world(monkeypatch) -> _World:
    import personalclaw.agents.native.runtime as runtime_mod

    w = _World()
    registry = ProviderRegistry()

    def _factory(*, entry: ProviderEntry, session_key: str | None = None, **kwargs: Any):
        return _Scripted(entry.name, str(kwargs.get("model") or entry.model), w)

    registry.register_type(_capability("scripted"), _factory)
    registry.register_entry(ProviderEntry(name=DOWN, type="scripted", model="down-1"))
    registry.register_entry(ProviderEntry(name=UP, type="scripted", model="up-1"))
    monkeypatch.setattr("personalclaw.llm.registry.get_default_registry", lambda: registry)
    monkeypatch.setattr("personalclaw.providers.use_cases.load_active_models", lambda: w.active)
    monkeypatch.setattr(runtime_mod, "_INFERENCE_RETRY_BACKOFF_SECS", 0.0)
    return w


async def _failing_over_runtime():
    from personalclaw.providers import provider_bridge

    rt = provider_bridge._build_native_runtime(
        use_case="chat",
        session_key="dashboard:fails-over",
        agent=None,
        model_override=None,
        cwd=None,
    )
    await rt.start()
    rt.set_approval_policy("auto")
    rt.announce_failover()
    return rt


@pytest.mark.asyncio
async def test_an_incognito_turn_whose_model_fails_is_not_moved_to_another(world):
    """🔴 Red before the fix: the turn fell back down the chat's chain to the next model."""
    world.active["chat"] = [DOWN_REF, UP_REF]
    world.failures[DOWN] = RuntimeError(OVERLOADED)
    rt = await _failing_over_runtime()

    with memory_writes.derived_from(INCOGNITO, memory_mode="incognito"):
        with pytest.raises(Exception):
            [ev async for ev in rt.stream("Adjust this week.")]

    assert UP not in world.calls, "an Incognito chat's turn was moved to another model"


@pytest.mark.asyncio
async def test_a_normal_turn_whose_model_fails_still_falls_back(world):
    world.active["chat"] = [DOWN_REF, UP_REF]
    world.failures[DOWN] = RuntimeError(OVERLOADED)
    rt = await _failing_over_runtime()

    with memory_writes.derived_from(NORMAL, memory_mode="persistent"):
        events = [ev async for ev in rt.stream("Adjust this week.")]

    assert f"answered by {UP}" in [ev.text for ev in events]


@pytest.mark.asyncio
async def test_an_incognito_chat_whose_model_cannot_run_is_not_sent_to_another(tmp_path):
    """🔴 Red before the fix: the runtime was built on the chain's next model, because the chat's
    own could not run, and the turn was sent there."""
    runtime, chat_model = _chat_runtime([])
    runtime.model_substitution = ModelSubstitution(
        requested=HERE_REF,
        served=RELAY_REF,
        why="its calls are paused after repeated failures",
        who="this chat's model",
    )
    state = await _turn_state(tmp_path, runtime)

    session = await _send(state, "chat-7-1700000000", "incognito", "Adjust this week.")

    assert chat_model.calls == 0, "an Incognito chat's message was sent to another model"
    said = [m.get("content") for m in session.messages if m.get("role") == "error"]
    assert said == [
        "This chat's model here:tiny can't answer right now: its calls are paused after repeated "
        "failures. This chat is Incognito, so nothing from it is sent to any model but the one it "
        f"runs on: {RELAY_REF} was not asked."
    ], said


# ── what is recorded ─────────────────────────────────────────────────────────────────────────


def test_each_embedding_call_is_recorded_in_usage_and_the_model_call_log_never_its_text(
    cloud_embeddings,
):
    """🔴 Red before the fix: no surface recorded an embedding call at all."""
    from personalclaw.config.loader import config_dir
    from personalclaw.guardrails.audit import read_recent
    from personalclaw.routing import rates as rates_mod
    from personalclaw.routing.usage import audit_census, ledgered_audit_ids

    # The owner's price for the model (high, so a few tokens cost more than a rounding): the one
    # pricing function prices the call by it.
    rates_mod._overlay_cache = None
    rates_mod.save_overlay({EMBED_REF: {"in_per_mtok": 1000.0, "out_per_mtok": 0.0}})
    one = embedding_registry.embed_fn_for("cloud-embed", "embed-v1")
    many = embedding_registry.embed_many_fn_for("cloud-embed", "embed-v1")
    assert one is not None and many is not None
    try:
        with memory_writes.derived_from(NORMAL, memory_mode="persistent"):
            one("a note about the kettle")
            many(["the first passage", "the second passage"])
        with memory_writes.derived_from(INCOGNITO, memory_mode="incognito"):
            assert one("the training log") is None
            assert many(["the training log"]) == [None]
    finally:
        rates_mod._overlay_cache = None

    calls = [r for r in read_recent() if r.get("use_case") == "embedding"]
    assert [(r["provider"], r["model"], r["texts"], r["passed"], r["session"]) for r in calls] == [
        ("cloud-embed", "embed-v1", 1, True, NORMAL),
        ("cloud-embed", "embed-v1", 2, True, NORMAL),
    ], "each call that reached the model is one row; a refused one sent nothing and is none"
    assert all(r["tokens_in"] > 0 and r["estimated"] and r["priced"] for r in calls)
    usage_file = config_dir() / "usage" / "turns.jsonl"
    usage = [json.loads(line) for line in usage_file.read_text(encoding="utf-8").splitlines()]
    assert [(u["source"], u["session_key"], u["provider"], u["model"]) for u in usage] == [
        ("chat", NORMAL, "cloud-embed", "embed-v1"),
        ("chat", NORMAL, "cloud-embed", "embed-v1"),
    ]
    assert [u["audit_ids"] for u in usage] == [[r["audit_id"]] for r in calls]
    assert [u["cost_usd"] for u in usage] == [r["dollars_est"] for r in calls]
    assert all(u["priced"] and u["cost_usd"] > 0 and u["input_tokens"] > 0 for u in usage)
    census = audit_census(calls, ledgered=ledgered_audit_ids(usage))
    assert census["calls"] == 0, "a call Usage counts is not also counted as left out"
    for path in (config_dir() / "model_calls.jsonl", usage_file):
        written = path.read_text(encoding="utf-8")
        for text in ("kettle", "passage", "training log"):
            assert text not in written, f"{path.name} holds the text {text!r}"


def test_an_embedding_call_that_fails_is_in_the_model_call_log_only(cloud_embeddings):
    """A failed call is charged nothing and writes no Usage row, as no model call that fails does;
    its model-call row still says the model was asked."""
    from personalclaw.config.loader import config_dir
    from personalclaw.guardrails.audit import read_recent

    async def _down(text: str, model: str = "") -> list[float] | None:
        cloud_embeddings.sent.append(text)
        raise ConnectionError("the embedding model is down")

    cloud_embeddings.embed = _down  # type: ignore[method-assign]
    one = embedding_registry.embed_fn_for("cloud-embed", "embed-v1")
    assert one is not None
    with memory_writes.derived_from(NORMAL, memory_mode="persistent"):
        try:
            one("a note about the kettle")
        except ConnectionError:
            pass

    calls = [r for r in read_recent() if r.get("use_case") == "embedding"]
    assert [(r["passed"], r["dollars_est"], r["priced"]) for r in calls] == [(False, 0.0, True)]
    assert not (config_dir() / "usage" / "turns.jsonl").exists()

"""Work a chat starts takes the chat's memory mode from the live chat, from its first turn on.

A Temporary chat's work reads none of your memory and writes none, and an Incognito chat's writes
none. The work such a chat starts keeps that mode: a workflow run or a batch it starts, each step of
the run, and each subagent. A chat's mode is recorded on the live chat the gateway holds, in the
in-process registry a channel, a run or a subagent's start marks, and in the chat's transcript,
which is written when its first turn ends. A run read only the registry and the transcript, and the
dashboard marks no registry: a workflow a Temporary or Incognito chat started on its first turn
started as normal work, free to read and write memory and to reach any model.

The behaviour now, driven through the real turn engine, the in-process workflow tools, the workflow
service and supervisor, the subagent hand-off, the gateway's route guard and the chat search index:

* a workflow a Temporary chat starts on its first turn, before its transcript exists, runs as
  Temporary on the chat's own model, and one an Incognito chat starts runs as Incognito; so does a
  run started for the chat outside its turn, where only the gateway's live chats say what it is;
* work for a chat whose mode nothing can say (its transcript's record of it cannot be read, its
  value is one this build does not know, or nothing records it, as for a Temporary chat that has
  ended) runs by a Temporary chat's rules, and each refusal it meets says why;
* a normal chat's first-turn workflow still runs as normal work;
* every reader asks the one reader of a session's mode, the live chat first.
"""

from __future__ import annotations

import functools
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from personalclaw import memory_reads, memory_writes, session_restrictions
from personalclaw.llm.base import EVENT_COMPLETE, EVENT_TEXT_CHUNK, LLMEvent
from personalclaw.llm.capabilities import Capability, ProviderCapability
from personalclaw.llm.events import EVENT_TOOL_CALL, AgentEvent
from personalclaw.llm.registry import ProviderEntry, ProviderRegistry
from personalclaw.workflows import ownership
from personalclaw.workflows.ownership import MemoryMode

#: The chat's own model, on this machine, and the model bound for everything else. Invented names.
HERE, HERE_REF = "here", "here:tiny"
FAR, FAR_REF = "far", "far:wide"

TEMPORARY_CHAT = "chat-31-1791000101"
INCOGNITO_CHAT = "chat-32-1791000102"
NORMAL_CHAT = "chat-33-1791000103"
#: A chat the gateway no longer holds and nothing records: a Temporary chat that has ended.
ENDED_CHAT = "chat-34-1791000104"
#: A chat whose transcript's first line cannot be read.
GARBLED_CHAT = "chat-35-1791000105"
#: A chat whose mode is one this build does not know.
UNKNOWN_MODE_CHAT = "chat-36-1791000106"

CHATS = (TEMPORARY_CHAT, INCOGNITO_CHAT, NORMAL_CHAT, ENDED_CHAT, GARBLED_CHAT, UNKNOWN_MODE_CHAT)
RESTRICTED = [(TEMPORARY_CHAT, "temporary"), (INCOGNITO_CHAT, "incognito")]

WORKFLOW = "pantry-check"
ROOT = {
    "kind": "sequence",
    "id": "root",
    "children": [{"kind": "transform", "id": "note", "config": {"expr": "the pantry list"}}],
}


# ── the models Settings holds ────────────────────────────────────────────────────────────────


class _Model:
    """A model the resolution seam builds for an entry: it answers, and records that it was
    asked."""

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
        yield LLMEvent(kind=EVENT_TEXT_CHUNK, text="Noted.")
        yield LLMEvent(kind=EVENT_COMPLETE, input_tokens=10, output_tokens=2)


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
    """Settings → Providers and Settings → Models: the chat's model on this machine, and another
    bound for Orchestration, Reasoning and Background. ``asked`` is every entry asked, in order."""
    from personalclaw.guardrails.breaker import reset_breakers

    w = SimpleNamespace(asked=[])
    registry = ProviderRegistry()

    def _factory(*, entry: ProviderEntry, session_key: str | None = None, **_kw: Any):
        return _Model(entry.name, w)

    registry.register_type(_capability("offline-weights"), _factory)
    registry.register_type(_capability("cloud-api"), _factory)
    registry.register_entry(ProviderEntry(name=HERE, type="offline-weights", model="tiny"))
    registry.register_entry(ProviderEntry(name=FAR, type="cloud-api", model="wide"))
    active = {
        "chat": [HERE_REF],
        "orchestration": [FAR_REF],
        "reasoning": [FAR_REF],
        "background": [FAR_REF],
    }
    monkeypatch.setattr("personalclaw.llm.registry.get_default_registry", lambda: registry)
    monkeypatch.setattr("personalclaw.providers.use_cases.load_active_models", lambda: active)
    reset_breakers()
    yield w
    reset_breakers()


@pytest.fixture
def home(tmp_path, monkeypatch) -> SimpleNamespace:
    """A home of the test's own, where the chats keep their transcripts and the runs their
    records, with the workflow saved. ``marked`` is every key whose marks in the process-wide
    registry are cleared after the test; the chats' own are among them."""
    from personalclaw.workflows import defs, native_defs

    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    monkeypatch.setattr("personalclaw.workflows.leases.config_dir", lambda: tmp_path)
    monkeypatch.setattr(defs, "_providers", {})
    defs.register_provider(native_defs.NativeWorkflowDefProvider())
    marked = [k for chat in CHATS for k in (chat, f"dashboard:{chat}")]
    for key in marked:
        session_restrictions.clear(key)
    yield SimpleNamespace(path=tmp_path, marked=marked)
    for key in marked:
        session_restrictions.clear(key)


async def _save_the_workflow() -> None:
    from personalclaw.workflows import service

    saved = await service.author_def(name=WORKFLOW, root=ROOT, strict=False)
    assert saved.get("ok"), saved


def _state(*chats: tuple[str, str]):
    """The dashboard state the gateway holds, with these chats live in it."""
    from personalclaw.dashboard.state import DashboardState

    state = DashboardState(sessions=MagicMock(count=0), start_time=0.0)
    state.push_sessions_update = MagicMock()
    for name, mode in chats:
        state.get_or_create_session(name, memory_mode=mode)
    return state


def _supervisor(state):
    """The gateway's workflow supervisor, beside its dashboard state."""
    from personalclaw.workflows.controller import EngineServices
    from personalclaw.workflows.watchdog import WorkflowWatchdog

    return WorkflowWatchdog(state, EngineServices())


def _transcript(name: str) -> Path:
    from personalclaw.history import session_path

    return session_path(f"dashboard:{name}")


def _garble(name: str, text: str = "Plan the lantern festival stall.") -> None:
    """The chat's transcript, its metadata line cut short so it cannot be read."""
    path = _transcript(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    message = json.dumps({"role": "user", "content": text})
    path.write_text('{"_type": "metadata", "memory_mode": "incognito"\n' + message + "\n")


async def _start_for(supervisor, name: str) -> Any:
    """A run started for the chat ``name`` outside its turn, from no work of the chat's: the
    subagent tool's origin and the chat's key, as ``batch_start`` starts its runs."""
    from personalclaw.workflows import service, store
    from personalclaw.workflows.models import OriginKind

    started = await service.start_run(
        name=WORKFLOW,
        supervisor=supervisor,
        origin_kind=OriginKind.SUBAGENT_TOOL,
        session_key=f"dashboard:{name}",
    )
    assert started.get("run_id"), started
    controller = supervisor.controller(started["run_id"])
    if controller is not None:
        await controller.wait_for_terminal(timeout=20.0)
    return store.get(started["run_id"])


# ── a chat's first turn starts a workflow ────────────────────────────────────────────────────


class _ChatModel:
    """The chat's own model as its turn calls it: it starts the workflow, then answers."""

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
                title="workflow_start",
                tool_input=json.dumps({"name": WORKFLOW}),
            )
            yield AgentEvent(kind=EVENT_COMPLETE)
            return
        yield AgentEvent(kind=EVENT_TEXT_CHUNK, text="The pantry check is running.")
        yield AgentEvent(kind=EVENT_COMPLETE)

    async def cancel(self, *, wait_ack_timeout: float = 0.0) -> str:
        return "acked"


async def _first_turn_that_starts_the_workflow(tmp_path: Path, name: str, mode: str, monkeypatch):
    """A new chat ``name`` in ``mode`` whose first turn's agent calls ``workflow_start``: the real
    turn engine, the in-process workflow tools the native runtime serves, and the gateway's
    workflow supervisor beside its dashboard state, running on the turn's loop. Returns the run the
    tool started, and whether the chat's transcript existed when it started."""
    from personalclaw.agents.native.runtime import NativeAgentRuntime
    from personalclaw.agents.native.tools import InProcessMcpToolProvider
    from personalclaw.agents.provider import AgentRuntimeDefinition
    from personalclaw.context import ContextBuilder
    from personalclaw.dashboard.chat_runner import run_chat
    from personalclaw.history import ConversationLog
    from personalclaw.memory import MemoryStore
    from personalclaw.skills import SkillsLoader
    from personalclaw.workflows import service, store

    await _save_the_workflow()
    chat_runtime = NativeAgentRuntime(
        definition=AgentRuntimeDefinition(name="PersonalClaw", provider="native", model="tiny"),
        model_provider=_ChatModel(),
        tool_providers=[
            InProcessMcpToolProvider(
                module="personalclaw.mcp_workflows",
                provider_name="personalclaw-workflows",
                display="Workflows",
            )
        ],
        session_key=f"dashboard:{name}",
    )
    await chat_runtime.start()
    chat_runtime.set_approval_policy("auto")
    state = _state()
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
    supervisor = _supervisor(state)
    supervisor.start()
    monkeypatch.setattr(
        "personalclaw.action_providers.services.get_action_services",
        lambda: SimpleNamespace(workflows=supervisor),
    )
    seen: dict[str, Any] = {}
    start_run = service.start_run

    async def _start_run(**kwargs: Any) -> dict[str, Any]:
        seen["transcript_existed"] = _transcript(name).exists()
        seen["started"] = answer = await start_run(**kwargs)
        return answer

    monkeypatch.setattr(service, "start_run", _start_run)
    try:
        session = state.get_or_create_session(name, memory_mode=mode)
        session.append("user", "Check the pantry against the shopping note.", "msg msg-u")
        with patch("personalclaw.dashboard.chat_runner.sel", MagicMock()):
            await run_chat(state, session, "Check the pantry against the shopping note.")
        started = seen.get("started") or {}
        assert started.get("run_id"), f"the turn started no run: {started}"
        controller = supervisor.controller(started["run_id"])
        if controller is not None:
            await controller.wait_for_terminal(timeout=20.0)
    finally:
        await supervisor.stop()
    return SimpleNamespace(
        run=store.get(started["run_id"]), transcript_existed=seen["transcript_existed"], state=state
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(("name", "mode"), RESTRICTED)
async def test_a_workflow_a_restricted_chat_starts_on_its_first_turn_keeps_its_mode(
    world, home, monkeypatch, name, mode
):
    """The first turn of a new Temporary (or Incognito) chat starts a workflow before the chat's
    transcript exists. The run is the chat's work: its record keeps the chat's mode and the chat's
    own model, and its steps run under both. 🔴 Red before the fix: nothing but the live chat
    knew the chat's mode yet, the run never asked it, and it started as normal work."""
    from personalclaw.workflows.ownership import owned_key

    turn = await _first_turn_that_starts_the_workflow(home.path, name, mode, monkeypatch)
    run = turn.run
    home.marked.extend([owned_key(run.id, "run"), owned_key(run.id, "note")])

    assert turn.transcript_existed is False, "the chat's transcript existed before the run began"
    assert ownership.run_mode(run) is MemoryMode(mode), run.extra
    assert ownership.run_model(run) == HERE_REF, run.extra
    step = owned_key(run.id, "note")
    assert memory_writes.blocks_memory_writes(step), "the run's step may write memory"
    assert memory_reads.reach_of(turn.state, step).temporary is (mode == "temporary")


@pytest.mark.asyncio
async def test_a_normal_chats_first_turn_workflow_still_runs_as_normal_work(
    world, home, monkeypatch
):
    from personalclaw.workflows.ownership import RUN_MODE_KEY, owned_key

    turn = await _first_turn_that_starts_the_workflow(
        home.path, NORMAL_CHAT, "persistent", monkeypatch
    )
    run = turn.run
    home.marked.extend([owned_key(run.id, "run"), owned_key(run.id, "note")])

    assert turn.transcript_existed is False
    assert RUN_MODE_KEY not in run.extra, run.extra
    assert ownership.run_mode(run) is MemoryMode.NORMAL
    step = owned_key(run.id, "note")
    assert not memory_writes.blocks_memory_writes(step)
    assert memory_reads.reach_of(turn.state, step).reads


# ── a run started for the chat outside its turn ──────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(("name", "mode"), RESTRICTED)
async def test_a_run_started_for_a_restricted_chat_outside_its_turn_reads_the_live_chat(
    home, name, mode
):
    """A run started for the chat outside its turn, before the chat has finished a turn, has no
    work of the chat's around it to say what the chat is. The supervisor's gateway holds the live
    chat, and the run reads the chat's mode there. 🔴 Red before the fix: it read the transcript
    and the registry only."""
    await _save_the_workflow()
    supervisor = _supervisor(_state((name, mode)))

    run = await _start_for(supervisor, name)
    home.marked.append(ownership.owned_key(run.id, "run"))

    assert not _transcript(name).exists()
    assert ownership.run_mode(run) is MemoryMode(mode), run.extra


# ── a chat whose mode nothing can say ────────────────────────────────────────────────────────


def _unknown_mode(state) -> None:
    """A live chat whose mode is one this build does not know, as a newer version could write."""
    state.get_or_create_session(UNKNOWN_MODE_CHAT, memory_mode="temporary").memory_mode = "vault"


CANNOT_BE_READ = [
    pytest.param(GARBLED_CHAT, lambda state: _garble(GARBLED_CHAT), id="its-record-is-garbled"),
    pytest.param(UNKNOWN_MODE_CHAT, _unknown_mode, id="its-mode-is-one-this-build-does-not-know"),
    pytest.param(ENDED_CHAT, lambda state: None, id="nothing-records-it-any-more"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("name", "make"), CANNOT_BE_READ)
async def test_work_for_a_chat_whose_mode_cannot_be_read_runs_by_temporary_rules(
    world, home, name, make
):
    """When nothing can say what the chat is (its record cannot be read, names a mode this build
    does not know, or is gone, as a Temporary chat's is once it ends), its run reads none of your
    memory, writes none and reaches no model but its chat's, and each refusal it meets says the
    chat's memory setting cannot be read. 🔴 Red before the fix: each started as normal work."""
    from personalclaw.workflows.run_start import run_context

    await _save_the_workflow()
    state = _state()
    make(state)
    run = await _start_for(_supervisor(state), name)
    home.marked.extend([ownership.owned_key(run.id, "run"), ownership.owned_key(run.id, "note")])
    step = ownership.owned_key(run.id, "note")

    assert ownership.run_mode(run).value == "unreadable", run.extra
    assert memory_writes.blocks_memory_writes(step)
    assert memory_reads.reach_of(state, step).refusal == memory_reads.UNREADABLE
    skipped, why = ownership.skips_node({"provider": "memory-write"}, ownership.run_mode(run))
    assert skipped and "memory setting cannot be read" in why, why

    def _inside_the_run() -> tuple[str | None, str]:
        return memory_writes.restricted_mode(), memory_writes.other_model_refusal(FAR_REF)

    restricted, refusal = run_context(run).run(_inside_the_run)
    assert restricted == memory_writes.UNREADABLE
    assert refusal.startswith("This chat's memory setting cannot be read"), refusal
    assert f"{FAR_REF} was not asked" in refusal, refusal


def test_a_registry_that_cannot_be_read_inherits_a_mode_nothing_can_say(home, monkeypatch):
    """A lookup that fails must not open the gate, nor stop the run from starting. 🔴 Red before
    the fix: it inherited normal."""

    def _cannot_read(_key: str) -> bool:
        raise RuntimeError("the registry cannot be read")

    monkeypatch.setattr("personalclaw.session_restrictions.is_temporary", _cannot_read)
    assert ownership.inherit_mode(f"dashboard:{NORMAL_CHAT}").value == "unreadable"


# ── a subagent the chat starts ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize(("name", "mode"), RESTRICTED)
def test_a_subagent_handed_on_outside_the_chats_turn_takes_the_live_chats_mode(home, name, mode):
    """A subagent is marked with its chat's mode when it is spawned, whichever work spawns it: the
    hand-off reads the parent chat through the gateway's state, the live chat first. 🔴 Red before
    the fix for an Incognito chat: only its Temporary-ness was read from the live chat, so a spawn
    outside its turn, before its first turn ended, started unmarked."""
    state = _state((name, mode))
    child = "subagent:3f00a1"
    home.marked.append(child)

    memory_writes.hand_on(
        child, f"dashboard:{name}", reach=functools.partial(memory_reads.reach_of, state)
    )

    assert session_restrictions.is_restricted(child)
    assert session_restrictions.is_temporary(child) is (mode == "temporary")


def test_a_subagent_of_a_chat_whose_mode_cannot_be_read_is_marked_so_and_says_why(home):
    """Work handed on from a chat whose mode nothing can say is marked as such: it reads nothing
    and writes nothing, and says why, without its chat in sight. 🔴 Red before the fix: it was
    marked Incognito at most, so it read your memory."""
    _garble(GARBLED_CHAT)
    child = "subagent:3f00a2"
    home.marked.append(child)

    memory_writes.hand_on(child, f"dashboard:{GARBLED_CHAT}")

    assert memory_reads.reach_of(None, child).refusal == memory_reads.UNREADABLE
    assert session_restrictions.is_unreadable(child)

    def _inside_the_subagent() -> str | None:
        return memory_writes.restricted_mode()

    assert memory_writes.work_context(child).run(_inside_the_subagent) == memory_writes.UNREADABLE


# ── every reader asks the one reader ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(("name", "mode"), RESTRICTED)
def test_every_reader_reads_a_first_turn_chat_from_the_live_chat(home, name, mode):
    """Before the chat's transcript exists, the reads, the writes, the inheritance and the route
    guard all read the live chat."""
    from personalclaw.dashboard.handlers._shared import _is_restricted_session

    state = _state((name, mode))
    key = f"dashboard:{name}"
    request = SimpleNamespace(headers={"X-Session-Key": key})

    assert not _transcript(name).exists()
    assert memory_writes.session_mode(key, state=state) == mode
    assert memory_writes.blocks_memory_writes(key, state=state)
    assert ownership.inherit_mode(key, state=state) is MemoryMode(mode)
    assert memory_reads.reach_of(state, key).temporary is (mode == "temporary")
    assert _is_restricted_session(state, request)


@pytest.mark.asyncio
async def test_the_route_guard_refuses_a_chat_the_gateway_no_longer_holds_and_says_why(home):
    """A request naming a chat the gateway no longer holds and nothing records (a Temporary chat
    that has ended, whose agent's tool still calls in) is refused as a restricted chat's is, saying
    why; a live normal chat's is not. 🔴 Red before the fix: the ended chat's run started as
    normal work."""
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer

    from personalclaw.workflows.handlers import api_run_start

    await _save_the_workflow()
    app = web.Application()
    app["state"] = _state((NORMAL_CHAT, "persistent"))
    app.router.add_post("/api/workflows/runs", api_run_start)
    async with TestClient(TestServer(app)) as client:

        async def start_for(name: str) -> tuple[int, dict[str, Any]]:
            resp = await client.post(
                "/api/workflows/runs",
                json={"name": WORKFLOW},
                headers={"X-Session-Key": f"dashboard:{name}"},
            )
            return resp.status, await resp.json()

        status, body = await start_for(ENDED_CHAT)
        assert status == 403, body
        assert body["error"]["code"] == "restricted_session", body
        assert "memory setting of the chat it is for cannot be read" in body["error"]["message"]
        status, body = await start_for(NORMAL_CHAT)
        assert status != 403, body


def test_a_chat_whose_record_cannot_be_read_stays_out_of_chat_search(home):
    """The search index reads a chat's mode from its transcript before it indexes it; one whose
    metadata line cannot be read is kept out, as a restricted chat is. 🔴 Red before the fix: the
    cut-short line read as one that records no mode, and the chat was indexed and findable."""
    from personalclaw import session_search

    session_search.reset_for_tests()
    try:
        _garble(GARBLED_CHAT)
        assert session_search.reindex_session(f"dashboard:{GARBLED_CHAT}") is False
        assert session_search.search_sessions("lantern") == []
    finally:
        session_search.reset_for_tests()

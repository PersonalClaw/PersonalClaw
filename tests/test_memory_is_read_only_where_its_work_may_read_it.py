"""Your memory is read only for work that may read it: not a Temporary chat's, not an app's that
was not given it.

The agent's search of earlier chats kept two boundaries that every memory read crossed:

* **A conversation an app started.** Its agent's ``memory_recall`` and ``memory_list`` read the
  facts, lessons and episodes kept about you, and its turns were assembled with your memory,
  though the app never asked for it: the ``memory`` permission an app declares (install consent:
  "Read and change your memory") gated only the app's own requests, and its conversation's agent
  read past it. Now an app's work reads your memory only when the app holds that permission, and a
  refused read says why.
* **A Temporary chat's subagent.** A Temporary chat starts blank, and says so. Its subagent was
  marked Incognito, which reads, so the subagent recalled your memory. Now work for a Temporary
  chat (a subagent, its own subagents, a workflow step of a run it started, every tool call any of
  them makes) reads none, and says so when asked. Incognito keeps its own rule: it reads.

Driven as the agent drives it: the real gateway asking for a sign-in over a scratch home with
memory in it, the memory tools' provider the native agent calls (bound to the session it is called
from), a turn of the real turn engine on a scripted model, and a subagent's first prompt as its
run builds it.
"""

from __future__ import annotations

import contextlib
import json
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from personalclaw import mcp_core, memory_writes, session_restrictions
from personalclaw.history import ConversationLog
from personalclaw.tool_providers.base import ToolResult
from personalclaw.tool_providers.registry import create_memory_provider, create_native_provider

#: Every auth shortcut a test process might inherit; each would admit a call before the internal
#: credential is looked at.
_AUTH_SHORTCUTS = (
    "PERSONALCLAW_AUTH_MODE",
    "PERSONALCLAW_BYPASS_LOCAL_NETWORKS",
    "PERSONALCLAW_SESSION_KEY",
)

#: What a Temporary chat's work is told when it asks for a memory (`TEMPORARY`).
TEMPORARY = (
    "This is a Temporary chat, which starts blank: nothing is read from your memory for it or for "
    "any work it starts — no saved facts, lessons or earlier conversations."
)

#: What memory holds about her: a lesson she taught, a fact it keeps, an episode of a chat.
LESSON = "The allotment is plot 14 on the north field, never the balcony boxes."
FACT = ("pref.allotment", "plot 14, north field, shared shed")
EPISODE = "Planned the allotment rota for plot 14 with the neighbours on Sunday."

#: Her own chat, and a conversation an app started.
HERE = "dashboard:chat-15-1790500005"
APP = "allotment-planner"
APP_CHAT = "dashboard:chat-20-1790600000"
#: What the app needs to start a conversation and run its turns.
DECLARED = {"api": ["/api/chat", "/api/context"], "agent": "tools"}


def _install(home: Path, name: str, permissions: dict, *, enabled: bool = True) -> None:
    """An installed app in *home*, as the Store leaves one."""
    appdir = home / "apps" / name
    appdir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "name": name,
        "version": "1.0.0",
        "displayName": name,
        "description": "x",
        "permissions": permissions,
    }
    (appdir / "app.json").write_text(json.dumps(manifest), encoding="utf-8")
    installed = {"name": name, "version": "1.0.0", "enabled": enabled}
    (appdir / "installed.json").write_text(json.dumps(installed), encoding="utf-8")


def _chat(home: Path, key: str, text: str, *, app: str = "", mode: str = "") -> None:
    """A chat's transcript as the dashboard saves one: its metadata line, then one message."""
    meta: dict = {"_type": "metadata", "created_at": "2026-10-02T15:00:00+00:00"}
    if app:
        meta["created_by_app"] = app
    if mode:
        meta["memory_mode"] = mode
    turn = {"role": "user", "ts": "2026-10-02T15:00:00+00:00", "content": text}
    name = key.replace(":", "_")
    path = home / "sessions" / f"{name}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(meta) + "\n" + json.dumps(turn) + "\n", encoding="utf-8")


def _remember(home: Path) -> None:
    """Her memory, in the store the gateway reads."""
    from personalclaw.vector_memory import VectorMemoryStore

    store = VectorMemoryStore(db_path=home / "memory.db")
    store.init()
    store._graph_enabled = False
    assert store.write_lesson(LESSON, source="user_explicit")
    assert store.set_semantic(FACT[0], FACT[1], 1.0, "user") is None
    store.write_episodic(EPISODE, conversation_id="chat-1")
    store.close()


@dataclass
class _Gateway:
    home: Path
    port: int
    state: Any


@contextlib.asynccontextmanager
async def _gateway(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[_Gateway]:
    home = tmp_path / "data"
    user_home = tmp_path / "user"
    user_home.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    monkeypatch.setenv("HOME", str(user_home))
    for name in _AUTH_SHORTCUTS:
        monkeypatch.delenv(name, raising=False)
    _remember(home)
    _chat(home, HERE, "Plan the week.")
    _chat(home, APP_CHAT, "Plan the rota.", app=APP)

    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>spa</html>", encoding="utf-8")
    import personalclaw.dashboard.handlers.core as handlers_core_mod
    import personalclaw.dashboard.server as server_mod

    monkeypatch.setattr(server_mod, "_DIST_DIR", dist)
    monkeypatch.setattr(handlers_core_mod, "_DIST_DIR", dist)
    runner, state = await server_mod.start_dashboard(
        sessions=MagicMock(count=0), port=0, conversation_log=ConversationLog()
    )
    port = runner.addresses[0][1]
    monkeypatch.setenv("PERSONALCLAW_PORT", str(port))
    try:
        yield _Gateway(home=home, port=port, state=state)
    finally:
        await runner.cleanup()


async def _call(tool: str, arguments: dict, *, asked_from: str) -> tuple[bool, str]:
    """One call of a tool through the provider the native agent's tool comes from (the memory
    tools', or the core one's for ``get_context``), made for the session *asked_from*, as the
    native loop binds it."""
    provider = create_native_provider() if tool == "get_context" else create_memory_provider()
    token = mcp_core.set_current_session_key(asked_from)
    try:
        result: ToolResult = await provider.invoke(tool, arguments)
    finally:
        mcp_core.reset_current_session_key(token)
    return result.success, (result.output if result.success else result.error) or ""


async def _recall(asked_from: str) -> str:
    ok, text = await _call("memory_recall", {"query": "allotment"}, asked_from=asked_from)
    assert ok, text
    return text


def _works_for(state: Any, monkeypatch: pytest.MonkeyPatch, parent: str, *, app: str = "") -> str:
    """A subagent working for *parent*, as the gateway tracks one: its calls name its own key."""
    info = SimpleNamespace(parent_session_key=parent, app=app)
    monkeypatch.setattr(state, "subagents", SimpleNamespace(get=lambda agent_id: info, count=0))
    return "subagent:a1b2c3d4"


def _says_why_for_the_app(text: str) -> None:
    assert text.startswith(f"This work is for the app {APP}, which was not given your memory"), text
    assert "memory permission" in text, text


@pytest.fixture(autouse=True)
def _unmarked():
    """The registry is process-wide: no mark one test makes reaches another."""
    yield
    for key in (HERE, APP_CHAT, "subagent:a1b2c3d4", "subagent:c0ffee00"):
        session_restrictions.clear(key)


# ── a conversation an app started ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_an_apps_conversation_without_the_permission_recalls_nothing_and_says_why(
    tmp_path, monkeypatch
):
    """🔴 Red on integration: the app's conversation recalled the lesson, fact and episode."""
    async with _gateway(tmp_path, monkeypatch) as gw:
        _install(gw.home, APP, DECLARED)
        recalled = await _recall(APP_CHAT)
        listed_ok, listed = await _call("memory_list", {}, asked_from=APP_CHAT)

    for remembered in (LESSON, FACT[1], EPISODE):
        assert remembered not in recalled, remembered
    _says_why_for_the_app(recalled)
    assert listed_ok, listed
    assert LESSON not in listed
    _says_why_for_the_app(listed)


@pytest.mark.asyncio
async def test_an_apps_conversation_with_the_permission_recalls(tmp_path, monkeypatch):
    async with _gateway(tmp_path, monkeypatch) as gw:
        _install(gw.home, APP, {**DECLARED, "memory": True})
        recalled = await _recall(APP_CHAT)
        _ok, listed = await _call("memory_list", {}, asked_from=APP_CHAT)

    assert LESSON in recalled and EPISODE in recalled, recalled
    assert LESSON in listed, listed


@pytest.mark.asyncio
async def test_an_app_that_is_turned_off_reads_nothing_whatever_it_declared(tmp_path, monkeypatch):
    async with _gateway(tmp_path, monkeypatch) as gw:
        _install(gw.home, APP, {**DECLARED, "memory": True}, enabled=False)
        recalled = await _recall(APP_CHAT)

    assert LESSON not in recalled
    assert recalled == (
        f"This work is for the app {APP}, so nothing is read from your memory for it: "
        "app is disabled."
    )


@pytest.mark.asyncio
async def test_an_agent_an_apps_conversation_started_reads_as_the_app_may(tmp_path, monkeypatch):
    """The app is found up the chain, from the agent to the conversation it works for."""
    async with _gateway(tmp_path, monkeypatch) as gw:
        _install(gw.home, APP, DECLARED)
        agent = _works_for(gw.state, monkeypatch, APP_CHAT)
        recalled = await _recall(agent)

    assert LESSON not in recalled
    _says_why_for_the_app(recalled)


@pytest.mark.asyncio
async def test_your_own_chat_still_recalls(tmp_path, monkeypatch):
    async with _gateway(tmp_path, monkeypatch) as gw:
        _install(gw.home, APP, DECLARED)
        recalled = await _recall(HERE)

    assert LESSON in recalled and EPISODE in recalled, recalled


# ── a Temporary chat, its subagents and its runs; an Incognito chat ────────────────────────


@pytest.mark.asyncio
async def test_a_temporary_chats_subagent_recalls_nothing(tmp_path, monkeypatch):
    """🔴 Red on integration: the subagent recalled the lesson, though its chat starts blank."""
    async with _gateway(tmp_path, monkeypatch) as gw:
        session_restrictions.mark_temporary(HERE)
        agent = _works_for(gw.state, monkeypatch, HERE)
        recalled = await _recall(agent)
        _ok, listed = await _call("memory_list", {}, asked_from=agent)

    for text in (recalled, listed):
        assert LESSON not in text
        assert text == TEMPORARY


@pytest.mark.asyncio
async def test_a_temporary_chat_recalls_nothing_and_says_why(tmp_path, monkeypatch):
    """Its answer used to be "No matching memory found.", which a model reads as "not there"."""
    async with _gateway(tmp_path, monkeypatch):
        session_restrictions.mark_temporary(HERE)
        recalled = await _recall(HERE)

    assert recalled == TEMPORARY


@pytest.mark.asyncio
async def test_a_workflow_step_of_a_temporary_chats_run_recalls_nothing(tmp_path, monkeypatch):
    """A step's agent works for the run's own key; the run records the chat that started it."""
    from personalclaw.workflows import ownership, store
    from personalclaw.workflows.models import RunOrigin, WorkflowRun

    async with _gateway(tmp_path, monkeypatch) as gw:
        run = store.create(
            WorkflowRun(
                id=store.new_run_id(),
                workflow_name="tidy-notes",
                origin=RunOrigin(session_key=HERE),
                extra=ownership.stamp_run_mode({}, ownership.MemoryMode.TEMPORARY),
            )
        )
        agent = _works_for(gw.state, monkeypatch, ownership.owned_key(run.id, "draft"))
        recalled = await _recall(agent)

    assert recalled == TEMPORARY


@pytest.mark.asyncio
async def test_an_incognito_chat_and_its_subagent_still_recall(tmp_path, monkeypatch):
    async with _gateway(tmp_path, monkeypatch) as gw:
        session_restrictions.mark_incognito(HERE)
        in_the_chat = await _recall(HERE)
        agent = _works_for(gw.state, monkeypatch, HERE)
        session_restrictions.mark_incognito(agent)
        in_its_agent = await _recall(agent)

    assert LESSON in in_the_chat and LESSON in in_its_agent


def test_a_temporary_chats_subagent_is_marked_temporary_when_it_starts(tmp_path, monkeypatch):
    """🔴 Red on integration: it was marked Incognito, which reads. Marked Temporary, it reads
    nothing and writes nothing, and every check that reads the mark agrees."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    with memory_writes.derived_from(HERE, memory_mode="temporary"):
        memory_writes.hand_on("subagent:a1b2c3d4", HERE)
    session_restrictions.mark_temporary(APP_CHAT)
    memory_writes.hand_on("subagent:c0ffee00", APP_CHAT)

    from personalclaw.memory_reads import reach_of

    for child in ("subagent:a1b2c3d4", "subagent:c0ffee00"):
        assert session_restrictions.is_temporary(child), child
        assert memory_writes.blocks_memory_writes(child), child
        assert reach_of(None, child).refusal == TEMPORARY


@pytest.mark.asyncio
async def test_a_subagent_started_outside_its_chats_request_is_still_marked_temporary(
    tmp_path, monkeypatch
):
    """The spawn asks the gateway's own answer (`SubagentManager.memory_reach`, wired by the
    state that holds the manager), so a Temporary chat that only the live chat records still
    hands its rule on."""
    from personalclaw.dashboard.state import DashboardState
    from personalclaw.subagent import SubagentManager

    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    sessions = MagicMock(count=0)
    sessions.get_or_create = AsyncMock(side_effect=RuntimeError("the run is not this test's"))
    ctx = MagicMock()
    ctx.hooks.auto_approve_subagent_spawn = True
    manager = SubagentManager(sessions=sessions, ctx_builder=ctx)
    state = DashboardState(sessions=MagicMock(count=0), start_time=0.0, subagents=manager)
    state.push_sessions_update = MagicMock()
    state.get_or_create_session("chat-15-1790500005", memory_mode="temporary")
    child = ""
    try:
        info = manager.spawn("Sum up the allotment rota.", parent_session_key=HERE)
        child = f"subagent:{info.id}"
        assert session_restrictions.is_temporary(child), child
        assert memory_writes.blocks_memory_writes(child)
    finally:
        await manager.cancel_all()
        session_restrictions.clear(child)


def test_an_incognito_chats_subagent_is_marked_incognito(tmp_path, monkeypatch):
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    with memory_writes.derived_from(HERE, memory_mode="incognito"):
        memory_writes.hand_on("subagent:a1b2c3d4", HERE)

    from personalclaw.memory_reads import reach_of

    assert session_restrictions.is_incognito("subagent:a1b2c3d4")
    assert not session_restrictions.is_temporary("subagent:a1b2c3d4")
    assert reach_of(None, "subagent:a1b2c3d4").reads


# ── what a turn is assembled with ──────────────────────────────────────────────────────────


class _Scripted:
    """A model that answers at once, and keeps what it was handed."""

    supports_tools = True
    _model = "scripted"

    def __init__(self) -> None:
        self.handed: list[str] = []

    async def complete(self, messages, *, tools=None, model=None, reasoning_effort=""):
        from personalclaw.llm.events import EVENT_COMPLETE, EVENT_TEXT_CHUNK, AgentEvent

        self.handed.append(json.dumps(messages))
        yield AgentEvent(kind=EVENT_TEXT_CHUNK, text="Rota drafted.")
        yield AgentEvent(kind=EVENT_COMPLETE, input_tokens=10, output_tokens=5)


def _builder(home: Path):
    """The context a turn is assembled from, over the memory the gateway keeps."""
    from personalclaw.context import ContextBuilder
    from personalclaw.memory import MemoryStore
    from personalclaw.skills import SkillsLoader
    from personalclaw.vector_memory import VectorMemoryStore

    markdown = MemoryStore(workspace=home / "workspace")
    markdown.init()
    store = VectorMemoryStore(db_path=home / "memory.db", confidence_threshold=0.0)
    store.init()
    markdown.vector_store = store
    builder = ContextBuilder(
        memory=markdown,
        skills=SkillsLoader(skills_path=home / "skills", install_builtins=False),
    )
    # Called as the real one is, by the turn's readers and by the after-turn review (``writes``).
    builder.get_memory_for = staticmethod(  # type: ignore[method-assign]
        lambda cwd=None, memory_store=None, *, writes=False: markdown
    )
    return builder, store


@contextlib.asynccontextmanager
async def _turns(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """The real turn engine over a native runtime on a scripted model, with her memory."""
    from personalclaw.agents.native.runtime import NativeAgentRuntime
    from personalclaw.agents.provider import AgentRuntimeDefinition
    from personalclaw.context_engine import set_engine
    from personalclaw.dashboard.state import DashboardState

    home = tmp_path / "data"
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    _remember(home)
    model = _Scripted()
    runtime = NativeAgentRuntime(
        definition=AgentRuntimeDefinition(name="PersonalClaw", provider="native", model="tiny"),
        model_provider=model,
        tool_providers=[],
    )
    await runtime.start()
    sessions = MagicMock(count=0)
    sessions._sessions = {}
    sessions.get_pid = MagicMock(return_value=None)
    sessions.get_channel_link = MagicMock(return_value=(None, None))
    sessions.get_or_create = AsyncMock(return_value=(runtime, True, False))
    sessions.record_failure = AsyncMock()
    log = ConversationLog(base_dir=home / "sessions")
    state = DashboardState(sessions=sessions, start_time=0.0, conversation_log=log)
    builder, store = _builder(home)
    builder.conversation_log = log
    state.context_builder = builder
    state._hook_store = None
    state.broadcast_ws = MagicMock()
    state.push_sessions_update = MagicMock()
    set_engine(None)
    try:
        yield state, model, home
    finally:
        store.close()


async def _turn(state, name: str, *, app: str = "", mode: str | None = None):
    from personalclaw.dashboard.chat_runner import run_chat

    session = state.get_or_create_session(name, memory_mode=mode, created_by_app=app)
    session.append("user", "Draft the allotment rota.", "msg msg-u")
    with patch("personalclaw.dashboard.chat_runner.sel", MagicMock()):
        await run_chat(state, session, "Draft the allotment rota.")


def _fed(state) -> dict:
    """The turn's context line: what it says it was fed."""
    lines = [
        c.args[1]
        for c in state.broadcast_ws.call_args_list
        if c.args[0] == "activity_event"
        and c.args[1].get("kind") in ("context", "context_without_memory")
    ]
    assert len(lines) == 1, lines
    return lines[0]


@pytest.mark.asyncio
async def test_an_apps_conversation_is_assembled_without_your_memory(tmp_path, monkeypatch):
    """🔴 Red on integration: the turn was handed the lesson, and its line said memory fed it."""
    async with _turns(tmp_path, monkeypatch) as (state, model, home):
        _install(home, APP, DECLARED)
        await _turn(state, "chat-20-1790600000", app=APP)

    handed = "".join(model.handed)
    assert model.handed, "the turn reached its model"
    for remembered in (LESSON, FACT[1], EPISODE):
        assert remembered not in handed, remembered
    fed = _fed(state)
    assert fed["kind"] == "context_without_memory", fed
    assert fed["text"].endswith(
        f"none of it from your memory: the app {APP} cannot read your memory"
    )


@pytest.mark.asyncio
async def test_an_apps_conversation_with_the_permission_is_assembled_with_it(tmp_path, monkeypatch):
    async with _turns(tmp_path, monkeypatch) as (state, model, home):
        _install(home, APP, {**DECLARED, "memory": True})
        await _turn(state, "chat-20-1790600000", app=APP)

    assert LESSON in "".join(model.handed)
    assert _fed(state)["kind"] == "context"


@pytest.mark.asyncio
async def test_a_temporary_turn_says_no_memory_fed_it(tmp_path, monkeypatch):
    """Its line claimed "memory, lessons, history, episodic" for a prompt that held none."""
    async with _turns(tmp_path, monkeypatch) as (state, model, _home):
        await _turn(state, "chat-21-1790600100", mode="temporary")

    assert LESSON not in "".join(model.handed)
    fed = _fed(state)
    assert fed["kind"] == "context_without_memory"
    assert fed["text"].endswith("none of it from your memory: this is a Temporary chat")


@pytest.mark.asyncio
async def test_your_own_turn_is_still_assembled_with_your_memory(tmp_path, monkeypatch):
    async with _turns(tmp_path, monkeypatch) as (state, model, _home):
        await _turn(state, "chat-22-1790600200")

    assert LESSON in "".join(model.handed)
    assert _fed(state)["kind"] == "context"


@pytest.mark.asyncio
@pytest.mark.parametrize(("mode", "reads"), [("temporary", False), ("incognito", True)])
async def test_a_subagents_first_prompt_reads_memory_as_its_chat_may(
    tmp_path, monkeypatch, mode, reads
):
    """🔴 Red on integration: a subagent's first prompt was assembled with memory whatever chat
    it worked for."""
    from personalclaw.dashboard.state import DashboardState
    from personalclaw.memory_reads import reach_of
    from personalclaw.subagent import SubagentInfo
    from personalclaw.subagent_prompt import first_prompt

    home = tmp_path / "data"
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    _remember(home)
    builder, store = _builder(home)
    state = DashboardState(sessions=MagicMock(count=0), start_time=0.0)
    chat = state.get_or_create_session("chat-15-1790500005", memory_mode=mode)
    info = SubagentInfo(id="a1b2c3d4", task="Sum up the allotment rota.", parent_session_key=HERE)
    monkeypatch.setattr(state, "subagents", SimpleNamespace(get=lambda agent_id: info, count=0))
    try:
        with patch(
            "personalclaw.context_headroom.resolve_window", new=AsyncMock(return_value=None)
        ):
            prompt = await first_prompt(
                info,
                named_agent="",
                assemble=partial(builder.build_message, agent=""),
                client=MagicMock(),
                is_new=True,
                session_key="subagent:a1b2c3d4",
                reach=partial(reach_of, state),
            )
    finally:
        store.close()

    assert chat.memory_mode == mode
    assert "Sum up the allotment rota." in prompt
    assert (LESSON in prompt) is reads, prompt


@pytest.mark.asyncio
async def test_a_subagents_first_prompt_reads_no_memory_when_nothing_can_say_whose_it_is(
    tmp_path, monkeypatch
):
    from personalclaw.subagent import SubagentInfo
    from personalclaw.subagent_prompt import first_prompt

    home = tmp_path / "data"
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    _remember(home)
    builder, store = _builder(home)
    info = SubagentInfo(id="a1b2c3d4", task="Sum up the allotment rota.", parent_session_key=HERE)
    try:
        with patch(
            "personalclaw.context_headroom.resolve_window", new=AsyncMock(return_value=None)
        ):
            prompt = await first_prompt(
                info,
                named_agent="",
                assemble=partial(builder.build_message, agent=""),
                client=MagicMock(),
                is_new=True,
                session_key="subagent:a1b2c3d4",
                reach=None,
            )
    finally:
        store.close()

    assert LESSON not in prompt


# ── get_context's memory tier ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_context_reads_no_memory_for_an_apps_conversation(tmp_path, monkeypatch):
    """🔴 Red on integration: `get_context` recalled episodes for any caller."""
    async with _gateway(tmp_path, monkeypatch) as gw:
        _install(gw.home, APP, DECLARED)
        ok, text = await _call("get_context", {"query": "allotment"}, asked_from=APP_CHAT)
        own_ok, own = await _call("get_context", {"query": "allotment"}, asked_from=HERE)

    assert ok, text
    assert EPISODE not in text
    assert f"_Not read here: This work is for the app {APP}, which was not given" in text
    assert "Memories: none are read for this work" in text
    assert own_ok and EPISODE in own, own


@pytest.mark.asyncio
async def test_get_context_reads_no_memory_for_an_apps_own_request(tmp_path, monkeypatch):
    """An app's own token reaches the route with `/api/context` declared, and no `memory`."""
    from aiohttp.test_utils import TestClient, TestServer
    from test_apps_cannot_post_into_your_chats import _gateway as _app_gateway

    from personalclaw.dashboard.handlers.context import api_context_get

    async with _gateway(tmp_path, monkeypatch) as gw:
        _install(gw.home, APP, DECLARED)
        app = _app_gateway(gw.state, APP, [("GET", "/api/context", api_context_get)])
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/api/context", params={"query": "allotment"})
            body = await resp.json()

    assert resp.status == 200, body
    assert body["memories"] == []
    assert body["memory_withheld"].startswith(f"This work is for the app {APP}")


# ── the one answer ──────────────────────────────────────────────────────────────────────────


def test_your_own_pages_and_tools_read_your_memory(tmp_path, monkeypatch):
    from personalclaw.memory_reads import reach_of

    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    for caller in ("", "dashboard:ui"):
        assert reach_of(None, caller).reads, caller


def test_a_mode_its_transcript_records_and_cannot_be_read_reads_nothing(tmp_path, monkeypatch):
    """As it writes nothing (`memory_writes.blocks_memory_writes`)."""
    from personalclaw.memory_reads import UNREADABLE, reach_of

    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    path = tmp_path / "sessions" / "slack_C1_1700000000.1.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text("{not json\n", encoding="utf-8")

    reach = reach_of(None, "slack:C1:1700000000.1")

    assert reach.refusal == UNREADABLE
    assert os.path.exists(path)


def test_a_channel_thread_its_transcript_records_as_temporary_reads_nothing(tmp_path, monkeypatch):
    """After a restart the registry is empty, and the thread's transcript is what says so."""
    from personalclaw.memory_reads import reach_of

    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    _chat(tmp_path, "slack:C1:1700000000.2", "what is plot 14?", mode="temporary")

    assert reach_of(None, "slack:C1:1700000000.2").temporary

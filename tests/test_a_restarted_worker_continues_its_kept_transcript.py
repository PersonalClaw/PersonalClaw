"""A chat kept on disk is continued by whatever opens it again by its name, and a restart never
shortens one.

A session made for a name whose transcript is kept on disk started with no messages: only the
dashboard's own ways back (the start's restore, opening a chat from the list, resume) loaded the
transcript, and every other caller that names its session got a blank one. Its next save rewrites
the file from what the session holds, so the kept turns were replaced. Measured: a loop worker the
watchdog re-armed after a restart went from six messages to two at the stop. The same held for a
loop's task workers and its planner, for a client of the OpenAI-compatible endpoint that keeps its
conversation, and for a schedule's chat.

Each of these is one ongoing conversation by what its feature says (an interrupted loop resumes, a
task worker carries on with its task, a loop's planner is one session across its passes, a client
that keeps its conversation continues it, and a schedule's chat is the schedule's conversation), so
each continues where it was, and its model is given its earlier turns.

A gateway here is what one is on a running host: the production session manager and runtimes over
the home's transcripts, on a model that records what it is handed. Its stop saves every chat it
holds, as a stopping gateway does, and a restart is a second one over the same home. Each way in
is the product's own: the watchdog's re-arm, the scheduler's task spawn, the planner pass, the
endpoint's route and the schedule's Open as chat.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from chat_test_helpers import _make_app_with_agent_routes, _make_state

from personalclaw.config.external_access import ExternalAccessConfig
from personalclaw.config.external_access import ExternalAccessSurfaceConfig as Surface
from personalclaw.config.loader import AgentProfile, AppConfig
from personalclaw.config.transactions import mutate_config
from personalclaw.context import ContextBuilder
from personalclaw.dashboard.chat import run_chat
from personalclaw.dashboard.chat_persistence import (
    _rehydrate_session_from_history,
    restore_recent_sessions,
    save_all_sessions_to_history,
    save_session_to_history,
)
from personalclaw.dashboard.chat_utils import persisted_history_key
from personalclaw.dashboard.routes import register_dashboard_routes
from personalclaw.dashboard.schedule_inject import inject_schedule_result_to_session
from personalclaw.dashboard.state import DashboardState
from personalclaw.history import ConversationLog
from personalclaw.inbound import auth, caps, clients
from personalclaw.inbound import openai_dialect as dialect
from personalclaw.llm.base import EVENT_COMPLETE, EVENT_TEXT_CHUNK, LLMEvent
from personalclaw.llm.capabilities import Capability, ProviderCapability
from personalclaw.llm.registry import ProviderEntry, ProviderRegistry
from personalclaw.loop import files as loop_files
from personalclaw.loop import manager, store
from personalclaw.loop.loop import Loop
from personalclaw.loop.plan_walkthrough import planner_session_key
from personalclaw.loop.watchdog import LoopWatchdog
from personalclaw.memory import MemoryStore
from personalclaw.planning import runner as planner
from personalclaw.providers.provider_bridge import create_provider_factory
from personalclaw.providers.use_cases import save_active_models
from personalclaw.schedule import ScheduleJob
from personalclaw.session import SessionManager
from personalclaw.skills import SkillsLoader

_SURFACES = ("OPENAI", "MCP", "A2A", "CAPTURE", "BRIDGE")
ENTRY = "recorder"
AGENT = "researcher"
RULES = "Work as the research assistant: cite every source you read."
TAG = "release-notes"

#: The mark a turn's message ends on, which its answer repeats: what each turn said is known by it.
_MARK = re.compile(r"\[mark-[a-z0-9]+\]")


def _mark_of(text: str) -> str:
    found = _MARK.findall(text)
    return found[-1] if found else ""


class _Model:
    """A chat model that answers each turn by the mark its message ends on, and records the
    message list it was handed."""

    supports_tools = False

    def __init__(self, asked: list[list[dict]]) -> None:
        self.asked = asked

    async def start(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None

    async def complete(self, messages: list[dict], *, model: str | None = None, **_kw: Any):
        handed = [dict(m) for m in messages]
        self.asked.append(handed)
        last = next((m for m in reversed(handed) if m.get("role") == "user"), {})
        yield LLMEvent(kind=EVENT_TEXT_CHUNK, text=f"noted {_mark_of(str(last.get('content')))}")
        yield LLMEvent(kind=EVENT_COMPLETE, input_tokens=3, output_tokens=2)

    async def stream(self, message: str):
        """The one-shot form a chat's background work (its title) asks with."""
        yield LLMEvent(kind=EVENT_TEXT_CHUNK, text="Work notes")
        yield LLMEvent(kind=EVENT_COMPLETE, input_tokens=1, output_tokens=1)


class _NudgeRows:
    """The nudge service's rows: what a loop, a task worker and a planner pass arm. A restarted
    gateway starts with none in memory: a row whose chat is not open is removed when it fires, and
    the scheduler spawns its worker again. ``on_add`` stands in for the row firing: it runs the
    cycle it was armed with."""

    def __init__(self) -> None:
        self.rows: dict[str, SimpleNamespace] = {}
        self.on_add: Any = None

    async def add(self, *, session_name: str, message: str, **_kw: Any) -> SimpleNamespace:
        row = SimpleNamespace(id=f"row-{len(self.rows) + 1}", session_name=session_name)
        row.message, row.active = message, True
        self.rows = {k: r for k, r in self.rows.items() if r.session_name != session_name}
        self.rows[row.id] = row
        if self.on_add is not None:
            await self.on_add(session_name, message)
        return row

    def get_by_session(self, session_name: str) -> SimpleNamespace | None:
        return next((r for r in self.rows.values() if r.session_name == session_name), None)

    def list_all(self) -> list[SimpleNamespace]:
        return list(self.rows.values())

    async def update(self, row_id: str, **kw: Any) -> None:
        row = self.rows.get(row_id)
        if row is not None:
            for name, value in kw.items():
                setattr(row, name, value)

    async def remove(self, row_id: str) -> None:
        self.rows.pop(row_id, None)


class _Gateway:
    """One run of the gateway over the test's home."""

    def __init__(self, home: Path, asked: list[list[dict]], monkeypatch: pytest.MonkeyPatch):
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
            lambda *, entry, session_key=None, **kw: _Model(asked),
        )
        registry.register_entry(ProviderEntry(name=ENTRY, type=ENTRY, model="research-1"))
        monkeypatch.setattr("personalclaw.llm.registry.get_default_registry", lambda: registry)
        self.sessions = SessionManager(AppConfig.load(), provider_factory=create_provider_factory())
        self.log = ConversationLog(base_dir=home / "history")
        self.state = DashboardState(
            sessions=self.sessions, start_time=0.0, conversation_log=self.log
        )
        self.state.context_builder = ContextBuilder(
            memory=MemoryStore(workspace=home / "ws"),
            skills=SkillsLoader(skills_path=home / "skills", install_builtins=False),
            conversation_log=self.log,
        )
        self.state._hook_store = None
        self.state.broadcast_ws = lambda *a, **k: None
        self.state.push_sessions_update = lambda *a, **k: None
        self.svc = _NudgeRows()
        self.http: TestClient | None = None

    async def serve(self) -> TestClient:
        """The gateway's own route table, for a way in that is a request."""
        if self.http is None:
            app = web.Application()
            app["state"] = self.state
            register_dashboard_routes(app)
            self.http = TestClient(TestServer(app))
            await self.http.start_server()
        return self.http

    async def stop(self) -> None:
        """The gateway stopping: the last save of every chat it holds, then its runtimes."""
        save_all_sessions_to_history(self.state)
        if self.http is not None:
            await self.http.close()
        await self.sessions.close_all()


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A home with one agent on the recording model, and the endpoint switched on."""
    cfg = AppConfig.load()
    cfg.agents[AGENT] = AgentProfile(system_prompt=RULES, model=f"{ENTRY}:research-1")
    cfg.default_agent = AGENT
    cfg.external_access = ExternalAccessConfig(
        enabled=True, openai=Surface(enabled=True, allow_remote=False)
    )
    cfg.save()
    mutate_config(
        lambda doc: doc.setdefault("providers", []).append(
            {"name": ENTRY, "type": ENTRY, "model": "research-1"}
        )
    )
    save_active_models({"chat": [f"{ENTRY}:research-1"]})
    monkeypatch.setattr(planner, "PLANNER_POLL_SECS", 0.05)
    monkeypatch.setattr(dialect, "TURN_TIMEOUT_SECS", 8.0)
    monkeypatch.setattr(dialect, "_POLL_TIMEOUT_SECS", 0.5)
    for surface in _SURFACES:
        monkeypatch.delenv(f"PERSONALCLAW_INBOUND_{surface}_TOKEN", raising=False)
    caps.reset_for_tests()
    # The surface is answered once it has a token of its own, as `inbound token create` mints.
    auth.create_surface_token(dialect.OPENAI_SURFACE)
    yield tmp_path
    caps.reset_for_tests()
    for surface in _SURFACES:
        os.environ.pop(f"PERSONALCLAW_INBOUND_{surface}_TOKEN", None)


async def _cycle(gw: _Gateway, session: Any, mark: str) -> None:
    """One cycle of a nudged worker, as the gateway's nudge loop runs one: the cycle's message on
    the worker's chat, then its turn."""
    message = f"[auto-nudge cycle]\nWork the next cycle of the brief. {mark}"
    session.append("nudge", message, "msg msg-nudge")
    await run_chat(gw.state, session, message)


async def _owner_says(gw: _Gateway, session: Any, mark: str) -> None:
    """The owner's message in a chat, as the dashboard sends one."""
    message = f"What should happen next? {mark}"
    session.append("user", message, "msg msg-u")
    await run_chat(gw.state, session, message)


def _goal_loop() -> Loop:
    return store.create(
        Loop(
            id="",
            name="Latency",
            kind="goal",
            task="investigate the latency regression",
            kind_config={"goal_type": "open_ended"},
            attended=False,
            agent=AGENT,
        )
    )


# ── the ways a named worker session is opened again ────────────────────────────────────────────


class _LoopWorker:
    """A running loop's worker: started on the first gateway, re-armed by the next one's watchdog
    as its boot sweep re-arms every loop a restart left running."""

    label = "a loop the watchdog re-arms"
    owner_words = False

    def __init__(self, home: Path) -> None:
        self.loop_id = _goal_loop().id

    async def turn(self, gw: _Gateway, mark: str) -> Any:
        key = manager.session_key(self.loop_id)
        if key not in gw.state._sessions:
            loop = store.get(self.loop_id)
            if loop is not None and loop.status == "running":
                assert await LoopWatchdog(gw.state, gw.svc)._rearm_running(loop)
            else:
                await manager.start(gw.state, gw.svc, self.loop_id)
        session = gw.state._sessions[key]
        await _cycle(gw, session, mark)
        return session


class _TaskWorker:
    """A loop's worker on one task, in its own checkout, spawned by the scheduler on each
    gateway."""

    label = "a loop's task worker"
    owner_words = False

    def __init__(self, home: Path) -> None:
        self.loop_id = _goal_loop().id
        self.task = SimpleNamespace(id="t-query", title="Fix the slow query", description="")
        self.checkout = home / "checkouts" / self.task.id
        self.checkout.mkdir(parents=True)

    async def turn(self, gw: _Gateway, mark: str) -> Any:
        key = manager.task_session_key(self.loop_id, self.task.id)
        if key not in gw.state._sessions:
            loop = store.get(self.loop_id)
            assert loop is not None
            await manager.spawn_task_worker(gw.state, gw.svc, loop, self.task, str(self.checkout))
        session = gw.state._sessions[key]
        await _cycle(gw, session, mark)
        return session


class _Planner:
    """A loop's planner: each pass arms the planner session and its nudge row, the row's cycle
    runs, and the planner writes the file the pass waits for."""

    label = "a loop's planner pass"
    owner_words = False

    def __init__(self, home: Path) -> None:
        self.loop_id = _goal_loop().id
        self.folder = str(loop_files.loop_dir(self.loop_id))

    async def turn(self, gw: _Gateway, mark: str) -> Any:
        key = planner_session_key(self.loop_id)

        async def _fires(session_name: str, message: str) -> None:
            await _cycle(gw, gw.state._sessions[session_name], mark)
            Path(self.folder, "plan_steps.json").write_text('{"steps": []}', encoding="utf-8")

        gw.svc.on_add = _fires
        passed = await planner.run_planner_pass(
            gw.state,
            gw.svc,
            session_key=key,
            agent_name=AGENT,
            workspace_dir="",
            files_dir=self.folder,
            sentinel="plan_steps.json",
            brief=f"Design the steps of the plan. {mark}",
            app="loops",
            timeout_secs=30,
        )
        assert passed.ended == planner.WROTE, passed
        return gw.state._sessions[key]


class _KeptEndpointConversation:
    """A client of the OpenAI-compatible endpoint registered to keep its conversation: each
    request continues its session."""

    label = "a /v1 client that keeps its conversation"
    owner_words = True

    def __init__(self, home: Path) -> None:
        record, self.token = clients.create_client("notes app", surfaces=["openai"])
        registered = clients.load_clients()
        registered[record.client_id].persistent_sessions = True
        clients.save_clients(registered)
        self.key = dialect.session_key_for(record.client_id, TAG)

    async def turn(self, gw: _Gateway, mark: str) -> Any:
        http = await gw.serve()
        body = {
            "model": AGENT,
            "user": TAG,
            "messages": [{"role": "user", "content": f"What should happen next? {mark}"}],
        }
        resp = await http.post(
            dialect.ROUTE_CHAT,
            data=json.dumps(body),
            headers={"Authorization": f"Bearer {self.token}"},
        )
        assert resp.status == 200, await resp.text()
        answer = (await resp.json())["choices"][0]["message"]["content"]
        assert answer == f"noted {mark}"
        session = gw.state._sessions[self.key]
        for _ in range(500):
            if not session.running:
                break
            await _settle()
        return session


class _ScheduleChat:
    """A schedule's chat: each Open as chat threads the schedule's last result in, and the owner
    talks to it there."""

    label = "a schedule's chat"
    owner_words = True

    def __init__(self, home: Path) -> None:
        self.job = ScheduleJob(id="nightly", name="Nightly digest")
        self.runs = 0
        #: The schedule's own conversation: what fills its chat the first time it is opened.
        self.history = [
            {"role": "user", "content": "Summarise the night's alerts."},
            {"role": "assistant", "content": "Two alerts overnight, both resolved."},
        ]

    async def turn(self, gw: _Gateway, mark: str) -> Any:
        self.runs += 1
        # The schedule ran again since the chat was last opened: its own conversation holds that
        # run too. A chat that is open, or kept, threads only the result in.
        self.history += [
            {"role": "user", "content": f"Summarise the night's alerts (run {self.runs})."},
            {"role": "assistant", "content": f"Run {self.runs}: nothing new overnight."},
        ]
        session = inject_schedule_result_to_session(
            gw.state, self.job, f"Run {self.runs} found nothing new.", history=self.history
        )
        await _owner_says(gw, session, mark)
        return session


_PATHS = [_LoopWorker, _TaskWorker, _Planner, _KeptEndpointConversation, _ScheduleChat]


async def _settle() -> None:
    import asyncio

    await asyncio.sleep(0.01)


def _kept(gw: _Gateway, name: str) -> list[tuple[str, str]]:
    """What the transcript kept under *name* says, read from the file, never from a chat."""
    path = gw.log._path(persisted_history_key(gw.log, name))
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    return [(m["role"], m["content"]) for m in map(json.loads, lines[1:]) if m]


def _handed_for(asked: list[list[dict]], mark: str) -> str:
    """Everything the model was handed for the turn whose message ends on *mark*."""
    calls = [
        call
        for call in asked
        if _mark_of(
            str(next((m for m in reversed(call) if m.get("role") == "user"), {}).get("content"))
        )
        == mark
    ]
    assert calls, f"the model was never asked for {mark}"
    return "\n".join(str(m.get("content") or "") for m in calls[-1])


@pytest.mark.asyncio
@pytest.mark.parametrize("path", _PATHS, ids=lambda p: p.label)
async def test_a_restart_continues_the_named_session_it_opens_again(home, monkeypatch, path):
    """🔴 Red before: after the restart the session came back with no messages, its model was given
    none of the earlier turns, and the stop's save replaced the kept transcript with the one turn
    since (a loop worker: six messages, then two)."""
    asked: list[list[dict]] = []
    way_in = path(home)
    first = _Gateway(home, asked, monkeypatch)
    try:
        session = await way_in.turn(first, "[mark-one]")
        await way_in.turn(first, "[mark-two]")
        name = session.key
    finally:
        await first.stop()
    before = _kept(first, name)
    assert ("assistant", "noted [mark-one]") in before, "precondition: the first run was kept"
    assert ("assistant", "noted [mark-two]") in before

    second = _Gateway(home, asked, monkeypatch)
    try:
        again = await way_in.turn(second, "[mark-three]")
        assert again.key == name
    finally:
        await second.stop()
    after = _kept(second, name)

    assert after[: len(before)] == before, "the restart's save replaced the kept transcript"
    assert ("assistant", "noted [mark-three]") in after[len(before) :]
    # A loop, a task worker, a planner, a kept conversation and a schedule's chat each continue:
    # the restarted session's model is given its earlier turns (what was said to it, when it was
    # the owner or a client, and what it answered).
    handed = _handed_for(asked, "[mark-three]")
    assert "noted [mark-one]" in handed and "noted [mark-two]" in handed, handed[-2000:]
    if way_in.owner_words:
        assert "What should happen next? [mark-one]" in handed


@pytest.mark.asyncio
async def test_a_schedules_chat_opened_after_a_restart_is_not_filled_again(home, monkeypatch):
    """A schedule's chat is filled from the schedule's own conversation once, when it is new; each
    later Open as chat threads only the result in, as it does while the chat stays open. A kept
    chat opened after a restart is not a new one, so the runs the schedule made since are not
    poured into it again as turns of their own."""
    asked: list[list[dict]] = []
    schedule = _ScheduleChat(home)
    first = _Gateway(home, asked, monkeypatch)
    try:
        session = await schedule.turn(first, "[mark-one]")
        name = session.key
    finally:
        await first.stop()
    before = _kept(first, name)

    second = _Gateway(home, asked, monkeypatch)
    try:
        await schedule.turn(second, "[mark-two]")
    finally:
        await second.stop()
    added = _kept(second, name)[len(before) :]

    assert [role for role, _ in added] == ["assistant", "user", "assistant"], added
    assert "Run 2 found nothing new." in added[0][1]
    assert not any("nothing new overnight" in text for _, text in added)


# ── positive controls ──────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("way_back", ["restored at the start", "opened from the list"])
async def test_a_dashboard_chat_restored_after_a_restart_is_unchanged(home, monkeypatch, way_back):
    """Positive control: a dashboard chat comes back from a restart holding exactly what was kept,
    continues, and its model is given its earlier turns."""
    asked: list[list[dict]] = []
    first = _Gateway(home, asked, monkeypatch)
    try:
        chat = first.state.get_or_create_session()
        await _owner_says(first, chat, "[mark-one]")
        await _owner_says(first, chat, "[mark-two]")
    finally:
        await first.stop()
    before = _kept(first, chat.key)

    second = _Gateway(home, asked, monkeypatch)
    try:
        if way_back == "restored at the start":
            restore_recent_sessions(second.state, 0)
            again = second.state._sessions[chat.key]
        else:
            again = _rehydrate_session_from_history(second.state, chat.key)
        assert again is not None
        assert [(m["role"], m["content"]) for m in again.messages] == before
        await _owner_says(second, again, "[mark-three]")
    finally:
        await second.stop()

    assert _kept(second, chat.key)[: len(before)] == before
    assert "noted [mark-one]" in _handed_for(asked, "[mark-three]")


@pytest.mark.asyncio
async def test_a_brand_new_named_session_still_starts_empty(home, monkeypatch):
    """Positive control: a name nothing is kept under is a new chat. It starts with no messages and
    a tab id of its own, and its model is given no earlier turn."""
    asked: list[list[dict]] = []
    gw = _Gateway(home, asked, monkeypatch)
    try:
        worker = gw.state.get_or_create_session(
            name=manager.session_key("l-fresh"), agent=AGENT, app="loop"
        )
        assert worker.messages == []
        assert worker._tab_id
        await _cycle(gw, worker, "[mark-one]")
    finally:
        await gw.stop()
    assert "noted [" not in _handed_for(asked, "[mark-one]")
    assert _kept(gw, worker.key)[-1] == ("assistant", "noted [mark-one]")


# ── every way a kept chat comes back keeps its whole record ────────────────────────────────────

#: A kept chat's record as a save writes it: each field a save rebuilds from the chat it holds.
_RECORD: dict[str, Any] = {
    "title": "Garden plans",
    "agent": AGENT,
    "reasoning_effort": "high",
    "mode": "focus",
    "project_id": "proj-garden",
    "task_mode": "plan",
    "folder_id": "folder-home",
    "pinned": True,
    "color_index": 3,
    "color_theme": "sage",
    "natural_voice": "on",
    "tags": ["home", "spring"],
    "lifecycle": "archived",
    "last_activity_at": 1_791_067_183.0,
    "never_archive": True,
    "forked_from": {"key": "chat-1-1791067000", "at": 2},
    "side": {
        "open": False,
        "messages": [{"role": "user", "content": "a side note"}],
        "last_run_id": "",
        "is_complete": True,
    },
}


def _keep_a_chat(state: DashboardState, name: str, workspace: Path) -> None:
    """A chat kept on disk as an earlier run of the gateway saved it."""
    lines = [
        {
            "_type": "metadata",
            "created_at": "2026-09-01T10:00:00+00:00",
            "memory_mode": "persistent",
            "tab_id": "tab-garden",
            "workspace_dir": str(workspace),
            **_RECORD,
        },
        {"role": "user", "content": "Which beds get sun?", "ts": "2026-09-01T10:00:00+00:00"},
        {
            "role": "assistant",
            "content": "The two by the fence.",
            "ts": "2026-09-01T10:00:01+00:00",
        },
    ]
    path = state.conversation_log._path(persisted_history_key(state.conversation_log, name))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")


async def _brought_back(state: DashboardState, name: str, way_back: str) -> Any:
    if way_back == "restored at the start":
        restore_recent_sessions(state, 0)
        return state._sessions[name]
    if way_back == "opened from the list":
        return _rehydrate_session_from_history(state, name)
    if way_back == "a worker that names its session":
        return state.get_or_create_session(name=name, agent="another-agent", app="loop")
    client = TestClient(TestServer(_make_app_with_agent_routes(state)))
    await client.start_server()
    try:
        if way_back == "resumed":
            resp = await client.post(f"/api/chat/sessions/{name}/resume", json={})
        else:
            resp = await client.post("/api/chat/sessions", json={"name": name})
        assert resp.status == 200, await resp.text()
    finally:
        await client.close()
    return state._sessions[name]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "way_back",
    [
        "restored at the start",
        "opened from the list",
        "resumed",
        "opened by the create route",
        "a worker that names its session",
    ],
)
async def test_every_way_a_kept_chat_comes_back_keeps_its_whole_record(tmp_path, way_back):
    """🔴 Red before, for every way but the start's restore: each way back restored its own subset
    of the record (opening a chat from the list left out its colour theme and its voice choice,
    resume its tags, lifecycle, project, task mode and reasoning effort, a worker everything),
    and the chat's next save rebuilt the record from what it held, so each left-out field was
    dropped."""
    state = _make_state(tmp_path)
    name = "chat-7-1791067183"
    _keep_a_chat(state, name, tmp_path)

    chat = await _brought_back(state, name, way_back)
    assert chat is not None
    assert [m["content"] for m in chat.messages] == ["Which beds get sun?", "The two by the fence."]
    save_session_to_history(state, chat, force=True)

    log = state.conversation_log
    meta = log.get_metadata(persisted_history_key(log, name))
    dropped = {field: meta.get(field) for field, kept in _RECORD.items() if meta.get(field) != kept}
    assert not dropped, f"the save dropped or changed {sorted(dropped)}: {dropped}"
    assert meta.get("tab_id") == "tab-garden"
    assert meta.get("workspace_dir") == str(tmp_path)
    assert meta.get("message_count") == 2

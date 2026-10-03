"""Every folder's own memory is in reach: listed, recalled by the work done for it, removable.

A chat working in a folder of its own keeps what it learns in that folder's memory partition and
reads it first. What these tests pin could not reach it:

* Settings → Memory, the vault, ``memory_list`` and ``memory_forget`` read and changed the global
  memory alone, so a folder's facts, lessons and episodes could be neither seen nor removed, and a
  partition whose folder was gone could not be removed at all;
* a subagent's first prompt, a workflow step's and a subagent's ``memory_recall`` read the global
  memory, not the memory of the chat the work was for;
* a project's chats kept their memory in the folder the project binds, while its runs' steps worked
  in, and were documented to keep their memory in, the project's context folder: two memories for
  one project.

Asserted against the real stores, routes, consolidation, assembly and start-up passes, over the
test's own home.
"""

from __future__ import annotations

import contextlib
import json
import shutil
from collections.abc import AsyncIterator
from functools import partial
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw import memory_locality, session_restrictions
from personalclaw.context import ContextBuilder
from personalclaw.dashboard import chat, handlers
from personalclaw.dashboard.chat_persistence import save_session_to_history
from personalclaw.dashboard.chat_utils import _history_key_for
from personalclaw.dashboard.state import DashboardState
from personalclaw.history import ConversationLog, HistoryConsolidator
from personalclaw.home_paths import from_home
from personalclaw.memory import MemoryStore
from personalclaw.memory_reads import reach_of
from personalclaw.memory_service import service_for
from personalclaw.skills import SkillsLoader
from personalclaw.vector_memory import VectorMemoryStore

#: What the consolidation model keeps from a chat about the garden notes. Invented content.
GARDEN = {
    "history_entry": "Planned the spring beds from the garden notes; seed order moves to March.",
    "episodic": [
        {
            "text": "The seed order for the raised beds goes out in the first week of March.",
            "tags": [],
        }
    ],
    "semantic": [
        {
            "key": "user.project.garden_beds",
            "value": "four raised beds by the fence",
            "confidence": 0.9,
        }
    ],
}
#: What it keeps from a chat about dinner, which works in no folder: the global memory.
DINNER = {
    "history_entry": "Planned Saturday's dinner: lentil soup with flatbread for six.",
    "episodic": [
        {"text": "Saturday's dinner is lentil soup with flatbread for six guests.", "tags": []}
    ],
}
GARDEN_EPISODE = GARDEN["episodic"][0]["text"]
GARDEN_FACT_KEY = "user.project.garden_beds"
DINNER_EPISODE = DINNER["episodic"][0]["text"]
#: A lesson a chat working in the garden notes taught, kept in that folder's memory.
GARDEN_LESSON = "Water the seedlings before noon, never in the evening."
#: A lesson taught for every chat, kept in the global memory.
EVERY_CHAT_LESSON = "Water reminders go out as one message a day."

#: The routes the Memory page and the memory tools reach, as the dashboard registers them.
ROUTES = (
    ("GET", "/api/memory/partitions", "api_memory_partitions"),
    ("DELETE", "/api/memory/partitions/{id}", "api_memory_partition_delete"),
    ("GET", "/api/memory/semantic", "api_memory_semantic"),
    ("DELETE", "/api/memory/semantic/{key:.+}", "api_memory_semantic_delete"),
    ("GET", "/api/memory/episodic", "api_memory_episodic_list"),
    ("DELETE", "/api/memory/episodic/{id}", "api_memory_episodic_delete"),
    ("GET", "/api/memory/recall", "api_memory_recall"),
    ("GET", "/api/lessons", "api_lessons"),
    ("DELETE", "/api/lessons", "api_lessons_delete"),
)


@pytest.fixture
def gw(tmp_path: Path):
    """A gateway's memory wiring over the test's home: the chat state that saves transcripts,
    the global memory (markdown and memory database) the context builder and the consolidator
    share, and a consolidation model that answers each chat with what it keeps from it."""
    garden = tmp_path / "garden-notes"
    garden.mkdir()
    log = ConversationLog()
    log.init()
    main = MemoryStore()
    main.init()
    store = VectorMemoryStore(confidence_threshold=0.0)
    store.init()
    main.vector_store = store
    builder = ContextBuilder(
        memory=main,
        skills=SkillsLoader(skills_path=tmp_path / "skills", install_builtins=False),
    )
    builder.conversation_log = log
    consolidator = HistoryConsolidator(
        log=log, memory=main, vector_store=store, migrated=True, history_idle_secs=0
    )
    answers: dict[str, dict] = {}
    model = AsyncMock(side_effect=lambda _prompt, key: json.loads(json.dumps(answers.get(key, {}))))
    consolidator._call_llm = model  # type: ignore[method-assign]
    sessions = MagicMock(count=0)
    sessions.remove = AsyncMock()
    state = DashboardState(
        sessions=sessions,
        start_time=0.0,
        context_builder=builder,
        conversation_log=log,
        consolidator=consolidator,
    )
    app = web.Application()
    for method, path, name in ROUTES:
        # A route a version has no handler for is left out, so asking it answers 404 as the
        # gateway would.
        if (handler := getattr(handlers, name, None)) is not None:
            app.router.add_route(method, path, handler)
    app.router.add_post("/api/chat/sessions", chat.api_chat_session_create)
    app["state"] = state
    yield SimpleNamespace(
        garden=str(garden),
        log=log,
        main=main,
        store=store,
        builder=builder,
        consolidator=consolidator,
        answers=answers,
        state=state,
        app=app,
    )
    store.close()


@pytest.fixture(autouse=True)
def _unmarked():
    """The registry is process-wide: no mark one test makes reaches another."""
    yield
    session_restrictions.clear("subagent:b1c2d3e4")


def _chat(gw: SimpleNamespace, folder: str, answer: dict | None = None) -> tuple[str, object]:
    """A chat working in *folder*, saved as the dashboard saves a running chat, whose
    consolidation the model answers with *answer*. Returns ``(history key, session)``."""
    session = gw.state.get_or_create_session(name=None, workspace_dir=folder)
    session.append("user", "Here is what I worked out today.", broadcast=False)
    session.append("assistant", "Noted, I will keep it in mind.", broadcast=False)
    session.drain()
    save_session_to_history(gw.state, session, force=True)
    key = _history_key_for(session.key)
    gw.answers[key] = answer or {}
    return key, session


async def _remembered(gw: SimpleNamespace) -> None:
    """What a chat working in the garden notes kept (its episode, its fact, its summary, and a
    lesson it was taught), and what a chat in no folder kept, each consolidated as it would be."""
    garden, _ = _chat(gw, gw.garden, GARDEN)
    dinner, _ = _chat(gw, "", DINNER)
    assert await gw.consolidator.consolidate_session(garden)
    assert await gw.consolidator.consolidate_session(dinner)
    folder = ContextBuilder.get_memory_for(gw.garden, writes=True)
    assert service_for(folder).write_lesson(GARDEN_LESSON, "preference", None)
    assert service_for(gw.main).write_lesson(EVERY_CHAT_LESSON, "preference", None)


@contextlib.asynccontextmanager
async def _client(gw: SimpleNamespace) -> AsyncIterator[TestClient]:
    async with TestClient(TestServer(gw.app)) as client:
        yield client


async def _partitions(client: TestClient) -> list[dict]:
    """Every memory Settings → Memory lists (``GET /api/memory/partitions``)."""
    resp = await client.get("/api/memory/partitions")
    assert resp.status == 200, f"nothing lists the memories she has: {resp.status}"
    return (await resp.json())["partitions"]


def _episodes(store: VectorMemoryStore) -> list[str]:
    rows = store.db.execute("SELECT text FROM episodic_memories WHERE is_deleted = 0").fetchall()
    return [str(r["text"]) for r in rows]


def _semantic(store: VectorMemoryStore) -> dict[str, str]:
    rows = store.db.execute("SELECT key, value_json FROM semantic_memory WHERE is_deleted = 0")
    return {str(r["key"]): str(r["value_json"]) for r in rows.fetchall()}


# ── Settings → Memory ─────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_settings_lists_a_folders_memory_and_forgets_from_it(gw) -> None:
    """🔴 Red on integration: no route listed a folder's memory, and every list and delete the
    Memory page makes read and changed the global memory alone."""
    await _remembered(gw)
    garden_part = memory_locality.partition_for(gw.garden)

    async with _client(gw) as client:
        rows = await _partitions(client)
        assert rows[0]["global"] is True, rows
        mine = next(r for r in rows if r["id"] == garden_part.name)
        assert mine["path"] == str(Path(gw.garden).resolve())
        assert mine["folder"] == from_home(mine["path"])
        assert mine["gone"] is False
        assert mine["semantic"] >= 2 and mine["episodic"] >= 1, mine
        pid = mine["id"]

        facts = (await (await client.get(f"/api/memory/semantic?partition={pid}")).json())[
            "entries"
        ]
        assert GARDEN_FACT_KEY in {f["key"] for f in facts}
        shared = (await (await client.get("/api/memory/semantic")).json())["entries"]
        assert GARDEN_FACT_KEY not in {f["key"] for f in shared}
        episodes = (await (await client.get(f"/api/memory/episodic?partition={pid}")).json())[
            "entries"
        ]
        garden_episode = next(e for e in episodes if e["text"] == GARDEN_EPISODE)
        lessons = (await (await client.get(f"/api/lessons?partition={pid}")).json())["lessons"]
        assert [le["rule"] for le in lessons] == [GARDEN_LESSON], lessons
        assert lessons[0]["partition"] == pid and lessons[0]["folder"] == mine["folder"]

        # Forgotten from that folder's memory, record by record.
        gone = await client.delete(f"/api/memory/semantic/{GARDEN_FACT_KEY}?partition={pid}")
        assert gone.status == 200, await gone.text()
        gone = await client.delete(f"/api/memory/episodic/{garden_episode['id']}?partition={pid}")
        assert gone.status == 200, await gone.text()
        gone = await client.delete(f"/api/lessons?partition={pid}", json={"rule": GARDEN_LESSON})
        assert (await gone.json())["removed"] == [
            {"partition": pid, "folder": mine["folder"], "folder_gone": False}
        ]

        facts = (await (await client.get(f"/api/memory/semantic?partition={pid}")).json())[
            "entries"
        ]
        assert GARDEN_FACT_KEY not in {f["key"] for f in facts}
        episodes = (await (await client.get(f"/api/memory/episodic?partition={pid}")).json())[
            "entries"
        ]
        assert GARDEN_EPISODE not in {e["text"] for e in episodes}
        assert (await (await client.get(f"/api/lessons?partition={pid}")).json())["lessons"] == []

        # The global memory is as it was.
        assert DINNER_EPISODE in _episodes(gw.store)
        shared_lessons = (await (await client.get("/api/lessons")).json())["lessons"]
        assert [le["rule"] for le in shared_lessons] == [EVERY_CHAT_LESSON]

        # An id that names no memory is refused, never read as the global memory.
        missing = await client.get("/api/memory/semantic?partition=no-such-folder")
        assert missing.status == 404
        assert (await missing.json())["error"]["code"] == "memory_partition_not_found"


@pytest.mark.asyncio
async def test_a_memory_whose_folder_is_gone_is_listed_so_and_can_be_removed(gw, tmp_path) -> None:
    """🔴 Red on integration: nothing listed it, and nothing but a folder of PersonalClaw's own
    being removed could remove it."""
    old = tmp_path / "old-recipes"
    old.mkdir()
    key, _ = _chat(gw, str(old), GARDEN)
    assert await gw.consolidator.consolidate_session(key)
    part = memory_locality.partition_for(str(old))
    assert part.is_dir()
    shutil.rmtree(old)

    async with _client(gw) as client:
        row = next(r for r in await _partitions(client) if r["id"] == part.name)
        assert row["gone"] is True and row["episodic"] >= 1, row

        removed = await client.delete(f"/api/memory/partitions/{part.name}")
        assert removed.status == 200, await removed.text()
        assert not part.exists()
        assert part.name not in {r["id"] for r in await _partitions(client)}

        # The global memory has no id of its own to be removed by.
        shared_id = memory_locality.partition_for(None).name
        assert (await client.delete(f"/api/memory/partitions/{shared_id}")).status == 404


def test_a_partition_is_named_by_the_folder_it_records_and_only_its_own(tmp_path) -> None:
    """A partition names its folder by the record it keeps; a record copied into another partition,
    or edited to name another folder, names nothing."""
    garden = tmp_path / "named-garden"
    garden.mkdir()
    ContextBuilder.get_memory_for(str(garden), writes=True)
    part = memory_locality.partition_for(str(garden))
    listed = {p.id: p for p in memory_locality.partitions()}
    assert listed[part.name].folder == str(garden.resolve())

    other = part.parent / "Users_someone_else"
    other.mkdir()
    shutil.copy(part / memory_locality.FOLDER_RECORD, other / memory_locality.FOLDER_RECORD)
    assert memory_locality.recorded_folder(other) == ""
    assert memory_locality.partition_named("../escape") is None
    assert memory_locality.partition_named(part.name).folder == str(garden.resolve())


# ── memory_list and memory_forget ─────────────────────────────────────────────────────────────


@contextlib.asynccontextmanager
async def _gateway(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[tuple[Path, DashboardState]]:
    """The real dashboard gateway over a scratch home, as the memory tools reach it, and its
    dashboard state."""
    home = tmp_path / "data"
    user_home = tmp_path / "user"
    user_home.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    monkeypatch.setenv("HOME", str(user_home))
    for name in (
        "PERSONALCLAW_AUTH_MODE",
        "PERSONALCLAW_BYPASS_LOCAL_NETWORKS",
        "PERSONALCLAW_DEV_NO_AUTH",
        "PERSONALCLAW_SESSION_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
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
    monkeypatch.setenv("PERSONALCLAW_PORT", str(runner.addresses[0][1]))
    try:
        yield home, state
    finally:
        await runner.cleanup()


async def _tool(tool: str, arguments: dict, *, asked_from: str) -> str:
    from personalclaw import mcp_core
    from personalclaw.tool_providers.registry import create_memory_provider

    token = mcp_core.set_current_session_key(asked_from)
    try:
        result = await create_memory_provider().invoke(tool, arguments)
    finally:
        mcp_core.reset_current_session_key(token)
    assert result.success, result.error
    return str(result.output)


@pytest.mark.asyncio
async def test_memory_list_and_memory_forget_reach_every_folders_memory(
    tmp_path, monkeypatch
) -> None:
    """🔴 Red on integration: both tools read and changed the global memory's lessons alone."""
    async with _gateway(tmp_path, monkeypatch) as (home, state):
        garden = tmp_path / "garden-notes"
        garden.mkdir()
        shared = VectorMemoryStore(db_path=home / "memory.db")
        shared.init()
        assert shared.write_lesson(EVERY_CHAT_LESSON, source="user_explicit")
        shared.close()
        folder = ContextBuilder.get_memory_for(str(garden), writes=True)
        assert service_for(folder).write_lesson(GARDEN_LESSON, "preference", None)
        shown = from_home(str(garden.resolve()))

        # Asked from a chat of yours that works in no folder.
        asker = state.get_or_create_session(name=None).key
        listed = await _tool("memory_list", {}, asked_from=asker)
        lines = listed.splitlines()
        assert f"[knowledge] {EVERY_CHAT_LESSON}" in lines, listed
        assert f"[knowledge] {GARDEN_LESSON} (kept in the memory of {shown})" in lines, listed

        forgot = await _tool("memory_forget", {"query": "water"}, asked_from=asker)
        assert forgot == (
            "Removed lessons matching: water (from the memory every chat keeps, "
            f"the memory of {shown})"
        ), forgot
        assert await _tool("memory_list", {}, asked_from=asker) == "No lessons saved."

        # A Temporary chat's work still reads nothing, from any folder's memory either.
        session_restrictions.mark_temporary("dashboard:chat-42-1790500042")
        try:
            withheld = await _tool("memory_list", {}, asked_from="dashboard:chat-42-1790500042")
        finally:
            session_restrictions.clear("dashboard:chat-42-1790500042")
        assert withheld.startswith("This is a Temporary chat"), withheld


# ── the command line ──────────────────────────────────────────────────────────────────────────


def test_the_command_line_lists_every_memory_and_reaches_a_folders(tmp_path, capsys) -> None:
    """🔴 Red on integration: ``personalclaw memory`` and ``personalclaw learn`` read and changed
    the global memory alone, so a folder's facts and lessons could be neither listed, exported
    nor forgotten from the command line."""
    import argparse

    from personalclaw.cli_commands import _learn, _memory_cmd

    garden = tmp_path / "garden-notes"
    garden.mkdir()
    folder = ContextBuilder.get_memory_for(str(garden), writes=True)
    assert service_for(folder).write_lesson(GARDEN_LESSON, "preference", None)
    assert folder.vector_store is not None
    folder.vector_store._graph_enabled = False
    assert (
        folder.vector_store.set_semantic(GARDEN_FACT_KEY, "four raised beds", 1.0, "user") is None
    )
    shared = VectorMemoryStore()
    shared.init()
    assert shared.write_lesson(EVERY_CHAT_LESSON, source="user_explicit")
    shared.close()
    pid = memory_locality.partition_for(str(garden)).name
    shown = from_home(str(garden.resolve()))

    _memory_cmd(argparse.Namespace(mem_action="partitions"))
    listed = capsys.readouterr().out
    assert pid in listed and f"the memory of {shown}:" in listed, listed

    _memory_cmd(argparse.Namespace(mem_action="list", partition=pid))
    assert GARDEN_FACT_KEY in capsys.readouterr().out
    exported = tmp_path / "garden-memory.json"
    _memory_cmd(argparse.Namespace(mem_action="export", partition=pid, output=str(exported)))
    capsys.readouterr()
    rows = json.loads(exported.read_text(encoding="utf-8"))["semantic"]
    assert GARDEN_FACT_KEY in {r["key"] for r in rows}
    _memory_cmd(argparse.Namespace(mem_action="list", partition=""))
    assert GARDEN_FACT_KEY not in capsys.readouterr().out

    _learn(argparse.Namespace(learn_action="list"))
    lines = capsys.readouterr().out.splitlines()
    assert f"  [knowledge] {EVERY_CHAT_LESSON}" in lines, lines
    assert f"  [knowledge] {GARDEN_LESSON} (kept in the memory of {shown})" in lines, lines
    _learn(argparse.Namespace(learn_action="remove", query="water"))
    assert capsys.readouterr().out.strip() == (
        f"Removed lessons matching: water (from the memory every chat keeps, the memory of {shown})"
    )


# ── work done for a folder chat ───────────────────────────────────────────────────────────────


def _works_for(gw: SimpleNamespace, parent: str) -> str:
    """A subagent working for the session *parent*, as the gateway tracks one."""
    info = SimpleNamespace(parent_session_key=parent, app="")
    gw.state.subagents = SimpleNamespace(get=lambda agent_id: info, count=0)
    return "subagent:b1c2d3e4"


async def _first_prompt(gw: SimpleNamespace, parent: str, task: str) -> str:
    """The first prompt a subagent working for *parent* is handed, as its run builds it."""
    from personalclaw.subagent import SubagentInfo
    from personalclaw.subagent_prompt import first_prompt

    key = _works_for(gw, parent)
    info = SubagentInfo(id="b1c2d3e4", task=task, parent_session_key=parent)
    with patch("personalclaw.context_headroom.resolve_window", new=AsyncMock(return_value=None)):
        return await first_prompt(
            info,
            named_agent="",
            assemble=partial(gw.builder.build_message, agent=""),
            client=MagicMock(),
            is_new=True,
            session_key=key,
            reach=partial(reach_of, gw.state),
            folder_of=partial(memory_locality.work_folder, gw.state),
        )


#: A subagent's task. Its memory is searched by the task, not by the instructions it is put under.
ASKED = (
    "Seed order and dinner plans: when does the seed order for the raised beds go out, "
    "and what is Saturday's dinner?"
)


@pytest.mark.asyncio
async def test_a_subagent_of_a_folder_chat_reads_the_folders_memory_first(gw) -> None:
    """🔴 Red on integration: its first prompt was assembled from the global memory alone."""
    await _remembered(gw)
    _, asker = _chat(gw, gw.garden)

    prompt = await _first_prompt(gw, asker.key, ASKED)

    assert GARDEN_EPISODE in prompt, prompt
    assert DINNER_EPISODE in prompt, prompt
    # The folder's memory first; then the global memory's, labeled as from outside the folder's.
    label = prompt.index("[CROSS-PARTITION RECALL")
    assert prompt.index(GARDEN_EPISODE) < label < prompt.index(DINNER_EPISODE)
    # A subagent of a chat in no folder reads the global memory, and the folder's not at all.
    _, elsewhere = _chat(gw, "")
    shared = await _first_prompt(gw, elsewhere.key, ASKED)
    assert DINNER_EPISODE in shared and GARDEN_EPISODE not in shared


@pytest.mark.asyncio
async def test_a_subagents_memory_recall_reads_its_chats_folder_first(gw) -> None:
    """🔴 Red on integration: a subagent's ``memory_recall`` read the global memory alone."""
    await _remembered(gw)
    _, asker = _chat(gw, gw.garden)
    agent = _works_for(gw, asker.key)

    async with _client(gw) as client:
        resp = await client.get(
            "/api/memory/recall",
            params={"q": "seed order raised beds lentil soup dinner"},
            headers={"X-Session-Key": agent},
        )
        recalled = (await resp.json())["result"]

    assert GARDEN_EPISODE in recalled, recalled
    assert DINNER_EPISODE in recalled, recalled
    assert recalled.index(GARDEN_EPISODE) < recalled.index(DINNER_EPISODE)


@pytest.mark.asyncio
async def test_a_workflow_step_reads_the_memory_of_the_chat_that_started_its_run(gw) -> None:
    """🔴 Red on integration: a step's agent read the global memory, whoever started its run."""
    from personalclaw.workflows import ownership, store
    from personalclaw.workflows.models import OriginKind, RunOrigin, WorkflowRun

    await _remembered(gw)
    _, asker = _chat(gw, gw.garden)
    run = store.create(
        WorkflowRun(
            id=store.new_run_id(),
            workflow_name="plan-the-beds",
            origin=RunOrigin(kind=OriginKind.CHAT, session_key=asker.key),
        )
    )
    step = ownership.owned_key(run.id, "draft")

    assert memory_locality.work_folder(gw.state, reach_of(gw.state, step)) == gw.garden
    prompt = await _first_prompt(gw, step, ASKED)
    assert GARDEN_EPISODE in prompt and DINNER_EPISODE in prompt
    assert prompt.index(GARDEN_EPISODE) < prompt.index(DINNER_EPISODE)


# ── one project, one memory ───────────────────────────────────────────────────────────────────


async def _project_chat(gw: SimpleNamespace, project_id: str) -> object:
    """A chat started in project *project_id*, as the chat page starts one."""
    async with _client(gw) as client:
        resp = await client.post("/api/chat/sessions", json={"project_id": project_id})
        assert resp.status == 200, await resp.text()
        name = str((await resp.json())["key"]).split(":", 1)[-1]
    return gw.state._sessions[name]


def _run_of(project_id: str) -> str:
    """A step of a run of project *project_id*, started from no chat (the project's own page)."""
    from personalclaw.workflows import ownership, store
    from personalclaw.workflows.models import OriginKind, RunOrigin, WorkflowRun

    run = store.create(
        WorkflowRun(
            id=store.new_run_id(),
            workflow_name="tidy-the-plan",
            project_id=project_id,
            origin=RunOrigin(kind=OriginKind.API, session_key=""),
        )
    )
    return ownership.owned_key(run.id, "tidy")


@pytest.mark.asyncio
async def test_a_projects_chat_and_its_run_share_one_memory(gw) -> None:
    """🔴 Red on integration: the project's chat kept its memory in the folder the project binds,
    and its run's steps were bound to the project's context folder for their memory."""
    from personalclaw import projects
    from personalclaw.tasks.hierarchy import HierarchyStore

    bound = HierarchyStore().create_project("Spring beds", workspace_dir=gw.garden)
    chat = await _project_chat(gw, bound.id)
    step = _run_of(bound.id)

    chats = memory_locality.partition_for(memory_locality.chat_folder(chat) or None)
    runs = memory_locality.partition_for(
        memory_locality.work_folder(gw.state, reach_of(gw.state, step)) or None
    )
    assert chats == runs == memory_locality.partition_for(gw.garden)
    assert runs != memory_locality.partition_for(projects.context_dir(bound.id))

    # What the project's chat keeps is what its run's steps read.
    key = _history_key_for(chat.key)
    chat.append("user", "Here is the plan for the beds.", broadcast=False)
    chat.append("assistant", "Noted.", broadcast=False)
    chat.drain()
    save_session_to_history(gw.state, chat, force=True)
    gw.answers[key] = GARDEN
    assert await gw.consolidator.consolidate_session(key)
    prompt = await _first_prompt(gw, step, ASKED)
    assert GARDEN_EPISODE in prompt, prompt

    # A project that binds no folder: its chats keep the global memory, and its runs read it.
    loose = HierarchyStore().create_project("Kitchen shelves")
    loose_chat = await _project_chat(gw, loose.id)
    loose_step = _run_of(loose.id)
    assert not memory_locality.is_local_partition(memory_locality.chat_folder(loose_chat))
    assert memory_locality.work_folder(gw.state, reach_of(gw.state, loose_step)) == ""


@pytest.mark.asyncio
async def test_a_projects_routed_context_reads_its_memory_first(gw) -> None:
    """🔴 Red on integration: ``get_context`` recalled from the global memory alone, whichever
    project's context it routed, so what the project's chats kept was not in it."""
    from personalclaw.dashboard.handlers.context import _route_for_project
    from personalclaw.tasks.hierarchy import HierarchyStore

    await _remembered(gw)
    bound = HierarchyStore().create_project("Spring beds", workspace_dir=gw.garden)

    routed = _route_for_project(gw.state, bound, "seed order raised beds lentil soup dinner")

    texts = [m["text"] for m in routed.memories]
    assert GARDEN_EPISODE in texts and DINNER_EPISODE in texts, texts
    assert texts.index(GARDEN_EPISODE) < texts.index(DINNER_EPISODE)
    dinner = next(m for m in routed.memories if m["text"] == DINNER_EPISODE)
    assert memory_locality.CROSS_PARTITION_SOURCE in dinner["source"]
    # A project that binds no folder keeps the global memory, and its context reads that alone.
    loose = HierarchyStore().create_project("Kitchen shelves")
    texts = [m["text"] for m in _route_for_project(gw.state, loose, "seed order dinner").memories]
    assert GARDEN_EPISODE not in texts and DINNER_EPISODE in texts, texts


def _context_partition_keeps(project_id: str) -> VectorMemoryStore:
    """What an earlier version could leave in project *project_id*'s context folder's memory: an
    episode, a fact, a lesson with its evidence, a preference line and a day's history entry."""
    from personalclaw import projects

    memory = ContextBuilder.get_memory_for(projects.context_dir(project_id), writes=True)
    vs = memory.vector_store
    assert vs is not None
    vs._graph_enabled = False
    assert vs.write_episodic(f"Mulched the beds for {project_id}.", conversation_id="run-1")
    assert (
        vs.set_semantic(f"user.project.mulch_{project_id[-4:]}", "bark mulch", 1.0, "user") is None
    )
    assert vs.write_lesson(f"Mulch {project_id} after rain, not before.", source="user_explicit")
    memory.add_preference(f"Plans for {project_id} as a checklist")
    memory.append_history(f"Mulched the beds of {project_id}.")
    return vs


def _held(memory: MemoryStore) -> tuple:
    vs = memory.vector_store
    assert vs is not None
    return (
        sorted(_episodes(vs)),
        sorted(_semantic(vs)),
        memory.read_preferences(),
        sorted(p.read_text(encoding="utf-8") for p in memory._history_dir.glob("*.md")),
    )


@pytest.mark.asyncio
async def test_what_a_projects_context_folder_kept_moves_to_its_project_once(gw) -> None:
    """🔴 Red on integration: no pass moved it, so it stayed where neither the project's chats nor
    its runs read it. The move is idempotent: a second start finds nothing to move."""
    from personalclaw import projects
    from personalclaw.tasks.hierarchy import HierarchyStore

    bound = HierarchyStore().create_project("Spring beds", workspace_dir=gw.garden)
    loose = HierarchyStore().create_project("Kitchen shelves")
    _context_partition_keeps(bound.id)
    _context_partition_keeps(loose.id)
    bound_ctx = memory_locality.partition_for(projects.context_dir(bound.id))
    loose_ctx = memory_locality.partition_for(projects.context_dir(loose.id))
    assert bound_ctx.is_dir() and loose_ctx.is_dir()

    memory_locality.settle_at_start(gw.store, gw.log)

    assert not bound_ctx.exists() and not loose_ctx.exists()
    garden = ContextBuilder.get_memory_for(gw.garden, writes=True)
    episodes, keys, prefs, history = _held(garden)
    assert f"Mulched the beds for {bound.id}." in episodes
    assert f"user.project.mulch_{bound.id[-4:]}" in keys
    lesson_keys = [k for k in keys if k.startswith("lesson.")]
    assert len(lesson_keys) == 1
    # The lesson moved with the evidence it stands on, so it is not held below the gate.
    standing = service_for(garden).lesson_standings(service_for(garden).get_lessons())
    assert standing[lesson_keys[0]].evidence.observations >= 1
    assert f"Plans for {bound.id} as a checklist" in prefs
    assert any(f"Mulched the beds of {bound.id}." in day for day in history)
    # A project that binds no folder keeps the global memory, so its context folder's went there.
    assert f"Mulched the beds for {loose.id}." in _episodes(gw.store)
    assert f"Plans for {loose.id} as a checklist" in gw.main.read_preferences()

    before = (_held(garden), sorted(_episodes(gw.store)), sorted(_semantic(gw.store)))
    assert memory_locality.move_what_context_folders_kept(gw.log) == 0
    memory_locality.settle_at_start(gw.store, gw.log)
    after = (_held(garden), sorted(_episodes(gw.store)), sorted(_semantic(gw.store)))
    assert after == before


def test_a_partition_an_earlier_version_left_unnamed_is_named_once(gw, tmp_path) -> None:
    """🔴 Red on integration: a partition kept no record of its folder, so a list of partitions
    had nothing true to call it by."""
    taxes = tmp_path / "tax-papers"
    taxes.mkdir()
    _chat(gw, str(taxes))
    ContextBuilder.get_memory_for(str(taxes), writes=True)
    part = memory_locality.partition_for(str(taxes))
    (part / memory_locality.FOLDER_RECORD).unlink()
    memory_locality._NAMED.discard(str(part))
    assert memory_locality.partition_named(part.name).folder == ""

    assert memory_locality.name_partitions(gw.log) == 1
    assert memory_locality.partition_named(part.name).folder == str(taxes.resolve())
    assert memory_locality.name_partitions(gw.log) == 0


# ── the vault ─────────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_vault_mirrors_each_folders_memory_as_its_own(gw, tmp_path) -> None:
    """🔴 Red on integration: the vault projected the global memory alone. Each folder's memory is
    mirrored as a vault of its own under ``folders/<its id>/``, linked from the vault's index."""
    from personalclaw.memory_vault import MemoryVault

    await _remembered(gw)
    pid = memory_locality.partition_for(gw.garden).name
    shown = from_home(str(Path(gw.garden).resolve()))
    vault_dir = tmp_path / "vault"
    vault = MemoryVault(service_for(gw.main), vault_dir, mode="mirror")

    summary = vault.sync()

    folder_vault = vault_dir / "folders" / pid
    assert (folder_vault / "MEMORY.md").is_file(), "the vault mirrors no folder's memory"
    # Private as every folder of the vault is, the one holding the folder vaults too.
    assert (folder_vault.parent.stat().st_mode & 0o777) == 0o700
    index = (folder_vault / "MEMORY.md").read_text(encoding="utf-8")
    assert f"# Memory of {shown}" in index
    assert (folder_vault / "facts" / f"{GARDEN_FACT_KEY}.md").is_file()
    root_index = (vault_dir / "MEMORY.md").read_text(encoding="utf-8")
    assert f"[{shown}](folders/{pid}/MEMORY.md)" in root_index
    assert summary["folders"][pid]["records"] >= 2
    assert not (vault_dir / "facts" / f"{GARDEN_FACT_KEY}.md").exists()
    # The global memory's lint reads its own pages, never a folder vault's as its orphans.
    assert not [f for f in vault.lint_flags() if f[1].startswith("folders/")]

    # A folder's memory removed, its vault goes with it.
    part = memory_locality.partition_named(pid)
    assert part is not None and memory_locality.remove_partition(part)
    vault.sync()
    assert not folder_vault.exists()

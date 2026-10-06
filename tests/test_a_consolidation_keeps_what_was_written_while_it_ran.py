"""A consolidation applies its rewrite of preferences.md and projects.md to the files as they are
when its model answers, and never undoes a change made while it waited.

Measured before this was written: `HistoryConsolidator._consolidate_locked` read both files, awaited
the model (seconds, for a real one), and then wrote the model's rewrite of the copy it had read over
each file. A line the owner saved in the Memory page while the model ran, or one the agent wrote
with its file tools or `memory_remember`, was gone when the pass ended, and the log said nothing.

Now the pass reads each file again once the model has answered, under the lock every writer of the
memory documents holds (`memory.hold_documents`), and applies the rewrite to that text
(`memory.apply_rewrite`): a part of the file the rewrite changes is changed where the file still
holds it as the model read it, at least one untouched line away from every change made since. A
part the owner changed too, or right beside it, keeps the owner's words: the file is left as it is,
this pass's rewrite of it is not applied, and the log says so.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

pytestmark = pytest.mark.asyncio

KEY = "dashboard:trip-planning"

PREFERENCES = (
    "# User Preferences\n"
    "\n"
    "- Prefers tea over coffee.\n"
    "- Works from Lisbon.\n"
    "- Likes short answers.\n"
)
#: The model's rewrite of PREFERENCES: it changes the first preference and nothing else.
REWRITE = PREFERENCES.replace("- Prefers tea over coffee.", "- Prefers green tea over coffee.")

PROJECTS = (
    "# Active Projects\n"
    "\n"
    "- Garden shed: the roof is on, the door is next.\n"
    "- Kitchen: tiles ordered.\n"
    "- Bike: new chain fitted.\n"
)
PROJECTS_REWRITE = PROJECTS.replace("the door is next", "the door is hung")


@pytest.fixture
def memory(tmp_path):
    from personalclaw.memory import MemoryStore

    mem = MemoryStore(workspace=tmp_path / "workspace")
    mem.init()
    mem.write_preferences(PREFERENCES)
    mem.write_projects(PROJECTS)
    return mem


@pytest.fixture
def consolidator(tmp_path, memory):
    from personalclaw.history import ConversationLog, HistoryConsolidator

    log = ConversationLog(base_dir=tmp_path / "sessions")
    log.init()
    log.append(KEY, "user", "I switched to green tea, and the shed door is hung now.")
    log.append(KEY, "assistant", "Noted: green tea, and the shed is nearly done.")
    return HistoryConsolidator(log=log, memory=memory)


async def _consolidate(
    consolidator,
    *,
    preferences: str | None = REWRITE,
    projects: str | None = None,
    meanwhile: Callable[[], Awaitable[None]] | None = None,
) -> None:
    """Run one consolidation of KEY whose model answers *preferences* and *projects*, the rewrites
    it was asked for, after *meanwhile* ran: what was written while it waited."""
    result: dict[str, str] = {"history_entry": "Talked about tea and the garden shed."}
    if preferences is not None:
        result["preferences_update"] = preferences
    if projects is not None:
        result["projects_update"] = projects

    async def answer(_prompt: str, _key: str, **_kw: object) -> dict:
        if meanwhile is not None:
            await meanwhile()
        return result

    with patch.object(consolidator, "_call_llm", side_effect=answer):
        await consolidator._consolidate(KEY, include_history=True)


def _memory_app(mem) -> web.Application:
    from personalclaw.dashboard.handlers.memory import api_memory_preferences, api_memory_projects

    app = web.Application()
    app["state"] = SimpleNamespace(context_builder=SimpleNamespace(memory=mem))
    for which, handler in (
        ("preferences", api_memory_preferences),
        ("projects", api_memory_projects),
    ):
        app.router.add_get(f"/api/memory/{which}", handler)
        app.router.add_put(f"/api/memory/{which}", handler)
    return app


async def _saved_in_the_memory_page(mem, which: str, change: Callable[[str], str]) -> int:
    """The owner opens a memory document in Settings → Memory, changes it, and saves over that
    read. Returns the save's status."""
    async with TestClient(TestServer(_memory_app(mem))) as c:
        read = await (await c.get(f"/api/memory/{which}")).json()
        saved = await c.put(
            f"/api/memory/{which}",
            json={"content": change(read["content"])},
            headers={"If-Match": f'"{read["revision"]}"'},
        )
        return saved.status


def _file_app() -> web.Application:
    from personalclaw.apps.permissions import scoped_to_app
    from personalclaw.dashboard.handlers import api_file_read, api_file_write

    @web.middleware
    async def owner(request, handler):
        with scoped_to_app(""):
            return await handler(request)

    app = web.Application(middlewares=[owner])
    state = MagicMock()
    state._sessions = {}
    app["state"] = state
    app.router.add_get("/api/file-read", api_file_read)
    app.router.add_post("/api/file-write", api_file_write)
    return app


async def _saved_in_the_files_editor(root: Path, path: Path, change: Callable[[str], str]) -> int:
    """The owner opens *path* in the Files editor, changes it, and saves over that read."""
    roots = [("Workspace", str(root))]
    with (
        patch("personalclaw.dashboard.handlers.files._dashboard_roots", return_value=roots),
        patch("personalclaw.file_roots.dashboard_roots", return_value=roots),
        patch("personalclaw.dashboard.handlers.files._sel"),
    ):
        async with TestClient(TestServer(_file_app())) as c:
            read = await c.get("/api/file-read", params={"path": str(path)})
            assert read.status == 200, await read.text()
            saved = await c.post(
                "/api/file-write",
                json={"path": str(path), "content": change(await read.text())},
                headers={"If-Match": read.headers["ETag"]},
            )
            return saved.status


def _preferences_file(mem) -> Path:
    return mem._workspace / "memory" / "preferences.md"


def _add_line(text: str) -> str:
    return text + "- Allergic to peanuts.\n"


# ── an edit made while the model ran survives ────────────────────────────────────────────────


async def test_a_line_added_in_the_memory_page_while_the_model_ran_is_kept(consolidator, memory):
    """🔴 Red before: the pass wrote the model's rewrite of the copy it read before the call, and
    the line the owner saved in the Memory page meanwhile was gone. Now the rewrite is applied to
    the file as it is when the model answers: both changes are in it."""
    saves: list[int] = []

    async def owner_saves() -> None:
        saves.append(await _saved_in_the_memory_page(memory, "preferences", _add_line))

    await _consolidate(consolidator, meanwhile=owner_saves)

    assert saves == [200]
    assert memory.read_preferences() == _add_line(REWRITE)


async def test_a_line_the_agent_edits_while_the_model_ran_is_kept(consolidator, memory, tmp_path):
    """The agent's ``edit_file`` is another writer of the file: its edit made while the model ran
    is kept, beside the rewrite."""
    from personalclaw.agents.native import read_gate
    from personalclaw.agents.native.builtin_tools import NativeBuiltinToolProvider

    read_gate.reset_all()
    tools = NativeBuiltinToolProvider(memory._workspace, session_key="memory-edit")
    edits = []

    async def agent_edits() -> None:
        assert (await tools.invoke("read_file", {"path": "memory/preferences.md"})).success
        edits.append(
            await tools.invoke(
                "edit_file",
                {
                    "path": "memory/preferences.md",
                    "old_str": "- Likes short answers.",
                    "new_str": "- Likes short answers with sources.",
                },
            )
        )

    try:
        await _consolidate(consolidator, meanwhile=agent_edits)
    finally:
        read_gate.reset_all()

    assert edits[0].success, edits[0].error
    assert memory.read_preferences() == REWRITE.replace(
        "- Likes short answers.", "- Likes short answers with sources."
    )


async def test_a_preference_remembered_while_the_model_ran_is_kept(consolidator, memory):
    """``add_preference`` — the sandboxed agent's ``memory_remember``, and the plain-text memory's
    write — appends a line while the model runs: it is kept, beside the rewrite."""

    async def remembered() -> None:
        memory.add_preference("Allergic to peanuts.")

    await _consolidate(consolidator, meanwhile=remembered)

    assert memory.read_preferences() == _add_line(REWRITE)


async def test_a_line_added_in_the_files_editor_while_the_model_ran_is_kept(consolidator, memory):
    saves: list[int] = []

    async def owner_saves() -> None:
        saves.append(
            await _saved_in_the_files_editor(
                memory._workspace, _preferences_file(memory), _add_line
            )
        )

    await _consolidate(consolidator, meanwhile=owner_saves)

    assert saves == [200]
    assert memory.read_preferences() == _add_line(REWRITE)


async def test_projects_edited_in_the_memory_page_while_the_model_ran_keeps_the_edit(
    consolidator, memory
):
    """projects.md the same way: the owner's line and the rewrite's change are both in it."""
    saves: list[int] = []

    def add_project(text: str) -> str:
        return text + "- Bookshelf: wood bought.\n"

    async def owner_saves() -> None:
        saves.append(await _saved_in_the_memory_page(memory, "projects", add_project))

    await _consolidate(
        consolidator, preferences=None, projects=PROJECTS_REWRITE, meanwhile=owner_saves
    )

    assert saves == [200]
    assert memory.read_projects() == add_project(PROJECTS_REWRITE)


async def test_a_line_both_changed_keeps_the_owners_words_and_the_log_says_so(
    consolidator, memory, caplog
):
    """🔴 Red before: the model's rewrite of the line went over the owner's. A line both changed
    cannot be merged: the file keeps the owner's text whole, the rewrite of it is not applied, and
    the gateway log says so."""

    def moved(text: str) -> str:
        return text.replace("- Prefers tea over coffee.", "- Prefers coffee now, black.")

    async def owner_saves() -> None:
        assert await _saved_in_the_memory_page(memory, "preferences", moved) == 200

    with caplog.at_level(logging.WARNING, logger="personalclaw.memory"):
        await _consolidate(consolidator, meanwhile=owner_saves)

    assert memory.read_preferences() == moved(PREFERENCES)
    said = [r.getMessage() for r in caplog.records if r.name == "personalclaw.memory"]
    assert any(
        f"Consolidation of {KEY}" in line
        and "preferences.md" in line
        and "left as it is" in line
        and "not applied" in line
        for line in said
    ), said


async def test_a_change_next_to_the_owners_keeps_the_owners_words(consolidator, memory):
    """A change on the line beside one the owner changed is not merged either: lines next to each
    other often belong together (a project and its notes), so the owner's text stays whole."""

    def edited(text: str) -> str:
        return text.replace("- Works from Lisbon.", "- Works from Porto.")

    async def owner_saves() -> None:
        assert await _saved_in_the_memory_page(memory, "preferences", edited) == 200

    beside = PREFERENCES.replace("- Likes short answers.", "- Likes short, direct answers.")
    await _consolidate(consolidator, preferences=beside, meanwhile=owner_saves)

    assert memory.read_preferences() == edited(PREFERENCES)


async def test_with_no_edit_meanwhile_the_rewrite_is_written_as_the_model_wrote_it(
    consolidator, memory
):
    """The control: nothing changed while the model ran, so its rewrite is the file, exactly."""
    rewrite = "# User Preferences\n\n- Prefers green tea.\n- Works from Lisbon, mornings."
    await _consolidate(consolidator, preferences=rewrite, projects=PROJECTS_REWRITE)

    assert memory.read_preferences() == rewrite
    assert memory.read_projects() == PROJECTS_REWRITE


# ── every writer holds the memory documents' lock ────────────────────────────────────────────


def _holding_the_documents_lock(
    path: Path, *, add: str, hold: float
) -> tuple[threading.Thread, threading.Event]:
    """Another writer: under the memory documents' lock it reads *path*, takes *hold* seconds, and
    writes back what it read with a line added. Returns its thread, and the event it sets once it
    holds the lock and has read."""
    from personalclaw import memory as memory_module

    holding = threading.Event()

    def write() -> None:
        with memory_module.hold_documents():
            text = path.read_text(encoding="utf-8")
            holding.set()
            time.sleep(hold)
            path.write_text(text + f"- {add}\n", encoding="utf-8")

    thread = threading.Thread(target=write)
    thread.start()
    return thread, holding


async def test_a_consolidation_writes_under_the_documents_lock(consolidator, memory):
    """A writer that holds the lock across its read and write is waited for: the pass reads the
    file once that writer is done, so neither undoes the other. A pass that did not take the lock
    would write first, and the writer's copy, read before, would undo the rewrite."""
    started: list[threading.Thread] = []

    async def other_writer() -> None:
        thread, holding = _holding_the_documents_lock(
            _preferences_file(memory), add="Allergic to peanuts.", hold=0.5
        )
        started.append(thread)
        assert holding.wait(10)

    await _consolidate(consolidator, meanwhile=other_writer)
    started[0].join(10)

    assert memory.read_preferences() == _add_line(REWRITE)


async def test_a_memory_page_save_that_meets_another_writer_waits_and_is_not_lost(memory):
    """The Memory page's save takes the lock too. A save sent while another writer holds it waits
    for that writer, then finds the file changed under the page's copy and is refused as stale,
    so nothing either wrote is undone. Without the lock the save landed first, and the other
    writer's copy took the owner's line."""
    async with TestClient(TestServer(_memory_app(memory))) as c:
        read = await (await c.get("/api/memory/preferences")).json()
        thread, holding = _holding_the_documents_lock(
            _preferences_file(memory), add="Allergic to peanuts.", hold=0.5
        )
        assert holding.wait(10)
        saved = await c.put(
            "/api/memory/preferences",
            json={"content": read["content"] + "- Walks to work.\n"},
            headers={"If-Match": f'"{read["revision"]}"'},
        )
        thread.join(10)

    assert saved.status == 409, await saved.text()
    assert memory.read_preferences() == _add_line(PREFERENCES)


async def test_an_editor_save_of_a_memory_document_waits_for_its_lock(memory):
    """The Files editor reaches the memory documents too, and takes the same lock for them."""
    path = _preferences_file(memory)
    roots = [("Workspace", str(memory._workspace))]
    with (
        patch("personalclaw.dashboard.handlers.files._dashboard_roots", return_value=roots),
        patch("personalclaw.file_roots.dashboard_roots", return_value=roots),
        patch("personalclaw.dashboard.handlers.files._sel"),
    ):
        async with TestClient(TestServer(_file_app())) as c:
            read = await c.get("/api/file-read", params={"path": str(path)})
            assert read.status == 200, await read.text()
            thread, holding = _holding_the_documents_lock(
                path, add="Allergic to peanuts.", hold=0.5
            )
            assert holding.wait(10)
            saved = await c.post(
                "/api/file-write",
                json={"path": str(path), "content": await read.text() + "- Walks to work.\n"},
                headers={"If-Match": read.headers["ETag"]},
            )
            thread.join(10)

    assert saved.status == 409, await saved.text()
    assert memory.read_preferences() == _add_line(PREFERENCES)


async def test_the_agents_edit_of_a_memory_document_waits_for_its_lock(memory):
    """The agent's ``edit_file`` writes from a worker thread, so it takes the lock too: an edit
    started while another writer holds it lands after that writer's line, and both stay."""
    from personalclaw.agents.native import read_gate
    from personalclaw.agents.native.builtin_tools import NativeBuiltinToolProvider

    read_gate.reset_all()
    try:
        tools = NativeBuiltinToolProvider(memory._workspace, session_key="memory-lock")
        assert (await tools.invoke("read_file", {"path": "memory/preferences.md"})).success
        thread, holding = _holding_the_documents_lock(
            _preferences_file(memory), add="Allergic to peanuts.", hold=0.5
        )
        assert holding.wait(10)
        edited = await tools.invoke(
            "edit_file",
            {"path": "memory/preferences.md", "old_str": "Works from", "new_str": "Lives in"},
        )
        thread.join(10)
    finally:
        read_gate.reset_all()

    assert edited.success, edited.error
    assert memory.read_preferences() == _add_line(PREFERENCES).replace("Works from", "Lives in")


async def test_a_remembered_preference_waits_for_the_lock(memory):
    """``add_preference`` reads, appends and writes under the lock: a preference remembered while
    another writer holds it is added after that writer's line, and both stay."""
    thread, holding = _holding_the_documents_lock(
        _preferences_file(memory), add="Allergic to peanuts.", hold=0.5
    )
    assert holding.wait(10)
    memory.add_preference("Walks to work.")
    thread.join(10)

    assert memory.read_preferences() == _add_line(PREFERENCES) + "- Walks to work.\n"


# ── the merge rule ───────────────────────────────────────────────────────────────────────────


def _apply(read: str, now: str, rewrite: str) -> str | None:
    from personalclaw.memory import apply_rewrite

    return apply_rewrite(read, now, rewrite)


async def test_the_rewrite_of_a_file_nobody_changed_is_the_file():
    assert _apply(PREFERENCES, PREFERENCES, "anything at all") == "anything at all"


async def test_a_rewrite_that_changes_nothing_leaves_the_owners_changes():
    now = _add_line(PREFERENCES)
    assert _apply(PREFERENCES, now, PREFERENCES) == now


async def test_a_line_the_rewrite_removes_goes_when_the_owner_left_it_alone():
    rewrite = PREFERENCES.replace("- Works from Lisbon.\n", "")
    now = PREFERENCES.replace("- Prefers tea over coffee.", "- Prefers mint tea.")
    assert _apply(PREFERENCES, now, rewrite) is None, "the removed line is next to the owner's"
    far = PREFERENCES.replace("- Likes short answers.\n", "")
    assert _apply(PREFERENCES, now, far) == now.replace("- Likes short answers.\n", "")


async def test_the_same_change_on_both_sides_is_kept_once():
    now = _add_line(PREFERENCES)
    assert _apply(PREFERENCES, now, now) == now


async def test_lines_both_added_at_the_same_place_keep_the_owners():
    """Two additions at one place cannot be ordered: the owner's file stays as it is."""
    now = _add_line(PREFERENCES)
    rewrite = PREFERENCES + "- Reads before bed.\n"
    assert _apply(PREFERENCES, now, rewrite) is None


async def test_a_file_that_appeared_while_the_model_ran_is_the_owners():
    assert _apply("", PREFERENCES, REWRITE) is None

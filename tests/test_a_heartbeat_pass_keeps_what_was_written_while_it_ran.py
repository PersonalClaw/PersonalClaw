"""A heartbeat pass takes out the tasks it finished, and only them, from HEARTBEAT.md as it is then.

Measured before this was written: `heartbeat.run_tasks` read the file, awaited every task's agent
turn (minutes, for a real one), and then wrote the file again from the list it had read before
them: its own header, then one `- ` line for each task it had read and not finished. So a task the
owner saved in the Files editor while a pass ran, or one the agent wrote, was gone when the pass
ended; an edit made meanwhile was put back; and every line the pass had not finished was written
again in its format, not the owner's (their header, a heading, a note, a `* ` marker, CRLF).

The pass, the Files editor's save and the agent's file tools read and write the file under one
lock (`heartbeat.hold_queue`), so none lands inside another's read and write.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

pytestmark = pytest.mark.asyncio


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    import personalclaw.heartbeat as hb
    from personalclaw.agents.native import read_gate

    ws = tmp_path / "workspace"
    ws.mkdir()
    monkeypatch.setattr(hb, "heartbeat_path", lambda: ws / "HEARTBEAT.md")
    hb.ensure_heartbeat_file()
    read_gate.reset_all()
    yield ws
    read_gate.reset_all()


def _queue_file(ws: Path) -> Path:
    return ws / "HEARTBEAT.md"


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


async def _owner_saves(ws: Path, change: Callable[[str], str]) -> int:
    """The owner opens HEARTBEAT.md in the Files editor, changes it, and saves over that read."""
    path = _queue_file(ws)
    roots = [("Workspace", str(ws))]
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


async def test_a_task_the_owner_saves_while_a_pass_runs_is_still_there(workspace):
    """🔴 Red before: the pass wrote the file from what it read before its turns, and the task the
    owner saved meanwhile was gone. Their save is their yes, so it runs on the next pass."""
    import personalclaw.heartbeat as hb

    path = _queue_file(workspace)
    path.write_text(hb._HEADER + "- Say hello\n")
    hb.allow("Say hello")
    saves: list[int] = []

    async def run(task: str, deliver: str) -> str:
        saves.append(await _owner_saves(workspace, lambda text: text + "- Water the plants\n"))
        return "hello"

    done = await hb.run_tasks(run)

    assert saves == [200]
    assert (done.ran, done.done) == (1, 1)
    assert path.read_text() == hb._HEADER + "- Water the plants\n"
    assert hb.allowed("Water the plants"), "the owner typed it, so it runs on the next pass"
    assert not hb.allowed("Say hello"), "a finished task takes its yes with it"


async def test_an_edit_made_while_a_pass_runs_is_not_put_back(workspace):
    """🔴 Red before: a task the pass did not run, edited while it ran, was written back as it
    read before the edit."""
    import personalclaw.heartbeat as hb

    path = _queue_file(workspace)
    path.write_text(hb._HEADER + "- Say hello\n- Check the build\n")
    hb.allow("Say hello")

    async def run(task: str, deliver: str) -> str:
        path.write_text(path.read_text().replace("Check the build", "Check the build and the docs"))
        return "hello"

    await hb.run_tasks(run)

    assert path.read_text() == hb._HEADER + "- Check the build and the docs\n"


async def test_a_task_edited_while_it_runs_keeps_its_edit_and_the_log_says_so(workspace, caplog):
    """🔴 Red before: the finished task's line was dropped, and its edit with it. The pass cannot
    tell a changed task from a new one, so the line stays as it is now, and the log says why."""
    import personalclaw.heartbeat as hb

    path = _queue_file(workspace)
    path.write_text(hb._HEADER + "- Check the deploy\n- Say hello\n")
    hb.allow("Check the deploy")
    hb.allow("Say hello")

    async def run(task: str, deliver: str) -> str:
        if task == "Check the deploy":
            status = await _owner_saves(
                workspace,
                lambda text: text.replace("Check the deploy", "Check the deploy and the database"),
            )
            assert status == 200
        return "done"

    with caplog.at_level(logging.WARNING, logger="personalclaw.heartbeat"):
        done = await hb.run_tasks(run)

    assert (done.ran, done.done) == (2, 2)
    assert path.read_text() == hb._HEADER + "- Check the deploy and the database\n"
    assert hb.allowed("Check the deploy and the database"), "the owner's edit is their new task"
    assert not hb.allowed("Check the deploy")
    said = [r.getMessage() for r in caplog.records if r.name == "personalclaw.heartbeat"]
    assert any(
        "edited or removed while it ran" in line and "Check the deploy" in line for line in said
    ), said


async def test_the_finished_tasks_are_taken_out_and_nothing_else(workspace):
    """The control: what the pass finished is gone — two tasks here — and what it kept (unfinished,
    failed, waiting for the owner, added while it ran) is exactly where it was."""
    import personalclaw.heartbeat as hb

    path = _queue_file(workspace)
    path.write_text(
        hb._HEADER
        + "- Say hello\n"
        + "- Watch the deploy  <!-- deliver:dashboard:s1 -->\n"
        + "- Tidy the downloads folder\n"
        + "- Read the release notes\n"
        + "- Email the team\n"
    )
    for task in ("Say hello", "Watch the deploy", "Tidy the downloads folder", "Email the team"):
        hb.allow(task)

    async def run(task: str, deliver: str) -> str:
        if task == "Watch the deploy":
            return "still rolling out HEARTBEAT_KEEP"
        if task == "Tidy the downloads folder":
            raise RuntimeError("the folder is not mounted")
        if task == "Email the team":
            path.write_text(path.read_text() + "- Book the venue\n")
        return "done"

    done = await hb.run_tasks(run)

    assert (done.ran, done.kept, done.waiting, done.done) == (4, 2, 1, 2)
    assert path.read_text() == (
        hb._HEADER
        + "- Watch the deploy  <!-- deliver:dashboard:s1 -->\n"
        + "- Tidy the downloads folder\n"
        + "- Read the release notes\n"
        + "- Book the venue\n"
    )


async def test_the_lines_a_pass_did_not_finish_keep_their_bytes(workspace):
    """🔴 Red before: one finished task rewrote the whole file in the pass's own format. Now its
    line goes, and every other byte stays: the owner's header, comments, headings, notes, markers,
    spacing, CRLF endings and a last line with no newline."""
    import personalclaw.heartbeat as hb

    path = _queue_file(workspace)
    original = (
        "# What to keep an eye on\r\n"
        "\r\n"
        "<!-- One task per line.\r\n"
        "     Allowed ones run every minute. -->\r\n"
        "Café opens at noon, ask about the order ☕\r\n"
        "* Check the build   \r\n"
        "  - [ ] Say hello\r\n"
        "- Watch the deploy  <!--deliver:dashboard:s1-->\r\n"
        "\r\n"
        "## Later\r\n"
        "- Water the plants"
    ).encode("utf-8")
    path.write_bytes(original)
    hb.allow("Say hello")
    hb.allow("Water the plants")

    async def run(task: str, deliver: str) -> str:
        return "HEARTBEAT_KEEP" if task == "Water the plants" else "hello"

    done = await hb.run_tasks(run)

    assert (done.ran, done.done, done.waiting) == (2, 1, 3)
    assert path.read_bytes() == original.replace("  - [ ] Say hello\r\n".encode("utf-8"), b"")


async def test_a_pass_whose_tasks_all_stay_does_not_write_the_file(workspace):
    """Nothing finished, nothing to take out: the file is not written at all, so a pass that only
    keeps tasks never touches what anyone else wrote."""
    import personalclaw.heartbeat as hb

    path = _queue_file(workspace)
    original = b"* Watch the deploy\n# notes\n"
    path.write_bytes(original)
    hb.allow("Watch the deploy")
    before = path.stat().st_ino

    await hb.run_tasks(lambda task, deliver: _keep())

    assert path.read_bytes() == original
    assert path.stat().st_ino == before, "not replaced"


async def _keep() -> str:
    return "HEARTBEAT_KEEP"


def _holding_the_queue_lock(
    path: Path, *, add: str, hold: float
) -> tuple[threading.Thread, threading.Event]:
    """Another writer in the gateway: under the queue's lock it reads the file, takes *hold*
    seconds, and writes back what it read with a line added. Returns its thread, and the event it
    sets once it holds the lock and has read."""
    import personalclaw.heartbeat as hb

    holding = threading.Event()

    def write() -> None:
        with hb.hold_queue():
            text = path.read_text(encoding="utf-8")
            holding.set()
            time.sleep(hold)
            path.write_text(text + f"- {add}\n", encoding="utf-8")

    thread = threading.Thread(target=write)
    thread.start()
    return thread, holding


async def test_a_pass_takes_out_its_tasks_under_the_queue_lock(workspace):
    """A writer that holds the queue's lock across its read and write is waited for: the pass
    reads the file once that writer is done, so neither undoes the other. A pass that did not take
    the lock would write first, and the writer's copy, read before, would bring the finished task
    back."""
    import personalclaw.heartbeat as hb

    path = _queue_file(workspace)
    path.write_text(hb._HEADER + "- Say hello\n")
    hb.allow("Say hello")
    started: list[threading.Thread] = []

    async def run(task: str, deliver: str) -> str:
        thread, holding = _holding_the_queue_lock(path, add="Water the plants", hold=0.5)
        started.append(thread)
        assert holding.wait(10)
        return "hello"

    await hb.run_tasks(run)
    started[0].join(10)

    assert path.read_text() == hb._HEADER + "- Water the plants\n"


async def test_an_editor_save_that_meets_another_writer_waits_and_is_not_lost(workspace):
    """The Files editor's save of HEARTBEAT.md takes the queue's lock too. A save sent while
    another writer holds it waits for that writer, then finds the file changed under the page's
    copy and is refused as stale, so the page keeps what the owner typed and nothing is undone.
    Without the lock the save landed first and the other writer's copy took the owner's line."""
    path = _queue_file(workspace)
    path.write_text("- Check the build\n")
    roots = [("Workspace", str(workspace))]
    with (
        patch("personalclaw.dashboard.handlers.files._dashboard_roots", return_value=roots),
        patch("personalclaw.file_roots.dashboard_roots", return_value=roots),
        patch("personalclaw.dashboard.handlers.files._sel"),
    ):
        async with TestClient(TestServer(_file_app())) as c:
            read = await c.get("/api/file-read", params={"path": str(path)})
            assert read.status == 200, await read.text()
            thread, holding = _holding_the_queue_lock(path, add="Water the plants", hold=0.5)
            assert holding.wait(10)
            saved = await c.post(
                "/api/file-write",
                json={"path": str(path), "content": await read.text() + "- Say hello\n"},
                headers={"If-Match": read.headers["ETag"]},
            )
            thread.join(10)

    assert saved.status == 409, await saved.text()
    assert path.read_text() == "- Check the build\n- Water the plants\n"


async def test_the_agents_edit_of_the_queue_waits_for_its_lock(workspace):
    """The agent's ``edit_file`` writes HEARTBEAT.md from a worker thread, so it takes the queue's
    lock too: an edit started while another writer holds it lands after that writer's line, and
    both stay. Without the lock the edit wrote first and the other writer's copy undid it."""
    from personalclaw.agents.native.builtin_tools import NativeBuiltinToolProvider

    path = _queue_file(workspace)
    path.write_text("- Check the build\n")
    tools = NativeBuiltinToolProvider(workspace, session_key="heartbeat-lock")
    assert (await tools.invoke("read_file", {"path": "HEARTBEAT.md"})).success

    thread, holding = _holding_the_queue_lock(path, add="Water the plants", hold=0.5)
    assert holding.wait(10)
    edited = await tools.invoke(
        "edit_file",
        {"path": "HEARTBEAT.md", "old_str": "Check the build", "new_str": "Check the docs"},
    )
    thread.join(10)

    assert edited.success, edited.error
    assert path.read_text() == "- Check the docs\n- Water the plants\n"

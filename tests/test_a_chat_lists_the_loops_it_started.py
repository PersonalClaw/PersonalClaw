"""A chat's Background tasks lists the loops and runs it started (#3824's follow-up).

The chat's session inspector lists its own work from ``GET /api/running``, by the chat a row names
(``session``). A loop's row named none, and nor did a workflow run's, so a chat that started a loop
or a run never listed it, though it listed every helper it started.

A loop now records the chat it was made for (``Loop.origin_session``): the chat at the top of the
chain the work that made it is done for (``memory_writes.filed_under``), so a loop a chat's helper
makes is that chat's too, and a loop made on a page, by a schedule or by another loop's worker is
no chat's. A run already records the session that started it (``RunOrigin.session_key``). Each row
names that chat.

Who reads a row is unchanged in rule: the owner reads every row, an agent its own chat's, and an
app the rows its socket would hear the phase of, which for work with a chat is work in a chat the
app started. So an app reads the loops and runs its own chats started, and no other.
"""

from __future__ import annotations

import sqlite3
from typing import Any

import pytest
from test_apps_cannot_run_code_or_bypass_approvals import _home, _install
from test_running_now import (
    APP,
    APPS_CHAT,
    CHAT,
    _as,
    _by_work,
    _everything,
    _read,
)

from personalclaw import memory_writes


def _loop_in(scope: str | None, **over: Any):
    """A goal loop made by work done for *scope* (``None``: by no work, as a page's request)."""
    from test_helpers_loops_and_runs_say_their_phases import _loop

    from personalclaw.loop import store
    from personalclaw.loop.loop import LoopStatus

    if scope is None:
        loop = _loop(**over)
    else:
        with memory_writes.derived_from(scope):
            loop = _loop(**over)
    store.update_status(loop.id, LoopStatus.RUNNING)
    return store.get(loop.id)


def _run_from(session_key: str, title: str):
    from personalclaw.workflows import store as runs
    from personalclaw.workflows.models import RunOrigin, RunStatus, WorkflowRun

    return runs.create(
        WorkflowRun(
            id="",
            workflow_name="digest",
            title=title,
            status=RunStatus.RUNNING,
            started_at="2026-10-22T09:00:00Z",
            origin=RunOrigin(session_key=session_key),
        )
    )


class TestTheRecord:
    def test_a_loop_made_in_a_chats_turn_names_that_chat(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
        loop = _loop_in(f"dashboard:{CHAT}")
        assert loop.origin_session == CHAT

    def test_a_loop_made_on_a_page_or_by_another_loop_names_no_chat(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
        assert _loop_in(None).origin_session == ""
        assert _loop_in("loop-0a1b2c3d").origin_session == ""

    def test_a_loop_stored_before_the_field_names_no_chat(self, tmp_path, monkeypatch):
        from personalclaw.loop import store

        monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
        loop = _loop_in(f"dashboard:{CHAT}")
        db = sqlite3.connect(str(store._db_path()))
        try:
            db.execute("ALTER TABLE loops DROP COLUMN origin_session")
            db.commit()
        finally:
            db.close()
        assert store.get(loop.id).origin_session == ""


@pytest.mark.asyncio
async def test_a_chats_loop_and_run_name_the_chat_they_were_started_from(tmp_path):
    async with _everything(tmp_path) as (w, ids):
        mine = _loop_in(f"dashboard:{CHAT}", name="Watch the soil delivery")
        run = _run_from(f"dashboard:{CHAT}", "Weekly digest")
        rows = _by_work(await _read(w.state))
    assert rows[f"loop:{mine.id}"]["session"] == CHAT
    assert rows[f"workflow:{run.id}"]["session"] == CHAT
    # Control: the loop and run no chat started still name none.
    assert rows[ids["loop"]]["session"] == ""
    assert rows[ids["run"]]["session"] == ""


@pytest.mark.asyncio
async def test_an_agent_reads_its_own_chats_loops_and_runs_and_no_others(tmp_path):
    async with _everything(tmp_path) as (w, ids):
        mine = _loop_in(f"dashboard:{CHAT}")
        theirs = _loop_in(f"dashboard:{APPS_CHAT}")
        run = _run_from(f"dashboard:{CHAT}", "Weekly digest")
        status, listed = await _as(
            w.state,
            "",
            "/api/running",
            **{"X-Internal-Secret": "the gateway's", "X-Session-Key": f"dashboard:{CHAT}"},
        )
    assert status == 200, listed
    assert set(_by_work(listed)) == {
        ids["chat"],
        ids["helper"],
        ids["queued"],
        f"loop:{mine.id}",
        f"workflow:{run.id}",
    }
    assert f"loop:{theirs.id}" not in _by_work(listed)


@pytest.mark.asyncio
async def test_an_app_reads_the_loops_its_own_chats_started_and_no_others(tmp_path):
    with _home(tmp_path / "home"):
        _install(tmp_path / "home", APP, {"api": ["/api/running"]})
        async with _everything(tmp_path) as (w, ids):
            its = _loop_in(f"dashboard:{APPS_CHAT}")
            yours = _loop_in(f"dashboard:{CHAT}")
            its_run = _run_from(f"dashboard:{APPS_CHAT}", "The app's digest")
            your_run = _run_from(f"dashboard:{CHAT}", "Your digest")
            status, listed = await _as(w.state, APP, "/api/running")
    assert status == 200, listed
    assert set(_by_work(listed)) == {
        ids["apps_chat"],
        ids["apps_helper"],
        f"loop:{its.id}",
        f"workflow:{its_run.id}",
    }
    for work in (f"loop:{yours.id}", f"workflow:{your_run.id}", ids["loop"], ids["run"]):
        assert work not in _by_work(listed), work

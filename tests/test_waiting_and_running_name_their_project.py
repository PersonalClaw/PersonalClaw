"""What waits on you and what runs names the project its work is in, and a loop's kind.

Every item of ``GET /api/waiting`` and every row of ``GET /api/running`` carries ``project_id``,
decided once on the gateway (``work_project``), and ``loop_kind``, from the same read of the loop:

- an approval in a project chat, a question in it, a helper it started, a loop's question and a
  gate of a run in a project each carry that project, and so does a helper a loop's worker or a
  run's step started, whose start waits on you or which runs, held by the gateway either way;
- a loop launched into no project, or into one that is gone, is in the project made for its tasks;
- a loop's item and row carry its kind, and a run started as a loop its kind too; any other work
  carries ``""``;
- a trigger's parked action is in no project;
- an id that names no project (a deleted one) reads ``""``;
- a project read that raises, or a store or a session's place that raises while an item is
  placed, leaves the list readable, that item in no project.

Each source is raised by its own producer, on a real ``DashboardState``, read back through the
real routes (the fixtures of ``test_waiting_list`` and ``test_running_now``).
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest
from test_helpers_loops_and_runs_say_their_phases import _helper
from test_running_now import _by_work
from test_running_now import _everything as _running_everything
from test_running_now import _Helpers
from test_running_now import _read as _read_running
from test_waiting_list import _by_id
from test_waiting_list import _everything as _waiting_everything
from test_waiting_list import _read as _read_waiting
from test_waiting_list import _until
from test_work_phase_classifier import CHAT

from personalclaw import running_now, waiting


def _garden() -> str:
    from personalclaw.tasks.hierarchy import HierarchyStore

    return HierarchyStore().create_project("Garden").id


def _in(loop_id: str, project_id: str, status) -> None:
    from personalclaw.loop import store as loops

    loops.update_status(loop_id, status, project_id=project_id)


def _run_in(run_id: str, project_id: str) -> None:
    from personalclaw.workflows import store as runs

    run = runs.get(run_id)
    assert run is not None
    run.project_id = project_id
    runs.save(run)


@pytest.mark.asyncio
async def test_each_waiting_item_names_its_project_and_a_loops_its_kind(tmp_path):
    from personalclaw.loop.loop import LoopStatus

    garden = _garden()
    async with _waiting_everything(tmp_path) as (w, ids):
        w.session.project_id = garden
        _in(ids["asking"], garden, LoopStatus.NEEDS_INPUT)
        _run_in(ids["run"], garden)
        # The gateway holds a helper from before its start is asked: the chat's researcher, and
        # a fixer the loop's worker asked to start.
        worker = f"loop-{ids['asking']}"
        w.state.subagents = _Helpers(_helper("researcher"), _helper("fixer", parent=worker))
        asked = [
            asyncio.create_task(
                w.state.request_approval(
                    "chat-call", "dashboard", "shell", session=f"dashboard:{CHAT}"
                )
            ),
            asyncio.create_task(
                w.state.request_approval(
                    "spawn:fixer", "subagent", "spawn_agent", tool_input="Fix it", session=worker
                )
            ),
        ]
        try:
            await _until(lambda: "chat-call" in w.state._pending_approvals, "the chat's approval")
            await _until(lambda: "spawn:fixer" in w.state._pending_approvals, "the fixer's start")
            status, body = await _read_waiting(w.state)
        finally:
            for task in asked:
                task.cancel()
            await asyncio.gather(*asked, return_exceptions=True)
    assert status == 200, body
    items = _by_id(body)
    for item in items.values():
        assert set(item) == set(waiting.ITEM_FIELDS), item["id"]

    def placed(item_id: str) -> tuple[str, str]:
        return items[item_id]["project_id"], items[item_id]["loop_kind"]

    assert placed("approval:chat-call") == (garden, "")
    (question,) = [i for i in items.values() if i["kind"] == "question"]
    assert (question["project_id"], question["loop_kind"]) == (garden, "")
    # A helper's start, asked in the project chat: the helper works for that chat.
    assert placed("approval:spawn:researcher") == (garden, "")
    # One a loop's worker asked to start is shown in no chat: it works for the loop.
    assert items["approval:spawn:fixer"]["session"] == ""
    assert placed("approval:spawn:fixer") == (garden, "")
    assert placed(f"loop_question:{ids['asking']}") == (garden, "goal")
    (gate,) = [i for i in items.values() if i["kind"] == "gate"]
    assert (gate["project_id"], gate["loop_kind"]) == (garden, "")
    # A loop in no project still says its kind; a background call, a park, a grant and a bridge
    # confirmation belong to no project.
    assert placed(f"loop_question:{ids['merging']}") == ("", "goal")
    assert placed("approval:bg-1") == ("", "")
    (park,) = [i for i in items.values() if i["kind"] == "park"]
    assert (park["project_id"], park["loop_kind"]) == ("", "")
    for kind in ("browse_grant", "bridge_confirmation"):
        (item,) = [i for i in items.values() if i["kind"] == kind]
        assert (item["project_id"], item["loop_kind"]) == ("", ""), kind


@pytest.mark.asyncio
async def test_each_running_row_names_its_project_and_a_loops_its_kind(tmp_path):
    from personalclaw.loop.loop import LoopStatus

    garden = _garden()
    async with _running_everything(tmp_path) as (w, ids):
        w.session.project_id = garden
        _in(ids["loop_id"], garden, LoopStatus.RUNNING)
        _run_in(ids["run_id"], garden)
        # A helper the loop's worker started, one started in the run, and an agent the run's step
        # started with no parent session (invoke-agent): each works for its loop or its run, and
        # is shown in no chat.
        w.state.subagents.all_agents += [
            _helper("h5", parent=f"loop-{ids['loop_id']}"),
            _helper("h6", parent="", parent_run=f"workflow:{ids['run_id']}"),
            _helper("h7", parent="", workflow_run=ids["run_id"]),
            _helper("h8", done=True),
        ]
        # The project chat's helpers h8, which the gateway still holds, and h9, which it has let
        # go, have ended: their rows are in the just-finished tail.
        for helper in ("h8", "h9"):
            running_now.ended(
                w.state,
                {
                    "work": f"subagent:{helper}",
                    "kind": "subagent",
                    "session": CHAT,
                    "phase": "done",
                    "line": "Done",
                    "since": None,
                },
            )
        listed = await _read_running(w.state)
    rows = _by_work(listed)
    finished = {r["work"]: r for r in listed["finished"]}
    for row in rows.values():
        assert set(running_now.ROW_FIELDS) <= set(row), row["work"]

    def placed(work: str) -> tuple[str, str]:
        return rows[work]["project_id"], rows[work]["loop_kind"]

    assert placed(ids["chat"]) == (garden, "")
    # A helper the project chat started is in that chat's project.
    assert placed(ids["helper"]) == (garden, "")
    assert placed(ids["loop"]) == (garden, "goal")
    assert placed(ids["run"]) == (garden, "")
    # A run started as a loop says its kind; a trigger's fire and the app's chat are in none.
    assert placed(ids["backed"]) == ("", "general")
    assert placed(ids["trigger"]) == ("", "")
    assert placed(ids["apps_chat"]) == ("", "")
    assert placed(ids["fired"]) == ("", "")
    for helper in ("subagent:h5", "subagent:h6", "subagent:h7"):
        assert rows[helper]["session"] == "", helper
        assert placed(helper) == (garden, ""), helper
    for helper in ("subagent:h8", "subagent:h9"):
        ended = finished[helper]
        assert (ended["project_id"], ended["loop_kind"]) == (garden, ""), helper


@pytest.mark.asyncio
async def test_a_loop_launched_into_no_project_or_a_gone_one_is_in_the_one_made_for_its_tasks(
    tmp_path,
):
    from personalclaw.loop import files as loop_files
    from personalclaw.loop import store as loops
    from personalclaw.loop.loop import Loop, LoopStatus

    garden = _garden()

    def code_session(status) -> str:
        made = loops.create(Loop(id="", name="Fix the sensor", kind="code", task="fix"))
        loops.update_status(made.id, LoopStatus.RUNNING, tasks_project_id=garden)
        if status is LoopStatus.NEEDS_INPUT:
            loop_files.write_question(made.id, "Which sensor first?")
            loops.update_status(made.id, status)
        return made.id

    async with _running_everything(tmp_path) as (w, ids):
        session = code_session(LoopStatus.RUNNING)
        _in(ids["loop_id"], "p-deleted", LoopStatus.RUNNING)
        loops.update_status(ids["loop_id"], LoopStatus.RUNNING, tasks_project_id=garden)
        rows = _by_work(await _read_running(w.state))
    assert (rows[f"loop:{session}"]["project_id"], rows[f"loop:{session}"]["loop_kind"]) == (
        garden,
        "code",
    )
    assert (rows[ids["loop"]]["project_id"], rows[ids["loop"]]["loop_kind"]) == (garden, "goal")

    async with _waiting_everything(tmp_path / "w") as (w, ids):
        asking = code_session(LoopStatus.NEEDS_INPUT)
        status, body = await _read_waiting(w.state)
    assert status == 200, body
    item = _by_id(body)[f"loop_question:{asking}"]
    assert (item["project_id"], item["loop_kind"]) == (garden, "code")


@pytest.mark.asyncio
async def test_an_id_that_names_no_project_reads_as_none(tmp_path):
    from personalclaw.loop.loop import LoopStatus

    async with _running_everything(tmp_path) as (w, ids):
        w.session.project_id = "p-deleted"
        _in(ids["loop_id"], "p-deleted", LoopStatus.RUNNING)
        _run_in(ids["run_id"], "p-deleted")
        listed = await _read_running(w.state)
    rows = _by_work(listed)
    for work in (ids["chat"], ids["helper"], ids["loop"], ids["run"]):
        assert rows[work]["project_id"] == "", work
    assert rows[ids["loop"]]["loop_kind"] == "goal"


@pytest.mark.asyncio
async def test_a_project_read_that_raises_leaves_both_lists_readable_in_no_project(tmp_path):
    from personalclaw.loop.loop import LoopStatus

    def broken(_project_id: str) -> bool:
        raise RuntimeError("the project store is unreadable")

    garden = _garden()
    async with _running_everything(tmp_path) as (w, ids):
        w.session.project_id = garden
        _in(ids["loop_id"], garden, LoopStatus.RUNNING)
        with patch("personalclaw.projects.project_exists", broken):
            listed = await _read_running(w.state)
    rows = _by_work(listed)
    assert rows[ids["chat"]]["project_id"] == ""
    assert rows[ids["loop"]]["project_id"] == ""
    assert rows[ids["loop"]]["loop_kind"] == "goal"

    async with _waiting_everything(tmp_path / "w") as (w, ids):
        w.session.project_id = garden
        with patch("personalclaw.projects.project_exists", broken):
            status, body = await _read_waiting(w.state)
    assert status == 200, body
    assert body["unreadable"] == []
    assert body["waiting"] and all(i["project_id"] == "" for i in body["waiting"])


@pytest.mark.asyncio
async def test_a_store_that_raises_while_placing_leaves_both_lists_readable(tmp_path):
    from personalclaw.loop.loop import LoopStatus

    def unreadable(_key: str):
        raise RuntimeError("the store is unreadable")

    real = waiting.project_readers

    def readers():
        return real()._replace(run=unreadable, place_of=unreadable)

    garden = _garden()
    async with _waiting_everything(tmp_path) as (w, ids):
        w.session.project_id = garden
        _run_in(ids["run"], garden)
        with patch("personalclaw.waiting.project_readers", readers):
            status, body = await _read_waiting(w.state)
    assert status == 200, body
    assert body["unreadable"] == []
    items = _by_id(body)
    (gate,) = [i for i in items.values() if i["kind"] == "gate"]
    assert (gate["project_id"], gate["loop_kind"]) == ("", "")
    (question,) = [i for i in items.values() if i["kind"] == "question"]
    assert question["project_id"] == ""

    async with _running_everything(tmp_path / "r") as (w, ids):
        w.session.project_id = garden
        _in(ids["loop_id"], garden, LoopStatus.RUNNING)
        with patch("personalclaw.waiting.project_readers", readers):
            rows = _by_work(await _read_running(w.state))
    assert rows[ids["chat"]]["project_id"] == ""
    assert rows[ids["helper"]]["project_id"] == ""
    # A loop's place reads neither the run store nor a session's place.
    assert (rows[ids["loop"]]["project_id"], rows[ids["loop"]]["loop_kind"]) == (garden, "goal")

"""A chat's Delete asks whether the workflow runs it started itself stop with it.

Deleting a chat ends every helper it started (``started_work.end_chat``), and the workflow runs
those helpers started with them; the runs the chat started itself went on, whatever you wanted. The
Delete dialog now reads how many are still going (``GET /api/chat/sessions/{session}/runs-going``,
``started_work.runs_going``) and asks; ``DELETE /api/chat/sessions/{session}?runs=stop`` ends them
with the chat, each saying its chat was deleted, and without it they go on as before.
"""

from __future__ import annotations

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from test_ended_owner_ends_its_approvals import world  # noqa: F401 - a fixture

from personalclaw import started_work
from personalclaw.dashboard.chat_forget import delete_chats
from personalclaw.workflows import store
from personalclaw.workflows.models import RunOrigin, RunStatus, WorkflowRun

CHAT = "chat-a"
KEY = f"dashboard:{CHAT}"
OTHER = "dashboard:chat-b"


def _run(session_key: str, name: str = "sweep") -> WorkflowRun:
    """A live workflow run the session *session_key* started."""
    run = store.create(
        WorkflowRun(id="", workflow_name=name, origin=RunOrigin(session_key=session_key))
    )
    run.status = RunStatus.RUNNING
    store.save(run)
    return run


def _chat(world) -> None:  # noqa: F811
    world.state.get_or_create_session(CHAT).append("user", "sweep the sources", broadcast=False)


@pytest.mark.asyncio
async def test_deleted_with_stop_its_own_runs_end_saying_its_chat_was_deleted(world):  # noqa: F811
    """🔴 Red before: the chat's own run went on whatever the delete was asked."""
    _chat(world)
    mine, theirs = _run(KEY), _run(OTHER)

    await delete_chats(world.state, [CHAT], by="you", stop_runs=True)

    assert store.cancel_requested(mine.id), "its own run went on"
    assert store.cancel_reason(mine.id) == started_work.CHAT_DELETED
    assert not store.cancel_requested(theirs.id), "another chat's run is not this delete's"


@pytest.mark.asyncio
async def test_deleted_without_stop_its_own_runs_go_on(world):  # noqa: F811
    _chat(world)
    mine = _run(KEY)

    await delete_chats(world.state, [CHAT], by="you")

    assert not store.cancel_requested(mine.id)
    assert store.get(mine.id).status is RunStatus.RUNNING


def test_it_counts_the_runs_the_chat_started_itself_still_going(world):  # noqa: F811
    """🔴 Red before: nothing could say how many there were."""
    _run(KEY)
    _run(CHAT, name="bare")
    stopped = _run(KEY, name="stopped")
    store.request_cancel(stopped.id, reason="you stopped it")
    _run(OTHER)
    done = _run(KEY, name="done")
    done.status = RunStatus.COMPLETE
    store.save(done)

    assert started_work.runs_going(CHAT) == 2
    assert started_work.runs_going(KEY) == 2, "under either spelling of the chat"
    assert started_work.runs_going("chat-none") == 0
    assert started_work.runs_going("") == 0


def _app(state) -> web.Application:
    from personalclaw.dashboard.chat import api_chat_session_delete, api_chat_session_runs_going
    from tests.chat_test_helpers import _api_app

    app = _api_app(state)
    app.router.add_get("/api/chat/sessions/{session}/runs-going", api_chat_session_runs_going)
    app.router.add_delete("/api/chat/sessions/{session}", api_chat_session_delete)
    return app


@pytest.mark.asyncio
async def test_the_routes_count_them_and_take_the_answer(world):  # noqa: F811
    """🔴 Red before: there was no count to read, and the delete took no answer."""
    _chat(world)
    mine = _run(KEY)
    async with TestClient(TestServer(_app(world.state))) as http:
        resp = await http.get(f"/api/chat/sessions/{CHAT}/runs-going")
        assert resp.status == 200, await resp.text()
        assert await resp.json() == {"runs": 1}

        refused = await http.delete(f"/api/chat/sessions/{CHAT}?runs=maybe")
        assert refused.status == 400, await refused.text()
        assert CHAT in world.state._sessions, "a refused delete deletes nothing"

        resp = await http.delete(f"/api/chat/sessions/{CHAT}?runs=stop")
        assert resp.status == 200, await resp.text()
    assert store.cancel_reason(mine.id) == started_work.CHAT_DELETED

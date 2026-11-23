"""Deleting a loop forgets every chat it made, its task workers' included, as deleting a chat does.

The loop's Delete (``DELETE /api/loops/{id}``) removed only three chats it spelled by hand: its
stage worker ``loop-<id>``, its planner ``loop-plan-<id>`` and a ``code-plan-<id>`` nothing makes
any more. A Code loop that fanned out also made one chat per task, ``loop-<id>-<task>``, and those
were never named: their live sessions stayed registered and their transcripts, which hold
everything those workers read and wrote, stayed on disk for good. The chats it did name went by
hand, transcript only, so their tool results and checkpoints stayed, and no audit row said they
were deleted. Now each chat the loop's own key reader (``loop.manager.session_loop``) maps to the
loop, live or kept on disk, goes through the one chat delete (``chat_forget.delete_chats``).

🔴 Red before: every row here but the one that keeps other loops' and a person's chats. The row
that the transcripts are listed off the event loop is red at this fix's first version, which listed
them on it.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from personalclaw.dashboard.chat_persistence import save_session_to_history
from personalclaw.dashboard.chat_utils import persisted_history_key
from personalclaw.dashboard.state import DashboardState
from personalclaw.history import ConversationLog
from personalclaw.loop import kinds, store
from personalclaw.loop.loop import Loop

#: A task id as the code kind mints them (``loop.files.valid_task_guidance_id``).
TASK = "t-1a2b3c4d"
OTHER_TASK = "t-5e6f7a8b"


@pytest.fixture
def state() -> DashboardState:
    log = ConversationLog()
    log.init()
    sessions = MagicMock(count=0)
    sessions.remove = AsyncMock()
    sessions.destroy = AsyncMock()
    kinds.ensure_loaded()
    return DashboardState(sessions=sessions, start_time=0.0, conversation_log=log)


def _loop(name: str = "Digest titles") -> str:
    return store.create(
        Loop(
            id="",
            name=name,
            kind="code",
            task="stop escaping digest titles twice",
            plan=[{"stage": "implementation", "title": "Fix"}],
        )
    ).id


def _chat(state: DashboardState, name: str, *, live: bool = True) -> str:
    """Chat *name* with a turn, saved as the dashboard saves a chat; with *live* off, kept on disk
    only, as after a restart. Returns the key its transcript is kept under."""
    session = state.get_or_create_session(name=name)
    session.append("user", "Read digest.py and say what it escapes.", broadcast=False)
    session.append("assistant", "It escapes the title twice.", broadcast=False)
    session.drain()
    save_session_to_history(state, session, force=True)
    key = persisted_history_key(state.conversation_log, name)
    assert state.conversation_log.has_log(key), key
    if not live:
        state._sessions.pop(name, None)
    return key


def _kept(state: DashboardState, key: str) -> bool:
    return bool(state.conversation_log.has_log(key))


async def _delete(state: DashboardState, loop_id: str) -> dict:
    """The loop page's Delete, as its owner presses it."""
    from personalclaw.dashboard.handlers.loop_routes import api_loop_delete

    app = web.Application()
    app["state"] = state
    request = make_mocked_request(
        "DELETE", f"/api/loops/{loop_id}", match_info={"id": loop_id}, app=app
    )
    request["user"] = "ada"
    resp = await api_loop_delete(request)
    assert resp.status == 200, resp.text
    return json.loads(resp.body)


@pytest.mark.asyncio
async def test_a_loops_task_workers_chats_go_with_it(state):
    cid = _loop()
    worker = _chat(state, f"loop-{cid}")
    planner = _chat(state, f"loop-plan-{cid}")
    task = _chat(state, f"loop-{cid}-{TASK}")

    assert (await _delete(state, cid))["ok"] is True

    assert f"loop-{cid}-{TASK}" not in state._sessions, "a task worker's chat stayed open"
    assert not _kept(state, task), "a task worker's transcript stayed on disk"
    for name, key in ((f"loop-{cid}", worker), (f"loop-plan-{cid}", planner)):
        assert name not in state._sessions and not _kept(state, key), name


@pytest.mark.asyncio
async def test_a_task_workers_transcript_with_no_live_chat_goes_too(state):
    """After a restart a task worker's chat is kept on disk alone: its transcript still goes."""
    cid = _loop()
    task = _chat(state, f"loop-{cid}-{TASK}", live=False)
    planner = _chat(state, f"loop-plan-{cid}", live=False)

    await _delete(state, cid)

    assert not _kept(state, task), "a task worker's transcript stayed on disk"
    assert not _kept(state, planner), "the planner's transcript stayed on disk"


@pytest.mark.asyncio
async def test_what_a_loop_chat_kept_besides_its_transcript_goes(state, tmp_path):
    """Its tool results (a file's contents, a command's output) and its turns' file backups."""
    from personalclaw import turn_checkpoints
    from personalclaw.session_workspace import workspace_path
    from personalclaw.tool_providers import result_store

    cid = _loop()
    for name in (f"loop-{cid}", f"loop-{cid}-{TASK}"):
        key = _chat(state, name)
        result_store.store_result(
            key, "TITLE = 'Q&A'  # digest.py", content_type="log", tool="read"
        )
        turn_checkpoints.begin_turn(key, cwd=None)
        edited = tmp_path / f"{name}.py"
        edited.write_text("TITLE = 'Q&A'\n", encoding="utf-8")
        turn_checkpoints.capture_pre_edit(key, edited, cwd=tmp_path)
        assert turn_checkpoints.session_dir(key).is_dir()

    await _delete(state, cid)

    for name in (f"loop-{cid}", f"loop-{cid}-{TASK}"):
        key = f"dashboard:{name}"
        assert not workspace_path(key).exists(), f"{name}'s tool results stayed"
        assert not turn_checkpoints.session_dir(key).exists(), f"{name}'s file backups stayed"


@pytest.mark.asyncio
async def test_each_loop_chat_is_recorded_as_deleted_and_by_whom(state, monkeypatch):
    from personalclaw import sel as sel_module

    rows: list[dict] = []
    log = SimpleNamespace(log_api_access=lambda **row: rows.append(row))
    monkeypatch.setattr(sel_module, "sel", lambda: log)
    cid = _loop()
    _chat(state, f"loop-{cid}")
    _chat(state, f"loop-{cid}-{TASK}")
    _chat(state, f"loop-{cid}-{OTHER_TASK}", live=False)

    await _delete(state, cid)

    deleted = sorted(r["resources"] for r in rows if r["operation"] == "chat.deleted")
    assert deleted == sorted([f"loop-{cid}", f"loop-{cid}-{TASK}", f"loop-{cid}-{OTHER_TASK}"])
    assert {(r["caller"], r["outcome"]) for r in rows if r["operation"] == "chat.deleted"} == {
        ("ada", "success")
    }


@pytest.mark.asyncio
async def test_another_loops_chats_and_a_persons_chat_stay(state):
    cid, other = _loop(), _loop("Feeds")
    _chat(state, f"loop-{cid}-{TASK}")
    theirs = _chat(state, f"loop-{other}-{TASK}")
    planner = _chat(state, f"loop-plan-{other}", live=False)
    person = state.get_or_create_session()
    person.append("user", "What did the digest loop change?", broadcast=False)
    person.drain()
    save_session_to_history(state, person, force=True)
    hers = persisted_history_key(state.conversation_log, person.key)

    await _delete(state, cid)

    assert f"loop-{other}-{TASK}" in state._sessions and _kept(state, theirs)
    assert _kept(state, planner)
    assert person.key in state._sessions and _kept(state, hers)


@pytest.mark.asyncio
async def test_a_loop_chat_that_could_not_be_removed_is_named_in_the_answer(state, monkeypatch):
    """Its transcript stays (a file the gateway may not unlink): the delete says which chat stayed
    rather than that every one went."""
    cid = _loop()
    _chat(state, f"loop-{cid}")
    stuck = _chat(state, f"loop-{cid}-{TASK}")
    real = state.conversation_log.delete_session
    monkeypatch.setattr(
        state.conversation_log,
        "delete_session",
        lambda key: False if key == stuck else real(key),
    )

    body = await _delete(state, cid)

    assert body["ok"] is True, "the loop itself was deleted"
    assert body["chats_kept"] == [f"loop-{cid}-{TASK}"]


@pytest.mark.asyncio
async def test_the_transcripts_are_listed_off_the_gateways_event_loop(state, monkeypatch):
    """A listing stats every transcript and reads each one that changed: seconds on a home with
    many chats, which the gateway's event loop would spend answering nothing else."""
    cid = _loop()
    task = _chat(state, f"loop-{cid}-{TASK}", live=False)
    real = state.conversation_log.list_sessions_with_metadata
    on_the_loop: list[bool] = []

    def listing():
        try:
            asyncio.get_running_loop()
            on_the_loop.append(True)
        except RuntimeError:
            on_the_loop.append(False)
        return real()

    monkeypatch.setattr(state.conversation_log, "list_sessions_with_metadata", listing)

    await _delete(state, cid)

    assert on_the_loop and not any(on_the_loop), on_the_loop
    assert not _kept(state, task)


@pytest.mark.asyncio
async def test_a_loop_chat_open_here_with_no_transcript_yet_goes_too(state):
    """A worker started this turn whose first save has not happened: it is found by its live
    session alone, and leaves the gateway with the loop."""
    cid = _loop()
    name = f"loop-{cid}-{TASK}"
    state.get_or_create_session(name=name)
    assert not _kept(state, persisted_history_key(state.conversation_log, name))

    await _delete(state, cid)

    assert name not in state._sessions, "a loop chat open here with no transcript stayed open"

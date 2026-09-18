"""A brand-new conversation's metadata survives a restart (#2969).

Name a fresh conversation, pin it, colour it, file it in a folder, mark it never-archive —
every one of those returned ``200 {"ok": true}`` and every one was gone after a restart,
because two message-count guards swallowed *metadata* writes that have nothing to do with
messages:

* ``state._flush_dirty_sessions``: ``if not session._dirty or not session.messages``
* ``chat_persistence.save_session_to_history``: ``if not state.conversation_log or not msgs``

The second was the sharp one. ``/pin`` and ``/folder`` call
``save_session_to_history(..., force=True)`` — they explicitly ask for an immediate write —
and the early return dropped it anyway, because ``force`` only bypasses the shorter-buffer
overwrite guard *below* it. ``never_archive`` was the sharpest case of all: the one flag
whose entire job is "do not let the auto-archiver take this one" could not protect itself
on a conversation with no turns yet.

``title`` had a THIRD source, and it is the one a fix aimed only at the two guards would
have missed: ``_persist_title`` → ``ConversationLog.set_title`` → ``update_metadata``, which
merges into an existing meta line and returns silently when there is no file to merge into.
The rename handler also never marked the session dirty, so no later flush covered it either.

The oracle here is always the DISK, re-read through a second ``DashboardState`` — never the
reply of the route under test.
"""

from __future__ import annotations

import json

import pytest
from aiohttp.test_utils import TestClient, TestServer
from chat_test_helpers import _make_state

from personalclaw.dashboard.state import DashboardState


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path, monkeypatch):
    """Every write lands in tmp_path — never the real ``~/.personalclaw``."""
    import personalclaw.config.loader as cfg
    import personalclaw.dashboard.state as st
    import personalclaw.session_workspace as ws

    monkeypatch.setattr(cfg, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(st, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(ws, "config_dir", lambda: tmp_path)
    return tmp_path


_NAME = "chat-new-1"


def _app(state: DashboardState):
    from aiohttp import web

    from personalclaw.dashboard import chat, session_bulk

    app = web.Application()
    app["state"] = state
    app.router.add_patch("/api/chat/sessions/{session}/title", chat.api_chat_session_rename)
    app.router.add_patch("/api/chat/sessions/{session}/color", chat.api_chat_session_color)
    app.router.add_patch("/api/chat/sessions/{session}/pin", chat.api_chat_session_pin)
    app.router.add_patch("/api/chat/sessions/{session}/folder", chat.api_chat_session_folder)
    app.router.add_put("/api/chat/sessions/{session}/tags", chat.api_chat_session_tags)
    app.router.add_patch(
        "/api/chat/sessions/{session}/lifecycle", session_bulk.api_chat_session_lifecycle
    )
    return app


def _log_path(state: DashboardState, name: str = _NAME):
    from personalclaw.dashboard.chat_utils import _history_key_for

    return state.conversation_log._path(_history_key_for(name))


def _persisted_meta(state: DashboardState, name: str = _NAME) -> dict:
    """The metadata line as it really is ON DISK, parsed straight out of the file."""
    path = _log_path(state, name)
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8").splitlines()[0])


def _restart(tmp_path) -> DashboardState:
    """A SECOND state over the same home — a gateway restart, minus the process."""
    from personalclaw.dashboard.chat_persistence import restore_recent_sessions

    fresh = _make_state(tmp_path)
    restore_recent_sessions(fresh, window_minutes=30)
    return fresh


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "suffix", "body", "meta_key", "meta_value", "attr"),
    [
        ("patch", "title", {"title": "qaS29retitled"}, "title", "qaS29retitled", "title"),
        ("patch", "pin", {"pinned": True}, "pinned", True, "pinned"),
        ("patch", "color", {"color_index": 3}, "color_index", 3, "color_index"),
        (
            "patch",
            "lifecycle",
            {"never_archive": True},
            "never_archive",
            True,
            "never_archive",
        ),
    ],
    ids=["title", "pin", "color", "never_archive"],
)
async def test_metadata_write_on_a_message_less_conversation_survives_a_restart(
    tmp_path, method, suffix, body, meta_key, meta_value, attr
):
    state = _make_state(tmp_path)
    state.get_or_create_session(_NAME)
    async with TestClient(TestServer(_app(state))) as client:
        resp = await getattr(client, method)(f"/api/chat/sessions/{_NAME}/{suffix}", json=body)
    assert resp.status == 200, await resp.text()

    # The 5s flush loop, run once synchronously — the route may only have marked dirty.
    state._flush_dirty_sessions()

    on_disk = _persisted_meta(state)
    assert on_disk.get(meta_key) == meta_value, (
        f"PATCH .../{suffix} returned 200 but the meta line on disk holds {on_disk!r} "
        "— the write was swallowed (#2969)"
    )

    restored = _restart(tmp_path)
    session = restored._sessions.get(_NAME)
    assert (
        session is not None
    ), f"the conversation did not come back after a restart, so its {suffix} could not"
    assert getattr(session, attr) == meta_value


@pytest.mark.asyncio
async def test_the_whole_cluster_survives_together(tmp_path):
    """The issue's own repro: name it, pin it, colour it, file it, protect it."""
    state = _make_state(tmp_path)
    state.get_or_create_session(_NAME)
    state._folders = [{"id": "f1", "name": "Work", "parent_id": "", "order": 0}]
    state._tags = [{"id": "t1", "name": "urgent"}]

    async with TestClient(TestServer(_app(state))) as client:
        for method, suffix, body in [
            ("patch", "title", {"title": "qaS29retitled"}),
            ("patch", "pin", {"pinned": True}),
            ("patch", "color", {"color_index": 3}),
            ("patch", "folder", {"folder_id": "f1"}),
            ("patch", "lifecycle", {"never_archive": True}),
            ("put", "tags", {"tags": ["t1"]}),
        ]:
            resp = await getattr(client, method)(f"/api/chat/sessions/{_NAME}/{suffix}", json=body)
            assert resp.status == 200, f"{suffix}: {await resp.text()}"
    state._flush_dirty_sessions()

    restored = _restart(tmp_path)
    session = restored._sessions.get(_NAME)
    assert session is not None, "the conversation itself was lost on restart"
    assert session.title == "qaS29retitled"
    assert session.pinned is True
    assert session.color_index == 3
    assert session.folder_id == "f1"
    assert session.never_archive is True
    assert session.tags == ["t1"]


@pytest.mark.asyncio
async def test_pin_and_folder_land_immediately_not_only_on_the_next_flush(tmp_path):
    """``force=True`` means "write it NOW" — the early return used to eat that too."""
    state = _make_state(tmp_path)
    state.get_or_create_session(_NAME)
    state._folders = [{"id": "f1", "name": "Work", "parent_id": "", "order": 0}]
    async with TestClient(TestServer(_app(state))) as client:
        assert (await client.patch(f"/api/chat/sessions/{_NAME}/pin", json={"pinned": True})).status
        # NO _flush_dirty_sessions() call — the route asked for an immediate write.
        assert _persisted_meta(state).get("pinned") is True
        await client.patch(f"/api/chat/sessions/{_NAME}/folder", json={"folder_id": "f1"})
        assert _persisted_meta(state).get("folder_id") == "f1"


def test_a_pristine_empty_conversation_is_NOT_persisted(tmp_path):
    """The gate on the fix: a shutdown flush must not mint a file for every empty tab.

    ``save_all_sessions_to_history`` passes ``force=True`` for every resident session, so
    without this gate a restart would resurrect every conversation the user opened and
    never typed in. A message-less session earns a file only when something was written to
    it AFTER create.
    """
    from personalclaw.dashboard.chat_persistence import save_all_sessions_to_history

    state = _make_state(tmp_path)
    state.get_or_create_session(_NAME, agent="coder", model="m1")
    save_all_sessions_to_history(state)
    assert _persisted_meta(state) == {}, (
        "a pristine empty conversation was written to disk — the create-time-only meta line "
        "must not be enough to mint a file"
    )
    assert _restart(tmp_path)._sessions == {}


def test_an_empty_buffer_never_overwrites_a_persisted_transcript(tmp_path):
    """The invariant the old ``not msgs`` return was accidentally providing.

    Dropping that return is only safe because the transcript-empty path refuses to touch a
    file that holds turns — including under ``force``, and including a buffer holding only
    non-transcript rows, which ``not msgs`` used to wave straight through to a full rewrite.
    """
    from personalclaw.dashboard.chat_persistence import save_session_to_history

    state = _make_state(tmp_path)
    session = state.get_or_create_session(_NAME)
    session.append("user", "the original question", "msg u0", broadcast=False)
    session.append("assistant", "the original answer", "msg a0", broadcast=False)
    session.drain()
    save_session_to_history(state, session, force=True)
    before = _log_path(state).read_bytes()

    blank = state.get_or_create_session(_NAME)
    blank.messages.clear()
    blank.pinned = True
    save_session_to_history(state, blank, force=True)
    after = _log_path(state).read_bytes()
    assert after == before, "a forced save with an empty buffer rewrote a real transcript"

    blank.messages.extend([{"role": "chunk", "content": "x"}, {"role": "done", "content": ""}])
    save_session_to_history(state, blank, force=True)
    assert (
        _log_path(state).read_bytes() == before
    ), "a buffer of only non-transcript rows rewrote a real transcript"


def test_the_flush_loop_no_longer_gates_on_message_count(tmp_path):
    """The colour and natural-voice handlers set ``_dirty`` and nothing else."""
    state = _make_state(tmp_path)
    session = state.get_or_create_session(_NAME)
    session.color_index = 5
    session._dirty = True
    state._flush_dirty_sessions()
    assert _persisted_meta(state).get("color_index") == 5
    assert session._dirty is False, "a successful flush must clear the dirty flag"

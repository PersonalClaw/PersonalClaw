"""A chat's rewind finds the files its agent wrote (#5726).

A dashboard chat goes by two keys: its own, which the chat runner opens each turn under and a
rewind reads, and the ``dashboard:`` key its agent's runtime carries, which the native file tools
back a file up under. The checkpoint store kept them in two folders, so a rewind found none of
the agent's backups and could only say each file had changed with no backup. Every form of a
chat's key now reaches one folder, and a home that already holds both folders for a chat gets
the backups back: on first read they join the chat's turns, each in the turn that was open when
it was taken.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from chat_test_helpers import _make_state

from personalclaw import turn_checkpoints as tc
from personalclaw.agents.native.builtin_tools import NativeBuiltinToolProvider
from personalclaw.dashboard.chat import api_chat_session_rewind, api_chat_session_rewind_preview
from personalclaw.dashboard.chat_utils import _history_key_for

ORIGINAL = "the plan, as it was\n"
WRITTEN = "the plan, as the agent wrote it\n"


def _app(state) -> web.Application:
    app = web.Application()
    app["state"] = state
    app.router.add_get("/api/chat/sessions/{session}/rewind", api_chat_session_rewind_preview)
    app.router.add_post("/api/chat/sessions/{session}/rewind", api_chat_session_rewind)
    return app


@pytest.fixture
def scene(tmp_path, monkeypatch):
    """A dashboard chat, ``s1``, with a folder holding one file, and its two turns opened as the
    chat runner opens them: under the chat's own key."""
    monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
    state = _make_state(tmp_path)
    session = state.get_or_create_session("s1")
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "plan.md").write_text(ORIGINAL, encoding="utf-8")
    return state, session, ws


async def _agent_writes(ws, session, text: str = WRITTEN) -> None:
    """The agent writes ``plan.md`` with the native ``write_file``, under the key its runtime
    carries for the chat (``dashboard:<key>``)."""
    tools = NativeBuiltinToolProvider(cwd=ws, session_key=_history_key_for(session.key))
    read = await tools.invoke("read_file", {"path": "plan.md"})
    assert read.success, read.error
    wrote = await tools.invoke("write_file", {"path": "plan.md", "content": text})
    assert wrote.success, wrote.error


async def _rewind_to_turn_1(state) -> dict:
    async with TestClient(TestServer(_app(state))) as client:
        r = await client.post("/api/chat/sessions/s1/rewind", json={"turn": 1, "confirm": True})
        assert r.status == 200, await r.text()
        return await r.json()


def _as_main_kept_it(monkeypatch):
    """The store as it was before #5726: each form of a chat's key its own folder."""
    return monkeypatch.setattr(tc, "store_key", lambda key: (key or "").strip(), raising=False)


def _old_folder(session) -> object:
    return tc.store_root() / tc.session_slug(_history_key_for(session.key))


@pytest.mark.asyncio
async def test_a_file_the_agent_wrote_is_put_back_by_rewinding_its_turn(scene) -> None:
    state, session, ws = scene
    tc.begin_turn(session.key, cwd=ws)
    tc.begin_turn(session.key, cwd=ws)
    await _agent_writes(ws, session)
    assert (ws / "plan.md").read_text(encoding="utf-8") == WRITTEN
    body = await _rewind_to_turn_1(state)
    assert body["ok"] is True, body
    assert (ws / "plan.md").read_text(encoding="utf-8") == ORIGINAL


@pytest.mark.parametrize(
    "key", ["../../etc", "a/../../b", "/etc/passwd", "dashboard:../x/y", "..", "."]
)
def test_a_key_with_dots_or_slashes_keeps_its_folders_in_the_store(key: str) -> None:
    """A session key can come from a request. Whatever it holds, its folders, its own and its old
    ``dashboard:`` one, sit directly under the store."""
    root = tc.store_root()
    assert tc.session_dir(key).parent == root
    assert tc._legacy_dir(key).parent == root


@pytest.mark.parametrize("name", ["../outside", "/etc", "a/../../outside", ""])
def test_a_folder_outside_the_store_is_refused(name: str) -> None:
    with pytest.raises(ValueError):
        tc._store_folder(name)


def test_every_form_of_a_chat_s_key_reaches_one_folder() -> None:
    forms = [
        "s1",
        "dashboard:s1",
        "dashboard_s1",
        "dashboard:dashboard:s1",
        "dashboard_dashboard_s1",
    ]
    assert len({tc.session_dir(k) for k in forms}) == 1
    assert tc.session_dir("slack:C1:T1") != tc.session_dir("s1"), "another channel keeps its own"


@pytest.mark.asyncio
async def test_a_home_with_both_folders_rewinds_the_files_from_the_dashboard_one(
    scene, monkeypatch
) -> None:
    state, session, ws = scene
    with monkeypatch.context() as before:
        _as_main_kept_it(before)
        tc.begin_turn(session.key, cwd=ws)
        tc.begin_turn(session.key, cwd=ws)
        await _agent_writes(ws, session)
        old = _old_folder(session)
        assert old.is_dir() and tc.session_dir(session.key).is_dir(), "two folders, as on main"
    body = await _rewind_to_turn_1(state)
    assert body["ok"] is True, body
    assert (ws / "plan.md").read_text(encoding="utf-8") == ORIGINAL
    assert not old.exists(), "the old folder has joined the chat's"


@pytest.mark.asyncio
async def test_a_backup_joins_the_turn_that_was_open_when_it_was_taken(scene, monkeypatch) -> None:
    state, session, ws = scene
    with monkeypatch.context() as before:
        _as_main_kept_it(before)
        tc.begin_turn(session.key, cwd=ws)
        tc.begin_turn(session.key, cwd=ws)
        await _agent_writes(ws, session)
    files = {
        t: [f["path"] for f in tc.recorded_file_entries(session.key) if f["turn"] == t]
        for t in (1, 2)
    }
    assert files == {1: [], 2: [str(ws / "plan.md")]}


@pytest.mark.asyncio
async def test_a_turn_keeps_the_older_of_two_backups_of_one_file(scene, monkeypatch) -> None:
    state, session, ws = scene
    plan = ws / "plan.md"
    with monkeypatch.context() as before:
        _as_main_kept_it(before)
        tc.begin_turn(session.key, cwd=ws)
        tc.begin_turn(session.key, cwd=ws)
        tc.capture_pre_edit(_history_key_for(session.key), plan, cwd=ws)
        plan.write_text("a first edit\n", encoding="utf-8")
        tc.capture_pre_edit(session.key, plan, cwd=ws)
        plan.write_text("a second edit\n", encoding="utf-8")
    assert (await _rewind_to_turn_1(state))["ok"] is True
    assert (
        plan.read_text(encoding="utf-8") == ORIGINAL
    ), "the backup from before the turn's first edit"


@pytest.mark.asyncio
async def test_a_chat_with_only_the_old_folder_takes_it_whole(scene, monkeypatch) -> None:
    state, session, ws = scene
    with monkeypatch.context() as before:
        _as_main_kept_it(before)
        tc.begin_turn(_history_key_for(session.key), cwd=ws)
        tc.begin_turn(_history_key_for(session.key), cwd=ws)
        await _agent_writes(ws, session)
        assert not tc.session_dir(session.key).exists()
    assert (await _rewind_to_turn_1(state))["ok"] is True
    assert (ws / "plan.md").read_text(encoding="utf-8") == ORIGINAL


@pytest.mark.asyncio
async def test_pruning_orphans_keeps_a_live_chat_s_old_backups(scene, monkeypatch) -> None:
    state, session, ws = scene
    with monkeypatch.context() as before:
        _as_main_kept_it(before)
        tc.begin_turn(session.key, cwd=ws)
        tc.begin_turn(session.key, cwd=ws)
        await _agent_writes(ws, session)
    assert tc.prune_orphans([session.key]) == 0
    assert (await _rewind_to_turn_1(state))["ok"] is True
    assert (ws / "plan.md").read_text(encoding="utf-8") == ORIGINAL


@pytest.mark.asyncio
async def test_forgetting_a_chat_deletes_both_its_folders(scene, monkeypatch) -> None:
    _state, session, ws = scene
    with monkeypatch.context() as before:
        _as_main_kept_it(before)
        tc.begin_turn(session.key, cwd=ws)
        await _agent_writes(ws, session)
        old = _old_folder(session)
    assert tc.prune_session(session.key) is True
    assert not old.exists() and not tc.session_dir(session.key).exists()


@pytest.mark.asyncio
async def test_a_backup_that_cannot_be_read_keeps_the_old_folder_for_the_next_read(
    scene, monkeypatch
) -> None:
    """Only a backup dropped on purpose may go with the old folder. One whose bytes can't be read
    as it joins keeps the folder, and the next read joins it."""
    state, session, ws = scene
    with monkeypatch.context() as before:
        _as_main_kept_it(before)
        tc.begin_turn(session.key, cwd=ws)
        tc.begin_turn(session.key, cwd=ws)
        await _agent_writes(ws, session)
        old = _old_folder(session)
    assert old != tc.session_dir(session.key)
    read_bytes = Path.read_bytes

    def unreadable(path: Path) -> bytes:
        if path.parent == old / "blobs":
            raise PermissionError(13, "Permission denied", str(path))
        return read_bytes(path)

    with monkeypatch.context() as broken:
        broken.setattr(Path, "read_bytes", unreadable)
        tc.recorded_file_entries(session.key)
    assert old.is_dir(), "a backup that failed to join is not deleted"

    assert (await _rewind_to_turn_1(state))["ok"] is True
    assert (ws / "plan.md").read_text(encoding="utf-8") == ORIGINAL
    assert not old.exists(), "the next read joined it"


@pytest.mark.asyncio
async def test_a_backup_whose_bytes_are_gone_is_dropped_with_the_old_folder(
    scene, monkeypatch
) -> None:
    """Bytes missing from the old folder are gone for good, as nothing writes it any more: the
    backup is dropped and the folder goes, rather than staying for a read that can never join it."""
    state, session, ws = scene
    with monkeypatch.context() as before:
        _as_main_kept_it(before)
        tc.begin_turn(session.key, cwd=ws)
        tc.begin_turn(session.key, cwd=ws)
        await _agent_writes(ws, session)
        old = _old_folder(session)
    blobs = list((old / "blobs").glob("*.bin"))
    assert blobs, "the agent's write was backed up in the old folder"
    for blob in blobs:
        blob.unlink()

    assert tc.recorded_file_entries(session.key) == []
    assert not old.exists(), "nothing is left there to join"


@pytest.mark.asyncio
async def test_a_join_that_raises_still_opens_the_turn(scene, monkeypatch) -> None:
    """A turn whose join fails still gets its checkpoint, so what it writes can be rewound."""
    state, session, ws = scene
    with monkeypatch.context() as before:
        _as_main_kept_it(before)
        tc.begin_turn(session.key, cwd=ws)
        await _agent_writes(ws, session, "a first edit\n")
        old = _old_folder(session)
    assert old != tc.session_dir(session.key)

    def fails(_key: str) -> int:
        raise OSError(28, "No space left on device")

    with monkeypatch.context() as broken:
        broken.setattr(tc, "_join_legacy", fails)
        assert tc.begin_turn(session.key, cwd=ws) == 2
    await _agent_writes(ws, session)
    assert old.is_dir(), "the join is left for the next read"

    assert (await _rewind_to_turn_1(state))["ok"] is True
    assert (ws / "plan.md").read_text(encoding="utf-8") == "a first edit\n", "turn 2 is undone"

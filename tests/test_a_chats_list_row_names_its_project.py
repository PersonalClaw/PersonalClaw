"""A chat's row in the chat list names the project it belongs to, live or kept only on disk.

The sidebar groups every thread under its project, and a chat's project is read off its row in
``GET /api/chat/sessions``. A live chat's row has carried ``project_id`` since the chat record
did, but a chat kept only on disk (every chat after a restart, with ``restore_sessions`` off)
was listed by a row built by hand from its meta line, which left the field out: the same chat
would sit under its project until the gateway restarted and then move to "No project".

The disk row now reads the meta line's ``project_id`` the way restoring the chat does
(``chat_persistence``): a string, else ``""``, so a hand-edited value can't name a project.
A chat with no project lists ``""`` either way.
"""

from __future__ import annotations

import pytest
from aiohttp.test_utils import TestClient, TestServer
from chat_test_helpers import _make_app, _make_state

from personalclaw.dashboard.chat_persistence import save_session_to_history
from personalclaw.dashboard.chat_utils import _history_key_for


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)


def _kept(state, key: str, project_id: str = "") -> None:
    """A chat with one message, saved to disk, bound to *project_id* ("" for none)."""
    chat = state.get_or_create_session(key)
    chat.project_id = project_id
    chat.append("user", "what should the garden grow", "msg msg-u")
    chat.drain()
    save_session_to_history(state, chat, force=True)


async def _row(state, key: str) -> dict:
    async with TestClient(TestServer(_make_app(state))) as http:
        rows = await (await http.get("/api/chat/sessions")).json()
    (row,) = [r for r in rows if r["key"] == key]
    return row


@pytest.mark.asyncio
async def test_a_live_chat_lists_its_project(tmp_path):
    state = _make_state(tmp_path)
    _kept(state, "garden-1", "p-garden")
    assert (await _row(state, "garden-1"))["project_id"] == "p-garden"


@pytest.mark.asyncio
async def test_a_chat_kept_only_on_disk_lists_the_same_project(tmp_path):
    state = _make_state(tmp_path)
    _kept(state, "garden-1", "p-garden")
    live = (await _row(state, "garden-1"))["project_id"]
    state._sessions.pop("garden-1")
    assert (await _row(state, "garden-1"))["project_id"] == live == "p-garden"


@pytest.mark.asyncio
async def test_a_chat_with_no_project_lists_none_live_or_on_disk(tmp_path):
    state = _make_state(tmp_path)
    _kept(state, "loose-1")
    assert (await _row(state, "loose-1"))["project_id"] == ""
    state._sessions.pop("loose-1")
    assert (await _row(state, "loose-1"))["project_id"] == ""


@pytest.mark.asyncio
@pytest.mark.parametrize("edited", [7, ["p-garden"], {"id": "p-garden"}, None])
async def test_a_hand_edited_project_that_is_no_string_lists_none(tmp_path, edited):
    """Read as restoring the chat reads it: only a string names a project."""
    state = _make_state(tmp_path)
    _kept(state, "garden-1", "p-garden")
    state._sessions.pop("garden-1")
    state.conversation_log.update_metadata(_history_key_for("garden-1"), {"project_id": edited})
    assert (await _row(state, "garden-1"))["project_id"] == ""

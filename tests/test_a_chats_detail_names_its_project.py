"""A chat's own read names the project it is in.

The chat's header draws the project as its chip. It used to take the project only from a new
chat's draft, seeded from a project's Chat button, and the first send opens the chat again under
its own key, so no chat ever showed it. ``GET /api/chat/sessions/{session}`` now serves the chat's
``project_id``, for a chat the gateway holds and for one kept only on disk, and ``""`` for a chat
in none.
"""

import pytest
from aiohttp.test_utils import TestClient, TestServer
from chat_test_helpers import _make_app, _make_state


def _seed(state, name: str, project_id: str = "") -> object:
    s = state.get_or_create_session(name)
    s.project_id = project_id
    s.append("user", "Which seeds ship this week?", "msg msg-u")
    s.append("assistant", "Two do.", "msg msg-a")
    s.drain()
    return s


@pytest.mark.asyncio
async def test_a_held_chat_in_a_project_names_it(tmp_path):
    state = _make_state(tmp_path)
    _seed(state, "seed-order", "p-garden")
    async with TestClient(TestServer(_make_app(state))) as client:
        detail = await (await client.get("/api/chat/sessions/seed-order")).json()
    assert detail["project_id"] == "p-garden"


@pytest.mark.asyncio
async def test_a_chat_kept_only_on_disk_names_its_project(tmp_path):
    state = _make_state(tmp_path)
    state.conversation_log.append("dashboard:seed-order", "user", "Which seeds ship this week?")
    state.conversation_log.update_metadata("dashboard:seed-order", {"project_id": "p-garden"})
    assert "seed-order" not in state._sessions
    async with TestClient(TestServer(_make_app(state))) as client:
        detail = await (await client.get("/api/chat/sessions/seed-order")).json()
    assert detail["project_id"] == "p-garden"


@pytest.mark.asyncio
async def test_a_chat_in_no_project_names_none(tmp_path):
    state = _make_state(tmp_path)
    _seed(state, "loose")
    async with TestClient(TestServer(_make_app(state))) as client:
        detail = await (await client.get("/api/chat/sessions/loose")).json()
    # Always present, so the page reads one value rather than a presence.
    assert detail["project_id"] == ""

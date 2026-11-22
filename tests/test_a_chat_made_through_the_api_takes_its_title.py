"""A chat made through the API can be given its title as it is made.

Measured before this was written: ``POST /api/chat/sessions`` read ``name``, ``agent`` and the rest,
and no ``title``. A chat made that way was titled by its key until auto-title ran after its first
turn, so it read "Untitled chat" (named, its name, which the dashboard counts as no title), and an
API caller had to rename it with a second request.

Now the route takes an optional ``title``, which is the chat's own title as a rename's is:
auto-title leaves it alone. It is cut at 200 characters, as a rename's is, and a title that is not
a string is refused. A chat that already has a title of its own, opened again by its name, keeps it.
"""

from __future__ import annotations

import pytest
from aiohttp.test_utils import TestClient, TestServer
from chat_test_helpers import _make_app_with_agent_routes, _make_state

pytestmark = pytest.mark.asyncio


async def _create(state, body: dict):
    async with TestClient(TestServer(_make_app_with_agent_routes(state))) as client:
        resp = await client.post("/api/chat/sessions", json=body)
        return resp.status, await resp.json()


async def test_a_title_given_as_the_chat_is_made_is_its_own(tmp_path):
    """🔴 Red before: the title was not read, and the chat was titled by its key."""
    state = _make_state(tmp_path)

    status, body = await _create(state, {"title": "Router notes"})

    assert status == 200, body
    chat = state._sessions[body["key"]]
    assert chat.title == "Router notes"
    assert chat._titled, "auto-title would write over it"


async def test_a_named_chat_takes_the_title_it_is_given(tmp_path):
    """🔴 Red before, as above."""
    state = _make_state(tmp_path)

    status, body = await _create(state, {"name": "my notes", "title": "My notes"})

    assert status == 200, body
    assert state._sessions["my notes"].title == "My notes"


async def test_a_long_title_is_cut_as_a_renames_is(tmp_path):
    """🔴 Red before, as above."""
    state = _make_state(tmp_path)

    _, body = await _create(state, {"title": "x" * 250})

    assert state._sessions[body["key"]].title == "x" * 200


async def test_a_title_that_is_not_a_string_is_refused(tmp_path):
    """🔴 Red before: it was ignored, and the chat made."""
    state = _make_state(tmp_path)

    status, body = await _create(state, {"title": ["Router notes"]})

    assert status == 400
    assert body["error"]["code"] == "invalid_request"
    assert not state._sessions


async def test_a_chat_with_a_title_of_its_own_keeps_it(tmp_path):
    state = _make_state(tmp_path)
    kept = state.get_or_create_session("my notes")
    kept.title, kept._titled = "Renamed by hand", True

    status, _ = await _create(state, {"name": "my notes", "title": "Something else"})

    assert status == 200
    assert state._sessions["my notes"].title == "Renamed by hand"


async def test_no_title_leaves_the_chat_for_auto_title(tmp_path):
    state = _make_state(tmp_path)

    _, body = await _create(state, {})

    assert not state._sessions[body["key"]]._titled

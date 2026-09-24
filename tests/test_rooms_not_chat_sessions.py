"""AR-8 — a room member's provider session is not a chat, and must not list as one.

**The measured defect this file exists for.** Every member of an Agent Room holds a REAL session,
keyed ``room:<room>:<member>`` (AGENT-ROOMS C2, so each member has its own context window). That
prefix is deliberately absent from every worker-origin tuple — its absence is what makes
``profile_for_session`` return INTERACTIVE and keeps the human the room's sole approver — and the
side effect is that ``chat_handlers._origin_of("room:x:y")`` answers ``("manual", "")``.

``api_chat_sessions``' in-memory branch had NO prefix filter at all (the disk branch already skips
worker namespaces), so every member of every live room surfaced in the chat history list as though
the USER had opened it, titled by its session key, one row per member. That is the room's
attribution property inverted — a member's conversation reading as the human's — which is the one
thing this atom exists to prevent.

They are FILTERED rather than tagged ``origin="room"``: a room is one room, not N chats, and it has
its own surface (``#/chat/room/<id>``) reading ``/api/rooms``. Publishing the members as rows would
be a second, wrong answer to "what rooms do I have".
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.dashboard.state import DashboardState, _ChatSession
from personalclaw.history import ConversationLog
from personalclaw.rooms import turn as rooms_turn


def _state(tmp_path):
    sessions = MagicMock(count=0)
    sessions.remove = AsyncMock()
    sessions.get_pid = MagicMock(return_value=None)
    return DashboardState(
        sessions=sessions,
        start_time=0.0,
        conversation_log=ConversationLog(base_dir=tmp_path),
    )


async def _list_sessions(state):
    from personalclaw.dashboard.chat import api_chat_sessions

    app = web.Application()
    app["state"] = state
    app.router.add_get("/api/chat/sessions", api_chat_sessions)
    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/api/chat/sessions")
        assert resp.status == 200
        return await resp.json()


@pytest.mark.asyncio
async def test_a_room_members_session_never_appears_in_the_chat_history(tmp_path):
    """The room member is absent AND an ordinary chat is present — the control for the zero.

    Without the positive control an empty list would satisfy the first assertion, which is the
    shape of every false green this kind of filter produces.
    """
    state = _state(tmp_path)
    state._sessions["my-chat"] = _ChatSession("my-chat")
    state._sessions["room:pricing-debate:analyst"] = _ChatSession("room:pricing-debate:analyst")
    state._sessions["room:pricing-debate:skeptic"] = _ChatSession("room:pricing-debate:skeptic")

    rows = await _list_sessions(state)
    keys = {r["key"] for r in rows}

    assert "my-chat" in keys, "the positive control — an ordinary chat still lists"
    assert not any(k.startswith(rooms_turn.SESSION_KEY_PREFIX) for k in keys), keys


@pytest.mark.asyncio
async def test_the_defect_would_otherwise_have_read_as_a_MANUAL_chat(tmp_path):
    """Why the filter is needed at all, asserted rather than argued.

    `_origin_of` classifies a `room:` key as `manual` — the user's OWN chat — because the prefix is
    absent from every worker-origin tuple BY DESIGN (that absence is the sole-approver property).
    So the row would not merely have appeared; it would have appeared in the DEFAULT scope, beside
    the user's real conversations.
    """
    from personalclaw.dashboard.chat_handlers import _origin_of

    assert _origin_of("room:pricing-debate:analyst") == ("manual", "")
    # …and the prefix really is absent from both tuples, which is what makes that true.
    from personalclaw.guardrails.policy import _EXTRA_UNATTENDED_PREFIXES
    from personalclaw.session import _STATELESS_PREFIXES

    assert not any(p.startswith("room") for p in _STATELESS_PREFIXES)
    assert not any(p.startswith("room") for p in _EXTRA_UNATTENDED_PREFIXES)


@pytest.mark.asyncio
async def test_a_chat_whose_title_merely_mentions_a_room_is_untouched(tmp_path):
    """The filter is on the KEY's prefix, not on the word "room" appearing somewhere.

    A user's chat called `roommate-budget` starts with the four letters but is not a member
    session, so a substring match would have deleted a real conversation from the list.
    """
    state = _state(tmp_path)
    state._sessions["roommate-budget"] = _ChatSession("roommate-budget")
    state._sessions["room:real:analyst"] = _ChatSession("room:real:analyst")

    keys = {r["key"] for r in await _list_sessions(state)}

    assert "roommate-budget" in keys
    assert "room:real:analyst" not in keys

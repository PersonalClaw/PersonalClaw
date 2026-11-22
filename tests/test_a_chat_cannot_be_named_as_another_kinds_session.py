"""A chat a client names cannot take a name that is another kind of session's.

Measured before this was written: ``POST /api/chat/sessions`` took a client's ``name`` as it was.
A chat named ``loop-<id>`` or ``loop-plan-<id>`` was judged as a loop's own session wherever a
session's key decides something (whether anybody watches its work, where its approvals are
answered, the notes it leaves when nothing answered), and a name a live loop worker held opened that
worker's session as a chat.

Now a name whose prefix belongs to a kind other than a chat (``session_keys.KINDS``) is refused with
a 400 that says why (``session_keys.chat_name_refusal``), before anything is made or opened. A
chat's own form, a name no kind claims, and the key ``personalclaw run --session`` makes for its
name (``inbound:cli:<name>``, judged as the CLI's whatever the name) are taken as before.
"""

from __future__ import annotations

import pytest
from aiohttp.test_utils import TestClient, TestServer
from chat_test_helpers import _make_app_with_agent_routes, _make_state

from personalclaw import session_keys
from personalclaw.cli_run import session_key_for

pytestmark = pytest.mark.asyncio

#: A name for each kind that is not a chat's, by its prefix.
NOT_A_CHAT = [k.prefix + "1a2b3c4d" for k in session_keys.KINDS if k is not session_keys.CHAT]


async def _create(tmp_path, name: str, *, state=None):
    state = state or _make_state(tmp_path)
    async with TestClient(TestServer(_make_app_with_agent_routes(state))) as client:
        resp = await client.post("/api/chat/sessions", json={"name": name})
        return state, resp.status, await resp.json()


@pytest.mark.parametrize("name", NOT_A_CHAT + ["loop-plan-1a2b3c4d", "loop-1a2b3c4d-task-2"])
async def test_a_name_another_kind_of_session_has_is_refused_and_nothing_is_made(tmp_path, name):
    """🔴 Red before: a chat was made under the name, judged as that kind's own session."""
    state, status, body = await _create(tmp_path, name)

    assert status == 400, body
    assert body["error"]["code"] == "chat_name_refused"
    assert name in body["error"]["message"]
    assert name not in state._sessions


async def test_a_live_loop_workers_name_does_not_open_its_session_as_a_chat(tmp_path):
    """🔴 Red before: the worker's own session was answered as the new chat."""
    state = _make_state(tmp_path)
    worker = state.get_or_create_session("loop-1a2b3c4d")
    worker.append("user", "Run the stage.", "msg msg-u", broadcast=False)

    _, status, _ = await _create(tmp_path, "loop-1a2b3c4d", state=state)

    assert status == 400
    assert state._sessions["loop-1a2b3c4d"] is worker


@pytest.mark.parametrize("name", ["chat-7-1791067183", "notes about the router", "my_chat.2"])
async def test_a_chats_own_name_is_taken(tmp_path, name):
    state, status, body = await _create(tmp_path, name)

    assert status == 200, body
    assert name in state._sessions


async def test_the_cli_names_its_session_as_its_own_whatever_name_it_is_given(tmp_path):
    """``personalclaw run --session loop-1a2b3c4d`` is the CLI's session, judged as the CLI's, and
    the gateway takes the name it sends."""
    key = session_key_for("loop-1a2b3c4d")

    assert key == "inbound:cli:loop-1a2b3c4d"
    assert session_keys.judged_kind(key) is session_keys.INBOUND
    state, status, body = await _create(tmp_path, key)
    assert status == 200, body
    assert key in state._sessions


def test_every_kind_but_a_chat_has_a_refusal_that_names_it():
    for kind in session_keys.KINDS:
        name = kind.prefix + "x"
        refusal = session_keys.chat_name_refusal(name)
        if kind is session_keys.CHAT:
            assert refusal is None
        else:
            assert refusal and repr(kind.prefix) in refusal, kind
    assert session_keys.chat_name_refusal("inbound:cli:nightly") is None
    assert session_keys.chat_name_refusal("inbound:c1:nightly") is not None

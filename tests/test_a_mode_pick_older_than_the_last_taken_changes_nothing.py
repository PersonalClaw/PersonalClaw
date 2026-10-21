"""A mode pick older than the last one a chat took changes nothing (#3808).

The composer sends a chat's permission and task mode picks one at a time, and gives up on one the
gateway does not answer in time so the next can go. A pick given up on can still reach the gateway
later, after a newer pick of the same control: then it would put the chat back in the older mode
while the composer shows the newer one, and a turn could act wider than the owner sees. So each pick
the composer sends is numbered, increasing for the chat, and the gateway refuses a pick numbered no
higher than the last one it took on that control: ``409 stale_pick``, nothing changed, the number it
took named. A request with no number (another channel, a script, the old header pill) is taken as
it always was.
"""

from __future__ import annotations

import pytest
from aiohttp.test_utils import TestClient, TestServer

from tests.chat_test_helpers import _make_app, _make_state

pytestmark = [pytest.mark.asyncio, pytest.mark.security]


async def _post(client: TestClient, path: str, body: dict) -> tuple[int, dict]:
    resp = await client.post(path, json=body)
    return resp.status, await resp.json()


def _posture(chat) -> str:
    """The chat's own permission mode, from its two posture fields (YOLO is the gateway's)."""
    return "trust" if chat._trust else "trust_reads" if chat._trust_reads else "normal"


@pytest.fixture
def chat_state(tmp_path):
    state = _make_state(tmp_path)
    return state, state.get_or_create_session()


async def test_a_trust_pick_that_lands_after_a_later_normal_pick_changes_nothing(chat_state):
    """The case the numbering exists for: Trust picked first, given up on, and delivered after the
    Normal picked next. The chat ends in Normal, the mode the composer shows."""
    state, chat = chat_state
    async with TestClient(TestServer(_make_app(state))) as client:
        assert (
            await _post(
                client, "/api/chat/mode", {"mode": "normal", "session": chat.key, "pick": 20}
            )
        )[0] == 200
        status, body = await _post(
            client, "/api/chat/mode", {"mode": "trust", "session": chat.key, "pick": 10}
        )
    assert status == 409
    assert body["error"]["code"] == "stale_pick"
    assert body["error"]["detail"] == {"last": 20}
    assert chat._trust is False
    assert _posture(chat) == "normal"


async def test_a_stale_yolo_pick_does_not_turn_yolo_on(chat_state):
    state, chat = chat_state
    async with TestClient(TestServer(_make_app(state))) as client:
        assert (
            await _post(
                client, "/api/chat/mode", {"mode": "normal", "session": chat.key, "pick": 20}
            )
        )[0] == 200
        status, _ = await _post(
            client, "/api/chat/mode", {"mode": "yolo", "session": chat.key, "pick": 19}
        )
    assert status == 409
    assert state.is_yolo_active() is False


async def test_a_task_mode_pick_older_than_the_last_one_taken_changes_nothing(chat_state):
    state, chat = chat_state
    async with TestClient(TestServer(_make_app(state))) as client:
        assert (
            await _post(
                client, "/api/chat/task-mode", {"mode": "ask", "session": chat.key, "pick": 20}
            )
        )[0] == 200
        status, body = await _post(
            client, "/api/chat/task-mode", {"mode": "agent", "session": chat.key, "pick": 10}
        )
    assert status == 409
    assert body["error"]["code"] == "stale_pick"
    assert chat._task_mode == "ask"


async def test_picks_in_order_are_each_taken_and_the_same_number_twice_is_not(chat_state):
    state, chat = chat_state
    async with TestClient(TestServer(_make_app(state))) as client:
        assert (
            await _post(client, "/api/chat/mode", {"mode": "trust", "session": chat.key, "pick": 1})
        )[0] == 200
        assert (
            await _post(
                client, "/api/chat/mode", {"mode": "normal", "session": chat.key, "pick": 2}
            )
        )[0] == 200
        assert (
            await _post(
                client, "/api/chat/mode", {"mode": "trust_reads", "session": chat.key, "pick": 2}
            )
        )[0] == 409
    assert _posture(chat) == "normal"


async def test_the_two_controls_are_numbered_apart(chat_state):
    """A task mode pick says nothing about the permission mode: a permission pick numbered below
    the last task pick is still taken."""
    state, chat = chat_state
    async with TestClient(TestServer(_make_app(state))) as client:
        assert (
            await _post(
                client, "/api/chat/task-mode", {"mode": "ask", "session": chat.key, "pick": 30}
            )
        )[0] == 200
        assert (
            await _post(
                client, "/api/chat/mode", {"mode": "trust", "session": chat.key, "pick": 10}
            )
        )[0] == 200
    assert chat._trust is True


async def test_a_refused_pick_takes_no_number(chat_state, monkeypatch):
    """A pick the gateway refuses for another reason (an operator ceiling) was not taken, so an
    older pick that comes after it is still judged against the last one that was."""
    from personalclaw import approval_grants

    state, chat = chat_state
    ceiling = {"holds": True}
    real = approval_grants.stands
    monkeypatch.setattr(
        approval_grants, "stands", lambda *a, **k: False if ceiling["holds"] else real(*a, **k)
    )
    async with TestClient(TestServer(_make_app(state))) as client:
        assert (
            await _post(
                client, "/api/chat/mode", {"mode": "trust", "session": chat.key, "pick": 50}
            )
        )[0] == 409
        ceiling["holds"] = False
        assert (
            await _post(
                client, "/api/chat/mode", {"mode": "normal", "session": chat.key, "pick": 40}
            )
        )[0] == 200


async def test_a_request_with_no_number_is_taken_as_before(chat_state):
    state, chat = chat_state
    async with TestClient(TestServer(_make_app(state))) as client:
        assert (
            await _post(
                client, "/api/chat/mode", {"mode": "normal", "session": chat.key, "pick": 20}
            )
        )[0] == 200
        assert (await _post(client, "/api/chat/mode", {"mode": "trust", "session": chat.key}))[
            0
        ] == 200
    assert chat._trust is True


@pytest.mark.parametrize("pick", [-1, "7", 1.5, True, None])
async def test_a_number_that_is_not_one_is_refused(chat_state, pick):
    state, chat = chat_state
    async with TestClient(TestServer(_make_app(state))) as client:
        status, _ = await _post(
            client, "/api/chat/mode", {"mode": "trust", "session": chat.key, "pick": pick}
        )
    assert status == 400
    assert chat._trust is False


async def test_a_numbered_pick_names_its_chat(chat_state):
    state, chat = chat_state
    async with TestClient(TestServer(_make_app(state))) as client:
        assert (await _post(client, "/api/chat/mode", {"mode": "trust", "pick": 5}))[0] == 400
        assert (
            await _post(
                client, "/api/chat/mode", {"mode": "trust", "session": "no-such-chat", "pick": 5}
            )
        )[0] == 400
    assert chat._trust is False


async def test_a_stale_yolo_from_one_chat_does_not_undo_another_chats_newer_pick(tmp_path):
    """YOLO is the gateway's, not a chat's: a YOLO pick chat A gave up on, landing after chat B's
    newer Normal, would turn YOLO back on for every chat while B shows Normal. So a permission pick
    is held to the last one any chat took."""
    state = _make_state(tmp_path)
    a = state.get_or_create_session("chat-a-stale-yolo")
    b = state.get_or_create_session("chat-b-stale-yolo")
    assert a.key != b.key
    async with TestClient(TestServer(_make_app(state))) as client:
        assert (
            await _post(client, "/api/chat/mode", {"mode": "normal", "session": b.key, "pick": 20})
        )[0] == 200
        status, body = await _post(
            client, "/api/chat/mode", {"mode": "yolo", "session": a.key, "pick": 10}
        )
    assert status == 409
    assert body["error"]["detail"] == {"last": 20}
    assert state.is_yolo_active() is False


async def test_a_number_far_ahead_of_the_gateways_clock_is_refused(chat_state):
    """Taken, it would leave no higher number to send, and every later pick refused."""
    import time

    from personalclaw.dashboard.chat_handlers import PICK_AHEAD_MS

    state, chat = chat_state
    ahead = int(time.time() * 1000) + PICK_AHEAD_MS + 60_000
    async with TestClient(TestServer(_make_app(state))) as client:
        assert (
            await _post(
                client, "/api/chat/mode", {"mode": "trust", "session": chat.key, "pick": ahead}
            )
        )[0] == 400
        now = int(time.time() * 1000)
        assert (
            await _post(
                client, "/api/chat/mode", {"mode": "trust", "session": chat.key, "pick": now}
            )
        )[0] == 200
    assert chat._trust is True

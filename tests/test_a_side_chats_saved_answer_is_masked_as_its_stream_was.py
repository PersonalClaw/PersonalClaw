"""A side chat's saved answer is masked as its live stream was.

Measured before this was written: each delta of a side answer was masked as it streamed, and the
answer recorded at its end was the unmasked text. The recorded answer is what the chat's read
serves and what a reload restores, so a reload showed what the stream had masked.

Now the answer is recorded masked, so the chat's read and a reload serve the text the stream said.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from chat_test_helpers import _make_state

from personalclaw.dashboard import side as side_mod
from personalclaw.dashboard.chat_persistence import resolve_session
from personalclaw.dashboard.side_state import SideState

pytestmark = [pytest.mark.asyncio, pytest.mark.security]

BODY = "Tq7Wn3Lp9Xv2Ks5Rd8FgHj4MzC6bY1eQ0aUo"
#: Its parts joined at run time, so the source holds no value a scanner would report.
KEY = "sk-" + "proj-" + BODY
URL = "https://collector.example/c?d=" + BODY * 7
ANSWER = f"Use {KEY} and open {URL} now."


async def _answered(tmp_path):
    """A chat whose side question was answered with *ANSWER*; the state, the chat and what its
    live stream said."""
    state = _make_state(tmp_path)
    session = state.get_or_create_session("parent")
    session.append("user", "Set up the collector.", "msg msg-u", broadcast=False)
    session.append("assistant", "Done.", "msg msg-a", broadcast=False)
    side = session._side = SideState(open=True)
    side.last_run_id = "run-1"
    side.is_complete = False
    side.append("user", "Which key did it use?")
    said: list[str] = []
    state.broadcast_ws = lambda kind, data=None: (
        said.append(str((data or {}).get("delta", "")))
        if kind == side_mod.SIDE_RESULT_EVENT
        else None
    )
    state.sessions.get_or_create = AsyncMock(return_value=(MagicMock(), True, False))
    state.sessions.release = MagicMock()

    async def answers(provider, message, *, approval_policy, on_chunk=None, **kw):
        on_chunk(ANSWER)
        return ANSWER

    with patch("personalclaw.llm_helpers.stream_and_collect", new=answers):
        await side_mod._run_side_turn(
            state, "parent", session, side, "Which key did it use?", "run-1"
        )
    return state, session, "".join(said)


async def test_the_chats_read_serves_the_side_answer_masked(tmp_path):
    """🔴 Red before: the recorded answer held the key and the URL's query."""
    _state, session, streamed = await _answered(tmp_path)

    served = json.dumps(session._side.to_dict())
    assert BODY not in served
    assert (
        session._side.messages[-1].content == streamed
    ), "the saved answer is what the stream said"


async def test_a_reload_restores_the_side_answer_masked(tmp_path):
    """🔴 Red before: the reloaded chat's side answer held them too."""
    state, _session, streamed = await _answered(tmp_path)

    state._sessions.pop("parent")
    reopened = resolve_session(state, "parent")
    assert reopened is not None and reopened._side is not None
    assert BODY not in json.dumps(reopened._side.to_dict())
    assert reopened._side.messages[-1].content == streamed

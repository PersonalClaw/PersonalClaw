"""A side answer still being written is in its chat's read, and each of its deltas is stamped.

Measured before this was written: a chat's read (session detail) held a side answer only once it
was recorded at its end. A page that opened the chat while the answer streamed restored the
question with no answer and marked it finished, so the deltas after the read had nowhere to go and
the deltas before it were lost. The deltas carried no stamp, so a page could not tell which ones a
read already held.

Now the side buffer keeps what the answer has streamed so far (``SideState.partial``, served by
``as_read``, kept by no save), grown in the
step each delta is stamped (``next_stream_seq``) and sent. A read reporting ``stream_seq = W`` holds
exactly the deltas stamped ``<= W``, as it does a chat's own chunks.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from chat_test_helpers import _make_state

from personalclaw.dashboard import side as side_mod
from personalclaw.dashboard.side_state import SideState


@pytest.mark.asyncio
async def test_a_read_mid_answer_holds_exactly_the_deltas_stamped_up_to_it(tmp_path):
    """🔴 Red before: the buffer kept no partial, and the deltas carried no stamp."""
    state = _make_state(tmp_path)
    session = state.get_or_create_session("parent")
    side = session._side = SideState(open=True)
    side.last_run_id = "run-1"
    side.is_complete = False
    side.append("user", "Which file is that?")
    frames: list[dict] = []
    state.broadcast_ws = lambda kind, data=None: (
        frames.append(dict(data or {})) if kind == side_mod.SIDE_RESULT_EVENT else None
    )
    state.sessions.get_or_create = AsyncMock(return_value=(MagicMock(), True, False))
    state.sessions.release = MagicMock()
    read: dict = {}

    async def answers(provider, message, *, approval_policy, on_chunk=None, **kw):
        on_chunk("It is ")
        # A chat read between two deltas, as the detail handler takes it: in one step.
        read.update(side=side.as_read(), stream_seq=state.stream_seq)
        on_chunk("feed.py.")
        return "It is feed.py."

    with patch("personalclaw.llm_helpers.stream_and_collect", new=answers):
        await side_mod._run_side_turn(
            state, "parent", session, side, "Which file is that?", "run-1"
        )

    first, second, done = frames
    assert [f["delta"] for f in frames] == ["It is ", "feed.py.", ""]
    assert first["seq"] < second["seq"] < done["seq"]
    assert read["side"]["partial"] == "It is "
    assert read["stream_seq"] == first["seq"], "the read holds the first delta and not the second"
    assert read["side"]["is_complete"] is False
    # Recorded at its end, the answer is a message of its own and no partial is left.
    assert side.partial == ""
    assert side.messages[-1].content == "It is feed.py."
    assert done["done"] is True


@pytest.mark.asyncio
async def test_a_new_side_question_starts_its_answer_empty(tmp_path):
    """A question asked while an earlier answer streamed supersedes it: none of the earlier
    answer's words are the new one's."""
    state = _make_state(tmp_path)
    session = state.get_or_create_session("parent")
    side = session._side = SideState(open=True)
    side.last_run_id = "run-0"
    side.is_complete = False
    side.partial = "It is "
    request = MagicMock()
    request.app = {"state": state}
    request.match_info = {"session": "parent"}
    request.json = AsyncMock(return_value={"question": "And the cache?"})
    request.get = MagicMock(return_value="")

    with patch.object(side_mod, "_run_side_turn", new=AsyncMock()):
        response = await side_mod.api_side_turn(request)
        for task in list(state._background_tasks):
            await task

    assert response.status == 200
    assert side.last_run_id != "run-0"
    assert side.partial == ""

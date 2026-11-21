"""A segment boundary and a turn's thinking carry the stream stamp a chunk does.

Measured before this was written: only ``chat_chunk`` was stamped (``next_stream_seq``). A page that
reads a chat again mid-answer replays the frames that arrived meanwhile on top of the snapshot, and
drops a chunk the snapshot already holds by its stamp (``stream_seq``). A ``chat_segment`` or
``chat_thinking`` frame had no stamp, so one from before the snapshot was applied again: it ended
the text run the snapshot resumed, and one paragraph of the answer read as two.

Now both are stamped from the same count. A segment's stamp is taken in the step that settles (or
drops) it, so a snapshot reporting ``stream_seq = W`` reflects exactly the boundaries stamped
``<= W``.
"""

from __future__ import annotations

import pytest
from chat_send_world import World, new_id
from chat_test_helpers import _make_state

from personalclaw.dashboard.chat_runner import _flush_segment
from personalclaw.llm.events import (
    EVENT_COMPLETE,
    EVENT_TEXT_CHUNK,
    EVENT_THINKING_CHUNK,
    AgentEvent,
)


def test_a_settled_segment_is_stamped_in_the_step_that_settles_it(tmp_path):
    """🔴 Red before: the frame carried no stamp."""
    state = _make_state(tmp_path)
    session = state.get_or_create_session("s1")
    told: list[tuple[dict, int, list[str]]] = []

    def broadcast(kind, data=None):
        if kind == "chat_segment":
            told.append((dict(data or {}), state.stream_seq, [m["role"] for m in session.messages]))

    state.broadcast_ws = broadcast
    session.stream_chunk("Hello ")
    chunk = state.next_stream_seq()

    _flush_segment(state, session, "Hello ")

    ((frame, newest, roles),) = told
    assert frame["seq"] > chunk
    # The newest stamp when it is said, and the segment already settled: a snapshot that reports
    # this stamp holds the settled segment, and one that reports less holds the streaming entry.
    assert frame["seq"] == newest
    assert "streaming" not in roles


class _Thinks:
    """A model that thinks, then answers."""

    supports_tools = True
    _model = "scripted"

    async def complete(self, messages, *, tools=None, model=None, reasoning_effort=""):
        yield AgentEvent(kind=EVENT_THINKING_CHUNK, text="Which notes first?")
        yield AgentEvent(kind=EVENT_TEXT_CHUNK, text="Your notes are fine.")
        yield AgentEvent(kind=EVENT_COMPLETE)

    async def cancel(self, *, wait_ack_timeout: float = 0.0) -> str:
        return "acked"


@pytest.mark.asyncio
async def test_thinking_is_stamped_before_the_words_after_it(tmp_path):
    """🔴 Red before: the thinking frame carried no stamp."""
    world = World(tmp_path, model=_Thinks())
    async with world.client() as client:
        key = (await world.send(client, "Read my notes.", client_message_id=new_id()))["session"]
        await world.settled(key)

    (thinking,) = world.frames_of("chat_thinking")
    (chunk,) = world.frames_of("chat_chunk")
    assert isinstance(thinking.get("seq"), int)
    assert thinking["seq"] < chunk["seq"]

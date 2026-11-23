"""Deleting a chat stops every helper it started, and nothing they write afterwards is kept.

Measured from the code: deleting a chat stopped its turn, removed what it kept on disk and what
memory filed under it, and left its helpers (subagents) alone. A helper still running carried on
with the chat's task, one waiting for a free slot or for your Allow started later, and what they
wrote to memory after the purge was filed under the deleted chat and stayed: the start-up sweep
removes records only for chats whose transcript says Incognito or Temporary, and a deleted chat has
no transcript. A Stop ended only its own turn's helpers, so an earlier turn's went on.

Driven through the real delete (``chat_forget.delete_chats``) over a real ``SubagentManager``
whose spawn gate asks the real approval registry, as the gateway's does.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest
from test_ended_owner_ends_its_approvals import world  # noqa: F401 - a fixture
from test_ended_owner_ends_its_approvals import _real_manager, _until

from personalclaw import memory_writes, session_keys, subagent_persistence
from personalclaw.dashboard.chat_forget import delete_chats

CHAT = "chat-a"
KEY = f"dashboard:{CHAT}"
#: How what a deleted chat started reads once its delete ends it (``started_work.CHAT_DELETED``).
CHAT_DELETED = "its chat was deleted"


def _blocking(manager) -> asyncio.Event:
    """Make every helper that starts wait for its runtime forever: it is running, and never ends
    on its own. Returns the event set once one has started."""
    started = asyncio.Event()

    async def _waits(*_a, **_kw):
        started.set()
        await asyncio.Event().wait()

    manager._sessions.get_or_create = AsyncMock(side_effect=_waits)
    return started


def _writes_kept(agent_id: str) -> bool:
    """Whether a memory write made in helper *agent_id*'s work would be kept, its chat's mode as
    the helper was handed it (``memory_writes.hand_on``)."""
    return not memory_writes.blocks_memory_writes(
        session_keys.SUBAGENT.key(agent_id), memory_mode=memory_writes.PERSISTENT
    )


@pytest.mark.asyncio
async def test_deleting_a_chat_stops_its_helpers_running_nested_and_asking(world):  # noqa: F811
    """🔴 Red before: every one of them went on, and an Allow would still have started the one
    that asked."""
    state = world.state
    manager = _real_manager(state)
    started = _blocking(manager)
    state.get_or_create_session(CHAT).append("user", "sweep the sources", broadcast=False)
    running = manager.spawn("sweep the sources", parent_session_key=KEY, approval_mode="auto")
    assert running is not None
    await asyncio.wait_for(started.wait(), 5)
    nested = manager.spawn(
        "check the first source",
        parent_session_key=session_keys.SUBAGENT.key(running.id),
        approval_mode="auto",
    )
    asking = manager.spawn("draft the summary", parent_session_key=KEY)
    elsewhere = manager.spawn("tidy the notes", parent_session_key="dashboard:chat-b")
    assert nested is not None and asking is not None and elsewhere is not None
    await _until(
        lambda: f"spawn:{asking.id}" in state._pending_approvals, "the third helper never asked"
    )
    assert _writes_kept(running.id), "precondition: a helper's write is kept while its chat is"

    await delete_chats(state, [CHAT], by="you")

    for info in (running, nested, asking):
        await _until(lambda info=info: info.done, f"{info.task!r} never ended")
        assert info.cancelled, info.task
        assert info.starter_ended == CHAT_DELETED, "its report starts no turn and tells nobody"
        assert not _writes_kept(info.id), f"{info.task!r} could still write memory for the chat"
    assert f"spawn:{asking.id}" not in state._pending_approvals, "its Allow still waited"
    waiting = {w.get("id") for w in subagent_persistence.list_waiting()}
    assert asking.id not in waiting, "a restart would bring the asking helper back"
    # Another chat's helper is not this delete's to end.
    assert not elsewhere.cancelled and not elsewhere.starter_ended
    assert _writes_kept(elsewhere.id)

    await manager.stop_agents([elsewhere.id], because="the test ended")


@pytest.mark.asyncio
async def test_an_earlier_turns_helper_ends_with_its_chat_too(world):  # noqa: F811
    """🔴 Red before: a Stop ends only its own turn's helpers, and the delete ended none, so a
    helper an earlier turn started outlived the chat."""
    state = world.state
    manager = _real_manager(state)
    started = _blocking(manager)
    state.get_or_create_session(CHAT).append("user", "sweep the sources", broadcast=False)
    earlier = manager.spawn(
        "sweep last week's sources", parent_session_key=KEY, approval_mode="auto"
    )
    assert earlier is not None
    await asyncio.wait_for(started.wait(), 5)
    earlier.started -= 3600  # an hour ago, by a turn long over

    await delete_chats(state, [CHAT], by="you")

    await _until(lambda: earlier.done, "the earlier turn's helper never ended")
    assert earlier.cancelled and earlier.starter_ended == CHAT_DELETED

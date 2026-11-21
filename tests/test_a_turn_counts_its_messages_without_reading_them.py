"""The check at the end of every chat turn counts the chat's messages without reading them.

Measured before this was written: after each turn the gateway decides whether 30 new messages call
for a preferences pass (``HistoryConsolidator.maybe_consolidate``), and it counted them with
``len(_read_messages(key))``. The turn's own save had just dropped that reader's cache, so every
turn read and parsed the whole transcript on the event loop: about 58 ms for 10 MB, 715 ms for
50 MB, and the gateway answered nothing else meanwhile.

Now it reads the count as the chat list does (``ConversationLog._message_count``): the count the
save recorded, while the file holds the bytes it was counted with, so after a save it is a stat
whatever the size. A transcript another writer changed since (a channel's append, an outside edit)
is counted once per change, and the count is the reader's either way.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from chat_test_helpers import _make_state

from personalclaw.dashboard.chat_persistence import save_session_to_history
from personalclaw.dashboard.chat_utils import _history_key_for
from personalclaw.history import ConversationLog, HistoryConsolidator


def _chat(state, name: str, turns: int):
    session = state.get_or_create_session(name)
    for i in range(turns):
        session.append("user", f"q{i}", "msg msg-u", broadcast=False)
        session.append("assistant", f"a{i}", "msg msg-a", broadcast=False)
    session.drain()
    save_session_to_history(state, session)
    return session, _history_key_for(session.key)


@pytest.mark.parametrize(("turns", "starts"), [(14, False), (15, True)])
@pytest.mark.asyncio
async def test_the_consolidation_check_parses_no_transcript(tmp_path, turns, starts):
    """🔴 Red before: the check read the whole transcript after the save that just wrote it."""
    state = _make_state(tmp_path)
    _session, key = _chat(state, "long", turns)
    from personalclaw.memory import MemoryStore

    memory = MemoryStore(workspace=tmp_path / "workspace")
    memory.init()
    consolidator = HistoryConsolidator(log=state.conversation_log, memory=memory)
    started: list[str] = []

    async def _pass(k: str, **_kw: object) -> None:
        started.append(k)

    with (
        patch.object(ConversationLog, "_read_messages", side_effect=AssertionError("parsed")),
        patch.object(consolidator, "_consolidate", side_effect=_pass),
    ):
        consolidator.maybe_consolidate(key)
        for task in list(consolidator._tasks):
            await task

    assert started == ([key] if starts else [])


@pytest.mark.asyncio
async def test_the_count_follows_every_writer(tmp_path):
    """After a save, a channel's append, a shorter forced save (an /undo's) and an edit from
    outside, the count is what the reader reads."""
    state = _make_state(tmp_path)
    session, key = _chat(state, "followed", 5)
    log = state.conversation_log

    def agree() -> None:
        log._invalidate_cache(key)
        assert log._message_count(key) == len(log._read_messages(key))

    agree()
    log.append(key, "user", "sent on the channel")
    agree()
    del session.messages[-4:]
    save_session_to_history(state, session, force=True)
    agree()
    with open(log._path(key), "a", encoding="utf-8") as f:
        f.write(json.dumps({"role": "assistant", "content": "added by hand", "ts": ""}) + "\n")
    agree()


def test_a_chat_with_no_transcript_counts_none(tmp_path):
    assert ConversationLog(base_dir=tmp_path)._message_count("nobody") == 0

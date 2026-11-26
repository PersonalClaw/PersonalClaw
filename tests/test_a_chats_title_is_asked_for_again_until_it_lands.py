"""A chat whose generated title did not land is asked for it again, and a restart does not
forget it.

Seen once in a week of use: a chat kept its key for a title, and the live list named it by its first
answer's raw Markdown, until Regenerate title was pressed. A title no model answered was owed
(``owed_chores``) and asked for again once one did. A title the chain answered with no title line
was not: it waited for the chat's next turn, which may never come. And the owed list is the
running gateway's own, so a restart forgot a title owed.

Now a title answered in the wrong shape is owed as well (it is asked for once more on the
heartbeat, and a second wrong answer settles it, as any other failure does), and the gateway's
start owes the title of every chat it restores that has a turn of the owner's and no title.
"""

from __future__ import annotations

import asyncio

import pytest
from chat_test_helpers import _make_state

from personalclaw import owed_chores
from personalclaw.dashboard import chat_title
from personalclaw.guardrails.failure import OutputContractError


@pytest.fixture(autouse=True)
def _ledger():
    owed_chores.reset_owed()
    yield
    owed_chores.reset_owed()


def _chat(state, key: str, *, asked: bool = True, title: str | None = None):
    chat = state.get_or_create_session(key)
    if asked:
        chat.append("user", "Plan three days in Lisbon.", "msg msg-u", broadcast=False)
        chat.append("assistant", "**Days** | Date | Plan", "msg msg-a", broadcast=False)
    if title:
        chat.title, chat._titled = title, True
    return chat


def test_a_title_answered_with_no_title_line_is_owed(tmp_path, monkeypatch):
    """🔴 Red before: it waited for the chat's next turn."""
    state = _make_state(tmp_path)
    chat = _chat(state, "chat-1-1791067183")

    async def wrong_shape(_state, _session):
        raise OutputContractError("a title line", "Sure! Here are some ideas")

    monkeypatch.setattr(chat_title, "_title_once", wrong_shape)
    asyncio.run(chat_title._maybe_auto_title(state, chat))

    assert owed_chores.owed() == [f"title:{chat.key}"]


def test_the_start_owes_the_title_of_every_restored_chat_that_has_none(tmp_path):
    """🔴 Red before: nothing owed it, and the restored chat waited for a next turn."""
    state = _make_state(tmp_path)
    untitled = _chat(state, "chat-2-1791067183")
    _chat(state, "chat-3-1791067183", title="Lisbon in three days")
    _chat(state, "chat-4-1791067183", asked=False)

    owed = chat_title.owe_untitled_chats(state)

    assert owed == 1
    assert owed_chores.owed() == [f"title:{untitled.key}"]
    assert chat_title.owe_untitled_chats(state) == 1, "owing it again changes nothing"
    assert owed_chores.owed() == [f"title:{untitled.key}"]

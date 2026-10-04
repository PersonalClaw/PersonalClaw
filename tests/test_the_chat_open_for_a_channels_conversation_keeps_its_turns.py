"""The dashboard's chat for a conversation a channel runs itself keeps every turn the channel wrote.

A channel that runs a conversation itself (Slack's threads) writes its turns straight to the
conversation's file (``save_conversation_turn``). The dashboard's chat for the conversation, open
once the owner looks at it, or once Allow for this chat was pressed on the channel, held the
conversation as it was when it was opened. That chat rewrites the whole file from what it holds, so
the next gateway stop wrote the stale copy over every turn the channel had written since: a
conversation of four messages was saved as two. The same rewrite dropped the title the channel gave
the conversation when the chat had no title of its own, and a chat opened before the channel named
the conversation (a Trust given on its first turn opens one) never showed the name.

Now each turn the channel writes reaches the chat open for the conversation, and an open dashboard
page is shown it; the name the channel gives the conversation reaches the open chat too, and a chat
with no title of its own keeps the one the file has.

The dashboard state and the conversation log are real, in a scratch home.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from chat_test_helpers import _make_state

from personalclaw.dashboard.chat_persistence import (
    _rehydrate_session_from_history,
    save_all_sessions_to_history,
)
from personalclaw.history import ConversationLog
from personalclaw.inbox_providers import native_source
from personalclaw.llm_helpers import save_conversation_turn

THREAD = "1700000200.000200"


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path, monkeypatch):
    import personalclaw.config.loader as cfg
    import personalclaw.dashboard.state as st
    import personalclaw.session_workspace as ws

    monkeypatch.setattr(cfg, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(st, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(ws, "config_dir", lambda: tmp_path)
    return tmp_path


@pytest.fixture
def state(tmp_path):
    """The gateway's dashboard state, the one a channel's turn reaches."""
    state = _make_state(tmp_path)
    state.push_sessions_update = MagicMock()
    before = native_source.get_dashboard_state()
    native_source.set_dashboard_state(state)
    yield state
    native_source.set_dashboard_state(before)


def _turn(state, asked: str, said: str, log=None) -> None:
    """One turn the channel ran, written as it writes it."""
    save_conversation_turn(
        log or state.conversation_log, THREAD, asked, said, source_thread=THREAD, source_user="U1"
    )


def _opened(state):
    """The chat the dashboard opens for the conversation, as it opens it from the history."""
    chat = _rehydrate_session_from_history(state, THREAD)
    assert chat is not None
    return chat


def test_a_turn_the_channel_writes_reaches_the_open_chat_and_survives_a_stop(state):
    _turn(state, "tidy the notes", "Done.")
    chat = _opened(state)
    _turn(state, "and the drafts?", "Moved them.")

    assert [m["content"] for m in chat.messages] == [
        "tidy the notes",
        "Done.",
        "and the drafts?",
        "Moved them.",
    ]
    save_all_sessions_to_history(state)
    on_disk = [m["content"] for m in state.conversation_log.read_messages(THREAD)]
    assert on_disk == [
        "tidy the notes",
        "Done.",
        "and the drafts?",
        "Moved them.",
    ], "the stop's save wrote the chat's copy over the turns the channel wrote after it opened"


def test_an_open_dashboard_page_is_shown_the_channels_turn(state):
    _turn(state, "tidy the notes", "Done.")
    _opened(state)
    state._broadcast_chat_message = MagicMock()
    _turn(state, "and the drafts?", "Moved them.")

    shown = [
        (c.args[0], c.args[1]["role"], c.args[1]["content"])
        for c in state._broadcast_chat_message.call_args_list
    ]
    assert shown == [
        (THREAD, "user", "and the drafts?"),
        (THREAD, "assistant", "Moved them."),
    ]
    state.push_sessions_update.assert_called()


def test_a_turn_with_no_reply_reaches_the_open_chat_as_it_was_written(state):
    _turn(state, "tidy the notes", "Done.")
    chat = _opened(state)
    _turn(state, "are you there?", "")
    assert [m["content"] for m in chat.messages][-1] == "are you there?"
    assert len(chat.messages) == len(state.conversation_log.read_messages(THREAD)) == 3


def test_no_chat_is_opened_by_a_turn_and_another_logs_turn_is_not_the_open_chats(state, tmp_path):
    _turn(state, "tidy the notes", "Done.")
    assert THREAD not in state._sessions, "a turn opens no chat of its own"

    chat = _opened(state)
    elsewhere = ConversationLog(base_dir=tmp_path / "elsewhere")
    _turn(state, "and the drafts?", "Moved them.", log=elsewhere)
    assert len(chat.messages) == 2


def test_a_chat_with_no_title_of_its_own_keeps_the_conversations_title(state):
    chat = state.get_or_create_session(THREAD)
    assert chat.title == THREAD, "a chat opened before the conversation was named"
    _turn(state, "tidy the notes", "Done.")
    state.conversation_log.set_title(THREAD, "Tidy the notes")

    save_all_sessions_to_history(state)
    assert state.conversation_log.get_metadata(THREAD).get("title") == "Tidy the notes"
    assert [m["content"] for m in state.conversation_log.read_messages(THREAD)] == [
        "tidy the notes",
        "Done.",
    ]


def test_a_chat_open_before_the_conversation_is_named_shows_the_name_it_is_given(state):
    chat = state.get_or_create_session(THREAD)  # opened while the first turn still runs
    state.push_session_title = MagicMock()
    _turn(state, "tidy the notes", "Done.")
    state.conversation_log.set_title(THREAD, "Tidy the notes")
    assert chat.title == "Tidy the notes"
    state.push_session_title.assert_called_once_with(THREAD, "Tidy the notes")

    state.conversation_log.set_title(THREAD, "The notes, tidied")  # renamed in the thread
    assert chat.title == "The notes, tidied"
    save_all_sessions_to_history(state)
    assert state.conversation_log.get_metadata(THREAD).get("title") == "The notes, tidied"


def test_a_name_given_to_another_conversation_or_log_is_not_the_open_chats(state, tmp_path):
    chat = state.get_or_create_session(THREAD)
    _turn(state, "tidy the notes", "Done.")
    state.conversation_log.set_title("1700000300.000300", "Another thread")
    elsewhere = ConversationLog(base_dir=tmp_path / "elsewhere")
    _turn(state, "tidy the notes", "Done.", log=elsewhere)
    elsewhere.set_title(THREAD, "Elsewhere")
    assert chat.title == THREAD


def test_a_chats_own_title_written_to_the_log_is_left_as_it_is(state):
    """The dashboard writes a chat's own title to the log too (``chat_title``): that title is the
    chat's already, so it is neither rewritten nor shown again."""
    _turn(state, "tidy the notes", "Done.")
    chat = _opened(state)
    state.push_session_title = MagicMock()
    chat.title, chat._titled = "Notes for the trip", True
    state.conversation_log.set_title(THREAD, chat.title)
    assert chat.title == "Notes for the trip"
    state.push_session_title.assert_not_called()

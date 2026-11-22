"""Until a chat has its title, every surface names it by its first message, without its Markdown.

Seen once: an untitled chat's live list entry read its first answer's raw Markdown ("**Days** |
Date | Plan | |---|---| …"), export named it by its key, and the title given after five turns with
none was the first message's first 60 characters, Markdown and all. The live list named a chat by
its latest line, the history list read from disk by its first message as written.

Now the chat summary carries the first message of the owner's, plain (``first_message``), which the
dashboard names an untitled chat by; the title given after five turns and Regenerate's fallback are
that message plain; export names an untitled chat by it; and the history list's first message is
plain too (``textfmt.plain_snippet``).
"""

from __future__ import annotations

import asyncio

from chat_test_helpers import _make_state

from personalclaw import history
from personalclaw.dashboard import chat_title, session_starters
from personalclaw.textfmt import plain_snippet

ASK = "Plan **three** days in [Lisbon](https://example.invalid/lisbon), please."
PLAIN = "Plan three days in Lisbon, please."
TABLE = "**Days** | Date | Plan |\n|---|---|---|\n| 1 | Fri | Alfama |"


def test_a_snippet_keeps_the_words_and_drops_the_markup():
    """🔴 Red before: there was no such reading."""
    assert plain_snippet(ASK, 80) == PLAIN
    assert plain_snippet(TABLE, 80) == "Days Date Plan 1 Fri Alfama"
    assert plain_snippet("# A heading\n> quoted `code` and snake_case_names", 80) == (
        "A heading quoted code and snake_case_names"
    )
    assert plain_snippet("one two three four five six", 14) == "one two three…"


def _chat(state, key: str = "chat-1-1791067183", asks: int = 1):
    chat = state.get_or_create_session(key)
    for i in range(asks):
        chat.append("user", ASK if i == 0 else f"And day {i}?", "msg msg-u", broadcast=False)
        chat.append("assistant", TABLE, "msg msg-a", broadcast=False)
    return chat


def test_the_live_summary_carries_the_first_message_plain(tmp_path):
    """🔴 Red before: it carried the latest line only, here the answer's table."""
    chat = _chat(_make_state(tmp_path))

    assert chat.to_dict()["first_message"] == PLAIN


def test_the_title_given_after_five_turns_with_none_is_the_first_message_plain(tmp_path):
    """🔴 Red before: the first 60 characters, Markdown and all."""
    state = _make_state(tmp_path)
    chat = _chat(state, asks=6)

    asyncio.run(chat_title._title_once(state, chat))

    assert chat.title == PLAIN


def test_export_names_an_untitled_chat_by_its_first_message():
    """🔴 Red before: by its key."""
    messages = [{"role": "user", "content": ASK}, {"role": "assistant", "content": TABLE}]

    assert session_starters.export_title("", "chat-1-1791067183", messages) == PLAIN
    assert session_starters.export_title("chat-1-1791067183", "chat-1-1791067183", messages) == (
        PLAIN
    )
    assert session_starters.export_title("Lisbon", "chat-1-1791067183", messages) == "Lisbon"


def test_the_history_lists_first_message_is_plain():
    """🔴 Red before: as written."""
    assert history._prompt_of({"role": "user", "content": ASK}) == PLAIN

"""A search while typing reads the chats longer than the index keeps, whole, within a bound.

The index keeps a chat's first 200,000 characters, which bounds both its size and what a save
re-reads. A search found a word said past that only when asked to read the rest: driven in the
chat list, a word said late in a 232 KB chat was not listed, and the list said "the other 1 are
longer than the search index keeps, so only their beginnings were searched."

Such chats are few, and reading one directly costs 5 to 7 ms a megabyte (measured), so a search
now reads them whole, newest first, as far as ``LONG_READ_BYTES`` goes, and says what it left.
"""

from __future__ import annotations

import os
import time

import pytest

from personalclaw import session_search as ss
from personalclaw.history import ConversationLog

#: Past the index's ceiling (``_MAX_SESSION_CHARS``) by a margin.
_FILLER = "nothing to see here " * 11_000


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path / "home"))
    ss.reset_for_tests()
    ss.INDEXER.reset()
    if not ss.probe().fts5:  # pragma: no cover — a build without FTS5
        pytest.skip("sqlite build lacks FTS5")
    yield
    ss.INDEXER.reset()
    ss.reset_for_tests()


@pytest.fixture
def log() -> ConversationLog:
    log = ConversationLog()
    assert log.is_home_log()
    return log


def _chat(log: ConversationLog, key: str, *said: str, age: float) -> None:
    for text in said:
        log.append(key, "user", text)
    stamp = time.time() - age  # the chat list is newest first, by the file's time
    os.utime(log._path(key), (stamp, stamp))


def _indexed(log: ConversationLog) -> None:
    ss.reindex_all(log)
    ss.INDEXER.check()


def test_a_word_said_past_the_index_ceiling_is_found_while_typing(log) -> None:
    _chat(log, "chat-short", "the marmalade order is late", age=300)
    _chat(log, "chat-long", _FILLER, "and the marmalade crates for the market", age=100)
    _indexed(log)
    assert ss.INDEXER.partial(["chat-short", "chat-long"]) == ["chat-long"], "premise"

    answer = ss.search("marmalade", log=log)

    assert {hit["key"] for hit in answer.hits} == {"chat-short", "chat-long"}
    assert answer.complete and (answer.searched, answer.of) == (2, 2)
    assert answer.matched == 2
    assert answer.source == "index+scan"


def test_the_direct_read_stops_at_its_bound_and_the_answer_says_what_it_left(
    log, monkeypatch
) -> None:
    _chat(log, "chat-older", _FILLER, "the marmalade ledger", age=300)
    _chat(log, "chat-newer", _FILLER, "the marmalade crates", age=100)
    _chat(log, "chat-short", "marmalade, briefly", age=50)
    _indexed(log)
    one = log._path("chat-newer").stat().st_size
    monkeypatch.setattr(ss, "LONG_READ_BYTES", one + one // 2)

    answer = ss.search("marmalade", log=log)

    # The newest long chat is read whole; the older one only as far as the index keeps.
    assert {hit["key"] for hit in answer.hits} == {"chat-short", "chat-newer"}
    assert not answer.complete and (answer.searched, answer.of) == (2, 3)
    assert answer.index is not None and answer.index["long"] == 2
    # And the rest, asked for, reads it too.
    rest = ss.search("marmalade", log=log, rest=True)
    assert rest.complete and {hit["key"] for hit in rest.hits} == {
        "chat-short",
        "chat-newer",
        "chat-older",
    }


def test_a_long_chat_is_read_once_when_the_index_finds_nothing(log) -> None:
    """With no index hit the newest chats are read directly anyway; a long chat already read is
    neither read nor counted twice."""
    _chat(log, "chat-short", "nothing about fruit", age=300)
    _chat(log, "chat-long", _FILLER, "and the marmalade crates for the market", age=100)
    _indexed(log)

    answer = ss.search("marmalade", log=log)

    assert [hit["key"] for hit in answer.hits] == ["chat-long"]
    assert answer.complete and (answer.searched, answer.of) == (2, 2)
    assert answer.matched == 1
    # Found nowhere: the newest chats are read too, and the long one still counts once.
    nowhere = ss.search("kumquat", log=log)
    assert nowhere.hits == [] and (nowhere.searched, nowhere.of) == (2, 2)


@pytest.mark.asyncio
async def test_the_inbound_search_names_one_long_conversation_in_the_singular(
    log, monkeypatch
) -> None:
    from personalclaw.inbound import tools

    _chat(log, "chat-short", "the marmalade order is late", age=300)
    _chat(log, "chat-long", _FILLER, "and the marmalade crates for the market", age=100)
    _indexed(log)
    monkeypatch.setattr(ss, "LONG_READ_BYTES", 0)  # past the bound: the index's reach alone

    text = await tools._sessions_search({"query": "marmalade"}, None)

    assert (
        "Only 1 of 2 conversations were searched whole: the other one is longer than the search "
        "index keeps, so only its beginning was searched."
    ) in text

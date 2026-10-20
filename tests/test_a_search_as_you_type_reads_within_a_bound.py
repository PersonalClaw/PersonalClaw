"""Search as you type reads the newest chats within a byte budget, and says why a chat matched.

When the index finds nothing, or there is none, a search reads the newest chats directly — that is
also what matches inside a word. The chat list and the palette search on every pause in typing,
and that read took the newest 500 chats whole however large they were: every keystroke that
found nothing re-read them all. It now reads the newest of them whole for as long as the next
still fits in ``SCAN_READ_BYTES``, and the answer counts what it read.

A chat found that way listed no passage, while one the index found showed where it was said. A
direct read now cuts the same kind of snippet from the transcript, marked the way the index
marks its own.
"""

from __future__ import annotations

import os
import time
from types import SimpleNamespace

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw import session_search as ss
from personalclaw.history import ConversationLog, match_snippet

_FILLER = "nothing much to report today " * 60


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


def _six(log: ConversationLog) -> list[str]:
    """Six chats of one size, ``chat-5`` the newest; each says "note"."""
    keys = [f"chat-{n}" for n in range(6)]
    for n, key in enumerate(keys):
        _chat(log, key, f"note {n} {_FILLER}", age=600 - n * 10)
    return keys


def _two_fit(log: ConversationLog, monkeypatch) -> None:
    one = log._path("chat-5").stat().st_size
    monkeypatch.setattr(ss, "SCAN_READ_BYTES", 2 * one + one // 2)


def _reads(log: ConversationLog, monkeypatch) -> list[list[str]]:
    """Every set of chats a search reads directly, in the order it reads them."""
    reads: list[list[str]] = []
    real = log.search_sessions

    def _recording(query, limit=50, *, keys=None):
        reads.append(list(keys or []))
        return real(query, limit, keys=keys)

    monkeypatch.setattr(log, "search_sessions", _recording)
    return reads


def test_a_search_that_finds_nothing_reads_the_newest_chats_within_its_budget(log, monkeypatch):
    """🔴 Red before: all six were read, on every keystroke that found nothing."""
    _six(log)
    ss.reindex_all(log)
    ss.INDEXER.check()
    _two_fit(log, monkeypatch)
    reads = _reads(log, monkeypatch)

    answer = ss.search("kumquat", log=log)

    assert answer.hits == []
    assert reads == [["chat-5", "chat-4"]], "the newest two, which fit, and nothing after"
    assert answer.complete, "the index looked in all six"


def test_with_no_index_the_answer_counts_only_the_newest_it_could_read(log, monkeypatch):
    """🔴 Red before: the answer read (and counted) all six whatever their size."""
    _six(log)
    monkeypatch.setattr(ss, "_connect", lambda: None)
    _two_fit(log, monkeypatch)

    answer = ss.search("note", log=log)

    assert (answer.searched, answer.of, answer.complete) == (2, 6, False)
    assert {hit["key"] for hit in answer.hits} == {"chat-5", "chat-4"}
    rest = ss.search("note", log=log, rest=True)
    assert rest.complete and len(rest.hits) == 6, "asked for, the rest is read regardless"


def test_a_chat_a_direct_read_found_says_why_it_matched(log, monkeypatch):
    """🔴 Red before: a direct read's hit carried no snippet."""
    _chat(log, "chat-a", "We ordered the marmalade crates for the Saturday market.", age=100)
    monkeypatch.setattr(ss, "_connect", lambda: None)

    (hit,) = ss.search("marmalade", log=log).hits

    assert hit["snippet"] == "We ordered the <<marmalade>> crates for the Saturday market."


def test_a_match_inside_a_word_the_index_cannot_find_shows_its_passage(log):
    """The index matches words; the direct read matches inside one, and now says where."""
    _chat(log, "chat-a", "The supermarmalades aisle moved to the back.", age=100)
    ss.reindex_all(log)
    ss.INDEXER.check()

    answer = ss.search("marmalade", log=log)

    (hit,) = answer.hits
    assert answer.source == "scan"
    assert hit["snippet"] == "The super<<marmalade>>s aisle moved to the back."


def test_the_passage_is_cut_from_the_text_as_it_was_written():
    assert match_snippet(["nothing here"], "kumquat") == "", "no text says it: no passage"
    # Full case folding, as the read counts matches: "ß" folds to "ss" and the passage keeps "ß".
    assert match_snippet(["Die Straße ist lang"], "strasse") == "Die <<Straße>> ist lang"
    long = "word " * 40 + "the kumquat grove " + "more " * 60
    cut = match_snippet([long], "kumquat")
    assert cut.startswith("…word ") and cut.endswith(" more…") and "the <<kumquat>> grove" in cut
    assert match_snippet(["line one\n\nthe kumquat\nline three"], "kumquat") == (
        "line one the <<kumquat>> line three"
    )


@pytest.mark.asyncio
async def test_a_direct_read_snippet_is_redacted_like_an_index_snippet(log, monkeypatch):
    """A snippet is transcript text leaving through the search, so the route redacts it."""
    from personalclaw.dashboard.handlers.sessions import api_sessions_search

    _chat(log, "chat-a", "the kumquat login is password=pc-fixture-planted-0451 for now", age=100)
    monkeypatch.setattr(ss, "_connect", lambda: None)
    app = web.Application()
    app["state"] = SimpleNamespace(conversation_log=log, session_creating_app=lambda key: "")
    app.router.add_get("/api/sessions/search", api_sessions_search)

    async with TestClient(TestServer(app)) as client:
        body = await (await client.get("/api/sessions/search?q=kumquat")).json()

    (hit,) = body["sessions"]
    assert "<<kumquat>>" in hit["snippet"]
    assert "pc-fixture-planted-0451" not in hit["snippet"]

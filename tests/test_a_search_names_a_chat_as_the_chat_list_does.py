"""A chat search names each chat the way the chat list does.

The index keeps the title a chat's transcript recorded when it was read, and answers with the
chat's key when there was none. Most chats record none: the chat list names them by their first
prompt. So ⌘K listed an untitled chat as ``dashboard_chat-1-1790567681`` beside a chat the same
answer had read directly and named by its first prompt, and a chat renamed since it was indexed
kept its old name until it was read again. A search now names every chat it answers with the
title the chat list shows.
"""

from __future__ import annotations

import pytest

from personalclaw import session_search as ss
from personalclaw.history import ConversationLog


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


def _listed_title(log: ConversationLog, key: str) -> str:
    return next(str(entry["title"]) for entry in log.list_sessions() if entry["key"] == key)


def test_an_untitled_chat_is_named_by_its_first_prompt(log) -> None:
    log.append("dashboard_chat-1", "user", "planning notes: the marmalade order is late")
    ss.reindex_all(log)
    assert _listed_title(log, "dashboard_chat-1") == "planning notes: the marmalade order is late"

    answer = ss.search("marmalade", log=log)

    assert [(hit["key"], hit["title"]) for hit in answer.hits] == [
        ("dashboard_chat-1", "planning notes: the marmalade order is late")
    ]


def test_a_chat_renamed_since_it_was_indexed_answers_with_its_new_name(log) -> None:
    log.append("dashboard_chat-2", "user", "the quince jam recipe")
    log.set_title("dashboard_chat-2", "Jam")
    ss.reindex_all(log)
    log.set_title("dashboard_chat-2", "Quince jam, final")  # the index has not read it again

    answer = ss.search("quince", log=log)

    assert [hit["title"] for hit in answer.hits] == ["Quince jam, final"]


@pytest.mark.asyncio
async def test_the_inbound_search_names_the_chat_by_its_first_prompt(log) -> None:
    from personalclaw.inbound import tools

    log.append("dashboard_chat-1", "user", "planning notes: the marmalade order is late")
    ss.reindex_all(log)

    text = await tools._sessions_search({"query": "marmalade"}, None)

    assert "- dashboard_chat-1: planning notes: the marmalade order is late" in text

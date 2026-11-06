"""Memory › Settings › Consolidate now consolidates the chats it names, or says it cannot.

The button sends `POST /api/memory/consolidate` once for each chat in the chat list, with the
chat's key as the list gives it: the chat's own name (`chat-7`). Its transcript is kept under the
`dashboard:` form of that name (`dashboard:chat-7`). The route used the key as given, so the pass
read a transcript that does not exist, found nothing to consolidate and kept nothing, while the
route had answered ok and the panel read "Consolidation started for 3/3 sessions".

The route now resolves the key to the one the chat's transcript is kept under, as every other
reader of a chat's transcript does (`chat_utils.candidate_history_keys`), and a key no transcript
is kept under is refused, 404, so the panel counts it as not started.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import pytest

from personalclaw.dashboard.handlers import memory as memory_handlers
from personalclaw.history import ConversationLog, HistoryConsolidator


class _Consolidator:
    """The parts of the history consolidator the route uses: it records the keys it is run on."""

    def __init__(self, log: ConversationLog) -> None:
        self._log = log
        self._running: set[str] = set()
        self._tasks: set[Any] = set()
        self.ran: list[str] = []

    def why_nothing_is_kept(self, key: str) -> str:
        return ""

    async def _consolidate(self, key: str, include_history: bool = True) -> None:
        self.ran.append(key)
        self._running.discard(key)

    def _owe_if_unanswered(self, key: str, *, ending: bool) -> None:
        pass


@pytest.fixture
def consolidator(tmp_path, monkeypatch) -> _Consolidator:
    monkeypatch.setattr(memory_handlers, "_is_restricted_session", lambda _state, _req: False)
    log = ConversationLog(tmp_path / "sessions")
    log.init()
    log.append("dashboard:chat-7", "user", "We moved the launch to Thursday.")
    log.append("channel:thread-4242", "user", "The venue is the old library.")
    return _Consolidator(log)


def _ask(consolidator: _Consolidator, key: Any) -> tuple[int, dict]:
    async def _body() -> dict:
        return {"key": key}

    request = SimpleNamespace(app={"state": SimpleNamespace(consolidator=consolidator)})
    request.json = _body

    async def scenario() -> Any:
        response = await memory_handlers.api_memory_consolidate(request)
        await asyncio.gather(*list(consolidator._tasks), return_exceptions=True)
        return response

    response = asyncio.run(scenario())
    return response.status, json.loads(response.text)


def test_a_chat_named_as_the_chat_list_names_it_is_consolidated(consolidator):
    status, body = _ask(consolidator, "chat-7")

    assert status == 200, body
    assert consolidator.ran == ["dashboard:chat-7"]
    assert body["key"] == "dashboard:chat-7"


def test_a_key_already_in_its_kept_form_is_consolidated_as_it_is(consolidator):
    assert _ask(consolidator, "dashboard:chat-7")[0] == 200
    assert _ask(consolidator, "channel:thread-4242")[0] == 200

    assert consolidator.ran == ["dashboard:chat-7", "channel:thread-4242"]


def test_a_key_no_transcript_is_kept_under_is_refused_and_starts_nothing(consolidator):
    status, body = _ask(consolidator, "chat-404")

    assert status == 404, body
    assert body["error"]["code"] == "session_not_found", body
    message = body["error"]["message"]
    assert "chat-404" in message and "nothing to consolidate" in message, body
    assert consolidator.ran == [] and consolidator._running == set()


class _Reads(_Consolidator):
    """The route's consolidator with the history consolidator's own reading of what a transcript
    keeps (`why_nothing_is_kept`): its recorded memory mode, its app, who asked for it."""

    why_nothing_is_kept = HistoryConsolidator.why_nothing_is_kept
    _as_its_work = HistoryConsolidator._as_its_work


@pytest.mark.parametrize("key", ["chat-9", "dashboard:chat-9"])
def test_an_incognito_chat_is_refused_by_its_bare_name_as_by_its_kept_key(consolidator, key):
    log = consolidator._log
    log.append("dashboard:chat-9", "user", "Book the clinic for Tuesday.")
    log.update_metadata("dashboard:chat-9", {"memory_mode": "incognito"})
    reads = _Reads(log)

    status, body = _ask(reads, key)

    assert status == 403, body
    assert reads.ran == [], "nothing of an Incognito chat is consolidated"


@pytest.mark.parametrize("key", ["", "   ", 7, None])
def test_no_key_is_still_a_bad_request(consolidator, key):
    status, _body = _ask(consolidator, key)

    assert status == 400
    assert consolidator.ran == []

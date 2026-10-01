"""A model call that failed is logged as its sentence; a defect keeps its traceback.

Measured over one day of a local-model install: every chat turn whose model timed out logged a
full ``httpx.ReadTimeout`` traceback at ERROR ("Dashboard chat error in session …"), and every
auto-title that timed out behind the chat model logged another ("Auto-title failed … Traceback").
The stack of a model that did not answer holds only the HTTP client's frames — nothing the one
sentence the chat already composes for it does not say — and a log whose tracebacks are mostly
expected conditions is one where a real defect's traceback is not found.

Both surfaces are driven through their real handlers: ``run_chat``'s failure branch and the
auto-title chore.
"""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from personalclaw.dashboard import chat_title
from personalclaw.dashboard.chat_runner import run_chat
from personalclaw.dashboard.state import DashboardState, _ChatSession
from personalclaw.history import ConversationLog

_ENDPOINT = httpx.Request("POST", "http://127.0.0.1:11434/api/chat")


def _slow_start() -> BaseException:
    from personalclaw.guardrails.failure import FirstTokenTimeout

    return FirstTokenTimeout(
        model="gemma4:12b",
        provider="Ollama",
        endpoint="http://127.0.0.1:11434",
        waited_secs=120,
        setting="Request Timeout",
        instance="ollama-bg",
    )


def _records(caplog, name: str) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == name and r.levelno >= logging.INFO]


def _chat_turn_failing_with(tmp_path, caplog, error: BaseException) -> list[logging.LogRecord]:
    """Run a real chat turn whose model call raises ``error``; return the turn's failure lines."""
    sessions = MagicMock(count=0)
    sessions.get_pid = MagicMock(return_value=None)
    sessions.record_failure = AsyncMock()
    sessions.get_or_create = AsyncMock(side_effect=error)
    state = DashboardState(
        sessions=sessions, start_time=0.0, conversation_log=ConversationLog(base_dir=tmp_path)
    )
    state.context_builder = MagicMock()
    state.broadcast_ws = MagicMock()
    state.push_sessions_update = MagicMock()
    state.push_refresh = MagicMock()
    session = _ChatSession("chat-2-test")
    session._titled = True
    with (
        caplog.at_level(logging.DEBUG, logger="personalclaw.dashboard.chat_runner"),
        patch("personalclaw.dashboard.chat_runner.maybe_offer_check_work"),
        patch("personalclaw.dashboard.chat_runner._maybe_followups", new=AsyncMock()),
        patch("personalclaw.dashboard.chat_plan.maybe_submit_plan_draft"),
    ):
        asyncio.run(run_chat(state, session, "hi"))
    return [
        r
        for r in _records(caplog, "personalclaw.dashboard.chat_runner")
        if "chat-2-test" in r.getMessage() and ("failed" in r.msg or "error" in r.msg)
    ]


@pytest.mark.parametrize(
    "failure",
    [
        _slow_start,
        lambda: httpx.ReadTimeout("", request=_ENDPOINT),
        lambda: httpx.ConnectError("[Errno 61] Connection refused", request=_ENDPOINT),
    ],
    ids=["first-token-timeout", "read-timeout", "connect-error"],
)
def test_a_chat_turn_whose_model_failed_is_one_line(tmp_path, caplog, failure):
    (record,) = _chat_turn_failing_with(tmp_path, caplog, failure())

    assert record.levelno == logging.WARNING
    assert record.exc_info is None
    assert record.getMessage().startswith("Chat turn in session chat-2-test failed: ")


def test_a_chat_turn_that_hit_a_defect_keeps_its_traceback(tmp_path, caplog):
    (record,) = _chat_turn_failing_with(
        tmp_path, caplog, AttributeError("'NoneType' object has no attribute 'key'")
    )

    assert record.levelno == logging.ERROR
    assert record.exc_info is not None


class _Session:
    key = "chat-4"
    _titled = False
    blocks_reads = False
    is_restricted = True  # no tag proposal, so no config read
    tags: list[str] = []
    messages = [
        {"role": "user", "content": "Draft my standup for today"},
        {"role": "assistant", "content": "To draft your standup, I need the repositories."},
    ]


def _auto_title_failing_with(monkeypatch, caplog, error: BaseException) -> logging.LogRecord:
    async def _fails(state, session, prompt, **_kw):
        raise error

    monkeypatch.setattr(chat_title, "_stream_chat_chore", _fails)
    with caplog.at_level(logging.DEBUG, logger="personalclaw.dashboard.chat_title"):
        asyncio.run(chat_title._maybe_auto_title(object(), _Session()))
    failures = [
        r for r in _records(caplog, "personalclaw.dashboard.chat_title") if "failed" in r.msg
    ]
    assert len(failures) == 1, [r.getMessage() for r in failures]
    return failures[0]


def test_an_auto_title_whose_model_timed_out_is_one_line(monkeypatch, caplog):
    record = _auto_title_failing_with(monkeypatch, caplog, httpx.ReadTimeout("", request=_ENDPOINT))

    assert record.exc_info is None
    assert record.getMessage() == (
        "Auto-title failed for session chat-4: the model provider at 127.0.0.1:11434 did not "
        "answer in time, so the request timed out; it is asked again once a model answers"
    )


def test_an_auto_title_says_which_model_did_not_start_answering(monkeypatch, caplog):
    record = _auto_title_failing_with(monkeypatch, caplog, _slow_start())

    assert record.exc_info is None
    assert record.getMessage() == (
        "Auto-title failed for session chat-4: gemma4:12b on Ollama at 127.0.0.1:11434 did not "
        "start answering within 120 seconds, so the request was stopped; it is asked again once "
        "a model answers"
    )


def test_an_auto_title_that_hit_a_defect_keeps_its_traceback(monkeypatch, caplog):
    record = _auto_title_failing_with(monkeypatch, caplog, KeyError("title"))

    assert record.exc_info is not None

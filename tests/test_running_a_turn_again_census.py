"""Every door that runs a turn again asks first, through one function.

A door that runs a turn again replaces the attempt the turn made, and the turn asked again is not
handed the calls that attempt finished, so it may make them again: a file written twice, a message
sent twice. Retry asked before it ran such a turn; Regenerate, Rewind, a resend she did not change
and a move to another agent while the turn answered did not. Each now asks through
``repeated_steps.ask_first``, under one rule. This census keeps it so.

* It reads every place in core that runs a turn again (a function that deletes transcript rows and
  starts a turn with ``run_chat``, a caller of the queue's re-send of the turn that just ended,
  ``queue_retry``, and a function handed that re-send, ``send_again``), and fails for one that is
  not named below: a door in :data:`DOORS`, or the gateway's own re-send in :data:`ON_ITS_OWN`.
* Each door calls ``ask_first`` before it deletes or runs anything.
* Each of the gateway's own re-sends (``run_chat``'s ``_send_again()``) sits behind the check that
  the turn made no call, so it never sends one that made a call.
* Each door is driven over a turn that finished a write: it answers with the question (an app,
  with the refusal; a move, with the error that offers Retry) and runs nothing; and over a turn
  that only read, it runs unasked.

What the census cannot see: a turn run again under a name other than these three; and a room's
Continue, which reopens a member's turn the gateway cut off, from the room's own record, which
keeps no call a member made.
"""

from __future__ import annotations

import ast
import asyncio
import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from chat_test_helpers import _api_app, _make_state
from running_a_turn_again_census_shared import _functions, _send_again_calls

from personalclaw.dashboard import running_turn, turn_endings
from personalclaw.dashboard.chat_regenerate import (
    api_chat_session_edit_resend,
    api_chat_session_regenerate,
)
from personalclaw.dashboard.consent_ask import (
    CONSENT_ASK_HEADER,
    CONSENT_ASKED_HEADER,
    consent_ask_middleware,
)
from personalclaw.sel import SecurityEventLog

# ── the census ─────────────────────────────────────────────────────────────────────────────


def test_the_guard_check_sees_an_unguarded_re_send():
    """The positive control: the reading above tells a guarded re-send from a bare one."""
    source = (
        "def run_chat():\n"
        "    if lost and _steps_made():\n"
        "        say()\n"
        "    elif lost:\n"
        "        _send_again()\n"
        "    if busy:\n"
        "        _send_again()\n"
        "    elif unanswered == turn_endings.UNANSWERED_BLANK:\n"
        "        _send_again()\n"
    )
    (func,) = _functions(ast.parse(source)).values()
    assert _send_again_calls(func) == [(5, True), (7, False), (9, True)]


# ── each door, driven ──────────────────────────────────────────────────────────────────────

QUESTION = "Save the plan to notes/plan.md and tell the team."
EARLIER = "First, read me notes/plan.md."
LATER = "Thanks. What is next?"
WRITE = ("write_file", {"path": "notes/plan.md", "content": "Ship on Friday."})
READ = ("read_file", {"path": "notes/plan.md"})


def _call_row(call_id: str, tool: str, args: dict) -> tuple[str, dict]:
    """A finished call as the chat runner keeps it."""
    return tool, {"tool_call_id": call_id, "input": json.dumps(args), "done": True, "output": "ok"}


def _turn(session, text: str, *calls: tuple[str, dict], ends: tuple[str, str]) -> None:
    session.append("user", text)
    for tool, meta in calls:
        session.append("tool", tool, "msg msg-tool", meta=meta)
    role, content = ends
    session.append(role, content, "msg msg-err" if role == "error" else "msg msg-a")


class _Runs:
    """Stands in for the turn engine and records each turn it is asked to run."""

    def __init__(self) -> None:
        self.messages: list[str] = []

    async def __call__(self, _state, _session, message, **_kw):
        self.messages.append(message)


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
    return _make_state(tmp_path)


@pytest.fixture
def runs():
    ran = _Runs()
    with patch("personalclaw.dashboard.chat_regenerate.run_chat", new=ran):
        yield ran


@pytest.fixture
def audit():
    """The audit rows the doors write, wherever they write them from."""
    rows = MagicMock()
    with patch.object(SecurityEventLog, "log_api_access", rows):
        yield rows


def _retry(state, *calls):
    session = state.get_or_create_session("s1")
    _turn(session, QUESTION, *calls, ends=("error", "The reply stopped before it finished."))
    return "regenerate", {}


def _regenerate(state, *calls):
    session = state.get_or_create_session("s1")
    _turn(session, QUESTION, *calls, ends=("assistant", "Saved it and told the team."))
    return "regenerate", {}


def _rewind(state, *calls):
    session = state.get_or_create_session("s1")
    _turn(session, QUESTION, *calls, ends=("assistant", "Saved it and told the team."))
    _turn(session, LATER, ends=("assistant", "The launch checklist."))
    return "edit-resend", {"content": QUESTION, "index": 0, "rewind": True}


def _resend(state, *calls):
    session = state.get_or_create_session("s1")
    _turn(session, EARLIER, ends=("assistant", "It says ship on Friday."))
    _turn(session, QUESTION, *calls, ends=("assistant", "Saved it and told the team."))
    return "edit-resend", {"content": QUESTION, "index": 2}


#: Each door a person uses, as the chat is when she uses it: the turn it runs again made *calls*.
HTTP_DOORS = {"Retry": _retry, "Regenerate": _regenerate, "Rewind": _rewind, "Resend": _resend}


async def _post(state, door: str, body: dict, *, app_name: str = "", headers=None):
    app = _api_app(state)
    if app_name:

        @web.middleware
        async def _as_app(request, handler):
            request["app"] = app_name
            return await handler(request)

        app.middlewares.append(_as_app)
    app.middlewares.append(consent_ask_middleware)
    app.router.add_post("/api/chat/sessions/{session}/regenerate", api_chat_session_regenerate)
    app.router.add_post("/api/chat/sessions/{session}/edit-resend", api_chat_session_edit_resend)
    async with TestClient(TestServer(app)) as client:
        resp = await client.post(f"/api/chat/sessions/s1/{door}", json=body, headers=headers or {})
        data = await resp.json()
        await asyncio.sleep(0)
        return resp, data


@pytest.mark.asyncio
@pytest.mark.parametrize("door", sorted(HTTP_DOORS))
async def test_each_door_asks_before_it_runs_a_turn_that_wrote(door, state, runs, audit):
    path, body = HTTP_DOORS[door](state, _call_row("c1", *WRITE))
    session = state._sessions["s1"]
    before = [dict(m) for m in session.messages]

    resp, data = await _post(state, path, body)

    assert resp.status == 409, data
    assert data["error"]["code"] == "retry_repeats_steps"
    detail = data["error"]["detail"]
    assert detail["title"] == "Run this turn again?"
    assert detail["steps"] == [{"tool": "write_file", "target": "notes/plan.md"}]
    assert runs.messages == [], f"{door} ran the turn again before anyone said yes"
    assert session.messages == before, f"{door} changed the chat before anyone said yes"
    assert [c.kwargs["outcome"] for c in audit.call_args_list] == ["needs_confirm"]

    resp, data = await _post(state, path, {**body, "confirm": detail["confirm"]})

    assert resp.status == 200, data
    assert runs.messages == [QUESTION]


@pytest.mark.asyncio
@pytest.mark.parametrize("door", sorted(HTTP_DOORS))
async def test_each_door_runs_a_turn_that_only_read_unasked(door, state, runs, audit):
    path, body = HTTP_DOORS[door](state, _call_row("c1", *READ))

    resp, data = await _post(state, path, body)

    assert resp.status == 200, data
    assert runs.messages == [QUESTION]


@pytest.mark.asyncio
@pytest.mark.parametrize("door", sorted(HTTP_DOORS))
async def test_each_door_tells_an_app_to_ask_her_from_the_dashboard(door, state, runs, audit):
    path, body = HTTP_DOORS[door](state, _call_row("c1", *WRITE))
    _resp, asked = await _post(state, path, body)

    resp, data = await _post(
        state, path, {**body, "confirm": asked["error"]["detail"]["confirm"]}, app_name="notes-app"
    )

    assert resp.status == 409, data
    assert "detail" not in data["error"], "an app was handed the owner's question"
    assert runs.messages == []


@pytest.mark.asyncio
@pytest.mark.parametrize("door", sorted(HTTP_DOORS))
async def test_each_door_gives_the_page_that_asks_the_question_as_its_answer(
    door, state, runs, audit
):
    path, body = HTTP_DOORS[door](state, _call_row("c1", *WRITE))

    resp, data = await _post(state, path, body, headers={CONSENT_ASK_HEADER: "ask"})

    assert resp.status == 200, data
    assert resp.headers.get(CONSENT_ASKED_HEADER) == "1"
    assert data["error"]["code"] == "retry_repeats_steps"
    assert runs.messages == []


async def _move(state, *calls: tuple[str, dict]) -> tuple[Any, MagicMock]:
    """A chat moved to another model while its turn, which made *calls*, was answering."""
    state.sessions.get_channel_link = MagicMock(return_value=(None, None))
    session = state.get_or_create_session("s1")
    session.append("user", QUESTION)
    for tool, meta in calls:
        session.append("tool", tool, "msg msg-tool", meta=meta)
    session._rebinding = running_turn.Rebinding(fields={"model": "fast-model"})
    send_again = MagicMock()
    row = session.messages[0]
    moved = await running_turn.say_moved(
        state, session, "dashboard:s1", False, send_again, lambda: (row, session.messages[1:])
    )
    assert moved
    return session, send_again


@pytest.mark.asyncio
async def test_a_move_does_not_send_a_turn_that_wrote_again_on_its_own(state):
    session, send_again = await _move(state, _call_row("c1", *WRITE))

    send_again.assert_not_called()
    to = running_turn.answering_label(session, session._rebinding)
    assert session.messages[-1]["role"] == "error", "the chat does not offer Retry on it"
    assert session.messages[-1]["content"] == turn_endings.moved_without_answering_notice(to, 1)


@pytest.mark.asyncio
async def test_a_move_sends_a_turn_that_only_read_again(state):
    session, send_again = await _move(state, _call_row("c1", *READ))

    send_again.assert_called_once_with()
    assert session.messages[-1]["role"] == "notice"

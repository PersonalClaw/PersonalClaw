"""Retry asks first when the attempt it replaces finished steps that may have changed something.

Retry on a turn that ended without its answer (an error, a reply cut short, a restart) runs the turn
again from its message and deletes the attempt it replaces, the calls that attempt finished
included. The turn asked again does not see them, so it may make them again: a file written twice,
a command run twice, a message sent twice. So the regenerate route answers such a Retry with a
question naming those calls and runs nothing until the request comes back with that question's
``confirm``. A turn whose finished calls only read, or that finished none, retries as before.

Whether a call may have changed something is read from what its tool declares, as the approval gate
reads it (``task_modes.reads_only``): the platform's ``read_file`` and a read-only shell command are
reads; ``write_file``, a writing command and a tool that declares nothing are not.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from chat_test_helpers import _make_app, _make_state

from personalclaw.dashboard.consent_ask import (
    CONSENT_ASK_HEADER,
    CONSENT_ASKED_HEADER,
    consent_ask_middleware,
)

QUESTION = "Save the plan to notes/plan.md and tell the team."
CUT_OFF = "The gateway restarted before this reply finished. Send your message again to retry."
URL = "/api/chat/sessions/s1/regenerate"


def _call(call_id: str, tool: str, args: dict, *, done: bool = True, **meta) -> dict:
    """A call row as the chat runner keeps it: the tool's name, its input as text, its result."""
    row = {"tool_call_id": call_id, "input": json.dumps(args), **meta}
    if done:
        row.update(done=True, output="ok")
    return row


def _turn(state, *calls: tuple[str, dict], acp_provider: str = "", rows=()):
    """A chat whose last turn made *calls* and then ended without its answer."""
    session = state.get_or_create_session("s1")
    session.acp_provider = acp_provider
    session.append("user", QUESTION)
    for tool, meta in calls:
        session.append("tool", tool, "msg msg-tool", meta=meta)
    for role, content, cls in rows:
        session.append(role, content, cls)
    session.append("error", CUT_OFF, "msg msg-err")
    session.drain()
    return session


class _Runs:
    """Stands in for the turn engine and records each turn it is asked to run."""

    def __init__(self) -> None:
        self.messages: list[str] = []

    async def __call__(self, _state, _session, message, **_kw):
        self.messages.append(message)


async def _post(state, body=None, *, app_name: str = "", headers=None):
    app = _make_app(state)
    if app_name:

        @web.middleware
        async def _as_app(request, handler):
            request["app"] = app_name
            return await handler(request)

        app.middlewares.append(_as_app)
    app.middlewares.append(consent_ask_middleware)
    async with TestClient(TestServer(app)) as client:
        resp = await client.post(URL, json=body, headers=headers or {})
        data = await resp.json()
        await asyncio.sleep(0)
        return resp, data


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
    """The route's own rows and the question's, which the one function every door that runs a
    turn again asks through writes (``repeated_steps.ask_first``)."""
    log = MagicMock()
    with (
        patch("personalclaw.dashboard.chat_regenerate.sel", return_value=log),
        patch("personalclaw.dashboard.repeated_steps.sel", return_value=log),
    ):
        yield log


def _audited(audit) -> list[dict]:
    return [c.kwargs for c in audit.log_api_access.call_args_list]


@pytest.mark.asyncio
async def test_a_retry_over_a_finished_write_asks_first_and_runs_nothing(state, runs, audit):
    session = _turn(
        state,
        ("write_file", _call("c1", "write_file", {"path": "notes/plan.md", "content": "Ship."})),
    )
    before = [dict(m) for m in session.messages]

    resp, data = await _post(state)

    assert resp.status == 409, data
    error = data["error"]
    assert error["code"] == "retry_repeats_steps"
    detail = error["detail"]
    assert detail["title"] == "Run this turn again?"
    assert detail["steps"] == [{"tool": "write_file", "target": "notes/plan.md"}]
    assert detail["more"] == 0
    assert detail["said"] == (
        "This turn finished 1 step that may have changed something. Running the turn again "
        "replaces this attempt and may repeat it."
    )
    assert "write_file (notes/plan.md)" in error["message"]
    assert detail["confirm"]
    assert runs.messages == [], "the turn ran again before anyone said yes"
    assert session.messages == before, "the attempt was deleted before anyone said yes"
    assert _audited(audit) == [
        {
            "caller": "dashboard",
            "operation": "chat.retry_failed_turn",
            "outcome": "needs_confirm",
            "source": "dashboard",
            "resources": "s1",
            "metadata": {"repeats": ["write_file (notes/plan.md)"]},
        }
    ]

    resp, data = await _post(state, {"confirm": detail["confirm"]})

    assert resp.status == 200, data
    assert runs.messages == [QUESTION]
    assert [m["role"] for m in session.messages] == ["user"], "the yes deleted other rows"
    assert _audited(audit)[-1]["outcome"] == "allowed"
    assert _audited(audit)[-1]["metadata"] == {
        "confirmed": True,
        "repeats": ["write_file (notes/plan.md)"],
    }


@pytest.mark.asyncio
async def test_a_turn_that_only_read_retries_unasked(state, runs, audit):
    session = _turn(
        state,
        ("read_file", _call("c1", "read_file", {"path": "notes/plan.md"})),
        ("bash", _call("c2", "bash", {"command": "ls -la notes"})),
    )

    resp, data = await _post(state)

    assert resp.status == 200, data
    assert runs.messages == [QUESTION]
    assert [m["role"] for m in session.messages] == ["user"]
    assert not _audited(audit)[-1].get("metadata"), "a retry nobody was asked about says it was"


@pytest.mark.asyncio
async def test_a_turn_that_finished_no_call_retries_unasked(state, runs, audit):
    # A write that was still running when the turn ended, and one her Deny refused.
    denied = json.dumps({"request_id": "c2", "tool_call_id": "c2", "resolved": "rejected"})
    _turn(
        state,
        ("write_file", _call("c1", "write_file", {"path": "a.md", "content": "x"}, done=False)),
        ("write_file", _call("c2", "write_file", {"path": "b.md", "content": "x"})),
        rows=[("permission", "write_file", denied)],
    )

    resp, data = await _post(state)

    assert resp.status == 200, data
    assert runs.messages == [QUESTION]


@pytest.mark.asyncio
async def test_a_call_whose_effect_is_unknown_counts_as_a_change(state, runs, audit):
    _turn(
        state,
        ("read_file", _call("c1", "read_file", {"path": "notes/plan.md"})),
        ("team_post", _call("c2", "team_post", {"channel": "#plans", "text": "Shipped."})),
    )

    resp, data = await _post(state)

    assert resp.status == 409, data
    assert data["error"]["detail"]["steps"] == [{"tool": "team_post", "target": "#plans"}]
    assert runs.messages == []


@pytest.mark.asyncio
async def test_a_command_that_writes_is_a_change_and_a_cut_input_still_names_its_target(
    state, runs, audit
):
    long_write = json.dumps({"path": "notes/long.md", "content": "x" * 5000})[:4000]
    _turn(
        state,
        ("bash", _call("c1", "bash", {"command": "git push origin main"})),
        ("write_file", {"tool_call_id": "c2", "input": long_write, "done": True}),
    )

    resp, data = await _post(state)

    assert resp.status == 409, data
    assert data["error"]["detail"]["steps"] == [
        {"tool": "bash", "target": "git push origin main"},
        {"tool": "write_file", "target": "notes/long.md"},
    ]
    assert data["error"]["detail"]["said"].startswith("This turn finished 2 steps that may have")


@pytest.mark.asyncio
async def test_a_call_that_failed_still_counts_and_is_said_to_have_failed(state, runs, audit):
    # Whether a failed call ran before it failed is not kept, so it may have changed something.
    _turn(
        state,
        ("bash", _call("c1", "bash", {"command": "make deploy"}, ok=False)),
    )

    resp, data = await _post(state)

    assert resp.status == 409, data
    assert data["error"]["detail"]["steps"] == [
        {"tool": "bash", "target": "make deploy", "failed": True}
    ]


@pytest.mark.asyncio
async def test_an_agent_cli_s_own_tool_declares_nothing_so_it_counts_as_a_change(
    state, runs, audit
):
    _turn(
        state,
        ("Terminal", _call("c1", "Terminal", {"command": "ls"}, kind="execute")),
        ("Read", _call("c2", "Read", {"file_path": "notes/plan.md"}, kind="read")),
        acp_provider="acp:claude-code",
    )

    resp, data = await _post(state)

    assert resp.status == 409, data
    assert data["error"]["detail"]["steps"] == [{"tool": "Read", "target": "notes/plan.md"}]


@pytest.mark.asyncio
async def test_a_yes_to_other_steps_is_asked_again(state, runs, audit):
    _turn(state, ("write_file", _call("c1", "write_file", {"path": "a.md", "content": "x"})))

    resp, data = await _post(state, {"confirm": "0" * 32})

    assert resp.status == 409, data
    assert data["error"]["detail"]["confirm"] != "0" * 32
    assert runs.messages == []


@pytest.mark.asyncio
async def test_an_app_is_told_to_retry_from_the_dashboard(state, runs, audit):
    session = _turn(
        state, ("write_file", _call("c1", "write_file", {"path": "a.md", "content": "x"}))
    )
    before = [dict(m) for m in session.messages]
    _resp, asked = await _post(state)
    confirm = asked["error"]["detail"]["confirm"]

    resp, data = await _post(state, {"confirm": confirm}, app_name="notes-helper")

    assert resp.status == 409, data
    assert data["error"]["code"] == "retry_repeats_steps"
    assert data["error"]["message"].endswith(
        "It was not run: retry it from the dashboard, where you can confirm it."
    )
    assert "detail" not in data["error"], "an app was handed the owner's question"
    assert runs.messages == []
    assert session.messages == before
    assert _audited(audit)[-1]["caller"] == "app:notes-helper"
    assert _audited(audit)[-1]["outcome"] == "refused"


@pytest.mark.asyncio
async def test_the_page_that_asks_gets_the_question_rather_than_a_failure(state, runs, audit):
    _turn(state, ("write_file", _call("c1", "write_file", {"path": "a.md", "content": "x"})))

    resp, data = await _post(state, headers={CONSENT_ASK_HEADER: "ask"})

    assert resp.status == 200, data
    assert resp.headers.get(CONSENT_ASKED_HEADER) == "1"
    assert data["error"]["code"] == "retry_repeats_steps"
    assert runs.messages == []

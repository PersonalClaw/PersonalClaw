"""An agent's question to its owner reaches her, and her answer reaches the call waiting on it.

The chat runner broadcast a ``question_card`` frame for a call titled ``AskUserQuestion`` and said
"the agent is already paused on the tool call awaiting the reply". Nothing rendered the frame and
nothing could carry a reply: no route took an answer, no runtime waited on one, and no runtime's
call is even titled that (Claude Code's adapter titles it with the question's own words, and only
offers the tool to a client that can answer it; PersonalClaw's own runtime had no question tool).
So an agent could not put a question to her, and the card promised something nothing did.

Now the question is put through one registry (``owner_questions``): a card in the chat that asked,
a "Needs you" row in the Inbox (which Mission Control's Your turn lane lists), her answer delivered
once to the waiting call, a second answer or a late one refused with a sentence, and her Stop
withdrawing it on every surface. A runtime's own question tool PersonalClaw cannot answer is shown,
saying so, and its turn goes on.

Driven with a real dashboard state over a real session manager, a live Inbox store, the real routes
and scripted runtimes: no agent CLI and no model runs.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock

import pytest
from aiohttp.test_utils import TestClient, TestServer
from chat_test_helpers import _api_app
from test_an_agent_cli_asks_its_question_over_acp import ONE_QUESTION
from test_ended_owner_ends_its_approvals import _until, world  # noqa: F401 - a fixture

from personalclaw import mcp_core
from personalclaw.apps.permissions import ROUTE_AUTHZ, OwnerOnly
from personalclaw.dashboard.chat import api_chat_session_detail, api_chat_session_stop, run_chat
from personalclaw.dashboard.chat_questions import api_chat_question_answer
from personalclaw.inbox import OPEN_STATUSES, InboxItem, ItemKind
from personalclaw.inbox_providers import native_source
from personalclaw.llm.base import (
    EVENT_COMPLETE,
    EVENT_TEXT_CHUNK,
    EVENT_TOOL_CALL,
    EVENT_TOOL_RESULT,
    LLMEvent,
)
from personalclaw.owner_questions import (
    ANSWERED,
    CANCELLED,
    EXPIRED,
    SKIPPED,
    Answer,
    normalize,
)
from personalclaw.tool_providers.ask_user import QuestionToolProvider
from personalclaw.tool_providers.base import is_interactive_tool

CHAT = "chat-trip"
KEY = f"dashboard:{CHAT}"

#: What PersonalClaw's own agent asks, in the tool's shape.
ASK = {
    "questions": [
        {
            "question": "Which database should the new service use?",
            "header": "Database",
            "options": [
                {"label": "Postgres", "description": "Relational, like the rest"},
                {"label": "SQLite", "description": "One file, no server"},
            ],
        }
    ]
}


def _app(state):
    app = _api_app(state)
    app.router.add_get("/api/chat/sessions/{session}", api_chat_session_detail)
    app.router.add_post("/api/chat/sessions/{session}/stop", api_chat_session_stop)
    app.router.add_post(
        "/api/chat/sessions/{session}/questions/{question}/answer", api_chat_question_answer
    )
    return app


def _chat(w, title: str = "Plan the service"):
    session = w.state.get_or_create_session(CHAT)
    session.title = title
    session.agent = "PersonalClaw"
    return session


def _cards(w) -> list[dict]:
    return [d for kind, d in w.frames if kind == "question_card"]


def _resolutions(w) -> list[dict]:
    return [d for kind, d in w.frames if kind == "question_resolved"]


def _rows(w, qid: str) -> list[InboxItem]:
    return [i for i in w.inbox.items.values() if i.refs.get("question") == qid]


async def _asked(w, questions=None, **kw) -> tuple[asyncio.Task, dict]:
    """Ask on the registry, as a runtime's call does, and return the waiting call and its card."""
    call = asyncio.ensure_future(
        w.state.owner_questions.ask(
            CHAT, questions or normalize(ASK), asked_by="PersonalClaw in “Plan the service”", **kw
        )
    )
    await _until(lambda: _cards(w), "the card")
    return call, _cards(w)[-1]


async def _post(state, path: str, body: Any) -> tuple[int, dict]:
    async with TestClient(TestServer(_app(state))) as client:
        resp = await client.post(path, json=body)
        return resp.status, await resp.json()


def _answer_path(qid: str) -> str:
    return f"/api/chat/sessions/{CHAT}/questions/{qid}/answer"


# ── her question reaches her ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_question_reaches_the_chat_as_a_card_with_its_options(world):  # noqa: F811
    w = world
    _chat(w)
    call, card = await _asked(w)
    assert card["session"] == CHAT and card["answerable"] is True
    [q] = card["questions"]
    assert q["question"] == "Which database should the new service use?"
    assert q["header"] == "Database"
    assert q["free_text"] is True and q["multiSelect"] is False
    assert q["options"] == [
        {"label": "Postgres", "description": "Relational, like the rest"},
        {"label": "SQLite", "description": "One file, no server"},
    ]
    # …and where she looks when she is not on that chat: a "Needs you" row naming who asks.
    [row] = _rows(w, card["id"])
    assert row.item_kind == ItemKind.NEEDS_INPUT.value and row.status in OPEN_STATUSES
    assert row.refs["session"] == CHAT
    assert row.message.startswith("Which database should the new service use?")
    assert "PersonalClaw in “Plan the service” is waiting for your answer" in row.message
    # The turn waits on her, so its clock stops; a page opened now shows the card.
    assert w.state.waiting_on_owner(CHAT)
    async with TestClient(TestServer(_app(w.state))) as client:
        detail = await (await client.get(f"/api/chat/sessions/{CHAT}")).json()
    assert [c["id"] for c in detail["pending_questions"]] == [card["id"]]
    call.cancel()


@pytest.mark.asyncio
async def test_her_answer_reaches_the_waiting_call_once(world):  # noqa: F811
    w = world
    _chat(w)
    call, card = await _asked(w)
    status, body = await _post(
        w.state, _answer_path(card["id"]), {"answers": [{"selected": [1], "other": ""}]}
    )
    assert (status, body) == (200, {"ok": True, "outcome": ANSWERED})
    outcome = await asyncio.wait_for(call, 5)
    assert outcome.kind == ANSWERED and outcome.answers == (Answer((1,), ""),)
    [resolved] = _resolutions(w)
    assert resolved["outcome"] == ANSWERED and resolved["answers"] == [
        {"selected": [1], "other": ""}
    ]
    assert [r.status for r in _rows(w, card["id"])] == ["handled"]
    assert not w.state.waiting_on_owner(CHAT)
    # A second answer is refused, saying why.
    status, body = await _post(w.state, _answer_path(card["id"]), {"skip": True})
    assert status == 409
    assert body["error"]["code"] == "question_answered"
    assert body["error"]["message"] == "This question has already been answered."


@pytest.mark.asyncio
async def test_her_stop_withdraws_it_everywhere_and_a_late_answer_is_refused(world):  # noqa: F811
    w = world
    _chat(w)
    call, card = await _asked(w)
    await w.state.sessions.stop_turn(KEY)
    outcome = await asyncio.wait_for(call, 5)
    assert outcome.kind == CANCELLED and outcome.ended == "its turn was stopped"
    [resolved] = _resolutions(w)
    assert resolved["outcome"] == CANCELLED and resolved["ended"] == "its turn was stopped"
    [row] = _rows(w, card["id"])
    assert row.status == "expired" and row.refs["ended"] == "its turn was stopped"
    assert w.state.owner_questions.pending_for(CHAT) == []
    status, body = await _post(w.state, _answer_path(card["id"]), {"answers": [{"selected": [0]}]})
    assert status == 409 and body["error"]["code"] == "question_ended"
    assert body["error"]["message"] == (
        "The agent is no longer waiting for this answer: its turn was stopped."
    )


@pytest.mark.asyncio
async def test_her_skip_is_delivered_as_one(world):  # noqa: F811
    w = world
    _chat(w)
    call, card = await _asked(w)
    assert (await _post(w.state, _answer_path(card["id"]), {"skip": True}))[0] == 200
    assert (await asyncio.wait_for(call, 5)).kind == SKIPPED
    assert [r.status for r in _rows(w, card["id"])] == ["handled"]


@pytest.mark.asyncio
async def test_nobody_answering_in_her_window_withdraws_it(world, monkeypatch):  # noqa: F811
    w = world
    _chat(w)
    monkeypatch.setattr(w.state, "approval_window_secs", lambda: 0.2)
    call, card = await _asked(w)
    outcome = await asyncio.wait_for(call, 5)
    assert outcome.kind == EXPIRED and outcome.ended.startswith("nobody answered within")
    assert [r.status for r in _rows(w, card["id"])] == ["expired"]
    assert _resolutions(w)[0]["outcome"] == EXPIRED


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body, why",
    [
        ({"answers": []}, "Send one answer for each of the 1 questions."),
        ({"answers": [{"selected": [2]}]}, "Answer 1 chooses an option question 1 does not offer."),
        ({"answers": [{"selected": [0, 1]}]}, "Question 1 takes one choice."),
        (
            {"answers": [{"selected": [], "other": "  "}]},
            "Question 1 has no answer. Choose an option, or skip the questions.",
        ),
        (
            {"answers": [{"selected": ["0"]}]},
            "Answer 1 names its options by their place in the list.",
        ),
        (
            {"answers": [{"selected": [0], "other": "x" * 2001}]},
            "Answer 1 is longer than 2000 characters.",
        ),
    ],
)
async def test_an_answer_the_question_cannot_take_is_refused(world, body, why):  # noqa: F811
    w = world
    _chat(w)
    call, card = await _asked(w)
    status, got = await _post(w.state, _answer_path(card["id"]), body)
    assert status == 400 and got["error"] == {"code": "question_answer_invalid", "message": why}
    assert not call.done(), "a refused answer must leave the question waiting"
    call.cancel()


@pytest.mark.asyncio
async def test_an_answer_for_a_question_this_chat_never_asked_is_not_found(world):  # noqa: F811
    w = world
    _chat(w)
    status, body = await _post(w.state, _answer_path("nope"), {"skip": True})
    assert status == 404 and body["error"]["code"] == "question_not_found"


def test_no_app_may_answer_her_question():
    row = ROUTE_AUTHZ["POST /api/chat/sessions/{session}/questions/{question}/answer"]
    assert isinstance(row, OwnerOnly)


@pytest.mark.asyncio
async def test_every_string_the_agent_wrote_is_masked_on_every_surface(world):  # noqa: F811
    w = world
    _chat(w)
    secret = "AKIAIOSFODNN7EXAMPLE"
    asked = normalize(
        {
            "questions": [
                {
                    "question": f"Use the key {secret}?",
                    "header": secret,
                    "options": [{"label": secret, "description": f"the {secret} key"}, "No"],
                }
            ]
        }
    )
    call, card = await _asked(w, asked)
    assert secret not in json.dumps(card)
    assert secret not in _rows(w, card["id"])[0].message
    # The call itself is told her answer in the words it asked with.
    await _post(w.state, _answer_path(card["id"]), {"answers": [{"selected": [0]}]})
    outcome = await asyncio.wait_for(call, 5)
    assert asked[0]["options"][outcome.answers[0].selected[0]]["label"] == secret


def test_only_the_owner_of_an_open_chat_is_asked(world):  # noqa: F811
    w = world
    session = _chat(w)
    registry = w.state.owner_questions
    assert registry.cannot_ask(KEY) == ""
    assert registry.cannot_ask("subagent:abc") == "this work is not a chat the user has open"
    session._channel_linked = True
    assert "chat channel" in registry.cannot_ask(KEY)
    session._channel_linked = False
    session._unattended = True
    assert "runs on its own" in registry.cannot_ask(KEY)


def test_a_restart_closes_the_rows_of_questions_nobody_can_answer_now(world):  # noqa: F811
    w = world
    from personalclaw.inbox import GATEWAY_RESTARTED, emit_attention_item

    emit_attention_item(
        w.state,
        source="system",
        kind="agent_request",
        item_kind=ItemKind.NEEDS_INPUT.value,
        title="Which database?",
        refs={"question": "from-last-run", "session": CHAT},
    )
    assert w.state.owner_questions.close_orphaned_rows() == 1
    [row] = _rows(w, "from-last-run")
    assert row.status == "expired" and row.refs["ended"] == GATEWAY_RESTARTED


# ── an agent CLI's question, through a real chat turn ────────────────────────────────────────


#: The question tool's own call, as Claude Code's adapter shows it: titled with the question's
#: words, its input the questions the form then asks (``ONE_QUESTION``).
QUESTION_CALL = {
    "questions": [
        {
            "question": "Which database should the service use?",
            "header": "Database",
            "multiSelect": False,
            "options": [
                {"label": "Postgres", "description": "Relational"},
                {"label": "SQLite", "description": ""},
            ],
        }
    ]
}


class _AgentCli:
    """An agent CLI's session as the chat runner drives it: it shows its question tool's call,
    asks the question through whoever the chat armed, says what it was told, and its tool's
    result lands, as the call it ran with no permission request."""

    provider_id = "acp:claude-code"
    asks_through_elicitation = True
    #: The call that asks: its title, kind and input.
    call = ("Which database should the service use?", "other", QUESTION_CALL)

    def __init__(self) -> None:
        self.handler = None
        self.told: list[dict] = []

    def set_question_handler(self, handler) -> None:
        self.handler = handler

    def context_usage_pct(self) -> float | None:
        return None

    def __getattr__(self, name: str):
        return AsyncMock()

    async def stream(self, _message: str):
        title, kind, call_input = self.call
        yield LLMEvent(
            kind=EVENT_TOOL_CALL,
            title=title,
            tool_kind=kind,
            tool_call_id="toolu_01",
            tool_input=json.dumps(call_input),
            tool_input_obj=call_input,
        )
        assert self.handler is not None, "nobody was armed to answer the agent's question"
        result = await self.handler(dict(ONE_QUESTION))
        self.told.append(result)
        yield LLMEvent(
            kind=EVENT_TOOL_RESULT, tool_call_id="toolu_01", tool_output="User has answered."
        )
        yield LLMEvent(kind=EVENT_TEXT_CHUNK, text=f"Told {json.dumps(result)}")
        yield LLMEvent(kind=EVENT_COMPLETE, stop_reason="end_turn")

    stream_command = stream


@pytest.mark.asyncio
async def test_an_agent_clis_question_is_answered_from_the_card(world):  # noqa: F811
    w = world
    session = _chat(w)
    cli = _AgentCli()
    w.state.sessions.get_or_create = AsyncMock(return_value=(cli, True, False))
    turn = session.task = asyncio.ensure_future(run_chat(w.state, session, "set up the service"))
    await _until(lambda: _cards(w), "the card")
    [card] = _cards(w)
    assert card["tool_call_id"] == "toolu_01"
    assert card["questions"][0]["header"] == "Database"
    assert "Claude Code in “Plan the service”" in _rows(w, card["id"])[0].message
    status, _ = await _post(w.state, _answer_path(card["id"]), {"answers": [{"selected": [1]}]})
    assert status == 200
    await asyncio.wait_for(turn, 10)
    assert cli.told == [{"action": "accept", "content": {"question_0": "SQLite"}}]
    # The call's row keeps how it went, so a reload shows the card she answered.
    [row] = [m for m in session.messages if (m.get("meta") or {}).get("tool_call_id") == "toolu_01"]
    assert row["meta"]["question"]["outcome"] == ANSWERED
    assert row["meta"]["question"]["answers"] == [{"selected": [1], "other": ""}]


def _call_row(session, call_id: str = "toolu_01") -> dict:
    [row] = [m for m in session.messages if (m.get("meta") or {}).get("tool_call_id") == call_id]
    return row


def _unasked_lines(w) -> list[str]:
    return [
        d.get("text", "")
        for kind, d in w.frames
        if kind == "activity_event" and "without host approval" in d.get("text", "")
    ]


async def _answered_in_plan_mode(w, cli) -> Any:
    session = _chat(w)
    session._task_mode = "plan"
    w.state.sessions.get_or_create = AsyncMock(return_value=(cli, True, False))
    turn = session.task = asyncio.ensure_future(run_chat(w.state, session, "plan the service"))
    await _until(lambda: _cards(w), "the card")
    await _post(w.state, _answer_path(_cards(w)[0]["id"]), {"answers": [{"selected": [1]}]})
    await asyncio.wait_for(turn, 10)
    return session


@pytest.mark.asyncio
async def test_a_question_she_answered_is_its_calls_gate_not_a_call_it_ran_unasked(
    world,  # noqa: F811
):
    # Its card was the call's gate. Read as a call the agent ran without asking her, it was marked
    # so on its row, audited "ungated", and in Plan mode it stopped the turn her answer was for.
    w = world
    session = await _answered_in_plan_mode(w, _AgentCli())
    assert "ungated" not in _call_row(session)["meta"]
    assert _unasked_lines(w) == []
    assert [e for e in w.audit if e.outcome == "ungated"] == []
    assert any(str(m.get("content", "")).startswith("Told ") for m in session.messages)


class _AnotherToolAsking(_AgentCli):
    """A call of another tool whose form, asked for it, is shaped like a question."""

    call = ("deploy", "execute", {"environment": "staging"})


@pytest.mark.asyncio
async def test_a_question_asked_for_another_tools_call_does_not_excuse_it(world):  # noqa: F811
    w = world
    session = await _answered_in_plan_mode(w, _AnotherToolAsking())
    assert "ran without asking you" in _call_row(session)["meta"]["ungated"].lower()
    assert [e.outcome for e in w.audit if e.outcome == "ungated"] == ["ungated"]


@pytest.mark.asyncio
async def test_a_stop_while_the_agent_cli_waits_ends_its_question(world):  # noqa: F811
    w = world
    session = _chat(w)
    cli = _AgentCli()
    w.state.sessions.get_or_create = AsyncMock(return_value=(cli, True, False))
    turn = session.task = asyncio.ensure_future(run_chat(w.state, session, "set up the service"))
    await _until(lambda: _cards(w), "the card")
    async with TestClient(TestServer(_app(w.state))) as client:
        assert (await client.post(f"/api/chat/sessions/{CHAT}/stop")).status == 200
    await asyncio.wait_for(turn, 10)
    assert cli.told == [{"action": "cancel"}]
    assert _resolutions(w)[0]["outcome"] == CANCELLED


@pytest.mark.asyncio
async def test_a_turn_that_runs_on_its_own_arms_nobody(world):  # noqa: F811
    w = world
    session = _chat(w)
    session._unattended = True
    cli = _AgentCli()
    w.state.sessions.get_or_create = AsyncMock(return_value=(cli, True, False))
    await run_chat(w.state, session, "set up the service")
    # The armed handler is what put the question to her; an unattended turn has none, so the
    # agent's session answers its question `cancel` itself (`acp/session.py`).
    assert cli.handler is None
    assert _cards(w) == []


class _CliWithItsOwnQuestionTool(_AgentCli):
    """A runtime whose own question tool answers itself: nothing is put to PersonalClaw."""

    provider_id = "acp:codex"
    asks_through_elicitation = False

    async def stream(self, _message: str):
        yield LLMEvent(
            kind=EVENT_TOOL_CALL,
            title="request_user_input",
            tool_call_id="call_7",
            tool_input=json.dumps(ASK),
            tool_input_obj=ASK,
        )
        yield LLMEvent(kind=EVENT_TEXT_CHUNK, text="I will assume Postgres.")
        yield LLMEvent(kind=EVENT_COMPLETE, stop_reason="end_turn")

    stream_command = stream


@pytest.mark.asyncio
async def test_a_question_she_cannot_answer_here_is_shown_saying_so(world):  # noqa: F811
    w = world
    session = _chat(w)
    w.state.sessions.get_or_create = AsyncMock(
        return_value=(_CliWithItsOwnQuestionTool(), True, False)
    )
    await asyncio.wait_for(run_chat(w.state, session, "set up the service"), 10)
    [card] = _cards(w)
    assert card["answerable"] is False
    assert card["questions"][0]["question"] == "Which database should the new service use?"
    assert "cannot send an answer to" in card["note"]
    assert "Answer in your next message" in card["note"]
    # Nothing waits on it, and the turn went on.
    assert w.state.owner_questions.pending_for(CHAT) == []
    assert any(m.get("content") == "I will assume Postgres." for m in session.messages)
    [row] = [m for m in session.messages if (m.get("meta") or {}).get("tool_call_id") == "call_7"]
    assert row["meta"]["question"]["outcome"] == "unanswerable"


# ── PersonalClaw's own agent ─────────────────────────────────────────────────────────────────


class _NativeTurn:
    """PersonalClaw's own runtime, as the chat runner drives it: it shows its ``ask_user`` call,
    then runs the call in the same task with the chat's session key bound, as ``_invoke`` does."""

    provider_id = "native"

    def __init__(self) -> None:
        self.result = None

    def context_usage_pct(self) -> float | None:
        return None

    def __getattr__(self, name: str):
        return AsyncMock()

    async def stream(self, _message: str):
        yield LLMEvent(
            kind=EVENT_TOOL_CALL, title="ask_user", tool_call_id="call-9", tool_input=ASK
        )
        token = mcp_core.set_current_session_key(KEY)
        try:
            self.result = await QuestionToolProvider().invoke("ask_user", ASK)
        finally:
            mcp_core.reset_current_session_key(token)
        yield LLMEvent(kind=EVENT_TEXT_CHUNK, text="Going with it.")
        yield LLMEvent(kind=EVENT_COMPLETE, stop_reason="end_turn")

    stream_command = stream


@pytest.fixture
def dashboard(world, monkeypatch):  # noqa: F811
    """The world, as the process-wide dashboard state a tool reaches outside a request."""
    monkeypatch.setattr(native_source, "_dashboard_state", world.state)
    return world


@pytest.mark.asyncio
async def test_her_own_agents_question_is_put_to_her_and_answered(dashboard):
    w = dashboard
    session = _chat(w)
    native = _NativeTurn()
    w.state.sessions.get_or_create = AsyncMock(return_value=(native, True, False))
    turn = session.task = asyncio.ensure_future(run_chat(w.state, session, "set up the service"))
    await _until(lambda: _cards(w), "the card")
    [card] = _cards(w)
    assert card["tool_call_id"] == "call-9", "the question attaches to the call that asked it"
    await _post(
        w.state, _answer_path(card["id"]), {"answers": [{"selected": [0], "other": "with backups"}]}
    )
    await asyncio.wait_for(turn, 10)
    assert native.result.success is True
    assert native.result.output == (
        "The user answered:\n"
        '1. Which database should the new service use? → Postgres; and wrote "with backups"'
    )


@pytest.mark.asyncio
async def test_her_own_agent_is_told_to_ask_in_its_reply_where_nobody_can_answer(dashboard):
    _chat(dashboard)
    token = mcp_core.set_current_session_key("subagent:research-1")
    try:
        result = await QuestionToolProvider().invoke("ask_user", ASK)
    finally:
        mcp_core.reset_current_session_key(token)
    assert result.success is False
    assert (
        result.error
        == "Nobody can answer a question here: this work is not a chat the user has open."
    )
    assert result.recovery_hints == ["Ask the question in your reply instead."]
    assert _cards(dashboard) == []


@pytest.mark.asyncio
async def test_her_own_agents_question_is_left_out_of_work_nobody_watches():
    [tool] = await QuestionToolProvider().list_tools()
    assert tool.name == "ask_user"
    assert is_interactive_tool(tool), "an unattended run's toolset must leave it out"
    # Asking changes nothing of hers: no approval, and Ask and Plan mode let it run.
    assert tool.requires_approval is False and tool.risk_level.value == "safe"

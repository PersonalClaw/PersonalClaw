"""An agent CLI's question to the user travels over ACP's form elicitation, and is answered.

Claude Code's ACP adapter turns its ``AskUserQuestion`` tool on only for a client that advertises
``clientCapabilities.elicitation.form``, and then asks each question with ``elicitation/create``
and waits for the answer. PersonalClaw advertised nothing, so the tool was off; and the turn loop
classified an ``elicitation/create`` as nothing to do, so an agent that asked anyway waited on an
answer that never came until the turn's two-hour deadline. Now:

* a chat's own attended session of a backend that asks this way is told it may (an unattended
  one, or any other host's, is not: nobody there answers, and the backend keeps its tool off);
* the question is answered by whoever the chat armed, and with nobody armed it is answered
  ``cancel`` at once, never left waiting;
* a Stop answers a question still waiting ``cancel``, once;
* any other request the client does not serve is refused at once (method not found).

Driven by a scripted session queue and stub senders: no agent CLI runs.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from personalclaw.acp.client import AcpClient
from personalclaw.acp.dialect import ClaudeCodeDialect, CodexDialect, DefaultDialect
from personalclaw.acp.elicitation import form_response, questions_from_form
from personalclaw.acp.session import AcpSession
from personalclaw.acp.types import AcpPromptStats, JsonRpcMessage
from personalclaw.constants import JSONRPC_METHOD_NOT_FOUND
from personalclaw.owner_questions import ANSWERED, CANCELLED, SKIPPED, Answer, Outcome

#: The request Claude Code's adapter sends for one AskUserQuestion question with two options.
ONE_QUESTION = {
    "sessionId": "A",
    "mode": "form",
    "toolCallId": "toolu_01",
    "message": "Which database should the service use?",
    "requestedSchema": {
        "type": "object",
        "properties": {
            "question_0": {
                "type": "string",
                "title": "Database",
                "oneOf": [
                    {"const": "Postgres", "title": "Postgres", "description": "Relational"},
                    {"const": "SQLite", "title": "SQLite"},
                ],
            },
            "question_0_custom": {
                "type": "string",
                "title": "Other",
                "description": "Type your own answer instead of choosing an option above.",
                "_meta": {
                    "_askUserQuestionCustomAnswer": {
                        "questionId": "question_0",
                        "isCustomAnswer": True,
                    }
                },
            },
        },
    },
}

#: Two questions, the second taking several choices.
TWO_QUESTIONS = {
    "sessionId": "A",
    "mode": "form",
    "toolCallId": "toolu_02",
    "message": "Please answer the following questions.",
    "requestedSchema": {
        "type": "object",
        "properties": {
            "question_0": {
                "type": "string",
                "title": "Region",
                "description": "Where should it run?",
                "oneOf": [{"const": "EU", "title": "EU"}, {"const": "US", "title": "US"}],
            },
            "question_0_custom": {"type": "string", "title": "Other"},
            "question_1": {
                "type": "array",
                "title": "Features",
                "description": "Which features ship first?",
                "items": {
                    "anyOf": [
                        {"const": "Auth", "title": "Auth"},
                        {"const": "Billing", "title": "Billing"},
                        {"const": "Search", "title": "Search"},
                    ]
                },
            },
            "question_1_custom": {"type": "string", "title": "Other"},
        },
    },
}


# ── the handshake ─────────────────────────────────────────────────────────────────────────────


def _conn():
    conn = MagicMock()
    conn.initialize = AsyncMock(return_value={})
    conn.agent_capabilities = {}
    sess = MagicMock()
    sess.session_id = "sess-1"
    sess.last_prompt_stats = AcpPromptStats()
    sess._last_stop_reason = ""
    conn.new_session = AsyncMock(return_value=sess)
    conn.load_session = AsyncMock(return_value=None)
    conn.last_session_new_snapshot = {"sessionId": "sess-1", "modes": {}}
    conn.send_request = AsyncMock(return_value=(1, MagicMock()))
    conn.drain_init_notifications = AsyncMock()
    return conn


async def _initialize(
    tmp_path, dialect, *, unattended=False, session_key: str | None = "dashboard:chat-1"
) -> tuple[AcpClient, dict]:
    client = AcpClient(
        work_dir=tmp_path, dialect=dialect, unattended=unattended, session_key=session_key
    )
    conn = _conn()
    client._connection = conn
    await client._initialize_session()
    return client, conn.initialize.call_args.args[0]


@pytest.mark.asyncio
async def test_an_attended_claude_code_session_is_told_it_may_ask(tmp_path):
    client, params = await _initialize(tmp_path, ClaudeCodeDialect())
    assert params["clientCapabilities"] == {"elicitation": {"form": {}}}
    assert client.asks_through_elicitation is True


@pytest.mark.asyncio
async def test_an_unattended_session_is_not(tmp_path):
    client, params = await _initialize(tmp_path, ClaudeCodeDialect(), unattended=True)
    assert "clientCapabilities" not in params
    assert client.asks_through_elicitation is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "key",
    [None, "subagent:research-1", "workflow:run-7", "cron:daily", "dashboard:inbound:mail-1"],
)
async def test_a_session_no_chat_card_answers_for_is_not(tmp_path, key):
    # Only a chat arms an answer; told it may ask anywhere else, the agent would ask and be
    # refused at once instead of keeping its question tool off.
    client, params = await _initialize(tmp_path, ClaudeCodeDialect(), session_key=key)
    assert "clientCapabilities" not in params
    assert client.asks_through_elicitation is False


@pytest.mark.asyncio
@pytest.mark.parametrize("dialect", [DefaultDialect(), CodexDialect()])
async def test_a_backend_whose_bridge_was_never_read_is_not_told(tmp_path, dialect):
    client, params = await _initialize(tmp_path, dialect)
    assert "clientCapabilities" not in params
    assert client.asks_through_elicitation is False


# ── the turn loop ─────────────────────────────────────────────────────────────────────────────


class _Wire:
    """A session over a scripted queue: what it answered, refused and cancelled."""

    def __init__(self) -> None:
        self.queue: asyncio.Queue[JsonRpcMessage] = asyncio.Queue()
        self.responses: list[tuple[object, dict]] = []
        self.errors: list[tuple[object, int, str]] = []
        self.prompt: asyncio.Future = asyncio.get_running_loop().create_future()

        async def send_request(method, params):
            return 900, self.prompt

        async def send_response(req_id, result):
            self.responses.append((req_id, result))

        async def send_error(req_id, code, message):
            self.errors.append((req_id, code, message))

        async def cancel_session():
            return None

        self.session = AcpSession(
            "A",
            self.queue,
            send_request=send_request,
            send_response=send_response,
            cancel_session=cancel_session,
            is_process_alive=lambda: True,
            dialect=ClaudeCodeDialect(),
            send_error=send_error,
        )

    def ask(self, req_id: int, params: dict) -> None:
        self.queue.put_nowait(
            JsonRpcMessage(id=req_id, method="elicitation/create", params=dict(params))
        )

    def end_turn(self) -> None:
        self.prompt.set_result(JsonRpcMessage(id=900, result={"stopReason": "end_turn"}))

    async def run(self) -> list:
        return [ev async for ev in self.session.stream_events("go", timeout=10)]


async def _until(predicate, timeout=5.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("timed out")
        await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_the_armed_chat_answers_the_question_and_the_agent_gets_it():
    wire = _Wire()
    asked: list[dict] = []

    async def chat(params: dict) -> dict:
        asked.append(params)
        return {"action": "accept", "content": {"question_0": "Postgres"}}

    wire.session.set_question_handler(chat)
    wire.ask(7, ONE_QUESTION)
    turn = asyncio.ensure_future(wire.run())
    await _until(lambda: wire.responses)
    wire.end_turn()
    await asyncio.wait_for(turn, 5)
    assert asked and asked[0]["message"] == "Which database should the service use?"
    assert wire.responses == [(7, {"action": "accept", "content": {"question_0": "Postgres"}})]


@pytest.mark.asyncio
async def test_with_nobody_armed_the_question_is_cancelled_at_once_not_left_waiting():
    wire = _Wire()
    wire.ask(7, ONE_QUESTION)
    turn = asyncio.ensure_future(wire.run())
    await _until(lambda: wire.responses, timeout=2)
    wire.end_turn()
    await asyncio.wait_for(turn, 5)
    assert wire.responses == [(7, {"action": "cancel"})]


@pytest.mark.asyncio
async def test_a_stop_answers_a_waiting_question_cancel_once():
    wire = _Wire()
    waiting = asyncio.Event()
    release = asyncio.Event()

    async def chat(params: dict) -> dict:
        waiting.set()
        await release.wait()
        return {"action": "cancel"}

    wire.session.set_question_handler(chat)
    wire.ask(7, ONE_QUESTION)
    turn = asyncio.ensure_future(wire.run())
    await asyncio.wait_for(waiting.wait(), 5)
    await wire.session.cancel()
    assert wire.responses == [(7, {"action": "cancel"})]
    release.set()
    wire.prompt.set_result(JsonRpcMessage(id=900, result={"stopReason": "cancelled"}))
    await asyncio.wait_for(turn, 5)
    assert wire.responses == [(7, {"action": "cancel"})], "answered twice"


@pytest.mark.asyncio
async def test_a_question_the_last_turn_left_is_cancelled_before_the_next():
    wire = _Wire()
    owed = asyncio.get_running_loop().create_future()
    owed.set_result(JsonRpcMessage(id=800, result={"stopReason": "cancelled"}))
    wire.session._owed_answer = owed
    wire.ask(6, ONE_QUESTION)
    assert await wire.session.settle_owed_answer()
    assert wire.responses == [(6, {"action": "cancel"})]


@pytest.mark.asyncio
async def test_a_request_the_client_does_not_serve_is_refused_at_once():
    wire = _Wire()
    wire.queue.put_nowait(
        JsonRpcMessage(id=9, method="fs/read_text_file", params={"sessionId": "A", "path": "x"})
    )
    turn = asyncio.ensure_future(wire.run())
    await _until(lambda: wire.errors, timeout=2)
    wire.end_turn()
    await asyncio.wait_for(turn, 5)
    assert wire.errors and wire.errors[0][:2] == (9, JSONRPC_METHOD_NOT_FOUND)


# ── the form ──────────────────────────────────────────────────────────────────────────────────


def test_the_adapters_form_reads_as_the_question_it_asks():
    [q] = questions_from_form(ONE_QUESTION)
    assert q["question"] == "Which database should the service use?"
    assert q["header"] == "Database"
    assert q["multiSelect"] is False and q["free_text"] is True
    assert [(o["label"], o["description"]) for o in q["options"]] == [
        ("Postgres", "Relational"),
        ("SQLite", ""),
    ]


def test_several_questions_read_each_with_its_own_words():
    first, second = questions_from_form(TWO_QUESTIONS)
    assert first["question"] == "Where should it run?" and first["multiSelect"] is False
    assert second["question"] == "Which features ship first?" and second["multiSelect"] is True
    assert [o["label"] for o in second["options"]] == ["Auth", "Billing", "Search"]


def test_her_answers_become_the_forms_content():
    questions = questions_from_form(TWO_QUESTIONS)
    answered = Outcome(ANSWERED, (Answer((1,)), Answer((0, 2), "and audit logs")))
    assert form_response(questions, answered) == {
        "action": "accept",
        "content": {
            "question_0": "US",
            "question_1": ["Auth", "Search"],
            "question_1_custom": "and audit logs",
        },
    }
    assert form_response(questions, Outcome(SKIPPED)) == {"action": "decline"}
    assert form_response(questions, Outcome(CANCELLED, ended="its turn was stopped")) == {
        "action": "cancel"
    }


@pytest.mark.parametrize(
    "params",
    [
        # a tool server's own form: not a question tool's
        {
            "mode": "form",
            "message": "Sign in",
            "requestedSchema": {"type": "object", "properties": {"email": {"type": "string"}}},
        },
        # a model switch the CLI asks about
        {
            "mode": "form",
            "message": "Retry with another model?",
            "requestedSchema": {
                "type": "object",
                "properties": {
                    "choice": {"type": "string", "oneOf": [{"const": "retry_fallback"}]}
                },
            },
        },
        # a question beside a field that is no question's
        {
            "mode": "form",
            "message": "Pick",
            "requestedSchema": {
                "type": "object",
                "properties": {
                    "question_0": {"type": "string", "oneOf": [{"const": "a"}]},
                    "token": {"type": "string"},
                },
            },
        },
        {"mode": "url", "message": "Open this", "url": "https://example.com/x"},
    ],
)
def test_any_other_form_is_not_a_question_put_to_her(params):
    assert questions_from_form(params) is None

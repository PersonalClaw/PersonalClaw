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
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from chat_test_helpers import _api_app, _make_state

import personalclaw
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

SRC = Path(personalclaw.__file__).resolve().parent

#: The doors that run a turn again, by (file under ``src/personalclaw``, function), with what the
#: owner presses (or does) to use each.
DOORS: dict[tuple[str, str], str] = {
    ("dashboard/chat_regenerate.py", "api_chat_session_regenerate"): (
        "Retry on a turn that ended without its answer, and Regenerate on an answer"
    ),
    ("dashboard/chat_regenerate.py", "api_chat_session_edit_resend"): (
        "Rewind to here, and Edit & resend with her message unchanged"
    ),
    ("dashboard/running_turn.py", "say_moved"): (
        "changing the agent, its CLI, its model or its effort while the turn answers"
    ),
}

#: The gateway's own re-send of a turn: after a lost connection, an empty reply or an agent that
#: stayed busy. Nobody presses anything, so it sends only a turn that made no call.
ON_ITS_OWN: dict[tuple[str, str], str] = {
    ("dashboard/chat_runner.py", "run_chat._send_again"): (
        "the turn that just ended, queued to run again as the same turn"
    ),
}

#: How many places in ``run_chat`` call ``_send_again()``: the floor that keeps the guard check
#: below from passing over a tree it no longer reads.
SEND_AGAIN_CALLS = 5

_STARTS_A_TURN = "run_chat"
_QUEUES_IT_AGAIN = "queue_retry"
_HANDED_THE_RE_SEND = "send_again"


# ── the census ─────────────────────────────────────────────────────────────────────────────


def _functions(tree: ast.AST) -> dict[str, ast.AST]:
    """Every function in *tree* by its qualified name (``Class.method.inner``)."""
    found: dict[str, ast.AST] = {}

    def visit(node: ast.AST, scope: list[str]) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                name = [*scope, child.name]
                if not isinstance(child, ast.ClassDef):
                    found[".".join(name)] = child
                visit(child, name)
            else:
                visit(child, scope)

    visit(tree, [])
    return found


def _own_nodes(func: ast.AST) -> list[ast.AST]:
    """The nodes of *func*'s own body: a function defined inside it is its own entry."""
    out: list[ast.AST] = []
    stack = list(ast.iter_child_nodes(func))
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        out.append(node)
        stack.extend(ast.iter_child_nodes(node))
    return out


def _called(node: ast.AST) -> str:
    if not isinstance(node, ast.Call):
        return ""
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _deletes_transcript_rows(nodes: list[ast.AST]) -> bool:
    """A ``del <...>.messages[...]``: the rows a turn wrote, taken out of the transcript."""
    return any(
        isinstance(node, ast.Delete)
        and any(
            isinstance(t, ast.Subscript)
            and isinstance(t.value, ast.Attribute)
            and t.value.attr == "messages"
            for t in node.targets
        )
        for node in nodes
    )


def _parameters(func: ast.AST) -> set[str]:
    args = getattr(func, "args", None)
    if args is None:
        return set()
    return {a.arg for a in [*args.posonlyargs, *args.args, *args.kwonlyargs]}


def _runs_a_turn_again(func: ast.AST) -> bool:
    nodes = _own_nodes(func)
    called = {_called(n) for n in nodes}
    return (
        (_STARTS_A_TURN in called and _deletes_transcript_rows(nodes))
        or _QUEUES_IT_AGAIN in called
        or (_HANDED_THE_RE_SEND in called and _HANDED_THE_RE_SEND in _parameters(func))
    )


def _reruns_in_the_tree() -> set[tuple[str, str]]:
    """Every function in core that runs a turn again. A file that names none of the three ways a
    turn is run again cannot hold one, so only the files that do are parsed."""
    names = (_STARTS_A_TURN, _QUEUES_IT_AGAIN, _HANDED_THE_RE_SEND)
    found: set[tuple[str, str]] = set()
    for path in sorted(SRC.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        if not any(name in text for name in names):
            continue
        rel = path.relative_to(SRC).as_posix()
        for qualified, func in _functions(ast.parse(text)).items():
            if _runs_a_turn_again(func):
                found.add((rel, qualified))
    return found


def _function(key: tuple[str, str]) -> ast.AST:
    rel, qualified = key
    return _functions(ast.parse((SRC / rel).read_text(encoding="utf-8")))[qualified]


def test_every_place_that_runs_a_turn_again_is_named():
    found = _reruns_in_the_tree()
    assert found, "the census found nothing that runs a turn again, so it reads nothing"
    named = set(DOORS) | set(ON_ITS_OWN)
    assert not found - named, (
        f"these run a turn again and are not in the census: {sorted(found - named)}. A door "
        "that runs a turn again asks first through `repeated_steps.ask_first`; name it in DOORS "
        "once it does."
    )
    assert not named - found, f"named here but no longer runs a turn again: {sorted(named - found)}"


@pytest.mark.parametrize("door", sorted(DOORS), ids=lambda d: d[1])
def test_each_door_asks_through_the_one_function_before_it_runs_anything(door):
    func = _function(door)
    nodes = _own_nodes(func)
    asks = [n.lineno for n in nodes if _called(n) == "ask_first"]
    assert asks, f"{door[1]} runs a turn again without asking through `ask_first`"
    acts = [
        n.lineno
        for n in nodes
        if _called(n) in (_STARTS_A_TURN, _HANDED_THE_RE_SEND) or isinstance(n, ast.Delete)
    ]
    assert acts, f"{door[1]} no longer runs anything: the census reads the wrong function"
    assert min(asks) < min(acts), f"{door[1]} deletes or runs something before it asks"


def _send_again_calls(func: ast.AST) -> list[tuple[int, bool]]:
    """Each ``_send_again()`` call in *func*, and whether it sits behind the check that the turn
    made no call: a branch of an if-chain after one that tests ``_steps_made()``, or one that
    only an empty turn (``UNANSWERED_BLANK``, a turn that ran nothing) reaches."""

    def mentions(test: ast.expr, name: str) -> bool:
        return any(
            (isinstance(n, ast.Name) and n.id == name)
            or (isinstance(n, ast.Attribute) and n.attr == name)
            for n in ast.walk(test)
        )

    found: list[tuple[int, bool]] = []

    def visit(node: ast.AST, earlier: list[ast.expr], own: list[ast.expr]) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            return
        if isinstance(node, ast.If):
            visit(node.test, earlier, own)
            for child in node.body:
                visit(child, earlier, [*own, node.test])
            for child in node.orelse:
                visit(child, [*earlier, node.test], own)
            return
        if _called(node) == "_send_again":
            guarded = any(mentions(t, "_steps_made") for t in earlier) or any(
                mentions(t, "UNANSWERED_BLANK") for t in own
            )
            found.append((node.lineno, guarded))
        for child in ast.iter_child_nodes(node):
            visit(child, earlier, own)

    for child in ast.iter_child_nodes(func):
        visit(child, [], [])
    return found


def test_the_gateway_re_sends_only_a_turn_that_made_no_call():
    calls = _send_again_calls(_function(("dashboard/chat_runner.py", "run_chat")))
    assert len(calls) == SEND_AGAIN_CALLS, (
        f"run_chat sends a turn again from {len(calls)} places, not {SEND_AGAIN_CALLS}: read each "
        "new one, and give it the check that the turn made no call"
    )
    unguarded = [line for line, guarded in calls if not guarded]
    assert not unguarded, (
        f"chat_runner.py sends a turn again with no check that it made no call, at lines "
        f"{unguarded}: a turn that made a call is not sent again on its own"
    )


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

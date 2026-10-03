"""A chat's part in an agent's question to its owner (:mod:`personalclaw.owner_questions`).

Three seams, each called from one place:

* :func:`arm` — before a turn streams (``chat_runner.run_chat``): an agent CLI that puts its
  questions to this host over ACP's form elicitation has them answered by the chat's owner,
  when she can be asked there. Otherwise nobody is armed and the session answers ``cancel``
  (``acp/session.py``), so the turn never waits on a question nobody sees.
* :func:`on_tool_call` — at each call the turn shows: a call of PersonalClaw's own ``ask_user``
  is noted so its question attaches to it, and a call of another runtime's own question tool
  that cannot be answered through PersonalClaw is shown, saying so.
* :func:`api_chat_question_answer` — her answer, or her Skip, from the card.
"""

from __future__ import annotations

import inspect
import logging
from typing import TYPE_CHECKING, Any

from aiohttp import web

from personalclaw import owner_questions
from personalclaw.acp.elicitation import CANCEL, form_response, questions_from_form
from personalclaw.dashboard import turn_endings
from personalclaw.http_errors import json_error
from personalclaw.owner_questions import ASK_USER_TOOL, QuestionRefused, normalize
from personalclaw.request_validation import bool_field, json_object_body
from personalclaw.tool_providers.base import names_interactive_tool
from personalclaw.validation import ValidationError

if TYPE_CHECKING:
    from personalclaw.dashboard.state import DashboardState, _ChatSession

logger = logging.getLogger(__name__)


def _asker(session: Any, client: object) -> str:
    """Who is asking: the agent CLI serving the turn, else the chat's own agent, in its chat."""
    return owner_questions.asker(session, turn_endings.serving_agent_name(client))


def arm(state: DashboardState, session: _ChatSession, client: object, *, attended: bool) -> None:
    """Arm (or disarm) who answers the agent CLI's questions on this turn."""
    setter = getattr(client, "set_question_handler", None)
    if not callable(setter) or inspect.iscoroutinefunction(setter):
        return
    handler = None
    if attended and not state.owner_questions.cannot_ask(session.key):
        handler = _answers_for(state, session, client)
    try:
        setter(handler)
    except Exception:
        logger.debug("question handler wiring skipped", exc_info=True)


def _answers_for(state: DashboardState, session: _ChatSession, client: object):
    async def answer(params: dict) -> dict:
        questions = questions_from_form(params)
        if questions is None:
            # A form of any other shape is not a question this host puts to her (a tool
            # server's own form, a model switch): she was never asked, so nothing is claimed.
            logger.info("chat %s: the agent asked for a form that is not a question", session.key)
            return dict(CANCEL)
        outcome = await state.owner_questions.ask(
            session.key,
            questions,
            asked_by=_asker(session, client),
            tool_call_id=str(params.get("toolCallId") or ""),
        )
        return form_response(questions, outcome)

    return answer


def _questions_of(event: Any) -> list[dict[str, Any]] | None:
    """The questions a call's own input asks, when it reads as a question tool's; else None."""
    raw = event.tool_input_obj if event.tool_input_obj is not None else event.tool_input
    try:
        return normalize(raw)
    except ValidationError:
        return None


def on_tool_call(state: DashboardState, session: _ChatSession, client: object, event: Any) -> None:
    """A call the turn shows, read for a question to its owner."""
    title = str(getattr(event, "title", "") or "")
    if title == ASK_USER_TOOL:
        state.owner_questions.note_call(session.key, event.tool_call_id, event.tool_input)
        return
    if getattr(client, "asks_through_elicitation", False):
        on_tool_call_update(state, session, client, event)
        return
    questions = _questions_of(event) if names_interactive_tool(title) else None
    if questions is not None:
        state.owner_questions.show_unanswerable(
            session.key,
            questions,
            asked_by=_asker(session, client),
            tool_call_id=event.tool_call_id,
        )


def on_tool_call_update(
    state: DashboardState, session: _ChatSession, client: object, event: Any
) -> None:
    """A call's arguments, as an agent CLI that asks her through the card shows them (on the
    call, or on a later update): a question tool's are kept, so its question, once asked, reads
    as the call's own gate rather than a call it ran without asking (``OwnerQuestions.was_put``).
    """
    if not getattr(client, "asks_through_elicitation", False):
        return
    questions = _questions_of(event)
    if questions is not None:
        state.owner_questions.note_question_call(session.key, event.tool_call_id, questions)


async def api_chat_question_answer(request: web.Request) -> web.Response:
    """POST /api/chat/sessions/{session}/questions/{question}/answer — answer an agent's question.

    Body ``{"answers": [{"selected": [option index…], "other": "her own words"}]}``, one answer
    per question, or ``{"skip": true}``. Answers the call waiting on it, once: a second answer is
    ``question_answered``, and one after the agent stopped waiting is ``question_ended``, each
    with its sentence.
    """
    state: DashboardState = request.app["state"]
    name = request.match_info["session"]
    if state.get_session(name) is None:
        return json_error("session_not_found", status=404)
    body = await json_object_body(request)
    skip = bool_field(body, "skip", default=False)
    try:
        outcome = state.owner_questions.answer(
            name, request.match_info["question"], answers=body.get("answers"), skip=skip
        )
    except QuestionRefused as exc:
        # Each code a literal, so the wire registry's check can see it.
        if exc.code == "question_not_found":
            return json_error("question_not_found", message=exc.message, status=404)
        if exc.code == "question_answered":
            return json_error("question_answered", message=exc.message, status=409)
        if exc.code == "question_ended":
            return json_error("question_ended", message=exc.message, status=409)
        return json_error("question_answer_invalid", message=exc.message, status=400)
    return web.json_response({"ok": True, "outcome": outcome.kind})

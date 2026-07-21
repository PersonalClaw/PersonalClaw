"""Side-chat handlers — /api/chat/sessions/{session}/side/{open,turn,close}.

A side chat is a non-blocking, tool-less Q&A against a frozen read-only snapshot
of the parent session. Isolation is structural (see side_state.py):
  - side messages live only on ``session._side`` — never ``session.messages``,
    so the persistence/broadcast hook (which fires only inside append()) is
    never triggered by a side turn;
  - the turn runs in a throwaway ``side:{key}`` LLM session, destroyed on close,
    which performs NO memory/lesson consolidation;
  - tools are hard-rejected (ToolApprovalPolicy.REJECT_ALL);
  - deltas stream over a dedicated ``chat.side_result`` WS event the main
    transcript does not consume.
"""

from __future__ import annotations

import asyncio
import logging
import uuid

from aiohttp import web

from personalclaw.dashboard.side_context import build_side_message
from personalclaw.dashboard.side_state import SideState
from personalclaw.dashboard.state import DashboardState
from personalclaw.security import redact_credentials, redact_exfiltration_urls
from personalclaw.sel import sel

logger = logging.getLogger(__name__)

SIDE_RESULT_EVENT = "chat.side_result"
_MAX_SIDE_QUESTION = 8_192


def _redact(text: str) -> str:
    text, _ = redact_exfiltration_urls(text)
    text, _ = redact_credentials(text)
    return text


def _resolve_owned_session(request: web.Request):
    """Return ``(state, name, session, err)`` with app-ownership checked.

    On success ``session`` is set and ``err`` is None; on failure ``session`` is
    None and ``err`` is the error Response to return (404 unknown / 403 not owned).
    """
    state: DashboardState = request.app["state"]
    name = request.match_info["session"]
    session = state._sessions.get(name)
    request_app = request.get("app", "")
    if not session:
        return state, name, None, web.json_response({"error": "not found"}, status=404)
    if request_app:
        if not session._app or session._app != request_app:
            sel().log_api_access(
                caller=request_app,
                operation="chat.side",
                outcome="denied",
                source="app_isolation",
                resources=f"session={name}",
                error="app does not own this session",
            )
            return (
                state,
                name,
                None,
                web.json_response({"error": "app does not own this session"}, status=403),
            )
    return state, name, session, None


async def api_side_open(request: web.Request) -> web.Response:
    """POST /api/chat/sessions/{session}/side/open — open (or reset) the side buffer."""
    state, name, session, err = _resolve_owned_session(request)
    if err is not None:
        return err
    session._side = SideState(open=True)
    return web.json_response({"ok": True, "side": session._side.to_dict()})


async def api_side_close(request: web.Request) -> web.Response:
    """POST /api/chat/sessions/{session}/side/close — drop the buffer + destroy
    the throwaway side session. Nothing is persisted."""
    state, name, session, err = _resolve_owned_session(request)
    if err is not None:
        return err
    session._side = None
    try:
        await state.sessions.destroy(f"side:{name}")
    except Exception:
        logger.debug("side session destroy failed for %s", name, exc_info=True)
    return web.json_response({"ok": True})


async def api_side_turn(request: web.Request) -> web.Response:
    """POST /api/chat/sessions/{session}/side/turn — ask one side question.

    Body: ``{ question: str }``. Fires the turn in the background and returns
    immediately with a run_id; deltas stream over the ``chat.side_result`` WS
    event. Non-blocking: the parent session is untouched.
    """
    state, name, session, err = _resolve_owned_session(request)
    if err is not None:
        return err
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON body"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "body must be a JSON object"}, status=400)
    question = body.get("question")
    if not isinstance(question, str) or not question.strip():
        return web.json_response({"error": "question must be a non-empty string"}, status=400)
    if len(question) > _MAX_SIDE_QUESTION:
        return web.json_response(
            {"error": f"question too long (max {_MAX_SIDE_QUESTION})"}, status=400
        )

    if session._side is None or not session._side.open:
        session._side = SideState(open=True)
    side = session._side
    run_id = uuid.uuid4().hex
    side.last_run_id = run_id
    side.is_complete = False
    side.append("user", question.strip())

    task = asyncio.create_task(_run_side_turn(state, name, session, side, question.strip(), run_id))
    state._background_tasks.add(task)
    task.add_done_callback(state._background_tasks.discard)

    sel().log_api_access(
        caller=request.get("app", "") or "dashboard",
        operation="chat.side_turn",
        outcome="allowed",
        source="dashboard",
        resources=f"session={name},q_len={len(question)}",
    )
    return web.json_response({"ok": True, "run_id": run_id})


async def _run_side_turn(
    state: DashboardState, name: str, session, side: SideState, question: str, run_id: str
) -> None:
    """Drive one side turn in a throwaway session with tools hard-rejected.

    Streams ``chat.side_result`` deltas. A late frame whose run_id no longer
    matches ``side.last_run_id`` (turn superseded or side closed) is dropped.
    """
    from personalclaw.llm_helpers import (
        PromptBusyExhaustedError,
        ToolApprovalPolicy,
        stream_and_collect,
    )

    side_key = f"side:{name}"

    def _emit(delta: str, *, done: bool) -> None:
        # Drop stale frames: the side chat was closed or a newer turn started.
        if session._side is not side or side.last_run_id != run_id:
            return
        state.broadcast_ws(
            SIDE_RESULT_EVENT,
            {"session": name, "run_id": run_id, "delta": _redact(delta), "done": done},
        )

    accumulated = ""
    try:
        prompt = build_side_message(session, side, question)
        provider, _is_new, _resumed = await state.sessions.get_or_create(
            side_key,
            agent=session.agent,
            model=session.model or None,
        )
        try:

            def _on_chunk(text: str) -> None:
                nonlocal accumulated
                accumulated += text
                _emit(text, done=False)

            await stream_and_collect(
                provider,
                prompt,
                approval_policy=ToolApprovalPolicy.REJECT_ALL,
                on_chunk=_on_chunk,
            )
        finally:
            state.sessions.release(side_key)
    except PromptBusyExhaustedError:
        _emit("\n_(side chat interrupted — try again)_", done=False)
    except Exception:
        logger.warning("side turn failed for %s", name, exc_info=True)
        _emit("\n_(side chat error)_", done=False)
    finally:
        # Record the answer on the side buffer (never the parent transcript) and
        # signal completion — only if this run is still the active one.
        if session._side is side and side.last_run_id == run_id:
            side.append("assistant", accumulated)
            side.is_complete = True
            # Persist the side buffer attached to the session so it reloads with
            # it (sidecar meta, not the main transcript). force=True since the
            # side turn doesn't add to session.messages.
            try:
                from personalclaw.dashboard.chat_persistence import _save_session_to_history

                _save_session_to_history(state, session, force=True)
            except Exception:
                logger.debug("side buffer persist failed for %s", name, exc_info=True)
        _emit("", done=True)

"""What a chat's running turn can be asked to do besides finish: take a steer, or move.

**A steer.** A message sent into a running turn reaches it only when the turn's runtime pulls
it in (``SessionManager.set_steer_drains``). The composer offers Steer only while that is so,
and says Queue otherwise, so its words are what the message does (:func:`set_steer_drains`).

**A move.** The chat's agent, its agent CLI, its model and its reasoning effort each decide the
runtime its turns run on, and changing one rebuilds that runtime. Between turns that is all a
change does. During a turn it rebuilt the runtime under the turn, and the turn died with no word
said: its approval card stayed answerable, a steer went to the queue, and her message was never
answered.

So every door that makes such a change (the routing chip, the composer's pickers, any API
caller) comes through :func:`rebind`, under one rule. The change is hers, and it is about the
message the chat is answering. The running turn ends as stopped: a pending approval is answered
cancelled, as a Stop answers it, and its runtime is asked to stop. Her message is then answered
again on the new runtime, as the same turn, and the chat says so where the conversation is. A
turn that had given its answer when the change landed keeps it, and the change applies from her
next message.

The change itself is made when the old turn has ended, never under it, so what the old turn
records about itself (its audit rows, its hooks, its usage) names the runtime it ran on.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from personalclaw.dashboard import turn_endings
from personalclaw.dashboard.chat_utils import (
    _history_key_for,
    _redact_for_display,
    persisted_history_key,
)
from personalclaw.security import redact_credentials, redact_exfiltration_urls
from personalclaw.sel import sel

if TYPE_CHECKING:
    from personalclaw.dashboard.state import DashboardState, _ChatSession

logger = logging.getLogger(__name__)

#: The session fields that say which runtime a turn runs on, and the binding a page adopts.
BINDING_FIELDS = ("agent", "model", "acp_provider", "acp_provider_agent", "reasoning_effort")


@dataclass
class Rebinding:
    """A change to what answers a chat: the session fields it sets, and the meta-line keys it
    persists with them (so the session resumes on it after a restart)."""

    fields: dict[str, Any]
    persisted: dict[str, Any] = field(default_factory=dict)

    def then(self, later: Rebinding) -> Rebinding:
        """This change followed by *later*, as one: a page that changes the agent and the
        effort in one pick sends two requests, and both are hers."""
        return Rebinding({**self.fields, **later.fields}, {**self.persisted, **later.persisted})


class TurnMoved(asyncio.CancelledError):
    """The turn was moved to another runtime before its prompt went out: it asks nothing, and
    ends the way a turn cancelled before its runtime reported a stop reason ends."""


def set_steer_drains(
    state: DashboardState, session: _ChatSession, session_key: str, takes: bool
) -> list[str]:
    """Declare whether the turn running now takes a steer (``SessionManager.set_steer_drains``,
    whose answer it returns), and say so to every page of the chat."""
    stranded = state.sessions.set_steer_drains(session_key, takes)
    session._takes_steers = takes
    state.broadcast_ws("turn_steerable", {"session": session.key, "steerable": takes})
    return stranded


def end_steers(
    state: DashboardState, session: _ChatSession, session_key: str, client: object
) -> None:
    """The turn is over: its runtime pulls no more, so a steer sent from here on queues.

    The buffer is emptied too (a steer aimed at THIS answer must not surface inside an unrelated
    later turn), but what comes back is not discarded, and neither is what an ACP turn pulled but
    could not write to the CLI (a rejected frame, a dead process, the per-turn cap): both feeders
    end on ONE visible path, so "undeliverable" means one thing for every runtime.

    A steer she was told was accepted must not evaporate. It is requeued, which is what
    ``mid_turn_policy: steer`` promises on a runtime that cannot take one ("fall back to queue —
    never drop, never cancel"): the chat's drain takes it as the very next turn, and the
    ``queue_push`` echo puts it in the composer's queue strip, where it can be read and cancelled.
    """
    stranded = list(set_steer_drains(state, session, session_key, False))
    undelivered = getattr(client, "undelivered_steers", None)
    if callable(undelivered):
        try:
            stranded.extend(t for t in (undelivered() or []) if t)
        except Exception:
            logger.debug("undelivered steer read failed", exc_info=True)
    for text in stranded:
        try:
            qid = session.queue_append(text)
        except Exception:
            logger.warning("failed to requeue an undelivered steer", exc_info=True)
            continue
        shown, _ = redact_exfiltration_urls(text)
        shown, _ = redact_credentials(shown)
        state.broadcast_ws(
            "queue_push",
            {
                "session": session.key,
                "content": _redact_for_display(shown),
                "ts": datetime.now(timezone.utc).isoformat(),
                "queue_id": qid,
            },
        )
        # WARNING, not info: the HTTP caller was already told `{"steered": true}`, so this is a
        # promise the turn did not keep. The default gateway log level is WARNING (measured: an
        # isolated run recorded zero INFO lines), and a broken promise nobody can see in the log
        # is the same defect one layer up.
        logger.warning(
            "steer was NOT delivered to the running turn — requeued for the next one "
            "(session=%s)",
            session_key,
        )


def end_if_moved(session: _ChatSession) -> None:
    """Called the moment before a turn's prompt goes out. A move that landed while the turn was
    being put together found nothing on the runtime to stop, so the turn ends here."""
    if session._rebinding is not None:
        raise TurnMoved


async def rebind(state: DashboardState, session: _ChatSession, change: Rebinding) -> bool:
    """Make *change* to what answers *session*. Returns True when it moved a running turn.

    Between turns it is made at once and the runtime is rebuilt for the next turn. During one,
    the turn is stopped and the change waits for its end (:func:`apply_pending_move`).
    """
    key = _history_key_for(session.key)
    if not session.running:
        _set(session, change)
        await state.sessions.reset(key)
        # Announced once the old runtime is gone, so no session is said to run on a runtime it
        # could not be moved to.
        _announce(state, session, change)
        return False
    held = session._rebinding
    session._rebinding = change if held is None else held.then(change)
    # Asked by her: a runtime the stop has to kill ended the turn she moved, not a fault, so the
    # turn neither retries itself nor says it lost its connection.
    session._stop_asked = True
    outcome = await state.sessions.stop_turn(key, force=False, preserve_queue=True)
    if outcome == "idle" and session._stop_state == "idle":
        # Nothing was in flight on the runtime: the turn is before its prompt (it ends there,
        # `end_if_moved`) or past its answer (which stands), and no stop was made of it.
        session._stop_asked = False
    sel().log_tool_invocation(
        session_key=key,
        agent=getattr(session, "agent", "") or "personalclaw",
        source="dashboard",
        tool_name="dashboard_stop",
        tool_kind="command",
        tool_input=None,
        outcome=outcome,
        metadata={"session": session.key, "moved": sorted(change.fields)},
    )
    state.push_sessions_update()
    return True


def say_moved(
    state: DashboardState,
    session: _ChatSession,
    session_key: str,
    answered: bool,
    send_again: Callable[[], None],
) -> bool:
    """End a turn she moved, where the conversation is. False when the turn was not moved.

    An answer the turn gave (*answered*: its runtime ended it with an answer, not a cancel) stands,
    and the change applies from her next message. Otherwise her message is asked again on the new
    runtime as the same turn (*send_again*), once: a retry the turn already queued for itself is
    that one. The chat says which, and its linked channel hears the same line.
    """
    change = session._rebinding
    if change is None:
        return False
    owed = not answered or session._last_turn_errored
    if owed and not (session._queue and session._queue[0].get("retry")):
        send_again()
    to = answering_label(session, change)
    note = (
        turn_endings.moved_turn_notice(to) if owed else turn_endings.moved_after_answer_notice(to)
    )
    session.append("notice", note, "msg msg-notice")
    state.broadcast_ws("chat_message", {"session": session.key, "role": "notice", "content": note})
    told = asyncio.ensure_future(state.tell_linked_channel(session_key, note))
    state._background_tasks.add(told)
    told.add_done_callback(state._background_tasks.discard)
    return True


async def apply_pending_move(
    state: DashboardState, session: _ChatSession, session_key: str
) -> None:
    """Make the change a turn was moved for, now that it has ended and before the chat's next
    turn starts: the old runtime goes, then the change is made. Nothing awaits between the change
    and the caller's next step, so no turn can start on the old binding."""
    if session._rebinding is None:
        return
    try:
        await state.sessions.reset(session_key)
    except Exception:
        logger.warning("Failed to retire the runtime session %s was moved off", session_key)
    # Taken after the wait, so a change made during it is made too.
    change, session._rebinding = session._rebinding, None
    if change is not None:
        _set(session, change)
        _announce(state, session, change)


def answering_label(session: _ChatSession, change: Rebinding) -> str:
    """What answers *session* once *change* is made, as the chat names it: the agent, and the
    model or reasoning effort when the change set one."""
    after = {name: getattr(session, name, "") or "" for name in BINDING_FIELDS}
    after.update({k: v for k, v in change.fields.items() if k in BINDING_FIELDS})
    if after["acp_provider"]:
        from personalclaw.providers.image_input import agent_label

        who = after["acp_provider_agent"] or agent_label(after["acp_provider"])
    else:
        from personalclaw.agents.defaults import DEFAULT_NATIVE_AGENT_NAME

        who = after["agent"] or DEFAULT_NATIVE_AGENT_NAME
    if "model" in change.fields and change.fields["model"] != (session.model or ""):
        who = f"{who} on {after['model'] or 'its default model'}"
    if "reasoning_effort" in change.fields and change.fields["reasoning_effort"] != (
        session.reasoning_effort or ""
    ):
        who = f"{who} at {after['reasoning_effort'] or 'its default'} reasoning effort"
    return who


def _set(session: _ChatSession, change: Rebinding) -> None:
    for name, value in change.fields.items():
        setattr(session, name, value)


def _announce(state: DashboardState, session: _ChatSession, change: Rebinding) -> None:
    """Persist what *session* now runs on and tell every surface that shows it."""
    if change.persisted and state.conversation_log:
        try:
            state.conversation_log.update_metadata(
                persisted_history_key(state.conversation_log, session.key), change.persisted
            )
        except Exception:
            logger.warning(
                "Failed to persist the binding of session %s", session.key, exc_info=True
            )
    state.push_sessions_update()
    # Every open page of the chat shows what now answers it, whoever made the change.
    state.broadcast_ws(
        "session_binding",
        {
            "session": session.key,
            **{name: getattr(session, name, "") or "" for name in BINDING_FIELDS},
        },
    )

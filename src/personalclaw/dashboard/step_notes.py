"""A line the gateway writes about one call of a turn, which the chat shows on that call's card.

A turn's steps are its calls, each drawn from the row that carries the call's id; a ``tool`` row
without one is a line the gateway wrote ABOUT a step, and is no step of its own
(``docs/architecture/chat-sessions.md``). Some of those lines are the only place the chat says what
happened to a call: a gate refused it before it ran (the chat's task mode, the shell denylist, a
hook, an unattended run's bounds, a tool name that does not validate), or it keeps failing the
same way (the loop breaker). So such a line names the call it is about (:data:`ABOUT_CALL`) and
carries its sentence (:data:`NOTE`), and the chat shows the sentence on that call's card: live,
from the row's ``chat_message`` frame, and after a reload, from the row (``foldStepLine`` in
``web/src/pages/chat/chatTypes.ts`` does both). A line about a call the turn shows no card for is
shown on the turn, where it happened.
"""

from __future__ import annotations

from typing import Any

from personalclaw.security import redact_credentials, redact_exfiltration_urls

#: The ``meta`` key naming the call a line is about. Never ``tool_call_id``: that names a call's own
#: row, the one its result, its refined details and its mark on the session map are found by.
ABOUT_CALL = "about_call"

#: The ``meta`` key holding the line's sentence, as the call's card shows it.
NOTE = "note"

#: How the line about a call a gate refused before it ran begins (:func:`note_refusal`): what a
#: reader of the transcript knows a refused call by, as a Retry does (``repeated_steps``).
NOT_RUN = "Not run: "


def _masked(text: str) -> str:
    masked, _ = redact_exfiltration_urls(text)
    masked, _ = redact_credentials(masked)
    return masked


def note_on_call(session: Any, *, call_id: str, title: str, note: str) -> str:
    """Write the line about the call *call_id* (titled *title*) that says *note*; return the line.

    Both are masked as a call's title is where it is shown: a shell call's title is its command,
    and a gate's reason can quote it. A call with no id is said on the turn.
    """
    title, note = _masked(title or "tool"), _masked(note)
    line = f"{title} — {note}"
    session.append(
        "tool",
        line,
        "msg msg-tool",
        meta={NOTE: note, **({ABOUT_CALL: call_id} if call_id else {})},
    )
    return line


def note_refusal(session: Any, event: Any, why: str) -> str:
    """The line for *event*'s call, which a gate refused before it ran: that it did not run, and
    *why*, in the gate's own words."""
    why = why.strip().rstrip(".")
    return note_on_call(
        session,
        call_id=str(getattr(event, "tool_call_id", "") or ""),
        title=str(getattr(event, "title", "") or ""),
        note=f"{NOT_RUN}{why}.",
    )


__all__ = ["ABOUT_CALL", "NOTE", "NOT_RUN", "note_on_call", "note_refusal"]

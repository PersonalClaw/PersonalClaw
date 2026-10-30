"""A stand-in session's door: ``_ChatSession.take_in_at_door`` for a test's fake chat.

A door that puts a message into a chat asks the chat to take it in (``chat_intake.take_in``), so a
test that hands a door a stand-in session gives it this one way in too: ``take_in_at_door =
door_fakes.take_in_at_door`` on a fake class, or :func:`give` on a ``MagicMock``. It does what the
chat does with the stand-in's own methods: queued while the session runs (``queue_append``), else
written as the row its turn starts with (``append``), each given only the arguments it takes.
"""

from __future__ import annotations

import functools
import inspect
from types import SimpleNamespace
from typing import Any

from personalclaw import ids

#: What a door says of a message that the queue keeps on its item.
_QUEUED = ("channel", "files", "source", "asked_for_by", "own_words", "attended")


def _taking(method: Any, **given: Any) -> dict[str, Any]:
    """*given*, less what *method* takes no argument for."""
    try:
        params = inspect.signature(method).parameters
    except (TypeError, ValueError):
        return given
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()):
        return given
    return {k: v for k, v in given.items() if k in params}


def take_in_at_door(
    self: Any, state: Any, message: str, role: str = "user", cls: str = "msg msg-u", **fields: Any
) -> SimpleNamespace:
    message_id = ids.new_id("msg")
    if self.running and not fields.get("starts"):
        kept = {k: fields[k] for k in _QUEUED if fields.get(k) is not None}
        self.queue_append(message, **_taking(self.queue_append, **kept))
        disposition = "queued"
    else:
        shown = fields.get("shown")
        said = {k: fields[k] for k in ("meta", "source") if fields.get(k)}
        self.append(role, message if shown is None else shown, cls, **_taking(self.append, **said))
        disposition = "started"
    return SimpleNamespace(
        session=getattr(self, "key", ""),
        message_id=message_id,
        accepted_at="",
        disposition=disposition,
        turn_message_id=message_id,
        duplicate=False,
    )


def give(session: Any) -> Any:
    """*session*, a ``MagicMock`` stand-in, with the door (:func:`take_in_at_door`)."""
    session.take_in_at_door = functools.partial(take_in_at_door, session)
    return session

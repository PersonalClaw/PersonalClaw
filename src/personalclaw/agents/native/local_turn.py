"""A native turn's place in line for a model on this machine.

A model on this machine serves one request at a time, in the order requests reach it
(``guardrails.local_queue``). A guarded call takes its turn in its guard; a chat's, a room
member's or a code turn's model resolves unguarded, so the native loop takes the turn for it
(:func:`take_local_turn`). Measured without: a chat's reply on a local model sat ten minutes behind
that chat's own title and consolidation, then read as a model that did not start answering in time.
"""

from __future__ import annotations

from typing import Any

from personalclaw.guardrails.local_queue import (
    Attended,
    Turn,
    attending,
    next_entry,
    queue_key,
    take_turn,
)

#: What a chat waiting for its own reply reads in the wait notice ("Your reply is waiting for…").
YOUR_REPLY = "Your reply"


async def take_local_turn(
    model: Any, *, served_ref: str, session_key: str, unattended: bool, next_ref: str
) -> Turn | None:
    """The turn of one inference on *model* (served as *served_ref*) when it runs on this machine
    and no guard takes the turn for it; ``None`` otherwise.

    A turn somebody is waiting for (not *unattended*) goes ahead of every background call, and
    when *next_ref* names a model to move on to it waits at most ``background.busy_model_wait_secs``
    for the call already running, then raises ``LocalModelBusy``, said as what it waited behind. Its
    provider's Request Timeout, which is how long a request may wait to start, bounds the wait.
    """
    if getattr(model, "takes_local_turns", False):
        return None
    entry, _, model_id = served_ref.partition(":")
    key = queue_key(entry, model_id) if entry and model_id else ""
    if not key:
        return None
    who = None if unattended else Attended(YOUR_REPLY, session=session_key)
    wait = getattr(model, "request_timeout_secs", None)
    with attending(who), next_entry(next_ref):
        return await take_turn(
            key, provider=entry, model=model_id, within=float(wait) if wait else None
        )

"""A change to your memory a turn you did not start asks for: held for your own word.

The agent's memory tools reach the gateway over its API (``memory_remember``, ``memory_forget``,
``triage_rules``), naming the session they serve. When someone other than you asked for the turn
that session is running (``memory_writes.asker``: a colleague in a shared channel thread, a
correspondent, a program through the OpenAI-compatible door, and a subagent such a turn started),
the route writes nothing on their say-so and holds the change for your own word instead
(:func:`held`):

* **You are asked through the approval registry itself** (``workflows.owner_allow.ask``): the card
  in the chat that asked, the Inbox, the phone, the channel that chat is on. No standing grant
  answers it (a chat's Trust, YOLO), a Trust or YOLO switch leaves it asking, and only you answer it
  (``approval_answer``).
* **What you allow is written as yours** (``memory_writes.on_the_owners_word``): your Allow is your
  word, so a lesson it keeps is one you taught. A Deny, an ask nobody answered in your approval
  window, and one whose turn was stopped first write nothing.
* **The agent is told at once, in a true sentence**: nothing was written, who asked, and that you
  were asked. It goes on meanwhile: an approval waits as long as your approval window, longer than
  any tool call may run.
* **Nobody to ask, nothing written.** A session that acts on its own (an Unattended loop's) and a
  gateway with nowhere to ask refuse the change, saying why.

Each hold, and how its ask ended, is a security-log row under the route's own operation.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

from aiohttp import web

from personalclaw import memory_writes
from personalclaw.turn_source import fence_source, named
from personalclaw.workflows import owner_allow

logger = logging.getLogger(__name__)

#: The security log's outcome for a change held for your answer: it waits for a person, as a call
#: a deny-list rule asks you about does.
WAITS_FOR_YOU = "needs_human"


def _sel():
    """Late-binding, as the handlers' own, so a test's log is the one written to."""
    import personalclaw.dashboard.handlers as _pkg

    return _pkg.sel()


@dataclass(frozen=True)
class Change:
    """A change to your memory a route was asked for, in the words its hold says it by.

    ``operation`` is the route's security-log operation; ``tool`` the tool that asked, which your
    card names; ``said`` what would change, as the card shows it; ``to_do`` what the agent was asked
    to do ("remember this"); ``ask`` what you are asked, as the agent is told it ("whether to keep
    it, and it is saved"); ``not_yet`` how the agent's answer opens ("Not saved yet"); ``answers``
    what your Allow and your Deny do ("Allow saves it as a lesson of yours, Deny keeps nothing")."""

    operation: str
    tool: str
    said: str
    to_do: str
    ask: str
    not_yet: str
    answers: str


def told(change: Change, who: str) -> str:
    """What the agent is told of a held change: nothing was written, who asked, and that the owner
    was asked."""
    return (
        f"{change.not_yet}: {who} asked for this, and nothing says they are the owner. The owner's "
        f"memory changes only on their own word, so the owner has been asked {change.ask} only if "
        "they allow it."
    )


def purpose(change: Change, who: str) -> str:
    """Why your card asks you: who asked for the change, and what your answers do."""
    return (
        f"{who} asked the agent to {change.to_do}, and nothing says they are you. Your memory "
        f"changes only on your word: {change.answers}"
    )


def held(
    request: web.Request, change: Change, write: Callable[[], Awaitable[None]]
) -> web.Response:
    """Hold *change* for your own word, *write* making it on your Allow, and answer the request
    that asked for it: 202 with what the agent is told. Called where a route would write, after it
    has checked what it was asked for, and only when someone other than you asked for the turn the
    request's work is for (``memory_writes.asker``)."""
    state = request.app["state"]
    session_key = request.headers.get("X-Session-Key", "")
    someone = memory_writes.asker()
    who = named(someone)
    # The chat at the top the work is done for: its card asks you, and its turn's Stop ends the ask.
    chat = (memory_writes.filed_under() or session_key).removeprefix("dashboard:")
    nobody = owner_allow.nobody_to_ask(state, chat)
    if nobody:
        _sel().log_api_access(
            caller=session_key,
            operation=change.operation,
            outcome="denied",
            source="dashboard",
            resources="asked_by_someone_else:nobody_to_ask",
        )
        return web.json_response(
            {
                "error": (
                    f"Nothing was written to the owner's memory: {who} asked for this, and nothing "
                    f"says they are the owner, whose memory changes only on their own word, and "
                    f"{nobody}."
                )
            },
            status=403,
        )
    ask_id = f"{owner_allow.MEMORY_PREFIX}{secrets.token_hex(6)}"
    task = asyncio.ensure_future(
        _ask_then_write(state, ask_id, change, someone, chat, session_key, write)
    )
    tasks = getattr(state, "_background_tasks", None)
    if isinstance(tasks, set):
        tasks.add(task)
        task.add_done_callback(tasks.discard)
    _sel().log_api_access(
        caller=session_key,
        operation=change.operation,
        outcome=WAITS_FOR_YOU,
        source="dashboard",
        resources=f"{ask_id}:{fence_source(someone)}",
    )
    return web.json_response({"held": ask_id, "message": told(change, who)}, status=202)


async def _ask_then_write(
    state: Any,
    ask_id: str,
    change: Change,
    someone: Mapping[str, str],
    chat: str,
    session_key: str,
    write: Callable[[], Awaitable[None]],
) -> None:
    """Ask the owner, then make the change on her Allow. Never raises: nobody awaits it. It runs
    as the work of the request that asked, so what it writes is filed under that chat."""
    decision = await owner_allow.ask(
        state,
        ask_id=ask_id,
        source="agent",
        tool=change.tool,
        purpose=purpose(change, named(someone)),
        said=change.said,
        session=chat,
    )
    if decision:
        outcome = "approved"
    elif decision.outcome in ("expired", "cancelled"):
        outcome = decision.outcome
    else:
        outcome = "rejected"
    try:
        _sel().log_api_access(
            caller=session_key,
            operation=change.operation,
            outcome=outcome,
            source="dashboard",
            resources=f"{ask_id}:{fence_source(someone)}",
        )
    except Exception:  # noqa: BLE001 - the answer stands whether or not its row is written
        logger.debug("security log failed for %s", ask_id, exc_info=True)
    if not decision:
        return
    try:
        with memory_writes.on_the_owners_word():
            await write()
    except Exception:  # noqa: BLE001 - nobody awaits this; the log says what was not written
        logger.warning("the change the owner allowed (%s) was not written", ask_id, exc_info=True)

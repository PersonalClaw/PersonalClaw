"""A create the gateway makes for a chat is made once, however often the chat asks within minutes.

The tool server an agent CLI runs hands the calls that write PersonalClaw's stores to the gateway
(``mcp_core.made_by_the_gateway``) and waits for its answer. When that answer is lost after the call
was made (the wait ran out, the connection dropped, the gateway failed as it answered), the tool
says the call may have been made, and an agent that calls it again made a second, identical
automation, webhook or proposal, which then fired or asked twice.

So ``POST /api/tools/invoke`` remembers each create it made for a chat, by its tool and its
arguments, for :data:`REPEAT_WINDOW_SECS` after it was made, and answers the same call from the same
chat with the first one's answer instead of making it again (:func:`claim`). One still being made is
waited for, so a call repeated while the first runs (its wait ran out first) is not made beside it.
A create that failed is forgotten, so it can be tried again; a second one made on purpose asks for
something different (another name, another time). Only the creates
:data:`personalclaw.mcp_core.MADE_ONCE` names, and only a chat's: a call no chat made (a script's)
is made each time. What is remembered lives in the gateway's memory, so a restart forgets it.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any

#: How long after a create was made the same call from the same chat is answered with it.
REPEAT_WINDOW_SECS = 600.0
#: The most creates remembered; the oldest made ones go first.
_KEPT = 500

#: What a call answered with an earlier one's answer carries in the route's answer.
REPEAT_KEY = "made_earlier"

Key = tuple[str, str, str]


@dataclass
class _Made:
    at: float
    answer: asyncio.Future[dict[str, Any] | None]


_MADE: dict[Key, _Made] = {}


def key_of(caller: str, tool: str, arguments: dict[str, Any]) -> Key | None:
    """The key the call *tool*(*arguments*) from the chat *caller* is remembered by, or ``None``
    for a call that is made each time: not a create, or no chat's."""
    from personalclaw.mcp_core import MADE_ONCE

    if not caller or caller == "internal" or tool not in MADE_ONCE:
        return None
    asked = json.dumps(arguments, sort_keys=True, default=str)
    return caller, tool, hashlib.sha256(asked.encode("utf-8")).hexdigest()


def _forget_old(now: float) -> None:
    """Forget the creates made longer ago than the window, and the oldest past :data:`_KEPT`. One
    still being made is kept, whatever its age."""
    made = [(k, m) for k, m in _MADE.items() if m.answer.done()]
    for key, entry in made:
        if now - entry.at > REPEAT_WINDOW_SECS:
            del _MADE[key]
    surplus = len(_MADE) - _KEPT
    for key, _entry in sorted(made, key=lambda pair: pair[1].at)[: max(surplus, 0)]:
        _MADE.pop(key, None)


async def claim(key: Key) -> dict[str, Any] | None:
    """The answer the same create got earlier from this chat, waited for while it is still being
    made; or ``None`` when this call is the one to make it, which then holds *key* until
    :func:`made` says how it went."""
    while True:
        _forget_old(time.monotonic())
        entry = _MADE.get(key)
        if entry is None:
            _MADE[key] = _Made(time.monotonic(), asyncio.get_running_loop().create_future())
            return None
        answer = await asyncio.shield(entry.answer)
        if answer is not None:
            return answer
        # It failed and was forgotten: this call may make it, unless another took it meanwhile.


def made(key: Key | None, answer: dict[str, Any] | None) -> None:
    """The create *key* holds is over: *answer* is the route's answer when it was made, ``None``
    when it was not (it failed, or never ran), which forgets it."""
    if key is None:
        return
    entry = _MADE.get(key)
    if entry is None:
        return
    if not entry.answer.done():
        entry.answer.set_result(answer)
    if answer is None:
        _MADE.pop(key, None)
    else:
        entry.at = time.monotonic()


def reset_for_tests() -> None:
    _MADE.clear()

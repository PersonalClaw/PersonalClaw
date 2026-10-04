"""A model's answer is finished only when its provider says so.

Every protocol a model adapter reads ends an answer with an event of its own: an OpenAI-compatible
chunk whose choice carries a ``finish_reason``, the Anthropic message's ``stop_reason``, Ollama's
``done`` line, Bedrock's ``messageStop``. A stream can also simply end before that event: a
connection that closed cleanly part way, a body a proxy cut short, a server that stopped writing.
The client libraries end their iteration at the end of the bytes and raise nothing, so read as the
end of the answer, the text so far read as the whole reply, nothing said otherwise, and a tool
call whose arguments never finished was passed on to run.

So every adapter reads its provider's events through :func:`until_terminal`, the one place the rule
lives: a stream that ends before the event that ends its answer was cut off, never complete. It
raises :class:`~personalclaw.guardrails.failure.AnswerCutOff`, naming whose stream it was and the
event that never came, and logs it. The adapter's own reading stops at the raise, so whatever it
emits only once its answer has ended (its tool calls, its terminal ``EVENT_COMPLETE`` and the usage
that rides it) is never emitted, and what had already streamed stays what it was: part of an
answer. A reader that stops before the stream's end (at the terminal event, or any other way out)
closes it, and nothing is judged.

Standard library and ``turn_streams`` only at import, so every adapter, the guard and an app reach
it without pulling the guardrails package in.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Callable
from typing import TypeVar

from personalclaw.turn_streams import closing_stream

logger = logging.getLogger(__name__)

_E = TypeVar("_E")


async def until_terminal(
    events: AsyncIterator[_E],
    *,
    ends: Callable[[_E], bool],
    adapter: str,
    missing: str,
    model: str = "",
) -> AsyncIterator[_E]:
    """Yield *events*, a provider's own stream, and raise ``AnswerCutOff`` when it ends before an
    event *ends* says ends the answer.

    *adapter* names whose stream it is and *missing* the event that ends an answer on it, as the
    log and the turn's record say them ("OpenAI-compatible", "a finish_reason"); *model* is the
    model the request named.
    """
    ended = False
    async with closing_stream(events) as source:
        async for event in source:
            ended = ended or ends(event)
            yield event
    if not ended:
        # Imported here: the guardrails package imports this module, and this one needs the
        # error only for a stream that was cut.
        from personalclaw.guardrails.failure import AnswerCutOff

        cut = AnswerCutOff(adapter=adapter, missing=missing, model=model)
        logger.warning("%s", cut)
        raise cut

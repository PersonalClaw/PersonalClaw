"""The reasoning a model wrote, carried between the requests of one conversation.

Some wires ask that the reasoning a model produced on one request be handed back, unchanged, on
the next request to the same model: between the calls of one tool loop and across turns. That
reasoning is opaque model data, never content. Nothing reads it but the protocol client that
wrote it.

The native loop keeps it as one record per assistant message, under :data:`REASONING_KEY`:
``{"model": <the model id that wrote it>, "blocks": [<the wire's own blocks, as received>]}``. The
record arrives on the provider's terminal ``EVENT_COMPLETE`` (``LLMEvent.reasoning``) and is stored
on the assistant message that inference produced. It is never shown, persisted, searched,
summarised or measured: every reader of a message's text reads ``content`` and ``tool_calls``,
and this key is neither.

Only a provider that declares it reads the record (``ModelProvider.reads_reasoning``) is handed
it: the native loop strips the key from every request to any other provider
(:func:`without_reasoning`). The provider that reads it decides which blocks a request may carry.
It sends back only the blocks its own model wrote, and leaves out any block a model it does not
recognise may refuse, since a request without them is always valid.
"""

from __future__ import annotations

#: Neutral message-dict key carrying the reasoning record on an assistant message.
REASONING_KEY = "_reasoning"


def without_reasoning(messages: list[dict]) -> list[dict]:
    """``messages`` with :data:`REASONING_KEY` removed from every message that carries it.

    A shallow copy of each such message, so the caller's history is never mutated. The same list
    object comes back when no message carries the key.
    """
    if not any(isinstance(m, dict) and REASONING_KEY in m for m in messages):
        return messages
    return [
        (
            {k: v for k, v in m.items() if k != REASONING_KEY}
            if isinstance(m, dict) and REASONING_KEY in m
            else m
        )
        for m in messages
    ]

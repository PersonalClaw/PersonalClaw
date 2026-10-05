"""Vendor-neutral prompt-cache substrate.

The provider-agnostic layer that lets the native loop signal a *cacheable prompt
prefix* without knowing how any concrete provider realizes caching on the wire.
The actual wire translation lives in each provider's OWN adapter (a later change) —
this module deliberately contains ZERO wire strings and no provider SDK import, so
importing it never violates Property 11 (Provider SDK Lazy Import).

The graded :class:`PromptCache` capability mirrors
:class:`~personalclaw.llm.capabilities.StructuredOutput`: a per-request marker is a
GRADED behavior (some families need one, some need none), so it rides on a provider
as its own value rather than a boolean flag.

It also owns the VOLATILE note contract (:data:`VOLATILE_KEY`, :func:`system_note_text`): how
the runtime's per-turn system note reaches a model as an instruction, never as the user's words,
with the cached prefix the same from one turn to the next.
"""

import re
from enum import Enum


class PromptCache(str, Enum):
    """Graded prompt-cache support for a provider.

    A GRADED capability, not a boolean flag — so it rides on
    :class:`~personalclaw.llm.capabilities.ProviderCapability` (declarative twin)
    and on the :class:`~personalclaw.llm.base.ModelProvider` instance (the attr the
    native loop reads), defaulting to :attr:`NONE` on both.

    * ``NONE`` — no caching hint; the message list is handed to the provider
      untouched. The correct, safe default for any provider that has not opted in.
    * ``AUTOMATIC`` — the provider caches a stable prompt PREFIX on its own, with no
      per-request marker to place. The stable-prefix wire ordering (stable
      assembled context leads, the volatile per-turn note rides at the tail) is what
      makes this work; there is nothing for the loop to mark. (OpenAI-family.)
    * ``EXPLICIT`` — the provider needs a per-request cache marker on exactly one
      message, which its OWN adapter translates to that vendor's wire form. The loop
      places a NEUTRAL marker; the adapter consumes it. (Anthropic.)
    """

    NONE = "none"
    AUTOMATIC = "automatic"
    EXPLICIT = "explicit"


#: Neutral message-dict key carrying the cache-prefix hint. A provider adapter that
#: supports :attr:`PromptCache.EXPLICIT` reads whichever message carries this key and
#: translates it to its own wire form (a later change); every other provider ignores it.
CACHE_HINT_KEY = "_cache_hint"

#: Neutral marker the native loop stamps on text it adds for one turn or one request: the turn's
#: tool note (a ``role: "system"`` message whose content changes every turn) and the notes it lays
#: on one request's tail. The cache hint is never anchored on such a message — its content is not
#: part of the stable, cacheable prefix.
#:
#: THE PLACEMENT CONTRACT for a ``role: "system"`` message carrying this key: the model must read
#: it as an instruction from the runtime, never as something the user said, and it must come after
#: every cache checkpoint of the request, so the cached prefix is the same from turn to turn. Its
#: content is already fenced as the runtime's (:func:`system_note_text`); a provider sends it
#: verbatim, and places it by what its wire allows:
#:
#: * a wire that takes a ``system`` message anywhere in the conversation (OpenAI Chat Completions,
#:   Ollama's ``/api/chat``) sends it as that system message, where the loop put it;
#: * a wire whose system prompt is out of band and served first (Anthropic Messages, Bedrock
#:   Converse) must not hoist it there — text that changes every turn at the head of the prompt
#:   means no cached prefix is ever read again — and carries it as ONE text block appended to the
#:   LAST user turn of the request, after that turn's own blocks (tool results included) and after
#:   any cache checkpoint. Never as a user turn of its own: there it is the newest thing the user
#:   said, and a model answers it in the chat.
VOLATILE_KEY = "_volatile"

#: The fence the runtime's per-turn note travels in, and the sentence that opens it: the model
#: reads it as the runtime's, not the user's, and leaves it out of its reply.
_SYSTEM_NOTE_OPEN = (
    "<system-note>\nThe runtime added this note; the user did not write it. Use it where it "
    "applies, and write your reply to the user: never answer or mention the note.\n\n"
)
_SYSTEM_NOTE_CLOSE = "\n</system-note>"
#: A fence tag inside a note's own text, opening or closing, in any case or spacing.
_FENCE_TAG_IN_TEXT = re.compile(r"<(?=\s*/?\s*system-note\b)", re.IGNORECASE)


def system_note_text(note: str) -> str:
    """``note`` fenced as the runtime's: the content of the loop's per-turn system note.

    Fenced at the source rather than by each provider, because the role alone does not carry
    "the runtime said this" to every model: a wire with no system turn inside the conversation
    puts it in a user turn (:data:`VOLATILE_KEY` says where), and so does a chat template with no
    system role, which renders every system message as the user's. Inside the fence it reads as
    the runtime's wherever it lands.

    The note's own text stays inside: a fence tag in it (a tool's description can say anything)
    is written as text, ``&lt;``, so the fence ends where the runtime ended it.
    """
    return f"{_SYSTEM_NOTE_OPEN}{_FENCE_TAG_IN_TEXT.sub('&lt;', note)}{_SYSTEM_NOTE_CLOSE}"


def turn_note_message(note: str) -> dict:
    """The loop's per-turn system note as the message it adds: fenced, and marked volatile."""
    return {"role": "system", "content": system_note_text(note), VOLATILE_KEY: True}


def effective_cache_mode(declared: PromptCache, *, enabled: bool) -> PromptCache:
    """Fold the user's ``agent.prompt_cache_enabled`` switch into ``declared``.

    The switch is the diagnosis escape hatch: when it is
    off, the loop must serve the provider exactly what it served before the marker
    existed. That is expressed by collapsing the mode to :attr:`PromptCache.NONE` —
    the mode that ALREADY means "hand the message list back untouched" — rather than
    by branching around :func:`mark_cacheable_prefix`. One code path, one definition of
    "no marker": disabling the switch takes the same route an undeclared provider takes.

    What the switch does NOT do: it does not revert the wire ordering (stable
    assembled context leads, the volatile per-turn note rides at the tail) or the
    date-line relocation. Those are unconditional correctness repairs, not cache
    features, and forking them into two maintained orderings is exactly the dual path
    the clean-break doctrine forbids.
    """
    return declared if enabled else PromptCache.NONE


def mark_cacheable_prefix(
    messages: list[dict], mode: PromptCache, *, generation: int = 0
) -> list[dict]:
    """Return ``messages`` with a cacheable-prefix hint applied per ``mode``.

    Rules:

    * ``NONE`` / ``AUTOMATIC`` → return ``messages`` UNCHANGED (same object
      identity). ``AUTOMATIC`` needs no marker: the provider caches the stable
      prefix on its own, and the wire ordering already keeps that prefix
      stable across turns.
    * ``EXPLICIT`` → return a NEW list in which exactly ONE message carries
      ``{CACHE_HINT_KEY: {"generation": generation}}``, applied via a SHALLOW COPY
      (``{**msg, CACHE_HINT_KEY: {...}}``). Every other message passes through by
      reference, unchanged. No caller dict is ever mutated.

    Which message gets the hint (deterministic, documented): the LAST message that
    is neither a tool result (``role == "tool"``) nor the volatile per-turn
    note (``_volatile`` key). That message is the trailing boundary of the stable,
    cacheable content — everything up to and including it is worth caching. If no
    such message exists (every message is a tool result or volatile note), the hint
    falls back to ``messages[0]``, the stable head the wire ordering establishes.

    An EXPLICIT-capable provider's adapter consumes whichever message carries
    :data:`CACHE_HINT_KEY`. ``generation`` lets the adapter tell a
    fresh cache prefix from one invalidated by compaction.

    An empty ``messages`` is returned unchanged for every mode.
    """
    if mode is not PromptCache.EXPLICIT:
        # NONE and AUTOMATIC both hand the list back untouched (same object). The
        # byte-identical invariant for an undeclared provider depends on this.
        return messages
    if not messages:
        return messages

    # Pick the hint target: last non-tool, non-volatile message; else the head.
    target = 0
    for i in range(len(messages) - 1, -1, -1):
        msg = messages[i]
        if msg.get("role") == "tool" or msg.get(VOLATILE_KEY):
            continue
        target = i
        break

    out: list[dict] = list(messages)
    # Shallow copy ONLY the hinted message so the caller's dict is never mutated.
    out[target] = {**messages[target], CACHE_HINT_KEY: {"generation": generation}}
    return out

"""Vendor-neutral prompt-cache substrate (PROMPT-CACHE-SUBSTRATE §C1/§C4).

The provider-agnostic layer that lets the native loop signal a *cacheable prompt
prefix* without knowing how any concrete provider realizes caching on the wire.
The actual wire translation lives in each provider's OWN adapter (a later atom) —
this module deliberately contains ZERO wire strings and no provider SDK import, so
importing it never violates Property 11 (Provider SDK Lazy Import).

The graded :class:`PromptCache` capability mirrors
:class:`~personalclaw.llm.capabilities.StructuredOutput`: a per-request marker is a
GRADED behavior (some families need one, some need none), so it rides on a provider
as its own value rather than a boolean flag.
"""

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
      per-request marker to place. The stable-prefix wire ordering (PCS-1 — stable
      assembled context leads, the volatile per-turn note rides at the tail) is what
      makes this work; there is nothing for the loop to mark. (OpenAI-family.)
    * ``EXPLICIT`` — the provider needs a per-request cache marker on exactly one
      message, which its OWN adapter translates to that vendor's wire form (a later
      atom). The loop places a NEUTRAL marker; the adapter consumes it. (Anthropic.)
    """

    NONE = "none"
    AUTOMATIC = "automatic"
    EXPLICIT = "explicit"


#: Neutral message-dict key carrying the cache-prefix hint. A provider adapter that
#: supports :attr:`PromptCache.EXPLICIT` reads whichever message carries this key and
#: translates it to its own wire form (a later atom); every other provider ignores it.
CACHE_HINT_KEY = "_cache_hint"

#: Neutral marker PCS-1 stamps on the per-turn VOLATILE note (a ``role: "system"``
#: message whose content changes every turn). We never anchor the cache hint on that
#: message — its content is not part of the stable, cacheable prefix.
_VOLATILE_HINT_KEY = "_volatile"


def effective_cache_mode(declared: PromptCache, *, enabled: bool) -> PromptCache:
    """Fold the user's ``agent.prompt_cache_enabled`` switch into ``declared``.

    The switch is the diagnosis escape hatch (PROMPT-CACHE-SUBSTRATE §C6): when it is
    off, the loop must serve the provider exactly what it served before the marker
    existed. That is expressed by collapsing the mode to :attr:`PromptCache.NONE` —
    the mode that ALREADY means "hand the message list back untouched" — rather than
    by branching around :func:`mark_cacheable_prefix`. One code path, one definition of
    "no marker": disabling the switch takes the same route an undeclared provider takes.

    What the switch does NOT do: it does not revert the §C2 wire ordering (stable
    assembled context leads, the volatile per-turn note rides at the tail) or the §C3
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
      prefix on its own, and PCS-1 already ordered the prompt so that prefix is
      stable across turns.
    * ``EXPLICIT`` → return a NEW list in which exactly ONE message carries
      ``{CACHE_HINT_KEY: {"generation": generation}}``, applied via a SHALLOW COPY
      (``{**msg, CACHE_HINT_KEY: {...}}``). Every other message passes through by
      reference, unchanged. No caller dict is ever mutated.

    Which message gets the hint (deterministic, documented): the LAST message that
    is neither a tool result (``role == "tool"``) nor the PCS-1 volatile per-turn
    note (``_volatile`` key). That message is the trailing boundary of the stable,
    cacheable content — everything up to and including it is worth caching. If no
    such message exists (every message is a tool result or volatile note), the hint
    falls back to ``messages[0]``, the stable head PCS-1 established.

    An EXPLICIT-capable provider's adapter consumes whichever message carries
    :data:`CACHE_HINT_KEY` (a later atom). ``generation`` lets the adapter tell a
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
        if msg.get("role") == "tool" or msg.get(_VOLATILE_HINT_KEY):
            continue
        target = i
        break

    out: list[dict] = list(messages)
    # Shallow copy ONLY the hinted message so the caller's dict is never mutated.
    out[target] = {**messages[target], CACHE_HINT_KEY: {"generation": generation}}
    return out

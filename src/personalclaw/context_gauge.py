"""ONE owner for the context gauge: a provider's token report → a window reading.

Every measured gauge in the tree used to be the same two-line expression —
``(input_tokens / window) * 100`` — written out at each provider. Two defects live in
those two lines, and both were measured live (issues #3405 / #3406, Ollama 0.34.2 /
``gemma4:12b`` against a 32768-token served window):

**1. A denominator nobody declared.** ``gemma4`` is absent from ``model_tokens.json``, so
the lookup fell through to the adapter's own fallback (128000 in ``llm/openai.py``,
200000 in ``llm/anthropic.py``) and the gauge divided by it. The denominator was 3.91×
too large, the highest value the gauge could *ever* report was 25.19%, and the 70%
compaction trigger was therefore unreachable — not late, unreachable. A percentage
computed against a fabricated window renders with exactly the same confidence as a real
one, which is what made it invisible. So there is no fallback here: an unresolvable
window is :data:`UNKNOWN`, and the composer draws a plain dot instead of a ring.

**2. A numerator that FALLS as the prompt grows.** ``usage.prompt_tokens`` is what the
provider *accepted*, not what it was *sent*, and a runtime that truncates silently
reports the truncated figure. Measured: the reported percentage tracked the real prompt
up to 98.39% of the window and then collapsed to a flat 50.01% (16,387 tokens — half the
served window) for *every* larger prompt, returning HTTP 200 with no exception to catch.
A context that was 100% full and shedding input read as half empty, and a session that
stepped over that band between two turns never saw a reading above 70% again.

**How truncation is detected, and why it is ORDINAL rather than a token estimate.** The
first attempt at this compared the report against a chars-per-token floor: a report too
small to be a plausible count of the prompt we sent is evidence the provider dropped
input. A live drive falsified it. Measured on the same host, a prompt of repeated
characters tokenizes at **7.99 chars/token**, while the curve recorded in #3405 ran at
**4.50** — and a token floor cannot straddle that. Being safe against the sparse end
(never accusing an honest provider) requires dividing by more than the densest content's
ratio; catching a half-window truncation at the cliff requires dividing by less than half
the sparsest content's ratio; across content from 2 to 8 chars/token those two demands are
arithmetically unsatisfiable. The measured false positive was a 998-character prompt
reporting **100% full**.

So this makes no assumption about tokenization at all. It keeps, per binding, the largest
prompt it has seen and what the provider reported for it, and applies one rule:

    a provider handed a LARGER prompt cannot honestly report FEWER input tokens.

That is ordinal, so it holds for any content at any density, and it is the same fact the
findings measured (16,387 tokens for a prompt that had just reported 32,241). Compaction
is handled by the same comparison rather than by an exception: compaction *shrinks* the
prompt, so the reading is allowed to fall with it.

The two invariants that follow, which ``tests/test_context_gauge_properties.py`` asserts
as properties rather than examples:

* **Monotone.** Within a binding, a larger served prompt never lowers the reading.
* **In range.** ``0 <= pct <= 100`` or ``None``; over-window is representable (100) and
  never wraps to a small number.

The window itself is resolved by :func:`personalclaw.model_windows.resolved_context_window`
— the one reader that can answer "nothing declared one" — and handed in. This module
imports nothing from ``personalclaw``, so every provider adapter (core, or a bundled app
via ``personalclaw.sdk.model``) can import it from a cold interpreter in any order without
a cycle. ``tests/test_sdk_import_cycle.py`` drives both.
"""

from __future__ import annotations

from typing import Any

#: The reading for a context at or past its window. The top of the range, not a
#: sentinel: a gauge that is full IS 100%, and the display and every threshold behave
#: identically whether the window was exactly filled or overflowed.
FULL_PCT = 100.0

#: The reading for "no window we can honestly claim", as returned by
#: :func:`personalclaw.model_windows.resolved_context_window`. ``None`` already means
#: "this provider measured nothing" everywhere downstream (``llm/base`` contract,
#: ``LLMEvent.context_usage_pct``, the composer's plain dot), and "we cannot trust the
#: denominator" is the same answer to the user: no number.
UNKNOWN: float | None = None


def prompt_text_chars(messages: list[dict[str, Any]]) -> int:
    """Characters of TEXT in an outbound message list — the size of what we sent.

    Only ever compared against ITSELF (see :class:`ContextGauge`), so it does not need to
    approximate tokens and deliberately does not try. What it does need is to be monotone
    in the prompt: more content in, a bigger number out.

    🪤 Deliberately not :func:`personalclaw.context_compaction.total_chars`, which counts
    ``str(content)``. A multimodal turn's image part would then contribute the repr of a
    base64 data URL — tens of thousands of characters for a part whose token cost is fixed
    and small — so a turn that merely *attached a screenshot* would look like a far larger
    prompt than the one before it. Compaction wants those bytes counted (it is really
    carrying them); a size comparison must not be dominated by them.

    Tool-call arguments count — they are text we sent. Tool SCHEMAS and a separate
    ``system`` prompt do not: neither is in this list, and both are roughly constant across
    the turns of one binding, which is what the comparison cares about.

    Both wire shapes are counted, because both reach a gauge: OpenAI's ``content`` string
    plus ``tool_calls[].function.arguments``, and Anthropic's block list
    (``{"type": "text", "text": …}``, ``{"type": "tool_result", "content": …}``).
    """
    total = 0
    for message in messages:
        total += _content_chars(message.get("content"))
        for call in message.get("tool_calls") or []:
            if isinstance(call, dict):
                total += len(str(call.get("function", {}).get("arguments", "")))
    return total


def _content_chars(content: Any) -> int:
    """Text characters in a message ``content`` — a string, or a block list one deep.

    Anything else (an image part, a number, a nested shape this does not know) counts
    ZERO rather than ``len(str(...))`` — see :func:`prompt_text_chars`.
    """
    if isinstance(content, str):
        return len(content)
    if not isinstance(content, list):
        return 0
    total = 0
    for part in content:
        if isinstance(part, str):
            total += len(part)
        elif isinstance(part, dict):
            text = part.get("text")
            if isinstance(text, str):
                total += len(text)
            else:
                inner = part.get("content")
                total += _content_chars(inner) if isinstance(inner, (str, list)) else 0
    return total


class ContextGauge:
    """The context reading for ONE binding, across its turns.

    Stateful on purpose, and the state is two integers: the largest prompt this binding has
    been handed, and the input-token count the provider reported for it. That pair is the
    only reference against which a later report can be judged, because the alternative — a
    chars-per-token estimate — is measurably not up to the job (see the module docstring).

    A provider adapter owns one of these for its lifetime and calls :meth:`measure` at the
    point it used to compute ``(input_tokens / window) * 100``.
    """

    __slots__ = ("_marked_chars", "_marked_tokens")

    def __init__(self) -> None:
        self._marked_chars = 0
        self._marked_tokens = 0

    def measure(
        self,
        *,
        reported_tokens: int,
        sent_chars: int,
        window: int | None,
    ) -> float | None:
        """``reported_tokens`` as a percentage of ``window``, or ``None`` for unknown.

        Arguments, and why each is needed:

        * ``reported_tokens`` — the provider's own input-token count for this turn. ``<= 0``
          means it reported none, which is :data:`UNKNOWN` and not ``0.0``: a fabricated
          zero both states a measurement nobody made and disables the char-estimate
          compaction backstop, which is reachable only while the gauge is unmeasured.
        * ``sent_chars`` — the size of the prompt we handed the provider, from
          :func:`prompt_text_chars`. Compared only against the largest prompt this gauge has
          already seen; no ratio is applied to it.
        * ``window`` — from
          :func:`personalclaw.model_windows.resolved_context_window`, which returns ``None``
          when nothing declared one. ``None`` (and a non-positive window) is
          :data:`UNKNOWN`.

        The reading is :data:`FULL_PCT` in two cases, and they are the same fact stated by
        two different witnesses: the provider reported FEWER tokens for a prompt at least as
        large as one it has already measured (it dropped input), or it reported at least a
        window's worth. Neither is a silent clamp: 100% *is* the honest reading for a
        context that no longer fits, and it is what makes every configured threshold
        (``runtime._COMPACT_THRESHOLD_PCT``, ``session._BG_RECYCLE_PCT``,
        ``config.autocompact_pct``) reachable on a truncating provider instead of leaving
        the gauge parked at 50% while history grows unbounded.

        🪤 A cold gauge has no reference, so the FIRST turn of a binding cannot be caught by
        the ordinal rule — a session whose very first prompt is already over the window
        reads whatever the provider reported (for the measured Ollama that is ~50%) until a
        larger prompt arrives, and from then on it reads full and stays there. Flat is not
        the same defect as falling: the reading never *reverses*, so the gauge cannot tell a
        user that pressure is easing while it is growing, which is what #3405 was about. A
        cold-start ratio test was the alternative and it is what the live drive disproved.
        """
        if window is None or window <= 0 or reported_tokens <= 0:
            return UNKNOWN
        if sent_chars >= self._marked_chars and reported_tokens < self._marked_tokens:
            # A larger prompt with a smaller token count: the provider dropped input. The
            # mark is deliberately NOT advanced — the truncated figure is not a measurement
            # of anything, so letting it become the reference would re-arm the collapse for
            # every later turn.
            return FULL_PCT
        if sent_chars >= self._marked_chars:
            self._marked_chars = sent_chars
            self._marked_tokens = reported_tokens
        if reported_tokens >= window:
            return FULL_PCT
        return reported_tokens / window * FULL_PCT

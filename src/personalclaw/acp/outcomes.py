"""Per-turn tool-outcome accumulation for the ACP seam (``G7`` / AAP-8).

Procedural memory (M5d) learns "tool X works / fails for this shape" from the
``(tool, outcome)`` pairs a runtime accumulated during the turn. The dashboard reads
them through one duck-typed hook — ``drain_tool_outcomes`` — which until now existed
on exactly ONE provider (:class:`personalclaw.agents.native.runtime.NativeAgentRuntime`).
For an ACP provider ``getattr(provider, "drain_tool_outcomes", None)`` returned ``None``,
so ``after_turn_review.record_procedural_outcomes`` was handed an empty list and a
six-tool-call ACP turn produced ZERO procedural rows. That is the whole of ``G7``.

Two deliberate choices, both about not deriving anything a second way:

* **The failure bit is read, never re-derived.** ``acp/translate.py`` already stamps
  ``tool_meta = {"ok": False}`` on a ``failed`` tool result (present-and-False only on
  failure, absent on success — the native runtime's ``tool_meta`` contract). This
  accumulator reads that key. It does NOT look at the ACP ``status`` field again, so
  the loop breaker and procedural memory cannot disagree about whether a call failed.
* **The outcome vocabulary is imported, never re-spelled.** Members come from
  :data:`personalclaw.memory_service.PROCEDURAL_OUTCOMES`; anything else is dropped
  with a warning here, before it can reach a store whose surfacing rules do not map it.

The ACP seam can only observe two of the three members. ``denied`` requires a denial
*observation* authored by ``security.classify_denial`` — the native runtime has one
because it refuses the call itself. On the ACP seam the CLI owns the refusal and a
rejected permission arrives as an ordinary failed (or absent) tool result, so there is
no honest ``denied`` signal to read. Inventing one would be a second derivation.
"""

from __future__ import annotations

import logging
from typing import Any

from personalclaw.acp.types import EVENT_TOOL_CALL, EVENT_TOOL_RESULT

logger = logging.getLogger(__name__)

#: Same ceiling the native accumulator uses (``runtime.py``: ``< 200``). A pathological
#: turn cannot grow this list without bound; procedural value saturates long before.
MAX_OUTCOMES = 200


def _vocabulary() -> frozenset[str]:
    """The closed procedural vocabulary, read from its single definition.

    Imported lazily: ``memory_service`` is a heavy module and the ACP layer must not
    pull it in at import time just to know three strings.
    """
    from personalclaw.memory_service import PROCEDURAL_OUTCOMES

    return PROCEDURAL_OUTCOMES


class ToolOutcomeAccumulator:
    """Accumulates one ACP turn's ``(tool, outcome)`` pairs.

    Lifecycle mirrors the native runtime's, plus one thing the native accumulator does
    NOT have: an explicit turn boundary.

    * :meth:`begin_turn` — called when a turn starts streaming. Clears everything.
      Without it, a turn whose after-turn review declined to run (the learning gate's
      ``worthwhile`` check returns before the drain) would leave its outcomes in place
      and the NEXT turn would be credited with the previous turn's failures.
    * :meth:`observe` — fed every event of the turn.
    * :meth:`drain` — read AND clear, exactly like ``NativeAgentRuntime.drain_tool_outcomes``.
      The dashboard drains once and shares the one list between procedural memory and the
      self-model observer, so a second drain MUST come back empty.
    """

    def __init__(self) -> None:
        # tool_call_id → tool name, learned from the tool_call event. An ACP tool RESULT
        # carries only the id (see ``acp/translate.py``), so the name has to be remembered.
        self._names: dict[str, str] = {}
        self._outcomes: list[tuple[str, str]] = []

    def begin_turn(self) -> None:
        """Reset for a new turn. See the class docstring for why this exists."""
        self._names.clear()
        self._outcomes.clear()

    def observe(self, event: Any) -> None:
        """Fold one stream event into this turn's outcomes. Never raises."""
        kind = str(getattr(event, "kind", "") or "")
        if kind == EVENT_TOOL_CALL:
            call_id = str(getattr(event, "tool_call_id", "") or "")
            # ACP gives no tool-name field; ``title`` is the tool identity (see
            # ``acp/translate.py``, which reads it off ``update.title``). Only the
            # tool_call event's title is trusted: a tool_call_UPDATE's title is a
            # progress DETAIL when it differs from the name (chat_runner treats it
            # that way when rendering), and folding details in would fragment one
            # tool's priors across every argument it was ever called with.
            title = str(getattr(event, "title", "") or "").strip()
            if call_id and title:
                self._names[call_id] = title
            return
        if kind != EVENT_TOOL_RESULT:
            return
        call_id = str(getattr(event, "tool_call_id", "") or "")
        name = self._names.pop(call_id, "")
        if not name:
            # No preceding tool_call, so no tool identity. Deliberately dropped rather
            # than filed under a placeholder: a prior keyed on "tool" would merge every
            # unnamed call into one meaningless row that the surfacing rules then read
            # as evidence about nothing.
            logger.debug("acp tool outcome: no tool name for call %r; dropped", call_id)
            return
        meta = getattr(event, "tool_meta", None) or {}
        # The failure bit `acp/translate.py` stamps. Present-and-False = failed;
        # absent = passed. Not re-derived from the ACP status field.
        outcome = "failed" if meta.get("ok") is False else "success"
        if outcome not in _vocabulary():
            logger.warning("acp tool outcome: %r is not a procedural outcome; dropped", outcome)
            return
        if len(self._outcomes) < MAX_OUTCOMES:
            self._outcomes.append((name, outcome))

    def drain(self) -> list[tuple[str, str]]:
        """Return this turn's pairs and clear them (read-and-clear, like native)."""
        out = list(self._outcomes)
        self._outcomes.clear()
        return out


class AcpToolOutcomesMixin:
    """Gives an ACP provider the duck-typed ``drain_tool_outcomes`` hook.

    Mixed into both ACP providers (the N=1 client-backed one and the pooled
    session-backed one) so the hook's contract is written once. Deliberately NOT
    promoted onto ``ModelProvider``/``AgentProvider``: the hook is read with
    ``getattr`` precisely so a provider with no tool loop (OpenAI, Anthropic) need not
    implement it, and widening the ABC to fix one seam would force four more
    implementers plus every test double to grow a method that returns ``[]``.
    """

    @property
    def _outcome_accumulator(self) -> ToolOutcomeAccumulator:
        acc = getattr(self, "_acp_tool_outcomes", None)
        if acc is None:
            acc = ToolOutcomeAccumulator()
            self._acp_tool_outcomes = acc
        return acc

    def drain_tool_outcomes(self) -> list[tuple[str, str]]:
        """Return this turn's accumulated ``(tool, outcome)`` pairs and clear them.

        ``outcome`` is a member of :data:`personalclaw.memory_service.PROCEDURAL_OUTCOMES`
        — ``success`` or ``failed`` on this seam (see the module docstring on ``denied``).
        Same read-and-clear contract as ``NativeAgentRuntime.drain_tool_outcomes``: the
        dashboard drains ONCE and shares the result, so a second drain returns ``[]``.
        """
        return self._outcome_accumulator.drain()

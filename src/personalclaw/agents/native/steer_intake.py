"""Mid-turn steers: what the user adds while a native turn is running.

A mixin of :class:`~personalclaw.agents.native.runtime.NativeAgentRuntime`. The dispatcher wires a
drain (:meth:`SteerIntake.set_steer_source`), the loop takes what it holds at each model boundary
into history as fresh user input (:meth:`SteerIntake._take_steers`), and what the per-turn cap left
over is named at turn end for the dispatcher to requeue (:meth:`SteerIntake.undelivered_steers`).
"""

from __future__ import annotations

import logging
from collections.abc import Callable

logger = logging.getLogger(__name__)

# Cap mid-turn steer injections so a message flood can't extend one turn
# forever — past this, further steers wait for the next turn.
_MAX_STEERS_PER_TURN = 4


class SteerIntake:
    """The steer drain, the per-turn cap, and the steers a turn still owes."""

    # The runtime's own state, set in ``__init__`` and reset at each turn's start: declared here
    # so this is checked against the runtime's types, not against what its assignments imply.
    _messages: list[dict]
    _pull_steer: Callable[[], list[str]] | None
    _steers_injected: int
    _steer_pending: list[str]

    def set_steer_source(self, pull: Callable[[], list[str]] | None) -> bool:
        """Wire the queue-steering source: a callable the loop drains at each
        model boundary for mid-turn user messages. None disables steering.

        Returns whether a drain is now armed, so the dispatcher reads one answer from
        every runtime instead of inferring native's from the absence of a refusal
        (the ACP seam CAN refuse, this one never does)."""
        self._pull_steer = pull
        return pull is not None

    def _take_steers(self) -> list[str]:
        """Append any pending steers to history as fresh user input, and return them: what
        this turn took, in order. The loop says each (``EVENT_STEER``), for the chat to write it
        where the turn took it, and runs another inference so it affects the answer in progress.

        Called at BOTH model boundaries — after a tool batch, and before a no-tool-call
        turn would end. The second call site is the one that matters in practice: most
        turns are plain prose and never execute a tool, and with the drain only after
        the tool batch those steers were dropped after the API had already reported
        success.

        Capped by ``_MAX_STEERS_PER_TURN`` so a flood cannot extend one turn forever;
        once the cap is hit this returns nothing and the turn ends normally.

        The overflow is RETAINED on ``_steer_pending``, never dropped. The pull empties
        the session's buffer, so popping the whole deque and then breaking at the cap
        discarded steers that existed nowhere else — measured: with the cap at 4 and 7
        buffered, three vanished from history, from the session deque, and from this
        runtime in one call. The cap is a bound on how much redirection ONE turn absorbs,
        not a licence to lose the rest, so what does not fit stays owed and is reported at
        turn end by :meth:`undelivered_steers` for the dispatcher to requeue visibly.
        """
        if self._pull_steer is not None:
            try:
                pulled = self._pull_steer()
            except Exception:
                logger.debug("steer drain failed", exc_info=True)
                pulled = []
            self._steer_pending.extend(s for s in (pulled or []) if s and s.strip())
        taken: list[str] = []
        while self._steer_pending and self._steers_injected < _MAX_STEERS_PER_TURN:
            s = self._steer_pending.pop(0)
            self._messages.append(
                {
                    "role": "user",
                    "content": f"[Steering — the user added this mid-task]\n{s}",
                }
            )
            self._steers_injected += 1
            taken.append(s)
        return taken

    def undelivered_steers(self) -> list[str]:
        """Steers this turn owes the user: pulled from the session's buffer but never
        appended to history, because the per-turn cap was already spent. Empty on the
        happy path.

        Read by the dispatcher at turn end (the same duck-typed seam
        :class:`~personalclaw.acp.session.AcpSession` exposes) so an undeliverable steer is
        requeued onto the visible queue instead of vanishing. Deliberately NOT an
        ``activity_event``: the chat drops a steer's own notes (``kind:"steer"``), so
        announcing the loss that way would be an invisible fix.
        """
        return list(self._steer_pending)

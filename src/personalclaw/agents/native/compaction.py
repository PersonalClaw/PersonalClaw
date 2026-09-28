"""The native loop compacts its own history, in process (#470).

A mixin of :class:`~personalclaw.agents.native.runtime.NativeAgentRuntime`: the trigger, the
pass, the explicit :meth:`~InProcessCompaction.compact`, and the two answers the session manager
and the chat runner read about it (``compacts_in_process``, ``compacts_automatically``). The
runtime's ``stream_command`` runs a typed ``/compact`` through the same pass. It is one concern
with one set of state, all of it the loop's own: the message list it rewrites, the gauge it
compacts against, the record of what the last automatic passes reclaimed, the prompt-cache
generation a rewrite invalidates, and the structural loop breaker a rewrite re-arms. Nothing here
streams, calls a model or runs a tool: the pass is ``context_compaction``'s no-LLM digest.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from personalclaw.token_estimate import CONSERVATIVE_CHARS_PER_TOKEN

if TYPE_CHECKING:
    from personalclaw.guardrails.loop_breaker import LoopBreaker
    from personalclaw.llm.base import ModelProvider

logger = logging.getLogger(__name__)


def compaction_summary(before: int, after: int) -> str:
    """How much a pass reclaimed, as ``/compact`` reports it and as an automatic pass is announced.

    One sentence for both, so a compaction the loop did on its own reads exactly like one you asked
    for: "Conversation compacted: freed 42% of the conversation (12,000 → 6,960 characters)".
    """
    return (
        f"freed {(before - after) / before * 100:.0f}% of the conversation "
        f"({before:,} → {after:,} characters)"
    )


class InProcessCompaction:
    """The loop compacts its history when context crosses the Settings threshold,
    ``context_compaction.autocompact_pct()`` — see :meth:`_maybe_compact`."""

    # The loop's own state, which the runtime sets in ``__init__``: declared here so the pass is
    # checked against the runtime's types rather than against what its own assignments imply.
    _messages: list[dict]
    _last_context_pct: float | None
    _compaction_saves: list[float]
    _cache_generation: int
    _breaker: LoopBreaker
    _model: ModelProvider

    if TYPE_CHECKING:

        @property
        def agent_model(self) -> str:
            """The model id the loop sends its turns to (``NativeAgentRuntime.agent_model``)."""
            ...

    # The CONSERVATIVE ratio, not the nominal one: this is a compaction TRIGGER, so it has
    # to over-estimate token usage and err toward compacting slightly early — cheap —
    # rather than overflowing the window, which kills the turn. See
    # `personalclaw.token_estimate` for why the repo keeps two ratios and not one.
    _EST_CHARS_PER_TOKEN = CONSERVATIVE_CHARS_PER_TOKEN

    def _estimated_context_pct(self) -> float | None:
        """Char-based context estimate for providers that report no usage.

        Endpoints that reject ``stream_options`` never deliver a usage chunk, so
        ``_last_context_pct`` stays ``None`` and threshold compaction would never
        fire — history then grows without bound until the model breaks. This
        estimate (total chars over the model window at a conservative
        chars-per-token) is the backstop trigger for exactly that case. It feeds
        COMPACTION ONLY and is never written to ``_last_context_pct``: an
        estimate must not be displayed as a measurement.

        The window this divides by has to be the window the provider SERVES. A local
        runtime is exactly the case that reaches here (a loopback endpoint that rejects
        ``stream_options`` reports no usage), and it is also the case the shared table
        answers with an architectural maximum — so resolving it as any other model would
        divide by a number up to ~31x too large and produce an estimate that can never
        cross the threshold this backstop exists to cross. The local-ness signal is the
        guard's own sniffer, not a second one; the per-binding ``context_window``
        override the provider popped out of its options overrides both.
        """
        from personalclaw import context_compaction as cc
        from personalclaw.guardrails.model_call import _is_local_provider
        from personalclaw.model_windows import model_context_window

        chars = cc.total_chars(self._messages)
        if chars <= 0:
            return None
        window_tokens = model_context_window(
            self.agent_model or None,
            local=_is_local_provider(self._model),
            override=getattr(self._model, "context_window", None),
        )
        if window_tokens <= 0:
            return None
        return (chars / self._EST_CHARS_PER_TOKEN) / window_tokens * 100.0

    def _compact_now(self, measured_pct: float | None) -> tuple[int, int]:
        """Run the structured compaction pass on ``self._messages``. Returns ``(before, after)``.

        THE compaction, with no trigger policy in it: the threshold gate, the anti-thrashing
        gate AND the ``_compaction_saves`` bookkeeping that feeds it all live in
        :meth:`_maybe_compact`, and are deliberately absent from the explicit path
        (:meth:`compact`) — a person who typed ``/compact`` has already decided.

        🪤 THE SAVES LIST MUST NOT BE APPENDED HERE. ``should_compact`` refuses when the last
        two entries each reclaimed <10%, and it is the *automatic* path that appends, so a
        list polluted by explicit presses would latch: two ``/compact`` clicks on a short
        chat (0% reclaimed, truthfully) would disable threshold compaction for the rest of
        the session, and because the skipped pass never appends, nothing could ever clear it
        — history would then grow unbounded until the model broke.

        ``after == before`` means the pass found nothing to reclaim — a truthful outcome, not
        a failure, and the caller reports it as such.

        *measured_pct* is the gauge the trigger read, or ``None`` when the gauge is
        unmeasured; it only scales the optimistic post-compaction gauge reset.
        """
        from personalclaw import context_compaction as cc

        before = cc.total_chars(self._messages)
        if before <= 0:
            return 0, 0
        compacted = cc.compact(self._messages)
        after = cc.total_chars(compacted)
        saved = (before - after) / before if before else 0.0
        if after < before:
            self._messages = compacted
            # Compaction rewrote history → any cached prompt prefix is now stale. Bump
            # the generation so an EXPLICIT-cache provider's next marker reads fresh.
            self._cache_generation += 1
            logger.debug("native: cache prefix invalidated → generation %d", self._cache_generation)
            # A compaction shrank context; the next provider turn re-measures, so
            # reset our gauge optimistically to avoid re-triggering immediately.
            # Only when the gauge was MEASURED: in the estimate-triggered path
            # _last_context_pct is None and must stay None — scaling the estimate
            # into it would display a number the provider never reported.
            if self._last_context_pct is not None and measured_pct is not None:
                self._last_context_pct = measured_pct * (after / before)
            # Post-compaction guard (E3.1): re-arm structural detection so a loop
            # that resumes identically after the history was compacted is caught
            # fresh, instead of its pre-compaction signatures aging out silently.
            self._breaker.reset_structural()
            logger.info(
                "native: compacted context %d→%d chars (saved %.0f%%)",
                before,
                after,
                saved * 100,
            )
        return before, after

    def _maybe_compact(self) -> tuple[int, int] | None:
        """Run structured compaction on ``self._messages`` if over the threshold.

        Trigger = provider-reported context usage ≥ the Settings threshold
        (``session.autocompact_pct``), with a char-based estimate as the trigger when
        the provider reports no usage at all (the local-model path). Anti-thrashing
        skips it when the last two passes each reclaimed <10%. Uses the no-LLM path
        (tool-output pruning pre-pass + structured digest) — cheap, safe, and
        synchronous; an LLM-summarized middle can layer on later. Records the save
        fraction for the anti-thrashing guard.

        Returns ``(before, after)`` when the pass rewrote the history, else ``None`` — what the
        turn loop announces (``compaction_summary``), so the conversation is not compacted
        without a word.
        """
        from personalclaw import context_compaction as cc

        measured_pct = self._last_context_pct
        if measured_pct is None:
            # No-usage backstop: estimate purely for the trigger decision. The
            # displayed gauge stays unmeasured — see _estimated_context_pct.
            measured_pct = self._estimated_context_pct()
        # Unmeasured context cannot cross a threshold — an unknown gauge must not
        # trigger compaction any more than it may print a percentage.
        if measured_pct is None or measured_pct < cc.autocompact_pct():
            return None
        if not cc.should_compact(self._compaction_saves):
            return None
        before, after = self._compact_now(measured_pct)
        if before > 0:
            # The anti-thrashing record is the AUTOMATIC trigger's own bookkeeping — see
            # `_compact_now`'s note on why an explicit `/compact` must never write to it.
            self._compaction_saves.append((before - after) / before)
        return (before, after) if after < before else None

    @property
    def compacts_in_process(self) -> bool:
        """True — ``self._messages`` is this runtime's own list, and
        :meth:`_compact_now` rewrites it synchronously.

        ``supports_native_commands`` stays False and must: there is no backend to hand a
        slash command to, so every OTHER ``/…`` word is still honestly reported as a plain
        message. This property is the narrow exception for the one command the runtime can
        genuinely execute.
        """
        return True

    @property
    def compacts_automatically(self) -> bool:
        """True while :meth:`_maybe_compact` will still compact this runtime's history on its
        own once the context crosses the Settings threshold.

        False once the last two automatic passes each reclaimed under a tenth
        (``context_compaction.should_compact``): compacting again would not help, so the
        session manager restarts the session at that threshold instead. While it is True the
        manager leaves the session alone, because a restart at the same threshold would
        always come first and throw away the history this loop is about to compact.
        """
        from personalclaw import context_compaction as cc

        return cc.should_compact(self._compaction_saves)

    async def compact(self, context: str = "") -> None:
        """Compact this session's history NOW, unconditionally.

        The explicit counterpart to :meth:`_maybe_compact`'s automatic trigger — same pass,
        no threshold and no anti-thrashing gate, because the caller asked. *context* is
        accepted for interface compatibility (the ACP provider folds it into a prompt for
        the backend's summariser) and ignored here: the no-LLM structured digest derives its
        summary from the history itself, so there is nothing to seed.
        """
        self._compact_now(self._last_context_pct)

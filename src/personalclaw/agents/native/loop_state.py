"""One native turn's state, as the loop's five phases share it (#4015).

:meth:`NativeAgentRuntime.stream <personalclaw.agents.native.runtime.NativeAgentRuntime.stream>`
builds one :class:`LoopState` at each call and hands it to each phase in turn
(:mod:`~personalclaw.agents.native.loop_phases`); it dies with the call, and nothing keeps it. What
the turn's code outside the phases reads (the breaker's stop sentence, the steer cap, the window,
the turn's own message, the owed reply) stays on the runtime, where those readers are.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any

from personalclaw.agents.native.owed_reply import OwedReply
from personalclaw.llm.events import EVENT_COMPLETE, EVENT_SPENT, AgentEvent
from personalclaw.llm.prompt_cache import PromptCache
from personalclaw.routing.rates import CallPrice, summed

#: The observation a queued-but-unstarted call is answered with when a stop reaches it
#: before it was dispatched. One spelling, because two exits pair a dropped call
#: with a result — the stop the loop's end phase reads after an answer, and the per-call
#: drop in :meth:`NativeAgentRuntime._execute_wave` — and a model reading the history back
#: must not see two different accounts of the same thing.
CANCELLED_BEFORE_RUN = "Error: cancelled before this tool ran"


class Next(enum.Enum):
    """What the loop does after a phase."""

    #: Go on to the next phase; after the last, to the next step.
    CONTINUE = "continue"
    #: This step is done: start the next one.
    BREAK = "break"
    #: The turn has ended, and the phase said its ending.
    RETURN = "return"


@dataclass
class UsageTotals:
    """What the turn's inferences spent, summed over them, and how much the turn did.

    The prompt-cache halves travel with the input count: every consumer of cache telemetry
    reconstructs the whole served prompt as ``input + cache_creation + cache_read``
    (``stats.py:160``, ``pricing.py:169``, ``llm/openai.py:444``), so a terminal event carrying a
    summed ``input_tokens`` next to a zeroed or last-inference-only cache count would divide a
    whole-turn numerator by a single-inference denominator. Omitting them is what made every
    ledger row read a STRUCTURAL zero — indistinguishable from "caching is not working".
    """

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    cost_usd: float = 0.0
    #: The guarded calls those sums came from (``AgentEvent.audit_ids``): the turn's usage row
    #: names them, so the model-call census does not count this turn's inferences again.
    audit_ids: list[str] = field(default_factory=list)
    #: What each inference was charged where it was made (``AgentEvent.charged``): the turn's
    #: usage row is their sum, not its tokens priced again (``routing.rates.summed``).
    charged: list[CallPrice | None] = field(default_factory=list)
    #: Turn telemetry (parity with ACP's last_prompt_stats): the events observed and the tool
    #: calls made, on the terminal event, for the chat runner's "Turn complete" line.
    events: int = 0
    tool_calls: int = 0

    def add(self, usage: AgentEvent) -> None:
        """Add one inference's usage (its ``EVENT_COMPLETE``)."""
        self.input_tokens += usage.input_tokens or 0
        self.output_tokens += usage.output_tokens or 0
        self.cache_read_tokens += usage.cache_read_tokens or 0
        self.cache_creation_tokens += usage.cache_creation_tokens or 0
        self.cost_usd += usage.cost_usd or 0.0
        self.audit_ids.extend(getattr(usage, "audit_ids", ()) or ())
        self.charged.append(getattr(usage, "charged", None))


@dataclass
class LoopState:
    """Everything one turn of the loop carries from phase to phase."""

    #: This turn's number on its runtime: the turn's cleanup runs only while no later one began.
    turn: int = 0
    #: The steps (inferences) taken; the loop stops at the runtime's ``max_turns``.
    steps: int = 0
    #: The tools this turn's next call is offered (``None``: none).
    tools: list[dict] | None = None
    #: The model this turn starts on, put back when it ends.
    home: tuple[Any, str] | None = None
    #: How many models of its chain this turn fell back to.
    fallbacks: int = 0
    #: The turn's ONE correction-retry (#2287/#252) is spent: it lives at turn scope, not per
    #: inference, because two separate transients in one turn mean the provider is genuinely
    #: unhealthy, and the second failure surfaces.
    retried: bool = False
    #: The turn's ONE request for the reply (``owed_reply``).
    owed: OwedReply = field(default_factory=OwedReply)
    totals: UsageTotals = field(default_factory=UsageTotals)
    #: The steers the turn took, in order.
    steers: list[str] = field(default_factory=list)
    # ── the step's answer ──
    text: str = ""
    calls: list[AgentEvent] = field(default_factory=list)
    usage: AgentEvent | None = None
    # ── the step's request, and its attempt ──
    mode: PromptCache = PromptCache.NONE
    request: list[dict] = field(default_factory=list)
    #: Something the user can see streamed in this attempt: a retry would show it twice.
    streamed: bool = False
    attempt_started: float = 0.0
    #: When the attempt's answer came back, on the same clock as :attr:`attempt_started`.
    answered_ms: float = 0.0
    #: The inferences of this turn cut at the output limit while writing a tool call
    #: (``owed_reply.CUT_CALLS_PER_TURN`` of them end the turn).
    cut_calls: int = 0
    #: The output cap the next request asks for, in tokens, or 0 for its provider's own: twice what
    #: a call cut at the output limit stopped at, asked once in a turn, of a provider that takes
    #: one (``ModelProvider.takes_output_cap``).
    room: int = 0
    #: What the phase that just ran says the loop does next.
    next: Next | None = None

    def may_retry(self, cancelled: bool) -> bool:
        """Whether this attempt's failure may be asked again: the turn's retry is unspent,
        nothing of the attempt was shown, the model called no tool (it did work), and the turn
        was not stopped."""
        return not self.retried and not self.streamed and not self.calls and not cancelled

    def ending(
        self,
        stop_reason: str,
        *,
        text: str = "",
        output_cap: int = 0,
        context_usage_pct: float | None,
        served_model_ref: str,
    ) -> AgentEvent:
        """The turn's terminal event: how it ended, and everything it spent."""
        totals = self.totals
        return AgentEvent(
            kind=EVENT_COMPLETE,
            stop_reason=stop_reason,
            text=text,
            output_cap=output_cap,
            input_tokens=totals.input_tokens,
            output_tokens=totals.output_tokens,
            cache_read_tokens=totals.cache_read_tokens,
            cache_creation_tokens=totals.cache_creation_tokens,
            cost_usd=totals.cost_usd,
            num_turns=self.steps,
            context_usage_pct=context_usage_pct,
            event_count=totals.events,
            tool_call_count=totals.tool_calls,
            served_model_ref=served_model_ref,
            audit_ids=tuple(totals.audit_ids),
            charged=summed(totals.charged),
        )

    def spent(self, *, served_model_ref: str) -> AgentEvent:
        """What a turn ending in an error has spent all the same (a dollar cap refusing the next
        call), said before the error so its usage row is written for the calls that finished."""
        totals = self.totals
        return AgentEvent(
            kind=EVENT_SPENT,
            input_tokens=totals.input_tokens,
            output_tokens=totals.output_tokens,
            cache_read_tokens=totals.cache_read_tokens,
            cache_creation_tokens=totals.cache_creation_tokens,
            cost_usd=totals.cost_usd,
            num_turns=self.steps,
            served_model_ref=served_model_ref,
            audit_ids=tuple(totals.audit_ids),
            charged=summed(totals.charged),
        )

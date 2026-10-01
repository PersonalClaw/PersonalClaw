"""An agent CLI's turns, metered like a guarded model's calls when it runs on a metered axis.

The daily and per-run spend caps count what the spend guard charges (``guardrails.model_call``),
and the guard wraps a MODEL the resolution seam builds. An agent CLI (``acp:<cli>``) makes its own
model calls inside its own process, so no guard ever saw one: a loop, a subagent or a scheduled
job running on one spent money the caps never counted, and the one place a subagent's CLI spend
was charged was its completion (which a loop's worker has none of).

So an ACP provider acquired on a metered axis (``provider_bridge.METERED_AXES``, handed over by
``SessionManager`` through :meth:`AcpTurnMeter.set_spend_axis`) meters each of its turns the way
the guard meters a call: admitted before the prompt is sent against the day's and the run's
ceilings (``guardrails.model_call.admit_call``), setting aside what the turn may cost beside the
guarded calls running with it and waiting for them when they hold the room it needs, refused when
a ceiling is spent or a dollar one has no price for its model, charged at the turn's
``EVENT_COMPLETE`` in place of what it set aside with the cost the CLI reports (priced from its
tokens by ``routing.rates.price_event`` when it reports none), recorded in the model-call log, and
named on that event (``audit_ids``) so the turn's usage row joins it. A provider on no metered axis
(a person's chat on an agent CLI) is left alone, as the chat binding is.

What it does not do is the guard's other half: no hard timeout (an agent's turn runs tools and
can rightly take many minutes), no circuit breaker and no outbound scan of the prompt, which the
CLI is handed as the host built it.
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

from personalclaw.llm.base import EVENT_COMPLETE, LLMEvent

if TYPE_CHECKING:
    from personalclaw.guardrails.budgets import Budget

logger = logging.getLogger(__name__)

#: The longest a turn waits for the calls running beside it to leave the room it needs under a
#: spend ceiling, before it is refused. Longer than a guarded call's wait: what it waits for may
#: be another agent's turn, which runs tools and can rightly take many minutes.
TURN_ROOM_WAIT_SECS = 1800.0


class AcpTurnMeter:
    """Gives an ACP provider :meth:`set_spend_axis` and the :meth:`_metered` stream wrapper.

    Mixed into both ACP providers (the client-backed one and the pooled session-backed one), as
    :class:`~personalclaw.acp.outcomes.AcpToolOutcomesMixin` is, so the contract is written once.
    A provider identifies itself through ``provider_id`` and ``agent_model``, which both have.
    """

    _spend_axis: str = ""
    #: The ceilings last read, kept when a later read fails: never a value nobody set.
    _spend_ceilings: "tuple[Budget, Budget] | None" = None

    def set_spend_axis(self, axis: str) -> None:
        """Meter this provider's turns when *axis* is a metered one; leave them alone otherwise."""
        from personalclaw.providers.provider_bridge import METERED_AXES

        self._spend_axis = axis if axis in METERED_AXES else ""

    @property
    def spend_axis(self) -> str:
        """The metered axis this provider's turns are charged on, or ``""`` when they are not."""
        return self._spend_axis

    def _ceilings(self) -> "tuple[Budget, Budget]":
        """The day's and the run's ceilings as they read now; the last read when they cannot be
        read, as the guard keeps its last (``ModelCallGuard._refresh_budgets``)."""
        from personalclaw.guardrails.budgets import (
            Budget,
            BudgetConfigUnreadable,
            budget_from_config,
            run_budget_from_config,
        )

        try:
            self._spend_ceilings = (budget_from_config(), run_budget_from_config())
        except BudgetConfigUnreadable:
            logger.warning("agent CLI turn: spend ceilings unreadable; keeping the last read")
        return self._spend_ceilings or (Budget(), Budget())

    async def _metered(
        self, events: AsyncIterator[LLMEvent], *, prompt: str = ""
    ) -> AsyncIterator[LLMEvent]:
        """*events*, one turn's, admitted before it starts (*prompt* is what it sends) and charged
        at its ``EVENT_COMPLETE``. Passes through untouched on no metered axis."""
        axis = self._spend_axis
        if not axis:
            async for event in events:
                yield event
            return

        from personalclaw.guardrails.audit import (
            AttemptRecord,
            current_caller,
            now_ms,
            record_attempt,
        )
        from personalclaw.guardrails.budgets import current_run_key, get_meter
        from personalclaw.guardrails.failure import BudgetExceededError, FailureMode
        from personalclaw.guardrails.model_call import (
            admit_call,
            call_cost,
            naming_the_call,
            new_audit_id,
        )
        from personalclaw.routing.rates import price_event

        provider = str(getattr(self, "provider_id", "") or "acp")
        model = str(getattr(self, "agent_model", "") or "")
        meter = get_meter()
        audit_id = new_audit_id()

        def _record(mode: FailureMode, **fields) -> None:
            record_attempt(
                AttemptRecord(
                    audit_id=audit_id,
                    ts=time.time(),
                    use_case=axis,
                    provider=provider,
                    model=model,
                    attempt=1,
                    failure_mode=mode.value,
                    caller=current_caller(),
                    **fields,
                )
            )

        day, run = self._ceilings()
        try:
            hold = await admit_call(
                meter,
                call_cost(provider, model, prompt_chars=len(prompt or "")),
                day,
                run,
                wait_secs=TURN_ROOM_WAIT_SECS,
            )
        except BudgetExceededError:
            # Refused before the prompt was sent: it cost nothing, a known $0.
            _record(FailureMode.BUDGET_EXCEEDED, priced=True)
            aclose = getattr(events, "aclose", None)
            if aclose is not None:
                await aclose()
            raise

        started = now_ms()
        charged = False
        try:
            async for event in events:
                if event.kind == EVENT_COMPLETE and not charged:
                    tokens_in = int(getattr(event, "input_tokens", 0) or 0)
                    tokens_out = int(getattr(event, "output_tokens", 0) or 0)
                    price = price_event(event, provider=provider, model=model)
                    meter.settle(
                        hold,
                        ref=f"{provider}:{model}",
                        tokens=tokens_in + tokens_out,
                        answer_tokens=tokens_out,
                        dollars=price.dollars,
                        priced=price.priced,
                        run_key=current_run_key() or None,
                    )
                    _record(
                        FailureMode.NONE,
                        latency_ms=round(now_ms() - started, 1),
                        tokens_in=tokens_in,
                        tokens_out=tokens_out,
                        dollars_est=round(price.dollars, 6),
                        estimated=price.source != "reported",
                        passed=True,
                        priced=price.priced,
                    )
                    charged = True
                    # The turn's usage row is written from this event and takes this price, the
                    # one charged above and recorded in the model-call log.
                    event = naming_the_call(event, audit_id, price)
                yield event
        finally:
            # A turn that ended without completing charged nothing: what it set aside is given
            # back, as a settled one's already was.
            meter.release(hold)

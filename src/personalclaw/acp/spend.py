"""An agent CLI's turns, metered like a guarded model's calls when it runs on a metered axis.

The daily and per-run spend caps count what the spend guard charges (``guardrails.model_call``),
and the guard wraps a MODEL the resolution seam builds. An agent CLI (``acp:<cli>``) makes its own
model calls inside its own process, so no guard ever saw one: a loop, a subagent or a scheduled
job running on one spent money the caps never counted, and the one place a subagent's CLI spend
was charged was its completion (which a loop's worker has none of).

So an ACP provider acquired on a metered axis (``provider_bridge.METERED_AXES``, handed over by
``SessionManager`` through :meth:`AcpTurnMeter.set_spend_axis`) meters each of its turns the way
the guard meters a call: refused before the prompt is sent once the day's or the run's ceiling is
spent (``guardrails.model_call.spent_refusal``), charged at the turn's ``EVENT_COMPLETE`` with the
cost the CLI reports (priced from its tokens when it reports none), recorded in the model-call
log, and named on that event (``audit_ids``) so the turn's usage row joins it. A provider on no
metered axis (a person's chat on an agent CLI) is left alone, as the chat binding is.

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

    async def _metered(self, events: AsyncIterator[LLMEvent]) -> AsyncIterator[LLMEvent]:
        """*events*, one turn's, refused before it starts when a ceiling is spent and charged at
        its ``EVENT_COMPLETE``. Passes through untouched on no metered axis."""
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
        from personalclaw.guardrails.failure import FailureMode
        from personalclaw.guardrails.model_call import (
            call_dollars,
            naming_the_call,
            new_audit_id,
            spent_refusal,
        )

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
        refused = spent_refusal(meter, day, run)
        if refused is not None:
            _record(FailureMode.BUDGET_EXCEEDED)
            aclose = getattr(events, "aclose", None)
            if aclose is not None:
                await aclose()
            raise refused

        started = now_ms()
        charged = False
        async for event in events:
            if event.kind == EVENT_COMPLETE and not charged:
                tokens_in = int(getattr(event, "input_tokens", 0) or 0)
                tokens_out = int(getattr(event, "output_tokens", 0) or 0)
                dollars = call_dollars(event, model, tokens_in, tokens_out)
                meter.charge(tokens_in + tokens_out, dollars, run_key=current_run_key() or None)
                _record(
                    FailureMode.NONE,
                    latency_ms=round(now_ms() - started, 1),
                    tokens_in=tokens_in,
                    tokens_out=tokens_out,
                    dollars_est=round(dollars, 6),
                    estimated=not float(getattr(event, "cost_usd", 0.0) or 0.0),
                    passed=True,
                )
                charged = True
                event = naming_the_call(event, audit_id)
            yield event

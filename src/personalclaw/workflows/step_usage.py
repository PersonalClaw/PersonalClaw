"""What one attempt at a step used, as every row that ends the attempt records it.

A step's model calls are measured where they happen: the controller binds a `guardrails.calls` log
around each node's dispatch, and `ModelCallGuard` publishes every call into it and settles the call
with what its provider reported. This module is the ONE reading of that log for the ledger, so the
rows an attempt can end on (`step_completed`; `step_failed` for a retried, final or stall-killed
attempt; `step_cancelled`) cannot disagree about the same calls, and neither can the run row a
token budget reads.

Before it only `step_completed` read the log, and only for tokens and model. A failed or
stall-killed attempt wrote no usage at all (measured: a best-of-n step killed mid-generation had
made two calls and Introspect read "Models: none recorded"), no row ever named its provider, and
the run row was charged only for steps that succeeded, so a budget never saw a failed attempt.

The rule for a dispatch the guard measured, per field:

* `tokens` / `cost_usd`: the sum of what the providers reported, or `null` when one that finished
  reported nothing. When calls were cut off before they finished (a cancel, a stall kill, the total
  timeout), the sum of those that did finish, as a FLOOR, with `model_calls_open` counting the rest.
  A floor nobody reported anything towards is `null`, not `0`.
* `model` / `provider`: every one the calls used, in first-use order. Known for a cut-off call too:
  the guard knows what it called before the provider answers.

A dispatched stage is the exception, because its calls run in a subagent: see `subagent_usage`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from personalclaw.guardrails.calls import CallLog


@dataclass(frozen=True)
class StepUsage:
    """What one attempt's model calls used. See the module docstring for the rule."""

    tokens: int | None
    cost_usd: float | None
    model: str = ""
    provider: str = ""
    #: Calls the attempt's end cut off before they finished. Non-zero makes `tokens` and
    #: `cost_usd` a floor.
    calls_cut_off: int = 0

    def fields(self) -> dict[str, Any]:
        """The ledger fields, under the names `step_cancelled` has always used."""
        return {
            "model_calls_open": int(self.calls_cut_off),
            "tokens": None if self.tokens is None else int(self.tokens),
            "model": self.model,
            "provider": self.provider,
            "cost_usd": None if self.cost_usd is None else round(float(self.cost_usd), 6),
        }

    def billable(self, estimate: int = 0) -> int:
        """What the run row's token budget is charged: the measurement, else `estimate`.

        The dispatcher's estimate stands in as a floor when no provider reported a count, because
        a budget must still see spend the ledger records as unknown.
        """
        return int(self.tokens) if self.tokens is not None else int(estimate)


#: An attempt that sent nothing to a model: refused before it dispatched, or a gate that timed
#: out. Its zero is a measurement.
NOTHING_SENT = StepUsage(tokens=0, cost_usd=0.0)

#: An attempt whose calls ran where no call log sees them and that ended before it could report
#: them: a dispatched stage's subagent, stopped by a cancel.
NOT_RECORDED = StepUsage(tokens=None, cost_usd=None)


def measured(calls: CallLog, *, estimate: int = 0) -> StepUsage:
    """What an awaited dispatch's model calls used.

    A dispatch that made no guarded call keeps `estimate`, its dispatcher's own count: a
    transform's zero is a measurement, and so is a stalled action that never called a model.
    """
    if not calls.calls:
        return StepUsage(tokens=int(estimate), cost_usd=0.0)
    cut_off = calls.cut_off
    return StepUsage(
        tokens=calls.floor_tokens if cut_off else calls.tokens,
        cost_usd=calls.floor_cost_usd if cut_off else calls.cost_usd,
        model=", ".join(calls.models),
        provider=", ".join(calls.providers),
        calls_cut_off=cut_off,
    )


def subagent_usage(info: Any) -> StepUsage:
    """What a dispatched stage's subagent reported (`SubagentInfo`), for either outcome.

    Its calls run in the subagent's own session, outside the step's call log. `input_tokens`,
    `output_tokens`, `cost_usd` and `model` are populated from the child's `EVENT_COMPLETE` before
    `done` is set, and the same numbers feed the spend meter and the usage ledger, so this rolls up
    an existing observation rather than taking a second one. The subagent names no provider.

    A 0/0 report stays zero rather than `null`: `SubagentInfo` has no flag like a guarded call's
    `usage_reported`, so a subagent that never reported cannot be told apart from one that failed
    before its first turn. `getattr` with defaults, because the manager is injected and a stand-in
    that does not model usage has to read as zero rather than crash the tick.
    """
    return StepUsage(
        tokens=int(getattr(info, "input_tokens", 0) or 0)
        + int(getattr(info, "output_tokens", 0) or 0),
        cost_usd=float(getattr(info, "cost_usd", 0.0) or 0.0),
        model=str(getattr(info, "model", "") or ""),
    )

/** The money sentences a run-shaped surface renders, and the ONE rounding rule behind them.
 *
 *  Extracted from `IntrospectPanel` when the loop cockpit became a second surface that states a
 *  run's cost (MRT-3). Two surfaces re-deciding how to round a dollar is how "$0.00" and "~$0.0001"
 *  end up meaning the same thing in one product, so the rounding lives here once and each surface
 *  composes its own sentence on top of it.
 *
 *  The 2dp-above-a-dollar / 4dp-below rule is `routing/usage.py::_usd`'s, mirrored rather than
 *  re-invented, so a run's money and the Usage panel's money round identically. */
import type { LoopSpend } from './api'

/** A dollar figure, rounded the way the backend's `_usd` rounds it. No tilde, no words —
 *  callers own the disclosure, because what the figure ESTIMATES differs per surface. */
export function runUsd(costUsd: number): string {
  return costUsd >= 1 ? `$${costUsd.toFixed(2)}` : `$${costUsd.toFixed(4)}`
}

/** "~$X this run" for a workflow run, with the estimate stated in the same breath.
 *
 *  The `~` is load-bearing: `pricing.py` derives this from token counts and a price table
 *  ("Providers report token counts but not always a dollar cost"), so rendering it as an exact
 *  charge would claim a precision the number does not have.
 *
 *  `priced` splits what this sentence used to have to hedge about. It is the backend's own
 *  disclosure (`ledger.reader.run_totals`, `RunStats.priced`, and the same word `LoopSpend` uses):
 *  `false` means some completed step booked no cost at all, so the figure is a FLOOR. With it, a
 *  genuinely-free local run and a run nobody costed stop sharing one sentence — they were
 *  indistinguishable from the number alone, which is exactly why the flag exists (#2566). */
export function runCostText(costUsd: number, priced: boolean): string {
  if (!priced) {
    return costUsd > 0
      ? `At least ~${runUsd(costUsd)} this run — some step recorded no cost, so the real total is higher`
      : 'Not recorded — no step on this run booked a cost, so nothing here says what it spent'
  }
  if (!(costUsd > 0)) return 'Nothing — every step was measured and cost nothing (a free local model)'
  return `~${runUsd(costUsd)} this run — estimated from model prices, not a provider-reported charge`
}

/** The compact stat-cell version of the same fact: a figure, or the words that replace it.
 *
 *  Separate from `runCostText` because a `<Stat>` cell has room for a value, not a sentence — but
 *  it must NOT fall back to the raw number when the number is a floor. "~$0.0000" in a cell is the
 *  precise defect #2566 names: a measured-looking figure standing in for "nobody recorded this". */
export function runCostStat(costUsd: number, priced: boolean): string {
  if (!priced) return costUsd > 0 ? `≥~${runUsd(costUsd)}` : 'not recorded'
  return `~${runUsd(costUsd)}`
}

/** A template card's p50/p95 cell. Same rule, one sample wider: an unpriced run anywhere in the
 *  sample makes every percentile drawn from it a floor. */
export function templateCostStat(costUsd: number, priced: boolean): string {
  return priced ? runUsd(costUsd) : `≥${runUsd(costUsd)}`
}

/** The loop cockpit's compact pill: "~$X this run", plus planning when there was any.
 *
 *  Planning is ADDED to the visible text rather than folded into the figure or hidden in a
 *  tooltip. `plan_walkthrough` keys the planner session `loop-plan-<id>`, outside the worker
 *  prefix this figure sums, so the two are genuinely different money; summing them would
 *  overstate "this run" and omitting them silently would imply the loop cost less than it did. */
export function loopSpendPill(spend: LoopSpend): string {
  if (!(spend.dollars_est > 0)) {
    return spend.planning.dollars_est > 0
      ? `~${runUsd(spend.planning.dollars_est)} planning`
      : 'no spend recorded'
  }
  const base = `~${runUsd(spend.dollars_est)}`
  return spend.planning.dollars_est > 0
    ? `${base} + ~${runUsd(spend.planning.dollars_est)} planning`
    : base
}

/** The full sentence behind the pill — what the figure covers, and what it does not.
 *
 *  Every clause here is a fact a reader cannot recover from the number: that it is an estimate,
 *  that it spans the loop's task workers, that planning is counted separately, and — when some
 *  model had no price row — that the total is a FLOOR rather than a total. A money figure that
 *  hides its own incompleteness is the defect this sentence exists to prevent. */
export function loopSpendTitle(spend: LoopSpend): string {
  if (!(spend.dollars_est > 0) && !(spend.planning.dollars_est > 0)) {
    return 'No model spend recorded for this loop — a local model, or one with no price row.'
  }
  const parts: string[] = []
  parts.push(
    spend.dollars_est > 0
      ? `~${runUsd(spend.dollars_est)} across ${spend.turns} ${spend.turns === 1 ? 'turn' : 'turns'} of this loop's worker and its task workers`
      : `No worker spend recorded yet`,
  )
  if (spend.planning.dollars_est > 0) {
    parts.push(
      `~${runUsd(spend.planning.dollars_est)} more in planning, counted separately because the planner runs as its own session`,
    )
  }
  parts.push(
    spend.priced
      ? 'Estimated from model prices, not a provider-reported charge'
      : 'At least this much — some model had no price row, so the real total is higher',
  )
  return parts.join('. ') + '.'
}

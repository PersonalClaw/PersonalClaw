import type { WorkflowRunDetailData } from '../../lib/api'

/** A run's caps and what it has spent against them, as the run page states them.
 *
 *  The figures are the server's (`service.status`): `budget` is what the run is held to (`0` is no
 *  cap), `spend` what its steps and the runs they started booked, and `unpriced_steps` how many of
 *  those steps no price covered, which makes the dollar figure a floor. Nothing here re-derives a
 *  cap or a total: the page says what the engine holds the run to. */
type BudgetRun = Pick<WorkflowRunDetailData, 'tokens' | 'budget' | 'spend' | 'at_budget'>

/** Dollars as the run's own sentences say them: to the cent, or to four figures below a cent. */
export function usd(value: number): string {
  return value >= 0.01 || value === 0 ? `$${value.toFixed(2)}` : `$${Number(value.toPrecision(4))}`
}

const steps = (n: number) => (n === 1 ? '1 step' : `${n} steps`)

/** The caption pieces: tokens against the token cap, dollars against the dollar cap, and what the
 *  dollar figure leaves out. A dimension with no cap and nothing spent says nothing. */
export function budgetLine(run: BudgetRun): string[] {
  const caps = run.budget ?? { max_tokens: 0, max_cost: 0 }
  const spent = run.spend ?? { tokens: run.tokens ?? 0, dollars: 0, unpriced_steps: 0 }
  const out: string[] = []
  if (caps.max_tokens > 0) {
    out.push(`${spent.tokens.toLocaleString()} of ${caps.max_tokens.toLocaleString()} tokens`)
  } else if (spent.tokens > 0) {
    out.push(`${spent.tokens.toLocaleString()} tokens`)
  }
  if (caps.max_cost > 0) {
    out.push(`${usd(spent.dollars)} of ${usd(caps.max_cost)}`)
  } else if (spent.dollars > 0) {
    out.push(`${usd(spent.dollars)} spent`)
  }
  if (spent.unpriced_steps > 0 && (caps.max_cost > 0 || spent.dollars > 0)) {
    out.push(`not counting ${steps(spent.unpriced_steps)} that had no price`)
  }
  return out
}

/** The caps a run paused at its budget has reached, which a resume has to raise. None reached on
 *  a run paused at its budget means its dollar cap could not count a step that had no price: her
 *  Resume lets it go on past that step, so nothing needs raising. */
export function capsReached(run: BudgetRun): { tokens: boolean; dollars: boolean } {
  if (!run.at_budget) return { tokens: false, dollars: false }
  const caps = run.budget ?? { max_tokens: 0, max_cost: 0 }
  const spent = run.spend ?? { tokens: 0, dollars: 0, unpriced_steps: 0 }
  return {
    tokens: caps.max_tokens > 0 && spent.tokens >= caps.max_tokens,
    dollars: caps.max_cost > 0 && spent.dollars >= caps.max_cost,
  }
}

/** Why a raised cap cannot be taken, or `null` when it can: a number of 0 or more (0 is no cap),
 *  a whole number of tokens, and above what the run has spent, or the run would pause again at
 *  once. */
export function raisedCapProblem(raw: string, spent: number, unit: 'tokens' | 'dollars'): string | null {
  const text = raw.trim().replace(/^\$/, '')
  const value = Number(text)
  if (!text || !Number.isFinite(value) || value < 0) return 'Enter a number of 0 or more (0 is no budget).'
  if (unit === 'tokens' && !Number.isInteger(value)) return 'Enter a whole number of tokens.'
  if (value > 0 && value <= spent) {
    return unit === 'tokens'
      ? `It has used ${spent.toLocaleString()} tokens: set more than that, or 0 for no budget.`
      : `It has spent ${usd(spent)}: set more than that, or 0 for no budget.`
  }
  return null
}

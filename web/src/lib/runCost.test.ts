import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { loopSpendPill, loopSpendTitle, runCostText, runUsd } from './runCost'
import type { LoopSpend } from './api'

// The loop cockpit's money figure (MRT-3). Every assertion here is on the NUMBER or on the words
// that qualify it, never on "an element rendered" — the failure mode for this surface is a
// perfectly-rendered pill showing a plausible wrong figure. `~$0.00` looks fine and means
// something false, so a test that only checks presence measures nothing.

const spend = (over: Partial<LoopSpend> = {}): LoopSpend => ({
  dollars_est: 1.25,
  turns: 3,
  tokens: 4200,
  priced: true,
  planning: { dollars_est: 0, turns: 0 },
  ...over,
})

describe('runUsd — one rounding rule, mirroring routing/usage.py::_usd', () => {
  it('uses 2dp at or above a dollar and 4dp below it', () => {
    expect(runUsd(4.2)).toBe('$4.20')
    expect(runUsd(1)).toBe('$1.00')
    expect(runUsd(0.1234)).toBe('$0.1234')
    expect(runUsd(0.0012)).toBe('$0.0012')
  })
})

describe('runCostText — unchanged by the move out of IntrospectPanel', () => {
  it('states the figure and that it is an estimate', () => {
    const text = runCostText(0.1234)
    expect(text).toContain('~$0.1234')
    expect(text).toContain('this run')
    expect(text).toMatch(/estimated from model prices/)
  })

  it('does not render zero as $0.00', () => {
    expect(runCostText(0)).not.toContain('$0.00')
    expect(runCostText(0)).toMatch(/local model|no price row/)
  })
})

describe('loopSpendPill — the visible figure', () => {
  it('shows the summed worker figure', () => {
    expect(loopSpendPill(spend({ dollars_est: 1.25 }))).toBe('~$1.25')
  })

  it('rounds below a dollar to 4dp, so a cheap loop is not shown as free', () => {
    // The regression this guards: 2dp everywhere renders a real $0.0004 loop as "~$0.00".
    expect(loopSpendPill(spend({ dollars_est: 0.0004 }))).toBe('~$0.0004')
    expect(loopSpendPill(spend({ dollars_est: 0.0004 }))).not.toContain('$0.00 ')
  })

  it('adds planning to the VISIBLE text, not only the tooltip', () => {
    const pill = loopSpendPill(spend({ dollars_est: 1.25, planning: { dollars_est: 0.4, turns: 1 } }))
    expect(pill).toContain('~$1.25')
    expect(pill).toContain('~$0.4000')
    expect(pill).toContain('planning')
  })

  it('does NOT fold planning into the headline figure', () => {
    // 1.25 + 0.40 = 1.65. If that number ever appears, the two buckets were summed and "this
    // run" now overstates what the loop's workers actually cost.
    const pill = loopSpendPill(spend({ dollars_est: 1.25, planning: { dollars_est: 0.4, turns: 1 } }))
    expect(pill).not.toContain('1.65')
  })

  it('reports planning alone when the loop has not started working yet', () => {
    const pill = loopSpendPill(spend({ dollars_est: 0, turns: 0, planning: { dollars_est: 0.4, turns: 1 } }))
    expect(pill).toContain('~$0.4000')
    expect(pill).toContain('planning')
  })

  it('says nothing was recorded rather than showing a zero figure', () => {
    const pill = loopSpendPill(spend({ dollars_est: 0, turns: 0 }))
    expect(pill).toBe('no spend recorded')
    expect(pill).not.toContain('$')
  })
})

describe('loopSpendTitle — what the figure covers', () => {
  it('says the figure spans the task workers, which is why it is a prefix read', () => {
    const title = loopSpendTitle(spend({ dollars_est: 1.25, turns: 3 }))
    expect(title).toContain('~$1.25')
    expect(title).toContain('3 turns')
    expect(title).toMatch(/task workers/)
  })

  it('names planning as counted separately, so the exclusion is stated not implied', () => {
    const title = loopSpendTitle(spend({ planning: { dollars_est: 0.4, turns: 2 } }))
    expect(title).toContain('~$0.4000')
    expect(title).toMatch(/separately/)
    expect(title).toMatch(/own session/)
  })

  it('states a FLOOR when some model had no price row', () => {
    const title = loopSpendTitle(spend({ priced: false }))
    expect(title).toMatch(/At least this much/)
    expect(title).toMatch(/higher/)
    // And the honest-estimate wording must NOT also appear — two different claims about one
    // dollar in one sentence is the defect the tilde work fixed in IntrospectPanel.
    expect(title).not.toMatch(/not a provider-reported charge/)
  })

  it('is an estimate disclosure when everything was priced', () => {
    expect(loopSpendTitle(spend({ priced: true }))).toMatch(/Estimated from model prices/)
  })

  it('singularises one turn', () => {
    expect(loopSpendTitle(spend({ turns: 1 }))).toContain('1 turn of')
  })

  it('says nothing was recorded when both buckets are empty', () => {
    const title = loopSpendTitle(spend({ dollars_est: 0, turns: 0 }))
    expect(title).toMatch(/No model spend recorded/)
    expect(title).not.toContain('$0.00')
  })
})

describe('the cockpit must not read spend off the loop entity', () => {
  it('renders from its own state, so an SSE snapshot cannot blank the figure', () => {
    // `GET /api/loops/{id}` carries `spend`; the per-loop SSE snapshot is `store.get_redacted`
    // WITHOUT it. `loopToGoalLoop` spreads the raw loop, so folding spend into `c` would make the
    // pill vanish on the first lifecycle event — a money figure that disappears while the loop is
    // still spending. A source rail, because the defect is structural rather than arithmetic.
    const src = readFileSync(join(__dirname, '../pages/loops/LoopCockpitPage.tsx'), 'utf8')
    expect(src).toContain('loopSpendPill(spend)')
    expect(src).not.toMatch(/\bc\.spend\b/)
    expect(src).not.toMatch(/\bgl\.spend\b/)
    // Vacuity floor: the state really is separate and really is populated from the detail GET.
    expect(src).toContain('useState<LoopSpend | null>(null)')
    expect(src).toContain('setSpend(raw?.spend ?? null)')
  })
})

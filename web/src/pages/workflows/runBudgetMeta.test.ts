/**
 * What the run page says of a run's budget: its caps and its spend beside each other, and what a
 * Resume of a run paused at its budget has to raise.
 */
import { describe, expect, it } from 'vitest'
import { budgetLine, capsReached, raisedCapProblem, usd } from './runBudgetMeta'

describe('budgetLine', () => {
  it('says each cap beside what was spent against it', () => {
    expect(budgetLine({
      budget: { max_tokens: 50000, max_cost: 2 },
      spend: { tokens: 12400, dollars: 1.24, unpriced_steps: 0 },
    })).toEqual(['12,400 of 50,000 tokens', '$1.24 of $2.00'])
  })

  it('says what was spent where there is no cap, and nothing where there is neither', () => {
    expect(budgetLine({ budget: { max_tokens: 0, max_cost: 0 }, spend: { tokens: 900, dollars: 0.3, unpriced_steps: 0 } }))
      .toEqual(['900 tokens', '$0.30 spent'])
    expect(budgetLine({ budget: { max_tokens: 0, max_cost: 0 }, spend: { tokens: 0, dollars: 0, unpriced_steps: 0 } }))
      .toEqual([])
  })

  it('never shows spend no price covered as part of a dollar figure', () => {
    expect(budgetLine({
      budget: { max_tokens: 0, max_cost: 1 },
      spend: { tokens: 360, dollars: 0.04, unpriced_steps: 1 },
    })).toEqual(['360 tokens', '$0.04 of $1.00', 'not counting 1 step that had no price'])
  })

  it('falls back to the run row tokens for a reply from before the budget fields', () => {
    expect(budgetLine({ tokens: 77 })).toEqual(['77 tokens'])
  })
})

describe('capsReached', () => {
  const at = (tokens: number, dollars: number) => ({
    at_budget: true,
    budget: { max_tokens: 100, max_cost: 0.1 },
    spend: { tokens, dollars, unpriced_steps: 0 },
  })

  it('names the cap a run paused at its budget reached', () => {
    expect(capsReached(at(10, 0.12))).toEqual({ tokens: false, dollars: true })
    expect(capsReached(at(120, 0.01))).toEqual({ tokens: true, dollars: false })
  })

  it('names none for a step no price covered, and none for a run not paused at its budget', () => {
    expect(capsReached(at(10, 0.01))).toEqual({ tokens: false, dollars: false })
    expect(capsReached({ ...at(120, 0.12), at_budget: false })).toEqual({ tokens: false, dollars: false })
  })
})

describe('raisedCapProblem', () => {
  it('takes a cap above what was spent, or 0 for none', () => {
    expect(raisedCapProblem('0.50', 0.12, 'dollars')).toBeNull()
    expect(raisedCapProblem('$1', 0.12, 'dollars')).toBeNull()
    expect(raisedCapProblem('0', 0.12, 'dollars')).toBeNull()
    expect(raisedCapProblem('20000', 12400, 'tokens')).toBeNull()
  })

  it('refuses a cap the run would pause at again at once', () => {
    expect(raisedCapProblem('0.11', 0.12, 'dollars')).toBe('It has spent $0.12: set more than that, or 0 for no budget.')
    expect(raisedCapProblem('12400', 12400, 'tokens')).toMatch(/12,400 tokens/)
  })

  it('refuses what is not a cap', () => {
    expect(raisedCapProblem('lots', 0, 'dollars')).toMatch(/number of 0 or more/)
    expect(raisedCapProblem('-1', 0, 'dollars')).toMatch(/number of 0 or more/)
    expect(raisedCapProblem('1.5', 0, 'tokens')).toBe('Enter a whole number of tokens.')
  })
})

describe('usd', () => {
  it('says dollars to the cent, and to four figures below a cent', () => {
    expect(usd(2)).toBe('$2.00')
    expect(usd(0)).toBe('$0.00')
    expect(usd(0.0042)).toBe('$0.0042')
  })
})

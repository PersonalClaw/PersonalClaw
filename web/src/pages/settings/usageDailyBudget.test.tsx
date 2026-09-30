import { describe, expect, it, vi } from 'vitest'
import { act, render } from '@testing-library/react'

// ── "Daily budget": the cap beside the spend it is actually held to ─────────────────────────
//
// The daily cap meters the model calls PersonalClaw makes on its own (automations, loops,
// subagents, background work) over the host's day. This line used to set the ledger's total for
// "Today" beside it: every chat turn included, on a UTC day. A user who chatted $9 of a $1 cap's
// day was told "Spent $9.00 of your $1.00 daily cap" while nothing had paused, and one whose
// automations had spent the cap could read that they had not.

const BUDGET = {
  spent_dollars: 0.25,
  spent_tokens: 1200,
  unpriced_calls: 0,
  max_dollars_per_day: 1,
  max_tokens_per_day: 0,
  cap_unreadable: false,
}

const mount = async (budget: unknown) => {
  vi.resetModules()
  vi.doMock('../../lib/api', () => ({
    api: {
      // $9 of chat today: the ledger's figure, which the cap does not count.
      usageTotals: () => Promise.resolve({
        totals: {
          input_tokens: 90000, output_tokens: 9000, cache_read_tokens: 0, cache_creation_tokens: 0,
          cost_usd: 9, turns: 30, priced: true,
        },
      }),
      usageRollup: () => Promise.resolve({ rows: [] }),
      usageFold: () => Promise.resolve(null),
      usageBudget: () => Promise.resolve(budget),
      modelRates: () => Promise.resolve({ rates: [], models: [], unreadable: '' }),
      personalclawConfig: () => Promise.resolve({ guardrails: { budgets: { max_dollars_per_day: 1 } } }),
      system: () => Promise.resolve({ stats: null }),
    },
  }))
  const { UsagePanel } = await import('./UsagePanel')
  let r!: ReturnType<typeof render>
  await act(async () => {
    r = render(<UsagePanel query={{ period: 'today' }} setQuery={() => {}} />)
    await new Promise((res) => setTimeout(res, 0))
  })
  return r
}

describe('Daily budget', () => {
  it('sets the metered spend beside the cap, never the ledger total', async () => {
    const { container } = await mount(BUDGET)
    const text = container.textContent ?? ''
    expect(text).toContain('Unattended spend today: $0.2500 of your $1.00 daily cap')
    expect(text).not.toContain('$9.00 of your')
    expect(text).toContain('Your chat turns are not capped.')
  })

  it('says what the dollar cap holds: calls running together, no unpriced model, no free one', async () => {
    // Calls fanned out together used to pass one $0 reading and spend 2.6x the cap, a model with
    // no price ran as if free, and a free local one was refused past the cap. The line says what
    // it guarantees now.
    const text = (await mount(BUDGET)).container.textContent ?? ''
    expect(text).toContain(
      'A call that costs money starts only when what it may cost fits in what is left, beside '
      + 'what the calls already running have set aside. A model with no price is refused, and '
      + 'one that costs nothing is not limited by it.',
    )
    const tokensOnly = await mount({ ...BUDGET, max_dollars_per_day: 0, max_tokens_per_day: 50000 })
    expect(tokensOnly.container.textContent ?? '').not.toContain('A call that costs money')
  })

  it('says how many calls the dollar total leaves out, rather than letting them read as free', async () => {
    // A call nothing priced is charged as one the cap could not count. Shown as the whole spend,
    // $0.2500 told the owner those calls were free.
    const { container } = await mount({ ...BUDGET, unpriced_calls: 3 })
    const text = container.textContent ?? ''
    expect(text).toContain('Unattended spend today: $0.2500 of your $1.00 daily cap')
    expect(text).toContain(
      '3 unattended calls today had no price, so the dollar cap could not count them: '
      + 'give their models a price under Model prices below.',
    )
    const one = (await mount({ ...BUDGET, unpriced_calls: 1 })).container.textContent ?? ''
    expect(one).toContain('1 unattended call today had no price, so the dollar cap could not count it:')
  })

  it('says nothing of unpriced calls when there are none, or no dollar cap to count them', async () => {
    const none = (await mount(BUDGET)).container.textContent ?? ''
    expect(none).not.toContain('had no price')
    const tokensOnly = await mount({ ...BUDGET, unpriced_calls: 3, max_dollars_per_day: 0, max_tokens_per_day: 50000 })
    // Tokens are known whatever the price, so a token cap counts every call.
    expect(tokensOnly.container.textContent ?? '').not.toContain('had no price')
  })

  it('states a token cap with its own metered count', async () => {
    const { container } = await mount({ ...BUDGET, max_dollars_per_day: 0, max_tokens_per_day: 50000 })
    const text = container.textContent ?? ''
    expect(text).toContain('Unattended tokens today: 1k of your 50k daily cap')
    expect(text).not.toContain('Unattended spend today')
  })

  it('says a cap it could not read could not be read', async () => {
    const { getByRole } = await mount({
      ...BUDGET, max_dollars_per_day: null, max_tokens_per_day: null, cap_unreadable: true,
    })
    expect(getByRole('status').textContent).toBe('The daily cap could not be read.')
  })

  it('shows nothing when no cap is set', async () => {
    const { container } = await mount({ ...BUDGET, max_dollars_per_day: 0, max_tokens_per_day: 0 })
    expect(container.textContent ?? '').not.toContain('Daily budget')
  })
})

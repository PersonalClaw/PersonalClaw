import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, render, screen } from '@testing-library/react'
import { AGENT_CLI_CAP, AGENT_CLI_USAGE } from './agentCliSpend'

// An agent CLI makes its model calls in its own process, so a turn on one counts only by what the
// CLI says it cost (the gateway's `acp.turn_spend`), and one that says nothing bills on the CLI's
// own account. The Usage page's total, its daily cap and the Guardrails daily budget said nothing
// of it, so each read as covering turns it never saw. 🔴 Red before: no surface said so.

const reply = (o: unknown) =>
  new Response(JSON.stringify(o), { status: 200, headers: { 'Content-Type': 'application/json' } })

afterEach(() => {
  vi.unstubAllGlobals()
  vi.doUnmock('../../lib/api')
  vi.resetModules()
})

describe('a spend surface names the agent CLI turns it leaves out', () => {
  it('the Usage page, beside its total and beside its daily cap', async () => {
    vi.resetModules()
    vi.doMock('../../lib/api', () => ({
      api: {
        usageTotals: () => Promise.resolve({
          totals: {
            input_tokens: 0, output_tokens: 0, cache_read_tokens: 0, cache_creation_tokens: 0,
            cost_usd: 0, turns: 0, priced: true,
          },
        }),
        usageRollup: () => Promise.resolve({ rows: [] }),
        usageFold: () => Promise.resolve(null),
        usageBudget: () => Promise.resolve({
          spent_dollars: 0.25, spent_tokens: 1200, unpriced_calls: 0, max_dollars_per_day: 1,
          max_tokens_per_day: 0, cap_unreadable: false,
        }),
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
    // Read once the render committed, as `usageDailyBudget.test.tsx` reads it.
    const text = r.container.textContent ?? ''
    expect(text).toContain(AGENT_CLI_USAGE)
    expect(text).toContain(AGENT_CLI_CAP)
  })

  it('the Guardrails daily budget, where the cap is set', async () => {
    vi.resetModules()
    vi.stubGlobal('fetch', vi.fn(async (url: string) => {
      if (url === '/api/config/personalclaw') {
        return reply({ guardrails: { budgets: { max_tokens_per_run: 0, max_tokens_per_day: 0, max_dollars_per_day: 5 } } })
      }
      if (url === '/api/incident') return reply({ active: false, reason: '', started_at: '' })
      if (url === '/api/models/health') return reply({ providers: [], callers: [], generated_from: 0 })
      if (url === '/api/autonomy') return reply({ rungs: [], rung_meta: [], incident_active: false, types: [], reversals: [] })
      return reply({})
    }))
    const { GuardrailsPanel } = await import('./GuardrailsPanel')
    await act(async () => { render(<GuardrailsPanel />) })
    await screen.findByRole('spinbutton', { name: 'Max dollars / day' })
    expect(document.body.textContent ?? '').toContain(AGENT_CLI_CAP)
  })

  it('never says a cap covers them', () => {
    for (const said of [AGENT_CLI_USAGE, AGENT_CLI_CAP]) {
      expect(said).toMatch(/only by what the CLI says it cost/)
      expect(said).not.toMatch(/covers|always counted|every agent/i)
    }
  })
})

import { describe, expect, it, vi } from 'vitest'
import { act, render, within } from '@testing-library/react'

// ── Settings → Usage names the provider each answer came from ──────────────────────────────
//
// The ledger's `provider` column held the runtime kind (`native`) until the seam learned to read
// the entry off the model that answered. Nothing on this page rendered the column at all, so the
// fix had nowhere to show: "By model" folds one model id served by two entries into one row.
// "By provider" reads the same ledger grouped by that column.

const ROW = { input_tokens: 100_000, output_tokens: 10_000, cache_read_tokens: 0, cache_creation_tokens: 0, turns: 1 }

const ROLLUPS: Record<string, unknown[]> = {
  model: [{ model: 'gpt-4o', cost_usd: 0.35, priced: true, ...ROW }],
  provider: [
    { provider: 'FakeUp', cost_usd: 0.35, priced: true, ...ROW },
    { provider: 'acp:claude-code', cost_usd: 0, priced: false, ...ROW },
  ],
  source: [{ source: 'room', cost_usd: 0.35, priced: true, ...ROW }],
}

const mount = async (rollup: (o: { group_by: string }) => Promise<unknown>) => {
  vi.resetModules()
  vi.doMock('../../lib/api', async (importOriginal) => ({
    ...(await importOriginal<typeof import('../../lib/api')>()),
    api: {
      usageTotals: () => Promise.resolve({ totals: null }),
      usageRollup: rollup,
      usageFold: () => Promise.resolve(null),
      personalclawConfig: () => Promise.resolve(null),
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

const table = (container: HTMLElement, caption: string) => {
  const found = [...container.querySelectorAll('table')].find((t) => t.textContent?.includes(caption))
  expect(found, `a table captioned ${caption}`).toBeTruthy()
  return found as HTMLTableElement
}

describe('Settings → Usage, by provider', () => {
  it('lists each provider entry with its tokens and cost, and an ACP runtime as it ran', async () => {
    const asked: string[] = []
    const { container } = await mount((o) => {
      asked.push(o.group_by)
      return Promise.resolve({ group_by: o.group_by, rows: ROLLUPS[o.group_by] ?? [] })
    })
    expect(asked).toContain('provider')
    const t = table(container, 'Token usage and cost per provider')
    expect(within(t).getByText('Provider')).toBeTruthy()
    const rows = [...t.querySelectorAll('tbody tr')].map((tr) => [...tr.querySelectorAll('td')].map((td) => td.textContent))
    expect(rows).toEqual([
      ['FakeUp', '110k', '$0.3500', '100%'],
      ['acp:claude-code', '110k', 'unpriced', '—'],
    ])
    // A room's turns are a source of their own, not folded into chat.
    expect(table(container, 'Token usage and cost per source').textContent).toContain('room')
  })

  it('says its own read failed instead of claiming nothing was used', async () => {
    const { container } = await mount((o) =>
      o.group_by === 'provider'
        ? Promise.reject(new Error(''))
        : Promise.resolve({ group_by: o.group_by, rows: ROLLUPS[o.group_by] ?? [] }))
    expect(container.textContent).not.toContain('No provider usage recorded this period.')
    const alerts = [...container.querySelectorAll('[role="alert"]')].map((a) => a.textContent)
    expect(alerts).toEqual(["Couldn't load usage by provider.Retry"])
  })
})

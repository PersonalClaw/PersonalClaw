import { describe, expect, it, vi } from 'vitest'
import { act, render } from '@testing-library/react'

// ── Settings → Usage counts the day the daily cap counts ──────────────────────────────────────
//
// The page worked out "Today" as midnight UTC and "7 days" as 168 hours back, while the daily cap
// beside it counts the gateway's local day. For an evening west of Greenwich the page then said a
// turn cost money today, and the cap line under it said nothing had been spent today. Every read
// now names the period as a window of the gateway's local days, and no read carries a start the
// browser computed.

type Opts = { window?: string; since?: string; until?: string; group_by?: string }

const mount = async (period: string) => {
  const asked: Array<{ read: string; opts: Opts }> = []
  const note = (read: string) => (opts: Opts = {}) => {
    asked.push({ read, opts })
    return Promise.resolve(read === 'rollup' ? { group_by: opts.group_by, rows: [] } : read === 'totals' ? { totals: null } : null)
  }
  vi.resetModules()
  vi.doMock('../../lib/api', async (importOriginal) => ({
    ...(await importOriginal<typeof import('../../lib/api')>()),
    api: {
      usageTotals: note('totals'),
      usageRollup: note('rollup'),
      usageFold: note('fold'),
      usageBudget: () => Promise.resolve(null),
      modelRates: () => Promise.resolve({ rates: [], models: [], unreadable: '' }),
      personalclawConfig: () => Promise.resolve(null),
      system: () => Promise.resolve({ stats: null }),
    },
  }))
  const { UsagePanel } = await import('./UsagePanel')
  await act(async () => {
    render(<UsagePanel query={{ period }} setQuery={() => {}} />)
    await new Promise((res) => setTimeout(res, 0))
  })
  return asked
}

describe('Settings → Usage, the day it counts', () => {
  it.each([
    ['today', 'day'],
    ['7d', 'week'],
    ['30d', 'month'],
  ])('reads %s as the gateway\'s %s window on every read, with no start of its own', async (period, window) => {
    const asked = await mount(period)
    expect(asked.map((a) => a.read).sort()).toEqual(['fold', 'rollup', 'rollup', 'rollup', 'totals'])
    for (const { read, opts } of asked) {
      expect(opts.window, `${read} names the window`).toBe(window)
      expect(opts.since, `${read} carries no start the browser computed`).toBeUndefined()
      expect(opts.until).toBeUndefined()
    }
  })
})

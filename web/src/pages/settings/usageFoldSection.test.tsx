import { describe, expect, it, vi } from 'vitest'
import { act, render } from '@testing-library/react'

// ── "By day and purpose": the fold reaches a user, exclusions included (MRT-3) ─────────────
//
// A backend fold nobody renders is the same defect one layer down, so these assert the SURFACE:
//
//   · the total, the daily shape, and the per-purpose split are on screen
//   · an unpriced model reads as a FLOOR, never as "$0.00 spent"
//   · the unattended spend the fold refuses to sum is STATED with its size — the whole reason this
//     atom was blocked was that merging it would double-count, and silently omitting it would
//     claim a completeness the data does not have
//   · every dollar carries a "~" — each is a price-table estimate, not a provider charge
//
// A purpose the fold did not return is absent rather than a confident 0 (`eval` has no writer yet).

const FOLD = {
  window: 'week',
  group: 'purpose',
  dates: ['2026-08-12', '2026-08-13'],
  rows: [
    {
      key: 'interactive', calls: 20, tokens_in: 8000, tokens_out: 800, tokens: 8800,
      dollars_est: 1.0, estimated_dollars: 1.0, estimated_share: 1.0,
      unpriced_calls: 0, local_calls: 0, priced: true,
    },
    {
      key: 'app', calls: 3, tokens_in: 21, tokens_out: 6, tokens: 27,
      dollars_est: 0.09, estimated_dollars: 0.09, estimated_share: 1.0,
      unpriced_calls: 0, local_calls: 0, priced: true,
    },
    {
      key: 'background', calls: 15, tokens_in: 550, tokens_out: 55, tokens: 605,
      dollars_est: 0.02, estimated_dollars: 0.02, estimated_share: 1.0,
      unpriced_calls: 5, local_calls: 0, priced: false,
    },
    {
      key: 'loop', calls: 12, tokens_in: 2400, tokens_out: 240, tokens: 2640,
      dollars_est: 0.0, estimated_dollars: 0.0, estimated_share: 0.0,
      unpriced_calls: 0, local_calls: 12, priced: true,
    },
  ],
  total: {
    key: '', calls: 50, tokens_in: 10971, tokens_out: 1101, tokens: 12072,
    dollars_est: 1.11, estimated_dollars: 1.11, estimated_share: 1.0,
    unpriced_calls: 5, local_calls: 12, priced: false,
  },
  series: [
    { date: '2026-08-12', calls: 32, dollars_est: 1.0, tokens: 11240 },
    { date: '2026-08-13', calls: 18, dollars_est: 0.11, tokens: 632 },
  ],
  estimated_share: 1.0,
  unmapped: {},
  app_sources: { 'weather-app': 3 },
  uncounted: {
    calls: 12,
    total_calls: 12,
    total_dollars_est: 4.0,
    by_use_case: { reasoning: 8, loops: 4 },
  },
  reachable_purposes: ['interactive', 'background', 'loop', 'app'],
}

const mount = async (fold: unknown = FOLD) => {
  vi.resetModules()
  vi.doMock('../../lib/api', () => ({
    api: {
      usageTotals: () => Promise.resolve({ totals: null }),
      usageRollup: () => Promise.resolve({ rows: [] }),
      usageFold: () => Promise.resolve(fold),
      personalclawConfig: () => Promise.resolve(null),
      system: () => Promise.resolve({ stats: null }),
    },
  }))
  const { UsagePanel } = await import('./UsagePanel')
  let r!: ReturnType<typeof render>
  // useQuery resolves async, so render AND flush both sit inside act().
  await act(async () => {
    r = render(<UsagePanel query={{ period: '7d' }} setQuery={() => {}} />)
    await new Promise((res) => setTimeout(res, 0))
  })
  return r
}

describe('the By day and purpose section', () => {
  it('renders the local share and does NOT restate the tiles above it', async () => {
    const { container } = await mount()
    expect(container.textContent).toContain('By day and purpose')
    // 12 of 50 local — the one headline figure the BigStat row does not carry.
    expect(container.textContent).toContain('24%')
    expect(container.textContent).toContain('of these turns ran locally at $0')
    // The section must NOT repeat cost/tokens/turns: the tiles ~100px above already say them for
    // the same window, and driving the page is what made that duplication obvious.
    expect(container.textContent).not.toContain('over 50 turns')
  })

  it('splits spend by purpose with an accessible share meter each', async () => {
    const { container } = await mount()
    const meters = container.querySelectorAll('[role="progressbar"]')
    expect(meters.length).toBe(4) // the four purposes the fold returned, NOT all five
    const labels = [...meters].map((m) => m.getAttribute('aria-label'))
    expect(labels).toContain('Interactive — turns you watched — share of spend')
    expect(labels).toContain('Loops — share of spend')
    expect(labels).toContain('Apps — share of spend')
    // A purpose no writer can produce is absent, not a confident zero row.
    expect(container.textContent).not.toContain('Evaluations')
  })

  it('states the unattended spend it refuses to sum, with its size', async () => {
    const { container } = await mount()
    expect(container.textContent).toContain('Not included:')
    expect(container.textContent).toContain('12 unattended model calls')
    expect(container.textContent).toContain('~$4.00')
    expect(container.textContent).toContain('double-counting loops')
  })

  it('states that an unpriced model makes the total a floor', async () => {
    const { container } = await mount()
    expect(container.textContent).toContain('Floor')
    expect(container.textContent).toContain('5 turns ran on a model with no price row')
    expect(container.textContent).toContain('Real spend is higher')
  })

  it('names the app that spent', async () => {
    const { container } = await mount()
    expect(container.textContent).toContain('App spend came from weather-app')
  })

  it('discloses that every figure is an estimate', async () => {
    const { container } = await mount()
    expect(container.textContent).toContain('computed from the price table, not')
  })

  it('gives the daily chart a text alternative instead of a mute row of divs', async () => {
    const { container } = await mount()
    const chart = container.querySelector('[role="img"]')
    expect(chart).not.toBeNull()
    expect(chart!.getAttribute('aria-label')).toContain('highest ~$1.00 on 2026-08-12')
    // One bar per window day, each carrying its own day/amount tooltip.
    const bars = [...chart!.querySelectorAll('[title]')].map((b) => b.getAttribute('title'))
    expect(bars).toEqual([
      '2026-08-12: ~$1.00 over 32 turns',
      '2026-08-13: ~$0.1100 over 18 turns',
    ])
  })

  it('says nothing happened rather than showing $0.00 when the window is empty — but still states the exclusion', async () => {
    const empty = {
      ...FOLD,
      rows: [],
      total: { ...FOLD.total, calls: 0, dollars_est: 0, unpriced_calls: 0, local_calls: 0, priced: true },
      series: [],
      app_sources: {},
    }
    const { container } = await mount(empty)
    expect(container.textContent).toContain('No turns recorded')
    expect(container.textContent).not.toContain('Floor')
    // An empty window does NOT mean nothing was spent — the excluded log still gets said.
    expect(container.textContent).toContain('12 unattended model calls')
  })

  it('renders nothing at all when the fold cannot be read', async () => {
    vi.resetModules()
    vi.doMock('../../lib/api', () => ({
      api: {
        usageTotals: () => Promise.resolve({ totals: null }),
        usageRollup: () => Promise.resolve({ rows: [] }),
        usageFold: () => Promise.reject(new Error('nope')),
        personalclawConfig: () => Promise.resolve(null),
        system: () => Promise.resolve({ stats: null }),
      },
    }))
    const { UsagePanel } = await import('./UsagePanel')
    let container!: HTMLElement
    await act(async () => {
      container = render(<UsagePanel query={{}} setQuery={() => {}} />).container
      await new Promise((res) => setTimeout(res, 0))
    })
    // A failed read must not invent a $0 section — better silent than confidently wrong. Asserted
    // on the section's own copy, since the panel header MENTIONS the section by name.
    expect(container.textContent).not.toContain('The same money as above')
    expect(container.querySelector('[role="img"]')).toBeNull()
  })
})

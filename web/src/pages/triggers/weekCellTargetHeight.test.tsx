import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, waitFor } from '@testing-library/react'

// ── A week cell you can click is a 44 px tall target ────────────────────────────────────────────
//
// A cell with fires is a control: it opens the trigger it belongs to. It was drawn `h-6`, so on a
// phone every one of them was 24 px tall, under the 44 px touch-target rule at every compact width
// the UI checks measure (320, 390, 412 and 768). Measured under a fixed clock on the seeded home,
// every fire cell in the week was under 44 px at all four widths.
//
// This fixes the HEIGHT. At 390 and 320 the seven day columns cannot also be 44 px wide beside the
// hour column, so those widths keep a width finding of their own.
//
// jsdom has no layout, so the class is the honest thing to pin: it is what sets the box.

const WEEK = {
  start: '2026-09-18T00:00:00', end: '2026-09-25T00:00:00', server_tz: 'UTC',
  truncated: [] as string[],
  occurrences: [
    { trigger_id: 'schedule:a', trigger_name: 'digest', at: 1789743600, suppressed_by: '', reason: '' },
  ],
}

// The grid draws the week that holds TODAY, so the clock is pinned to the planted fire's week (only
// `Date` is faked, so `waitFor`'s own timers still run).
afterEach(() => { vi.useRealTimers() })

beforeEach(() => {
  vi.resetModules()
  sessionStorage.clear()
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(new Date(1789743600 * 1000 - 3600 * 1000))
  vi.doMock('../../lib/api', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    api: { triggersWeek: () => Promise.resolve(WEEK) },
  }))
})

describe('the week grid', () => {
  it('draws a clickable cell at least 44 px tall', async () => {
    const { WeekGridView } = await import('./WeekGridView')
    render(<WeekGridView onOpenTrigger={() => {}} />)
    const cells = await waitFor(() => {
      const found = [...document.querySelectorAll('td[role="button"]')]
      expect(found.length, 'the fire is drawn as a clickable cell').toBeGreaterThan(0)
      return found
    })
    for (const td of cells) {
      expect(td.className, 'a 24 px cell is under the 44 px touch-target rule').not.toMatch(/\bh-6\b/)
      expect(td.className, 'h-11 is 44 px').toMatch(/\bh-11\b/)
    }
  })
})

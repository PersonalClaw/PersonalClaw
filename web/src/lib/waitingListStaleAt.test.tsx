// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest'
import { act, render } from '@testing-library/react'
import { useStaleAt } from './waitingList'

// ── An item answerable for weeks does not spin the clock ─────────────────────────────────────────
//
// `useStaleAt` re-reads the time when the next item stops being answerable. A timer longer than a
// browser holds (2^31 - 1 ms, about 24.8 days) fires at once, so an item a month out re-armed a
// zero-length timer on every render. The delay is clamped to the longest a timer holds.

afterEach(() => { vi.useRealTimers() })

describe('useStaleAt', () => {
  it('asks a timer for no longer than a browser holds, for an item a month out', () => {
    const spy = vi.spyOn(window, 'setTimeout')
    const month = new Date(Date.now() + 31 * 24 * 3600_000).toISOString()
    function Probe() { useStaleAt([{ expires_at: month }]); return null }
    render(<Probe />)
    const delays = spy.mock.calls.map((c) => Number(c[1] ?? 0))
    expect(delays.length).toBeGreaterThan(0)
    expect(Math.max(...delays)).toBeLessThanOrEqual(2 ** 31 - 1)
    spy.mockRestore()
  })

  it('still re-reads the time the moment an item expires', async () => {
    vi.useFakeTimers()
    const soon = new Date(Date.now() + 5_000).toISOString()
    const seen: number[] = []
    function Probe() { seen.push(useStaleAt([{ expires_at: soon }])); return null }
    render(<Probe />)
    await act(async () => { await vi.advanceTimersByTimeAsync(5_100) })
    expect(seen.length).toBe(2)
    expect(seen[1]).toBeGreaterThanOrEqual(Date.parse(soon))
  })
})

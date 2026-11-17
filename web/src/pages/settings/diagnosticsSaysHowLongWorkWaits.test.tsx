import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, render, screen, within } from '@testing-library/react'

// ── Settings › Diagnostics says how long the gateway keeps work waiting ───────────────────────
//
// When pages are slow to fill, the reason is one of two: work holding the gateway's event loop,
// or work waiting for one of its worker threads. The gateway's always-on gauge measures both
// (`GET /api/resilience/loop`); the panel shows each now, usually, and at its worst in the last
// minute and ten, and reads it again while open.

let answers: unknown[] = []
let asked = 0
vi.mock('../../lib/api', async (orig) => {
  const real = await orig<typeof import('../../lib/api')>()
  return {
    ...real,
    api: new Proxy(real.api, {
      get: (target, prop: string) => {
        if (prop === 'loopGauge') return () => {
          asked += 1
          const a = answers[Math.min(asked, answers.length) - 1]
          return a === 'fail' ? Promise.reject(new Error('down')) : Promise.resolve(a)
        }
        return (target as unknown as Record<string, unknown>)[prop]
      },
    }),
  }
})

const { GatewayWaitSection } = await import('./GatewayWaitSection')
const { resetDataStore } = await import('../../lib/data')

const figures = (now: number, p95: number, m1: number, m10: number) =>
  ({ last_sample_s_ago: 0.2, now_ms: now, p50_ms: now, p95_ms: p95, max_1m_ms: m1, max_10m_ms: m10 })

const GAUGE = {
  sampling: true,
  sampled_every_ms: 500,
  running_for_s: 3600,
  loop_lag: figures(2, 12, 180, 2014),
  pool_wait: figures(0.4, 3, 6400, 9600),
  pool: { workers: 22, started: 22, queued_now: 7, queued_max_1m: 31, queued_max_10m: 40 },
  cpu_count: 18,
}

beforeEach(() => { resetDataStore(); asked = 0; answers = [GAUGE] })
afterEach(() => {
  cleanup()
  vi.useRealTimers()
  delete (document as unknown as Record<string, unknown>).hidden  // the own property a test set
})

const settle = () => act(async () => { await new Promise((resolve) => setTimeout(resolve, 30)) })

describe('the Diagnostics wait section', () => {
  it('shows the loop’s lag and the worker wait, now, usually and at their worst', async () => {
    render(<GatewayWaitSection />)
    await settle()

    const loop = screen.getByRole('group', { name: 'The gateway’s loop' })
    expect(within(loop).getAllByRole('term').map((c) => c.textContent))
      .toEqual(['Now', 'Usually (95%)', 'Worst, last minute', 'Worst, last 10 minutes'])
    expect(within(loop).getAllByRole('definition').map((c) => c.textContent)).toEqual(['2 ms', '12 ms', '180 ms', '2.0 s'])
    const pool = screen.getByRole('group', { name: 'Waiting for a worker' })
    expect(within(pool).getAllByRole('definition').map((c) => c.textContent)).toEqual(['0 ms', '3 ms', '6.4 s', '9.6 s'])
    expect(pool.textContent).toContain('22 worker threads; 7 waiting now, 31 at most in the last minute.')
  })

  it('reads the gauge again while it is open', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    answers = [GAUGE, { ...GAUGE, loop_lag: figures(900, 950, 990, 2014) }]
    render(<GatewayWaitSection />)
    await act(async () => { await vi.advanceTimersByTimeAsync(5100) })

    expect(asked).toBeGreaterThanOrEqual(2)
    const loop = screen.getByRole('group', { name: 'The gateway’s loop' })
    expect(within(loop).getAllByRole('definition')[0].textContent).toBe('900 ms')
  })

  it('reads nothing again while the tab is hidden', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    render(<GatewayWaitSection />)
    await act(async () => { await vi.advanceTimersByTimeAsync(100) })
    expect(asked).toBe(1)

    Object.defineProperty(document, 'hidden', { configurable: true, get: () => true })
    document.dispatchEvent(new Event('visibilitychange'))
    await act(async () => { await vi.advanceTimersByTimeAsync(16_000) })

    expect(asked, 'a hidden tab kept asking').toBe(1)
  })

  it('says it is not measuring when the gauge stopped sampling, not a calm 0 ms', async () => {
    // A stopped gauge's window empties, and every figure falls to 0.
    const zero = { ...figures(0, 0, 0, 0), last_sample_s_ago: 75 }
    answers = [{ ...GAUGE, sampling: false, loop_lag: zero, pool_wait: zero }]
    render(<GatewayWaitSection />)
    await settle()

    expect(screen.getByText(
      'The gateway is not measuring how long it keeps work waiting right now: its last reading is 75 s old.',
    )).toBeTruthy()
    expect(screen.queryByRole('group', { name: 'The gateway’s loop' }), 'figures shown for a stopped gauge').toBeNull()
  })

  it('says so when the gateway does not answer', async () => {
    answers = ['fail']
    render(<GatewayWaitSection />)
    await settle()

    expect(screen.getByText('The gateway did not say how long it keeps work waiting.')).toBeTruthy()
  })

  it('says the same when an answer comes without the figures', async () => {
    // An older gateway answers this path with something else, or a stand-in answers `{}`.
    answers = [{ ok: true }]
    render(<GatewayWaitSection />)
    await settle()

    expect(screen.getByText('The gateway did not say how long it keeps work waiting.')).toBeTruthy()
    expect(screen.queryByRole('group', { name: 'The gateway’s loop' })).toBeNull()
  })
})

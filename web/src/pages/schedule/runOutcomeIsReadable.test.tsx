import { beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import type { ScheduleJob } from '../../lib/api'

// ── What a run recorded is said where it can be read ─────────────────────────────────────────────
//
// The schedule panel said what the run it started recorded ("Run finished", "Waiting for you") as
// the Run button's own label for a couple of seconds, and kept the button disabled meanwhile, so the
// words were drawn at the disabled 40% opacity, in the run's tone: faint enough to miss, and a
// screen reader heard nothing. The button now says it is running while it is (announced as busy),
// and what the run recorded is a status line of its own, at full strength, with the button back to
// what it does.

const { API } = vi.hoisted(() => ({
  API: {
    runSchedule: vi.fn(),
    triggerHistory: vi.fn(() => Promise.resolve({ runs: [], total: 0, supported: true })),
    triggerRunDetail: vi.fn(),
    channels: vi.fn(() => Promise.resolve([])),
  },
}))

vi.mock('../../lib/api', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  api: API,
}))

const { ScheduleDetail } = await import('./ScheduleDetail')

function job(over: Partial<ScheduleJob> = {}): ScheduleJob {
  return {
    id: 'clock:balance', name: 'Check my balance', message: '', enabled: true,
    schedule: 'every day at 09:00', every_secs: null, cron_expr: '0 9 * * *',
    action: { provider: 'browse', config: { goal: 'read my balance' } },
    last_run_ts: null, last_status: 'ok', run_count: 0,
    next_run_ts: null, is_running: false, warnings: [], broken: [],
    ...over,
  }
}

const props = { providers: [], onSaved: vi.fn(), onDeleted: vi.fn(), onChanged: vi.fn(), editing: false, onEditingChange: vi.fn() }

beforeEach(() => {
  cleanup()
  for (const fn of Object.values(API)) fn.mockClear()
})

/** The opacity a person sees an element at: its own times every ancestor's. */
function seenOpacity(el: HTMLElement): number {
  let opacity = 1
  for (let n: HTMLElement | null = el; n; n = n.parentElement) {
    const own = Number.parseFloat(getComputedStyle(n).opacity || '1')
    opacity *= Number.isNaN(own) ? 1 : own
    if (n.matches(':disabled, [aria-disabled="true"]') && /opacity-40/.test(n.className)) opacity *= 0.4
  }
  return opacity
}

describe('the schedule panel after Run now', () => {
  it('says it is running while it is, and says so to assistive tech', async () => {
    API.runSchedule.mockResolvedValue({ ok: true, result: 'ran', status: 'waiting' })
    render(<ScheduleDetail job={job()} {...props} />)
    fireEvent.click(screen.getByRole('button', { name: /Run now/ }))
    await screen.findByText('Running…')
    expect(screen.getByRole('button', { name: /Run now/ }).getAttribute('aria-busy')).toBe('true')
  })

  it('🔴 says what the run recorded as a status of its own, not on a disabled button', async () => {
    API.runSchedule.mockResolvedValue({ ok: true, result: 'ran', status: 'waiting' })
    const view = render(<ScheduleDetail job={job()} {...props} />)
    fireEvent.click(screen.getByRole('button', { name: /Run now/ }))
    await screen.findByText('Running…')
    view.rerender(<ScheduleDetail job={job({ last_run_ts: 1790367812.7 })} {...props} />)

    const status = await screen.findByRole('status')
    expect(status.textContent).toBe('Waiting for you')
    expect(status.closest('button')).toBeNull()
    expect(seenOpacity(status)).toBe(1)
    // The button is back to what it does, and can be pressed.
    const run = screen.getByRole('button', { name: /Run now/ })
    expect(run.hasAttribute('disabled') || run.getAttribute('aria-disabled') === 'true').toBe(false)
  })
})

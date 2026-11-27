import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import type { SchedulePreview, SchedulePreviewBody } from '../../lib/api'

// ── The form shows a schedule's next runs while it is edited ───────────────────────────────────
//
// A schedule showed one next run, so a cadence that landed on a clock change, a skip date or quiet
// hours surprised its owner only after it fired. Under the When controls the form now shows the
// next five runs as the clock will make them (`POST /api/triggers/preview`), a skipped day struck
// through and each note in words, and the automation's detail shows its next three. These tests
// drive the form against a fake preview route: what they pin is what the owner reads, and that
// typing never draws an older answer over a newer one.

const h = vi.hoisted(() => ({
  asked: [] as SchedulePreviewBody[],
  answer: (_body: SchedulePreviewBody): Promise<SchedulePreview> => Promise.reject(new Error('unset')),
  channels: vi.fn(() => Promise.resolve([])),
}))

vi.mock('../../lib/api', async () => {
  const actual = await vi.importActual<typeof import('../../lib/api')>('../../lib/api')
  return {
    ...actual,
    api: {
      ...actual.api,
      channels: h.channels,
      previewSchedule: (body: SchedulePreviewBody, opts: { signal?: AbortSignal } = {}) => {
        h.asked.push(body)
        const answer = h.answer(body)
        return new Promise<SchedulePreview>((resolve, reject) => {
          opts.signal?.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')))
          answer.then(resolve, reject)
        })
      },
    },
  }
})
vi.mock('../../lib/agents', () => ({
  useAgentCatalog: () => ({ options: [] }),
  useModelCatalog: () => ({ options: [] }),
}))

const { ScheduleForm, emptyDraft } = await import('./ScheduleForm')
const { NextRuns, PREVIEW_DEBOUNCE_MS } = await import('./NextRuns')

const ZONE = Intl.DateTimeFormat().resolvedOptions().timeZone

function preview(over: Partial<SchedulePreview> = {}): SchedulePreview {
  return {
    outcome: 'ok', problem: '', zone: ZONE, cadence: 'At 09:00 AM, Monday through Friday', next_cursor: null,
    items: [
      { at: '2026-12-01T14:00:00+00:00', wall: 'Tue 1 Dec 2026, 09:00', runs: true, note_kind: '', note: '' },
      { at: '2026-12-02T14:00:00+00:00', wall: 'Wed 2 Dec 2026, 09:00', runs: true, note_kind: '', note: '' },
      { at: '2026-12-03T14:00:00+00:00', wall: 'Thu 3 Dec 2026, 09:00', runs: false, note_kind: 'skip_date', note: 'Skipped: 3 Dec 2026 is one of its skip dates.' },
      { at: '2026-12-04T14:00:00+00:00', wall: 'Fri 4 Dec 2026, 09:00', runs: true, note_kind: '', note: '' },
      { at: '2026-12-07T14:00:00+00:00', wall: 'Mon 7 Dec 2026, 09:00', runs: true, note_kind: '', note: '' },
      { at: '2026-12-08T14:00:00+00:00', wall: 'Tue 8 Dec 2026, 09:00', runs: true, note_kind: '', note: '' },
    ],
    ...over,
  }
}

const weekday = () => ({ ...emptyDraft(), kind: 'cron' as const, cron: '0 9 * * 1-5', skip_dates: ['2026-12-03'] })

async function settle() {
  await act(async () => { vi.advanceTimersByTime(PREVIEW_DEBOUNCE_MS + 10) })
  await act(async () => { await Promise.resolve() })
}

beforeEach(() => { vi.useFakeTimers(); h.asked = []; h.answer = () => Promise.resolve(preview()) })
afterEach(() => { cleanup(); vi.useRealTimers() })

describe('the next runs under the When controls', () => {
  it('🔴 lists the next runs for a weekday cron, a skipped day struck through and said', async () => {
    render(<ScheduleForm draft={weekday()} onChange={() => {}} />)
    expect(screen.getByText('Working out the next runs…')).toBeTruthy()
    await settle()
    const list = screen.getByRole('list', { name: 'Next runs' })
    const rows = within(list).getAllByRole('listitem')
    expect(rows).toHaveLength(6)
    const skipped = rows[2]
    expect(within(skipped).getByText('Thu 3 Dec 2026, 09:00').className).toContain('line-through')
    expect(within(skipped).getByText('skipped')).toBeTruthy()
    expect(within(skipped).getByText('Skipped: 3 Dec 2026 is one of its skip dates.').className).toContain('text-warn')
    expect(h.asked[0]).toEqual({ count: 5, cron: '0 9 * * 1-5', timezone: '', skip_dates: ['2026-12-03'], strict_schedule: false })
  })

  it('keeps its announcer inside a positioned box of its own, so 200% text cannot carry it past the frame', async () => {
    // `sr-only` is absolute. With no positioned parent the announcer sat where the form would put it, and at
    // 200% text that was past the app's frame, which then clipped (measured in the browser: the frame
    // scrolled to exactly the announcer's bottom). jsdom has no layout, so this holds the box it needs.
    const { container } = render(<ScheduleForm draft={weekday()} onChange={() => {}} />)
    for (const phase of ['while it works them out', 'once they are shown']) {
      const announcers = [...container.querySelectorAll('[aria-live="polite"].sr-only')]
      expect(announcers.length, phase).toBeGreaterThan(0)
      for (const a of announcers) expect(a.parentElement?.classList.contains('relative'), phase).toBe(true)
      await settle()
    }
  })

  it('says the zone when it is not this browser’s', async () => {
    h.answer = () => Promise.resolve(preview({ zone: ZONE === 'Pacific/Chatham' ? 'Asia/Kolkata' : 'Pacific/Chatham' }))
    render(<ScheduleForm draft={weekday()} onChange={() => {}} />)
    await settle()
    expect(screen.getByText(/^Times are in (Pacific\/Chatham|Asia\/Kolkata)\.$/)).toBeTruthy()
  })

  it('says a one-time run that has passed never runs', async () => {
    h.answer = () => Promise.resolve(preview({ outcome: 'never', problem: 'That time has passed.', items: [] }))
    render(<ScheduleForm draft={{ ...emptyDraft(), kind: 'at', at: '2020-01-01T09:00' }} onChange={() => {}} />)
    await settle()
    expect(screen.getByText('It never runs: That time has passed.')).toBeTruthy()
  })

  it('shows the server’s refusal only when the field’s own check passes', async () => {
    h.answer = () => Promise.resolve(preview({ outcome: 'refused', problem: 'skip_dates[0]: not a date', items: [] }))
    const { rerender } = render(<ScheduleForm draft={weekday()} onChange={() => {}} />)
    await settle()
    expect(screen.getByText('skip_dates[0]: not a date')).toBeTruthy()
    rerender(<ScheduleForm draft={{ ...weekday(), cron: '99 99 * * *' }} onChange={() => {}} />)
    await settle()
    expect(screen.queryByText('skip_dates[0]: not a date')).toBeNull()
  })

  it('says it could not work them out, and Retry asks again', async () => {
    h.answer = () => Promise.reject(new Error('gateway unreachable'))
    render(<ScheduleForm draft={weekday()} onChange={() => {}} />)
    await settle()
    expect(screen.getByRole('alert').textContent).toBe('Couldn’t work out the next runs.Retry')
    h.answer = () => Promise.resolve(preview())
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
    await settle()
    expect(screen.getByRole('list', { name: 'Next runs' })).toBeTruthy()
    expect(h.asked).toHaveLength(2)
  })

  it('🔴 asks once per pause in typing, and never draws an older answer over a newer one', async () => {
    let release: (p: SchedulePreview) => void = () => {}
    h.answer = (body) => body.cron === '0 9 * * 1-5'
      ? new Promise<SchedulePreview>((resolve) => { release = resolve })
      : Promise.resolve(preview({ cadence: 'At 10:00 AM', items: [{ at: '2026-12-01T15:00:00+00:00', wall: 'Tue 1 Dec 2026, 10:00', runs: true, note_kind: '', note: '' }] }))
    const { rerender } = render(<ScheduleForm draft={weekday()} onChange={() => {}} />)
    await settle()
    rerender(<ScheduleForm draft={{ ...weekday(), cron: '0 1' }} onChange={() => {}} />)
    rerender(<ScheduleForm draft={{ ...weekday(), cron: '0 10 * * *' }} onChange={() => {}} />)
    await settle()
    expect(h.asked.map((b) => b.cron)).toEqual(['0 9 * * 1-5', '0 10 * * *'])
    await act(async () => { release(preview()); await Promise.resolve() })
    expect(screen.getByText('Tue 1 Dec 2026, 10:00')).toBeTruthy()
    expect(screen.queryByText('Tue 1 Dec 2026, 09:00')).toBeNull()
  })

  it('describes the cron field’s schedule in words', async () => {
    render(<ScheduleForm draft={weekday()} onChange={() => {}} />)
    await settle()
    expect(screen.getByText('At 09:00 AM, Monday through Friday')).toBeTruthy()
  })

  it('announces only the first run, and only when it changes', async () => {
    const { rerender } = render(<ScheduleForm draft={weekday()} onChange={() => {}} />)
    await settle()
    const region = document.querySelector('[aria-live="polite"]') as HTMLElement
    expect(region.textContent).toBe('Next run Tue 1 Dec 2026, 09:00')
    rerender(<ScheduleForm draft={{ ...weekday(), skip_dates: ['2026-12-03', '2026-12-25'] }} onChange={() => {}} />)
    await settle()
    expect(region.textContent).toBe('Next run Tue 1 Dec 2026, 09:00')
  })

  it('asks nothing for a one-time run with no time picked yet', async () => {
    render(<ScheduleForm draft={{ ...emptyDraft(), kind: 'at', at: '' }} onChange={() => {}} />)
    await settle()
    expect(h.asked).toEqual([])
    expect(screen.queryByText('Working out the next runs…')).toBeNull()
  })
})

describe('the next runs in the automation’s detail', () => {
  it('lists three runs of the automation it names, with no controls', async () => {
    h.answer = () => Promise.resolve(preview({ items: preview().items.slice(0, 3) }))
    render(<NextRuns body={{ trigger_id: 'schedule:clock:morning', count: 3 }} readOnly />)
    await settle()
    expect(within(screen.getByRole('list', { name: 'Next runs' })).getAllByRole('listitem')).toHaveLength(3)
    expect(screen.queryByRole('button')).toBeNull()
    expect(h.asked).toEqual([{ trigger_id: 'schedule:clock:morning', count: 3 }])
  })
})

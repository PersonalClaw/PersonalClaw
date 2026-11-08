import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { ScheduleWidget } from './ScheduleWidget'
import type { ScheduleRun } from '../../../lib/api'
import { HOSTILE, MD_LINE, inert, plain, rendersLine } from '../../../test/markdownSinks'

// ── Recent activity: a run's reason renders as the line it is ─────────────────────────────────────
//
// 🔴 A run's reason is a hook's or an agent's words, and the row showed their backticks and asterisks.
// Now the reason renders as a line beside our own outcome word, and the row's name reads it as plain
// text.

const run = vi.hoisted(() => ({ reason: '' }))
vi.mock('../DashboardLive', () => ({
  useDashboardLive: () => ({
    schedule: [{
      id: 'r1', trigger_id: 'schedule:clock:digest', trigger_name: 'Nightly digest', outcome: 'failed',
      reason: run.reason, weight: 'full',
      started_at: '2026-09-06T09:23:56.971775+00:00', finished_at: '2026-09-06T09:23:56.971811+00:00',
    }] satisfies ScheduleRun[],
    scheduleDidIds: ['r1'],
    scheduleSuppressed: 0,
  }),
}))

const route = { sub: '', navigate: vi.fn(), navEpoch: 0, query: {}, setQuery: vi.fn() }

/** The row's button (an overlay inside the row) and the line under its name. */
function row() {
  const button = screen.getAllByRole('button').find((b) => (b.getAttribute('aria-label') ?? '').startsWith('Nightly digest'))!
  return { button, line: button.parentElement!.querySelector('p[data-type="body-m"]')! }
}

describe("a run's reason renders as a line", () => {
  it('renders its marks beside our outcome word, and the row is named in plain text', () => {
    run.reason = MD_LINE
    render(<ScheduleWidget {...route} />)
    const { button, line } = row()
    expect(rendersLine(line)).toEqual([])
    expect(line.textContent).toBe('failed · Skip model_calls in export_digest() in published.py')
    expect(plain(button.getAttribute('aria-label') ?? '')).toEqual([])
    // The name is cut to its length (`rowSubject`), and what it keeps of the reason is the plain line.
    expect(button.getAttribute('aria-label')).toMatch(/^Nightly digest — failed — Skip model_calls in export_d/)
  })

  it('a hostile reason is inert', () => {
    run.reason = HOSTILE
    render(<ScheduleWidget {...route} />)
    expect(inert(row().line)).toEqual([])
  })
})

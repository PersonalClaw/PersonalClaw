import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen } from '@testing-library/react'

// ── A partial week names its triggers as the list does ──────────────────────────────────────────
//
// A trigger that fires more often than the grid can plot is named in the notice above the week. The
// notice joined the week answer's `truncated` keys as they came ("schedule:system:heartbeat-tasks"),
// so it named each by its internal id. Each key is an occurrence's `trigger_id`, whose
// `trigger_name` is the name the list shows: the notice says that.

const AT = 1789743600
const fires = (id: string, name: string) => Array.from({ length: 3 }, (_, i) => (
  { trigger_id: id, trigger_name: name, at: AT + i * 60, suppressed_by: '', reason: '' }))
const WEEK = {
  start: '2026-09-18T00:00:00', end: '2026-09-25T00:00:00', server_tz: 'UTC',
  truncated: ['schedule:system:heartbeat-tasks', 'schedule:system:self-remediation'],
  occurrences: [...fires('schedule:system:heartbeat-tasks', 'Heartbeat tasks'), ...fires('schedule:system:self-remediation', 'Self-remediation')],
}

afterEach(() => { vi.useRealTimers() })

beforeEach(() => {
  vi.resetModules()
  sessionStorage.clear()
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(new Date(AT * 1000 - 3600 * 1000))
  vi.doMock('../../lib/api', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    api: { triggersWeek: () => Promise.resolve(WEEK) },
  }))
})

describe('a partial week', () => {
  it('🔴 names each trigger it cannot plot in full by the name the list shows', async () => {
    const { WeekGridView } = await import('./WeekGridView')
    render(<WeekGridView onOpenTrigger={() => {}} />)
    const notice = await screen.findByText(/fire too often to plot in full/)
    expect(notice.textContent).toContain('this week is partial for Heartbeat tasks, Self-remediation.')
    expect(notice.textContent, 'never the internal key').not.toMatch(/schedule:/)
  })
})

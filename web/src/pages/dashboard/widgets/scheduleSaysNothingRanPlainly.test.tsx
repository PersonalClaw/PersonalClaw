import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import type { ScheduleRun } from '../../../lib/api'

// ── Recent activity says "Nothing ran recently." and nothing more ───────────────────────────────
//
// With every recent fire held (quiet hours, a condition, a rung), the card read "Nothing ran
// recently — every recent fire was held by a gate." and offered to show the held fires: a caveat
// that only confused. When nothing worth your attention ran, the card says nothing ran and nothing
// more. Held fires stay listed in Automations' history.

const HELD: ScheduleRun = {
  id: 'held-1', trigger_id: 'schedule:morning', trigger_name: 'Morning check',
  outcome: 'skipped_gate', reason: 'held: inside quiet hours', weight: 'ledger',
  started_at: '2026-11-04T06:00:00+00:00', finished_at: '2026-11-04T06:00:00+00:00',
}

vi.mock('../DashboardLive', () => ({
  useDashboardLive: () => ({
    schedule: [HELD],
    scheduleDidIds: [],
    scheduleSuppressed: 1,
    read: { tasks: true, schedule: true },
  }),
}))

const { ScheduleWidget } = await import('./ScheduleWidget')
const route = { sub: '', navigate: vi.fn(), navEpoch: 0, query: {}, setQuery: vi.fn() }

describe('Recent activity with every recent fire held', () => {
  it('says nothing ran, with no caveat', () => {
    render(<ScheduleWidget {...route} />)

    expect(screen.getByText('Nothing ran recently.', { exact: true })).toBeTruthy()
    expect(document.body.textContent).not.toMatch(/gate|held by|suppressed/i)
  })

  it('and offers nothing to show the held fires', () => {
    render(<ScheduleWidget {...route} />)

    expect(screen.queryByRole('button')).toBeNull()
    expect(screen.queryByText('Morning check')).toBeNull()
  })
})

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import type { AuditPage } from '../../lib/api'
import { AuditPanel } from './AuditPanel'

// ── A verdict word wider than its column was drawn over the next one ────────────────────────────
//
// The row's outcome cell was a fixed `w-14` (3.5rem) with nothing to stop a longer word spilling
// out of it, so every outcome past about eight characters was painted OVER the event-type pill and
// the operation beside it. Driven on a live log: `denied_revision_required` read "denied_re" with
// "uired" printed across "apps.config". The vocabulary has had such words for a while
// (`refused_budget_unverified`, `rejected_invalid_cwd`, `not_auto_approved`); the stale-write
// refusals' two outcomes made it the common case. jsdom has no layout, so what is pinned here is
// the cell's contract — the whole word, on one line, in a cell at least the old column wide that
// grows for a longer word instead of overflowing — and the drive measures the boxes.

const auditEvents = vi.fn()
vi.mock('../../lib/api', () => ({
  api: {
    auditEvents: (...a: unknown[]) => auditEvents(...a),
    auditVerify: vi.fn(),
    selRotate: vi.fn(),
  },
}))
vi.mock('../../lib/data', () => ({ invalidateKeys: vi.fn() }))
vi.mock('../../app/appSdk', () => ({ notify: vi.fn() }))

const WORD = 'denied_revision_required'
const page = (): AuditPage => ({
  events: [{ event_id: 'e1', timestamp: '2026-09-27T07:16:37Z', event_type: 'api_access', outcome: WORD, outcome_tone: 'danger', operation: 'apps.config' }],
  count: 1, next_cursor: '', scanned: 1, truncated: false,
  outcome_families: [{ key: 'denied', label: 'Denied', tone: 'danger', values: ['denied'] }],
})

describe('an outcome word wider than the column', () => {
  beforeEach(() => { vi.clearAllMocks(); auditEvents.mockResolvedValue(page()) })

  it('is shown whole, on one line, in a cell that grows for it instead of spilling over', async () => {
    render(<AuditPanel />)
    const cell = await screen.findByText(WORD)
    expect(cell.textContent).toBe(WORD)
    const classes = cell.className.split(/\s+/)
    expect(classes).not.toContain('w-14')
    expect(classes).toEqual(expect.arrayContaining(['min-w-14', 'shrink-0', 'whitespace-nowrap']))
  })
})

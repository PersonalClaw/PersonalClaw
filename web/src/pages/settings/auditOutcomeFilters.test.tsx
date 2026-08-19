import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import type { AuditPage } from '../../lib/api'
import { AuditPanel } from './AuditPanel'

// ── A filter pill that could only ever return zero ────────────────────────────────────────────────
//
// The outcome pills used to be a two-entry list in THIS file, one literal substring each — `denied`
// and `failed` — against an audit log whose writers emit **62** distinct outcome words. Measured
// across `src/personalclaw`:
//
//     denied 163 · rejected 24 · blocked 5 · refused 1     "Denied" matched 163 of 193
//     failure 23 · error 21 · failed 4                     "Failed" matched 4 of 48
//
// and confirmed on a live instance: a real `DELETE /api/terminal/sessions/…` recorded
// `outcome=error` was invisible to the Failed pill (`outcome=failed` → 0 rows, `outcome=error` → 1).
// On an audit surface that is the worst failure available — the operator reads the empty list as
// "nothing happened", and the endpoint's own comment already says exactly that about a dropped
// filter KEY. The VALUES had no such guard.
//
// The families now live in `sel.AUDIT_OUTCOME_FAMILIES` (the module that owns the log) and arrive
// with every page. `tests/test_audit_outcome_families.py` proves no family offers a term nobody
// writes and that the matcher is any-of within a field / AND across fields. This file proves the
// panel RENDERS what it is sent, invents nothing, and sends the whole family.

const auditEvents = vi.fn()
const auditVerify = vi.fn()
const selRotate = vi.fn()
vi.mock('../../lib/api', () => ({
  api: {
    auditEvents: (...a: unknown[]) => auditEvents(...a),
    auditVerify: (...a: unknown[]) => auditVerify(...a),
    selRotate: (...a: unknown[]) => selRotate(...a),
  },
}))
vi.mock('../../lib/useCachedData', () => ({ invalidateCache: vi.fn() }))
vi.mock('../../app/appSdk', () => ({ notify: vi.fn() }))

const FAMILIES: AuditPage['outcome_families'] = [
  { key: 'denied', label: 'Denied', values: ['denied', 'rejected', 'blocked', 'refused'] },
  { key: 'failed', label: 'Failed', values: ['failure', 'failed', 'error', 'not_found'] },
]

const page = (over: Partial<AuditPage> = {}): AuditPage => ({
  events: [{ event_id: 'e1', timestamp: '2026-08-19T10:00:00Z', event_type: 'tool', outcome: 'error', operation: 'DELETE /api/terminal/sessions/abc' }],
  count: 1, next_cursor: '', scanned: 1, truncated: false, outcome_families: FAMILIES, ...over,
})

describe('the outcome pills come from the log, not from this panel', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    auditEvents.mockResolvedValue(page())
  })

  it('renders one pill per family the server sent, plus All', async () => {
    render(<AuditPanel />)
    const group = await screen.findByRole('group', { name: 'Filter by outcome' })
    await waitFor(() => expect(group.querySelectorAll('button').length).toBe(3))
    expect([...group.querySelectorAll('button')].map((b) => b.textContent)).toEqual(['All', 'Denied', 'Failed'])
  })

  it('a family the server adds appears without touching this file', async () => {
    auditEvents.mockResolvedValue(page({
      outcome_families: [...FAMILIES, { key: 'warned', label: 'Warned', values: ['needs_confirm'] }],
    }))
    render(<AuditPanel />)
    const group = await screen.findByRole('group', { name: 'Filter by outcome' })
    await waitFor(() => expect(group.querySelectorAll('button').length).toBe(4))
    expect(screen.getByRole('button', { name: 'Warned' })).toBeTruthy()
  })

  it('clicking a pill queries the WHOLE family, server-side', async () => {
    render(<AuditPanel />)
    fireEvent.click(await screen.findByRole('button', { name: 'Failed' }))
    // Debounced refetch; the filter that goes over the wire is the comma-joined family, which the
    // server matches any-of. Sending only the key is the original defect.
    await waitFor(() => {
      const last = auditEvents.mock.calls.at(-1)?.[0] as { filters: { outcome?: string } }
      expect(last.filters.outcome).toBe('failure,failed,error,not_found')
    }, { timeout: 2000 })
  })

  it('the pressed pill is the one whose family is applied', async () => {
    render(<AuditPanel />)
    const failed = await screen.findByRole('button', { name: 'Failed' })
    expect(failed.getAttribute('aria-pressed')).toBe('false')
    fireEvent.click(failed)
    await waitFor(() => expect(failed.getAttribute('aria-pressed')).toBe('true'))
    // Exactly one pill pressed — a group where two read as selected describes no state at all.
    const group = screen.getByRole('group', { name: 'Filter by outcome' })
    expect([...group.querySelectorAll('button')].filter((b) => b.getAttribute('aria-pressed') === 'true').length).toBe(1)
  })

  it('holds no outcome vocabulary of its own', async () => {
    // The structural half: a local list is what made the pills incomplete, so its absence is the
    // thing to pin. `OUTCOME_TONE` stays — it colours a rendered value and is not a filter.
    const { readFileSync } = await import('node:fs')
    const { join } = await import('node:path')
    const src = readFileSync(join(process.cwd(), 'src/pages/settings/AuditPanel.tsx'), 'utf8')
      .replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
    expect(src, 'no local preset list').not.toMatch(/OUTCOME_PRESETS/)
    expect(src, 'the pills are the served families').toMatch(/const presets = \[ALL_PRESET, \.\.\.families\]/)
    expect(src, 'and the click sends the joined family').toMatch(/f\.values\.join\(','\)/)
  })
})

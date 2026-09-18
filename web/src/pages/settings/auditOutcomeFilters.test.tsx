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
vi.mock('../../lib/data', () => ({ invalidateKeys: vi.fn() }))
vi.mock('../../app/appSdk', () => ({ notify: vi.fn() }))

const FAMILIES: AuditPage['outcome_families'] = [
  { key: 'denied', label: 'Denied', tone: 'danger', values: ['denied', 'rejected', 'blocked', 'refused'] },
  { key: 'failed', label: 'Failed', tone: 'danger', values: ['failure', 'failed', 'error', 'not_found'] },
]

const page = (over: Partial<AuditPage> = {}): AuditPage => ({
  events: [{ event_id: 'e1', timestamp: '2026-08-19T10:00:00Z', event_type: 'tool', outcome: 'error', outcome_tone: 'danger', operation: 'DELETE /api/terminal/sessions/abc' }],
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

  it('holds no outcome vocabulary of its own — not for filtering, and not for colour', async () => {
    // The structural half: a local list is what made the pills incomplete, so its absence is the
    // thing to pin. `OUTCOME_TONE` used to be exempted here on the grounds that it "colours a
    // rendered value and is not a filter" — and that exemption was the remaining half of the bug.
    // It was a fourteen-entry hand-maintained map over a 66-word vocabulary, and it had drifted:
    // `not_found` is a member of the `failed` family and had no entry, so a record the Failed pill
    // calls a failure rendered in neutral grey. The server stamps every row's tone now.
    const { readFileSync } = await import('node:fs')
    const { join } = await import('node:path')
    const src = readFileSync(join(process.cwd(), 'src/pages/settings/AuditPanel.tsx'), 'utf8')
      .replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
    expect(src, 'no local preset list').not.toMatch(/OUTCOME_PRESETS/)
    expect(src, 'no local outcome to colour map').not.toMatch(/OUTCOME_TONE/)
    expect(src, 'the pills are the served families').toMatch(/const presets = \[ALL_PRESET, \.\.\.families\]/)
    expect(src, 'and the click sends the joined family').toMatch(/f\.values\.join\(','\)/)
    // Named outcome words are the tell. Every one of these appeared in the old local map; the
    // panel must now know only the four TONE names, which are design tokens, not vocabulary.
    for (const word of ['not_found', 'needs_confirm', 'auto_approved', 'not_triggered', 'refused', 'rejected']) {
      expect(src, `${word} is vocabulary — it belongs to sel.py`).not.toMatch(new RegExp(word))
    }
  })

  it('colours a row from the tone the server sent, not from the outcome word', async () => {
    // Two rows the old local map got wrong: `not_found` was a Failed-family member with no entry
    // (neutral grey), and `disabled` was a success with no entry (also grey).
    auditEvents.mockResolvedValue(page({
      events: [
        { event_id: 'a', timestamp: '2026-08-19T10:00:00Z', event_type: 'tool', outcome: 'not_found', outcome_tone: 'danger' },
        { event_id: 'b', timestamp: '2026-08-19T10:00:01Z', event_type: 'tool', outcome: 'disabled', outcome_tone: 'success' },
        { event_id: 'c', timestamp: '2026-08-19T10:00:02Z', event_type: 'tool', outcome: 'halted_on_budget', outcome_tone: 'neutral' },
        // No tone at all must degrade to neutral, never to a guess from the word.
        { event_id: 'd', timestamp: '2026-08-19T10:00:03Z', event_type: 'tool', outcome: 'denied' },
      ],
      count: 4,
    }))
    render(<AuditPanel />)
    const styleOf = async (word: string) =>
      (await screen.findByText(word)).getAttribute('style') ?? ''
    expect(await styleOf('not_found')).toContain('--color-danger')
    expect(await styleOf('disabled')).toContain('--color-success')
    expect(await styleOf('halted_on_budget')).toContain('--color-on-surface-low')
    expect(await styleOf('denied')).toContain('--color-on-surface-low')
  })

  it('a budget-stopped page keeps looking instead of reporting an empty log', async () => {
    // The reachability half (#593) as the operator meets it: the server bounds ONE request's scan,
    // so a sparse filter deep in a long log returns zero rows plus an anchor. A click that renders
    // nothing looks like a broken button, so the panel chases the empty pages itself.
    auditEvents
      .mockResolvedValueOnce(page({ events: [], count: 0, truncated: true, next_cursor: '900.n1' }))
      .mockResolvedValueOnce(page({ events: [], count: 0, truncated: true, next_cursor: '450.n2' }))
      .mockResolvedValueOnce(page({
        events: [{ event_id: 'found', timestamp: '2026-08-19T09:00:00Z', event_type: 'tool', outcome: 'denied', outcome_tone: 'danger' }],
        count: 1, truncated: false, next_cursor: '',
      }))
    render(<AuditPanel />)
    expect(await screen.findByText('denied')).toBeTruthy()
    expect(auditEvents.mock.calls.length).toBe(3)
    // ...and it followed the anchors it was given rather than restarting from the newest record,
    // which would re-serve the whole trail as if it were a fresh page.
    expect((auditEvents.mock.calls[1]?.[0] as { cursor?: string }).cursor).toBe('900.n1')
    expect((auditEvents.mock.calls[2]?.[0] as { cursor?: string }).cursor).toBe('450.n2')
  })

  it('tells "nothing matched anywhere" apart from "nothing matched yet"', async () => {
    // Same empty list, two different facts. Presenting the second as the first is the failure that
    // matters on an audit surface: the operator concludes nothing happened.
    auditEvents.mockResolvedValue(page({ events: [], count: 0, truncated: false, next_cursor: '' }))
    const whole = render(<AuditPanel />)
    expect(await screen.findByText('No matching events.')).toBeTruthy()
    whole.unmount()

    vi.clearAllMocks()
    // Budget-stopped on every follow-up, so the panel gives up with an anchor still in hand.
    auditEvents.mockResolvedValue(page({ events: [], count: 0, truncated: true, next_cursor: '10.z' }))
    render(<AuditPanel />)
    expect(await screen.findByText(/No matches yet in the events scanned so far/)).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Load older events' })).toBeTruthy()
  })
})

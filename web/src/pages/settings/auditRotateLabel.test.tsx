import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import type { AuditPage } from '../../lib/api'
import { AuditPanel } from './AuditPanel'

// ── The "Rotate" control promised something it never did (issue 534) ───────────────────────────────
//
// The confirm dialog said "Rotate the audit-log signing key?" / "Past entries stay verifiable under
// the old key." — but `SecurityEventLog.rotate()` only ARCHIVES the log to a timestamped `.bak.jsonl`
// and starts a fresh HMAC chain; the signing key is create-only and is never rewritten. And the
// archived entries leave the dashboard verify/browse surface entirely, so "stay verifiable" was false
// on two counts. This pins the corrected, honest copy AND the previously-dropped success payload:
// `selRotate` was typed `{ ok?: boolean }`, discarding the real `{ rotated, entries_before,
// entries_after, archive_path }` — so the user was never told where their log went.

const auditEvents = vi.fn()
const auditVerify = vi.fn()
const selRotate = vi.fn()
const confirm = vi.fn()
const notify = vi.fn()
vi.mock('../../lib/api', () => ({
  api: {
    auditEvents: (...a: unknown[]) => auditEvents(...a),
    auditVerify: (...a: unknown[]) => auditVerify(...a),
    selRotate: (...a: unknown[]) => selRotate(...a),
  },
}))
vi.mock('../../lib/data', () => ({ invalidateKeys: vi.fn() }))
vi.mock('../../app/appSdk', () => ({ notify: (...a: unknown[]) => notify(...a) }))
vi.mock('../../ui/dialog', () => ({ confirm: (...a: unknown[]) => confirm(...a) }))

const page = () => ({ events: [], next_cursor: null, outcome_families: [], total: 0 } as unknown as AuditPage)

const PANEL = join(process.cwd(), 'src/pages/settings/AuditPanel.tsx')
const strip = (p: string) => readFileSync(p, 'utf8').replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

describe('the rotate control makes no promise about a signing key', () => {
  it('the confirm copy names an archive, never a key promise — vacuity floor: the block must be locatable', () => {
    // Locate the confirm({...}) dialog that guards the rotate action. If it cannot be found (a refactor
    // moved or reshaped it), this FAILS rather than passing vacuously — the copy assertions below only
    // mean something when they run against a real dialog block.
    const block = strip(PANEL).match(/confirm\(\{[\s\S]*?\}\)/)
    expect(block, 'the rotate confirm dialog block must be locatable').not.toBeNull()
    const copy = block![0]

    // It states the action it actually performs.
    expect(copy, 'the copy names the real action: archive').toMatch(/archive/i)

    // The removed false promises: entries do NOT "stay verifiable under the old key", and nothing
    // "rotates" a key here.
    expect(copy, 'no verifiable-under-a-key promise').not.toMatch(/verifiable/i)
    expect(copy, 'no claim that a key is rotated').not.toMatch(/rotate/i)

    // The ONLY place "key"/"signing" may appear is the honest disclaimer that the key is untouched —
    // never as a promise about what the key does for old entries.
    const clauses = copy.match(/[^.?!]*\b(?:key|signing)\b[^.?!]*/gi) ?? []
    for (const c of clauses)
      expect(c.trim(), `key/signing mentioned only as unchanged: "${c.trim()}"`).toMatch(
        /unchang|not\s+(?:changed|rotated|touched)/i,
      )
  })
})

describe('a successful rotate tells the user where the log was archived', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    auditEvents.mockResolvedValue(page())
    confirm.mockResolvedValue(true)
  })

  it('surfaces the archive file basename (not the full path) in a success notification', async () => {
    selRotate.mockResolvedValue({
      rotated: true,
      entries_before: 3,
      entries_after: 0,
      archive_path: '/home/u/.personalclaw/security_events.20260902T010203Z.bak.jsonl',
    })
    render(<AuditPanel />)
    fireEvent.click(await screen.findByRole('button', { name: /^Rotate$/ }))
    await waitFor(() => expect(selRotate).toHaveBeenCalled())
    await waitFor(() => expect(notify).toHaveBeenCalled())
    const [message, level] = notify.mock.calls.at(-1) as [string, string]
    expect(message).toContain('security_events.20260902T010203Z.bak.jsonl')
    expect(message, 'basename only — never the containing directory').not.toContain('/home/u/')
    expect(level).toBe('success')
  })

  it('still confirms a reset when the payload carries no archive path', async () => {
    selRotate.mockResolvedValue({ rotated: true, entries_before: 0, entries_after: 0, archive_path: '' })
    render(<AuditPanel />)
    fireEvent.click(await screen.findByRole('button', { name: /^Rotate$/ }))
    await waitFor(() => expect(notify).toHaveBeenCalled())
    const [message, level] = notify.mock.calls.at(-1) as [string, string]
    expect(level).toBe('success')
    expect(message).toMatch(/reset|fresh chain/i)
  })
})

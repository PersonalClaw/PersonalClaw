import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import type { AuditPage } from '../../lib/api'
import { AuditPanel, verifiedScope, capped } from './AuditPanel'

// ── A tamper-evidence verdict that overstated what it had checked ──────────────────────────────────
//
// `sel.verify_integrity` defaults to a 5000-entry cap — the live tamper-detection window, added
// because a full walk "had reached >1M entries, taking 20s+ and hanging the audit UI" (its own
// docstring) — and `GET /api/security/audit/verify` reported that honestly as `windowed: true`.
// **Nothing in the SPA read the flag**: `git grep -n windowed web/src` returned only the unrelated
// `WindowedList` primitive. So both consumers rendered the count alone:
//
//   AuditPanel      "Chain intact — 5000 events verified."
//   settings bento  "5000 events verified"
//
// On a tamper-evidence surface that reads as *the chain is intact*, full stop. Measured live:
// `{"checked": 5000, "ok": true, "valid": 5000, "tampered": 0, "windowed": true}`.
//
// 🔑 AND THE FIX HAD TO BE HONEST IN BOTH DIRECTIONS. On a fresh instance the same endpoint returned
// `{"checked": 43, ..., "windowed": true}` — the cap was SET but never bit, so "the last 43 events"
// would understate a complete answer exactly as badly as the original overstated a partial one.
// `windowed` cannot tell those apart, so the handler now also sends `window` (the cap it applied) and
// `capped()` compares. A total would be exact but costs the O(n) walk the window exists to avoid.
// The boundary case (log length == window) resolves as "capped": the safe direction to be wrong in.
//
// 🔑 WHAT THIS DELIBERATELY DOES NOT DO: offer a "verify everything" button. `personalclaw security
// verify` already runs the exhaustive check — its own comment calls it "an explicit offline audit" —
// and a button here would re-create the hang the window was added to fix. So the panel NAMES the
// command, which is the same choice `DurabilityPanel` makes for `personalclaw restore --replace`.

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

const page = () => ({ events: [], next_cursor: null, outcome_families: [], total: 0 } as unknown as AuditPage)

const PANEL = join(process.cwd(), 'src/pages/settings/AuditPanel.tsx')
const WIDGETS = join(process.cwd(), 'src/pages/settings/settingsWidgets.tsx')
const HANDLER = join(process.cwd(), '..', 'src/personalclaw/dashboard/handlers/security_audit.py')
const strip = (p: string) => readFileSync(p, 'utf8').replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

describe('the verdict names the scope it actually covered', () => {
  it('a cap that BIT is reported as a window', () => {
    expect(verifiedScope({ checked: 5000, windowed: true, window: 5000 })).toBe('the last 5,000 events')
    expect(capped({ checked: 5000, windowed: true, window: 5000 })).toBe(true)
  })

  it('a cap that never bit is reported as complete — the understatement is a defect too', () => {
    // The live fresh-instance shape: a window was set, 43 events existed, all 43 were checked.
    expect(verifiedScope({ checked: 43, windowed: true, window: 5000 })).toBe('all 43 events')
    expect(capped({ checked: 43, windowed: true, window: 5000 })).toBe(false)
  })

  it('an exhaustive check says all, and the count is grouped', () => {
    expect(verifiedScope({ checked: 1234567, windowed: false, window: null })).toBe('all 1,234,567 events')
    expect(capped({ checked: 1234567, windowed: false, window: null })).toBe(false)
  })

  it('a server that sends no window cannot be made to claim one', () => {
    // Forward/backward compatibility: `windowed` without `window` is not enough to assert truncation,
    // and guessing would put an unprovable claim on a security surface.
    expect(capped({ checked: 5000, windowed: true })).toBe(false)
    expect(verifiedScope({ checked: 5000, windowed: true })).toBe('all 5,000 events')
  })

  it('one phrase, two surfaces — the bento tile does not re-word it', () => {
    const w = strip(WIDGETS)
    expect(w).toMatch(/\{verifiedScope\(v\)\} verified/)
    expect(w, 'the bare count must not come back').not.toMatch(/\{v\.checked\} events verified/)
    expect(w).toMatch(/import \{ verifiedScope \} from '\.\/AuditPanel'/)
  })

  it('the server sends the cap it applied, not just that one existed', () => {
    const py = readFileSync(HANDLER, 'utf8')
    expect(py, 'the window size is what makes the distinction possible')
      .toMatch(/"window": None if full else _VERIFY_WINDOW,/)
    expect(py, 'and the older flag stays — it is what says a cap was set at all')
      .toMatch(/"windowed": not full,/)
  })
})

describe('the panel says what was left out, and where the whole check lives', () => {
  beforeEach(() => { vi.clearAllMocks(); auditEvents.mockResolvedValue(page()) })

  it('a capped pass names the window AND the offline command', async () => {
    auditVerify.mockResolvedValue({ ok: true, checked: 5000, valid: 5000, tampered: 0, windowed: true, window: 5000 })
    render(<AuditPanel />)
    fireEvent.click(await screen.findByRole('button', { name: /^Verify$/ }))
    await waitFor(() => expect(screen.getByText(/the last 5,000 events verified/)).toBeTruthy())
    expect(screen.getByText(/Older entries were not checked/)).toBeTruthy()
    expect(screen.getByText('personalclaw security verify')).toBeTruthy()
    // And NOT a button that would walk the whole log in the browser.
    expect(screen.queryByRole('button', { name: /whole log|verify everything|full/i })).toBeNull()
  })

  it('an uncapped pass says nothing about windows — there is nothing to escalate', async () => {
    auditVerify.mockResolvedValue({ ok: true, checked: 43, valid: 43, tampered: 0, windowed: true, window: 5000 })
    render(<AuditPanel />)
    fireEvent.click(await screen.findByRole('button', { name: /^Verify$/ }))
    await waitFor(() => expect(screen.getByText(/all 43 events verified/)).toBeTruthy())
    expect(screen.queryByText(/Older entries were not checked/)).toBeNull()
    expect(screen.queryByText('personalclaw security verify')).toBeNull()
  })

  it('a BROKEN chain states its scope too, and still points at the full check', async () => {
    auditVerify.mockResolvedValue({ ok: false, checked: 5000, valid: 4998, tampered: 2, windowed: true, window: 5000 })
    render(<AuditPanel />)
    fireEvent.click(await screen.findByRole('button', { name: /^Verify$/ }))
    await waitFor(() => expect(screen.getByText(/2 of the last 5,000 events altered/)).toBeTruthy())
    // A break is exactly when the rest of the log matters most.
    expect(screen.getByText('personalclaw security verify')).toBeTruthy()
  })

  it('the verify request stays the windowed one — the browser never walks the log', () => {
    const src = strip(PANEL)
    expect(src, 'no call site may pass full=true from the UI').not.toMatch(/auditVerify\(true\)/)
    expect(src, 'and the CLI is named where a button would otherwise go')
      .toMatch(/personalclaw security verify/)
  })
})

// ── A check that never ran is not a verdict ────────────────────────────────────────────────────
//
// The same defect as the windowed "Chain intact", pointing the other way. `runVerify` used to
// answer a rejected fetch by BUILDING a result — `{ ok: false, checked: 0, error: 'verify failed' }`
// — and feeding it to the verdict renderer. Measured against the shapes at the top of this file:
//
//   network failure  →  "Chain broken — ? of all 0 events altered (verify failed)."
//
// in the same danger red that two genuinely altered records get, from zero entries examined. #536
// named this ("a network failure and real tampering paint the identical red 'Chain broken' state")
// and it outlived the scope fix, because `SelVerify.error` gave the fabrication somewhere to live.
// A verdict may be as narrow as its evidence but never wider, and "nothing was read" is as narrow
// as it gets — so it is a third state with a third variable, and the type can no longer hold it.
describe('a failed check reports that it did not run, not that the chain is bad', () => {
  beforeEach(() => { vi.clearAllMocks(); auditEvents.mockResolvedValue(page()) })

  it('a rejected verify says nothing about the chain, and carries the real reason', async () => {
    auditVerify.mockRejectedValue(new Error('gateway unreachable'))
    render(<AuditPanel />)
    fireEvent.click(await screen.findByRole('button', { name: /^Verify$/ }))
    await waitFor(() => expect(screen.getByText(/Couldn't check the chain/)).toBeTruthy())

    expect(screen.getByText(/gateway unreachable/), 'a 403 and an offline gateway are different problems').toBeTruthy()
    expect(screen.getByText(/No entries were examined/)).toBeTruthy()
    // THE ASSERTION. Neither verdict word may appear anywhere on the panel.
    expect(screen.queryByText(/Chain broken/), 'a transport failure is not a tamper finding').toBeNull()
    expect(screen.queryByText(/Chain intact/), 'and it is not a clean bill of health either').toBeNull()
    // Not the windowed-pass disclosure either: there is no window to disclose when nothing was read.
    expect(screen.queryByText(/Older entries were not checked/)).toBeNull()
  })

  it('a real verdict still renders — the failure branch is not the only branch', async () => {
    // Vacuity floor for the pair above: a panel wired to always say "couldn't check" passes
    // every assertion in the previous test.
    auditVerify.mockResolvedValue({ ok: true, checked: 5000, valid: 5000, tampered: 0, windowed: true, window: 5000 })
    render(<AuditPanel />)
    fireEvent.click(await screen.findByRole('button', { name: /^Verify$/ }))
    await waitFor(() => expect(screen.getByText(/the last 5,000 events verified/)).toBeTruthy())
    expect(screen.queryByText(/Couldn't check the chain/)).toBeNull()
  })

  it('the panel never manufactures a result object', () => {
    const src = strip(PANEL)
    expect(src, 'no synthetic verdict in the catch').not.toMatch(/setVerify\(\{/)
    expect(src, 'and the fabricated string is gone').not.toMatch(/verify failed/)
  })
})

// ── The verdict type may not declare a field the handler cannot send ───────────────────────────
//
// `SelVerify` used to carry `broken_at` and `error`; the handler has never emitted either. #536
// caught `broken_at` ("the failure message promises a location it can never have") and it was
// removed, but `error` — the same shape — survived and became the hole the fabricated verdict
// above fit through. Comparing the two files closes the class rather than the instance: a future
// ghost field cannot be added to the type without either being emitted or failing here.
describe('the payload type and the handler agree on the exact key set', () => {
  const typeKeys = () => {
    const body = readFileSync(join(process.cwd(), 'src/lib/api.ts'), 'utf8')
      .match(/export interface SelVerify \{([\s\S]*?)\n\}/)![1]
      .replace(/\/\*[\s\S]*?\*\//g, '')
    return new Set([...body.matchAll(/(\w+)\??:/g)].map((m) => m[1]))
  }
  const handlerKeys = () => {
    const fn = readFileSync(HANDLER, 'utf8').split('async def api_security_audit_verify')[1]
    return new Set([...fn.matchAll(/^\s{12}"(\w+)":/gm)].map((m) => m[1]))
  }

  it('every declared field is one the handler emits, and vice versa', () => {
    const declared = typeKeys()
    const emitted = handlerKeys()
    expect(emitted.size, 'the regex found the response body').toBe(6)
    expect([...declared].sort()).toEqual([...emitted].sort())
    expect(declared.has('error'), 'the client-fabricated field is gone').toBe(false)
    expect(declared.has('broken_at'), 'and the one #536 named stays gone').toBe(false)
  })

  it('the bento tile reads only emitted fields, and binds the REACHABLE did-not-run state', () => {
    const w = strip(WIDGETS)
    expect(w, 'reading v.error was reading a ghost — the field is client-fabricated')
      .not.toMatch(/v\.error/)
    // This assertion used to require `v === null`, and that was requiring DEAD CODE. Measured in
    // `lib/api.ts`: `j()` throws `apiError(r)` on any non-2xx and `fetch` rejects on a dead
    // connection, so `api.auditVerify()` REJECTS rather than resolving to `null`; `useQuery` stores
    // nothing for a rejection, so `data` is `undefined` and `status === 'error'`. Reaching
    // `v === null` needed the handler to emit literal JSON `null`, which its 6-field body (asserted
    // above) cannot. The did-not-run state a user can actually hit is the failure band, so the rail
    // now requires the tile to BIND it — an unbound tile is the empty-card-under-a-security-title
    // regression this test exists to stop.
    // `[^>]*` cannot be used to stay inside the tag: the tag holds `onClick={() => go('audit')}`,
    // whose arrow is a `>`, so a negated-`>` class stops dead there. Bounded `[\s\S]{0,400}?` is
    // shorter than the distance to the next tile and so cannot borrow a sibling's binding.
    expect(w, 'a failed chain check must paint the failure band, not an empty tile body')
      .toMatch(/title="Audit log"[\s\S]{0,400}?failed=\{[^}]*=== 'error'\}/)
  })
})

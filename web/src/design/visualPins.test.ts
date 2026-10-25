import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { FIXED_CLOCK } from '../../e2e/fixedClock'
import { AUDIT_VERIFIED, LEARNING_WEEK, SYSTEM_READING, weekEndingOn } from '../../e2e/stubbedReads'
import { verifiedScope } from '../pages/settings/AuditPanel'

// ── The visual spec's pins, without a browser (#3943) ────────────────────────────────────────────
//
// `e2e/visual.spec.ts` pins what a shot draws that the run does not decide: the day, the run's own
// counts and readings, a probe's timing and a caret (the Inbox refresh, run 40150040941, found
// four). The spec's own "each pin holds" tests plant each in the browser; these hold the fixed
// values themselves.

const week = (last: string, extra: Record<string, unknown> = {}) => {
  const days = [6, 5, 4, 3, 2, 1, 0].map((n) => new Date(Date.parse(`${last}T00:00:00Z`) - n * 86_400_000).toISOString().slice(0, 10))
  return { days: 7, buckets: days.map((day) => ({ day, passes: 0, produced: 0, errors: 0 })), silent_days: [], error_days: [], produced_total: 0, cost_usd: 0, first_pass_day: '', ...extra }
}

describe("Learning's week", () => {
  it('is the same week whichever day the gateway answered on', () => {
    const saturday = weekEndingOn(week('2026-10-24'), '2026-11-02')
    const sunday = weekEndingOn(week('2026-10-25'), '2026-11-02')
    expect(saturday).toEqual(sunday)
    expect(saturday.buckets.map((b) => b.day)).toEqual(week('2026-11-02').buckets.map((b) => b.day))
  })

  it('moves every day it names by the same count, and keeps every count', () => {
    const live = week('2026-10-24', { silent_days: ['2026-10-22'], error_days: ['2026-10-23'], first_pass_day: '2026-10-20' })
    live.buckets[6] = { ...live.buckets[6], passes: 3, produced: 1 }
    const moved = weekEndingOn(live, '2026-11-02')
    expect(moved.silent_days).toEqual(['2026-10-31'])
    expect(moved.error_days).toEqual(['2026-11-01'])
    expect(moved.first_pass_day).toBe('2026-10-29')
    expect(moved.buckets[6]).toEqual({ day: '2026-11-02', passes: 3, produced: 1, errors: 0 })
  })

  it('leaves a week it cannot read as it is', () => {
    const odd = { buckets: [{ day: 'not a day' }] }
    expect(weekEndingOn(odd, '2026-11-02')).toBe(odd)
    const empty = { buckets: [] }
    expect(weekEndingOn(empty, '2026-11-02')).toBe(empty)
  })

  it("ends on the day Learning's fixed clock reads, the UI matrix's table", () => {
    expect(FIXED_CLOCK.learning).toBeDefined()
    if (!('patch' in LEARNING_WEEK)) throw new Error('LEARNING_WEEK is a patch of the live read')
    const answered = LEARNING_WEEK.patch(week('2026-10-24')) as ReturnType<typeof week>
    expect(answered.buckets.at(-1)?.day).toBe(FIXED_CLOCK.learning.slice(0, 10))
    expect(LEARNING_WEEK.url.test('/api/learning/staging/week?days=7')).toBe(true)
  })
})

describe("Settings' tiles", () => {
  it('read a fixed audit count, in the six keys the handler emits', () => {
    if (!('json' in AUDIT_VERIFIED)) throw new Error('AUDIT_VERIFIED is a fixed answer')
    const answer = AUDIT_VERIFIED.json as { checked: number; windowed: boolean; window: number }
    // `SelVerify` (`lib/api.ts`): exactly the six keys `api_security_audit_verify` emits.
    expect(Object.keys(answer).sort()).toEqual(['checked', 'ok', 'tampered', 'valid', 'window', 'windowed'])
    expect(verifiedScope(answer)).toBe('all 150 events')
    expect(AUDIT_VERIFIED.url.test('/api/security/audit/verify')).toBe(true)
  })

  it("pin the System tile's cpu and memory and keep the rest of the live reading", () => {
    if (!('patch' in SYSTEM_READING)) throw new Error('SYSTEM_READING is a patch of the live read')
    const live = { hostname: 'runner', version: '0.2.7', cpu_pct: 100, mem_used_gb: 3.5, mem_total_gb: 15.6, net_rx_kbs: 9 }
    expect(SYSTEM_READING.patch(live)).toEqual({ ...live, cpu_pct: 12, mem_used_gb: 3.2, mem_total_gb: 15.6, mem_free_gb: 12.4 })
    expect(SYSTEM_READING.url.test('/api/system')).toBe(true)
    expect(SYSTEM_READING.url.test('/api/system/models')).toBe(false)
  })
})

describe('the clock table', () => {
  it('is the one table both specs read', () => {
    const e2e = join(resolve(__dirname, '../..'), 'e2e')
    for (const spec of ['surfaces.spec.ts', 'visual.spec.ts']) {
      const text = readFileSync(join(e2e, spec), 'utf8')
      expect(text, spec).toContain("import { FIXED_CLOCK } from './fixedClock'")
      expect(text, spec).not.toMatch(/const FIXED_CLOCK\b/)
    }
  })
})

import { describe, it, expect, vi, afterEach } from 'vitest'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { relTime } from './knowledgeMeta'

// ── "412d ago" is not a date ─────────────────────────────────────────────────────────────────────
//
// A knowledge library is long-lived BY DEFINITION, and `relTime` renders on four of its surfaces
// (`LibraryHome` twice — created_at and updated_at — plus `KnowledgeListPage` rows and the
// `KnowledgeDetail` header). Its day branch was unbounded, so an item from last year read
// "412d ago". It also had no future guard, so a stamp ahead of now made the elapsed seconds
// negative, fell through the `< 60` branch, and rendered a confident "just now".
//
// 🔑 NEITHER IS A NEW CONVENTION. Censused across the ELEVEN time formatters under `web/src`,
// `taskMeta.relTime` already had both guards; this was the only one with neither. So the fix is to
// match the sibling that got it right, not to invent a rule.
//
// 🪤 AND THE CENSUS IS WHY THIS PR IS ONE FUNCTION, NOT NINE. A grep for `relTime` finds four
// copies and reads like duplication to consolidate — a trap `lib/epoch.test.ts` documents in its own
// words ("a grep for one name found four and this census found the rest"), because the family spans
// three naming conventions (`relPast`/`relFuture`, `relTime`, `relativeTime`). That rail also states
// its bar deliberately: "the bar is the DEFECT SHAPE, not adoption of one module" — unvalidated
// arithmetic, which all of them already pass. Consolidating them is NOT an outstanding task, and
// several genuinely SHOULD lack a date fallback: `relFuture`/`relPast` on schedule, triggers and
// inbox describe an imminent or recent run, where "in 4m" is the right register and a date is worse.

const FIXED_NOW = Date.parse('2026-09-07T12:00:00Z')
const ago = (secs: number) => new Date(FIXED_NOW - secs * 1000).toISOString()

afterEach(() => { vi.useRealTimers() })
const at = (now: number) => { vi.useFakeTimers(); vi.setSystemTime(now) }

describe('an aged library item reads as a date, not a day count', () => {
  it('shows the DATE past a week, where it used to count days forever', () => {
    at(FIXED_NOW)
    const yearOld = ago(412 * 86400)
    const out = relTime(yearOld)
    expect(out, 'a year-old item still counting days').not.toMatch(/\d+d ago/)
    // Whatever the reader's locale renders, it must carry the year — that is the information a
    // day count was hiding.
    expect(out).toBe(new Date(Date.parse(yearOld)).toLocaleDateString())
    expect(out).not.toBe('')
  })

  it('keeps day counts INSIDE the week — the tiers below the ceiling are unchanged', () => {
    at(FIXED_NOW)
    expect(relTime(ago(30))).toBe('just now')
    expect(relTime(ago(5 * 60))).toBe('5m ago')
    expect(relTime(ago(3 * 3600))).toBe('3h ago')
    expect(relTime(ago(2 * 86400))).toBe('2d ago')
    expect(relTime(ago(6 * 86400))).toBe('6d ago')
  })

  it('🔑 a FUTURE stamp is not "just now"', () => {
    // The confident-wrong-answer half. Negative elapsed seconds fell through the `< 60` branch.
    at(FIXED_NOW)
    const ahead = new Date(FIXED_NOW + 3 * 86400 * 1000).toISOString()
    expect(relTime(ahead)).not.toBe('just now')
    expect(relTime(ahead)).toBe(new Date(Date.parse(ahead)).toLocaleDateString())
  })

  it('still renders nothing for an absent or unreadable stamp', () => {
    at(FIXED_NOW)
    // The empty string is live on this app's wire, not hypothetical — `epochSeconds` exists because
    // `/api/chat/sessions` sends stamp fields as `''`.
    for (const bad of [undefined, '', 'not a date']) expect(relTime(bad)).toBe('')
  })
})

describe('the fix matches the sibling it was measured against', () => {
  const SRC = join(process.cwd(), 'src')
  const read = (...p: string[]) => readFileSync(join(SRC, ...p), 'utf8')

  it('routes parsing through the canonical parser rather than a fourth Date.parse', () => {
    const code = read('pages', 'knowledge', 'knowledgeMeta.ts')
      .replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
    const body = code.match(/export function relTime[\s\S]*?\n\}/)?.[1 - 1] ?? ''
    expect(body, 'found the relTime body').not.toBe('')
    expect(body).toMatch(/epochSeconds\(/)
    expect(body, 'a local Date.parse is the duplication this removes').not.toMatch(/Date\.parse/)
  })

  it('VACUITY: the formatter family is still bigger than one name', () => {
    // Guards the reasoning in the header, not the fix: if this ever drops toward one, the "do not
    // consolidate" argument above needs revisiting rather than being inherited on trust.
    const walk = (d: string): string[] => readdirSync(d).flatMap((n) => {
      const p = join(d, n)
      if (statSync(p).isDirectory()) return walk(p)
      return /\.tsx?$/.test(n) && !/\.(test|doc)\.tsx?$/.test(n) ? [p] : []
    })
    const found = walk(SRC).flatMap((f) => [
      ...readFileSync(f, 'utf8').matchAll(/function (relPast|relFuture|relTime|relTimeShort|relativeTime|absTime)\s*\(/g),
    ].map((m) => m[1]))
    expect(found.length, 'time formatters across web/src').toBeGreaterThanOrEqual(9)
    // Three naming conventions is the reason a single-name grep undercounts.
    expect(new Set(found).size).toBeGreaterThanOrEqual(3)
  })
})

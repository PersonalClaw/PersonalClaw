import { describe, it, expect, vi, afterEach } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { epochSeconds } from './epoch'
import { relPast, relFuture, absTime } from '../pages/schedule/scheduleMeta'

// ── "in NaNd", six times, on the first screen of the app ─────────────────────────────────
//
// Measured on `#/dashboard` (1440×1600, live gateway): the Schedule Timeline rendered
//
//     Schedule · failed · in NaNd     ×6
//
// and a tree-wide sweep of user-visible text across 14 routes found those six and nothing
// else. Root cause, from the wire rather than from reading: `/api/triggers/history` returns
//
//     "started_at": "2026-08-12T08:00:00.006315+00:00"
//
// while `lib/api.ts` declared `started_at?: number` and every relative-time formatter did
// arithmetic on it directly. `Date.now()/1000 - "2026-…"` is `NaN`, so every `if (s < …)`
// comparison was false, execution fell out of the last branch, and the unit printed with NaN
// in front of it. The `past` test (`secs <= now`) is also false for a string, which is why it
// said "in" — a FUTURE time — for runs that had already happened.
//
// 🔑 THE CLASS OF DEFECT: a formatter with no honest failure value. Four near-identical copies
// of these thresholds existed (`scheduleMeta`, `triggerMeta`, `TriggersListPage`,
// `ScheduleWidget`), each typed `number | null`, each silently producing `NaN` for input it
// could not read. Parsing now happens in ONE place with `undefined` as its failure value, and
// the widget composes the shared pair instead of owning a fourth copy of the thresholds.
//
// 🪤 WHY THE TYPE DID NOT CATCH IT. `started_at?: number` was a declaration, not a check —
// nothing validates a fetch against it. The type was simply wrong about the endpoint, and
// TypeScript faithfully type-checked the wrong thing. Only reading the payload found it.

describe('epochSeconds', () => {
  it('passes a number through as seconds', () => {
    expect(epochSeconds(1786521600)).toBe(1786521600)
  })

  it('parses the ISO-8601 the history endpoint actually sends, microseconds and all', () => {
    // Six fractional digits — V8 truncates rather than rejecting, verified in node.
    expect(epochSeconds('2026-08-12T08:00:00.006315+00:00')).toBeCloseTo(1786521600.006, 2)
  })

  it('returns undefined for everything unreadable', () => {
    for (const v of [undefined, null, '', 'not a date', NaN, Infinity]) {
      expect(epochSeconds(v as number | string | null | undefined), `${String(v)} must not become a number`).toBeUndefined()
    }
  })

  it('does not treat 0 as missing', () => {
    // `if (!ts)` was the old guard, and it discards the epoch itself. Rare, but it is the
    // difference between a guard on READABILITY and a guard on truthiness.
    expect(epochSeconds(0)).toBe(0)
  })
})

describe('the formatters render an empty form, never NaN', () => {
  afterEach(() => vi.useRealTimers())

  it('reads an ISO string as a past time', () => {
    vi.useFakeTimers().setSystemTime(new Date('2026-08-12T12:00:00Z'))
    expect(relPast('2026-08-12T08:00:00+00:00')).toBe('4h ago')
    // The wire's microseconds put the delta a hair UNDER four hours, and the thresholds floor.
    // Worth pinning: it is the difference between reading the payload and rounding it.
    expect(relPast('2026-08-12T08:00:00.006315+00:00')).toBe('3h ago')
  })

  it('says nothing rather than NaN for garbage', () => {
    expect(relFuture('not a date')).toBe('')
    expect(absTime('not a date')).toBe('')
    expect(relPast('not a date')).toBe('never')
  })

  it('never emits the string NaN for any input a wire field could hold', () => {
    const inputs = [undefined, null, '', 'not a date', NaN, '2026-08-12T08:00:00Z', 1786521600, 0, '1786521600']
    for (const v of inputs) {
      for (const [name, f] of [['relPast', relPast], ['relFuture', relFuture], ['absTime', absTime]] as const) {
        expect(f(v as number | string | null | undefined), `${name}(${String(v)})`).not.toMatch(/NaN|Invalid/)
      }
    }
  })
})

describe('every relative-time formatter in the tree coerces', () => {
  const SRC = join(process.cwd(), 'src')
  const walk = (d: string): string[] =>
    readdirSync(d).flatMap((n) => {
      const p = join(d, n)
      if (statSync(p).isDirectory()) return walk(p)
      return /\.tsx?$/.test(n) && !/\.(test|doc)\.tsx?$/.test(n) ? [p] : []
    })

  /** Each `function rel…`/`function absTime` declaration with its body, by brace depth. A
   *  regex that stops at the first `}` ends at the first `if` block and reports a coercing
   *  formatter as bare. */
  const formatters = walk(SRC).flatMap((f) => {
    const src = readFileSync(f, 'utf8')
    const out: Array<{ file: string; name: string; body: string }> = []
    // Anchored to the naming conventions that ARE time formatters. A `rel[A-Za-z]*` matcher
    // also caught `releaseLiveSlot` in ArtifactCard — a rail must be scoped to what it measured.
    for (const m of src.matchAll(/function (relPast|relFuture|relTime|relTimeShort|relativeTime|absTime)\s*\(/g)) {
      const open = src.indexOf('{', m.index! + m[0].length)
      if (open < 0) continue
      let depth = 0
      for (let i = open; i < src.length; i++) {
        if (src[i] === '{') depth++
        else if (src[i] === '}') { depth--; if (depth === 0) { out.push({ file: f.replace(SRC, 'src'), name: m[1], body: src.slice(open, i + 1) }); break } }
      }
    }
    return out
  })

  it('finds them (not vacuously green)', () => {
    // NINE at the time of writing, under THREE naming conventions — `relPast`/`relFuture`,
    // `relTime`, `relativeTime` — which is why a grep for one name found four and this census
    // found the rest. The family is bigger than any single name suggests.
    expect(formatters.map((f) => `${f.file}:${f.name}`).sort()).toContain('src/pages/schedule/scheduleMeta.ts:relPast')
    expect(formatters.length, 'the matcher must find the time formatters').toBeGreaterThanOrEqual(9)
  })

  it('has none that does arithmetic on unvalidated input', () => {
    // The bar is the DEFECT SHAPE, not adoption of one module: `fileMeta`, `knowledgeMeta`,
    // `notificationMeta` and `taskMeta` already shipped `Date.parse` + `Number.isNaN(t)` and
    // are correct. The schedule/triggers family were outliers against a working sibling, not a
    // missing convention — so either guard passes, and unguarded arithmetic does not.
    // ONE named exemption, with its reason: `ChatPage.relTimeShort` guards with `if (!t)`,
    // and NaN is falsy, so it cannot emit NaN either. It has a different problem — it is fed
    // `last_activity_ts`, a NUMBER, which `Date.parse` rejects, so it renders BLANK for a
    // timestamp that exists. That is fabricated emptiness, not NaN, and it belongs to its own
    // cycle with its own before/after. Named rather than silently matched by a looser rule.
    const EXEMPT = new Set(['src/pages/ChatPage.tsx:relTimeShort'])
    const bare = formatters
      .filter((f) => !/epochSeconds\(/.test(f.body) && !/Number\.isNaN\(/.test(f.body))
      .filter((f) => !EXEMPT.has(`${f.file}:${f.name}`))
    expect(bare.map((f) => `${f.file}:${f.name}`), 'a formatter reads a timestamp without validating it').toEqual([])
  })
})

import { describe, it, expect, vi, afterEach } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { epochSeconds, sessionActivitySeconds, sessionRecencyMs } from './epoch'
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

describe('sessionActivitySeconds — the field choice, in one place', () => {
  const ISO = '2026-08-07T04:24:20.670204+00:00'

  it('prefers last_activity_ts, then last_ts, then created', () => {
    expect(sessionActivitySeconds({ last_activity_ts: ISO, last_ts: '2020-01-01T00:00:00Z', created: '2019-01-01T00:00:00Z' }))
      .toBeCloseTo(Date.parse(ISO) / 1000, 3)
    expect(sessionActivitySeconds({ last_ts: ISO, created: '2019-01-01T00:00:00Z' })).toBeCloseTo(Date.parse(ISO) / 1000, 3)
    expect(sessionActivitySeconds({ created: ISO })).toBeCloseTo(Date.parse(ISO) / 1000, 3)
  })

  it('skips the EMPTY STRING the endpoint really sends for last_ts', () => {
    // Measured on 31 of 32 sessions in a real dev home. `??` would pass `''` to be parsed; the
    // whole reason this chain is `||`-shaped inside one helper instead of at each call site.
    expect(sessionActivitySeconds({ last_activity_ts: '', last_ts: '', created: ISO }))
      .toBeCloseTo(Date.parse(ISO) / 1000, 3)
  })

  it('is undefined — not 0 — when nothing reads', () => {
    // A formatter must tell "no timestamp" (render nothing) from "the epoch"; only the
    // comparator wants 0, and it does that collapse itself.
    expect(sessionActivitySeconds({})).toBeUndefined()
    expect(sessionActivitySeconds({ last_activity_ts: 'not a date', last_ts: '', created: '' })).toBeUndefined()
    expect(sessionRecencyMs({})).toBe(0)
  })

  it('the sorter and the label now read the SAME field choice', () => {
    // The defect this closes is divergence, not arithmetic: a list ordered by `last_activity_ts`
    // beside a label that fell back to `created` sorts by a number the user cannot see.
    const s = { last_activity_ts: ISO, last_ts: '', created: '2019-01-01T00:00:00Z' }
    expect(sessionRecencyMs(s)).toBeCloseTo((sessionActivitySeconds(s) as number) * 1000, 0)
  })

  it('#/chat reads both through lib/epoch, with no local parse left', () => {
    const src = readFileSync(join(process.cwd(), 'src/pages/ChatPage.tsx'), 'utf8')
    const code = src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
    expect(code, 'the label takes the shared field choice').toMatch(/relTimeShort\(sessionActivitySeconds\(s\)\)/)
    expect(code, 'the formatter parses through epochSeconds').toMatch(/const at_s = epochSeconds\(at\)/)
    expect(code, 'no hand-rolled fallback chain remains').not.toMatch(/last_activity_ts \|\| s\.last_ts/)
    expect(code, 'no local Date.parse in the formatter').not.toMatch(/function relTimeShort[\s\S]{0,300}?Date\.parse/)
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
    // 🔴 THE ONE EXEMPTION IS GONE, AND ITS RECORDED REASON WAS WRONG. It read: "`ChatPage`
    // .relTimeShort … is fed `last_activity_ts`, a NUMBER, which `Date.parse` rejects, so it
    // renders BLANK for a timestamp that exists", deferred to "its own cycle". That cycle came,
    // and the premise did not survive contact with the wire: `/api/chat/sessions` sends
    // `last_activity_ts`, `last_ts` and `created` as ISO STRINGS on **all 32** sessions in this
    // dev home, `Date.parse` reads every one, and the history list rendered "3d"/"4d"/"1w" with
    // **0 blank** of 10 sampled labels. The `number` in that claim came from `ChatSession
    // .last_ts?: number` — a DIFFERENT interface from the `ChatSessionSummary` this list uses.
    //
    // 🔑 So the deferred defect was a premise mismatch, not a bug — and it is recorded here
    // rather than quietly dropped, because the next cycle would otherwise chase it again. What
    // was real is the duplication: that formatter re-implemented `Date.parse` + a truthiness
    // guard. It now calls `epochSeconds`, which is why no exemption is needed to keep this green.
    const bare = formatters
      .filter((f) => !/epochSeconds\(/.test(f.body) && !/Number\.isNaN\(/.test(f.body))
    expect(bare.map((f) => `${f.file}:${f.name}`), 'a formatter reads a timestamp without validating it').toEqual([])
  })
})
